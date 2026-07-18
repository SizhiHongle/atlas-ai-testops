"""Explicit command boundary tests for immutable Task result re-evaluation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.application.test_result_projection import (
    _closed_snapshot_run,
    _closed_snapshot_unit,
    _OutboxRepository,
    _ResultRepository,
    _task_hygiene_resolution,
    _task_resolution,
    _TaskRepository,
)
from tests.infrastructure.test_task_run_repository import NOW, _aggregate

from atlas_testops.application.access import ActorContext
from atlas_testops.application.result_projection import ResultProjectionService
from atlas_testops.application.result_reevaluation import ResultReevaluationService
from atlas_testops.core.errors import ApplicationError
from atlas_testops.domain.result import (
    TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
    DataHygiene,
    ExecutionInfluence,
    RequestTaskResultReevaluation,
    TaskResultReevaluationCommand,
    TaskResultSnapshot,
    TaskResultSnapshotFinality,
    Verdict,
)
from atlas_testops.domain.task import ExecutionQuality, TaskRun
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyReservation,
)


class _Connection:
    async def execute(
        self,
        _query: str,
        _parameters: object = None,
    ) -> _Cursor:
        return _Cursor()


class _Cursor:
    async def fetchone(self) -> dict[str, object]:
        return {"observed_at": NOW + timedelta(minutes=20)}


class _Database:
    @asynccontextmanager
    async def transaction(self, _context: object) -> AsyncIterator[_Connection]:
        yield _Connection()


class _ReevaluationTaskRepository(_TaskRepository):
    def __init__(self, run: TaskRun) -> None:
        super().__init__(())
        self.run = run

    async def get_run_for_update(
        self,
        _connection: object,
        task_run_id: UUID,
    ) -> TaskRun | None:
        return self.run if self.run.id == task_run_id else None


class _ReevaluationResultRepository(_ResultRepository):
    def __init__(self, snapshots: list[TaskResultSnapshot]) -> None:
        super().__init__()
        self.snapshots = snapshots
        self.commands: list[TaskResultReevaluationCommand] = []

    async def get_snapshot_by_id(
        self,
        _connection: object,
        snapshot_id: UUID,
    ) -> TaskResultSnapshot | None:
        return next((item for item in self.snapshots if item.id == snapshot_id), None)

    async def get_reevaluated_snapshot(
        self,
        _connection: object,
        *,
        task_run_id: UUID,
        source_snapshot_id: UUID,
        policy_digest: str,
    ) -> TaskResultSnapshot | None:
        return next(
            (
                item
                for item in self.snapshots
                if item.task_run_id == task_run_id
                and item.finality is TaskResultSnapshotFinality.REEVALUATED
                and item.reevaluation_source_snapshot_id == source_snapshot_id
                and item.aggregation_policy_digest == policy_digest
            ),
            None,
        )

    async def insert_reevaluation_command(
        self,
        _connection: object,
        command: TaskResultReevaluationCommand,
    ) -> None:
        self.commands.append(command)


class _IdempotencyRepository:
    def __init__(self) -> None:
        self.responses: dict[tuple[UUID, str, str], CachedHttpResponse] = {}

    async def reserve(
        self,
        _connection: object,
        *,
        tenant_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        now: object,
        ttl: object,
    ) -> IdempotencyReservation:
        del now, ttl
        cached = self.responses.get((tenant_id, scope, key))
        if cached is None:
            return IdempotencyReservation(acquired=True)
        return IdempotencyReservation(acquired=False, cached_response=cached)

    async def complete(
        self,
        _connection: object,
        *,
        tenant_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        response: CachedHttpResponse,
    ) -> None:
        del request_hash
        self.responses[(tenant_id, scope, key)] = response


class _AuditRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def append(self, _connection: object, **values: object) -> UUID:
        self.events.append(values)
        return uuid4()


def _actor(run: TaskRun) -> ActorContext:
    return ActorContext(
        tenant_id=run.tenant_id,
        actor_id=uuid4(),
        request_id="result-reevaluation-test",
        development_override=True,
    )


async def _result_chain() -> tuple[TaskRun, list[TaskResultSnapshot]]:
    raw_run, manifest, raw_units, attempts = _aggregate(unit_count=1)
    run = _closed_snapshot_run(raw_run, unit_count=1)
    unit = _closed_snapshot_unit(raw_units[0], quality=ExecutionQuality.PASSED)
    tasks = _TaskRepository(attempts)
    tasks.units = [unit]
    results = _ResultRepository()
    results.bind_attempts(tasks.attempts)
    results.bind_units(tasks.units)
    results.resolutions = [
        _task_resolution(
            unit,
            attempts[0],
            verdict=Verdict.PASSED,
            influence=ExecutionInfluence.AUTONOMOUS,
        )
    ]
    results.hygiene_resolutions = [
        _task_hygiene_resolution(
            unit,
            attempts[0],
            hygiene=DataHygiene.NOT_APPLICABLE,
        )
    ]
    service = ResultProjectionService(
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        outbox_repository=cast(Any, _OutboxRepository()),
    )
    await service.snapshot_task_fully_resolved(
        cast(AsyncConnection[DictRow], object()),
        run=run,
        manifest=manifest,
        created_at=cast(Any, run.closed_at),
    )
    return run, results.snapshots


@pytest.mark.anyio
async def test_explicit_command_appends_one_reevaluated_revision_and_replays() -> None:
    run, snapshots = await _result_chain()
    source = snapshots[-1]
    tasks = _ReevaluationTaskRepository(run)
    results = _ReevaluationResultRepository(snapshots)
    idempotency = _IdempotencyRepository()
    audit = _AuditRepository()
    outbox = _OutboxRepository()
    service = ResultReevaluationService(
        cast(Any, _Database()),
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        audit_repository=cast(Any, audit),
        outbox_repository=cast(Any, outbox),
        idempotency_repository=cast(Any, idempotency),
    )
    request = RequestTaskResultReevaluation(
        source_snapshot_id=source.id,
        client_mutation_id="reevaluate-result-001",
    )

    created = await service.reevaluate(
        _actor(run),
        run.id,
        request,
        idempotency_key=request.client_mutation_id,
    )
    replay = await service.reevaluate(
        _actor(run),
        run.id,
        request,
        idempotency_key=request.client_mutation_id,
    )
    equivalent = await service.reevaluate(
        _actor(run),
        run.id,
        request.model_copy(update={"client_mutation_id": "reevaluate-result-002"}),
        idempotency_key="reevaluate-result-002",
    )

    assert created.status_code == 201
    assert created.replayed is False
    assert created.value.finality is TaskResultSnapshotFinality.REEVALUATED
    assert created.value.revision == source.revision + 1
    assert created.value.reevaluation_source_snapshot_id == source.id
    assert created.value.aggregation_policy_digest == TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST
    assert created.value.verdict_counts == source.verdict_counts
    assert replay.value == created.value
    assert replay.replayed is True
    assert equivalent.value == created.value
    assert equivalent.status_code == 200
    assert len(results.commands) == 1
    assert len(audit.events) == 1
    assert [event.event_type for event in outbox.events] == [
        "task.snapshot_created"
    ]


@pytest.mark.anyio
async def test_reevaluation_rejects_quality_source_and_mismatched_key() -> None:
    run, snapshots = await _result_chain()
    quality = snapshots[0]
    results = _ReevaluationResultRepository(snapshots)
    service = ResultReevaluationService(
        cast(Any, _Database()),
        result_repository=cast(Any, results),
        task_repository=cast(Any, _ReevaluationTaskRepository(run)),
        audit_repository=cast(Any, _AuditRepository()),
        outbox_repository=cast(Any, _OutboxRepository()),
        idempotency_repository=cast(Any, _IdempotencyRepository()),
    )
    request = RequestTaskResultReevaluation(
        source_snapshot_id=quality.id,
        client_mutation_id="reevaluate-result-003",
    )

    with pytest.raises(ApplicationError, match="Idempotency-Key"):
        await service.reevaluate(
            _actor(run),
            run.id,
            request,
            idempotency_key="different-mutation-key",
        )
    with pytest.raises(ApplicationError, match="FULLY_RESOLVED"):
        await service.reevaluate(
            _actor(run),
            run.id,
            request,
            idempotency_key=request.client_mutation_id,
        )
