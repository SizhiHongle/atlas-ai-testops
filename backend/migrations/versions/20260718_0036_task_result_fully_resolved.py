"""Add Hygiene-bound FULLY_RESOLVED Task result revisions.

Revision ID: 20260718_0036
Revises: 20260718_0035
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0036"
down_revision: str | None = "20260718_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    "drop trigger task_result_snapshot_guard_insert on atlas.task_result_snapshot",
    """
    alter table atlas.task_result_snapshot
      drop constraint task_result_snapshot_input_unique,
      drop constraint task_result_snapshot_finality_valid,
      drop constraint task_result_snapshot_coverage_valid,
      drop constraint task_result_snapshot_digest_valid,
      drop constraint task_result_snapshot_policy_valid,
      add column unit_hygiene_resolution_revision_ids uuid[],
      add column input_hygiene_resolution_set_hash text
    """,
    """
    alter table atlas.task_result_snapshot
      add constraint task_result_snapshot_finality_valid check (
        finality in ('QUALITY_FINAL', 'FULLY_RESOLVED')
      ),
      add constraint task_result_snapshot_coverage_valid check (
        manifest_count between 1 and 100000
        and cardinality(unit_resolution_revision_ids) = manifest_count
        and (
          (
            finality = 'QUALITY_FINAL'
            and unit_hygiene_resolution_revision_ids is null
            and input_hygiene_resolution_set_hash is null
          )
          or
          (
            finality = 'FULLY_RESOLVED'
            and cardinality(unit_hygiene_resolution_revision_ids) = manifest_count
            and input_hygiene_resolution_set_hash is not null
          )
        )
      ),
      add constraint task_result_snapshot_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and input_resolution_set_hash ~ '^sha256:[0-9a-f]{64}$'
        and aggregation_policy_digest ~ '^sha256:[0-9a-f]{64}$'
        and snapshot_hash ~ '^sha256:[0-9a-f]{64}$'
        and (
          input_hygiene_resolution_set_hash is null
          or input_hygiene_resolution_set_hash ~ '^sha256:[0-9a-f]{64}$'
        )
      ),
      add constraint task_result_snapshot_policy_valid check (
        (
          finality = 'QUALITY_FINAL'
          and aggregation_policy_version = '0.1.0'
          and aggregation_policy_digest =
            'sha256:f047f7c9925cce522ccf743a0dcaf69d89f9a5d60a6856ab7654de971be8951e'
        )
        or
        (
          finality = 'FULLY_RESOLVED'
          and aggregation_policy_version = '0.2.0'
          and aggregation_policy_digest =
            'sha256:e4a7985c6a76073cc78179e57330f373d7baa6eb25246c8932ee3fac71dcf759'
        )
      )
    """,
    """
    create unique index task_result_snapshot_quality_input_unique
      on atlas.task_result_snapshot (
        task_run_id, input_resolution_set_hash,
        aggregation_policy_digest, finality
      )
      where finality = 'QUALITY_FINAL'
    """,
    """
    create unique index task_result_snapshot_fully_resolved_input_unique
      on atlas.task_result_snapshot (
        task_run_id, input_resolution_set_hash,
        input_hygiene_resolution_set_hash,
        aggregation_policy_digest, finality
      )
      where finality = 'FULLY_RESOLVED'
    """,
    """
    create function atlas.guard_task_result_snapshot_v2_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_run atlas.task_run%rowtype;
      previous atlas.task_result_snapshot%rowtype;
      expected_resolution_ids uuid[];
      expected_resolution_inputs jsonb;
      expected_resolution_hash text;
      expected_hygiene_ids uuid[];
      expected_hygiene_inputs jsonb;
      expected_hygiene_hash text;
      quality_watermark timestamptz;
      hygiene_watermark timestamptz;
      expected_manifest_count integer;
      closed_unit_count integer;
      hygiene_count integer;
      terminal_hygiene_count integer;
      passed_count integer;
      failed_count integer;
      inconclusive_count integer;
      not_evaluated_count integer;
      trusted_passed_count integer;
      autonomous_passed_count integer;
      expected_verdict_counts jsonb;
      expected_quality_axes jsonb;
      expected_hygiene_counts jsonb;
      expected_axis_distributions jsonb;
      expected_raw_pass_rate jsonb;
      expected_trusted_pass_rate jsonb;
      expected_autonomous_pass_rate jsonb;
      expected_decisive_pass_rate jsonb;
      expected_projection_watermark timestamptz;
      expected_schema_version text;
      expected_json_size integer;
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
          or (
            previous.finality = 'FULLY_RESOLVED'
            and new.finality = 'QUALITY_FINAL'
          )
        then
          raise exception 'TaskResultSnapshot revision chain is invalid';
        end if;
      elsif new.revision <> 1
        or new.supersedes_snapshot_id is not null
        or new.finality <> 'QUALITY_FINAL'
      then
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
        expected_resolution_inputs,
        quality_watermark,
        expected_manifest_count,
        closed_unit_count,
        passed_count,
        failed_count,
        inconclusive_count,
        not_evaluated_count,
        trusted_passed_count,
        autonomous_passed_count,
        expected_quality_axes
      from latest;

      if expected_manifest_count <> new.manifest_count
        or closed_unit_count <> new.manifest_count
        or expected_resolution_ids <> new.unit_resolution_revision_ids
        or passed_count + failed_count + inconclusive_count
          + not_evaluated_count <> new.manifest_count
      then
        raise exception 'TaskResultSnapshot Resolution coverage is invalid';
      end if;

      expected_resolution_hash := atlas.task_sha256_json(
        jsonb_build_object(
          'schemaVersion', 'atlas.task-result-resolution-set/0.1',
          'taskRunId', new.task_run_id::text,
          'manifestHash', new.manifest_hash,
          'inputs', expected_resolution_inputs
        )
      );
      if new.input_resolution_set_hash <> expected_resolution_hash then
        raise exception 'TaskResultSnapshot input Resolution set hash is invalid';
      end if;

      if new.finality = 'FULLY_RESOLVED' then
        if not exists (
          select 1
          from atlas.task_result_snapshot quality
          where quality.task_run_id = new.task_run_id
            and quality.finality = 'QUALITY_FINAL'
            and quality.input_resolution_set_hash =
              new.input_resolution_set_hash
            and quality.aggregation_policy_digest =
              'sha256:f047f7c9925cce522ccf743a0dcaf69d89f9a5d60a6856ab7654de971be8951e'
        ) then
          raise exception 'FULLY_RESOLVED requires its exact QUALITY_FINAL input';
        end if;

        with latest_hygiene as (
          select distinct on (hygiene.execution_unit_id)
            hygiene.*,
            unit.ordinal as unit_ordinal
          from atlas.execution_unit unit
          join atlas.unit_hygiene_resolution_revision hygiene
            on hygiene.execution_unit_id = unit.id
           and hygiene.task_run_id = unit.task_run_id
           and hygiene.tenant_id = unit.tenant_id
           and hygiene.project_id = unit.project_id
          where unit.task_run_id = new.task_run_id
            and unit.tenant_id = new.tenant_id
            and unit.project_id = new.project_id
          order by hygiene.execution_unit_id, hygiene.revision desc
        ),
        fresh_hygiene as (
          select hygiene.*
          from latest_hygiene hygiene
          where jsonb_array_length(hygiene.inputs) = (
            select count(*)
            from atlas.unit_attempt attempt
            where attempt.execution_unit_id = hygiene.execution_unit_id
              and attempt.lifecycle = 'CLOSED'
          )
          and not exists (
            select 1
            from jsonb_array_elements(hygiene.inputs)
              with ordinality as input(value, ordinal)
            left join atlas.unit_attempt attempt
              on attempt.id = (input.value ->> 'unitAttemptId')::uuid
             and attempt.execution_unit_id = hygiene.execution_unit_id
             and attempt.task_run_id = hygiene.task_run_id
             and attempt.tenant_id = hygiene.tenant_id
             and attempt.project_id = hygiene.project_id
            left join atlas.attempt_fixture_binding binding
              on binding.unit_attempt_id = attempt.id
            left join atlas.fixture_run fixture
              on fixture.id = binding.fixture_run_id
            where attempt.id is null
              or attempt.lifecycle <> 'CLOSED'
              or attempt.attempt_number <> input.ordinal
              or (input.value ->> 'attemptNumber')::integer <> input.ordinal
              or case input.value ->> 'source'
                when 'EXPLICIT_NOT_REQUIRED' then
                  attempt.hygiene <> 'NOT_REQUIRED'
                  or binding.id is not null
                  or input.value ->> 'dataHygiene' <> 'NOT_APPLICABLE'
                  or (input.value ->> 'observedAt')::timestamptz
                    <> coalesce(
                      attempt.cleanup_resolved_at,
                      attempt.updated_at
                    )
                when 'FIXTURE_RUN' then
                  binding.id is null
                  or fixture.id is null
                  or input.value ->> 'fixtureBindingId'
                    <> binding.id::text
                  or input.value ->> 'fixtureRunId'
                    <> fixture.id::text
                  or (input.value ->> 'fixtureRunRevision')::bigint
                    <> fixture.revision
                  or input.value ->> 'fixtureRunStatus'
                    <> fixture.status
                  or (input.value ->> 'cleanupGeneration')::integer
                    <> fixture.cleanup_generation
                  or input.value ->> 'fixturePlanDigest'
                    <> fixture.plan_digest
                  or (input.value ->> 'observedAt')::timestamptz
                    <> fixture.updated_at
                  or input.value ->> 'dataHygiene' <> case fixture.cleanup_state
                    when 'NOT_REQUIRED' then 'NOT_APPLICABLE'
                    when 'CLEANED' then 'CLEANED'
                    when 'LEAKED' then 'LEAKED'
                    else 'PENDING'
                  end
                else true
              end
          )
        )
        select
          array_agg(id order by unit_ordinal),
          jsonb_agg(
            jsonb_build_object(
              'ordinal', unit_ordinal,
              'executionUnitId', execution_unit_id::text,
              'unitHygieneResolutionRevisionId', id::text,
              'revision', revision,
              'inputSetHash', input_set_hash,
              'dataHygiene', data_hygiene,
              'resolutionPolicyDigest', resolution_policy_digest,
              'resolutionHash', resolution_hash
            )
            order by unit_ordinal
          ),
          max(created_at),
          count(*),
          count(*) filter (
            where data_hygiene in ('CLEANED', 'LEAKED', 'NOT_APPLICABLE')
          ),
          jsonb_build_object(
            'pending', count(*) filter (where data_hygiene = 'PENDING'),
            'cleaned', count(*) filter (where data_hygiene = 'CLEANED'),
            'cleanupFailed',
              count(*) filter (where data_hygiene = 'CLEANUP_FAILED'),
            'leaked', count(*) filter (where data_hygiene = 'LEAKED'),
            'notApplicable',
              count(*) filter (where data_hygiene = 'NOT_APPLICABLE')
          )
        into
          expected_hygiene_ids,
          expected_hygiene_inputs,
          hygiene_watermark,
          hygiene_count,
          terminal_hygiene_count,
          expected_hygiene_counts
        from fresh_hygiene;

        if hygiene_count <> new.manifest_count
          or terminal_hygiene_count <> new.manifest_count
          or expected_hygiene_ids
            <> new.unit_hygiene_resolution_revision_ids
        then
          raise exception 'TaskResultSnapshot Hygiene coverage is invalid';
        end if;
        expected_hygiene_hash := atlas.task_sha256_json(
          jsonb_build_object(
            'schemaVersion',
              'atlas.task-result-hygiene-resolution-set/0.1',
            'taskRunId', new.task_run_id::text,
            'manifestHash', new.manifest_hash,
            'inputs', expected_hygiene_inputs
          )
        );
        if new.input_hygiene_resolution_set_hash
          <> expected_hygiene_hash
        then
          raise exception 'TaskResultSnapshot Hygiene set hash is invalid';
        end if;
        expected_axis_distributions := jsonb_set(
          expected_quality_axes,
          '{dataHygiene}',
          expected_hygiene_counts
        );
        expected_projection_watermark :=
          greatest(quality_watermark, hygiene_watermark);
        expected_schema_version := 'atlas.task-result-snapshot/0.2';
        expected_json_size := 25;
      else
        expected_axis_distributions := expected_quality_axes;
        expected_projection_watermark := quality_watermark;
        expected_schema_version := 'atlas.task-result-snapshot/0.1';
        expected_json_size := 23;
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
      if new.projection_watermark <> expected_projection_watermark
        or new.verdict_counts <> expected_verdict_counts
        or new.axis_distributions <> expected_axis_distributions
        or new.raw_pass_rate <> expected_raw_pass_rate
        or new.trusted_pass_rate <> expected_trusted_pass_rate
        or new.autonomous_pass_rate <> expected_autonomous_pass_rate
        or new.decisive_pass_rate <> expected_decisive_pass_rate
      then
        raise exception 'TaskResultSnapshot aggregation is invalid';
      end if;

      if atlas.task_json_has_sensitive_keys(new.snapshot)
        or atlas.task_json_object_size(new.snapshot) <> expected_json_size
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
        or (
          new.finality = 'FULLY_RESOLVED'
          and not (
            new.snapshot ?& array[
              'unitHygieneResolutionRevisionIds',
              'inputHygieneResolutionSetHash'
            ]
          )
        )
        or new.snapshot ->> 'schemaVersion'
          is distinct from expected_schema_version
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
        or (
          new.finality = 'FULLY_RESOLVED'
          and (
            new.snapshot -> 'unitHygieneResolutionRevisionIds'
              is distinct from
                to_jsonb(new.unit_hygiene_resolution_revision_ids)
            or new.snapshot ->> 'inputHygieneResolutionSetHash'
              is distinct from new.input_hygiene_resolution_set_hash
          )
        )
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
      for each row execute function atlas.guard_task_result_snapshot_v2_insert()
    """,
    """
    revoke all on function atlas.guard_task_result_snapshot_v2_insert()
      from public, atlas_app, atlas_dispatcher
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (
        select 1
        from atlas.task_result_snapshot
        where finality = 'FULLY_RESOLVED'
           or unit_hygiene_resolution_revision_ids is not null
           or input_hygiene_resolution_set_hash is not null
        limit 1
      ) then
        raise exception 'cannot downgrade while FULLY_RESOLVED Snapshot facts exist';
      end if;
    end;
    $$
    """,
    "drop trigger task_result_snapshot_guard_insert on atlas.task_result_snapshot",
    "drop function atlas.guard_task_result_snapshot_v2_insert()",
    "drop index atlas.task_result_snapshot_fully_resolved_input_unique",
    "drop index atlas.task_result_snapshot_quality_input_unique",
    """
    alter table atlas.task_result_snapshot
      drop constraint task_result_snapshot_finality_valid,
      drop constraint task_result_snapshot_coverage_valid,
      drop constraint task_result_snapshot_digest_valid,
      drop constraint task_result_snapshot_policy_valid,
      drop column input_hygiene_resolution_set_hash,
      drop column unit_hygiene_resolution_revision_ids
    """,
    """
    alter table atlas.task_result_snapshot
      add constraint task_result_snapshot_input_unique unique (
        task_run_id, input_resolution_set_hash,
        aggregation_policy_digest, finality
      ),
      add constraint task_result_snapshot_finality_valid check (
        finality = 'QUALITY_FINAL'
      ),
      add constraint task_result_snapshot_coverage_valid check (
        manifest_count between 1 and 100000
        and cardinality(unit_resolution_revision_ids) = manifest_count
      ),
      add constraint task_result_snapshot_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and input_resolution_set_hash ~ '^sha256:[0-9a-f]{64}$'
        and aggregation_policy_digest ~ '^sha256:[0-9a-f]{64}$'
        and snapshot_hash ~ '^sha256:[0-9a-f]{64}$'
      ),
      add constraint task_result_snapshot_policy_valid check (
        aggregation_policy_version = '0.1.0'
        and aggregation_policy_digest =
          'sha256:f047f7c9925cce522ccf743a0dcaf69d89f9a5d60a6856ab7654de971be8951e'
      )
    """,
    """
    create trigger task_result_snapshot_guard_insert
      before insert on atlas.task_result_snapshot
      for each row execute function atlas.guard_task_result_snapshot_insert()
    """,
)


def upgrade() -> None:
    """Apply the FULLY_RESOLVED Snapshot revision contract."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove only the unpopulated FULLY_RESOLVED extension."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
