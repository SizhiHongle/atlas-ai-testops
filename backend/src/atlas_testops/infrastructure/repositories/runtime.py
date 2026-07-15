"""PostgreSQL repository for trusted execution and evidence facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from atlas_testops.domain.case import DebugRun, DebugRunOutcome
from atlas_testops.domain.runtime import (
    EvidenceArtifactInput,
    EvidenceManifest,
    ExecutionContract,
)
from atlas_testops.infrastructure.repositories.debug_runs import DEBUG_RUN_COLUMNS


@dataclass(frozen=True, slots=True)
class FixtureBindingRecord:
    """Database facts required to verify one fixture execution binding."""

    fixture_run_id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    blueprint_version_id: UUID
    blueprint_version_ref: str
    blueprint_content_digest: str
    run_kind: str
    status: str
    execution_id: str
    fixture_plan_digest: str
    fixture_manifest_digest: str
    exports: dict[str, JsonValue]
    execution_deadline: datetime


@dataclass(frozen=True, slots=True)
class ActorBindingRecord:
    """Database-verified lease, role, session, and Fixture actor facts."""

    actor_slot: str
    role_id: UUID
    role_key: str
    role_revision: int
    role_status: str
    account_lease_id: UUID
    account_handle: str
    fencing_token: int
    lease_status: str
    lease_worker_id: str
    lease_execution_id: str
    lease_expires_at: datetime
    lease_max_expires_at: datetime
    browser_context_ref: str
    session_status: str
    session_worker_identity: str
    session_expires_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserContextRestoreRecord:
    """Encrypted session metadata projected only into a protected Worker envelope."""

    actor_slot: str
    browser_context_ref: str
    artifact_id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    lease_id: UUID
    lease_fence: int
    account_id: UUID
    connector_installation_id: UUID
    credential_binding_id: UUID
    allowed_origins: tuple[str, ...]
    object_ref: str
    object_digest: str
    key_version: str
    format_version: Literal["playwright-storage-state/v1"]
    session_status: str
    session_worker_identity: str
    session_expires_at: datetime


class RuntimeRepository:
    """Persist immutable runtime facts after application-level verification."""

    async def get_fixture_binding(
        self,
        connection: AsyncConnection[DictRow],
        fixture_run_id: UUID,
    ) -> FixtureBindingRecord | None:
        cursor = await connection.execute(
            """
            select
              run.id as fixture_run_id,
              run.tenant_id,
              run.project_id,
              run.environment_id,
              run.blueprint_version_id,
              definition.blueprint_key || '@' || version.version
                as blueprint_version_ref,
              version.content_digest as blueprint_content_digest,
              run.run_kind,
              run.status,
              run.execution_id,
              manifest.plan_digest as fixture_plan_digest,
              manifest.manifest_digest as fixture_manifest_digest,
              manifest.manifest -> 'exports' as exports,
              run.execution_deadline
            from atlas.fixture_run run
            join atlas.fixture_manifest manifest
              on manifest.fixture_run_id = run.id
            join atlas.data_blueprint_version version
              on version.id = run.blueprint_version_id
            join atlas.data_blueprint_definition definition
              on definition.id = version.blueprint_id
            where run.id = %s
            for share of run, manifest, version, definition
            """,
            (fixture_run_id,),
        )
        row = await cursor.fetchone()
        return FixtureBindingRecord(**row) if row is not None else None

    async def get_actor_binding(
        self,
        connection: AsyncConnection[DictRow],
        *,
        fixture_run_id: UUID,
        actor_slot: str,
        account_lease_id: UUID,
        browser_context_ref: str,
    ) -> ActorBindingRecord | None:
        cursor = await connection.execute(
            """
            select
              binding.actor_slot,
              role.id as role_id,
              role.role_key,
              role.revision as role_revision,
              role.status as role_status,
              lease.id as account_lease_id,
              lease.account_handle,
              lease.fencing_token,
              lease.status as lease_status,
              lease.worker_id as lease_worker_id,
              lease.execution_id as lease_execution_id,
              lease.expires_at as lease_expires_at,
              lease.max_expires_at as lease_max_expires_at,
              session.browser_context_ref,
              session.status as session_status,
              session.worker_identity as session_worker_identity,
              session.expires_at as session_expires_at
            from atlas.fixture_actor_binding binding
            join atlas.account_lease lease
              on lease.id = binding.account_lease_id
            join atlas.account_pool pool
              on pool.id = lease.pool_id
            join atlas.test_role role
              on role.id = pool.role_id
            join atlas.browser_session_artifact session
              on session.browser_context_ref = %s
             and session.lease_id = lease.id
             and session.lease_fence = lease.fencing_token
            where binding.fixture_run_id = %s
              and binding.actor_slot = %s
              and binding.account_lease_id = %s
            for share of binding, lease, pool, role, session
            """,
            (
                browser_context_ref,
                fixture_run_id,
                actor_slot,
                account_lease_id,
            ),
        )
        row = await cursor.fetchone()
        return ActorBindingRecord(**row) if row is not None else None

    async def create_contract(
        self,
        connection: AsyncConnection[DictRow],
        contract: ExecutionContract,
    ) -> None:
        await connection.execute(
            """
            insert into atlas.execution_contract (
              id, tenant_id, project_id, environment_id, debug_run_id,
              test_case_id, semantic_revision, test_ir_digest, plan_digest,
              compiled_digest, fixture_run_id, fixture_manifest_digest,
              worker_identity, contract, contract_digest, execution_deadline,
              created_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s
            )
            """,
            (
                contract.id,
                contract.tenant_id,
                contract.project_id,
                contract.environment_id,
                contract.debug_run_id,
                contract.test_case_id,
                contract.semantic_revision,
                contract.test_ir_digest,
                contract.plan_digest,
                contract.compiled_digest,
                contract.fixture.fixture_run_id,
                contract.fixture.fixture_manifest_digest,
                contract.worker_identity,
                Jsonb(contract.model_dump(mode="json", by_alias=True)),
                contract.content_digest,
                contract.execution_deadline,
                contract.created_at,
            ),
        )
        async with connection.cursor() as cursor:
            await cursor.executemany(
                """
            insert into atlas.execution_contract_actor_binding (
              execution_contract_id, debug_run_id, tenant_id, project_id,
              environment_id, actor_slot, role_id, role_revision,
              account_lease_id, account_handle, fencing_token,
              browser_context_ref, bound_at
            ) values (
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s
            )
                """,
                [
                    (
                        contract.id,
                        contract.debug_run_id,
                        contract.tenant_id,
                        contract.project_id,
                        contract.environment_id,
                        actor.actor_slot,
                        actor.role_id,
                        actor.role_revision,
                        actor.account_lease_id,
                        actor.account_handle,
                        actor.fencing_token,
                        actor.browser_context_ref,
                        contract.created_at,
                    )
                    for actor in contract.actors
                ],
            )

    async def get_browser_context_restore_records(
        self,
        connection: AsyncConnection[DictRow],
        execution_contract_id: UUID,
    ) -> tuple[BrowserContextRestoreRecord, ...]:
        """Load exact encrypted SessionArtifact metadata for one bound contract."""

        cursor = await connection.execute(
            """
            select binding.actor_slot,
                   binding.browser_context_ref,
                   session.id as artifact_id,
                   session.tenant_id,
                   session.project_id,
                   session.environment_id,
                   session.lease_id,
                   session.lease_fence,
                   session.account_id,
                   session.connector_installation_id,
                   session.credential_binding_id,
                   session.allowed_origins,
                   session.object_ref,
                   session.object_digest,
                   session.key_version,
                   session.format_version,
                   session.status as session_status,
                   session.worker_identity as session_worker_identity,
                   session.expires_at as session_expires_at
            from atlas.execution_contract_actor_binding binding
            join atlas.browser_session_artifact session
              on session.browser_context_ref = binding.browser_context_ref
             and session.lease_id = binding.account_lease_id
             and session.lease_fence = binding.fencing_token
             and session.tenant_id = binding.tenant_id
             and session.project_id = binding.project_id
             and session.environment_id = binding.environment_id
            where binding.execution_contract_id = %s
            order by binding.actor_slot
            for share of binding, session
            """,
            (execution_contract_id,),
        )
        return tuple(
            BrowserContextRestoreRecord(
                actor_slot=row["actor_slot"],
                browser_context_ref=row["browser_context_ref"],
                artifact_id=row["artifact_id"],
                tenant_id=row["tenant_id"],
                project_id=row["project_id"],
                environment_id=row["environment_id"],
                lease_id=row["lease_id"],
                lease_fence=row["lease_fence"],
                account_id=row["account_id"],
                connector_installation_id=row["connector_installation_id"],
                credential_binding_id=row["credential_binding_id"],
                allowed_origins=tuple(row["allowed_origins"]),
                object_ref=row["object_ref"],
                object_digest=row["object_digest"],
                key_version=row["key_version"],
                format_version=row["format_version"],
                session_status=row["session_status"],
                session_worker_identity=row["session_worker_identity"],
                session_expires_at=row["session_expires_at"],
            )
            for row in await cursor.fetchall()
        )

    async def get_contract_for_run(
        self,
        connection: AsyncConnection[DictRow],
        run_id: UUID,
    ) -> ExecutionContract | None:
        cursor = await connection.execute(
            """
            select contract
            from atlas.execution_contract
            where debug_run_id = %s
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        return ExecutionContract.model_validate(row["contract"]) if row is not None else None

    async def bind_run(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: DebugRun,
        contract: ExecutionContract,
    ) -> DebugRun | None:
        cursor = await connection.execute(
            f"""
            update atlas.debug_run
            set lifecycle = 'BINDING',
                started_at = %s,
                execution_contract_id = %s,
                execution_contract_digest = %s,
                revision = revision + 1
            where id = %s
              and revision = %s
              and lifecycle = 'CREATED'
              and cancel_requested_at is null
            returning {DEBUG_RUN_COLUMNS}
            """,
            (
                contract.created_at,
                contract.id,
                contract.content_digest,
                run.id,
                run.revision,
            ),
        )
        row = await cursor.fetchone()
        return DebugRun.model_validate(row) if row is not None else None

    async def transition_run(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: DebugRun,
        expected_lifecycle: str,
        next_lifecycle: str,
    ) -> DebugRun | None:
        cursor = await connection.execute(
            f"""
            update atlas.debug_run
            set lifecycle = %s,
                revision = revision + 1
            where id = %s
              and revision = %s
              and lifecycle = %s
              and execution_contract_id is not null
            returning {DEBUG_RUN_COLUMNS}
            """,
            (next_lifecycle, run.id, run.revision, expected_lifecycle),
        )
        row = await cursor.fetchone()
        return DebugRun.model_validate(row) if row is not None else None

    async def persist_evidence(
        self,
        connection: AsyncConnection[DictRow],
        *,
        contract: ExecutionContract,
        manifest: EvidenceManifest,
        private_artifacts: tuple[EvidenceArtifactInput, ...],
        finalization_command_digest: str,
    ) -> None:
        async with connection.cursor() as cursor:
            await cursor.executemany(
                """
            insert into atlas.assertion_result (
              id, tenant_id, project_id, environment_id, debug_run_id,
              execution_contract_id, assertion_id, node_id, strength, status,
              expected_digest, actual_safe_summary, evaluator_version_ref,
              evidence_refs, observed_at, duration_ms, result, result_digest,
              created_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s
            )
                """,
                [
                    (
                        result.id,
                        manifest.tenant_id,
                        manifest.project_id,
                        manifest.environment_id,
                        manifest.debug_run_id,
                        contract.id,
                        result.assertion_id,
                        result.node_id,
                        result.strength,
                        result.status,
                        result.expected_digest,
                        result.actual_safe_summary,
                        result.evaluator_version_ref,
                        list(result.evidence_refs),
                        result.observed_at,
                        result.duration_ms,
                        Jsonb(result.model_dump(mode="json", by_alias=True)),
                        result.result_digest,
                        manifest.finalized_at,
                    )
                    for result in manifest.assertion_results
                ],
            )
        async with connection.cursor() as cursor:
            await cursor.executemany(
                """
            insert into atlas.evidence_artifact (
              id, tenant_id, project_id, environment_id, debug_run_id,
              execution_contract_id, kind, object_ref, content_digest,
              size_bytes, mime_type, redaction_policy_digest, integrity,
              required, captured_at, created_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s
            )
                """,
                [
                    (
                        artifact.id,
                        manifest.tenant_id,
                        manifest.project_id,
                        manifest.environment_id,
                        manifest.debug_run_id,
                        contract.id,
                        artifact.kind,
                        artifact.object_ref,
                        artifact.content_digest,
                        artifact.size_bytes,
                        artifact.mime_type,
                        artifact.redaction_policy_digest,
                        artifact.integrity,
                        artifact.required,
                        artifact.captured_at,
                        manifest.finalized_at,
                    )
                    for artifact in private_artifacts
                ],
            )
        await connection.execute(
            """
            insert into atlas.evidence_manifest (
              id, tenant_id, project_id, environment_id, debug_run_id,
              execution_contract_id, execution_contract_digest,
              test_ir_digest, plan_digest, fixture_run_id,
              fixture_manifest_digest, outcome, completeness, integrity,
              oracle_results_digest, artifact_manifest_digest,
              event_chain_head_digest, event_count, passed_assertions,
              failed_assertions, inconclusive_assertions, manifest,
              manifest_digest, finalization_command_digest, finalized_at, created_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s
            )
            """,
            (
                manifest.id,
                manifest.tenant_id,
                manifest.project_id,
                manifest.environment_id,
                manifest.debug_run_id,
                manifest.execution_contract_id,
                manifest.execution_contract_digest,
                manifest.test_ir_digest,
                manifest.plan_digest,
                manifest.fixture_run_id,
                manifest.fixture_manifest_digest,
                manifest.outcome,
                manifest.completeness,
                manifest.integrity,
                manifest.oracle_results_digest,
                manifest.artifact_manifest_digest,
                manifest.event_chain_head_digest,
                manifest.event_count,
                manifest.passed_assertions,
                manifest.failed_assertions,
                manifest.inconclusive_assertions,
                Jsonb(manifest.model_dump(mode="json", by_alias=True)),
                manifest.content_digest,
                finalization_command_digest,
                manifest.finalized_at,
                manifest.finalized_at,
            ),
        )

    async def finish_run(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: DebugRun,
        outcome: DebugRunOutcome,
        manifest: EvidenceManifest,
    ) -> DebugRun | None:
        cursor = await connection.execute(
            f"""
            update atlas.debug_run
            set lifecycle = 'TERMINATED',
                outcome = %s,
                evidence_manifest_id = %s,
                evidence_manifest_digest = %s,
                completed_at = %s,
                revision = revision + 1
            where id = %s
              and revision = %s
              and lifecycle = 'FINALIZING'
              and execution_contract_id = %s
              and execution_contract_digest = %s
            returning {DEBUG_RUN_COLUMNS}
            """,
            (
                outcome,
                manifest.id,
                manifest.content_digest,
                manifest.finalized_at,
                run.id,
                run.revision,
                manifest.execution_contract_id,
                manifest.execution_contract_digest,
            ),
        )
        row = await cursor.fetchone()
        return DebugRun.model_validate(row) if row is not None else None

    async def get_evidence_manifest(
        self,
        connection: AsyncConnection[DictRow],
        manifest_id: UUID,
    ) -> EvidenceManifest | None:
        cursor = await connection.execute(
            """
            select manifest
            from atlas.evidence_manifest
            where id = %s
            """,
            (manifest_id,),
        )
        row = await cursor.fetchone()
        return EvidenceManifest.model_validate(row["manifest"]) if row is not None else None

    async def get_evidence_finalization_digest(
        self,
        connection: AsyncConnection[DictRow],
        manifest_id: UUID,
    ) -> str | None:
        cursor = await connection.execute(
            """
            select finalization_command_digest
            from atlas.evidence_manifest
            where id = %s
            """,
            (manifest_id,),
        )
        row = await cursor.fetchone()
        return row["finalization_command_digest"] if row is not None else None
