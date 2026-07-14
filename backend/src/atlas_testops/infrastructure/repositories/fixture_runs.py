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
    DataNodeReconcileAttempt,
    DataNodeReconcileAttemptStatus,
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
    FixtureRunTerminalIntent,
    FixtureValidationEvidence,
    ResourceCleanupAttempt,
    ResourceCleanupAttemptStatus,
    ResourceOwnership,
    ResourceRecord,
    ResourceRecordInternal,
    ResourceRecordStatus,
    StartFixtureRun,
    ValidationEvidenceSubject,
)
from atlas_testops.domain.identity import LeaseReleaseReason, ReleaseAccountLease
from atlas_testops.infrastructure.repositories.leases import LeaseRepository

RUN_COLUMNS = (
    "id, tenant_id, project_id, environment_id, blueprint_version_id, run_kind, "
    "execution_id, plan_digest, input_digest, status, cleanup_state, "
    "terminal_intent, temporal_workflow_id, requested_by, failure_category, "
    "failure_code, failure_detail, execution_deadline, requested_at, "
    "cancel_requested_at, cancel_requested_by, cleanup_generation, started_at, "
    "ready_at, finished_at, released_at, revision, updated_at"
)
RUN_RECORD_COLUMNS = f"{RUN_COLUMNS}, compiled_plan, run_inputs, cleanup_policy"
BINDING_COLUMNS = (
    "fixture_run_id, actor_slot, account_lease_id, fencing_token, "
    "connector_installation_id, bound_at"
)
NODE_COLUMNS = (
    "id, fixture_run_id, node_id, atom_version_id, actor_slot, execution_level, "
    "status, attempt_count, reconcile_state, reconcile_attempt_count, "
    "next_reconcile_at, output_digest, failure_category, failure_code, "
    "failure_detail, started_at, finished_at, revision, updated_at"
)
NODE_RECORD_COLUMNS = f"{NODE_COLUMNS}, atom_id, logical_idempotency_key, inputs, outputs"
ATTEMPT_COLUMNS = (
    "id, fixture_run_id, data_node_run_id, attempt_number, status, "
    "failure_category, failure_code, failure_detail, provider_request_id, "
    "started_at, finished_at, updated_at"
)
RECONCILE_ATTEMPT_COLUMNS = (
    "id, fixture_run_id, data_node_run_id, attempt_number, status, "
    "failure_category, failure_code, failure_detail, provider_request_id, "
    "started_at, finished_at, updated_at"
)
RESOURCE_COLUMNS = (
    "id, fixture_run_id, data_node_run_id, connector_installation_id, "
    "resource_handle, resource_type, ownership, status, expires_at, "
    "cleanup_generation, next_cleanup_at, created_at, cleaned_at, revision, updated_at"
)
RESOURCE_INTERNAL_COLUMNS = (
    f"{RESOURCE_COLUMNS}, data_node_attempt_id, opaque_ref, "
    "cleanup_operation_key, cleanup_operation_version"
)
CLEANUP_ATTEMPT_COLUMNS = (
    "id, fixture_run_id, resource_record_id, cleanup_generation, status, "
    "worker_identity, failure_category, failure_code, failure_detail, "
    "provider_request_id, started_at, finished_at, updated_at"
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
        reconcile_cursor = await connection.execute(
            f"""
            select {RECONCILE_ATTEMPT_COLUMNS}
            from atlas.data_node_reconcile_attempt
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
            reconcile_attempts=tuple(
                DataNodeReconcileAttempt.model_validate(row)
                for row in await reconcile_cursor.fetchall()
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

    async def get_node_record_by_id(
        self,
        connection: AsyncConnection[DictRow],
        node_run_id: UUID,
    ) -> DataNodeRunRecord | None:
        cursor = await connection.execute(
            f"select {NODE_RECORD_COLUMNS} from atlas.data_node_run where id = %s",
            (node_run_id,),
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

    async def get_latest_attempt(
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
            where data_node_run_id = %s
            order by attempt_number desc
            limit 1{lock}
            """,
            (node_run_id,),
        )
        row = await cursor.fetchone()
        return DataNodeAttempt.model_validate(row) if row is not None else None

    async def list_due_reconcile_nodes(
        self,
        connection: AsyncConnection[DictRow],
        *,
        now: datetime,
        limit: int,
        run_id: UUID | None = None,
    ) -> tuple[tuple[UUID, str], ...]:
        cursor = await connection.execute(
            """
            select fixture_run_id, node_id
            from atlas.data_node_run
            where status = 'OUTCOME_UNCERTAIN'
              and reconcile_state in ('PENDING', 'INCONCLUSIVE')
              and next_reconcile_at <= %s
              and (%s::uuid is null or fixture_run_id = %s)
            order by next_reconcile_at, fixture_run_id, id
            limit %s
            """,
            (now, run_id, run_id, limit),
        )
        return tuple((row["fixture_run_id"], row["node_id"]) for row in await cursor.fetchall())

    async def recover_stale_reconcile_claims(
        self,
        connection: AsyncConnection[DictRow],
        *,
        stale_before: datetime,
        retry_at: datetime,
        max_attempts: int,
        limit: int,
    ) -> tuple[int, int]:
        cursor = await connection.execute(
            """
            with candidates as (
              select attempt.id, attempt.data_node_run_id,
                     node.reconcile_attempt_count
              from atlas.data_node_reconcile_attempt as attempt
              join atlas.data_node_run as node
                on node.id = attempt.data_node_run_id
              where attempt.status = 'RUNNING' and attempt.started_at <= %s
                and node.status = 'OUTCOME_UNCERTAIN'
                and node.reconcile_state = 'RUNNING'
              order by attempt.started_at, attempt.id
              limit %s
              for update of attempt, node skip locked
            ), failed_attempts as (
              update atlas.data_node_reconcile_attempt as attempt
              set status = 'INCONCLUSIVE', failure_category = 'UNCERTAIN',
                  failure_code = 'RECONCILE_CLAIM_EXPIRED',
                  failure_detail =
                    'The prior reconcile claim expired without a durable outcome.',
                  finished_at = %s
              from candidates
              where attempt.id = candidates.id
              returning candidates.data_node_run_id,
                        candidates.reconcile_attempt_count
            )
            update atlas.data_node_run as node
            set reconcile_state = case
                  when failed_attempts.reconcile_attempt_count >= %s
                    then 'EXHAUSTED'
                  else 'INCONCLUSIVE'
                end,
                next_reconcile_at = case
                  when failed_attempts.reconcile_attempt_count >= %s then null
                  else %s
                end,
                failure_category = 'UNCERTAIN',
                failure_code = 'RECONCILE_CLAIM_EXPIRED',
                failure_detail =
                  'The prior reconcile claim expired without a durable outcome.',
                revision = node.revision + 1
            from failed_attempts
            where node.id = failed_attempts.data_node_run_id
              and node.status = 'OUTCOME_UNCERTAIN'
              and node.reconcile_state = 'RUNNING'
            returning node.reconcile_state
            """,
            (
                stale_before,
                limit,
                retry_at,
                max_attempts,
                max_attempts,
                retry_at,
            ),
        )
        rows = await cursor.fetchall()
        exhausted = sum(row["reconcile_state"] == "EXHAUSTED" for row in rows)
        return len(rows) - exhausted, exhausted

    async def start_reconcile_attempt(
        self,
        connection: AsyncConnection[DictRow],
        *,
        node: DataNodeRunRecord,
        attempt_id: UUID,
        started_at: datetime,
    ) -> tuple[DataNodeRunRecord, DataNodeReconcileAttempt] | None:
        cursor = await connection.execute(
            f"""
            update atlas.data_node_run
            set reconcile_state = 'RUNNING', reconcile_attempt_count = reconcile_attempt_count + 1,
                next_reconcile_at = null, revision = revision + 1
            where id = %s and revision = %s and status = 'OUTCOME_UNCERTAIN'
              and reconcile_state in ('PENDING', 'INCONCLUSIVE')
              and next_reconcile_at <= %s
              and reconcile_attempt_count < 32
            returning {NODE_RECORD_COLUMNS}
            """,
            (node.id, node.revision, started_at),
        )
        node_row = await cursor.fetchone()
        if node_row is None:
            return None
        updated_node = DataNodeRunRecord.model_validate(node_row)
        attempt_cursor = await connection.execute(
            f"""
            insert into atlas.data_node_reconcile_attempt (
              id, tenant_id, project_id, environment_id, fixture_run_id,
              data_node_run_id, attempt_number, started_at
            )
            select %s, tenant_id, project_id, environment_id, fixture_run_id,
                   id, reconcile_attempt_count, %s
            from atlas.data_node_run
            where id = %s
            returning {RECONCILE_ATTEMPT_COLUMNS}
            """,
            (attempt_id, started_at, node.id),
        )
        attempt_row = await attempt_cursor.fetchone()
        if attempt_row is None:
            raise RuntimeError("reconcile attempt insert did not return a row")
        return updated_node, DataNodeReconcileAttempt.model_validate(attempt_row)

    async def complete_reconcile_found(
        self,
        connection: AsyncConnection[DictRow],
        *,
        node: DataNodeRunRecord,
        attempt: DataNodeReconcileAttempt,
        outputs: dict[str, JsonValue],
        output_digest: str,
        provider_request_id: str | None,
        finished_at: datetime,
    ) -> DataNodeRunRecord | None:
        await connection.execute(
            """
            update atlas.data_node_reconcile_attempt
            set status = 'FOUND', provider_request_id = %s, finished_at = %s
            where id = %s and status = 'RUNNING'
            """,
            (provider_request_id, finished_at, attempt.id),
        )
        cursor = await connection.execute(
            f"""
            update atlas.data_node_run
            set status = 'VERIFYING', reconcile_state = 'FOUND', outputs = %s,
                output_digest = %s, failure_category = null, failure_code = null,
                failure_detail = null, finished_at = null, revision = revision + 1
            where id = %s and revision = %s and status = 'OUTCOME_UNCERTAIN'
              and reconcile_state = 'RUNNING'
            returning {NODE_RECORD_COLUMNS}
            """,
            (Jsonb(outputs), output_digest, node.id, node.revision),
        )
        row = await cursor.fetchone()
        return DataNodeRunRecord.model_validate(row) if row is not None else None

    async def complete_reconcile_absent(
        self,
        connection: AsyncConnection[DictRow],
        *,
        node: DataNodeRunRecord,
        attempt: DataNodeReconcileAttempt,
        provider_request_id: str | None,
        finished_at: datetime,
        retry_create: bool,
    ) -> DataNodeRunRecord | None:
        await connection.execute(
            """
            update atlas.data_node_reconcile_attempt
            set status = 'ABSENT', provider_request_id = %s, finished_at = %s
            where id = %s and status = 'RUNNING'
            """,
            (provider_request_id, finished_at, attempt.id),
        )
        cursor = await connection.execute(
            f"""
            update atlas.data_node_run
            set status = %s, reconcile_state = %s, next_reconcile_at = null,
                failure_category = %s, failure_code = %s, failure_detail = %s,
                finished_at = %s, revision = revision + 1
            where id = %s and revision = %s and status = 'OUTCOME_UNCERTAIN'
              and reconcile_state = 'RUNNING'
            returning {NODE_RECORD_COLUMNS}
            """,
            (
                DataNodeRunStatus.READY if retry_create else DataNodeRunStatus.FAILED,
                "ABSENT" if retry_create else "EXHAUSTED",
                None if retry_create else FixtureFailureCategory.UNCERTAIN,
                None if retry_create else "RECONCILE_ABSENT_RETRY_EXHAUSTED",
                None
                if retry_create
                else "Reconcile proved the resource absent after the create retry budget.",
                None if retry_create else finished_at,
                node.id,
                node.revision,
            ),
        )
        row = await cursor.fetchone()
        return DataNodeRunRecord.model_validate(row) if row is not None else None

    async def complete_reconcile_inconclusive(
        self,
        connection: AsyncConnection[DictRow],
        *,
        node: DataNodeRunRecord,
        attempt: DataNodeReconcileAttempt,
        attempt_status: DataNodeReconcileAttemptStatus,
        category: FixtureFailureCategory,
        code: str,
        detail: str,
        provider_request_id: str | None,
        finished_at: datetime,
        retry_at: datetime | None,
    ) -> DataNodeRunRecord | None:
        await connection.execute(
            """
            update atlas.data_node_reconcile_attempt
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
            set reconcile_state = %s, next_reconcile_at = %s,
                failure_category = %s, failure_code = %s, failure_detail = %s,
                revision = revision + 1
            where id = %s and revision = %s and status = 'OUTCOME_UNCERTAIN'
              and reconcile_state = 'RUNNING'
            returning {NODE_RECORD_COLUMNS}
            """,
            (
                "INCONCLUSIVE" if retry_at is not None else "EXHAUSTED",
                retry_at,
                category,
                code,
                detail,
                node.id,
                node.revision,
            ),
        )
        row = await cursor.fetchone()
        return DataNodeRunRecord.model_validate(row) if row is not None else None

    async def fail_reconciled_node(
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
                failure_detail = %s, finished_at = %s, revision = revision + 1
            where id = %s and revision = %s and status = 'VERIFYING'
              and reconcile_state = 'FOUND'
            returning {NODE_RECORD_COLUMNS}
            """,
            (category, code, detail, finished_at, node.id, node.revision),
        )
        row = await cursor.fetchone()
        return DataNodeRunRecord.model_validate(row) if row is not None else None

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
        status: ResourceRecordStatus = ResourceRecordStatus.ACTIVE,
    ) -> None:
        cursor = await connection.execute(
            """
            insert into atlas.resource_record (
              id, tenant_id, project_id, environment_id, fixture_run_id,
              data_node_run_id, data_node_attempt_id,
              connector_installation_id, resource_handle, resource_type,
              ownership, opaque_ref, opaque_ref_hash, status, expires_at,
              next_cleanup_at,
              cleanup_operation_key, cleanup_operation_version, created_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s
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
                status,
                expires_at,
                recorded_at
                if status
                in {
                    ResourceRecordStatus.CLEANUP_PENDING,
                    ResourceRecordStatus.BLOCKED_BY_CHILD,
                    ResourceRecordStatus.ORPHAN_SUSPECTED,
                }
                else None,
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
                failure_detail = %s, finished_at = %s,
                reconcile_state = case when %s then 'PENDING' else reconcile_state end,
                next_reconcile_at = case when %s then %s else next_reconcile_at end,
                revision = revision + 1
            where id = %s and revision = %s and status in ('RUNNING', 'VERIFYING')
            returning {NODE_RECORD_COLUMNS}
            """,
            (
                status,
                category,
                code,
                detail,
                finished_at,
                status is DataNodeRunStatus.OUTCOME_UNCERTAIN,
                status is DataNodeRunStatus.OUTCOME_UNCERTAIN,
                finished_at,
                node.id,
                node.revision,
            ),
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
        attempt_cursor = await connection.execute(
            f"""
            select {CLEANUP_ATTEMPT_COLUMNS}
            from atlas.resource_cleanup_attempt
            where fixture_run_id = %s
            order by started_at, id
            """,
            (run_id,),
        )
        return FixtureResourcePage(
            items=tuple(ResourceRecord.model_validate(row) for row in await cursor.fetchall()),
            cleanup_attempts=tuple(
                ResourceCleanupAttempt.model_validate(row)
                for row in await attempt_cursor.fetchall()
            ),
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
            set status = 'CLEANUP_PENDING', next_cleanup_at = clock_timestamp(),
                revision = revision + 1
            where fixture_run_id = %s and ownership = 'CREATED' and status = 'ACTIVE'
            """,
            (run.id,),
        )
        cursor = await connection.execute(
            f"""
            update atlas.fixture_run
            set status = 'CLEANING', terminal_intent = 'RELEASED',
                cleanup_state = case
                  when cleanup_state = 'NOT_REQUIRED' then 'NOT_REQUIRED'
                  else 'RUNNING'
                end,
                cleanup_generation = cleanup_generation + 1,
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
            set status = 'CLEANUP_PENDING', next_cleanup_at = clock_timestamp(),
                revision = revision + 1
            where fixture_run_id = %s and ownership = 'CREATED' and status = 'ACTIVE'
            """,
            (run.id,),
        )
        cursor = await connection.execute(
            f"""
            update atlas.fixture_run
            set status = 'CLEANING', terminal_intent = 'FAILED',
                cleanup_state = case
                  when cleanup_state = 'NOT_REQUIRED' then 'NOT_REQUIRED'
                  else 'RUNNING'
                end,
                cleanup_generation = cleanup_generation + 1,
                failure_category = %s,
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

    async def request_cancellation(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRun,
        requested_by: UUID,
        requested_at: datetime,
    ) -> FixtureRun | None:
        cursor = await connection.execute(
            f"""
            update atlas.fixture_run
            set cancel_requested_at = coalesce(cancel_requested_at, %s),
                cancel_requested_by = coalesce(cancel_requested_by, %s),
                revision = revision + 1
            where id = %s and revision = %s
              and status in ('REQUESTED', 'RUNNING', 'READY')
            returning {RUN_COLUMNS}
            """,
            (requested_at, requested_by, run.id, run.revision),
        )
        row = await cursor.fetchone()
        return FixtureRun.model_validate(row) if row is not None else None

    async def list_cancel_requested_run_ids(
        self,
        connection: AsyncConnection[DictRow],
        *,
        limit: int,
    ) -> tuple[UUID, ...]:
        cursor = await connection.execute(
            """
            select id
            from atlas.fixture_run
            where cancel_requested_at is not null
              and status in ('REQUESTED', 'RUNNING', 'READY')
            order by cancel_requested_at, id
            limit %s
            """,
            (limit,),
        )
        return tuple(row["id"] for row in await cursor.fetchall())

    async def begin_canceled_cleanup(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
    ) -> FixtureRun | None:
        await connection.execute(
            """
            update atlas.resource_record
            set status = 'CLEANUP_PENDING', next_cleanup_at = clock_timestamp(),
                revision = revision + 1
            where fixture_run_id = %s and ownership = 'CREATED' and status = 'ACTIVE'
            """,
            (run.id,),
        )
        cursor = await connection.execute(
            f"""
            update atlas.fixture_run
            set status = 'CLEANING', terminal_intent = 'CANCELED',
                cleanup_state = case
                  when cleanup_state = 'NOT_REQUIRED' then 'NOT_REQUIRED'
                  else 'RUNNING'
                end,
                cleanup_generation = cleanup_generation + 1,
                started_at = coalesce(started_at, requested_at),
                revision = revision + 1
            where id = %s and revision = %s
              and status in ('REQUESTED', 'RUNNING', 'READY')
            returning {RUN_COLUMNS}
            """,
            (run.id, run.revision),
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
              and resource.status in (
                'CLEANUP_PENDING', 'CLEANING', 'BLOCKED_BY_CHILD', 'ORPHAN_SUSPECTED'
              )
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
        attempt_id: UUID,
        worker_identity: str,
        started_at: datetime,
        blocked_retry_at: datetime,
    ) -> tuple[ResourceRecordInternal, ResourceCleanupAttempt] | None:
        blocked = await connection.execute(
            """
            update atlas.resource_record
            set status = 'BLOCKED_BY_CHILD', next_cleanup_at = %s,
                revision = revision + 1
            where id = %s and revision = %s
              and status in ('CLEANUP_PENDING', 'ORPHAN_SUSPECTED', 'BLOCKED_BY_CHILD')
              and exists (
                select 1
                from atlas.resource_dependency as dependency
                join atlas.resource_record as child
                  on child.id = dependency.child_resource_id
                where dependency.parent_resource_id = atlas.resource_record.id
                  and child.status <> 'CLEANED'
              )
            returning id
            """,
            (blocked_retry_at, resource_id, expected_revision),
        )
        if await blocked.fetchone() is not None:
            return None
        cursor = await connection.execute(
            f"""
            update atlas.resource_record
            set status = 'CLEANING', cleanup_generation = cleanup_generation + 1,
                next_cleanup_at = null, revision = revision + 1
            where id = %s and revision = %s
              and status in ('CLEANUP_PENDING', 'ORPHAN_SUSPECTED')
              and (next_cleanup_at is null or next_cleanup_at <= %s)
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
            (resource_id, expected_revision, started_at),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        resource = ResourceRecordInternal.model_validate(row)
        attempt_cursor = await connection.execute(
            f"""
            insert into atlas.resource_cleanup_attempt (
              id, tenant_id, project_id, environment_id, fixture_run_id,
              resource_record_id, cleanup_generation, worker_identity, started_at
            )
            select %s, tenant_id, project_id, environment_id, fixture_run_id,
                   id, cleanup_generation, %s, %s
            from atlas.resource_record
            where id = %s
            returning {CLEANUP_ATTEMPT_COLUMNS}
            """,
            (
                attempt_id,
                worker_identity,
                started_at,
                resource.id,
            ),
        )
        attempt_row = await attempt_cursor.fetchone()
        if attempt_row is None:
            raise RuntimeError("resource cleanup attempt insert did not return a row")
        return resource, ResourceCleanupAttempt.model_validate(attempt_row)

    async def complete_resource_cleanup(
        self,
        connection: AsyncConnection[DictRow],
        *,
        resource: ResourceRecordInternal,
        attempt: ResourceCleanupAttempt,
        provider_request_id: str | None,
        cleaned_at: datetime,
    ) -> ResourceRecord | None:
        await connection.execute(
            """
            update atlas.resource_cleanup_attempt
            set status = 'SUCCEEDED', provider_request_id = %s, finished_at = %s
            where id = %s and status = 'RUNNING'
            """,
            (provider_request_id, cleaned_at, attempt.id),
        )
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

    async def prepare_cleanup_retry(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
        requested_at: datetime,
    ) -> FixtureRun | None:
        await connection.execute(
            """
            update atlas.resource_record
            set status = 'CLEANUP_PENDING', next_cleanup_at = %s,
                revision = revision + 1
            where fixture_run_id = %s and ownership = 'CREATED'
              and status in (
                'LEAKED', 'BLOCKED_BY_CHILD', 'ORPHAN_SUSPECTED', 'CLEANUP_PENDING'
              )
            """,
            (requested_at, run.id),
        )
        cursor = await connection.execute(
            f"""
            update atlas.fixture_run
            set status = 'CLEANING', cleanup_state = case
                  when cleanup_state = 'NOT_REQUIRED' then 'NOT_REQUIRED'
                  else 'RUNNING'
                end,
                cleanup_generation = cleanup_generation + 1,
                revision = revision + 1
            where id = %s and revision = %s and status = 'CLEANING'
              and terminal_intent is not null
            returning {RUN_RECORD_COLUMNS}
            """,
            (run.id, run.revision),
        )
        row = await cursor.fetchone()
        return FixtureRunRecord.model_validate(row) if row is not None else None

    async def queue_expired_resources(
        self,
        connection: AsyncConnection[DictRow],
        *,
        now: datetime,
        limit: int,
        run_id: UUID | None = None,
    ) -> int:
        cursor = await connection.execute(
            """
            with candidates as (
              select id
              from atlas.resource_record
              where ownership = 'CREATED' and status = 'ACTIVE'
                and expires_at <= %s
                and (%s::uuid is null or fixture_run_id = %s)
              order by expires_at, fixture_run_id, id
              limit %s
              for update skip locked
            )
            update atlas.resource_record as resource
            set status = 'ORPHAN_SUSPECTED', next_cleanup_at = %s,
                revision = resource.revision + 1
            from candidates
            where resource.id = candidates.id
            returning resource.id
            """,
            (now, run_id, run_id, limit, now),
        )
        return len(await cursor.fetchall())

    async def list_due_cleanup_resources(
        self,
        connection: AsyncConnection[DictRow],
        *,
        now: datetime,
        limit: int,
        run_id: UUID | None = None,
    ) -> tuple[ResourceRecordInternal, ...]:
        cursor = await connection.execute(
            f"""
            select {RESOURCE_INTERNAL_COLUMNS}
            from atlas.resource_record
            where ownership = 'CREATED'
              and status in ('CLEANUP_PENDING', 'BLOCKED_BY_CHILD', 'ORPHAN_SUSPECTED')
              and next_cleanup_at <= %s
              and (%s::uuid is null or fixture_run_id = %s)
            order by next_cleanup_at, fixture_run_id, id
            limit %s
            """,
            (now, run_id, run_id, limit),
        )
        return tuple(ResourceRecordInternal.model_validate(row) for row in await cursor.fetchall())

    async def recover_stale_cleanup_claims(
        self,
        connection: AsyncConnection[DictRow],
        *,
        stale_before: datetime,
        retry_at: datetime,
        max_attempts: int,
        limit: int,
    ) -> tuple[int, int]:
        cursor = await connection.execute(
            """
            with candidates as (
              select attempt.id, attempt.resource_record_id
              from atlas.resource_cleanup_attempt as attempt
              join atlas.resource_record as resource
                on resource.id = attempt.resource_record_id
              where attempt.status = 'RUNNING' and attempt.started_at <= %s
                and resource.status = 'CLEANING'
              order by attempt.started_at, attempt.id
              limit %s
              for update of attempt, resource skip locked
            ), failed_attempts as (
              update atlas.resource_cleanup_attempt as attempt
              set status = 'OUTCOME_UNCERTAIN', failure_category = 'UNCERTAIN',
                  failure_code = 'CLEANUP_CLAIM_EXPIRED',
                  failure_detail = 'The prior cleanup claim expired without a durable outcome.',
                  finished_at = %s
              from candidates
              where attempt.id = candidates.id
              returning candidates.resource_record_id
            )
            update atlas.resource_record as resource
            set status = case
                  when resource.cleanup_generation >= %s then 'LEAKED'
                  else 'CLEANUP_PENDING'
                end,
                next_cleanup_at = case
                  when resource.cleanup_generation >= %s then null
                  else %s
                end,
                revision = resource.revision + 1
            from failed_attempts
            where resource.id = failed_attempts.resource_record_id
              and resource.status = 'CLEANING'
            returning resource.status
            """,
            (
                stale_before,
                limit,
                retry_at,
                max_attempts,
                max_attempts,
                retry_at,
            ),
        )
        rows = await cursor.fetchall()
        leaked = sum(row["status"] == "LEAKED" for row in rows)
        return len(rows) - leaked, leaked

    async def list_cleanup_run_ids(
        self,
        connection: AsyncConnection[DictRow],
        *,
        limit: int,
        run_id: UUID | None = None,
    ) -> tuple[UUID, ...]:
        cursor = await connection.execute(
            """
            select id
            from atlas.fixture_run
            where status = 'CLEANING'
              and cleanup_state in ('PENDING', 'RUNNING', 'LEAKED')
              and (%s::uuid is null or id = %s)
            order by updated_at, id
            limit %s
            """,
            (run_id, run_id, limit),
        )
        return tuple(row["id"] for row in await cursor.fetchall())

    async def count_exhausted_reconciliations(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run_id: UUID,
    ) -> int:
        cursor = await connection.execute(
            """
            select count(*) as total
            from atlas.data_node_run
            where fixture_run_id = %s and status = 'OUTCOME_UNCERTAIN'
              and reconcile_state = 'EXHAUSTED'
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        return int(row["total"]) if row is not None else 0

    async def fail_resource_cleanup(
        self,
        connection: AsyncConnection[DictRow],
        *,
        resource: ResourceRecordInternal,
        attempt: ResourceCleanupAttempt,
        status: ResourceCleanupAttemptStatus,
        category: FixtureFailureCategory,
        code: str,
        detail: str,
        provider_request_id: str | None,
        finished_at: datetime,
        retry_at: datetime | None,
    ) -> ResourceRecord | None:
        await connection.execute(
            """
            update atlas.resource_cleanup_attempt
            set status = %s, failure_category = %s, failure_code = %s,
                failure_detail = %s, provider_request_id = %s, finished_at = %s
            where id = %s and status = 'RUNNING'
            """,
            (
                status,
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
            update atlas.resource_record
            set status = %s, next_cleanup_at = %s, revision = revision + 1
            where id = %s and revision = %s and status = 'CLEANING'
            returning {RESOURCE_COLUMNS}
            """,
            (
                ResourceRecordStatus.CLEANUP_PENDING
                if retry_at is not None
                else ResourceRecordStatus.LEAKED,
                retry_at,
                resource.id,
                resource.revision,
            ),
        )
        row = await cursor.fetchone()
        return ResourceRecord.model_validate(row) if row is not None else None

    async def finalize_release(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: FixtureRunRecord,
        finished_at: datetime,
        cleanup_evidence: tuple[FixtureValidationEvidence, ...] = (),
    ) -> FixtureRun | None:
        cursor = await connection.execute(
            """
            select count(*) filter (
                     where status in (
                       'ACTIVE', 'CLEANUP_PENDING', 'CLEANING',
                       'BLOCKED_BY_CHILD', 'ORPHAN_SUSPECTED'
                     )
                   ) as retryable,
                   count(*) filter (where status = 'LEAKED') as leaked
            from atlas.resource_record
            where fixture_run_id = %s and ownership = 'CREATED'
            """,
            (run.id,),
        )
        counts = await cursor.fetchone()
        retryable = int(counts["retryable"]) if counts is not None else 0
        leaked = int(counts["leaked"]) if counts is not None else 0
        reconcile_cursor = await connection.execute(
            """
            select count(*) filter (
                     where reconcile_state in ('PENDING', 'RUNNING', 'INCONCLUSIVE')
                   ) as retryable,
                   count(*) filter (where reconcile_state = 'EXHAUSTED') as leaked
            from atlas.data_node_run
            where fixture_run_id = %s and status = 'OUTCOME_UNCERTAIN'
            """,
            (run.id,),
        )
        reconcile_counts = await reconcile_cursor.fetchone()
        if reconcile_counts is not None:
            retryable += int(reconcile_counts["retryable"])
            leaked += int(reconcile_counts["leaked"])
        if retryable > 0:
            result = await connection.execute(
                f"""
                update atlas.fixture_run
                set cleanup_state = 'PENDING', revision = revision + 1
                where id = %s and revision = %s and status = 'CLEANING'
                returning {RUN_COLUMNS}
                """,
                (run.id, run.revision),
            )
            row = await result.fetchone()
            return FixtureRun.model_validate(row) if row is not None else None

        intent = run.terminal_intent
        if intent is None:
            raise RuntimeError("cleaning fixture run has no terminal intent")
        if leaked:
            status = (
                FixtureRunStatus.CLEANUP_FAILED
                if intent is FixtureRunTerminalIntent.RELEASED
                else FixtureRunStatus(intent.value)
            )
            cleanup_state = FixtureCleanupState.LEAKED
        else:
            status = FixtureRunStatus(intent.value)
            cleanup_state = (
                FixtureCleanupState.NOT_REQUIRED
                if run.cleanup_state is FixtureCleanupState.NOT_REQUIRED
                else FixtureCleanupState.CLEANED
            )
            for item in cleanup_evidence:
                await self._record_cleanup_evidence(
                    connection,
                    evidence=item,
                    observed_at=finished_at,
                )
        await self._release_bound_leases(
            connection,
            run_id=run.id,
            reason=LeaseReleaseReason.CLEANUP_FAILED if leaked else LeaseReleaseReason.COMPLETED,
            released_at=finished_at,
        )
        result = await connection.execute(
            f"""
            update atlas.fixture_run
            set status = %s, cleanup_state = %s, finished_at = coalesce(finished_at, %s),
                released_at = %s,
                failure_category = %s, failure_code = %s, failure_detail = %s,
                revision = revision + 1
            where id = %s and revision = %s and status = 'CLEANING'
            returning {RUN_COLUMNS}
            """,
            (
                status,
                cleanup_state,
                finished_at,
                finished_at if status is FixtureRunStatus.RELEASED else None,
                (
                    FixtureFailureCategory.CLEANUP
                    if status is FixtureRunStatus.CLEANUP_FAILED
                    else (
                        None
                        if intent is FixtureRunTerminalIntent.RELEASED
                        else run.failure_category
                    )
                ),
                (
                    "FIXTURE_CLEANUP_INCOMPLETE"
                    if status is FixtureRunStatus.CLEANUP_FAILED
                    else (
                        None if intent is FixtureRunTerminalIntent.RELEASED else run.failure_code
                    )
                ),
                (
                    "One or more fixture resources could not be cleaned."
                    if status is FixtureRunStatus.CLEANUP_FAILED
                    else (
                        None
                        if intent is FixtureRunTerminalIntent.RELEASED
                        else run.failure_detail
                    )
                ),
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
        return await self.finalize_release(
            connection,
            run=run,
            finished_at=finished_at,
        )

    async def _record_cleanup_evidence(
        self,
        connection: AsyncConnection[DictRow],
        *,
        evidence: FixtureValidationEvidence,
        observed_at: datetime,
    ) -> None:
        atom_version_id = (
            evidence.subject_version_id
            if evidence.subject is ValidationEvidenceSubject.ATOM_VERSION
            else None
        )
        blueprint_version_id = (
            evidence.subject_version_id
            if evidence.subject is ValidationEvidenceSubject.BLUEPRINT_VERSION
            else None
        )
        evidence_cursor = await connection.execute(
            """
            insert into atlas.fixture_validation_evidence (
              id, tenant_id, project_id, environment_id, fixture_run_id,
              kind, subject, atom_version_id, blueprint_version_id,
              subject_digest, passed, safe_summary, observed_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning id
            """,
            (
                evidence.id,
                evidence.tenant_id,
                evidence.project_id,
                evidence.environment_id,
                evidence.fixture_run_id,
                evidence.kind,
                evidence.subject,
                atom_version_id,
                blueprint_version_id,
                evidence.subject_digest,
                evidence.passed,
                evidence.safe_summary,
                evidence.observed_at,
            ),
        )
        evidence_row = await evidence_cursor.fetchone()
        if evidence_row is None:
            subject_column = (
                "atom_version_id"
                if evidence.subject is ValidationEvidenceSubject.ATOM_VERSION
                else "blueprint_version_id"
            )
            existing_cursor = await connection.execute(
                f"""
                select id
                from atlas.fixture_validation_evidence
                where fixture_run_id = %s and kind = %s
                  and {subject_column} = %s
                """,
                (evidence.fixture_run_id, evidence.kind, evidence.subject_version_id),
            )
            evidence_row = await existing_cursor.fetchone()
            if evidence_row is None:
                raise RuntimeError("fixture cleanup evidence could not be resolved")
        stored_evidence_id = evidence_row["id"]
        table = (
            "data_atom_version"
            if evidence.subject is ValidationEvidenceSubject.ATOM_VERSION
            else "data_blueprint_version"
        )
        digest_column = "content_digest" if table == "data_atom_version" else "plan_digest"
        cursor = await connection.execute(
            f"""
            update atlas.{table}
            set cleanup_validation_state = 'PASSED',
                cleanup_validation_evidence_id = %s,
                cleanup_validated_at = %s,
                revision = revision + 1
            where id = %s and {digest_column} = %s
            returning id
            """,
            (
                stored_evidence_id,
                observed_at,
                evidence.subject_version_id,
                evidence.subject_digest,
            ),
        )
        if await cursor.fetchone() is None:
            raise RuntimeError("fixture cleanup evidence subject changed during execution")

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
