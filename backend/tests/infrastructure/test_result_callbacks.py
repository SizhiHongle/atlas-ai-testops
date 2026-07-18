"""HMAC authentication and fixed-endpoint callback adapter tests."""

from __future__ import annotations

import json
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import httpx2
import pytest

from atlas_testops.application.result_callback_delivery import (
    TaskGateCallbackSendStatus,
)
from atlas_testops.domain.result import (
    TASK_GATE_CALLBACK_SCHEMA_VERSION,
    TaskGateCallbackEventContent,
    TaskGateVerdict,
)
from atlas_testops.infrastructure.result_callback_intents import (
    ClaimedTaskGateCallbackIntent,
)
from atlas_testops.infrastructure.result_callbacks import (
    CALLBACK_SCHEMA_HEADER,
    HttpTaskGateCallbackSender,
    TaskGateCallbackAuthenticationError,
    TaskGateCallbackSigner,
)

DIGEST = "sha256:" + "b" * 64
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
KEY = b"k" * 32
KEY_BASE64 = b64encode(KEY).decode("ascii")


def _intent() -> ClaimedTaskGateCallbackIntent:
    return ClaimedTaskGateCallbackIntent(
        event_id=uuid7(),
        tenant_id=uuid7(),
        project_id=uuid7(),
        task_run_id=uuid7(),
        manifest_hash=DIGEST,
        gate_decision=TaskGateVerdict.REJECTED,
        claim_token=uuid7(),
        dispatch_revision=1,
        dispatch_attempts=1,
        claim_expires_at=NOW + timedelta(minutes=1),
        created_at=NOW,
    )


def test_signer_authenticates_exact_fields_and_replay_window() -> None:
    signer = TaskGateCallbackSigner.from_base64_key(
        KEY_BASE64,
        replay_window=timedelta(minutes=5),
    )
    intent = _intent()
    event = signer.sign(
        TaskGateCallbackEventContent(
            event_id=intent.event_id,
            task_run_id=intent.task_run_id,
            manifest_hash=intent.manifest_hash,
            gate_decision=intent.gate_decision,
            timestamp=NOW,
        )
    )

    assert signer.verify(event, now=NOW) == event

    with pytest.raises(TaskGateCallbackAuthenticationError):
        signer.verify(
            event.model_copy(update={"manifest_hash": "sha256:" + "c" * 64}),
            now=NOW,
        )
    with pytest.raises(TaskGateCallbackAuthenticationError):
        signer.verify(event, now=NOW + timedelta(minutes=6))
    with pytest.raises(TaskGateCallbackAuthenticationError):
        TaskGateCallbackSigner(
            b"x" * 32,
            replay_window=timedelta(minutes=5),
        ).verify(event, now=NOW)
    with pytest.raises(ValueError, match="valid base64"):
        TaskGateCallbackSigner.from_base64_key("not-base64")


@pytest.mark.anyio
async def test_http_sender_delivers_exact_signed_body_once() -> None:
    intent = _intent()
    signer = TaskGateCallbackSigner.from_base64_key(KEY_BASE64)
    calls = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert request.url == "https://callback.test/hooks/task-gates"
        assert request.headers["Idempotency-Key"] == str(intent.event_id)
        assert request.headers[CALLBACK_SCHEMA_HEADER] == (TASK_GATE_CALLBACK_SCHEMA_VERSION)
        document = json.loads(await request.aread())
        verified = signer.verify(document, now=NOW)
        assert verified.event_id == intent.event_id
        assert verified.task_run_id == intent.task_run_id
        assert verified.manifest_hash == intent.manifest_hash
        assert verified.gate_decision is TaskGateVerdict.REJECTED
        return httpx2.Response(204, request=request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    sender = HttpTaskGateCallbackSender(
        callback_url="https://callback.test/hooks/task-gates",
        signer=signer,
        timeout=timedelta(seconds=5),
        client=client,
        clock=lambda: NOW,
    )

    result = await sender.deliver(intent)

    assert result.status is TaskGateCallbackSendStatus.DELIVERED
    assert result.response_status_code == 204
    assert calls == 1
    await sender.aclose()
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_code"),
    [
        (
            "transport",
            TaskGateCallbackSendStatus.RETRYABLE,
            "TASK_GATE_CALLBACK_TRANSPORT_ERROR",
        ),
        (
            "rate-limit",
            TaskGateCallbackSendStatus.RETRYABLE,
            "TASK_GATE_CALLBACK_HTTP_RETRYABLE",
        ),
        (
            "server",
            TaskGateCallbackSendStatus.RETRYABLE,
            "TASK_GATE_CALLBACK_HTTP_RETRYABLE",
        ),
        (
            "redirect",
            TaskGateCallbackSendStatus.PERMANENT_FAILURE,
            "TASK_GATE_CALLBACK_HTTP_REJECTED",
        ),
        (
            "reject",
            TaskGateCallbackSendStatus.PERMANENT_FAILURE,
            "TASK_GATE_CALLBACK_HTTP_REJECTED",
        ),
    ],
)
async def test_http_sender_classifies_one_bounded_attempt(
    mode: str,
    expected_status: TaskGateCallbackSendStatus,
    expected_code: str,
) -> None:
    calls = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if mode == "transport":
            raise httpx2.ReadTimeout("response lost", request=request)
        status = {
            "rate-limit": 429,
            "server": 503,
            "redirect": 307,
            "reject": 400,
        }[mode]
        return httpx2.Response(
            status,
            headers={"Location": "https://attacker.invalid"} if mode == "redirect" else None,
            request=request,
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    sender = HttpTaskGateCallbackSender(
        callback_url="https://callback.test/hooks",
        signer=TaskGateCallbackSigner.from_base64_key(KEY_BASE64),
        timeout=timedelta(seconds=5),
        client=client,
        clock=lambda: NOW,
    )

    result = await sender.deliver(_intent())

    assert result.status is expected_status
    assert result.error_code == expected_code
    assert calls == 1
    await client.aclose()


def test_http_sender_rejects_non_global_or_insecure_endpoint_shapes() -> None:
    signer = TaskGateCallbackSigner.from_base64_key(KEY_BASE64)

    with pytest.raises(ValueError, match="requires HTTPS"):
        HttpTaskGateCallbackSender(
            callback_url="http://callback.test/hooks",
            signer=signer,
            timeout=timedelta(seconds=5),
        )
    with pytest.raises(ValueError, match="exact HTTP"):
        HttpTaskGateCallbackSender(
            callback_url="https://user:password@callback.test/hooks",
            signer=signer,
            timeout=timedelta(seconds=5),
        )
    with pytest.raises(ValueError, match="exact HTTP"):
        HttpTaskGateCallbackSender(
            callback_url="https://callback.test/hooks?target=arbitrary",
            signer=signer,
            timeout=timedelta(seconds=5),
        )
