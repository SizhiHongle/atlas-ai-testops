"""Add no-Seal closure facts and append-only Unit result resolution.

Revision ID: 20260718_0033
Revises: 20260718_0032
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0033"
down_revision: str | None = "20260718_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.attempt_closure_notice (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      manifest_hash text not null,
      unit_key text not null,
      attempt_number integer not null,
      source_status text not null,
      verdict text not null,
      outcome_class text not null,
      closure_reason text not null,
      data_hygiene text not null,
      evidence_completeness text not null,
      evidence_integrity text not null,
      execution_influence text not null,
      closed_at timestamptz not null,
      created_at timestamptz not null,
      notice_hash text not null,
      notice jsonb not null,
      constraint attempt_closure_notice_attempt_scope_fk foreign key (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.unit_attempt (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint attempt_closure_notice_attempt_unique unique (unit_attempt_id),
      constraint attempt_closure_notice_hash_unique unique (
        tenant_id, notice_hash
      ),
      constraint attempt_closure_notice_full_scope_unique unique (
        id, unit_attempt_id, execution_unit_id, task_run_id,
        tenant_id, project_id, notice_hash
      ),
      constraint attempt_closure_notice_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and unit_key ~ '^sha256:[0-9a-f]{64}$'
        and notice_hash ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint attempt_closure_notice_attempt_number_valid check (
        attempt_number >= 1
      ),
      constraint attempt_closure_notice_source_valid check (
        source_status in (
          'FINISHED_UNSEALED', 'FAILED', 'INFRA_ERROR',
          'INCONCLUSIVE', 'CANCELED'
        )
      ),
      constraint attempt_closure_notice_verdict_valid check (
        verdict in ('INCONCLUSIVE', 'NOT_EVALUATED')
      ),
      constraint attempt_closure_notice_outcome_valid check (
        outcome_class in (
          'BUSINESS', 'DEPENDENCY', 'PLATFORM', 'USER',
          'AUTOMATION', 'POLICY', 'UNKNOWN'
        )
      ),
      constraint attempt_closure_notice_reason_valid check (
        closure_reason ~ '^[A-Z][A-Z0-9_]{1,95}$'
      ),
      constraint attempt_closure_notice_axes_valid check (
        data_hygiene in (
          'PENDING', 'CLEANED', 'CLEANUP_FAILED', 'LEAKED', 'NOT_APPLICABLE'
        )
        and evidence_completeness in ('MISSING', 'NOT_APPLICABLE')
        and evidence_integrity = 'UNVERIFIED'
        and execution_influence = 'AUTONOMOUS'
        and (
          (verdict = 'NOT_EVALUATED'
            and evidence_completeness = 'NOT_APPLICABLE')
          or
          (verdict = 'INCONCLUSIVE'
            and evidence_completeness = 'MISSING')
        )
      ),
      constraint attempt_closure_notice_status_semantics check (
        (source_status = 'CANCELED' and outcome_class = 'USER')
        or
        (source_status = 'INFRA_ERROR'
          and verdict = 'INCONCLUSIVE'
          and outcome_class = 'PLATFORM')
        or
        (source_status in ('FINISHED_UNSEALED', 'FAILED')
          and verdict = 'INCONCLUSIVE'
          and outcome_class = 'AUTOMATION')
        or
        (source_status = 'INCONCLUSIVE'
          and verdict = 'INCONCLUSIVE'
          and outcome_class = 'UNKNOWN')
      ),
      constraint attempt_closure_notice_time_valid check (
        created_at >= closed_at
      ),
      constraint attempt_closure_notice_object check (
        jsonb_typeof(notice) = 'object'
      )
    )
    """,
    """
    create table atlas.unit_resolution_revision (
      id uuid primary key,
      unit_resolution_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      manifest_hash text not null,
      unit_key text not null,
      revision integer not null,
      input_seal_ids uuid[] not null,
      input_closure_notice_ids uuid[] not null,
      input_set_hash text not null,
      effective_verdict text not null,
      outcome_class text not null,
      closure_reason text not null,
      data_hygiene text not null,
      evidence_completeness text not null,
      evidence_integrity text not null,
      execution_influence text not null,
      stability text not null,
      decisive_unit_attempt_id uuid not null,
      decisive_attempt_number integer not null,
      resolution_policy_version text not null,
      resolution_policy_digest text not null,
      supersedes_revision_id uuid,
      created_at timestamptz not null,
      constraint unit_resolution_unit_scope_fk foreign key (
        execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.execution_unit (
        id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint unit_resolution_decisive_attempt_scope_fk foreign key (
        decisive_unit_attempt_id, execution_unit_id, task_run_id,
        tenant_id, project_id
      ) references atlas.unit_attempt (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint unit_resolution_supersedes_fk foreign key (
        supersedes_revision_id
      ) references atlas.unit_resolution_revision(id) on delete restrict,
      constraint unit_resolution_revision_unique unique (
        execution_unit_id, revision
      ),
      constraint unit_resolution_input_unique unique (
        execution_unit_id, input_set_hash, resolution_policy_digest
      ),
      constraint unit_resolution_full_scope_unique unique (
        id, execution_unit_id, task_run_id, tenant_id, project_id, revision
      ),
      constraint unit_resolution_revision_valid check (
        revision >= 1
        and decisive_attempt_number >= 1
        and (
          (revision = 1 and supersedes_revision_id is null)
          or
          (revision > 1 and supersedes_revision_id is not null)
        )
      ),
      constraint unit_resolution_inputs_valid check (
        cardinality(input_seal_ids) + cardinality(input_closure_notice_ids)
          between 1 and 100
      ),
      constraint unit_resolution_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and unit_key ~ '^sha256:[0-9a-f]{64}$'
        and input_set_hash ~ '^sha256:[0-9a-f]{64}$'
        and resolution_policy_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint unit_resolution_verdict_valid check (
        effective_verdict in (
          'PASSED', 'FAILED', 'INCONCLUSIVE', 'NOT_EVALUATED'
        )
      ),
      constraint unit_resolution_outcome_valid check (
        outcome_class in (
          'BUSINESS', 'DEPENDENCY', 'PLATFORM', 'USER',
          'AUTOMATION', 'POLICY', 'UNKNOWN'
        )
      ),
      constraint unit_resolution_reason_valid check (
        closure_reason ~ '^[A-Z][A-Z0-9_]{1,95}$'
      ),
      constraint unit_resolution_axes_valid check (
        data_hygiene in (
          'PENDING', 'CLEANED', 'CLEANUP_FAILED', 'LEAKED', 'NOT_APPLICABLE'
        )
        and evidence_completeness in (
          'PENDING', 'COMPLETE', 'PARTIAL', 'MISSING', 'NOT_APPLICABLE'
        )
        and evidence_integrity in ('UNVERIFIED', 'VERIFIED', 'INVALID')
        and execution_influence in (
          'AUTONOMOUS', 'MANUAL_ASSISTED', 'MANUAL_ONLY'
        )
        and stability in (
          'UNKNOWN', 'STABLE', 'INFRA_RECOVERED',
          'FLAKY_SUSPECT', 'FLAKY_CONFIRMED'
        )
      ),
      constraint unit_resolution_pass_valid check (
        effective_verdict <> 'PASSED'
        or (
          evidence_completeness = 'COMPLETE'
          and evidence_integrity = 'VERIFIED'
        )
      ),
      constraint unit_resolution_policy_valid check (
        resolution_policy_version = '0.1.0'
        and resolution_policy_digest =
          'sha256:54bd8eb8d8dd0d24c925a36b4800f5fa69977b4b3153b46e55573e3e1286be26'
      )
    )
    """,
    """
    create function atlas.guard_result_terminal_exclusivity()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    begin
      if tg_table_name = 'unit_attempt_result_fact' then
        if exists (
          select 1 from atlas.attempt_closure_notice notice
          where notice.unit_attempt_id = new.unit_attempt_id
        ) then
          raise exception 'UnitAttempt already has a ClosureNotice';
        end if;
      elsif exists (
        select 1 from atlas.unit_attempt_result_fact fact
        where fact.unit_attempt_id = new.unit_attempt_id
      ) then
        raise exception 'UnitAttempt already has an AttemptSeal';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger unit_attempt_result_fact_terminal_exclusivity
      before insert on atlas.unit_attempt_result_fact
      for each row execute function atlas.guard_result_terminal_exclusivity()
    """,
    """
    create trigger attempt_closure_notice_terminal_exclusivity
      before insert on atlas.attempt_closure_notice
      for each row execute function atlas.guard_result_terminal_exclusivity()
    """,
    """
    create function atlas.guard_attempt_closure_notice_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_attempt atlas.unit_attempt%rowtype;
      expected_hygiene text;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'ClosureNotice insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into stored_attempt
      from atlas.unit_attempt attempt
      where attempt.id = new.unit_attempt_id
        and attempt.execution_unit_id = new.execution_unit_id
        and attempt.task_run_id = new.task_run_id
        and attempt.tenant_id = new.tenant_id
        and attempt.project_id = new.project_id
      for update;

      expected_hygiene := case stored_attempt.hygiene
        when 'NOT_REQUIRED' then 'NOT_APPLICABLE'
        when 'PENDING' then 'PENDING'
        when 'RUNNING' then 'PENDING'
        when 'CLEANED' then 'CLEANED'
        when 'CLEANUP_FAILED' then 'CLEANUP_FAILED'
        when 'LEAKED' then 'LEAKED'
        else null
      end;
      if not found
        or stored_attempt.lifecycle <> 'CLOSED'
        or stored_attempt.closed_at is null
        or stored_attempt.closed_at <> new.closed_at
        or stored_attempt.manifest_hash <> new.manifest_hash
        or stored_attempt.unit_key <> new.unit_key
        or stored_attempt.attempt_number <> new.attempt_number
        or expected_hygiene <> new.data_hygiene
        or new.created_at <> transaction_timestamp()
        or not (
          (stored_attempt.quality = 'INCONCLUSIVE'
            and new.source_status in ('FINISHED_UNSEALED', 'INCONCLUSIVE'))
          or
          (stored_attempt.quality = 'FAILED' and new.source_status = 'FAILED')
          or
          (stored_attempt.quality = 'INFRA_ERROR'
            and new.source_status = 'INFRA_ERROR')
          or
          (stored_attempt.quality = 'CANCELED'
            and new.source_status = 'CANCELED')
        )
        or (
          new.source_status = 'CANCELED'
          and (
            new.verdict <> case
              when stored_attempt.started_at is null
                then 'NOT_EVALUATED'
              else 'INCONCLUSIVE'
            end
            or new.outcome_class <> 'USER'
          )
        )
      then
        raise exception 'ClosureNotice requires the exact terminal UnitAttempt';
      end if;

      if atlas.task_json_has_sensitive_keys(new.notice)
        or atlas.task_json_object_size(new.notice) <> 21
        or new.notice ->> 'schemaVersion'
          <> 'atlas.attempt-closure-notice/0.1'
        or new.notice ->> 'id' <> new.id::text
        or new.notice ->> 'tenantId' <> new.tenant_id::text
        or new.notice ->> 'projectId' <> new.project_id::text
        or new.notice ->> 'taskRunId' <> new.task_run_id::text
        or new.notice ->> 'executionUnitId' <> new.execution_unit_id::text
        or new.notice ->> 'unitAttemptId' <> new.unit_attempt_id::text
        or new.notice ->> 'manifestHash' <> new.manifest_hash
        or new.notice ->> 'unitKey' <> new.unit_key
        or (new.notice ->> 'attemptNumber')::integer <> new.attempt_number
        or new.notice ->> 'sourceStatus' <> new.source_status
        or new.notice ->> 'verdict' <> new.verdict
        or new.notice ->> 'outcomeClass' <> new.outcome_class
        or new.notice ->> 'closureReason' <> new.closure_reason
        or new.notice ->> 'dataHygiene' <> new.data_hygiene
        or new.notice ->> 'evidenceCompleteness'
          <> new.evidence_completeness
        or new.notice ->> 'evidenceIntegrity' <> new.evidence_integrity
        or new.notice ->> 'executionInfluence' <> new.execution_influence
        or (new.notice ->> 'closedAt')::timestamptz <> new.closed_at
        or (new.notice ->> 'createdAt')::timestamptz <> new.created_at
        or new.notice ->> 'noticeHash' <> new.notice_hash
        or atlas.task_sha256_json(new.notice - 'noticeHash')
          <> new.notice_hash
      then
        raise exception 'ClosureNotice persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger attempt_closure_notice_guard_insert
      before insert on atlas.attempt_closure_notice
      for each row execute function atlas.guard_attempt_closure_notice_insert()
    """,
    """
    create function atlas.guard_unit_resolution_revision_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_unit atlas.execution_unit%rowtype;
      previous atlas.unit_resolution_revision%rowtype;
      decisive record;
      expected_seal_ids uuid[];
      expected_closure_ids uuid[];
      expected_inputs jsonb;
      expected_input_hash text;
      source_count integer;
      passed_count integer;
      failed_count integer;
      prior_platform_count integer;
      failure_fingerprint_count integer;
      expected_stability text;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'UnitResolution insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into stored_unit
      from atlas.execution_unit unit
      where unit.id = new.execution_unit_id
        and unit.task_run_id = new.task_run_id
        and unit.tenant_id = new.tenant_id
        and unit.project_id = new.project_id
      for update;
      if not found
        or stored_unit.manifest_hash <> new.manifest_hash
        or stored_unit.unit_key <> new.unit_key
        or new.created_at <> transaction_timestamp()
      then
        raise exception 'UnitResolution requires the exact ExecutionUnit';
      end if;

      select * into previous
      from atlas.unit_resolution_revision resolution
      where resolution.execution_unit_id = new.execution_unit_id
      order by resolution.revision desc
      limit 1;
      if found then
        if new.revision <> previous.revision + 1
          or new.unit_resolution_id <> previous.unit_resolution_id
          or new.supersedes_revision_id <> previous.id
        then
          raise exception 'UnitResolution revision chain is invalid';
        end if;
      elsif new.revision <> 1 or new.supersedes_revision_id is not null then
        raise exception 'UnitResolution first revision is invalid';
      end if;

      with sources as (
        select
          attempt.attempt_number,
          attempt.id as unit_attempt_id,
          fact.seal_id,
          notice.id as closure_notice_id,
          coalesce(fact.seal_id, notice.id) as fact_id,
          coalesce(fact.content_hash, notice.notice_hash) as fact_hash,
          case when fact.seal_id is not null
            then 'SEAL' else 'CLOSURE_NOTICE'
          end as fact_kind,
          case
            when fact.seal_id is null then notice.verdict
            when fact.evidence_integrity <> 'VERIFIED'
              or fact.evidence_completeness in ('PENDING', 'MISSING')
              then 'INCONCLUSIVE'
            else fact.oracle_verdict
          end as effective_verdict,
          coalesce(fact.outcome_class, notice.outcome_class) as outcome_class,
          coalesce(fact.closure_reason, notice.closure_reason) as closure_reason,
          coalesce(fact.data_hygiene, notice.data_hygiene) as data_hygiene,
          coalesce(
            fact.evidence_completeness, notice.evidence_completeness
          ) as evidence_completeness,
          coalesce(fact.evidence_integrity, notice.evidence_integrity)
            as evidence_integrity,
          coalesce(fact.execution_influence, notice.execution_influence)
            as execution_influence,
          case
            when fact.seal_id is not null
              and fact.oracle_verdict = 'FAILED'
            then fact.closure_reason || ':' || fact.oracle_results_hash
            else null
          end as failure_fingerprint
        from atlas.unit_attempt attempt
        left join atlas.unit_attempt_result_fact fact
          on fact.unit_attempt_id = attempt.id
        left join atlas.attempt_closure_notice notice
          on notice.unit_attempt_id = attempt.id
        where attempt.execution_unit_id = new.execution_unit_id
          and attempt.lifecycle = 'CLOSED'
      )
      select
        coalesce(
          array_agg(seal_id order by attempt_number)
            filter (where seal_id is not null),
          '{}'::uuid[]
        ),
        coalesce(
          array_agg(closure_notice_id order by attempt_number)
            filter (where closure_notice_id is not null),
          '{}'::uuid[]
        ),
        jsonb_agg(
          jsonb_build_object(
            'attemptNumber', attempt_number,
            'unitAttemptId', unit_attempt_id::text,
            'kind', fact_kind,
            'factId', fact_id::text,
            'factHash', fact_hash
          )
          order by attempt_number
        ),
        count(*),
        count(*) filter (where effective_verdict = 'PASSED'),
        count(*) filter (where effective_verdict = 'FAILED'),
        count(*) filter (
          where attempt_number < new.decisive_attempt_number
            and outcome_class = 'PLATFORM'
        ),
        count(distinct failure_fingerprint)
          filter (where failure_fingerprint is not null)
      into
        expected_seal_ids,
        expected_closure_ids,
        expected_inputs,
        source_count,
        passed_count,
        failed_count,
        prior_platform_count,
        failure_fingerprint_count
      from sources;

      if source_count = 0
        or source_count <> cardinality(expected_seal_ids)
          + cardinality(expected_closure_ids)
        or expected_seal_ids <> new.input_seal_ids
        or expected_closure_ids <> new.input_closure_notice_ids
      then
        raise exception 'UnitResolution terminal input coverage is invalid';
      end if;

      expected_input_hash := atlas.task_sha256_json(
        jsonb_build_object(
          'schemaVersion', 'atlas.unit-resolution-input-set/0.1',
          'executionUnitId', new.execution_unit_id::text,
          'manifestHash', new.manifest_hash,
          'unitKey', new.unit_key,
          'inputs', expected_inputs
        )
      );
      if new.input_set_hash <> expected_input_hash then
        raise exception 'UnitResolution input set hash is invalid';
      end if;

      with sources as (
        select
          attempt.id as unit_attempt_id,
          attempt.attempt_number,
          case
            when fact.seal_id is null then notice.verdict
            when fact.evidence_integrity <> 'VERIFIED'
              or fact.evidence_completeness in ('PENDING', 'MISSING')
              then 'INCONCLUSIVE'
            else fact.oracle_verdict
          end as effective_verdict,
          coalesce(fact.outcome_class, notice.outcome_class) as outcome_class,
          coalesce(fact.closure_reason, notice.closure_reason) as closure_reason,
          coalesce(fact.data_hygiene, notice.data_hygiene) as data_hygiene,
          coalesce(
            fact.evidence_completeness, notice.evidence_completeness
          ) as evidence_completeness,
          coalesce(fact.evidence_integrity, notice.evidence_integrity)
            as evidence_integrity,
          coalesce(fact.execution_influence, notice.execution_influence)
            as execution_influence
        from atlas.unit_attempt attempt
        left join atlas.unit_attempt_result_fact fact
          on fact.unit_attempt_id = attempt.id
        left join atlas.attempt_closure_notice notice
          on notice.unit_attempt_id = attempt.id
        where attempt.execution_unit_id = new.execution_unit_id
          and attempt.lifecycle = 'CLOSED'
      )
      select * into decisive
      from sources
      order by attempt_number desc
      limit 1;
      if decisive.unit_attempt_id <> new.decisive_unit_attempt_id
        or decisive.attempt_number <> new.decisive_attempt_number
        or decisive.effective_verdict <> new.effective_verdict
        or decisive.outcome_class <> new.outcome_class
        or decisive.closure_reason <> new.closure_reason
        or decisive.data_hygiene <> new.data_hygiene
        or decisive.evidence_completeness <> new.evidence_completeness
        or decisive.evidence_integrity <> new.evidence_integrity
        or decisive.execution_influence <> new.execution_influence
      then
        raise exception 'UnitResolution decisive projection is invalid';
      end if;

      expected_stability := case
        when source_count = 1
          and new.effective_verdict in ('PASSED', 'FAILED')
          then 'STABLE'
        when new.effective_verdict = 'PASSED' and failed_count > 0
          then 'FLAKY_SUSPECT'
        when new.effective_verdict = 'PASSED' and prior_platform_count > 0
          then 'INFRA_RECOVERED'
        when new.effective_verdict = 'PASSED' and passed_count = source_count
          then 'STABLE'
        when failed_count = source_count and failure_fingerprint_count = 1
          then 'STABLE'
        when passed_count > 0 and failed_count > 0
          then 'FLAKY_SUSPECT'
        else 'UNKNOWN'
      end;
      if new.stability <> expected_stability then
        raise exception 'UnitResolution Stability is invalid';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger unit_resolution_revision_guard_insert
      before insert on atlas.unit_resolution_revision
      for each row execute function atlas.guard_unit_resolution_revision_insert()
    """,
    """
    create trigger attempt_closure_notice_prevent_mutation
      before update or delete on atlas.attempt_closure_notice
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger unit_resolution_revision_prevent_mutation
      before update or delete on atlas.unit_resolution_revision
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create index attempt_closure_notice_task_idx
      on atlas.attempt_closure_notice (
        tenant_id, project_id, task_run_id, execution_unit_id, attempt_number
      )
    """,
    """
    create index unit_resolution_task_idx
      on atlas.unit_resolution_revision (
        tenant_id, project_id, task_run_id, execution_unit_id, revision desc
      )
    """,
    """
    create index unit_resolution_latest_idx
      on atlas.unit_resolution_revision (
        tenant_id, execution_unit_id, revision desc
      )
    """,
    "alter table atlas.attempt_closure_notice enable row level security",
    "alter table atlas.attempt_closure_notice force row level security",
    "alter table atlas.unit_resolution_revision enable row level security",
    "alter table atlas.unit_resolution_revision force row level security",
    """
    create policy attempt_closure_notice_tenant_isolation
      on atlas.attempt_closure_notice for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy unit_resolution_revision_tenant_isolation
      on atlas.unit_resolution_revision for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.attempt_closure_notice from atlas_app",
    "revoke all on atlas.unit_resolution_revision from atlas_app",
    "grant select, insert on atlas.attempt_closure_notice to atlas_app",
    "grant select, insert on atlas.unit_resolution_revision to atlas_app",
    """
    revoke all on function atlas.guard_result_terminal_exclusivity()
      from public, atlas_app, atlas_dispatcher
    """,
    """
    revoke all on function atlas.guard_attempt_closure_notice_insert()
      from public, atlas_app, atlas_dispatcher
    """,
    """
    revoke all on function atlas.guard_unit_resolution_revision_insert()
      from public, atlas_app, atlas_dispatcher
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.attempt_closure_notice limit 1)
        or exists (select 1 from atlas.unit_resolution_revision limit 1)
      then
        raise exception 'cannot downgrade while Result projection facts exist';
      end if;
    end;
    $$
    """,
    """
    drop trigger if exists unit_attempt_result_fact_terminal_exclusivity
      on atlas.unit_attempt_result_fact
    """,
    "drop table if exists atlas.unit_resolution_revision",
    "drop table if exists atlas.attempt_closure_notice",
    "drop function if exists atlas.guard_unit_resolution_revision_insert()",
    "drop function if exists atlas.guard_attempt_closure_notice_insert()",
    "drop function if exists atlas.guard_result_terminal_exclusivity()",
)


def upgrade() -> None:
    """Apply Result projection truth tables and guards."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove only empty Result projection tables."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
