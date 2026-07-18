"""Temporal identity and retry tests for TaskRun command Signals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from temporalio.client import Client, WorkflowExecutionDescription
from temporalio.service import RPCError, RPCStatusCode

from atlas_testops.application.task_commands import (
    TaskCommandInvariantError,
    TaskCommandTransientError,
)
from atlas_testops.domain.task import (
    TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION,
    TaskRunCommandType,
    task_run_command_digest,
)
from atlas_testops.infrastructure.task_commands import ClaimedTaskRunCommandIntent
from atlas_testops.orchestration.task_commands import TemporalTaskCommandSignaler
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

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _intent(**updates: Any) -> ClaimedTaskRunCommandIntent:
    tenant_id = UUID(int=2)
    project_id = UUID(int=3)
    run_id = UUID(int=4)
    workflow_id = f"atlas-task/run/{tenant_id.hex}/{run_id.hex}"
    values: dict[str, Any] = {
        "id": UUID(int=1),
        "tenant_id": tenant_id,
        "project_id": project_id,
        "task_run_id": run_id,
        "schema_version": "atlas.task-run-command/0.2",
        "command_type": "CANCEL",
        "client_mutation_id": "cancel-command-001",
        "expected_run_revision": 2,
        "accepted_run_revision": 3,
        "request_digest": DIGEST_A,
        "manifest_hash": DIGEST_B,
        "namespace": "atlas-task",
        "workflow_id": workflow_id,
        "status": "CLAIMED",
        "claim_token": UUID(int=5),
        "dispatch_revision": 2,
        "dispatch_attempts": 1,
        "claim_expires_at": NOW + timedelta(minutes=1),
        "created_at": NOW,
    }
    values.update(updates)
    digest_command_type = (
        TaskRunCommandType(values["command_type"])
        if values["command_type"] in {item.value for item in TaskRunCommandType}
        else TaskRunCommandType.CANCEL
    )
    values.setdefault(
        "command_digest",
        task_run_command_digest(
            tenant_id=values["tenant_id"],
            project_id=values["project_id"],
            task_run_id=values["task_run_id"],
            command_type=digest_command_type,
            client_mutation_id=values["client_mutation_id"],
            expected_run_revision=values["expected_run_revision"],
            request_digest=values["request_digest"],
            manifest_hash=values["manifest_hash"],
            temporal_namespace=values["namespace"],
            temporal_workflow_id=values["workflow_id"],
            schema_version=cast(Any, values["schema_version"]),
        ),
    )
    return ClaimedTaskRunCommandIntent(**values)


def _memo(intent: ClaimedTaskRunCommandIntent) -> dict[str, Any]:
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


class _Description:
    def __init__(self, intent: ClaimedTaskRunCommandIntent, memo: dict[str, Any]) -> None:
        self.id = intent.workflow_id
        self.namespace = intent.namespace
        self.workflow_type = TASK_RUN_WORKFLOW_TYPE
        self.task_queue = TASK_RUN_TASK_QUEUE
        self._memo = memo
        self.memo_error: Exception | None = None

    async def memo(self) -> dict[str, Any]:
        if self.memo_error is not None:
            raise self.memo_error
        return self._memo


class _Handle:
    def __init__(self, description: _Description) -> None:
        self.description = description
        self.describe_errors: list[RPCError] = []
        self.signal_errors: list[RPCError] = []
        self.signals: list[tuple[str, TaskRunCommandSignal, dict[str, object]]] = []
        self.describe_calls = 0

    async def describe(self, **options: Any) -> WorkflowExecutionDescription:
        assert cast(timedelta, options["rpc_timeout"]) > timedelta(0)
        self.describe_calls += 1
        if self.describe_errors:
            raise self.describe_errors.pop(0)
        return cast(WorkflowExecutionDescription, self.description)

    async def signal(
        self,
        name: str,
        value: TaskRunCommandSignal,
        **options: object,
    ) -> None:
        self.signals.append((name, value, options))
        if self.signal_errors:
            raise self.signal_errors.pop(0)


class _Client:
    def __init__(self, intent: ClaimedTaskRunCommandIntent) -> None:
        self.namespace = "atlas-task"
        self.handle = _Handle(_Description(intent, _memo(intent)))
        self.workflow_ids: list[str] = []

    def get_workflow_handle(self, workflow_id: str) -> _Handle:
        self.workflow_ids.append(workflow_id)
        return self.handle


@pytest.mark.anyio
async def test_signaler_verifies_identity_and_sends_exact_secret_free_signal() -> None:
    intent = _intent()
    client = _Client(intent)

    await TemporalTaskCommandSignaler(
        cast(Client, client),
        rpc_timeout=timedelta(seconds=2),
    ).signal(intent)

    assert client.workflow_ids == [intent.workflow_id]
    name, signal, options = client.handle.signals[0]
    assert name == TASK_RUN_COMMAND_SIGNAL
    assert signal == TaskRunCommandSignal(
        command_id=str(intent.id),
        tenant_id=str(intent.tenant_id),
        project_id=str(intent.project_id),
        task_run_id=str(intent.task_run_id),
        command_type="CANCEL",
        command_digest=intent.command_digest,
        accepted_run_revision=3,
        schema_version=TASK_RUN_COMMAND_SIGNAL_SCHEMA,
    )
    assert options == {"rpc_timeout": timedelta(seconds=2)}
    assert set(signal.__dataclass_fields__) == {
        "command_id",
        "tenant_id",
        "project_id",
        "task_run_id",
        "command_type",
        "command_digest",
        "accepted_run_revision",
        "schema_version",
    }


@pytest.mark.anyio
@pytest.mark.parametrize("command_type", ["PAUSE", "RESUME"])
async def test_signaler_delivers_v02_pause_and_resume(command_type: str) -> None:
    intent = _intent(
        command_type=command_type,
        client_mutation_id=f"{command_type.casefold()}-command-001",
    )
    client = _Client(intent)

    await TemporalTaskCommandSignaler(cast(Client, client)).signal(intent)

    _, signal, _ = client.handle.signals[0]
    assert signal.command_type == command_type
    assert signal.schema_version == TASK_RUN_COMMAND_SIGNAL_SCHEMA


@pytest.mark.anyio
async def test_signaler_maps_legacy_cancel_and_rejects_legacy_pause() -> None:
    legacy_cancel = _intent(
        schema_version=TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION,
    )
    client = _Client(legacy_cancel)

    await TemporalTaskCommandSignaler(cast(Client, client)).signal(legacy_cancel)

    assert (
        client.handle.signals[0][1].schema_version
        == TASK_RUN_COMMAND_SIGNAL_LEGACY_SCHEMA
    )

    legacy_pause = _intent(
        schema_version=TASK_RUN_COMMAND_LEGACY_SCHEMA_VERSION,
        command_type="PAUSE",
        client_mutation_id="pause-command-legacy",
    )
    with pytest.raises(TaskCommandInvariantError, match="COMMAND_CONTRACT_MISMATCH"):
        await TemporalTaskCommandSignaler(
            cast(Client, _Client(legacy_pause))
        ).signal(legacy_pause)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command_type", "UNSUPPORTED"),
        ("status", "PENDING"),
        ("namespace", "other-namespace"),
        ("workflow_id", "wrong-workflow"),
        ("command_digest", DIGEST_A),
        ("accepted_run_revision", 4),
        ("request_digest", "invalid"),
        ("manifest_hash", "invalid"),
    ],
)
async def test_signaler_rejects_tampered_claim_before_temporal(
    field: str,
    value: object,
) -> None:
    intent = _intent(**{field: value})
    client = _Client(intent)

    with pytest.raises(TaskCommandInvariantError, match="COMMAND_CONTRACT_MISMATCH"):
        await TemporalTaskCommandSignaler(cast(Client, client)).signal(intent)

    assert client.workflow_ids == []


@pytest.mark.anyio
async def test_signaler_rejects_workflow_identity_memo_and_memo_decode_failure() -> None:
    intent = _intent()
    identity_client = _Client(intent)
    identity_client.handle.description.task_queue = "wrong-queue"
    with pytest.raises(TaskCommandInvariantError, match="IDENTITY_MISMATCH"):
        await TemporalTaskCommandSignaler(cast(Client, identity_client)).signal(intent)

    memo_client = _Client(intent)
    memo_client.handle.description._memo = {TASK_INTENT_MEMO_KEY: {}}
    with pytest.raises(TaskCommandInvariantError, match="MEMO_MISMATCH"):
        await TemporalTaskCommandSignaler(cast(Client, memo_client)).signal(intent)

    invalid_memo_client = _Client(intent)
    invalid_memo_client.handle.description.memo_error = RuntimeError("sensitive")
    with pytest.raises(TaskCommandInvariantError, match="MEMO_INVALID") as failure:
        await TemporalTaskCommandSignaler(cast(Client, invalid_memo_client)).signal(
            intent
        )
    assert "sensitive" not in str(failure.value)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [RPCStatusCode.NOT_FOUND, RPCStatusCode.UNAVAILABLE])
async def test_signaler_retries_not_started_or_ambiguous_workflow(status: RPCStatusCode) -> None:
    intent = _intent()
    client = _Client(intent)
    client.handle.describe_errors = [RPCError("sensitive", status, b"")]
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    await TemporalTaskCommandSignaler(
        cast(Client, client),
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=2),
        retry_delay=timedelta(milliseconds=100),
        sleep=record_sleep,
    ).signal(intent)

    assert client.handle.describe_calls == 2
    assert delays == [0.1]


@pytest.mark.anyio
async def test_signaler_exhausts_transient_signal_failure() -> None:
    intent = _intent()
    client = _Client(intent)
    error = RPCError("sensitive", RPCStatusCode.UNAVAILABLE, b"")
    client.handle.signal_errors = [error, error]

    with pytest.raises(TaskCommandTransientError, match="TEMPORAL_RPC_UNAVAILABLE"):
        await TemporalTaskCommandSignaler(
            cast(Client, client),
            rpc_attempts=2,
            rpc_timeout=timedelta(seconds=2),
            retry_delay=timedelta(0),
        ).signal(intent)

    assert len(client.handle.signals) == 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        (RPCStatusCode.INVALID_ARGUMENT, "TEMPORAL_RPC_INVALID_ARGUMENT"),
        (RPCStatusCode.PERMISSION_DENIED, "TEMPORAL_RPC_PERMISSION_DENIED"),
        (RPCStatusCode.UNAUTHENTICATED, "TEMPORAL_RPC_UNAUTHENTICATED"),
        (RPCStatusCode.FAILED_PRECONDITION, "TEMPORAL_WORKFLOW_NOT_RUNNING"),
        (RPCStatusCode.OUT_OF_RANGE, "TEMPORAL_RPC_OUT_OF_RANGE"),
        (RPCStatusCode.UNIMPLEMENTED, "TEMPORAL_RPC_UNIMPLEMENTED"),
        (RPCStatusCode.DATA_LOSS, "TEMPORAL_RPC_DATA_LOSS"),
    ],
)
async def test_signaler_fails_permanent_rpc_without_retry(
    status: RPCStatusCode,
    error_code: str,
) -> None:
    intent = _intent()
    client = _Client(intent)
    client.handle.describe_errors = [RPCError("sensitive", status, b"")]

    with pytest.raises(TaskCommandInvariantError, match=error_code) as failure:
        await TemporalTaskCommandSignaler(
            cast(Client, client),
            rpc_attempts=3,
        ).signal(intent)

    assert failure.value.error_code == error_code
    assert client.handle.describe_calls == 1


@pytest.mark.anyio
async def test_signaler_rejects_unclassified_rpc_status() -> None:
    intent = _intent()
    client = _Client(intent)
    client.handle.describe_errors = [
        RPCError("sensitive", RPCStatusCode.ALREADY_EXISTS, b"")
    ]
    with pytest.raises(TaskCommandInvariantError, match="PROTOCOL_ERROR"):
        await TemporalTaskCommandSignaler(cast(Client, client)).signal(intent)


def test_signaler_validates_rpc_configuration() -> None:
    client = cast(Client, _Client(_intent()))
    with pytest.raises(ValueError, match="attempts"):
        TemporalTaskCommandSignaler(client, rpc_attempts=0)
    with pytest.raises(ValueError, match="timing"):
        TemporalTaskCommandSignaler(client, rpc_timeout=timedelta(0))
