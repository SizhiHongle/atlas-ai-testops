"""PostgreSQL repository for append-only Browser Worker reports."""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.domain.runtime import (
    AppendBrowserRuntimeReport,
    BrowserRuntimeReport,
)


class BrowserRuntimeReportRepository:
    """Persist one monotonic, hash-linked report stream per ExecutionContract."""

    async def get_by_id(
        self,
        connection: AsyncConnection[DictRow],
        report_id: UUID,
    ) -> BrowserRuntimeReport | None:
        cursor = await connection.execute(
            """
            select tenant_id, project_id, environment_id, debug_run_id,
                   execution_contract_id, execution_contract_digest,
                   id, report_sequence, report_kind, actor_slot, action_id,
                   payload, payload_digest, previous_chain_digest,
                   chain_digest, occurred_at, recorded_at
            from atlas.browser_runtime_report
            where id = %s
            """,
            (report_id,),
        )
        row = await cursor.fetchone()
        return self._to_domain(row) if row is not None else None

    async def get_by_sequence(
        self,
        connection: AsyncConnection[DictRow],
        *,
        execution_contract_id: UUID,
        sequence: int,
    ) -> BrowserRuntimeReport | None:
        cursor = await connection.execute(
            """
            select tenant_id, project_id, environment_id, debug_run_id,
                   execution_contract_id, execution_contract_digest,
                   id, report_sequence, report_kind, actor_slot, action_id,
                   payload, payload_digest, previous_chain_digest,
                   chain_digest, occurred_at, recorded_at
            from atlas.browser_runtime_report
            where execution_contract_id = %s and report_sequence = %s
            """,
            (execution_contract_id, sequence),
        )
        row = await cursor.fetchone()
        return self._to_domain(row) if row is not None else None

    async def get_latest(
        self,
        connection: AsyncConnection[DictRow],
        execution_contract_id: UUID,
    ) -> BrowserRuntimeReport | None:
        cursor = await connection.execute(
            """
            select tenant_id, project_id, environment_id, debug_run_id,
                   execution_contract_id, execution_contract_digest,
                   id, report_sequence, report_kind, actor_slot, action_id,
                   payload, payload_digest, previous_chain_digest,
                   chain_digest, occurred_at, recorded_at
            from atlas.browser_runtime_report
            where execution_contract_id = %s
            order by report_sequence desc
            limit 1
            """,
            (execution_contract_id,),
        )
        row = await cursor.fetchone()
        return self._to_domain(row) if row is not None else None

    async def action_exists(
        self,
        connection: AsyncConnection[DictRow],
        *,
        execution_contract_id: UUID,
        action_id: UUID,
    ) -> bool:
        """Return whether an action ID is already present in a contract report chain."""

        cursor = await connection.execute(
            """
            select exists (
              select 1
              from atlas.browser_runtime_report
              where execution_contract_id = %s
                and action_id = %s
            ) as action_exists
            """,
            (execution_contract_id, action_id),
        )
        row = await cursor.fetchone()
        return bool(row["action_exists"]) if row is not None else False

    async def list_for_contract(
        self,
        connection: AsyncConnection[DictRow],
        execution_contract_id: UUID,
    ) -> tuple[BrowserRuntimeReport, ...]:
        cursor = await connection.execute(
            """
            select tenant_id, project_id, environment_id, debug_run_id,
                   execution_contract_id, execution_contract_digest,
                   id, report_sequence, report_kind, actor_slot, action_id,
                   payload, payload_digest, previous_chain_digest,
                   chain_digest, occurred_at, recorded_at
            from atlas.browser_runtime_report
            where execution_contract_id = %s
            order by report_sequence
            """,
            (execution_contract_id,),
        )
        return tuple(self._to_domain(row) for row in await cursor.fetchall())

    async def append(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        project_id: UUID,
        environment_id: UUID,
        debug_run_id: UUID,
        report: AppendBrowserRuntimeReport,
        recorded_at: datetime,
    ) -> BrowserRuntimeReport:
        cursor = await connection.execute(
            """
            insert into atlas.browser_runtime_report (
              id, tenant_id, project_id, environment_id, debug_run_id,
              execution_contract_id, execution_contract_digest,
              report_sequence, report_kind, actor_slot, action_id, payload,
              payload_digest, previous_chain_digest, chain_digest,
              occurred_at, recorded_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            returning tenant_id, project_id, environment_id, debug_run_id,
                      execution_contract_id, execution_contract_digest,
                      id, report_sequence, report_kind, actor_slot, action_id,
                      payload, payload_digest, previous_chain_digest,
                      chain_digest, occurred_at, recorded_at
            """,
            (
                report.report_id,
                tenant_id,
                project_id,
                environment_id,
                debug_run_id,
                report.execution_contract_id,
                report.execution_contract_digest,
                report.sequence,
                report.kind.value,
                report.actor_slot,
                report.action_id,
                Jsonb(report.payload),
                report.payload_digest,
                report.previous_chain_digest,
                report.chain_digest,
                report.occurred_at,
                recorded_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("browser runtime report insert did not return a row")
        return self._to_domain(row)

    @staticmethod
    def _to_domain(row: DictRow) -> BrowserRuntimeReport:
        return BrowserRuntimeReport(
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            environment_id=row["environment_id"],
            debug_run_id=row["debug_run_id"],
            value=AppendBrowserRuntimeReport(
                execution_contract_id=row["execution_contract_id"],
                execution_contract_digest=row["execution_contract_digest"],
                report_id=row["id"],
                sequence=row["report_sequence"],
                kind=row["report_kind"],
                actor_slot=row["actor_slot"],
                action_id=row["action_id"],
                payload=row["payload"],
                occurred_at=row["occurred_at"],
                previous_chain_digest=row["previous_chain_digest"],
                payload_digest=row["payload_digest"],
                chain_digest=row["chain_digest"],
            ),
            recorded_at=row["recorded_at"],
        )
