"""Real Temporal checks for bounded Task root and UnitAttempt orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from os import environ
from typing import Any, cast
from uuid import UUID, uuid7

import pytest
from pydantic import SecretStr
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError as TemporalApplicationError
from temporalio.worker import Worker
from tests.integration.test_task_execution_hosts_pg import (
    DATABASE_URL,
    SeededCaseVersion,
    TaskAggregate,
    _build_aggregate,
    _seed_published_case_version,
)
from tests.integration.test_task_orchestration_pg import _persist_sealed_aggregate

from atlas_testops.application.task_orchestration import TaskWorkerService
from atlas_testops.core.config import Settings
from atlas_testops.domain.task import (
    ExecutionLifecycle,
    ExecutionQuality,
    task_run_workflow_id,
    unit_attempt_workflow_id,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository
from atlas_testops.infrastructure.task_intents import ClaimedTaskWorkflowIntent
from atlas_testops.orchestration.task_intents import (
    TASK_RUN_TASK_QUEUE,
    TASK_RUN_WORKFLOW_TYPE,
    TaskRunWorkflowInput,
    TemporalTaskIntentStarter,
)
from atlas_testops.orchestration.tasks import (
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
    TaskBatchPrepareInput,
    TaskBatchPreparePayload,
    TaskDispatchPlanPayload,
    TaskOrchestrationActivities,
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

TEMPORAL_ADDRESS = environ.get("ATLAS_TEST_TEMPORAL_ADDRESS")
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        TEMPORAL_ADDRESS is None,
        reason="ATLAS_TEST_TEMPORAL_ADDRESS is not configured",
    ),
]


def _intent() -> ClaimedTaskWorkflowIntent:
    now = datetime.now(UTC)
    tenant_id = uuid7()
    task_run_id = uuid7()
    return ClaimedTaskWorkflowIntent(
        id=uuid7(),
        tenant_id=tenant_id,
        project_id=uuid7(),
        task_run_id=task_run_id,
        owner_kind="TASK_RUN",
        owner_id=task_run_id,
        namespace="default",
        workflow_id=task_run_workflow_id(
            tenant_id=tenant_id,
            task_run_id=task_run_id,
        ),
        request_digest=DIGEST_A,
        manifest_hash=DIGEST_B,
        workflow_type=TASK_RUN_WORKFLOW_TYPE,
        task_queue=TASK_RUN_TASK_QUEUE,
        status="CLAIMED",
        claim_token=uuid7(),
        dispatch_revision=2,
        dispatch_attempts=1,
        claim_expires_at=now + timedelta(minutes=2),
        created_at=now,
    )


def _root_input(intent: ClaimedTaskWorkflowIntent) -> TaskRunWorkflowInput:
    return TaskRunWorkflowInput(
        tenant_id=str(intent.tenant_id),
        project_id=str(intent.project_id),
        task_run_id=str(intent.task_run_id),
        request_digest=intent.request_digest,
        manifest_hash=intent.manifest_hash,
    )


def _plan(
    request: TaskRunWorkflowInput,
    count: int,
) -> TaskDispatchPlanPayload:
    deadline = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    units: list[TaskUnitDispatchPayload] = []
    for ordinal in range(1, count + 1):
        attempt_id = uuid7()
        units.append(
            TaskUnitDispatchPayload(
                ordinal=ordinal,
                execution_unit_id=str(uuid7()),
                unit_attempt_id=str(attempt_id),
                unit_attempt_workflow_id=unit_attempt_workflow_id(
                    tenant_id=UUID(request.tenant_id),
                    unit_attempt_id=attempt_id,
                ),
                not_before=datetime.now(UTC).isoformat(),
                execution_deadline=deadline,
                activity_timeout_seconds=60,
            )
        )
    return TaskDispatchPlanPayload(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        task_run_id=request.task_run_id,
        request_digest=request.request_digest,
        manifest_hash=request.manifest_hash,
        units=tuple(units),
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


class _FakeTaskService:
    def __init__(self, plan: TaskDispatchPlanPayload) -> None:
        self.plan = plan
        self.load_calls = 0
        self.prepare_calls: list[UnitAttemptWorkflowInput] = []
        self.prepare_batch_calls: list[TaskBatchPrepareInput] = []
        self.checkpoint_control_calls: list[TaskRunWorkflowInput] = []
        self.start_calls: list[UnitAttemptWorkflowInput] = []
        self.finish_attempt_calls: list[TaskAttemptFinishInput] = []
        self.finish_run_calls: list[TaskRunFinishInput] = []
        self.finish_partitioned_run_calls: list[TaskRunProjectedFinishInput] = []

    async def load_dispatch_plan(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskDispatchPlanPayload:
        assert request == _root_input_from_plan(self.plan)
        self.load_calls += 1
        return self.plan

    async def prepare_batch(
        self,
        request: TaskBatchPrepareInput,
    ) -> TaskBatchPreparePayload:
        self.prepare_batch_calls.append(request)
        return TaskBatchPreparePayload(status="AUTHORIZED")

    async def checkpoint_control(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskRunControlCheckpointPayload:
        self.checkpoint_control_calls.append(request)
        return TaskRunControlCheckpointPayload(state="DISPATCHABLE")

    async def settle_attempt_batch(
        self,
        request: TaskAttemptBatchSettleInput,
    ) -> TaskAttemptBatchSettlePayload:
        return TaskAttemptBatchSettlePayload(
            state="SETTLED",
            final_outcomes=request.outcomes,
        )

    async def start_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskAttemptBeginPayload:
        self.start_calls.append(request)
        return TaskAttemptBeginPayload(status="READY")

    async def prepare_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskUnitExecutionRequest:
        self.prepare_calls.append(request)
        return TaskUnitExecutionRequest(
            attempt=request,
            ticket_id=str(uuid7()),
            ticket_digest=DIGEST_A,
        )

    async def finish_attempt(
        self,
        request: TaskAttemptFinishInput,
    ) -> TaskAttemptWorkflowPayload:
        self.finish_attempt_calls.append(request)
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
            retry_after_seconds=request.execution.retry_after_seconds,
        )

    async def finish_run(self, request: TaskRunFinishInput) -> TaskRunWorkflowPayload:
        self.finish_run_calls.append(request)
        completed = sum(item.status == "FINISHED_UNSEALED" for item in request.outcomes)
        failed = sum(item.status == "FAILED" for item in request.outcomes)
        inconclusive = sum(
            item.status in {"INCONCLUSIVE", "INFRA_ERROR"}
            for item in request.outcomes
        )
        canceled = sum(item.status == "CANCELED" for item in request.outcomes)
        if request.cancel_requested:
            status = "CANCELED"
        elif inconclusive or request.skipped_units:
            status = "INCONCLUSIVE"
        elif failed:
            status = "FAILED"
        else:
            status = "FINISHED_UNSEALED"
        return TaskRunWorkflowPayload(
            task_run_id=request.request.task_run_id,
            status=cast(Any, status),
            completed_units=completed,
            failed_units=failed,
            inconclusive_units=inconclusive,
            canceled_units=canceled,
            skipped_units=request.skipped_units,
        )

    async def finish_partitioned_run(
        self,
        request: TaskRunProjectedFinishInput,
    ) -> TaskRunWorkflowPayload:
        self.finish_partitioned_run_calls.append(request)
        total_units = self.plan.total_units or len(self.plan.units)
        return TaskRunWorkflowPayload(
            task_run_id=request.request.task_run_id,
            status="CANCELED" if request.cancel_requested else "FINISHED_UNSEALED",
            completed_units=0 if request.cancel_requested else total_units,
            failed_units=0,
            inconclusive_units=0,
            canceled_units=0,
            skipped_units=total_units if request.cancel_requested else 0,
        )


class _GatedBeginTaskService(_FakeTaskService):
    """Hold admission open while the first Activity worker stops polling."""

    def __init__(self, plan: TaskDispatchPlanPayload) -> None:
        super().__init__(plan)
        self.begin_started = asyncio.Event()
        self.release_begin = asyncio.Event()

    async def start_attempt(
        self,
        request: UnitAttemptWorkflowInput,
    ) -> TaskAttemptBeginPayload:
        self.start_calls.append(request)
        self.begin_started.set()
        await self.release_begin.wait()
        return TaskAttemptBeginPayload(status="READY")


class _TransientFinishRunTaskService(_FakeTaskService):
    """Recover only after the former three-attempt database retry ceiling."""

    def __init__(self, plan: TaskDispatchPlanPayload) -> None:
        super().__init__(plan)
        self.finish_run_attempts = 0

    async def finish_run(self, request: TaskRunFinishInput) -> TaskRunWorkflowPayload:
        self.finish_run_attempts += 1
        if self.finish_run_attempts <= 3:
            raise ConnectionError("transient database outage")
        return await super().finish_run(request)


class _PagedTaskService(_FakeTaskService):
    """Return one exact page per Continue-As-New cursor."""

    def __init__(
        self,
        first: TaskDispatchPlanPayload,
        final: TaskDispatchPlanPayload,
    ) -> None:
        super().__init__(first)
        self.pages = {
            first.after_ordinal: first,
            final.after_ordinal: final,
        }
        self.load_requests: list[TaskRunWorkflowInput] = []

    async def load_dispatch_plan(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskDispatchPlanPayload:
        self.load_calls += 1
        self.load_requests.append(request)
        plan = self.pages[request.dispatch_after_ordinal]
        assert (
            request.tenant_id,
            request.project_id,
            request.task_run_id,
            request.request_digest,
            request.manifest_hash,
        ) == (
            plan.tenant_id,
            plan.project_id,
            plan.task_run_id,
            plan.request_digest,
            plan.manifest_hash,
        )
        return plan


class _FakeExecutionPort:
    def __init__(
        self,
        *,
        inconclusive_ordinal: int | None = None,
        block_first_batch: bool = False,
    ) -> None:
        self.inconclusive_ordinal = inconclusive_ordinal
        self.block_first_batch = block_first_batch
        self.calls: list[UnitAttemptWorkflowInput] = []
        self.prepared_calls: list[TaskUnitExecutionRequest] = []
        self.first_batch_started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(
        self,
        request: TaskUnitExecutionRequest,
    ) -> TaskAttemptExecutionPayload:
        self.prepared_calls.append(request)
        attempt = request.attempt
        self.calls.append(attempt)
        if self.block_first_batch:
            if len(self.calls) >= 8:
                self.first_batch_started.set()
            await self.release.wait()
        if attempt.ordinal == self.inconclusive_ordinal:
            return TaskAttemptExecutionPayload(
                status="INCONCLUSIVE",
                error_code="TASK_EXECUTION_INCONCLUSIVE",
            )
        return TaskAttemptExecutionPayload(status="EXECUTED_UNSEALED")


class _CancellableExecutionPort(_FakeExecutionPort):
    """Block one Unit until native Temporal cancellation reaches the Activity."""

    def __init__(self, *, blocked_ordinal: int) -> None:
        super().__init__()
        self.blocked_ordinal = blocked_ordinal
        self.blocking_started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(
        self,
        request: TaskUnitExecutionRequest,
    ) -> TaskAttemptExecutionPayload:
        self.prepared_calls.append(request)
        attempt = request.attempt
        self.calls.append(attempt)
        if attempt.ordinal != self.blocked_ordinal:
            return TaskAttemptExecutionPayload(status="EXECUTED_UNSEALED")
        self.blocking_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("blocked execution must be cancelled")


class _ObservedAttemptFinishTaskService(_FakeTaskService):
    """Expose the first persisted Unit outcome for cancellation race tests."""

    def __init__(self, plan: TaskDispatchPlanPayload) -> None:
        super().__init__(plan)
        self.first_attempt_finished = asyncio.Event()

    async def finish_attempt(
        self,
        request: TaskAttemptFinishInput,
    ) -> TaskAttemptWorkflowPayload:
        result = await super().finish_attempt(request)
        if request.attempt.ordinal == 1:
            self.first_attempt_finished.set()
        return result


class _PauseResumeTaskService(_FakeTaskService):
    """Expose the durable control gate observed at Root batch boundaries."""

    def __init__(self, plan: TaskDispatchPlanPayload) -> None:
        super().__init__(plan)
        self.control_state = "DISPATCHABLE"
        self.paused_checkpoint = asyncio.Event()

    async def checkpoint_control(
        self,
        request: TaskRunWorkflowInput,
    ) -> TaskRunControlCheckpointPayload:
        self.checkpoint_control_calls.append(request)
        if self.control_state == "PAUSED":
            self.paused_checkpoint.set()
        return TaskRunControlCheckpointPayload(state=cast(Any, self.control_state))


class _SecretFailingExecutionPort(_FakeExecutionPort):
    async def execute(
        self,
        request: TaskUnitExecutionRequest,
    ) -> TaskAttemptExecutionPayload:
        self.prepared_calls.append(request)
        self.calls.append(request.attempt)
        raise RuntimeError("adapter-secret-must-not-enter-temporal-history")


class _InvalidExecutionPort(_FakeExecutionPort):
    async def execute(
        self,
        request: TaskUnitExecutionRequest,
    ) -> TaskAttemptExecutionPayload:
        self.prepared_calls.append(request)
        self.calls.append(request.attempt)
        return TaskAttemptExecutionPayload(status=cast(Any, "PASSED"))


def _root_input_from_plan(plan: TaskDispatchPlanPayload) -> TaskRunWorkflowInput:
    return TaskRunWorkflowInput(
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        task_run_id=plan.task_run_id,
        request_digest=plan.request_digest,
        manifest_hash=plan.manifest_hash,
    )


def _workers(
    client: Client,
    service: TaskOrchestrationService,
    port: TaskUnitExecutionPort,
) -> tuple[Worker, Worker]:
    activities = TaskOrchestrationActivities(service, port)
    return (
        Worker(
            client,
            task_queue=TASK_RUN_TASK_QUEUE,
            workflows=[AtlasTaskRunWorkflow],
            activities=[
                activities.load_dispatch_plan,
                activities.prepare_batch,
                activities.checkpoint_control,
                activities.settle_attempt_batch,
                activities.finish_run,
                activities.finish_partitioned_run,
            ],
        ),
        Worker(
            client,
            task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
            workflows=[AtlasUnitAttemptWorkflow],
            activities=[
                activities.prepare_attempt,
                activities.begin_attempt,
                activities.execute_attempt,
                activities.finish_attempt,
            ],
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("unit_count", [1, 9])
async def test_real_task_root_uses_intent_contract_batches_children_and_replays_once(
    unit_count: int,
) -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    intent = _intent()
    request = _root_input(intent)
    plan = _plan(request, unit_count)
    service = _FakeTaskService(plan)
    port = _FakeExecutionPort(
        inconclusive_ordinal=unit_count if unit_count > 1 else None,
    )
    root_worker, attempt_worker = _workers(client, service, port)
    starter = TemporalTaskIntentStarter(
        client,
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=5),
        retry_delay=timedelta(milliseconds=100),
    )

    async with root_worker, attempt_worker:
        await starter.start(intent)
        handle = client.get_workflow_handle_for(
            AtlasTaskRunWorkflow.run,
            intent.workflow_id,
        )
        result = await handle.result()
        root_description = await handle.describe()
        executions_before_replay = len(port.calls)
        replay = replace(
            intent,
            claim_token=uuid7(),
            dispatch_revision=3,
            dispatch_attempts=2,
        )
        await starter.start(replay)
        await asyncio.sleep(0.1)

        child_descriptions = []
        child_histories: list[str] = []
        for unit in plan.units:
            child_handle = client.get_workflow_handle(
                unit.unit_attempt_workflow_id,
                result_type=TaskAttemptWorkflowPayload,
            )
            child_descriptions.append(await child_handle.describe())
            child_histories.append((await child_handle.fetch_history()).to_json())

    assert root_description.workflow_type == TASK_RUN_WORKFLOW_TYPE
    assert root_description.task_queue == TASK_RUN_TASK_QUEUE
    assert len(port.calls) == executions_before_replay == unit_count
    assert service.load_calls == 1
    assert len(service.start_calls) == unit_count
    assert len(service.prepare_calls) == unit_count
    assert len(service.finish_attempt_calls) == unit_count
    assert len(service.finish_run_calls) == 1
    assert sorted(item.ordinal for item in port.calls) == list(range(1, unit_count + 1))
    assert all(
        description.workflow_type == TASK_UNIT_ATTEMPT_WORKFLOW_TYPE
        and description.task_queue == TASK_UNIT_ATTEMPT_TASK_QUEUE
        for description in child_descriptions
    )
    assert {item.unit_attempt_id for item in port.calls} == {
        unit.unit_attempt_id for unit in plan.units
    }
    assert all(
        prepared.attempt in port.calls
        and UUID(prepared.ticket_id)
        and prepared.ticket_digest == DIGEST_A
        for prepared in port.prepared_calls
    )
    assert all(
        "password" not in history.casefold()
        and "credential" not in history.casefold()
        and "authorization" not in history.casefold()
        for history in child_histories
    )
    assert result.status == ("INCONCLUSIVE" if unit_count > 1 else "FINISHED_UNSEALED")
    assert "PASSED" not in str(result)


@pytest.mark.anyio
async def test_real_task_root_continues_history_and_finishes_65_units_once() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    intent = _intent()
    request = _root_input(intent)
    first = replace(
        _plan(request, 64),
        total_units=65,
        has_more=True,
    )
    final_unit = replace(_plan(request, 1).units[0], ordinal=65)
    final = TaskDispatchPlanPayload(
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        task_run_id=request.task_run_id,
        request_digest=request.request_digest,
        manifest_hash=request.manifest_hash,
        units=(final_unit,),
        after_ordinal=64,
        total_units=65,
        has_more=False,
    )
    service = _PagedTaskService(first, final)
    port = _FakeExecutionPort()
    root_worker, attempt_worker = _workers(
        client,
        cast(TaskOrchestrationService, service),
        cast(TaskUnitExecutionPort, port),
    )
    starter = TemporalTaskIntentStarter(
        client,
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=5),
        retry_delay=timedelta(milliseconds=100),
    )

    async with root_worker, attempt_worker:
        await starter.start(intent)
        handle = client.get_workflow_handle_for(
            AtlasTaskRunWorkflow.run,
            intent.workflow_id,
        )
        result = await handle.result()

    assert result.status == "FINISHED_UNSEALED"
    assert result.completed_units == 65
    assert [item.dispatch_after_ordinal for item in service.load_requests] == [0, 64]
    assert service.finish_run_calls == []
    assert len(service.finish_partitioned_run_calls) == 1
    assert len(port.calls) == 65
    assert sorted(item.ordinal for item in port.calls) == list(range(1, 66))


@pytest.mark.anyio
async def test_real_attempt_redacts_adapter_exception_from_result_and_history() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    request = _root_input(_intent())
    plan = _plan(request, 1)
    attempt = _attempt_input(request, plan.units[0])
    service = _FakeTaskService(plan)
    port = _SecretFailingExecutionPort()
    activities = TaskOrchestrationActivities(service, port)
    worker = Worker(
        client,
        task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        workflows=[AtlasUnitAttemptWorkflow],
        activities=[
            activities.prepare_attempt,
            activities.begin_attempt,
            activities.execute_attempt,
            activities.finish_attempt,
        ],
    )

    async with worker:
        handle = await client.start_workflow(
            AtlasUnitAttemptWorkflow.run,
            attempt,
            id=plan.units[0].unit_attempt_workflow_id,
            task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        )
        result = await asyncio.wait_for(handle.result(), timeout=10)
        history = await handle.fetch_history()

    history_json = history.to_json()
    event_types = [event.event_type for event in history.events]
    assert result.status == "INCONCLUSIVE"
    assert result.error_code == "TASK_ATTEMPT_ACTIVITY_FAILED"
    assert len(port.calls) == 1
    assert event_types.count(EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED) == 1
    assert event_types.count(EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED) == 0
    assert "adapter-secret" not in history_json.casefold()
    assert "adapter-secret" not in str(result).casefold()
    assert "TASK_UNIT_EXECUTION_ADAPTER_FAILED" in history_json


@pytest.mark.anyio
async def test_real_invalid_workflow_input_fails_once_without_workflow_task_retry() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    request = _root_input(_intent())
    invalid = replace(request, schema_version="invalid-schema-version")
    worker = Worker(
        client,
        task_queue=TASK_RUN_TASK_QUEUE,
        workflows=[AtlasTaskRunWorkflow],
    )

    async with worker:
        handle = await client.start_workflow(
            AtlasTaskRunWorkflow.run,
            invalid,
            id=task_run_workflow_id(
                tenant_id=UUID(request.tenant_id),
                task_run_id=UUID(request.task_run_id),
            ),
            task_queue=TASK_RUN_TASK_QUEUE,
        )
        with pytest.raises(WorkflowFailureError) as captured:
            await asyncio.wait_for(handle.result(), timeout=10)
        history = await handle.fetch_history()

    failure = captured.value.cause
    event_types = [event.event_type for event in history.events]
    assert isinstance(failure, TemporalApplicationError)
    assert failure.message == "TASK_ROOT_WORKFLOW_INPUT_INVALID"
    assert failure.type == "TaskWorkflowValidationError"
    assert failure.non_retryable is True
    assert event_types.count(EventType.EVENT_TYPE_WORKFLOW_TASK_SCHEDULED) == 1
    assert event_types.count(EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED) == 0
    assert event_types.count(EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED) == 1


@pytest.mark.anyio
async def test_real_invalid_execution_return_converges_without_workflow_task_retry() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    request = _root_input(_intent())
    plan = _plan(request, 1)
    attempt = _attempt_input(request, plan.units[0])
    service = _FakeTaskService(plan)
    port = _InvalidExecutionPort()
    activities = TaskOrchestrationActivities(service, port)
    worker = Worker(
        client,
        task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        workflows=[AtlasUnitAttemptWorkflow],
        activities=[
            activities.prepare_attempt,
            activities.begin_attempt,
            activities.execute_attempt,
            activities.finish_attempt,
        ],
    )

    async with worker:
        handle = await client.start_workflow(
            AtlasUnitAttemptWorkflow.run,
            attempt,
            id=plan.units[0].unit_attempt_workflow_id,
            task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        )
        result = await asyncio.wait_for(handle.result(), timeout=10)
        history = await handle.fetch_history()

    event_types = [event.event_type for event in history.events]
    assert result.status == "INCONCLUSIVE"
    assert result.error_code == "TASK_ATTEMPT_ACTIVITY_FAILED"
    assert len(port.calls) == 1
    assert event_types.count(EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED) == 0


@pytest.mark.anyio
async def test_real_native_attempt_cancel_marks_running_side_effect_unknown() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    request = _root_input(_intent())
    plan = _plan(request, 1)
    attempt = _attempt_input(request, plan.units[0])
    service = _FakeTaskService(plan)
    port = _CancellableExecutionPort(blocked_ordinal=1)
    activities = TaskOrchestrationActivities(service, port)
    worker = Worker(
        client,
        task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        workflows=[AtlasUnitAttemptWorkflow],
        activities=[
            activities.prepare_attempt,
            activities.begin_attempt,
            activities.execute_attempt,
            activities.finish_attempt,
        ],
    )

    async with worker:
        handle = await client.start_workflow(
            AtlasUnitAttemptWorkflow.run,
            attempt,
            id=plan.units[0].unit_attempt_workflow_id,
            task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        )
        await asyncio.wait_for(port.blocking_started.wait(), timeout=10)
        await handle.cancel()
        result = await asyncio.wait_for(handle.result(), timeout=15)

    assert port.cancelled.is_set()
    assert len(port.calls) == 1
    assert len(service.finish_attempt_calls) == 1
    persisted = service.finish_attempt_calls[0].execution
    assert persisted.status == "INCONCLUSIVE"
    assert persisted.error_code == "TASK_ATTEMPT_EXECUTION_CANCELED_UNKNOWN"
    assert result.status == "INCONCLUSIVE"
    assert result.error_code == "TASK_ATTEMPT_EXECUTION_CANCELED_UNKNOWN"


@pytest.mark.anyio
async def test_real_native_root_cancel_preserves_completed_child_outcomes() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    intent = _intent()
    request = _root_input(intent)
    plan = _plan(request, 2)
    service = _ObservedAttemptFinishTaskService(plan)
    port = _CancellableExecutionPort(blocked_ordinal=2)
    root_worker, attempt_worker = _workers(client, service, port)

    async with root_worker, attempt_worker:
        handle = await client.start_workflow(
            AtlasTaskRunWorkflow.run,
            request,
            id=intent.workflow_id,
            task_queue=TASK_RUN_TASK_QUEUE,
        )
        await asyncio.wait_for(port.blocking_started.wait(), timeout=10)
        await asyncio.wait_for(service.first_attempt_finished.wait(), timeout=10)
        await handle.cancel()
        result = await asyncio.wait_for(handle.result(), timeout=15)

    assert port.cancelled.is_set()
    assert sorted(item.ordinal for item in port.calls) == [1, 2]
    assert 1 <= len(service.finish_run_calls) <= 2
    finish = service.finish_run_calls[-1]
    assert all(call == finish for call in service.finish_run_calls)
    assert finish.cancel_requested is True
    assert finish.skipped_units == 0
    assert [(item.ordinal, item.status) for item in finish.outcomes] == [
        (1, "FINISHED_UNSEALED"),
        (2, "INCONCLUSIVE"),
    ]
    assert result.status == "CANCELED"
    assert result.completed_units == 1
    assert result.inconclusive_units == 1
    assert result.skipped_units == 0


@pytest.mark.anyio
async def test_real_durable_command_signal_deduplicates_and_preserves_completed_child() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    intent = _intent()
    request = _root_input(intent)
    plan = _plan(request, 2)
    service = _ObservedAttemptFinishTaskService(plan)
    port = _CancellableExecutionPort(blocked_ordinal=2)
    root_worker, attempt_worker = _workers(client, service, port)
    command = TaskRunCommandSignal(
        command_id=str(uuid7()),
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        task_run_id=request.task_run_id,
        command_type="CANCEL",
        command_digest=DIGEST_A,
        accepted_run_revision=3,
        schema_version=TASK_RUN_COMMAND_SIGNAL_SCHEMA,
    )

    async with root_worker, attempt_worker:
        handle = await client.start_workflow(
            AtlasTaskRunWorkflow.run,
            request,
            id=intent.workflow_id,
            task_queue=TASK_RUN_TASK_QUEUE,
        )
        await asyncio.wait_for(port.blocking_started.wait(), timeout=10)
        await asyncio.wait_for(service.first_attempt_finished.wait(), timeout=10)
        await handle.signal(AtlasTaskRunWorkflow.apply_command, command)
        await handle.signal(AtlasTaskRunWorkflow.apply_command, command)
        result = await asyncio.wait_for(handle.result(), timeout=15)
        history = (await handle.fetch_history()).to_json()

    assert port.cancelled.is_set()
    assert 1 <= len(service.finish_run_calls) <= 2
    finish = service.finish_run_calls[-1]
    assert all(call == finish for call in service.finish_run_calls)
    assert finish.cancel_requested is True
    assert finish.commands == (command,)
    assert [(item.ordinal, item.status) for item in finish.outcomes] == [
        (1, "FINISHED_UNSEALED"),
        (2, "INCONCLUSIVE"),
    ]
    assert result.status == "CANCELED"
    assert result.completed_units == 1
    assert result.inconclusive_units == 1
    assert "password" not in history.casefold()


@pytest.mark.anyio
async def test_real_task_root_cancel_stops_before_the_second_child_batch() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    intent = _intent()
    request = _root_input(intent)
    plan = _plan(request, 10)
    service = _FakeTaskService(plan)
    port = _FakeExecutionPort(block_first_batch=True)
    root_worker, attempt_worker = _workers(client, service, port)
    starter = TemporalTaskIntentStarter(
        client,
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=5),
        retry_delay=timedelta(milliseconds=100),
    )

    async with root_worker, attempt_worker:
        await starter.start(intent)
        handle = client.get_workflow_handle_for(
            AtlasTaskRunWorkflow.run,
            intent.workflow_id,
        )
        await asyncio.wait_for(port.first_batch_started.wait(), timeout=10)
        await handle.signal(AtlasTaskRunWorkflow.request_cancel)
        await asyncio.sleep(0.1)
        port.release.set()
        result = await handle.result()

    assert len(port.calls) == 8
    assert sorted(item.ordinal for item in port.calls) == list(range(1, 9))
    assert len(service.finish_run_calls) == 1
    assert service.finish_run_calls[0].cancel_requested is True
    assert service.finish_run_calls[0].skipped_units == 2
    assert result.status == "CANCELED"
    assert result.skipped_units == 2
    assert "PASSED" not in str(result)


@pytest.mark.anyio
async def test_real_task_root_pause_waits_at_batch_boundary_then_resume_continues() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    intent = _intent()
    request = _root_input(intent)
    plan = _plan(request, 10)
    service = _PauseResumeTaskService(plan)
    port = _FakeExecutionPort(block_first_batch=True)
    root_worker, attempt_worker = _workers(client, service, port)
    pause = TaskRunCommandSignal(
        command_id=str(uuid7()),
        tenant_id=request.tenant_id,
        project_id=request.project_id,
        task_run_id=request.task_run_id,
        command_type="PAUSE",
        command_digest=DIGEST_A,
        accepted_run_revision=3,
        schema_version=TASK_RUN_COMMAND_SIGNAL_SCHEMA,
    )
    resume = replace(
        pause,
        command_id=str(uuid7()),
        command_type="RESUME",
        accepted_run_revision=5,
    )

    async with root_worker, attempt_worker:
        handle = await client.start_workflow(
            AtlasTaskRunWorkflow.run,
            request,
            id=intent.workflow_id,
            task_queue=TASK_RUN_TASK_QUEUE,
        )
        await asyncio.wait_for(port.first_batch_started.wait(), timeout=10)
        service.control_state = "PAUSED"
        await handle.signal(AtlasTaskRunWorkflow.apply_command, pause)
        port.release.set()
        await asyncio.wait_for(service.paused_checkpoint.wait(), timeout=10)
        await asyncio.sleep(0.2)

        assert sorted(item.ordinal for item in port.calls) == list(range(1, 9))

        service.control_state = "DISPATCHABLE"
        await handle.signal(AtlasTaskRunWorkflow.apply_command, resume)
        result = await asyncio.wait_for(handle.result(), timeout=15)

    assert sorted(item.ordinal for item in port.calls) == list(range(1, 11))
    assert result.status == "FINISHED_UNSEALED"
    assert service.finish_run_calls[-1].commands == ()
    assert service.finish_run_calls[-1].skipped_units == 0


@pytest.mark.anyio
async def test_real_attempt_deadline_expires_while_execution_activity_is_queued() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    request = _root_input(_intent())
    plan = _plan(request, 1)
    deadline = datetime.now(UTC) + timedelta(seconds=4)
    unit = replace(
        plan.units[0],
        execution_deadline=deadline.isoformat(),
        activity_timeout_seconds=30,
    )
    plan = replace(plan, units=(unit,))
    attempt = UnitAttemptWorkflowInput(
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
    service = _GatedBeginTaskService(plan)
    port = _FakeExecutionPort()
    activities = TaskOrchestrationActivities(service, port)
    workflow_worker = Worker(
        client,
        task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        workflows=[AtlasUnitAttemptWorkflow],
    )
    first_activity_worker = Worker(
        client,
        task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        activities=[
            activities.prepare_attempt,
            activities.begin_attempt,
            activities.execute_attempt,
            activities.finish_attempt,
        ],
        graceful_shutdown_timeout=timedelta(seconds=5),
    )

    async with workflow_worker, first_activity_worker:
        handle = await client.start_workflow(
            AtlasUnitAttemptWorkflow.run,
            attempt,
            id=unit.unit_attempt_workflow_id,
            task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
        )
        await asyncio.wait_for(service.begin_started.wait(), timeout=10)
        shutdown = asyncio.create_task(first_activity_worker.shutdown())
        await asyncio.sleep(0.1)
        service.release_begin.set()
        await asyncio.wait_for(shutdown, timeout=10)

        wait_for_deadline = (deadline - datetime.now(UTC)).total_seconds() + 0.75
        await asyncio.sleep(max(wait_for_deadline, 0))
        replacement_activity_worker = Worker(
            client,
            task_queue=TASK_UNIT_ATTEMPT_TASK_QUEUE,
            activities=[
                activities.prepare_attempt,
                activities.begin_attempt,
                activities.execute_attempt,
                activities.finish_attempt,
            ],
        )
        async with replacement_activity_worker:
            result = await asyncio.wait_for(handle.result(), timeout=10)

    assert port.calls == []
    assert len(service.start_calls) == 1
    assert len(service.finish_attempt_calls) == 1
    assert service.finish_attempt_calls[0].execution.status == "INCONCLUSIVE"
    assert service.finish_attempt_calls[0].execution.error_code == ("TASK_ATTEMPT_ACTIVITY_FAILED")
    assert result.status == "INCONCLUSIVE"


@pytest.mark.anyio
async def test_real_root_retries_transient_database_activity_beyond_three_attempts() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    intent = _intent()
    request = _root_input(intent)
    plan = _plan(request, 1)
    service = _TransientFinishRunTaskService(plan)
    port = _FakeExecutionPort()
    root_worker, attempt_worker = _workers(client, service, port)
    starter = TemporalTaskIntentStarter(
        client,
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=5),
        retry_delay=timedelta(milliseconds=100),
    )

    async with root_worker, attempt_worker:
        await starter.start(intent)
        handle = client.get_workflow_handle_for(
            AtlasTaskRunWorkflow.run,
            intent.workflow_id,
        )
        result = await asyncio.wait_for(handle.result(), timeout=20)

    assert service.finish_run_attempts == 4
    assert len(service.finish_run_calls) == 1
    assert len(port.calls) == 1
    assert result.status == "FINISHED_UNSEALED"


@pytest.mark.skipif(
    DATABASE_URL is None,
    reason="ATLAS_TEST_DATABASE_URL is not configured",
)
def test_real_task_workers_persist_the_complete_postgresql_chain() -> None:
    """Run the real Workflows and database Activities together as atlas_app."""

    assert DATABASE_URL is not None
    assert TEMPORAL_ADDRESS is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)

    asyncio.run(_exercise_real_task_workers(settings, seeded))


async def _exercise_real_task_workers(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    assert TEMPORAL_ADDRESS is not None
    database = Database(settings)
    repository = TaskRunRepository()
    port = _FakeExecutionPort()
    await database.open()
    try:
        aggregate = await _persist_sealed_aggregate(
            database,
            _build_aggregate(seeded),
        )
        service = TaskWorkerService(database)
        client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
        root_worker, attempt_worker = _workers(client, service, port)
        intent = _claimed_intent_for_aggregate(aggregate)
        starter = TemporalTaskIntentStarter(
            client,
            rpc_attempts=2,
            rpc_timeout=timedelta(seconds=5),
            retry_delay=timedelta(milliseconds=100),
        )

        async with root_worker, attempt_worker:
            await starter.start(intent)
            handle = client.get_workflow_handle_for(
                AtlasTaskRunWorkflow.run,
                intent.workflow_id,
            )
            result = await handle.result()

        context = DatabaseContext(
            tenant_id=seeded.tenant_id,
            request_id=f"task-workflow-e2e:{aggregate.run.id}",
        )
        async with database.transaction(context) as connection:
            run = await repository.get_run(connection, aggregate.run.id)
            unit = await repository.get_unit(connection, aggregate.unit.id)
            attempt = await repository.get_attempt(connection, aggregate.attempt.id)
            events = await repository.list_events(
                connection,
                task_run_id=aggregate.run.id,
                after_seq=0,
                limit=100,
            )

        assert result.status == "FINISHED_UNSEALED"
        assert result.completed_units == 1
        assert len(port.calls) == 1
        assert run is not None and unit is not None and attempt is not None
        assert all(
            projection.lifecycle is ExecutionLifecycle.CLOSED for projection in (run, unit, attempt)
        )
        assert all(
            projection.quality is ExecutionQuality.INCONCLUSIVE
            for projection in (run, unit, attempt)
        )
        assert tuple(event.seq for event in events) == tuple(range(1, 10))
        assert all(event.quality is not ExecutionQuality.PASSED for event in events)
        assert "PASSED" not in str(result)
    finally:
        await database.close()


def _claimed_intent_for_aggregate(aggregate: TaskAggregate) -> ClaimedTaskWorkflowIntent:
    now = datetime.now(UTC)
    request_digest = aggregate.run.request_digest
    workflow_id = aggregate.run.temporal_workflow_id
    assert request_digest is not None
    assert workflow_id is not None
    return ClaimedTaskWorkflowIntent(
        id=aggregate.run.id,
        tenant_id=aggregate.run.tenant_id,
        project_id=aggregate.run.project_id,
        task_run_id=aggregate.run.id,
        owner_kind="TASK_RUN",
        owner_id=aggregate.run.id,
        namespace="default",
        workflow_id=workflow_id,
        request_digest=request_digest,
        manifest_hash=aggregate.run.manifest_hash,
        workflow_type=TASK_RUN_WORKFLOW_TYPE,
        task_queue=TASK_RUN_TASK_QUEUE,
        status="CLAIMED",
        claim_token=uuid7(),
        dispatch_revision=2,
        dispatch_attempts=1,
        claim_expires_at=now + timedelta(minutes=2),
        created_at=now,
    )
