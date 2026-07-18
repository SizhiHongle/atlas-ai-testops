"""Add snapshot-bound append-only Task Gate decisions.

Revision ID: 20260718_0039
Revises: 20260718_0038
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0039"
down_revision: str | None = "20260718_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GATE_POLICY_DIGEST = (
    "sha256:a430540f5b5cd41f82e1b46751f6f3b91ef44c76468badae98800fe366e6b2df"
)


UPGRADE_STATEMENTS = (
    f"""
    create table atlas.task_gate_decision (
      id uuid primary key,
      task_gate_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      result_snapshot_id uuid not null,
      result_snapshot_hash text not null,
      revision integer not null,
      failure_classification_revision_ids uuid[] not null,
      classification_set_hash text not null,
      gate_policy_version text not null,
      gate_policy_digest text not null,
      decision text not null,
      reasons jsonb not null,
      evaluated_by uuid not null,
      client_mutation_id text not null,
      supersedes_gate_decision_id uuid,
      evaluated_at timestamptz not null,
      decision_hash text not null,
      decision_document jsonb not null,
      constraint task_gate_decision_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id
      ) references atlas.task_run (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_gate_decision_snapshot_fk foreign key (
        result_snapshot_id
      ) references atlas.task_result_snapshot(id) on delete restrict,
      constraint task_gate_decision_predecessor_fk foreign key (
        supersedes_gate_decision_id
      ) references atlas.task_gate_decision(id) on delete restrict,
      constraint task_gate_decision_chain_unique unique (
        task_gate_id, revision
      ),
      constraint task_gate_decision_mutation_unique unique (
        task_run_id, client_mutation_id
      ),
      constraint task_gate_decision_hash_unique unique (
        tenant_id, decision_hash
      ),
      constraint task_gate_decision_id_scope_unique unique (
        id, task_gate_id, task_run_id, tenant_id, project_id
      ),
      constraint task_gate_decision_revision_valid check (
        revision > 0
        and (
          (revision = 1 and supersedes_gate_decision_id is null)
          or
          (revision > 1 and supersedes_gate_decision_id is not null)
        )
      ),
      constraint task_gate_decision_classification_count_valid check (
        cardinality(failure_classification_revision_ids) between 0 and 10000
      ),
      constraint task_gate_decision_policy_valid check (
        gate_policy_version = '0.1.0'
        and gate_policy_digest = '{_GATE_POLICY_DIGEST}'
      ),
      constraint task_gate_decision_verdict_valid check (
        decision in ('ACCEPTED', 'REJECTED', 'INCONCLUSIVE')
      ),
      constraint task_gate_decision_strings_valid check (
        char_length(client_mutation_id) between 8 and 200
        and client_mutation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
      ),
      constraint task_gate_decision_digests_valid check (
        result_snapshot_hash ~ '^sha256:[0-9a-f]{{64}}$'
        and classification_set_hash ~ '^sha256:[0-9a-f]{{64}}$'
        and gate_policy_digest ~ '^sha256:[0-9a-f]{{64}}$'
        and decision_hash ~ '^sha256:[0-9a-f]{{64}}$'
      ),
      constraint task_gate_decision_json_valid check (
        jsonb_typeof(reasons) = 'array'
        and jsonb_array_length(reasons) between 0 and 11
        and jsonb_typeof(decision_document) = 'object'
      )
    )
    """,
    """
    create index task_gate_decision_task_latest_idx
      on atlas.task_gate_decision (
        tenant_id, project_id, task_run_id, revision desc
      )
    """,
    """
    create index task_gate_decision_snapshot_idx
      on atlas.task_gate_decision (
        tenant_id, project_id, result_snapshot_id, revision desc
      )
    """,
    f"""
    create function atlas.guard_task_gate_decision_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_run atlas.task_run%rowtype;
      source_snapshot atlas.task_result_snapshot%rowtype;
      previous atlas.task_gate_decision%rowtype;
      expected_diagnostic_ids uuid[];
      cluster_affected_ids uuid[];
      cluster_affected_count bigint;
      cluster_affected_distinct_count bigint;
      expected_classification_ids uuid[];
      expected_classification_inputs jsonb;
      latest_cluster_count bigint;
      latest_classification_count bigint;
      not_ready_classification_count integer;
      expected_classification_set_hash text;
      expected_reasons jsonb := '[]'::jsonb;
      expected_decision text;
      count_value integer;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
        or atlas.current_actor_id() is null
        or new.evaluated_by <> atlas.current_actor_id()
      then
        raise exception
          'TaskGateDecision insertion requires exact tenant and actor context'
          using errcode = '42501';
      end if;

      select * into stored_run
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
      for share;
      select * into source_snapshot
      from atlas.task_result_snapshot snapshot
      where snapshot.id = new.result_snapshot_id
        and snapshot.task_run_id = new.task_run_id
        and snapshot.tenant_id = new.tenant_id
        and snapshot.project_id = new.project_id
      for share;
      if stored_run.id is null
        or source_snapshot.id is null
        or stored_run.lifecycle <> 'CLOSED'
        or stored_run.materialization_state <> 'SEALED'
        or stored_run.manifest_hash <> source_snapshot.manifest_hash
        or new.result_snapshot_hash <> source_snapshot.snapshot_hash
        or new.evaluated_at <> transaction_timestamp()
        or new.gate_policy_version <> '0.1.0'
        or new.gate_policy_digest <> '{_GATE_POLICY_DIGEST}'
      then
        raise exception
          'TaskGateDecision requires an exact closed Snapshot and frozen Policy';
      end if;

      select coalesce(
        array_agg(resolution.id order by resolution.id),
        array[]::uuid[]
      )
      into expected_diagnostic_ids
      from unnest(source_snapshot.unit_resolution_revision_ids)
        with ordinality requested(id, ordinal)
      join atlas.unit_resolution_revision resolution
        on resolution.id = requested.id
       and resolution.task_run_id = source_snapshot.task_run_id
       and resolution.tenant_id = source_snapshot.tenant_id
       and resolution.project_id = source_snapshot.project_id
      left join atlas.unit_hygiene_resolution_revision hygiene
        on source_snapshot.unit_hygiene_resolution_revision_ids is not null
       and hygiene.id =
         source_snapshot.unit_hygiene_resolution_revision_ids[requested.ordinal]
       and hygiene.execution_unit_id = resolution.execution_unit_id
      where resolution.effective_verdict <> 'PASSED'
        or resolution.stability <> 'STABLE'
        or coalesce(hygiene.data_hygiene, resolution.data_hygiene)
          in ('CLEANUP_FAILED', 'LEAKED')
        or resolution.evidence_integrity <> 'VERIFIED'
        or resolution.evidence_completeness in ('MISSING', 'PARTIAL');

      with latest_clusters as (
        select distinct on (cluster.failure_cluster_id)
          cluster.id,
          cluster.affected_unit_resolution_revision_ids
        from atlas.failure_cluster_revision cluster
        where cluster.result_snapshot_id = source_snapshot.id
        order by cluster.failure_cluster_id, cluster.revision desc
      ),
      affected as (
        select item.id
        from latest_clusters cluster
        cross join lateral unnest(
          cluster.affected_unit_resolution_revision_ids
        ) item(id)
      )
      select
        coalesce(
          array_agg(distinct affected.id order by affected.id),
          array[]::uuid[]
        ),
        count(affected.id),
        count(distinct affected.id)
      into
        cluster_affected_ids,
        cluster_affected_count,
        cluster_affected_distinct_count
      from affected;
      if cluster_affected_ids is distinct from expected_diagnostic_ids
        or cluster_affected_count <> cluster_affected_distinct_count
      then
        raise exception
          'TaskGateDecision requires complete non-overlapping Cluster coverage';
      end if;

      with latest_clusters as (
        select distinct on (cluster.failure_cluster_id)
          cluster.id,
          cluster.failure_cluster_id,
          cluster.fingerprint,
          cluster.cluster_hash
        from atlas.failure_cluster_revision cluster
        where cluster.result_snapshot_id = source_snapshot.id
        order by cluster.failure_cluster_id, cluster.revision desc
      ),
      current_pairs as (
        select
          cluster.id as cluster_revision_id,
          cluster.failure_cluster_id,
          cluster.fingerprint,
          cluster.cluster_hash,
          classification.id as classification_revision_id,
          classification.revision as classification_revision,
          classification.classification_hash,
          classification.failure_domain,
          classification.confidence_numerator,
          classification.evidence_gap_codes,
          classification.judgment_state,
          classification.author_kind
        from latest_clusters cluster
        left join lateral (
          select source.*
          from atlas.failure_classification_revision source
          where source.failure_cluster_revision_id = cluster.id
          order by source.revision desc
          limit 1
        ) classification on true
      ),
      ranked_pairs as (
        select
          pair.*,
          row_number() over (
            order by pair.fingerprint, pair.failure_cluster_id,
              pair.cluster_revision_id
          )::integer as ordinal
        from current_pairs pair
      )
      select
        coalesce(
          array_agg(
            pair.classification_revision_id
            order by pair.fingerprint, pair.failure_cluster_id,
              pair.cluster_revision_id
          ) filter (where pair.classification_revision_id is not null),
          array[]::uuid[]
        ),
        coalesce(
          jsonb_agg(
            jsonb_build_object(
              'ordinal', pair.ordinal,
              'failureClusterRevisionId', pair.cluster_revision_id,
              'clusterHash', pair.cluster_hash,
              'failureClassificationRevisionId',
                pair.classification_revision_id,
              'classificationRevision', pair.classification_revision,
              'classificationHash', pair.classification_hash
            )
            order by pair.fingerprint, pair.failure_cluster_id,
              pair.cluster_revision_id
          ) filter (where pair.classification_revision_id is not null),
          '[]'::jsonb
        ),
        count(*),
        count(pair.classification_revision_id),
        count(*) filter (
          where pair.classification_revision_id is not null
            and not (
              (
                pair.author_kind = 'SYSTEM_RULE'
                and pair.judgment_state = 'RULE_PROPOSED'
                and pair.failure_domain in (
                  'CLEANUP', 'EVIDENCE', 'POLICY_SECURITY'
                )
                and pair.confidence_numerator >= 9500
                and cardinality(pair.evidence_gap_codes) = 0
              )
              or
              (
                pair.author_kind = 'HUMAN'
                and pair.judgment_state in (
                  'HUMAN_CONFIRMED', 'HUMAN_REVISED'
                )
                and pair.failure_domain <> 'UNKNOWN'
                and pair.confidence_numerator >= 7000
                and cardinality(pair.evidence_gap_codes) = 0
              )
            )
        )::integer
      into
        expected_classification_ids,
        expected_classification_inputs,
        latest_cluster_count,
        latest_classification_count,
        not_ready_classification_count
      from ranked_pairs pair;

      if latest_cluster_count <> latest_classification_count
        or new.failure_classification_revision_ids
          is distinct from expected_classification_ids
      then
        raise exception
          'TaskGateDecision requires the complete latest Classification set';
      end if;

      expected_classification_set_hash := atlas.task_sha256_json(
        jsonb_build_object(
          'schemaVersion', 'atlas.task-gate-classification-set/0.1',
          'resultSnapshotId', source_snapshot.id,
          'inputs', expected_classification_inputs
        )
      );
      if new.classification_set_hash <> expected_classification_set_hash then
        raise exception
          'TaskGateDecision Classification set hash does not match exact inputs';
      end if;

      if source_snapshot.finality = 'QUALITY_FINAL' then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object(
            'code', 'SNAPSHOT_NOT_FULLY_RESOLVED',
            'count', 1
          )
        );
      end if;
      count_value := (source_snapshot.verdict_counts ->> 'inconclusive')::integer;
      if count_value > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object('code', 'INCONCLUSIVE_UNITS', 'count', count_value)
        );
      end if;
      count_value := (source_snapshot.verdict_counts ->> 'notEvaluated')::integer;
      if count_value > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object('code', 'NOT_EVALUATED_UNITS', 'count', count_value)
        );
      end if;
      count_value :=
        (source_snapshot.axis_distributions
          -> 'dataHygiene' ->> 'pending')::integer
        +
        (source_snapshot.axis_distributions
          -> 'dataHygiene' ->> 'cleanupFailed')::integer;
      if count_value > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object(
            'code', 'DATA_HYGIENE_UNRESOLVED',
            'count', count_value
          )
        );
      end if;
      count_value :=
        (source_snapshot.axis_distributions
          -> 'evidenceCompleteness' ->> 'pending')::integer
        +
        (source_snapshot.axis_distributions
          -> 'evidenceCompleteness' ->> 'partial')::integer
        +
        (source_snapshot.axis_distributions
          -> 'evidenceCompleteness' ->> 'missing')::integer;
      if count_value > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object('code', 'EVIDENCE_INCOMPLETE', 'count', count_value)
        );
      end if;
      count_value :=
        (source_snapshot.axis_distributions
          -> 'evidenceIntegrity' ->> 'unverified')::integer
        +
        (source_snapshot.axis_distributions
          -> 'evidenceIntegrity' ->> 'invalid')::integer;
      if count_value > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object(
            'code', 'EVIDENCE_INVALID_OR_UNVERIFIED',
            'count', count_value
          )
        );
      end if;
      count_value :=
        (source_snapshot.axis_distributions
          -> 'executionInfluence' ->> 'manualAssisted')::integer
        +
        (source_snapshot.axis_distributions
          -> 'executionInfluence' ->> 'manualOnly')::integer;
      if count_value > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object('code', 'MANUAL_INFLUENCE', 'count', count_value)
        );
      end if;
      count_value :=
        (source_snapshot.axis_distributions
          -> 'stability' ->> 'unknown')::integer
        +
        (source_snapshot.axis_distributions
          -> 'stability' ->> 'infraRecovered')::integer
        +
        (source_snapshot.axis_distributions
          -> 'stability' ->> 'flakySuspect')::integer
        +
        (source_snapshot.axis_distributions
          -> 'stability' ->> 'flakyConfirmed')::integer;
      if count_value > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object('code', 'UNSTABLE_EXECUTION', 'count', count_value)
        );
      end if;
      if not_ready_classification_count > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object(
            'code', 'CLASSIFICATION_NOT_GATE_READY',
            'count', not_ready_classification_count
          )
        );
      end if;
      count_value := (source_snapshot.verdict_counts ->> 'failed')::integer;
      if count_value > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object('code', 'FAILED_UNITS', 'count', count_value)
        );
      end if;
      count_value := (source_snapshot.axis_distributions
        -> 'dataHygiene' ->> 'leaked')::integer;
      if count_value > 0 then
        expected_reasons := expected_reasons || jsonb_build_array(
          jsonb_build_object('code', 'DATA_LEAK', 'count', count_value)
        );
      end if;

      select coalesce(
        jsonb_agg(reason.value order by reason.value ->> 'code'),
        '[]'::jsonb
      )
      into expected_reasons
      from jsonb_array_elements(expected_reasons) reason(value);

      if exists (
        select 1
        from jsonb_array_elements(expected_reasons) reason(value)
        where reason.value ->> 'code' in (
          'CLASSIFICATION_NOT_GATE_READY',
          'DATA_HYGIENE_UNRESOLVED',
          'EVIDENCE_INCOMPLETE',
          'EVIDENCE_INVALID_OR_UNVERIFIED',
          'INCONCLUSIVE_UNITS',
          'MANUAL_INFLUENCE',
          'NOT_EVALUATED_UNITS',
          'SNAPSHOT_NOT_FULLY_RESOLVED',
          'UNSTABLE_EXECUTION'
        )
      ) then
        expected_decision := 'INCONCLUSIVE';
      elsif exists (
        select 1
        from jsonb_array_elements(expected_reasons) reason(value)
        where reason.value ->> 'code' in ('FAILED_UNITS', 'DATA_LEAK')
      ) then
        expected_decision := 'REJECTED';
      else
        expected_decision := 'ACCEPTED';
      end if;
      if new.decision <> expected_decision
        or new.reasons is distinct from expected_reasons
      then
        raise exception
          'TaskGateDecision verdict and reasons do not match frozen Policy';
      end if;

      if new.revision = 1 then
        if new.supersedes_gate_decision_id is not null then
          raise exception 'first TaskGateDecision cannot supersede another';
        end if;
      else
        select * into previous
        from atlas.task_gate_decision gate
        where gate.task_run_id = new.task_run_id
        order by gate.revision desc
        limit 1
        for share;
        if not found
          or new.revision <> previous.revision + 1
          or new.task_gate_id <> previous.task_gate_id
          or new.supersedes_gate_decision_id <> previous.id
          or new.tenant_id <> previous.tenant_id
          or new.project_id <> previous.project_id
          or new.task_run_id <> previous.task_run_id
        then
          raise exception 'TaskGateDecision revision chain is invalid';
        end if;
      end if;

      if atlas.task_json_object_size(new.decision_document) <> 20
        or new.decision_document ->> 'schemaVersion'
          <> 'atlas.task-gate-decision/0.1'
        or (new.decision_document ->> 'id')::uuid is distinct from new.id
        or (new.decision_document ->> 'taskGateId')::uuid
          is distinct from new.task_gate_id
        or (new.decision_document ->> 'tenantId')::uuid
          is distinct from new.tenant_id
        or (new.decision_document ->> 'projectId')::uuid
          is distinct from new.project_id
        or (new.decision_document ->> 'taskRunId')::uuid
          is distinct from new.task_run_id
        or (new.decision_document ->> 'resultSnapshotId')::uuid
          is distinct from new.result_snapshot_id
        or new.decision_document ->> 'resultSnapshotHash'
          is distinct from new.result_snapshot_hash
        or (new.decision_document ->> 'revision')::integer
          is distinct from new.revision
        or array(
          select value::uuid
          from jsonb_array_elements_text(
            new.decision_document -> 'failureClassificationRevisionIds'
          )
        ) is distinct from new.failure_classification_revision_ids
        or new.decision_document ->> 'classificationSetHash'
          is distinct from new.classification_set_hash
        or new.decision_document ->> 'gatePolicyVersion'
          is distinct from new.gate_policy_version
        or new.decision_document ->> 'gatePolicyDigest'
          is distinct from new.gate_policy_digest
        or new.decision_document ->> 'decision' is distinct from new.decision
        or new.decision_document -> 'reasons' is distinct from new.reasons
        or (new.decision_document ->> 'evaluatedBy')::uuid
          is distinct from new.evaluated_by
        or new.decision_document ->> 'clientMutationId'
          is distinct from new.client_mutation_id
        or (new.decision_document ->> 'supersedesGateDecisionId')::uuid
          is distinct from new.supersedes_gate_decision_id
        or (new.decision_document ->> 'evaluatedAt')::timestamptz
          is distinct from new.evaluated_at
        or new.decision_document ->> 'decisionHash'
          is distinct from new.decision_hash
        or atlas.task_sha256_json(
          new.decision_document - array[
            'id', 'taskGateId', 'revision', 'supersedesGateDecisionId',
            'evaluatedAt', 'decisionHash'
          ]
        ) is distinct from new.decision_hash
      then
        raise exception 'TaskGateDecision persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_gate_decision_guard_insert
      before insert on atlas.task_gate_decision
      for each row execute function atlas.guard_task_gate_decision_insert()
    """,
    """
    create trigger task_gate_decision_prevent_mutation
      before update or delete on atlas.task_gate_decision
      for each row execute function atlas.prevent_fact_mutation()
    """,
    "alter table atlas.task_gate_decision enable row level security",
    "alter table atlas.task_gate_decision force row level security",
    """
    create policy task_gate_decision_tenant_isolation
      on atlas.task_gate_decision for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.task_gate_decision from atlas_app",
    "grant select, insert on atlas.task_gate_decision to atlas_app",
    """
    revoke all on function atlas.guard_task_gate_decision_insert()
      from public, atlas_app, atlas_dispatcher
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.task_gate_decision) then
        raise exception
          'cannot downgrade while TaskGateDecision facts exist';
      end if;
    end;
    $$
    """,
    "drop table atlas.task_gate_decision",
    "drop function atlas.guard_task_gate_decision_insert()",
)


def upgrade() -> None:
    """Apply the exact Snapshot and Classification-bound Gate truth layer."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Refuse lossy downgrade after any Gate fact exists."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
