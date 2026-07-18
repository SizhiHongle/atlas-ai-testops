"""Application tests for bounded, authorized TaskRun queries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.infrastructure.test_task_run_repository import NOW, _aggregate, _event, uid

from atlas_testops.application.access import ActorContext
from atlas_testops.application.task_runs import TaskRunQueryService
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.task import (
    ExecutionUnit,
    TaskExecutionEvent,
    TaskRun,
    TaskRunManifest,
    UnitAttempt,
    unit_attempt_workflow_id,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository


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


class RecordingTaskRunRepository:
    def __init__(
        self,
        *,
        runs: tuple[TaskRun, ...],
        manifest: TaskRunManifest | None,
        units: tuple[ExecutionUnit, ...],
        attempts: tuple[UnitAttempt, ...],
        events: tuple[TaskExecutionEvent, ...],
    ) -> None:
        self.runs = runs
        self.manifest = manifest
        self.units = units
        self.attempts = attempts
        self.events = events
        self.list_run_calls: list[tuple[UUID, TimeCursor | None, int]] = []
        self.unit_page_calls: list[tuple[UUID, int, int]] = []
        self.attempt_page_calls: list[tuple[UUID, int, int]] = []
        self.event_page_calls: list[tuple[UUID, int, int]] = []

    async def list_runs(
        self,
        _connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[TaskRun, ...]:
        self.list_run_calls.append((project_id, cursor, limit))
        return self.runs[:limit]

    async def get_run(
        self,
        _connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> TaskRun | None:
        return next((run for run in self.runs if run.id == task_run_id), None)

    async def get_manifest(
        self,
        _connection: AsyncConnection[DictRow],
        _task_run_id: UUID,
    ) -> TaskRunManifest | None:
        return self.manifest

    async def get_unit(
        self,
        _connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
    ) -> ExecutionUnit | None:
        return next((unit for unit in self.units if unit.id == execution_unit_id), None)

    async def list_units_page(
        self,
        _connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        after_ordinal: int,
        limit: int,
    ) -> tuple[ExecutionUnit, ...]:
        self.unit_page_calls.append((task_run_id, after_ordinal, limit))
        return tuple(unit for unit in self.units if unit.ordinal > after_ordinal)[:limit]

    async def list_attempts_page(
        self,
        _connection: AsyncConnection[DictRow],
        *,
        execution_unit_id: UUID,
        after_attempt_number: int,
        limit: int,
    ) -> tuple[UnitAttempt, ...]:
        self.attempt_page_calls.append(
            (execution_unit_id, after_attempt_number, limit)
        )
        return tuple(
            attempt
            for attempt in self.attempts
            if attempt.execution_unit_id == execution_unit_id
            and attempt.attempt_number > after_attempt_number
        )[:limit]

    async def list_events(
        self,
        _connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        after_seq: int,
        limit: int,
    ) -> tuple[TaskExecutionEvent, ...]:
        self.event_page_calls.append((task_run_id, after_seq, limit))
        return tuple(event for event in self.events if event.seq > after_seq)[:limit]


class RecordingPlatformRepository:
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id
        self.calls: list[UUID] = []

    async def get_project(
        self,
        _connection: AsyncConnection[DictRow],
        project_id: UUID,
    ) -> object | None:
        self.calls.append(project_id)
        return object() if project_id == self.project_id else None


def _service(
    repository: RecordingTaskRunRepository,
) -> tuple[TaskRunQueryService, RecordingDatabase]:
    database = RecordingDatabase()
    project_id = repository.runs[0].project_id
    service = TaskRunQueryService(
        cast(Database, database),
        task_run_repository=cast(TaskRunRepository, repository),
        platform_repository=cast(
            PlatformRepository,
            RecordingPlatformRepository(project_id),
        ),
    )
    return service, database


def _actor(*, visible: bool = True) -> ActorContext:
    return ActorContext(
        tenant_id=uid(1),
        actor_id=uid(6),
        request_id="task-run-query-test",
        development_override=visible,
    )


def _repository() -> tuple[RecordingTaskRunRepository, TaskRun, tuple[ExecutionUnit, ...]]:
    run, manifest, units, attempts = _aggregate()
    later_run, _, _, _ = _aggregate(task_run_id=uid(101))
    later_run = later_run.model_copy(
        update={
            "requested_at": NOW - timedelta(minutes=1),
            "queued_at": NOW - timedelta(minutes=1),
            "created_at": NOW - timedelta(minutes=1),
            "updated_at": NOW - timedelta(minutes=1),
        }
    )
    second_attempt = attempts[0].model_copy(
        update={
            "id": uid(999),
            "attempt_number": 2,
            "temporal_workflow_id": unit_attempt_workflow_id(
                tenant_id=run.tenant_id,
                unit_attempt_id=uid(999),
            ),
        }
    )
    first_event = _event(run)
    second_event = first_event.model_copy(update={"id": uid(901), "seq": 2})
    return (
        RecordingTaskRunRepository(
            runs=(run, later_run),
            manifest=manifest,
            units=units,
            attempts=(attempts[0], second_attempt, attempts[1]),
            events=(first_event, second_event),
        ),
        run,
        units,
    )


@pytest.mark.anyio
async def test_queries_return_bounded_pages_and_parent_scoped_details() -> None:
    repository, run, units = _repository()
    service, database = _service(repository)
    actor = _actor()

    run_page = await service.list_for_project(
        actor,
        run.project_id,
        cursor=None,
        limit=1,
    )
    assert run_page.items == (run,)
    assert run_page.next_cursor is not None
    assert repository.list_run_calls == [(run.project_id, None, 2)]
    assert await service.get(actor, run.id) == run
    assert await service.get_manifest(actor, run.id) == repository.manifest

    unit_page = await service.list_units(actor, run.id, after_ordinal=0, limit=1)
    assert unit_page.items == (units[0],)
    assert unit_page.next_after_ordinal == units[0].ordinal
    assert repository.unit_page_calls == [(run.id, 0, 2)]

    attempt_page = await service.list_attempts(
        actor,
        run.id,
        units[0].id,
        after_attempt_number=0,
        limit=1,
    )
    assert attempt_page.items[0].attempt_number == 1
    assert attempt_page.next_after_attempt_number == 1
    assert repository.attempt_page_calls == [(units[0].id, 0, 2)]

    event_page = await service.list_events(actor, run.id, after_seq=0, limit=1)
    assert event_page.items[0].seq == 1
    assert event_page.next_after_seq == 1
    assert repository.event_page_calls == [(run.id, 0, 2)]
    assert len(database.contexts) == 6
    assert all(context.tenant_id == actor.tenant_id for context in database.contexts)


@pytest.mark.anyio
async def test_queries_reject_invisible_or_inconsistent_resources() -> None:
    repository, run, units = _repository()
    service, database = _service(repository)

    with pytest.raises(ApplicationError) as forbidden:
        await service.list_for_project(
            _actor(visible=False),
            run.project_id,
            cursor=None,
            limit=25,
        )
    assert forbidden.value.error_code is ErrorCode.NOT_FOUND
    assert len(database.contexts) == 1

    with pytest.raises(ApplicationError) as hidden:
        await service.get(_actor(visible=False), run.id)
    assert hidden.value.error_code is ErrorCode.NOT_FOUND

    repository.manifest = None
    with pytest.raises(RuntimeError, match="missing its immutable manifest"):
        await service.get_manifest(_actor(), run.id)

    foreign_unit = units[0].model_copy(update={"task_run_id": uid(5000)})
    repository.units = (foreign_unit,)
    with pytest.raises(ApplicationError) as wrong_parent:
        await service.list_attempts(
            _actor(),
            run.id,
            foreign_unit.id,
            after_attempt_number=0,
            limit=25,
        )
    assert wrong_parent.value.error_code is ErrorCode.NOT_FOUND


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [
        ("bad-limit", ErrorCode.INVALID_REQUEST),
        ("bad-after", ErrorCode.INVALID_REQUEST),
        ("bad-cursor", ErrorCode.INVALID_REQUEST),
    ],
)
async def test_query_pagination_validation(
    operation: str,
    expected_code: ErrorCode,
) -> None:
    repository, run, _units = _repository()
    service, _database = _service(repository)

    with pytest.raises(ApplicationError) as raised:
        if operation == "bad-limit":
            await service.list_for_project(
                _actor(), run.project_id, cursor=None, limit=101
            )
        elif operation == "bad-after":
            await service.list_events(_actor(), run.id, after_seq=-1, limit=25)
        else:
            await service.list_for_project(
                _actor(), run.project_id, cursor="broken", limit=25
            )
    assert raised.value.error_code is expected_code
