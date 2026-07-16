"""Dedicated dispatcher storage for durable Task Workflow start intents."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True, slots=True)
class ClaimedTaskWorkflowIntent:
    """One leased dispatch command projected by trusted PostgreSQL code."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    owner_kind: str
    owner_id: UUID
    namespace: str
    workflow_id: str
    request_digest: str
    manifest_hash: str
    workflow_type: str
    task_queue: str
    status: str
    claim_token: UUID
    dispatch_revision: int
    dispatch_attempts: int
    claim_expires_at: datetime
    created_at: datetime

    @classmethod
    def from_row(cls, row: DictRow) -> ClaimedTaskWorkflowIntent:
        """Build the explicit claim projection without retaining a database row."""

        return cls(**{field: row[field] for field in cls.__dataclass_fields__})


class TaskIntentDispatcherDatabase:
    """Small connection pool authenticated only as the dispatcher database role."""

    def __init__(
        self,
        database_url: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 4,
        connect_timeout_seconds: float = 10.0,
        statement_timeout_ms: int = 10_000,
    ) -> None:
        normalized_url = database_url.strip()
        if not normalized_url:
            raise ValueError("dispatcher database URL must not be blank")
        if pool_min_size < 0 or pool_max_size < max(1, pool_min_size):
            raise ValueError("dispatcher database pool sizes are invalid")
        if connect_timeout_seconds <= 0 or statement_timeout_ms <= 0:
            raise ValueError("dispatcher database timeouts must be positive")

        self._statement_timeout_ms = statement_timeout_ms
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
            conninfo=normalized_url,
            min_size=pool_min_size,
            max_size=pool_max_size,
            open=False,
            timeout=connect_timeout_seconds,
            kwargs={"autocommit": False, "row_factory": dict_row},
            configure=self._configure_connection,
            name="atlas-task-intent-dispatcher",
        )

    async def _configure_connection(self, connection: AsyncConnection[DictRow]) -> None:
        """Install only non-sensitive session defaults; there is no Tenant context."""

        await connection.execute("select set_config('timezone', 'UTC', false)")
        await connection.execute(
            "select set_config('statement_timeout', %s, false)",
            (f"{self._statement_timeout_ms}ms",),
        )
        await connection.execute("select set_config('search_path', 'atlas,public', false)")
        await connection.commit()

    async def open(self) -> None:
        """Open the dispatcher-only connection pool."""

        await self._pool.open(wait=True)

    async def close(self) -> None:
        """Close the dispatcher-only connection pool."""

        await self._pool.close()

    async def check(self) -> None:
        """Check that the dispatcher role can reach PostgreSQL."""

        async with self._pool.connection() as connection:
            await connection.execute("select 1")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection[DictRow]]:
        """Open one short dispatcher transaction without API Tenant authority."""

        async with self._pool.connection() as connection, connection.transaction():
            yield connection


class TaskWorkflowIntentRepository:
    """Call only the dispatcher role's narrow SECURITY DEFINER functions."""

    async def claim(
        self,
        connection: AsyncConnection[DictRow],
        *,
        claimed_by: str,
        namespace: str,
        limit: int,
        lease_duration: timedelta,
    ) -> tuple[ClaimedTaskWorkflowIntent, ...]:
        lease_seconds = _whole_seconds(lease_duration, field="lease duration")
        cursor = await connection.execute(
            """
            select *
            from atlas.claim_task_workflow_start_intents(%s, %s, %s, %s)
            """,
            (claimed_by, namespace, limit, lease_seconds),
        )
        return tuple(ClaimedTaskWorkflowIntent.from_row(row) for row in await cursor.fetchall())

    async def mark_started(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.mark_task_workflow_start_intent_started(
              %s, %s, %s
            ) as applied
            """,
            (intent_id, claim_token, dispatch_revision),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def retry(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
        error_code: str,
        retry_delay: timedelta,
    ) -> bool:
        retry_delay_ms = _retry_milliseconds(retry_delay)
        cursor = await connection.execute(
            """
            select atlas.retry_task_workflow_start_intent(
              %s, %s, %s, %s, %s
            ) as applied
            """,
            (
                intent_id,
                claim_token,
                dispatch_revision,
                error_code,
                retry_delay_ms,
            ),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def fail(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
        error_code: str,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.fail_task_workflow_start_intent(
              %s, %s, %s, %s
            ) as applied
            """,
            (intent_id, claim_token, dispatch_revision, error_code),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])


def _whole_seconds(value: timedelta, *, field: str) -> int:
    seconds = value.total_seconds()
    if seconds < 1 or not seconds.is_integer():
        raise ValueError(f"{field} must be a positive whole number of seconds")
    return int(seconds)


def _retry_milliseconds(value: timedelta) -> int:
    milliseconds = ceil(value.total_seconds() * 1_000)
    if not 100 <= milliseconds <= 3_600_000:
        raise ValueError("retry delay must be between 100 and 3600000 milliseconds")
    return milliseconds
