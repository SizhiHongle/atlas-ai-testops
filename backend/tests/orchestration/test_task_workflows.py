"""Bounded Task root and UnitAttempt Temporal orchestration tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError as TemporalApplicationError

from atlas_testops.domain.task import task_run_workflow_id, unit_attempt_workflow_id
from atlas_testops.orchestration.task_intents import (
    TASK_RUN_TASK_QUEUE,
    TASK_RUN_WORKFLOW_TYPE,
    TaskRunWorkflowInput,
)
from atlas_testops.orchestration.tasks import (
    BEGIN_TASK_UNIT_ATTEMPT_ACTIVITY,
    CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY,
    EXECUTE_TASK_UNIT_ATTEMPT_ACTIVITY,
    FINISH_PARTITIONED_TASK_RUN_ACTIVITY,
    FINISH_TASK_RUN_ACTIVITY,
    FINISH_TASK_UNIT_ATTEMPT_ACTIVITY,
    LOAD_TASK_DISPATCH_PLAN_ACTIVITY,
    PREPARE_TASK_RUN_BATCH_ACTIVITY,
    PREPARE_TASK_UNIT_ATTEMPT_ACTIVITY,
    SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY,
    TASK_RUN_COMMAND_SIGNAL_SCHEMA,
    TASK_UNIT_ATTEMPT_TASK_QUEUE,
    TASK_UNIT_ATTEMPT_WORKFLOW_TYPE,
    AtlasTaskRunWorkflow,
    AtlasUnitAttemptWorkflow,
    TaskAttemptBatchSettleInput,
    TaskAttemptBatchSettlePayload,
    TaskAttemptBeginPayload,
    TaskAttemptExecutionPayload,
    TaskAttemptFinishInput,
    TaskAttemptWorkflowPayload,
    TaskBatchPreparePayload,
    TaskDispatchPlanPayload,
    TaskOrchestrationActivities,
    TaskOrchestrationInvariantError,
    TaskOrchestrationService,
    TaskRunCommandSignal,
    TaskRunControlCheckpointPayload,
    TaskRunFinishInput,
    TaskRunProjectedFinishInput,
    TaskRunWorkflowPayload,
    TaskUnitDispatchPayload,
    TaskUnitExecutionPort,
    TaskUnitExecutionRequest,
    UnitAttemptWorkflowInput,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
EXECUTION_DEADLINE = "2099-01-01T00:00:00+00:00"


def _root_request() -> TaskRunWorkflowInput:
    return TaskRunWorkflowInput(
        tenant_id=str(UUID(int=1)),
        project_id=str(UUID(int=2)),
        task_run_id=str(UUID(int=3)),
        request_digest=DIGEST_A,
        manifest_hash=DIGEST_B,
    )


def _unit(request: TaskRunWorkflowInput, ordinal: int) -> TaskUnitDispatchPayload:
    attempt_id = UUID(int=100 + ordinal)
    return TaskUnitDispatchPayload(
        ordinal=ordinal,
        execution_unit_id=str(UUID(int=10 + ordinal)),
        unit_attempt_id=str(attempt_id),
        unit_attempt_workflow_id=unit_attempt_workflow_id(
            tenant_id=UUID(request.tenant_id),
            unit_attempt_id=attempt_id,
        ),
        not_before="2026-01-01T00:00:00+00:00",
        execution_deadline=EXECUTION_DEADLINE,
        activity_timeout_seconds=60,
    )


def _plan(request: TaskRunWorkflowInput, count: int) -> TaskDispatchPlanPayload:
    return TaskDispatchPlanPayload(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        task_run_id=request.task_run_id,
        request_digest=request.request_digest,
        manifest_hash=request.manifest_hash,
        units=tuple(_unit(request, ordinal) for ordinal in range(1, count + 1)),
    )


def _command_signal(
    request: TaskRunWorkflowInput,
    *,
    command_type: str = "CANCEL",
    command_id: int = 401,
) -> TaskRunCommandSignal:
    return TaskRunCommandSignal(
        command_id=str(UUID(int=command_id)),
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        task_run_id=request.task_run_id,
        command_type=cast(Any, command_type),
        command_digest=DIGEST_A,
        accepted_run_revision=3,
        schema_version=TASK_RUN_COMMAND_SIGNAL_SCHEMA,
    )


def _attempt_input(
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


def _prepared(attempt: UnitAttemptWorkflowInput) -> TaskUnitExecutionRequest:
    return TaskUnitExecutionRequest(
        attempt=attempt,
        ticket_id=str(UUID(int=201)),
        ticket_digest=DIGEST_A,
    )


def _patch_root_info(monkeypatch: pytest.MonkeyPatch, request: TaskRunWorkflowInput) -> None:
    monkeypatch.setattr(
        workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=task_run_workflow_id(
                tenant_id=UUID(request.tenant_id),
                task_run_id=UUID(request.task_run_id),
            ),
            workflow_type=TASK_RUN_WORKFLOW_TYPE,
            task_queue=TASK_RUN_TASK_QUEUE,
        ),
    )
    monkeypatch.setattr(workflow, "now", lambda: datetime(2026, 1, 1, tzinfo=UTC))


def _patch_attempt_info(
    monkeypatch: pytest.MonkeyPatch,
    request: UnitAttemptWorkflowInput,
) -> None:
    monkeypatch.setattr(
        workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=unit_attempt_workflow_id(
                tenant_id=UUID(request.tenant_id),
                unit_attempt_id=UUID(request.unit_attempt_id),
            ),
            workflow_type=TASK_UNIT_ATTEMPT_WORKFLOW_TYPE,
            task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        ),
    )
    monkeypatch.setattr(workflow, "now", lambda: datetime(2026, 1, 1, tzinfo=UTC))


class _Service:
    def __init__(self, plan: TaskDispatchPlanPayload) -> None:
        self.plan = plan
        self.calls: list[tuple[str, object]] = []

    async def load_dispatch_plan(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskDispatchPlanPayload:
        self.calls.append(("load", request))
        return self.plan

    async def start_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskAttemptBeginPayload:
        self.calls.append(("start", request))
        return TaskAttemptBeginPayload(status="READY")

    async def prepare_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskUnitExecutionRequest:
        self.calls.append(("prepare", request))
        return _prepared(request)

    async def finish_attempt(
        self,
        request: TaskAttemptFinishInput,
    ) -> TaskAttemptWorkflowPayload:
        self.calls.append(("finish-attempt", request))
        status = (
            "FINISHED_UNSEALED"
            if request.execution.status == "EXECUTED_UNSEALED"
            else request.execution.status
        )
        return TaskAttemptWorkflowPayload(
            execution_unit_id=request.attempt.execution_unit_id,
            unit_attempt_id=request.attempt.unit_attempt_id,
            ordinal=request.attempt.ordinal,
            status=cast(Any, status),
            error_code=request.execution.error_code,
        )

    async def finish_run(self, request: TaskRunFinishInput) -> TaskRunWorkflowPayload:
        self.calls.append(("finish-run", request))
        return _run_result(request)

    async def finish_partitioned_run(
        self,
        request: TaskRunProjectedFinishInput,
    ) -> TaskRunWorkflowPayload:
        self.calls.append(("finish-partitioned-run", request))
        total = self.plan.total_units or len(self.plan.units)
        return TaskRunWorkflowPayload(
            task_run_id=request.request.task_run_id,
            status="CANCELED" if request.cancel_requested else "FINISHED_UNSEALED",
            completed_units=0 if request.cancel_requested else total,
            failed_units=0,
            inconclusive_units=0,
            canceled_units=0,
            skipped_units=total if request.cancel_requested else 0,
        )


class _ExecutionPort:
    def __init__(self) -> None:
        self.calls: list[TaskUnitExecutionRequest] = []

    async def execute(
        self,
        request: TaskUnitExecutionRequest,
    ) -> TaskAttemptExecutionPayload:
        self.calls.append(request)
        return TaskAttemptExecutionPayload(status="EXECUTED_UNSEALED")


class _FailingExecutionPort(_ExecutionPort):
    async def execute(
        self,
        request: TaskUnitExecutionRequest,
    ) -> TaskAttemptExecutionPayload:
        self.calls.append(request)
        raise RuntimeError("adapter-secret-must-not-enter-history")


class _LoadFailureService(_Service):
    def __init__(
        self,
        plan: TaskDispatchPlanPayload,
        error: Exception,
    ) -> None:
        super().__init__(plan)
        self.error = error

    async def load_dispatch_plan(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskDispatchPlanPayload:
        self.calls.append(("load", request))
        raise self.error


def _run_result(request: TaskRunFinishInput) -> TaskRunWorkflowPayload:
    completed = sum(item.status == "FINISHED_UNSEALED" for item in request.outcomes)
    failed = sum(item.status == "FAILED" for item in request.outcomes)
    inconclusive = sum(item.status == "INCONCLUSIVE" for item in request.outcomes)
    canceled = sum(item.status == "CANCELED" for item in request.outcomes)
    status = (
        "CANCELED"
        if request.cancel_requested
        else (
            "INCONCLUSIVE"
            if inconclusive or request.skipped_units
            else ("FAILED" if failed else "FINISHED_UNSEALED")
        )
    )
    return TaskRunWorkflowPayload(
        task_run_id=request.request.task_run_id,
        status=cast(Any, status),
        completed_units=completed,
        failed_units=failed,
        inconclusive_units=inconclusive,
        canceled_units=canceled,
        skipped_units=request.skipped_units,
    )


def _settled(value: object) -> TaskAttemptBatchSettlePayload:
    request = cast(TaskAttemptBatchSettleInput, value)
    return TaskAttemptBatchSettlePayload(
        state="SETTLED",
        final_outcomes=request.outcomes,
    )


@pytest.mark.anyio
async def test_activities_are_thin_typed_service_and_execution_port_adapters() -> None:
    root = _root_request()
    plan = _plan(root, 1)
    attempt = _attempt_input(root, plan.units[0])
    service = _Service(plan)
    port = _ExecutionPort()
    activities = TaskOrchestrationActivities(
        cast(TaskOrchestrationService, service),
        cast(TaskUnitExecutionPort, port),
    )

    assert await activities.load_dispatch_plan(root) == plan
    prepared = await activities.prepare_attempt(attempt)
    assert (await activities.begin_attempt(attempt)).status == "READY"
    execution = await activities.execute_attempt(prepared)
    assert execution.status == "EXECUTED_UNSEALED"
    finish_input = TaskAttemptFinishInput(attempt=attempt, execution=execution)
    assert (await activities.finish_attempt(finish_input)).status == ("FINISHED_UNSEALED")
    run_finish = TaskRunFinishInput(
        request=root,
        outcomes=(await activities.finish_attempt(finish_input),),
        cancel_requested=False,
        skipped_units=0,
    )
    assert (await activities.finish_run(run_finish)).status == "FINISHED_UNSEALED"
    projected = TaskRunProjectedFinishInput(
        request=root,
        cancel_requested=False,
    )
    assert (
        await activities.finish_partitioned_run(projected)
    ).status == "FINISHED_UNSEALED"
    assert port.calls == [prepared]


@pytest.mark.anyio
async def test_database_activities_mark_only_safe_invariants_non_retryable() -> None:
    root = _root_request()
    plan = _plan(root, 1)
    port = _ExecutionPort()
    invariant_service = _LoadFailureService(
        plan,
        TaskOrchestrationInvariantError("TASK_ROOT_IDENTITY_MISMATCH"),
    )
    activities = TaskOrchestrationActivities(
        cast(TaskOrchestrationService, invariant_service),
        cast(TaskUnitExecutionPort, port),
    )

    with pytest.raises(TemporalApplicationError) as captured:
        await activities.load_dispatch_plan(root)

    assert captured.value.message == "TASK_ROOT_IDENTITY_MISMATCH"
    assert captured.value.type == "TaskOrchestrationInvariantError"
    assert captured.value.non_retryable is True

    transient_service = _LoadFailureService(plan, ConnectionError("database connection lost"))
    activities = TaskOrchestrationActivities(
        cast(TaskOrchestrationService, transient_service),
        cast(TaskUnitExecutionPort, port),
    )
    with pytest.raises(TemporalApplicationError) as retryable:
        await activities.load_dispatch_plan(root)
    assert retryable.value.message == "TASK_DATABASE_ACTIVITY_RETRYABLE"
    assert retryable.value.type == "TaskDatabaseTransientError"
    assert retryable.value.non_retryable is False
    assert retryable.value.__cause__ is None
    assert "connection" not in str(retryable.value).casefold()


@pytest.mark.anyio
async def test_execution_activity_sanitizes_any_adapter_exception_permanently() -> None:
    root = _root_request()
    plan = _plan(root, 1)
    attempt = _attempt_input(root, plan.units[0])
    prepared = _prepared(attempt)
    port = _FailingExecutionPort()
    activities = TaskOrchestrationActivities(
        cast(TaskOrchestrationService, _Service(plan)),
        cast(TaskUnitExecutionPort, port),
    )

    with pytest.raises(TemporalApplicationError) as captured:
        await activities.execute_attempt(prepared)

    assert captured.value.message == "TASK_UNIT_EXECUTION_ADAPTER_FAILED"
    assert captured.value.type == "TaskUnitExecutionAdapterError"
    assert captured.value.non_retryable is True
    assert captured.value.__cause__ is None
    assert "adapter-secret" not in str(captured.value)
    assert port.calls == [prepared]


@pytest.mark.anyio
async def test_root_starts_deterministic_children_in_fixed_batches_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    plan = _plan(request, 10)
    _patch_root_info(monkeypatch, request)
    active = 0
    maximum_active = 0
    launched: list[int] = []
    child_options: list[dict[str, object]] = []
    database_activity_options: dict[str, dict[str, object]] = {}
    finish_input: TaskRunFinishInput | None = None

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        nonlocal finish_input
        database_activity_options[name] = dict(_options)
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            return _settled(value)
        assert name == FINISH_TASK_RUN_ACTIVITY
        finish_input = cast(TaskRunFinishInput, value)
        return _run_result(finish_input)

    async def execute_child(
        child_type: str,
        child_input: UnitAttemptWorkflowInput,
        **options: object,
    ) -> TaskAttemptWorkflowPayload:
        nonlocal active, maximum_active
        assert child_type == TASK_UNIT_ATTEMPT_WORKFLOW_TYPE
        launched.append(child_input.ordinal)
        child_options.append(options)
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        if child_input.ordinal == 3:
            raise RuntimeError("sensitive child failure")
        return TaskAttemptWorkflowPayload(
            execution_unit_id=child_input.execution_unit_id,
            unit_attempt_id=child_input.unit_attempt_id,
            ordinal=child_input.ordinal,
            status="FINISHED_UNSEALED",
        )

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", execute_child)

    result = await AtlasTaskRunWorkflow().run(request)

    assert launched == list(range(1, 11))
    assert maximum_active == 8
    assert all(
        options["task_queue"] == TASK_UNIT_ATTEMPT_TASK_QUEUE
        and options["id_reuse_policy"] is WorkflowIDReusePolicy.REJECT_DUPLICATE
        for options in child_options
    )
    assert [options["id"] for options in child_options] == [
        unit.unit_attempt_workflow_id for unit in plan.units
    ]
    for name in (
        LOAD_TASK_DISPATCH_PLAN_ACTIVITY,
        CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY,
        PREPARE_TASK_RUN_BATCH_ACTIVITY,
        SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY,
        FINISH_TASK_RUN_ACTIVITY,
    ):
        retry = cast(RetryPolicy, database_activity_options[name]["retry_policy"])
        assert retry.maximum_attempts == 0
        assert retry.maximum_interval == timedelta(minutes=1)
    assert finish_input is not None
    failed_child = finish_input.outcomes[2]
    assert failed_child.status == "INCONCLUSIVE"
    assert failed_child.error_code == "TASK_ATTEMPT_WORKFLOW_FAILED"
    assert "sensitive" not in str(failed_child).lower()
    assert result.status == "INCONCLUSIVE"
    assert result.completed_units == 9
    assert "PASSED" not in str(result)


@pytest.mark.anyio
async def test_root_dispatches_database_authorized_retry_and_keeps_latest_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    plan = _plan(request, 1)
    _patch_root_info(monkeypatch, request)
    retry_id = UUID(int=901)
    retry = TaskUnitDispatchPayload(
        ordinal=1,
        execution_unit_id=plan.units[0].execution_unit_id,
        unit_attempt_id=str(retry_id),
        unit_attempt_workflow_id=unit_attempt_workflow_id(
            tenant_id=UUID(request.tenant_id),
            unit_attempt_id=retry_id,
        ),
        not_before="2026-01-01T00:00:00+00:00",
        execution_deadline=EXECUTION_DEADLINE,
        activity_timeout_seconds=60,
    )
    launched: list[str] = []
    settlement_calls = 0
    finish_input: TaskRunFinishInput | None = None

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        nonlocal settlement_calls, finish_input
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            settlement_calls += 1
            settle = cast(TaskAttemptBatchSettleInput, value)
            if settlement_calls == 1:
                assert settle.outcomes[0].status == "INFRA_ERROR"
                return TaskAttemptBatchSettlePayload(
                    state="SETTLED",
                    retry_attempts=(retry,),
                )
            return TaskAttemptBatchSettlePayload(
                state="SETTLED",
                final_outcomes=settle.outcomes,
            )
        assert name == FINISH_TASK_RUN_ACTIVITY
        finish_input = cast(TaskRunFinishInput, value)
        return _run_result(finish_input)

    async def execute_child(
        _child_type: str,
        child_input: UnitAttemptWorkflowInput,
        **_options: object,
    ) -> TaskAttemptWorkflowPayload:
        launched.append(child_input.unit_attempt_id)
        return TaskAttemptWorkflowPayload(
            execution_unit_id=child_input.execution_unit_id,
            unit_attempt_id=child_input.unit_attempt_id,
            ordinal=child_input.ordinal,
            status="INFRA_ERROR" if len(launched) == 1 else "FINISHED_UNSEALED",
            error_code="TASK_HOST_UNAVAILABLE" if len(launched) == 1 else None,
        )

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", execute_child)

    result = await AtlasTaskRunWorkflow().run(request)

    assert launched == [plan.units[0].unit_attempt_id, retry.unit_attempt_id]
    assert settlement_calls == 2
    assert finish_input is not None
    assert finish_input.outcomes[0].unit_attempt_id == retry.unit_attempt_id
    assert result.status == "FINISHED_UNSEALED"


@pytest.mark.anyio
async def test_root_backoff_wait_is_durable_and_interruptible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    unit = replace(
        _unit(request, 1),
        not_before="2026-01-01T00:00:10+00:00",
    )
    monkeypatch.setattr(workflow, "now", lambda: datetime(2026, 1, 1, tzinfo=UTC))
    observed_timeout: timedelta | None = None

    async def timeout_wait(
        _predicate: object,
        *,
        timeout: timedelta,
    ) -> None:
        nonlocal observed_timeout
        observed_timeout = timeout
        raise TimeoutError

    monkeypatch.setattr(workflow, "wait_condition", timeout_wait)
    root = AtlasTaskRunWorkflow()

    assert await root._wait_for_batch_not_before((unit,)) is False
    assert observed_timeout == timedelta(seconds=10)

    async def signal_wait(
        _predicate: object,
        *,
        timeout: timedelta,
    ) -> None:
        assert timeout == timedelta(seconds=10)

    monkeypatch.setattr(workflow, "wait_condition", signal_wait)
    assert await root._wait_for_batch_not_before((unit,)) is True


@pytest.mark.anyio
async def test_root_cancel_starts_no_new_unit_and_rejects_more_than_64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    _patch_root_info(monkeypatch, request)
    plan = _plan(request, 10)
    launched = 0

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            return _settled(value)
        assert name == FINISH_TASK_RUN_ACTIVITY
        return _run_result(cast(TaskRunFinishInput, value))

    async def unexpected_child(*_args: object, **_kwargs: object) -> object:
        nonlocal launched
        launched += 1
        raise AssertionError("a canceled Root must not start a Unit")

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", unexpected_child)
    root = AtlasTaskRunWorkflow()
    await root.request_cancel()

    canceled = await root.run(request)

    assert launched == 0
    assert canceled.status == "CANCELED"
    assert canceled.skipped_units == 10

    plan = _plan(request, 65)
    with pytest.raises(TemporalApplicationError) as captured:
        await AtlasTaskRunWorkflow().run(request)
    assert captured.value.message == "TASK_ROOT_DISPATCH_PLAN_INVALID"
    assert captured.value.type == "TaskWorkflowValidationError"
    assert captured.value.non_retryable is True


@pytest.mark.anyio
async def test_partitioned_root_continues_only_after_the_page_is_fully_settled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    _patch_root_info(monkeypatch, request)
    plan = replace(
        _plan(request, 64),
        total_units=65,
        has_more=True,
    )
    settled_ordinals: list[int] = []
    continued: TaskRunWorkflowInput | None = None
    root = AtlasTaskRunWorkflow()

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            settle = cast(TaskAttemptBatchSettleInput, value)
            settled_ordinals.extend(item.ordinal for item in settle.outcomes)
            return _settled(value)
        raise AssertionError("a non-final page must not finish the Root")

    async def execute_child(
        _workflow_type: str,
        child: UnitAttemptWorkflowInput,
        **_options: object,
    ) -> TaskAttemptWorkflowPayload:
        return TaskAttemptWorkflowPayload(
            execution_unit_id=child.execution_unit_id,
            unit_attempt_id=child.unit_attempt_id,
            ordinal=child.ordinal,
            status="FINISHED_UNSEALED",
            error_code="TASK_ATTEMPT_RESULT_UNSEALED",
        )

    class _Continued(RuntimeError):
        pass

    def continue_as_new(next_request: TaskRunWorkflowInput) -> None:
        nonlocal continued
        assert root._active_child_tasks == ()
        continued = next_request
        raise _Continued

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", execute_child)
    monkeypatch.setattr(workflow, "continue_as_new", continue_as_new)

    with pytest.raises(_Continued):
        await root.run(request)

    assert settled_ordinals == list(range(1, 65))
    assert continued == replace(request, dispatch_after_ordinal=64)


@pytest.mark.anyio
async def test_partitioned_root_finishes_the_last_page_from_database_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = replace(_root_request(), dispatch_after_ordinal=64)
    _patch_root_info(monkeypatch, request)
    plan = TaskDispatchPlanPayload(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        task_run_id=request.task_run_id,
        request_digest=request.request_digest,
        manifest_hash=request.manifest_hash,
        units=(_unit(request, 65),),
        after_ordinal=64,
        total_units=65,
        has_more=False,
    )
    projected_finish: TaskRunProjectedFinishInput | None = None

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        nonlocal projected_finish
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            return _settled(value)
        assert name == FINISH_PARTITIONED_TASK_RUN_ACTIVITY
        projected_finish = cast(TaskRunProjectedFinishInput, value)
        return TaskRunWorkflowPayload(
            task_run_id=request.task_run_id,
            status="FINISHED_UNSEALED",
            completed_units=65,
            failed_units=0,
            inconclusive_units=0,
            canceled_units=0,
            skipped_units=0,
        )

    async def execute_child(
        _workflow_type: str,
        child: UnitAttemptWorkflowInput,
        **_options: object,
    ) -> TaskAttemptWorkflowPayload:
        return TaskAttemptWorkflowPayload(
            execution_unit_id=child.execution_unit_id,
            unit_attempt_id=child.unit_attempt_id,
            ordinal=child.ordinal,
            status="FINISHED_UNSEALED",
            error_code="TASK_ATTEMPT_RESULT_UNSEALED",
        )

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", execute_child)

    result = await AtlasTaskRunWorkflow().run(request)

    assert result.completed_units == 65
    assert projected_finish == TaskRunProjectedFinishInput(
        request=request,
        cancel_requested=False,
    )


@pytest.mark.anyio
async def test_partitioned_cancel_drains_the_page_before_continuing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    _patch_root_info(monkeypatch, request)
    plan = replace(
        _plan(request, 64),
        cancel_requested=True,
        total_units=65,
        has_more=True,
    )
    canceled: list[TaskAttemptWorkflowPayload] = []
    continued: TaskRunWorkflowInput | None = None

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="CANCELING")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            settle = cast(TaskAttemptBatchSettleInput, value)
            assert settle.cancel_requested is True
            canceled.extend(settle.outcomes)
            return _settled(value)
        raise AssertionError("cancel drain must not dispatch or finish a non-final page")

    async def unexpected_child(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a canceled page must not start a child")

    class _Continued(RuntimeError):
        pass

    def continue_as_new(next_request: TaskRunWorkflowInput) -> None:
        nonlocal continued
        continued = next_request
        raise _Continued

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", unexpected_child)
    monkeypatch.setattr(workflow, "continue_as_new", continue_as_new)

    with pytest.raises(_Continued):
        await AtlasTaskRunWorkflow().run(request)

    assert [item.ordinal for item in canceled] == list(range(1, 65))
    assert {item.error_code for item in canceled} == {
        "TASK_RUN_CANCELED_BEFORE_DISPATCH"
    }
    assert continued == replace(
        request,
        dispatch_after_ordinal=64,
        continuation_cancel_requested=True,
    )


@pytest.mark.anyio
async def test_root_observes_cancel_already_persisted_before_workflow_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    _patch_root_info(monkeypatch, request)
    plan = replace(_plan(request, 3), cancel_requested=True)
    finished: TaskRunFinishInput | None = None

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        nonlocal finished
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            return _settled(value)
        assert name == FINISH_TASK_RUN_ACTIVITY
        finished = cast(TaskRunFinishInput, value)
        return _run_result(finished)

    async def unexpected_child(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("persisted Cancel must stop dispatch before any child")

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", unexpected_child)

    result = await AtlasTaskRunWorkflow().run(request)

    assert result.status == "CANCELED"
    assert finished is not None
    assert finished.cancel_requested is True
    assert finished.skipped_units == 3
    assert finished.commands == ()


@pytest.mark.anyio
async def test_root_command_signal_deduplicates_and_cancels_active_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    _patch_root_info(monkeypatch, request)
    plan = _plan(request, 10)
    started = asyncio.Event()
    active = 0
    canceled_ordinals: list[int] = []
    finish_input: TaskRunFinishInput | None = None

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        nonlocal finish_input
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            return _settled(value)
        assert name == FINISH_TASK_RUN_ACTIVITY
        finish_input = cast(TaskRunFinishInput, value)
        return _run_result(finish_input)

    async def execute_child(
        _child_type: str,
        child_input: UnitAttemptWorkflowInput,
        **_options: object,
    ) -> TaskAttemptWorkflowPayload:
        nonlocal active
        active += 1
        if active == 8:
            started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            canceled_ordinals.append(child_input.ordinal)
            raise
        raise AssertionError("unreachable")

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", execute_child)
    root = AtlasTaskRunWorkflow()
    running = asyncio.create_task(root.run(request))
    await asyncio.wait_for(started.wait(), timeout=1)
    command = _command_signal(request)
    await root.apply_command(command)
    await root.apply_command(command)

    result = await running

    assert result.status == "CANCELED"
    assert sorted(canceled_ordinals) == list(range(1, 9))
    assert finish_input is not None
    assert finish_input.cancel_requested is True
    assert finish_input.skipped_units == 2
    assert finish_input.commands == (command,)
    assert all(outcome.status == "INCONCLUSIVE" for outcome in finish_input.outcomes)
    assert all(
        outcome.error_code == "TASK_ATTEMPT_WORKFLOW_CANCELED_UNKNOWN"
        for outcome in finish_input.outcomes
    )


@pytest.mark.anyio
async def test_root_ignores_invalid_foreign_and_conflicting_command_redelivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    _patch_root_info(monkeypatch, request)
    plan = _plan(request, 1)
    captured: TaskRunFinishInput | None = None

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        nonlocal captured
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            return _settled(value)
        assert name == FINISH_TASK_RUN_ACTIVITY
        captured = cast(TaskRunFinishInput, value)
        return _run_result(captured)

    async def execute_child(
        _child_type: str,
        child_input: UnitAttemptWorkflowInput,
        **_options: object,
    ) -> TaskAttemptWorkflowPayload:
        return TaskAttemptWorkflowPayload(
            execution_unit_id=child_input.execution_unit_id,
            unit_attempt_id=child_input.unit_attempt_id,
            ordinal=child_input.ordinal,
            status="FINISHED_UNSEALED",
        )

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", execute_child)
    root = AtlasTaskRunWorkflow()
    await root.apply_command(cast(TaskRunCommandSignal, {"unexpected": "payload"}))
    await root.apply_command(replace(_command_signal(request), task_run_id=str(UUID(int=99))))
    valid = _command_signal(request)
    await root.apply_command(valid)
    await root.apply_command(replace(valid, command_digest=DIGEST_B))

    result = await root.run(request)

    assert result.status == "CANCELED"
    assert captured is not None
    assert captured.commands == (valid,)
    assert captured.skipped_units == 1


@pytest.mark.anyio
async def test_root_replays_finish_when_command_arrives_during_finish_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    _patch_root_info(monkeypatch, request)
    plan = _plan(request, 1)
    command = _command_signal(request)
    finish_calls: list[TaskRunFinishInput] = []
    root = AtlasTaskRunWorkflow()

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            return TaskRunControlCheckpointPayload(state="DISPATCHABLE")
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            return _settled(value)
        assert name == FINISH_TASK_RUN_ACTIVITY
        finish = cast(TaskRunFinishInput, value)
        finish_calls.append(finish)
        if len(finish_calls) == 1:
            assert finish.commands == ()
            await root.apply_command(command)
            return _run_result(replace(finish, cancel_requested=True))
        return _run_result(finish)

    async def execute_child(
        _child_type: str,
        child_input: UnitAttemptWorkflowInput,
        **_options: object,
    ) -> TaskAttemptWorkflowPayload:
        return TaskAttemptWorkflowPayload(
            execution_unit_id=child_input.execution_unit_id,
            unit_attempt_id=child_input.unit_attempt_id,
            ordinal=child_input.ordinal,
            status="FINISHED_UNSEALED",
        )

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", execute_child)

    result = await root.run(request)

    assert result.status == "CANCELED"
    assert len(finish_calls) == 2
    assert finish_calls[1].cancel_requested is True
    assert finish_calls[1].commands == (command,)


@pytest.mark.anyio
async def test_root_pause_waits_after_active_batch_and_resume_dispatches_next_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _root_request()
    plan = _plan(request, 10)
    _patch_root_info(monkeypatch, request)
    launched: list[int] = []
    first_batch_started = asyncio.Event()
    release_first_batch = asyncio.Event()
    paused_wait_started = asyncio.Event()
    checkpoint_calls = 0
    finish_input: TaskRunFinishInput | None = None

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        nonlocal checkpoint_calls, finish_input
        if name == LOAD_TASK_DISPATCH_PLAN_ACTIVITY:
            return plan
        if name == CHECKPOINT_TASK_RUN_CONTROL_ACTIVITY:
            checkpoint_calls += 1
            return TaskRunControlCheckpointPayload(
                state="PAUSED" if checkpoint_calls == 2 else "DISPATCHABLE"
            )
        if name == PREPARE_TASK_RUN_BATCH_ACTIVITY:
            return TaskBatchPreparePayload(status="AUTHORIZED")
        if name == SETTLE_TASK_ATTEMPT_BATCH_ACTIVITY:
            return _settled(value)
        assert name == FINISH_TASK_RUN_ACTIVITY
        finish_input = cast(TaskRunFinishInput, value)
        return _run_result(finish_input)

    async def execute_child(
        _child_type: str,
        child_input: UnitAttemptWorkflowInput,
        **_options: object,
    ) -> TaskAttemptWorkflowPayload:
        launched.append(child_input.ordinal)
        if len(launched) == 8:
            first_batch_started.set()
        if child_input.ordinal <= 8:
            await release_first_batch.wait()
        return TaskAttemptWorkflowPayload(
            execution_unit_id=child_input.execution_unit_id,
            unit_attempt_id=child_input.unit_attempt_id,
            ordinal=child_input.ordinal,
            status="FINISHED_UNSEALED",
        )

    async def wait_condition(predicate: object) -> None:
        paused_wait_started.set()
        typed_predicate = cast(Any, predicate)
        while not typed_predicate():
            await asyncio.sleep(0)

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflow, "execute_child_workflow", execute_child)
    monkeypatch.setattr(workflow, "wait_condition", wait_condition)
    root = AtlasTaskRunWorkflow()
    running = asyncio.create_task(root.run(request))
    await asyncio.wait_for(first_batch_started.wait(), timeout=1)
    pause = _command_signal(
        request,
        command_type="PAUSE",
        command_id=402,
    )
    await root.apply_command(pause)
    release_first_batch.set()
    await asyncio.wait_for(paused_wait_started.wait(), timeout=1)
    await asyncio.sleep(0)

    assert launched == list(range(1, 9))
    assert not running.done()

    resume = _command_signal(
        request,
        command_type="RESUME",
        command_id=403,
    )
    await root.apply_command(resume)
    result = await asyncio.wait_for(running, timeout=1)

    assert launched == list(range(1, 11))
    assert result.status == "FINISHED_UNSEALED"
    assert checkpoint_calls == 6
    assert finish_input is not None
    assert finish_input.commands == ()
    assert finish_input.skipped_units == 0


@pytest.mark.anyio
async def test_attempt_runs_begin_execute_once_finish_without_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root_request()
    attempt = replace(
        _attempt_input(root, _unit(root, 1)),
        execution_deadline="2026-01-01T00:01:30+00:00",
    )
    _patch_attempt_info(monkeypatch, attempt)
    calls: list[tuple[str, object, dict[str, object]]] = []

    async def execute_activity(
        name: str,
        value: object,
        **options: object,
    ) -> object:
        calls.append((name, value, options))
        if name == PREPARE_TASK_UNIT_ATTEMPT_ACTIVITY:
            return _prepared(attempt)
        if name == BEGIN_TASK_UNIT_ATTEMPT_ACTIVITY:
            return TaskAttemptBeginPayload(status="READY")
        if name == EXECUTE_TASK_UNIT_ATTEMPT_ACTIVITY:
            return TaskAttemptExecutionPayload(status="EXECUTED_UNSEALED")
        assert name == FINISH_TASK_UNIT_ATTEMPT_ACTIVITY
        finish = cast(TaskAttemptFinishInput, value)
        return TaskAttemptWorkflowPayload(
            execution_unit_id=finish.attempt.execution_unit_id,
            unit_attempt_id=finish.attempt.unit_attempt_id,
            ordinal=finish.attempt.ordinal,
            status="FINISHED_UNSEALED",
        )

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)

    result = await AtlasUnitAttemptWorkflow().run(attempt)

    assert [item[0] for item in calls] == [
        PREPARE_TASK_UNIT_ATTEMPT_ACTIVITY,
        BEGIN_TASK_UNIT_ATTEMPT_ACTIVITY,
        EXECUTE_TASK_UNIT_ATTEMPT_ACTIVITY,
        FINISH_TASK_UNIT_ATTEMPT_ACTIVITY,
    ]
    execution_retry = cast(RetryPolicy, calls[2][2]["retry_policy"])
    prepare_retry = cast(RetryPolicy, calls[0][2]["retry_policy"])
    begin_retry = cast(RetryPolicy, calls[1][2]["retry_policy"])
    finish_retry = cast(RetryPolicy, calls[3][2]["retry_policy"])
    assert calls[2][2]["schedule_to_close_timeout"] == timedelta(seconds=90)
    assert calls[2][2]["start_to_close_timeout"] == timedelta(seconds=60)
    assert execution_retry.maximum_attempts == 1
    assert begin_retry.maximum_attempts == finish_retry.maximum_attempts == 0
    assert prepare_retry.maximum_attempts == 0
    assert begin_retry.maximum_interval == finish_retry.maximum_interval == timedelta(minutes=1)
    assert result.status == "FINISHED_UNSEALED"
    assert "PASSED" not in str(result)


@pytest.mark.anyio
async def test_attempt_cancel_and_activity_failure_are_typed_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root_request()
    attempt = _attempt_input(root, _unit(root, 1))
    _patch_attempt_info(monkeypatch, attempt)
    finished: list[TaskAttemptFinishInput] = []
    execute_calls = 0

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        nonlocal execute_calls
        if name == PREPARE_TASK_UNIT_ATTEMPT_ACTIVITY:
            return _prepared(attempt)
        if name == BEGIN_TASK_UNIT_ATTEMPT_ACTIVITY:
            return TaskAttemptBeginPayload(status="READY")
        if name == EXECUTE_TASK_UNIT_ATTEMPT_ACTIVITY:
            execute_calls += 1
            raise RuntimeError("secret provider response")
        assert name == FINISH_TASK_UNIT_ATTEMPT_ACTIVITY
        finish = cast(TaskAttemptFinishInput, value)
        finished.append(finish)
        return TaskAttemptWorkflowPayload(
            execution_unit_id=finish.attempt.execution_unit_id,
            unit_attempt_id=finish.attempt.unit_attempt_id,
            ordinal=finish.attempt.ordinal,
            status=cast(Any, finish.execution.status),
            error_code=finish.execution.error_code,
        )

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)
    failed = await AtlasUnitAttemptWorkflow().run(attempt)
    assert failed.status == "INCONCLUSIVE"
    assert failed.error_code == "TASK_ATTEMPT_ACTIVITY_FAILED"
    assert "secret" not in str(failed).lower()

    child = AtlasUnitAttemptWorkflow()
    await child.request_cancel()
    canceled = await child.run(attempt)
    assert canceled.status == "CANCELED"
    assert execute_calls == 1
    assert finished[-1].execution.status == "CANCELED"


@pytest.mark.anyio
async def test_attempt_deadline_expiry_skips_the_execution_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root_request()
    attempt = replace(
        _attempt_input(root, _unit(root, 1)),
        execution_deadline="2025-01-01T00:00:00+00:00",
    )
    _patch_attempt_info(monkeypatch, attempt)
    finished: TaskAttemptFinishInput | None = None

    async def execute_activity(
        name: str,
        value: object,
        **_options: object,
    ) -> object:
        nonlocal finished
        if name == PREPARE_TASK_UNIT_ATTEMPT_ACTIVITY:
            return _prepared(attempt)
        if name == BEGIN_TASK_UNIT_ATTEMPT_ACTIVITY:
            return TaskAttemptBeginPayload(status="READY")
        if name == EXECUTE_TASK_UNIT_ATTEMPT_ACTIVITY:
            raise AssertionError("expired Attempt must not execute a side effect")
        assert name == FINISH_TASK_UNIT_ATTEMPT_ACTIVITY
        finished = cast(TaskAttemptFinishInput, value)
        return TaskAttemptWorkflowPayload(
            execution_unit_id=finished.attempt.execution_unit_id,
            unit_attempt_id=finished.attempt.unit_attempt_id,
            ordinal=finished.attempt.ordinal,
            status="CANCELED",
            error_code=finished.execution.error_code,
        )

    monkeypatch.setattr(workflow, "execute_activity", execute_activity)

    result = await AtlasUnitAttemptWorkflow().run(attempt)

    assert finished is not None
    assert finished.execution.status == "CANCELED"
    assert finished.execution.error_code == "TASK_ATTEMPT_DEADLINE_EXPIRED"
    assert result.status == "CANCELED"
