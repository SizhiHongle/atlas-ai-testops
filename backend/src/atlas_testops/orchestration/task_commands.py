"""Temporal Signal delivery for durable TaskRun control commands."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from re import fullmatch
from typing import Any, Protocol, cast

from temporalio.client import Client, WorkflowExecutionDescription, WorkflowHandle
from temporalio.service import RPCError, RPCStatusCode

from atlas_testops.application.task_commands import (
    TaskCommandInvariantError,
    TaskCommandTransientError,
)
from atlas_testops.domain.task import (
    TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION,
    TASK_RUN_COMMAND_SCHEMA_VERSION,
    TaskRunCommandType,
    task_run_command_digest,
    task_run_workflow_id,
)
from atlas_testops.infrastructure.task_commands import ClaimedTaskRunCommandIntent
from atlas_testops.orchestration.task_intents import (
    TASK_INTENT_MEMO_KEY,
    TASK_INTENT_MEMO_SCHEMA,
    TASK_RUN_TASK_QUEUE,
    TASK_RUN_WORKFLOW_TYPE,
)
from atlas_testops.orchestration.tasks import (
    TASK_RUN_COMMAND_SIGNAL,
    TASK_RUN_COMMAND_SIGNAL_LEGACY_SCHEMA,
    TASK_RUN_COMMAND_SIGNAL_SCHEMA,
    TaskRunCommandSignal,
)

_PERMANENT_RPC_ERROR_CODES = {
    RPCStatusCode.INVALID_ARGUMENT: "TEMPORAL_RPC_INVALID_ARGUMENT",
    RPCStatusCode.PERMISSION_DENIED: "TEMPORAL_RPC_PERMISSION_DENIED",
    RPCStatusCode.UNAUTHENTICATED: "TEMPORAL_RPC_UNAUTHENTICATED",
    RPCStatusCode.FAILED_PRECONDITION: "TEMPORAL_WORKFLOW_NOT_RUNNING",
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
    RPCStatusCode.NOT_FOUND,
    RPCStatusCode.UNAVAILABLE,
}


class _TaskCommandTemporalClient(Protocol):
    @property
    def namespace(self) -> str: ...

    def get_workflow_handle(self, workflow_id: str) -> WorkflowHandle[Any, Any]: ...


class TemporalTaskCommandSignaler:
    """Verify one exact root Workflow before sending a deduplicated command Signal."""

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
            raise ValueError("Task command RPC attempts must be between 1 and 5")
        if (
            not timedelta(0) < rpc_timeout <= timedelta(minutes=2)
            or not timedelta(0) <= retry_delay <= timedelta(seconds=5)
        ):
            raise ValueError("Task command RPC timing is invalid")
        self._client = cast(_TaskCommandTemporalClient, client)
        self._rpc_attempts = rpc_attempts
        self._rpc_timeout = rpc_timeout
        self._retry_delay = retry_delay
        self._sleep = sleep

    async def signal(self, intent: ClaimedTaskRunCommandIntent) -> None:
        """Send exact command identity; duplicate Signals remain Workflow-idempotent."""

        signal = _validate_and_build_signal(
            intent,
            client_namespace=self._client.namespace,
        )
        expected_memo = _expected_memo(intent)
        last_error: RPCError | None = None
        for attempt in range(self._rpc_attempts):
            handle = self._client.get_workflow_handle(intent.workflow_id)
            try:
                description = await handle.describe(rpc_timeout=self._rpc_timeout)
                await _verify_description(intent, description, expected_memo)
                await handle.signal(
                    TASK_RUN_COMMAND_SIGNAL,
                    signal,
                    rpc_timeout=self._rpc_timeout,
                )
                return
            except TaskCommandInvariantError:
                raise
            except RPCError as error:
                permanent_code = _PERMANENT_RPC_ERROR_CODES.get(error.status)
                if permanent_code is not None:
                    raise TaskCommandInvariantError(permanent_code) from error
                if error.status not in _TRANSIENT_RPC_STATUSES:
                    raise TaskCommandInvariantError("TEMPORAL_RPC_PROTOCOL_ERROR") from error
                last_error = error
                if attempt + 1 < self._rpc_attempts:
                    await self._sleep(
                        self._retry_delay.total_seconds() * (attempt + 1)
                    )
        raise TaskCommandTransientError("TEMPORAL_RPC_UNAVAILABLE") from last_error


def _validate_and_build_signal(
    intent: ClaimedTaskRunCommandIntent,
    *,
    client_namespace: str,
) -> TaskRunCommandSignal:
    expected_workflow_id = task_run_workflow_id(
        tenant_id=intent.tenant_id,
        task_run_id=intent.task_run_id,
    )
    try:
        command_type = TaskRunCommandType(intent.command_type)
    except ValueError:
        raise TaskCommandInvariantError("COMMAND_CONTRACT_MISMATCH") from None
    if intent.schema_version not in {
        TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION,
        TASK_RUN_COMMAND_SCHEMA_VERSION,
    }:
        raise TaskCommandInvariantError("COMMAND_CONTRACT_MISMATCH")
    expected_digest = task_run_command_digest(
        tenant_id=intent.tenant_id,
        project_id=intent.project_id,
        task_run_id=intent.task_run_id,
        command_type=command_type,
        client_mutation_id=intent.client_mutation_id,
        expected_run_revision=intent.expected_run_revision,
        request_digest=intent.request_digest,
        manifest_hash=intent.manifest_hash,
        temporal_namespace=intent.namespace,
        temporal_workflow_id=intent.workflow_id,
        schema_version=cast(Any, intent.schema_version),
    )
    if (
        intent.status != "CLAIMED"
        or intent.namespace != client_namespace
        or intent.workflow_id != expected_workflow_id
        or intent.command_digest != expected_digest
        or intent.accepted_run_revision != intent.expected_run_revision + 1
        or fullmatch(r"sha256:[0-9a-f]{64}", intent.request_digest) is None
        or fullmatch(r"sha256:[0-9a-f]{64}", intent.manifest_hash) is None
        or (
            intent.schema_version == TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION
            and command_type is not TaskRunCommandType.CANCEL
        )
    ):
        raise TaskCommandInvariantError("COMMAND_CONTRACT_MISMATCH")
    return TaskRunCommandSignal(
        command_id=str(intent.id),
        tenant_id=str(intent.tenant_id),
        project_id=str(intent.project_id),
        task_run_id=str(intent.task_run_id),
        command_type=cast(Any, command_type.value),
        command_digest=intent.command_digest,
        accepted_run_revision=intent.accepted_run_revision,
        schema_version=(
            TASK_RUN_COMMAND_SIGNAL_LEGACY_SCHEMA
            if intent.schema_version == TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION
            else TASK_RUN_COMMAND_SIGNAL_SCHEMA
        ),
    )


def _expected_memo(intent: ClaimedTaskRunCommandIntent) -> dict[str, str]:
    return {
        "schemaVersion": TASK_INTENT_MEMO_SCHEMA,
        "tenantId": str(intent.tenant_id),
        "projectId": str(intent.project_id),
        "taskRunId": str(intent.task_run_id),
        "requestDigest": intent.request_digest,
        "manifestHash": intent.manifest_hash,
    }


async def _verify_description(
    intent: ClaimedTaskRunCommandIntent,
    description: WorkflowExecutionDescription,
    expected_memo: dict[str, str],
) -> None:
    if (
        description.id != intent.workflow_id
        or description.namespace != intent.namespace
        or description.workflow_type != TASK_RUN_WORKFLOW_TYPE
        or description.task_queue != TASK_RUN_TASK_QUEUE
    ):
        raise TaskCommandInvariantError("TEMPORAL_WORKFLOW_IDENTITY_MISMATCH")
    try:
        memo = await description.memo()
    except Exception as error:
        raise TaskCommandInvariantError("TEMPORAL_WORKFLOW_MEMO_INVALID") from error
    if memo.get(TASK_INTENT_MEMO_KEY) != expected_memo:
        raise TaskCommandInvariantError("TEMPORAL_WORKFLOW_MEMO_MISMATCH")
