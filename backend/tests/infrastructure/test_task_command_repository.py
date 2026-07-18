"""Repository tests for tenant commands and dispatcher-only CAS functions."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.task import (
    TaskRunCommandIntent,
    TaskRunCommandStatus,
    TaskRunCommandType,
    task_run_command_digest,
    task_run_workflow_id,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableFactConflictError,
)
from atlas_testops.infrastructure.task_commands import (
    ClaimedTaskRunCommandIntent,
    TaskRunCommandRepository,
)

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _command(*, revision: int = 2) -> TaskRunCommandIntent:
    tenant_id = UUID(int=2)
    project_id = UUID(int=3)
    run_id = UUID(int=4)
    workflow_id = task_run_workflow_id(tenant_id=tenant_id, task_run_id=run_id)
    mutation_id = "cancel-command-001"
    return TaskRunCommandIntent(
        id=UUID(int=1),
        tenant_id=tenant_id,
        project_id=project_id,
        task_run_id=run_id,
        command_type=TaskRunCommandType.CANCEL,
        client_mutation_id=mutation_id,
        command_digest=task_run_command_digest(
            tenant_id=tenant_id,
            project_id=project_id,
            task_run_id=run_id,
            command_type=TaskRunCommandType.CANCEL,
            client_mutation_id=mutation_id,
            expected_run_revision=revision,
            request_digest=DIGEST_A,
            manifest_hash=DIGEST_B,
            temporal_namespace="atlas-task",
            temporal_workflow_id=workflow_id,
        ),
        expected_run_revision=revision,
        accepted_run_revision=revision + 1,
        request_digest=DIGEST_A,
        manifest_hash=DIGEST_B,
        temporal_namespace="atlas-task",
        temporal_workflow_id=workflow_id,
        status=TaskRunCommandStatus.PENDING,
        dispatch_attempts=0,
        created_by=UUID(int=6),
        created_at=NOW,
        updated_at=NOW,
    )


def _public_row(command: TaskRunCommandIntent) -> DictRow:
    return command.model_dump(mode="python", by_alias=False)


def _claim_row(command: TaskRunCommandIntent) -> DictRow:
    values = {
        "id": command.id,
        "tenant_id": command.tenant_id,
        "project_id": command.project_id,
        "task_run_id": command.task_run_id,
        "schema_version": command.schema_version,
        "command_type": command.command_type.value,
        "client_mutation_id": command.client_mutation_id,
        "command_digest": command.command_digest,
        "expected_run_revision": command.expected_run_revision,
        "accepted_run_revision": command.accepted_run_revision,
        "request_digest": command.request_digest,
        "manifest_hash": command.manifest_hash,
        "namespace": command.temporal_namespace,
        "workflow_id": command.temporal_workflow_id,
        "status": "CLAIMED",
        "claim_token": UUID(int=7),
        "dispatch_revision": 1,
        "dispatch_attempts": 1,
        "claim_expires_at": NOW + timedelta(seconds=30),
        "created_at": NOW,
    }
    return cast(DictRow, values)


class _Cursor:
    def __init__(
        self,
        *,
        row: DictRow | None = None,
        rows: list[DictRow] | None = None,
        applied: bool = True,
        return_none: bool = False,
    ) -> None:
        self._row = row
        self._rows = rows or []
        self._applied = applied
        self._return_none = return_none

    async def fetchone(self) -> DictRow | None:
        if self._return_none:
            return None
        if self._row is not None:
            return self._row
        return cast(DictRow, {"applied": self._applied, "affected": 1})

    async def fetchall(self) -> list[DictRow]:
        return self._rows


class _Connection:
    def __init__(
        self,
        command: TaskRunCommandIntent,
        *,
        insert_conflict: bool = False,
        existing: TaskRunCommandIntent | None = None,
        applied: bool = True,
    ) -> None:
        self.command = command
        self.insert_conflict = insert_conflict
        self.existing = existing or command
        self.applied = applied
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, statement: str, params: tuple[object, ...]) -> _Cursor:
        self.calls.append((statement, params))
        if "insert into atlas.task_run_command_intent" in statement:
            return _Cursor(
                row=None if self.insert_conflict else _public_row(self.command),
                applied=False if self.insert_conflict else self.applied,
                return_none=self.insert_conflict,
            )
        if "claim_task_run_command_intents" in statement:
            return _Cursor(rows=[_claim_row(self.command)])
        if "from atlas.task_run_command_intent" in statement:
            return _Cursor(row=_public_row(self.existing))
        return _Cursor(applied=self.applied)


@pytest.mark.anyio
async def test_repository_creates_reads_and_replays_exact_command() -> None:
    command = _command()
    repository = TaskRunCommandRepository()
    raw_connection = _Connection(command)
    connection = cast(AsyncConnection[DictRow], raw_connection)

    created = await repository.create(connection, command)
    assert created.kind is ImmutableCreateKind.CREATED
    assert created.fact == command
    assert await repository.get(connection, command.id) == command
    assert await repository.get_by_mutation(
        connection,
        task_run_id=command.task_run_id,
        client_mutation_id=command.client_mutation_id,
    ) == command

    replay_connection = cast(
        AsyncConnection[DictRow],
        _Connection(command, insert_conflict=True),
    )
    replay = await repository.create(replay_connection, command)
    assert replay.kind is ImmutableCreateKind.EXISTING
    assert replay.fact == command


@pytest.mark.anyio
async def test_repository_rejects_conflicting_mutation_identity() -> None:
    command = _command()
    existing = _command(revision=3)
    connection = cast(
        AsyncConnection[DictRow],
        _Connection(command, insert_conflict=True, existing=existing),
    )

    with pytest.raises(ImmutableFactConflictError, match="different immutable content"):
        await TaskRunCommandRepository().create(connection, command)


@pytest.mark.anyio
async def test_repository_calls_only_claim_delivery_and_apply_functions() -> None:
    command = _command()
    repository = TaskRunCommandRepository()
    raw_connection = _Connection(command)
    connection = cast(AsyncConnection[DictRow], raw_connection)

    claimed = await repository.claim(
        connection,
        claimed_by="dispatcher-1",
        namespace="atlas-task",
        limit=20,
        lease_duration=timedelta(seconds=30),
    )
    intent = claimed[0]
    assert isinstance(intent, ClaimedTaskRunCommandIntent)
    assert raw_connection.calls[0][1] == ("dispatcher-1", "atlas-task", 20, 30)

    assert await repository.mark_delivered(
        connection,
        intent_id=intent.id,
        claim_token=intent.claim_token,
        dispatch_revision=intent.dispatch_revision,
    )
    assert await repository.retry(
        connection,
        intent_id=intent.id,
        claim_token=intent.claim_token,
        dispatch_revision=intent.dispatch_revision,
        error_code="TEMPORAL_RPC_UNAVAILABLE",
        retry_delay=timedelta(milliseconds=250),
    )
    assert raw_connection.calls[2][1][-1] == 250
    assert await repository.fail(
        connection,
        intent_id=intent.id,
        claim_token=intent.claim_token,
        dispatch_revision=intent.dispatch_revision,
        error_code="COMMAND_CONTRACT_MISMATCH",
    )
    assert await repository.apply_cancel(
        connection,
        intent_id=intent.id,
        command_digest=intent.command_digest,
    )
    assert await repository.apply_pause(
        connection,
        intent_id=intent.id,
        command_digest=intent.command_digest,
    )
    assert await repository.apply_resume(
        connection,
        intent_id=intent.id,
        command_digest=intent.command_digest,
    )
    assert (
        await repository.supersede_for_cancel(
            connection,
            task_run_id=intent.task_run_id,
            cancel_command_id=intent.id,
        )
        == 1
    )

    expected_functions = (
        "claim_task_run_command_intents",
        "mark_task_run_command_intent_delivered",
        "retry_task_run_command_intent",
        "fail_task_run_command_intent",
        "apply_task_run_cancel_command",
        "apply_task_run_pause_command",
        "apply_task_run_resume_command",
        "supersede_task_run_commands",
    )
    assert all(
        function in call[0]
        for function, call in zip(expected_functions, raw_connection.calls, strict=True)
    )


@pytest.mark.anyio
async def test_repository_reports_lost_fence_and_rejects_invalid_durations() -> None:
    command = _command()
    repository = TaskRunCommandRepository()
    connection = cast(AsyncConnection[DictRow], _Connection(command, applied=False))
    assert not await repository.apply_cancel(
        connection,
        intent_id=command.id,
        command_digest=command.command_digest,
    )

    for duration in (timedelta(0), timedelta(milliseconds=1500)):
        with pytest.raises(ValueError, match="whole number"):
            await repository.claim(
                connection,
                claimed_by="dispatcher-1",
                namespace="atlas-task",
                limit=1,
                lease_duration=duration,
            )
    for duration in (timedelta(milliseconds=99), timedelta(hours=2)):
        with pytest.raises(ValueError, match="retry delay"):
            await repository.retry(
                connection,
                intent_id=command.id,
                claim_token=UUID(int=7),
                dispatch_revision=1,
                error_code="TEMPORAL_RPC_UNAVAILABLE",
                retry_delay=duration,
            )
