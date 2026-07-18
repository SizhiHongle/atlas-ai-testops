"""Unit tests for the database-backed Task orchestration authority."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.application.test_task_execution import (
    _admission_fixture,
)
from tests.application.test_task_execution import (
    _attempt as _admission_attempt,
)
from tests.application.test_task_execution import (
    _run as _admission_run,
)
from tests.infrastructure.test_task_run_repository import NOW, _aggregate, _sealed_run

from atlas_testops.application.task_execution import TaskAdmissionSnapshot
from atlas_testops.application.task_orchestration import TaskWorkerService
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.result import (
    AttemptEventChain,
    AttemptSeal,
    AttemptSealContent,
    AttemptSealSignature,
    DataHygiene,
    EvidenceCompleteness,
    EvidenceIntegrity,
    ExecutionInfluence,
    OutcomeClass,
    ResultRef,
    Stability,
    Verdict,
    attempt_seal_content_hash,
)
from atlas_testops.domain.task import (
    TASK_RUN_MANIFEST_SCHEMA_VERSION,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    TaskExecutionEvent,
    TaskRetryPolicy,
    TaskRun,
    TaskRunCommandIntent,
    TaskRunCommandStatus,
    TaskRunCommandType,
    TaskRunManifest,
    TaskUnitExecutionTicket,
    UnitAttempt,
    task_retry_policy_digest,
    task_run_command_digest,
    task_run_manifest_hash,
    unit_attempt_workflow_id,
    unit_retry_attempt_id,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableCreateResult,
)
from atlas_testops.infrastructure.task_commands import TaskRunCommandRepository
from atlas_testops.orchestration.task_intents import TaskRunWorkflowInput
from atlas_testops.orchestration.tasks import (
    TASK_RUN_COMMAND_SIGNAL_SCHEMA,
    TASK_WORKFLOW_RESULT_SCHEMA,
    TaskAttemptBatchSettleInput,
    TaskAttemptExecutionPayload,
    TaskAttemptFinishInput,
    TaskAttemptWorkflowPayload,
    TaskBatchPrepareInput,
    TaskRunCommandSignal,
    TaskRunFinishInput,
    TaskRunWorkflowPayload,
    UnitAttemptWorkflowInput,
)


class _ClockCursor:
    def __init__(self, observed_at: object) -> None:
        self._observed_at = observed_at

    async def fetchone(self) -> DictRow:
        return cast(DictRow, {"observed_at": self._observed_at})


class _ClockConnection:
    def __init__(self, observed_at: object) -> None:
        self.observed_at = observed_at
        self.calls: list[tuple[str, Sequence[object] | None]] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> _ClockCursor:
        self.calls.append((query, params))
        assert "transaction_timestamp" in query
        return _ClockCursor(self.observed_at)


class _Database:
    def __init__(self, observed_at: object) -> None:
        self.connection = _ClockConnection(observed_at)
        self.contexts: list[DatabaseContext] = []
        self.active_transactions = 0

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        self.active_transactions += 1
        try:
            yield cast(AsyncConnection[DictRow], self.connection)
        finally:
            self.active_transactions -= 1


class _TaskRepository:
    def __init__(
        self,
        run: TaskRun,
        manifest: TaskRunManifest,
        units: tuple[ExecutionUnit, ...],
        attempts: tuple[UnitAttempt, ...],
    ) -> None:
        self.run = run
        self.manifest = manifest
        self.units = list(units)
        self.attempts = list(attempts)
        self.events: list[TaskExecutionEvent] = []
        self.list_event_calls: list[tuple[int, int]] = []
        self.trace: list[str] = []

    async def get_run(self, _connection: object, run_id: object) -> TaskRun | None:
        return self.run if self.run.id == run_id else None

    async def get_manifest(
        self,
        _connection: object,
        run_id: object,
    ) -> TaskRunManifest | None:
        return self.manifest if self.manifest.task_run_id == run_id else None

    async def list_units(
        self,
        _connection: object,
        run_id: object,
    ) -> tuple[ExecutionUnit, ...]:
        return tuple(unit for unit in self.units if unit.task_run_id == run_id)

    async def list_first_attempts(
        self,
        _connection: object,
        run_id: object,
    ) -> tuple[UnitAttempt, ...]:
        return tuple(
            attempt
            for attempt in self.attempts
            if attempt.task_run_id == run_id and attempt.attempt_number == 1
        )

    async def list_attempts_for_run(
        self,
        _connection: object,
        run_id: object,
    ) -> tuple[UnitAttempt, ...]:
        return tuple(attempt for attempt in self.attempts if attempt.task_run_id == run_id)

    async def list_attempts(
        self,
        _connection: object,
        execution_unit_id: object,
    ) -> tuple[UnitAttempt, ...]:
        return tuple(
            sorted(
                (
                    attempt
                    for attempt in self.attempts
                    if attempt.execution_unit_id == execution_unit_id
                ),
                key=lambda attempt: attempt.attempt_number,
            )
        )

    async def get_attempt_by_number(
        self,
        _connection: object,
        *,
        execution_unit_id: object,
        attempt_number: int,
    ) -> UnitAttempt | None:
        return next(
            (
                attempt
                for attempt in self.attempts
                if attempt.execution_unit_id == execution_unit_id
                and attempt.attempt_number == attempt_number
            ),
            None,
        )

    async def count_retry_attempts(
        self,
        _connection: object,
        run_id: object,
    ) -> int:
        return sum(
            attempt.task_run_id == run_id and attempt.attempt_number > 1
            for attempt in self.attempts
        )

    async def create_attempt(
        self,
        _connection: object,
        attempt: UnitAttempt,
    ) -> ImmutableCreateResult[UnitAttempt]:
        existing = await self.get_attempt_by_number(
            _connection,
            execution_unit_id=attempt.execution_unit_id,
            attempt_number=attempt.attempt_number,
        )
        if existing is not None:
            return ImmutableCreateResult(ImmutableCreateKind.EXISTING, existing)
        self.attempts.append(attempt)
        return ImmutableCreateResult(ImmutableCreateKind.CREATED, attempt)

    async def get_run_for_update(
        self,
        _connection: object,
        run_id: object,
    ) -> TaskRun | None:
        self.trace.append("lock_run")
        return await self.get_run(_connection, run_id)

    async def get_unit_for_update(
        self,
        _connection: object,
        unit_id: object,
    ) -> ExecutionUnit | None:
        self.trace.append("lock_unit")
        return next((unit for unit in self.units if unit.id == unit_id), None)

    async def get_attempt_for_update(
        self,
        _connection: object,
        attempt_id: object,
    ) -> UnitAttempt | None:
        self.trace.append("lock_attempt")
        return next((item for item in self.attempts if item.id == attempt_id), None)

    async def append_event(self, _connection: object, event: TaskExecutionEvent) -> None:
        self.trace.append(f"event:{event.event_type}")
        self.events.append(event)

    async def list_events(
        self,
        _connection: object,
        *,
        task_run_id: object,
        after_seq: int,
        limit: int,
    ) -> tuple[TaskExecutionEvent, ...]:
        self.list_event_calls.append((after_seq, limit))
        return tuple(
            event
            for event in sorted(self.events, key=lambda item: item.seq)
            if event.task_run_id == task_run_id and event.seq > after_seq
        )[:limit]

    def replace_run(self, run: TaskRun) -> None:
        self.run = run

    def replace_unit(self, unit: ExecutionUnit) -> None:
        self.units = [unit if item.id == unit.id else item for item in self.units]

    def replace_attempt(self, attempt: UnitAttempt) -> None:
        self.attempts = [attempt if item.id == attempt.id else item for item in self.attempts]


class _StateRepository:
    def __init__(self, tasks: _TaskRepository) -> None:
        self.tasks = tasks
        self.calls: list[str] = []
        self._next_seq = 0

    async def next_task_execution_event_seq(self, *_args: object, **_kwargs: object) -> int:
        self._next_seq = max((self._next_seq, *(event.seq for event in self.tasks.events)))
        self._next_seq += 1
        return self._next_seq

    async def transition_task_run_state(
        self,
        _connection: object,
        **values: Any,
    ) -> TaskRun:
        self.calls.append("run")
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
                "updated_at": NOW + timedelta(minutes=self._next_seq + 1),
            }
        )
        self.tasks.replace_run(updated)
        return updated

    async def transition_execution_unit_state(
        self,
        _connection: object,
        **values: Any,
    ) -> ExecutionUnit:
        self.calls.append("unit")
        current = next(item for item in self.tasks.units if item.id == values["execution_unit_id"])
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
                "updated_at": NOW + timedelta(minutes=self._next_seq + 1),
            }
        )
        self.tasks.replace_unit(updated)
        return updated

    async def transition_unit_attempt_state(
        self,
        _connection: object,
        **values: Any,
    ) -> UnitAttempt:
        self.calls.append("attempt")
        current = next(item for item in self.tasks.attempts if item.id == values["unit_attempt_id"])
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
                "updated_at": NOW + timedelta(minutes=self._next_seq + 1),
            }
        )
        self.tasks.replace_attempt(updated)
        return updated


class _Admission:
    def __init__(
        self,
        error: ApplicationError | None = None,
        snapshot: TaskAdmissionSnapshot | None = None,
    ) -> None:
        self.error = error
        self.snapshot = snapshot
        self.calls = 0

    async def admit_loaded_unit_in_transaction(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> TaskAdmissionSnapshot | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.snapshot


class _TicketRepository:
    def __init__(self) -> None:
        self.ticket: TaskUnitExecutionTicket | None = None
        self.create_calls = 0

    async def get_by_attempt(
        self,
        _connection: object,
        unit_attempt_id: object,
    ) -> TaskUnitExecutionTicket | None:
        if self.ticket is None or self.ticket.unit_attempt_id != unit_attempt_id:
            return None
        return self.ticket

    async def create(
        self,
        _connection: object,
        ticket: TaskUnitExecutionTicket,
    ) -> ImmutableCreateResult[TaskUnitExecutionTicket]:
        self.create_calls += 1
        if self.ticket is None:
            self.ticket = ticket
            return ImmutableCreateResult(ImmutableCreateKind.CREATED, ticket)
        return ImmutableCreateResult(ImmutableCreateKind.EXISTING, self.ticket)


class _CommandRepository:
    def __init__(
        self,
        *,
        applied: bool = True,
        command: TaskRunCommandIntent | None = None,
    ) -> None:
        self.applied = applied
        self.command = command
        self.calls: list[dict[str, object]] = []
        self.pause_calls: list[dict[str, object]] = []
        self.resume_calls: list[dict[str, object]] = []

    async def apply_cancel(self, _connection: object, **values: object) -> bool:
        self.calls.append(values)
        return self.applied

    async def get_open_for_run(
        self,
        _connection: object,
        *,
        task_run_id: UUID,
    ) -> TaskRunCommandIntent | None:
        if self.command is None or self.command.task_run_id != task_run_id:
            return None
        return self.command

    async def apply_pause(self, _connection: object, **values: object) -> bool:
        self.pause_calls.append(values)
        return self.applied

    async def apply_resume(self, _connection: object, **values: object) -> bool:
        self.resume_calls.append(values)
        return self.applied


class _ResultRepository:
    def __init__(
        self,
        *,
        seals: dict[UUID, AttemptSeal] | None = None,
        refs: dict[UUID, ResultRef] | None = None,
    ) -> None:
        self.seals = seals or {}
        self.refs = refs or {}

    async def get_seal_by_attempt(
        self,
        _connection: object,
        unit_attempt_id: UUID,
    ) -> AttemptSeal | None:
        return self.seals.get(unit_attempt_id)

    async def get_ref_by_attempt(
        self,
        _connection: object,
        unit_attempt_id: UUID,
    ) -> ResultRef | None:
        return self.refs.get(unit_attempt_id)


class _ResultProjection:
    def __init__(self) -> None:
        self.snapshot_calls: list[dict[str, object]] = []
        self.fully_resolved_snapshot_calls: list[dict[str, object]] = []

    async def close_without_seal(self, *_args: object, **_kwargs: object) -> object:
        return object()

    async def resolve_unit(self, *_args: object, **_kwargs: object) -> object:
        return object()

    async def snapshot_task(self, *_args: object, **_kwargs: object) -> object:
        self.snapshot_calls.append(_kwargs)
        return object()

    async def snapshot_task_fully_resolved(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        self.fully_resolved_snapshot_calls.append(_kwargs)
        return object()


def _fixture(
    *,
    unit_count: int = 2,
    observed_at: object = NOW + timedelta(minutes=1),
    command_repository: _CommandRepository | None = None,
    result_repository: _ResultRepository | None = None,
) -> tuple[TaskWorkerService, _Database, _TaskRepository, _StateRepository, _Admission]:
    raw_run, manifest, units, attempts = _aggregate(unit_count=unit_count)
    run = _sealed_run(raw_run, unit_count=unit_count)
    database = _Database(observed_at)
    tasks = _TaskRepository(run, manifest, units, attempts)
    state = _StateRepository(tasks)
    admission = _Admission()
    service = TaskWorkerService(
        cast(Database, database),
        task_repository=cast(Any, tasks),
        state_repository=cast(Any, state),
        admission_service=cast(Any, admission),
        result_repository=cast(Any, result_repository or _ResultRepository()),
        command_repository=cast(
            TaskRunCommandRepository,
            command_repository or _CommandRepository(),
        ),
        result_projection_service=cast(Any, _ResultProjection()),
    )
    return service, database, tasks, state, admission


def _root_request(run: TaskRun) -> TaskRunWorkflowInput:
    assert run.request_digest is not None
    return TaskRunWorkflowInput(
        tenant_id=str(run.tenant_id),
        project_id=str(run.project_id),
        task_run_id=str(run.id),
        request_digest=run.request_digest,
        manifest_hash=run.manifest_hash,
    )


def _enable_retry_policy(
    tasks: _TaskRepository,
    *,
    infra_retry_attempts: int = 2,
    max_total_infra_retries: int = 8,
) -> None:
    digest = task_retry_policy_digest(
        infra_retry_attempts=infra_retry_attempts,
        max_total_infra_retries=max_total_infra_retries,
        initial_backoff_seconds=5,
        maximum_backoff_seconds=60,
        jitter_percent=0,
    )
    policy = TaskRetryPolicy(
        infra_retry_attempts=infra_retry_attempts,
        max_total_infra_retries=max_total_infra_retries,
        initial_backoff_seconds=5,
        maximum_backoff_seconds=60,
        jitter_percent=0,
        content_digest=digest,
    )
    manifest = tasks.manifest
    policy_digests = {**manifest.policy_digests, "infra-retry": digest}
    manifest_hash = task_run_manifest_hash(
        task_run_id=manifest.task_run_id,
        task_plan_version_id=manifest.task_plan_version_id,
        trigger_source=manifest.trigger_source,
        trigger_fingerprint=manifest.trigger_fingerprint,
        tenant_id=manifest.tenant_id,
        project_id=manifest.project_id,
        iteration_id=manifest.iteration_id,
        units=manifest.units,
        policy_digests=policy_digests,
        compiler_version=manifest.compiler_version,
        schema_version=TASK_RUN_MANIFEST_SCHEMA_VERSION,
        retry_policy=policy,
    )
    tasks.manifest = TaskRunManifest(
        **manifest.model_dump(
            mode="python",
            exclude={
                "schema_version",
                "policy_digests",
                "retry_policy",
                "manifest_hash",
            },
        ),
        schema_version=TASK_RUN_MANIFEST_SCHEMA_VERSION,
        policy_digests=policy_digests,
        retry_policy=policy,
        manifest_hash=manifest_hash,
    )
    tasks.replace_run(
        tasks.run.model_copy(
            update={
                "manifest_hash": manifest_hash,
                "request_digest": tasks.manifest.recompute_request_digest(),
            }
        )
    )
    tasks.units = [unit.model_copy(update={"manifest_hash": manifest_hash}) for unit in tasks.units]
    tasks.attempts = [
        attempt.model_copy(update={"manifest_hash": manifest_hash}) for attempt in tasks.attempts
    ]


def _attempt_request(
    run: TaskRun,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
) -> UnitAttemptWorkflowInput:
    assert run.request_digest is not None
    return UnitAttemptWorkflowInput(
        tenant_id=str(run.tenant_id),
        project_id=str(run.project_id),
        task_run_id=str(run.id),
        request_digest=run.request_digest,
        manifest_hash=run.manifest_hash,
        ordinal=unit.ordinal,
        execution_unit_id=str(unit.id),
        unit_attempt_id=str(attempt.id),
        execution_deadline=attempt.execution_deadline.isoformat(),
        activity_timeout_seconds=300,
    )


def _running(projection: TaskRun | ExecutionUnit | UnitAttempt) -> Any:
    return projection.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": NOW + timedelta(minutes=1),
            "updated_at": NOW + timedelta(minutes=1),
        }
    )


def _control_command(
    run: TaskRun,
    command_type: TaskRunCommandType,
) -> TaskRunCommandIntent:
    assert run.request_digest is not None
    assert run.temporal_namespace is not None
    assert run.temporal_workflow_id is not None
    expected_revision = run.revision - 1
    mutation_id = f"{command_type.value.casefold()}-command-001"
    return TaskRunCommandIntent(
        id=uuid4(),
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        task_run_id=run.id,
        command_type=command_type,
        client_mutation_id=mutation_id,
        command_digest=task_run_command_digest(
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            command_type=command_type,
            client_mutation_id=mutation_id,
            expected_run_revision=expected_revision,
            request_digest=run.request_digest,
            manifest_hash=run.manifest_hash,
            temporal_namespace=run.temporal_namespace,
            temporal_workflow_id=run.temporal_workflow_id,
        ),
        expected_run_revision=expected_revision,
        accepted_run_revision=run.revision,
        request_digest=run.request_digest,
        manifest_hash=run.manifest_hash,
        temporal_namespace=run.temporal_namespace,
        temporal_workflow_id=run.temporal_workflow_id,
        status=TaskRunCommandStatus.PENDING,
        dispatch_attempts=0,
        created_by=run.requested_by,
        created_at=NOW,
        updated_at=NOW,
    )


def _finalizing(
    projection: TaskRun | ExecutionUnit | UnitAttempt,
    quality: ExecutionQuality = ExecutionQuality.PENDING,
) -> Any:
    return projection.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.FINALIZING,
            "quality": quality,
            "started_at": NOW + timedelta(minutes=1),
            "finalized_at": NOW + timedelta(minutes=2),
            "updated_at": NOW + timedelta(minutes=2),
        }
    )


def _closed(
    projection: ExecutionUnit | UnitAttempt,
    quality: ExecutionQuality,
) -> Any:
    return projection.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.CLOSED,
            "quality": quality,
            "started_at": NOW + timedelta(minutes=1),
            "finalized_at": NOW + timedelta(minutes=2),
            "closed_at": NOW + timedelta(minutes=3),
            "updated_at": NOW + timedelta(minutes=3),
        }
    )


def _accepted_result(
    tasks: _TaskRepository,
    *,
    verdict: Verdict = Verdict.PASSED,
) -> tuple[AttemptSeal, ResultRef]:
    run = tasks.run
    unit = tasks.units[0]
    attempt = tasks.attempts[0]
    digest = "sha256:" + "a" * 64
    content = AttemptSealContent(
        seal_id=uuid4(),
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        task_run_id=run.id,
        execution_unit_id=unit.id,
        unit_attempt_id=attempt.id,
        manifest_id=run.id,
        manifest_hash=run.manifest_hash,
        unit_key=unit.unit_key,
        execution_ticket_id=uuid4(),
        execution_ticket_digest=digest,
        oracle_verdict=verdict,
        outcome_class=OutcomeClass.BUSINESS,
        closure_reason="ORACLE_PASSED" if verdict is Verdict.PASSED else "ORACLE_FAILED",
        data_hygiene=DataHygiene.CLEANED,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        evidence_integrity=EvidenceIntegrity.VERIFIED,
        execution_influence=ExecutionInfluence.AUTONOMOUS,
        stability=Stability.STABLE,
        oracle_results_hash=digest,
        artifact_manifest_hash=digest,
        event_chain=AttemptEventChain(head=digest, event_count=4),
        evidence_policy_digest=digest,
        runtime_digest=digest,
        sealed_at=NOW + timedelta(minutes=2),
        signature=AttemptSealSignature(kid="runtime-key-1"),
    )
    seal = AttemptSeal(
        **content.model_dump(mode="python"),
        signature_value="base64url:" + "A" * 86,
        content_hash=attempt_seal_content_hash(content),
    )
    result_ref = ResultRef(
        id=uuid4(),
        tenant_id=seal.tenant_id,
        project_id=seal.project_id,
        task_run_id=seal.task_run_id,
        execution_unit_id=seal.execution_unit_id,
        unit_attempt_id=seal.unit_attempt_id,
        seal_id=seal.seal_id,
        seal_content_hash=seal.content_hash,
        created_at=NOW + timedelta(minutes=2),
    )
    return seal, result_ref


def _outcome(
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    status: str,
) -> TaskAttemptWorkflowPayload:
    return TaskAttemptWorkflowPayload(
        execution_unit_id=str(unit.id),
        unit_attempt_id=str(attempt.id),
        ordinal=unit.ordinal,
        status=cast(Any, status),
        error_code=f"TASK_{status}",
    )


def _seed_attempt_result_event(
    tasks: _TaskRepository,
    *,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    status: str,
    error_code: str,
) -> None:
    tasks.events.append(
        TaskExecutionEvent(
            id=uuid4(),
            tenant_id=attempt.tenant_id,
            project_id=attempt.project_id,
            task_run_id=attempt.task_run_id,
            execution_unit_id=unit.id,
            unit_attempt_id=attempt.id,
            seq=max((0, *(event.seq for event in tasks.events))) + 1,
            event_type="unit_attempt.finalized",
            lifecycle=ExecutionLifecycle.FINALIZING,
            quality=attempt.quality,
            hygiene=attempt.hygiene,
            payload={
                "schemaVersion": TASK_WORKFLOW_RESULT_SCHEMA,
                "status": status,
                "errorCode": error_code,
            },
            occurred_at=attempt.finalized_at or NOW,
        )
    )


@pytest.mark.anyio
async def test_load_dispatch_plan_checks_exact_identity_and_orders_first_attempts() -> None:
    service, database, tasks, _, _ = _fixture()

    plan = await service.load_dispatch_plan(_root_request(tasks.run))

    assert [item.ordinal for item in plan.units] == [1, 2]
    assert [item.unit_attempt_id for item in plan.units] == [
        str(item.id) for item in tasks.attempts
    ]
    assert all(item.activity_timeout_seconds == 840 for item in plan.units)
    assert database.contexts[0].tenant_id == tasks.run.tenant_id
    assert database.contexts[0].actor_id is None
    assert database.active_transactions == 0

    with pytest.raises(RuntimeError, match="TASK_ROOT_IDENTITY_MISMATCH"):
        await service.load_dispatch_plan(
            replace(
                _root_request(tasks.run),
                manifest_hash="sha256:" + "f" * 64,
            )
        )


@pytest.mark.anyio
async def test_load_dispatch_plan_enforces_the_64_unit_ceiling() -> None:
    within_service, _, within_tasks, _, _ = _fixture(unit_count=64)
    plan = await within_service.load_dispatch_plan(_root_request(within_tasks.run))
    assert len(plan.units) == 64

    oversized_service, _, oversized_tasks, _, _ = _fixture(unit_count=65)
    with pytest.raises(RuntimeError, match="TASK_MATERIALIZATION_INCOMPLETE"):
        await oversized_service.load_dispatch_plan(_root_request(oversized_tasks.run))


@pytest.mark.anyio
async def test_batch_prepare_stops_before_authorization_for_pause_or_cancel() -> None:
    for lifecycle, expected_status in (
        (ExecutionLifecycle.PAUSE_REQUESTED, "PAUSE_REQUESTED"),
        (ExecutionLifecycle.PAUSED, "PAUSE_REQUESTED"),
        (ExecutionLifecycle.CANCELING, "CANCEL_REQUESTED"),
    ):
        service, _, tasks, _, admission = _fixture(unit_count=1)
        tasks.replace_run(_running(tasks.run).model_copy(update={"lifecycle": lifecycle}))
        request = _root_request(tasks.run)
        attempt = _attempt_request(
            tasks.run,
            tasks.units[0],
            tasks.attempts[0],
        )

        result = await service.prepare_batch(
            TaskBatchPrepareInput(request=request, attempts=(attempt,))
        )

        assert result.status == expected_status
        assert admission.calls == 0
        assert tasks.trace == ["lock_run"]


@pytest.mark.anyio
async def test_control_checkpoint_applies_pause_then_resume_at_exact_boundaries() -> None:
    service, _, tasks, state, _ = _fixture(unit_count=1)
    pause_requested = _running(tasks.run).model_copy(
        update={
            "lifecycle": ExecutionLifecycle.PAUSE_REQUESTED,
            "revision": tasks.run.revision + 1,
        }
    )
    tasks.replace_run(pause_requested)
    commands = _CommandRepository(
        command=_control_command(pause_requested, TaskRunCommandType.PAUSE)
    )
    service._commands = cast(TaskRunCommandRepository, commands)
    request = _root_request(tasks.run)

    paused = await service.checkpoint_control(request)

    assert paused.state == "PAUSED"
    assert tasks.run.lifecycle is ExecutionLifecycle.PAUSED
    pause_command = commands.command
    assert pause_command is not None
    assert commands.pause_calls == [
        {
            "intent_id": pause_command.id,
            "command_digest": pause_command.command_digest,
        }
    ]
    assert tasks.events[-1].event_type == "task_run.paused"

    resumed_request = tasks.run.model_copy(
        update={
            "revision": tasks.run.revision + 1,
            "updated_at": NOW + timedelta(minutes=3),
        }
    )
    tasks.replace_run(resumed_request)
    resume_command = _control_command(
        resumed_request,
        TaskRunCommandType.RESUME,
    )
    commands.command = resume_command

    resumed = await service.checkpoint_control(request)

    assert resumed.state == "DISPATCHABLE"
    assert cast(Any, tasks.run.lifecycle) is ExecutionLifecycle.RUNNING
    assert commands.resume_calls == [
        {
            "intent_id": resume_command.id,
            "command_digest": resume_command.command_digest,
        }
    ]
    assert tasks.events[-1].event_type == "task_run.resumed"
    assert state.calls == ["run", "run"]


@pytest.mark.anyio
async def test_prepare_attempt_creates_exact_ticket_and_replays_without_readmission(
    valid_graph: Any,
    intent_factory: Any,
) -> None:
    fixture = _admission_fixture(valid_graph, intent_factory)
    run = _admission_run(
        sealed=True,
        task_run_id=fixture.unit.task_run_id,
        tenant_id=fixture.unit.tenant_id,
        project_id=fixture.unit.project_id,
    )
    attempt = _admission_attempt(run, fixture.unit).model_copy(
        update={
            "temporal_namespace": run.temporal_namespace,
            "temporal_workflow_id": unit_attempt_workflow_id(
                tenant_id=run.tenant_id,
                unit_attempt_id=_admission_attempt(run, fixture.unit).id,
            ),
        }
    )
    _, manifest, _, _ = _aggregate(unit_count=1)
    database = _Database(attempt.queued_at + timedelta(minutes=1))
    tasks = _TaskRepository(run, manifest, (fixture.unit,), (attempt,))
    state = _StateRepository(tasks)
    admission = _Admission(
        snapshot=TaskAdmissionSnapshot(
            unit=fixture.unit,
            case_version=fixture.case,
            execution_profile=fixture.profiles.execution,
            identity_profile=fixture.profiles.identity,
            browser_profile=fixture.profiles.browser,
            data_profile=fixture.profiles.data,
            fixture_blueprint_version=cast(Any, fixture.fixture),
            environment=fixture.environment,
            roles=(cast(Any, fixture.role),),
        )
    )
    tickets = _TicketRepository()
    service = TaskWorkerService(
        cast(Database, database),
        task_repository=cast(Any, tasks),
        state_repository=cast(Any, state),
        admission_service=cast(Any, admission),
        ticket_repository=cast(Any, tickets),
    )
    request = _attempt_request(run, fixture.unit, attempt)

    prepared = await service.prepare_attempt(request)

    assert prepared.attempt == request
    assert tickets.ticket is not None
    assert prepared.ticket_id == str(tickets.ticket.id)
    assert prepared.ticket_digest == tickets.ticket.ticket_digest
    assert tickets.ticket.execution_profile_version_id == fixture.profiles.execution.id
    assert tickets.ticket.allowed_origins == fixture.environment.allowed_origins
    assert admission.calls == 1
    assert tickets.create_calls == 1
    assert tasks.trace[:3] == ["lock_run", "lock_unit", "lock_attempt"]

    tasks.replace_run(
        run.model_copy(
            update={
                "lifecycle": ExecutionLifecycle.PAUSE_REQUESTED,
                "started_at": NOW + timedelta(minutes=1),
                "updated_at": NOW + timedelta(minutes=1),
            }
        )
    )
    started = await service.start_attempt(request)
    assert started.status == "READY"
    assert admission.calls == 1

    admission.error = ApplicationError(
        error_code=ErrorCode.CONSTRAINT_UNSATISFIED,
        title="revoked",
        detail="dependency was revoked after ticket creation",
        status_code=422,
    )
    assert await service.prepare_attempt(request) == prepared
    assert admission.calls == 1
    assert tickets.create_calls == 1

    tickets.ticket = tickets.ticket.model_copy(update={"ticket_digest": "sha256:" + "f" * 64})
    with pytest.raises(RuntimeError, match="TASK_EXECUTION_TICKET_IDENTITY_MISMATCH"):
        await service.prepare_attempt(request)


@pytest.mark.anyio
async def test_start_attempt_uses_one_transaction_and_run_unit_attempt_order() -> None:
    service, database, tasks, state, admission = _fixture(unit_count=1)
    request = _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])

    result = await service.start_attempt(request)

    assert result.status == "READY"
    assert tasks.trace[:3] == ["lock_run", "lock_unit", "lock_attempt"]
    assert state.calls == ["run", "unit", "attempt"]
    assert [event.event_type for event in tasks.events] == [
        "task_run.started",
        "execution_unit.started",
        "unit_attempt.started",
    ]
    assert admission.calls == 1
    assert len(database.contexts) == 1


@pytest.mark.anyio
async def test_start_attempt_replays_before_queued_admission() -> None:
    service, _, tasks, state, admission = _fixture(unit_count=1)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_running(tasks.attempts[0]))

    result = await service.start_attempt(
        _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])
    )

    assert result.status == "READY"
    assert admission.calls == 0
    assert state.calls == []
    assert tasks.events == []


@pytest.mark.anyio
async def test_start_attempt_never_reexecutes_a_finalizing_attempt() -> None:
    service, _, tasks, state, admission = _fixture(unit_count=1)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_finalizing(tasks.attempts[0]))

    result = await service.start_attempt(
        _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])
    )

    assert result.status == "REJECTED"
    assert result.error_code == "TASK_ATTEMPT_ALREADY_FINALIZING"
    assert admission.calls == 0
    assert state.calls == []
    assert tasks.events == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("quality", "expected_status", "expected_code"),
    (
        (
            ExecutionQuality.FAILED,
            "REJECTED",
            "TASK_ATTEMPT_ALREADY_CLOSED",
        ),
        (
            ExecutionQuality.CANCELED,
            "CANCELED",
            "TASK_ORIGINAL_CANCEL_REASON",
        ),
    ),
)
async def test_start_attempt_never_readies_a_closed_attempt(
    quality: ExecutionQuality,
    expected_status: str,
    expected_code: str,
) -> None:
    service, _, tasks, state, admission = _fixture(unit_count=1)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_closed(tasks.units[0], quality))
    tasks.replace_attempt(_closed(tasks.attempts[0], quality))
    if quality is ExecutionQuality.CANCELED:
        _seed_attempt_result_event(
            tasks,
            unit=tasks.units[0],
            attempt=tasks.attempts[0],
            status="CANCELED",
            error_code=expected_code,
        )

    result = await service.start_attempt(
        _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])
    )

    assert result.status == expected_status
    assert result.error_code == expected_code
    assert admission.calls == 0
    assert state.calls == []


@pytest.mark.anyio
async def test_start_attempt_rejects_admission_and_expired_deadline_safely() -> None:
    service, _, tasks, state, admission = _fixture(unit_count=1)
    admission.error = ApplicationError(
        error_code=ErrorCode.CONSTRAINT_UNSATISFIED,
        title="rejected",
        detail="raw dependency detail",
        status_code=422,
    )
    request = _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])

    rejected = await service.start_attempt(request)

    assert rejected.status == "REJECTED"
    assert rejected.error_code == "TASK_ADMISSION_REJECTED"
    assert state.calls == []

    service, _, tasks, state, admission = _fixture(
        unit_count=1,
        observed_at=NOW + timedelta(hours=1),
    )
    expired = await service.start_attempt(
        _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])
    )
    assert expired.status == "CANCELED"
    assert expired.error_code == "TASK_ATTEMPT_DEADLINE_EXPIRED"
    assert admission.calls == 0
    assert state.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("execution_status", "quality", "workflow_status", "expected_code"),
    (
        (
            "EXECUTED_UNSEALED",
            ExecutionQuality.INCONCLUSIVE,
            "FINISHED_UNSEALED",
            "TASK_ATTEMPT_RESULT_UNSEALED",
        ),
        ("FAILED", ExecutionQuality.FAILED, "FAILED", "TASK_ATTEMPT_FAILED"),
        (
            "INCONCLUSIVE",
            ExecutionQuality.INCONCLUSIVE,
            "INCONCLUSIVE",
            "TASK_ATTEMPT_INCONCLUSIVE",
        ),
        ("CANCELED", ExecutionQuality.CANCELED, "CANCELED", "TASK_ATTEMPT_CANCELED"),
    ),
)
async def test_finish_attempt_maps_only_non_passed_safe_results(
    execution_status: str,
    quality: ExecutionQuality,
    workflow_status: str,
    expected_code: str,
) -> None:
    service, _, tasks, state, _ = _fixture(unit_count=1)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_running(tasks.attempts[0]))
    attempt_request = _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])
    command = TaskAttemptFinishInput(
        attempt=attempt_request,
        execution=TaskAttemptExecutionPayload(
            status=cast(Any, execution_status),
            error_code="ValueError: raw secret=do-not-store",
        ),
    )

    result = await service.finish_attempt(command)

    assert result.status == workflow_status
    assert result.error_code == expected_code
    assert tasks.attempts[0].lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.attempts[0].quality is quality
    assert tasks.units[0].lifecycle is ExecutionLifecycle.RUNNING
    assert tasks.units[0].quality is ExecutionQuality.PENDING
    assert state.calls == ["attempt", "attempt"]
    assert all("raw secret" not in str(event.payload) for event in tasks.events)

    calls_before_replay = list(state.calls)
    assert await service.finish_attempt(command) == result
    assert state.calls == calls_before_replay


@pytest.mark.anyio
async def test_sealed_result_recovers_lost_activity_response_and_passes_run() -> None:
    results = _ResultRepository()
    service, _, tasks, _, _ = _fixture(
        unit_count=1,
        result_repository=results,
    )
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_closed(tasks.attempts[0], ExecutionQuality.PASSED))
    seal, result_ref = _accepted_result(tasks)
    results.seals[seal.unit_attempt_id] = seal
    results.refs[seal.unit_attempt_id] = result_ref
    attempt_request = _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])

    recovered = await service.finish_attempt(
        TaskAttemptFinishInput(
            attempt=attempt_request,
            execution=TaskAttemptExecutionPayload(
                status="INCONCLUSIVE",
                error_code="TASK_ATTEMPT_ACTIVITY_FAILED",
            ),
        )
    )

    assert recovered.status == "PASSED"
    assert recovered.error_code is None
    assert recovered.result_ref_id == str(result_ref.id)
    assert recovered.seal_content_hash == seal.content_hash

    settled = await service.settle_attempt_batch(
        TaskAttemptBatchSettleInput(
            request=_root_request(tasks.run),
            outcomes=(recovered,),
        )
    )
    assert settled.final_outcomes == (recovered,)
    assert tasks.units[0].lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.units[0].quality is ExecutionQuality.PASSED

    finished = await service.finish_run(
        TaskRunFinishInput(
            request=_root_request(tasks.run),
            outcomes=(recovered,),
            cancel_requested=False,
            skipped_units=0,
        )
    )
    assert finished.status == "PASSED"
    assert finished.completed_units == 1
    assert tasks.run.lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.run.quality is ExecutionQuality.PASSED


@pytest.mark.anyio
async def test_settle_attempt_batch_appends_exact_infrastructure_retry_and_replays() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    _enable_retry_policy(tasks)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_running(tasks.attempts[0]))
    first = tasks.attempts[0]
    outcome = await service.finish_attempt(
        TaskAttemptFinishInput(
            attempt=_attempt_request(tasks.run, tasks.units[0], first),
            execution=TaskAttemptExecutionPayload(
                status="INFRA_ERROR",
                error_code="TASK_BROWSER_HOST_UNAVAILABLE",
                retry_after_seconds=7,
            ),
        )
    )
    request = TaskAttemptBatchSettleInput(
        request=_root_request(tasks.run),
        outcomes=(outcome,),
    )

    settled = await service.settle_attempt_batch(request)
    replay = await service.settle_attempt_batch(request)

    assert replay == settled
    assert settled.state == "SETTLED"
    assert settled.final_outcomes == ()
    assert len(settled.retry_attempts) == 1
    retry = tasks.attempts[-1]
    assert retry.id == unit_retry_attempt_id(
        execution_unit_id=tasks.units[0].id,
        attempt_number=2,
    )
    assert retry.attempt_number == 2
    assert retry.lifecycle is ExecutionLifecycle.QUEUED
    assert retry.execution_deadline == first.execution_deadline
    assert retry.queued_at == NOW + timedelta(minutes=1, seconds=7)
    assert tasks.units[0].lifecycle is ExecutionLifecycle.RUNNING
    assert tasks.units[0].quality is ExecutionQuality.PENDING
    assert sum(event.event_type == "unit_attempt.retry_scheduled" for event in tasks.events) == 1


@pytest.mark.anyio
async def test_settle_attempt_batch_never_retries_non_infrastructure_outcome() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    _enable_retry_policy(tasks)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_running(tasks.attempts[0]))
    outcome = await service.finish_attempt(
        TaskAttemptFinishInput(
            attempt=_attempt_request(tasks.run, tasks.units[0], tasks.attempts[0]),
            execution=TaskAttemptExecutionPayload(status="FAILED"),
        )
    )

    settled = await service.settle_attempt_batch(
        TaskAttemptBatchSettleInput(
            request=_root_request(tasks.run),
            outcomes=(outcome,),
        )
    )

    assert settled.retry_attempts == ()
    assert settled.final_outcomes == (outcome,)
    assert len(tasks.attempts) == 1
    assert tasks.units[0].lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.units[0].quality is ExecutionQuality.FAILED


@pytest.mark.anyio
async def test_settle_attempt_batch_closes_exhausted_infrastructure_failure() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    _enable_retry_policy(tasks, infra_retry_attempts=0)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_running(tasks.attempts[0]))
    outcome = await service.finish_attempt(
        TaskAttemptFinishInput(
            attempt=_attempt_request(tasks.run, tasks.units[0], tasks.attempts[0]),
            execution=TaskAttemptExecutionPayload(
                status="INFRA_ERROR",
                retry_after_seconds=30,
            ),
        )
    )

    settled = await service.settle_attempt_batch(
        TaskAttemptBatchSettleInput(
            request=_root_request(tasks.run),
            outcomes=(outcome,),
        )
    )

    assert settled.final_outcomes == (outcome,)
    assert settled.retry_attempts == ()
    assert tasks.units[0].lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.units[0].quality is ExecutionQuality.INFRA_ERROR


@pytest.mark.anyio
async def test_settle_attempt_batch_defers_all_decisions_for_pause_or_cancel() -> None:
    for lifecycle, expected_state in (
        (ExecutionLifecycle.PAUSE_REQUESTED, "PAUSE_REQUESTED"),
        (ExecutionLifecycle.PAUSED, "PAUSE_REQUESTED"),
        (ExecutionLifecycle.CANCELING, "CANCEL_REQUESTED"),
    ):
        service, _, tasks, state, _ = _fixture(unit_count=1)
        tasks.replace_run(
            tasks.run.model_copy(
                update={
                    "lifecycle": lifecycle,
                    "updated_at": NOW + timedelta(minutes=1),
                }
            )
        )
        outcome = TaskAttemptWorkflowPayload(
            execution_unit_id=str(tasks.units[0].id),
            unit_attempt_id=str(tasks.attempts[0].id),
            ordinal=1,
            status="INCONCLUSIVE",
        )

        settled = await service.settle_attempt_batch(
            TaskAttemptBatchSettleInput(
                request=_root_request(tasks.run),
                outcomes=(outcome,),
            )
        )

        assert settled.state == expected_state
        assert settled.retry_attempts == ()
        assert settled.final_outcomes == ()
        assert state.calls == []


@pytest.mark.anyio
async def test_settle_attempt_batch_safely_reconciles_failed_child_workflow() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_running(tasks.attempts[0]))
    fallback = TaskAttemptWorkflowPayload(
        execution_unit_id=str(tasks.units[0].id),
        unit_attempt_id=str(tasks.attempts[0].id),
        ordinal=1,
        status="INCONCLUSIVE",
        error_code="TASK_ATTEMPT_WORKFLOW_FAILED",
    )

    settled = await service.settle_attempt_batch(
        TaskAttemptBatchSettleInput(
            request=_root_request(tasks.run),
            outcomes=(fallback,),
        )
    )

    assert settled.final_outcomes == (fallback,)
    assert tasks.attempts[0].lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.attempts[0].quality is ExecutionQuality.INCONCLUSIVE
    assert tasks.units[0].lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.units[0].quality is ExecutionQuality.INCONCLUSIVE


@pytest.mark.anyio
async def test_finish_attempt_rejects_invalid_or_conflicting_results() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_closed(tasks.units[0], ExecutionQuality.FAILED))
    tasks.replace_attempt(_closed(tasks.attempts[0], ExecutionQuality.FAILED))
    attempt_request = _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])

    with pytest.raises(RuntimeError, match="TASK_ATTEMPT_RESULT_INVALID"):
        await service.finish_attempt(
            TaskAttemptFinishInput(
                attempt=attempt_request,
                execution=TaskAttemptExecutionPayload(status=cast(Any, "PASSED")),
            )
        )
    with pytest.raises(RuntimeError, match="TASK_ATTEMPT_RESULT_CONFLICT"):
        await service.finish_attempt(
            TaskAttemptFinishInput(
                attempt=attempt_request,
                execution=TaskAttemptExecutionPayload(status="INCONCLUSIVE"),
            )
        )


@pytest.mark.anyio
async def test_finish_attempt_backfills_exact_event_for_preexisting_finalizing_state() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_finalizing(tasks.units[0]))
    tasks.replace_attempt(_finalizing(tasks.attempts[0]))
    command = TaskAttemptFinishInput(
        attempt=_attempt_request(tasks.run, tasks.units[0], tasks.attempts[0]),
        execution=TaskAttemptExecutionPayload(
            status="FAILED",
            error_code="TASK_ADAPTER_FAILED",
        ),
    )

    result = await service.finish_attempt(command)
    replay = await service.finish_attempt(command)

    assert replay == result
    finalized = [event for event in tasks.events if event.event_type == "unit_attempt.finalized"]
    assert len(finalized) == 1
    assert finalized[0].payload["status"] == "FAILED"
    assert finalized[0].payload["errorCode"] == "TASK_ADAPTER_FAILED"


@pytest.mark.anyio
async def test_finish_attempt_replay_requires_exact_status_and_error_code() -> None:
    service, _, tasks, state, _ = _fixture(unit_count=1)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_running(tasks.attempts[0]))
    attempt_request = _attempt_request(tasks.run, tasks.units[0], tasks.attempts[0])
    command = TaskAttemptFinishInput(
        attempt=attempt_request,
        execution=TaskAttemptExecutionPayload(
            status="EXECUTED_UNSEALED",
            error_code="TASK_SHARED_RESULT_CODE",
        ),
    )

    result = await service.finish_attempt(command)
    calls_before_conflicts = list(state.calls)

    assert result.status == "FINISHED_UNSEALED"
    with pytest.raises(RuntimeError, match="TASK_ATTEMPT_RESULT_CONFLICT"):
        await service.finish_attempt(
            replace(
                command,
                execution=TaskAttemptExecutionPayload(
                    status="INCONCLUSIVE",
                    error_code="TASK_SHARED_RESULT_CODE",
                ),
            )
        )
    with pytest.raises(RuntimeError, match="TASK_ATTEMPT_RESULT_CONFLICT"):
        await service.finish_attempt(
            replace(
                command,
                execution=TaskAttemptExecutionPayload(
                    status="EXECUTED_UNSEALED",
                    error_code="TASK_DIFFERENT_RESULT_CODE",
                ),
            )
        )
    assert state.calls == calls_before_conflicts


@pytest.mark.anyio
async def test_finish_attempt_replay_reads_result_fact_after_event_page_boundary() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_running(tasks.units[0]))
    tasks.replace_attempt(_running(tasks.attempts[0]))
    for seq in range(1, 257):
        tasks.events.append(
            TaskExecutionEvent(
                id=uuid4(),
                tenant_id=tasks.run.tenant_id,
                project_id=tasks.run.project_id,
                task_run_id=tasks.run.id,
                seq=seq,
                event_type="task_run.observed",
                lifecycle=ExecutionLifecycle.RUNNING,
                quality=ExecutionQuality.PENDING,
                hygiene=tasks.run.hygiene,
                occurred_at=NOW,
            )
        )
    command = TaskAttemptFinishInput(
        attempt=_attempt_request(tasks.run, tasks.units[0], tasks.attempts[0]),
        execution=TaskAttemptExecutionPayload(status="FAILED"),
    )

    result = await service.finish_attempt(command)
    replay = await service.finish_attempt(command)

    assert replay == result
    assert tasks.list_event_calls == [(0, 256), (256, 256)]


def _finish_request(
    tasks: _TaskRepository,
    statuses: tuple[str, ...],
    *,
    cancel_requested: bool = False,
    skipped_units: int = 0,
    durable: bool = True,
) -> TaskRunFinishInput:
    quality_by_status = {
        "FINISHED_UNSEALED": ExecutionQuality.INCONCLUSIVE,
        "INCONCLUSIVE": ExecutionQuality.INCONCLUSIVE,
        "FAILED": ExecutionQuality.FAILED,
        "CANCELED": ExecutionQuality.CANCELED,
    }
    for index, status in enumerate(statuses):
        if durable:
            tasks.replace_unit(_closed(tasks.units[index], quality_by_status[status]))
            tasks.replace_attempt(_closed(tasks.attempts[index], quality_by_status[status]))
            _seed_attempt_result_event(
                tasks,
                unit=tasks.units[index],
                attempt=tasks.attempts[index],
                status=status,
                error_code=f"TASK_{status}",
            )
    return TaskRunFinishInput(
        request=_root_request(tasks.run),
        outcomes=tuple(
            _outcome(tasks.units[index], tasks.attempts[index], status)
            for index, status in enumerate(statuses)
        ),
        cancel_requested=cancel_requested,
        skipped_units=skipped_units,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("statuses", "expected_status", "expected_quality"),
    (
        (
            ("FINISHED_UNSEALED", "FINISHED_UNSEALED"),
            "FINISHED_UNSEALED",
            ExecutionQuality.INCONCLUSIVE,
        ),
        (
            ("FINISHED_UNSEALED", "INCONCLUSIVE"),
            "INCONCLUSIVE",
            ExecutionQuality.INCONCLUSIVE,
        ),
        (
            ("FINISHED_UNSEALED", "FAILED"),
            "FAILED",
            ExecutionQuality.FAILED,
        ),
        (("FINISHED_UNSEALED", "CANCELED"), "CANCELED", ExecutionQuality.CANCELED),
    ),
)
async def test_finish_run_reduces_non_passed_children_and_replays(
    statuses: tuple[str, ...],
    expected_status: str,
    expected_quality: ExecutionQuality,
) -> None:
    service, _, tasks, state, _ = _fixture(unit_count=2)
    tasks.replace_run(_running(tasks.run))
    command = _finish_request(tasks, statuses)

    result = await service.finish_run(command)

    assert result.status == expected_status
    assert tasks.run.lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.run.quality is expected_quality
    assert state.calls == ["run", "run"]

    calls_before_replay = list(state.calls)
    assert await service.finish_run(command) == result
    assert state.calls == calls_before_replay
    snapshot_calls = cast(Any, service)._result_projection.snapshot_calls
    fully_resolved_calls = (
        cast(Any, service)._result_projection.fully_resolved_snapshot_calls
    )
    assert len(snapshot_calls) == 2
    assert all(call["run"].lifecycle is ExecutionLifecycle.CLOSED for call in snapshot_calls)
    assert all(call["manifest"] == tasks.manifest for call in snapshot_calls)
    assert len(fully_resolved_calls) == 2
    assert all(
        call["run"].lifecycle is ExecutionLifecycle.CLOSED
        for call in fully_resolved_calls
    )


@pytest.mark.anyio
async def test_finish_run_replay_requires_exact_child_status_and_error_code() -> None:
    service, _, tasks, state, _ = _fixture(unit_count=2)
    tasks.replace_run(_running(tasks.run))
    command = _finish_request(
        tasks,
        ("FINISHED_UNSEALED", "FINISHED_UNSEALED"),
    )

    result = await service.finish_run(command)
    calls_before_conflicts = list(state.calls)

    assert result.status == "FINISHED_UNSEALED"
    with pytest.raises(RuntimeError, match="TASK_ATTEMPT_RESULT_CONFLICT"):
        await service.finish_run(
            replace(
                command,
                outcomes=(
                    replace(command.outcomes[0], status="INCONCLUSIVE"),
                    command.outcomes[1],
                ),
            )
        )
    with pytest.raises(RuntimeError, match="TASK_ATTEMPT_RESULT_CONFLICT"):
        await service.finish_run(
            replace(
                command,
                outcomes=(
                    replace(
                        command.outcomes[0],
                        error_code="TASK_DIFFERENT_RESULT_CODE",
                    ),
                    command.outcomes[1],
                ),
            )
        )
    assert state.calls == calls_before_conflicts


@pytest.mark.anyio
async def test_finish_run_replay_requires_exact_persisted_counts() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=2)
    tasks.replace_run(_running(tasks.run))
    command = _finish_request(
        tasks,
        ("FINISHED_UNSEALED", "FINISHED_UNSEALED"),
    )
    await service.finish_run(command)
    event_index = next(
        index
        for index, event in enumerate(tasks.events)
        if event.event_type == "task_run.finalized"
    )
    finalized = tasks.events[event_index]
    tasks.events[event_index] = finalized.model_copy(
        update={
            "payload": {
                **finalized.payload,
                "completedUnits": 1,
                "inconclusiveUnits": 1,
            }
        }
    )

    with pytest.raises(RuntimeError, match="TASK_RUN_RESULT_CONFLICT"):
        await service.finish_run(command)


@pytest.mark.anyio
async def test_finish_run_rejects_bad_coverage() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=2)
    tasks.replace_run(_running(tasks.run))
    one_outcome = _outcome(tasks.units[0], tasks.attempts[0], "INCONCLUSIVE")
    count_mismatch = TaskRunFinishInput(
        request=_root_request(tasks.run),
        outcomes=(one_outcome,),
        cancel_requested=False,
        skipped_units=0,
    )

    with pytest.raises(RuntimeError, match="TASK_RUN_OUTCOME_COUNT_MISMATCH"):
        await service.finish_run(count_mismatch)


@pytest.mark.anyio
async def test_finish_run_durably_reconciles_child_fallback_and_replays() -> None:
    service, _, tasks, state, _ = _fixture(unit_count=2)
    tasks.replace_run(_running(tasks.run))
    command = _finish_request(
        tasks,
        ("FAILED", "INCONCLUSIVE"),
        durable=False,
    )
    command = replace(
        command,
        outcomes=(
            replace(command.outcomes[0], error_code="RuntimeError: raw secret"),
            command.outcomes[1],
        ),
    )

    result = await service.finish_run(command)

    assert result.status == "FAILED"
    assert [unit.lifecycle for unit in tasks.units] == [
        ExecutionLifecycle.CLOSED,
        ExecutionLifecycle.CLOSED,
    ]
    assert [unit.quality for unit in tasks.units] == [
        ExecutionQuality.FAILED,
        ExecutionQuality.INCONCLUSIVE,
    ]
    assert all(attempt.lifecycle is ExecutionLifecycle.CLOSED for attempt in tasks.attempts)
    assert all("raw secret" not in str(event.payload) for event in tasks.events)
    assert tasks.trace[:5] == [
        "lock_run",
        "lock_unit",
        "lock_attempt",
        "event:unit_attempt.finalized",
        "event:unit_attempt.closed",
    ]

    event_count = len(tasks.events)
    state_calls = list(state.calls)
    assert await service.finish_run(command) == result
    assert len(tasks.events) == event_count
    assert state.calls == state_calls


@pytest.mark.anyio
async def test_finish_run_backfills_exact_events_for_preexisting_finalizing_chain() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    tasks.replace_run(_finalizing(tasks.run))
    tasks.replace_unit(_finalizing(tasks.units[0]))
    tasks.replace_attempt(_finalizing(tasks.attempts[0]))
    command = _finish_request(tasks, ("FAILED",), durable=False)

    result = await service.finish_run(command)
    replay = await service.finish_run(command)

    assert replay == result
    assert result.status == "FAILED"
    assert sum(event.event_type == "unit_attempt.finalized" for event in tasks.events) == 1
    assert sum(event.event_type == "task_run.finalized" for event in tasks.events) == 1


@pytest.mark.anyio
async def test_finish_run_closes_skipped_tail_as_canceled_before_root() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=2)
    tasks.replace_run(_running(tasks.run))
    command = _finish_request(
        tasks,
        ("INCONCLUSIVE",),
        cancel_requested=True,
        skipped_units=1,
        durable=False,
    )

    result = await service.finish_run(command)

    assert result.status == "CANCELED"
    assert tasks.units[0].quality is ExecutionQuality.INCONCLUSIVE
    assert tasks.attempts[0].quality is ExecutionQuality.INCONCLUSIVE
    assert tasks.units[1].quality is ExecutionQuality.CANCELED
    assert tasks.attempts[1].quality is ExecutionQuality.CANCELED
    assert all(unit.lifecycle is ExecutionLifecycle.CLOSED for unit in tasks.units)
    assert all(attempt.lifecycle is ExecutionLifecycle.CLOSED for attempt in tasks.attempts)
    assert tasks.run.lifecycle is ExecutionLifecycle.CLOSED


@pytest.mark.anyio
async def test_canceling_dispatch_plan_stops_root_even_before_signal_delivery() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    tasks.replace_run(tasks.run.model_copy(update={"lifecycle": ExecutionLifecycle.CANCELING}))

    plan = await service.load_dispatch_plan(_root_request(tasks.run))

    assert plan.cancel_requested is True
    assert len(plan.units) == 1


@pytest.mark.anyio
async def test_finish_run_respects_database_canceling_even_before_signal_arrives() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    tasks.replace_run(
        _running(tasks.run).model_copy(update={"lifecycle": ExecutionLifecycle.CANCELING})
    )
    request = _finish_request(
        tasks,
        ("FINISHED_UNSEALED",),
        cancel_requested=False,
        skipped_units=0,
        durable=False,
    )

    result = await service.finish_run(request)

    assert result.status == "CANCELED"
    assert result.completed_units == 1
    assert tasks.run.lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.run.quality is ExecutionQuality.CANCELED


@pytest.mark.anyio
async def test_finish_run_atomically_applies_exact_cancel_command_after_closure() -> None:
    commands = _CommandRepository()
    service, _, tasks, _, _ = _fixture(
        unit_count=2,
        command_repository=commands,
    )
    tasks.replace_run(_running(tasks.run))
    request = _finish_request(
        tasks,
        ("INCONCLUSIVE",),
        cancel_requested=True,
        skipped_units=1,
        durable=False,
    )
    command = TaskRunCommandSignal(
        command_id=str(uuid4()),
        tenant_id=request.request.tenant_id,
        project_id=request.request.project_id,
        task_run_id=request.request.task_run_id,
        command_type="CANCEL",
        command_digest="sha256:" + "a" * 64,
        accepted_run_revision=3,
        schema_version=TASK_RUN_COMMAND_SIGNAL_SCHEMA,
    )
    request = replace(request, commands=(command,))

    result = await service.finish_run(request)

    assert result.status == "CANCELED"
    assert tasks.run.lifecycle is ExecutionLifecycle.CLOSED
    assert commands.calls == [
        {
            "intent_id": UUID(command.command_id),
            "command_digest": command.command_digest,
        }
    ]
    assert await service.finish_run(request) == result
    assert len(commands.calls) == 2


@pytest.mark.anyio
async def test_finish_run_rejects_invalid_or_unapplied_command_acknowledgement() -> None:
    commands = _CommandRepository(applied=False)
    service, _, tasks, _, _ = _fixture(
        unit_count=1,
        command_repository=commands,
    )
    tasks.replace_run(_running(tasks.run))
    base = _finish_request(
        tasks,
        (),
        cancel_requested=True,
        skipped_units=1,
        durable=False,
    )
    valid = TaskRunCommandSignal(
        command_id=str(uuid4()),
        tenant_id=base.request.tenant_id,
        project_id=base.request.project_id,
        task_run_id=base.request.task_run_id,
        command_type="CANCEL",
        command_digest="sha256:" + "b" * 64,
        accepted_run_revision=3,
    )

    result = TaskRunWorkflowPayload(
        task_run_id=base.request.task_run_id,
        status="CANCELED",
        completed_units=0,
        failed_units=0,
        inconclusive_units=0,
        canceled_units=0,
        skipped_units=1,
    )
    connection = cast(AsyncConnection[DictRow], object())
    with pytest.raises(RuntimeError, match="TASK_RUN_COMMAND_RESULT_CONFLICT"):
        await service._apply_finish_commands(
            connection,
            request=replace(base, cancel_requested=False, commands=(valid,)),
            result=result,
        )
    with pytest.raises(RuntimeError, match="TASK_RUN_COMMAND_COUNT_INVALID"):
        await service._apply_finish_commands(
            connection,
            request=replace(base, commands=(valid, valid)),
            result=result,
        )
    with pytest.raises(RuntimeError, match="TASK_RUN_COMMAND_IDENTITY_INVALID"):
        await service._apply_finish_commands(
            connection,
            request=replace(
                base,
                commands=(replace(valid, task_run_id=str(uuid4())),),
            ),
            result=result,
        )
    with pytest.raises(RuntimeError, match="TASK_RUN_COMMAND_APPLY_FAILED"):
        await service._apply_finish_commands(
            connection,
            request=replace(base, commands=(valid,)),
            result=result,
        )


@pytest.mark.anyio
async def test_finish_run_fails_closed_on_terminal_quality_or_identity_conflict() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=2)
    tasks.replace_run(_running(tasks.run))
    tasks.replace_unit(_closed(tasks.units[0], ExecutionQuality.FAILED))
    tasks.replace_attempt(_closed(tasks.attempts[0], ExecutionQuality.FAILED))
    conflict = _finish_request(
        tasks,
        ("INCONCLUSIVE", "INCONCLUSIVE"),
        durable=False,
    )

    with pytest.raises(RuntimeError, match="TASK_ATTEMPT_RESULT_CONFLICT"):
        await service.finish_run(conflict)

    service, _, tasks, _, _ = _fixture(unit_count=2)
    identity_conflict = _finish_request(
        tasks,
        ("INCONCLUSIVE", "INCONCLUSIVE"),
        durable=False,
    )
    identity_conflict = replace(
        identity_conflict,
        outcomes=(
            replace(
                identity_conflict.outcomes[0],
                unit_attempt_id=str(tasks.attempts[1].id),
            ),
            identity_conflict.outcomes[1],
        ),
    )
    with pytest.raises(RuntimeError, match="TASK_RUN_CHILD_IDENTITY_MISMATCH"):
        await service.finish_run(identity_conflict)


@pytest.mark.anyio
async def test_finish_run_never_accepts_unsealed_passed_child() -> None:
    service, _, tasks, _, _ = _fixture(unit_count=1)
    passed = TaskAttemptWorkflowPayload(
        execution_unit_id=str(tasks.units[0].id),
        unit_attempt_id=str(tasks.attempts[0].id),
        ordinal=1,
        status=cast(Any, "PASSED"),
    )
    command = TaskRunFinishInput(
        request=_root_request(tasks.run),
        outcomes=(passed,),
        cancel_requested=False,
        skipped_units=0,
    )

    with pytest.raises(RuntimeError, match="TASK_ATTEMPT_RESULT_INVALID"):
        await service.finish_run(command)
