"""Insight brief compilation, pinning, access, and replay tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from tests.domain.insight.test_insight_contracts import (
    PLAN_A,
    PROJECT_ID,
    _actor,
    _source,
)
from tests.infrastructure.test_task_run_repository import NOW

from atlas_testops.application.access import ActorContext
from atlas_testops.application.insights import InsightService
from atlas_testops.core.errors import ApplicationError
from atlas_testops.domain.insight import InsightSnapshot, RequestInsightSnapshot
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyReservation,
)
from atlas_testops.infrastructure.repositories.insights import InsightSourceRecord


class _Cursor:
    async def fetchone(self) -> dict[str, object]:
        return {"observed_at": NOW}


class _Connection:
    async def execute(
        self,
        _query: str,
        _params: object = None,
    ) -> _Cursor:
        return _Cursor()


class _Database:
    @asynccontextmanager
    async def transaction(self, _context: object) -> AsyncIterator[_Connection]:
        yield _Connection()


class _InsightRepository:
    def __init__(self, sources: tuple[InsightSourceRecord, ...]) -> None:
        self.sources = sources
        self.snapshots: list[InsightSnapshot] = []
        self.calls: list[tuple[object, ...]] = []

    async def project_exists(self, _connection: object, project_id: UUID) -> bool:
        self.calls.append(("project", project_id))
        return project_id == PROJECT_ID

    async def list_comparable_sources(
        self,
        _connection: object,
        *,
        project_id: UUID,
        as_of: object,
        start_at: object,
    ) -> tuple[InsightSourceRecord, ...]:
        self.calls.append(("sources", project_id, as_of, start_at))
        return self.sources

    async def get_snapshot_by_mutation(
        self,
        _connection: object,
        *,
        project_id: UUID,
        client_mutation_id: str,
    ) -> InsightSnapshot | None:
        return next(
            (
                snapshot
                for snapshot in self.snapshots
                if snapshot.project_id == project_id
                and snapshot.client_mutation_id == client_mutation_id
            ),
            None,
        )

    async def insert_snapshot(
        self,
        _connection: object,
        snapshot: InsightSnapshot,
    ) -> InsightSnapshot:
        self.snapshots.append(snapshot)
        return snapshot

    async def get_snapshot(
        self,
        _connection: object,
        snapshot_id: UUID,
    ) -> InsightSnapshot | None:
        return next(
            (snapshot for snapshot in self.snapshots if snapshot.id == snapshot_id),
            None,
        )


class _IdempotencyRepository:
    def __init__(self) -> None:
        self.completed: CachedHttpResponse | None = None

    async def reserve(self, *_args: object, **_kwargs: object) -> IdempotencyReservation:
        return IdempotencyReservation(acquired=True)

    async def complete(
        self,
        *_args: object,
        response: CachedHttpResponse,
        **_kwargs: object,
    ) -> None:
        self.completed = response


class _AuditRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def append(self, _connection: object, **kwargs: object) -> UUID:
        self.events.append(kwargs)
        return UUID("00000000-0000-7000-8000-000000000121")


class _OutboxRepository:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def append(self, _connection: object, event: object) -> None:
        self.events.append(event)


def _service(
    sources: tuple[InsightSourceRecord, ...],
) -> tuple[
    InsightService,
    _InsightRepository,
    _IdempotencyRepository,
    _AuditRepository,
    _OutboxRepository,
]:
    repository = _InsightRepository(sources)
    idempotency = _IdempotencyRepository()
    audit = _AuditRepository()
    outbox = _OutboxRepository()
    service = InsightService(
        cast(Any, _Database()),
        repository=cast(Any, repository),
        idempotency_repository=cast(Any, idempotency),
        audit_repository=cast(Any, audit),
        outbox_repository=cast(Any, outbox),
    )
    return service, repository, idempotency, audit, outbox


@pytest.mark.anyio
async def test_preview_and_pin_use_exact_cut_and_permanent_replay() -> None:
    sources = (
        _source(
            "current",
            days_ago=1,
            manifest_count=10,
            trusted_passed=8,
            stable=9,
            plan_id=PLAN_A,
        ),
        _source(
            "baseline",
            days_ago=35,
            manifest_count=5,
            trusted_passed=3,
            stable=4,
            plan_id=PLAN_A,
        ),
    )
    service, repository, idempotency, audit, outbox = _service(sources)
    command = RequestInsightSnapshot(
        window_days=30,
        as_of=NOW,
        client_mutation_id="insight:pin:application:1",
    )

    preview = await service.preview(
        _actor(),
        PROJECT_ID,
        window_days=30,
        as_of=NOW,
    )
    created = await service.pin_snapshot(
        _actor(),
        PROJECT_ID,
        command,
        idempotency_key=command.client_mutation_id,
    )
    replayed = await service.pin_snapshot(
        _actor(),
        PROJECT_ID,
        command,
        idempotency_key=command.client_mutation_id,
    )
    loaded = await service.get_snapshot(_actor(), created.value.id)

    assert preview.current.execution_unit_count == 10
    assert preview.current.trusted_pass_rate.basis_points == 8_000
    assert preview.baseline.trusted_pass_rate.basis_points == 6_000
    assert created.status_code == 201 and created.replayed is False
    assert replayed.status_code == 200 and replayed.replayed
    assert replayed.value == loaded == created.value
    assert len(repository.snapshots) == 1
    assert idempotency.completed is not None
    assert len(audit.events) == len(outbox.events) == 1
    assert audit.events[0]["event_type"] == "insight_snapshot.pinned"


@pytest.mark.anyio
async def test_insight_access_window_asof_and_idempotency_fail_closed() -> None:
    service, _, _, _, _ = _service(())
    hidden = ActorContext(
        tenant_id=_actor().tenant_id,
        actor_id=_actor().actor_id,
        request_id="insight-hidden",
    )
    command = RequestInsightSnapshot(
        client_mutation_id="insight:pin:invalid:1",
    )

    with pytest.raises(ApplicationError) as forbidden_project:
        await service.preview(
            hidden,
            PROJECT_ID,
            window_days=30,
            as_of=NOW,
        )
    with pytest.raises(ApplicationError) as invalid_window:
        await service.preview(
            _actor(),
            PROJECT_ID,
            window_days=14,
            as_of=NOW,
        )
    with pytest.raises(ApplicationError) as future:
        await service.preview(
            _actor(),
            PROJECT_ID,
            window_days=30,
            as_of=NOW + timedelta(seconds=1),
        )
    with pytest.raises(ApplicationError) as idempotency:
        await service.pin_snapshot(
            _actor(),
            PROJECT_ID,
            command,
            idempotency_key="different:mutation:key",
        )

    assert forbidden_project.value.status_code == 404
    assert invalid_window.value.status_code == 400
    assert future.value.status_code == 400
    assert idempotency.value.status_code == 400


@pytest.mark.anyio
async def test_snapshot_read_rechecks_project_visibility() -> None:
    service, repository, _, _, _ = _service(
        (
            _source(
                "read",
                days_ago=1,
                manifest_count=1,
                trusted_passed=1,
                stable=1,
                plan_id=PLAN_A,
            ),
        )
    )
    command = RequestInsightSnapshot(
        as_of=NOW,
        client_mutation_id="insight:pin:read:1",
    )
    created = await service.pin_snapshot(
        _actor(),
        PROJECT_ID,
        command,
        idempotency_key=command.client_mutation_id,
    )
    hidden = ActorContext(
        tenant_id=_actor().tenant_id,
        actor_id=_actor().actor_id,
        request_id="insight-read-hidden",
    )

    with pytest.raises(ApplicationError) as exc:
        await service.get_snapshot(hidden, repository.snapshots[0].id)

    assert created.value.id == repository.snapshots[0].id
    assert exc.value.status_code == 404
