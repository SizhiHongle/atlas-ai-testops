"""PostgreSQL repository for durable fixture execution facts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from atlas_testops.domain.fixture import (
    DataAtomVersion,
    DataBlueprintVersion,
    DataNodeAttempt,
    DataNodeAttemptStatus,
    DataNodeRun,
    DataNodeRunRecord,
    DataNodeRunStatus,
    FixtureActorBinding,
    FixtureActorBindingRecord,
    FixtureCleanupState,
    FixtureFailureCategory,
    FixtureManifest,
    FixtureManifestRecord,
    FixtureResourcePage,
    FixtureRun,
    FixtureRunDetail,
    FixtureRunRecord,
    FixtureRunStatus,
    FixtureValidationEvidence,
    ResourceOwnership,
    ResourceRecord,
    ResourceRecordInternal,
    StartFixtureRun,
    ValidationEvidenceSubject,
)
from atlas_testops.domain.identity import LeaseReleaseReason, ReleaseAccountLease
from atlas_testops.infrastructure.repositories.leases import LeaseRepository

RUN_COLUMNS = (
    "id, tenant_id, project_id, environment_id, blueprint_version_id, run_kind, "
    "execution_id, plan_digest, input_digest, status, cleanup_state, "
    "temporal_workflow_id, requested_by, failure_category, failure_code, "
    "failure_detail, execution_deadline, requested_at, started_at, ready_at, "
    "finished_at, released_at, revision, updated_at"
)
RUN_RECORD_COLUMNS = f"{RUN_COLUMNS}, compiled_plan, run_inputs, cleanup_policy"
BINDING_COLUMNS = (
    "fixture_run_id, actor_slot, account_lease_id, fencing_token, "
    "connector_installation_id, bound_at"
)
NODE_COLUMNS = (
    "id, fixture_run_id, node_id, atom_version_id, actor_slot, execution_level, "
    "status, attempt_count, output_digest, failure_category, failure_code, "
    "failure_detail, started_at, finished_at, revision, updated_at"
)
NODE_RECORD_COLUMNS = f"{NODE_COLUMNS}, atom_id, logical_idempotency_key, inputs, outputs"
ATTEMPT_COLUMNS = (
    "id, fixture_run_id, data_node_run_id, attempt_number, status, "
    "failure_category, failure_code, failure_detail, provider_request_id, "
    "started_at, finished_at, updated_at"
)
RESOURCE_COLUMNS = (
    "id, fixture_run_id, data_node_run_id, connector_installation_id, "
    "resource_handle, resource_type, ownership, status, expires_at, "
    "cleanup_generation, created_at, cleaned_at, revision, updated_at"
)
RESOURCE_INTERNAL_COLUMNS = (
    f"{RESOURCE_COLUMNS}, data_node_attempt_id, opaque_ref, "
    "cleanup_operation_key, cleanup_operation_version"
)


@dataclass(frozen=True, slots=True)
class FixtureLeaseSnapshot:
    """Lease and connector facts validated before freezing actor bindings."""

    account_lease_id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    execution_id: str
    worker_id: str
    account_handle: str
    fencing_token: int
    lease_status: str
    lease_expires_at: datetime
    connector_installation_id: UUID
    connector_adapter_key: str
    connector_configuration_ref: str
    connector_status: str
    connector_health_state: str | None
    connector_revision: int


class FixtureRunRepository:
    """Persist immutable inputs and advance guarded runtime lifecycles."""

    async def get_blueprint_version_for_share(
        self,
        connection: AsyncConnection[DictRow],
        version_id: UUID,
    ) -> DataBlueprintVersion | None:
        cursor = await connection.execute(
            """
            select id, tenant_id, project_id, blueprint_id, version, status,
                   contract, content_digest, static_validation_state,
                   runtime_validation_state, cleanup_validation_state,
                   validated_at, compiled_plan, plan_digest, compile_issues,
                   compiled_at, published_at, published_by, revision,
                   created_at, updated_at
            from atlas.data_blueprint_version
            where id = %s
            for share
            """,
            (version_id,),
        )
        row = await cursor.fetchone()
        return DataBlueprintVersion.model_validate(row) if row is not None else None

    async def get_atom_versions_for_share(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        version_ids: tuple[UUID, ...],
    ) -> dict[UUID, DataAtomVersion]:
        if not version_ids:
            return {}
        cursor = await connection.execute(
            """
            select id, tenant_id, project_id, atom_id, version, status,
                   contract, content_digest, static_validation_state,
                   runtime_validation_state, cleanup_validation_state,
                   validated_at, published_at, published_by, revision,
                   created_at, updated_at
            from atlas.data_atom_version
            where project_id = %s and id = any(%s)
            order by id
            for share
            """,
            (project_id, list(version_ids)),
        )
        versions = tuple(DataAtomVersion.model_validate(row) for row in await cursor.fetchall())
        return {version.id: version for version in versions}

    async def get_lease_snapshots_for_share(
        self,
        connection: AsyncConnection[DictRow],
        lease_ids: tuple[UUID, ...],
    ) -> dict[UUID, FixtureLeaseSnapshot]:
        if not lease_ids:
            return {}
        cursor = await connection.execute(
            """
            select lease.id as account_lease_id,
                   lease.tenant_id, lease.project_id, lease.environment_id,
                   lease.execution_id, lease.worker_id, lease.account_handle,
                   lease.fencing_token, lease.status as lease_status,
                   lease.expires_at as lease_expires_at,
                   account.connector_installation_id,
                   connector.adapter_key as connector_adapter_key,
                   connector.configuration_ref as connector_configuration_ref,
                   connector.status as connector_status,
                   connector.health_state as connector_health_state,
                   connector.revision as connector_revision
            from atlas.account_lease as lease
            join atlas.test_account as account on account.id = lease.account_id
            join atlas.connector_installation as connector
              on connector.id = account.connector_installation_id
            where lease.id = any(%s)
            order by lease.id
            for share of lease, account, connector
            """,
            (list(lease_ids),),
        )
        snapshots = tuple(FixtureLeaseSnapshot(**row) for row in await cursor.fetchall())
        return {snapshot.account_lease_id: snapshot for snapshot in snapshots}

    async def create_run(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        command: StartFixtureRun,
        blueprint: DataBlueprintVersion,
        atom_versions: dict[UUID, DataAtomVersion],
        lease_snapshots: dict[UUID, FixtureLeaseSnapshot],
        requested_by: UUID | None,
        workflow_id: str,
        cleanup_state: FixtureCleanupState,
        requested_at: datetime,
        node_ids: dict[str, UUID],
    ) -> FixtureRunRecord | None:
        plan = blueprint.compiled_plan
        if plan is None or blueprint.plan_digest is None:
            raise ValueError("blueprint must have a compiled plan")
        cursor = await connection.execute(
            f"""
            insert into atlas.fixture_run (
              id, tenant_id, project_id, environment_id, blueprint_version_id,
              run_kind, execution_id, plan_digest, input_digest,
              compiled_plan, run_inputs, cleanup_policy, cleanup_state,
              temporal_workflow_id, requested_by, execution_deadline, requested_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s
            )
            on conflict do nothing
            returning {RUN_RECORD_COLUMNS}
            """,
            (
                run_id,
                tenant_id,
                project_id,
                command.environment_id,
                command.blueprint_version_id,
                command.run_kind,
                command.execution_id,
                plan.plan_digest,
                _json_digest(command.inputs),
                Jsonb(plan.model_dump(mode="json", by_alias=True)),
                Jsonb(command.inputs),
                blueprint.contract.cleanup_policy,
                cleanup_state,
                workflow_id,
                requested_by,
                command.execution_deadline,
                requested_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        for requested_binding in command.actor_bindings:
            snapshot = lease_snapshots[requested_binding.account_lease_id]
            await connection.execute(
                """
                insert into atlas.fixture_actor_binding (
                  fixture_run_id, tenant_id, project_id, environment_id,
                  actor_slot, account_lease_id, fencing_token,
                  connector_installation_id, bound_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    tenant_id,
                    project_id,
                    command.environment_id,
                    requested_binding.actor_slot,
                    requested_binding.account_lease_id,
                    requested_binding.fencing_token,
                    snapshot.connector_installation_id,
                    requested_at,
                ),
            )

        for node in plan.nodes:
            atom = atom_versions[node.atom_version_id]
            await connection.execute(
                """
                insert into atlas.data_node_run (
                  id, tenant_id, project_id, environment_id, fixture_run_id,
                  node_id, atom_id, atom_version_id, actor_slot,
                  execution_level, logical_idempotency_key
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    node_ids[node.node_id],
                    tenant_id,
                    project_id,
                    command.environment_id,
                    run_id,
                    node.node_id,
                    atom.atom_id,
                    node.atom_version_id,
                    node.actor_slot,
                    node.execution_level,
                    _logical_idempotency_key(
                        environment_id=command.environment_id,
                        blueprint_version_id=command.blueprint_version_id,
                        execution_id=command.execution_id,
                        node_id=node.node_id,
                    ),
                ),
            )
        return FixtureRunRecord.model_validate(row)

    async def get_run(
        self,
        connection: AsyncConnection[DictRow],
        run_id: UUID,
    ) -> FixtureRun | None:
        cursor = await connection.execute(
            f"select {RUN_COLUMNS} from atlas.fixture_run where id = %s",
            (run_id,),
        )
        row = await cursor.fetchone()
        return FixtureRun.model_validate(row) if row is not None else None

    async def get_run_record(
        self,
        connection: AsyncConnection[DictRow],
        run_id: UUID,
        *,
        for_update: bool = False,
    ) -> FixtureRunRecord | None:
        lock = " for update" if for_update else ""
        cursor = await connection.execute(
            f"select {RUN_RECORD_COLUMNS} from atlas.fixture_run where id = %s{lock}",
            (run_id,),
        )
        row = await cursor.fetchone()
        return FixtureRunRecord.model_validate(row) if row is not None else None

    async def get_detail(
        self,
        connection: AsyncConnection[DictRow],
        run_id: UUID,
    ) -> FixtureRunDetail | None:
        run = await self.get_run(connection, run_id)
        if run is None:
            return None
        binding_cursor = await connection.execute(
            f"""
            select {BINDING_COLUMNS}
            from atlas.fixture_actor_binding
            where fixture_run_id = %s
            order by actor_slot
            """,
            (run_id,),
        )
        node_cursor = await connection.execute(
            f"""
            select {NODE_COLUMNS}
            from atlas.data_node_run
            where fixture_run_id = %s
            order by execution_level, node_id
            """,
            (run_id,),
        )
        attempt_cursor = await connection.execute(
            f"""
            select {ATTEMPT_COLUMNS}
            from atlas.data_node_attempt
            where fixture_run_id = %s
            order by started_at, id
            """,
            (run_id,),
        )
        return FixtureRunDetail(
            run=run,
            actor_bindings=tuple(
                FixtureActorBinding.model_validate(row) for row in await binding_cursor.fetchall()
            ),
            nodes=tuple(DataNodeRun.model_validate(row) for row in await node_cursor.fetchall()),
            attempts=tuple(
                DataNodeAttempt.model_validate(row) for row in await attempt_cursor.fetchall()
            ),
        )

    async def get_binding_record(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        actor_slot: str,
    ) -> FixtureActorBindingRecord | None:
        cursor = await connection.execute(
            """
            select binding.fixture_run_id, binding.actor_slot,
                   binding.account_lease_id, binding.fencing_token,
                   binding.connector_installation_id, binding.bound_at,
                   lease.account_handle, lease.worker_id as lease_worker_id,
                   lease.status as lease_status,
                   lease.expires_at as lease_expires_at,
                   connector.adapter_key as connector_adapter_key,
                   connector.configuration_ref as connector_configuration_ref,
                   connector.status as connector_status,
                   connector.revision as connector_revision
            from atlas.fixture_actor_binding as binding
            join atlas.account_lease as lease on lease.id = binding.account_lease_id
            join atlas.connector_installation as connector
              on connector.id = binding.connector_installation_id
            where binding.fixture_run_id = %s and binding.actor_slot = %s
            """,
            (run_id, actor_slot),
        )
        row = await cursor.fetchone()
        return FixtureActorBindingRecord.model_validate(row) if row is not None else None

    async def get_node_record(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        node_id: str,
        for_update: bool = False,
    ) -> DataNodeRunRecord | None:
        lock = " for update" if for_update else ""
        cursor = await connection.execute(
            f"""
            select {NODE_RECORD_COLUMNS}
            from atlas.data_node_run
            where fixture_run_id = %s and node_id = %s{lock}
            """,
            (run_id, node_id),
        )
        row = await cursor.fetchone()
        return DataNodeRunRecord.model_validate(row) if row is not None else None

    async def get_atom_version(
        self,
        connection: AsyncConnection[DictRow],
        version_id: UUID,
    ) -> DataAtomVersion | None:
        cursor = await connection.execute(
            """
            select id, tenant_id, project_id, atom_id, version, status,
                   contract, content_digest, static_validation_state,
                   runtime_validation_state, cleanup_validation_state,
                   validated_at, published_at, published_by, revision,
                   created_at, updated_at
            from atlas.data_atom_version
            where id = %s
            """,
            (version_id,),
        )
        row = await cursor.fetchone()
        return DataAtomVersion.model_validate(row) if row is not None else None

    async def get_node_outputs(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        node_ids: tuple[str, ...],
    ) -> dict[str, dict[str, JsonValue]]:
        if not node_ids:
            return {}
        cursor = await connection.execute(
            """
            select node_id, outputs
            from atlas.data_node_run
            where fixture_run_id = %s and node_id = any(%s)
              and status = 'SUCCEEDED' and outputs is not null
            """,
            (run_id, list(node_ids)),
        )
        return {row["node_id"]: row["outputs"] for row in await cursor.fetchall()}

    async def start_node_attempt(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
        node: DataNodeRunRecord,
        attempt_id: UUID,
        inputs: dict[str, JsonValue],
        started_at: datetime,
    ) -> tuple[DataNodeRunRecord, DataNodeAttempt] | None:
        if run.status is FixtureRunStatus.REQUESTED:
            await connection.execute(
                """
                update atlas.fixture_run
                set status = 'RUNNING', started_at = %s, revision = revision + 1
                where id = %s and status = 'REQUESTED' and revision = %s
                """,
                (started_at, run.id, run.revision),
            )
        cursor = await connection.execute(
            f"""
            update atlas.data_node_run
            set status = 'RUNNING', inputs = %s,
                attempt_count = attempt_count + 1,
                started_at = coalesce(started_at, %s), revision = revision + 1
            where id = %s and revision = %s and status in ('PENDING', 'READY')
            returning {NODE_RECORD_COLUMNS}
            """,
            (Jsonb(inputs), started_at, node.id, node.revision),
        )
        node_row = await cursor.fetchone()
        if node_row is None:
            return None
        updated_node = DataNodeRunRecord.model_validate(node_row)
        attempt_cursor = await connection.execute(
            f"""
            insert into atlas.data_node_attempt (
              id, tenant_id, project_id, environment_id, fixture_run_id,
              data_node_run_id, attempt_number, started_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            returning {ATTEMPT_COLUMNS}
            """,
            (
                attempt_id,
                run.tenant_id,
                run.project_id,
                run.environment_id,
                run.id,
                node.id,
                updated_node.attempt_count,
                started_at,
            ),
        )
        attempt_row = await attempt_cursor.fetchone()
        if attempt_row is None:
            raise RuntimeError("fixture node attempt insert did not return a row")
        return updated_node, DataNodeAttempt.model_validate(attempt_row)

    async def get_running_attempt(
        self,
        connection: AsyncConnection[DictRow],
        node_run_id: UUID,
        *,
        for_update: bool = False,
    ) -> DataNodeAttempt | None:
        lock = " for update" if for_update else ""
        cursor = await connection.execute(
            f"""
            select {ATTEMPT_COLUMNS}
            from atlas.data_node_attempt
            where data_node_run_id = %s and status = 'RUNNING'
            order by attempt_number desc
            limit 1{lock}
            """,
            (node_run_id,),
        )
        row = await cursor.fetchone()
        return DataNodeAttempt.model_validate(row) if row is not None else None

    async def mark_node_verifying(
        self,
        connection: AsyncConnection[DictRow],
        *,
        node_run_id: UUID,
        expected_revision: int,
        outputs: dict[str, JsonValue],
        output_digest: str,
    ) -> DataNodeRunRecord | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_node_run
            set status = 'VERIFYING', outputs = %s, output_digest = %s,
                revision = revision + 1
            where id = %s and revision = %s and status = 'RUNNING'
            returning {NODE_RECORD_COLUMNS}
            """,
            (Jsonb(outputs), output_digest, node_run_id, expected_revision),
        )
        row = await cursor.fetchone()
        return DataNodeRunRecord.model_validate(row) if row is not None else None

    async def record_resource(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
        node: DataNodeRunRecord,
        attempt: DataNodeAttempt,
        connector_installation_id: UUID,
        resource_id: UUID,
        resource_handle: str,
        resource_type: str,
        resource_ownership: ResourceOwnership,
        opaque_ref: str,
        expires_at: datetime,
        cleanup_operation_key: str | None,
        cleanup_operation_version: str | None,
        recorded_at: datetime,
        parent_resource_ids: tuple[UUID, ...] = (),
    ) -> None:
        cursor = await connection.execute(
            """
            insert into atlas.resource_record (
              id, tenant_id, project_id, environment_id, fixture_run_id,
              data_node_run_id, data_node_attempt_id,
              connector_installation_id, resource_handle, resource_type,
              ownership, opaque_ref, opaque_ref_hash, expires_at,
              cleanup_operation_key, cleanup_operation_version, created_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s
            )
            on conflict (tenant_id, resource_handle) do nothing
            returning id
            """,
            (
                resource_id,
                run.tenant_id,
                run.project_id,
                run.environment_id,
                run.id,
                node.id,
                attempt.id,
                connector_installation_id,
                resource_handle,
                resource_type,
                resource_ownership,
                opaque_ref,
                _json_digest(opaque_ref),
                expires_at,
                cleanup_operation_key,
                cleanup_operation_version,
                recorded_at,
            ),
        )
        if await cursor.fetchone() is None:
            raise RuntimeError("fixture resource handle already exists")
        for parent_resource_id in parent_resource_ids:
            await connection.execute(
                """
                insert into atlas.resource_dependency (
                  tenant_id, project_id, environment_id, fixture_run_id,
                  child_resource_id, parent_resource_id, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                """,
                (
                    run.tenant_id,
                    run.project_id,
                    run.environment_id,
                    run.id,
                    resource_id,
                    parent_resource_id,
                    recorded_at,
                ),
            )

    async def complete_node_success(
        self,
        connection: AsyncConnection[DictRow],
        *,
        node: DataNodeRunRecord,
        attempt: DataNodeAttempt,
        provider_request_id: str | None,
        finished_at: datetime,
    ) -> DataNodeRunRecord | None:
        await connection.execute(
            """
            update atlas.data_node_attempt
            set status = 'SUCCEEDED', provider_request_id = %s, finished_at = %s
            where id = %s and status = 'RUNNING'
            """,
            (provider_request_id, finished_at, attempt.id),
        )
        cursor = await connection.execute(
            f"""
            update atlas.data_node_run
            set status = 'SUCCEEDED', finished_at = %s, revision = revision + 1
            where id = %s and revision = %s and status = 'VERIFYING'
            returning {NODE_RECORD_COLUMNS}
            """,
            (finished_at, node.id, node.revision),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return DataNodeRunRecord.model_validate(row)

    async def complete_node_failure(
        self,
        connection: AsyncConnection[DictRow],
        *,
        node: DataNodeRunRecord,
        attempt: DataNodeAttempt,
        status: DataNodeRunStatus,
        category: FixtureFailureCategory,
        code: str,
        detail: str,
        provider_request_id: str | None,
        finished_at: datetime,
    ) -> DataNodeRunRecord | None:
        attempt_status = (
            DataNodeAttemptStatus.OUTCOME_UNCERTAIN
            if status is DataNodeRunStatus.OUTCOME_UNCERTAIN
            else DataNodeAttemptStatus.FAILED
        )
        await connection.execute(
            """
            update atlas.data_node_attempt
            set status = %s, failure_category = %s, failure_code = %s,
                failure_detail = %s, provider_request_id = %s, finished_at = %s
            where id = %s and status = 'RUNNING'
            """,
            (
                attempt_status,
                category,
                code,
                detail,
                provider_request_id,
                finished_at,
                attempt.id,
            ),
        )
        cursor = await connection.execute(
            f"""
            update atlas.data_node_run
            set status = %s, failure_category = %s, failure_code = %s,
                failure_detail = %s, finished_at = %s, revision = revision + 1
            where id = %s and revision = %s and status in ('RUNNING', 'VERIFYING')
            returning {NODE_RECORD_COLUMNS}
            """,
            (status, category, code, detail, finished_at, node.id, node.revision),
        )
        row = await cursor.fetchone()
        return DataNodeRunRecord.model_validate(row) if row is not None else None

    async def fail_node_without_attempt(
        self,
        connection: AsyncConnection[DictRow],
        *,
        node: DataNodeRunRecord,
        category: FixtureFailureCategory,
        code: str,
        detail: str,
        finished_at: datetime,
    ) -> DataNodeRunRecord | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_node_run
            set status = 'FAILED', failure_category = %s, failure_code = %s,
                failure_detail = %s, started_at = coalesce(started_at, %s),
                finished_at = %s, revision = revision + 1
            where id = %s and revision = %s and status in ('PENDING', 'READY')
            returning {NODE_RECORD_COLUMNS}
            """,
            (category, code, detail, finished_at, finished_at, node.id, node.revision),
        )
        row = await cursor.fetchone()
        return DataNodeRunRecord.model_validate(row) if row is not None else None

    async def get_manifest(
        self,
        connection: AsyncConnection[DictRow],
        run_id: UUID,
    ) -> FixtureManifestRecord | None:
        cursor = await connection.execute(
            """
            select fixture_run_id, manifest, manifest_digest, created_at
            from atlas.fixture_manifest
            where fixture_run_id = %s
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        return FixtureManifestRecord.model_validate(row) if row is not None else None

    async def list_resources(
        self,
        connection: AsyncConnection[DictRow],
        run_id: UUID,
    ) -> FixtureResourcePage:
        cursor = await connection.execute(
            f"""
            select {RESOURCE_COLUMNS}
            from atlas.resource_record
            where fixture_run_id = %s
            order by created_at, id
            """,
            (run_id,),
        )
        return FixtureResourcePage(
            items=tuple(ResourceRecord.model_validate(row) for row in await cursor.fetchall())
        )

    async def get_resource_by_opaque_ref(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        opaque_ref: str,
    ) -> ResourceRecord | None:
        cursor = await connection.execute(
            f"""
            select {RESOURCE_COLUMNS}
            from atlas.resource_record
            where fixture_run_id = %s and opaque_ref_hash = %s
            """,
            (run_id, _json_digest(opaque_ref)),
        )
        row = await cursor.fetchone()
        return ResourceRecord.model_validate(row) if row is not None else None

    async def finalize_ready(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
        manifest: FixtureManifest,
        manifest_digest: str,
        observed_at: datetime,
        evidence: tuple[FixtureValidationEvidence, ...],
    ) -> FixtureRun | None:
        node_cursor = await connection.execute(
            """
            select count(*) filter (where status = 'SUCCEEDED') as succeeded,
                   count(*) as total
            from atlas.data_node_run
            where fixture_run_id = %s
            """,
            (run.id,),
        )
        counts = await node_cursor.fetchone()
        if counts is None or counts["succeeded"] != counts["total"]:
            return None
        await connection.execute(
            """
            insert into atlas.fixture_manifest (
              fixture_run_id, tenant_id, project_id, environment_id,
              blueprint_version_id, plan_digest, manifest, manifest_digest, created_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (fixture_run_id) do nothing
            """,
            (
                run.id,
                run.tenant_id,
                run.project_id,
                run.environment_id,
                run.blueprint_version_id,
                run.plan_digest,
                Jsonb(manifest.model_dump(mode="json", by_alias=True)),
                manifest_digest,
                observed_at,
            ),
        )
        for item in evidence:
            atom_version_id = (
                item.subject_version_id
                if item.subject is ValidationEvidenceSubject.ATOM_VERSION
                else None
            )
            blueprint_version_id = (
                item.subject_version_id
                if item.subject is ValidationEvidenceSubject.BLUEPRINT_VERSION
                else None
            )
            await connection.execute(
                """
                insert into atlas.fixture_validation_evidence (
                  id, tenant_id, project_id, environment_id, fixture_run_id,
                  kind, subject, atom_version_id, blueprint_version_id,
                  subject_digest, passed, safe_summary, observed_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                """,
                (
                    item.id,
                    item.tenant_id,
                    item.project_id,
                    item.environment_id,
                    item.fixture_run_id,
                    item.kind,
                    item.subject,
                    atom_version_id,
                    blueprint_version_id,
                    item.subject_digest,
                    item.passed,
                    item.safe_summary,
                    item.observed_at,
                ),
            )
            table = (
                "data_atom_version"
                if item.subject is ValidationEvidenceSubject.ATOM_VERSION
                else "data_blueprint_version"
            )
            cursor = await connection.execute(
                f"""
                update atlas.{table}
                set runtime_validation_state = 'PASSED',
                    runtime_validation_evidence_id = %s,
                    runtime_validated_at = %s,
                    revision = revision + 1
                where id = %s and content_digest = %s
                returning id
                """
                if table == "data_atom_version"
                else f"""
                update atlas.{table}
                set runtime_validation_state = 'PASSED',
                    runtime_validation_evidence_id = %s,
                    runtime_validated_at = %s,
                    revision = revision + 1
                where id = %s and plan_digest = %s
                returning id
                """,
                (item.id, observed_at, item.subject_version_id, item.subject_digest),
            )
            if await cursor.fetchone() is None:
                raise RuntimeError("fixture validation subject changed during execution")
        cursor = await connection.execute(
            f"""
            update atlas.fixture_run
            set status = 'READY', ready_at = %s, finished_at = %s,
                revision = revision + 1
            where id = %s and revision = %s and status = 'RUNNING'
            returning {RUN_COLUMNS}
            """,
            (observed_at, observed_at, run.id, run.revision),
        )
        row = await cursor.fetchone()
        return FixtureRun.model_validate(row) if row is not None else None

    async def begin_release(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
    ) -> FixtureRun | None:
        await connection.execute(
            """
            update atlas.resource_record
            set status = 'CLEANUP_PENDING', revision = revision + 1
            where fixture_run_id = %s and ownership = 'CREATED' and status = 'ACTIVE'
            """,
            (run.id,),
        )
        cursor = await connection.execute(
            f"""
            update atlas.fixture_run
            set status = 'CLEANING', cleanup_state = 'RUNNING',
                revision = revision + 1
            where id = %s and revision = %s and status = 'READY'
            returning {RUN_COLUMNS}
            """,
            (run.id, run.revision),
        )
        row = await cursor.fetchone()
        return FixtureRun.model_validate(row) if row is not None else None

    async def begin_failed_cleanup(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
        category: FixtureFailureCategory,
        code: str,
        detail: str,
    ) -> FixtureRun | None:
        await connection.execute(
            """
            update atlas.resource_record
            set status = 'CLEANUP_PENDING', revision = revision + 1
            where fixture_run_id = %s and ownership = 'CREATED' and status = 'ACTIVE'
            """,
            (run.id,),
        )
        cursor = await connection.execute(
            f"""
            update atlas.fixture_run
            set cleanup_state = 'RUNNING', failure_category = %s,
                failure_code = %s, failure_detail = %s,
                started_at = coalesce(started_at, requested_at),
                revision = revision + 1
            where id = %s and revision = %s and status in ('REQUESTED', 'RUNNING')
            returning {RUN_COLUMNS}
            """,
            (category, code, detail, run.id, run.revision),
        )
        row = await cursor.fetchone()
        return FixtureRun.model_validate(row) if row is not None else None

    async def list_cleanup_resources(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        node_id: str,
    ) -> tuple[ResourceRecordInternal, ...]:
        cursor = await connection.execute(
            f"""
            select resource.{RESOURCE_INTERNAL_COLUMNS.replace(", ", ", resource.")}
            from atlas.resource_record as resource
            join atlas.data_node_run as node on node.id = resource.data_node_run_id
            where resource.fixture_run_id = %s and node.node_id = %s
              and resource.ownership = 'CREATED'
              and resource.status in ('CLEANUP_PENDING', 'CLEANING')
            order by resource.created_at desc, resource.id desc
            """,
            (run_id, node_id),
        )
        return tuple(ResourceRecordInternal.model_validate(row) for row in await cursor.fetchall())

    async def claim_resource_cleanup(
        self,
        connection: AsyncConnection[DictRow],
        *,
        resource_id: UUID,
        expected_revision: int,
    ) -> ResourceRecordInternal | None:
        cursor = await connection.execute(
            f"""
            update atlas.resource_record
            set status = 'CLEANING', cleanup_generation = cleanup_generation + 1,
                revision = revision + 1
            where id = %s and revision = %s and status = 'CLEANUP_PENDING'
              and not exists (
                select 1
                from atlas.resource_dependency as dependency
                join atlas.resource_record as child
                  on child.id = dependency.child_resource_id
                where dependency.parent_resource_id = atlas.resource_record.id
                  and child.status <> 'CLEANED'
              )
            returning {RESOURCE_INTERNAL_COLUMNS}
            """,
            (resource_id, expected_revision),
        )
        row = await cursor.fetchone()
        return ResourceRecordInternal.model_validate(row) if row is not None else None

    async def complete_resource_cleanup(
        self,
        connection: AsyncConnection[DictRow],
        *,
        resource: ResourceRecordInternal,
        cleaned_at: datetime,
    ) -> ResourceRecord | None:
        cursor = await connection.execute(
            f"""
            update atlas.resource_record
            set status = 'CLEANED', cleaned_at = %s, revision = revision + 1
            where id = %s and revision = %s and status = 'CLEANING'
            returning {RESOURCE_COLUMNS}
            """,
            (cleaned_at, resource.id, resource.revision),
        )
        row = await cursor.fetchone()
        return ResourceRecord.model_validate(row) if row is not None else None

    async def fail_resource_cleanup(
        self,
        connection: AsyncConnection[DictRow],
        *,
        resource: ResourceRecordInternal,
    ) -> ResourceRecord | None:
        cursor = await connection.execute(
            f"""
            update atlas.resource_record
            set status = 'LEAKED', revision = revision + 1
            where id = %s and revision = %s and status = 'CLEANING'
            returning {RESOURCE_COLUMNS}
            """,
            (resource.id, resource.revision),
        )
        row = await cursor.fetchone()
        return ResourceRecord.model_validate(row) if row is not None else None

    async def finalize_release(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
        finished_at: datetime,
    ) -> FixtureRun | None:
        cursor = await connection.execute(
            """
            select count(*) filter (where status <> 'CLEANED') as remaining,
                   count(*) filter (where status = 'CLEANED') as cleaned
            from atlas.resource_record
            where fixture_run_id = %s and ownership = 'CREATED'
            """,
            (run.id,),
        )
        counts = await cursor.fetchone()
        remaining = int(counts["remaining"]) if counts is not None else 0
        status = FixtureRunStatus.RELEASED if remaining == 0 else FixtureRunStatus.CLEANUP_FAILED
        cleanup_state = (
            FixtureCleanupState.CLEANED if remaining == 0 else FixtureCleanupState.LEAKED
        )
        await self._release_bound_leases(
            connection,
            run_id=run.id,
            reason=(
                LeaseReleaseReason.COMPLETED
                if remaining == 0
                else LeaseReleaseReason.CLEANUP_FAILED
            ),
            released_at=finished_at,
        )
        result = await connection.execute(
            f"""
            update atlas.fixture_run
            set status = %s, cleanup_state = %s, released_at = %s,
                failure_category = %s, failure_code = %s, failure_detail = %s,
                revision = revision + 1
            where id = %s and revision = %s and status = 'CLEANING'
            returning {RUN_COLUMNS}
            """,
            (
                status,
                cleanup_state,
                finished_at if status is FixtureRunStatus.RELEASED else None,
                None if remaining == 0 else FixtureFailureCategory.CLEANUP,
                None if remaining == 0 else "FIXTURE_CLEANUP_INCOMPLETE",
                None if remaining == 0 else "One or more fixture resources were not cleaned.",
                run.id,
                run.revision,
            ),
        )
        row = await result.fetchone()
        return FixtureRun.model_validate(row) if row is not None else None

    async def finalize_failed_run(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
        finished_at: datetime,
    ) -> FixtureRun | None:
        cursor = await connection.execute(
            """
            select count(*) filter (where status <> 'CLEANED') as remaining
            from atlas.resource_record
            where fixture_run_id = %s and ownership = 'CREATED'
            """,
            (run.id,),
        )
        counts = await cursor.fetchone()
        remaining = int(counts["remaining"]) if counts is not None else 0
        cleanup_state = (
            FixtureCleanupState.CLEANED if remaining == 0 else FixtureCleanupState.LEAKED
        )
        await self._release_bound_leases(
            connection,
            run_id=run.id,
            reason=(
                LeaseReleaseReason.CLEANUP_FAILED if remaining else LeaseReleaseReason.COMPLETED
            ),
            released_at=finished_at,
        )
        result = await connection.execute(
            f"""
            update atlas.fixture_run
            set status = 'FAILED', cleanup_state = %s, finished_at = %s,
                revision = revision + 1
            where id = %s and revision = %s and status in ('REQUESTED', 'RUNNING')
              and failure_category is not null
            returning {RUN_COLUMNS}
            """,
            (cleanup_state, finished_at, run.id, run.revision),
        )
        row = await result.fetchone()
        return FixtureRun.model_validate(row) if row is not None else None

    async def _release_bound_leases(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
        reason: LeaseReleaseReason,
        released_at: datetime,
    ) -> None:
        cursor = await connection.execute(
            """
            select account_lease_id, fencing_token
            from atlas.fixture_actor_binding
            where fixture_run_id = %s
            order by account_lease_id
            """,
            (run_id,),
        )
        leases = LeaseRepository()
        for row in await cursor.fetchall():
            await leases.release(
                connection,
                lease_id=row["account_lease_id"],
                command=ReleaseAccountLease(
                    fencing_token=row["fencing_token"],
                    reason=reason,
                ),
                now=released_at,
            )


def _json_digest(value: object) -> str:
    import json

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _logical_idempotency_key(
    *,
    environment_id: UUID,
    blueprint_version_id: UUID,
    execution_id: str,
    node_id: str,
) -> str:
    material = "\n".join((str(environment_id), str(blueprint_version_id), execution_id, node_id))
    return "fix_" + hashlib.sha256(material.encode()).hexdigest()
