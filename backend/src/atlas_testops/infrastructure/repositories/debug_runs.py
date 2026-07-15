"""PostgreSQL repository for immutable DebugRun snapshots and events."""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.case import (
    DebugRun,
    DebugRunEvent,
    PlanTemplate,
    StartDebugRun,
    TestIR,
    WorkflowDraftSnapshot,
)

DEBUG_RUN_COLUMNS = (
    "id, tenant_id, project_id, environment_id, test_case_id, draft_id, "
    "semantic_revision, semantic_digest, compiled_digest, test_ir, "
    "test_ir_digest, plan_template, plan_digest, lifecycle, outcome, "
    "snapshot_status, temporal_workflow_id, requested_by, execution_deadline, "
    "execution_contract_id, execution_contract_digest, "
    "evidence_manifest_id, evidence_manifest_digest, failure_code, "
    "failure_detail, cancel_requested_at, cancel_requested_by, requested_at, "
    "started_at, completed_at, outdated_at, revision, created_at, updated_at"
)
DEBUG_RUN_EVENT_COLUMNS = (
    "id, tenant_id, project_id, test_case_id, debug_run_id, seq, event_type, "
    "lifecycle, outcome, snapshot_status, payload, occurred_at"
)


class DebugRunRepository:
    """Persist DebugRun facts without performing authorization decisions."""

    async def create_run(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        draft: WorkflowDraftSnapshot,
        command: StartDebugRun,
        test_ir: TestIR,
        plan_template: PlanTemplate,
        compiled_digest: str,
        temporal_workflow_id: str,
        requested_by: UUID | None,
        requested_at: datetime,
    ) -> DebugRun:
        cursor = await connection.execute(
            f"""
            insert into atlas.debug_run (
              id, tenant_id, project_id, environment_id, test_case_id, draft_id,
              semantic_revision, semantic_digest, compiled_digest,
              test_ir, test_ir_digest, plan_template, plan_digest,
              temporal_workflow_id, requested_by, execution_deadline, requested_at
            ) values (
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s
            )
            returning {DEBUG_RUN_COLUMNS}
            """,
            (
                run_id,
                draft.tenant_id,
                draft.project_id,
                command.environment_id,
                draft.test_case_id,
                draft.id,
                draft.semantic_revision,
                draft.semantic_digest,
                compiled_digest,
                Jsonb(test_ir.model_dump(mode="json", by_alias=True)),
                test_ir.content_digest,
                Jsonb(plan_template.model_dump(mode="json", by_alias=True)),
                plan_template.plan_digest,
                temporal_workflow_id,
                requested_by,
                command.execution_deadline,
                requested_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("debug run insert did not return a row")
        return DebugRun.model_validate(row)

    async def get_run(
        self,
        connection: AsyncConnection[DictRow],
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> DebugRun | None:
        lock_clause = "for update" if for_update else "for share"
        cursor = await connection.execute(
            f"""
            select {DEBUG_RUN_COLUMNS}
            from atlas.debug_run
            where id = %s
            {lock_clause}
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        return DebugRun.model_validate(row) if row is not None else None

    async def list_runs(
        self,
        connection: AsyncConnection[DictRow],
        *,
        test_case_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[DebugRun, ...]:
        cursor_filter = ""
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (test_case_id, limit + 1)
        else:
            cursor_filter = "and (requested_at, id) < (%s, %s)"
            parameters = (
                test_case_id,
                cursor.created_at,
                cursor.id,
                limit + 1,
            )
        result = await connection.execute(
            f"""
            select {DEBUG_RUN_COLUMNS}
            from atlas.debug_run
            where test_case_id = %s {cursor_filter}
            order by requested_at desc, id desc
            limit %s
            """,
            parameters,
        )
        return tuple(DebugRun.model_validate(row) for row in await result.fetchall())

    async def request_cancel(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        expected_revision: int,
        requested_by: UUID,
        requested_at: datetime,
    ) -> DebugRun | None:
        cursor = await connection.execute(
            f"""
            update atlas.debug_run
            set cancel_requested_at = %s,
                cancel_requested_by = %s,
                revision = revision + 1
            where id = %s
              and revision = %s
              and cancel_requested_at is null
              and lifecycle <> 'TERMINATED'
            returning {DEBUG_RUN_COLUMNS}
            """,
            (requested_at, requested_by, run_id, expected_revision),
        )
        row = await cursor.fetchone()
        return DebugRun.model_validate(row) if row is not None else None

    async def mark_case_runs_outdated(
        self,
        connection: AsyncConnection[DictRow],
        *,
        test_case_id: UUID,
        current_semantic_revision: int,
        current_semantic_digest: str,
        outdated_at: datetime,
    ) -> tuple[DebugRun, ...]:
        result = await connection.execute(
            f"""
            update atlas.debug_run
            set snapshot_status = 'OUTDATED',
                outdated_at = %s,
                revision = revision + 1
            where test_case_id = %s
              and snapshot_status = 'CURRENT'
              and (
                semantic_revision <> %s
                or semantic_digest <> %s
              )
            returning {DEBUG_RUN_COLUMNS}
            """,
            (
                outdated_at,
                test_case_id,
                current_semantic_revision,
                current_semantic_digest,
            ),
        )
        return tuple(DebugRun.model_validate(row) for row in await result.fetchall())

    async def append_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: DebugRun,
        event_type: str,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> DebugRunEvent:
        cursor = await connection.execute(
            f"""
            insert into atlas.debug_run_event (
              id, tenant_id, project_id, test_case_id, debug_run_id,
              seq, event_type, lifecycle, outcome, snapshot_status,
              payload, occurred_at
            )
            select
              %s, %s, %s, %s, %s,
              coalesce(max(seq), 0) + 1, %s, %s, %s, %s, %s, %s
            from atlas.debug_run_event
            where debug_run_id = %s
            returning {DEBUG_RUN_EVENT_COLUMNS}
            """,
            (
                new_entity_id(),
                run.tenant_id,
                run.project_id,
                run.test_case_id,
                run.id,
                event_type,
                run.lifecycle,
                run.outcome,
                run.snapshot_status,
                Jsonb(payload),
                occurred_at,
                run.id,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("debug run event insert did not return a row")
        return DebugRunEvent.model_validate(row)

    async def list_events(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        after_seq: int,
        limit: int,
    ) -> tuple[DebugRunEvent, ...]:
        result = await connection.execute(
            f"""
            select {DEBUG_RUN_EVENT_COLUMNS}
            from atlas.debug_run_event
            where debug_run_id = %s and seq > %s
            order by seq
            limit %s
            """,
            (run_id, after_seq, limit + 1),
        )
        return tuple(DebugRunEvent.model_validate(row) for row in await result.fetchall())
