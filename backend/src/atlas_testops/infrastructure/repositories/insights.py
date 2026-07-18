"""PostgreSQL reads and immutable persistence for quality Insights."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.domain.insight import InsightSnapshot
from atlas_testops.domain.result import TaskGateDecision, TaskResultSnapshot


@dataclass(frozen=True, slots=True)
class InsightSourceRecord:
    """One latest stable Result Snapshot and its TaskPlan/Gate context."""

    snapshot: TaskResultSnapshot
    quality_finalized_at: datetime
    task_plan_id: UUID
    task_plan_name: str
    gate_decision: TaskGateDecision | None


class InsightRepository:
    """Compile comparable Result facts and persist pinned InsightSnapshots."""

    async def project_exists(
        self,
        connection: AsyncConnection[DictRow],
        project_id: UUID,
    ) -> bool:
        """Check Project visibility through the active tenant RLS context."""

        cursor = await connection.execute(
            """
            select 1
            from atlas.project
            where id = %s
            """,
            (project_id,),
        )
        return await cursor.fetchone() is not None

    async def list_comparable_sources(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        as_of: datetime,
        start_at: datetime,
    ) -> tuple[InsightSourceRecord, ...]:
        """Load one latest stable Snapshot per TaskRun in a fixed event-time range."""

        cursor = await connection.execute(
            """
            with eligible as (
              select distinct on (source.task_run_id)
                source.id,
                source.task_run_id,
                source.revision,
                source.projection_watermark,
                source.snapshot,
                run.finalized_at as quality_finalized_at,
                plan.id as task_plan_id,
                plan.name as task_plan_name
              from atlas.task_result_snapshot source
              join atlas.task_run run
                on run.id = source.task_run_id
               and run.tenant_id = source.tenant_id
               and run.project_id = source.project_id
              join atlas.task_plan_version version
                on version.id = run.task_plan_version_id
               and version.tenant_id = run.tenant_id
               and version.project_id = run.project_id
              join atlas.task_plan plan
                on plan.id = version.task_plan_id
               and plan.tenant_id = version.tenant_id
               and plan.project_id = version.project_id
              where source.project_id = %s
                and source.finality in ('FULLY_RESOLVED', 'REEVALUATED')
                and run.finalized_at is not null
                and source.created_at <= %s
                and run.finalized_at >= %s
                and run.finalized_at <= %s
              order by source.task_run_id, source.revision desc
            )
            select
              eligible.snapshot,
              eligible.quality_finalized_at,
              eligible.task_plan_id,
              eligible.task_plan_name,
              gate.decision_document
            from eligible
            left join lateral (
              select decision.decision_document
              from atlas.task_gate_decision decision
              where decision.result_snapshot_id = eligible.id
                and decision.evaluated_at <= %s
              order by decision.revision desc
              limit 1
            ) gate on true
            order by
              eligible.quality_finalized_at,
              eligible.task_run_id,
              eligible.revision
            """,
            (project_id, as_of, start_at, as_of, as_of),
        )
        rows = await cursor.fetchall()
        return tuple(
            InsightSourceRecord(
                snapshot=TaskResultSnapshot.model_validate(row["snapshot"]),
                quality_finalized_at=row["quality_finalized_at"],
                task_plan_id=row["task_plan_id"],
                task_plan_name=row["task_plan_name"],
                gate_decision=(
                    TaskGateDecision.model_validate(row["decision_document"])
                    if row["decision_document"] is not None
                    else None
                ),
            )
            for row in rows
        )

    async def get_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        snapshot_id: UUID,
    ) -> InsightSnapshot | None:
        """Load one exact immutable InsightSnapshot."""

        cursor = await connection.execute(
            """
            select snapshot
            from atlas.insight_snapshot
            where id = %s
            """,
            (snapshot_id,),
        )
        row = await cursor.fetchone()
        return InsightSnapshot.model_validate(row["snapshot"]) if row is not None else None

    async def get_snapshot_by_mutation(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        client_mutation_id: str,
    ) -> InsightSnapshot | None:
        """Load the permanent result for one Project mutation identity."""

        cursor = await connection.execute(
            """
            select snapshot
            from atlas.insight_snapshot
            where project_id = %s and client_mutation_id = %s
            """,
            (project_id, client_mutation_id),
        )
        row = await cursor.fetchone()
        return InsightSnapshot.model_validate(row["snapshot"]) if row is not None else None

    async def insert_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        snapshot: InsightSnapshot,
    ) -> InsightSnapshot:
        """Insert a pinned Snapshot and return an exact replay on conflict."""

        cursor = await connection.execute(
            """
            insert into atlas.insight_snapshot (
              id, tenant_id, project_id, window_days,
              request_hash, client_mutation_id,
              as_of, baseline_start_at, current_start_at, current_end_at,
              source_snapshot_ids, source_snapshot_hashes, source_set_digest,
              gate_decision_ids, gate_decision_hashes,
              projection_watermark, query_hash, auth_scope_hash,
              created_by, created_at, snapshot_hash, snapshot
            ) values (
              %s, %s, %s, %s,
              %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s
            )
            on conflict (tenant_id, project_id, client_mutation_id) do nothing
            returning snapshot
            """,
            (
                snapshot.id,
                snapshot.tenant_id,
                snapshot.project_id,
                snapshot.window_days,
                snapshot.request_hash,
                snapshot.client_mutation_id,
                snapshot.dataset_cut.as_of,
                snapshot.baseline.start_at,
                snapshot.current.start_at,
                snapshot.current.end_at,
                list(snapshot.dataset_cut.source_snapshot_ids),
                list(snapshot.dataset_cut.source_snapshot_hashes),
                snapshot.dataset_cut.source_set_digest,
                list(snapshot.dataset_cut.gate_decision_ids),
                list(snapshot.dataset_cut.gate_decision_hashes),
                snapshot.dataset_cut.projection_watermark,
                snapshot.dataset_cut.query_hash,
                snapshot.dataset_cut.auth_scope_hash,
                snapshot.created_by,
                snapshot.created_at,
                snapshot.snapshot_hash,
                Jsonb(snapshot.model_dump(mode="json", by_alias=True)),
            ),
        )
        row = await cursor.fetchone()
        if row is not None:
            return InsightSnapshot.model_validate(row["snapshot"])
        existing = await self.get_snapshot_by_mutation(
            connection,
            project_id=snapshot.project_id,
            client_mutation_id=snapshot.client_mutation_id,
        )
        if existing is None:
            raise RuntimeError("InsightSnapshot conflict did not return an existing row")
        if existing != snapshot:
            raise ValueError("InsightSnapshot mutation identity conflicts with other content")
        return existing


__all__ = ["InsightRepository", "InsightSourceRecord"]
