"""Versioned public contracts for replayable DebugRun live projections."""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, ValidationError

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import (
    DebugRunLifecycle,
    DebugRunOutcome,
    DebugRunSnapshotStatus,
)

DEBUG_LIVE_CURSOR_SCHEMA_VERSION: Literal["atlas.debug-live-cursor/0.1"] = (
    "atlas.debug-live-cursor/0.1"
)
DEBUG_LIVE_RUN_PROJECTION_SCHEMA_VERSION: Literal["atlas.debug-live-run-projection/0.1"] = (
    "atlas.debug-live-run-projection/0.1"
)
DEBUG_LIVE_EVENT_SCHEMA_VERSION: Literal["atlas.debug-live-event/0.1"] = (
    "atlas.debug-live-event/0.1"
)
DEBUG_LIVE_SNAPSHOT_SCHEMA_VERSION: Literal["atlas.debug-live-snapshot/0.1"] = (
    "atlas.debug-live-snapshot/0.1"
)
DEBUG_LIVE_CURSOR_MAX_LENGTH = 512

_BASE64URL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class DebugLiveCursor(FrozenWireModel):
    """Opaque replay position bound to one exact DebugRun."""

    schema_version: Literal["atlas.debug-live-cursor/0.1"] = DEBUG_LIVE_CURSOR_SCHEMA_VERSION
    debug_run_id: UUID
    after_seq: int = Field(ge=0)


class DebugLiveRunProjection(FrozenWireModel):
    """Safe DebugRun state embedded in the initial live snapshot."""

    schema_version: Literal["atlas.debug-live-run-projection/0.1"] = (
        DEBUG_LIVE_RUN_PROJECTION_SCHEMA_VERSION
    )
    debug_run_id: UUID
    project_id: UUID
    test_case_id: UUID
    environment_id: UUID
    lifecycle: DebugRunLifecycle
    outcome: DebugRunOutcome
    snapshot_status: DebugRunSnapshotStatus
    revision: int = Field(ge=1)
    execution_deadline: AwareDatetime
    cancel_requested_at: AwareDatetime | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None


class DebugLiveEvent(FrozenWireModel):
    """One allowlisted DebugRun event delivered through the live stream."""

    schema_version: Literal["atlas.debug-live-event/0.1"] = DEBUG_LIVE_EVENT_SCHEMA_VERSION
    event_id: UUID
    debug_run_id: UUID
    seq: int = Field(ge=1)
    event_type: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z][a-z0-9_.-]+$",
    )
    lifecycle: DebugRunLifecycle
    outcome: DebugRunOutcome
    snapshot_status: DebugRunSnapshotStatus
    data: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: AwareDatetime
    cursor: str = Field(min_length=1, max_length=DEBUG_LIVE_CURSOR_MAX_LENGTH)


class DebugLiveSnapshot(FrozenWireModel):
    """Consistent initial projection and event high-water mark for one DebugRun."""

    schema_version: Literal["atlas.debug-live-snapshot/0.1"] = DEBUG_LIVE_SNAPSHOT_SCHEMA_VERSION
    run: DebugLiveRunProjection
    cursor: str = Field(min_length=1, max_length=DEBUG_LIVE_CURSOR_MAX_LENGTH)
    latest_event: DebugLiveEvent | None = None
    observed_at: AwareDatetime


def encode_debug_live_cursor(cursor: DebugLiveCursor) -> str:
    """Encode one cursor as canonical, unpadded Base64URL JSON."""

    payload = json.dumps(
        cursor.model_dump(mode="json", by_alias=True),
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_debug_live_cursor(
    value: str,
    *,
    expected_run_id: UUID,
) -> DebugLiveCursor:
    """Decode an untrusted cursor and require its exact DebugRun binding."""

    try:
        if not value or len(value) > DEBUG_LIVE_CURSOR_MAX_LENGTH:
            raise ValueError("live cursor length is invalid")
        if _BASE64URL_PATTERN.fullmatch(value) is None:
            raise ValueError("live cursor is not unpadded Base64URL")
        padded = value + "=" * (-len(value) % 4)
        payload = base64.b64decode(padded, altchars=b"-_", validate=True)
        parsed = json.loads(payload.decode("utf-8"))
        cursor = DebugLiveCursor.model_validate(parsed)
        if cursor.debug_run_id != expected_run_id:
            raise ValueError("live cursor belongs to another DebugRun")
        return cursor
    except (
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        ValidationError,
    ):
        raise _invalid_live_cursor() from None


def _invalid_live_cursor() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.LIVE_CURSOR_INVALID,
        title="Live Cursor 无效",
        detail="Live Cursor 已损坏、超出限制或不属于当前 DebugRun。",
        status_code=400,
    )
