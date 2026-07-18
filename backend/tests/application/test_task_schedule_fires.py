"""Database-authoritative Schedule fire bridge tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.domain.task.test_schedules import schedule_payload, uid

from atlas_testops.application.task_launches import TaskPlanLaunchService
from atlas_testops.application.task_schedule_fires import (
    TaskScheduleFireRequest,
    TaskScheduleFireService,
    TaskScheduleFireStatus,
)
from atlas_testops.domain.task import (
    ScheduleTaskRunTrigger,
    TaskSchedule,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_schedules import (
    TaskScheduleRepository,
)

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


class RecordingDatabase:
    def __init__(self) -> None:
        self.contexts: list[DatabaseContext] = []

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        yield cast(AsyncConnection[DictRow], object())


class ScheduleRepository:
    def __init__(self, schedule: TaskSchedule) -> None:
        self.schedule = schedule

    async def get(self, *_args: object, **_kwargs: object) -> TaskSchedule:
        return self.schedule


class LaunchService:
    def __init__(self, *, replayed: bool = False) -> None:
        self.replayed = replayed
        self.calls: list[tuple[Any, Any, str]] = []

    async def trigger(
        self,
        actor: Any,
        command: Any,
        *,
        idempotency_key: str,
    ) -> Any:
        self.calls.append((actor, command, idempotency_key))
        return SimpleNamespace(
            replayed=self.replayed,
            value=SimpleNamespace(id=uid(20)),
        )


def schedule(*, paused: bool = False) -> TaskSchedule:
    payload = schedule_payload()
    if paused:
        payload.update(
            {
                "status": "PAUSED",
                "pauseReason": "维护窗口",
                "syncStatus": "PENDING",
                "syncedRevision": 1,
                "revision": 2,
                "updatedAt": NOW.isoformat(),
            }
        )
    return TaskSchedule.model_validate(payload)


def request(
    stored: TaskSchedule,
    *,
    workflow_started_at: datetime = NOW + timedelta(seconds=1),
) -> TaskScheduleFireRequest:
    return TaskScheduleFireRequest(
        tenant_id=stored.tenant_id,
        project_id=stored.project_id,
        task_schedule_id=stored.id,
        content_digest=stored.content_digest,
        scheduled_fire_time_utc=NOW,
        workflow_started_at_utc=workflow_started_at,
    )


@pytest.mark.anyio
async def test_fire_uses_unified_schedule_trigger_and_exact_fingerprint() -> None:
    stored = schedule()
    database = RecordingDatabase()
    launches = LaunchService()
    service = TaskScheduleFireService(
        cast(Database, database),
        temporal_namespace="atlas-task",
        schedule_repository=cast(
            TaskScheduleRepository,
            ScheduleRepository(stored),
        ),
        launch_service=cast(TaskPlanLaunchService, launches),
    )

    result = await service.fire(request(stored))
    replayed = await TaskScheduleFireService(
        cast(Database, database),
        temporal_namespace="atlas-task",
        schedule_repository=cast(
            TaskScheduleRepository,
            ScheduleRepository(stored),
        ),
        launch_service=cast(TaskPlanLaunchService, LaunchService(replayed=True)),
    ).fire(request(stored))

    assert result.status is TaskScheduleFireStatus.CREATED
    assert replayed.status is TaskScheduleFireStatus.REPLAYED
    actor, command, idempotency_key = launches.calls[0]
    assert actor.tenant_id == stored.tenant_id
    assert actor.current_project_id == stored.project_id
    assert command.task_plan_version_id == stored.task_plan_version_id
    assert isinstance(command.trigger, ScheduleTaskRunTrigger)
    assert command.trigger.schedule_id == str(stored.id)
    assert command.trigger.scheduled_fire_time_utc == NOW
    assert idempotency_key.startswith(f"schedule-fire:{stored.id.hex}:")
    assert database.contexts[0].actor_id is None


@pytest.mark.anyio
async def test_pause_skips_only_workflows_started_after_pause() -> None:
    stored = schedule(paused=True)
    after_pause = LaunchService()
    service = TaskScheduleFireService(
        cast(Database, RecordingDatabase()),
        temporal_namespace="atlas-task",
        schedule_repository=cast(
            TaskScheduleRepository,
            ScheduleRepository(stored),
        ),
        launch_service=cast(TaskPlanLaunchService, after_pause),
    )

    skipped = await service.fire(request(stored, workflow_started_at=NOW + timedelta(seconds=1)))
    assert skipped.status is TaskScheduleFireStatus.SKIPPED_PAUSED
    assert skipped.task_run_id is None
    assert after_pause.calls == []

    before_pause = LaunchService()
    service = TaskScheduleFireService(
        cast(Database, RecordingDatabase()),
        temporal_namespace="atlas-task",
        schedule_repository=cast(
            TaskScheduleRepository,
            ScheduleRepository(stored),
        ),
        launch_service=cast(TaskPlanLaunchService, before_pause),
    )
    launched = await service.fire(request(stored, workflow_started_at=NOW - timedelta(seconds=1)))
    assert launched.status is TaskScheduleFireStatus.CREATED
    assert len(before_pause.calls) == 1
