"""Durable orchestration for one already-bound, side-effecting Browser execution."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, cast
from uuid import UUID

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from atlas_testops.application.browser_execution import BrowserWorkerService
from atlas_testops.application.ports.browser_runtime import (
    BrowserExecutionEngine,
    BrowserRuntimeGateway,
)
from atlas_testops.core.contracts import utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import DebugRun, DebugRunLifecycle
from atlas_testops.domain.runtime import ExecutionContract
from atlas_testops.infrastructure.browser_auth import BrowserRuntimePermitSigner

BROWSER_EXECUTION_ACTIVITY = "atlas.execute-browser-run/0.1"
BROWSER_EXECUTION_WORKFLOW = "atlas.browser-execution-workflow/0.1"


class CloseableBrowserRuntimeGateway(BrowserRuntimeGateway, Protocol):
    """One-Activity HTTP gateway whose connection pool is always reclaimed."""

    async def aclose(self) -> None: ...


class BrowserRuntimeGatewayFactory(Protocol):
    """Create an exact run-scoped gateway from a short-lived Temporal input permit."""

    def create(
        self,
        *,
        tenant_id: UUID,
        worker_identity: str,
        execution_permit: str,
    ) -> CloseableBrowserRuntimeGateway: ...


@dataclass(frozen=True, slots=True)
class BrowserExecutionWorkflowInput:
    """Run-scoped authority; the permit expires and also requires the Worker HMAC key."""

    tenant_id: str
    run_id: str
    worker_identity: str
    execution_permit: str
    activity_timeout_seconds: int
    heartbeat_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class BrowserExecutionPayload:
    """Secret-free result returned through Temporal history."""

    run_id: str
    lifecycle: str
    outcome: str
    evidence_manifest_id: str
    evidence_manifest_digest: str


class BrowserExecutionActivities:
    """Long-running Activity boundary around the database-free BrowserWorkerService."""

    def __init__(
        self,
        *,
        gateway_factory: BrowserRuntimeGatewayFactory,
        engine: BrowserExecutionEngine,
    ) -> None:
        self._gateway_factory = gateway_factory
        self._engine = engine

    @activity.defn(name=BROWSER_EXECUTION_ACTIVITY)
    async def execute(
        self,
        request: BrowserExecutionWorkflowInput,
    ) -> BrowserExecutionPayload:
        heartbeat_task = asyncio.create_task(
            self._heartbeat(max(1, request.heartbeat_timeout_seconds // 2))
        )
        gateway = self._gateway_factory.create(
            tenant_id=UUID(request.tenant_id),
            worker_identity=request.worker_identity,
            execution_permit=request.execution_permit,
        )
        try:
            service = BrowserWorkerService(gateway, self._engine)
            run, manifest = await service.execute(
                tenant_id=UUID(request.tenant_id),
                run_id=UUID(request.run_id),
                worker_identity=request.worker_identity,
            )
            return BrowserExecutionPayload(
                run_id=str(run.id),
                lifecycle=run.lifecycle.value,
                outcome=run.outcome.value,
                evidence_manifest_id=str(manifest.id),
                evidence_manifest_digest=manifest.content_digest,
            )
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
            await gateway.aclose()

    @staticmethod
    async def _heartbeat(interval_seconds: int) -> None:
        while True:
            activity.heartbeat("browser execution active")
            await asyncio.sleep(interval_seconds)


@workflow.defn(name=BROWSER_EXECUTION_WORKFLOW)
class BrowserExecutionWorkflow:
    """Execute browser side effects once; Temporal must never retry them blindly."""

    @workflow.run
    async def run(
        self,
        request: BrowserExecutionWorkflowInput,
    ) -> BrowserExecutionPayload:
        result = await workflow.execute_activity(
            BROWSER_EXECUTION_ACTIVITY,
            request,
            result_type=BrowserExecutionPayload,
            start_to_close_timeout=timedelta(seconds=request.activity_timeout_seconds),
            heartbeat_timeout=timedelta(seconds=request.heartbeat_timeout_seconds),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        return cast(BrowserExecutionPayload, result)


class TemporalBrowserExecutionDispatcher:
    """Start the Browser workflow only after Runtime preparation bound a contract."""

    def __init__(
        self,
        client: Client,
        *,
        task_queue: str,
        worker_identity: str,
        permit_signer: BrowserRuntimePermitSigner,
        activity_timeout: timedelta,
        heartbeat_timeout: timedelta,
        permit_ttl: timedelta,
    ) -> None:
        normalized_queue = task_queue.strip()
        normalized_worker = worker_identity.strip()
        if not normalized_queue:
            raise ValueError("browser task queue must not be blank")
        if not 3 <= len(normalized_worker) <= 160:
            raise ValueError("browser worker identity is invalid")
        if heartbeat_timeout >= activity_timeout:
            raise ValueError("browser heartbeat timeout must be below activity timeout")
        if permit_ttl <= activity_timeout:
            raise ValueError("browser permit TTL must exceed activity timeout")
        self._client = client
        self._task_queue = normalized_queue
        self._worker_identity = normalized_worker
        self._permit_signer = permit_signer
        self._activity_timeout = activity_timeout
        self._heartbeat_timeout = heartbeat_timeout
        self._permit_ttl = permit_ttl

    async def start_bound(
        self,
        run: DebugRun,
        contract: ExecutionContract,
    ) -> None:
        """Dispatch one exact BINDING run; preparation remains a separate control-plane step."""

        now = utc_now()
        if (
            run.lifecycle is not DebugRunLifecycle.BINDING
            or run.id != contract.debug_run_id
            or run.tenant_id != contract.tenant_id
            or run.execution_contract_id != contract.id
            or run.execution_contract_digest != contract.content_digest
            or contract.worker_identity != self._worker_identity
            or now >= contract.execution_deadline
        ):
            raise ValueError("browser execution dispatch requires one exact bound contract")
        expires_at = min(now + self._permit_ttl, contract.execution_deadline)
        if expires_at - now <= self._activity_timeout:
            raise ApplicationError(
                error_code=ErrorCode.CONFLICT,
                title="DebugRun 剩余执行窗口不足",
                detail="ExecutionContract 不能覆盖一个完整 Browser Activity。",
                status_code=409,
            )
        permit = self._permit_signer.mint(
            tenant_id=run.tenant_id,
            run_id=run.id,
            worker_identity=self._worker_identity,
            issued_at=now,
            expires_at=expires_at,
        )
        try:
            await self._client.start_workflow(
                BrowserExecutionWorkflow.run,
                BrowserExecutionWorkflowInput(
                    tenant_id=str(run.tenant_id),
                    run_id=str(run.id),
                    worker_identity=self._worker_identity,
                    execution_permit=permit,
                    activity_timeout_seconds=int(self._activity_timeout.total_seconds()),
                    heartbeat_timeout_seconds=int(self._heartbeat_timeout.total_seconds()),
                ),
                id=run.temporal_workflow_id,
                task_queue=self._task_queue,
                execution_timeout=self._activity_timeout + timedelta(seconds=30),
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except WorkflowAlreadyStartedError:
            return
        except RPCError as error:
            raise ApplicationError(
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                title="Browser Worker 不可用",
                detail="Browser execution workflow 未能提交，请稍后重试。",
                status_code=503,
            ) from error
