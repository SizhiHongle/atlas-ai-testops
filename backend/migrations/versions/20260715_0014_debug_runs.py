"""Create immutable DebugRun snapshots and monotonic runtime events.

Revision ID: 20260715_0014
Revises: 20260715_0013
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260715_0014"
down_revision: str | None = "20260715_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.debug_run (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      test_case_id uuid not null,
      draft_id uuid not null,
      semantic_revision bigint not null,
      semantic_digest text not null,
      compiled_digest text not null,
      test_ir jsonb not null,
      test_ir_digest text not null,
      plan_template jsonb not null,
      plan_digest text not null,
      lifecycle text not null default 'CREATED',
      outcome text not null default 'NOT_SET',
      snapshot_status text not null default 'CURRENT',
      temporal_workflow_id text not null,
      requested_by uuid,
      execution_deadline timestamptz not null,
      evidence_manifest_id uuid,
      evidence_manifest_digest text,
      failure_code text,
      failure_detail text,
      cancel_requested_at timestamptz,
      cancel_requested_by uuid,
      requested_at timestamptz not null,
      started_at timestamptz,
      completed_at timestamptz,
      outdated_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint debug_run_project_scope_fk foreign key (
        project_id, tenant_id
      ) references atlas.project (id, tenant_id) on delete restrict,
      constraint debug_run_environment_scope_fk foreign key (
        environment_id, tenant_id, project_id
      ) references atlas.environment (id, tenant_id, project_id) on delete restrict,
      constraint debug_run_draft_scope_fk foreign key (
        draft_id, test_case_id, tenant_id, project_id
      ) references atlas.workflow_draft (
        id, test_case_id, tenant_id, project_id
      ) on delete restrict,
      constraint debug_run_full_scope_unique unique (
        id, tenant_id, project_id, test_case_id
      ),
      constraint debug_run_temporal_workflow_unique unique (
        tenant_id, temporal_workflow_id
      ),
      constraint debug_run_revision_valid check (
        semantic_revision > 0 and revision > 0
      ),
      constraint debug_run_digest_valid check (
        semantic_digest ~ '^sha256:[0-9a-f]{64}$'
        and compiled_digest ~ '^sha256:[0-9a-f]{64}$'
        and test_ir_digest ~ '^sha256:[0-9a-f]{64}$'
        and plan_digest ~ '^sha256:[0-9a-f]{64}$'
        and (
          evidence_manifest_digest is null
          or evidence_manifest_digest ~ '^sha256:[0-9a-f]{64}$'
        )
      ),
      constraint debug_run_test_ir_shape check (
        jsonb_typeof(test_ir) = 'object'
        and test_ir ->> 'schemaVersion' = 'atlas.test-ir/0.2'
        and test_ir ->> 'testCaseId' = test_case_id::text
        and (test_ir ->> 'semanticRevision')::bigint = semantic_revision
        and test_ir ->> 'contentDigest' = test_ir_digest
      ),
      constraint debug_run_plan_shape check (
        jsonb_typeof(plan_template) = 'object'
        and plan_template ->> 'schemaVersion' = 'atlas.plan-template/0.1'
        and plan_template ->> 'testCaseId' = test_case_id::text
        and (plan_template ->> 'semanticRevision')::bigint = semantic_revision
        and plan_template ->> 'testIrDigest' = test_ir_digest
        and plan_template ->> 'planDigest' = plan_digest
      ),
      constraint debug_run_lifecycle_valid check (
        lifecycle in (
          'CREATED', 'BINDING', 'READY', 'RUNNING', 'FINALIZING', 'TERMINATED'
        )
      ),
      constraint debug_run_outcome_valid check (
        outcome in (
          'NOT_SET', 'PASSED', 'FAILED', 'BLOCKED', 'INCONCLUSIVE',
          'INFRA_ERROR', 'CANCELED'
        )
      ),
      constraint debug_run_snapshot_status_valid check (
        snapshot_status in ('CURRENT', 'OUTDATED')
      ),
      constraint debug_run_workflow_id_valid check (
        temporal_workflow_id ~ '^atlas-debug/[A-Za-z0-9/_-]+$'
        and char_length(temporal_workflow_id) between 20 and 320
      ),
      constraint debug_run_state_shape check (
        (
          lifecycle = 'TERMINATED'
          and outcome <> 'NOT_SET'
          and completed_at is not null
        ) or (
          lifecycle <> 'TERMINATED'
          and outcome = 'NOT_SET'
          and completed_at is null
        )
      ),
      constraint debug_run_started_shape check (
        (lifecycle = 'CREATED' and started_at is null)
        or (lifecycle <> 'CREATED' and started_at is not null)
      ),
      constraint debug_run_snapshot_shape check (
        (snapshot_status = 'CURRENT' and outdated_at is null)
        or (snapshot_status = 'OUTDATED' and outdated_at is not null)
      ),
      constraint debug_run_evidence_shape check (
        (evidence_manifest_id is null) = (evidence_manifest_digest is null)
        and (evidence_manifest_id is null or lifecycle = 'TERMINATED')
        and (
          outcome <> 'PASSED'
          or (
            evidence_manifest_id is not null
            and evidence_manifest_digest is not null
          )
        )
      ),
      constraint debug_run_failure_code_valid check (
        failure_code is null or failure_code ~ '^[A-Z][A-Z0-9_]{2,79}$'
      ),
      constraint debug_run_failure_detail_safe check (
        failure_detail is null or (
          btrim(failure_detail) <> '' and octet_length(failure_detail) <= 2000
        )
      ),
      constraint debug_run_failure_shape check (
        (
          failure_code is null
          and failure_detail is null
        ) or (
          failure_code is not null
          and failure_detail is not null
          and lifecycle in ('FINALIZING', 'TERMINATED')
          and outcome <> 'PASSED'
        )
      ),
      constraint debug_run_cancel_shape check (
        (cancel_requested_at is null) = (cancel_requested_by is null)
      ),
      constraint debug_run_time_order check (
        requested_at < execution_deadline
        and created_at >= requested_at
        and (started_at is null or started_at >= requested_at)
        and (completed_at is null or (
          started_at is not null and completed_at >= started_at
        ))
        and (cancel_requested_at is null or cancel_requested_at >= requested_at)
        and (outdated_at is null or outdated_at >= requested_at)
      )
    )
    """,
    """
    create table atlas.debug_run_event (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      test_case_id uuid not null,
      debug_run_id uuid not null,
      seq bigint not null,
      event_type text not null,
      lifecycle text not null,
      outcome text not null,
      snapshot_status text not null,
      payload jsonb not null default '{}'::jsonb,
      occurred_at timestamptz not null,
      constraint debug_run_event_run_scope_fk foreign key (
        debug_run_id, tenant_id, project_id, test_case_id
      ) references atlas.debug_run (
        id, tenant_id, project_id, test_case_id
      ) on delete restrict,
      constraint debug_run_event_sequence_unique unique (debug_run_id, seq),
      constraint debug_run_event_seq_positive check (seq > 0),
      constraint debug_run_event_type_valid check (
        event_type ~ '^[a-z][a-z0-9_.-]+$'
        and octet_length(event_type) <= 640
      ),
      constraint debug_run_event_lifecycle_valid check (
        lifecycle in (
          'CREATED', 'BINDING', 'READY', 'RUNNING', 'FINALIZING', 'TERMINATED'
        )
      ),
      constraint debug_run_event_outcome_valid check (
        outcome in (
          'NOT_SET', 'PASSED', 'FAILED', 'BLOCKED', 'INCONCLUSIVE',
          'INFRA_ERROR', 'CANCELED'
        )
      ),
      constraint debug_run_event_snapshot_status_valid check (
        snapshot_status in ('CURRENT', 'OUTDATED')
      ),
      constraint debug_run_event_payload_valid check (
        jsonb_typeof(payload) = 'object'
      )
    )
    """,
    """
    create index debug_run_case_history_idx
      on atlas.debug_run (
        tenant_id, project_id, test_case_id, requested_at desc, id desc
      )
    """,
    """
    create index debug_run_draft_scope_fk_idx
      on atlas.debug_run (draft_id, test_case_id, tenant_id, project_id)
    """,
    """
    create index debug_run_environment_scope_fk_idx
      on atlas.debug_run (environment_id, tenant_id, project_id)
    """,
    """
    create index debug_run_active_idx
      on atlas.debug_run (tenant_id, project_id, environment_id, requested_at, id)
      where lifecycle <> 'TERMINATED'
    """,
    """
    create index debug_run_current_pass_idx
      on atlas.debug_run (
        tenant_id, project_id, test_case_id, semantic_revision,
        semantic_digest, compiled_digest, completed_at desc, id desc
      )
      where snapshot_status = 'CURRENT'
        and lifecycle = 'TERMINATED'
        and outcome = 'PASSED'
    """,
    """
    create index debug_run_event_replay_idx
      on atlas.debug_run_event (debug_run_id, seq)
    """,
    """
    create index debug_run_event_scope_fk_idx
      on atlas.debug_run_event (
        debug_run_id, tenant_id, project_id, test_case_id
      )
    """,
    """
    create function atlas.guard_debug_run_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.test_case_id, new.draft_id, new.semantic_revision,
        new.semantic_digest, new.compiled_digest,
        new.test_ir, new.test_ir_digest, new.plan_template, new.plan_digest,
        new.temporal_workflow_id, new.requested_by,
        new.execution_deadline, new.requested_at, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.test_case_id, old.draft_id, old.semantic_revision,
        old.semantic_digest, old.compiled_digest,
        old.test_ir, old.test_ir_digest, old.plan_template, old.plan_digest,
        old.temporal_workflow_id, old.requested_by,
        old.execution_deadline, old.requested_at, old.created_at
      ) then
        raise exception 'debug run scope and compiled snapshot are immutable';
      end if;

      if old.snapshot_status = 'OUTDATED'
        and row(new.snapshot_status, new.outdated_at)
          is distinct from row(old.snapshot_status, old.outdated_at)
      then
        raise exception 'outdated debug run cannot become current';
      end if;

      if old.cancel_requested_at is not null and row(
        new.cancel_requested_at, new.cancel_requested_by
      ) is distinct from row(old.cancel_requested_at, old.cancel_requested_by) then
        raise exception 'debug run cancellation request is immutable';
      end if;

      if old.started_at is not null
        and new.started_at is distinct from old.started_at
      then
        raise exception 'debug run start timestamp is immutable';
      end if;

      if not (
        (old.lifecycle = 'CREATED' and new.lifecycle in (
          'CREATED', 'BINDING', 'FINALIZING'
        ))
        or (old.lifecycle = 'BINDING' and new.lifecycle in (
          'BINDING', 'READY', 'FINALIZING'
        ))
        or (old.lifecycle = 'READY' and new.lifecycle in (
          'READY', 'RUNNING', 'FINALIZING'
        ))
        or (old.lifecycle = 'RUNNING' and new.lifecycle in (
          'RUNNING', 'FINALIZING'
        ))
        or (old.lifecycle = 'FINALIZING' and new.lifecycle in (
          'FINALIZING', 'TERMINATED'
        ))
        or (old.lifecycle = 'TERMINATED' and new.lifecycle = 'TERMINATED')
      ) then
        raise exception 'invalid debug run lifecycle transition';
      end if;

      if old.lifecycle = 'TERMINATED' and row(
        new.outcome, new.evidence_manifest_id, new.evidence_manifest_digest,
        new.failure_code, new.failure_detail, new.started_at, new.completed_at,
        new.cancel_requested_at, new.cancel_requested_by
      ) is distinct from row(
        old.outcome, old.evidence_manifest_id, old.evidence_manifest_digest,
        old.failure_code, old.failure_detail, old.started_at, old.completed_at,
        old.cancel_requested_at, old.cancel_requested_by
      ) then
        raise exception 'terminated debug run result is immutable';
      end if;

      if new.revision <> old.revision + 1 then
        raise exception 'debug run revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_debug_run_event_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      run_lifecycle text;
      run_outcome text;
      run_snapshot_status text;
      expected_seq bigint;
      run_requested_at timestamptz;
    begin
      select lifecycle, outcome, snapshot_status, requested_at
      into run_lifecycle, run_outcome, run_snapshot_status, run_requested_at
      from atlas.debug_run
      where id = new.debug_run_id
        and tenant_id = new.tenant_id
        and project_id = new.project_id
        and test_case_id = new.test_case_id
      for update;

      if not found then
        raise exception 'debug run event requires a matching run';
      end if;
      if row(new.lifecycle, new.outcome, new.snapshot_status)
        is distinct from row(run_lifecycle, run_outcome, run_snapshot_status)
      then
        raise exception 'debug run event state must match the current run';
      end if;
      select coalesce(max(seq), 0) + 1
      into expected_seq
      from atlas.debug_run_event
      where debug_run_id = new.debug_run_id;
      if new.seq <> expected_seq then
        raise exception 'debug run event sequence must be gapless';
      end if;
      if new.occurred_at < run_requested_at then
        raise exception 'debug run event cannot predate the run';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger debug_run_guard_update
      before update on atlas.debug_run
      for each row execute function atlas.guard_debug_run_update()
    """,
    """
    create trigger debug_run_event_guard_insert
      before insert on atlas.debug_run_event
      for each row execute function atlas.guard_debug_run_event_insert()
    """,
    """
    create trigger debug_run_set_updated_at
      before update on atlas.debug_run
      for each row execute function atlas.set_updated_at()
    """,
    "alter table atlas.debug_run enable row level security",
    "alter table atlas.debug_run force row level security",
    "alter table atlas.debug_run_event enable row level security",
    "alter table atlas.debug_run_event force row level security",
    """
    create policy debug_run_tenant_isolation
      on atlas.debug_run for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy debug_run_event_tenant_isolation
      on atlas.debug_run_event for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.debug_run from atlas_app",
    "revoke all on atlas.debug_run_event from atlas_app",
    "grant select, insert, update on atlas.debug_run to atlas_app",
    "grant select, insert on atlas.debug_run_event to atlas_app",
)


def upgrade() -> None:
    """Create tenant-isolated immutable DebugRun control-plane storage."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove DebugRun snapshots, events, and lifecycle guards."""

    op.execute("drop table if exists atlas.debug_run_event")
    op.execute("drop table if exists atlas.debug_run")
    op.execute("drop function if exists atlas.guard_debug_run_event_insert()")
    op.execute("drop function if exists atlas.guard_debug_run_update()")
