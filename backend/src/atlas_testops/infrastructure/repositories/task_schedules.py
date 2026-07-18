"""PostgreSQL repository for Task Schedule desired state and sync intents."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.task import (
    TaskSchedule,
    TaskScheduleStatus,
)

TASK_SCHEDULE_COLUMNS = (
    "id, tenant_id, project_id, task_plan_version_id, schema_version, "
    "schedule_key, name, calendar, time_zone_name, overlap_policy, "
    "catchup_policy, catchup_window_seconds, jitter_seconds, iteration_id, "
    "retry_policy, temporal_namespace, temporal_schedule_id, content_digest, "
    "status, pause_reason, sync_status, synced_revision, last_sync_error_code, "
    "next_fire_times_utc, created_by, updated_by, revision, created_at, updated_at"
)


class TaskScheduleRepository:
    """Persist immutable definitions and narrow pause/resume desired-state changes."""

    async def create(
        self,
        connection: AsyncConnection[DictRow],
        schedule: TaskSchedule,
    ) -> TaskSchedule:
        """Insert one database-validated Schedule definition."""

        cursor = await connection.execute(
            f"""
            insert into atlas.task_schedule (
              id, tenant_id, project_id, task_plan_version_id, schema_version,
              schedule_key, name, calendar, time_zone_name, overlap_policy,
              catchup_policy, catchup_window_seconds, jitter_seconds,
              iteration_id, retry_policy, temporal_namespace,
              temporal_schedule_id, content_digest, status, pause_reason,
              sync_status, synced_revision, last_sync_error_code,
              next_fire_times_utc, created_by, updated_by, revision,
              created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s
            )
            returning {TASK_SCHEDULE_COLUMNS}
            """,
            (
                schedule.id,
                schedule.tenant_id,
                schedule.project_id,
                schedule.task_plan_version_id,
                schedule.schema_version,
                schedule.schedule_key,
                schedule.name,
                Jsonb(schedule.calendar.model_dump(mode="json", by_alias=True)),
                schedule.time_zone_name,
                schedule.overlap_policy.value,
                schedule.catchup_policy.value,
                schedule.catchup_window_seconds,
                schedule.jitter_seconds,
                schedule.iteration_id,
                Jsonb(schedule.retry_policy.model_dump(mode="json", by_alias=True)),
                schedule.temporal_namespace,
                schedule.temporal_schedule_id,
                schedule.content_digest,
                schedule.status.value,
                schedule.pause_reason,
                schedule.sync_status.value,
                schedule.synced_revision,
                schedule.last_sync_error_code,
                list(schedule.next_fire_times_utc),
                schedule.created_by,
                schedule.updated_by,
                schedule.revision,
                schedule.created_at,
                schedule.updated_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Task Schedule insert did not return a row")
        return TaskSchedule.model_validate(row)

    async def insert_sync_intent(
        self,
        connection: AsyncConnection[DictRow],
        *,
        intent_id: UUID,
        schedule: TaskSchedule,
        action: str,
        created_at: datetime,
    ) -> None:
        """Append one desired-revision intent in the same business transaction."""

        await connection.execute(
            """
            insert into atlas.task_schedule_sync_intent (
              id, tenant_id, project_id, task_schedule_id, schedule_revision,
              action, content_digest, temporal_namespace, temporal_schedule_id,
              available_at, created_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s
            )
            """,
            (
                intent_id,
                schedule.tenant_id,
                schedule.project_id,
                schedule.id,
                schedule.revision,
                action,
                schedule.content_digest,
                schedule.temporal_namespace,
                schedule.temporal_schedule_id,
                created_at,
                created_at,
            ),
        )

    async def get(
        self,
        connection: AsyncConnection[DictRow],
        schedule_id: UUID,
        *,
        for_update: bool = False,
    ) -> TaskSchedule | None:
        """Load one RLS-scoped Schedule, optionally locking desired state."""

        lock = "for update" if for_update else ""
        cursor = await connection.execute(
            f"""
            select {TASK_SCHEDULE_COLUMNS}
            from atlas.task_schedule
            where id = %s
            {lock}
            """,
            (schedule_id,),
        )
        row = await cursor.fetchone()
        return TaskSchedule.model_validate(row) if row is not None else None

    async def list_for_version(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_plan_version_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[TaskSchedule, ...]:
        """List one immutable Plan Version's schedules by latest desired change."""

        cursor_filter = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (task_plan_version_id, limit)
        else:
            cursor_filter = "and (updated_at, id) < (%s, %s)"
            parameters = (
                task_plan_version_id,
                cursor.created_at,
                cursor.id,
                limit,
            )
        result = await connection.execute(
            f"""
            select {TASK_SCHEDULE_COLUMNS}
            from atlas.task_schedule
            where task_plan_version_id = %s
            {cursor_filter}
            order by updated_at desc, id desc
            limit %s
            """,
            parameters,
        )
        return tuple(TaskSchedule.model_validate(row) for row in await result.fetchall())

    async def transition_status(
        self,
        connection: AsyncConnection[DictRow],
        *,
        schedule_id: UUID,
        expected_revision: int,
        old_status: TaskScheduleStatus,
        new_status: TaskScheduleStatus,
        pause_reason: str | None,
        updated_by: UUID,
        updated_at: datetime,
    ) -> TaskSchedule | None:
        """Apply exact Revision CAS; the database guard owns transition validity."""

        cursor = await connection.execute(
            f"""
            update atlas.task_schedule
            set status = %s,
                pause_reason = %s,
                sync_status = 'PENDING',
                last_sync_error_code = null,
                updated_by = %s,
                revision = revision + 1,
                updated_at = %s
            where id = %s
              and revision = %s
              and status = %s
            returning {TASK_SCHEDULE_COLUMNS}
            """,
            (
                new_status.value,
                pause_reason,
                updated_by,
                updated_at,
                schedule_id,
                expected_revision,
                old_status.value,
            ),
        )
        row = await cursor.fetchone()
        return TaskSchedule.model_validate(row) if row is not None else None


__all__ = ["TASK_SCHEDULE_COLUMNS", "TaskScheduleRepository"]
