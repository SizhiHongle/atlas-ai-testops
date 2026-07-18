"""PostgreSQL repository for immutable Attempt, Unit, and Task Result truth."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.domain.result import (
    AttemptClosureNotice,
    AttemptFixtureBinding,
    AttemptSeal,
    FailureClassificationRevision,
    FailureClusterRevision,
    ResultIntegrityIncident,
    ResultRef,
    TaskGateDecision,
    TaskResultReevaluationCommand,
    TaskResultSnapshot,
    TaskResultSnapshotFinality,
    UnitHygieneResolutionRevision,
    UnitResolutionRevision,
    task_gate_decision_document,
    task_result_snapshot_document,
)

RESULT_REF_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, execution_unit_id, unit_attempt_id, "
    "seal_id, seal_content_hash, created_at"
)
UNIT_RESOLUTION_COLUMNS = (
    "id, unit_resolution_id, tenant_id, project_id, task_run_id, "
    "execution_unit_id, manifest_hash, unit_key, revision, input_seal_ids, "
    "input_closure_notice_ids, input_set_hash, effective_verdict, "
    "outcome_class, closure_reason, data_hygiene, evidence_completeness, "
    "evidence_integrity, execution_influence, stability, "
    "decisive_unit_attempt_id, decisive_attempt_number, "
    "resolution_policy_version, resolution_policy_digest, "
    "supersedes_revision_id, created_at"
)
UNIT_RESOLUTION_JOIN_COLUMNS = ", ".join(
    f"resolution.{column.strip()}" for column in UNIT_RESOLUTION_COLUMNS.split(",")
)
ATTEMPT_FIXTURE_BINDING_COLUMNS = (
    "id, tenant_id, project_id, task_run_id, execution_unit_id, "
    "unit_attempt_id, fixture_run_id, fixture_blueprint_version_id, "
    "environment_id, fixture_plan_digest, created_at, binding_hash"
)
ATTEMPT_FIXTURE_BINDING_JOIN_COLUMNS = (
    "binding.id, binding.tenant_id, binding.project_id, binding.task_run_id, "
    "binding.execution_unit_id, binding.unit_attempt_id, binding.fixture_run_id, "
    "binding.fixture_blueprint_version_id, binding.environment_id, "
    "binding.fixture_plan_digest, binding.created_at, binding.binding_hash"
)
UNIT_HYGIENE_RESOLUTION_COLUMNS = (
    "id, unit_hygiene_resolution_id, tenant_id, project_id, task_run_id, "
    "execution_unit_id, manifest_hash, unit_key, revision, inputs, "
    "input_set_hash, data_hygiene, resolution_policy_version, "
    "resolution_policy_digest, supersedes_revision_id, projection_watermark, "
    "created_at, resolution_hash"
)
UNIT_HYGIENE_RESOLUTION_JOIN_COLUMNS = ", ".join(
    f"resolution.{column.strip()}" for column in UNIT_HYGIENE_RESOLUTION_COLUMNS.split(",")
)
UNIT_HYGIENE_RESOLUTION_LATEST_COLUMNS = (
    "latest.id, latest.unit_hygiene_resolution_id, latest.tenant_id, "
    "latest.project_id, latest.task_run_id, latest.execution_unit_id, "
    "latest.manifest_hash, latest.unit_key, latest.revision, latest.inputs, "
    "latest.input_set_hash, latest.data_hygiene, "
    "latest.resolution_policy_version, latest.resolution_policy_digest, "
    "latest.supersedes_revision_id, latest.projection_watermark, "
    "latest.created_at, latest.resolution_hash"
)


class ResultFactRepository:
    """Persist and replay append-only Result facts and deterministic projections."""

    async def get_seal_by_attempt(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
    ) -> AttemptSeal | None:
        """Load the accepted signed Seal for an exact UnitAttempt."""

        cursor = await connection.execute(
            """
            select seal
            from atlas.unit_attempt_result_fact
            where unit_attempt_id = %s
            """,
            (unit_attempt_id,),
        )
        row = await cursor.fetchone()
        return AttemptSeal.model_validate(row["seal"]) if row is not None else None

    async def get_ref_by_attempt(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
    ) -> ResultRef | None:
        """Load the stable opaque ResultRef for an exact UnitAttempt."""

        cursor = await connection.execute(
            f"""
            select {RESULT_REF_COLUMNS}
            from atlas.result_ref
            where unit_attempt_id = %s
            """,
            (unit_attempt_id,),
        )
        row = await cursor.fetchone()
        return ResultRef.model_validate(row) if row is not None else None

    async def get_closure_by_attempt(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
    ) -> AttemptClosureNotice | None:
        """Load the exact no-Seal terminal fact for one UnitAttempt."""

        cursor = await connection.execute(
            """
            select notice
            from atlas.attempt_closure_notice
            where unit_attempt_id = %s
            """,
            (unit_attempt_id,),
        )
        row = await cursor.fetchone()
        return AttemptClosureNotice.model_validate(row["notice"]) if row is not None else None

    async def list_seals_for_unit(
        self,
        connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
    ) -> tuple[AttemptSeal, ...]:
        """Load accepted Seals in physical Attempt order."""

        cursor = await connection.execute(
            """
            select fact.seal
            from atlas.unit_attempt_result_fact fact
            join atlas.unit_attempt attempt
              on attempt.id = fact.unit_attempt_id
            where fact.execution_unit_id = %s
            order by attempt.attempt_number
            """,
            (execution_unit_id,),
        )
        rows = await cursor.fetchall()
        return tuple(AttemptSeal.model_validate(row["seal"]) for row in rows)

    async def list_closures_for_unit(
        self,
        connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
    ) -> tuple[AttemptClosureNotice, ...]:
        """Load no-Seal terminal facts in physical Attempt order."""

        cursor = await connection.execute(
            """
            select notice.notice
            from atlas.attempt_closure_notice notice
            where notice.execution_unit_id = %s
            order by notice.attempt_number
            """,
            (execution_unit_id,),
        )
        rows = await cursor.fetchall()
        return tuple(AttemptClosureNotice.model_validate(row["notice"]) for row in rows)

    async def get_latest_resolution(
        self,
        connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
    ) -> UnitResolutionRevision | None:
        """Load the current append-only Unit interpretation."""

        cursor = await connection.execute(
            f"""
            select {UNIT_RESOLUTION_COLUMNS}
            from atlas.unit_resolution_revision
            where execution_unit_id = %s
            order by revision desc
            limit 1
            """,
            (execution_unit_id,),
        )
        row = await cursor.fetchone()
        return UnitResolutionRevision.model_validate(row) if row is not None else None

    async def get_resolution_revision(
        self,
        connection: AsyncConnection[DictRow],
        *,
        execution_unit_id: UUID,
        revision: int,
    ) -> UnitResolutionRevision | None:
        """Load one exact immutable Unit Resolution revision."""

        cursor = await connection.execute(
            f"""
            select {UNIT_RESOLUTION_COLUMNS}
            from atlas.unit_resolution_revision
            where execution_unit_id = %s and revision = %s
            """,
            (execution_unit_id, revision),
        )
        row = await cursor.fetchone()
        return UnitResolutionRevision.model_validate(row) if row is not None else None

    async def list_latest_resolutions_for_task(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> tuple[UnitResolutionRevision, ...]:
        """Load one latest Resolution per Unit in immutable Manifest ordinal order."""

        cursor = await connection.execute(
            """
            select latest.*
            from (
              select distinct on (resolution.execution_unit_id)
                resolution.*
              from atlas.unit_resolution_revision resolution
              where resolution.task_run_id = %s
              order by resolution.execution_unit_id, resolution.revision desc
            ) latest
            join atlas.execution_unit unit
              on unit.id = latest.execution_unit_id
             and unit.task_run_id = latest.task_run_id
             and unit.tenant_id = latest.tenant_id
             and unit.project_id = latest.project_id
            order by unit.ordinal, unit.id
            """,
            (task_run_id,),
        )
        return tuple(UnitResolutionRevision.model_validate(row) for row in await cursor.fetchall())

    async def get_latest_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> TaskResultSnapshot | None:
        """Load the latest immutable Task result revision."""

        cursor = await connection.execute(
            """
            select snapshot
            from atlas.task_result_snapshot
            where task_run_id = %s
            order by revision desc
            limit 1
            """,
            (task_run_id,),
        )
        row = await cursor.fetchone()
        return TaskResultSnapshot.model_validate(row["snapshot"]) if row is not None else None

    async def list_resolutions_by_ids(
        self,
        connection: AsyncConnection[DictRow],
        resolution_ids: tuple[UUID, ...],
    ) -> tuple[UnitResolutionRevision, ...]:
        """Load exact UnitResolution revisions in caller-supplied Snapshot order."""

        cursor = await connection.execute(
            f"""
            select {UNIT_RESOLUTION_JOIN_COLUMNS}
            from unnest(%s::uuid[]) with ordinality requested(id, ordinal)
            join atlas.unit_resolution_revision resolution
              on resolution.id = requested.id
            order by requested.ordinal
            """,
            (list(resolution_ids),),
        )
        return tuple(UnitResolutionRevision.model_validate(row) for row in await cursor.fetchall())

    async def list_hygiene_resolutions_by_ids(
        self,
        connection: AsyncConnection[DictRow],
        resolution_ids: tuple[UUID, ...],
    ) -> tuple[UnitHygieneResolutionRevision, ...]:
        """Load exact Hygiene revisions in caller-supplied Snapshot order."""

        cursor = await connection.execute(
            f"""
            select {UNIT_HYGIENE_RESOLUTION_JOIN_COLUMNS}
            from unnest(%s::uuid[]) with ordinality requested(id, ordinal)
            join atlas.unit_hygiene_resolution_revision resolution
              on resolution.id = requested.id
            order by requested.ordinal
            """,
            (list(resolution_ids),),
        )
        return tuple(
            UnitHygieneResolutionRevision.model_validate(row) for row in await cursor.fetchall()
        )

    async def get_snapshot_by_id(
        self,
        connection: AsyncConnection[DictRow],
        snapshot_id: UUID,
    ) -> TaskResultSnapshot | None:
        """Load one exact immutable Task result revision."""

        cursor = await connection.execute(
            """
            select snapshot
            from atlas.task_result_snapshot
            where id = %s
            """,
            (snapshot_id,),
        )
        row = await cursor.fetchone()
        return TaskResultSnapshot.model_validate(row["snapshot"]) if row is not None else None

    async def lock_failure_classification_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        snapshot_id: UUID,
    ) -> None:
        """Serialize deterministic Cluster materialization for one Snapshot."""

        await connection.execute(
            """
            select pg_advisory_xact_lock(
              hashtextextended(%s::text, 1)
            )
            """,
            (snapshot_id,),
        )

    async def get_latest_snapshot_for_finality(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
        finality: TaskResultSnapshotFinality,
    ) -> TaskResultSnapshot | None:
        """Load the latest revision for one explicit finality without crossing phases."""

        cursor = await connection.execute(
            """
            select snapshot
            from atlas.task_result_snapshot
            where task_run_id = %s and finality = %s
            order by revision desc
            limit 1
            """,
            (task_run_id, finality),
        )
        row = await cursor.fetchone()
        return TaskResultSnapshot.model_validate(row["snapshot"]) if row is not None else None

    async def get_reevaluated_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        source_snapshot_id: UUID,
        policy_digest: str,
    ) -> TaskResultSnapshot | None:
        """Load the unique output for one source Snapshot and frozen Policy."""

        cursor = await connection.execute(
            """
            select snapshot
            from atlas.task_result_snapshot
            where task_run_id = %s
              and finality = 'REEVALUATED'
              and reevaluation_source_snapshot_id = %s
              and aggregation_policy_digest = %s
            order by revision desc
            limit 1
            """,
            (task_run_id, source_snapshot_id, policy_digest),
        )
        row = await cursor.fetchone()
        return TaskResultSnapshot.model_validate(row["snapshot"]) if row is not None else None

    async def get_reevaluation_command(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        client_mutation_id: str,
    ) -> TaskResultReevaluationCommand | None:
        """Load the permanent command fact bound to one mutation identity."""

        cursor = await connection.execute(
            """
            select command
            from atlas.task_result_reevaluation_command
            where task_run_id = %s and client_mutation_id = %s
            """,
            (task_run_id, client_mutation_id),
        )
        row = await cursor.fetchone()
        return (
            TaskResultReevaluationCommand.model_validate(row["command"])
            if row is not None
            else None
        )

    async def get_failure_cluster(
        self,
        connection: AsyncConnection[DictRow],
        *,
        result_snapshot_id: UUID,
        fingerprint: str,
        policy_digest: str,
    ) -> FailureClusterRevision | None:
        """Load the unique initial cluster for one Snapshot and fingerprint."""

        cursor = await connection.execute(
            """
            select cluster
            from atlas.failure_cluster_revision
            where result_snapshot_id = %s
              and fingerprint = %s
              and fingerprint_policy_digest = %s
            order by revision desc
            limit 1
            """,
            (result_snapshot_id, fingerprint, policy_digest),
        )
        row = await cursor.fetchone()
        return FailureClusterRevision.model_validate(row["cluster"]) if row is not None else None

    async def get_failure_cluster_by_revision_id(
        self,
        connection: AsyncConnection[DictRow],
        cluster_revision_id: UUID,
    ) -> FailureClusterRevision | None:
        """Load one exact immutable FailureCluster revision."""

        cursor = await connection.execute(
            """
            select cluster
            from atlas.failure_cluster_revision
            where id = %s
            """,
            (cluster_revision_id,),
        )
        row = await cursor.fetchone()
        return FailureClusterRevision.model_validate(row["cluster"]) if row is not None else None

    async def get_latest_failure_classification_for_cluster(
        self,
        connection: AsyncConnection[DictRow],
        cluster_revision_id: UUID,
    ) -> FailureClassificationRevision | None:
        """Load the latest judgment for one exact immutable Cluster revision."""

        cursor = await connection.execute(
            """
            select classification
            from atlas.failure_classification_revision
            where failure_cluster_revision_id = %s
            order by revision desc
            limit 1
            """,
            (cluster_revision_id,),
        )
        row = await cursor.fetchone()
        return (
            FailureClassificationRevision.model_validate(row["classification"])
            if row is not None
            else None
        )

    async def get_latest_failure_classification_for_update(
        self,
        connection: AsyncConnection[DictRow],
        failure_classification_id: UUID,
    ) -> FailureClassificationRevision | None:
        """Serialize one append-only chain and load its latest revision."""

        await connection.execute(
            """
            select pg_advisory_xact_lock(
              hashtextextended(%s::text, 0)
            )
            """,
            (failure_classification_id,),
        )
        cursor = await connection.execute(
            """
            select classification
            from atlas.failure_classification_revision
            where failure_classification_id = %s
            order by revision desc
            limit 1
            """,
            (failure_classification_id,),
        )
        row = await cursor.fetchone()
        return (
            FailureClassificationRevision.model_validate(row["classification"])
            if row is not None
            else None
        )

    async def list_current_gate_classifications(
        self,
        connection: AsyncConnection[DictRow],
        result_snapshot_id: UUID,
    ) -> tuple[
        tuple[FailureClusterRevision, FailureClassificationRevision | None],
        ...,
    ]:
        """Load latest Cluster revisions and latest bound judgments canonically."""

        cursor = await connection.execute(
            """
            with latest_clusters as (
              select distinct on (source.failure_cluster_id)
                source.id,
                source.failure_cluster_id,
                source.fingerprint,
                source.cluster
              from atlas.failure_cluster_revision source
              where source.result_snapshot_id = %s
              order by source.failure_cluster_id, source.revision desc
            )
            select latest.cluster, judgment.classification
            from latest_clusters latest
            left join lateral (
              select source.classification
              from atlas.failure_classification_revision source
              where source.failure_cluster_revision_id = latest.id
              order by source.revision desc
              limit 1
            ) judgment on true
            order by latest.fingerprint, latest.failure_cluster_id, latest.id
            """,
            (result_snapshot_id,),
        )
        rows = await cursor.fetchall()
        return tuple(
            (
                FailureClusterRevision.model_validate(row["cluster"]),
                (
                    FailureClassificationRevision.model_validate(row["classification"])
                    if row["classification"] is not None
                    else None
                ),
            )
            for row in rows
        )

    async def lock_failure_classification_chains(
        self,
        connection: AsyncConnection[DictRow],
        failure_classification_ids: tuple[UUID, ...],
    ) -> None:
        """Fence concurrent human reviews while one Gate freezes latest judgments."""

        for classification_id in sorted(set(failure_classification_ids)):
            await connection.execute(
                """
                select pg_advisory_xact_lock(
                  hashtextextended(%s::text, 0)
                )
                """,
                (classification_id,),
            )

    async def get_latest_task_gate_for_update(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> TaskGateDecision | None:
        """Serialize one Task Gate chain and load its latest immutable decision."""

        await connection.execute(
            """
            select pg_advisory_xact_lock(
              hashtextextended(%s::text, 2)
            )
            """,
            (task_run_id,),
        )
        cursor = await connection.execute(
            """
            select decision_document
            from atlas.task_gate_decision
            where task_run_id = %s
            order by revision desc
            limit 1
            """,
            (task_run_id,),
        )
        row = await cursor.fetchone()
        return (
            TaskGateDecision.model_validate(row["decision_document"])
            if row is not None
            else None
        )

    async def get_latest_task_gate_for_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        result_snapshot_id: UUID,
    ) -> TaskGateDecision | None:
        """Load the latest immutable Gate decision bound to one exact Snapshot."""

        cursor = await connection.execute(
            """
            select decision_document
            from atlas.task_gate_decision
            where result_snapshot_id = %s
            order by revision desc
            limit 1
            """,
            (result_snapshot_id,),
        )
        row = await cursor.fetchone()
        return (
            TaskGateDecision.model_validate(row["decision_document"])
            if row is not None
            else None
        )

    async def list_failure_clusters_page(
        self,
        connection: AsyncConnection[DictRow],
        *,
        result_snapshot_id: UUID,
        as_of: datetime,
        after_fingerprint: str | None,
        after_failure_cluster_id: UUID | None,
        after_cluster_revision_id: UUID | None,
        limit: int,
    ) -> tuple[
        tuple[FailureClusterRevision, FailureClassificationRevision | None],
        ...,
    ]:
        """List current Cluster revisions and judgments behind one as-of fence."""

        cursor = await connection.execute(
            """
            with latest_clusters as (
              select distinct on (source.failure_cluster_id)
                source.id,
                source.failure_cluster_id,
                source.fingerprint,
                source.cluster
              from atlas.failure_cluster_revision source
              where source.result_snapshot_id = %s
                and source.created_at <= %s
              order by source.failure_cluster_id, source.revision desc, source.id desc
            )
            select latest.cluster, judgment.classification
            from latest_clusters latest
            left join lateral (
              select source.classification
              from atlas.failure_classification_revision source
              where source.failure_cluster_revision_id = latest.id
                and source.created_at <= %s
              order by source.revision desc, source.id desc
              limit 1
            ) judgment on true
            where (
              %s::text is null
              or (
                latest.fingerprint,
                latest.failure_cluster_id,
                latest.id
              ) > (
                %s::text,
                %s::uuid,
                %s::uuid
              )
            )
            order by latest.fingerprint, latest.failure_cluster_id, latest.id
            limit %s
            """,
            (
                result_snapshot_id,
                as_of,
                as_of,
                after_fingerprint,
                after_fingerprint,
                after_failure_cluster_id,
                after_cluster_revision_id,
                limit,
            ),
        )
        rows = await cursor.fetchall()
        return tuple(
            (
                FailureClusterRevision.model_validate(row["cluster"]),
                (
                    FailureClassificationRevision.model_validate(row["classification"])
                    if row["classification"] is not None
                    else None
                ),
            )
            for row in rows
        )

    async def insert_task_gate_decision(
        self,
        connection: AsyncConnection[DictRow],
        decision: TaskGateDecision,
    ) -> None:
        """Insert one database-revalidated immutable Task Gate decision."""

        await connection.execute(
            """
            insert into atlas.task_gate_decision (
              id, task_gate_id, tenant_id, project_id, task_run_id,
              result_snapshot_id, result_snapshot_hash, revision,
              failure_classification_revision_ids, classification_set_hash,
              gate_policy_version, gate_policy_digest, decision, reasons,
              evaluated_by, client_mutation_id, supersedes_gate_decision_id,
              evaluated_at, decision_hash, decision_document
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s
            )
            """,
            (
                decision.id,
                decision.task_gate_id,
                decision.tenant_id,
                decision.project_id,
                decision.task_run_id,
                decision.result_snapshot_id,
                decision.result_snapshot_hash,
                decision.revision,
                list(decision.failure_classification_revision_ids),
                decision.classification_set_hash,
                decision.gate_policy_version,
                decision.gate_policy_digest,
                decision.decision,
                Jsonb(
                    [
                        item.model_dump(mode="json", by_alias=True)
                        for item in decision.reasons
                    ]
                ),
                decision.evaluated_by,
                decision.client_mutation_id,
                decision.supersedes_gate_decision_id,
                decision.evaluated_at,
                decision.decision_hash,
                Jsonb(task_gate_decision_document(decision)),
            ),
        )

    async def get_attempt_fixture_binding(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
    ) -> AttemptFixtureBinding | None:
        """Load the immutable Fixture authority bound to one physical Attempt."""

        cursor = await connection.execute(
            f"""
            select {ATTEMPT_FIXTURE_BINDING_COLUMNS}
            from atlas.attempt_fixture_binding
            where unit_attempt_id = %s
            """,
            (unit_attempt_id,),
        )
        row = await cursor.fetchone()
        return AttemptFixtureBinding.model_validate(row) if row is not None else None

    async def get_fixture_binding_by_run(
        self,
        connection: AsyncConnection[DictRow],
        fixture_run_id: UUID,
    ) -> AttemptFixtureBinding | None:
        """Resolve a Fixture completion back to its exact Task Attempt."""

        cursor = await connection.execute(
            f"""
            select {ATTEMPT_FIXTURE_BINDING_COLUMNS}
            from atlas.attempt_fixture_binding
            where fixture_run_id = %s
            """,
            (fixture_run_id,),
        )
        row = await cursor.fetchone()
        return AttemptFixtureBinding.model_validate(row) if row is not None else None

    async def list_fixture_bindings_for_unit(
        self,
        connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
    ) -> tuple[AttemptFixtureBinding, ...]:
        """Load Fixture bindings in gapless physical Attempt order."""

        cursor = await connection.execute(
            f"""
            select {ATTEMPT_FIXTURE_BINDING_JOIN_COLUMNS}
            from atlas.attempt_fixture_binding binding
            join atlas.unit_attempt attempt
              on attempt.id = binding.unit_attempt_id
             and attempt.execution_unit_id = binding.execution_unit_id
            where binding.execution_unit_id = %s
            order by attempt.attempt_number
            """,
            (execution_unit_id,),
        )
        return tuple(AttemptFixtureBinding.model_validate(row) for row in await cursor.fetchall())

    async def get_latest_hygiene_resolution(
        self,
        connection: AsyncConnection[DictRow],
        execution_unit_id: UUID,
    ) -> UnitHygieneResolutionRevision | None:
        """Load the latest append-only cleanup interpretation for one Unit."""

        cursor = await connection.execute(
            f"""
            select {UNIT_HYGIENE_RESOLUTION_COLUMNS}
            from atlas.unit_hygiene_resolution_revision
            where execution_unit_id = %s
            order by revision desc
            limit 1
            """,
            (execution_unit_id,),
        )
        row = await cursor.fetchone()
        return UnitHygieneResolutionRevision.model_validate(row) if row is not None else None

    async def list_latest_hygiene_resolutions_for_task(
        self,
        connection: AsyncConnection[DictRow],
        task_run_id: UUID,
    ) -> tuple[UnitHygieneResolutionRevision, ...]:
        """Load one latest Hygiene revision per Unit in Manifest ordinal order."""

        cursor = await connection.execute(
            f"""
            select {UNIT_HYGIENE_RESOLUTION_LATEST_COLUMNS}
            from (
              select distinct on (resolution.execution_unit_id)
                resolution.*
              from atlas.unit_hygiene_resolution_revision resolution
              where resolution.task_run_id = %s
              order by resolution.execution_unit_id, resolution.revision desc
            ) latest
            join atlas.execution_unit unit
              on unit.id = latest.execution_unit_id
             and unit.task_run_id = latest.task_run_id
             and unit.tenant_id = latest.tenant_id
             and unit.project_id = latest.project_id
            order by unit.ordinal, unit.id
            """,
            (task_run_id,),
        )
        return tuple(
            UnitHygieneResolutionRevision.model_validate(row) for row in await cursor.fetchall()
        )

    async def insert_attempt_fixture_binding(
        self,
        connection: AsyncConnection[DictRow],
        binding: AttemptFixtureBinding,
    ) -> None:
        """Insert one immutable Attempt-to-Fixture authority bridge."""

        await connection.execute(
            """
            insert into atlas.attempt_fixture_binding (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, fixture_run_id, fixture_blueprint_version_id,
              environment_id, fixture_plan_digest, created_at, binding_hash,
              binding
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            """,
            (
                binding.id,
                binding.tenant_id,
                binding.project_id,
                binding.task_run_id,
                binding.execution_unit_id,
                binding.unit_attempt_id,
                binding.fixture_run_id,
                binding.fixture_blueprint_version_id,
                binding.environment_id,
                binding.fixture_plan_digest,
                binding.created_at,
                binding.binding_hash,
                Jsonb(binding.model_dump(mode="json", by_alias=True)),
            ),
        )

    async def insert_hygiene_resolution(
        self,
        connection: AsyncConnection[DictRow],
        resolution: UnitHygieneResolutionRevision,
    ) -> None:
        """Append one immutable Unit cleanup interpretation."""

        await connection.execute(
            """
            insert into atlas.unit_hygiene_resolution_revision (
              id, unit_hygiene_resolution_id, tenant_id, project_id,
              task_run_id, execution_unit_id, manifest_hash, unit_key,
              revision, inputs, input_set_hash, data_hygiene,
              resolution_policy_version, resolution_policy_digest,
              supersedes_revision_id, projection_watermark, created_at,
              resolution_hash, resolution
            ) values (
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s,
              %s, %s, %s,
              %s, %s
            )
            """,
            (
                resolution.id,
                resolution.unit_hygiene_resolution_id,
                resolution.tenant_id,
                resolution.project_id,
                resolution.task_run_id,
                resolution.execution_unit_id,
                resolution.manifest_hash,
                resolution.unit_key,
                resolution.revision,
                Jsonb([item.model_dump(mode="json", by_alias=True) for item in resolution.inputs]),
                resolution.input_set_hash,
                resolution.data_hygiene,
                resolution.resolution_policy_version,
                resolution.resolution_policy_digest,
                resolution.supersedes_revision_id,
                resolution.projection_watermark,
                resolution.created_at,
                resolution.resolution_hash,
                Jsonb(resolution.model_dump(mode="json", by_alias=True)),
            ),
        )

    async def insert_reevaluation_command(
        self,
        connection: AsyncConnection[DictRow],
        command: TaskResultReevaluationCommand,
    ) -> None:
        """Persist one immutable explicit re-evaluation request."""

        await connection.execute(
            """
            insert into atlas.task_result_reevaluation_command (
              id, tenant_id, project_id, task_run_id, source_snapshot_id,
              target_policy_version, target_policy_digest, client_mutation_id,
              requested_by, requested_at, command_hash, command
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s
            )
            """,
            (
                command.id,
                command.tenant_id,
                command.project_id,
                command.task_run_id,
                command.source_snapshot_id,
                command.target_policy_version,
                command.target_policy_digest,
                command.client_mutation_id,
                command.requested_by,
                command.requested_at,
                command.command_hash,
                Jsonb(command.model_dump(mode="json", by_alias=True)),
            ),
        )

    async def insert_failure_cluster(
        self,
        connection: AsyncConnection[DictRow],
        cluster: FailureClusterRevision,
    ) -> None:
        """Insert one database-revalidated immutable FailureCluster revision."""

        await connection.execute(
            """
            insert into atlas.failure_cluster_revision (
              id, failure_cluster_id, tenant_id, project_id, task_run_id,
              result_snapshot_id, revision, fingerprint_version,
              fingerprint_policy_digest, fingerprint, signal,
              affected_unit_resolution_revision_ids, affected_count,
              representative_unit_resolution_revision_id,
              supersedes_cluster_revision_id, projection_watermark,
              created_at, cluster_hash, cluster
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s,
              %s,
              %s, %s,
              %s, %s, %s
            )
            """,
            (
                cluster.id,
                cluster.failure_cluster_id,
                cluster.tenant_id,
                cluster.project_id,
                cluster.task_run_id,
                cluster.result_snapshot_id,
                cluster.revision,
                cluster.fingerprint_version,
                cluster.fingerprint_policy_digest,
                cluster.fingerprint,
                Jsonb(cluster.signal.model_dump(mode="json", by_alias=True)),
                list(cluster.affected_unit_resolution_revision_ids),
                cluster.affected_count,
                cluster.representative_unit_resolution_revision_id,
                cluster.supersedes_cluster_revision_id,
                cluster.projection_watermark,
                cluster.created_at,
                cluster.cluster_hash,
                Jsonb(cluster.model_dump(mode="json", by_alias=True)),
            ),
        )

    async def insert_failure_classification(
        self,
        connection: AsyncConnection[DictRow],
        classification: FailureClassificationRevision,
    ) -> None:
        """Insert one database-revalidated immutable Classification revision."""

        await connection.execute(
            """
            insert into atlas.failure_classification_revision (
              id, failure_classification_id, tenant_id, project_id, task_run_id,
              result_snapshot_id, failure_cluster_revision_id, revision,
              failure_domain, hypothesis_code, hypothesis,
              confidence_numerator, supporting_evidence_refs,
              contradicting_evidence_refs, evidence_gap_codes, judgment_state,
              author_kind, authored_by, model_version_ref,
              classification_policy_version, classification_policy_digest,
              client_mutation_id, supersedes_revision_id, created_at,
              classification_hash, classification
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s,
              %s, %s, %s,
              %s, %s
            )
            """,
            (
                classification.id,
                classification.failure_classification_id,
                classification.tenant_id,
                classification.project_id,
                classification.task_run_id,
                classification.result_snapshot_id,
                classification.failure_cluster_revision_id,
                classification.revision,
                classification.failure_domain,
                classification.hypothesis_code,
                classification.hypothesis,
                classification.confidence.numerator,
                Jsonb(
                    [
                        item.model_dump(mode="json", by_alias=True)
                        for item in classification.supporting_evidence_refs
                    ]
                ),
                Jsonb(
                    [
                        item.model_dump(mode="json", by_alias=True)
                        for item in classification.contradicting_evidence_refs
                    ]
                ),
                list(classification.evidence_gap_codes),
                classification.judgment_state,
                classification.author_kind,
                classification.authored_by,
                classification.model_version_ref,
                classification.classification_policy_version,
                classification.classification_policy_digest,
                classification.client_mutation_id,
                classification.supersedes_revision_id,
                classification.created_at,
                classification.classification_hash,
                Jsonb(classification.model_dump(mode="json", by_alias=True)),
            ),
        )

    async def insert_fact(
        self,
        connection: AsyncConnection[DictRow],
        *,
        seal: AttemptSeal,
        accepted_at: datetime,
    ) -> None:
        """Insert one database-revalidated immutable AttemptSeal."""

        await connection.execute(
            """
            insert into atlas.unit_attempt_result_fact (
              seal_id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, manifest_id, manifest_hash, unit_key,
              execution_ticket_id, execution_ticket_digest, oracle_verdict,
              outcome_class, closure_reason, lifecycle, data_hygiene,
              evidence_completeness, evidence_integrity, execution_influence,
              stability, oracle_results_hash, artifact_manifest_hash,
              event_chain_head, event_count, evidence_policy_digest,
              runtime_digest, signature_alg, signature_kid, signature_value,
              content_hash, seal, sealed_at, accepted_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s
            )
            """,
            (
                seal.seal_id,
                seal.tenant_id,
                seal.project_id,
                seal.task_run_id,
                seal.execution_unit_id,
                seal.unit_attempt_id,
                seal.manifest_id,
                seal.manifest_hash,
                seal.unit_key,
                seal.execution_ticket_id,
                seal.execution_ticket_digest,
                seal.oracle_verdict,
                seal.outcome_class,
                seal.closure_reason,
                seal.lifecycle,
                seal.data_hygiene,
                seal.evidence_completeness,
                seal.evidence_integrity,
                seal.execution_influence,
                seal.stability,
                seal.oracle_results_hash,
                seal.artifact_manifest_hash,
                seal.event_chain.head,
                seal.event_chain.event_count,
                seal.evidence_policy_digest,
                seal.runtime_digest,
                seal.signature.alg,
                seal.signature.kid,
                seal.signature_value,
                seal.content_hash,
                Jsonb(seal.model_dump(mode="json", by_alias=True)),
                seal.sealed_at,
                accepted_at,
            ),
        )

    async def insert_ref(
        self,
        connection: AsyncConnection[DictRow],
        result_ref: ResultRef,
    ) -> None:
        """Insert the stable idempotent ResultRef beside its accepted fact."""

        await connection.execute(
            """
            insert into atlas.result_ref (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, seal_id, seal_content_hash, created_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                result_ref.id,
                result_ref.tenant_id,
                result_ref.project_id,
                result_ref.task_run_id,
                result_ref.execution_unit_id,
                result_ref.unit_attempt_id,
                result_ref.seal_id,
                result_ref.seal_content_hash,
                result_ref.created_at,
            ),
        )

    async def insert_closure(
        self,
        connection: AsyncConnection[DictRow],
        notice: AttemptClosureNotice,
    ) -> None:
        """Insert one immutable no-Seal terminal fact."""

        await connection.execute(
            """
            insert into atlas.attempt_closure_notice (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, manifest_hash, unit_key, attempt_number,
              source_status, verdict, outcome_class, closure_reason,
              data_hygiene, evidence_completeness, evidence_integrity,
              execution_influence, closed_at, created_at, notice_hash, notice
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            """,
            (
                notice.id,
                notice.tenant_id,
                notice.project_id,
                notice.task_run_id,
                notice.execution_unit_id,
                notice.unit_attempt_id,
                notice.manifest_hash,
                notice.unit_key,
                notice.attempt_number,
                notice.source_status,
                notice.verdict,
                notice.outcome_class,
                notice.closure_reason,
                notice.data_hygiene,
                notice.evidence_completeness,
                notice.evidence_integrity,
                notice.execution_influence,
                notice.closed_at,
                notice.created_at,
                notice.notice_hash,
                Jsonb(notice.model_dump(mode="json", by_alias=True)),
            ),
        )

    async def insert_resolution(
        self,
        connection: AsyncConnection[DictRow],
        resolution: UnitResolutionRevision,
    ) -> None:
        """Append one deterministic UnitResolution revision."""

        await connection.execute(
            """
            insert into atlas.unit_resolution_revision (
              id, unit_resolution_id, tenant_id, project_id, task_run_id,
              execution_unit_id, manifest_hash, unit_key, revision,
              input_seal_ids, input_closure_notice_ids, input_set_hash,
              effective_verdict, outcome_class, closure_reason, data_hygiene,
              evidence_completeness, evidence_integrity, execution_influence,
              stability, decisive_unit_attempt_id, decisive_attempt_number,
              resolution_policy_version, resolution_policy_digest,
              supersedes_revision_id, created_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s,
              %s, %s
            )
            """,
            (
                resolution.id,
                resolution.unit_resolution_id,
                resolution.tenant_id,
                resolution.project_id,
                resolution.task_run_id,
                resolution.execution_unit_id,
                resolution.manifest_hash,
                resolution.unit_key,
                resolution.revision,
                list(resolution.input_seal_ids),
                list(resolution.input_closure_notice_ids),
                resolution.input_set_hash,
                resolution.effective_verdict,
                resolution.outcome_class,
                resolution.closure_reason,
                resolution.data_hygiene,
                resolution.evidence_completeness,
                resolution.evidence_integrity,
                resolution.execution_influence,
                resolution.stability,
                resolution.decisive_unit_attempt_id,
                resolution.decisive_attempt_number,
                resolution.resolution_policy_version,
                resolution.resolution_policy_digest,
                resolution.supersedes_revision_id,
                resolution.created_at,
            ),
        )

    async def insert_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        snapshot: TaskResultSnapshot,
    ) -> None:
        """Append one database-revalidated TaskResultSnapshot."""

        await connection.execute(
            """
            insert into atlas.task_result_snapshot (
              id, tenant_id, project_id, task_run_id, manifest_hash,
              revision, finality, unit_resolution_revision_ids,
              input_resolution_set_hash,
              unit_hygiene_resolution_revision_ids,
              input_hygiene_resolution_set_hash,
              reevaluation_source_snapshot_id, reevaluation_command_id,
              aggregation_policy_version,
              aggregation_policy_digest, projection_watermark, manifest_count,
              verdict_counts, axis_distributions, raw_pass_rate,
              trusted_pass_rate, autonomous_pass_rate, decisive_pass_rate,
              supersedes_snapshot_id, created_at, snapshot_hash, snapshot
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s, %s
            )
            """,
            (
                snapshot.id,
                snapshot.tenant_id,
                snapshot.project_id,
                snapshot.task_run_id,
                snapshot.manifest_hash,
                snapshot.revision,
                snapshot.finality,
                list(snapshot.unit_resolution_revision_ids),
                snapshot.input_resolution_set_hash,
                (
                    list(snapshot.unit_hygiene_resolution_revision_ids)
                    if snapshot.unit_hygiene_resolution_revision_ids is not None
                    else None
                ),
                snapshot.input_hygiene_resolution_set_hash,
                snapshot.reevaluation_source_snapshot_id,
                snapshot.reevaluation_command_id,
                snapshot.aggregation_policy_version,
                snapshot.aggregation_policy_digest,
                snapshot.projection_watermark,
                snapshot.manifest_count,
                Jsonb(snapshot.verdict_counts.model_dump(mode="json", by_alias=True)),
                Jsonb(
                    snapshot.axis_distributions.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                ),
                Jsonb(snapshot.raw_pass_rate.model_dump(mode="json", by_alias=True)),
                Jsonb(snapshot.trusted_pass_rate.model_dump(mode="json", by_alias=True)),
                Jsonb(
                    snapshot.autonomous_pass_rate.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                ),
                Jsonb(
                    snapshot.decisive_pass_rate.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                ),
                snapshot.supersedes_snapshot_id,
                snapshot.created_at,
                snapshot.snapshot_hash,
                Jsonb(task_result_snapshot_document(snapshot)),
            ),
        )

    async def append_integrity_incident(
        self,
        connection: AsyncConnection[DictRow],
        incident: ResultIntegrityIncident,
    ) -> None:
        """Append a conflict once without overwriting the accepted Seal."""

        await connection.execute(
            """
            insert into atlas.result_integrity_incident (
              id, tenant_id, project_id, task_run_id, execution_unit_id,
              unit_attempt_id, accepted_seal_id, accepted_content_hash,
              conflicting_seal_id, conflicting_content_hash, signature_kid,
              observed_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (unit_attempt_id, conflicting_content_hash) do nothing
            """,
            (
                incident.id,
                incident.tenant_id,
                incident.project_id,
                incident.task_run_id,
                incident.execution_unit_id,
                incident.unit_attempt_id,
                incident.accepted_seal_id,
                incident.accepted_content_hash,
                incident.conflicting_seal_id,
                incident.conflicting_content_hash,
                incident.signature_kid,
                incident.observed_at,
            ),
        )


__all__ = ["ResultFactRepository"]
