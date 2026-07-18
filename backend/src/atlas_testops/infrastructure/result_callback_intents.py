"""Narrow dispatcher access for durable Task Gate callback intents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.result import TaskGateVerdict


@dataclass(frozen=True, slots=True)
class ClaimedTaskGateCallbackIntent:
    """One secret-free callback delivery leased by trusted PostgreSQL code."""

    event_id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    manifest_hash: str
    gate_decision: TaskGateVerdict
    claim_token: UUID
    dispatch_revision: int
    dispatch_attempts: int
    claim_expires_at: datetime
    created_at: datetime

    @classmethod
    def from_row(cls, row: DictRow) -> ClaimedTaskGateCallbackIntent:
        """Validate the explicit dispatcher projection."""

        return cls(
            event_id=row["event_id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            task_run_id=row["task_run_id"],
            manifest_hash=row["manifest_hash"],
            gate_decision=TaskGateVerdict(row["gate_decision"]),
            claim_token=row["claim_token"],
            dispatch_revision=row["dispatch_revision"],
            dispatch_attempts=row["dispatch_attempts"],
            claim_expires_at=row["claim_expires_at"],
            created_at=row["created_at"],
        )


class TaskGateCallbackIntentRepository:
    """Call only callback-specific owner functions as ``atlas_dispatcher``."""

    async def claim(
        self,
        connection: AsyncConnection[DictRow],
        *,
        claimed_by: str,
        limit: int,
        lease_duration: timedelta,
    ) -> tuple[ClaimedTaskGateCallbackIntent, ...]:
        lease_seconds = _whole_seconds(lease_duration, field="callback lease duration")
        cursor = await connection.execute(
            """
            select *
            from atlas.claim_task_gate_callback_intents(%s, %s, %s)
            """,
            (claimed_by, limit, lease_seconds),
        )
        return tuple(ClaimedTaskGateCallbackIntent.from_row(row) for row in await cursor.fetchall())

    async def mark_delivered(
        self,
        connection: AsyncConnection[DictRow],
        *,
        event_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
        response_status_code: int,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.mark_task_gate_callback_delivered(
              %s, %s, %s, %s
            ) as applied
            """,
            (
                event_id,
                claim_token,
                dispatch_revision,
                response_status_code,
            ),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def retry(
        self,
        connection: AsyncConnection[DictRow],
        *,
        event_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
        error_code: str,
        response_status_code: int | None,
        retry_delay: timedelta,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.retry_task_gate_callback_intent(
              %s, %s, %s, %s, %s, %s
            ) as applied
            """,
            (
                event_id,
                claim_token,
                dispatch_revision,
                error_code,
                response_status_code,
                _retry_milliseconds(retry_delay),
            ),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])

    async def fail(
        self,
        connection: AsyncConnection[DictRow],
        *,
        event_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
        error_code: str,
        response_status_code: int | None,
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.fail_task_gate_callback_intent(
              %s, %s, %s, %s, %s
            ) as applied
            """,
            (
                event_id,
                claim_token,
                dispatch_revision,
                error_code,
                response_status_code,
            ),
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


__all__ = [
    "ClaimedTaskGateCallbackIntent",
    "TaskGateCallbackIntentRepository",
]
