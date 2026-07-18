"""PostgreSQL repository for immutable UnitAttempt execution tickets."""

from __future__ import annotations

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.task import TaskUnitExecutionTicket
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableCreateResult,
    ImmutableFactConflictError,
)

TASK_UNIT_EXECUTION_TICKET_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, execution_unit_id, unit_attempt_id, "
    "schema_version, request_digest, manifest_hash, ordinal, unit_key, "
    "case_version_id, case_content_digest, test_ir_digest, plan_digest, "
    "compiled_digest, attempt_number, execution_profile_version_id, "
    "execution_profile_digest, identity_profile_version_id, identity_profile_digest, "
    "browser_profile_version_id, browser_profile_digest, data_profile_version_id, "
    "data_profile_digest, fixture_blueprint_version_id, fixture_blueprint_digest, "
    "environment_id, environment_revision, allowed_origins, execution_deadline, "
    "ticket_digest, created_at"
)


class TaskExecutionTicketRepository:
    """Persist and replay one secret-free ticket for each physical attempt."""

    async def create(
        self,
        connection: AsyncConnection[DictRow],
        ticket: TaskUnitExecutionTicket,
    ) -> ImmutableCreateResult[TaskUnitExecutionTicket]:
        """Insert a ticket or return its exact immutable replay."""

        cursor = await connection.execute(
            f"""
            insert into atlas.task_unit_execution_ticket (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, schema_version, request_digest, manifest_hash,
              ordinal, unit_key, case_version_id, case_content_digest,
              test_ir_digest, plan_digest, compiled_digest, attempt_number,
              execution_profile_version_id, execution_profile_digest,
              identity_profile_version_id, identity_profile_digest,
              browser_profile_version_id, browser_profile_digest,
              data_profile_version_id, data_profile_digest,
              fixture_blueprint_version_id, fixture_blueprint_digest,
              environment_id, environment_revision, allowed_origins,
              execution_deadline, ticket_digest, created_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            on conflict do nothing
            returning {TASK_UNIT_EXECUTION_TICKET_COLUMNS}
            """,
            (
                ticket.id,
                ticket.tenant_id,
                ticket.project_id,
                ticket.task_run_id,
                ticket.execution_unit_id,
                ticket.unit_attempt_id,
                ticket.schema_version,
                ticket.request_digest,
                ticket.manifest_hash,
                ticket.ordinal,
                ticket.unit_key,
                ticket.case_version_id,
                ticket.case_content_digest,
                ticket.test_ir_digest,
                ticket.plan_digest,
                ticket.compiled_digest,
                ticket.attempt_number,
                ticket.execution_profile_version_id,
                ticket.execution_profile_digest,
                ticket.identity_profile_version_id,
                ticket.identity_profile_digest,
                ticket.browser_profile_version_id,
                ticket.browser_profile_digest,
                ticket.data_profile_version_id,
                ticket.data_profile_digest,
                ticket.fixture_blueprint_version_id,
                ticket.fixture_blueprint_digest,
                ticket.environment_id,
                ticket.environment_revision,
                list(ticket.allowed_origins),
                ticket.execution_deadline,
                ticket.ticket_digest,
                ticket.created_at,
            ),
        )
        row = await cursor.fetchone()
        if row is not None:
            return ImmutableCreateResult(
                ImmutableCreateKind.CREATED,
                TaskUnitExecutionTicket.model_validate(row),
            )
        existing = await self._get_conflict(connection, ticket)
        if existing is None:
            raise RuntimeError("execution ticket conflict did not resolve to a stored row")
        if self._replay_identity(existing) != self._replay_identity(ticket):
            raise ImmutableFactConflictError(
                "unit attempt execution ticket already stores different content"
            )
        return ImmutableCreateResult(ImmutableCreateKind.EXISTING, existing)

    async def get_by_attempt(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
    ) -> TaskUnitExecutionTicket | None:
        """Load the one ticket bound to an exact UnitAttempt."""

        cursor = await connection.execute(
            f"""
            select {TASK_UNIT_EXECUTION_TICKET_COLUMNS}
            from atlas.task_unit_execution_ticket
            where unit_attempt_id = %s
            """,
            (unit_attempt_id,),
        )
        row = await cursor.fetchone()
        return TaskUnitExecutionTicket.model_validate(row) if row is not None else None

    async def get(
        self,
        connection: AsyncConnection[DictRow],
        ticket_id: UUID,
    ) -> TaskUnitExecutionTicket | None:
        """Load one ticket by its opaque immutable identity."""

        cursor = await connection.execute(
            f"""
            select {TASK_UNIT_EXECUTION_TICKET_COLUMNS}
            from atlas.task_unit_execution_ticket
            where id = %s
            """,
            (ticket_id,),
        )
        row = await cursor.fetchone()
        return TaskUnitExecutionTicket.model_validate(row) if row is not None else None

    async def _get_conflict(
        self,
        connection: AsyncConnection[DictRow],
        ticket: TaskUnitExecutionTicket,
    ) -> TaskUnitExecutionTicket | None:
        cursor = await connection.execute(
            f"""
            select {TASK_UNIT_EXECUTION_TICKET_COLUMNS}
            from atlas.task_unit_execution_ticket
            where id = %s or unit_attempt_id = %s
            order by (id = %s) desc
            limit 1
            """,
            (ticket.id, ticket.unit_attempt_id, ticket.id),
        )
        row = await cursor.fetchone()
        return TaskUnitExecutionTicket.model_validate(row) if row is not None else None

    @staticmethod
    def _replay_identity(ticket: TaskUnitExecutionTicket) -> tuple[UUID, UUID, str]:
        return ticket.unit_attempt_id, ticket.tenant_id, ticket.ticket_digest


__all__ = ["TaskExecutionTicketRepository"]
