"""Application tests for atomic TaskRun Cancel command acceptance."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.errors import SerializationFailure
from psycopg.rows import DictRow
from tests.infrastructure.test_task_run_repository import NOW, _aggregate, _sealed_run, uid

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.task_commands import TaskRunCommandService
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.task import (
    ExecutionLifecycle,
    ExecutionQuality,
    RequestTaskRunCancel,
    RequestTaskRunPause,
    RequestTaskRunResume,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskRun,
    TaskRunCommandIntent,
    TaskRunCommandStatus,
    TaskRunCommandType,
    task_run_command_digest,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableCreateResult,
    TaskRunRepository,
)
from atlas_testops.infrastructure.task_commands import TaskRunCommandRepository


class _Cursor:
    def __init__(self, row: DictRow | None) -> None:
        self.row = row

    async def fetchone(self) -> DictRow | None:
        return self.row


class _Connection:
    async def execute(
        self,
        statement: str,
        _params: Sequence[object] | None = None,
    ) -> _Cursor:
        assert "transaction_timestamp" in statement
        return _Cursor(cast(DictRow, {"now": NOW + timedelta(seconds=1)}))


class _Database:
    def __init__(self) -> None:
        self.contexts: list[DatabaseContext] = []

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        yield cast(AsyncConnection[DictRow], _Connection())


class _Tasks:
    def __init__(self, run: TaskRun) -> None:
        self.run = run
        self.events: list[TaskExecutionEvent] = []
        self.raise_serialization_once = False

    async def get_run_for_update(self, _connection: object, run_id: UUID) -> TaskRun | None:
        if self.raise_serialization_once:
            self.raise_serialization_once = False
            raise SerializationFailure("retry")
        return self.run if self.run.id == run_id else None

    async def get_run(self, _connection: object, run_id: UUID) -> TaskRun | None:
        return self.run if self.run.id == run_id else None

    async def append_event(self, _connection: object, event: TaskExecutionEvent) -> None:
        self.events.append(event)


class _State:
    def __init__(self, tasks: _Tasks) -> None:
        self.tasks = tasks
        self.return_none = False

    async def transition_task_run_state(
        self,
        _connection: object,
        **values: Any,
    ) -> TaskRun | None:
        if self.return_none:
            return None
        current = self.tasks.run
        updated = current.model_copy(
            update={
                "lifecycle": values["lifecycle"],
                "quality": values["quality"],
                "hygiene": values["hygiene"],
                "started_at": values["started_at"],
                "finalized_at": values["finalized_at"],
                "cleanup_resolved_at": values["cleanup_resolved_at"],
                "closed_at": values["closed_at"],
                "revision": current.revision + 1,
                "updated_at": NOW + timedelta(seconds=1),
            }
        )
        self.tasks.run = updated
        return updated

    async def next_task_execution_event_seq(
        self,
        _connection: object,
        **_values: object,
    ) -> int:
        return len(self.tasks.events) + 1


class _Commands:
    def __init__(self) -> None:
        self.commands: list[TaskRunCommandIntent] = []
        self.superseded: list[tuple[UUID, UUID]] = []

    async def get_by_mutation(
        self,
        _connection: object,
        *,
        task_run_id: UUID,
        client_mutation_id: str,
    ) -> TaskRunCommandIntent | None:
        return next(
            (
                command
                for command in self.commands
                if command.task_run_id == task_run_id
                and command.client_mutation_id == client_mutation_id
            ),
            None,
        )

    async def get(
        self,
        _connection: object,
        command_id: UUID,
    ) -> TaskRunCommandIntent | None:
        return next((item for item in self.commands if item.id == command_id), None)

    async def create(
        self,
        _connection: object,
        command: TaskRunCommandIntent,
    ) -> ImmutableCreateResult[TaskRunCommandIntent]:
        self.commands.append(command)
        return ImmutableCreateResult(ImmutableCreateKind.CREATED, command)

    async def get_open_for_run(
        self,
        _connection: object,
        *,
        task_run_id: UUID,
    ) -> TaskRunCommandIntent | None:
        return next(
            (
                command
                for command in self.commands
                if command.task_run_id == task_run_id
                and command.command_type
                in {TaskRunCommandType.PAUSE, TaskRunCommandType.RESUME}
                and command.status
                in {
                    TaskRunCommandStatus.PENDING,
                    TaskRunCommandStatus.DELIVERED,
                }
            ),
            None,
        )

    async def supersede_for_cancel(
        self,
        _connection: object,
        *,
        task_run_id: UUID,
        cancel_command_id: UUID,
    ) -> int:
        self.superseded.append((task_run_id, cancel_command_id))
        superseded_at = NOW + timedelta(seconds=2)
        affected = 0
        for index, command in enumerate(self.commands):
            if (
                command.task_run_id == task_run_id
                and command.id != cancel_command_id
                and command.command_type
                in {TaskRunCommandType.PAUSE, TaskRunCommandType.RESUME}
                and command.status
                in {
                    TaskRunCommandStatus.PENDING,
                    TaskRunCommandStatus.DELIVERED,
                }
            ):
                self.commands[index] = command.model_copy(
                    update={
                        "status": TaskRunCommandStatus.SUPERSEDED,
                        "superseded_at": superseded_at,
                        "superseded_by_command_id": cancel_command_id,
                        "updated_at": superseded_at,
                    }
                )
                affected += 1
        return affected


class _Audit:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def append(self, _connection: object, **values: object) -> None:
        self.calls.append(values)


class _Outbox:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def append(self, _connection: object, event: object) -> None:
        self.events.append(event)


def _actor(*, override: bool = True, role: PlatformRole | None = None) -> ActorContext:
    grants = (
        ()
        if role is None
        else (
            AccessGrant(
                role=role,
                project_id=uid(2),
            ),
        )
    )
    return ActorContext(
        tenant_id=uid(1),
        actor_id=uid(6),
        request_id="task-command-test",
        grants=grants,
        development_override=override,
    )


def _fixture(
    run: TaskRun | None = None,
) -> tuple[
    TaskRunCommandService,
    _Database,
    _Tasks,
    _State,
    _Commands,
    _Audit,
    _Outbox,
]:
    if run is None:
        aggregate_run, _, units, _ = _aggregate()
        run = _sealed_run(aggregate_run, unit_count=len(units))
    database = _Database()
    tasks = _Tasks(run)
    state = _State(tasks)
    commands = _Commands()
    audit = _Audit()
    outbox = _Outbox()
    service = TaskRunCommandService(
        cast(Database, database),
        task_repository=cast(TaskRunRepository, tasks),
        state_repository=cast(TaskExecutionStateRepository, state),
        command_repository=cast(TaskRunCommandRepository, commands),
        audit_repository=cast(AuditRepository, audit),
        outbox_repository=cast(OutboxRepository, outbox),
    )
    return service, database, tasks, state, commands, audit, outbox


@pytest.mark.anyio
async def test_cancel_atomically_moves_run_and_records_exact_command_events() -> None:
    service, database, tasks, _, commands, audit, outbox = _fixture()
    expected_revision = tasks.run.revision
    request = RequestTaskRunCancel(client_mutation_id="cancel-command-001")

    result = await service.cancel(
        _actor(),
        tasks.run.id,
        request,
        expected_revision=expected_revision,
        idempotency_key=request.client_mutation_id,
    )

    assert result.status_code == 202
    assert result.replayed is False
    assert cast(Any, tasks.run.lifecycle) is ExecutionLifecycle.CANCELING
    assert tasks.run.revision == expected_revision + 1
    assert result.value == commands.commands[0]
    assert result.value.command_digest == task_run_command_digest(
        tenant_id=tasks.run.tenant_id,
        project_id=tasks.run.project_id,
        task_run_id=tasks.run.id,
        command_type=result.value.command_type,
        client_mutation_id=request.client_mutation_id,
        expected_run_revision=expected_revision,
        request_digest=cast(str, tasks.run.request_digest),
        manifest_hash=tasks.run.manifest_hash,
        temporal_namespace=cast(str, tasks.run.temporal_namespace),
        temporal_workflow_id=cast(str, tasks.run.temporal_workflow_id),
    )
    assert tasks.events[0].event_type == "task_run.cancel_requested"
    assert tasks.events[0].payload["commandId"] == str(result.value.id)
    assert audit.calls[0]["event_type"] == "task_run.cancel_requested"
    assert len(outbox.events) == 1
    assert database.contexts[0].actor_id == uid(6)

    replay = await service.cancel(
        _actor(),
        tasks.run.id,
        request,
        expected_revision=expected_revision,
        idempotency_key=request.client_mutation_id,
    )
    assert replay.replayed is True
    assert replay.value == result.value
    assert len(tasks.events) == len(audit.calls) == len(outbox.events) == 1
    assert await service.get(
        _actor(),
        task_run_id=tasks.run.id,
        command_id=result.value.id,
    ) == result.value


@pytest.mark.anyio
async def test_cancel_rejects_header_revision_authority_and_run_state_conflicts() -> None:
    service, _, tasks, _, _, _, _ = _fixture()
    request = RequestTaskRunCancel(client_mutation_id="cancel-command-001")

    with pytest.raises(ApplicationError) as header_mismatch:
        await service.cancel(
            _actor(),
            tasks.run.id,
            request,
            expected_revision=tasks.run.revision,
            idempotency_key="another-command-001",
        )
    assert header_mismatch.value.error_code is ErrorCode.INVALID_REQUEST

    with pytest.raises(ApplicationError) as stale:
        await service.cancel(
            _actor(),
            tasks.run.id,
            request,
            expected_revision=tasks.run.revision + 1,
            idempotency_key=request.client_mutation_id,
        )
    assert stale.value.error_code is ErrorCode.PRECONDITION_FAILED
    assert stale.value.headers["ETag"] == f'"revision-{tasks.run.revision}"'

    with pytest.raises(ApplicationError) as hidden:
        await service.cancel(
            _actor(override=False),
            tasks.run.id,
            request,
            expected_revision=tasks.run.revision,
            idempotency_key=request.client_mutation_id,
        )
    assert hidden.value.error_code is ErrorCode.NOT_FOUND

    with pytest.raises(ApplicationError) as forbidden:
        await service.cancel(
            _actor(override=False, role=PlatformRole.CASE_AUTHOR),
            tasks.run.id,
            request,
            expected_revision=tasks.run.revision,
            idempotency_key=request.client_mutation_id,
        )
    assert forbidden.value.error_code is ErrorCode.FORBIDDEN

    for update in (
        {"materialization_state": TaskMaterializationState.MATERIALIZING},
        {"lifecycle": ExecutionLifecycle.CLOSED},
        {"quality": ExecutionQuality.FAILED},
    ):
        conflicting_run = tasks.run.model_copy(update=update)
        conflicting, _, conflict_tasks, _, _, _, _ = _fixture(conflicting_run)
        with pytest.raises(ApplicationError) as conflict:
            await conflicting.cancel(
                _actor(),
                conflict_tasks.run.id,
                request,
                expected_revision=conflict_tasks.run.revision,
                idempotency_key=request.client_mutation_id,
            )
        assert conflict.value.error_code is ErrorCode.CONFLICT


@pytest.mark.anyio
async def test_cancel_replay_conflict_and_serialization_recovery_are_fail_closed() -> None:
    service, _, tasks, state, commands, _, _ = _fixture()
    request = RequestTaskRunCancel(client_mutation_id="cancel-command-001")
    expected_revision = tasks.run.revision
    accepted = await service.cancel(
        _actor(),
        tasks.run.id,
        request,
        expected_revision=expected_revision,
        idempotency_key=request.client_mutation_id,
    )

    with pytest.raises(ApplicationError) as conflict:
        await service.cancel(
            _actor(),
            tasks.run.id,
            request,
            expected_revision=expected_revision + 1,
            idempotency_key=request.client_mutation_id,
        )
    assert conflict.value.error_code is ErrorCode.CONFLICT

    tasks.raise_serialization_once = True
    recovered = await service.cancel(
        _actor(),
        tasks.run.id,
        request,
        expected_revision=expected_revision,
        idempotency_key=request.client_mutation_id,
    )
    assert recovered.replayed is True
    assert recovered.value == accepted.value

    commands.commands.clear()
    tasks.raise_serialization_once = True
    with pytest.raises(ApplicationError) as lost_race:
        await service.cancel(
            _actor(),
            tasks.run.id,
            RequestTaskRunCancel(client_mutation_id="cancel-command-002"),
            expected_revision=expected_revision,
            idempotency_key="cancel-command-002",
        )
    assert lost_race.value.error_code is ErrorCode.PRECONDITION_FAILED

    fresh_service, _, fresh_tasks, fresh_state, _, _, _ = _fixture()
    fresh_state.return_none = True
    with pytest.raises(RuntimeError, match="transition returned no row"):
        await fresh_service.cancel(
            _actor(),
            fresh_tasks.run.id,
            request,
            expected_revision=fresh_tasks.run.revision,
            idempotency_key=request.client_mutation_id,
        )
    assert state.return_none is False


@pytest.mark.anyio
async def test_command_status_hides_missing_wrong_parent_and_invisible_run() -> None:
    service, _, tasks, _, _, _, _ = _fixture()
    with pytest.raises(ApplicationError) as missing:
        await service.get(
            _actor(),
            task_run_id=tasks.run.id,
            command_id=UUID(int=999),
        )
    assert missing.value.error_code is ErrorCode.NOT_FOUND

    with pytest.raises(ApplicationError) as hidden:
        await service.get(
            _actor(override=False),
            task_run_id=tasks.run.id,
            command_id=UUID(int=999),
        )
    assert hidden.value.error_code is ErrorCode.NOT_FOUND


@pytest.mark.anyio
async def test_pause_resume_and_cancel_supersession_preserve_exact_state_boundaries() -> None:
    aggregate_run, _, units, _ = _aggregate()
    sealed = _sealed_run(aggregate_run, unit_count=len(units))
    running = sealed.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": NOW,
            "updated_at": NOW,
        }
    )
    service, _, tasks, _, commands, audit, outbox = _fixture(running)
    pause_request = RequestTaskRunPause(client_mutation_id="pause-command-001")

    paused_requested = await service.pause(
        _actor(),
        tasks.run.id,
        pause_request,
        expected_revision=tasks.run.revision,
        idempotency_key=pause_request.client_mutation_id,
    )

    assert tasks.run.lifecycle is ExecutionLifecycle.PAUSE_REQUESTED
    assert paused_requested.value.command_type is TaskRunCommandType.PAUSE
    assert tasks.events[-1].event_type == "task_run.pause_requested"
    assert audit.calls[-1]["event_type"] == "task_run.pause_requested"
    assert len(outbox.events) == 1

    cancel_request = RequestTaskRunCancel(client_mutation_id="cancel-command-002")
    canceled = await service.cancel(
        _actor(),
        tasks.run.id,
        cancel_request,
        expected_revision=tasks.run.revision,
        idempotency_key=cancel_request.client_mutation_id,
    )

    assert cast(Any, tasks.run.lifecycle) is ExecutionLifecycle.CANCELING
    assert commands.superseded == [(tasks.run.id, canceled.value.id)]
    assert commands.commands[0].status is TaskRunCommandStatus.SUPERSEDED
    assert commands.commands[0].superseded_by_command_id == canceled.value.id

    paused = running.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.PAUSED,
            "revision": running.revision + 2,
            "updated_at": NOW + timedelta(seconds=2),
        }
    )
    resume_service, _, resume_tasks, _, resume_commands, _, _ = _fixture(paused)
    resume_request = RequestTaskRunResume(client_mutation_id="resume-command-001")
    resumed = await resume_service.resume(
        _actor(),
        resume_tasks.run.id,
        resume_request,
        expected_revision=resume_tasks.run.revision,
        idempotency_key=resume_request.client_mutation_id,
    )

    assert resume_tasks.run.lifecycle is ExecutionLifecycle.PAUSED
    assert resume_tasks.run.revision == paused.revision + 1
    assert resumed.value.command_type is TaskRunCommandType.RESUME
    assert resume_commands.commands == [resumed.value]
    assert resume_tasks.events[-1].event_type == "task_run.resume_requested"
