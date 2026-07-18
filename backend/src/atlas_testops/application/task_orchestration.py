"""Database authority used by the durable Task orchestration worker."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from re import fullmatch
from typing import cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.task_execution import (
    TaskAdmissionService,
    TaskAdmissionSnapshot,
)
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.task import (
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskRetryPolicy,
    TaskRun,
    TaskRunCommandIntent,
    TaskRunCommandType,
    TaskUnitExecutionTicket,
    UnitAttempt,
    task_run_workflow_id,
    task_unit_execution_ticket_digest,
    unit_attempt_workflow_id,
    unit_retry_attempt_id,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_execution_tickets import (
    TaskExecutionTicketRepository,
)
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository
from atlas_testops.infrastructure.task_commands import TaskRunCommandRepository
from atlas_testops.orchestration.task_intents import TaskRunWorkflowInput
from atlas_testops.orchestration.tasks import (
    TASK_RUN_CHILD_BATCH_SIZE,
    TASK_RUN_COMMAND_SIGNAL_LEGACY_SCHEMA,
    TASK_RUN_COMMAND_SIGNAL_SCHEMA,
    TASK_WORKFLOW_RESULT_SCHEMA,
    TaskAttemptBatchSettleInput,
    TaskAttemptBatchSettlePayload,
    TaskAttemptBeginPayload,
    TaskAttemptFinishInput,
    TaskAttemptWorkflowPayload,
    TaskBatchPrepareInput,
    TaskBatchPreparePayload,
    TaskDispatchPlanPayload,
    TaskOrchestrationInvariantError,
    TaskRunCommandSignal,
    TaskRunControlCheckpointPayload,
    TaskRunFinishInput,
    TaskRunWorkflowPayload,
    TaskUnitDispatchPayload,
    TaskUnitExecutionPort,
    TaskUnitExecutionRequest,
    UnitAttemptWorkflowInput,
)

_MAXIMUM_UNITS = 64
_EVENT_PAGE_SIZE = 256
_SAFE_ERROR_CODE = "TASK_ATTEMPT_RESULT_UNSEALED"
_RECONCILABLE_LIFECYCLES = frozenset(
    {
        ExecutionLifecycle.QUEUED,
        ExecutionLifecycle.RUNNING,
        ExecutionLifecycle.PAUSE_REQUESTED,
        ExecutionLifecycle.PAUSED,
        ExecutionLifecycle.CANCELING,
        ExecutionLifecycle.FINALIZING,
    }
)

type _Projection = TaskRun | ExecutionUnit | UnitAttempt


def _require_exact_finish_command(
    request: TaskRunWorkflowInput,
    command: TaskRunCommandSignal,
) -> None:
    try:
        UUID(command.command_id)
        tenant_id = UUID(command.tenant_id)
        project_id = UUID(command.project_id)
        task_run_id = UUID(command.task_run_id)
    except ValueError:
        raise _worker_error("TASK_RUN_COMMAND_IDENTITY_INVALID") from None
    if (
        command.schema_version
        not in {
            TASK_RUN_COMMAND_SIGNAL_LEGACY_SCHEMA,
            TASK_RUN_COMMAND_SIGNAL_SCHEMA,
        }
        or command.command_type != "CANCEL"
        or command.accepted_run_revision < 2
        or fullmatch(r"sha256:[0-9a-f]{64}", command.command_digest) is None
        or tenant_id != UUID(request.tenant_id)
        or project_id != UUID(request.project_id)
        or task_run_id != UUID(request.task_run_id)
    ):
        raise _worker_error("TASK_RUN_COMMAND_IDENTITY_INVALID")


class TaskWorkerService:
    """Advance Task facts through short tenant-scoped worker transactions."""

    def __init__(
        self,
        database: Database,
        *,
        task_repository: TaskRunRepository | None = None,
        state_repository: TaskExecutionStateRepository | None = None,
        admission_service: TaskAdmissionService | None = None,
        ticket_repository: TaskExecutionTicketRepository | None = None,
        command_repository: TaskRunCommandRepository | None = None,
    ) -> None:
        self._database = database
        self._tasks = task_repository or TaskRunRepository()
        self._state = state_repository or TaskExecutionStateRepository()
        self._tickets = ticket_repository or TaskExecutionTicketRepository()
        self._commands = command_repository or TaskRunCommandRepository()
        self._admission = admission_service or TaskAdmissionService(
            database,
            task_repository=self._tasks,
        )

    async def load_dispatch_plan(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskDispatchPlanPayload:
        """Load the complete sealed first-attempt plan without mutating execution state."""

        tenant_id, project_id, task_run_id = _root_ids(request)
        context = _worker_context(tenant_id, f"task-plan:{task_run_id}")
        async with self._database.transaction(context) as connection:
            run = await self._tasks.get_run(connection, task_run_id)
            manifest = await self._tasks.get_manifest(connection, task_run_id)
            units = await self._tasks.list_units(connection, task_run_id)
            attempts = await self._tasks.list_first_attempts(connection, task_run_id)
            _require_exact_root(
                request,
                run=run,
                manifest_hash=(manifest.manifest_hash if manifest is not None else None),
            )
            assert run is not None
            if run.materialization_state is not TaskMaterializationState.SEALED:
                raise _worker_error("TASK_RUN_NOT_SEALED")
            if run.lifecycle not in {
                ExecutionLifecycle.QUEUED,
                ExecutionLifecycle.RUNNING,
                ExecutionLifecycle.PAUSE_REQUESTED,
                ExecutionLifecycle.PAUSED,
                ExecutionLifecycle.CANCELING,
            }:
                raise _worker_error("TASK_RUN_NOT_DISPATCHABLE")
            if (
                not 1 <= len(units) <= _MAXIMUM_UNITS
                or len(units) != len(attempts)
                or run.materialized_unit_count != len(units)
                or run.materialized_first_attempt_count != len(attempts)
            ):
                raise _worker_error("TASK_MATERIALIZATION_INCOMPLETE")

            attempt_by_unit = {attempt.execution_unit_id: attempt for attempt in attempts}
            if len(attempt_by_unit) != len(attempts):
                raise _worker_error("TASK_FIRST_ATTEMPT_DUPLICATE")
            now = await _database_now(connection)
            dispatches: list[TaskUnitDispatchPayload] = []
            for expected_ordinal, unit in enumerate(units, start=1):
                attempt = attempt_by_unit.get(unit.id)
                if attempt is None:
                    raise _worker_error("TASK_FIRST_ATTEMPT_MISSING")
                _require_exact_attempt(
                    request,
                    unit=unit,
                    attempt=attempt,
                    expected_ordinal=expected_ordinal,
                )
                if attempt.temporal_namespace != run.temporal_namespace:
                    raise _worker_error("TASK_ATTEMPT_NAMESPACE_MISMATCH")
                remaining_seconds = int((attempt.execution_deadline - now).total_seconds())
                dispatches.append(
                    TaskUnitDispatchPayload(
                        ordinal=unit.ordinal,
                        execution_unit_id=str(unit.id),
                        unit_attempt_id=str(attempt.id),
                        unit_attempt_workflow_id=unit_attempt_workflow_id(
                            tenant_id=tenant_id,
                            unit_attempt_id=attempt.id,
                        ),
                        not_before=attempt.queued_at.isoformat(),
                        execution_deadline=attempt.execution_deadline.isoformat(),
                        activity_timeout_seconds=min(3_600, max(1, remaining_seconds)),
                    )
                )
            return TaskDispatchPlanPayload(
                tenant_id=str(tenant_id),
                project_id=str(project_id),
                task_run_id=str(task_run_id),
                request_digest=request.request_digest,
                manifest_hash=request.manifest_hash,
                units=tuple(dispatches),
                cancel_requested=run.lifecycle is ExecutionLifecycle.CANCELING,
            )

    async def prepare_batch(
        self,
        request: TaskBatchPrepareInput,
    ) -> TaskBatchPreparePayload:
        """Atomically authorize one bounded batch before any Child can start."""

        if not 1 <= len(request.attempts) <= TASK_RUN_CHILD_BATCH_SIZE:
            raise _worker_error("TASK_ROOT_BATCH_SIZE_INVALID")
        tenant_id, _, task_run_id = _root_ids(request.request)
        context = _worker_context(tenant_id, f"task-batch-prepare:{task_run_id}")
        async with self._database.transaction(context) as connection:
            run = await self._tasks.get_run_for_update(connection, task_run_id)
            manifest = await self._tasks.get_manifest(connection, task_run_id)
            _require_exact_root(
                request.request,
                run=run,
                manifest_hash=(manifest.manifest_hash if manifest is not None else None),
            )
            assert run is not None
            if run.lifecycle in {
                ExecutionLifecycle.PAUSE_REQUESTED,
                ExecutionLifecycle.PAUSED,
            }:
                return TaskBatchPreparePayload(status="PAUSE_REQUESTED")
            if run.lifecycle is ExecutionLifecycle.CANCELING:
                return TaskBatchPreparePayload(status="CANCEL_REQUESTED")
            if run.lifecycle not in {
                ExecutionLifecycle.QUEUED,
                ExecutionLifecycle.RUNNING,
            }:
                raise _worker_error("TASK_RUN_NOT_DISPATCHABLE")

            now = await _database_now(connection)
            seen_attempts: set[UUID] = set()
            for attempt_request in request.attempts:
                (
                    attempt_tenant_id,
                    _project_id,
                    attempt_run_id,
                    unit_id,
                    attempt_id,
                ) = _attempt_ids(attempt_request)
                if (
                    attempt_tenant_id != tenant_id
                    or attempt_run_id != task_run_id
                    or attempt_id in seen_attempts
                ):
                    raise _worker_error("TASK_ROOT_BATCH_IDENTITY_INVALID")
                seen_attempts.add(attempt_id)
                unit = await self._tasks.get_unit_for_update(connection, unit_id)
                attempt = await self._tasks.get_attempt_for_update(connection, attempt_id)
                if unit is None or attempt is None:
                    raise _worker_error("TASK_ATTEMPT_SCOPE_MISSING")
                _require_exact_attempt_request(
                    attempt_request,
                    run=run,
                    unit=unit,
                    attempt=attempt,
                )
                existing = await self._tickets.get_by_attempt(connection, attempt.id)
                if existing is not None:
                    _require_exact_execution_ticket(
                        attempt_request,
                        run=run,
                        unit=unit,
                        attempt=attempt,
                        ticket=existing,
                    )
                    continue
                retry_attempt = await self._require_dispatchable_attempt(
                    connection,
                    unit=unit,
                    attempt=attempt,
                )
                if attempt.lifecycle is not ExecutionLifecycle.QUEUED:
                    raise _worker_error("TASK_ATTEMPT_NOT_QUEUED")
                if now < attempt.queued_at:
                    raise _worker_error("TASK_ATTEMPT_NOT_READY")
                if now >= attempt.execution_deadline:
                    raise _worker_error("TASK_ATTEMPT_DEADLINE_EXPIRED")
                try:
                    snapshot = await self._admission.admit_loaded_unit_in_transaction(
                        connection,
                        run=run,
                        unit=unit,
                        allow_running_retry=retry_attempt,
                    )
                except ApplicationError as error:
                    if error.error_code is not ErrorCode.CONSTRAINT_UNSATISFIED:
                        raise
                    raise _worker_error("TASK_ADMISSION_REJECTED") from None
                ticket = _build_execution_ticket(
                    attempt_request,
                    run=run,
                    attempt=attempt,
                    snapshot=snapshot,
                    created_at=now,
                )
                stored = (await self._tickets.create(connection, ticket)).fact
                _require_exact_execution_ticket(
                    attempt_request,
                    run=run,
                    unit=unit,
                    attempt=attempt,
                    ticket=stored,
                )
            return TaskBatchPreparePayload(status="AUTHORIZED")

    async def checkpoint_control(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskRunControlCheckpointPayload:
        """Apply Pause/Resume only at a Root dispatch boundary."""

        tenant_id, _, task_run_id = _root_ids(request)
        context = _worker_context(tenant_id, f"task-control:{task_run_id}")
        async with self._database.transaction(context) as connection:
            run = await self._tasks.get_run_for_update(connection, task_run_id)
            manifest = await self._tasks.get_manifest(connection, task_run_id)
            _require_exact_root(
                request,
                run=run,
                manifest_hash=(manifest.manifest_hash if manifest is not None else None),
            )
            assert run is not None
            if run.lifecycle is ExecutionLifecycle.CANCELING:
                return TaskRunControlCheckpointPayload(state="CANCELING")
            if run.lifecycle in {
                ExecutionLifecycle.FINALIZING,
                ExecutionLifecycle.CLOSED,
            }:
                return TaskRunControlCheckpointPayload(state="CLOSED")
            command = await self._commands.get_open_for_run(
                connection,
                task_run_id=run.id,
            )
            if run.lifecycle is ExecutionLifecycle.PAUSE_REQUESTED:
                command = _require_control_command(
                    command,
                    command_type=TaskRunCommandType.PAUSE,
                )
                run = await self._transition_run(
                    connection,
                    run,
                    lifecycle=ExecutionLifecycle.PAUSED,
                    quality=run.quality,
                    started_at=run.started_at,
                    finalized_at=run.finalized_at,
                    closed_at=run.closed_at,
                    event_type="task_run.paused",
                    payload=_control_command_payload(command),
                )
                if not await self._commands.apply_pause(
                    connection,
                    intent_id=command.id,
                    command_digest=command.command_digest,
                ):
                    raise _worker_error("TASK_PAUSE_COMMAND_APPLY_FAILED")
                return TaskRunControlCheckpointPayload(state="PAUSED")
            if run.lifecycle is ExecutionLifecycle.PAUSED:
                if command is None:
                    return TaskRunControlCheckpointPayload(state="PAUSED")
                command = _require_control_command(
                    command,
                    command_type=TaskRunCommandType.RESUME,
                )
                await self._transition_run(
                    connection,
                    run,
                    lifecycle=ExecutionLifecycle.RUNNING,
                    quality=run.quality,
                    started_at=run.started_at,
                    finalized_at=run.finalized_at,
                    closed_at=run.closed_at,
                    event_type="task_run.resumed",
                    payload=_control_command_payload(command),
                )
                if not await self._commands.apply_resume(
                    connection,
                    intent_id=command.id,
                    command_digest=command.command_digest,
                ):
                    raise _worker_error("TASK_RESUME_COMMAND_APPLY_FAILED")
                return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
            if command is not None:
                raise _worker_error("TASK_CONTROL_COMMAND_STATE_CONFLICT")
            if run.lifecycle in {
                ExecutionLifecycle.QUEUED,
                ExecutionLifecycle.RUNNING,
            }:
                return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
            raise _worker_error("TASK_RUN_NOT_DISPATCHABLE")

    async def settle_attempt_batch(
        self,
        request: TaskAttemptBatchSettleInput,
    ) -> TaskAttemptBatchSettlePayload:
        """Atomically finalize completed Units or append exact infra retry Attempts."""

        if not 1 <= len(request.outcomes) <= TASK_RUN_CHILD_BATCH_SIZE:
            raise _worker_error("TASK_ATTEMPT_BATCH_SIZE_INVALID")
        if len({outcome.ordinal for outcome in request.outcomes}) != len(request.outcomes):
            raise _worker_error("TASK_ATTEMPT_BATCH_DUPLICATE")
        tenant_id, _, task_run_id = _root_ids(request.request)
        context = _worker_context(tenant_id, f"task-attempt-settle:{task_run_id}")
        async with self._database.transaction(context) as connection:
            run = await self._tasks.get_run_for_update(connection, task_run_id)
            manifest = await self._tasks.get_manifest(connection, task_run_id)
            _require_exact_root(
                request.request,
                run=run,
                manifest_hash=(manifest.manifest_hash if manifest is not None else None),
            )
            assert run is not None
            if run.lifecycle in {
                ExecutionLifecycle.PAUSE_REQUESTED,
                ExecutionLifecycle.PAUSED,
            }:
                return TaskAttemptBatchSettlePayload(state="PAUSE_REQUESTED")
            if run.lifecycle is ExecutionLifecycle.CANCELING:
                return TaskAttemptBatchSettlePayload(state="CANCEL_REQUESTED")
            if run.lifecycle not in {
                ExecutionLifecycle.QUEUED,
                ExecutionLifecycle.RUNNING,
            }:
                raise _worker_error("TASK_RUN_NOT_DISPATCHABLE")
            if manifest is None:
                raise _worker_error("TASK_RUN_MANIFEST_MISSING")

            now = await _database_now(connection)
            events = await self._list_events(connection, task_run_id=run.id)
            retry_count = await self._tasks.count_retry_attempts(connection, run.id)
            retry_attempts: list[TaskUnitDispatchPayload] = []
            final_outcomes: list[TaskAttemptWorkflowPayload] = []
            policy = manifest.retry_policy

            for outcome in sorted(request.outcomes, key=lambda item: item.ordinal):
                try:
                    unit_id = UUID(outcome.execution_unit_id)
                    attempt_id = UUID(outcome.unit_attempt_id)
                except ValueError:
                    raise _worker_error("TASK_ATTEMPT_BATCH_IDENTITY_INVALID") from None
                unit = await self._tasks.get_unit_for_update(connection, unit_id)
                attempt = await self._tasks.get_attempt_for_update(
                    connection,
                    attempt_id,
                )
                if unit is None or attempt is None:
                    raise _worker_error("TASK_ATTEMPT_SCOPE_MISSING")
                _require_exact_attempt(
                    request.request,
                    unit=unit,
                    attempt=attempt,
                    expected_ordinal=outcome.ordinal,
                )
                if attempt.id != attempt_id or unit.id != unit_id:
                    raise _worker_error("TASK_ATTEMPT_BATCH_IDENTITY_INVALID")
                quality = _outcome_quality(outcome.status)
                error_code = _safe_outcome_error_code(outcome)
                if attempt.lifecycle is not ExecutionLifecycle.CLOSED:
                    if outcome.status not in {"INCONCLUSIVE", "CANCELED"}:
                        raise _worker_error("TASK_ATTEMPT_NOT_CLOSED")
                    await self._reconcile_attempt_chain(
                        connection,
                        run=run,
                        unit=unit,
                        attempt=attempt,
                        quality=quality,
                        status=outcome.status,
                        error_code=error_code,
                        retry_after_seconds=outcome.retry_after_seconds,
                        events=events,
                        now=now,
                    )
                    final_outcomes.append(outcome)
                    continue
                if attempt.quality is not quality:
                    raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
                _require_exact_attempt_result_event(
                    events,
                    unit=unit,
                    attempt=attempt,
                    quality=quality,
                    status=outcome.status,
                    error_code=error_code,
                    retry_after_seconds=outcome.retry_after_seconds,
                )
                if unit.lifecycle is ExecutionLifecycle.CLOSED:
                    if unit.quality is not quality:
                        raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
                    final_outcomes.append(outcome)
                    continue

                existing_retry = await self._tasks.get_attempt_by_number(
                    connection,
                    execution_unit_id=unit.id,
                    attempt_number=attempt.attempt_number + 1,
                )
                if existing_retry is not None:
                    if outcome.status != "INFRA_ERROR":
                        raise _worker_error("TASK_RETRY_RESULT_CONFLICT")
                    retry_attempts.append(
                        _retry_dispatch_payload(
                            tenant_id=tenant_id,
                            unit=unit,
                            attempt=existing_retry,
                            now=now,
                        )
                    )
                    continue

                if self._can_retry_attempt(
                    outcome=outcome,
                    attempt=attempt,
                    policy=policy,
                    retry_count=retry_count,
                    now=now,
                ):
                    assert policy is not None
                    delay_seconds = _retry_delay_seconds(
                        unit=unit,
                        attempt=attempt,
                        policy=policy,
                        retry_after_seconds=outcome.retry_after_seconds,
                    )
                    not_before = now + timedelta(seconds=delay_seconds)
                    if not_before < attempt.execution_deadline:
                        next_number = attempt.attempt_number + 1
                        next_id = unit_retry_attempt_id(
                            execution_unit_id=unit.id,
                            attempt_number=next_number,
                        )
                        retry = UnitAttempt(
                            id=next_id,
                            tenant_id=attempt.tenant_id,
                            project_id=attempt.project_id,
                            task_run_id=attempt.task_run_id,
                            execution_unit_id=attempt.execution_unit_id,
                            manifest_hash=attempt.manifest_hash,
                            unit_key=attempt.unit_key,
                            case_version_id=attempt.case_version_id,
                            attempt_number=next_number,
                            lifecycle=ExecutionLifecycle.QUEUED,
                            quality=ExecutionQuality.PENDING,
                            hygiene=ExecutionHygiene.NOT_REQUIRED,
                            temporal_namespace=attempt.temporal_namespace,
                            temporal_workflow_id=unit_attempt_workflow_id(
                                tenant_id=tenant_id,
                                unit_attempt_id=next_id,
                            ),
                            queued_at=not_before,
                            execution_deadline=attempt.execution_deadline,
                            revision=1,
                            created_at=now,
                            updated_at=now,
                        )
                        retry = (await self._tasks.create_attempt(connection, retry)).fact
                        await self._append_event(
                            connection,
                            run=run,
                            projection=unit,
                            event_type="unit_attempt.retry_scheduled",
                            execution_unit_id=unit.id,
                            payload={
                                "unitAttemptId": str(retry.id),
                                "previousUnitAttemptId": str(attempt.id),
                                "attemptNumber": retry.attempt_number,
                                "notBefore": retry.queued_at.isoformat(),
                                "errorCode": error_code,
                            },
                        )
                        retry_attempts.append(
                            _retry_dispatch_payload(
                                tenant_id=tenant_id,
                                unit=unit,
                                attempt=retry,
                                now=now,
                            )
                        )
                        retry_count += 1
                        continue

                await self._close_unit_after_attempt(
                    connection,
                    run=run,
                    unit=unit,
                    attempt=attempt,
                    outcome=outcome,
                    now=now,
                )
                final_outcomes.append(outcome)

            return TaskAttemptBatchSettlePayload(
                state="SETTLED",
                retry_attempts=tuple(retry_attempts),
                final_outcomes=tuple(final_outcomes),
            )

    async def prepare_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskUnitExecutionRequest:
        """Create or replay the immutable authority required by the execution Port."""

        tenant_id, _, task_run_id, unit_id, attempt_id = _attempt_ids(request)
        context = _worker_context(tenant_id, f"task-attempt-prepare:{attempt_id}")
        async with self._database.transaction(context) as connection:
            run, unit, attempt = await self._lock_attempt_chain(
                connection,
                task_run_id=task_run_id,
                unit_id=unit_id,
                attempt_id=attempt_id,
            )
            _require_exact_attempt_request(request, run=run, unit=unit, attempt=attempt)
            existing = await self._tickets.get_by_attempt(connection, attempt.id)
            if existing is not None:
                _require_exact_execution_ticket(
                    request,
                    run=run,
                    unit=unit,
                    attempt=attempt,
                    ticket=existing,
                )
                return _execution_request(request, existing)
            if run.lifecycle not in {
                ExecutionLifecycle.QUEUED,
                ExecutionLifecycle.RUNNING,
            }:
                raise _worker_error("TASK_RUN_NOT_DISPATCHABLE")
            retry_attempt = await self._require_dispatchable_attempt(
                connection,
                unit=unit,
                attempt=attempt,
            )
            if attempt.lifecycle is not ExecutionLifecycle.QUEUED:
                raise _worker_error("TASK_ATTEMPT_NOT_QUEUED")
            now = await _database_now(connection)
            if now < attempt.queued_at:
                raise _worker_error("TASK_ATTEMPT_NOT_READY")
            if now >= attempt.execution_deadline:
                raise _worker_error("TASK_ATTEMPT_DEADLINE_EXPIRED")
            try:
                snapshot = await self._admission.admit_loaded_unit_in_transaction(
                    connection,
                    run=run,
                    unit=unit,
                    allow_running_retry=retry_attempt,
                )
            except ApplicationError as error:
                if error.error_code is not ErrorCode.CONSTRAINT_UNSATISFIED:
                    raise
                raise _worker_error("TASK_ADMISSION_REJECTED") from None
            ticket = _build_execution_ticket(
                request,
                run=run,
                attempt=attempt,
                snapshot=snapshot,
                created_at=now,
            )
            stored = (await self._tickets.create(connection, ticket)).fact
            _require_exact_execution_ticket(
                request,
                run=run,
                unit=unit,
                attempt=attempt,
                ticket=stored,
            )
            return _execution_request(request, stored)

    async def start_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskAttemptBeginPayload:
        """Atomically revalidate admission and start one exact Run/Unit/Attempt chain."""

        tenant_id, _project_id, task_run_id, unit_id, attempt_id = _attempt_ids(request)
        context = _worker_context(tenant_id, f"task-attempt-start:{attempt_id}")
        async with self._database.transaction(context) as connection:
            run, unit, attempt = await self._lock_attempt_chain(
                connection,
                task_run_id=task_run_id,
                unit_id=unit_id,
                attempt_id=attempt_id,
            )
            _require_exact_attempt_request(request, run=run, unit=unit, attempt=attempt)
            if attempt.lifecycle is ExecutionLifecycle.CLOSED:
                if attempt.quality is ExecutionQuality.CANCELED:
                    events = await self._list_events(connection, task_run_id=run.id)
                    (
                        stored_quality,
                        status,
                        error_code,
                        _retry_after_seconds,
                    ) = _stored_attempt_result_event(
                        events,
                        unit=unit,
                        attempt=attempt,
                    )
                    if stored_quality is not ExecutionQuality.CANCELED or status != "CANCELED":
                        raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
                    return TaskAttemptBeginPayload(
                        status="CANCELED",
                        error_code=error_code,
                    )
                return TaskAttemptBeginPayload(
                    status="REJECTED",
                    error_code="TASK_ATTEMPT_ALREADY_CLOSED",
                )
            if attempt.lifecycle is ExecutionLifecycle.RUNNING:
                return TaskAttemptBeginPayload(status="READY")
            if attempt.lifecycle is ExecutionLifecycle.FINALIZING:
                return TaskAttemptBeginPayload(
                    status="REJECTED",
                    error_code="TASK_ATTEMPT_ALREADY_FINALIZING",
                )
            pause_authorized = False
            if run.lifecycle is ExecutionLifecycle.PAUSE_REQUESTED:
                ticket = await self._tickets.get_by_attempt(connection, attempt.id)
                if ticket is not None:
                    _require_exact_execution_ticket(
                        request,
                        run=run,
                        unit=unit,
                        attempt=attempt,
                        ticket=ticket,
                    )
                    pause_authorized = True
            if not pause_authorized and run.lifecycle not in {
                ExecutionLifecycle.QUEUED,
                ExecutionLifecycle.RUNNING,
            }:
                return TaskAttemptBeginPayload(
                    status="CANCELED",
                    error_code="TASK_RUN_NOT_DISPATCHABLE",
                )
            try:
                retry_attempt = await self._require_dispatchable_attempt(
                    connection,
                    unit=unit,
                    attempt=attempt,
                )
            except TaskOrchestrationInvariantError:
                return TaskAttemptBeginPayload(
                    status="REJECTED",
                    error_code="TASK_ATTEMPT_NOT_QUEUED",
                )
            if attempt.lifecycle is not ExecutionLifecycle.QUEUED:
                return TaskAttemptBeginPayload(
                    status="REJECTED",
                    error_code="TASK_ATTEMPT_NOT_QUEUED",
                )
            if await _database_now(connection) >= attempt.execution_deadline:
                return TaskAttemptBeginPayload(
                    status="CANCELED",
                    error_code="TASK_ATTEMPT_DEADLINE_EXPIRED",
                )
            if not pause_authorized:
                try:
                    await self._admission.admit_loaded_unit_in_transaction(
                        connection,
                        run=run,
                        unit=unit,
                        allow_running_retry=retry_attempt,
                    )
                except ApplicationError as error:
                    if error.error_code is not ErrorCode.CONSTRAINT_UNSATISFIED:
                        raise
                    return TaskAttemptBeginPayload(
                        status="REJECTED",
                        error_code="TASK_ADMISSION_REJECTED",
                    )

            now = await _database_now(connection)
            if run.lifecycle is ExecutionLifecycle.QUEUED:
                run = await self._transition_run(
                    connection,
                    run,
                    lifecycle=ExecutionLifecycle.RUNNING,
                    quality=run.quality,
                    started_at=now,
                    finalized_at=run.finalized_at,
                    closed_at=run.closed_at,
                    event_type="task_run.started",
                )
            if not retry_attempt:
                unit = await self._transition_unit(
                    connection,
                    run,
                    unit,
                    lifecycle=ExecutionLifecycle.RUNNING,
                    quality=unit.quality,
                    started_at=now,
                    finalized_at=unit.finalized_at,
                    closed_at=unit.closed_at,
                    event_type="execution_unit.started",
                )
            await self._transition_attempt(
                connection,
                run,
                unit,
                attempt,
                lifecycle=ExecutionLifecycle.RUNNING,
                quality=attempt.quality,
                started_at=now,
                finalized_at=attempt.finalized_at,
                closed_at=attempt.closed_at,
                event_type="unit_attempt.started",
            )
            return TaskAttemptBeginPayload(status="READY")

    async def finish_attempt(
        self,
        request: TaskAttemptFinishInput,
    ) -> TaskAttemptWorkflowPayload:
        """Persist one safe physical outcome while leaving Unit settlement to Root."""

        attempt_request = request.attempt
        tenant_id, _, task_run_id, unit_id, attempt_id = _attempt_ids(attempt_request)
        quality, workflow_status, safe_code = _execution_outcome(request)
        context = _worker_context(tenant_id, f"task-attempt-finish:{attempt_id}")
        async with self._database.transaction(context) as connection:
            run, unit, attempt = await self._lock_attempt_chain(
                connection,
                task_run_id=task_run_id,
                unit_id=unit_id,
                attempt_id=attempt_id,
            )
            _require_exact_attempt_request(
                attempt_request,
                run=run,
                unit=unit,
                attempt=attempt,
            )
            if attempt.lifecycle is ExecutionLifecycle.CLOSED:
                if attempt.quality is not quality:
                    raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
                events = await self._list_events(connection, task_run_id=run.id)
                _require_exact_attempt_result_event(
                    events,
                    unit=unit,
                    attempt=attempt,
                    quality=quality,
                    status=workflow_status,
                    error_code=safe_code,
                    retry_after_seconds=request.execution.retry_after_seconds,
                )
                return _attempt_payload(
                    attempt_request,
                    status=workflow_status,
                    error_code=safe_code,
                    retry_after_seconds=request.execution.retry_after_seconds,
                )

            now = await _database_now(connection)
            if attempt.lifecycle is ExecutionLifecycle.FINALIZING:
                if attempt.quality not in {ExecutionQuality.PENDING, quality}:
                    raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
                events = await self._list_events(connection, task_run_id=run.id)
                if _attempt_result_events(events, unit=unit, attempt=attempt):
                    _require_exact_attempt_result_event(
                        events,
                        unit=unit,
                        attempt=attempt,
                        quality=quality,
                        status=workflow_status,
                        error_code=safe_code,
                        retry_after_seconds=request.execution.retry_after_seconds,
                    )
                else:
                    attempt = await self._transition_attempt(
                        connection,
                        run,
                        unit,
                        attempt,
                        lifecycle=ExecutionLifecycle.FINALIZING,
                        quality=quality,
                        started_at=attempt.started_at,
                        finalized_at=attempt.finalized_at or now,
                        closed_at=None,
                        event_type="unit_attempt.finalized",
                        payload=_attempt_result_payload(
                            status=workflow_status,
                            error_code=safe_code,
                            retry_after_seconds=request.execution.retry_after_seconds,
                        ),
                    )
            else:
                attempt = await self._transition_attempt(
                    connection,
                    run,
                    unit,
                    attempt,
                    lifecycle=ExecutionLifecycle.FINALIZING,
                    quality=quality,
                    started_at=attempt.started_at,
                    finalized_at=now,
                    closed_at=None,
                    event_type="unit_attempt.finalized",
                    payload=_attempt_result_payload(
                        status=workflow_status,
                        error_code=safe_code,
                        retry_after_seconds=request.execution.retry_after_seconds,
                    ),
                )
            await self._transition_attempt(
                connection,
                run,
                unit,
                attempt,
                lifecycle=ExecutionLifecycle.CLOSED,
                quality=quality,
                started_at=attempt.started_at,
                finalized_at=attempt.finalized_at,
                closed_at=now,
                event_type="unit_attempt.closed",
            )
            return _attempt_payload(
                attempt_request,
                status=workflow_status,
                error_code=safe_code,
                retry_after_seconds=request.execution.retry_after_seconds,
            )

    async def finish_run(self, request: TaskRunFinishInput) -> TaskRunWorkflowPayload:
        """Close a parent only after every exact first-attempt chain is durable."""

        tenant_id, _, task_run_id = _root_ids(request.request)
        context = _worker_context(tenant_id, f"task-run-finish:{task_run_id}")
        async with self._database.transaction(context) as connection:
            run = await self._tasks.get_run_for_update(connection, task_run_id)
            manifest = await self._tasks.get_manifest(connection, task_run_id)
            _require_exact_root(
                request.request,
                run=run,
                manifest_hash=(manifest.manifest_hash if manifest is not None else None),
            )
            assert run is not None
            run = await self._settle_control_before_finish(connection, run)
            if run.lifecycle is ExecutionLifecycle.CANCELING and not (request.cancel_requested):
                request = replace(request, cancel_requested=True)
            units = await self._tasks.list_units(connection, task_run_id)
            attempts = await self._tasks.list_attempts_for_run(connection, task_run_id)
            now = await _database_now(connection)
            events = (
                await self._list_events(connection, task_run_id=task_run_id)
                if run.lifecycle in {ExecutionLifecycle.FINALIZING, ExecutionLifecycle.CLOSED}
                or any(
                    unit.lifecycle in {ExecutionLifecycle.FINALIZING, ExecutionLifecycle.CLOSED}
                    for unit in units
                )
                or any(
                    attempt.lifecycle in {ExecutionLifecycle.FINALIZING, ExecutionLifecycle.CLOSED}
                    for attempt in attempts
                )
                else ()
            )
            await self._reconcile_run_children(
                connection,
                request=request,
                run=run,
                units=units,
                attempts=attempts,
                events=events,
                now=now,
            )
            result = _run_result(request)
            if run.lifecycle is ExecutionLifecycle.CLOSED:
                if run.quality is not _run_quality(result.status):
                    raise _worker_error("TASK_RUN_RESULT_CONFLICT")
                _require_exact_run_result_event(events, run=run, result=result)
                await self._apply_finish_commands(
                    connection,
                    request=request,
                    result=result,
                )
                return result

            quality = _run_quality(result.status)
            if run.quality is not ExecutionQuality.PENDING and run.quality is not quality:
                raise _worker_error("TASK_RUN_RESULT_CONFLICT")
            if run.lifecycle is ExecutionLifecycle.FINALIZING:
                if _run_result_events(events, run=run):
                    _require_exact_run_result_event(events, run=run, result=result)
                else:
                    run = await self._transition_run(
                        connection,
                        run,
                        lifecycle=ExecutionLifecycle.FINALIZING,
                        quality=quality,
                        started_at=run.started_at,
                        finalized_at=run.finalized_at or now,
                        closed_at=None,
                        event_type="task_run.finalized",
                        payload=_run_result_payload(result),
                    )
            else:
                run = await self._transition_run(
                    connection,
                    run,
                    lifecycle=ExecutionLifecycle.FINALIZING,
                    quality=quality,
                    started_at=run.started_at,
                    finalized_at=now,
                    closed_at=None,
                    event_type="task_run.finalized",
                    payload=_run_result_payload(result),
                )
            await self._transition_run(
                connection,
                run,
                lifecycle=ExecutionLifecycle.CLOSED,
                quality=quality,
                started_at=run.started_at,
                finalized_at=run.finalized_at,
                closed_at=now,
                event_type="task_run.closed",
            )
            await self._apply_finish_commands(
                connection,
                request=request,
                result=result,
            )
            return result

    async def _settle_control_before_finish(
        self,
        connection: AsyncConnection[DictRow],
        run: TaskRun,
    ) -> TaskRun:
        """Close the final-batch race without leaving Pause/Resume commands open."""

        if run.lifecycle not in {
            ExecutionLifecycle.PAUSE_REQUESTED,
            ExecutionLifecycle.PAUSED,
        }:
            return run
        command = await self._commands.get_open_for_run(
            connection,
            task_run_id=run.id,
        )
        if run.lifecycle is ExecutionLifecycle.PAUSE_REQUESTED:
            command = _require_control_command(
                command,
                command_type=TaskRunCommandType.PAUSE,
            )
            run = await self._transition_run(
                connection,
                run,
                lifecycle=ExecutionLifecycle.PAUSED,
                quality=run.quality,
                started_at=run.started_at,
                finalized_at=run.finalized_at,
                closed_at=run.closed_at,
                event_type="task_run.paused",
                payload=_control_command_payload(command),
            )
            if not await self._commands.apply_pause(
                connection,
                intent_id=command.id,
                command_digest=command.command_digest,
            ):
                raise _worker_error("TASK_PAUSE_COMMAND_APPLY_FAILED")
            command = await self._commands.get_open_for_run(
                connection,
                task_run_id=run.id,
            )
        if command is None:
            return run
        command = _require_control_command(
            command,
            command_type=TaskRunCommandType.RESUME,
        )
        run = await self._transition_run(
            connection,
            run,
            lifecycle=ExecutionLifecycle.RUNNING,
            quality=run.quality,
            started_at=run.started_at,
            finalized_at=run.finalized_at,
            closed_at=run.closed_at,
            event_type="task_run.resumed",
            payload=_control_command_payload(command),
        )
        if not await self._commands.apply_resume(
            connection,
            intent_id=command.id,
            command_digest=command.command_digest,
        ):
            raise _worker_error("TASK_RESUME_COMMAND_APPLY_FAILED")
        return run

    async def _apply_finish_commands(
        self,
        connection: AsyncConnection[DictRow],
        *,
        request: TaskRunFinishInput,
        result: TaskRunWorkflowPayload,
    ) -> None:
        """Acknowledge exact cancel commands only after canceled Run closure is durable."""

        if not request.commands:
            return
        if result.status != "CANCELED" or not request.cancel_requested:
            raise _worker_error("TASK_RUN_COMMAND_RESULT_CONFLICT")
        if len(request.commands) != 1:
            raise _worker_error("TASK_RUN_COMMAND_COUNT_INVALID")
        command = request.commands[0]
        _require_exact_finish_command(request.request, command)
        applied = await self._commands.apply_cancel(
            connection,
            intent_id=UUID(command.command_id),
            command_digest=command.command_digest,
        )
        if not applied:
            raise _worker_error("TASK_RUN_COMMAND_APPLY_FAILED")

    async def _reconcile_run_children(
        self,
        connection: AsyncConnection[DictRow],
        *,
        request: TaskRunFinishInput,
        run: TaskRun,
        units: tuple[ExecutionUnit, ...],
        attempts: tuple[UnitAttempt, ...],
        events: tuple[TaskExecutionEvent, ...],
        now: datetime,
    ) -> None:
        """Lock and safely close every Unit's latest exact Attempt before its Root."""

        expected_outcomes = len(units) - request.skipped_units
        if (
            run.materialization_state is not TaskMaterializationState.SEALED
            or not 1 <= len(units) <= _MAXIMUM_UNITS
            or len(attempts) < len(units)
            or run.materialized_unit_count != len(units)
            or run.materialized_first_attempt_count != len(units)
            or request.skipped_units < 0
            or len(request.outcomes) != expected_outcomes
        ):
            raise _worker_error("TASK_RUN_OUTCOME_COUNT_MISMATCH")
        if request.skipped_units and not request.cancel_requested:
            raise _worker_error("TASK_RUN_UNITS_SKIPPED_WITHOUT_CANCEL")

        attempts_by_unit: dict[UUID, list[UnitAttempt]] = {}
        for attempt in attempts:
            attempts_by_unit.setdefault(attempt.execution_unit_id, []).append(attempt)

        planned: list[
            tuple[ExecutionUnit, UnitAttempt, ExecutionQuality, str, str, int | None]
        ] = []
        for index, unit in enumerate(units):
            expected_ordinal = index + 1
            unit_attempts = attempts_by_unit.get(unit.id)
            if unit_attempts is None:
                raise _worker_error("TASK_FIRST_ATTEMPT_MISSING")
            unit_attempts.sort(key=lambda item: item.attempt_number)
            if tuple(item.attempt_number for item in unit_attempts) != tuple(
                range(1, len(unit_attempts) + 1)
            ):
                raise _worker_error("TASK_ATTEMPT_SEQUENCE_INVALID")
            for historical_attempt in unit_attempts:
                _require_exact_attempt(
                    request.request,
                    unit=unit,
                    attempt=historical_attempt,
                    expected_ordinal=expected_ordinal,
                )
                if historical_attempt.temporal_namespace != run.temporal_namespace:
                    raise _worker_error("TASK_ATTEMPT_NAMESPACE_MISMATCH")
            if any(
                historical.lifecycle is not ExecutionLifecycle.CLOSED
                for historical in unit_attempts[:-1]
            ):
                raise _worker_error("TASK_ATTEMPT_HISTORY_OPEN")
            attempt = unit_attempts[-1]
            status: str
            if index < expected_outcomes:
                outcome = request.outcomes[index]
                if (
                    outcome.ordinal != expected_ordinal
                    or outcome.execution_unit_id != str(unit.id)
                    or outcome.unit_attempt_id != str(attempt.id)
                ):
                    raise _worker_error("TASK_RUN_CHILD_IDENTITY_MISMATCH")
                quality = _outcome_quality(outcome.status)
                error_code = _safe_outcome_error_code(outcome)
                status = outcome.status
                retry_after_seconds = outcome.retry_after_seconds
            else:
                if len(unit_attempts) != 1:
                    raise _worker_error("TASK_RUN_SKIPPED_CHILD_CONFLICT")
                quality = ExecutionQuality.CANCELED
                error_code = "TASK_RUN_CANCELED_BEFORE_DISPATCH"
                status = "CANCELED"
                retry_after_seconds = None
            planned.append(
                (
                    unit,
                    attempt,
                    quality,
                    status,
                    error_code,
                    retry_after_seconds,
                )
            )

        for index, (
            unit_snapshot,
            attempt_snapshot,
            quality,
            status,
            error_code,
            retry_after_seconds,
        ) in enumerate(planned):
            locked_unit = await self._tasks.get_unit_for_update(
                connection,
                unit_snapshot.id,
            )
            locked_attempt = await self._tasks.get_attempt_for_update(
                connection,
                attempt_snapshot.id,
            )
            if locked_unit is None or locked_attempt is None:
                raise _worker_error("TASK_ATTEMPT_SCOPE_MISSING")
            _require_exact_attempt(
                request.request,
                unit=locked_unit,
                attempt=locked_attempt,
                expected_ordinal=index + 1,
            )
            if locked_attempt.temporal_namespace != run.temporal_namespace:
                raise _worker_error("TASK_ATTEMPT_NAMESPACE_MISMATCH")
            skipped = index >= expected_outcomes
            if skipped and not _is_queued_or_closed_canceled(
                locked_unit,
                locked_attempt,
            ):
                raise _worker_error("TASK_RUN_SKIPPED_CHILD_CONFLICT")
            if run.lifecycle is ExecutionLifecycle.CLOSED and (
                locked_unit.lifecycle is not ExecutionLifecycle.CLOSED
                or locked_attempt.lifecycle is not ExecutionLifecycle.CLOSED
            ):
                raise _worker_error("TASK_RUN_CLOSED_WITH_OPEN_CHILD")
            await self._reconcile_attempt_chain(
                connection,
                run=run,
                unit=locked_unit,
                attempt=locked_attempt,
                quality=quality,
                status=status,
                error_code=error_code,
                retry_after_seconds=retry_after_seconds,
                events=events,
                now=now,
            )

    async def _reconcile_attempt_chain(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: TaskRun,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
        quality: ExecutionQuality,
        status: str,
        error_code: str,
        retry_after_seconds: int | None,
        events: tuple[TaskExecutionEvent, ...],
        now: datetime,
    ) -> None:
        """Finish a missing child acknowledgement without inventing PASSED."""

        if quality in {ExecutionQuality.PENDING, ExecutionQuality.PASSED}:
            raise _worker_error("TASK_ATTEMPT_RESULT_INVALID")
        if attempt.lifecycle is ExecutionLifecycle.CLOSED:
            _require_exact_attempt_result_event(
                events,
                unit=unit,
                attempt=attempt,
                quality=quality,
                status=status,
                error_code=error_code,
                retry_after_seconds=retry_after_seconds,
            )
        if unit.lifecycle is ExecutionLifecycle.CLOSED:
            if (
                attempt.lifecycle is not ExecutionLifecycle.CLOSED
                or unit.quality is not quality
                or attempt.quality is not quality
            ):
                raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
            return
        if unit.quality not in {ExecutionQuality.PENDING, quality} or attempt.quality not in {
            ExecutionQuality.PENDING,
            quality,
        }:
            raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
        if unit.lifecycle not in _RECONCILABLE_LIFECYCLES or (
            attempt.lifecycle not in _RECONCILABLE_LIFECYCLES
            and attempt.lifecycle is not ExecutionLifecycle.CLOSED
        ):
            raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")

        payload = _attempt_result_payload(
            status=status,
            error_code=error_code,
            retry_after_seconds=retry_after_seconds,
        )
        if attempt.lifecycle is not ExecutionLifecycle.CLOSED:
            if attempt.lifecycle is ExecutionLifecycle.FINALIZING:
                if _attempt_result_events(events, unit=unit, attempt=attempt):
                    _require_exact_attempt_result_event(
                        events,
                        unit=unit,
                        attempt=attempt,
                        quality=quality,
                        status=status,
                        error_code=error_code,
                        retry_after_seconds=retry_after_seconds,
                    )
                else:
                    attempt = await self._transition_attempt(
                        connection,
                        run,
                        unit,
                        attempt,
                        lifecycle=ExecutionLifecycle.FINALIZING,
                        quality=quality,
                        started_at=attempt.started_at,
                        finalized_at=attempt.finalized_at or now,
                        closed_at=None,
                        event_type="unit_attempt.finalized",
                        payload=payload,
                    )
            else:
                attempt = await self._transition_attempt(
                    connection,
                    run,
                    unit,
                    attempt,
                    lifecycle=ExecutionLifecycle.FINALIZING,
                    quality=quality,
                    started_at=attempt.started_at,
                    finalized_at=attempt.finalized_at or now,
                    closed_at=None,
                    event_type="unit_attempt.finalized",
                    payload=payload,
                )
            attempt = await self._transition_attempt(
                connection,
                run,
                unit,
                attempt,
                lifecycle=ExecutionLifecycle.CLOSED,
                quality=quality,
                started_at=attempt.started_at,
                finalized_at=attempt.finalized_at,
                closed_at=attempt.closed_at or now,
                event_type="unit_attempt.closed",
            )
        elif attempt.quality is not quality:
            raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")

        if unit.lifecycle is ExecutionLifecycle.FINALIZING and unit.quality not in {
            ExecutionQuality.PENDING,
            quality,
        }:
            raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
        if unit.lifecycle is not ExecutionLifecycle.FINALIZING:
            unit = await self._transition_unit(
                connection,
                run,
                unit,
                lifecycle=ExecutionLifecycle.FINALIZING,
                quality=quality,
                started_at=unit.started_at,
                finalized_at=unit.finalized_at or now,
                closed_at=None,
                event_type="execution_unit.finalized",
                payload=payload,
            )
        await self._transition_unit(
            connection,
            run,
            unit,
            lifecycle=ExecutionLifecycle.CLOSED,
            quality=quality,
            started_at=unit.started_at,
            finalized_at=unit.finalized_at,
            closed_at=unit.closed_at or now,
            event_type="execution_unit.closed",
        )

    @staticmethod
    def _can_retry_attempt(
        *,
        outcome: TaskAttemptWorkflowPayload,
        attempt: UnitAttempt,
        policy: TaskRetryPolicy | None,
        retry_count: int,
        now: datetime,
    ) -> bool:
        """Apply the frozen per-Unit, per-Run, classification, and deadline bounds."""

        return (
            outcome.status == "INFRA_ERROR"
            and policy is not None
            and attempt.attempt_number <= policy.infra_retry_attempts
            and retry_count < policy.max_total_infra_retries
            and now < attempt.execution_deadline
        )

    async def _close_unit_after_attempt(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: TaskRun,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
        outcome: TaskAttemptWorkflowPayload,
        now: datetime,
    ) -> None:
        """Close only the logical Unit after the database has declined another retry."""

        quality = _outcome_quality(outcome.status)
        payload = _attempt_result_payload(
            status=outcome.status,
            error_code=_safe_outcome_error_code(outcome),
            retry_after_seconds=outcome.retry_after_seconds,
        )
        if unit.lifecycle is ExecutionLifecycle.FINALIZING:
            if unit.quality not in {ExecutionQuality.PENDING, quality}:
                raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
        else:
            unit = await self._transition_unit(
                connection,
                run,
                unit,
                lifecycle=ExecutionLifecycle.FINALIZING,
                quality=quality,
                started_at=unit.started_at,
                finalized_at=now,
                closed_at=None,
                event_type="execution_unit.finalized",
                payload=payload,
            )
        await self._transition_unit(
            connection,
            run,
            unit,
            lifecycle=ExecutionLifecycle.CLOSED,
            quality=quality,
            started_at=unit.started_at,
            finalized_at=unit.finalized_at or now,
            closed_at=unit.closed_at or now,
            event_type="execution_unit.closed",
        )

    async def _lock_attempt_chain(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        unit_id: UUID,
        attempt_id: UUID,
    ) -> tuple[TaskRun, ExecutionUnit, UnitAttempt]:
        run = await self._tasks.get_run_for_update(connection, task_run_id)
        unit = await self._tasks.get_unit_for_update(connection, unit_id)
        attempt = await self._tasks.get_attempt_for_update(connection, attempt_id)
        if run is None or unit is None or attempt is None:
            raise _worker_error("TASK_ATTEMPT_SCOPE_MISSING")
        return run, unit, attempt

    async def _require_dispatchable_attempt(
        self,
        connection: AsyncConnection[DictRow],
        *,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
    ) -> bool:
        """Validate either the immutable first Attempt or one exact infra retry."""

        if attempt.attempt_number == 1:
            if unit.lifecycle is not ExecutionLifecycle.QUEUED:
                raise _worker_error("TASK_ATTEMPT_NOT_QUEUED")
            return False
        if unit.lifecycle is not ExecutionLifecycle.RUNNING:
            raise _worker_error("TASK_RETRY_UNIT_NOT_RUNNING")
        previous = await self._tasks.get_attempt_by_number(
            connection,
            execution_unit_id=unit.id,
            attempt_number=attempt.attempt_number - 1,
        )
        if (
            previous is None
            or previous.lifecycle is not ExecutionLifecycle.CLOSED
            or previous.quality is not ExecutionQuality.INFRA_ERROR
        ):
            raise _worker_error("TASK_RETRY_PREVIOUS_ATTEMPT_INVALID")
        latest = await self._tasks.list_attempts(connection, unit.id)
        if not latest or latest[-1].id != attempt.id:
            raise _worker_error("TASK_RETRY_ATTEMPT_NOT_LATEST")
        return True

    async def _transition_run(
        self,
        connection: AsyncConnection[DictRow],
        run: TaskRun,
        *,
        lifecycle: ExecutionLifecycle,
        quality: ExecutionQuality,
        started_at: datetime | None,
        finalized_at: datetime | None,
        closed_at: datetime | None,
        event_type: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> TaskRun:
        updated = await self._state.transition_task_run_state(
            connection,
            task_run_id=run.id,
            expected_revision=run.revision,
            lifecycle=lifecycle,
            quality=quality,
            hygiene=run.hygiene,
            started_at=started_at,
            finalized_at=finalized_at,
            cleanup_resolved_at=run.cleanup_resolved_at,
            closed_at=closed_at,
        )
        if updated is None:
            raise _worker_error("TASK_RUN_TRANSITION_LOST")
        await self._append_event(
            connection,
            run=updated,
            projection=updated,
            event_type=event_type,
            payload=payload,
        )
        return updated

    async def _list_events(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
    ) -> tuple[TaskExecutionEvent, ...]:
        """Read the bounded event ledger without assuming one repository page."""

        events: list[TaskExecutionEvent] = []
        after_seq = 0
        while True:
            page = await self._tasks.list_events(
                connection,
                task_run_id=task_run_id,
                after_seq=after_seq,
                limit=_EVENT_PAGE_SIZE,
            )
            if not page:
                return tuple(events)
            if any(event.seq <= after_seq for event in page):
                raise _worker_error("TASK_EVENT_LEDGER_INVALID")
            events.extend(page)
            after_seq = page[-1].seq
            if len(page) < _EVENT_PAGE_SIZE:
                return tuple(events)

    async def _transition_unit(
        self,
        connection: AsyncConnection[DictRow],
        run: TaskRun,
        unit: ExecutionUnit,
        *,
        lifecycle: ExecutionLifecycle,
        quality: ExecutionQuality,
        started_at: datetime | None,
        finalized_at: datetime | None,
        closed_at: datetime | None,
        event_type: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> ExecutionUnit:
        if unit.lifecycle is lifecycle and unit.quality is quality:
            return unit
        updated = await self._state.transition_execution_unit_state(
            connection,
            task_run_id=run.id,
            execution_unit_id=unit.id,
            expected_revision=unit.revision,
            lifecycle=lifecycle,
            quality=quality,
            hygiene=unit.hygiene,
            started_at=started_at,
            finalized_at=finalized_at,
            cleanup_resolved_at=unit.cleanup_resolved_at,
            closed_at=closed_at,
        )
        if updated is None:
            raise _worker_error("TASK_UNIT_TRANSITION_LOST")
        await self._append_event(
            connection,
            run=run,
            projection=updated,
            event_type=event_type,
            execution_unit_id=unit.id,
            payload=payload,
        )
        return updated

    async def _transition_attempt(
        self,
        connection: AsyncConnection[DictRow],
        run: TaskRun,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
        *,
        lifecycle: ExecutionLifecycle,
        quality: ExecutionQuality,
        started_at: datetime | None,
        finalized_at: datetime | None,
        closed_at: datetime | None,
        event_type: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> UnitAttempt:
        if attempt.lifecycle is lifecycle and attempt.quality is quality:
            return attempt
        updated = await self._state.transition_unit_attempt_state(
            connection,
            task_run_id=run.id,
            execution_unit_id=unit.id,
            unit_attempt_id=attempt.id,
            expected_revision=attempt.revision,
            lifecycle=lifecycle,
            quality=quality,
            hygiene=attempt.hygiene,
            started_at=started_at,
            finalized_at=finalized_at,
            cleanup_resolved_at=attempt.cleanup_resolved_at,
            closed_at=closed_at,
        )
        if updated is None:
            raise _worker_error("TASK_ATTEMPT_TRANSITION_LOST")
        await self._append_event(
            connection,
            run=run,
            projection=updated,
            event_type=event_type,
            execution_unit_id=unit.id,
            unit_attempt_id=attempt.id,
            payload=payload,
        )
        return updated

    async def _append_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: TaskRun,
        projection: _Projection,
        event_type: str,
        payload: dict[str, JsonValue] | None = None,
        execution_unit_id: UUID | None = None,
        unit_attempt_id: UUID | None = None,
    ) -> None:
        seq = await self._state.next_task_execution_event_seq(
            connection,
            task_run_id=run.id,
        )
        event = TaskExecutionEvent(
            id=new_entity_id(),
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            execution_unit_id=execution_unit_id,
            unit_attempt_id=unit_attempt_id,
            seq=seq,
            event_type=event_type,
            lifecycle=projection.lifecycle,
            quality=projection.quality,
            hygiene=projection.hygiene,
            payload=payload or {},
            occurred_at=projection.updated_at,
        )
        await self._tasks.append_event(connection, event)


def _build_execution_ticket(
    request: UnitAttemptWorkflowInput,
    *,
    run: TaskRun,
    attempt: UnitAttempt,
    snapshot: TaskAdmissionSnapshot,
    created_at: datetime,
) -> TaskUnitExecutionTicket:
    unit = snapshot.unit
    case = snapshot.case_version
    execution_profile = snapshot.execution_profile
    identity_profile = snapshot.identity_profile
    browser_profile = snapshot.browser_profile
    data_profile = snapshot.data_profile
    fixture = snapshot.fixture_blueprint_version
    environment = snapshot.environment
    allowed_origins = tuple(sorted(set(environment.allowed_origins)))
    if not allowed_origins:
        raise _worker_error("TASK_ENVIRONMENT_ORIGIN_BOUNDARY_MISSING")
    digest = task_unit_execution_ticket_digest(
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        task_run_id=run.id,
        execution_unit_id=unit.id,
        unit_attempt_id=attempt.id,
        request_digest=request.request_digest,
        manifest_hash=run.manifest_hash,
        ordinal=unit.ordinal,
        unit_key=unit.unit_key,
        case_version_id=case.id,
        case_content_digest=case.content_digest,
        test_ir_digest=case.test_ir_digest,
        plan_digest=case.plan_digest,
        compiled_digest=case.compiled_digest,
        attempt_number=attempt.attempt_number,
        execution_profile_version_id=execution_profile.id,
        execution_profile_digest=execution_profile.content_digest,
        identity_profile_version_id=identity_profile.id,
        identity_profile_digest=identity_profile.content_digest,
        browser_profile_version_id=browser_profile.id,
        browser_profile_digest=browser_profile.content_digest,
        data_profile_version_id=data_profile.id,
        data_profile_digest=data_profile.content_digest,
        fixture_blueprint_version_id=fixture.id,
        fixture_blueprint_digest=fixture.content_digest,
        environment_id=environment.id,
        environment_revision=environment.revision,
        allowed_origins=allowed_origins,
        execution_deadline=attempt.execution_deadline,
    )
    return TaskUnitExecutionTicket(
        id=new_entity_id(),
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        task_run_id=run.id,
        execution_unit_id=unit.id,
        unit_attempt_id=attempt.id,
        request_digest=request.request_digest,
        manifest_hash=run.manifest_hash,
        ordinal=unit.ordinal,
        unit_key=unit.unit_key,
        case_version_id=case.id,
        case_content_digest=case.content_digest,
        test_ir_digest=case.test_ir_digest,
        plan_digest=case.plan_digest,
        compiled_digest=case.compiled_digest,
        attempt_number=attempt.attempt_number,
        execution_profile_version_id=execution_profile.id,
        execution_profile_digest=execution_profile.content_digest,
        identity_profile_version_id=identity_profile.id,
        identity_profile_digest=identity_profile.content_digest,
        browser_profile_version_id=browser_profile.id,
        browser_profile_digest=browser_profile.content_digest,
        data_profile_version_id=data_profile.id,
        data_profile_digest=data_profile.content_digest,
        fixture_blueprint_version_id=fixture.id,
        fixture_blueprint_digest=fixture.content_digest,
        environment_id=environment.id,
        environment_revision=environment.revision,
        allowed_origins=allowed_origins,
        execution_deadline=attempt.execution_deadline,
        ticket_digest=digest,
        created_at=created_at,
    )


def _require_exact_execution_ticket(
    request: UnitAttemptWorkflowInput,
    *,
    run: TaskRun,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    ticket: TaskUnitExecutionTicket,
) -> None:
    expected_digest = task_unit_execution_ticket_digest(
        **ticket.model_dump(
            mode="python",
            by_alias=False,
            exclude={"id", "schema_version", "ticket_digest", "created_at"},
        )
    )
    if (
        ticket.ticket_digest != expected_digest
        or ticket.tenant_id != run.tenant_id
        or ticket.project_id != run.project_id
        or ticket.task_run_id != run.id
        or ticket.execution_unit_id != unit.id
        or ticket.unit_attempt_id != attempt.id
        or ticket.request_digest != request.request_digest
        or ticket.manifest_hash != run.manifest_hash
        or ticket.ordinal != unit.ordinal
        or ticket.unit_key != unit.unit_key
        or ticket.case_version_id != unit.case_version_id
        or ticket.attempt_number != attempt.attempt_number
        or ticket.execution_profile_version_id != unit.execution_profile_version_id
        or ticket.identity_profile_version_id != unit.identity_profile_version_id
        or ticket.browser_profile_version_id != unit.browser_profile_version_id
        or ticket.data_profile_version_id != unit.data_profile_version_id
        or ticket.fixture_blueprint_version_id != unit.fixture_blueprint_version_id
        or ticket.environment_id != unit.environment_id
        or ticket.execution_deadline != attempt.execution_deadline
    ):
        raise _worker_error("TASK_EXECUTION_TICKET_IDENTITY_MISMATCH")


def _execution_request(
    attempt: UnitAttemptWorkflowInput,
    ticket: TaskUnitExecutionTicket,
) -> TaskUnitExecutionRequest:
    return TaskUnitExecutionRequest(
        attempt=attempt,
        ticket_id=str(ticket.id),
        ticket_digest=ticket.ticket_digest,
    )


async def _database_now(connection: AsyncConnection[DictRow]) -> datetime:
    cursor = await connection.execute("select transaction_timestamp() as observed_at")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("database clock query returned no row")
    return cast(datetime, row["observed_at"])


def _worker_context(tenant_id: UUID, request_id: str) -> DatabaseContext:
    return DatabaseContext(tenant_id=tenant_id, request_id=request_id)


def _root_ids(request: TaskRunWorkflowInput) -> tuple[UUID, UUID, UUID]:
    try:
        return UUID(request.tenant_id), UUID(request.project_id), UUID(request.task_run_id)
    except (TypeError, ValueError) as error:
        raise _worker_error("TASK_ROOT_IDENTITY_INVALID") from error


def _attempt_ids(
    request: UnitAttemptWorkflowInput,
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    try:
        return (
            UUID(request.tenant_id),
            UUID(request.project_id),
            UUID(request.task_run_id),
            UUID(request.execution_unit_id),
            UUID(request.unit_attempt_id),
        )
    except (TypeError, ValueError) as error:
        raise _worker_error("TASK_ATTEMPT_IDENTITY_INVALID") from error


def _require_exact_root(
    request: TaskRunWorkflowInput,
    *,
    run: TaskRun | None,
    manifest_hash: str | None,
) -> None:
    tenant_id, project_id, task_run_id = _root_ids(request)
    if (
        run is None
        or run.id != task_run_id
        or run.tenant_id != tenant_id
        or run.project_id != project_id
        or run.request_digest != request.request_digest
        or run.manifest_hash != request.manifest_hash
        or manifest_hash != request.manifest_hash
        or run.temporal_workflow_id
        != task_run_workflow_id(tenant_id=tenant_id, task_run_id=task_run_id)
    ):
        raise _worker_error("TASK_ROOT_IDENTITY_MISMATCH")


def _require_exact_attempt(
    request: TaskRunWorkflowInput,
    *,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    expected_ordinal: int,
) -> None:
    tenant_id, project_id, task_run_id = _root_ids(request)
    if (
        unit.ordinal != expected_ordinal
        or unit.tenant_id != tenant_id
        or unit.project_id != project_id
        or unit.task_run_id != task_run_id
        or unit.manifest_hash != request.manifest_hash
        or attempt.tenant_id != tenant_id
        or attempt.project_id != project_id
        or attempt.task_run_id != task_run_id
        or attempt.execution_unit_id != unit.id
        or attempt.manifest_hash != request.manifest_hash
        or attempt.unit_key != unit.unit_key
        or attempt.case_version_id != unit.case_version_id
        or attempt.attempt_number < 1
        or (
            attempt.attempt_number > 1
            and attempt.id
            != unit_retry_attempt_id(
                execution_unit_id=unit.id,
                attempt_number=attempt.attempt_number,
            )
        )
        or attempt.temporal_namespace is None
        or attempt.temporal_workflow_id
        != unit_attempt_workflow_id(tenant_id=tenant_id, unit_attempt_id=attempt.id)
    ):
        raise _worker_error("TASK_ATTEMPT_IDENTITY_MISMATCH")


def _require_exact_attempt_request(
    request: UnitAttemptWorkflowInput,
    *,
    run: TaskRun,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
) -> None:
    root = TaskRunWorkflowInput(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        task_run_id=request.task_run_id,
        request_digest=request.request_digest,
        manifest_hash=request.manifest_hash,
    )
    _require_exact_root(root, run=run, manifest_hash=request.manifest_hash)
    _require_exact_attempt(root, unit=unit, attempt=attempt, expected_ordinal=request.ordinal)
    if (
        unit.id != UUID(request.execution_unit_id)
        or attempt.id != UUID(request.unit_attempt_id)
        or attempt.temporal_namespace != run.temporal_namespace
        or attempt.execution_deadline.isoformat() != request.execution_deadline
    ):
        raise _worker_error("TASK_ATTEMPT_REQUEST_MISMATCH")


def _retry_delay_seconds(
    *,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    policy: TaskRetryPolicy,
    retry_after_seconds: int | None,
) -> int:
    """Calculate deterministic exponential backoff with stable bounded jitter."""

    base: int = min(
        policy.maximum_backoff_seconds,
        policy.initial_backoff_seconds * (2 ** (attempt.attempt_number - 1)),
    )
    jitter = policy.jitter_percent
    if jitter:
        seed = unit_retry_attempt_id(
            execution_unit_id=unit.id,
            attempt_number=attempt.attempt_number + 1,
        ).int
        offset_percent = (seed % (2 * jitter + 1)) - jitter
        base = max(1, round(base * (100 + offset_percent) / 100))
    if retry_after_seconds is not None:
        base = max(base, retry_after_seconds)
    return min(3_600, base)


def _retry_dispatch_payload(
    *,
    tenant_id: UUID,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    now: datetime,
) -> TaskUnitDispatchPayload:
    """Expose one stored retry Attempt as a secret-free Root dispatch fact."""

    if (
        attempt.attempt_number <= 1
        or attempt.temporal_workflow_id
        != unit_attempt_workflow_id(
            tenant_id=tenant_id,
            unit_attempt_id=attempt.id,
        )
        or attempt.lifecycle is not ExecutionLifecycle.QUEUED
        or attempt.quality is not ExecutionQuality.PENDING
        or attempt.execution_unit_id != unit.id
    ):
        raise _worker_error("TASK_RETRY_ATTEMPT_INVALID")
    remaining_seconds = int(
        (attempt.execution_deadline - max(now, attempt.queued_at)).total_seconds()
    )
    if remaining_seconds <= 0:
        raise _worker_error("TASK_RETRY_DEADLINE_EXPIRED")
    return TaskUnitDispatchPayload(
        ordinal=unit.ordinal,
        execution_unit_id=str(attempt.execution_unit_id),
        unit_attempt_id=str(attempt.id),
        unit_attempt_workflow_id=attempt.temporal_workflow_id,
        not_before=attempt.queued_at.isoformat(),
        execution_deadline=attempt.execution_deadline.isoformat(),
        activity_timeout_seconds=min(3_600, max(1, remaining_seconds)),
    )


def _execution_outcome(
    request: TaskAttemptFinishInput,
) -> tuple[ExecutionQuality, str, str | None]:
    execution = request.execution
    if execution.retry_after_seconds is not None and (
        execution.status != "INFRA_ERROR"
        or type(execution.retry_after_seconds) is not int
        or not 1 <= execution.retry_after_seconds <= 3_600
    ):
        raise _worker_error("TASK_ATTEMPT_RETRY_AFTER_INVALID")
    code = (
        execution.error_code
        if execution.error_code is not None
        and fullmatch(r"[A-Z][A-Z0-9_]{0,63}", execution.error_code) is not None
        else None
    )
    if execution.status == "EXECUTED_UNSEALED":
        return ExecutionQuality.INCONCLUSIVE, "FINISHED_UNSEALED", code or _SAFE_ERROR_CODE
    if execution.status == "FAILED":
        return ExecutionQuality.FAILED, "FAILED", code or "TASK_ATTEMPT_FAILED"
    if execution.status == "INFRA_ERROR":
        return ExecutionQuality.INFRA_ERROR, "INFRA_ERROR", code or "TASK_INFRA_ERROR"
    if execution.status == "INCONCLUSIVE":
        return ExecutionQuality.INCONCLUSIVE, "INCONCLUSIVE", code or "TASK_ATTEMPT_INCONCLUSIVE"
    if execution.status == "CANCELED":
        return ExecutionQuality.CANCELED, "CANCELED", code or "TASK_ATTEMPT_CANCELED"
    raise _worker_error("TASK_ATTEMPT_RESULT_INVALID")


def _attempt_payload(
    request: UnitAttemptWorkflowInput,
    *,
    status: str,
    error_code: str | None,
    retry_after_seconds: int | None = None,
) -> TaskAttemptWorkflowPayload:
    if status not in {
        "FINISHED_UNSEALED",
        "FAILED",
        "INFRA_ERROR",
        "INCONCLUSIVE",
        "CANCELED",
    }:
        raise _worker_error("TASK_ATTEMPT_STATUS_INVALID")
    return TaskAttemptWorkflowPayload(
        execution_unit_id=request.execution_unit_id,
        unit_attempt_id=request.unit_attempt_id,
        ordinal=request.ordinal,
        status=status,  # type: ignore[arg-type]
        error_code=error_code,
        retry_after_seconds=retry_after_seconds,
    )


def _attempt_result_payload(
    *,
    status: str,
    error_code: str | None,
    retry_after_seconds: int | None = None,
) -> dict[str, JsonValue]:
    if status not in {
        "FINISHED_UNSEALED",
        "FAILED",
        "INFRA_ERROR",
        "INCONCLUSIVE",
        "CANCELED",
    }:
        raise _worker_error("TASK_ATTEMPT_STATUS_INVALID")
    if error_code is not None and fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code) is None:
        raise _worker_error("TASK_ATTEMPT_ERROR_CODE_INVALID")
    if retry_after_seconds is not None and not 1 <= retry_after_seconds <= 3_600:
        raise _worker_error("TASK_ATTEMPT_RETRY_AFTER_INVALID")
    payload: dict[str, JsonValue] = {
        "schemaVersion": TASK_WORKFLOW_RESULT_SCHEMA,
        "status": status,
        "errorCode": error_code,
    }
    if retry_after_seconds is not None:
        payload["retryAfterSeconds"] = retry_after_seconds
    return payload


def _stored_attempt_result_event(
    events: tuple[TaskExecutionEvent, ...],
    *,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
) -> tuple[ExecutionQuality, str, str | None, int | None]:
    matches = _attempt_result_events(events, unit=unit, attempt=attempt)
    if len(matches) != 1:
        raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
    event = matches[0]
    status = event.payload.get("status")
    error_code = event.payload.get("errorCode")
    retry_after_seconds = event.payload.get("retryAfterSeconds")
    if (
        event.tenant_id != attempt.tenant_id
        or event.project_id != attempt.project_id
        or event.lifecycle is not ExecutionLifecycle.FINALIZING
        or not isinstance(status, str)
        or status
        not in {
            "FINISHED_UNSEALED",
            "FAILED",
            "INFRA_ERROR",
            "INCONCLUSIVE",
            "CANCELED",
        }
        or (error_code is not None and not isinstance(error_code, str))
        or (
            retry_after_seconds is not None
            and (type(retry_after_seconds) is not int or not 1 <= retry_after_seconds <= 3_600)
        )
        or (isinstance(error_code, str) and fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code) is None)
    ):
        raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
    canonical_payload = _attempt_result_payload(
        status=status,
        error_code=error_code,
        retry_after_seconds=retry_after_seconds,
    )
    if event.payload != canonical_payload:
        raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")
    return event.quality, status, error_code, retry_after_seconds


def _attempt_result_events(
    events: tuple[TaskExecutionEvent, ...],
    *,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
) -> tuple[TaskExecutionEvent, ...]:
    return tuple(
        event
        for event in events
        if event.event_type == "unit_attempt.finalized"
        and event.task_run_id == attempt.task_run_id
        and event.execution_unit_id == unit.id
        and event.unit_attempt_id == attempt.id
    )


def _require_exact_attempt_result_event(
    events: tuple[TaskExecutionEvent, ...],
    *,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    quality: ExecutionQuality,
    status: str,
    error_code: str | None,
    retry_after_seconds: int | None = None,
) -> None:
    (
        stored_quality,
        stored_status,
        stored_error_code,
        stored_retry_after_seconds,
    ) = _stored_attempt_result_event(
        events,
        unit=unit,
        attempt=attempt,
    )
    if (
        stored_quality is not quality
        or stored_status != status
        or stored_error_code != error_code
        or stored_retry_after_seconds != retry_after_seconds
    ):
        raise _worker_error("TASK_ATTEMPT_RESULT_CONFLICT")


def _outcome_quality(status: str) -> ExecutionQuality:
    if status == "FAILED":
        return ExecutionQuality.FAILED
    if status == "CANCELED":
        return ExecutionQuality.CANCELED
    if status == "INFRA_ERROR":
        return ExecutionQuality.INFRA_ERROR
    if status in {"FINISHED_UNSEALED", "INCONCLUSIVE"}:
        return ExecutionQuality.INCONCLUSIVE
    raise _worker_error("TASK_ATTEMPT_STATUS_INVALID")


def _safe_outcome_error_code(outcome: TaskAttemptWorkflowPayload) -> str:
    if (
        outcome.error_code is not None
        and fullmatch(r"[A-Z][A-Z0-9_]{0,63}", outcome.error_code) is not None
    ):
        return outcome.error_code
    if outcome.status == "FAILED":
        return "TASK_ATTEMPT_WORKFLOW_FAILED"
    if outcome.status == "CANCELED":
        return "TASK_ATTEMPT_WORKFLOW_CANCELED"
    if outcome.status == "INFRA_ERROR":
        return "TASK_INFRA_ERROR"
    if outcome.status == "FINISHED_UNSEALED":
        return _SAFE_ERROR_CODE
    return "TASK_ATTEMPT_WORKFLOW_INCONCLUSIVE"


def _is_queued_or_closed_canceled(
    unit: ExecutionUnit,
    attempt: UnitAttempt,
) -> bool:
    return (
        unit.lifecycle is ExecutionLifecycle.QUEUED
        and attempt.lifecycle is ExecutionLifecycle.QUEUED
        and unit.quality is ExecutionQuality.PENDING
        and attempt.quality is ExecutionQuality.PENDING
    ) or (
        unit.lifecycle is ExecutionLifecycle.CLOSED
        and attempt.lifecycle is ExecutionLifecycle.CLOSED
        and unit.quality is ExecutionQuality.CANCELED
        and attempt.quality is ExecutionQuality.CANCELED
    )


def _run_result(request: TaskRunFinishInput) -> TaskRunWorkflowPayload:
    completed = sum(item.status == "FINISHED_UNSEALED" for item in request.outcomes)
    failed = sum(item.status == "FAILED" for item in request.outcomes)
    inconclusive = sum(item.status in {"INCONCLUSIVE", "INFRA_ERROR"} for item in request.outcomes)
    canceled = sum(item.status == "CANCELED" for item in request.outcomes)
    if request.cancel_requested or request.skipped_units or canceled:
        status = "CANCELED"
    elif failed:
        status = "FAILED"
    elif inconclusive:
        status = "INCONCLUSIVE"
    else:
        status = "FINISHED_UNSEALED"
    return TaskRunWorkflowPayload(
        task_run_id=request.request.task_run_id,
        status=status,  # type: ignore[arg-type]
        completed_units=completed,
        failed_units=failed,
        inconclusive_units=inconclusive,
        canceled_units=canceled,
        skipped_units=request.skipped_units,
    )


def _run_result_payload(result: TaskRunWorkflowPayload) -> dict[str, JsonValue]:
    return {
        "schemaVersion": result.schema_version,
        "status": result.status,
        "completedUnits": result.completed_units,
        "failedUnits": result.failed_units,
        "inconclusiveUnits": result.inconclusive_units,
        "canceledUnits": result.canceled_units,
        "skippedUnits": result.skipped_units,
    }


def _require_exact_run_result_event(
    events: tuple[TaskExecutionEvent, ...],
    *,
    run: TaskRun,
    result: TaskRunWorkflowPayload,
) -> None:
    matches = _run_result_events(events, run=run)
    expected_quality = _run_quality(result.status)
    if len(matches) != 1:
        raise _worker_error("TASK_RUN_RESULT_CONFLICT")
    event = matches[0]
    if (
        event.tenant_id != run.tenant_id
        or event.project_id != run.project_id
        or event.lifecycle is not ExecutionLifecycle.FINALIZING
        or event.quality is not expected_quality
        or event.payload != _run_result_payload(result)
    ):
        raise _worker_error("TASK_RUN_RESULT_CONFLICT")


def _run_result_events(
    events: tuple[TaskExecutionEvent, ...],
    *,
    run: TaskRun,
) -> tuple[TaskExecutionEvent, ...]:
    return tuple(
        event
        for event in events
        if event.event_type == "task_run.finalized"
        and event.task_run_id == run.id
        and event.execution_unit_id is None
        and event.unit_attempt_id is None
    )


def _run_quality(status: str) -> ExecutionQuality:
    if status == "FAILED":
        return ExecutionQuality.FAILED
    if status == "CANCELED":
        return ExecutionQuality.CANCELED
    return ExecutionQuality.INCONCLUSIVE


def _require_control_command(
    command: TaskRunCommandIntent | None,
    *,
    command_type: TaskRunCommandType,
) -> TaskRunCommandIntent:
    if command is None or command.command_type is not command_type:
        raise _worker_error("TASK_CONTROL_COMMAND_STATE_CONFLICT")
    return command


def _control_command_payload(command: TaskRunCommandIntent) -> dict[str, JsonValue]:
    return {
        "commandId": str(command.id),
        "commandType": command.command_type.value,
        "commandDigest": command.command_digest,
        "acceptedRunRevision": command.accepted_run_revision,
    }


def _worker_error(code: str) -> TaskOrchestrationInvariantError:
    return TaskOrchestrationInvariantError(code)


__all__ = ["TaskUnitExecutionPort", "TaskWorkerService"]
