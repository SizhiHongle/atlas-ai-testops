"""Authorized evidence manifests, bounded read grants, and verified byte reads."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.ports.evidence import (
    EvidenceObjectDescriptor,
    EvidenceObjectIntegrityError,
    EvidenceObjectMissingError,
    EvidenceObjectReader,
    EvidenceStoreUnavailableError,
)
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.runtime import (
    EvidenceIntegrity,
    EvidenceManifest,
    EvidenceReadGrant,
    EvidenceReadPurpose,
    IssueEvidenceReadGrant,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.debug_runs import DebugRunRepository
from atlas_testops.infrastructure.repositories.evidence import (
    EvidenceArtifactScopeRecord,
    EvidenceRepository,
)
from atlas_testops.infrastructure.repositories.runtime import RuntimeRepository

_INLINE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})


@dataclass(frozen=True, slots=True)
class EvidenceArtifactContent:
    """Complete verified bytes and fixed presentation metadata for one response."""

    artifact_id: UUID
    payload: bytes = field(repr=False)
    mime_type: str
    purpose: EvidenceReadPurpose
    content_disposition: str
    filename: str


class EvidenceService:
    """Keep authorization and database work separate from object-store I/O."""

    def __init__(
        self,
        database: Database,
        object_reader: EvidenceObjectReader | None,
        *,
        grant_ttl: timedelta,
        maximum_reads: int,
        evidence_repository: EvidenceRepository | None = None,
        debug_run_repository: DebugRunRepository | None = None,
        runtime_repository: RuntimeRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        if not timedelta(seconds=10) <= grant_ttl <= timedelta(minutes=2):
            raise ValueError("evidence read grant TTL must be 10-120 seconds")
        if not 1 <= maximum_reads <= 32:
            raise ValueError("evidence read grant maximum reads must be 1-32")
        self._database = database
        self._object_reader = object_reader
        self._grant_ttl = grant_ttl
        self._maximum_reads = maximum_reads
        self._evidence = evidence_repository or EvidenceRepository()
        self._runs = debug_run_repository or DebugRunRepository()
        self._runtime = runtime_repository or RuntimeRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def get_manifest(
        self,
        actor: ActorContext,
        run_id: UUID,
    ) -> EvidenceManifest:
        """Return the safe root projection without any object-store location."""

        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._runs.get_run(connection, run_id)
            if run is None or not actor.can_read_project(run.project_id):
                raise self._not_found("DebugRun 不存在或不可见。")
            if run.evidence_manifest_id is None or run.evidence_manifest_digest is None:
                raise self._not_ready("DebugRun 尚未封存 EvidenceManifest。")
            manifest = await self._runtime.get_evidence_manifest(
                connection,
                run.evidence_manifest_id,
            )
            if (
                manifest is None
                or manifest.debug_run_id != run.id
                or manifest.tenant_id != run.tenant_id
                or manifest.project_id != run.project_id
                or manifest.content_digest != run.evidence_manifest_digest
            ):
                raise self._integrity_failed("EvidenceManifest 与 DebugRun 根引用不一致。")
            return manifest

    async def issue_read_grant(
        self,
        actor: ActorContext,
        run_id: UUID,
        artifact_id: UUID,
        command: IssueEvidenceReadGrant,
    ) -> EvidenceReadGrant:
        """Mint a hash-only, actor/session-bound grant for one finalized artifact."""

        self._require_reader()
        actor_id = actor.actor_id
        if actor_id is None:
            raise self._forbidden("Evidence 读取授权需要可审计的 Actor 身份。")
        if actor.session_id is None and not actor.development_override:
            raise self._forbidden("Evidence 读取授权必须绑定有效 Platform Session。")
        issued_at = utc_now()
        expires_at = issued_at + self._grant_ttl
        grant_id = new_entity_id()
        read_token = f"evr_{token_urlsafe(48)}"
        token_hash = self.hash_read_token(read_token)
        purpose = command.purpose
        async with self._database.transaction(actor.database_context()) as connection:
            artifact = await self._evidence.get_manifest_artifact(
                connection,
                debug_run_id=run_id,
                artifact_id=artifact_id,
            )
            if artifact is None or not actor.can_read_project(artifact.project_id):
                raise self._not_found("Evidence Artifact 不存在或不可见。")
            if artifact.integrity != EvidenceIntegrity.VERIFIED.value:
                raise self._not_ready("Evidence Artifact 尚未通过完整性验证。")
            if actor.session_id is not None and actor.current_project_id != artifact.project_id:
                raise self._forbidden("请切换到 Evidence 所属 Project 后重新签发读取授权。")
            await self._evidence.lock_read_grant_scope(
                connection,
                tenant_id=actor.tenant_id,
                artifact_id=artifact.artifact_id,
                issued_to_actor_id=actor_id,
                platform_session_id=actor.session_id,
                purpose=purpose,
            )
            await self._evidence.revoke_active_read_grants(
                connection,
                tenant_id=actor.tenant_id,
                artifact_id=artifact.artifact_id,
                issued_to_actor_id=actor_id,
                platform_session_id=actor.session_id,
                purpose=purpose,
                revoked_at=issued_at,
            )
            record = await self._evidence.issue_read_grant(
                connection,
                grant_id=grant_id,
                token_hash=token_hash,
                artifact=artifact,
                issued_to_actor_id=actor_id,
                platform_session_id=actor.session_id,
                purpose=purpose,
                max_reads=self._maximum_reads,
                created_at=issued_at,
                expires_at=expires_at,
            )
            payload: dict[str, JsonValue] = {
                "artifactId": str(artifact.artifact_id),
                "debugRunId": str(artifact.debug_run_id),
                "purpose": command.purpose.value,
                "maxReads": record.max_reads,
                "expiresAt": record.expires_at.isoformat(),
            }
            await self._audit.append(
                connection,
                tenant_id=artifact.tenant_id,
                project_id=artifact.project_id,
                environment_id=artifact.environment_id,
                actor_id=actor_id,
                event_type="evidence_read_grant.issued",
                entity_type="evidence_read_grant",
                entity_id=record.id,
                occurred_at=issued_at,
                payload=payload,
                request_id=actor.request_id,
            )
            await self._outbox.append(
                connection,
                DomainEvent(
                    tenant_id=artifact.tenant_id,
                    aggregate_type="evidence_read_grant",
                    aggregate_id=record.id,
                    event_type="evidence_read_grant.issued",
                    occurred_at=issued_at,
                    payload=payload,
                ),
            )
        return EvidenceReadGrant(
            id=record.id,
            artifact_id=record.artifact_id,
            purpose=command.purpose,
            read_token=read_token,
            issued_at=record.created_at,
            expires_at=record.expires_at,
            max_reads=record.max_reads,
        )

    async def read_content(
        self,
        actor: ActorContext,
        artifact_id: UUID,
        *,
        read_token: str,
        purpose: EvidenceReadPurpose,
    ) -> EvidenceArtifactContent:
        """Consume one read, close the transaction, then verify all bytes."""

        reader = self._require_reader()
        actor_id = actor.actor_id
        if actor_id is None or not self._valid_token_shape(read_token):
            raise self._invalid_grant()
        redeemed_at = utc_now()
        repository_purpose = purpose
        async with self._database.transaction(actor.database_context()) as connection:
            grant = await self._evidence.redeem_read_grant(
                connection,
                tenant_id=actor.tenant_id,
                token_hash=self.hash_read_token(read_token),
                artifact_id=artifact_id,
                issued_to_actor_id=actor_id,
                platform_session_id=actor.session_id,
                purpose=repository_purpose,
                redeemed_at=redeemed_at,
            )
            if grant is None:
                raise self._invalid_grant()
            artifact = await self._evidence.get_manifest_artifact(
                connection,
                debug_run_id=grant.debug_run_id,
                artifact_id=grant.artifact_id,
            )
            if (
                artifact is None
                or artifact.tenant_id != grant.tenant_id
                or artifact.project_id != grant.project_id
                or artifact.execution_contract_id != grant.execution_contract_id
                or not actor.can_read_project(grant.project_id)
            ):
                raise self._invalid_grant()
            if artifact.integrity != EvidenceIntegrity.VERIFIED.value:
                raise self._not_ready("Evidence Artifact 尚未通过完整性验证。")
            await self._append_read_audit(
                connection,
                actor=actor,
                artifact=artifact,
                grant_id=grant.id,
                purpose=purpose,
                event_type="evidence_read_grant.redeemed",
                read_count=grant.read_count,
                occurred_at=redeemed_at,
            )

        descriptor = EvidenceObjectDescriptor(
            artifact_id=artifact.artifact_id,
            tenant_id=artifact.tenant_id,
            project_id=artifact.project_id,
            environment_id=artifact.environment_id,
            debug_run_id=artifact.debug_run_id,
            execution_contract_id=artifact.execution_contract_id,
            object_ref=artifact.object_ref,
            content_digest=artifact.content_digest,
            size_bytes=artifact.size_bytes,
            mime_type=artifact.mime_type,
        )
        try:
            payload = await reader.read_verified(descriptor)
        except (EvidenceObjectMissingError, EvidenceObjectIntegrityError) as error:
            await self._record_read_failure(
                actor=actor,
                artifact=artifact,
                grant_id=grant.id,
                purpose=purpose,
                event_type="evidence_artifact.integrity_failed",
            )
            raise self._integrity_failed(
                "Evidence 对象缺失或字节与不可变 Receipt 不一致。"
            ) from error
        except EvidenceStoreUnavailableError as error:
            await self._record_read_failure(
                actor=actor,
                artifact=artifact,
                grant_id=grant.id,
                purpose=purpose,
                event_type="evidence_artifact.read_unavailable",
            )
            raise ApplicationError(
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                title="Evidence Object Store 不可用",
                detail="Evidence 字节暂时无法读取，请稍后重试。",
                status_code=503,
            ) from error

        disposition = (
            "inline"
            if purpose is EvidenceReadPurpose.INLINE and artifact.mime_type in _INLINE_MIME_TYPES
            else "attachment"
        )
        return EvidenceArtifactContent(
            artifact_id=artifact.artifact_id,
            payload=payload,
            mime_type=artifact.mime_type,
            purpose=purpose,
            content_disposition=disposition,
            filename=self._safe_filename(artifact),
        )

    async def _record_read_failure(
        self,
        *,
        actor: ActorContext,
        artifact: EvidenceArtifactScopeRecord,
        grant_id: UUID,
        purpose: EvidenceReadPurpose,
        event_type: str,
    ) -> None:
        occurred_at = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            await self._append_read_audit(
                connection,
                actor=actor,
                artifact=artifact,
                grant_id=grant_id,
                purpose=purpose,
                event_type=event_type,
                read_count=None,
                occurred_at=occurred_at,
            )

    async def _append_read_audit(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        artifact: EvidenceArtifactScopeRecord,
        grant_id: UUID,
        purpose: EvidenceReadPurpose,
        event_type: str,
        read_count: int | None,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "grantId": str(grant_id),
            "artifactId": str(artifact.artifact_id),
            "debugRunId": str(artifact.debug_run_id),
            "purpose": purpose.value,
        }
        if read_count is not None:
            payload["readCount"] = read_count
        await self._audit.append(
            connection,
            tenant_id=artifact.tenant_id,
            project_id=artifact.project_id,
            environment_id=artifact.environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="evidence_artifact",
            entity_id=artifact.artifact_id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )

    def _require_reader(self) -> EvidenceObjectReader:
        if self._object_reader is None:
            raise ApplicationError(
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                title="Evidence Reader 未配置",
                detail="当前 API 实例未连接可信 Evidence Object Store。",
                status_code=503,
            )
        return self._object_reader

    @staticmethod
    def hash_read_token(read_token: str) -> str:
        """Persist and compare only an irreversible token digest."""

        return sha256(read_token.encode()).hexdigest()

    @staticmethod
    def _valid_token_shape(read_token: str) -> bool:
        return (
            read_token.startswith("evr_")
            and 36 <= len(read_token) <= 204
            and all(character.isalnum() or character in "_-" for character in read_token[4:])
        )

    @staticmethod
    def _safe_filename(artifact: EvidenceArtifactScopeRecord) -> str:
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
        }.get(artifact.mime_type, "bin")
        return f"atlas-evidence-{artifact.artifact_id.hex}.{extension}"

    @staticmethod
    def _not_found(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="资源不存在",
            detail=detail,
            status_code=404,
        )

    @staticmethod
    def _forbidden(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.FORBIDDEN,
            title="操作被拒绝",
            detail=detail,
            status_code=403,
        )

    @staticmethod
    def _invalid_grant() -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.AUTHENTICATION_FAILED,
            title="Evidence Read Grant 无效",
            detail="Evidence 读取授权无效、已过期、已撤销或已耗尽。",
            status_code=401,
            headers={"WWW-Authenticate": "Atlas-Evidence"},
        )

    @staticmethod
    def _not_ready(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.EVIDENCE_NOT_READY,
            title="Evidence 尚不可读取",
            detail=detail,
            status_code=409,
        )

    @staticmethod
    def _integrity_failed(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.EVIDENCE_INTEGRITY_FAILED,
            title="Evidence 完整性校验失败",
            detail=detail,
            status_code=409,
        )
