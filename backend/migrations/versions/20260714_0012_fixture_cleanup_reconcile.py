# ruff: noqa: E501
"""Add fixture reconcile, retryable cleanup, cancellation, and cleanup evidence.

Revision ID: 20260714_0012
Revises: 20260714_0011
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260714_0012"
down_revision: str | None = "20260714_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    alter table atlas.fixture_run
      add column terminal_intent text,
      add column cancel_requested_at timestamptz,
      add column cancel_requested_by uuid,
      add column cleanup_generation integer not null default 0,
      add constraint fixture_run_terminal_intent_valid check (
        terminal_intent is null or terminal_intent in ('RELEASED', 'FAILED', 'CANCELED')
      ),
      add constraint fixture_run_cancel_request_shape check (
        (cancel_requested_at is null) = (cancel_requested_by is null)
      ),
      add constraint fixture_run_cleanup_generation_valid check (
        cleanup_generation between 0 and 1000
      )
    """,
    "alter table atlas.fixture_run disable trigger fixture_run_guard_update",
    """
    update atlas.fixture_run
    set terminal_intent = case
      when status in ('CLEANING', 'RELEASED', 'CLEANUP_FAILED') then 'RELEASED'
      when status = 'FAILED' then 'FAILED'
      when status = 'CANCELED' then 'CANCELED'
      else null
    end
    """,
    "alter table atlas.fixture_run enable trigger fixture_run_guard_update",
    """
    alter table atlas.fixture_run
      add constraint fixture_run_terminal_intent_shape check (
        (status in ('REQUESTED', 'RUNNING') and terminal_intent is null)
        or (status = 'READY' and terminal_intent in (null, 'RELEASED'))
        or (status = 'CLEANING' and terminal_intent is not null)
        or (status = 'RELEASED' and terminal_intent = 'RELEASED')
        or (status = 'CLEANUP_FAILED' and terminal_intent = 'RELEASED')
        or (status = 'FAILED' and terminal_intent = 'FAILED')
        or (status = 'CANCELED' and terminal_intent = 'CANCELED')
      ) not valid
    """,
    "alter table atlas.fixture_run validate constraint fixture_run_terminal_intent_shape",
    """
    alter table atlas.data_node_run
      add column reconcile_state text not null default 'NOT_REQUIRED',
      add column reconcile_attempt_count integer not null default 0,
      add column next_reconcile_at timestamptz,
      add constraint data_node_run_reconcile_state_valid check (
        reconcile_state in (
          'NOT_REQUIRED', 'PENDING', 'RUNNING', 'FOUND', 'ABSENT',
          'INCONCLUSIVE', 'EXHAUSTED'
        )
      ),
      add constraint data_node_run_reconcile_attempt_count_valid check (
        reconcile_attempt_count between 0 and 32
      )
    """,
    "alter table atlas.data_node_run disable trigger data_node_run_guard_update",
    """
    update atlas.data_node_run
    set reconcile_state = 'PENDING', next_reconcile_at = updated_at
    where status = 'OUTCOME_UNCERTAIN'
    """,
    "alter table atlas.data_node_run enable trigger data_node_run_guard_update",
    """
    alter table atlas.data_node_run
      add constraint data_node_run_reconcile_schedule_shape check (
        (
          reconcile_state in ('PENDING', 'INCONCLUSIVE')
          and next_reconcile_at is not null
        ) or (
          reconcile_state not in ('PENDING', 'INCONCLUSIVE')
          and next_reconcile_at is null
        )
      ) not valid
    """,
    "alter table atlas.data_node_run validate constraint data_node_run_reconcile_schedule_shape",
    """
    alter table atlas.resource_record
      add column next_cleanup_at timestamptz
    """,
    "alter table atlas.resource_record disable trigger resource_record_guard_update",
    """
    update atlas.resource_record
    set next_cleanup_at = updated_at
    where status in ('CLEANUP_PENDING', 'BLOCKED_BY_CHILD', 'ORPHAN_SUSPECTED')
    """,
    "alter table atlas.resource_record enable trigger resource_record_guard_update",
    """
    alter table atlas.resource_record
      add constraint resource_record_cleanup_schedule_shape check (
        (
          status in ('CLEANUP_PENDING', 'BLOCKED_BY_CHILD', 'ORPHAN_SUSPECTED')
          and next_cleanup_at is not null
        ) or (
          status not in ('CLEANUP_PENDING', 'BLOCKED_BY_CHILD', 'ORPHAN_SUSPECTED')
          and next_cleanup_at is null
        )
      ) not valid
    """,
    "alter table atlas.resource_record validate constraint resource_record_cleanup_schedule_shape",
    """
    create table atlas.data_node_reconcile_attempt (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      fixture_run_id uuid not null,
      data_node_run_id uuid not null,
      attempt_number integer not null,
      status text not null default 'RUNNING',
      failure_category text,
      failure_code text,
      failure_detail text,
      provider_request_id text,
      started_at timestamptz not null,
      finished_at timestamptz,
      updated_at timestamptz not null default clock_timestamp(),
      constraint data_node_reconcile_attempt_node_scope_fk foreign key (
        data_node_run_id, fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.data_node_run (
        id, fixture_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint data_node_reconcile_attempt_full_scope_unique unique (
        id, data_node_run_id, fixture_run_id, tenant_id, project_id, environment_id
      ),
      constraint data_node_reconcile_attempt_number_unique unique (
        data_node_run_id, attempt_number
      ),
      constraint data_node_reconcile_attempt_number_valid check (
        attempt_number between 1 and 32
      ),
      constraint data_node_reconcile_attempt_status_valid check (
        status in ('RUNNING', 'FOUND', 'ABSENT', 'INCONCLUSIVE', 'FAILED')
      ),
      constraint data_node_reconcile_attempt_failure_category_valid check (
        failure_category is null or failure_category in (
          'VALIDATION', 'POLICY', 'AUTH', 'RATE_LIMIT', 'TRANSIENT',
          'UNCERTAIN', 'CLEANUP', 'INFRASTRUCTURE'
        )
      ),
      constraint data_node_reconcile_attempt_failure_code_format check (
        failure_code is null or failure_code ~ '^[A-Z][A-Z0-9_]{2,79}$'
      ),
      constraint data_node_reconcile_attempt_failure_detail_safe check (
        failure_detail is null or (
          btrim(failure_detail) <> '' and octet_length(failure_detail) <= 2000
        )
      ),
      constraint data_node_reconcile_attempt_request_id_size check (
        provider_request_id is null or (
          btrim(provider_request_id) <> '' and octet_length(provider_request_id) <= 800
        )
      ),
      constraint data_node_reconcile_attempt_shape check (
        (
          status = 'RUNNING' and finished_at is null
          and failure_category is null and failure_code is null and failure_detail is null
        ) or (
          status in ('FOUND', 'ABSENT') and finished_at is not null
          and failure_category is null and failure_code is null and failure_detail is null
        ) or (
          status in ('INCONCLUSIVE', 'FAILED') and finished_at is not null
          and failure_category is not null and failure_code is not null
          and failure_detail is not null
        )
      )
    )
    """,
    """
    create table atlas.resource_cleanup_attempt (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      fixture_run_id uuid not null,
      resource_record_id uuid not null,
      cleanup_generation integer not null,
      status text not null default 'RUNNING',
      worker_identity text not null,
      failure_category text,
      failure_code text,
      failure_detail text,
      provider_request_id text,
      started_at timestamptz not null,
      finished_at timestamptz,
      updated_at timestamptz not null default clock_timestamp(),
      constraint resource_cleanup_attempt_resource_scope_fk foreign key (
        resource_record_id, fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.resource_record (
        id, fixture_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint resource_cleanup_attempt_full_scope_unique unique (
        id, resource_record_id, fixture_run_id,
        tenant_id, project_id, environment_id
      ),
      constraint resource_cleanup_attempt_generation_unique unique (
        resource_record_id, cleanup_generation
      ),
      constraint resource_cleanup_attempt_generation_valid check (
        cleanup_generation between 1 and 1000
      ),
      constraint resource_cleanup_attempt_status_valid check (
        status in ('RUNNING', 'SUCCEEDED', 'FAILED', 'OUTCOME_UNCERTAIN')
      ),
      constraint resource_cleanup_attempt_worker_format check (
        worker_identity ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$'
      ),
      constraint resource_cleanup_attempt_failure_category_valid check (
        failure_category is null or failure_category in (
          'VALIDATION', 'POLICY', 'AUTH', 'RATE_LIMIT', 'TRANSIENT',
          'UNCERTAIN', 'CLEANUP', 'INFRASTRUCTURE'
        )
      ),
      constraint resource_cleanup_attempt_failure_code_format check (
        failure_code is null or failure_code ~ '^[A-Z][A-Z0-9_]{2,79}$'
      ),
      constraint resource_cleanup_attempt_failure_detail_safe check (
        failure_detail is null or (
          btrim(failure_detail) <> '' and octet_length(failure_detail) <= 2000
        )
      ),
      constraint resource_cleanup_attempt_request_id_size check (
        provider_request_id is null or (
          btrim(provider_request_id) <> '' and octet_length(provider_request_id) <= 800
        )
      ),
      constraint resource_cleanup_attempt_shape check (
        (
          status = 'RUNNING' and finished_at is null
          and failure_category is null and failure_code is null and failure_detail is null
        ) or (
          status = 'SUCCEEDED' and finished_at is not null
          and failure_category is null and failure_code is null and failure_detail is null
        ) or (
          status in ('FAILED', 'OUTCOME_UNCERTAIN') and finished_at is not null
          and failure_category is not null and failure_code is not null
          and failure_detail is not null
        )
      )
    )
    """,
    "alter table atlas.data_atom_version disable trigger data_atom_version_guard_update",
    """
    update atlas.data_atom_version
    set status = 'VALIDATED',
        runtime_validation_state = 'PENDING',
        cleanup_validation_state = 'PENDING',
        runtime_validation_evidence_id = null,
        runtime_validated_at = null,
        published_at = null,
        published_by = null,
        revision = revision + 1
    where status = 'PUBLISHED'
      and cleanup_validation_state = 'PASSED'
    """,
    """
    update atlas.data_atom_version
    set runtime_validation_state = 'PENDING',
        runtime_validation_evidence_id = null,
        runtime_validated_at = null,
        revision = revision + 1
    where runtime_validation_state = 'PASSED'
      and runtime_validation_evidence_id is null
    """,
    """
    update atlas.data_atom_version
    set cleanup_validation_state = 'PENDING', revision = revision + 1
    where cleanup_validation_state = 'PASSED'
    """,
    "alter table atlas.data_atom_version enable trigger data_atom_version_guard_update",
    "alter table atlas.data_blueprint_version disable trigger data_blueprint_version_guard_update",
    """
    update atlas.data_blueprint_version
    set status = 'VALIDATED',
        runtime_validation_state = 'PENDING',
        cleanup_validation_state = 'PENDING',
        runtime_validation_evidence_id = null,
        runtime_validated_at = null,
        published_at = null,
        published_by = null,
        revision = revision + 1
    where status = 'PUBLISHED'
      and cleanup_validation_state = 'PASSED'
    """,
    """
    update atlas.data_blueprint_version
    set runtime_validation_state = 'PENDING',
        runtime_validation_evidence_id = null,
        runtime_validated_at = null,
        revision = revision + 1
    where runtime_validation_state = 'PASSED'
      and runtime_validation_evidence_id is null
    """,
    """
    update atlas.data_blueprint_version
    set cleanup_validation_state = 'PENDING', revision = revision + 1
    where cleanup_validation_state = 'PASSED'
    """,
    "alter table atlas.data_blueprint_version enable trigger data_blueprint_version_guard_update",
    "alter table atlas.data_atom_version validate constraint data_atom_version_runtime_evidence_shape",
    "alter table atlas.data_blueprint_version validate constraint data_blueprint_version_runtime_evidence_shape",
    """
    alter table atlas.data_atom_version
      add column cleanup_validation_evidence_id uuid,
      add column cleanup_validated_at timestamptz,
      add constraint data_atom_version_cleanup_evidence_fk foreign key (
        cleanup_validation_evidence_id
      ) references atlas.fixture_validation_evidence (id) on delete restrict,
      add constraint data_atom_version_cleanup_evidence_shape check (
        (cleanup_validation_state = 'PASSED') = (
          cleanup_validation_evidence_id is not null and cleanup_validated_at is not null
        )
      ) not valid
    """,
    """
    alter table atlas.data_blueprint_version
      add column cleanup_validation_evidence_id uuid,
      add column cleanup_validated_at timestamptz,
      add constraint data_blueprint_version_cleanup_evidence_fk foreign key (
        cleanup_validation_evidence_id
      ) references atlas.fixture_validation_evidence (id) on delete restrict,
      add constraint data_blueprint_version_cleanup_evidence_shape check (
        (cleanup_validation_state = 'PASSED') = (
          cleanup_validation_evidence_id is not null and cleanup_validated_at is not null
        )
      ) not valid
    """,
    "alter table atlas.data_atom_version validate constraint data_atom_version_cleanup_evidence_shape",
    "alter table atlas.data_blueprint_version validate constraint data_blueprint_version_cleanup_evidence_shape",
    """
    create index fixture_run_cancel_pending_idx
      on atlas.fixture_run (tenant_id, cancel_requested_at, id)
      where cancel_requested_at is not null
        and status in ('REQUESTED', 'RUNNING', 'READY')
    """,
    """
    create index fixture_run_cleanup_work_idx
      on atlas.fixture_run (tenant_id, cleanup_state, updated_at, id)
      where status in ('CLEANING', 'FAILED', 'CANCELED', 'CLEANUP_FAILED')
        and cleanup_state in ('PENDING', 'RUNNING', 'LEAKED')
    """,
    """
    create index data_node_run_reconcile_due_idx
      on atlas.data_node_run (tenant_id, next_reconcile_at, fixture_run_id, id)
      where status = 'OUTCOME_UNCERTAIN'
        and reconcile_state in ('PENDING', 'INCONCLUSIVE')
    """,
    """
    create index data_node_reconcile_attempt_run_idx
      on atlas.data_node_reconcile_attempt (fixture_run_id, started_at, id)
    """,
    """
    create index data_node_reconcile_attempt_node_idx
      on atlas.data_node_reconcile_attempt (data_node_run_id, attempt_number)
    """,
    """
    create index resource_record_cleanup_due_idx
      on atlas.resource_record (tenant_id, next_cleanup_at, fixture_run_id, id)
      where status in ('CLEANUP_PENDING', 'BLOCKED_BY_CHILD', 'ORPHAN_SUSPECTED')
    """,
    """
    create index resource_record_orphan_due_idx
      on atlas.resource_record (tenant_id, expires_at, fixture_run_id, id)
      where ownership = 'CREATED' and status = 'ACTIVE'
    """,
    """
    create index resource_cleanup_attempt_run_idx
      on atlas.resource_cleanup_attempt (fixture_run_id, started_at, id)
    """,
    """
    create index resource_cleanup_attempt_resource_idx
      on atlas.resource_cleanup_attempt (resource_record_id, cleanup_generation)
    """,
    """
    create index data_atom_version_cleanup_evidence_idx
      on atlas.data_atom_version (cleanup_validation_evidence_id)
      where cleanup_validation_evidence_id is not null
    """,
    """
    create index data_blueprint_version_cleanup_evidence_idx
      on atlas.data_blueprint_version (cleanup_validation_evidence_id)
      where cleanup_validation_evidence_id is not null
    """,
    """
    create or replace function atlas.guard_fixture_run_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.blueprint_version_id, new.run_kind, new.execution_id,
        new.plan_digest, new.input_digest, new.compiled_plan, new.run_inputs,
        new.cleanup_policy, new.temporal_workflow_id, new.requested_by,
        new.execution_deadline, new.requested_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.blueprint_version_id, old.run_kind, old.execution_id,
        old.plan_digest, old.input_digest, old.compiled_plan, old.run_inputs,
        old.cleanup_policy, old.temporal_workflow_id, old.requested_by,
        old.execution_deadline, old.requested_at
      ) then
        raise exception 'fixture run scope and frozen inputs are immutable';
      end if;
      if old.cancel_requested_at is not null and row(
        new.cancel_requested_at, new.cancel_requested_by
      ) is distinct from row(old.cancel_requested_at, old.cancel_requested_by) then
        raise exception 'fixture cancellation request is immutable';
      end if;
      if old.terminal_intent is not null
        and new.terminal_intent is distinct from old.terminal_intent
      then
        raise exception 'fixture terminal intent is immutable';
      end if;
      if not (
        (old.status = 'REQUESTED' and new.status in (
          'REQUESTED', 'RUNNING', 'CLEANING', 'FAILED', 'CANCELED'
        ))
        or (old.status = 'RUNNING' and new.status in (
          'RUNNING', 'READY', 'CLEANING', 'FAILED', 'CANCELED'
        ))
        or (old.status = 'READY' and new.status in ('READY', 'CLEANING'))
        or (old.status = 'CLEANING' and new.status in (
          'CLEANING', 'RELEASED', 'FAILED', 'CANCELED', 'CLEANUP_FAILED'
        ))
        or (old.status = 'CLEANUP_FAILED' and new.status in (
          'CLEANUP_FAILED', 'CLEANING', 'RELEASED'
        ))
        or (old.status = 'FAILED' and new.status in ('FAILED', 'CLEANING'))
        or (old.status = 'CANCELED' and new.status in ('CANCELED', 'CLEANING'))
      ) then
        raise exception 'invalid fixture run lifecycle transition';
      end if;
      if old.status = 'RELEASED' then
        raise exception 'released fixture run is immutable';
      end if;
      if not (
        (old.cleanup_state = 'NOT_REQUIRED' and new.cleanup_state = 'NOT_REQUIRED')
        or (old.cleanup_state = 'PENDING' and new.cleanup_state in (
          'PENDING', 'RUNNING', 'CLEANED', 'LEAKED'
        ))
        or (old.cleanup_state = 'RUNNING' and new.cleanup_state in (
          'PENDING', 'RUNNING', 'CLEANED', 'LEAKED'
        ))
        or (old.cleanup_state = 'LEAKED' and new.cleanup_state in (
          'LEAKED', 'PENDING', 'RUNNING', 'CLEANED'
        ))
        or (old.cleanup_state = 'CLEANED' and new.cleanup_state = 'CLEANED')
      ) then
        raise exception 'invalid fixture cleanup state transition';
      end if;
      if new.cleanup_generation < old.cleanup_generation
        or new.cleanup_generation > old.cleanup_generation + 1
      then
        raise exception 'fixture cleanup generation must increase monotonically';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'fixture run revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create or replace function atlas.guard_data_node_run_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.fixture_run_id, new.node_id, new.atom_id, new.atom_version_id,
        new.actor_slot, new.execution_level, new.logical_idempotency_key
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.fixture_run_id, old.node_id, old.atom_id, old.atom_version_id,
        old.actor_slot, old.execution_level, old.logical_idempotency_key
      ) then
        raise exception 'data node run identity is immutable';
      end if;
      if old.status in ('SUCCEEDED', 'FAILED') then
        raise exception 'terminal data node run is immutable';
      end if;
      if not (
        (old.status = 'PENDING' and new.status in ('PENDING', 'READY', 'RUNNING', 'FAILED'))
        or (old.status = 'READY' and new.status in ('READY', 'RUNNING', 'FAILED'))
        or (
          old.status = 'RUNNING'
          and new.status in ('RUNNING', 'VERIFYING', 'FAILED', 'OUTCOME_UNCERTAIN')
        )
        or (
          old.status = 'VERIFYING'
          and new.status in ('VERIFYING', 'SUCCEEDED', 'FAILED', 'OUTCOME_UNCERTAIN')
        )
        or (
          old.status = 'OUTCOME_UNCERTAIN'
          and new.status in ('OUTCOME_UNCERTAIN', 'READY', 'VERIFYING', 'FAILED')
        )
      ) then
        raise exception 'invalid data node run lifecycle transition';
      end if;
      if not (
        (old.reconcile_state = 'NOT_REQUIRED' and new.reconcile_state in (
          'NOT_REQUIRED', 'PENDING'
        ))
        or (old.reconcile_state in ('PENDING', 'INCONCLUSIVE', 'ABSENT')
          and new.reconcile_state in (
            'PENDING', 'RUNNING', 'INCONCLUSIVE', 'ABSENT', 'EXHAUSTED'
          ))
        or (old.reconcile_state = 'RUNNING' and new.reconcile_state in (
          'RUNNING', 'FOUND', 'ABSENT', 'INCONCLUSIVE', 'EXHAUSTED'
        ))
        or (old.reconcile_state in ('FOUND', 'EXHAUSTED')
          and new.reconcile_state = old.reconcile_state)
      ) then
        raise exception 'invalid data node reconcile transition';
      end if;
      if new.attempt_count < old.attempt_count
        or new.attempt_count > old.attempt_count + 1
      then
        raise exception 'data node attempt count must increase monotonically';
      end if;
      if new.reconcile_attempt_count < old.reconcile_attempt_count
        or new.reconcile_attempt_count > old.reconcile_attempt_count + 1
      then
        raise exception 'reconcile attempt count must increase monotonically';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'data node run revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create or replace function atlas.guard_resource_record_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.fixture_run_id, new.data_node_run_id, new.data_node_attempt_id,
        new.connector_installation_id, new.resource_handle,
        new.resource_type, new.ownership, new.opaque_ref, new.opaque_ref_hash,
        new.expires_at, new.cleanup_operation_key,
        new.cleanup_operation_version, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.fixture_run_id, old.data_node_run_id, old.data_node_attempt_id,
        old.connector_installation_id, old.resource_handle,
        old.resource_type, old.ownership, old.opaque_ref, old.opaque_ref_hash,
        old.expires_at, old.cleanup_operation_key,
        old.cleanup_operation_version, old.created_at
      ) then
        raise exception 'resource record identity and locator are immutable';
      end if;
      if old.status = 'CLEANED' then
        raise exception 'cleaned resource record is immutable';
      end if;
      if not (
        (old.status = 'ACTIVE' and new.status in (
          'ACTIVE', 'CLEANUP_PENDING', 'ORPHAN_SUSPECTED'
        ))
        or (old.status = 'CLEANUP_PENDING' and new.status in (
          'CLEANUP_PENDING', 'CLEANING', 'BLOCKED_BY_CHILD', 'LEAKED'
        ))
        or (old.status = 'CLEANING' and new.status in (
          'CLEANING', 'CLEANUP_PENDING', 'CLEANED', 'LEAKED'
        ))
        or (old.status = 'BLOCKED_BY_CHILD' and new.status in (
          'BLOCKED_BY_CHILD', 'CLEANUP_PENDING', 'LEAKED'
        ))
        or (old.status = 'LEAKED' and new.status in (
          'LEAKED', 'CLEANUP_PENDING', 'CLEANING'
        ))
        or (old.status = 'ORPHAN_SUSPECTED' and new.status in (
          'ORPHAN_SUSPECTED', 'CLEANUP_PENDING', 'CLEANING', 'LEAKED'
        ))
      ) then
        raise exception 'invalid resource record lifecycle transition';
      end if;
      if new.cleanup_generation < old.cleanup_generation
        or new.cleanup_generation > old.cleanup_generation + 1
      then
        raise exception 'resource cleanup generation must increase monotonically';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'resource record revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_data_node_reconcile_attempt_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.fixture_run_id, new.data_node_run_id, new.attempt_number,
        new.started_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.fixture_run_id, old.data_node_run_id, old.attempt_number,
        old.started_at
      ) then
        raise exception 'reconcile attempt identity is immutable';
      end if;
      if old.status <> 'RUNNING' or new.status = 'RUNNING' then
        raise exception 'reconcile attempt must enter one terminal state';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_resource_cleanup_attempt_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.fixture_run_id, new.resource_record_id, new.cleanup_generation,
        new.worker_identity, new.started_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.fixture_run_id, old.resource_record_id, old.cleanup_generation,
        old.worker_identity, old.started_at
      ) then
        raise exception 'cleanup attempt identity is immutable';
      end if;
      if old.status <> 'RUNNING' or new.status = 'RUNNING' then
        raise exception 'cleanup attempt must enter one terminal state';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_fixture_cleanup_evidence_link()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      evidence_id uuid;
      evidence_kind text;
      evidence_subject text;
      evidence_version_id uuid;
      evidence_digest text;
      evidence_passed boolean;
    begin
      evidence_id := new.cleanup_validation_evidence_id;
      if evidence_id is null then
        if new.cleanup_validation_state = 'PASSED'
          and old.cleanup_validation_state <> 'PASSED'
        then
          raise exception 'cleanup PASSED requires fixture validation evidence';
        end if;
        return new;
      end if;

      select kind, subject,
             coalesce(atom_version_id, blueprint_version_id),
             subject_digest, passed
        into evidence_kind, evidence_subject, evidence_version_id,
             evidence_digest, evidence_passed
      from atlas.fixture_validation_evidence
      where id = evidence_id
        and tenant_id = new.tenant_id
        and project_id = new.project_id;

      if evidence_kind is distinct from 'CLEANUP'
        or evidence_passed is distinct from true
        or evidence_version_id is distinct from new.id
        or (
          tg_table_name = 'data_atom_version'
          and evidence_subject is distinct from 'ATOM_VERSION'
        )
        or (
          tg_table_name = 'data_blueprint_version'
          and evidence_subject is distinct from 'BLUEPRINT_VERSION'
        )
        or evidence_digest is distinct from (case
          when tg_table_name = 'data_atom_version' then new.content_digest
          else to_jsonb(new) ->> 'plan_digest'
        end)
        or new.cleanup_validation_state <> 'PASSED'
        or new.cleanup_validated_at is null
      then
        raise exception 'cleanup validation evidence does not match fixture version';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger data_node_reconcile_attempt_guard_update
      before update on atlas.data_node_reconcile_attempt
      for each row execute function atlas.guard_data_node_reconcile_attempt_update()
    """,
    """
    create trigger data_node_reconcile_attempt_set_updated_at
      before update on atlas.data_node_reconcile_attempt
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger resource_cleanup_attempt_guard_update
      before update on atlas.resource_cleanup_attempt
      for each row execute function atlas.guard_resource_cleanup_attempt_update()
    """,
    """
    create trigger resource_cleanup_attempt_set_updated_at
      before update on atlas.resource_cleanup_attempt
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger data_atom_version_guard_cleanup_evidence
      before update on atlas.data_atom_version
      for each row execute function atlas.guard_fixture_cleanup_evidence_link()
    """,
    """
    create trigger data_blueprint_version_guard_cleanup_evidence
      before update on atlas.data_blueprint_version
      for each row execute function atlas.guard_fixture_cleanup_evidence_link()
    """,
    "alter table atlas.data_node_reconcile_attempt enable row level security",
    "alter table atlas.data_node_reconcile_attempt force row level security",
    "alter table atlas.resource_cleanup_attempt enable row level security",
    "alter table atlas.resource_cleanup_attempt force row level security",
    """
    create policy data_node_reconcile_attempt_tenant_isolation
      on atlas.data_node_reconcile_attempt
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy resource_cleanup_attempt_tenant_isolation
      on atlas.resource_cleanup_attempt
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    grant select, insert, update on
      atlas.data_node_reconcile_attempt, atlas.resource_cleanup_attempt
      to atlas_app
    """,
    """
    revoke delete on
      atlas.data_node_reconcile_attempt, atlas.resource_cleanup_attempt
      from atlas_app
    """,
)


def upgrade() -> None:
    """Add P3-03 durable recovery, cleanup retry, and evidence facts."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Return to the P3-02 one-pass cleanup model."""

    op.execute("drop index if exists atlas.fixture_run_cleanup_work_idx")
    op.execute("drop index if exists atlas.resource_record_orphan_due_idx")
    op.execute(
        "drop trigger if exists data_blueprint_version_guard_cleanup_evidence "
        "on atlas.data_blueprint_version"
    )
    op.execute(
        "drop trigger if exists data_atom_version_guard_cleanup_evidence "
        "on atlas.data_atom_version"
    )
    op.execute(
        "alter table atlas.data_blueprint_version "
        "drop column if exists cleanup_validation_evidence_id, "
        "drop column if exists cleanup_validated_at"
    )
    op.execute(
        "alter table atlas.data_atom_version "
        "drop column if exists cleanup_validation_evidence_id, "
        "drop column if exists cleanup_validated_at"
    )
    op.execute("drop table if exists atlas.resource_cleanup_attempt")
    op.execute("drop table if exists atlas.data_node_reconcile_attempt")
    op.execute("drop function if exists atlas.guard_fixture_cleanup_evidence_link()")
    op.execute("drop function if exists atlas.guard_resource_cleanup_attempt_update()")
    op.execute("drop function if exists atlas.guard_data_node_reconcile_attempt_update()")
    op.execute(
        "alter table atlas.resource_record "
        "drop constraint if exists resource_record_cleanup_schedule_shape, "
        "drop column if exists next_cleanup_at"
    )
    op.execute(
        "alter table atlas.data_node_run "
        "drop constraint if exists data_node_run_reconcile_schedule_shape, "
        "drop constraint if exists data_node_run_reconcile_state_valid, "
        "drop constraint if exists data_node_run_reconcile_attempt_count_valid, "
        "drop column if exists reconcile_state, "
        "drop column if exists reconcile_attempt_count, "
        "drop column if exists next_reconcile_at"
    )
    op.execute(
        "alter table atlas.fixture_run "
        "drop constraint if exists fixture_run_terminal_intent_shape, "
        "drop constraint if exists fixture_run_terminal_intent_valid, "
        "drop constraint if exists fixture_run_cancel_request_shape, "
        "drop constraint if exists fixture_run_cleanup_generation_valid, "
        "drop column if exists terminal_intent, "
        "drop column if exists cancel_requested_at, "
        "drop column if exists cancel_requested_by, "
        "drop column if exists cleanup_generation"
    )
    op.execute(_P3_02_FIXTURE_RUN_GUARD)
    op.execute(_P3_02_NODE_RUN_GUARD)
    op.execute(_P3_02_RESOURCE_GUARD)


_P3_02_FIXTURE_RUN_GUARD = """
create or replace function atlas.guard_fixture_run_update()
returns trigger language plpgsql set search_path = pg_catalog, atlas as $$
begin
  if row(
    new.id, new.tenant_id, new.project_id, new.environment_id,
    new.blueprint_version_id, new.run_kind, new.execution_id,
    new.plan_digest, new.input_digest, new.compiled_plan, new.run_inputs,
    new.cleanup_policy, new.temporal_workflow_id, new.requested_by,
    new.execution_deadline, new.requested_at
  ) is distinct from row(
    old.id, old.tenant_id, old.project_id, old.environment_id,
    old.blueprint_version_id, old.run_kind, old.execution_id,
    old.plan_digest, old.input_digest, old.compiled_plan, old.run_inputs,
    old.cleanup_policy, old.temporal_workflow_id, old.requested_by,
    old.execution_deadline, old.requested_at
  ) then
    raise exception 'fixture run scope and frozen inputs are immutable';
  end if;
  if not (
    (old.status = 'REQUESTED' and new.status in ('REQUESTED', 'RUNNING', 'FAILED', 'CANCELED'))
    or (old.status = 'RUNNING' and new.status in ('RUNNING', 'READY', 'FAILED', 'CANCELED'))
    or (old.status = 'READY' and new.status in ('READY', 'CLEANING'))
    or (old.status = 'CLEANING' and new.status in ('CLEANING', 'RELEASED', 'CLEANUP_FAILED'))
  ) then
    raise exception 'invalid fixture run lifecycle transition';
  end if;
  if old.status in ('FAILED', 'CANCELED', 'RELEASED', 'CLEANUP_FAILED') then
    raise exception 'terminal fixture run is immutable';
  end if;
  if new.revision <> old.revision + 1 then
    raise exception 'fixture run revision must increase by one';
  end if;
  return new;
end;
$$
"""


_P3_02_NODE_RUN_GUARD = """
create or replace function atlas.guard_data_node_run_update()
returns trigger language plpgsql set search_path = pg_catalog, atlas as $$
begin
  if row(
    new.id, new.tenant_id, new.project_id, new.environment_id,
    new.fixture_run_id, new.node_id, new.atom_id, new.atom_version_id,
    new.actor_slot, new.execution_level, new.logical_idempotency_key
  ) is distinct from row(
    old.id, old.tenant_id, old.project_id, old.environment_id,
    old.fixture_run_id, old.node_id, old.atom_id, old.atom_version_id,
    old.actor_slot, old.execution_level, old.logical_idempotency_key
  ) then
    raise exception 'data node run identity is immutable';
  end if;
  if old.status in ('SUCCEEDED', 'FAILED', 'OUTCOME_UNCERTAIN') then
    raise exception 'terminal data node run is immutable';
  end if;
  if not (
    (old.status = 'PENDING' and new.status in ('PENDING', 'READY', 'RUNNING', 'FAILED'))
    or (old.status = 'READY' and new.status in ('READY', 'RUNNING', 'FAILED'))
    or (old.status = 'RUNNING' and new.status in ('RUNNING', 'VERIFYING', 'FAILED', 'OUTCOME_UNCERTAIN'))
    or (old.status = 'VERIFYING' and new.status in ('VERIFYING', 'SUCCEEDED', 'FAILED', 'OUTCOME_UNCERTAIN'))
  ) then
    raise exception 'invalid data node run lifecycle transition';
  end if;
  if new.attempt_count < old.attempt_count or new.attempt_count > old.attempt_count + 1 then
    raise exception 'data node attempt count must increase monotonically';
  end if;
  if new.revision <> old.revision + 1 then
    raise exception 'data node run revision must increase by one';
  end if;
  return new;
end;
$$
"""


_P3_02_RESOURCE_GUARD = """
create or replace function atlas.guard_resource_record_update()
returns trigger language plpgsql set search_path = pg_catalog, atlas as $$
begin
  if row(
    new.id, new.tenant_id, new.project_id, new.environment_id,
    new.fixture_run_id, new.data_node_run_id, new.data_node_attempt_id,
    new.connector_installation_id, new.resource_handle,
    new.resource_type, new.ownership, new.opaque_ref, new.opaque_ref_hash,
    new.expires_at, new.cleanup_operation_key,
    new.cleanup_operation_version, new.created_at
  ) is distinct from row(
    old.id, old.tenant_id, old.project_id, old.environment_id,
    old.fixture_run_id, old.data_node_run_id, old.data_node_attempt_id,
    old.connector_installation_id, old.resource_handle,
    old.resource_type, old.ownership, old.opaque_ref, old.opaque_ref_hash,
    old.expires_at, old.cleanup_operation_key,
    old.cleanup_operation_version, old.created_at
  ) then
    raise exception 'resource record identity and locator are immutable';
  end if;
  if old.status = 'CLEANED' then raise exception 'cleaned resource record is immutable'; end if;
  if not (
    (old.status = 'ACTIVE' and new.status in ('ACTIVE', 'CLEANUP_PENDING'))
    or (old.status = 'CLEANUP_PENDING' and new.status in ('CLEANUP_PENDING', 'CLEANING', 'BLOCKED_BY_CHILD'))
    or (old.status = 'CLEANING' and new.status in ('CLEANING', 'CLEANED', 'LEAKED'))
    or (old.status = 'BLOCKED_BY_CHILD' and new.status in ('BLOCKED_BY_CHILD', 'CLEANUP_PENDING'))
    or (old.status in ('LEAKED', 'ORPHAN_SUSPECTED') and new.status in ('LEAKED', 'ORPHAN_SUSPECTED', 'CLEANUP_PENDING', 'CLEANING'))
  ) then raise exception 'invalid resource record lifecycle transition'; end if;
  if new.cleanup_generation < old.cleanup_generation or new.cleanup_generation > old.cleanup_generation + 1 then
    raise exception 'resource cleanup generation must increase monotonically';
  end if;
  if new.revision <> old.revision + 1 then
    raise exception 'resource record revision must increase by one';
  end if;
  return new;
end;
$$
"""
