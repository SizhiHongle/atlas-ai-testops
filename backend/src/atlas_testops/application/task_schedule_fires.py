"""Trusted Temporal Schedule fire bridge into the unified TaskRun compiler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.task_launches import TaskPlanLaunchService
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.task import (
    ScheduleTaskRunTrigger,
    TaskScheduleStatus,
    TriggerTaskPlanVersionRun,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_schedules import (
    TaskScheduleRepository,
)


class TaskScheduleFireStatus(StrEnum):
    """Safe result of one nominal Temporal Schedule action."""

    CREATED = "CREATED"
    REPLAYED = "REPLAYED"
    SKIPPED_PAUSED = "SKIPPED_PAUSED"


@dataclass(frozen=True, slots=True)
class TaskScheduleFireRequest:
    """Secret-free identity emitted only by the registered Schedule Workflow."""

    tenant_id: UUID
    project_id: UUID
    task_schedule_id: UUID
    content_digest: str
    scheduled_fire_time_utc: datetime
    workflow_started_at_utc: datetime


@dataclass(frozen=True, slots=True)
class TaskScheduleFireResult:
    """Workflow-safe result without embedding a TaskRun document."""

    status: TaskScheduleFireStatus
    task_run_id: UUID | None
    scheduled_fire_time_utc: datetime


class TaskScheduleFireInvariantError(RuntimeError):
    """Schedule identity or desired state no longer authorizes this fire."""


class TaskScheduleFireService:
    """Revalidate database authority, then reuse the exact E3 launch chain."""

    def __init__(
        self,
        database: Database,
        *,
        temporal_namespace: str,
        schedule_repository: TaskScheduleRepository | None = None,
        launch_service: TaskPlanLaunchService | None = None,
    ) -> None:
        self._database = database
        self._schedules = schedule_repository or TaskScheduleRepository()
        self._launches = launch_service or TaskPlanLaunchService(
            database,
            temporal_namespace=temporal_namespace,
        )

    async def fire(
        self,
        request: TaskScheduleFireRequest,
    ) -> TaskScheduleFireResult:
        """Launch one permanent scheduleId + nominal UTC fire identity."""

        fire_time = request.scheduled_fire_time_utc
        if fire_time.tzinfo is None:
            raise TaskScheduleFireInvariantError("Schedule fire time is not aware")
        fire_time = fire_time.astimezone(UTC)
        workflow_started_at = request.workflow_started_at_utc
        if workflow_started_at.tzinfo is None:
            raise TaskScheduleFireInvariantError("Schedule Workflow start time is not aware")
        workflow_started_at = workflow_started_at.astimezone(UTC)
        async with self._database.transaction(
            DatabaseContext(
                tenant_id=request.tenant_id,
                actor_id=None,
                request_id=f"task-schedule-fire-read:{request.task_schedule_id}",
            )
        ) as connection:
            schedule = await self._schedules.get(
                connection,
                request.task_schedule_id,
            )
        if (
            schedule is None
            or schedule.tenant_id != request.tenant_id
            or schedule.project_id != request.project_id
            or schedule.content_digest != request.content_digest
        ):
            raise TaskScheduleFireInvariantError(
                "Schedule fire identity does not match database authority"
            )
        if (
            schedule.status is TaskScheduleStatus.PAUSED
            and workflow_started_at > schedule.updated_at
        ):
            return TaskScheduleFireResult(
                status=TaskScheduleFireStatus.SKIPPED_PAUSED,
                task_run_id=None,
                scheduled_fire_time_utc=fire_time,
            )

        client_mutation_id = (
            f"schedule-fire:{schedule.id.hex}:{int(fire_time.timestamp() * 1_000_000)}"
        )
        actor = ActorContext(
            tenant_id=schedule.tenant_id,
            actor_id=schedule.created_by,
            request_id=client_mutation_id,
            current_project_id=schedule.project_id,
            grants=(
                AccessGrant(
                    role=PlatformRole.RUN_OPERATOR,
                    project_id=schedule.project_id,
                ),
            ),
        )
        command = TriggerTaskPlanVersionRun(
            task_plan_version_id=schedule.task_plan_version_id,
            client_mutation_id=client_mutation_id,
            trigger=ScheduleTaskRunTrigger(
                schedule_id=str(schedule.id),
                scheduled_fire_time_utc=fire_time,
            ),
            iteration_id=schedule.iteration_id,
            retry_policy=schedule.retry_policy,
        )
        result = await self._launches.trigger(
            actor,
            command,
            idempotency_key=client_mutation_id,
        )
        return TaskScheduleFireResult(
            status=(
                TaskScheduleFireStatus.REPLAYED
                if result.replayed
                else TaskScheduleFireStatus.CREATED
            ),
            task_run_id=result.value.id,
            scheduled_fire_time_utc=fire_time,
        )


__all__ = [
    "TaskScheduleFireInvariantError",
    "TaskScheduleFireRequest",
    "TaskScheduleFireResult",
    "TaskScheduleFireService",
    "TaskScheduleFireStatus",
]
