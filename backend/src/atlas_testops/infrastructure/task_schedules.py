"""Dispatcher-only access to fenced Task Schedule synchronization functions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.task import (
    TaskRetryPolicy,
    TaskScheduleCalendar,
    TaskScheduleCatchupPolicy,
    TaskScheduleOverlapPolicy,
    TaskScheduleStatus,
)


@dataclass(frozen=True, slots=True)
class ClaimedTaskScheduleSyncIntent:
    """One exact desired Schedule revision claimed through a narrow SQL function."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_schedule_id: UUID
    schedule_revision: int
    action: str
    content_digest: str
    temporal_namespace: str
    temporal_schedule_id: str
    claim_token: UUID
    dispatch_revision: int
    dispatch_attempts: int
    claim_expires_at: datetime
    desired_status: TaskScheduleStatus
    task_plan_version_id: UUID
    calendar: TaskScheduleCalendar
    time_zone_name: str
    overlap_policy: TaskScheduleOverlapPolicy
    catchup_policy: TaskScheduleCatchupPolicy
    catchup_window_seconds: int
    jitter_seconds: int
    iteration_id: str | None
    retry_policy: TaskRetryPolicy
    created_by: UUID

    @classmethod
    def from_row(cls, row: DictRow) -> ClaimedTaskScheduleSyncIntent:
        """Validate nested wire contracts before any Temporal operation."""

        return cls(
            id=row["id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            task_schedule_id=row["task_schedule_id"],
            schedule_revision=row["schedule_revision"],
            action=row["action"],
            content_digest=row["content_digest"],
            temporal_namespace=row["temporal_namespace"],
            temporal_schedule_id=row["temporal_schedule_id"],
            claim_token=row["claim_token"],
            dispatch_revision=row["dispatch_revision"],
            dispatch_attempts=row["dispatch_attempts"],
            claim_expires_at=row["claim_expires_at"],
            desired_status=TaskScheduleStatus(row["desired_status"]),
            task_plan_version_id=row["task_plan_version_id"],
            calendar=TaskScheduleCalendar.model_validate(row["calendar"]),
            time_zone_name=row["time_zone_name"],
            overlap_policy=TaskScheduleOverlapPolicy(row["overlap_policy"]),
            catchup_policy=TaskScheduleCatchupPolicy(row["catchup_policy"]),
            catchup_window_seconds=row["catchup_window_seconds"],
            jitter_seconds=row["jitter_seconds"],
            iteration_id=row["iteration_id"],
            retry_policy=TaskRetryPolicy.model_validate(row["retry_policy"]),
            created_by=row["created_by"],
        )


class TaskScheduleSyncIntentRepository:
    """Call only the dispatcher role's Schedule functions."""

    async def claim(
        self,
        connection: AsyncConnection[DictRow],
        *,
        claimed_by: str,
        namespace: str,
        limit: int,
        lease_duration: timedelta,
    ) -> tuple[ClaimedTaskScheduleSyncIntent, ...]:
        lease_seconds = _whole_seconds(lease_duration)
        cursor = await connection.execute(
            """
            select *
            from atlas.claim_task_schedule_sync_intents(%s, %s, %s, %s)
            """,
            (claimed_by, namespace, limit, lease_seconds),
        )
        return tuple(ClaimedTaskScheduleSyncIntent.from_row(row) for row in await cursor.fetchall())

    async def mark_applied(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        claim_token: UUID,
        dispatch_revision: int,
        next_fire_times: tuple[datetime, ...],
    ) -> bool:
        cursor = await connection.execute(
            """
            select atlas.mark_task_schedule_sync_applied(
              %s, %s, %s, %s
            ) as applied
            """,
            (
                intent_id,
                claim_token,
                dispatch_revision,
                list(next_fire_times),
            ),
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
        cursor = await connection.execute(
            """
            select atlas.retry_task_schedule_sync_intent(
              %s, %s, %s, %s, %s
            ) as applied
            """,
            (
                intent_id,
                claim_token,
                dispatch_revision,
                error_code,
                _retry_milliseconds(retry_delay),
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
            select atlas.fail_task_schedule_sync_intent(
              %s, %s, %s, %s
            ) as applied
            """,
            (intent_id, claim_token, dispatch_revision, error_code),
        )
        row = await cursor.fetchone()
        return bool(row is not None and row["applied"])


def _whole_seconds(value: timedelta) -> int:
    seconds = value.total_seconds()
    if not 5 <= seconds <= 300 or not seconds.is_integer():
        raise ValueError("Schedule sync lease must be 5-300 whole seconds")
    return int(seconds)


def _retry_milliseconds(value: timedelta) -> int:
    milliseconds = ceil(value.total_seconds() * 1_000)
    if not 100 <= milliseconds <= 3_600_000:
        raise ValueError("retry delay must be between 100 and 3600000 milliseconds")
    return milliseconds


__all__ = [
    "ClaimedTaskScheduleSyncIntent",
    "TaskScheduleSyncIntentRepository",
]
