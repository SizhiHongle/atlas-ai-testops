"""Application tests for Task Schedule desired state and durable sync facts."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.application.test_task_launches import _retry_policy, _version
from tests.infrastructure.test_task_run_repository import NOW, uid

from atlas_testops.application.access import ActorContext
from atlas_testops.application.task_schedules import TaskScheduleService
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.task import (
    CreateTaskSchedule,
    RequestTaskSchedulePause,
    RequestTaskScheduleResume,
    TaskSchedule,
    TaskScheduleCalendar,
    TaskScheduleStatus,
    TaskScheduleSyncStatus,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    IdempotencyReservation,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository
from atlas_testops.infrastructure.repositories.task_schedules import (
    TaskScheduleRepository,
)

ACTOR_ID = uid(6)


class ClockCursor:
    async def fetchone(self) -> dict[str, object]:
        return {"observed_at": NOW}


class ClockConnection:
    async def execute(
        self,
        _query: str,
        _parameters: object = None,
    ) -> ClockCursor:
        return ClockCursor()


class RecordingDatabase:
    def __init__(self) -> None:
        self.contexts: list[DatabaseContext] = []

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        yield cast(AsyncConnection[DictRow], ClockConnection())


class RecordingTaskRepository:
    def __init__(self) -> None:
        self.version = _version()

    async def get_task_plan_version(
        self,
        _connection: object,
        task_plan_version_id: UUID,
    ) -> object | None:
        return self.version if task_plan_version_id == self.version.id else None


class RecordingScheduleRepository:
    def __init__(self) -> None:
        self.schedules: list[TaskSchedule] = []
        self.intents: list[tuple[str, int]] = []
        self.list_calls: list[tuple[UUID, TimeCursor | None, int]] = []

    async def create(
        self,
        _connection: object,
        schedule: TaskSchedule,
    ) -> TaskSchedule:
        self.schedules.append(schedule)
        return schedule

    async def insert_sync_intent(
        self,
        _connection: object,
        *,
        schedule: TaskSchedule,
        action: str,
        **_values: object,
    ) -> None:
        self.intents.append((action, schedule.revision))

    async def get(
        self,
        _connection: object,
        schedule_id: UUID,
        *,
        for_update: bool = False,
    ) -> TaskSchedule | None:
        del for_update
        return next(
            (schedule for schedule in self.schedules if schedule.id == schedule_id),
            None,
        )

    async def list_for_version(
        self,
        _connection: object,
        *,
        task_plan_version_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[TaskSchedule, ...]:
        self.list_calls.append((task_plan_version_id, cursor, limit))
        return tuple(
            schedule
            for schedule in self.schedules
            if schedule.task_plan_version_id == task_plan_version_id
        )[:limit]

    async def transition_status(
        self,
        _connection: object,
        *,
        schedule_id: UUID,
        expected_revision: int,
        old_status: TaskScheduleStatus,
        new_status: TaskScheduleStatus,
        pause_reason: str | None,
        updated_by: UUID,
        **_values: object,
    ) -> TaskSchedule | None:
        current = await self.get(object(), schedule_id)
        if (
            current is None
            or current.revision != expected_revision
            or current.status is not old_status
        ):
            return None
        updated = current.model_copy(
            update={
                "status": new_status,
                "pause_reason": pause_reason,
                "sync_status": TaskScheduleSyncStatus.PENDING,
                "last_sync_error_code": None,
                "updated_by": updated_by,
                "revision": current.revision + 1,
            }
        )
        self.schedules[self.schedules.index(current)] = updated
        return updated


class RecordingIdempotencyRepository:
    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], CachedHttpResponse] = {}

    async def reserve(
        self,
        _connection: object,
        *,
        scope: str,
        key: str,
        **_values: object,
    ) -> IdempotencyReservation:
        response = self.responses.get((scope, key))
        return IdempotencyReservation(
            acquired=response is None,
            cached_response=response,
        )

    async def complete(
        self,
        _connection: object,
        *,
        scope: str,
        key: str,
        response: CachedHttpResponse,
        **_values: object,
    ) -> None:
        self.responses[(scope, key)] = response


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def append(
        self,
        _connection: object,
        event: object = None,
        **values: Any,
    ) -> None:
        self.calls.append(values or {"event": event})


def actor(*, allowed: bool = True) -> ActorContext:
    return ActorContext(
        tenant_id=uid(1),
        actor_id=ACTOR_ID,
        request_id="task-schedule-test",
        development_override=allowed,
    )


def command() -> CreateTaskSchedule:
    return CreateTaskSchedule(
        schedule_key="crm.nightly",
        name="CRM nightly",
        calendar=TaskScheduleCalendar(minutes=(0,), hours=(2,)),
        time_zone_name="Asia/Shanghai",
        retry_policy=_retry_policy(),
        client_mutation_id="schedule-create-001",
    )


def service_fixture() -> tuple[
    TaskScheduleService,
    RecordingTaskRepository,
    RecordingScheduleRepository,
    RecordingIdempotencyRepository,
    RecordingSink,
    RecordingSink,
]:
    tasks = RecordingTaskRepository()
    schedules = RecordingScheduleRepository()
    idempotency = RecordingIdempotencyRepository()
    audit = RecordingSink()
    outbox = RecordingSink()
    service = TaskScheduleService(
        cast(Database, RecordingDatabase()),
        temporal_namespace="atlas-task",
        schedule_repository=cast(TaskScheduleRepository, schedules),
        task_repository=cast(TaskRunRepository, tasks),
        audit_repository=cast(AuditRepository, audit),
        outbox_repository=cast(OutboxRepository, outbox),
        idempotency_repository=cast(IdempotencyRepository, idempotency),
    )
    return service, tasks, schedules, idempotency, audit, outbox


@pytest.mark.anyio
async def test_create_replay_read_and_list_schedule() -> None:
    service, tasks, schedules, _idempotency, audit, outbox = service_fixture()
    created = await service.create(
        actor(),
        tasks.version.id,
        command(),
        idempotency_key=command().client_mutation_id,
    )
    replayed = await service.create(
        actor(),
        tasks.version.id,
        command(),
        idempotency_key=command().client_mutation_id,
    )
    loaded = await service.get(actor(), created.value.id)
    page = await service.list_for_version(
        actor(),
        tasks.version.id,
        cursor=None,
        limit=25,
    )

    assert created.status_code == 201
    assert created.value.status is TaskScheduleStatus.ACTIVE
    assert created.value.sync_status is TaskScheduleSyncStatus.PENDING
    assert len(created.value.next_fire_times_utc) == 5
    assert schedules.intents == [("CREATE", 1)]
    assert replayed.replayed is True
    assert replayed.value == created.value
    assert loaded == created.value
    assert page.items == (created.value,)
    assert schedules.list_calls == [(tasks.version.id, None, 26)]
    assert len(audit.calls) == 1
    assert len(outbox.calls) == 1


@pytest.mark.anyio
async def test_pause_and_resume_append_revisioned_sync_intents() -> None:
    service, tasks, schedules, _idempotency, audit, _outbox = service_fixture()
    created = (
        await service.create(
            actor(),
            tasks.version.id,
            command(),
            idempotency_key=command().client_mutation_id,
        )
    ).value
    pause = RequestTaskSchedulePause(
        client_mutation_id="schedule-pause-001",
        reason="维护窗口",
    )
    paused = await service.pause(
        actor(),
        created.id,
        pause,
        expected_revision=1,
        idempotency_key=pause.client_mutation_id,
    )
    resume = RequestTaskScheduleResume(
        client_mutation_id="schedule-resume-001",
        reason="维护完成",
    )
    resumed = await service.resume(
        actor(),
        created.id,
        resume,
        expected_revision=2,
        idempotency_key=resume.client_mutation_id,
    )

    assert paused.status_code == 202
    assert paused.value.status is TaskScheduleStatus.PAUSED
    assert paused.value.pause_reason == "维护窗口"
    assert resumed.value.status is TaskScheduleStatus.ACTIVE
    assert resumed.value.pause_reason is None
    assert schedules.intents == [
        ("CREATE", 1),
        ("PAUSE", 2),
        ("RESUME", 3),
    ]
    assert len(audit.calls) == 3


@pytest.mark.anyio
async def test_schedule_rejects_bad_key_permission_revision_and_state() -> None:
    service, tasks, _schedules, _idempotency, _audit, _outbox = service_fixture()
    with pytest.raises(ApplicationError) as key_error:
        await service.create(
            actor(),
            tasks.version.id,
            command(),
            idempotency_key="different-key",
        )
    assert key_error.value.error_code is ErrorCode.INVALID_REQUEST

    with pytest.raises(ApplicationError) as permission_error:
        await service.create(
            actor(allowed=False),
            tasks.version.id,
            command(),
            idempotency_key=command().client_mutation_id,
        )
    assert permission_error.value.error_code is ErrorCode.NOT_FOUND

    created = (
        await service.create(
            actor(),
            tasks.version.id,
            command(),
            idempotency_key=command().client_mutation_id,
        )
    ).value
    pause = RequestTaskSchedulePause(
        client_mutation_id="schedule-pause-002",
        reason="维护窗口",
    )
    with pytest.raises(ApplicationError) as revision_error:
        await service.pause(
            actor(),
            created.id,
            pause,
            expected_revision=99,
            idempotency_key=pause.client_mutation_id,
        )
    assert revision_error.value.error_code is ErrorCode.PRECONDITION_FAILED

    resume = RequestTaskScheduleResume(
        client_mutation_id="schedule-resume-002",
        reason="错误状态",
    )
    with pytest.raises(ApplicationError) as state_error:
        await service.resume(
            actor(),
            created.id,
            resume,
            expected_revision=1,
            idempotency_key=resume.client_mutation_id,
        )
    assert state_error.value.error_code is ErrorCode.CONFLICT
