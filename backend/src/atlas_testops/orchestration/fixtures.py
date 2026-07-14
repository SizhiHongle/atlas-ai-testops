"""Temporal orchestration for durable fixture preparation and release."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import (
    RetryPolicy,
    WorkflowIDConflictPolicy,
    WorkflowIDReusePolicy,
)
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from atlas_testops.application.fixture_dispatcher import FixtureRunDispatcher
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.fixture import (
    FixtureCleanupSweepBatch,
    FixtureFailureCategory,
    FixtureRun,
)

if TYPE_CHECKING:
    from atlas_testops.application.fixture_runs import FixtureWorkerService

LOAD_FIXTURE_PLAN_ACTIVITY = "atlas.load-fixture-plan/0.1"
EXECUTE_FIXTURE_NODE_ACTIVITY = "atlas.execute-fixture-node/0.1"
RECONCILE_FIXTURE_NODE_ACTIVITY = "atlas.reconcile-fixture-node/0.1"
FINALIZE_FIXTURE_READY_ACTIVITY = "atlas.finalize-fixture-ready/0.1"
BEGIN_FIXTURE_RELEASE_ACTIVITY = "atlas.begin-fixture-release/0.1"
BEGIN_FIXTURE_FAILED_CLEANUP_ACTIVITY = "atlas.begin-fixture-failed-cleanup/0.1"
BEGIN_FIXTURE_CANCELED_CLEANUP_ACTIVITY = "atlas.begin-fixture-canceled-cleanup/0.1"
CLEANUP_FIXTURE_NODE_ACTIVITY = "atlas.cleanup-fixture-node/0.1"
FINALIZE_FIXTURE_RELEASE_ACTIVITY = "atlas.finalize-fixture-release/0.1"
SWEEP_FIXTURE_CLEANUP_ACTIVITY = "atlas.sweep-fixture-cleanup/0.1"
FIXTURE_RUN_WORKFLOW = "atlas.fixture-run-workflow/0.1"
FIXTURE_CLEANUP_WORKFLOW = "atlas.fixture-cleanup-workflow/0.1"
FIXTURE_CLEANUP_SWEEP_WORKFLOW = "atlas.fixture-cleanup-sweep-workflow/0.1"


@dataclass(frozen=True, slots=True)
class FixtureWorkflowInput:
    """Secret-free immutable workflow input."""

    tenant_id: str
    run_id: str
    activity_timeout_seconds: int
    reconcile_poll_seconds: int = 2


@dataclass(frozen=True, slots=True)
class FixtureNodeInput:
    tenant_id: str
    run_id: str
    node_id: str


@dataclass(frozen=True, slots=True)
class FixtureFailureInput:
    tenant_id: str
    run_id: str
    failure_category: str
    failure_code: str


@dataclass(frozen=True, slots=True)
class FixtureFinalizeInput:
    tenant_id: str
    run_id: str
    failed_run: bool


@dataclass(frozen=True, slots=True)
class FixtureSweepInput:
    tenant_id: str
    worker_identity: str
    limit: int
    activity_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class FixturePlanPayload:
    execution_levels: tuple[tuple[str, ...], ...]
    cleanup_order: tuple[str, ...]
    execution_deadline: str


@dataclass(frozen=True, slots=True)
class FixtureNodePayload:
    node_id: str
    status: str
    failure_category: str | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class FixtureRunPayload:
    status: str
    cleanup_state: str


@dataclass(frozen=True, slots=True)
class FixtureReleasePayload:
    status: str
    cleanup_state: str
    cleaned_resources: int
    leaked_resources: int


@dataclass(frozen=True, slots=True)
class FixtureSweepPayload:
    reconciled_found: int
    reconciled_absent: int
    reconciled_inconclusive: int
    cleanup_claimed: int
    cleaned_resources: int
    retry_scheduled: int
    leaked_resources: int
    finalized_runs: int
    observed_at: str


class FixtureActivities:
    """Thin Activity boundary around the isolated Fixture Worker service."""

    def __init__(self, service: FixtureWorkerService) -> None:
        self._service = service

    @activity.defn(name=LOAD_FIXTURE_PLAN_ACTIVITY)
    async def load_plan(self, request: FixtureWorkflowInput) -> FixturePlanPayload:
        plan = await self._service.load_plan(UUID(request.tenant_id), UUID(request.run_id))
        return FixturePlanPayload(
            execution_levels=plan.execution_levels,
            cleanup_order=plan.cleanup_order,
            execution_deadline=plan.execution_deadline.isoformat(),
        )

    @activity.defn(name=EXECUTE_FIXTURE_NODE_ACTIVITY)
    async def execute_node(self, request: FixtureNodeInput) -> FixtureNodePayload:
        result = await self._service.execute_node(
            UUID(request.tenant_id),
            UUID(request.run_id),
            request.node_id,
        )
        return FixtureNodePayload(
            node_id=result.node_id,
            status=result.status.value,
            failure_category=(
                result.failure_category.value if result.failure_category is not None else None
            ),
            failure_code=result.failure_code,
        )

    @activity.defn(name=RECONCILE_FIXTURE_NODE_ACTIVITY)
    async def reconcile_node(self, request: FixtureNodeInput) -> FixtureNodePayload:
        result = await self._service.reconcile_node(
            UUID(request.tenant_id),
            UUID(request.run_id),
            request.node_id,
        )
        return FixtureNodePayload(
            node_id=result.node_id,
            status=result.status.value,
            failure_category=(
                result.failure_category.value if result.failure_category is not None else None
            ),
            failure_code=result.failure_code,
        )

    @activity.defn(name=FINALIZE_FIXTURE_READY_ACTIVITY)
    async def finalize_ready(self, request: FixtureWorkflowInput) -> FixtureRunPayload:
        run = await self._service.finalize_ready(
            UUID(request.tenant_id),
            UUID(request.run_id),
        )
        return FixtureRunPayload(
            status=run.status.value,
            cleanup_state=run.cleanup_state.value,
        )

    @activity.defn(name=BEGIN_FIXTURE_RELEASE_ACTIVITY)
    async def begin_release(self, request: FixtureWorkflowInput) -> FixtureRunPayload:
        run = await self._service.begin_release(
            UUID(request.tenant_id),
            UUID(request.run_id),
        )
        return FixtureRunPayload(
            status=run.status.value,
            cleanup_state=run.cleanup_state.value,
        )

    @activity.defn(name=BEGIN_FIXTURE_FAILED_CLEANUP_ACTIVITY)
    async def begin_failed_cleanup(
        self,
        request: FixtureFailureInput,
    ) -> FixtureRunPayload:
        run = await self._service.begin_failed_cleanup(
            UUID(request.tenant_id),
            UUID(request.run_id),
            category=FixtureFailureCategory(request.failure_category),
            code=request.failure_code,
        )
        return FixtureRunPayload(
            status=run.status.value,
            cleanup_state=run.cleanup_state.value,
        )

    @activity.defn(name=BEGIN_FIXTURE_CANCELED_CLEANUP_ACTIVITY)
    async def begin_canceled_cleanup(
        self,
        request: FixtureWorkflowInput,
    ) -> FixtureRunPayload:
        run = await self._service.begin_canceled_cleanup(
            UUID(request.tenant_id),
            UUID(request.run_id),
        )
        return FixtureRunPayload(
            status=run.status.value,
            cleanup_state=run.cleanup_state.value,
        )

    @activity.defn(name=CLEANUP_FIXTURE_NODE_ACTIVITY)
    async def cleanup_node(self, request: FixtureNodeInput) -> FixtureReleasePayload:
        result = await self._service.cleanup_node(
            UUID(request.tenant_id),
            UUID(request.run_id),
            request.node_id,
        )
        return FixtureReleasePayload(
            status=result.status.value,
            cleanup_state=result.cleanup_state.value,
            cleaned_resources=result.cleaned_resources,
            leaked_resources=result.leaked_resources,
        )

    @activity.defn(name=FINALIZE_FIXTURE_RELEASE_ACTIVITY)
    async def finalize_release(
        self,
        request: FixtureFinalizeInput,
    ) -> FixtureReleasePayload:
        result = await self._service.finalize_release(
            UUID(request.tenant_id),
            UUID(request.run_id),
            failed_run=request.failed_run,
        )
        return FixtureReleasePayload(
            status=result.status.value,
            cleanup_state=result.cleanup_state.value,
            cleaned_resources=result.cleaned_resources,
            leaked_resources=result.leaked_resources,
        )

    @activity.defn(name=SWEEP_FIXTURE_CLEANUP_ACTIVITY)
    async def sweep_cleanup(self, request: FixtureSweepInput) -> FixtureSweepPayload:
        result = await self._service.sweep_cleanup(
            UUID(request.tenant_id),
            worker_identity=request.worker_identity,
            limit=request.limit,
        )
        return FixtureSweepPayload(
            reconciled_found=result.reconciled_found,
            reconciled_absent=result.reconciled_absent,
            reconciled_inconclusive=result.reconciled_inconclusive,
            cleanup_claimed=result.cleanup_claimed,
            cleaned_resources=result.cleaned_resources,
            retry_scheduled=result.retry_scheduled,
            leaked_resources=result.leaked_resources,
            finalized_runs=result.finalized_runs,
            observed_at=result.observed_at.isoformat(),
        )


@workflow.defn(name=FIXTURE_RUN_WORKFLOW)
class FixtureRunWorkflow:
    """Run a frozen DAG, hold READY, then clean resources in reverse order."""

    def __init__(self) -> None:
        self._release_requested = False
        self._cancel_requested = False

    @workflow.signal
    async def request_release(self) -> None:
        self._release_requested = True

    @workflow.signal
    async def request_cancel(self) -> None:
        self._cancel_requested = True

    @workflow.run
    async def run(self, request: FixtureWorkflowInput) -> FixtureReleasePayload:
        activity_timeout = timedelta(seconds=request.activity_timeout_seconds)
        database_retry = RetryPolicy(
            initial_interval=timedelta(seconds=1),
            maximum_interval=timedelta(seconds=5),
            maximum_attempts=3,
        )
        external_retry = RetryPolicy(maximum_attempts=1)
        plan: FixturePlanPayload | None = None
        cleanup_started = False
        failed_run = False
        failed_code: str | None = None
        failed_category: str | None = None
        try:
            loaded_plan = await workflow.execute_activity(
                LOAD_FIXTURE_PLAN_ACTIVITY,
                request,
                result_type=FixturePlanPayload,
                start_to_close_timeout=activity_timeout,
                retry_policy=database_retry,
            )
            plan = cast(FixturePlanPayload, loaded_plan)
            for level in plan.execution_levels:
                if self._cancel_requested:
                    break
                outcomes = await asyncio.gather(
                    *(
                        self._execute_node_with_reconcile(
                            request,
                            node_id=node_id,
                            activity_timeout=activity_timeout,
                            external_retry=external_retry,
                        )
                        for node_id in level
                    )
                )
                failed = next(
                    (item for item in outcomes if item.status != "SUCCEEDED"),
                    None,
                )
                if failed is not None and not self._cancel_requested:
                    failed_code = failed.failure_code or "FIXTURE_NODE_FAILED"
                    failed_category = failed.failure_category
                    break

            failed_run = failed_code is not None
            if self._cancel_requested:
                await workflow.execute_activity(
                    BEGIN_FIXTURE_CANCELED_CLEANUP_ACTIVITY,
                    request,
                    result_type=FixtureRunPayload,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=database_retry,
                )
            elif failed_run:
                await workflow.execute_activity(
                    BEGIN_FIXTURE_FAILED_CLEANUP_ACTIVITY,
                    FixtureFailureInput(
                        tenant_id=request.tenant_id,
                        run_id=request.run_id,
                        failure_category=(
                            failed_category or FixtureFailureCategory.INFRASTRUCTURE.value
                        ),
                        failure_code=failed_code or "FIXTURE_NODE_FAILED",
                    ),
                    result_type=FixtureRunPayload,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=database_retry,
                )
            else:
                await workflow.execute_activity(
                    FINALIZE_FIXTURE_READY_ACTIVITY,
                    request,
                    result_type=FixtureRunPayload,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=database_retry,
                )
                deadline = datetime.fromisoformat(plan.execution_deadline)
                remaining = max(timedelta(0), deadline - workflow.now())
                if (
                    not self._release_requested
                    and not self._cancel_requested
                    and remaining > timedelta(0)
                ):
                    with suppress(TimeoutError):
                        await workflow.wait_condition(
                            lambda: self._release_requested or self._cancel_requested,
                            timeout=remaining,
                        )
                begin_activity = (
                    BEGIN_FIXTURE_CANCELED_CLEANUP_ACTIVITY
                    if self._cancel_requested
                    else BEGIN_FIXTURE_RELEASE_ACTIVITY
                )
                await workflow.execute_activity(
                    begin_activity,
                    request,
                    result_type=FixtureRunPayload,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=database_retry,
                )
            cleanup_started = True
        except asyncio.CancelledError:
            self._cancel_requested = True
            if plan is None:
                loaded_plan = await workflow.execute_activity(
                    LOAD_FIXTURE_PLAN_ACTIVITY,
                    request,
                    result_type=FixturePlanPayload,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=database_retry,
                )
                plan = cast(FixturePlanPayload, loaded_plan)
            await workflow.execute_activity(
                BEGIN_FIXTURE_CANCELED_CLEANUP_ACTIVITY,
                request,
                result_type=FixtureRunPayload,
                start_to_close_timeout=activity_timeout,
                retry_policy=database_retry,
            )
            cleanup_started = True
        finally:
            if cleanup_started and plan is not None:
                for node_id in plan.cleanup_order:
                    await workflow.execute_activity(
                        CLEANUP_FIXTURE_NODE_ACTIVITY,
                        FixtureNodeInput(
                            tenant_id=request.tenant_id,
                            run_id=request.run_id,
                            node_id=node_id,
                        ),
                        result_type=FixtureReleasePayload,
                        start_to_close_timeout=activity_timeout,
                        retry_policy=external_retry,
                    )
                finalized = await workflow.execute_activity(
                    FINALIZE_FIXTURE_RELEASE_ACTIVITY,
                    FixtureFinalizeInput(
                        tenant_id=request.tenant_id,
                        run_id=request.run_id,
                        failed_run=failed_run,
                    ),
                    result_type=FixtureReleasePayload,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=database_retry,
                )
        if not cleanup_started:
            raise RuntimeError("fixture workflow did not enter cleanup")
        return cast(FixtureReleasePayload, finalized)

    async def _execute_node_with_reconcile(
        self,
        request: FixtureWorkflowInput,
        *,
        node_id: str,
        activity_timeout: timedelta,
        external_retry: RetryPolicy,
    ) -> FixtureNodePayload:
        node_input = FixtureNodeInput(
            tenant_id=request.tenant_id,
            run_id=request.run_id,
            node_id=node_id,
        )
        executed = await workflow.execute_activity(
            EXECUTE_FIXTURE_NODE_ACTIVITY,
            node_input,
            result_type=FixtureNodePayload,
            start_to_close_timeout=activity_timeout,
            retry_policy=external_retry,
        )
        outcome = cast(FixtureNodePayload, executed)
        while not self._cancel_requested and outcome.status in {
            "READY",
            "OUTCOME_UNCERTAIN",
        }:
            if outcome.status == "READY":
                retried = await workflow.execute_activity(
                    EXECUTE_FIXTURE_NODE_ACTIVITY,
                    node_input,
                    result_type=FixtureNodePayload,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=external_retry,
                )
                outcome = cast(FixtureNodePayload, retried)
                continue
            if outcome.failure_code == "RECONCILE_EXHAUSTED":
                break
            reconciled = await workflow.execute_activity(
                RECONCILE_FIXTURE_NODE_ACTIVITY,
                node_input,
                result_type=FixtureNodePayload,
                start_to_close_timeout=activity_timeout,
                retry_policy=external_retry,
            )
            outcome = cast(FixtureNodePayload, reconciled)
            if outcome.status == "OUTCOME_UNCERTAIN" and not self._cancel_requested:
                with suppress(TimeoutError):
                    await workflow.wait_condition(
                        lambda: self._cancel_requested,
                        timeout=timedelta(seconds=request.reconcile_poll_seconds),
                    )
        return outcome


@workflow.defn(name=FIXTURE_CLEANUP_WORKFLOW)
class FixtureCleanupWorkflow:
    """Retry one run's due cleanup work without replaying preparation."""

    @workflow.run
    async def run(self, request: FixtureWorkflowInput) -> FixtureReleasePayload:
        activity_timeout = timedelta(seconds=request.activity_timeout_seconds)
        database_retry = RetryPolicy(
            initial_interval=timedelta(seconds=1),
            maximum_interval=timedelta(seconds=5),
            maximum_attempts=3,
        )
        external_retry = RetryPolicy(maximum_attempts=1)
        loaded_plan = await workflow.execute_activity(
            LOAD_FIXTURE_PLAN_ACTIVITY,
            request,
            result_type=FixturePlanPayload,
            start_to_close_timeout=activity_timeout,
            retry_policy=database_retry,
        )
        plan = cast(FixturePlanPayload, loaded_plan)
        for node_id in plan.cleanup_order:
            await workflow.execute_activity(
                CLEANUP_FIXTURE_NODE_ACTIVITY,
                FixtureNodeInput(
                    tenant_id=request.tenant_id,
                    run_id=request.run_id,
                    node_id=node_id,
                ),
                result_type=FixtureReleasePayload,
                start_to_close_timeout=activity_timeout,
                retry_policy=external_retry,
            )
        finalized = await workflow.execute_activity(
            FINALIZE_FIXTURE_RELEASE_ACTIVITY,
            FixtureFinalizeInput(
                tenant_id=request.tenant_id,
                run_id=request.run_id,
                failed_run=False,
            ),
            result_type=FixtureReleasePayload,
            start_to_close_timeout=activity_timeout,
            retry_policy=database_retry,
        )
        return cast(FixtureReleasePayload, finalized)


@workflow.defn(name=FIXTURE_CLEANUP_SWEEP_WORKFLOW)
class FixtureCleanupSweepWorkflow:
    """Run one bounded tenant-scoped recovery and orphan sweep."""

    @workflow.run
    async def run(self, request: FixtureSweepInput) -> FixtureSweepPayload:
        swept = await workflow.execute_activity(
            SWEEP_FIXTURE_CLEANUP_ACTIVITY,
            request,
            result_type=FixtureSweepPayload,
            start_to_close_timeout=timedelta(
                seconds=request.activity_timeout_seconds,
            ),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        return cast(FixtureSweepPayload, swept)


class TemporalFixtureRunDispatcher(FixtureRunDispatcher):
    """Start and signal fixture workflows without waiting for completion."""

    def __init__(
        self,
        client: Client,
        *,
        task_queue: str,
        activity_timeout: timedelta,
        cleanup_grace: timedelta,
    ) -> None:
        normalized_queue = task_queue.strip()
        if not normalized_queue:
            raise ValueError("fixture task queue must not be blank")
        if activity_timeout < timedelta(seconds=10):
            raise ValueError("fixture activity timeout must be at least ten seconds")
        if cleanup_grace < activity_timeout:
            raise ValueError("fixture cleanup grace must cover one activity timeout")
        self._client = client
        self._task_queue = normalized_queue
        self._activity_timeout = activity_timeout
        self._cleanup_grace = cleanup_grace

    async def start(self, run: FixtureRun) -> None:
        execution_timeout = max(
            self._cleanup_grace,
            run.execution_deadline - utc_now() + self._cleanup_grace,
        )
        try:
            await self._client.start_workflow(
                FixtureRunWorkflow.run,
                FixtureWorkflowInput(
                    tenant_id=str(run.tenant_id),
                    run_id=str(run.id),
                    activity_timeout_seconds=int(self._activity_timeout.total_seconds()),
                ),
                id=run.temporal_workflow_id,
                task_queue=self._task_queue,
                execution_timeout=execution_timeout,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except WorkflowAlreadyStartedError:
            return
        except RPCError as error:
            raise _worker_unavailable() from error

    async def release(self, run: FixtureRun) -> None:
        try:
            handle = self._client.get_workflow_handle(run.temporal_workflow_id)
            await handle.signal(FixtureRunWorkflow.request_release)
        except RPCError as error:
            raise _worker_unavailable() from error

    async def cancel(self, run: FixtureRun) -> None:
        try:
            handle = self._client.get_workflow_handle(run.temporal_workflow_id)
            await handle.signal(FixtureRunWorkflow.request_cancel)
        except RPCError as error:
            raise _worker_unavailable() from error

    async def retry_cleanup(self, run: FixtureRun) -> None:
        workflow_id = f"{run.temporal_workflow_id}/cleanup/{run.cleanup_generation}"
        try:
            await self._client.start_workflow(
                FixtureCleanupWorkflow.run,
                FixtureWorkflowInput(
                    tenant_id=str(run.tenant_id),
                    run_id=str(run.id),
                    activity_timeout_seconds=int(self._activity_timeout.total_seconds()),
                ),
                id=workflow_id,
                task_queue=self._task_queue,
                execution_timeout=self._cleanup_grace,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except WorkflowAlreadyStartedError:
            return
        except RPCError as error:
            raise _worker_unavailable() from error

    async def sweep(
        self,
        *,
        tenant_id: UUID,
        worker_identity: str,
        limit: int,
    ) -> FixtureCleanupSweepBatch:
        try:
            result = await self._client.execute_workflow(
                FixtureCleanupSweepWorkflow.run,
                FixtureSweepInput(
                    tenant_id=str(tenant_id),
                    worker_identity=worker_identity,
                    limit=limit,
                    activity_timeout_seconds=int(self._cleanup_grace.total_seconds()),
                ),
                id=f"atlas-fixture-sweep/{tenant_id}/{new_entity_id()}",
                task_queue=self._task_queue,
                execution_timeout=self._cleanup_grace,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
            payload = result
            return FixtureCleanupSweepBatch(
                reconciled_found=payload.reconciled_found,
                reconciled_absent=payload.reconciled_absent,
                reconciled_inconclusive=payload.reconciled_inconclusive,
                cleanup_claimed=payload.cleanup_claimed,
                cleaned_resources=payload.cleaned_resources,
                retry_scheduled=payload.retry_scheduled,
                leaked_resources=payload.leaked_resources,
                finalized_runs=payload.finalized_runs,
                observed_at=datetime.fromisoformat(payload.observed_at),
            )
        except RPCError as error:
            raise _worker_unavailable() from error


def _worker_unavailable() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Fixture Worker 不可用",
        detail="Fixture Workflow 未能提交到独立 Worker，请稍后重试。",
        status_code=503,
    )
