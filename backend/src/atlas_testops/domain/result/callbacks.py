"""Signed, replay-bounded Task Gate callback wire contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, field_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import DIGEST_PATTERN
from atlas_testops.domain.result.gate import TaskGateVerdict

TASK_GATE_CALLBACK_SCHEMA_VERSION: Literal["atlas.task-gate-callback/0.1"] = (
    "atlas.task-gate-callback/0.1"
)
TASK_GATE_CALLBACK_SIGNATURE_PATTERN = r"^hmac-sha256:[A-Za-z0-9_-]{43}$"


class TaskGateCallbackEventContent(FrozenWireModel):
    """The five immutable callback fields covered by the HMAC."""

    event_id: UUID
    task_run_id: UUID
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    gate_decision: TaskGateVerdict
    timestamp: AwareDatetime

    @field_validator("timestamp")
    @classmethod
    def require_canonical_timestamp(cls, value: datetime) -> datetime:
        """Keep callback signatures stable and replay-window checks unambiguous."""

        if value.utcoffset() != timedelta(0) or value.microsecond != 0:
            raise ValueError("Task Gate callback timestamp must use whole UTC seconds")
        return value.astimezone(UTC)


class TaskGateCallbackEvent(TaskGateCallbackEventContent):
    """Exact externally delivered callback document."""

    signature: str = Field(pattern=TASK_GATE_CALLBACK_SIGNATURE_PATTERN)


def task_gate_callback_signing_bytes(
    value: TaskGateCallbackEventContent | TaskGateCallbackEvent,
) -> bytes:
    """Build the frozen canonical HMAC input without the signature field."""

    timestamp = value.timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return "\n".join(
        (
            "ATLAS-TASK-GATE-CALLBACK-V1",
            str(value.event_id),
            str(value.task_run_id),
            value.manifest_hash,
            value.gate_decision.value,
            timestamp,
        )
    ).encode("utf-8")


def task_gate_callback_document(
    value: TaskGateCallbackEvent,
) -> dict[str, JsonValue]:
    """Return the strict six-field public callback body."""

    document = cast(
        dict[str, JsonValue],
        value.model_dump(mode="json", by_alias=True),
    )
    if set(document) != {
        "eventId",
        "taskRunId",
        "manifestHash",
        "gateDecision",
        "timestamp",
        "signature",
    }:
        raise ValueError("Task Gate callback document shape is invalid")
    return document


def encode_task_gate_callback_document(value: TaskGateCallbackEvent) -> bytes:
    """Serialize one deterministic, secret-free callback request body."""

    return json.dumps(
        task_gate_callback_document(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "TASK_GATE_CALLBACK_SCHEMA_VERSION",
    "TASK_GATE_CALLBACK_SIGNATURE_PATTERN",
    "TaskGateCallbackEvent",
    "TaskGateCallbackEventContent",
    "encode_task_gate_callback_document",
    "task_gate_callback_document",
    "task_gate_callback_signing_bytes",
]
