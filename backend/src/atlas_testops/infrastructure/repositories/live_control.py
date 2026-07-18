"""PostgreSQL repository for fenced UnitAttempt live control."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.domain.runtime import (
    ControlLease,
    LiveActionGrant,
    LiveControlCommand,
    LiveControlEvent,
    LiveSession,
    LiveSessionState,
)

LIVE_SESSION_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, execution_unit_id, unit_attempt_id, "
    "execution_ticket_id, execution_ticket_digest, browser_session_id, schema_version, "
    "state, control_epoch, fencing_token, browser_revision, human_influenced, "
    "revision, created_at, updated_at, closed_at"
)
CONTROL_LEASE_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, execution_unit_id, unit_attempt_id, "
    "live_session_id, schema_version, owner_type, owner_id, control_epoch, "
    "fencing_token, state, expires_at, reason, created_by, created_at, updated_at, "
    "released_at"
)
CONTROL_LEASE_RETURNING = ", ".join(
    f"item.{column.strip()}" for column in CONTROL_LEASE_COLUMNS.split(",")
)
LIVE_CONTROL_COMMAND_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, execution_unit_id, unit_attempt_id, "
    "live_session_id, schema_version, command_type, client_mutation_id, reason, "
    "requested_ttl_sec, expected_control_epoch, accepted_session_revision, status, "
    "requested_by, created_at, updated_at, applied_at, resulting_control_epoch, "
    "resulting_fencing_token, checkpoint_digest"
)
LIVE_ACTION_GRANT_COLUMNS = (
    "grant_id, tenant_id, project_id, task_run_id, execution_unit_id, "
    "unit_attempt_id, live_session_id, control_lease_id, schema_version, action_id, "
    "proposal_digest, browser_session_id, page_id, page_revision, control_epoch, "
    "fencing_token, owner_type, owner_id, allowed_adapter, expires_at, "
    "max_executions, policy_digest, state, created_at, consumed_at, completed_at, "
    "revoked_at, receipt_id, execution_status, resulting_page_revision"
)
LIVE_ACTION_GRANT_RETURNING = ", ".join(
    f"item.{column.strip()}" for column in LIVE_ACTION_GRANT_COLUMNS.split(",")
)
LIVE_CONTROL_EVENT_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, execution_unit_id, unit_attempt_id, "
    "live_session_id, seq, event_type, control_epoch, fencing_token, payload, "
    "occurred_at"
)


class LiveControlRepository:
    """Persist control facts while application services own transition policy."""

    async def get_session_by_attempt(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
        *,
        for_update: bool = False,
    ) -> LiveSession | None:
        lock = " for update" if for_update else ""
        cursor = await connection.execute(
            f"""
            select {LIVE_SESSION_COLUMNS}
            from atlas.live_session
            where unit_attempt_id = %s{lock}
            """,
            (unit_attempt_id,),
        )
        row = await cursor.fetchone()
        return LiveSession.model_validate(row) if row is not None else None

    async def get_session_by_id(
        self,
        connection: AsyncConnection[DictRow],
        live_session_id: UUID,
        *,
        for_update: bool = False,
    ) -> LiveSession | None:
        lock = " for update" if for_update else ""
        cursor = await connection.execute(
            f"""
            select {LIVE_SESSION_COLUMNS}
            from atlas.live_session
            where id = %s{lock}
            """,
            (live_session_id,),
        )
        row = await cursor.fetchone()
        return LiveSession.model_validate(row) if row is not None else None

    async def create_session(
        self,
        connection: AsyncConnection[DictRow],
        session: LiveSession,
    ) -> LiveSession | None:
        cursor = await connection.execute(
            f"""
            insert into atlas.live_session (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, execution_ticket_id, execution_ticket_digest,
              browser_session_id, schema_version, state, control_epoch,
              fencing_token, browser_revision, human_influenced, revision,
              created_at, updated_at, closed_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            on conflict (unit_attempt_id) do nothing
            returning {LIVE_SESSION_COLUMNS}
            """,
            (
                session.id,
                session.tenant_id,
                session.project_id,
                session.task_run_id,
                session.execution_unit_id,
                session.unit_attempt_id,
                session.execution_ticket_id,
                session.execution_ticket_digest,
                session.browser_session_id,
                session.schema_version,
                session.state,
                session.control_epoch,
                session.fencing_token,
                session.browser_revision,
                session.human_influenced,
                session.revision,
                session.created_at,
                session.updated_at,
                session.closed_at,
            ),
        )
        row = await cursor.fetchone()
        return LiveSession.model_validate(row) if row is not None else None

    async def get_current_lease(
        self,
        connection: AsyncConnection[DictRow],
        live_session_id: UUID,
        *,
        for_update: bool = False,
    ) -> ControlLease | None:
        lock = " for update" if for_update else ""
        cursor = await connection.execute(
            f"""
            select {CONTROL_LEASE_COLUMNS}
            from atlas.control_lease
            where live_session_id = %s
              and state in ('ACTIVE', 'REVOKING')
            order by control_epoch desc
            limit 1{lock}
            """,
            (live_session_id,),
        )
        row = await cursor.fetchone()
        return ControlLease.model_validate(row) if row is not None else None

    async def create_lease(
        self,
        connection: AsyncConnection[DictRow],
        lease: ControlLease,
    ) -> ControlLease:
        cursor = await connection.execute(
            f"""
            insert into atlas.control_lease (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, live_session_id, schema_version, owner_type,
              owner_id, control_epoch, fencing_token, state, expires_at,
              reason, created_by, created_at, updated_at, released_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            returning {CONTROL_LEASE_COLUMNS}
            """,
            (
                lease.id,
                lease.tenant_id,
                lease.project_id,
                lease.task_run_id,
                lease.execution_unit_id,
                lease.unit_attempt_id,
                lease.live_session_id,
                lease.schema_version,
                lease.owner_type,
                lease.owner_id,
                lease.control_epoch,
                lease.fencing_token,
                lease.state,
                lease.expires_at,
                lease.reason,
                lease.created_by,
                lease.created_at,
                lease.updated_at,
                lease.released_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("ControlLease insert returned no row")
        return ControlLease.model_validate(row)

    async def heartbeat_lease(
        self,
        connection: AsyncConnection[DictRow],
        *,
        session: LiveSession,
        lease: ControlLease,
        expires_at: datetime,
        now: datetime,
    ) -> ControlLease | None:
        cursor = await connection.execute(
            f"""
            update atlas.control_lease as item
            set expires_at = %s, updated_at = %s
            from atlas.live_session session
            where item.id = %s
              and item.state = 'ACTIVE'
              and item.expires_at > %s
              and item.expires_at < %s
              and item.control_epoch = %s
              and item.fencing_token = %s
              and session.id = item.live_session_id
              and session.id = %s
              and session.state = 'AGENT_CONTROLLED'
              and session.control_epoch = item.control_epoch
              and session.fencing_token = item.fencing_token
            returning {CONTROL_LEASE_RETURNING}
            """,
            (
                expires_at,
                now,
                lease.id,
                now,
                expires_at,
                lease.control_epoch,
                lease.fencing_token,
                session.id,
            ),
        )
        row = await cursor.fetchone()
        return ControlLease.model_validate(row) if row is not None else None

    async def claim_expired_session_ids(
        self,
        connection: AsyncConnection[DictRow],
        *,
        now: datetime,
        limit: int,
    ) -> list[UUID]:
        cursor = await connection.execute(
            """
            select session.id as live_session_id
            from atlas.live_session session
            join atlas.control_lease lease
              on lease.live_session_id = session.id
             and lease.state in ('ACTIVE', 'REVOKING')
            where lease.expires_at <= %s
              and session.state in (
                'AGENT_CONTROLLED', 'QUIESCING',
                'HUMAN_CONTROLLED', 'RECONCILING'
              )
            order by lease.expires_at, session.id
            limit %s
            for update of session, lease skip locked
            """,
            (now, limit),
        )
        return [UUID(str(row["live_session_id"])) for row in await cursor.fetchall()]

    async def expire_current_control(
        self,
        connection: AsyncConnection[DictRow],
        *,
        session: LiveSession,
        lease: ControlLease,
        now: datetime,
    ) -> tuple[LiveSession, ControlLease] | None:
        lease_cursor = await connection.execute(
            f"""
            update atlas.control_lease
            set state = 'EXPIRED', released_at = %s, updated_at = %s
            where id = %s
              and live_session_id = %s
              and state in ('ACTIVE', 'REVOKING')
              and expires_at <= %s
              and control_epoch = %s
              and fencing_token = %s
            returning {CONTROL_LEASE_COLUMNS}
            """,
            (
                now,
                now,
                lease.id,
                session.id,
                now,
                session.control_epoch,
                session.fencing_token,
            ),
        )
        lease_row = await lease_cursor.fetchone()
        if lease_row is None:
            return None
        await connection.execute(
            """
            update atlas.live_action_grant
            set state = 'REVOKED', revoked_at = %s
            where live_session_id = %s and state = 'ISSUED'
            """,
            (now, session.id),
        )
        await connection.execute(
            """
            update atlas.live_control_command
            set status = 'REJECTED', updated_at = %s
            where live_session_id = %s and status = 'PENDING'
            """,
            (now, session.id),
        )
        session_cursor = await connection.execute(
            f"""
            update atlas.live_session
            set state = 'NO_CONTROLLER',
                control_epoch = control_epoch + 1,
                fencing_token = fencing_token + 1,
                revision = revision + 1,
                updated_at = %s
            where id = %s
              and revision = %s
              and control_epoch = %s
              and fencing_token = %s
            returning {LIVE_SESSION_COLUMNS}
            """,
            (
                now,
                session.id,
                session.revision,
                session.control_epoch,
                session.fencing_token,
            ),
        )
        session_row = await session_cursor.fetchone()
        if session_row is None:
            raise RuntimeError("LiveSession expiry reconciliation lost its fence")
        return (
            LiveSession.model_validate(session_row),
            ControlLease.model_validate(lease_row),
        )

    async def get_pending_command(
        self,
        connection: AsyncConnection[DictRow],
        live_session_id: UUID,
        *,
        for_update: bool = False,
    ) -> LiveControlCommand | None:
        lock = " for update" if for_update else ""
        cursor = await connection.execute(
            f"""
            select {LIVE_CONTROL_COMMAND_COLUMNS}
            from atlas.live_control_command
            where live_session_id = %s and status = 'PENDING'{lock}
            """,
            (live_session_id,),
        )
        row = await cursor.fetchone()
        return LiveControlCommand.model_validate(row) if row is not None else None

    async def get_command(
        self,
        connection: AsyncConnection[DictRow],
        command_id: UUID,
    ) -> LiveControlCommand | None:
        cursor = await connection.execute(
            f"""
            select {LIVE_CONTROL_COMMAND_COLUMNS}
            from atlas.live_control_command
            where id = %s
            """,
            (command_id,),
        )
        row = await cursor.fetchone()
        return LiveControlCommand.model_validate(row) if row is not None else None

    async def get_command_by_mutation(
        self,
        connection: AsyncConnection[DictRow],
        *,
        live_session_id: UUID,
        client_mutation_id: str,
    ) -> LiveControlCommand | None:
        cursor = await connection.execute(
            f"""
            select {LIVE_CONTROL_COMMAND_COLUMNS}
            from atlas.live_control_command
            where live_session_id = %s and client_mutation_id = %s
            """,
            (live_session_id, client_mutation_id),
        )
        row = await cursor.fetchone()
        return LiveControlCommand.model_validate(row) if row is not None else None

    async def create_command(
        self,
        connection: AsyncConnection[DictRow],
        command: LiveControlCommand,
    ) -> LiveControlCommand:
        cursor = await connection.execute(
            f"""
            insert into atlas.live_control_command (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, live_session_id, schema_version, command_type,
              client_mutation_id, reason, requested_ttl_sec,
              expected_control_epoch, accepted_session_revision, status,
              requested_by, created_at, updated_at, applied_at,
              resulting_control_epoch, resulting_fencing_token, checkpoint_digest
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            returning {LIVE_CONTROL_COMMAND_COLUMNS}
            """,
            (
                command.id,
                command.tenant_id,
                command.project_id,
                command.task_run_id,
                command.execution_unit_id,
                command.unit_attempt_id,
                command.live_session_id,
                command.schema_version,
                command.command_type,
                command.client_mutation_id,
                command.reason,
                command.requested_ttl_sec,
                command.expected_control_epoch,
                command.accepted_session_revision,
                command.status,
                command.requested_by,
                command.created_at,
                command.updated_at,
                command.applied_at,
                command.resulting_control_epoch,
                command.resulting_fencing_token,
                command.checkpoint_digest,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("LiveControlCommand insert returned no row")
        return LiveControlCommand.model_validate(row)

    async def request_transition(
        self,
        connection: AsyncConnection[DictRow],
        *,
        session: LiveSession,
        state: LiveSessionState,
        now: datetime,
        revoke_current_lease: bool,
    ) -> LiveSession:
        if revoke_current_lease:
            await connection.execute(
                """
                update atlas.control_lease
                set state = 'REVOKING', updated_at = %s
                where live_session_id = %s and state = 'ACTIVE'
                """,
                (now, session.id),
            )
            await connection.execute(
                """
                update atlas.live_action_grant
                set state = 'REVOKED', revoked_at = %s
                where live_session_id = %s and state = 'ISSUED'
                """,
                (now, session.id),
            )
        cursor = await connection.execute(
            f"""
            update atlas.live_session
            set state = %s, revision = revision + 1, updated_at = %s
            where id = %s and revision = %s and control_epoch = %s
            returning {LIVE_SESSION_COLUMNS}
            """,
            (
                state,
                now,
                session.id,
                session.revision,
                session.control_epoch,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("LiveSession control request lost its revision")
        return LiveSession.model_validate(row)

    async def has_inflight_action(
        self,
        connection: AsyncConnection[DictRow],
        live_session_id: UUID,
    ) -> bool:
        cursor = await connection.execute(
            """
            select exists (
              select 1 from atlas.live_action_grant
              where live_session_id = %s and state = 'CONSUMED'
            ) as value
            """,
            (live_session_id,),
        )
        row = await cursor.fetchone()
        return bool(row["value"]) if row is not None else False

    async def apply_transition(
        self,
        connection: AsyncConnection[DictRow],
        *,
        session: LiveSession,
        command: LiveControlCommand,
        state: LiveSessionState,
        browser_revision: int,
        human_influenced: bool,
        checkpoint_digest: str,
        now: datetime,
    ) -> tuple[LiveSession, LiveControlCommand]:
        await connection.execute(
            """
            update atlas.control_lease
            set state = 'RELEASED', released_at = %s, updated_at = %s
            where live_session_id = %s and state in ('ACTIVE', 'REVOKING')
            """,
            (now, now, session.id),
        )
        next_epoch = session.control_epoch + 1
        next_fence = session.fencing_token + 1
        session_cursor = await connection.execute(
            f"""
            update atlas.live_session
            set state = %s, control_epoch = %s, fencing_token = %s,
                browser_revision = %s, human_influenced = %s,
                revision = revision + 1, updated_at = %s
            where id = %s and revision = %s
              and control_epoch = %s and fencing_token = %s
            returning {LIVE_SESSION_COLUMNS}
            """,
            (
                state,
                next_epoch,
                next_fence,
                browser_revision,
                human_influenced,
                now,
                session.id,
                session.revision,
                session.control_epoch,
                session.fencing_token,
            ),
        )
        session_row = await session_cursor.fetchone()
        if session_row is None:
            raise RuntimeError("LiveSession safe-point transition lost its fence")
        command_cursor = await connection.execute(
            f"""
            update atlas.live_control_command
            set status = 'APPLIED', updated_at = %s, applied_at = %s,
                resulting_control_epoch = %s, resulting_fencing_token = %s,
                checkpoint_digest = %s
            where id = %s and status = 'PENDING'
            returning {LIVE_CONTROL_COMMAND_COLUMNS}
            """,
            (
                now,
                now,
                next_epoch,
                next_fence,
                checkpoint_digest,
                command.id,
            ),
        )
        command_row = await command_cursor.fetchone()
        if command_row is None:
            raise RuntimeError("LiveControlCommand acknowledgement was already consumed")
        return (
            LiveSession.model_validate(session_row),
            LiveControlCommand.model_validate(command_row),
        )

    async def create_action_grant(
        self,
        connection: AsyncConnection[DictRow],
        grant: LiveActionGrant,
    ) -> LiveActionGrant | None:
        cursor = await connection.execute(
            f"""
            insert into atlas.live_action_grant (
              grant_id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, live_session_id, control_lease_id, schema_version,
              action_id, proposal_digest, browser_session_id, page_id,
              page_revision, control_epoch, fencing_token, owner_type, owner_id,
              allowed_adapter, expires_at, max_executions, policy_digest, state,
              created_at, consumed_at, completed_at, revoked_at, receipt_id,
              execution_status, resulting_page_revision
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            on conflict (unit_attempt_id, action_id) do nothing
            returning {LIVE_ACTION_GRANT_COLUMNS}
            """,
            (
                grant.grant_id,
                grant.tenant_id,
                grant.project_id,
                grant.task_run_id,
                grant.execution_unit_id,
                grant.unit_attempt_id,
                grant.live_session_id,
                grant.control_lease_id,
                grant.schema_version,
                grant.action_id,
                grant.proposal_digest,
                grant.browser_session_id,
                grant.page_id,
                grant.page_revision,
                grant.control_epoch,
                grant.fencing_token,
                grant.owner_type,
                grant.owner_id,
                grant.allowed_adapter,
                grant.expires_at,
                grant.max_executions,
                grant.policy_digest,
                grant.state,
                grant.created_at,
                grant.consumed_at,
                grant.completed_at,
                grant.revoked_at,
                grant.receipt_id,
                grant.execution_status,
                grant.resulting_page_revision,
            ),
        )
        row = await cursor.fetchone()
        return LiveActionGrant.model_validate(row) if row is not None else None

    async def get_action_grant(
        self,
        connection: AsyncConnection[DictRow],
        grant_id: UUID,
        *,
        for_update: bool = False,
    ) -> LiveActionGrant | None:
        lock = " for update" if for_update else ""
        cursor = await connection.execute(
            f"""
            select {LIVE_ACTION_GRANT_COLUMNS}
            from atlas.live_action_grant
            where grant_id = %s{lock}
            """,
            (grant_id,),
        )
        row = await cursor.fetchone()
        return LiveActionGrant.model_validate(row) if row is not None else None

    async def get_action_grant_by_action(
        self,
        connection: AsyncConnection[DictRow],
        *,
        unit_attempt_id: UUID,
        action_id: UUID,
    ) -> LiveActionGrant | None:
        cursor = await connection.execute(
            f"""
            select {LIVE_ACTION_GRANT_COLUMNS}
            from atlas.live_action_grant
            where unit_attempt_id = %s and action_id = %s
            """,
            (unit_attempt_id, action_id),
        )
        row = await cursor.fetchone()
        return LiveActionGrant.model_validate(row) if row is not None else None

    async def consume_action_grant(
        self,
        connection: AsyncConnection[DictRow],
        *,
        grant: LiveActionGrant,
        now: datetime,
    ) -> LiveActionGrant | None:
        cursor = await connection.execute(
            f"""
            update atlas.live_action_grant as item
            set state = 'CONSUMED', consumed_at = %s
            from atlas.live_session session, atlas.control_lease lease
            where item.grant_id = %s
              and item.state = 'ISSUED'
              and item.expires_at > %s
              and session.id = item.live_session_id
              and session.state in ('AGENT_CONTROLLED', 'HUMAN_CONTROLLED')
              and session.control_epoch = item.control_epoch
              and session.fencing_token = item.fencing_token
              and lease.id = item.control_lease_id
              and lease.state = 'ACTIVE'
              and lease.expires_at > %s
              and lease.control_epoch = item.control_epoch
              and lease.fencing_token = item.fencing_token
            returning {LIVE_ACTION_GRANT_RETURNING}
            """,
            (now, grant.grant_id, now, now),
        )
        row = await cursor.fetchone()
        return LiveActionGrant.model_validate(row) if row is not None else None

    async def complete_action_grant(
        self,
        connection: AsyncConnection[DictRow],
        *,
        grant: LiveActionGrant,
        receipt_id: UUID,
        execution_status: str,
        resulting_page_revision: int,
        now: datetime,
    ) -> LiveActionGrant | None:
        cursor = await connection.execute(
            f"""
            update atlas.live_action_grant
            set state = 'COMPLETED', completed_at = %s, receipt_id = %s,
                execution_status = %s, resulting_page_revision = %s
            where grant_id = %s and state = 'CONSUMED'
              and control_epoch = %s and fencing_token = %s
            returning {LIVE_ACTION_GRANT_COLUMNS}
            """,
            (
                now,
                receipt_id,
                execution_status,
                resulting_page_revision,
                grant.grant_id,
                grant.control_epoch,
                grant.fencing_token,
            ),
        )
        row = await cursor.fetchone()
        return LiveActionGrant.model_validate(row) if row is not None else None

    async def advance_browser_revision(
        self,
        connection: AsyncConnection[DictRow],
        *,
        session: LiveSession,
        browser_revision: int,
        now: datetime,
    ) -> LiveSession:
        cursor = await connection.execute(
            f"""
            update atlas.live_session
            set browser_revision = %s, revision = revision + 1, updated_at = %s
            where id = %s and revision = %s
              and control_epoch = %s and fencing_token = %s
            returning {LIVE_SESSION_COLUMNS}
            """,
            (
                browser_revision,
                now,
                session.id,
                session.revision,
                session.control_epoch,
                session.fencing_token,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("LiveSession browser revision update lost its fence")
        return LiveSession.model_validate(row)

    async def append_event(
        self,
        connection: AsyncConnection[DictRow],
        event: LiveControlEvent,
    ) -> LiveControlEvent:
        cursor = await connection.execute(
            f"""
            insert into atlas.live_control_event (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, live_session_id, seq, event_type, control_epoch,
              fencing_token, payload, occurred_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            returning {LIVE_CONTROL_EVENT_COLUMNS}
            """,
            (
                event.id,
                event.tenant_id,
                event.project_id,
                event.task_run_id,
                event.execution_unit_id,
                event.unit_attempt_id,
                event.live_session_id,
                event.seq,
                event.event_type,
                event.control_epoch,
                event.fencing_token,
                Jsonb(event.payload),
                event.occurred_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("LiveControlEvent insert returned no row")
        return LiveControlEvent.model_validate(row)

    async def next_event_seq(
        self,
        connection: AsyncConnection[DictRow],
        live_session_id: UUID,
    ) -> int:
        cursor = await connection.execute(
            """
            select coalesce(max(seq), 0) + 1 as value
            from atlas.live_control_event
            where live_session_id = %s
            """,
            (live_session_id,),
        )
        row = await cursor.fetchone()
        return int(row["value"]) if row is not None else 1

    async def get_environment_kind(
        self,
        connection: AsyncConnection[DictRow],
        environment_id: UUID,
    ) -> str | None:
        cursor = await connection.execute(
            "select kind from atlas.environment where id = %s",
            (environment_id,),
        )
        row = await cursor.fetchone()
        return str(row["kind"]) if row is not None else None


__all__ = ["LiveControlRepository"]
