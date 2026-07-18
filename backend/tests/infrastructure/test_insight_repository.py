"""Repository tests for comparable sources and immutable InsightSnapshots."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import cast

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.domain.insight.test_insight_contracts import (
    ACTOR_ID,
    PLAN_A,
    PROJECT_ID,
    _actor,
    _source,
)
from tests.infrastructure.test_task_run_repository import NOW

from atlas_testops.application.insights import _compile_brief, _pin_brief
from atlas_testops.domain.insight import InsightSnapshot
from atlas_testops.infrastructure.repositories.insights import InsightRepository


class _Cursor:
    def __init__(self, result: DictRow | tuple[DictRow, ...] | None) -> None:
        self._result = result

    async def fetchone(self) -> DictRow | None:
        if isinstance(self._result, tuple):
            return self._result[0] if self._result else None
        return self._result

    async def fetchall(self) -> list[DictRow]:
        if isinstance(self._result, tuple):
            return list(self._result)
        return [self._result] if self._result is not None else []


class _Connection:
    def __init__(self, *rows: DictRow | tuple[DictRow, ...] | None) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, Sequence[object] | None]] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> _Cursor:
        self.calls.append((query, params))
        result = self._rows.pop(0) if self._rows else None
        return _Cursor(result)


def _snapshot() -> InsightSnapshot:
    source = _source(
        "repository",
        days_ago=1,
        manifest_count=4,
        trusted_passed=3,
        stable=3,
        plan_id=PLAN_A,
    )
    brief = _compile_brief(
        actor=_actor(),
        project_id=PROJECT_ID,
        window_days=30,
        as_of=NOW,
        sources=(source,),
    )
    return _pin_brief(
        brief,
        request_hash="sha256:" + "a" * 64,
        client_mutation_id="insight:repository:1",
        created_by=ACTOR_ID,
        created_at=NOW,
    )


@pytest.mark.anyio
async def test_lists_latest_stable_sources_with_exact_gate_fence() -> None:
    source = _source(
        "source",
        days_ago=1,
        manifest_count=1,
        trusted_passed=1,
        stable=1,
        plan_id=PLAN_A,
    )
    connection = _Connection(
        (
            cast(
                DictRow,
                {
                    "snapshot": source.snapshot.model_dump(
                        mode="json",
                        by_alias=True,
                    ),
                    "quality_finalized_at": source.quality_finalized_at,
                    "task_plan_id": PLAN_A,
                    "task_plan_name": "客户权限",
                    "decision_document": None,
                },
            ),
        )
    )
    repository = InsightRepository()

    records = await repository.list_comparable_sources(
        cast(AsyncConnection[DictRow], connection),
        project_id=PROJECT_ID,
        as_of=NOW,
        start_at=NOW - timedelta(days=60),
    )

    assert records == (source,)
    query, params = connection.calls[0]
    assert "distinct on (source.task_run_id)" in query
    assert "FULLY_RESOLVED" in query and "REEVALUATED" in query
    assert "run.finalized_at >= %s" in query
    assert "decision.evaluated_at <= %s" in query
    assert params == (
        PROJECT_ID,
        NOW,
        NOW - timedelta(days=60),
        NOW,
        NOW,
    )


@pytest.mark.anyio
async def test_reads_and_inserts_complete_insight_snapshot_projection() -> None:
    snapshot = _snapshot()
    document = snapshot.model_dump(mode="json", by_alias=True)
    connection = _Connection(
        cast(DictRow, {"snapshot": document}),
        cast(DictRow, {"snapshot": document}),
        cast(DictRow, {"snapshot": document}),
    )
    repository = InsightRepository()

    loaded = await repository.get_snapshot(
        cast(AsyncConnection[DictRow], connection),
        snapshot.id,
    )
    by_mutation = await repository.get_snapshot_by_mutation(
        cast(AsyncConnection[DictRow], connection),
        project_id=PROJECT_ID,
        client_mutation_id=snapshot.client_mutation_id,
    )
    inserted = await repository.insert_snapshot(
        cast(AsyncConnection[DictRow], connection),
        snapshot,
    )

    assert loaded == by_mutation == inserted == snapshot
    insert_query, params = connection.calls[2]
    assert "insert into atlas.insight_snapshot" in insert_query
    assert "on conflict (tenant_id, project_id, client_mutation_id)" in insert_query
    assert params is not None and len(params) == 22
    assert params[10] == list(snapshot.dataset_cut.source_snapshot_ids)
    assert params[13] == list(snapshot.dataset_cut.gate_decision_ids)
    assert params[-2] == snapshot.snapshot_hash


@pytest.mark.anyio
async def test_project_visibility_query_uses_rls_scoped_project_id() -> None:
    connection = _Connection(cast(DictRow, {"exists": 1}))
    repository = InsightRepository()

    exists = await repository.project_exists(
        cast(AsyncConnection[DictRow], connection),
        PROJECT_ID,
    )

    assert exists
    assert "from atlas.project" in connection.calls[0][0]
    assert connection.calls[0][1] == (PROJECT_ID,)
