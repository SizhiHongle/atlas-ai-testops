"""Application boundaries for safe, replayable DebugRun live streams."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid7

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.domain.case.test_runtime_evidence import _run

import atlas_testops.application.live as live_module
from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.live import (
    DebugLiveService,
    DebugLiveStreamLimiter,
    DebugLiveStreamPlan,
)
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.case import (
    DebugRun,
    DebugRunEvent,
    DebugRunLifecycle,
    DebugRunOutcome,
    DebugRunSnapshotStatus,
)
from atlas_testops.domain.case import TestIntent as CaseIntent
from atlas_testops.domain.runtime.live import (
    DebugLiveCursor,
    DebugLiveRunProjection,
    decode_debug_live_cursor,
    encode_debug_live_cursor,
)
from atlas_testops.domain.workflow import WorkflowGraph
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.debug_runs import DebugRunLiveSeed


@pytest.fixture
def debug_run(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> DebugRun:
    return _run(valid_graph, intent_factory)


class RecordingDatabase:
    def __init__(self) -> None:
        self.active_transactions = 0
        self.contexts: list[DatabaseContext] = []

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        self.active_transactions += 1
        try:
            yield cast(AsyncConnection[DictRow], object())
        finally:
            self.active_transactions -= 1


class RecordingDebugRunRepository:
    def __init__(
        self,
        seed: DebugRunLiveSeed | None,
        *,
        events: tuple[DebugRunEvent, ...] = (),
    ) -> None:
        self.seed = seed
        self.events = events
        self.seed_calls: list[UUID] = []
        self.event_calls: list[tuple[UUID, int, int]] = []

    async def get_live_seed(
        self,
        _connection: AsyncConnection[DictRow],
        run_id: UUID,
    ) -> DebugRunLiveSeed | None:
        self.seed_calls.append(run_id)
        return self.seed

    async def list_events(
        self,
        _connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        after_seq: int,
        limit: int,
    ) -> tuple[DebugRunEvent, ...]:
        self.event_calls.append((run_id, after_seq, limit))
        return tuple(event for event in self.events if event.seq > after_seq)[: limit + 1]


def _actor(run: DebugRun, *, project_id: UUID | None = None) -> ActorContext:
    return ActorContext(
        tenant_id=run.tenant_id,
        actor_id=uuid7(),
        request_id="request-debug-live-test",
        session_id=uuid7(),
        current_project_id=project_id or run.project_id,
        grants=(
            AccessGrant(
                role=PlatformRole.RUN_OPERATOR,
                project_id=project_id or run.project_id,
            ),
        ),
    )


def _event(
    run: DebugRun,
    *,
    seq: int,
    event_type: str,
    payload: dict[str, Any],
    lifecycle: DebugRunLifecycle | None = None,
    outcome: DebugRunOutcome | None = None,
    occurred_at: datetime | None = None,
) -> DebugRunEvent:
    return DebugRunEvent(
        id=uuid7(),
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        test_case_id=run.test_case_id,
        debug_run_id=run.id,
        seq=seq,
        event_type=event_type,
        lifecycle=lifecycle or run.lifecycle,
        outcome=outcome or run.outcome,
        snapshot_status=run.snapshot_status,
        payload=payload,
        occurred_at=occurred_at or run.requested_at,
    )


def _terminated(run: DebugRun) -> DebugRun:
    return DebugRun.model_validate(
        run.model_copy(
            update={
                "lifecycle": DebugRunLifecycle.TERMINATED,
                "outcome": DebugRunOutcome.FAILED,
                "started_at": run.requested_at,
                "completed_at": run.requested_at,
                "revision": run.revision + 1,
                "updated_at": run.requested_at,
            }
        ).model_dump(mode="python")
    )


def _outdated(run: DebugRun) -> DebugRun:
    return DebugRun.model_validate(
        run.model_copy(
            update={
                "snapshot_status": DebugRunSnapshotStatus.OUTDATED,
                "outdated_at": run.completed_at,
                "revision": run.revision + 1,
            }
        ).model_dump(mode="python")
    )


def _projection(run: DebugRun) -> DebugLiveRunProjection:
    return DebugLiveRunProjection(
        debug_run_id=run.id,
        project_id=run.project_id,
        test_case_id=run.test_case_id,
        environment_id=run.environment_id,
        lifecycle=run.lifecycle,
        outcome=run.outcome,
        snapshot_status=run.snapshot_status,
        revision=run.revision,
        execution_deadline=run.execution_deadline,
        cancel_requested_at=run.cancel_requested_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _service(
    database: RecordingDatabase,
    repository: RecordingDebugRunRepository,
    *,
    poll_interval_seconds: float = 0.5,
    heartbeat_interval_seconds: float = 1.0,
    maximum_connection_seconds: float = 3.0,
) -> DebugLiveService:
    return DebugLiveService(
        cast(Database, database),
        poll_interval_seconds=poll_interval_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        maximum_connection_seconds=maximum_connection_seconds,
        batch_size=100,
        repository=cast(Any, repository),
    )


@pytest.mark.anyio
async def test_snapshot_uses_exact_head_cursor_and_redacts_cancel_reason(
    debug_run: DebugRun,
) -> None:
    cancel_actor = uuid7()
    cancelled_run = DebugRun.model_validate(
        debug_run.model_copy(
            update={
                "cancel_requested_at": debug_run.requested_at,
                "cancel_requested_by": cancel_actor,
                "revision": 2,
            }
        ).model_dump(mode="python")
    )
    cancel_event = _event(
        cancelled_run,
        seq=2,
        event_type="debug_run.cancel_requested",
        payload={
            "clientMutationId": "cancel-request-0001",
            "reason": "Authorization: Bearer must-never-reach-live-clients",
        },
    )
    database = RecordingDatabase()
    repository = RecordingDebugRunRepository(
        DebugRunLiveSeed(
            run=_projection(cancelled_run),
            latest_event=cancel_event,
            head_seq=2,
        )
    )

    snapshot = await _service(database, repository).get_snapshot(
        _actor(cancelled_run),
        cancelled_run.id,
    )

    cursor = decode_debug_live_cursor(
        snapshot.cursor,
        expected_run_id=cancelled_run.id,
    )
    assert cursor.after_seq == 2
    assert snapshot.latest_event is not None
    assert snapshot.latest_event.data == {}
    assert snapshot.run.cancel_requested_at == cancelled_run.cancel_requested_at
    serialized = snapshot.model_dump_json()
    assert "must-never-reach-live-clients" not in serialized
    assert str(cancel_actor) not in serialized
    assert database.active_transactions == 0
    assert repository.seed_calls == [cancelled_run.id]


@pytest.mark.anyio
async def test_cursor_replay_crosses_terminal_event_for_later_snapshot_updates(
    debug_run: DebugRun,
) -> None:
    terminated_run = _terminated(debug_run)
    outdated_run = _outdated(terminated_run)
    proposed = _event(
        debug_run,
        seq=2,
        event_type="debug_run.browser.action.proposed",
        lifecycle=DebugRunLifecycle.RUNNING,
        payload={
            "reportId": str(uuid7()),
            "reportSequence": 7,
            "reportKind": "ACTION_PROPOSED",
            "actorSlot": "operator",
            "actionId": "action-0001",
            "safeSummary": "click the submit control",
            "action": "CLICK",
            "risk": "LOW",
            "nodeId": "submit-order",
            "targetRef": "target-submit",
            "routeKey": "checkout",
            "authorization": "Bearer secret",
            "password": "never-project-this",
            "objectRef": "evidence://private-object",
            "value": "sensitive input",
        },
    )
    terminated = _event(
        terminated_run,
        seq=3,
        event_type="debug_run.terminated",
        lifecycle=DebugRunLifecycle.TERMINATED,
        outcome=DebugRunOutcome.FAILED,
        payload={"outcome": "FAILED", "failureDetail": "private stack trace"},
    )
    snapshot_outdated = _event(
        outdated_run,
        seq=4,
        event_type="debug_run.snapshot_outdated",
        payload={
            "currentSemanticRevision": 8,
            "currentSemanticDigest": "sha256:" + "f" * 64,
        },
    )
    database = RecordingDatabase()
    repository = RecordingDebugRunRepository(
        DebugRunLiveSeed(
            run=_projection(outdated_run),
            latest_event=snapshot_outdated,
            head_seq=4,
        ),
        events=(proposed, terminated, snapshot_outdated),
    )
    service = _service(database, repository)
    resume_cursor = encode_debug_live_cursor(
        DebugLiveCursor(debug_run_id=debug_run.id, after_seq=1)
    )

    plan = await service.prepare_stream(
        _actor(debug_run),
        debug_run.id,
        last_event_id=resume_cursor,
    )

    disconnect_checks = 0

    async def disconnected_after_batch() -> bool:
        nonlocal disconnect_checks
        disconnect_checks += 1
        return disconnect_checks > 4

    replayed = [
        event
        async for event in service.iter_events(
            _actor(debug_run),
            debug_run.id,
            plan,
            is_disconnected=disconnected_after_batch,
        )
    ]

    assert plan.snapshot is None
    assert plan.after_seq == 1
    assert [event.seq for event in replayed if event is not None] == [2, 3, 4]
    first = replayed[0]
    assert first is not None
    assert first.data == {
        "reportId": proposed.payload["reportId"],
        "reportSequence": 7,
        "reportKind": "ACTION_PROPOSED",
        "actorSlot": "operator",
        "actionId": "action-0001",
        "safeSummary": "click the submit control",
        "action": "CLICK",
        "risk": "LOW",
        "nodeId": "submit-order",
        "targetRef": "target-submit",
        "routeKey": "checkout",
    }
    assert set(first.data).isdisjoint({"authorization", "password", "objectRef", "value"})
    terminal = replayed[1]
    assert terminal is not None
    assert terminal.data == {"outcome": "FAILED"}
    last = replayed[2]
    assert last is not None
    assert last.data == {"currentSemanticRevision": 8}
    assert repository.event_calls == [(debug_run.id, 1, 100)]
    assert database.active_transactions == 0


@pytest.mark.anyio
async def test_prepare_stream_rejects_future_and_wrong_run_cursors(
    debug_run: DebugRun,
) -> None:
    repository = RecordingDebugRunRepository(
        DebugRunLiveSeed(run=_projection(debug_run), latest_event=None, head_seq=2)
    )
    database = RecordingDatabase()
    service = _service(database, repository)
    actor = _actor(debug_run)
    future = encode_debug_live_cursor(DebugLiveCursor(debug_run_id=debug_run.id, after_seq=3))

    with pytest.raises(ApplicationError) as future_error:
        await service.prepare_stream(
            actor,
            debug_run.id,
            last_event_id=future,
        )
    assert future_error.value.error_code is ErrorCode.LIVE_CURSOR_INVALID
    assert future_error.value.status_code == 400

    another_run = encode_debug_live_cursor(DebugLiveCursor(debug_run_id=uuid7(), after_seq=1))
    seed_calls_before_wrong_run = len(repository.seed_calls)
    with pytest.raises(ApplicationError) as wrong_run_error:
        await service.prepare_stream(
            actor,
            debug_run.id,
            last_event_id=another_run,
        )
    assert wrong_run_error.value.error_code is ErrorCode.LIVE_CURSOR_INVALID
    assert wrong_run_error.value.status_code == 400
    assert len(repository.seed_calls) == seed_calls_before_wrong_run


@pytest.mark.anyio
async def test_heartbeat_never_advances_replay_position_or_holds_transaction(
    debug_run: DebugRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = RecordingDatabase()
    repository = RecordingDebugRunRepository(
        DebugRunLiveSeed(run=_projection(debug_run), latest_event=None, head_seq=5)
    )
    service = _service(database, repository)
    clock = 0.0

    def fake_monotonic() -> float:
        return clock

    async def fake_sleep(delay: float) -> None:
        nonlocal clock
        assert database.active_transactions == 0
        clock += delay

    disconnected = False

    async def connected() -> bool:
        return disconnected

    monkeypatch.setattr(live_module, "monotonic", fake_monotonic)
    monkeypatch.setattr(live_module, "sleep", fake_sleep)
    plan = DebugLiveStreamPlan(snapshot=None, after_seq=5)
    stream = service.iter_events(
        _actor(debug_run),
        debug_run.id,
        plan,
        is_disconnected=connected,
    )

    heartbeat = await anext(stream)
    disconnected = True
    assert [event async for event in stream] == []

    assert heartbeat is None
    assert len(repository.event_calls) >= 2
    assert {after_seq for _, after_seq, _ in repository.event_calls} == {5}
    assert database.active_transactions == 0


@pytest.mark.anyio
async def test_missing_and_hidden_run_share_the_same_not_found_boundary(
    debug_run: DebugRun,
) -> None:
    hidden_actor = _actor(debug_run, project_id=uuid7())
    errors: list[ApplicationError] = []
    for seed in (
        None,
        DebugRunLiveSeed(run=_projection(debug_run), latest_event=None, head_seq=0),
    ):
        database = RecordingDatabase()
        service = _service(database, RecordingDebugRunRepository(seed))
        with pytest.raises(ApplicationError) as captured:
            await service.get_snapshot(hidden_actor, debug_run.id)
        errors.append(captured.value)
        assert database.active_transactions == 0

    assert [(error.error_code, error.status_code) for error in errors] == [
        (ErrorCode.NOT_FOUND, 404),
        (ErrorCode.NOT_FOUND, 404),
    ]
    assert errors[0].title == errors[1].title
    assert errors[0].detail == errors[1].detail


@pytest.mark.anyio
async def test_stream_limiter_rejects_capacity_without_waiting() -> None:
    limiter = DebugLiveStreamLimiter(maximum_connections=1)
    await limiter.acquire()

    with pytest.raises(ApplicationError) as captured:
        await limiter.acquire()

    assert captured.value.error_code is ErrorCode.LIVE_STREAM_CAPACITY_EXCEEDED
    assert captured.value.status_code == 429
    assert captured.value.headers == {"Retry-After": "2"}
    assert limiter.active_connections == 1
    limiter.release()
    assert limiter.active_connections == 0
