"""Temporal submission and collision verification for Task start intents."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from re import fullmatch
from typing import Any, Protocol, cast

from temporalio.client import Client, WorkflowExecutionDescription, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from atlas_testops.application.task_intents import (
    TaskIntentInvariantError,
    TaskIntentTransientError,
)
from atlas_testops.domain.task import task_run_workflow_id
from atlas_testops.infrastructure.task_intents import ClaimedTaskWorkflowIntent

TASK_RUN_WORKFLOW_TYPE = "AtlasTaskRunWorkflow"
TASK_RUN_TASK_QUEUE = "atlas-task-run"
TASK_INTENT_MEMO_KEY = "atlasTaskIntent"
TASK_INTENT_MEMO_SCHEMA = "atlas.task-workflow-intent/0.1"
TASK_RUN_WORKFLOW_INPUT_SCHEMA = "atlas.task-run-workflow-input/0.1"
_SHA256_DIGEST_PATTERN = r"sha256:[0-9a-f]{64}"
_PERMANENT_RPC_ERROR_CODES = {
    RPCStatusCode.INVALID_ARGUMENT: "TEMPORAL_RPC_INVALID_ARGUMENT",
    RPCStatusCode.PERMISSION_DENIED: "TEMPORAL_RPC_PERMISSION_DENIED",
    RPCStatusCode.UNAUTHENTICATED: "TEMPORAL_RPC_UNAUTHENTICATED",
    RPCStatusCode.NOT_FOUND: "TEMPORAL_RPC_NOT_FOUND",
    RPCStatusCode.FAILED_PRECONDITION: "TEMPORAL_RPC_FAILED_PRECONDITION",
    RPCStatusCode.OUT_OF_RANGE: "TEMPORAL_RPC_OUT_OF_RANGE",
    RPCStatusCode.UNIMPLEMENTED: "TEMPORAL_RPC_UNIMPLEMENTED",
    RPCStatusCode.DATA_LOSS: "TEMPORAL_RPC_DATA_LOSS",
}
_TRANSIENT_RPC_STATUSES = {
    RPCStatusCode.CANCELLED,
    RPCStatusCode.UNKNOWN,
    RPCStatusCode.DEADLINE_EXCEEDED,
    RPCStatusCode.RESOURCE_EXHAUSTED,
    RPCStatusCode.ABORTED,
    RPCStatusCode.INTERNAL,
    RPCStatusCode.UNAVAILABLE,
}


@dataclass(frozen=True, slots=True)
class TaskRunWorkflowInput:
    """Secret-free immutable identity handed to the real Task root Workflow."""

    tenant_id: str
    project_id: str
    task_run_id: str
    request_digest: str
    manifest_hash: str
    schema_version: str = TASK_RUN_WORKFLOW_INPUT_SCHEMA


class _TaskIntentTemporalClient(Protocol):
    """SDK surface including the runtime-supported stable request ID option."""

    @property
    def namespace(self) -> str: ...

    async def start_workflow(
        self,
        workflow: str,
        request: TaskRunWorkflowInput,
        *,
        id: str,
        task_queue: str,
        id_reuse_policy: WorkflowIDReusePolicy,
        id_conflict_policy: WorkflowIDConflictPolicy,
        memo: Mapping[str, Any],
        request_id: str,
        rpc_timeout: timedelta,
    ) -> WorkflowHandle[Any, Any]: ...

    def get_workflow_handle(self, workflow_id: str) -> WorkflowHandle[Any, Any]: ...


class TemporalTaskIntentStarter:
    """Start or verify exactly one namespace-global Task root Workflow."""

    def __init__(
        self,
        client: Client,
        *,
        rpc_attempts: int = 3,
        rpc_timeout: timedelta = timedelta(seconds=10),
        retry_delay: timedelta = timedelta(milliseconds=250),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 1 <= rpc_attempts <= 5:
            raise ValueError("Temporal RPC attempts must be between 1 and 5")
        if (
            not timedelta(0) < rpc_timeout <= timedelta(minutes=2)
            or not timedelta(0) <= retry_delay <= timedelta(seconds=5)
        ):
            raise ValueError("Temporal RPC timing is invalid")
        self._client = cast(_TaskIntentTemporalClient, client)
        self._rpc_attempts = rpc_attempts
        self._rpc_timeout = rpc_timeout
        self._retry_delay = retry_delay
        self._sleep = sleep

    async def start(self, intent: ClaimedTaskWorkflowIntent) -> None:
        """Submit with stable identity, then verify the accepted execution."""

        request = _validate_and_build_request(intent, client_namespace=self._client.namespace)
        expected_memo = _memo_identity(request)
        last_error: RPCError | None = None

        for attempt in range(self._rpc_attempts):
            try:
                handle = await self._start_or_get(intent, request, expected_memo)
                description = await handle.describe(rpc_timeout=self._rpc_timeout)
                await _verify_description(intent, description, expected_memo)
                return
            except TaskIntentInvariantError:
                raise
            except RPCError as error:
                permanent_code = _PERMANENT_RPC_ERROR_CODES.get(error.status)
                if permanent_code is not None:
                    raise TaskIntentInvariantError(permanent_code) from error
                if error.status not in _TRANSIENT_RPC_STATUSES:
                    raise TaskIntentInvariantError("TEMPORAL_RPC_PROTOCOL_ERROR") from error
                last_error = error
                if attempt + 1 < self._rpc_attempts:
                    await self._sleep(
                        self._retry_delay.total_seconds() * (attempt + 1)
                    )

        raise TaskIntentTransientError("TEMPORAL_RPC_UNAVAILABLE") from last_error

    async def _start_or_get(
        self,
        intent: ClaimedTaskWorkflowIntent,
        request: TaskRunWorkflowInput,
        memo_identity: Mapping[str, str],
    ) -> WorkflowHandle[Any, Any]:
        try:
            return await self._client.start_workflow(
                TASK_RUN_WORKFLOW_TYPE,
                request,
                id=intent.workflow_id,
                task_queue=TASK_RUN_TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                memo={TASK_INTENT_MEMO_KEY: dict(memo_identity)},
                request_id=str(intent.id),
                rpc_timeout=self._rpc_timeout,
            )
        except WorkflowAlreadyStartedError:
            return self._client.get_workflow_handle(intent.workflow_id)
        except RPCError as error:
            if error.status is RPCStatusCode.ALREADY_EXISTS:
                return self._client.get_workflow_handle(intent.workflow_id)
            raise


def _validate_and_build_request(
    intent: ClaimedTaskWorkflowIntent,
    *,
    client_namespace: str,
) -> TaskRunWorkflowInput:
    expected_workflow_id = task_run_workflow_id(
        tenant_id=intent.tenant_id,
        task_run_id=intent.task_run_id,
    )
    if (
        intent.owner_kind != "TASK_RUN"
        or intent.owner_id != intent.task_run_id
        or intent.workflow_type != TASK_RUN_WORKFLOW_TYPE
        or intent.task_queue != TASK_RUN_TASK_QUEUE
        or intent.workflow_id != expected_workflow_id
        or intent.namespace != client_namespace
        or intent.status != "CLAIMED"
        or fullmatch(_SHA256_DIGEST_PATTERN, intent.request_digest) is None
        or fullmatch(_SHA256_DIGEST_PATTERN, intent.manifest_hash) is None
    ):
        raise TaskIntentInvariantError("INTENT_CONTRACT_MISMATCH")
    return TaskRunWorkflowInput(
        tenant_id=str(intent.tenant_id),
        project_id=str(intent.project_id),
        task_run_id=str(intent.task_run_id),
        request_digest=intent.request_digest,
        manifest_hash=intent.manifest_hash,
    )


def _memo_identity(request: TaskRunWorkflowInput) -> dict[str, str]:
    return {
        "schemaVersion": TASK_INTENT_MEMO_SCHEMA,
        "tenantId": request.tenant_id,
        "projectId": request.project_id,
        "taskRunId": request.task_run_id,
        "requestDigest": request.request_digest,
        "manifestHash": request.manifest_hash,
    }


async def _verify_description(
    intent: ClaimedTaskWorkflowIntent,
    description: WorkflowExecutionDescription,
    expected_memo: Mapping[str, str],
) -> None:
    if (
        description.id != intent.workflow_id
        or description.namespace != intent.namespace
        or description.workflow_type != TASK_RUN_WORKFLOW_TYPE
        or description.task_queue != TASK_RUN_TASK_QUEUE
    ):
        raise TaskIntentInvariantError("TEMPORAL_WORKFLOW_IDENTITY_MISMATCH")
    try:
        memo = await description.memo()
    except Exception as error:
        raise TaskIntentInvariantError("TEMPORAL_WORKFLOW_MEMO_INVALID") from error
    if memo.get(TASK_INTENT_MEMO_KEY) != dict(expected_memo):
        raise TaskIntentInvariantError("TEMPORAL_WORKFLOW_MEMO_MISMATCH")
