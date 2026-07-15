"""Machine-authenticated Browser Runtime internal API tests."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid7

import httpx2
import pytest
from tests.domain.case.test_runtime_evidence import _run
from tests.domain.runtime.test_browser_protocol import DIGEST_A, _payload, _runtime

from atlas_testops.api.internal.browser_runtime import get_debug_runtime_service
from atlas_testops.application.debug_runtime import DebugRuntimeService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError
from atlas_testops.domain.case import (
    DebugRun,
    DebugRunLifecycle,
    DebugRunOutcome,
)
from atlas_testops.domain.case import (
    TestIntent as CaseIntent,
)
from atlas_testops.domain.runtime import (
    CHAIN_START_DIGEST,
    AppendBrowserRuntimeReport,
    BrowserExecutionBundle,
    BrowserRuntimeReport,
    BrowserRuntimeReportKind,
    EvidenceManifest,
    FinalizeDebugEvidence,
    build_browser_runtime_report,
    build_evidence_manifest,
)
from atlas_testops.domain.workflow import WorkflowGraph
from atlas_testops.infrastructure.browser_auth import (
    BrowserRuntimePermitSigner,
    BrowserRuntimeRequestSigner,
)
from atlas_testops.infrastructure.browser_gateway import HttpBrowserRuntimeGateway
from atlas_testops.main import create_app


class FakeRuntimeService:
    def __init__(self, bundle: BrowserExecutionBundle, run: DebugRun) -> None:
        self.bundle = bundle
        self.run = run

    async def get_browser_execution_bundle(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        worker_identity: str,
    ) -> BrowserExecutionBundle:
        return self.bundle

    async def mark_ready(self, *args: object, **kwargs: object) -> DebugRun:
        return self._active(DebugRunLifecycle.READY)

    async def start_execution(self, *args: object, **kwargs: object) -> DebugRun:
        return self._active(DebugRunLifecycle.RUNNING)

    async def append_browser_report(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        worker_identity: str,
        report: AppendBrowserRuntimeReport,
    ) -> BrowserRuntimeReport:
        return BrowserRuntimeReport(
            tenant_id=tenant_id,
            project_id=self.bundle.execution_contract.project_id,
            environment_id=self.bundle.execution_contract.environment_id,
            debug_run_id=run_id,
            value=report,
            recorded_at=report.occurred_at,
        )

    async def finalize_evidence(
        self,
        tenant_id: UUID,
        run_id: UUID,
        command: FinalizeDebugEvidence,
        *,
        worker_identity: str | None = None,
    ) -> tuple[DebugRun, EvidenceManifest]:
        manifest, _private = build_evidence_manifest(
            manifest_id=uuid7(),
            run=self.run,
            contract=self.bundle.execution_contract,
            command=command,
        )
        terminated = DebugRun.model_validate(
            self._active(DebugRunLifecycle.RUNNING)
            .model_copy(
                update={
                    "lifecycle": DebugRunLifecycle.TERMINATED,
                    "outcome": DebugRunOutcome.INCONCLUSIVE,
                    "evidence_manifest_id": manifest.id,
                    "evidence_manifest_digest": manifest.content_digest,
                    "completed_at": manifest.finalized_at,
                    "revision": 4,
                    "updated_at": manifest.finalized_at,
                }
            )
            .model_dump(mode="python")
        )
        return terminated, manifest

    def _active(self, lifecycle: DebugRunLifecycle) -> DebugRun:
        return DebugRun.model_validate(
            self.run.model_copy(
                update={
                    "lifecycle": lifecycle,
                    "started_at": self.bundle.execution_contract.created_at,
                    "execution_contract_id": self.bundle.execution_contract.id,
                    "execution_contract_digest": (
                        self.bundle.execution_contract.content_digest
                    ),
                    "revision": 2 if lifecycle is DebugRunLifecycle.READY else 3,
                    "updated_at": self.bundle.execution_contract.created_at,
                }
            ).model_dump(mode="python")
        )


@pytest.mark.anyio
async def test_internal_api_round_trip_uses_permit_and_request_signature(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    bundle, codec = _runtime(valid_graph, intent_factory)
    contract = bundle.execution_contract
    run = _run(valid_graph, intent_factory)
    service = FakeRuntimeService(bundle, run)
    permit_signer = BrowserRuntimePermitSigner(
        b"p" * 32,
        maximum_lifetime=timedelta(hours=1),
    )
    request_signer = BrowserRuntimeRequestSigner(
        b"r" * 32,
        maximum_clock_skew=timedelta(seconds=30),
    )
    now = datetime.now(UTC)
    permit = permit_signer.mint(
        tenant_id=contract.tenant_id,
        run_id=contract.debug_run_id,
        worker_identity=contract.worker_identity,
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    app = create_app(
        Settings(environment="test", cors_origins=[]),
        browser_runtime_permit_signer=permit_signer,
        browser_runtime_request_signer=request_signer,
        browser_context_envelope_codec=codec,
    )
    app.dependency_overrides[get_debug_runtime_service] = lambda: cast(
        DebugRuntimeService,
        service,
    )
    client = httpx2.AsyncClient(
        base_url="https://runtime.test",
        transport=httpx2.ASGITransport(app=app),
    )
    gateway = HttpBrowserRuntimeGateway(
        api_base_url="https://runtime.test",
        tenant_id=contract.tenant_id,
        worker_identity=contract.worker_identity,
        execution_permit=permit,
        request_signer=request_signer,
        client=client,
    )
    assert (
        await gateway.get_execution_bundle(
            tenant_id=contract.tenant_id,
            run_id=contract.debug_run_id,
            worker_identity=contract.worker_identity,
        )
    ) == bundle
    ready = await gateway.mark_ready(
        tenant_id=contract.tenant_id,
        run_id=contract.debug_run_id,
        worker_identity=contract.worker_identity,
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
    )
    assert ready.lifecycle is DebugRunLifecycle.READY
    started = await gateway.start_execution(
        tenant_id=contract.tenant_id,
        run_id=contract.debug_run_id,
        worker_identity=contract.worker_identity,
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
    )
    assert started.lifecycle is DebugRunLifecycle.RUNNING

    report = build_browser_runtime_report(
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
        report_id=uuid7(),
        sequence=1,
        kind=BrowserRuntimeReportKind.EXECUTION_STARTED,
        payload=_payload(BrowserRuntimeReportKind.EXECUTION_STARTED),  # type: ignore[arg-type]
        occurred_at=now,
        previous_chain_digest=CHAIN_START_DIGEST,
    )
    assert (
        await gateway.append_report(
            tenant_id=contract.tenant_id,
            run_id=contract.debug_run_id,
            worker_identity=contract.worker_identity,
            report=report,
        )
    ).value == report

    command = FinalizeDebugEvidence(
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
        event_chain_head_digest=DIGEST_A,
        event_count=1,
        finalized_at=now + timedelta(seconds=1),
    )
    finalized_run, manifest = await gateway.finalize_evidence(
        tenant_id=contract.tenant_id,
        run_id=contract.debug_run_id,
        worker_identity=contract.worker_identity,
        command=command,
    )
    assert finalized_run.lifecycle is DebugRunLifecycle.TERMINATED
    assert manifest.outcome.value == "INCONCLUSIVE"

    invalid_gateway = HttpBrowserRuntimeGateway(
        api_base_url="https://runtime.test",
        tenant_id=contract.tenant_id,
        worker_identity=contract.worker_identity,
        execution_permit="x" * 64,
        request_signer=request_signer,
        client=client,
    )
    with pytest.raises(ApplicationError, match="execution permit"):
        await invalid_gateway.get_execution_bundle(
            tenant_id=contract.tenant_id,
            run_id=contract.debug_run_id,
            worker_identity=contract.worker_identity,
        )
    await client.aclose()


@pytest.mark.anyio
async def test_internal_runtime_rejects_unbounded_body_before_authentication() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))
    async with httpx2.AsyncClient(
        base_url="https://runtime.test",
        transport=httpx2.ASGITransport(app=app),
    ) as client:
        configuration_error = await client.get(
            f"/internal/v1/debug-runs/{uuid7()}/browser-execution"
        )
        response = await client.post(
            f"/internal/v1/debug-runs/{uuid7()}/browser-reports",
            content=b"x" * (1024 * 1024 + 1),
        )
    assert configuration_error.status_code == 503
    assert configuration_error.headers["Cache-Control"] == "no-store"
    assert configuration_error.headers["Pragma"] == "no-cache"
    assert response.status_code == 413
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
