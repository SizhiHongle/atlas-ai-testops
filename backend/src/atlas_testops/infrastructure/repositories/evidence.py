"""PostgreSQL repository for private evidence reads and bounded grants."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.runtime import EvidenceReadPurpose


@dataclass(frozen=True, slots=True)
class EvidenceArtifactScopeRecord:
    """Private storage metadata for one artifact rooted in a finalized manifest."""

    artifact_id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    debug_run_id: UUID
    execution_contract_id: UUID
    kind: str
    object_ref: str = field(repr=False)
    content_digest: str
    size_bytes: int
    mime_type: str
    redaction_policy_digest: str
    integrity: str
    required: bool
    captured_at: datetime
    manifest_id: UUID
    manifest_digest: str
    outcome: str
    finalized_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceReadGrantRecord:
    """Hash-only, actor-bound authority to read one private artifact."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    debug_run_id: UUID
    execution_contract_id: UUID
    artifact_id: UUID
    issued_to_actor_id: UUID
    platform_session_id: UUID | None
    purpose: EvidenceReadPurpose
    max_reads: int
    read_count: int
    created_at: datetime
    expires_at: datetime
    last_read_at: datetime | None
    revoked_at: datetime | None
    revision: int


_ARTIFACT_SCOPE_COLUMNS = """
  artifact.id as artifact_id,
  artifact.tenant_id,
  artifact.project_id,
  artifact.environment_id,
  artifact.debug_run_id,
  artifact.execution_contract_id,
  artifact.kind,
  artifact.object_ref,
  artifact.content_digest,
  artifact.size_bytes,
  artifact.mime_type,
  artifact.redaction_policy_digest,
  artifact.integrity,
  artifact.required,
  artifact.captured_at,
  manifest.id as manifest_id,
  manifest.manifest_digest,
  manifest.outcome,
  manifest.finalized_at
"""

_READ_GRANT_COLUMNS = """
  id, tenant_id, project_id, environment_id, debug_run_id,
  execution_contract_id, artifact_id, issued_to_actor_id,
  platform_session_id, purpose, max_reads, read_count, created_at,
  expires_at, last_read_at, revoked_at, revision
"""


class EvidenceRepository:
    """Read finalized artifact scope and persist short-lived read authority."""

    async def lock_read_grant_scope(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        artifact_id: UUID,
        issued_to_actor_id: UUID,
        platform_session_id: UUID | None,
        purpose: EvidenceReadPurpose,
    ) -> None:
        """Serialize replacement grants for one exact scope within the transaction."""

        scope_key = ":".join(
            (
                str(tenant_id),
                str(artifact_id),
                str(issued_to_actor_id),
                str(platform_session_id) if platform_session_id is not None else "development",
                purpose.value,
            )
        )
        await connection.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (scope_key,),
        )

    async def get_manifest_artifact(
        self,
        connection: AsyncConnection[DictRow],
        *,
        debug_run_id: UUID,
        artifact_id: UUID,
    ) -> EvidenceArtifactScopeRecord | None:
        """Load one artifact only when it belongs to the run's final root."""

        cursor = await connection.execute(
            f"""
            select {_ARTIFACT_SCOPE_COLUMNS}
            from atlas.evidence_artifact artifact
            join atlas.evidence_manifest manifest
              on manifest.execution_contract_id = artifact.execution_contract_id
             and manifest.debug_run_id = artifact.debug_run_id
             and manifest.tenant_id = artifact.tenant_id
             and manifest.project_id = artifact.project_id
             and manifest.environment_id = artifact.environment_id
            join atlas.debug_run run
              on run.id = artifact.debug_run_id
             and run.tenant_id = artifact.tenant_id
             and run.project_id = artifact.project_id
             and run.environment_id = artifact.environment_id
            where artifact.debug_run_id = %s
              and artifact.id = %s
              and run.lifecycle = 'TERMINATED'
              and run.evidence_manifest_id = manifest.id
              and run.evidence_manifest_digest = manifest.manifest_digest
              and exists (
                select 1
                from jsonb_array_elements(manifest.manifest -> 'artifacts') item
                where item ->> 'id' = artifact.id::text
                  and item ->> 'integrity' = artifact.integrity
              )
            """,
            (debug_run_id, artifact_id),
        )
        row = await cursor.fetchone()
        return self._to_artifact_scope(row) if row is not None else None

    async def list_manifest_artifacts(
        self,
        connection: AsyncConnection[DictRow],
        *,
        manifest_id: UUID,
    ) -> tuple[EvidenceArtifactScopeRecord, ...]:
        """List verified private artifacts included in one finalized manifest."""

        cursor = await connection.execute(
            f"""
            select {_ARTIFACT_SCOPE_COLUMNS}
            from atlas.evidence_manifest manifest
            join atlas.evidence_artifact artifact
              on artifact.execution_contract_id = manifest.execution_contract_id
             and artifact.debug_run_id = manifest.debug_run_id
             and artifact.tenant_id = manifest.tenant_id
             and artifact.project_id = manifest.project_id
             and artifact.environment_id = manifest.environment_id
            join atlas.debug_run run
              on run.id = manifest.debug_run_id
             and run.tenant_id = manifest.tenant_id
             and run.project_id = manifest.project_id
             and run.environment_id = manifest.environment_id
            where manifest.id = %s
              and artifact.integrity = 'VERIFIED'
              and run.lifecycle = 'TERMINATED'
              and run.evidence_manifest_id = manifest.id
              and run.evidence_manifest_digest = manifest.manifest_digest
              and exists (
                select 1
                from jsonb_array_elements(manifest.manifest -> 'artifacts') item
                where item ->> 'id' = artifact.id::text
                  and item ->> 'integrity' = 'VERIFIED'
              )
            order by artifact.captured_at, artifact.id
            """,
            (manifest_id,),
        )
        return tuple(self._to_artifact_scope(row) for row in await cursor.fetchall())

    async def issue_read_grant(
        self,
        connection: AsyncConnection[DictRow],
        *,
        grant_id: UUID,
        token_hash: str,
        artifact: EvidenceArtifactScopeRecord,
        issued_to_actor_id: UUID,
        platform_session_id: UUID | None,
        purpose: EvidenceReadPurpose,
        max_reads: int,
        created_at: datetime,
        expires_at: datetime,
    ) -> EvidenceReadGrantRecord:
        """Insert one exact-scope grant; plaintext tokens never reach PostgreSQL."""

        cursor = await connection.execute(
            f"""
            insert into atlas.evidence_read_grant (
              id, token_hash, tenant_id, project_id, environment_id,
              debug_run_id, execution_contract_id, artifact_id,
              issued_to_actor_id, platform_session_id, purpose, max_reads,
              created_at, expires_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s
            )
            returning {_READ_GRANT_COLUMNS}
            """,
            (
                grant_id,
                token_hash,
                artifact.tenant_id,
                artifact.project_id,
                artifact.environment_id,
                artifact.debug_run_id,
                artifact.execution_contract_id,
                artifact.artifact_id,
                issued_to_actor_id,
                platform_session_id,
                purpose,
                max_reads,
                created_at,
                expires_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("evidence read grant insert did not return a row")
        return self._to_read_grant(row)

    async def redeem_read_grant(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        token_hash: str,
        artifact_id: UUID,
        issued_to_actor_id: UUID,
        platform_session_id: UUID | None,
        purpose: EvidenceReadPurpose,
        redeemed_at: datetime,
    ) -> EvidenceReadGrantRecord | None:
        """Atomically consume one read without holding a lock during object I/O."""

        cursor = await connection.execute(
            f"""
            update atlas.evidence_read_grant read_grant
            set read_count = read_count + 1,
                last_read_at = %s,
                revision = revision + 1
            where read_grant.tenant_id = %s
              and read_grant.token_hash = %s
              and read_grant.artifact_id = %s
              and read_grant.issued_to_actor_id = %s
              and read_grant.platform_session_id is not distinct from %s
              and read_grant.purpose = %s
              and read_grant.revoked_at is null
              and read_grant.created_at <= %s
              and read_grant.expires_at > %s
              and read_grant.read_count < read_grant.max_reads
              and (
                read_grant.platform_session_id is null
                or exists (
                  select 1
                  from atlas.platform_session session
                  where session.id = read_grant.platform_session_id
                    and session.user_id = read_grant.issued_to_actor_id
                    and session.tenant_id = read_grant.tenant_id
                    and session.project_id = read_grant.project_id
                    and session.revoked_at is null
                    and session.idle_expires_at > %s
                    and session.absolute_expires_at > %s
                )
              )
            returning {_READ_GRANT_COLUMNS}
            """,
            (
                redeemed_at,
                tenant_id,
                token_hash,
                artifact_id,
                issued_to_actor_id,
                platform_session_id,
                purpose,
                redeemed_at,
                redeemed_at,
                redeemed_at,
                redeemed_at,
            ),
        )
        row = await cursor.fetchone()
        return self._to_read_grant(row) if row is not None else None

    async def revoke_active_read_grants(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        artifact_id: UUID,
        issued_to_actor_id: UUID,
        platform_session_id: UUID | None,
        purpose: EvidenceReadPurpose,
        revoked_at: datetime,
    ) -> int:
        """Revoke prior unexpired grants for the same exact read scope."""

        cursor = await connection.execute(
            """
            update atlas.evidence_read_grant
            set revoked_at = %s,
                revision = revision + 1
            where tenant_id = %s
              and artifact_id = %s
              and issued_to_actor_id = %s
              and platform_session_id is not distinct from %s
              and purpose = %s
              and revoked_at is null
              and expires_at > %s
            """,
            (
                revoked_at,
                tenant_id,
                artifact_id,
                issued_to_actor_id,
                platform_session_id,
                purpose,
                revoked_at,
            ),
        )
        return max(cursor.rowcount, 0)

    async def revoke_read_grant(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        grant_id: UUID,
        artifact_id: UUID,
        issued_to_actor_id: UUID,
        revoked_at: datetime,
    ) -> EvidenceReadGrantRecord | None:
        """Revoke one actor-owned grant without deleting its audit fact."""

        cursor = await connection.execute(
            f"""
            update atlas.evidence_read_grant
            set revoked_at = %s,
                revision = revision + 1
            where id = %s
              and tenant_id = %s
              and artifact_id = %s
              and issued_to_actor_id = %s
              and revoked_at is null
            returning {_READ_GRANT_COLUMNS}
            """,
            (
                revoked_at,
                grant_id,
                tenant_id,
                artifact_id,
                issued_to_actor_id,
            ),
        )
        row = await cursor.fetchone()
        return self._to_read_grant(row) if row is not None else None

    @staticmethod
    def _to_artifact_scope(row: DictRow) -> EvidenceArtifactScopeRecord:
        return EvidenceArtifactScopeRecord(**row)

    @staticmethod
    def _to_read_grant(row: DictRow) -> EvidenceReadGrantRecord:
        return EvidenceReadGrantRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            environment_id=row["environment_id"],
            debug_run_id=row["debug_run_id"],
            execution_contract_id=row["execution_contract_id"],
            artifact_id=row["artifact_id"],
            issued_to_actor_id=row["issued_to_actor_id"],
            platform_session_id=row["platform_session_id"],
            purpose=EvidenceReadPurpose(row["purpose"]),
            max_reads=row["max_reads"],
            read_count=row["read_count"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_read_at=row["last_read_at"],
            revoked_at=row["revoked_at"],
            revision=row["revision"],
        )
