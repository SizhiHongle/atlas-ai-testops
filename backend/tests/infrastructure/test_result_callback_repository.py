"""Dispatcher repository tests for Task Gate callback intents."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.result import TaskGateVerdict
from atlas_testops.infrastructure.result_callback_intents import (
    ClaimedTaskGateCallbackIntent,
    TaskGateCallbackIntentRepository,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def _row(**updates: Any) -> DictRow:
    values: dict[str, Any] = {
        "event_id": UUID(int=1),
        "tenant_id": UUID(int=2),
        "project_id": UUID(int=3),
        "task_run_id": UUID(int=4),
        "manifest_hash": DIGEST,
        "gate_decision": "REJECTED",
        "claim_token": UUID(int=5),
        "dispatch_revision": 1,
        "dispatch_attempts": 1,
        "claim_expires_at": NOW + timedelta(minutes=1),
        "created_at": NOW,
    }
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
        if "claim_task_gate_callback_intents" in statement:
            return _Cursor(rows=[_row()])
        return _Cursor()


@pytest.mark.anyio
async def test_repository_uses_only_callback_claim_and_fenced_functions() -> None:
    repository = TaskGateCallbackIntentRepository()
    raw_connection = _Connection()
    connection = cast(AsyncConnection[DictRow], raw_connection)

    claimed = await repository.claim(
        connection,
        claimed_by="callback-dispatcher",
        limit=20,
        lease_duration=timedelta(seconds=30),
    )
    intent = claimed[0]

    assert isinstance(intent, ClaimedTaskGateCallbackIntent)
    assert intent.gate_decision is TaskGateVerdict.REJECTED
    assert raw_connection.calls[0][1] == ("callback-dispatcher", 20, 30)

    assert await repository.mark_delivered(
        connection,
        event_id=intent.event_id,
        claim_token=intent.claim_token,
        dispatch_revision=intent.dispatch_revision,
        response_status_code=204,
    )
    assert await repository.retry(
        connection,
        event_id=intent.event_id,
        claim_token=intent.claim_token,
        dispatch_revision=intent.dispatch_revision,
        error_code="TASK_GATE_CALLBACK_HTTP_RETRYABLE",
        response_status_code=503,
        retry_delay=timedelta(seconds=5),
    )
    assert raw_connection.calls[2][1][-1] == 5_000
    assert await repository.fail(
        connection,
        event_id=intent.event_id,
        claim_token=intent.claim_token,
        dispatch_revision=intent.dispatch_revision,
        error_code="TASK_GATE_CALLBACK_HTTP_REJECTED",
        response_status_code=400,
    )
    functions = (
        "claim_task_gate_callback_intents",
        "mark_task_gate_callback_delivered",
        "retry_task_gate_callback_intent",
        "fail_task_gate_callback_intent",
    )
    assert all(
        function in call[0] for function, call in zip(functions, raw_connection.calls, strict=True)
    )


@pytest.mark.anyio
async def test_repository_rejects_fractional_lease_and_short_retry() -> None:
    repository = TaskGateCallbackIntentRepository()
    connection = cast(AsyncConnection[DictRow], _Connection())

    with pytest.raises(ValueError, match="whole number"):
        await repository.claim(
            connection,
            claimed_by="callback-dispatcher",
            limit=1,
            lease_duration=timedelta(seconds=5.5),
        )
    with pytest.raises(ValueError, match="retry delay"):
        await repository.retry(
            connection,
            event_id=UUID(int=1),
            claim_token=UUID(int=2),
            dispatch_revision=1,
            error_code="TASK_GATE_CALLBACK_TRANSPORT_ERROR",
            response_status_code=None,
            retry_delay=timedelta(milliseconds=99),
        )
