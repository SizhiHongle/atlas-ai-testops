"""Signed production Task Unit execution adapter tests."""

from __future__ import annotations

import json
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import httpx2
import pytest
from pydantic import SecretStr

from atlas_testops.core.config import Settings
from atlas_testops.infrastructure.task_execution import (
    ATTEMPT_HEADER,
    CONTENT_DIGEST_HEADER,
    NONCE_HEADER,
    TASK_EXECUTION_PATH,
    TASK_EXECUTOR_RESULT_SCHEMA,
    HttpTaskUnitExecutionPort,
    TaskExecutionAuthenticationError,
    TaskExecutionMessageSigner,
    build_optional_task_unit_execution_port,
)
from atlas_testops.orchestration.tasks import (
    TaskUnitExecutionRequest,
    UnitAttemptWorkflowInput,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
KEY_BASE64 = b64encode(b"k" * 32).decode("ascii")


def _request(*, deadline: datetime | None = None) -> TaskUnitExecutionRequest:
    tenant_id = uuid7()
    return TaskUnitExecutionRequest(
        attempt=UnitAttemptWorkflowInput(
            tenant_id=str(tenant_id),
            project_id=str(uuid7()),
            task_run_id=str(uuid7()),
            request_digest=DIGEST_A,
            manifest_hash=DIGEST_B,
            ordinal=1,
            execution_unit_id=str(uuid7()),
            unit_attempt_id=str(uuid7()),
            execution_deadline=(deadline or datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            activity_timeout_seconds=120,
        ),
        ticket_id=str(uuid7()),
        ticket_digest=DIGEST_A,
    )


def _result_body(
    *,
    status: str = "EXECUTED_UNSEALED",
    error_code: str | None = None,
    **extra: object,
) -> bytes:
    return json.dumps(
        {
            "schemaVersion": TASK_EXECUTOR_RESULT_SCHEMA,
            "status": status,
            "errorCode": error_code,
            **extra,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_message_signatures_bind_exact_attempt_ticket_body_and_response() -> None:
    now = datetime.now(UTC)
    request = _request()
    attempt_id = UUID(request.attempt.unit_attempt_id)
    tenant_id = UUID(request.attempt.tenant_id)
    ticket_id = UUID(request.ticket_id)
    signer = TaskExecutionMessageSigner(
        b"k" * 32,
        maximum_clock_skew=timedelta(seconds=30),
    )
    body = b'{"secretFree":true}'
    headers = signer.sign_request_headers(
        worker_identity="task-worker-01",
        tenant_id=tenant_id,
        attempt_id=attempt_id,
        ticket_id=ticket_id,
        ticket_digest=request.ticket_digest,
        body=body,
        now=now,
        nonce="n" * 24,
    )
    identity = signer.verify_request_headers(headers=headers, body=body, now=now)
    assert identity.attempt_id == attempt_id
    assert identity.ticket_id == ticket_id
    assert headers["Idempotency-Key"] == str(attempt_id)

    response_body = _result_body()
    response_headers = signer.sign_response_headers(
        worker_identity=identity.worker_identity,
        tenant_id=identity.tenant_id,
        attempt_id=identity.attempt_id,
        ticket_id=identity.ticket_id,
        ticket_digest=identity.ticket_digest,
        request_nonce=headers[NONCE_HEADER],
        request_content_digest=headers[CONTENT_DIGEST_HEADER],
        status_code=200,
        body=response_body,
        now=now,
    )
    signer.verify_response_headers(
        headers=response_headers,
        worker_identity=identity.worker_identity,
        tenant_id=identity.tenant_id,
        attempt_id=identity.attempt_id,
        ticket_id=identity.ticket_id,
        ticket_digest=identity.ticket_digest,
        request_nonce=headers[NONCE_HEADER],
        request_content_digest=headers[CONTENT_DIGEST_HEADER],
        status_code=200,
        body=response_body,
        now=now,
    )

    with pytest.raises(TaskExecutionAuthenticationError):
        signer.verify_request_headers(headers=headers, body=b"{}", now=now)
    with pytest.raises(TaskExecutionAuthenticationError):
        signer.verify_response_headers(
            headers=response_headers,
            worker_identity=identity.worker_identity,
            tenant_id=identity.tenant_id,
            attempt_id=uuid7(),
            ticket_id=identity.ticket_id,
            ticket_digest=identity.ticket_digest,
            request_nonce=headers[NONCE_HEADER],
            request_content_digest=headers[CONTENT_DIGEST_HEADER],
            status_code=200,
            body=response_body,
            now=now,
        )


@pytest.mark.anyio
async def test_http_port_accepts_only_signed_correlated_result_ref() -> None:
    request = _request()
    signer = TaskExecutionMessageSigner.from_base64_key(KEY_BASE64)
    result_ref_id = str(uuid7())
    observed_body: dict[str, object] = {}

    async def handler(http_request: httpx2.Request) -> httpx2.Response:
        assert http_request.url.path == TASK_EXECUTION_PATH
        body = await http_request.aread()
        identity = signer.verify_request_headers(
            headers=http_request.headers,
            body=body,
        )
        observed_body.update(json.loads(body))
        response_body = _result_body(
            status="RESULT_FINALIZED",
            resultRefId=result_ref_id,
            sealContentHash=DIGEST_B,
        )
        response_headers = signer.sign_response_headers(
            worker_identity=identity.worker_identity,
            tenant_id=identity.tenant_id,
            attempt_id=identity.attempt_id,
            ticket_id=identity.ticket_id,
            ticket_digest=identity.ticket_digest,
            request_nonce=http_request.headers[NONCE_HEADER],
            request_content_digest=http_request.headers[CONTENT_DIGEST_HEADER],
            status_code=200,
            body=response_body,
        )
        return httpx2.Response(
            200,
            content=response_body,
            headers=response_headers,
            request=http_request,
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    port = HttpTaskUnitExecutionPort(
        api_base_url="https://executor.test",
        worker_identity="task-worker-01",
        signer=signer,
        timeout=timedelta(seconds=30),
        response_maximum_bytes=16 * 1024,
        client=client,
    )
    result = await port.execute(request)

    assert result.status == "RESULT_FINALIZED"
    assert result.result_ref_id == result_ref_id
    assert result.seal_content_hash == DIGEST_B
    assert observed_body["schemaVersion"] == "atlas.task-unit-executor-request/0.1"
    serialized = json.dumps(observed_body).casefold()
    assert all(
        forbidden not in serialized
        for forbidden in ("password", "credential", "cookie", "token", "secret")
    )
    await port.aclose()
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "expected_code"),
    [
        ("transport", "TASK_EXECUTOR_OUTCOME_UNKNOWN"),
        ("status", "TASK_EXECUTOR_OUTCOME_UNKNOWN"),
        ("unsigned", "TASK_EXECUTOR_RESPONSE_INVALID"),
        ("invalid-payload", "TASK_EXECUTOR_RESPONSE_INVALID"),
        ("oversized", "TASK_EXECUTOR_OUTCOME_UNKNOWN"),
    ],
)
async def test_http_port_fails_closed_without_replaying_unknown_side_effect(
    mode: str,
    expected_code: str,
) -> None:
    request = _request()
    signer = TaskExecutionMessageSigner.from_base64_key(KEY_BASE64)
    calls = 0

    async def handler(http_request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if mode == "transport":
            raise httpx2.ReadTimeout("response lost", request=http_request)
        request_body = await http_request.aread()
        identity = signer.verify_request_headers(
            headers=http_request.headers,
            body=request_body,
        )
        status_code = 503 if mode == "status" else 200
        if mode == "oversized":
            response_body = b"x" * 2_000
        elif mode == "invalid-payload":
            response_body = _result_body(status="PASSED")
        else:
            response_body = _result_body()
        response_headers: dict[str, str] = {"Content-Type": "application/json"}
        if mode not in {"unsigned", "status"}:
            response_headers = signer.sign_response_headers(
                worker_identity=identity.worker_identity,
                tenant_id=identity.tenant_id,
                attempt_id=identity.attempt_id,
                ticket_id=identity.ticket_id,
                ticket_digest=identity.ticket_digest,
                request_nonce=http_request.headers[NONCE_HEADER],
                request_content_digest=http_request.headers[CONTENT_DIGEST_HEADER],
                status_code=status_code,
                body=response_body,
            )
        return httpx2.Response(
            status_code,
            content=response_body,
            headers=response_headers,
            request=http_request,
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    port = HttpTaskUnitExecutionPort(
        api_base_url="https://executor.test",
        worker_identity="task-worker-01",
        signer=signer,
        timeout=timedelta(seconds=30),
        response_maximum_bytes=1_024,
        client=client,
    )
    result = await port.execute(request)

    assert result.status == "INCONCLUSIVE"
    assert result.error_code == expected_code
    assert calls == 1
    await client.aclose()


@pytest.mark.anyio
async def test_http_port_does_not_call_executor_after_frozen_deadline() -> None:
    request = _request(deadline=datetime.now(UTC) - timedelta(seconds=1))

    async def handler(_request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("expired request reached the executor")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    port = HttpTaskUnitExecutionPort(
        api_base_url="https://executor.test",
        worker_identity="task-worker-01",
        signer=TaskExecutionMessageSigner.from_base64_key(KEY_BASE64),
        timeout=timedelta(seconds=30),
        response_maximum_bytes=1_024,
        client=client,
    )

    result = await port.execute(request)

    assert result.status == "CANCELED"
    assert result.error_code == "TASK_ATTEMPT_DEADLINE_EXPIRED"
    await client.aclose()


def test_factory_requires_complete_safe_configuration() -> None:
    assert (
        build_optional_task_unit_execution_port(Settings(environment="test"))
        is None
    )
    configured = Settings(
        environment="test",
        task_execution_api_base_url="http://executor.test/",
        task_execution_hmac_key_base64=SecretStr(KEY_BASE64),
        task_execution_worker_identity="task-worker-test",
        task_execution_http_timeout_seconds=17,
        task_execution_response_maximum_bytes=2_048,
    )
    port = build_optional_task_unit_execution_port(configured)
    assert port is not None
    assert port._api_base_url == "http://executor.test"
    assert port._worker_identity == "task-worker-test"

    with pytest.raises(ValueError, match="requires HTTPS"):
        HttpTaskUnitExecutionPort(
            api_base_url="http://executor.test",
            worker_identity="task-worker",
            signer=TaskExecutionMessageSigner.from_base64_key(KEY_BASE64),
            timeout=timedelta(seconds=30),
            response_maximum_bytes=1_024,
        )


def test_request_signature_rejects_expiry_and_invalid_key_material() -> None:
    now = datetime.now(UTC)
    signer = TaskExecutionMessageSigner.from_base64_key(
        KEY_BASE64,
        maximum_clock_skew=timedelta(seconds=5),
    )
    request = _request()
    body = b"{}"
    headers = signer.sign_request_headers(
        worker_identity="task-worker",
        tenant_id=UUID(request.attempt.tenant_id),
        attempt_id=UUID(request.attempt.unit_attempt_id),
        ticket_id=UUID(request.ticket_id),
        ticket_digest=request.ticket_digest,
        body=body,
        now=now,
    )
    with pytest.raises(TaskExecutionAuthenticationError):
        signer.verify_request_headers(
            headers=headers,
            body=body,
            now=now + timedelta(seconds=6),
        )
    with pytest.raises(ValueError, match="base64"):
        TaskExecutionMessageSigner.from_base64_key("not-base64")
    with pytest.raises(ValueError, match="at least 32"):
        TaskExecutionMessageSigner(b"short", maximum_clock_skew=timedelta(seconds=30))


def test_request_headers_do_not_confuse_attempt_identity() -> None:
    request = _request()
    signer = TaskExecutionMessageSigner.from_base64_key(KEY_BASE64)
    headers = signer.sign_request_headers(
        worker_identity="task-worker",
        tenant_id=UUID(request.attempt.tenant_id),
        attempt_id=UUID(request.attempt.unit_attempt_id),
        ticket_id=UUID(request.ticket_id),
        ticket_digest=request.ticket_digest,
        body=b"{}",
    )
    assert headers[ATTEMPT_HEADER] == request.attempt.unit_attempt_id
