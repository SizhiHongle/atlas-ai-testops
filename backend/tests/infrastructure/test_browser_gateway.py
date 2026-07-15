"""Signed Browser Runtime HTTP gateway retries and response validation."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import httpx2
import pytest
from tests.domain.runtime.test_browser_protocol import DIGEST_A, _payload, _runtime

from atlas_testops.core.errors import ApplicationError
from atlas_testops.domain.case import (
    DebugRunLifecycle,
    DebugRunOutcome,
)
from atlas_testops.domain.case import (
    TestIntent as CaseIntent,
)
from atlas_testops.domain.runtime import (
    CHAIN_START_DIGEST,
    BrowserRuntimeReport,
    BrowserRuntimeReportKind,
    BrowserRuntimeTransition,
    FinalizeDebugEvidence,
    build_browser_runtime_report,
)
from atlas_testops.domain.workflow import WorkflowGraph
from atlas_testops.infrastructure.browser_auth import BrowserRuntimeRequestSigner
from atlas_testops.infrastructure.browser_gateway import HttpBrowserRuntimeGateway


@pytest.mark.anyio
async def test_gateway_retries_same_signed_idempotent_request(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    bundle, _codec = _runtime(valid_graph, intent_factory)
    contract = bundle.execution_contract
    observed_headers: list[tuple[str, str]] = []
    calls = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        observed_headers.append(
            (
                request.headers["Authorization"],
                request.headers["X-Atlas-Request-Nonce"],
            )
        )
        if calls == 1:
            raise httpx2.ConnectError("response lost", request=request)
        body = bundle.model_dump_json(by_alias=True).encode()
        return httpx2.Response(
            200,
            content=body,
            headers={"Cache-Control": "no-store"},
            request=request,
        )

    client = httpx2.AsyncClient(
        base_url="https://runtime.test",
        transport=httpx2.MockTransport(handler),
    )
    gateway = HttpBrowserRuntimeGateway(
        api_base_url="https://runtime.test",
        tenant_id=contract.tenant_id,
        worker_identity=contract.worker_identity,
        execution_permit="p" * 64,
        request_signer=BrowserRuntimeRequestSigner(
            b"r" * 32,
            maximum_clock_skew=timedelta(seconds=30),
        ),
        client=client,
    )
    result = await gateway.get_execution_bundle(
        tenant_id=contract.tenant_id,
        run_id=contract.debug_run_id,
        worker_identity=contract.worker_identity,
    )
    assert result == bundle
    assert calls == 2
    assert observed_headers[0] == observed_headers[1]
    await gateway.aclose()
    await client.aclose()


@pytest.mark.anyio
async def test_gateway_appends_exact_report_and_rejects_untrusted_response(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    bundle, _codec = _runtime(valid_graph, intent_factory)
    contract = bundle.execution_contract
    report = build_browser_runtime_report(
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
        report_id=uuid7(),
        sequence=1,
        kind=BrowserRuntimeReportKind.EXECUTION_STARTED,
        payload=_payload(BrowserRuntimeReportKind.EXECUTION_STARTED),  # type: ignore[arg-type]
        occurred_at=datetime.now(UTC),
        previous_chain_digest=CHAIN_START_DIGEST,
    )
    persisted = BrowserRuntimeReport(
        tenant_id=contract.tenant_id,
        project_id=contract.project_id,
        environment_id=contract.environment_id,
        debug_run_id=contract.debug_run_id,
        value=report,
        recorded_at=report.occurred_at,
    )
    mode = "valid"

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if mode == "problem":
            return httpx2.Response(
                409,
                json={
                    "type": "https://atlas.test/problems/conflict",
                    "title": "conflict",
                    "status": 409,
                    "detail": "report conflict",
                    "instance": request.url.path,
                    "errorCode": "CONFLICT",
                    "requestId": "request-12345678",
                    "violations": [],
                },
                request=request,
            )
        headers = {} if mode == "cacheable" else {"Cache-Control": "no-store"}
        return httpx2.Response(
            200,
            content=persisted.model_dump_json(by_alias=True).encode(),
            headers=headers,
            request=request,
        )

    client = httpx2.AsyncClient(
        base_url="https://runtime.test",
        transport=httpx2.MockTransport(handler),
    )
    gateway = HttpBrowserRuntimeGateway(
        api_base_url="https://runtime.test",
        tenant_id=contract.tenant_id,
        worker_identity=contract.worker_identity,
        execution_permit="p" * 64,
        request_signer=BrowserRuntimeRequestSigner(
            b"r" * 32,
            maximum_clock_skew=timedelta(seconds=30),
        ),
        client=client,
    )
    assert (
        await gateway.append_report(
            tenant_id=contract.tenant_id,
            run_id=contract.debug_run_id,
            worker_identity=contract.worker_identity,
            report=report,
        )
    ) == persisted

    mode = "cacheable"
    with pytest.raises(ApplicationError, match="不可信"):
        await gateway.append_report(
            tenant_id=contract.tenant_id,
            run_id=contract.debug_run_id,
            worker_identity=contract.worker_identity,
            report=report,
        )
    mode = "problem"
    with pytest.raises(ApplicationError, match="report conflict"):
        await gateway.append_report(
            tenant_id=contract.tenant_id,
            run_id=contract.debug_run_id,
            worker_identity=contract.worker_identity,
            report=report,
        )
    with pytest.raises(ValueError, match="scope"):
        await gateway.get_execution_bundle(
            tenant_id=uuid7(),
            run_id=contract.debug_run_id,
            worker_identity=contract.worker_identity,
        )
    await client.aclose()


def test_gateway_rejects_unsafe_origin_and_transition_serializes_exact_contract() -> None:
    with pytest.raises(ValueError, match="origin"):
        HttpBrowserRuntimeGateway(
            api_base_url="https://user:password@runtime.test/path",
            tenant_id=uuid7(),
            worker_identity="browser-worker",
            execution_permit="p" * 64,
            request_signer=BrowserRuntimeRequestSigner(
                b"r" * 32,
                maximum_clock_skew=timedelta(seconds=30),
            ),
        )
    command = BrowserRuntimeTransition(
        execution_contract_id=uuid7(),
        execution_contract_digest=DIGEST_A,
    )
    assert "executionContractDigest" in command.model_dump_json(by_alias=True)


@pytest.mark.anyio
async def test_gateway_requires_explicit_opt_in_for_plaintext_http() -> None:
    arguments = {
        "api_base_url": "http://runtime.test",
        "tenant_id": uuid7(),
        "worker_identity": "browser-worker",
        "execution_permit": "p" * 64,
        "request_signer": BrowserRuntimeRequestSigner(
            b"r" * 32,
            maximum_clock_skew=timedelta(seconds=30),
        ),
    }
    with pytest.raises(ValueError, match="requires HTTPS"):
        HttpBrowserRuntimeGateway(**arguments)  # type: ignore[arg-type]

    gateway = HttpBrowserRuntimeGateway(
        **arguments,  # type: ignore[arg-type]
        allow_insecure_http=True,
    )
    await gateway.aclose()


def test_finalize_command_remains_worker_owned() -> None:
    command = FinalizeDebugEvidence(
        execution_contract_id=uuid7(),
        execution_contract_digest=DIGEST_A,
        event_chain_head_digest=DIGEST_A,
        event_count=1,
        finalized_at=datetime.now(UTC),
    )
    assert command.assertion_results == ()
    assert DebugRunLifecycle.TERMINATED.value == "TERMINATED"
    assert DebugRunOutcome.INCONCLUSIVE.value == "INCONCLUSIVE"
