"""Domain tests for DebugRun live projection and replay cursor contracts."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import (
    DebugRunLifecycle,
    DebugRunOutcome,
    DebugRunSnapshotStatus,
)
from atlas_testops.domain.runtime.live import (
    DEBUG_LIVE_CURSOR_MAX_LENGTH,
    DebugLiveCursor,
    DebugLiveEvent,
    DebugLiveRunProjection,
    DebugLiveSnapshot,
    decode_debug_live_cursor,
    encode_debug_live_cursor,
)


def test_debug_live_cursor_round_trip_is_unpadded_and_run_bound() -> None:
    run_id = uuid7()
    cursor = DebugLiveCursor(debug_run_id=run_id, after_seq=42)

    encoded = encode_debug_live_cursor(cursor)

    assert "=" not in encoded
    assert decode_debug_live_cursor(encoded, expected_run_id=run_id) == cursor


@pytest.mark.parametrize("value", ["%%%", "e30", "not-json"])
def test_debug_live_cursor_rejects_corrupted_values(value: str) -> None:
    _assert_invalid_cursor(value, expected_run_id=uuid7())


def test_debug_live_cursor_rejects_overlong_value() -> None:
    _assert_invalid_cursor(
        "a" * (DEBUG_LIVE_CURSOR_MAX_LENGTH + 1),
        expected_run_id=uuid7(),
    )


def test_debug_live_cursor_rejects_another_run() -> None:
    encoded = encode_debug_live_cursor(DebugLiveCursor(debug_run_id=uuid7(), after_seq=3))

    _assert_invalid_cursor(encoded, expected_run_id=uuid7())


def test_debug_live_cursor_rejects_negative_sequence() -> None:
    run_id = uuid7()
    encoded = _encode_untrusted_payload(
        {
            "schemaVersion": "atlas.debug-live-cursor/0.1",
            "debugRunId": str(run_id),
            "afterSeq": -1,
        }
    )

    _assert_invalid_cursor(encoded, expected_run_id=run_id)


def test_debug_live_models_use_camel_case_and_are_frozen() -> None:
    now = datetime(2026, 7, 16, 9, 30, tzinfo=UTC)
    run_id = uuid7()
    project_id = uuid7()
    cursor = encode_debug_live_cursor(DebugLiveCursor(debug_run_id=run_id, after_seq=1))
    run = DebugLiveRunProjection(
        debug_run_id=run_id,
        project_id=project_id,
        test_case_id=uuid7(),
        environment_id=uuid7(),
        lifecycle=DebugRunLifecycle.RUNNING,
        outcome=DebugRunOutcome.NOT_SET,
        snapshot_status=DebugRunSnapshotStatus.CURRENT,
        revision=4,
        execution_deadline=now + timedelta(minutes=5),
        started_at=now,
    )
    event = DebugLiveEvent(
        event_id=uuid7(),
        debug_run_id=run_id,
        seq=1,
        event_type="debug_run.browser.execution.started",
        lifecycle=DebugRunLifecycle.RUNNING,
        outcome=DebugRunOutcome.NOT_SET,
        snapshot_status=DebugRunSnapshotStatus.CURRENT,
        data={"safeSummary": "browser execution started"},
        occurred_at=now,
        cursor=cursor,
    )
    snapshot = DebugLiveSnapshot(
        run=run,
        cursor=cursor,
        latest_event=event,
        observed_at=now,
    )

    payload = snapshot.model_dump(mode="json")

    assert payload["schemaVersion"] == "atlas.debug-live-snapshot/0.1"
    assert payload["observedAt"] == "2026-07-16T09:30:00Z"
    assert payload["run"]["debugRunId"] == str(run_id)
    assert payload["run"]["projectId"] == str(project_id)
    assert payload["run"]["executionDeadline"] == "2026-07-16T09:35:00Z"
    assert payload["latestEvent"]["eventId"] == str(event.event_id)
    assert payload["latestEvent"]["eventType"] == event.event_type
    assert payload["latestEvent"]["data"] == {"safeSummary": "browser execution started"}

    with pytest.raises(ValidationError):
        snapshot.cursor = "replacement"


def _assert_invalid_cursor(value: str, *, expected_run_id: UUID) -> None:
    with pytest.raises(ApplicationError) as captured:
        decode_debug_live_cursor(value, expected_run_id=expected_run_id)

    assert captured.value.error_code is ErrorCode.LIVE_CURSOR_INVALID
    assert captured.value.status_code == 400


def _encode_untrusted_payload(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(serialized).decode("ascii").rstrip("=")
