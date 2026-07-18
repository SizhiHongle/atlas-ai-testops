"""Durable Temporal orchestration for sealed Task runs and Unit attempts."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from re import fullmatch
from typing import Literal, Protocol, cast
from uuid import UUID

from temporalio import activity, workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import (
    ActivityError,
)
from temporalio.exceptions import (
    ApplicationError as TemporalApplicationError,
)
from temporalio.exceptions import (
    CancelledError as TemporalCancelledError,
)
from temporalio.workflow import ActivityCancellationType

from atlas_testops.domain.task import task_run_workflow_id, unit_attempt_workflow_id
from atlas_testops.orchestration.task_intents import (
    TASK_RUN_TASK_QUEUE,
    TASK_RUN_WORKFLOW_INPUT_SCHEMA,
    TASK_RUN_WORKFLOW_TYPE,
    TaskRunWorkflowInput,
)

TASK_UNIT_ATTEMPT_WORKFLOW_TYPE = "AtlasUnitAttemptWorkflow"
TASK_UNIT_ATTEMPT_TASK_QUEUE = "atlas-unit-attempt"

LOAD_TASK_DISPATCH_PLAN_ACTIVITY = "atlas.load-task-dispatch-plan/0.1"
PREPARE_TASK_RUN_BATCH_ACTIVITY = "atlas.prepare-task-run-batch/0.1"
CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY = "atlas.checkpoint-task-run-control/0.1"
SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY = "atlas.settle-task-attempt-batch/0.1"
PREPARE_TASK_UNIT_ATTEMPT_ACTIVITY = "atlas.prepare-task-unit-attempt/0.1"
BEGIN_TASK_UNIT_ATTEMPT_ACTIVITY = "atlas.begin-task-unit-attempt/0.1"
EXECUTE_TASK_UNIT_ATTEMPT_ACTIVITY = "atlas.execute-task-unit-attempt/0.1"
FINISH_TASK_UNIT_ATTEMPT_ACTIVITY = "atlas.finish-task-unit-attempt/0.1"
FINISH_TASK_RUN_ACTIVITY = "atlas.finish-task-run/0.1"

TASK_UNIT_ATTEMPT_INPUT_SCHEMA = "atlas.unit-attempt-workflow-input/0.1"
TASK_UNIT_EXECUTION_REQUEST_SCHEMA = "atlas.task-unit-execution-request/0.1"
TASK_WORKFLOW_RESULT_SCHEMA = "atlas.task-workflow-result/0.1"
TASK_RUN_COMMAND_SIGNAL_LEGACY_SCHEMA = "atlas.task-run-command-signal/0.1"
TASK_RUN_COMMAND_SIGNAL_SCHEMA = "atlas.task-run-command-signal/0.2"
TASK_RUN_COMMAND_SIGNAL = "atlas.apply-task-run-command/0.1"

TASK_RUN_MAXIMUM_UNITS = 64
TASK_RUN_CHILD_BATCH_SIZE = 8
_DATABASE_ACTIVITY_TIMEOUT = timedelta(seconds=30)
_DATABASE_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=0,
)
_SIDE_EFFECT_RETRY_POLICY = RetryPolicy(maximum_attempts=1)
_SIDE_EFFECT_HEARTBEAT_TIMEOUT = timedelta(seconds=5)
_SIDE_EFFECT_HEARTBEAT_INTERVAL_SECONDS = 1


class _SkipAttemptExecution(Exception):
    """Internal deterministic branch used after the frozen deadline expires."""


class TaskOrchestrationInvariantError(RuntimeError):
    """Safe permanent Task invariant that Temporal must never retry."""

    def __init__(self, error_code: str) -> None:
        if fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code) is None:
            raise ValueError("Task orchestration invariant code is invalid")
        self.error_code = error_code
        super().__init__(error_code)


TaskAttemptBeginStatus = Literal["READY", "CANCELED", "REJECTED"]
TaskAttemptExecutionStatus = Literal[
    "EXECUTED_UNSEALED",
    "FAILED",
    "INFRA_ERROR",
    "INCONCLUSIVE",
    "CANCELED",
]
TaskAttemptWorkflowStatus = Literal[
    "FINISHED_UNSEALED",
    "FAILED",
    "INFRA_ERROR",
    "INCONCLUSIVE",
    "CANCELED",
]
TaskRunWorkflowStatus = Literal[
    "FINISHED_UNSEALED",
    "FAILED",
    "INCONCLUSIVE",
    "CANCELED",
]
TaskBatchPrepareStatus = Literal["AUTHORIZED", "PAUSE_REQUESTED", "CANCEL_REQUESTED"]
TaskRunControlState = Literal["DISPATCHABLE", "PAUSED", "CANCELING", "CLOSED"]
TaskAttemptBatchSettleState = Literal[
    "SETTLED",
    "PAUSE_REQUESTED",
    "CANCEL_REQUESTED",
]

_SAFE_ATTEMPT_STATUSES = frozenset(
    {
        "FINISHED_UNSEALED",
        "FAILED",
        "INFRA_ERROR",
        "INCONCLUSIVE",
        "CANCELED",
    }
)
_SAFE_RUN_STATUSES = frozenset({"FINISHED_UNSEALED", "FAILED", "INCONCLUSIVE", "CANCELED"})


@dataclass(frozen=True, slots=True)
class TaskUnitDispatchPayload:
    """One database-authorized, deterministic first-attempt child dispatch."""

    ordinal: int
    execution_unit_id: str
    unit_attempt_id: str
    unit_attempt_workflow_id: str
    not_before: str
    execution_deadline: str
    activity_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class TaskDispatchPlanPayload:
    """Authoritative sealed Run projection loaded by one short Activity."""

    tenant_id: str
    project_id: str
    task_run_id: str
    request_digest: str
    manifest_hash: str
    units: tuple[TaskUnitDispatchPayload, ...]
    cancel_requested: bool = False


@dataclass(frozen=True, slots=True)
class TaskBatchPrepareInput:
    """One bounded batch that must be authorized atomically before Child start."""

    request: TaskRunWorkflowInput
    attempts: tuple[UnitAttemptWorkflowInput, ...]


@dataclass(frozen=True, slots=True)
class TaskBatchPreparePayload:
    """Database-owned dispatch gate result for one Root batch boundary."""

    status: TaskBatchPrepareStatus


@dataclass(frozen=True, slots=True)
class TaskRunControlCheckpointPayload:
    """Current database-owned Run control state observed at a safe boundary."""

    state: TaskRunControlState


@dataclass(frozen=True, slots=True)
class UnitAttemptWorkflowInput:
    """Secret-free immutable child Workflow input."""

    tenant_id: str
    project_id: str
    task_run_id: str
    request_digest: str
    manifest_hash: str
    ordinal: int
    execution_unit_id: str
    unit_attempt_id: str
    execution_deadline: str
    activity_timeout_seconds: int
    schema_version: str = TASK_UNIT_ATTEMPT_INPUT_SCHEMA


@dataclass(frozen=True, slots=True)
class TaskAttemptBeginPayload:
    """Typed admission result written before an execution side effect."""

    status: TaskAttemptBeginStatus
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class TaskUnitExecutionRequest:
    """Secret-free Port input proving that an immutable ticket was prepared."""

    attempt: UnitAttemptWorkflowInput
    ticket_id: str
    ticket_digest: str
    schema_version: str = TASK_UNIT_EXECUTION_REQUEST_SCHEMA


@dataclass(frozen=True, slots=True)
class TaskAttemptExecutionPayload:
    """Typed execution-port result; it deliberately cannot claim PASSED."""

    status: TaskAttemptExecutionStatus
    error_code: str | None = None
    retry_after_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class TaskAttemptFinishInput:
    """Safe command used to persist one execution-port outcome."""

    attempt: UnitAttemptWorkflowInput
    execution: TaskAttemptExecutionPayload


@dataclass(frozen=True, slots=True)
class TaskAttemptWorkflowPayload:
    """Child result safe for Temporal history and parent aggregation."""

    execution_unit_id: str
    unit_attempt_id: str
    ordinal: int
    status: TaskAttemptWorkflowStatus
    error_code: str | None = None
    retry_after_seconds: int | None = None
    schema_version: str = TASK_WORKFLOW_RESULT_SCHEMA


@dataclass(frozen=True, slots=True)
class TaskAttemptBatchSettleInput:
    """Completed physical Attempts awaiting Unit finalization or retry scheduling."""

    request: TaskRunWorkflowInput
    outcomes: tuple[TaskAttemptWorkflowPayload, ...]


@dataclass(frozen=True, slots=True)
class TaskAttemptBatchSettlePayload:
    """Database-owned decision for one completed Attempt wave."""

    state: TaskAttemptBatchSettleState
    retry_attempts: tuple[TaskUnitDispatchPayload, ...] = ()
    final_outcomes: tuple[TaskAttemptWorkflowPayload, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskRunCommandSignal:
    """Secret-free command identity accepted by the trusted dispatcher."""

    command_id: str
    tenant_id: str
    project_id: str
    task_run_id: str
    command_type: Literal["CANCEL", "PAUSE", "RESUME"]
    command_digest: str
    accepted_run_revision: int
    schema_version: str = TASK_RUN_COMMAND_SIGNAL_SCHEMA


@dataclass(frozen=True, slots=True)
class TaskRunFinishInput:
    """Bounded exact child outcomes submitted to the database authority."""

    request: TaskRunWorkflowInput
    outcomes: tuple[TaskAttemptWorkflowPayload, ...]
    cancel_requested: bool
    skipped_units: int
    commands: tuple[TaskRunCommandSignal, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskRunWorkflowPayload:
    """Root result that cannot represent an unsealed run as PASSED."""

    task_run_id: str
    status: TaskRunWorkflowStatus
    completed_units: int
    failed_units: int
    inconclusive_units: int
    canceled_units: int
    skipped_units: int
    schema_version: str = TASK_WORKFLOW_RESULT_SCHEMA


class TaskOrchestrationService(Protocol):
    """Database-backed Task state boundary implemented by the application layer."""

    async def load_dispatch_plan(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskDispatchPlanPayload: ...

    async def prepare_batch(
        self,
        request: TaskBatchPrepareInput,
    ) -> TaskBatchPreparePayload: ...

    async def checkpoint_control(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskRunControlCheckpointPayload: ...

    async def settle_attempt_batch(
        self,
        request: TaskAttemptBatchSettleInput,
    ) -> TaskAttemptBatchSettlePayload: ...

    async def start_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskAttemptBeginPayload: ...

    async def prepare_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskUnitExecutionRequest: ...

    async def finish_attempt(
        self,
        request: TaskAttemptFinishInput,
    ) -> TaskAttemptWorkflowPayload: ...

    async def finish_run(
        self,
        request: TaskRunFinishInput,
    ) -> TaskRunWorkflowPayload: ...


class TaskUnitExecutionPort(Protocol):
    """One side-effecting Unit execution boundary owned by a reviewed adapter."""

    async def execute(
        self,
        request: TaskUnitExecutionRequest,
    ) -> TaskAttemptExecutionPayload: ...


class TaskOrchestrationActivities:
    """Thin Activities around database state and the Unit execution port."""

    def __init__(
        self,
        service: TaskOrchestrationService,
        execution_port: TaskUnitExecutionPort,
    ) -> None:
        self._service = service
        self._execution_port = execution_port

    @activity.defn(name=LOAD_TASK_DISPATCH_PLAN_ACTIVITY)
    async def load_dispatch_plan(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskDispatchPlanPayload:
        try:
            plan = _decode_dispatch_plan(await self._service.load_dispatch_plan(request))
            _validate_dispatch_plan(request, plan)
            return plan
        except TaskOrchestrationInvariantError as error:
            raise _non_retryable_invariant(error) from None
        except TypeError, ValueError:
            raise _non_retryable_activity_payload("TASK_ROOT_DISPATCH_PLAN_INVALID") from None
        except Exception:
            raise _retryable_database_failure() from None

    @activity.defn(name=PREPARE_TASK_RUN_BATCH_ACTIVITY)
    async def prepare_batch(
        self,
        request: TaskBatchPrepareInput,
    ) -> TaskBatchPreparePayload:
        try:
            result = _decode_batch_prepare_payload(await self._service.prepare_batch(request))
            _validate_batch_prepare_payload(request, result)
            return result
        except TaskOrchestrationInvariantError as error:
            raise _non_retryable_invariant(error) from None
        except TypeError, ValueError:
            raise _non_retryable_activity_payload(
                "TASK_ROOT_BATCH_PREPARE_RESULT_INVALID"
            ) from None
        except Exception:
            raise _retryable_database_failure() from None

    @activity.defn(name=CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY)
    async def checkpoint_control(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskRunControlCheckpointPayload:
        try:
            result = _decode_control_checkpoint_payload(
                await self._service.checkpoint_control(request)
            )
            _validate_control_checkpoint_payload(result)
            return result
        except TaskOrchestrationInvariantError as error:
            raise _non_retryable_invariant(error) from None
        except TypeError, ValueError:
            raise _non_retryable_activity_payload("TASK_ROOT_CONTROL_CHECKPOINT_INVALID") from None
        except Exception:
            raise _retryable_database_failure() from None

    @activity.defn(name=SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY)
    async def settle_attempt_batch(
        self,
        request: TaskAttemptBatchSettleInput,
    ) -> TaskAttemptBatchSettlePayload:
        try:
            result = _decode_attempt_batch_settle_payload(
                await self._service.settle_attempt_batch(request)
            )
            _validate_attempt_batch_settlement(request, result)
            return result
        except TaskOrchestrationInvariantError as error:
            raise _non_retryable_invariant(error) from None
        except TypeError, ValueError:
            raise _non_retryable_activity_payload("TASK_ATTEMPT_BATCH_SETTLEMENT_INVALID") from None
        except Exception:
            raise _retryable_database_failure() from None

    @activity.defn(name=BEGIN_TASK_UNIT_ATTEMPT_ACTIVITY)
    async def begin_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskAttemptBeginPayload:
        try:
            begin = _decode_begin_payload(await self._service.start_attempt(request))
            _validate_begin_payload(begin)
            return begin
        except TaskOrchestrationInvariantError as error:
            raise _non_retryable_invariant(error) from None
        except TypeError, ValueError:
            raise _non_retryable_activity_payload("TASK_ATTEMPT_ADMISSION_RESULT_INVALID") from None
        except Exception:
            raise _retryable_database_failure() from None

    @activity.defn(name=PREPARE_TASK_UNIT_ATTEMPT_ACTIVITY)
    async def prepare_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskUnitExecutionRequest:
        try:
            prepared = _decode_execution_request(await self._service.prepare_attempt(request))
            _validate_execution_request(request, prepared)
            return prepared
        except TaskOrchestrationInvariantError as error:
            raise _non_retryable_invariant(error) from None
        except TypeError, ValueError:
            raise _non_retryable_activity_payload("TASK_ATTEMPT_EXECUTION_TICKET_INVALID") from None
        except Exception:
            raise _retryable_database_failure() from None

    @activity.defn(name=EXECUTE_TASK_UNIT_ATTEMPT_ACTIVITY)
    async def execute_attempt(
        self,
        request: TaskUnitExecutionRequest,
    ) -> TaskAttemptExecutionPayload:
        heartbeat_task = asyncio.create_task(self._heartbeat())
        try:
            execution = _decode_execution_payload(await self._execution_port.execute(request))
            _validate_execution_payload(execution)
            return execution
        except Exception:
            raise _non_retryable_execution_failure() from None
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

    @activity.defn(name=FINISH_TASK_UNIT_ATTEMPT_ACTIVITY)
    async def finish_attempt(
        self,
        request: TaskAttemptFinishInput,
    ) -> TaskAttemptWorkflowPayload:
        try:
            result = _decode_attempt_result(await self._service.finish_attempt(request))
            _validate_attempt_result(request.attempt, result, execution=request.execution)
            return result
        except TaskOrchestrationInvariantError as error:
            raise _non_retryable_invariant(error) from None
        except TypeError, ValueError:
            raise _non_retryable_activity_payload("TASK_ATTEMPT_FINISH_RESULT_INVALID") from None
        except Exception:
            raise _retryable_database_failure() from None

    @activity.defn(name=FINISH_TASK_RUN_ACTIVITY)
    async def finish_run(
        self,
        request: TaskRunFinishInput,
    ) -> TaskRunWorkflowPayload:
        try:
            result = _decode_run_result(await self._service.finish_run(request))
            _validate_run_result(
                request,
                result,
                total_units=len(request.outcomes) + request.skipped_units,
            )
            return result
        except TaskOrchestrationInvariantError as error:
            raise _non_retryable_invariant(error) from None
        except TypeError, ValueError:
            raise _non_retryable_activity_payload("TASK_RUN_FINISH_RESULT_INVALID") from None
        except Exception:
            raise _retryable_database_failure() from None

    @staticmethod
    async def _heartbeat() -> None:
        while True:
            activity.heartbeat("task unit execution active")
            await asyncio.sleep(_SIDE_EFFECT_HEARTBEAT_INTERVAL_SECONDS)


def _non_retryable_invariant(
    error: TaskOrchestrationInvariantError,
) -> TemporalApplicationError:
    return TemporalApplicationError(
        error.error_code,
        type="TaskOrchestrationInvariantError",
        non_retryable=True,
    )


def _non_retryable_execution_failure() -> TemporalApplicationError:
    return TemporalApplicationError(
        "TASK_UNIT_EXECUTION_ADAPTER_FAILED",
        type="TaskUnitExecutionAdapterError",
        non_retryable=True,
    )


def _non_retryable_activity_payload(error_code: str) -> TemporalApplicationError:
    return TemporalApplicationError(
        error_code,
        type="TaskActivityPayloadError",
        non_retryable=True,
    )


def _retryable_database_failure() -> TemporalApplicationError:
    return TemporalApplicationError(
        "TASK_DATABASE_ACTIVITY_RETRYABLE",
        type="TaskDatabaseTransientError",
    )


def _non_retryable_workflow_validation(error_code: str) -> TemporalApplicationError:
    return TemporalApplicationError(
        error_code,
        type="TaskWorkflowValidationError",
        non_retryable=True,
    )


@workflow.defn(name=TASK_UNIT_ATTEMPT_WORKFLOW_TYPE)
class AtlasUnitAttemptWorkflow:
    """Admit, execute once, and persist one physical Unit attempt."""

    def __init__(self) -> None:
        self._cancel_requested = False

    @workflow.signal
    async def request_cancel(self) -> None:
        self._cancel_requested = True

    def _is_cancel_requested(self) -> bool:
        return self._cancel_requested

    @workflow.run
    async def run(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskAttemptWorkflowPayload:
        try:
            _validate_attempt_input(request)
        except TypeError, ValueError:
            raise _non_retryable_workflow_validation(
                "TASK_ATTEMPT_WORKFLOW_INPUT_INVALID"
            ) from None
        execution = TaskAttemptExecutionPayload(status="INCONCLUSIVE")
        try:
            raw_prepared = await workflow.execute_activity(
                PREPARE_TASK_UNIT_ATTEMPT_ACTIVITY,
                request,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
            prepared = _decode_execution_request(raw_prepared)
            _validate_execution_request(request, prepared)
            raw_begin = await workflow.execute_activity(
                BEGIN_TASK_UNIT_ATTEMPT_ACTIVITY,
                request,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
            begin = _decode_begin_payload(raw_begin)
            _validate_begin_payload(begin)
            if self._is_cancel_requested() or begin.status == "CANCELED":
                execution = TaskAttemptExecutionPayload(
                    status="CANCELED",
                    error_code=begin.error_code or "TASK_ATTEMPT_CANCELED",
                )
            elif begin.status == "REJECTED":
                execution = TaskAttemptExecutionPayload(
                    status="INCONCLUSIVE",
                    error_code=begin.error_code or "TASK_ATTEMPT_ADMISSION_REJECTED",
                )
            else:
                remaining = datetime.fromisoformat(request.execution_deadline) - workflow.now()
                if remaining <= timedelta(0):
                    execution = TaskAttemptExecutionPayload(
                        status="CANCELED",
                        error_code="TASK_ATTEMPT_DEADLINE_EXPIRED",
                    )
                    raise _SkipAttemptExecution
                execution_timeout = min(
                    timedelta(seconds=request.activity_timeout_seconds),
                    remaining,
                )
                raw_execution = await workflow.execute_activity(
                    EXECUTE_TASK_UNIT_ATTEMPT_ACTIVITY,
                    prepared,
                    schedule_to_close_timeout=remaining,
                    start_to_close_timeout=execution_timeout,
                    heartbeat_timeout=min(
                        _SIDE_EFFECT_HEARTBEAT_TIMEOUT,
                        execution_timeout,
                    ),
                    retry_policy=_SIDE_EFFECT_RETRY_POLICY,
                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                execution = _decode_execution_payload(raw_execution)
                _validate_execution_payload(execution)
        except _SkipAttemptExecution:
            pass
        except asyncio.CancelledError:
            self._cancel_requested = True
            execution = TaskAttemptExecutionPayload(
                status="INCONCLUSIVE",
                error_code="TASK_ATTEMPT_EXECUTION_CANCELED_UNKNOWN",
            )
        except Exception as error:
            execution = TaskAttemptExecutionPayload(
                status="INCONCLUSIVE",
                error_code=(
                    "TASK_ATTEMPT_EXECUTION_CANCELED_UNKNOWN"
                    if _is_cancellation_failure(error)
                    else "TASK_ATTEMPT_ACTIVITY_FAILED"
                ),
            )

        finish_input = TaskAttemptFinishInput(attempt=request, execution=execution)
        try:
            raw_finished = await workflow.execute_activity(
                FINISH_TASK_UNIT_ATTEMPT_ACTIVITY,
                finish_input,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
        except asyncio.CancelledError:
            self._cancel_requested = True
            raw_finished = await workflow.execute_activity(
                FINISH_TASK_UNIT_ATTEMPT_ACTIVITY,
                finish_input,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
        except Exception as error:
            if not _is_cancellation_failure(error):
                raise
            self._cancel_requested = True
            raw_finished = await workflow.execute_activity(
                FINISH_TASK_UNIT_ATTEMPT_ACTIVITY,
                finish_input,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
        try:
            finished = _decode_attempt_result(raw_finished)
            _validate_attempt_result(request, finished, execution=execution)
        except TypeError, ValueError:
            raise _non_retryable_workflow_validation(
                "TASK_ATTEMPT_WORKFLOW_RESULT_INVALID"
            ) from None
        return finished


@workflow.defn(name=TASK_RUN_WORKFLOW_TYPE)
class AtlasTaskRunWorkflow:
    """Load one sealed Run and durably schedule its bounded Unit children."""

    def __init__(self) -> None:
        self._cancel_requested = False
        self._control_generation = 0
        self._wait_generation = 0
        self._request: TaskRunWorkflowInput | None = None
        self._commands: dict[str, TaskRunCommandSignal] = {}
        self._pending_commands: dict[tuple[str, str, str, str], TaskRunCommandSignal] = {}
        self._active_child_tasks: tuple[asyncio.Task[object], ...] = ()

    @workflow.signal
    async def request_cancel(self) -> None:
        self._cancel_requested = True
        self._cancel_active_children()

    @workflow.signal(name=TASK_RUN_COMMAND_SIGNAL)
    async def apply_command(self, raw_command: TaskRunCommandSignal) -> None:
        """Accept one dispatcher-validated command and deduplicate exact redelivery."""

        try:
            command = _decode_task_run_command_signal(raw_command)
            _validate_task_run_command_signal(command)
        except TypeError, ValueError:
            return
        if self._request is None:
            pending_key = (
                command.command_id,
                command.tenant_id,
                command.project_id,
                command.task_run_id,
            )
            existing = self._pending_commands.get(pending_key)
            if existing is None:
                self._pending_commands[pending_key] = command
            return
        if not _command_matches_root(command, self._request):
            return
        self._record_command(command)

    def _record_command(self, command: TaskRunCommandSignal) -> None:
        existing = self._commands.get(command.command_id)
        if existing is not None:
            return
        self._commands[command.command_id] = command
        self._control_generation += 1
        if command.command_type == "CANCEL":
            self._cancel_requested = True
            self._cancel_active_children()

    def _cancel_active_children(self) -> None:
        for child_task in self._active_child_tasks:
            if not child_task.done():
                child_task.cancel()

    def _control_wait_complete(self) -> bool:
        return self._control_generation != self._wait_generation or self._cancel_requested

    @workflow.run
    async def run(self, request: TaskRunWorkflowInput) -> TaskRunWorkflowPayload:
        try:
            _validate_root_input(request)
        except TypeError, ValueError:
            raise _non_retryable_workflow_validation("TASK_ROOT_WORKFLOW_INPUT_INVALID") from None
        self._request = request
        for command in self._pending_commands.values():
            if _command_matches_root(command, request):
                self._record_command(command)
        self._pending_commands = {}
        try:
            raw_plan = await workflow.execute_activity(
                LOAD_TASK_DISPATCH_PLAN_ACTIVITY,
                request,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
        except asyncio.CancelledError:
            self._cancel_requested = True
            raw_plan = await workflow.execute_activity(
                LOAD_TASK_DISPATCH_PLAN_ACTIVITY,
                request,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
        except Exception as error:
            if not _is_cancellation_failure(error):
                raise
            self._cancel_requested = True
            raw_plan = await workflow.execute_activity(
                LOAD_TASK_DISPATCH_PLAN_ACTIVITY,
                request,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
        try:
            plan = _decode_dispatch_plan(raw_plan)
            _validate_dispatch_plan(request, plan)
        except TypeError, ValueError:
            raise _non_retryable_workflow_validation("TASK_ROOT_DISPATCH_PLAN_INVALID") from None
        self._cancel_requested = self._cancel_requested or plan.cancel_requested

        pending = list(plan.units)
        unsettled: (
            tuple[
                tuple[TaskUnitDispatchPayload, ...],
                tuple[TaskAttemptWorkflowPayload, ...],
            ]
            | None
        ) = None
        final_by_ordinal: dict[int, TaskAttemptWorkflowPayload] = {}
        latest_by_ordinal: dict[int, TaskAttemptWorkflowPayload] = {}
        while pending or unsettled is not None:
            observed_generation = self._control_generation
            checkpoint = _decode_control_checkpoint_payload(
                await self._execute_database_activity(
                    CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY,
                    request,
                )
            )
            _validate_control_checkpoint_payload(checkpoint)
            if self._cancel_requested or checkpoint.state == "CANCELING":
                self._cancel_requested = True
                break
            if checkpoint.state == "CLOSED":
                raise _non_retryable_workflow_validation("TASK_ROOT_CLOSED_BEFORE_FINISH")
            if checkpoint.state == "PAUSED":
                if self._control_generation == observed_generation:
                    self._wait_generation = observed_generation
                    try:
                        await workflow.wait_condition(self._control_wait_complete)
                    except asyncio.CancelledError:
                        self._cancel_requested = True
                continue
            if unsettled is not None:
                settled_input = TaskAttemptBatchSettleInput(
                    request=request,
                    outcomes=unsettled[1],
                )
                settled = _decode_attempt_batch_settle_payload(
                    await self._execute_database_activity(
                        SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY,
                        settled_input,
                    )
                )
                _validate_attempt_batch_settlement(settled_input, settled)
                if settled.state == "CANCEL_REQUESTED":
                    self._cancel_requested = True
                    break
                if settled.state == "PAUSE_REQUESTED":
                    continue
                for outcome in settled.final_outcomes:
                    final_by_ordinal[outcome.ordinal] = outcome
                batch, _batch_outcomes = unsettled
                if tuple(pending[: len(batch)]) != batch:
                    raise _non_retryable_workflow_validation("TASK_ROOT_PENDING_BATCH_CONFLICT")
                pending = pending[len(batch) :]
                pending.extend(settled.retry_attempts)
                unsettled = None
                continue

            batch = tuple(pending[:TASK_RUN_CHILD_BATCH_SIZE])
            if await self._wait_for_batch_not_before(batch):
                continue
            child_inputs = tuple(_child_input(request, unit) for unit in batch)
            prepared = _decode_batch_prepare_payload(
                await self._execute_database_activity(
                    PREPARE_TASK_RUN_BATCH_ACTIVITY,
                    TaskBatchPrepareInput(
                        request=request,
                        attempts=child_inputs,
                    ),
                )
            )
            _validate_batch_prepare_payload(
                TaskBatchPrepareInput(request=request, attempts=child_inputs),
                prepared,
            )
            if self._cancel_requested or prepared.status == "CANCEL_REQUESTED":
                self._cancel_requested = True
                break
            if prepared.status == "PAUSE_REQUESTED":
                continue
            child_tasks = tuple(
                asyncio.create_task(
                    workflow.execute_child_workflow(
                        TASK_UNIT_ATTEMPT_WORKFLOW_TYPE,
                        child_input,
                        id=unit.unit_attempt_workflow_id,
                        task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    )
                )
                for unit, child_input in zip(batch, child_inputs, strict=True)
            )
            self._active_child_tasks = cast(tuple[asyncio.Task[object], ...], child_tasks)
            try:
                raw_outcomes = await asyncio.gather(*child_tasks, return_exceptions=True)
            except asyncio.CancelledError:
                self._cancel_requested = True
                raw_outcomes = await asyncio.gather(*child_tasks, return_exceptions=True)
            finally:
                self._active_child_tasks = ()
            batch_outcomes = tuple(
                _normalize_child_result(unit, raw_result)
                for unit, raw_result in zip(batch, raw_outcomes, strict=True)
            )
            for outcome in batch_outcomes:
                latest_by_ordinal[outcome.ordinal] = outcome
            unsettled = (batch, batch_outcomes)

        if not self._cancel_requested and len(final_by_ordinal) == len(plan.units):
            checkpoint = _decode_control_checkpoint_payload(
                await self._execute_database_activity(
                    CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY,
                    request,
                )
            )
            _validate_control_checkpoint_payload(checkpoint)
            if checkpoint.state == "CANCELING":
                self._cancel_requested = True

        if self._cancel_requested:
            outcomes = dict(final_by_ordinal)
            outcomes.update(latest_by_ordinal)
            for unit in pending:
                previous = outcomes.get(unit.ordinal)
                if previous is not None and previous.unit_attempt_id != unit.unit_attempt_id:
                    outcomes[unit.ordinal] = _pending_retry_canceled(unit)
        else:
            outcomes = final_by_ordinal
        ordered_outcomes = tuple(outcomes[index] for index in sorted(outcomes))
        if tuple(item.ordinal for item in ordered_outcomes) != tuple(
            range(1, len(ordered_outcomes) + 1)
        ):
            raise _non_retryable_workflow_validation("TASK_ROOT_OUTCOME_ORDER_INVALID")
        skipped_units = len(plan.units) - len(ordered_outcomes)
        finish_input = self._finish_input(
            request,
            outcomes=ordered_outcomes,
            skipped_units=skipped_units,
        )
        raw_finished = await _execute_finish_run_activity(finish_input)
        latest_finish_input = self._finish_input(
            request,
            outcomes=ordered_outcomes,
            skipped_units=skipped_units,
        )
        if latest_finish_input != finish_input:
            finish_input = latest_finish_input
            raw_finished = await _execute_finish_run_activity(finish_input)
        try:
            finished = _decode_run_result(raw_finished)
            validation_input = finish_input
            if finished.status == "CANCELED" and not finish_input.cancel_requested:
                validation_input = replace(finish_input, cancel_requested=True)
            _validate_run_result(
                validation_input,
                finished,
                total_units=len(plan.units),
            )
        except TypeError, ValueError:
            raise _non_retryable_workflow_validation("TASK_ROOT_WORKFLOW_RESULT_INVALID") from None
        return finished

    async def _wait_for_batch_not_before(
        self,
        batch: tuple[TaskUnitDispatchPayload, ...],
    ) -> bool:
        """Wait durably for frozen backoff while allowing control Signals to interrupt."""

        target = max(datetime.fromisoformat(unit.not_before) for unit in batch)
        remaining = target - workflow.now()
        if remaining <= timedelta(0):
            return False
        self._wait_generation = self._control_generation
        try:
            await workflow.wait_condition(
                self._control_wait_complete,
                timeout=remaining,
            )
        except TimeoutError:
            return False
        except asyncio.CancelledError:
            self._cancel_requested = True
        return True

    def _finish_input(
        self,
        request: TaskRunWorkflowInput,
        *,
        outcomes: tuple[TaskAttemptWorkflowPayload, ...],
        skipped_units: int,
    ) -> TaskRunFinishInput:
        return TaskRunFinishInput(
            request=request,
            outcomes=outcomes,
            cancel_requested=self._cancel_requested,
            skipped_units=skipped_units,
            commands=tuple(
                self._commands[command_id]
                for command_id in sorted(self._commands)
                if (
                    self._commands[command_id].command_type == "CANCEL"
                    and _command_matches_root(self._commands[command_id], request)
                )
            ),
        )

    async def _execute_database_activity(
        self,
        name: str,
        value: object,
    ) -> object:
        try:
            return await workflow.execute_activity(
                name,
                value,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
        except asyncio.CancelledError:
            self._cancel_requested = True
            self._cancel_active_children()
            return await workflow.execute_activity(
                name,
                value,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )
        except Exception as error:
            if not _is_cancellation_failure(error):
                raise
            self._cancel_requested = True
            self._cancel_active_children()
            return await workflow.execute_activity(
                name,
                value,
                start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
                retry_policy=_DATABASE_RETRY_POLICY,
            )


async def _execute_finish_run_activity(
    finish_input: TaskRunFinishInput,
) -> object:
    try:
        return await workflow.execute_activity(
            FINISH_TASK_RUN_ACTIVITY,
            finish_input,
            start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
            retry_policy=_DATABASE_RETRY_POLICY,
        )
    except asyncio.CancelledError:
        return await workflow.execute_activity(
            FINISH_TASK_RUN_ACTIVITY,
            finish_input,
            start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
            retry_policy=_DATABASE_RETRY_POLICY,
        )
    except Exception as error:
        if not _is_cancellation_failure(error):
            raise
        return await workflow.execute_activity(
            FINISH_TASK_RUN_ACTIVITY,
            finish_input,
            start_to_close_timeout=_DATABASE_ACTIVITY_TIMEOUT,
            retry_policy=_DATABASE_RETRY_POLICY,
        )


def _child_input(
    request: TaskRunWorkflowInput,
    unit: TaskUnitDispatchPayload,
) -> UnitAttemptWorkflowInput:
    return UnitAttemptWorkflowInput(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        task_run_id=request.task_run_id,
        request_digest=request.request_digest,
        manifest_hash=request.manifest_hash,
        ordinal=unit.ordinal,
        execution_unit_id=unit.execution_unit_id,
        unit_attempt_id=unit.unit_attempt_id,
        execution_deadline=unit.execution_deadline,
        activity_timeout_seconds=unit.activity_timeout_seconds,
    )


def _normalize_child_result(
    unit: TaskUnitDispatchPayload,
    raw_result: object | BaseException,
) -> TaskAttemptWorkflowPayload:
    if isinstance(raw_result, asyncio.CancelledError):
        return _unknown_canceled_child(unit)
    if isinstance(raw_result, BaseException):
        return _failed_child(unit, canceled=False)
    try:
        result = _decode_attempt_result(raw_result)
        _validate_attempt_result_identity(unit, result)
    except TypeError, ValueError:
        return _failed_child(unit, canceled=False)
    return result


def _unknown_canceled_child(
    unit: TaskUnitDispatchPayload,
) -> TaskAttemptWorkflowPayload:
    return TaskAttemptWorkflowPayload(
        execution_unit_id=unit.execution_unit_id,
        unit_attempt_id=unit.unit_attempt_id,
        ordinal=unit.ordinal,
        status="INCONCLUSIVE",
        error_code="TASK_ATTEMPT_WORKFLOW_CANCELED_UNKNOWN",
    )


def _pending_retry_canceled(
    unit: TaskUnitDispatchPayload,
) -> TaskAttemptWorkflowPayload:
    return TaskAttemptWorkflowPayload(
        execution_unit_id=unit.execution_unit_id,
        unit_attempt_id=unit.unit_attempt_id,
        ordinal=unit.ordinal,
        status="CANCELED",
        error_code="TASK_RUN_CANCELED_BEFORE_RETRY",
    )


def _is_cancellation_failure(error: BaseException) -> bool:
    """Recognize SDK cancellation wrappers without trusting their messages."""

    current: BaseException | None = error
    while current is not None:
        if isinstance(current, (asyncio.CancelledError, TemporalCancelledError)):
            return True
        if not isinstance(current, ActivityError):
            return False
        current = current.__cause__
    return False


def _decode_dispatch_plan(raw: object) -> TaskDispatchPlanPayload:
    if isinstance(raw, TaskDispatchPlanPayload):
        return raw
    required_fields = {
        "tenant_id",
        "project_id",
        "task_run_id",
        "request_digest",
        "manifest_hash",
        "units",
    }
    if (
        not isinstance(raw, dict)
        or not required_fields <= set(raw)
        or set(raw) - (required_fields | {"cancel_requested"})
    ):
        raise TypeError("Task dispatch plan has an invalid payload shape")
    payload = cast(dict[str, object], raw)
    raw_units = payload["units"]
    if not isinstance(raw_units, (list, tuple)):
        raise TypeError("Task dispatch plan Units are invalid")
    return TaskDispatchPlanPayload(
        tenant_id=_string_field(payload["tenant_id"]),
        project_id=_string_field(payload["project_id"]),
        task_run_id=_string_field(payload["task_run_id"]),
        request_digest=_string_field(payload["request_digest"]),
        manifest_hash=_string_field(payload["manifest_hash"]),
        units=tuple(_decode_dispatch_unit(unit) for unit in raw_units),
        cancel_requested=_boolean_field(payload.get("cancel_requested", False)),
    )


def _decode_batch_prepare_payload(raw: object) -> TaskBatchPreparePayload:
    if isinstance(raw, TaskBatchPreparePayload):
        return raw
    payload = _exact_mapping(raw, {"status"})
    return TaskBatchPreparePayload(
        status=cast(TaskBatchPrepareStatus, _string_field(payload["status"]))
    )


def _decode_control_checkpoint_payload(
    raw: object,
) -> TaskRunControlCheckpointPayload:
    if isinstance(raw, TaskRunControlCheckpointPayload):
        return raw
    payload = _exact_mapping(raw, {"state"})
    return TaskRunControlCheckpointPayload(
        state=cast(TaskRunControlState, _string_field(payload["state"]))
    )


def _decode_attempt_batch_settle_payload(
    raw: object,
) -> TaskAttemptBatchSettlePayload:
    if isinstance(raw, TaskAttemptBatchSettlePayload):
        return raw
    payload = _exact_mapping(
        raw,
        {"state", "retry_attempts", "final_outcomes"},
    )
    raw_retries = payload["retry_attempts"]
    raw_outcomes = payload["final_outcomes"]
    if not isinstance(raw_retries, (list, tuple)) or not isinstance(
        raw_outcomes,
        (list, tuple),
    ):
        raise TypeError("Task Attempt settlement collections are invalid")
    return TaskAttemptBatchSettlePayload(
        state=cast(TaskAttemptBatchSettleState, _string_field(payload["state"])),
        retry_attempts=tuple(_decode_dispatch_unit(item) for item in raw_retries),
        final_outcomes=tuple(_decode_attempt_result(item) for item in raw_outcomes),
    )


def _decode_dispatch_unit(raw: object) -> TaskUnitDispatchPayload:
    if isinstance(raw, TaskUnitDispatchPayload):
        return raw
    payload = _exact_mapping(
        raw,
        {
            "ordinal",
            "execution_unit_id",
            "unit_attempt_id",
            "unit_attempt_workflow_id",
            "not_before",
            "execution_deadline",
            "activity_timeout_seconds",
        },
    )
    return TaskUnitDispatchPayload(
        ordinal=_integer_field(payload["ordinal"]),
        execution_unit_id=_string_field(payload["execution_unit_id"]),
        unit_attempt_id=_string_field(payload["unit_attempt_id"]),
        unit_attempt_workflow_id=_string_field(payload["unit_attempt_workflow_id"]),
        not_before=_string_field(payload["not_before"]),
        execution_deadline=_string_field(payload["execution_deadline"]),
        activity_timeout_seconds=_integer_field(payload["activity_timeout_seconds"]),
    )


def _decode_begin_payload(raw: object) -> TaskAttemptBeginPayload:
    if isinstance(raw, TaskAttemptBeginPayload):
        return raw
    payload = _exact_mapping(raw, {"status", "error_code"})
    return TaskAttemptBeginPayload(
        status=cast(TaskAttemptBeginStatus, _string_field(payload["status"])),
        error_code=_optional_string_field(payload["error_code"]),
    )


def _decode_execution_request(raw: object) -> TaskUnitExecutionRequest:
    if isinstance(raw, TaskUnitExecutionRequest):
        return raw
    payload = _exact_mapping(
        raw,
        {"attempt", "ticket_id", "ticket_digest", "schema_version"},
    )
    return TaskUnitExecutionRequest(
        attempt=_decode_attempt_input(payload["attempt"]),
        ticket_id=_string_field(payload["ticket_id"]),
        ticket_digest=_string_field(payload["ticket_digest"]),
        schema_version=_string_field(payload["schema_version"]),
    )


def _decode_attempt_input(raw: object) -> UnitAttemptWorkflowInput:
    if isinstance(raw, UnitAttemptWorkflowInput):
        return raw
    payload = _exact_mapping(
        raw,
        {
            "tenant_id",
            "project_id",
            "task_run_id",
            "request_digest",
            "manifest_hash",
            "ordinal",
            "execution_unit_id",
            "unit_attempt_id",
            "execution_deadline",
            "activity_timeout_seconds",
            "schema_version",
        },
    )
    return UnitAttemptWorkflowInput(
        tenant_id=_string_field(payload["tenant_id"]),
        project_id=_string_field(payload["project_id"]),
        task_run_id=_string_field(payload["task_run_id"]),
        request_digest=_string_field(payload["request_digest"]),
        manifest_hash=_string_field(payload["manifest_hash"]),
        ordinal=_integer_field(payload["ordinal"]),
        execution_unit_id=_string_field(payload["execution_unit_id"]),
        unit_attempt_id=_string_field(payload["unit_attempt_id"]),
        execution_deadline=_string_field(payload["execution_deadline"]),
        activity_timeout_seconds=_integer_field(payload["activity_timeout_seconds"]),
        schema_version=_string_field(payload["schema_version"]),
    )


def _decode_execution_payload(raw: object) -> TaskAttemptExecutionPayload:
    if isinstance(raw, TaskAttemptExecutionPayload):
        return raw
    if not isinstance(raw, dict) or set(raw) not in (
        {"status", "error_code"},
        {"status", "error_code", "retry_after_seconds"},
    ):
        raise TypeError("Task execution payload has an invalid shape")
    payload = cast(dict[str, object], raw)
    return TaskAttemptExecutionPayload(
        status=cast(TaskAttemptExecutionStatus, _string_field(payload["status"])),
        error_code=_optional_string_field(payload["error_code"]),
        retry_after_seconds=_optional_integer_field(payload.get("retry_after_seconds")),
    )


def _decode_attempt_result(raw: object) -> TaskAttemptWorkflowPayload:
    if isinstance(raw, TaskAttemptWorkflowPayload):
        return raw
    required_fields = {
        "execution_unit_id",
        "unit_attempt_id",
        "ordinal",
        "status",
        "error_code",
        "schema_version",
    }
    if (
        not isinstance(raw, dict)
        or not required_fields <= set(raw)
        or set(raw) - (required_fields | {"retry_after_seconds"})
    ):
        raise TypeError("Task Attempt result has an invalid shape")
    payload = cast(dict[str, object], raw)
    return TaskAttemptWorkflowPayload(
        execution_unit_id=_string_field(payload["execution_unit_id"]),
        unit_attempt_id=_string_field(payload["unit_attempt_id"]),
        ordinal=_integer_field(payload["ordinal"]),
        status=cast(TaskAttemptWorkflowStatus, _string_field(payload["status"])),
        error_code=_optional_string_field(payload["error_code"]),
        retry_after_seconds=_optional_integer_field(payload.get("retry_after_seconds")),
        schema_version=_string_field(payload["schema_version"]),
    )


def _decode_run_result(raw: object) -> TaskRunWorkflowPayload:
    if isinstance(raw, TaskRunWorkflowPayload):
        return raw
    payload = _exact_mapping(
        raw,
        {
            "task_run_id",
            "status",
            "completed_units",
            "failed_units",
            "inconclusive_units",
            "canceled_units",
            "skipped_units",
            "schema_version",
        },
    )
    return TaskRunWorkflowPayload(
        task_run_id=_string_field(payload["task_run_id"]),
        status=cast(TaskRunWorkflowStatus, _string_field(payload["status"])),
        completed_units=_integer_field(payload["completed_units"]),
        failed_units=_integer_field(payload["failed_units"]),
        inconclusive_units=_integer_field(payload["inconclusive_units"]),
        canceled_units=_integer_field(payload["canceled_units"]),
        skipped_units=_integer_field(payload["skipped_units"]),
        schema_version=_string_field(payload["schema_version"]),
    )


def _decode_task_run_command_signal(raw: object) -> TaskRunCommandSignal:
    if isinstance(raw, TaskRunCommandSignal):
        return raw
    payload = _exact_mapping(
        raw,
        {
            "command_id",
            "tenant_id",
            "project_id",
            "task_run_id",
            "command_type",
            "command_digest",
            "accepted_run_revision",
            "schema_version",
        },
    )
    return TaskRunCommandSignal(
        command_id=_string_field(payload["command_id"]),
        tenant_id=_string_field(payload["tenant_id"]),
        project_id=_string_field(payload["project_id"]),
        task_run_id=_string_field(payload["task_run_id"]),
        command_type=cast(
            Literal["CANCEL", "PAUSE", "RESUME"],
            _string_field(payload["command_type"]),
        ),
        command_digest=_string_field(payload["command_digest"]),
        accepted_run_revision=_integer_field(payload["accepted_run_revision"]),
        schema_version=_string_field(payload["schema_version"]),
    )


def _exact_mapping(raw: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != fields:
        raise TypeError("Task Workflow Activity returned an invalid payload shape")
    return cast(dict[str, object], raw)


def _string_field(raw: object) -> str:
    if not isinstance(raw, str):
        raise TypeError("Task Workflow Activity returned an invalid string field")
    return raw


def _optional_string_field(raw: object) -> str | None:
    if raw is not None and not isinstance(raw, str):
        raise TypeError("Task Workflow Activity returned an invalid optional string field")
    return raw


def _integer_field(raw: object) -> int:
    if type(raw) is not int:
        raise TypeError("Task Workflow Activity returned an invalid integer field")
    return raw


def _optional_integer_field(raw: object) -> int | None:
    if raw is not None and type(raw) is not int:
        raise TypeError("Task Workflow Activity returned an invalid optional integer field")
    return raw


def _boolean_field(raw: object) -> bool:
    if type(raw) is not bool:
        raise TypeError("Task Workflow Activity returned an invalid boolean field")
    return raw


def _failed_child(
    unit: TaskUnitDispatchPayload,
    *,
    canceled: bool,
) -> TaskAttemptWorkflowPayload:
    return TaskAttemptWorkflowPayload(
        execution_unit_id=unit.execution_unit_id,
        unit_attempt_id=unit.unit_attempt_id,
        ordinal=unit.ordinal,
        status="CANCELED" if canceled else "INCONCLUSIVE",
        error_code=(
            "TASK_ATTEMPT_WORKFLOW_CANCELED" if canceled else "TASK_ATTEMPT_WORKFLOW_FAILED"
        ),
    )


def _validate_dispatch_plan(
    request: TaskRunWorkflowInput,
    plan: TaskDispatchPlanPayload,
) -> None:
    if (
        plan.tenant_id != request.tenant_id
        or plan.project_id != request.project_id
        or plan.task_run_id != request.task_run_id
        or plan.request_digest != request.request_digest
        or plan.manifest_hash != request.manifest_hash
        or type(plan.cancel_requested) is not bool
        or not 1 <= len(plan.units) <= TASK_RUN_MAXIMUM_UNITS
    ):
        raise ValueError("Task dispatch plan does not match the sealed root input")
    if tuple(unit.ordinal for unit in plan.units) != tuple(range(1, len(plan.units) + 1)):
        raise ValueError("Task dispatch plan Units must use contiguous ordinal order")
    unit_ids = {unit.execution_unit_id for unit in plan.units}
    attempt_ids = {unit.unit_attempt_id for unit in plan.units}
    workflow_ids = {unit.unit_attempt_workflow_id for unit in plan.units}
    if len(unit_ids) != len(plan.units) or len(attempt_ids) != len(plan.units):
        raise ValueError("Task dispatch plan contains duplicate Unit identities")
    if len(workflow_ids) != len(plan.units):
        raise ValueError("Task dispatch plan contains duplicate Workflow identities")
    for unit in plan.units:
        _validate_dispatch_identity(request, unit)


def _validate_dispatch_identity(
    request: TaskRunWorkflowInput,
    unit: TaskUnitDispatchPayload,
) -> None:
    UUID(unit.execution_unit_id)
    attempt_id = UUID(unit.unit_attempt_id)
    expected_workflow_id = unit_attempt_workflow_id(
        tenant_id=UUID(request.tenant_id),
        unit_attempt_id=attempt_id,
    )
    not_before = datetime.fromisoformat(unit.not_before)
    deadline = datetime.fromisoformat(unit.execution_deadline)
    if (
        unit.ordinal < 1
        or unit.unit_attempt_workflow_id != expected_workflow_id
        or not_before.tzinfo is None
        or deadline.tzinfo is None
        or not_before >= deadline
        or not 1 <= unit.activity_timeout_seconds <= 3_600
    ):
        raise ValueError("UnitAttempt dispatch identity or timing is invalid")


def _validate_root_input(request: TaskRunWorkflowInput) -> None:
    expected_workflow_id = task_run_workflow_id(
        tenant_id=UUID(request.tenant_id),
        task_run_id=UUID(request.task_run_id),
    )
    info = workflow.info()
    if (
        request.schema_version != TASK_RUN_WORKFLOW_INPUT_SCHEMA
        or info.workflow_id != expected_workflow_id
        or info.workflow_type != TASK_RUN_WORKFLOW_TYPE
        or info.task_queue != TASK_RUN_TASK_QUEUE
    ):
        raise ValueError("TaskRun Workflow input or execution identity is invalid")


def _validate_batch_prepare_payload(
    request: TaskBatchPrepareInput,
    result: TaskBatchPreparePayload,
) -> None:
    if (
        not 1 <= len(request.attempts) <= TASK_RUN_CHILD_BATCH_SIZE
        or result.status not in {"AUTHORIZED", "PAUSE_REQUESTED", "CANCEL_REQUESTED"}
        or any(
            attempt.tenant_id != request.request.tenant_id
            or attempt.project_id != request.request.project_id
            or attempt.task_run_id != request.request.task_run_id
            or attempt.request_digest != request.request.request_digest
            or attempt.manifest_hash != request.request.manifest_hash
            for attempt in request.attempts
        )
    ):
        raise ValueError("Task batch preparation result is invalid")


def _validate_control_checkpoint_payload(
    result: TaskRunControlCheckpointPayload,
) -> None:
    if result.state not in {"DISPATCHABLE", "PAUSED", "CANCELING", "CLOSED"}:
        raise ValueError("Task control checkpoint result is invalid")


def _validate_attempt_batch_settlement(
    request: TaskAttemptBatchSettleInput,
    result: TaskAttemptBatchSettlePayload,
) -> None:
    if not 1 <= len(request.outcomes) <= TASK_RUN_CHILD_BATCH_SIZE or result.state not in {
        "SETTLED",
        "PAUSE_REQUESTED",
        "CANCEL_REQUESTED",
    }:
        raise ValueError("Task Attempt batch settlement state is invalid")
    if result.state != "SETTLED":
        if result.retry_attempts or result.final_outcomes:
            raise ValueError("Deferred Task Attempt settlement cannot return decisions")
        return
    request_by_unit = {outcome.execution_unit_id: outcome for outcome in request.outcomes}
    if len(request_by_unit) != len(request.outcomes):
        raise ValueError("Task Attempt batch settlement contains duplicate Units")
    decided_units = {item.execution_unit_id for item in result.retry_attempts} | {
        item.execution_unit_id for item in result.final_outcomes
    }
    if decided_units != set(request_by_unit) or (
        len(result.retry_attempts) + len(result.final_outcomes) != len(request.outcomes)
    ):
        raise ValueError("Task Attempt batch settlement is not one decision per Unit")
    for retry in result.retry_attempts:
        previous = request_by_unit.get(retry.execution_unit_id)
        if previous is None or previous.status != "INFRA_ERROR":
            raise ValueError("Only explicit infrastructure failures may retry")
        _validate_dispatch_identity(request.request, retry)
        if retry.ordinal != previous.ordinal:
            raise ValueError("Retry Attempt changed the Unit ordinal")
    for final in result.final_outcomes:
        previous = request_by_unit.get(final.execution_unit_id)
        if previous != final:
            raise ValueError("Final Unit outcome changed during settlement")


def _validate_task_run_command_signal(command: TaskRunCommandSignal) -> None:
    if (
        command.schema_version
        not in {
            TASK_RUN_COMMAND_SIGNAL_LEGACY_SCHEMA,
            TASK_RUN_COMMAND_SIGNAL_SCHEMA,
        }
        or command.command_type not in {"CANCEL", "PAUSE", "RESUME"}
        or (
            command.schema_version == TASK_RUN_COMMAND_SIGNAL_LEGACY_SCHEMA
            and command.command_type != "CANCEL"
        )
        or command.accepted_run_revision < 2
        or fullmatch(r"sha256:[0-9a-f]{64}", command.command_digest) is None
    ):
        raise ValueError("TaskRun command Signal is invalid")
    UUID(command.command_id)
    UUID(command.tenant_id)
    UUID(command.project_id)
    UUID(command.task_run_id)


def _command_matches_root(
    command: TaskRunCommandSignal,
    request: TaskRunWorkflowInput,
) -> bool:
    return (
        command.tenant_id == request.tenant_id
        and command.project_id == request.project_id
        and command.task_run_id == request.task_run_id
    )


def _validate_attempt_input(request: UnitAttemptWorkflowInput) -> None:
    expected_workflow_id = unit_attempt_workflow_id(
        tenant_id=UUID(request.tenant_id),
        unit_attempt_id=UUID(request.unit_attempt_id),
    )
    deadline = datetime.fromisoformat(request.execution_deadline)
    info = workflow.info()
    if (
        request.schema_version != TASK_UNIT_ATTEMPT_INPUT_SCHEMA
        or request.ordinal < 1
        or deadline.tzinfo is None
        or not 1 <= request.activity_timeout_seconds <= 3_600
        or info.workflow_id != expected_workflow_id
        or info.workflow_type != TASK_UNIT_ATTEMPT_WORKFLOW_TYPE
        or info.task_queue != TASK_UNIT_ATTEMPT_TASK_QUEUE
    ):
        raise ValueError("UnitAttempt Workflow input or execution identity is invalid")


def _validate_execution_request(
    attempt: UnitAttemptWorkflowInput,
    request: TaskUnitExecutionRequest,
) -> None:
    if (
        request.schema_version != TASK_UNIT_EXECUTION_REQUEST_SCHEMA
        or request.attempt != attempt
        or str(UUID(request.ticket_id)) != request.ticket_id
        or fullmatch(r"sha256:[0-9a-f]{64}", request.ticket_digest) is None
    ):
        raise ValueError("Task execution request is not bound to an exact ticket")


def _validate_begin_payload(payload: TaskAttemptBeginPayload) -> None:
    if payload.status not in {"READY", "CANCELED", "REJECTED"} or not _is_safe_error_code(
        payload.error_code
    ):
        raise ValueError("Task Attempt admission returned an invalid status")


def _validate_execution_payload(payload: TaskAttemptExecutionPayload) -> None:
    if payload.status not in {
        "EXECUTED_UNSEALED",
        "FAILED",
        "INFRA_ERROR",
        "INCONCLUSIVE",
        "CANCELED",
    } or not _is_safe_error_code(payload.error_code):
        raise ValueError("Task Attempt execution returned an invalid status")
    if payload.retry_after_seconds is not None and (
        payload.status != "INFRA_ERROR"
        or type(payload.retry_after_seconds) is not int
        or not 1 <= payload.retry_after_seconds <= 3_600
    ):
        raise ValueError("Task Attempt retry-after is invalid")


def _validate_attempt_result(
    request: UnitAttemptWorkflowInput,
    result: TaskAttemptWorkflowPayload,
    *,
    execution: TaskAttemptExecutionPayload,
) -> None:
    _validate_attempt_result_identity(
        TaskUnitDispatchPayload(
            ordinal=request.ordinal,
            execution_unit_id=request.execution_unit_id,
            unit_attempt_id=request.unit_attempt_id,
            unit_attempt_workflow_id=unit_attempt_workflow_id(
                tenant_id=UUID(request.tenant_id),
                unit_attempt_id=UUID(request.unit_attempt_id),
            ),
            not_before=request.execution_deadline,
            execution_deadline=request.execution_deadline,
            activity_timeout_seconds=request.activity_timeout_seconds,
        ),
        result,
    )
    expected_status: TaskAttemptWorkflowStatus = (
        "FINISHED_UNSEALED" if execution.status == "EXECUTED_UNSEALED" else execution.status
    )
    if result.status != expected_status:
        raise ValueError("UnitAttempt result does not match the execution outcome")
    if result.retry_after_seconds != execution.retry_after_seconds:
        raise ValueError("UnitAttempt result changed the retry-after hint")


def _validate_attempt_result_identity(
    unit: TaskUnitDispatchPayload,
    result: TaskAttemptWorkflowPayload,
) -> None:
    if not isinstance(result, TaskAttemptWorkflowPayload):
        raise TypeError("UnitAttempt Workflow returned an invalid payload")
    if (
        result.execution_unit_id != unit.execution_unit_id
        or result.unit_attempt_id != unit.unit_attempt_id
        or result.ordinal != unit.ordinal
        or result.status not in _SAFE_ATTEMPT_STATUSES
        or result.schema_version != TASK_WORKFLOW_RESULT_SCHEMA
        or not _is_safe_error_code(result.error_code)
        or (
            result.retry_after_seconds is not None
            and (
                result.status != "INFRA_ERROR"
                or type(result.retry_after_seconds) is not int
                or not 1 <= result.retry_after_seconds <= 3_600
            )
        )
    ):
        raise ValueError("UnitAttempt Workflow returned an invalid identity or status")


def _validate_run_result(
    request: TaskRunFinishInput,
    result: TaskRunWorkflowPayload,
    *,
    total_units: int,
) -> None:
    counts = (
        result.completed_units,
        result.failed_units,
        result.inconclusive_units,
        result.canceled_units,
        result.skipped_units,
    )
    expected_counts = (
        sum(item.status == "FINISHED_UNSEALED" for item in request.outcomes),
        sum(item.status == "FAILED" for item in request.outcomes),
        sum(item.status in {"INCONCLUSIVE", "INFRA_ERROR"} for item in request.outcomes),
        sum(item.status == "CANCELED" for item in request.outcomes),
        request.skipped_units,
    )
    if (
        len(request.commands) > 1
        or (request.commands and not request.cancel_requested)
        or any(not _command_matches_root(command, request.request) for command in request.commands)
    ):
        raise ValueError("TaskRun finish commands do not match the root cancellation")
    if request.cancel_requested or expected_counts[3] or expected_counts[4]:
        expected_status = "CANCELED"
    elif expected_counts[1]:
        expected_status = "FAILED"
    elif expected_counts[2]:
        expected_status = "INCONCLUSIVE"
    else:
        expected_status = "FINISHED_UNSEALED"
    if (
        result.task_run_id != request.request.task_run_id
        or result.status not in _SAFE_RUN_STATUSES
        or result.status != expected_status
        or result.schema_version != TASK_WORKFLOW_RESULT_SCHEMA
        or any(count < 0 for count in counts)
        or counts != expected_counts
        or sum(counts) != total_units
    ):
        raise ValueError("TaskRun Workflow returned an invalid unsealed result")


def _is_safe_error_code(value: str | None) -> bool:
    return value is None or fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) is not None


__all__ = [
    "AtlasTaskRunWorkflow",
    "AtlasUnitAttemptWorkflow",
    "TaskAttemptBatchSettleInput",
    "TaskAttemptBatchSettlePayload",
    "TaskAttemptBeginPayload",
    "TaskAttemptExecutionPayload",
    "TaskAttemptFinishInput",
    "TaskAttemptWorkflowPayload",
    "TaskBatchPrepareInput",
    "TaskBatchPreparePayload",
    "TaskDispatchPlanPayload",
    "TaskOrchestrationActivities",
    "TaskOrchestrationService",
    "TaskRunCommandSignal",
    "TaskRunControlCheckpointPayload",
    "TaskRunFinishInput",
    "TaskRunWorkflowPayload",
    "TaskUnitDispatchPayload",
    "TaskUnitExecutionPort",
    "TaskUnitExecutionRequest",
    "UnitAttemptWorkflowInput",
]
