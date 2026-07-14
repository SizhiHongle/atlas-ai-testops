"""Bounded session artifact expiry and ciphertext destruction service."""

from collections.abc import Callable
from datetime import datetime, timedelta

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.ports.sessions import SessionArtifactVault
from atlas_testops.core.contracts import utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.identity import SessionArtifactRecord, SessionJanitorBatch
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.sessions import SessionRepository
from atlas_testops.infrastructure.session_vault import SessionVaultError


class SessionJanitorService:
    """Claim cleanup work in PostgreSQL and delete ciphertext outside transactions."""

    def __init__(
        self,
        database: Database,
        *,
        session_vault: SessionArtifactVault | None,
        repository: SessionRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        cleanup_claim_ttl: timedelta = timedelta(minutes=2),
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not timedelta(seconds=30) <= cleanup_claim_ttl <= timedelta(minutes=10):
            raise ValueError("cleanup_claim_ttl must be between 30 seconds and ten minutes")
        self._database = database
        self._vault = session_vault
        self._sessions = repository or SessionRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._cleanup_claim_ttl = cleanup_claim_ttl
        self._clock = clock

    async def run_once(
        self,
        actor: ActorContext,
        *,
        worker_identity: str,
        limit: int,
    ) -> SessionJanitorBatch:
        """Expire due records, claim a bounded batch, and delete each object idempotently."""

        if not actor.is_organization_admin():
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="没有 Session 清理权限",
                detail="只有内部 Janitor 或组织管理员可以清理 Session Artifact。",
                status_code=403,
            )
        if self._vault is None:
            raise ApplicationError(
                error_code=ErrorCode.SESSION_UNAVAILABLE,
                title="Session Vault 未配置",
                detail="当前进程不能安全销毁 Session Artifact。",
                status_code=503,
            )
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        now = self._clock()
        async with self._database.transaction(actor.database_context()) as connection:
            expired = await self._sessions.expire_due(
                connection,
                now=now,
                limit=limit,
            )
            claimed = await self._sessions.claim_cleanup(
                connection,
                now=now,
                worker_identity=worker_identity,
                claim_ttl=self._cleanup_claim_ttl,
                limit=limit,
            )
        destroyed = 0
        failed = 0
        for artifact in claimed:
            object_ref = artifact.object_ref
            if object_ref is None:
                failed += 1
                continue
            try:
                await self._vault.delete(object_ref)
            except SessionVaultError:
                failed += 1
                continue
            completed_at = self._clock()
            async with self._database.transaction(actor.database_context()) as connection:
                terminal = await self._sessions.mark_destroyed(
                    connection,
                    artifact_id=artifact.id,
                    worker_identity=worker_identity,
                    now=completed_at,
                )
                if terminal is None:
                    failed += 1
                    continue
                await self._record_destroyed_event(
                    connection,
                    actor=actor,
                    artifact=terminal,
                    occurred_at=completed_at,
                )
                destroyed += 1
        return SessionJanitorBatch(
            expired=len(expired),
            destroyed=destroyed,
            failed=failed,
            observed_at=now,
        )

    async def _record_destroyed_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        artifact: SessionArtifactRecord,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "leaseId": str(artifact.lease_id),
            "fencingToken": artifact.lease_fence,
            "status": artifact.status.value,
            "terminationReason": (
                artifact.termination_reason.value
                if artifact.termination_reason is not None
                else None
            ),
        }
        await self._audit.append(
            connection,
            tenant_id=artifact.tenant_id,
            project_id=artifact.project_id,
            environment_id=artifact.environment_id,
            actor_id=actor.actor_id,
            event_type="browser_session_artifact.destroyed",
            entity_type="browser_session_artifact",
            entity_id=artifact.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=artifact.tenant_id,
                aggregate_type="browser_session_artifact",
                aggregate_id=artifact.id,
                event_type="browser_session_artifact.destroyed",
                occurred_at=occurred_at,
                payload=payload,
            ),
        )
