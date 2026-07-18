"""Add immutable, secret-free execution tickets for Task UnitAttempts.

Revision ID: 20260717_0027
Revises: 20260716_0026
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_0027"
down_revision: str | None = "20260716_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    do $$
    begin
      if not exists (
        select 1
        from pg_catalog.pg_roles role
        where role.rolname = current_user
          and (role.rolsuper or role.rolbypassrls)
      ) then
        raise exception 'execution ticket guard owner must bypass row-level security'
          using errcode = '42501';
      end if;
    end;
    $$
    """,
    """
    create table atlas.task_unit_execution_ticket (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      schema_version text not null default 'atlas.task-unit-execution-ticket/0.1',
      request_digest text not null,
      manifest_hash text not null,
      ordinal integer not null,
      unit_key text not null,
      case_version_id uuid not null,
      case_content_digest text not null,
      test_ir_digest text not null,
      plan_digest text not null,
      compiled_digest text not null,
      attempt_number integer not null,
      execution_profile_version_id uuid not null,
      execution_profile_digest text not null,
      identity_profile_version_id uuid not null,
      identity_profile_digest text not null,
      browser_profile_version_id uuid not null,
      browser_profile_digest text not null,
      data_profile_version_id uuid not null,
      data_profile_digest text not null,
      fixture_blueprint_version_id uuid not null,
      fixture_blueprint_digest text not null,
      environment_id uuid not null,
      environment_revision bigint not null,
      allowed_origins text[] not null,
      execution_deadline timestamptz not null,
      ticket_digest text not null,
      created_at timestamptz not null,
      constraint task_unit_execution_ticket_attempt_scope_fk foreign key (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.unit_attempt (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint task_unit_execution_ticket_unit_scope_fk foreign key (
        execution_unit_id, tenant_id, project_id, task_run_id,
        manifest_hash, unit_key, case_version_id
      ) references atlas.execution_unit (
        id, tenant_id, project_id, task_run_id,
        manifest_hash, unit_key, case_version_id
      ) on delete restrict,
      constraint task_unit_execution_ticket_case_scope_fk foreign key (
        case_version_id, tenant_id, project_id
      ) references atlas.case_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_unit_execution_ticket_execution_profile_scope_fk foreign key (
        execution_profile_version_id, case_version_id, tenant_id, project_id
      ) references atlas.execution_profile_version (
        id, case_version_id, tenant_id, project_id
      ) on delete restrict,
      constraint task_unit_execution_ticket_identity_profile_scope_fk foreign key (
        identity_profile_version_id, case_version_id, tenant_id, project_id
      ) references atlas.identity_profile_version (
        id, case_version_id, tenant_id, project_id
      ) on delete restrict,
      constraint task_unit_execution_ticket_browser_profile_scope_fk foreign key (
        browser_profile_version_id, tenant_id, project_id
      ) references atlas.browser_profile_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_unit_execution_ticket_data_profile_scope_fk foreign key (
        data_profile_version_id, tenant_id, project_id
      ) references atlas.data_profile_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_unit_execution_ticket_fixture_scope_fk foreign key (
        fixture_blueprint_version_id, tenant_id, project_id
      ) references atlas.data_blueprint_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_unit_execution_ticket_environment_scope_fk foreign key (
        environment_id, tenant_id, project_id
      ) references atlas.environment (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_unit_execution_ticket_attempt_unique unique (unit_attempt_id),
      constraint task_unit_execution_ticket_full_scope_unique unique (
        id, tenant_id, project_id, unit_attempt_id
      ),
      constraint task_unit_execution_ticket_schema_valid check (
        schema_version = 'atlas.task-unit-execution-ticket/0.1'
      ),
      constraint task_unit_execution_ticket_numbers_valid check (
        ordinal > 0 and attempt_number > 0 and environment_revision > 0
      ),
      constraint task_unit_execution_ticket_digests_valid check (
        request_digest ~ '^sha256:[0-9a-f]{64}$'
        and manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and unit_key ~ '^sha256:[0-9a-f]{64}$'
        and case_content_digest ~ '^sha256:[0-9a-f]{64}$'
        and test_ir_digest ~ '^sha256:[0-9a-f]{64}$'
        and plan_digest ~ '^sha256:[0-9a-f]{64}$'
        and compiled_digest ~ '^sha256:[0-9a-f]{64}$'
        and execution_profile_digest ~ '^sha256:[0-9a-f]{64}$'
        and identity_profile_digest ~ '^sha256:[0-9a-f]{64}$'
        and browser_profile_digest ~ '^sha256:[0-9a-f]{64}$'
        and data_profile_digest ~ '^sha256:[0-9a-f]{64}$'
        and fixture_blueprint_digest ~ '^sha256:[0-9a-f]{64}$'
        and ticket_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint task_unit_execution_ticket_origins_valid check (
        cardinality(allowed_origins) between 1 and 32
        and atlas.valid_http_origins(allowed_origins)
      ),
      constraint task_unit_execution_ticket_time_order check (
        created_at < execution_deadline
      )
    )
    """,
    """
    create index task_unit_execution_ticket_attempt_scope_fk_idx
      on atlas.task_unit_execution_ticket (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      )
    """,
    """
    create index task_unit_execution_ticket_run_idx
      on atlas.task_unit_execution_ticket (
        tenant_id, project_id, task_run_id, ordinal, id
      )
    """,
    """
    create function atlas.guard_task_unit_execution_ticket_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_request_digest text;
      stored_manifest_hash text;
      stored_ordinal integer;
      stored_unit_key text;
      stored_case_version_id uuid;
      stored_case_content_digest text;
      stored_test_ir_digest text;
      stored_plan_digest text;
      stored_compiled_digest text;
      stored_attempt_number integer;
      stored_execution_profile_id uuid;
      stored_execution_profile_digest text;
      stored_identity_profile_id uuid;
      stored_identity_profile_digest text;
      stored_browser_profile_id uuid;
      stored_browser_profile_digest text;
      stored_data_profile_id uuid;
      stored_data_profile_digest text;
      stored_fixture_id uuid;
      stored_fixture_digest text;
      stored_environment_id uuid;
      stored_environment_revision bigint;
      stored_allowed_origins text[];
      stored_deadline timestamptz;
      expected_digest text;
    begin
      select
        run.request_digest,
        run.manifest_hash,
        unit.ordinal,
        unit.unit_key,
        unit.case_version_id,
        case_version.content_digest,
        case_version.test_ir_digest,
        case_version.plan_digest,
        case_version.compiled_digest,
        attempt.attempt_number,
        unit.execution_profile_version_id,
        execution_profile.content_digest,
        unit.identity_profile_version_id,
        identity_profile.content_digest,
        unit.browser_profile_version_id,
        browser_profile.content_digest,
        unit.data_profile_version_id,
        data_profile.content_digest,
        unit.fixture_blueprint_version_id,
        fixture.content_digest,
        unit.environment_id,
        environment.revision,
        (
          select array_agg(origin.value order by origin.value)
          from unnest(environment.allowed_origins) origin(value)
        ),
        attempt.execution_deadline
      into
        stored_request_digest,
        stored_manifest_hash,
        stored_ordinal,
        stored_unit_key,
        stored_case_version_id,
        stored_case_content_digest,
        stored_test_ir_digest,
        stored_plan_digest,
        stored_compiled_digest,
        stored_attempt_number,
        stored_execution_profile_id,
        stored_execution_profile_digest,
        stored_identity_profile_id,
        stored_identity_profile_digest,
        stored_browser_profile_id,
        stored_browser_profile_digest,
        stored_data_profile_id,
        stored_data_profile_digest,
        stored_fixture_id,
        stored_fixture_digest,
        stored_environment_id,
        stored_environment_revision,
        stored_allowed_origins,
        stored_deadline
      from atlas.unit_attempt attempt
      join atlas.execution_unit unit
        on unit.id = attempt.execution_unit_id
       and unit.task_run_id = attempt.task_run_id
       and unit.tenant_id = attempt.tenant_id
       and unit.project_id = attempt.project_id
      join atlas.task_run run
        on run.id = attempt.task_run_id
       and run.tenant_id = attempt.tenant_id
       and run.project_id = attempt.project_id
      join atlas.case_version case_version
        on case_version.id = unit.case_version_id
       and case_version.tenant_id = unit.tenant_id
       and case_version.project_id = unit.project_id
      join atlas.execution_profile_version execution_profile
        on execution_profile.id = unit.execution_profile_version_id
       and execution_profile.case_version_id = unit.case_version_id
       and execution_profile.tenant_id = unit.tenant_id
       and execution_profile.project_id = unit.project_id
      join atlas.identity_profile_version identity_profile
        on identity_profile.id = unit.identity_profile_version_id
       and identity_profile.case_version_id = unit.case_version_id
       and identity_profile.tenant_id = unit.tenant_id
       and identity_profile.project_id = unit.project_id
      join atlas.browser_profile_version browser_profile
        on browser_profile.id = unit.browser_profile_version_id
       and browser_profile.tenant_id = unit.tenant_id
       and browser_profile.project_id = unit.project_id
      join atlas.data_profile_version data_profile
        on data_profile.id = unit.data_profile_version_id
       and data_profile.tenant_id = unit.tenant_id
       and data_profile.project_id = unit.project_id
      join atlas.data_blueprint_version fixture
        on fixture.id = unit.fixture_blueprint_version_id
       and fixture.tenant_id = unit.tenant_id
       and fixture.project_id = unit.project_id
      join atlas.environment environment
        on environment.id = unit.environment_id
       and environment.tenant_id = unit.tenant_id
       and environment.project_id = unit.project_id
      where attempt.id = new.unit_attempt_id
        and attempt.execution_unit_id = new.execution_unit_id
        and attempt.task_run_id = new.task_run_id
        and attempt.tenant_id = new.tenant_id
        and attempt.project_id = new.project_id
        and run.materialization_state = 'SEALED'
        and not run.legacy_unsealed
        and run.lifecycle in ('QUEUED', 'RUNNING')
        and unit.lifecycle = 'QUEUED'
        and attempt.lifecycle = 'QUEUED'
        and case_version.status = 'PUBLISHED'
        and execution_profile.status = 'PUBLISHED'
        and identity_profile.status = 'PUBLISHED'
        and browser_profile.status = 'PUBLISHED'
        and data_profile.status = 'PUBLISHED'
        and fixture.status = 'PUBLISHED'
        and environment.status = 'ACTIVE'
        and environment.kind in ('TEST', 'STAGING')
      for share of attempt, unit, run, case_version, execution_profile,
        identity_profile, browser_profile, data_profile, fixture, environment;

      if not found then
        raise exception 'execution ticket requires one currently admissible exact attempt';
      end if;
      if stored_allowed_origins is null or cardinality(stored_allowed_origins) = 0 then
        raise exception 'execution ticket requires a non-empty environment origin boundary';
      end if;
      if new.created_at is distinct from transaction_timestamp()
        or new.created_at >= stored_deadline
      then
        raise exception 'execution ticket creation time or deadline is invalid';
      end if;
      if row(
        new.request_digest, new.manifest_hash, new.ordinal, new.unit_key,
        new.case_version_id, new.case_content_digest, new.test_ir_digest,
        new.plan_digest, new.compiled_digest, new.attempt_number,
        new.execution_profile_version_id, new.execution_profile_digest,
        new.identity_profile_version_id, new.identity_profile_digest,
        new.browser_profile_version_id, new.browser_profile_digest,
        new.data_profile_version_id, new.data_profile_digest,
        new.fixture_blueprint_version_id, new.fixture_blueprint_digest,
        new.environment_id, new.environment_revision, new.allowed_origins,
        new.execution_deadline
      ) is distinct from row(
        stored_request_digest, stored_manifest_hash, stored_ordinal, stored_unit_key,
        stored_case_version_id, stored_case_content_digest, stored_test_ir_digest,
        stored_plan_digest, stored_compiled_digest, stored_attempt_number,
        stored_execution_profile_id, stored_execution_profile_digest,
        stored_identity_profile_id, stored_identity_profile_digest,
        stored_browser_profile_id, stored_browser_profile_digest,
        stored_data_profile_id, stored_data_profile_digest,
        stored_fixture_id, stored_fixture_digest,
        stored_environment_id, stored_environment_revision, stored_allowed_origins,
        stored_deadline
      ) then
        raise exception 'execution ticket does not match its exact stored dependencies';
      end if;

      expected_digest := atlas.task_sha256_json(
        jsonb_build_object(
          'schemaVersion', new.schema_version,
          'tenantId', new.tenant_id,
          'projectId', new.project_id,
          'taskRunId', new.task_run_id,
          'executionUnitId', new.execution_unit_id,
          'unitAttemptId', new.unit_attempt_id,
          'requestDigest', new.request_digest,
          'manifestHash', new.manifest_hash,
          'ordinal', new.ordinal,
          'unitKey', new.unit_key,
          'caseVersionId', new.case_version_id,
          'caseContentDigest', new.case_content_digest,
          'testIrDigest', new.test_ir_digest,
          'planDigest', new.plan_digest,
          'compiledDigest', new.compiled_digest,
          'attemptNumber', new.attempt_number,
          'executionProfileVersionId', new.execution_profile_version_id,
          'executionProfileDigest', new.execution_profile_digest,
          'identityProfileVersionId', new.identity_profile_version_id,
          'identityProfileDigest', new.identity_profile_digest,
          'browserProfileVersionId', new.browser_profile_version_id,
          'browserProfileDigest', new.browser_profile_digest,
          'dataProfileVersionId', new.data_profile_version_id,
          'dataProfileDigest', new.data_profile_digest,
          'fixtureBlueprintVersionId', new.fixture_blueprint_version_id,
          'fixtureBlueprintDigest', new.fixture_blueprint_digest,
          'environmentId', new.environment_id,
          'environmentRevision', new.environment_revision,
          'allowedOrigins', to_jsonb(new.allowed_origins),
          'executionDeadline', new.execution_deadline
        )
      );
      if new.ticket_digest is distinct from expected_digest then
        raise exception 'execution ticket digest is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_unit_execution_ticket_guard_insert
      before insert on atlas.task_unit_execution_ticket
      for each row execute function atlas.guard_task_unit_execution_ticket_insert()
    """,
    """
    revoke all on function atlas.guard_task_unit_execution_ticket_insert()
      from public, atlas_dispatcher
    """,
    "grant execute on function atlas.guard_task_unit_execution_ticket_insert() to atlas_app",
    """
    create trigger task_unit_execution_ticket_prevent_mutation
      before update or delete on atlas.task_unit_execution_ticket
      for each row execute function atlas.prevent_fact_mutation()
    """,
    "alter table atlas.task_unit_execution_ticket enable row level security",
    "alter table atlas.task_unit_execution_ticket force row level security",
    """
    create policy task_unit_execution_ticket_tenant_isolation
      on atlas.task_unit_execution_ticket for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.task_unit_execution_ticket from atlas_app, atlas_dispatcher",
    "grant select, insert on atlas.task_unit_execution_ticket to atlas_app",
)


DOWNGRADE_STATEMENTS = (
    "drop table if exists atlas.task_unit_execution_ticket",
    "drop function if exists atlas.guard_task_unit_execution_ticket_insert()",
)


def upgrade() -> None:
    """Create the immutable authority prepared before a Unit side effect."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove execution tickets without changing the existing Task hosts."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
