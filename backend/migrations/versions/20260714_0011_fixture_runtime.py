"""Create durable fixture runs, node attempts, resource ledger, and evidence.

Revision ID: 20260714_0011
Revises: 20260714_0010
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260714_0011"
down_revision: str | None = "20260714_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    alter table atlas.account_lease
      add constraint account_lease_fixture_scope_unique
      unique (id, tenant_id, project_id, environment_id)
    """,
    """
    alter table atlas.data_atom_version
      add constraint data_atom_version_fixture_scope_unique
      unique (id, tenant_id, project_id)
    """,
    """
    alter table atlas.data_blueprint_version
      add constraint data_blueprint_version_fixture_scope_unique
      unique (id, tenant_id, project_id)
    """,
    """
    create table atlas.fixture_run (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      blueprint_version_id uuid not null,
      run_kind text not null,
      execution_id text not null,
      plan_digest text not null,
      input_digest text not null,
      compiled_plan jsonb not null,
      run_inputs jsonb not null,
      cleanup_policy text not null,
      status text not null default 'REQUESTED',
      cleanup_state text not null default 'PENDING',
      temporal_workflow_id text not null,
      requested_by uuid,
      failure_category text,
      failure_code text,
      failure_detail text,
      execution_deadline timestamptz not null,
      requested_at timestamptz not null,
      started_at timestamptz,
      ready_at timestamptz,
      finished_at timestamptz,
      released_at timestamptz,
      revision bigint not null default 1,
      updated_at timestamptz not null default clock_timestamp(),
      constraint fixture_run_project_scope_fk foreign key (
        project_id, tenant_id
      ) references atlas.project (id, tenant_id) on delete restrict,
      constraint fixture_run_environment_scope_fk foreign key (
        environment_id, tenant_id, project_id
      ) references atlas.environment (id, tenant_id, project_id) on delete restrict,
      constraint fixture_run_blueprint_scope_fk foreign key (
        blueprint_version_id, tenant_id, project_id
      ) references atlas.data_blueprint_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint fixture_run_full_scope_unique unique (
        id, tenant_id, project_id, environment_id
      ),
      constraint fixture_run_logical_execution_unique unique (
        tenant_id, project_id, environment_id, blueprint_version_id, execution_id
      ),
      constraint fixture_run_temporal_workflow_unique unique (
        tenant_id, temporal_workflow_id
      ),
      constraint fixture_run_kind_valid check (
        run_kind in ('VALIDATION', 'EXECUTION')
      ),
      constraint fixture_run_execution_id_format check (
        execution_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$'
      ),
      constraint fixture_run_digest_format check (
        plan_digest ~ '^sha256:[a-f0-9]{64}$'
        and input_digest ~ '^sha256:[a-f0-9]{64}$'
      ),
      constraint fixture_run_plan_shape check (
        jsonb_typeof(compiled_plan) = 'object'
        and compiled_plan ->> 'schemaVersion' = 'atlas.compiled-fixture-plan/0.1'
        and compiled_plan ->> 'blueprintVersionId' = blueprint_version_id::text
        and compiled_plan ->> 'planDigest' = plan_digest
      ),
      constraint fixture_run_inputs_object check (
        jsonb_typeof(run_inputs) = 'object'
      ),
      constraint fixture_run_cleanup_policy_valid check (
        cleanup_policy in ('ALWAYS', 'RETAIN_ON_FAILURE')
      ),
      constraint fixture_run_status_valid check (
        status in (
          'REQUESTED', 'RUNNING', 'READY', 'FAILED', 'CANCELED',
          'CLEANING', 'RELEASED', 'CLEANUP_FAILED'
        )
      ),
      constraint fixture_run_cleanup_state_valid check (
        cleanup_state in ('NOT_REQUIRED', 'PENDING', 'RUNNING', 'CLEANED', 'LEAKED')
      ),
      constraint fixture_run_workflow_id_format check (
        temporal_workflow_id ~ '^atlas-fixture/[A-Za-z0-9/_-]+$'
        and char_length(temporal_workflow_id) between 20 and 300
      ),
      constraint fixture_run_failure_category_valid check (
        failure_category is null or failure_category in (
          'VALIDATION', 'POLICY', 'AUTH', 'RATE_LIMIT', 'TRANSIENT',
          'UNCERTAIN', 'CLEANUP', 'INFRASTRUCTURE'
        )
      ),
      constraint fixture_run_failure_code_format check (
        failure_code is null or failure_code ~ '^[A-Z][A-Z0-9_]{2,79}$'
      ),
      constraint fixture_run_failure_detail_safe check (
        failure_detail is null or (
          btrim(failure_detail) <> '' and octet_length(failure_detail) <= 2000
        )
      ),
      constraint fixture_run_failure_shape check (
        (failure_category is null and failure_code is null and failure_detail is null)
        or
        (failure_category is not null and failure_code is not null and failure_detail is not null)
      ),
      constraint fixture_run_time_order check (
        requested_at < execution_deadline
        and (started_at is null or started_at >= requested_at)
        and (ready_at is null or (started_at is not null and ready_at >= started_at))
        and (finished_at is null or finished_at >= requested_at)
        and (released_at is null or (ready_at is not null and released_at >= ready_at))
      ),
      constraint fixture_run_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.fixture_actor_binding (
      fixture_run_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      actor_slot text not null,
      account_lease_id uuid not null,
      fencing_token bigint not null,
      connector_installation_id uuid not null,
      bound_at timestamptz not null,
      primary key (fixture_run_id, actor_slot),
      constraint fixture_actor_binding_run_scope_fk foreign key (
        fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.fixture_run (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint fixture_actor_binding_lease_scope_fk foreign key (
        account_lease_id, tenant_id, project_id, environment_id
      ) references atlas.account_lease (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint fixture_actor_binding_connector_scope_fk foreign key (
        connector_installation_id, tenant_id, project_id, environment_id
      ) references atlas.connector_installation (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint fixture_actor_binding_full_scope_unique unique (
        fixture_run_id, actor_slot, tenant_id, project_id, environment_id
      ),
      constraint fixture_actor_binding_account_lease_unique unique (account_lease_id),
      constraint fixture_actor_binding_slot_format check (
        actor_slot ~ '^[A-Za-z_][A-Za-z0-9_.-]{1,79}$'
      ),
      constraint fixture_actor_binding_fence_positive check (fencing_token > 0)
    )
    """,
    """
    create table atlas.data_node_run (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      fixture_run_id uuid not null,
      node_id text not null,
      atom_id uuid not null,
      atom_version_id uuid not null,
      actor_slot text not null,
      execution_level integer not null,
      logical_idempotency_key text not null,
      status text not null default 'PENDING',
      attempt_count integer not null default 0,
      inputs jsonb,
      outputs jsonb,
      output_digest text,
      failure_category text,
      failure_code text,
      failure_detail text,
      started_at timestamptz,
      finished_at timestamptz,
      revision bigint not null default 1,
      updated_at timestamptz not null default clock_timestamp(),
      constraint data_node_run_fixture_scope_fk foreign key (
        fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.fixture_run (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint data_node_run_atom_scope_fk foreign key (
        atom_version_id, tenant_id, project_id, atom_id
      ) references atlas.data_atom_version (
        id, tenant_id, project_id, atom_id
      ) on delete restrict,
      constraint data_node_run_actor_scope_fk foreign key (
        fixture_run_id, actor_slot, tenant_id, project_id, environment_id
      ) references atlas.fixture_actor_binding (
        fixture_run_id, actor_slot, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint data_node_run_full_scope_unique unique (
        id, fixture_run_id, tenant_id, project_id, environment_id
      ),
      constraint data_node_run_node_unique unique (fixture_run_id, node_id),
      constraint data_node_run_idempotency_unique unique (
        tenant_id, logical_idempotency_key
      ),
      constraint data_node_run_node_id_format check (
        node_id ~ '^[A-Za-z_][A-Za-z0-9_.-]{0,127}$'
      ),
      constraint data_node_run_level_valid check (execution_level between 0 and 99),
      constraint data_node_run_idempotency_format check (
        logical_idempotency_key ~ '^fix_[a-f0-9]{64}$'
      ),
      constraint data_node_run_status_valid check (
        status in (
          'PENDING', 'READY', 'RUNNING', 'VERIFYING', 'SUCCEEDED',
          'FAILED', 'OUTCOME_UNCERTAIN'
        )
      ),
      constraint data_node_run_attempt_count_valid check (
        attempt_count between 0 and 32
      ),
      constraint data_node_run_payload_shape check (
        (inputs is null or jsonb_typeof(inputs) = 'object')
        and (outputs is null or jsonb_typeof(outputs) = 'object')
      ),
      constraint data_node_run_output_digest_format check (
        output_digest is null or output_digest ~ '^sha256:[a-f0-9]{64}$'
      ),
      constraint data_node_run_failure_category_valid check (
        failure_category is null or failure_category in (
          'VALIDATION', 'POLICY', 'AUTH', 'RATE_LIMIT', 'TRANSIENT',
          'UNCERTAIN', 'CLEANUP', 'INFRASTRUCTURE'
        )
      ),
      constraint data_node_run_failure_code_format check (
        failure_code is null or failure_code ~ '^[A-Z][A-Z0-9_]{2,79}$'
      ),
      constraint data_node_run_failure_detail_safe check (
        failure_detail is null or (
          btrim(failure_detail) <> '' and octet_length(failure_detail) <= 2000
        )
      ),
      constraint data_node_run_failure_shape check (
        (failure_category is null and failure_code is null and failure_detail is null)
        or
        (failure_category is not null and failure_code is not null and failure_detail is not null)
      ),
      constraint data_node_run_time_order check (
        (finished_at is null or started_at is not null)
        and (finished_at is null or finished_at >= started_at)
      ),
      constraint data_node_run_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.data_node_attempt (
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
      constraint data_node_attempt_node_scope_fk foreign key (
        data_node_run_id, fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.data_node_run (
        id, fixture_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint data_node_attempt_full_scope_unique unique (
        id, data_node_run_id, fixture_run_id, tenant_id, project_id, environment_id
      ),
      constraint data_node_attempt_number_unique unique (
        data_node_run_id, attempt_number
      ),
      constraint data_node_attempt_number_valid check (
        attempt_number between 1 and 32
      ),
      constraint data_node_attempt_status_valid check (
        status in ('RUNNING', 'SUCCEEDED', 'FAILED', 'OUTCOME_UNCERTAIN')
      ),
      constraint data_node_attempt_failure_category_valid check (
        failure_category is null or failure_category in (
          'VALIDATION', 'POLICY', 'AUTH', 'RATE_LIMIT', 'TRANSIENT',
          'UNCERTAIN', 'CLEANUP', 'INFRASTRUCTURE'
        )
      ),
      constraint data_node_attempt_failure_code_format check (
        failure_code is null or failure_code ~ '^[A-Z][A-Z0-9_]{2,79}$'
      ),
      constraint data_node_attempt_failure_detail_safe check (
        failure_detail is null or (
          btrim(failure_detail) <> '' and octet_length(failure_detail) <= 2000
        )
      ),
      constraint data_node_attempt_failure_shape check (
        (failure_category is null and failure_code is null and failure_detail is null)
        or
        (failure_category is not null and failure_code is not null and failure_detail is not null)
      ),
      constraint data_node_attempt_request_id_size check (
        provider_request_id is null or (
          btrim(provider_request_id) <> '' and octet_length(provider_request_id) <= 800
        )
      ),
      constraint data_node_attempt_time_order check (
        finished_at is null or finished_at >= started_at
      )
    )
    """,
    """
    create table atlas.resource_record (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      fixture_run_id uuid not null,
      data_node_run_id uuid not null,
      data_node_attempt_id uuid not null,
      connector_installation_id uuid not null,
      resource_handle text not null,
      resource_type text not null,
      ownership text not null,
      opaque_ref text not null,
      opaque_ref_hash text not null,
      status text not null default 'ACTIVE',
      expires_at timestamptz not null,
      cleanup_operation_key text,
      cleanup_operation_version text,
      cleanup_generation integer not null default 0,
      cleaned_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null default clock_timestamp(),
      constraint resource_record_run_scope_fk foreign key (
        fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.fixture_run (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint resource_record_node_scope_fk foreign key (
        data_node_run_id, fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.data_node_run (
        id, fixture_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint resource_record_attempt_scope_fk foreign key (
        data_node_attempt_id, data_node_run_id, fixture_run_id,
        tenant_id, project_id, environment_id
      ) references atlas.data_node_attempt (
        id, data_node_run_id, fixture_run_id,
        tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint resource_record_connector_scope_fk foreign key (
        connector_installation_id, tenant_id, project_id, environment_id
      ) references atlas.connector_installation (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint resource_record_full_scope_unique unique (
        id, fixture_run_id, tenant_id, project_id, environment_id
      ),
      constraint resource_record_handle_unique unique (tenant_id, resource_handle),
      constraint resource_record_provider_ref_unique unique (
        connector_installation_id, resource_type, opaque_ref_hash
      ),
      constraint resource_record_handle_format check (
        resource_handle ~ '^fr_[A-Za-z0-9_-]{16,128}$'
      ),
      constraint resource_record_type_format check (
        resource_type ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){1,7}$'
      ),
      constraint resource_record_ownership_valid check (
        ownership in ('CREATED', 'ADOPTED', 'LEASED', 'SHARED')
      ),
      constraint resource_record_ref_safe check (
        btrim(opaque_ref) <> '' and octet_length(opaque_ref) <= 8000
        and opaque_ref_hash ~ '^sha256:[a-f0-9]{64}$'
      ),
      constraint resource_record_status_valid check (
        status in (
          'ACTIVE', 'CLEANUP_PENDING', 'CLEANING', 'CLEANED', 'LEAKED',
          'BLOCKED_BY_CHILD', 'ORPHAN_SUSPECTED'
        )
      ),
      constraint resource_record_cleanup_operation_presence check (
        (cleanup_operation_key is null) = (cleanup_operation_version is null)
        and (ownership <> 'CREATED' or cleanup_operation_key is not null)
      ),
      constraint resource_record_cleanup_operation_format check (
        cleanup_operation_key is null or (
          cleanup_operation_key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){1,7}$'
          and cleanup_operation_version ~
            '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)'
            '(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
        )
      ),
      constraint resource_record_cleanup_generation_valid check (
        cleanup_generation between 0 and 1000
      ),
      constraint resource_record_terminal_shape check (
        (status = 'CLEANED' and cleaned_at is not null)
        or (status <> 'CLEANED' and cleaned_at is null)
      ),
      constraint resource_record_time_order check (
        expires_at > created_at and (cleaned_at is null or cleaned_at >= created_at)
      ),
      constraint resource_record_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.resource_dependency (
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      fixture_run_id uuid not null,
      child_resource_id uuid not null,
      parent_resource_id uuid not null,
      created_at timestamptz not null,
      primary key (child_resource_id, parent_resource_id),
      constraint resource_dependency_child_scope_fk foreign key (
        child_resource_id, fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.resource_record (
        id, fixture_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint resource_dependency_parent_scope_fk foreign key (
        parent_resource_id, fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.resource_record (
        id, fixture_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint resource_dependency_distinct check (
        child_resource_id <> parent_resource_id
      )
    )
    """,
    """
    create table atlas.fixture_manifest (
      fixture_run_id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      blueprint_version_id uuid not null,
      plan_digest text not null,
      manifest jsonb not null,
      manifest_digest text not null,
      created_at timestamptz not null,
      constraint fixture_manifest_run_scope_fk foreign key (
        fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.fixture_run (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint fixture_manifest_blueprint_scope_fk foreign key (
        blueprint_version_id, tenant_id, project_id
      ) references atlas.data_blueprint_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint fixture_manifest_digest_format check (
        plan_digest ~ '^sha256:[a-f0-9]{64}$'
        and manifest_digest ~ '^sha256:[a-f0-9]{64}$'
      ),
      constraint fixture_manifest_shape check (
        jsonb_typeof(manifest) = 'object'
        and manifest ->> 'schemaVersion' = 'atlas.fixture-manifest/0.1'
        and manifest ->> 'fixtureRunId' = fixture_run_id::text
        and manifest ->> 'blueprintVersionId' = blueprint_version_id::text
        and manifest ->> 'planDigest' = plan_digest
        and jsonb_typeof(manifest -> 'exports') = 'object'
      )
    )
    """,
    """
    create table atlas.fixture_validation_evidence (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      fixture_run_id uuid not null,
      kind text not null,
      subject text not null,
      atom_version_id uuid,
      blueprint_version_id uuid,
      subject_digest text not null,
      passed boolean not null,
      safe_summary text not null,
      observed_at timestamptz not null,
      constraint fixture_validation_evidence_run_scope_fk foreign key (
        fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.fixture_run (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint fixture_validation_evidence_atom_scope_fk foreign key (
        atom_version_id, tenant_id, project_id
      ) references atlas.data_atom_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint fixture_validation_evidence_blueprint_scope_fk foreign key (
        blueprint_version_id, tenant_id, project_id
      ) references atlas.data_blueprint_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint fixture_validation_evidence_kind_valid check (
        kind in ('RUNTIME', 'CLEANUP')
      ),
      constraint fixture_validation_evidence_subject_valid check (
        subject in ('ATOM_VERSION', 'BLUEPRINT_VERSION')
      ),
      constraint fixture_validation_evidence_subject_shape check (
        (
          subject = 'ATOM_VERSION'
          and atom_version_id is not null
          and blueprint_version_id is null
        ) or (
          subject = 'BLUEPRINT_VERSION'
          and atom_version_id is null
          and blueprint_version_id is not null
        )
      ),
      constraint fixture_validation_evidence_digest_format check (
        subject_digest ~ '^sha256:[a-f0-9]{64}$'
      ),
      constraint fixture_validation_evidence_summary_safe check (
        btrim(safe_summary) <> '' and octet_length(safe_summary) <= 2000
      )
    )
    """,
    """
    alter table atlas.data_atom_version
      add column runtime_validation_evidence_id uuid,
      add column runtime_validated_at timestamptz,
      add constraint data_atom_version_runtime_evidence_fk foreign key (
        runtime_validation_evidence_id
      ) references atlas.fixture_validation_evidence (id) on delete restrict,
      add constraint data_atom_version_runtime_evidence_shape check (
        (runtime_validation_state = 'PASSED')
          = (runtime_validation_evidence_id is not null and runtime_validated_at is not null)
      ) not valid
    """,
    """
    alter table atlas.data_blueprint_version
      add column runtime_validation_evidence_id uuid,
      add column runtime_validated_at timestamptz,
      add constraint data_blueprint_version_runtime_evidence_fk foreign key (
        runtime_validation_evidence_id
      ) references atlas.fixture_validation_evidence (id) on delete restrict,
      add constraint data_blueprint_version_runtime_evidence_shape check (
        (runtime_validation_state = 'PASSED')
          = (runtime_validation_evidence_id is not null and runtime_validated_at is not null)
      ) not valid
    """,
    """
    create index fixture_run_project_status_idx
      on atlas.fixture_run (
        project_id, tenant_id, status, requested_at desc, id desc
      )
    """,
    """
    create index fixture_run_environment_status_idx
      on atlas.fixture_run (
        environment_id, tenant_id, status, execution_deadline, id
      )
    """,
    """
    create index fixture_run_blueprint_history_idx
      on atlas.fixture_run (blueprint_version_id, requested_at desc, id desc)
    """,
    """
    create index fixture_actor_binding_connector_idx
      on atlas.fixture_actor_binding (connector_installation_id, fixture_run_id)
    """,
    """
    create index data_node_run_execution_idx
      on atlas.data_node_run (
        fixture_run_id, execution_level, status, node_id
      )
    """,
    """
    create index data_node_run_atom_history_idx
      on atlas.data_node_run (atom_version_id, updated_at desc, id desc)
    """,
    """
    create index data_node_run_actor_idx
      on atlas.data_node_run (fixture_run_id, actor_slot, node_id)
    """,
    """
    create index data_node_attempt_run_idx
      on atlas.data_node_attempt (fixture_run_id, started_at, id)
    """,
    """
    create index resource_record_run_status_idx
      on atlas.resource_record (fixture_run_id, status, created_at, id)
    """,
    """
    create index resource_record_node_idx
      on atlas.resource_record (data_node_run_id, id)
    """,
    """
    create index resource_record_attempt_idx
      on atlas.resource_record (data_node_attempt_id, id)
    """,
    """
    create index resource_record_cleanup_pending_idx
      on atlas.resource_record (tenant_id, expires_at, fixture_run_id, id)
      where status in ('ACTIVE', 'CLEANUP_PENDING', 'LEAKED', 'ORPHAN_SUSPECTED')
    """,
    """
    create index resource_dependency_parent_idx
      on atlas.resource_dependency (parent_resource_id, child_resource_id)
    """,
    """
    create index fixture_manifest_blueprint_idx
      on atlas.fixture_manifest (blueprint_version_id, fixture_run_id)
    """,
    """
    create unique index fixture_validation_evidence_atom_unique
      on atlas.fixture_validation_evidence (
        fixture_run_id, kind, atom_version_id
      ) where subject = 'ATOM_VERSION'
    """,
    """
    create unique index fixture_validation_evidence_blueprint_unique
      on atlas.fixture_validation_evidence (
        fixture_run_id, kind, blueprint_version_id
      ) where subject = 'BLUEPRINT_VERSION'
    """,
    """
    create index fixture_validation_evidence_atom_history_idx
      on atlas.fixture_validation_evidence (
        atom_version_id, kind, passed, observed_at desc, id desc
      ) where atom_version_id is not null
    """,
    """
    create index fixture_validation_evidence_blueprint_history_idx
      on atlas.fixture_validation_evidence (
        blueprint_version_id, kind, passed, observed_at desc, id desc
      ) where blueprint_version_id is not null
    """,
    """
    create index fixture_validation_evidence_run_idx
      on atlas.fixture_validation_evidence (fixture_run_id, subject, kind, id)
    """,
    """
    create index data_atom_version_runtime_evidence_idx
      on atlas.data_atom_version (runtime_validation_evidence_id)
      where runtime_validation_evidence_id is not null
    """,
    """
    create index data_blueprint_version_runtime_evidence_idx
      on atlas.data_blueprint_version (runtime_validation_evidence_id)
      where runtime_validation_evidence_id is not null
    """,
    """
    create function atlas.guard_fixture_actor_binding_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      run_execution_id text;
      account_connector_id uuid;
      lease_status text;
      lease_fence bigint;
      lease_expiry timestamptz;
    begin
      select execution_id into run_execution_id
      from atlas.fixture_run
      where id = new.fixture_run_id;

      select l.status, l.fencing_token, l.expires_at, a.connector_installation_id
        into lease_status, lease_fence, lease_expiry, account_connector_id
      from atlas.account_lease l
      join atlas.test_account a on a.id = l.account_id
      where l.id = new.account_lease_id
        and l.tenant_id = new.tenant_id
        and l.project_id = new.project_id
        and l.environment_id = new.environment_id
        and l.execution_id = run_execution_id;

      if lease_status is distinct from 'ACTIVE'
        or lease_fence is distinct from new.fencing_token
        or lease_expiry <= clock_timestamp()
        or account_connector_id is distinct from new.connector_installation_id
      then
        raise exception 'fixture actor binding requires a matching active fenced lease';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_fixture_run_update()
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
    """,
    """
    create function atlas.guard_data_node_run_update()
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
      if old.status in ('SUCCEEDED', 'FAILED', 'OUTCOME_UNCERTAIN') then
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
      ) then
        raise exception 'invalid data node run lifecycle transition';
      end if;
      if new.attempt_count < old.attempt_count
        or new.attempt_count > old.attempt_count + 1
      then
        raise exception 'data node attempt count must increase monotonically';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'data node run revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_data_node_attempt_update()
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
        raise exception 'data node attempt identity is immutable';
      end if;
      if old.status <> 'RUNNING' then
        raise exception 'terminal data node attempt is immutable';
      end if;
      if new.status not in ('SUCCEEDED', 'FAILED', 'OUTCOME_UNCERTAIN') then
        raise exception 'data node attempt must enter a terminal state';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_resource_record_update()
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
        (old.status = 'ACTIVE' and new.status in ('ACTIVE', 'CLEANUP_PENDING'))
        or (
          old.status = 'CLEANUP_PENDING'
          and new.status in ('CLEANUP_PENDING', 'CLEANING', 'BLOCKED_BY_CHILD')
        )
        or (
          old.status = 'CLEANING'
          and new.status in ('CLEANING', 'CLEANED', 'LEAKED')
        )
        or (
          old.status = 'BLOCKED_BY_CHILD'
          and new.status in ('BLOCKED_BY_CHILD', 'CLEANUP_PENDING')
        )
        or (
          old.status in ('LEAKED', 'ORPHAN_SUSPECTED')
          and new.status in (
            'LEAKED', 'ORPHAN_SUSPECTED', 'CLEANUP_PENDING', 'CLEANING'
          )
        )
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
    create function atlas.guard_fixture_runtime_evidence_link()
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
      evidence_id := new.runtime_validation_evidence_id;
      if evidence_id is null then
        if new.runtime_validation_state = 'PASSED'
          and old.runtime_validation_state <> 'PASSED'
        then
          raise exception 'runtime PASSED requires fixture validation evidence';
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

      if evidence_kind is distinct from 'RUNTIME'
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
        or new.runtime_validation_state <> 'PASSED'
        or new.runtime_validated_at is null
      then
        raise exception 'runtime validation evidence does not match fixture version';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger fixture_actor_binding_guard_insert
      before insert on atlas.fixture_actor_binding
      for each row execute function atlas.guard_fixture_actor_binding_insert()
    """,
    """
    create trigger fixture_actor_binding_prevent_mutation
      before update or delete on atlas.fixture_actor_binding
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger fixture_run_guard_update
      before update on atlas.fixture_run
      for each row execute function atlas.guard_fixture_run_update()
    """,
    """
    create trigger fixture_run_set_updated_at
      before update on atlas.fixture_run
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger data_node_run_guard_update
      before update on atlas.data_node_run
      for each row execute function atlas.guard_data_node_run_update()
    """,
    """
    create trigger data_node_run_set_updated_at
      before update on atlas.data_node_run
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger data_node_attempt_guard_update
      before update on atlas.data_node_attempt
      for each row execute function atlas.guard_data_node_attempt_update()
    """,
    """
    create trigger data_node_attempt_set_updated_at
      before update on atlas.data_node_attempt
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger resource_record_guard_update
      before update on atlas.resource_record
      for each row execute function atlas.guard_resource_record_update()
    """,
    """
    create trigger resource_record_set_updated_at
      before update on atlas.resource_record
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger resource_dependency_prevent_mutation
      before update or delete on atlas.resource_dependency
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger fixture_manifest_prevent_mutation
      before update or delete on atlas.fixture_manifest
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger fixture_validation_evidence_prevent_mutation
      before update or delete on atlas.fixture_validation_evidence
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger data_atom_version_guard_runtime_evidence
      before update on atlas.data_atom_version
      for each row execute function atlas.guard_fixture_runtime_evidence_link()
    """,
    """
    create trigger data_blueprint_version_guard_runtime_evidence
      before update on atlas.data_blueprint_version
      for each row execute function atlas.guard_fixture_runtime_evidence_link()
    """,
    "alter table atlas.fixture_run enable row level security",
    "alter table atlas.fixture_run force row level security",
    "alter table atlas.fixture_actor_binding enable row level security",
    "alter table atlas.fixture_actor_binding force row level security",
    "alter table atlas.data_node_run enable row level security",
    "alter table atlas.data_node_run force row level security",
    "alter table atlas.data_node_attempt enable row level security",
    "alter table atlas.data_node_attempt force row level security",
    "alter table atlas.resource_record enable row level security",
    "alter table atlas.resource_record force row level security",
    "alter table atlas.resource_dependency enable row level security",
    "alter table atlas.resource_dependency force row level security",
    "alter table atlas.fixture_manifest enable row level security",
    "alter table atlas.fixture_manifest force row level security",
    "alter table atlas.fixture_validation_evidence enable row level security",
    "alter table atlas.fixture_validation_evidence force row level security",
    """
    create policy fixture_run_tenant_isolation on atlas.fixture_run
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy fixture_actor_binding_tenant_isolation on atlas.fixture_actor_binding
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy data_node_run_tenant_isolation on atlas.data_node_run
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy data_node_attempt_tenant_isolation on atlas.data_node_attempt
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy resource_record_tenant_isolation on atlas.resource_record
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy resource_dependency_tenant_isolation on atlas.resource_dependency
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy fixture_manifest_tenant_isolation on atlas.fixture_manifest
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy fixture_validation_evidence_tenant_isolation
      on atlas.fixture_validation_evidence
      for all using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    grant select, insert, update on
      atlas.fixture_run, atlas.data_node_run, atlas.data_node_attempt,
      atlas.resource_record
      to atlas_app
    """,
    """
    grant select, insert on
      atlas.fixture_actor_binding, atlas.resource_dependency,
      atlas.fixture_manifest, atlas.fixture_validation_evidence
      to atlas_app
    """,
    """
    revoke delete on
      atlas.fixture_run, atlas.fixture_actor_binding, atlas.data_node_run,
      atlas.data_node_attempt, atlas.resource_record, atlas.resource_dependency,
      atlas.fixture_manifest, atlas.fixture_validation_evidence
      from atlas_app
    """,
)


def upgrade() -> None:
    """Create the P3-02 fixture runtime truth model and tenant isolation."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove fixture runtime facts while preserving P3-01 asset storage."""

    op.execute(
        "drop trigger if exists data_blueprint_version_guard_runtime_evidence "
        "on atlas.data_blueprint_version"
    )
    op.execute(
        "drop trigger if exists data_atom_version_guard_runtime_evidence on atlas.data_atom_version"
    )
    op.execute(
        "alter table atlas.data_blueprint_version "
        "drop column if exists runtime_validation_evidence_id, "
        "drop column if exists runtime_validated_at"
    )
    op.execute(
        "alter table atlas.data_atom_version "
        "drop column if exists runtime_validation_evidence_id, "
        "drop column if exists runtime_validated_at"
    )
    op.execute("drop table if exists atlas.resource_dependency")
    op.execute("drop table if exists atlas.fixture_manifest")
    op.execute("drop table if exists atlas.fixture_validation_evidence")
    op.execute("drop table if exists atlas.resource_record")
    op.execute("drop table if exists atlas.data_node_attempt")
    op.execute("drop table if exists atlas.data_node_run")
    op.execute("drop table if exists atlas.fixture_actor_binding")
    op.execute("drop table if exists atlas.fixture_run")
    op.execute("drop function if exists atlas.guard_fixture_runtime_evidence_link()")
    op.execute("drop function if exists atlas.guard_resource_record_update()")
    op.execute("drop function if exists atlas.guard_data_node_attempt_update()")
    op.execute("drop function if exists atlas.guard_data_node_run_update()")
    op.execute("drop function if exists atlas.guard_fixture_run_update()")
    op.execute("drop function if exists atlas.guard_fixture_actor_binding_insert()")
    op.execute(
        "alter table atlas.data_blueprint_version "
        "drop constraint if exists data_blueprint_version_fixture_scope_unique"
    )
    op.execute(
        "alter table atlas.data_atom_version "
        "drop constraint if exists data_atom_version_fixture_scope_unique"
    )
    op.execute(
        "alter table atlas.account_lease "
        "drop constraint if exists account_lease_fixture_scope_unique"
    )
