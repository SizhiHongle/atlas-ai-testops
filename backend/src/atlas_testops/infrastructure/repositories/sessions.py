"""PostgreSQL repository for encrypted session metadata and cleanup claims."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.application.ports.sessions import SealedSessionArtifact
from atlas_testops.domain.identity import (
    AccountLease,
    CredentialAuthMethod,
    ManualActionReason,
    ManualActionTicketRecord,
    SessionArtifactFailureCode,
    SessionArtifactRecord,
    SessionArtifactTerminationReason,
)

SESSION_COLUMNS = (
    "id, tenant_id, project_id, environment_id, lease_id, account_id, "
    "connector_installation_id, credential_binding_id, lease_fence, "
    "worker_identity, browser_context_ref, allowed_origins, auth_strength, "
    "status, object_ref, object_digest, object_size_bytes, key_version, "
    "format_version, refreshable, account_revision, connector_revision, "
    "credential_revision, safe_summary, failure_code, created_at, "
    "attempt_expires_at, ready_at, expires_at, terminated_at, "
    "termination_reason, cleanup_claimed_at, cleanup_worker_identity, "
    "destroyed_at, revision, updated_at"
)
QUALIFIED_SESSION_COLUMNS = ", ".join(
    f"artifact.{column.strip()}" for column in SESSION_COLUMNS.split(",")
)
TICKET_COLUMNS = (
    "id, tenant_id, project_id, environment_id, lease_id, account_id, "
    "connector_installation_id, lease_fence, worker_identity, allowed_origins, "
    "auth_method, reason, status, safe_reason, created_at, expires_at, "
    "completed_at, terminated_at, revision, updated_at"
)


@dataclass(frozen=True, slots=True)
class SessionDependencySnapshot:
    """Immutable revisions and identity expectations captured before external I/O."""

    account_revision: int
    connector_revision: int
    credential_revision: int
    credential_binding_id: UUID
    external_subject_id: str | None
    identity_fingerprint: str
    role_key: str


class SessionRepository:
    """Maintain two-phase session creation without holding DB connections over I/O."""

    async def get_dependency_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        *,
        lease: AccountLease,
        connector_installation_id: UUID,
        credential_binding_id: UUID | None,
        auth_method: CredentialAuthMethod,
        now: datetime,
    ) -> SessionDependencySnapshot | None:
        cursor = await connection.execute(
            """
            select a.revision as account_revision,
                   ci.revision as connector_revision,
                   c.revision as credential_revision,
                   c.id as credential_binding_id,
                   a.external_subject_id,
                   a.identity_fingerprint,
                   r.role_key
            from atlas.test_account a
            join atlas.account_pool p on p.id = a.pool_id
            join atlas.test_role r on r.id = p.role_id
            join atlas.connector_installation ci
              on ci.id = a.connector_installation_id
            join atlas.credential_binding c on c.account_id = a.id
            where a.id = %s
              and a.connector_installation_id = %s
              and (%s::uuid is null or c.id = %s)
              and c.auth_method = %s
              and c.purpose = 'LOGIN'
              and c.status = 'ACTIVE'
              and (c.expires_at is null or c.expires_at > %s)
              and a.lifecycle_status = 'ACTIVE'
              and a.health_status = 'HEALTHY'
              and a.operational_status = 'READY'
              and a.sync_status not in ('CONFLICT', 'TOMBSTONED')
              and a.lease_epoch = %s
              and a.identity_fingerprint is not null
              and ci.status = 'ACTIVE'
              and r.status = 'ACTIVE'
            for share of a, p, r, ci, c
            """,
            (
                lease.account_id,
                connector_installation_id,
                credential_binding_id,
                credential_binding_id,
                auth_method,
                now,
                lease.fencing_token,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return SessionDependencySnapshot(
            account_revision=row["account_revision"],
            connector_revision=row["connector_revision"],
            credential_revision=row["credential_revision"],
            credential_binding_id=row["credential_binding_id"],
            external_subject_id=row["external_subject_id"],
            identity_fingerprint=row["identity_fingerprint"],
            role_key=row["role_key"],
        )

    async def get_live_for_lease(
        self,
        connection: AsyncConnection[DictRow],
        lease_id: UUID,
    ) -> SessionArtifactRecord | None:
        cursor = await connection.execute(
            f"""
            select {SESSION_COLUMNS}
            from atlas.browser_session_artifact
            where lease_id = %s and status in ('CREATING', 'READY')
            order by created_at desc, id desc
            limit 1
            """,
            (lease_id,),
        )
        row = await cursor.fetchone()
        return SessionArtifactRecord.model_validate(row) if row is not None else None

    async def get_by_id(
        self,
        connection: AsyncConnection[DictRow],
        artifact_id: UUID,
    ) -> SessionArtifactRecord | None:
        cursor = await connection.execute(
            f"select {SESSION_COLUMNS} from atlas.browser_session_artifact where id = %s",
            (artifact_id,),
        )
        row = await cursor.fetchone()
        return SessionArtifactRecord.model_validate(row) if row is not None else None

    async def expire_stale_for_lease(
        self,
        connection: AsyncConnection[DictRow],
        *,
        lease_id: UUID,
        now: datetime,
    ) -> tuple[SessionArtifactRecord, ...]:
        cursor = await connection.execute(
            f"""
            update atlas.browser_session_artifact as artifact
            set status = case when status = 'READY' then 'EXPIRED' else 'FAILED' end,
                failure_code = case
                  when status = 'CREATING' then 'STALE_SNAPSHOT'
                  else null
                end,
                safe_summary = case
                  when status = 'CREATING' then 'session creation attempt expired'
                  else safe_summary
                end,
                terminated_at = %s,
                termination_reason = case
                  when status = 'CREATING' then 'STALE_SNAPSHOT'
                  else 'TTL_EXPIRED'
                end,
                revision = artifact.revision + 1
            where lease_id = %s
              and (
                (status = 'CREATING' and attempt_expires_at <= %s)
                or (status = 'READY' and expires_at <= %s)
              )
            returning {QUALIFIED_SESSION_COLUMNS}
            """,
            (now, lease_id, now, now),
        )
        return tuple(SessionArtifactRecord.model_validate(row) for row in await cursor.fetchall())

    async def revoke_live_for_lease(
        self,
        connection: AsyncConnection[DictRow],
        *,
        lease_id: UUID,
        reason: SessionArtifactTerminationReason,
        now: datetime,
    ) -> tuple[SessionArtifactRecord, ...]:
        cursor = await connection.execute(
            f"""
            update atlas.browser_session_artifact as artifact
            set status = 'REVOKED', terminated_at = %s,
                termination_reason = %s, revision = artifact.revision + 1
            where lease_id = %s and status in ('CREATING', 'READY')
            returning {QUALIFIED_SESSION_COLUMNS}
            """,
            (now, reason, lease_id),
        )
        return tuple(SessionArtifactRecord.model_validate(row) for row in await cursor.fetchall())

    async def reserve(
        self,
        connection: AsyncConnection[DictRow],
        *,
        artifact_id: UUID,
        browser_context_ref: str,
        object_ref: str,
        lease: AccountLease,
        connector_installation_id: UUID,
        snapshot: SessionDependencySnapshot,
        allowed_origins: tuple[str, ...],
        created_at: datetime,
        attempt_expires_at: datetime,
        expires_at: datetime,
    ) -> SessionArtifactRecord | None:
        cursor = await connection.execute(
            f"""
            insert into atlas.browser_session_artifact (
              id, tenant_id, project_id, environment_id, lease_id, account_id,
              connector_installation_id, credential_binding_id, lease_fence,
              worker_identity, browser_context_ref, allowed_origins, object_ref,
              account_revision, connector_revision, credential_revision,
              safe_summary, created_at, attempt_expires_at, expires_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            on conflict (lease_id) where status in ('CREATING', 'READY')
              do nothing
            returning {SESSION_COLUMNS}
            """,
            (
                artifact_id,
                lease.tenant_id,
                lease.project_id,
                lease.environment_id,
                lease.id,
                lease.account_id,
                connector_installation_id,
                snapshot.credential_binding_id,
                lease.fencing_token,
                lease.worker_id,
                browser_context_ref,
                list(allowed_origins),
                object_ref,
                snapshot.account_revision,
                snapshot.connector_revision,
                snapshot.credential_revision,
                "session creation reserved",
                created_at,
                attempt_expires_at,
                expires_at,
            ),
        )
        row = await cursor.fetchone()
        return SessionArtifactRecord.model_validate(row) if row is not None else None

    async def finalize_ready(
        self,
        connection: AsyncConnection[DictRow],
        *,
        artifact: SessionArtifactRecord,
        sealed: SealedSessionArtifact,
        auth_strength: tuple[CredentialAuthMethod, ...],
        now: datetime,
    ) -> SessionArtifactRecord | None:
        cursor = await connection.execute(
            f"""
            update atlas.browser_session_artifact as artifact
            set status = 'READY', auth_strength = %s, object_digest = %s,
                object_size_bytes = %s, key_version = %s,
                safe_summary = 'authenticated browser session is ready',
                ready_at = %s, revision = artifact.revision + 1
            where artifact.id = %s
              and artifact.status = 'CREATING'
              and artifact.revision = %s
              and artifact.attempt_expires_at > %s
              and artifact.expires_at > %s
              and artifact.object_ref = %s
              and exists (
                select 1
                from atlas.account_lease l
                join atlas.test_account a on a.id = l.account_id
                join atlas.connector_installation ci
                  on ci.id = artifact.connector_installation_id
                join atlas.credential_binding c
                  on c.id = artifact.credential_binding_id
                where l.id = artifact.lease_id
                  and l.status = 'ACTIVE'
                  and l.fencing_token = artifact.lease_fence
                  and l.worker_id = artifact.worker_identity
                  and l.expires_at > %s
                  and l.max_expires_at > %s
                  and a.id = artifact.account_id
                  and a.lease_epoch = artifact.lease_fence
                  and a.revision = artifact.account_revision
                  and a.lifecycle_status = 'ACTIVE'
                  and a.health_status = 'HEALTHY'
                  and a.operational_status = 'READY'
                  and a.sync_status not in ('CONFLICT', 'TOMBSTONED')
                  and ci.status = 'ACTIVE'
                  and ci.revision = artifact.connector_revision
                  and c.account_id = artifact.account_id
                  and c.status = 'ACTIVE'
                  and c.revision = artifact.credential_revision
                  and (c.expires_at is null or c.expires_at > %s)
              )
            returning {QUALIFIED_SESSION_COLUMNS}
            """,
            (
                [method.value for method in auth_strength],
                sealed.object_digest,
                sealed.object_size_bytes,
                sealed.key_version,
                now,
                artifact.id,
                artifact.revision,
                now,
                now,
                sealed.object_ref,
                now,
                now,
                now,
            ),
        )
        row = await cursor.fetchone()
        return SessionArtifactRecord.model_validate(row) if row is not None else None

    async def fail_creation(
        self,
        connection: AsyncConnection[DictRow],
        *,
        artifact_id: UUID,
        failure_code: SessionArtifactFailureCode,
        termination_reason: SessionArtifactTerminationReason,
        safe_summary: str,
        now: datetime,
        sealed: SealedSessionArtifact | None = None,
        auth_strength: tuple[CredentialAuthMethod, ...] = (),
    ) -> SessionArtifactRecord | None:
        cursor = await connection.execute(
            f"""
            update atlas.browser_session_artifact as artifact
            set status = 'FAILED', auth_strength = %s,
                object_digest = coalesce(%s, object_digest),
                object_size_bytes = coalesce(%s, object_size_bytes),
                key_version = coalesce(%s, key_version),
                failure_code = %s, safe_summary = %s,
                terminated_at = %s, termination_reason = %s,
                revision = artifact.revision + 1
            where id = %s and status = 'CREATING'
            returning {QUALIFIED_SESSION_COLUMNS}
            """,
            (
                [method.value for method in auth_strength],
                sealed.object_digest if sealed is not None else None,
                sealed.object_size_bytes if sealed is not None else None,
                sealed.key_version if sealed is not None else None,
                failure_code,
                safe_summary,
                now,
                termination_reason,
                artifact_id,
            ),
        )
        row = await cursor.fetchone()
        return SessionArtifactRecord.model_validate(row) if row is not None else None

    async def get_open_ticket_for_lease(
        self,
        connection: AsyncConnection[DictRow],
        lease_id: UUID,
    ) -> ManualActionTicketRecord | None:
        cursor = await connection.execute(
            f"""
            select {TICKET_COLUMNS}
            from atlas.auth_action_ticket
            where lease_id = %s and status = 'OPEN'
            order by created_at desc, id desc
            limit 1
            """,
            (lease_id,),
        )
        row = await cursor.fetchone()
        return ManualActionTicketRecord.model_validate(row) if row is not None else None

    async def expire_ticket_for_lease(
        self,
        connection: AsyncConnection[DictRow],
        *,
        lease_id: UUID,
        now: datetime,
    ) -> ManualActionTicketRecord | None:
        cursor = await connection.execute(
            f"""
            update atlas.auth_action_ticket
            set status = 'EXPIRED', terminated_at = %s, revision = revision + 1
            where lease_id = %s and status = 'OPEN' and expires_at <= %s
            returning {TICKET_COLUMNS}
            """,
            (now, lease_id, now),
        )
        row = await cursor.fetchone()
        return ManualActionTicketRecord.model_validate(row) if row is not None else None

    async def cancel_open_ticket(
        self,
        connection: AsyncConnection[DictRow],
        *,
        lease_id: UUID,
        now: datetime,
    ) -> ManualActionTicketRecord | None:
        cursor = await connection.execute(
            f"""
            update atlas.auth_action_ticket
            set status = 'CANCELLED', terminated_at = %s, revision = revision + 1
            where lease_id = %s and status = 'OPEN'
            returning {TICKET_COLUMNS}
            """,
            (now, lease_id),
        )
        row = await cursor.fetchone()
        return ManualActionTicketRecord.model_validate(row) if row is not None else None

    async def create_manual_ticket(
        self,
        connection: AsyncConnection[DictRow],
        *,
        ticket_id: UUID,
        lease: AccountLease,
        connector_installation_id: UUID,
        allowed_origins: tuple[str, ...],
        auth_method: CredentialAuthMethod,
        reason: ManualActionReason,
        safe_reason: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> ManualActionTicketRecord | None:
        cursor = await connection.execute(
            f"""
            insert into atlas.auth_action_ticket (
              id, tenant_id, project_id, environment_id, lease_id, account_id,
              connector_installation_id, lease_fence, worker_identity,
              allowed_origins, auth_method, reason, safe_reason, created_at,
              expires_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            on conflict (lease_id) where status = 'OPEN' do nothing
            returning {TICKET_COLUMNS}
            """,
            (
                ticket_id,
                lease.tenant_id,
                lease.project_id,
                lease.environment_id,
                lease.id,
                lease.account_id,
                connector_installation_id,
                lease.fencing_token,
                lease.worker_id,
                list(allowed_origins),
                auth_method,
                reason,
                safe_reason,
                created_at,
                expires_at,
            ),
        )
        row = await cursor.fetchone()
        return ManualActionTicketRecord.model_validate(row) if row is not None else None

    async def expire_due(
        self,
        connection: AsyncConnection[DictRow],
        *,
        now: datetime,
        limit: int,
    ) -> tuple[SessionArtifactRecord, ...]:
        cursor = await connection.execute(
            f"""
            with candidates as (
              select id
              from atlas.browser_session_artifact
              where (
                status = 'CREATING' and attempt_expires_at <= %s
              ) or (
                status = 'READY' and expires_at <= %s
              )
              order by least(attempt_expires_at, expires_at), id
              limit %s
              for update skip locked
            )
            update atlas.browser_session_artifact as artifact
            set status = case when artifact.status = 'READY' then 'EXPIRED' else 'FAILED' end,
                failure_code = case
                  when artifact.status = 'CREATING' then 'STALE_SNAPSHOT'
                  else null
                end,
                safe_summary = case
                  when artifact.status = 'CREATING' then 'session creation attempt expired'
                  else artifact.safe_summary
                end,
                terminated_at = %s,
                termination_reason = case
                  when artifact.status = 'CREATING' then 'STALE_SNAPSHOT'
                  else 'TTL_EXPIRED'
                end,
                revision = artifact.revision + 1
            from candidates
            where artifact.id = candidates.id
            returning {QUALIFIED_SESSION_COLUMNS}
            """,
            (now, now, limit, now),
        )
        artifacts = tuple(
            SessionArtifactRecord.model_validate(row) for row in await cursor.fetchall()
        )
        await connection.execute(
            """
            with candidates as (
              select id
              from atlas.auth_action_ticket
              where status = 'OPEN' and expires_at <= %s
              order by expires_at, id
              limit %s
              for update skip locked
            )
            update atlas.auth_action_ticket as ticket
            set status = 'EXPIRED', terminated_at = %s,
                revision = ticket.revision + 1
            from candidates
            where ticket.id = candidates.id
            """,
            (now, limit, now),
        )
        return artifacts

    async def claim_cleanup(
        self,
        connection: AsyncConnection[DictRow],
        *,
        now: datetime,
        worker_identity: str,
        claim_ttl: timedelta,
        limit: int,
    ) -> tuple[SessionArtifactRecord, ...]:
        claim_expired_before = now - claim_ttl
        cursor = await connection.execute(
            f"""
            with candidates as (
              select id
              from atlas.browser_session_artifact
              where status in ('REVOKED', 'EXPIRED', 'FAILED')
                 or (
                   status = 'DESTROYING'
                   and cleanup_claimed_at <= %s
                 )
              order by coalesce(terminated_at, created_at), id
              limit %s
              for update skip locked
            )
            update atlas.browser_session_artifact as artifact
            set status = 'DESTROYING', cleanup_claimed_at = %s,
                cleanup_worker_identity = %s,
                revision = artifact.revision + 1
            from candidates
            where artifact.id = candidates.id
            returning {QUALIFIED_SESSION_COLUMNS}
            """,
            (claim_expired_before, limit, now, worker_identity),
        )
        return tuple(SessionArtifactRecord.model_validate(row) for row in await cursor.fetchall())

    async def mark_destroyed(
        self,
        connection: AsyncConnection[DictRow],
        *,
        artifact_id: UUID,
        worker_identity: str,
        now: datetime,
    ) -> SessionArtifactRecord | None:
        cursor = await connection.execute(
            f"""
            update atlas.browser_session_artifact as artifact
            set status = 'DESTROYED', destroyed_at = %s,
                revision = artifact.revision + 1
            where id = %s and status = 'DESTROYING'
              and cleanup_worker_identity = %s
            returning {QUALIFIED_SESSION_COLUMNS}
            """,
            (now, artifact_id, worker_identity),
        )
        row = await cursor.fetchone()
        return SessionArtifactRecord.model_validate(row) if row is not None else None
