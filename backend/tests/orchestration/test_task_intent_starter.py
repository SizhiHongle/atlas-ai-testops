"""Temporal collision and identity tests for Task Workflow start intents."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from temporalio.client import Client, WorkflowExecutionDescription
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from atlas_testops.application.task_intents import (
    TaskIntentInvariantError,
    TaskIntentTransientError,
)
from atlas_testops.infrastructure.task_intents import ClaimedTaskWorkflowIntent
from atlas_testops.orchestration.task_intents import (
    TASK_INTENT_MEMO_KEY,
    TASK_INTENT_MEMO_SCHEMA,
    TASK_RUN_TASK_QUEUE,
    TASK_RUN_WORKFLOW_INPUT_SCHEMA,
    TASK_RUN_WORKFLOW_TYPE,
    TaskRunWorkflowInput,
    TemporalTaskIntentStarter,
)

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _intent(**updates: Any) -> ClaimedTaskWorkflowIntent:
    tenant_id = UUID(int=2)
    task_run_id = UUID(int=4)
    values: dict[str, Any] = {
        "id": UUID(int=1),
        "tenant_id": tenant_id,
        "project_id": UUID(int=3),
        "task_run_id": task_run_id,
        "owner_kind": "TASK_RUN",
        "owner_id": task_run_id,
        "namespace": "atlas-task",
        "workflow_id": f"atlas-task/run/{tenant_id.hex}/{task_run_id.hex}",
        "request_digest": DIGEST_A,
        "manifest_hash": DIGEST_B,
        "workflow_type": TASK_RUN_WORKFLOW_TYPE,
        "task_queue": TASK_RUN_TASK_QUEUE,
        "status": "CLAIMED",
        "claim_token": UUID(int=5),
        "dispatch_revision": 2,
        "dispatch_attempts": 1,
        "claim_expires_at": NOW + timedelta(minutes=1),
        "created_at": NOW,
    }
    values.update(updates)
    return ClaimedTaskWorkflowIntent(**values)


class _Description:
    def __init__(self, intent: ClaimedTaskWorkflowIntent, memo: dict[str, Any]) -> None:
        self.id = intent.workflow_id
        self.namespace = intent.namespace
        self.workflow_type = intent.workflow_type
        self.task_queue = intent.task_queue
        self._memo = memo

    async def memo(self) -> dict[str, Any]:
        return self._memo


class _Handle:
    def __init__(self, description: _Description, error: RPCError | None = None) -> None:
        self.description = description
        self.error = error
        self.describe_calls = 0

    async def describe(self, **options: Any) -> WorkflowExecutionDescription:
        assert options["rpc_timeout"] == timedelta(seconds=2)
        self.describe_calls += 1
        if self.error is not None:
            raise self.error
        return cast(WorkflowExecutionDescription, self.description)


class _Client:
    def __init__(
        self,
        intent: ClaimedTaskWorkflowIntent,
        *,
        memo: dict[str, Any],
        errors: list[Exception] | None = None,
    ) -> None:
        self.namespace = "atlas-task"
        self.handle = _Handle(_Description(intent, memo))
        self.errors = errors or []
        self.calls: list[tuple[object, TaskRunWorkflowInput, dict[str, Any]]] = []
        self.get_calls = 0

    async def start_workflow(
        self,
        workflow: object,
        request: TaskRunWorkflowInput,
        **options: Any,
    ) -> _Handle:
        self.calls.append((workflow, request, options))
        if self.errors:
            raise self.errors.pop(0)
        return self.handle

    def get_workflow_handle(self, workflow_id: str) -> _Handle:
        assert workflow_id == self.handle.description.id
        self.get_calls += 1
        return self.handle


def _memo(intent: ClaimedTaskWorkflowIntent) -> dict[str, Any]:
    return {
        TASK_INTENT_MEMO_KEY: {
            "schemaVersion": TASK_INTENT_MEMO_SCHEMA,
            "tenantId": str(intent.tenant_id),
            "projectId": str(intent.project_id),
            "taskRunId": str(intent.task_run_id),
            "requestDigest": intent.request_digest,
            "manifestHash": intent.manifest_hash,
        }
    }


@pytest.mark.anyio
async def test_starter_uses_exact_allowlist_secret_free_input_and_collision_policy() -> None:
    intent = _intent()
    client = _Client(intent, memo=_memo(intent))
    starter = TemporalTaskIntentStarter(
        cast(Client, client),
        rpc_timeout=timedelta(seconds=2),
    )

    await starter.start(intent)

    workflow, request, options = client.calls[0]
    assert workflow == TASK_RUN_WORKFLOW_TYPE
    assert request == TaskRunWorkflowInput(
        tenant_id=str(intent.tenant_id),
        project_id=str(intent.project_id),
        task_run_id=str(intent.task_run_id),
        request_digest=DIGEST_A,
        manifest_hash=DIGEST_B,
        schema_version=TASK_RUN_WORKFLOW_INPUT_SCHEMA,
    )
    assert set(request.__dataclass_fields__) == {
        "tenant_id",
        "project_id",
        "task_run_id",
        "request_digest",
        "manifest_hash",
        "schema_version",
    }
    assert options["id"] == intent.workflow_id
    assert options["task_queue"] == TASK_RUN_TASK_QUEUE
    assert options["id_reuse_policy"] is WorkflowIDReusePolicy.REJECT_DUPLICATE
    assert options["id_conflict_policy"] is WorkflowIDConflictPolicy.USE_EXISTING
    assert options["memo"] == _memo(intent)
    assert options["request_id"] == str(intent.id)
    assert client.handle.describe_calls == 1


@pytest.mark.anyio
async def test_starter_describes_already_started_workflow_before_accepting() -> None:
    intent = _intent()
    already_started = WorkflowAlreadyStartedError(
        intent.workflow_id,
        TASK_RUN_WORKFLOW_TYPE,
    )
    client = _Client(intent, memo=_memo(intent), errors=[already_started])
    starter = TemporalTaskIntentStarter(
        cast(Client, client),
        rpc_timeout=timedelta(seconds=2),
    )

    await starter.start(intent)

    assert client.get_calls == 1
    assert client.handle.describe_calls == 1


@pytest.mark.anyio
async def test_starter_fails_closed_on_existing_identity_or_memo_mismatch() -> None:
    intent = _intent()
    wrong_type_client = _Client(intent, memo=_memo(intent))
    wrong_type_client.handle.description.workflow_type = "UnexpectedWorkflow"
    starter = TemporalTaskIntentStarter(
        cast(Client, wrong_type_client),
        rpc_timeout=timedelta(seconds=2),
    )
    with pytest.raises(TaskIntentInvariantError, match="IDENTITY_MISMATCH"):
        await starter.start(intent)

    wrong_memo_client = _Client(intent, memo={TASK_INTENT_MEMO_KEY: {}})
    starter = TemporalTaskIntentStarter(
        cast(Client, wrong_memo_client),
        rpc_timeout=timedelta(seconds=2),
    )
    with pytest.raises(TaskIntentInvariantError, match="MEMO_MISMATCH"):
        await starter.start(intent)


@pytest.mark.anyio
async def test_starter_retries_ambiguous_rpc_then_verifies_existing_workflow() -> None:
    intent = _intent()
    rpc_error = RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
    client = _Client(intent, memo=_memo(intent), errors=[rpc_error])
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    starter = TemporalTaskIntentStarter(
        cast(Client, client),
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=2),
        retry_delay=timedelta(milliseconds=100),
        sleep=record_sleep,
    )
    await starter.start(intent)
    assert len(client.calls) == 2
    assert delays == [0.1]

    unavailable = _Client(intent, memo=_memo(intent), errors=[rpc_error, rpc_error])
    starter = TemporalTaskIntentStarter(
        cast(Client, unavailable),
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=2),
        retry_delay=timedelta(0),
    )
    with pytest.raises(TaskIntentTransientError, match="TEMPORAL_RPC_UNAVAILABLE"):
        await starter.start(intent)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (RPCStatusCode.INVALID_ARGUMENT, "TEMPORAL_RPC_INVALID_ARGUMENT"),
        (RPCStatusCode.PERMISSION_DENIED, "TEMPORAL_RPC_PERMISSION_DENIED"),
        (RPCStatusCode.UNAUTHENTICATED, "TEMPORAL_RPC_UNAUTHENTICATED"),
        (RPCStatusCode.NOT_FOUND, "TEMPORAL_RPC_NOT_FOUND"),
        (RPCStatusCode.FAILED_PRECONDITION, "TEMPORAL_RPC_FAILED_PRECONDITION"),
        (RPCStatusCode.OUT_OF_RANGE, "TEMPORAL_RPC_OUT_OF_RANGE"),
        (RPCStatusCode.UNIMPLEMENTED, "TEMPORAL_RPC_UNIMPLEMENTED"),
        (RPCStatusCode.DATA_LOSS, "TEMPORAL_RPC_DATA_LOSS"),
    ],
)
async def test_starter_fails_permanent_rpc_without_retrying(
    status: RPCStatusCode,
    error_code: str,
) -> None:
    intent = _intent()
    client = _Client(
        intent,
        memo=_memo(intent),
        errors=[RPCError("sensitive remote detail", status, b"")],
    )
    starter = TemporalTaskIntentStarter(
        cast(Client, client),
        rpc_attempts=3,
        rpc_timeout=timedelta(seconds=2),
    )

    with pytest.raises(TaskIntentInvariantError, match=error_code) as failure:
        await starter.start(intent)

    assert failure.value.error_code == error_code
    assert "sensitive" not in str(failure.value)
    assert len(client.calls) == 1


@pytest.mark.anyio
async def test_starter_describes_raw_already_exists_collision() -> None:
    intent = _intent()
    client = _Client(
        intent,
        memo=_memo(intent),
        errors=[RPCError("already exists", RPCStatusCode.ALREADY_EXISTS, b"")],
    )
    starter = TemporalTaskIntentStarter(
        cast(Client, client),
        rpc_timeout=timedelta(seconds=2),
    )

    await starter.start(intent)

    assert client.get_calls == 1
    assert client.handle.describe_calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("update", "value"),
    [
        ("owner_kind", "UNIT_ATTEMPT"),
        ("workflow_type", "AtlasUnitAttemptWorkflow"),
        ("task_queue", "other-queue"),
        ("namespace", "other-namespace"),
        ("request_digest", "not-a-digest"),
    ],
)
async def test_starter_rejects_non_allowlisted_intent_before_temporal(
    update: str,
    value: str,
) -> None:
    intent = _intent(**{update: value})
    client = _Client(intent, memo=_memo(intent))
    starter = TemporalTaskIntentStarter(
        cast(Client, client),
        rpc_timeout=timedelta(seconds=2),
    )

    with pytest.raises(TaskIntentInvariantError, match="INTENT_CONTRACT_MISMATCH"):
        await starter.start(intent)

    assert client.calls == []


def test_starter_validates_rpc_configuration() -> None:
    intent = _intent()
    client = cast(Client, _Client(intent, memo=_memo(intent)))
    with pytest.raises(ValueError, match="attempts"):
        TemporalTaskIntentStarter(client, rpc_attempts=0)
    with pytest.raises(ValueError, match="timing"):
        TemporalTaskIntentStarter(client, rpc_timeout=timedelta(0))
