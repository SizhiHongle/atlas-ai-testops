"""Add append-only TaskResultSnapshot truth over exact Unit resolutions.

Revision ID: 20260718_0034
Revises: 20260718_0033
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0034"
down_revision: str | None = "20260718_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.task_result_snapshot (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      manifest_hash text not null,
      revision integer not null,
      finality text not null,
      unit_resolution_revision_ids uuid[] not null,
      input_resolution_set_hash text not null,
      aggregation_policy_version text not null,
      aggregation_policy_digest text not null,
      projection_watermark timestamptz not null,
      manifest_count integer not null,
      verdict_counts jsonb not null,
      axis_distributions jsonb not null,
      raw_pass_rate jsonb not null,
      trusted_pass_rate jsonb not null,
      autonomous_pass_rate jsonb not null,
      decisive_pass_rate jsonb not null,
      supersedes_snapshot_id uuid,
      created_at timestamptz not null,
      snapshot_hash text not null,
      snapshot jsonb not null,
      constraint task_result_snapshot_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id
      ) references atlas.task_run (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_result_snapshot_supersedes_fk foreign key (
        supersedes_snapshot_id
      ) references atlas.task_result_snapshot(id) on delete restrict,
      constraint task_result_snapshot_revision_unique unique (
        task_run_id, revision
      ),
      constraint task_result_snapshot_input_unique unique (
        task_run_id, input_resolution_set_hash,
        aggregation_policy_digest, finality
      ),
      constraint task_result_snapshot_hash_unique unique (
        tenant_id, snapshot_hash
      ),
      constraint task_result_snapshot_full_scope_unique unique (
        id, task_run_id, tenant_id, project_id, revision
      ),
      constraint task_result_snapshot_revision_valid check (
        revision >= 1
        and (
          (revision = 1 and supersedes_snapshot_id is null)
          or
          (revision > 1 and supersedes_snapshot_id is not null)
        )
      ),
      constraint task_result_snapshot_finality_valid check (
        finality = 'QUALITY_FINAL'
      ),
      constraint task_result_snapshot_coverage_valid check (
        manifest_count between 1 and 100000
        and cardinality(unit_resolution_revision_ids) = manifest_count
      ),
      constraint task_result_snapshot_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and input_resolution_set_hash ~ '^sha256:[0-9a-f]{64}$'
        and aggregation_policy_digest ~ '^sha256:[0-9a-f]{64}$'
        and snapshot_hash ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint task_result_snapshot_policy_valid check (
        aggregation_policy_version = '0.1.0'
        and aggregation_policy_digest =
          'sha256:f047f7c9925cce522ccf743a0dcaf69d89f9a5d60a6856ab7654de971be8951e'
      ),
      constraint task_result_snapshot_time_valid check (
        created_at >= projection_watermark
      ),
      constraint task_result_snapshot_json_valid check (
        jsonb_typeof(verdict_counts) = 'object'
        and jsonb_typeof(axis_distributions) = 'object'
        and jsonb_typeof(raw_pass_rate) = 'object'
        and jsonb_typeof(trusted_pass_rate) = 'object'
        and jsonb_typeof(autonomous_pass_rate) = 'object'
        and jsonb_typeof(decisive_pass_rate) = 'object'
        and jsonb_typeof(snapshot) = 'object'
      )
    )
    """,
    """
    create function atlas.guard_task_result_snapshot_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_run atlas.task_run%rowtype;
      previous atlas.task_result_snapshot%rowtype;
      expected_resolution_ids uuid[];
      expected_inputs jsonb;
      expected_input_hash text;
      expected_watermark timestamptz;
      expected_manifest_count integer;
      closed_unit_count integer;
      passed_count integer;
      failed_count integer;
      inconclusive_count integer;
      not_evaluated_count integer;
      trusted_passed_count integer;
      autonomous_passed_count integer;
      expected_verdict_counts jsonb;
      expected_axis_distributions jsonb;
      expected_raw_pass_rate jsonb;
      expected_trusted_pass_rate jsonb;
      expected_autonomous_pass_rate jsonb;
      expected_decisive_pass_rate jsonb;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'TaskResultSnapshot insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into stored_run
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
      for update;
      if not found
        or stored_run.lifecycle <> 'CLOSED'
        or stored_run.closed_at is null
        or stored_run.manifest_hash <> new.manifest_hash
        or stored_run.materialization_state <> 'SEALED'
        or stored_run.materialized_unit_count <> new.manifest_count
        or new.created_at <> transaction_timestamp()
      then
        raise exception 'TaskResultSnapshot requires the exact closed TaskRun';
      end if;

      select * into previous
      from atlas.task_result_snapshot candidate
      where candidate.task_run_id = new.task_run_id
      order by candidate.revision desc
      limit 1;
      if found then
        if new.revision <> previous.revision + 1
          or new.supersedes_snapshot_id <> previous.id
        then
          raise exception 'TaskResultSnapshot revision chain is invalid';
        end if;
      elsif new.revision <> 1 or new.supersedes_snapshot_id is not null then
        raise exception 'TaskResultSnapshot first revision is invalid';
      end if;

      with latest_resolution as (
        select distinct on (resolution.execution_unit_id)
          resolution.*,
          unit.ordinal as unit_ordinal,
          unit.lifecycle as unit_lifecycle
        from atlas.execution_unit unit
        join atlas.unit_resolution_revision resolution
          on resolution.execution_unit_id = unit.id
         and resolution.task_run_id = unit.task_run_id
         and resolution.tenant_id = unit.tenant_id
         and resolution.project_id = unit.project_id
        where unit.task_run_id = new.task_run_id
          and unit.tenant_id = new.tenant_id
          and unit.project_id = new.project_id
        order by resolution.execution_unit_id, resolution.revision desc
      ),
      latest as (
        select latest_resolution.*
        from latest_resolution
        join lateral (
          select
            coalesce(
              array_agg(fact.seal_id order by attempt.attempt_number)
                filter (where fact.seal_id is not null),
              '{}'::uuid[]
            ) as seal_ids,
            coalesce(
              array_agg(notice.id order by attempt.attempt_number)
                filter (where notice.id is not null),
              '{}'::uuid[]
            ) as closure_notice_ids,
            count(*) as closed_attempt_count,
            count(coalesce(fact.seal_id, notice.id)) as terminal_fact_count
          from atlas.unit_attempt attempt
          left join atlas.unit_attempt_result_fact fact
            on fact.unit_attempt_id = attempt.id
          left join atlas.attempt_closure_notice notice
            on notice.unit_attempt_id = attempt.id
          where attempt.execution_unit_id =
              latest_resolution.execution_unit_id
            and attempt.lifecycle = 'CLOSED'
        ) terminal on true
        where terminal.closed_attempt_count = terminal.terminal_fact_count
          and terminal.seal_ids = latest_resolution.input_seal_ids
          and terminal.closure_notice_ids =
            latest_resolution.input_closure_notice_ids
      )
      select
        array_agg(id order by unit_ordinal),
        jsonb_agg(
          jsonb_build_object(
            'ordinal', unit_ordinal,
            'executionUnitId', execution_unit_id::text,
            'unitResolutionRevisionId', id::text,
            'revision', revision,
            'inputSetHash', input_set_hash
          )
          order by unit_ordinal
        ),
        max(created_at),
        count(*),
        count(*) filter (where unit_lifecycle = 'CLOSED'),
        count(*) filter (where effective_verdict = 'PASSED'),
        count(*) filter (where effective_verdict = 'FAILED'),
        count(*) filter (where effective_verdict = 'INCONCLUSIVE'),
        count(*) filter (where effective_verdict = 'NOT_EVALUATED'),
        count(*) filter (
          where effective_verdict = 'PASSED'
            and evidence_completeness = 'COMPLETE'
            and evidence_integrity = 'VERIFIED'
        ),
        count(*) filter (
          where effective_verdict = 'PASSED'
            and execution_influence = 'AUTONOMOUS'
        ),
        jsonb_build_object(
          'dataHygiene', jsonb_build_object(
            'pending', count(*) filter (where data_hygiene = 'PENDING'),
            'cleaned', count(*) filter (where data_hygiene = 'CLEANED'),
            'cleanupFailed',
              count(*) filter (where data_hygiene = 'CLEANUP_FAILED'),
            'leaked', count(*) filter (where data_hygiene = 'LEAKED'),
            'notApplicable',
              count(*) filter (where data_hygiene = 'NOT_APPLICABLE')
          ),
          'evidenceCompleteness', jsonb_build_object(
            'pending',
              count(*) filter (where evidence_completeness = 'PENDING'),
            'complete',
              count(*) filter (where evidence_completeness = 'COMPLETE'),
            'partial',
              count(*) filter (where evidence_completeness = 'PARTIAL'),
            'missing',
              count(*) filter (where evidence_completeness = 'MISSING'),
            'notApplicable',
              count(*) filter (where evidence_completeness = 'NOT_APPLICABLE')
          ),
          'evidenceIntegrity', jsonb_build_object(
            'unverified',
              count(*) filter (where evidence_integrity = 'UNVERIFIED'),
            'verified',
              count(*) filter (where evidence_integrity = 'VERIFIED'),
            'invalid',
              count(*) filter (where evidence_integrity = 'INVALID')
          ),
          'executionInfluence', jsonb_build_object(
            'autonomous',
              count(*) filter (where execution_influence = 'AUTONOMOUS'),
            'manualAssisted',
              count(*) filter (where execution_influence = 'MANUAL_ASSISTED'),
            'manualOnly',
              count(*) filter (where execution_influence = 'MANUAL_ONLY')
          ),
          'stability', jsonb_build_object(
            'unknown', count(*) filter (where stability = 'UNKNOWN'),
            'stable', count(*) filter (where stability = 'STABLE'),
            'infraRecovered',
              count(*) filter (where stability = 'INFRA_RECOVERED'),
            'flakySuspect',
              count(*) filter (where stability = 'FLAKY_SUSPECT'),
            'flakyConfirmed',
              count(*) filter (where stability = 'FLAKY_CONFIRMED')
          ),
          'outcomeClass', jsonb_build_object(
            'business', count(*) filter (where outcome_class = 'BUSINESS'),
            'dependency', count(*) filter (where outcome_class = 'DEPENDENCY'),
            'platform', count(*) filter (where outcome_class = 'PLATFORM'),
            'user', count(*) filter (where outcome_class = 'USER'),
            'automation', count(*) filter (where outcome_class = 'AUTOMATION'),
            'policy', count(*) filter (where outcome_class = 'POLICY'),
            'unknown', count(*) filter (where outcome_class = 'UNKNOWN')
          )
        )
      into
        expected_resolution_ids,
        expected_inputs,
        expected_watermark,
        expected_manifest_count,
        closed_unit_count,
        passed_count,
        failed_count,
        inconclusive_count,
        not_evaluated_count,
        trusted_passed_count,
        autonomous_passed_count,
        expected_axis_distributions
      from latest;

      if expected_manifest_count <> new.manifest_count
        or closed_unit_count <> new.manifest_count
        or expected_resolution_ids <> new.unit_resolution_revision_ids
        or expected_watermark <> new.projection_watermark
        or passed_count + failed_count + inconclusive_count
          + not_evaluated_count <> new.manifest_count
      then
        raise exception 'TaskResultSnapshot Resolution coverage is invalid';
      end if;

      expected_input_hash := atlas.task_sha256_json(
        jsonb_build_object(
          'schemaVersion', 'atlas.task-result-resolution-set/0.1',
          'taskRunId', new.task_run_id::text,
          'manifestHash', new.manifest_hash,
          'inputs', expected_inputs
        )
      );
      if new.input_resolution_set_hash <> expected_input_hash then
        raise exception 'TaskResultSnapshot input Resolution set hash is invalid';
      end if;

      expected_verdict_counts := jsonb_build_object(
        'passed', passed_count,
        'failed', failed_count,
        'inconclusive', inconclusive_count,
        'notEvaluated', not_evaluated_count
      );
      expected_raw_pass_rate := jsonb_build_object(
        'numerator', passed_count,
        'denominator', new.manifest_count
      );
      expected_trusted_pass_rate := jsonb_build_object(
        'numerator', trusted_passed_count,
        'denominator', new.manifest_count
      );
      expected_autonomous_pass_rate := jsonb_build_object(
        'numerator', autonomous_passed_count,
        'denominator', new.manifest_count
      );
      expected_decisive_pass_rate := jsonb_build_object(
        'numerator', passed_count,
        'denominator', passed_count + failed_count
      );
      if new.verdict_counts <> expected_verdict_counts
        or new.axis_distributions <> expected_axis_distributions
        or new.raw_pass_rate <> expected_raw_pass_rate
        or new.trusted_pass_rate <> expected_trusted_pass_rate
        or new.autonomous_pass_rate <> expected_autonomous_pass_rate
        or new.decisive_pass_rate <> expected_decisive_pass_rate
      then
        raise exception 'TaskResultSnapshot aggregation is invalid';
      end if;

      if atlas.task_json_has_sensitive_keys(new.snapshot)
        or atlas.task_json_object_size(new.snapshot) <> 23
        or not (
          new.snapshot ?& array[
            'schemaVersion', 'id', 'tenantId', 'projectId', 'taskRunId',
            'manifestHash', 'revision', 'finality',
            'unitResolutionRevisionIds', 'inputResolutionSetHash',
            'aggregationPolicyVersion', 'aggregationPolicyDigest',
            'projectionWatermark', 'manifestCount', 'verdictCounts',
            'axisDistributions', 'rawPassRate', 'trustedPassRate',
            'autonomousPassRate', 'decisivePassRate',
            'supersedesSnapshotId', 'createdAt', 'snapshotHash'
          ]
        )
        or new.snapshot ->> 'schemaVersion'
          is distinct from 'atlas.task-result-snapshot/0.1'
        or new.snapshot ->> 'id' is distinct from new.id::text
        or new.snapshot ->> 'tenantId' is distinct from new.tenant_id::text
        or new.snapshot ->> 'projectId' is distinct from new.project_id::text
        or new.snapshot ->> 'taskRunId' is distinct from new.task_run_id::text
        or new.snapshot ->> 'manifestHash' is distinct from new.manifest_hash
        or (new.snapshot ->> 'revision')::integer is distinct from new.revision
        or new.snapshot ->> 'finality' is distinct from new.finality
        or new.snapshot -> 'unitResolutionRevisionIds'
          is distinct from to_jsonb(new.unit_resolution_revision_ids)
        or new.snapshot ->> 'inputResolutionSetHash'
          is distinct from new.input_resolution_set_hash
        or new.snapshot ->> 'aggregationPolicyVersion'
          is distinct from new.aggregation_policy_version
        or new.snapshot ->> 'aggregationPolicyDigest'
          is distinct from new.aggregation_policy_digest
        or (new.snapshot ->> 'projectionWatermark')::timestamptz
          is distinct from new.projection_watermark
        or (new.snapshot ->> 'manifestCount')::integer
          is distinct from new.manifest_count
        or new.snapshot -> 'verdictCounts'
          is distinct from new.verdict_counts
        or new.snapshot -> 'axisDistributions'
          is distinct from new.axis_distributions
        or new.snapshot -> 'rawPassRate'
          is distinct from new.raw_pass_rate
        or new.snapshot -> 'trustedPassRate'
          is distinct from new.trusted_pass_rate
        or new.snapshot -> 'autonomousPassRate'
          is distinct from new.autonomous_pass_rate
        or new.snapshot -> 'decisivePassRate'
          is distinct from new.decisive_pass_rate
        or (new.snapshot ->> 'supersedesSnapshotId')::uuid
          is distinct from new.supersedes_snapshot_id
        or (new.snapshot ->> 'createdAt')::timestamptz
          is distinct from new.created_at
        or new.snapshot ->> 'snapshotHash'
          is distinct from new.snapshot_hash
        or atlas.task_sha256_json(
          new.snapshot - array[
            'id', 'revision', 'supersedesSnapshotId',
            'createdAt', 'snapshotHash'
          ]::text[]
        ) is distinct from new.snapshot_hash
      then
        raise exception 'TaskResultSnapshot persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_result_snapshot_guard_insert
      before insert on atlas.task_result_snapshot
      for each row execute function atlas.guard_task_result_snapshot_insert()
    """,
    """
    create trigger task_result_snapshot_prevent_mutation
      before update or delete on atlas.task_result_snapshot
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create index task_result_snapshot_latest_idx
      on atlas.task_result_snapshot (
        tenant_id, project_id, task_run_id, revision desc
      )
    """,
    """
    create index task_result_snapshot_watermark_idx
      on atlas.task_result_snapshot (
        tenant_id, projection_watermark desc, id desc
      )
    """,
    "alter table atlas.task_result_snapshot enable row level security",
    "alter table atlas.task_result_snapshot force row level security",
    """
    create policy task_result_snapshot_tenant_isolation
      on atlas.task_result_snapshot for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.task_result_snapshot from atlas_app",
    "grant select, insert on atlas.task_result_snapshot to atlas_app",
    """
    revoke all on function atlas.guard_task_result_snapshot_insert()
      from public, atlas_app, atlas_dispatcher
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.task_result_snapshot limit 1) then
        raise exception 'cannot downgrade while TaskResultSnapshot facts exist';
      end if;
    end;
    $$
    """,
    "drop table if exists atlas.task_result_snapshot",
    "drop function if exists atlas.guard_task_result_snapshot_insert()",
)


def upgrade() -> None:
    """Apply the immutable Task result truth table and database guards."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove only an empty TaskResultSnapshot table."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
