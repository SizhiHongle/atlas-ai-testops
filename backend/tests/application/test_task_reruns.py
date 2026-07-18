"""Application tests for immutable child TaskRuns over infra-failed Units."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.infrastructure.test_task_run_repository import NOW, _aggregate, uid

from atlas_testops.application.access import ActorContext
from atlas_testops.application.task_reruns import TaskRunRerunService
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.task import (
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    RequestTaskRunInfraFailureRerun,
    TaskMaterializationState,
    TaskRun,
    TaskRunManifest,
    TaskRunRerunSelectionMode,
    UnitAttempt,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    TaskRunCreateResult,
    TaskRunRepository,
)


class StubCursor:
    async def fetchone(self) -> dict[str, object]:
        return {"now": NOW + timedelta(hours=1)}


class StubConnection:
    async def execute(self, _query: object) -> StubCursor:
        return StubCursor()


class RecordingDatabase:
    def __init__(self) -> None:
        self.contexts: list[DatabaseContext] = []

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        yield cast(AsyncConnection[DictRow], StubConnection())


class RecordingTaskRepository:
    def __init__(
        self,
        *,
        replay: bool = False,
        include_infra_failure: bool = True,
    ) -> None:
        run, self.manifest, units, self.attempts = _aggregate()
        self.run = run.model_copy(
            update={
                "materialization_state": TaskMaterializationState.SEALED,
                "materialized_unit_count": len(units),
                "materialized_first_attempt_count": len(units),
                "materialization_sealed_at": NOW,
                "lifecycle": ExecutionLifecycle.CLOSED,
                "quality": ExecutionQuality.INCONCLUSIVE,
                "started_at": NOW,
                "finalized_at": NOW,
                "closed_at": NOW,
                "revision": 7,
            }
        )
        resolved_units = []
        for index, unit in enumerate(units):
            quality = (
                ExecutionQuality.INFRA_ERROR
                if index == 0 and include_infra_failure
                else ExecutionQuality.FAILED
            )
            resolved_units.append(
                unit.model_copy(
                    update={
                        "lifecycle": ExecutionLifecycle.CLOSED,
                        "quality": quality,
                        "started_at": NOW,
                        "finalized_at": NOW,
                        "closed_at": NOW,
                    }
                )
            )
        self.units = tuple(resolved_units)
        self.replay = replay
        self.created: dict[str, object] | None = None
        self.events: list[object] = []

    async def get_run_for_update(
        self,
        _connection: AsyncConnection[DictRow],
        task_run_id: object,
    ) -> TaskRun | None:
        return self.run if task_run_id == self.run.id else None

    async def get_manifest(
        self,
        _connection: AsyncConnection[DictRow],
        _task_run_id: object,
    ) -> object:
        return self.manifest

    async def list_units(
        self,
        _connection: AsyncConnection[DictRow],
        _task_run_id: object,
    ) -> tuple[object, ...]:
        return self.units

    async def list_first_attempts(
        self,
        _connection: AsyncConnection[DictRow],
        _task_run_id: object,
    ) -> tuple[object, ...]:
        return self.attempts

    async def create_run(
        self,
        _connection: AsyncConnection[DictRow],
        **kwargs: object,
    ) -> TaskRunCreateResult:
        self.created = kwargs
        requested = cast(TaskRun, kwargs["task_run"])
        manifest = kwargs["manifest"]
        units = cast(tuple[object, ...], kwargs["units"])
        stored = requested.model_copy(
            update={
                "materialization_state": TaskMaterializationState.SEALED,
                "materialized_unit_count": len(units),
                "materialized_first_attempt_count": len(units),
                "materialization_sealed_at": requested.created_at,
                "revision": 2,
            }
        )
        return TaskRunCreateResult(
            kind=(
                ImmutableCreateKind.EXISTING
                if self.replay
                else ImmutableCreateKind.CREATED
            ),
            task_run=stored,
            manifest=manifest,  # type: ignore[arg-type]
        )

    async def append_event(
        self,
        _connection: AsyncConnection[DictRow],
        event: object,
    ) -> None:
        self.events.append(event)


class RecordingStateRepository:
    async def next_task_execution_event_seq(
        self,
        _connection: AsyncConnection[DictRow],
        *,
        task_run_id: object,
    ) -> int:
        assert task_run_id is not None
        return 1


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def append(self, _connection: object, event: object = None, **kwargs: Any) -> None:
        self.calls.append(kwargs or {"event": event})


def _service(
    repository: RecordingTaskRepository,
) -> tuple[TaskRunRerunService, RecordingDatabase, RecordingSink, RecordingSink]:
    database = RecordingDatabase()
    audit = RecordingSink()
    outbox = RecordingSink()
    return (
        TaskRunRerunService(
            cast(Database, database),
            task_repository=cast(TaskRunRepository, repository),
            state_repository=cast(
                TaskExecutionStateRepository,
                RecordingStateRepository(),
            ),
            audit_repository=cast(AuditRepository, audit),
            outbox_repository=cast(OutboxRepository, outbox),
        ),
        database,
        audit,
        outbox,
    )


def _actor(repository: RecordingTaskRepository, *, allowed: bool = True) -> ActorContext:
    return ActorContext(
        tenant_id=repository.run.tenant_id,
        actor_id=uid(6),
        request_id="task-rerun-test",
        development_override=allowed,
    )


@pytest.mark.anyio
async def test_rerun_materializes_only_infra_failed_units_and_records_creation() -> None:
    repository = RecordingTaskRepository()
    service, database, audit, outbox = _service(repository)
    request = RequestTaskRunInfraFailureRerun(
        client_mutation_id="infra-rerun-001"
    )

    result = await service.rerun_infrastructure_failures(
        _actor(repository),
        repository.run.id,
        request,
        expected_revision=repository.run.revision,
        idempotency_key=request.client_mutation_id,
    )

    assert result.status_code == 201
    assert result.replayed is False
    assert result.value.rerun_of_task_run_id == repository.run.id
    assert (
        result.value.rerun_selection_mode
        is TaskRunRerunSelectionMode.INFRA_FAILURES
    )
    assert repository.created is not None
    child_manifest = cast(TaskRunManifest, repository.created["manifest"])
    child_units = cast(tuple[ExecutionUnit, ...], repository.created["units"])
    child_attempts = cast(
        tuple[UnitAttempt, ...],
        repository.created["first_attempts"],
    )
    assert len(child_units) == len(child_attempts) == 1
    assert child_manifest.units[0].unit_key == repository.units[0].unit_key
    assert child_manifest.units[0].ordinal == 1
    assert child_attempts[0].execution_deadline - child_attempts[0].queued_at == (
        repository.attempts[0].execution_deadline
        - repository.attempts[0].queued_at
    )
    assert len(repository.events) == len(audit.calls) == len(outbox.calls) == 1
    assert database.contexts[0].tenant_id == repository.run.tenant_id


@pytest.mark.anyio
async def test_exact_rerun_replay_does_not_duplicate_events() -> None:
    repository = RecordingTaskRepository(replay=True)
    service, _database, audit, outbox = _service(repository)
    request = RequestTaskRunInfraFailureRerun(
        client_mutation_id="infra-rerun-002"
    )

    result = await service.rerun_infrastructure_failures(
        _actor(repository),
        repository.run.id,
        request,
        expected_revision=repository.run.revision,
        idempotency_key=request.client_mutation_id,
    )

    assert result.status_code == 200
    assert result.replayed is True
    assert repository.events == []
    assert audit.calls == outbox.calls == []


@pytest.mark.anyio
async def test_rerun_rejects_missing_infra_failures_and_header_mismatch() -> None:
    repository = RecordingTaskRepository(include_infra_failure=False)
    service, _database, _audit, _outbox = _service(repository)
    request = RequestTaskRunInfraFailureRerun(
        client_mutation_id="infra-rerun-003"
    )

    with pytest.raises(ApplicationError) as mismatch:
        await service.rerun_infrastructure_failures(
            _actor(repository),
            repository.run.id,
            request,
            expected_revision=repository.run.revision,
            idempotency_key="different-key",
        )
    assert mismatch.value.error_code is ErrorCode.INVALID_REQUEST

    with pytest.raises(ApplicationError) as empty:
        await service.rerun_infrastructure_failures(
            _actor(repository),
            repository.run.id,
            request,
            expected_revision=repository.run.revision,
            idempotency_key=request.client_mutation_id,
        )
    assert empty.value.error_code is ErrorCode.CONFLICT


@pytest.mark.anyio
async def test_rerun_requires_exact_revision_closed_source_and_operator() -> None:
    repository = RecordingTaskRepository()
    service, _database, _audit, _outbox = _service(repository)
    request = RequestTaskRunInfraFailureRerun(
        client_mutation_id="infra-rerun-004"
    )

    with pytest.raises(ApplicationError) as revision:
        await service.rerun_infrastructure_failures(
            _actor(repository),
            repository.run.id,
            request,
            expected_revision=repository.run.revision - 1,
            idempotency_key=request.client_mutation_id,
        )
    assert revision.value.error_code is ErrorCode.PRECONDITION_FAILED

    with pytest.raises(ApplicationError) as forbidden:
        await service.rerun_infrastructure_failures(
            _actor(repository, allowed=False),
            repository.run.id,
            request,
            expected_revision=repository.run.revision,
            idempotency_key=request.client_mutation_id,
        )
    assert forbidden.value.error_code is ErrorCode.NOT_FOUND

    repository.run = repository.run.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "quality": ExecutionQuality.PENDING,
            "finalized_at": None,
            "closed_at": None,
        }
    )
    with pytest.raises(ApplicationError) as running:
        await service.rerun_infrastructure_failures(
            _actor(repository),
            repository.run.id,
            request,
            expected_revision=repository.run.revision,
            idempotency_key=request.client_mutation_id,
        )
    assert running.value.error_code is ErrorCode.CONFLICT
