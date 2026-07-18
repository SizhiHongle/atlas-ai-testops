"""Dispatcher repository tests for fenced Task Schedule sync functions."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.orchestration.test_task_schedule_orchestration import (
    NEXT_FIRES,
    intent,
)

from atlas_testops.infrastructure.task_schedules import (
    ClaimedTaskScheduleSyncIntent,
    TaskScheduleSyncIntentRepository,
)


def _row(**updates: Any) -> DictRow:
    selected = intent()
    values: dict[str, Any] = {
        field: getattr(selected, field) for field in selected.__dataclass_fields__
    }
    values["calendar"] = selected.calendar.model_dump(
        mode="python",
        by_alias=True,
    )
    values["retry_policy"] = selected.retry_policy.model_dump(
        mode="python",
        by_alias=True,
    )
    values.update(updates)
    return values


class _Cursor:
    def __init__(
        self,
        *,
        rows: list[DictRow] | None = None,
        applied: bool = True,
    ) -> None:
        self._rows = rows or []
        self._applied = applied

    async def fetchall(self) -> list[DictRow]:
        return self._rows

    async def fetchone(self) -> DictRow:
        return cast(DictRow, {"applied": self._applied})


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(
        self,
        statement: str,
        params: tuple[object, ...],
    ) -> _Cursor:
        self.calls.append((statement, params))
        if "claim_task_schedule_sync_intents" in statement:
            return _Cursor(rows=[_row()])
        return _Cursor()


@pytest.mark.anyio
async def test_repository_uses_only_narrow_claim_and_fenced_cas_functions() -> None:
    repository = TaskScheduleSyncIntentRepository()
    raw_connection = _Connection()
    connection = cast(AsyncConnection[DictRow], raw_connection)

    claimed = await repository.claim(
        connection,
        claimed_by="schedule-dispatcher",
        namespace="atlas-task",
        limit=20,
        lease_duration=timedelta(seconds=30),
    )
    selected = claimed[0]
    assert isinstance(selected, ClaimedTaskScheduleSyncIntent)
    assert selected.calendar.minutes == (0, 30)
    assert selected.retry_policy.content_digest == intent().retry_policy.content_digest
    assert raw_connection.calls[0][1] == (
        "schedule-dispatcher",
        "atlas-task",
        20,
        30,
    )

    assert await repository.mark_applied(
        connection,
        intent_id=selected.id,
        claim_token=selected.claim_token,
        dispatch_revision=selected.dispatch_revision,
        next_fire_times=NEXT_FIRES,
    )
    assert raw_connection.calls[1][1][-1] == list(NEXT_FIRES)
    assert await repository.retry(
        connection,
        intent_id=selected.id,
        claim_token=selected.claim_token,
        dispatch_revision=selected.dispatch_revision,
        error_code="TEMPORAL_SCHEDULE_RPC_UNAVAILABLE",
        retry_delay=timedelta(seconds=5),
    )
    assert raw_connection.calls[2][1][-1] == 5_000
    assert await repository.fail(
        connection,
        intent_id=selected.id,
        claim_token=selected.claim_token,
        dispatch_revision=selected.dispatch_revision,
        error_code="TEMPORAL_SCHEDULE_MEMO_MISMATCH",
    )
    functions = (
        "claim_task_schedule_sync_intents",
        "mark_task_schedule_sync_applied",
        "retry_task_schedule_sync_intent",
        "fail_task_schedule_sync_intent",
    )
    assert all(
        function in call[0] for function, call in zip(functions, raw_connection.calls, strict=True)
    )


@pytest.mark.anyio
async def test_repository_rejects_invalid_lease_and_retry_delay() -> None:
    repository = TaskScheduleSyncIntentRepository()
    connection = cast(AsyncConnection[DictRow], _Connection())
    for duration in (
        timedelta(0),
        timedelta(milliseconds=1_500),
        timedelta(seconds=301),
    ):
        with pytest.raises(ValueError, match="whole seconds"):
            await repository.claim(
                connection,
                claimed_by="schedule-dispatcher",
                namespace="atlas-task",
                limit=1,
                lease_duration=duration,
            )

    with pytest.raises(ValueError, match="retry delay"):
        await repository.retry(
            connection,
            intent_id=intent().id,
            claim_token=intent().claim_token,
            dispatch_revision=1,
            error_code="TEMPORAL_SCHEDULE_RPC_UNAVAILABLE",
            retry_delay=timedelta(milliseconds=99),
        )
