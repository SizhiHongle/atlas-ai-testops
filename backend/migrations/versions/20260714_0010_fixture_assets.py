"""Create versioned fixture atoms and data blueprints.

Revision ID: 20260714_0010
Revises: 20260714_0009
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260714_0010"
down_revision: str | None = "20260714_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.data_atom_definition (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      atom_key text not null,
      business_domain text not null,
      name text not null,
      description text not null,
      status text not null default 'ACTIVE',
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint data_atom_definition_project_scope_fk foreign key (
        project_id, tenant_id
      ) references atlas.project (id, tenant_id) on delete restrict,
      constraint data_atom_definition_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint data_atom_definition_project_key_unique unique (
        tenant_id, project_id, atom_key
      ),
      constraint data_atom_definition_key_format check (
        atom_key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){1,7}$'
      ),
      constraint data_atom_definition_domain_format check (
        business_domain ~ '^[a-z][a-z0-9-]{1,79}$'
      ),
      constraint data_atom_definition_name_valid check (
        btrim(name) <> '' and octet_length(name) <= 640
      ),
      constraint data_atom_definition_description_valid check (
        btrim(description) <> '' and octet_length(description) <= 4000
      ),
      constraint data_atom_definition_status_valid check (
        status in ('ACTIVE', 'ARCHIVED')
      ),
      constraint data_atom_definition_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.data_atom_version (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      atom_id uuid not null,
      version text not null,
      status text not null default 'DRAFT',
      contract jsonb not null,
      content_digest text not null,
      static_validation_state text not null default 'PENDING',
      runtime_validation_state text not null default 'PENDING',
      cleanup_validation_state text not null default 'PENDING',
      validated_at timestamptz,
      published_at timestamptz,
      published_by uuid,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint data_atom_version_definition_scope_fk foreign key (
        atom_id, tenant_id, project_id
      ) references atlas.data_atom_definition (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint data_atom_version_full_scope_unique unique (
        id, tenant_id, project_id, atom_id
      ),
      constraint data_atom_version_number_unique unique (
        tenant_id, project_id, atom_id, version
      ),
      constraint data_atom_version_semver_format check (
        version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)'
          '(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint data_atom_version_status_valid check (
        status in ('DRAFT', 'VALIDATED', 'PUBLISHED', 'DEPRECATED')
      ),
      constraint data_atom_version_contract_object check (
        jsonb_typeof(contract) = 'object'
        and contract ->> 'schemaVersion' = 'atlas.atom/0.1'
      ),
      constraint data_atom_version_digest_format check (
        content_digest ~ '^sha256:[a-f0-9]{64}$'
      ),
      constraint data_atom_version_static_state_valid check (
        static_validation_state in ('PENDING', 'PASSED', 'FAILED', 'NOT_REQUIRED')
      ),
      constraint data_atom_version_runtime_state_valid check (
        runtime_validation_state in ('PENDING', 'PASSED', 'FAILED', 'NOT_REQUIRED')
      ),
      constraint data_atom_version_cleanup_state_valid check (
        cleanup_validation_state in ('PENDING', 'PASSED', 'FAILED', 'NOT_REQUIRED')
      ),
      constraint data_atom_version_lifecycle_evidence check (
        (
          status = 'DRAFT'
          and static_validation_state <> 'PASSED'
          and validated_at is null
          and published_at is null
          and published_by is null
        ) or (
          status = 'VALIDATED'
          and static_validation_state = 'PASSED'
          and validated_at is not null
          and published_at is null
          and published_by is null
        ) or (
          status in ('PUBLISHED', 'DEPRECATED')
          and static_validation_state = 'PASSED'
          and runtime_validation_state = 'PASSED'
          and cleanup_validation_state = 'PASSED'
          and validated_at is not null
          and published_at is not null
          and published_by is not null
        )
      ),
      constraint data_atom_version_publication_order check (
        published_at is null or published_at >= validated_at
      ),
      constraint data_atom_version_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.data_blueprint_definition (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      blueprint_key text not null,
      name text not null,
      description text not null,
      status text not null default 'ACTIVE',
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint data_blueprint_definition_project_scope_fk foreign key (
        project_id, tenant_id
      ) references atlas.project (id, tenant_id) on delete restrict,
      constraint data_blueprint_definition_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint data_blueprint_definition_project_key_unique unique (
        tenant_id, project_id, blueprint_key
      ),
      constraint data_blueprint_definition_key_format check (
        blueprint_key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){1,7}$'
      ),
      constraint data_blueprint_definition_name_valid check (
        btrim(name) <> '' and octet_length(name) <= 640
      ),
      constraint data_blueprint_definition_description_valid check (
        btrim(description) <> '' and octet_length(description) <= 4000
      ),
      constraint data_blueprint_definition_status_valid check (
        status in ('ACTIVE', 'ARCHIVED')
      ),
      constraint data_blueprint_definition_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.data_blueprint_version (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      blueprint_id uuid not null,
      version text not null,
      status text not null default 'DRAFT',
      contract jsonb not null,
      content_digest text not null,
      static_validation_state text not null default 'PENDING',
      runtime_validation_state text not null default 'PENDING',
      cleanup_validation_state text not null default 'PENDING',
      validated_at timestamptz,
      compiled_plan jsonb,
      plan_digest text,
      compile_issues jsonb not null default '[]'::jsonb,
      compiled_at timestamptz,
      published_at timestamptz,
      published_by uuid,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint data_blueprint_version_definition_scope_fk foreign key (
        blueprint_id, tenant_id, project_id
      ) references atlas.data_blueprint_definition (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint data_blueprint_version_full_scope_unique unique (
        id, tenant_id, project_id, blueprint_id
      ),
      constraint data_blueprint_version_number_unique unique (
        tenant_id, project_id, blueprint_id, version
      ),
      constraint data_blueprint_version_semver_format check (
        version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)'
          '(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint data_blueprint_version_status_valid check (
        status in ('DRAFT', 'VALIDATED', 'PUBLISHED', 'DEPRECATED')
      ),
      constraint data_blueprint_version_contract_object check (
        jsonb_typeof(contract) = 'object'
        and contract ->> 'schemaVersion' = 'atlas.fixture-blueprint/0.1'
      ),
      constraint data_blueprint_version_digest_format check (
        content_digest ~ '^sha256:[a-f0-9]{64}$'
      ),
      constraint data_blueprint_version_static_state_valid check (
        static_validation_state in ('PENDING', 'PASSED', 'FAILED', 'NOT_REQUIRED')
      ),
      constraint data_blueprint_version_runtime_state_valid check (
        runtime_validation_state in ('PENDING', 'PASSED', 'FAILED', 'NOT_REQUIRED')
      ),
      constraint data_blueprint_version_cleanup_state_valid check (
        cleanup_validation_state in ('PENDING', 'PASSED', 'FAILED', 'NOT_REQUIRED')
      ),
      constraint data_blueprint_version_compile_issues_array check (
        jsonb_typeof(compile_issues) = 'array'
      ),
      constraint data_blueprint_version_plan_digest_format check (
        plan_digest is null or plan_digest ~ '^sha256:[a-f0-9]{64}$'
      ),
      constraint data_blueprint_version_plan_shape check (
        (
          compiled_plan is null and plan_digest is null
        ) or (
          jsonb_typeof(compiled_plan) = 'object'
          and plan_digest is not null
          and compiled_plan ->> 'schemaVersion' = 'atlas.compiled-fixture-plan/0.1'
          and compiled_plan ->> 'blueprintVersionId' = id::text
          and compiled_plan ->> 'planDigest' = plan_digest
          and jsonb_array_length(compile_issues) = 0
        )
      ),
      constraint data_blueprint_version_compilation_shape check (
        compiled_at is null
        or compiled_plan is not null
        or jsonb_array_length(compile_issues) > 0
      ),
      constraint data_blueprint_version_lifecycle_evidence check (
        (
          status = 'DRAFT'
          and static_validation_state <> 'PASSED'
          and validated_at is null
          and published_at is null
          and published_by is null
        ) or (
          status = 'VALIDATED'
          and static_validation_state = 'PASSED'
          and validated_at is not null
          and compiled_plan is not null
          and plan_digest is not null
          and compiled_at is not null
          and published_at is null
          and published_by is null
        ) or (
          status in ('PUBLISHED', 'DEPRECATED')
          and static_validation_state = 'PASSED'
          and runtime_validation_state = 'PASSED'
          and cleanup_validation_state = 'PASSED'
          and validated_at is not null
          and compiled_plan is not null
          and plan_digest is not null
          and compiled_at is not null
          and published_at is not null
          and published_by is not null
        )
      ),
      constraint data_blueprint_version_revision_positive check (revision > 0)
    )
    """,
    """
    create index data_atom_definition_project_created_idx
      on atlas.data_atom_definition (
        project_id, tenant_id, created_at desc, id desc
      )
    """,
    """
    create index data_atom_version_definition_created_idx
      on atlas.data_atom_version (
        atom_id, tenant_id, project_id, created_at desc, id desc
      )
    """,
    """
    create index data_atom_version_project_status_idx
      on atlas.data_atom_version (
        project_id, tenant_id, status, created_at desc, id desc
      )
    """,
    """
    create index data_blueprint_definition_project_created_idx
      on atlas.data_blueprint_definition (
        project_id, tenant_id, created_at desc, id desc
      )
    """,
    """
    create index data_blueprint_version_definition_created_idx
      on atlas.data_blueprint_version (
        blueprint_id, tenant_id, project_id, created_at desc, id desc
      )
    """,
    """
    create index data_blueprint_version_project_status_idx
      on atlas.data_blueprint_version (
        project_id, tenant_id, status, created_at desc, id desc
      )
    """,
    """
    create function atlas.guard_fixture_definition_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      key_column text := tg_argv[0];
    begin
      if row(new.id, new.tenant_id, new.project_id, new.created_at)
        is distinct from row(old.id, old.tenant_id, old.project_id, old.created_at)
        or to_jsonb(new) ->> key_column is distinct from to_jsonb(old) ->> key_column
      then
        raise exception 'fixture definition identity and scope are immutable';
      end if;
      if tg_table_name = 'data_atom_definition'
        and to_jsonb(new) ->> 'business_domain'
          is distinct from to_jsonb(old) ->> 'business_domain'
      then
        raise exception 'atom business domain is immutable';
      end if;
      if old.status = 'ARCHIVED' then
        raise exception 'archived fixture definition is immutable';
      end if;
      if old.status = 'ACTIVE' and new.status not in ('ACTIVE', 'ARCHIVED') then
        raise exception 'invalid fixture definition lifecycle transition';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'fixture definition revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_fixture_version_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      definition_column text := tg_argv[0];
    begin
      if row(new.id, new.tenant_id, new.project_id, new.version, new.created_at)
        is distinct from row(old.id, old.tenant_id, old.project_id, old.version, old.created_at)
        or to_jsonb(new) ->> definition_column
          is distinct from to_jsonb(old) ->> definition_column
      then
        raise exception 'fixture version identity and scope are immutable';
      end if;
      if old.status = 'DEPRECATED' then
        raise exception 'deprecated fixture version is immutable';
      end if;
      if old.status = 'PUBLISHED' then
        if new.status <> 'DEPRECATED' then
          raise exception 'published fixture version can only be deprecated';
        end if;
        if (to_jsonb(new) - 'status' - 'revision' - 'updated_at')
          is distinct from (to_jsonb(old) - 'status' - 'revision' - 'updated_at')
        then
          raise exception 'published fixture version content is immutable';
        end if;
      elsif not (
        (old.status = 'DRAFT' and new.status in ('DRAFT', 'VALIDATED'))
        or (
          old.status = 'VALIDATED'
          and new.status in ('DRAFT', 'VALIDATED', 'PUBLISHED')
        )
      ) then
        raise exception 'invalid fixture version lifecycle transition';
      end if;
      if new.contract is distinct from old.contract
        or new.content_digest is distinct from old.content_digest
      then
        if new.status <> 'DRAFT'
          or new.static_validation_state <> 'PENDING'
          or new.runtime_validation_state <> 'PENDING'
          or new.cleanup_validation_state <> 'PENDING'
          or new.validated_at is not null
          or new.published_at is not null
          or new.published_by is not null
        then
          raise exception 'changed fixture contract must reset all validation evidence';
        end if;
        if (new.contract is distinct from old.contract)
          <> (new.content_digest is distinct from old.content_digest)
        then
          raise exception 'fixture contract and digest must change together';
        end if;
      end if;
      if new.status = 'PUBLISHED' and (
        new.static_validation_state <> 'PASSED'
        or new.runtime_validation_state <> 'PASSED'
        or new.cleanup_validation_state <> 'PASSED'
        or new.validated_at is null
        or new.published_at is null
        or new.published_by is null
      ) then
        raise exception 'fixture publication requires complete validation evidence';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'fixture version revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger data_atom_definition_guard_update
      before update on atlas.data_atom_definition
      for each row execute function atlas.guard_fixture_definition_update('atom_key')
    """,
    """
    create trigger data_atom_definition_set_updated_at
      before update on atlas.data_atom_definition
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger data_atom_version_guard_update
      before update on atlas.data_atom_version
      for each row execute function atlas.guard_fixture_version_update('atom_id')
    """,
    """
    create trigger data_atom_version_set_updated_at
      before update on atlas.data_atom_version
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger data_blueprint_definition_guard_update
      before update on atlas.data_blueprint_definition
      for each row execute function atlas.guard_fixture_definition_update('blueprint_key')
    """,
    """
    create trigger data_blueprint_definition_set_updated_at
      before update on atlas.data_blueprint_definition
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger data_blueprint_version_guard_update
      before update on atlas.data_blueprint_version
      for each row execute function atlas.guard_fixture_version_update('blueprint_id')
    """,
    """
    create trigger data_blueprint_version_set_updated_at
      before update on atlas.data_blueprint_version
      for each row execute function atlas.set_updated_at()
    """,
    "alter table atlas.data_atom_definition enable row level security",
    "alter table atlas.data_atom_definition force row level security",
    "alter table atlas.data_atom_version enable row level security",
    "alter table atlas.data_atom_version force row level security",
    "alter table atlas.data_blueprint_definition enable row level security",
    "alter table atlas.data_blueprint_definition force row level security",
    "alter table atlas.data_blueprint_version enable row level security",
    "alter table atlas.data_blueprint_version force row level security",
    """
    create policy data_atom_definition_tenant_isolation
      on atlas.data_atom_definition for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy data_atom_version_tenant_isolation
      on atlas.data_atom_version for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy data_blueprint_definition_tenant_isolation
      on atlas.data_blueprint_definition for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy data_blueprint_version_tenant_isolation
      on atlas.data_blueprint_version for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    grant select, insert, update
      on atlas.data_atom_definition, atlas.data_atom_version,
         atlas.data_blueprint_definition, atlas.data_blueprint_version
      to atlas_app
    """,
    """
    revoke delete
      on atlas.data_atom_definition, atlas.data_atom_version,
         atlas.data_blueprint_definition, atlas.data_blueprint_version
      from atlas_app
    """,
)


def upgrade() -> None:
    """Create fixture asset definitions, versions, gates, and tenant RLS."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove fixture asset storage and lifecycle guards."""

    op.execute("drop table if exists atlas.data_blueprint_version")
    op.execute("drop table if exists atlas.data_blueprint_definition")
    op.execute("drop table if exists atlas.data_atom_version")
    op.execute("drop table if exists atlas.data_atom_definition")
    op.execute("drop function if exists atlas.guard_fixture_version_update()")
    op.execute("drop function if exists atlas.guard_fixture_definition_update()")
