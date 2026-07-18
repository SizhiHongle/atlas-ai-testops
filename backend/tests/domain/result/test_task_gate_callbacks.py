"""Strict Task Gate callback wire contract tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.domain.result import (
    TaskGateCallbackEvent,
    TaskGateCallbackEventContent,
    TaskGateVerdict,
    encode_task_gate_callback_document,
    task_gate_callback_signing_bytes,
)

DIGEST = "sha256:" + "a" * 64
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def test_callback_contract_is_exact_canonical_and_secret_free() -> None:
    content = TaskGateCallbackEventContent(
        event_id=uuid7(),
        task_run_id=uuid7(),
        manifest_hash=DIGEST,
        gate_decision=TaskGateVerdict.ACCEPTED,
        timestamp=NOW,
    )
    event = TaskGateCallbackEvent(
        **content.model_dump(mode="python"),
        signature="hmac-sha256:" + "A" * 43,
    )

    document = json.loads(encode_task_gate_callback_document(event))

    assert set(document) == {
        "eventId",
        "taskRunId",
        "manifestHash",
        "gateDecision",
        "timestamp",
        "signature",
    }
    assert document["gateDecision"] == "ACCEPTED"
    assert task_gate_callback_signing_bytes(content).startswith(b"ATLAS-TASK-GATE-CALLBACK-V1\n")
    serialized = json.dumps(document).casefold()
    assert all(
        forbidden not in serialized
        for forbidden in ("password", "credential", "cookie", "token", "secret")
    )


def test_callback_contract_rejects_extra_fields_and_noncanonical_time() -> None:
    payload = {
        "eventId": str(uuid7()),
        "taskRunId": str(uuid7()),
        "manifestHash": DIGEST,
        "gateDecision": "REJECTED",
        "timestamp": NOW.isoformat(),
        "signature": "hmac-sha256:" + "A" * 43,
        "callbackUrl": "https://attacker.invalid",
    }

    with pytest.raises(ValidationError):
        TaskGateCallbackEvent.model_validate(payload)
    with pytest.raises(ValidationError, match="whole UTC seconds"):
        TaskGateCallbackEventContent(
            event_id=uuid7(),
            task_run_id=uuid7(),
            manifest_hash=DIGEST,
            gate_decision=TaskGateVerdict.INCONCLUSIVE,
            timestamp=NOW + timedelta(microseconds=1),
        )
    with pytest.raises(ValidationError, match="whole UTC seconds"):
        TaskGateCallbackEventContent(
            event_id=uuid7(),
            task_run_id=uuid7(),
            manifest_hash=DIGEST,
            gate_decision=TaskGateVerdict.INCONCLUSIVE,
            timestamp=NOW.astimezone(timezone(timedelta(hours=8))),
        )
