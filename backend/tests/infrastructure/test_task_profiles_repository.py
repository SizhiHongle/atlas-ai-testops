"""Repository tests for Task profile facts, trusted CAS calls, and start intents."""

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.domain.task.test_profiles import (
    browser_payload,
    data_payload,
    execution_payload,
    identity_payload,
)

from atlas_testops.domain.task import (
    BrowserProfileVersion,
    DataProfileVersion,
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionProfileVersion,
    ExecutionQuality,
    IdentityProfileVersion,
    TaskProfileStatus,
)
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
    TaskProfileRepository,
    TaskWorkflowStartIntent,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableFactConflictError,
)


class StubCursor:
    def __init__(
        self,
        *,
        row: DictRow | None = None,
        rows: tuple[DictRow, ...] = (),
    ) -> None:
        self._row = row
        self._rows = rows

    async def fetchone(self) -> DictRow | None:
        return self._row

    async def fetchall(self) -> list[DictRow]:
        return list(self._rows)


class StubConnection:
    def __init__(self, *cursors: StubCursor) -> None:
        self._cursors = list(cursors)
        self.calls: list[tuple[str, Sequence[object] | None]] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> StubCursor:
        self.calls.append((query, params))
        return self._cursors.pop(0)


def _row(model: object, *, exclude: set[str] | None = None) -> DictRow:
    return cast(
        DictRow,
        model.model_dump(mode="python", exclude=exclude or set()),  # type: ignore[attr-defined]
    )


def _connection(value: StubConnection) -> AsyncConnection[DictRow]:
    return cast(AsyncConnection[DictRow], value)


@pytest.mark.anyio
async def test_create_and_get_execution_profile_preserve_typed_json() -> None:
    profile = ExecutionProfileVersion.model_validate(execution_payload())
    connection = StubConnection(StubCursor(row=_row(profile)))

    result = await TaskProfileRepository().create_execution_profile_version(
        _connection(connection),
        profile,
    )

    assert result.kind is ImmutableCreateKind.CREATED
    assert result.fact == profile
    query, params = connection.calls[0]
    assert "insert into atlas.execution_profile_version" in query
    assert params is not None
    assert cast(Any, params[13]).obj == profile.model.model_dump(
        mode="json", by_alias=True
    )
    assert cast(Any, params[14]).obj == profile.tools.model_dump(
        mode="json", by_alias=True
    )

    read_connection = StubConnection(StubCursor(row=_row(profile)))
    loaded = await TaskProfileRepository().get_execution_profile_version(
        _connection(read_connection),
        profile.id,
    )
    assert loaded == profile


@pytest.mark.anyio
async def test_profile_natural_key_replay_and_content_conflict_are_explicit() -> None:
    profile = BrowserProfileVersion.model_validate(browser_payload())
    repository = TaskProfileRepository()
    replay_connection = StubConnection(
        StubCursor(),
        StubCursor(row=_row(profile)),
    )

    replay = await repository.create_browser_profile_version(
        _connection(replay_connection),
        profile,
    )

    assert replay.kind is ImmutableCreateKind.EXISTING
    assert replay.fact == profile

    conflict_connection = StubConnection(
        StubCursor(),
        StubCursor(row=_row(profile)),
    )
    changed = profile.model_copy(update={"content_digest": "sha256:" + "f" * 64})
    with pytest.raises(ImmutableFactConflictError, match="different immutable content"):
        await repository.create_browser_profile_version(
            _connection(conflict_connection),
            changed,
        )


@pytest.mark.anyio
async def test_profile_creation_requires_published_status_and_resolved_conflict() -> None:
    profile = DataProfileVersion.model_validate(data_payload())
    deprecated = profile.model_copy(update={"status": TaskProfileStatus.DEPRECATED})
    with pytest.raises(ValueError, match="PUBLISHED"):
        await TaskProfileRepository().create_data_profile_version(
            _connection(StubConnection()),
            deprecated,
        )

    unresolved = StubConnection(StubCursor(), StubCursor())
    with pytest.raises(RuntimeError, match="did not resolve"):
        await TaskProfileRepository().create_data_profile_version(
            _connection(unresolved),
            profile,
        )


@pytest.mark.anyio
async def test_identity_profile_writes_and_loads_canonical_actor_rows() -> None:
    profile = IdentityProfileVersion.model_validate(identity_payload())
    parent_row = _row(profile, exclude={"actors"})
    connection = StubConnection(
        StubCursor(row=parent_row),
        *(StubCursor() for _actor in profile.actors),
    )

    result = await TaskProfileRepository().create_identity_profile_version(
        _connection(connection),
        profile,
    )

    assert result.kind is ImmutableCreateKind.CREATED
    assert result.fact == profile
    child_calls = connection.calls[1:]
    assert len(child_calls) == len(profile.actors)
    assert [call[1][3] for call in child_calls if call[1] is not None] == [
        actor.actor_slot for actor in profile.actors
    ]
    assert [call[1][4] for call in child_calls if call[1] is not None] == [1, 2]

    actor_rows = tuple(_row(actor) for actor in profile.actors)
    read_connection = StubConnection(
        StubCursor(row=parent_row),
        StubCursor(rows=tuple(reversed(actor_rows))),
    )
    loaded = await TaskProfileRepository().get_identity_profile_version(
        _connection(read_connection),
        profile.id,
    )
    assert loaded == profile
    assert "order by ordinal, actor_slot" in read_connection.calls[1][0]


@pytest.mark.anyio
async def test_missing_profile_getters_return_none_without_child_queries() -> None:
    repository = TaskProfileRepository()
    profile_id = UUID(int=999)
    for getter in (
        repository.get_execution_profile_version,
        repository.get_identity_profile_version,
        repository.get_browser_profile_version,
        repository.get_data_profile_version,
    ):
        connection = StubConnection(StubCursor())
        assert await getter(_connection(connection), profile_id) is None
        assert len(connection.calls) == 1


@pytest.mark.anyio
async def test_state_repository_invokes_only_trusted_functions() -> None:
    repository = TaskExecutionStateRepository()
    ids = (UUID(int=1), UUID(int=2), UUID(int=3))
    connection = StubConnection(*(StubCursor() for _index in range(4)))

    assert (
        await repository.seal_task_run_materialization(
            _connection(connection),
            task_run_id=ids[0],
            expected_revision=3,
        )
        is None
    )
    assert (
        await repository.transition_task_run_state(
            _connection(connection),
            task_run_id=ids[0],
            expected_revision=4,
            lifecycle=ExecutionLifecycle.RUNNING,
            quality=ExecutionQuality.PENDING,
            hygiene=ExecutionHygiene.PENDING,
            started_at=None,
            finalized_at=None,
            cleanup_resolved_at=None,
            closed_at=None,
        )
        is None
    )
    assert (
        await repository.transition_execution_unit_state(
            _connection(connection),
            task_run_id=ids[0],
            execution_unit_id=ids[1],
            expected_revision=4,
            lifecycle=ExecutionLifecycle.RUNNING,
            quality=ExecutionQuality.PENDING,
            hygiene=ExecutionHygiene.PENDING,
            started_at=None,
            finalized_at=None,
            cleanup_resolved_at=None,
            closed_at=None,
        )
        is None
    )
    assert (
        await repository.transition_unit_attempt_state(
            _connection(connection),
            task_run_id=ids[0],
            execution_unit_id=ids[1],
            unit_attempt_id=ids[2],
            expected_revision=4,
            lifecycle=ExecutionLifecycle.RUNNING,
            quality=ExecutionQuality.PENDING,
            hygiene=ExecutionHygiene.PENDING,
            started_at=None,
            finalized_at=None,
            cleanup_resolved_at=None,
            closed_at=None,
        )
        is None
    )

    sql = "\n".join(query for query, _params in connection.calls)
    assert "atlas.seal_task_run_materialization" in sql
    assert "atlas.transition_task_run_state" in sql
    assert "atlas.transition_execution_unit_state" in sql
    assert "atlas.transition_unit_attempt_state" in sql
    assert "select * from atlas." not in sql.casefold()
    assert "update atlas.task_run" not in sql.casefold()

    sequence_connection = StubConnection(
        StubCursor(row=cast(DictRow, {"next_seq": 7}))
    )
    assert (
        await repository.next_task_execution_event_seq(
            _connection(sequence_connection),
            task_run_id=ids[0],
        )
        == 7
    )
    assert "coalesce(max(seq), 0) + 1" in sequence_connection.calls[0][0]


def _intent_row(*, identifier: int) -> DictRow:
    return cast(
        DictRow,
        {
            "id": UUID(int=identifier),
            "tenant_id": UUID(int=1),
            "project_id": UUID(int=2),
            "task_run_id": UUID(int=3),
            "owner_kind": "TASK_RUN",
            "owner_id": UUID(int=3),
            "namespace": "atlas-prod",
            "workflow_id": "atlas-task/tenant/1/run/3",
            "request_digest": "sha256:" + "a" * 64,
            "manifest_hash": "sha256:" + "b" * 64,
            "workflow_type": "AtlasTaskRunWorkflow",
            "task_queue": "atlas-task-run",
            "status": "PENDING",
            "available_at": DataProfileVersion.model_validate(data_payload()).created_at,
            "claim_token": None,
            "claimed_by": None,
            "claimed_at": None,
            "claim_expires_at": None,
            "dispatch_attempts": 0,
            "last_error_code": None,
            "last_error_at": None,
            "workflow_started_at": None,
            "dispatch_failed_at": None,
            "dispatch_revision": 0,
            "created_at": DataProfileVersion.model_validate(data_payload()).created_at,
        },
    )


@pytest.mark.anyio
async def test_start_intents_are_read_only_and_stably_ordered() -> None:
    repository = TaskExecutionStateRepository()
    row = _intent_row(identifier=10)
    connection = StubConnection(
        StubCursor(row=row),
        StubCursor(rows=(row, _intent_row(identifier=11))),
    )

    intent = await repository.get_workflow_start_intent(
        _connection(connection),
        owner_kind="TASK_RUN",
        owner_id=UUID(int=3),
    )
    pending = await repository.list_pending_workflow_start_intents(
        _connection(connection),
        project_id=UUID(int=2),
        limit=64,
    )

    assert isinstance(intent, TaskWorkflowStartIntent)
    assert intent.status == "PENDING"
    assert len(pending) == 2
    assert "status = 'PENDING'" in connection.calls[1][0]
    assert "order by created_at, id" in connection.calls[1][0]
    assert all("update" not in query.casefold() for query, _params in connection.calls)
