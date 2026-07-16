"""Unit tests for the dedicated Task intent dispatcher storage boundary."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.infrastructure.task_intents import (
    ClaimedTaskWorkflowIntent,
    TaskIntentDispatcherDatabase,
    TaskWorkflowIntentRepository,
)

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def _row(**updates: Any) -> DictRow:
    values: dict[str, Any] = {
        "id": UUID(int=1),
        "tenant_id": UUID(int=2),
        "project_id": UUID(int=3),
        "task_run_id": UUID(int=4),
        "owner_kind": "TASK_RUN",
        "owner_id": UUID(int=4),
        "namespace": "atlas-task",
        "workflow_id": f"atlas-task/run/{UUID(int=2).hex}/{UUID(int=4).hex}",
        "request_digest": DIGEST,
        "manifest_hash": DIGEST,
        "workflow_type": "AtlasTaskRunWorkflow",
        "task_queue": "atlas-task-run",
        "status": "CLAIMED",
        "claim_token": UUID(int=5),
        "dispatch_revision": 2,
        "dispatch_attempts": 1,
        "claim_expires_at": NOW + timedelta(minutes=1),
        "created_at": NOW,
    }
    values.update(updates)
    return values


def _async_context(value: object) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


def test_dispatcher_database_validates_settings() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        TaskIntentDispatcherDatabase(" ")
    with pytest.raises(ValueError, match="pool sizes"):
        TaskIntentDispatcherDatabase("postgresql://dispatcher/db", pool_max_size=0)
    with pytest.raises(ValueError, match="timeouts"):
        TaskIntentDispatcherDatabase(
            "postgresql://dispatcher/db",
            statement_timeout_ms=0,
        )


@pytest.mark.anyio
async def test_dispatcher_database_has_no_tenant_context() -> None:
    pool = MagicMock()
    with patch(
        "atlas_testops.infrastructure.task_intents.AsyncConnectionPool",
        return_value=pool,
    ):
        database = TaskIntentDispatcherDatabase(
            "postgresql://atlas_dispatcher:secret@localhost/atlas",
            statement_timeout_ms=2_500,
        )

    connection = MagicMock()
    connection.execute = AsyncMock()
    connection.commit = AsyncMock()
    connection.transaction.return_value = _async_context(None)
    pool.connection.return_value = _async_context(connection)
    pool.open = AsyncMock()
    pool.close = AsyncMock()

    await database._configure_connection(connection)
    async with database.transaction() as yielded:
        assert yielded is connection
    await database.check()
    await database.open()
    await database.close()

    statements = [call.args[0] for call in connection.execute.await_args_list]
    assert not any("atlas.tenant_id" in statement for statement in statements)
    assert len(statements) == 4
    connection.commit.assert_awaited_once_with()
    pool.open.assert_awaited_once_with(wait=True)
    pool.close.assert_awaited_once_with()


class _Cursor:
    def __init__(self, *, rows: list[DictRow] | None = None, applied: bool = True) -> None:
        self._rows = rows or []
        self._applied = applied

    async def fetchall(self) -> list[DictRow]:
        return self._rows

    async def fetchone(self) -> DictRow:
        return cast(DictRow, {"applied": self._applied})


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, statement: str, params: tuple[object, ...]) -> _Cursor:
        self.calls.append((statement, params))
        if "claim_task_workflow_start_intents" in statement:
            return _Cursor(rows=[_row()])
        return _Cursor()


@pytest.mark.anyio
async def test_repository_uses_only_trusted_claim_and_cas_functions() -> None:
    repository = TaskWorkflowIntentRepository()
    raw_connection = _Connection()
    connection = cast(AsyncConnection[DictRow], raw_connection)

    claimed = await repository.claim(
        connection,
        claimed_by="dispatcher-1",
        namespace="atlas-task",
        limit=20,
        lease_duration=timedelta(seconds=30),
    )
    intent = claimed[0]
    assert isinstance(intent, ClaimedTaskWorkflowIntent)
    assert intent.manifest_hash == DIGEST
    assert raw_connection.calls[0][1] == ("dispatcher-1", "atlas-task", 20, 30)
    assert raw_connection.calls[0][0].count("%s") == 4

    assert await repository.mark_started(
        connection,
        intent_id=intent.id,
        claim_token=intent.claim_token,
        dispatch_revision=intent.dispatch_revision,
    )
    assert await repository.retry(
        connection,
        intent_id=intent.id,
        claim_token=intent.claim_token,
        dispatch_revision=intent.dispatch_revision,
        error_code="TEMPORAL_RPC_UNAVAILABLE",
        retry_delay=timedelta(seconds=5),
    )
    assert raw_connection.calls[2][1][-1] == 5_000
    assert await repository.fail(
        connection,
        intent_id=intent.id,
        claim_token=intent.claim_token,
        dispatch_revision=intent.dispatch_revision,
        error_code="INTENT_CONTRACT_MISMATCH",
    )

    functions = [
        "claim_task_workflow_start_intents",
        "mark_task_workflow_start_intent_started",
        "retry_task_workflow_start_intent",
        "fail_task_workflow_start_intent",
    ]
    assert all(
        function in call[0]
        for function, call in zip(functions, raw_connection.calls, strict=True)
    )


@pytest.mark.anyio
async def test_repository_rejects_fractional_or_zero_lease() -> None:
    repository = TaskWorkflowIntentRepository()
    connection = cast(AsyncConnection[DictRow], _Connection())
    for duration in (timedelta(0), timedelta(milliseconds=1500)):
        with pytest.raises(ValueError, match="whole number"):
            await repository.claim(
                connection,
                claimed_by="dispatcher-1",
                namespace="atlas-task",
                limit=1,
                lease_duration=duration,
            )

    with pytest.raises(ValueError, match="retry delay"):
        await repository.retry(
            connection,
            intent_id=UUID(int=1),
            claim_token=UUID(int=2),
            dispatch_revision=1,
            error_code="TEMPORAL_RPC_UNAVAILABLE",
            retry_delay=timedelta(milliseconds=99),
        )
