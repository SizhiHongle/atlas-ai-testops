# ruff: noqa: E501
"""Create TestCase authoring aggregates and normalized WorkflowDraft storage.

Revision ID: 20260715_0013
Revises: 20260714_0012
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260715_0013"
down_revision: str | None = "20260714_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.test_case (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      case_key text not null,
      name text not null,
      status text not null default 'ACTIVE',
      intent_version text not null,
      intent_version_ref text not null,
      intent jsonb not null,
      intent_digest text not null,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint test_case_project_scope_fk foreign key (
        project_id, tenant_id
      ) references atlas.project (id, tenant_id) on delete restrict,
      constraint test_case_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint test_case_project_key_unique unique (
        tenant_id, project_id, case_key
      ),
      constraint test_case_key_format check (
        case_key ~ '^[A-Z][A-Z0-9]*(-[A-Z0-9]+){1,7}$'
      ),
      constraint test_case_name_valid check (
        btrim(name) <> '' and octet_length(name) <= 640
      ),
      constraint test_case_status_valid check (
        status in ('ACTIVE', 'ARCHIVED')
      ),
      constraint test_case_intent_version_valid check (
        intent_version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint test_case_intent_ref_valid check (
        btrim(intent_version_ref) <> '' and octet_length(intent_version_ref) <= 256
      ),
      constraint test_case_intent_schema_valid check (
        jsonb_typeof(intent) = 'object'
        and intent ->> 'schemaVersion' = 'atlas.test-intent/0.1'
      ),
      constraint test_case_intent_digest_valid check (
        intent_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint test_case_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.workflow_draft (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      test_case_id uuid not null,
      schema_version text not null default 'atlas.workflow-draft/0.1',
      semantic_revision bigint not null default 1,
      layout_revision bigint not null default 1,
      intent_version_ref text not null,
      layout jsonb not null default '{}'::jsonb,
      updated_by text not null,
      semantic_digest text not null,
      graph_valid boolean not null default false,
      validation_issues jsonb not null default '[]'::jsonb,
      execution_levels jsonb not null default '[]'::jsonb,
      matched_required_inputs integer not null default 0,
      total_required_inputs integer not null default 0,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint workflow_draft_case_scope_fk foreign key (
        test_case_id, tenant_id, project_id
      ) references atlas.test_case (id, tenant_id, project_id) on delete restrict,
      constraint workflow_draft_case_unique unique (test_case_id),
      constraint workflow_draft_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint workflow_draft_case_scope_unique unique (
        id, test_case_id, tenant_id, project_id
      ),
      constraint workflow_draft_schema_valid check (
        schema_version = 'atlas.workflow-draft/0.1'
      ),
      constraint workflow_draft_revision_valid check (
        semantic_revision > 0 and layout_revision > 0
      ),
      constraint workflow_draft_intent_ref_valid check (
        btrim(intent_version_ref) <> '' and octet_length(intent_version_ref) <= 256
      ),
      constraint workflow_draft_layout_valid check (
        jsonb_typeof(layout) = 'object'
      ),
      constraint workflow_draft_author_valid check (
        updated_by in ('ai', 'human')
      ),
      constraint workflow_draft_semantic_digest_valid check (
        semantic_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint workflow_draft_validation_issues_valid check (
        jsonb_typeof(validation_issues) = 'array'
      ),
      constraint workflow_draft_execution_levels_valid check (
        jsonb_typeof(execution_levels) = 'array'
      ),
      constraint workflow_draft_input_counts_valid check (
        matched_required_inputs >= 0
        and total_required_inputs >= 0
        and matched_required_inputs <= total_required_inputs
      )
    )
    """,
    """
    create table atlas.workflow_node (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      draft_id uuid not null,
      node_key text not null,
      kind text not null,
      version_ref text not null,
      phase text not null,
      input_ports jsonb not null default '[]'::jsonb,
      output_ports jsonb not null default '[]'::jsonb,
      params jsonb not null default '{}'::jsonb,
      terminal boolean not null default false,
      oracle_strength text,
      constraint workflow_node_draft_scope_fk foreign key (
        draft_id, tenant_id, project_id
      ) references atlas.workflow_draft (id, tenant_id, project_id) on delete restrict,
      constraint workflow_node_draft_key_unique unique (
        draft_id, node_key
      ),
      constraint workflow_node_edge_scope_unique unique (
        draft_id, node_key, tenant_id, project_id
      ),
      constraint workflow_node_key_valid check (
        node_key ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$'
      ),
      constraint workflow_node_kind_valid check (
        btrim(kind) <> '' and octet_length(kind) <= 256
      ),
      constraint workflow_node_version_ref_valid check (
        btrim(version_ref) <> '' and octet_length(version_ref) <= 256
      ),
      constraint workflow_node_phase_valid check (
        phase in ('setup', 'identity', 'execute', 'assert', 'cleanup')
      ),
      constraint workflow_node_ports_valid check (
        jsonb_typeof(input_ports) = 'array'
        and jsonb_typeof(output_ports) = 'array'
      ),
      constraint workflow_node_params_valid check (
        jsonb_typeof(params) = 'object'
      ),
      constraint workflow_node_oracle_valid check (
        oracle_strength is null or oracle_strength in ('hard', 'soft', 'diagnostic')
      )
    )
    """,
    """
    create table atlas.workflow_edge (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      draft_id uuid not null,
      edge_key text not null,
      source_node_key text not null,
      source_port text not null,
      target_node_key text not null,
      target_port text not null,
      semantic_type text not null,
      kind text not null default 'data',
      mapping text not null default 'direct',
      constraint workflow_edge_draft_scope_fk foreign key (
        draft_id, tenant_id, project_id
      ) references atlas.workflow_draft (id, tenant_id, project_id) on delete restrict,
      constraint workflow_edge_source_node_fk foreign key (
        draft_id, source_node_key, tenant_id, project_id
      ) references atlas.workflow_node (
        draft_id, node_key, tenant_id, project_id
      ) on delete restrict,
      constraint workflow_edge_target_node_fk foreign key (
        draft_id, target_node_key, tenant_id, project_id
      ) references atlas.workflow_node (
        draft_id, node_key, tenant_id, project_id
      ) on delete restrict,
      constraint workflow_edge_draft_key_unique unique (
        draft_id, edge_key
      ),
      constraint workflow_edge_key_valid check (
        edge_key ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$'
      ),
      constraint workflow_edge_ports_valid check (
        source_port ~ '^[A-Za-z_][A-Za-z0-9_.-]{0,127}$'
        and target_port ~ '^[A-Za-z_][A-Za-z0-9_.-]{0,127}$'
      ),
      constraint workflow_edge_semantic_type_valid check (
        btrim(semantic_type) <> '' and octet_length(semantic_type) <= 512
      ),
      constraint workflow_edge_kind_valid check (
        kind in ('data', 'control')
      ),
      constraint workflow_edge_mapping_valid check (
        mapping = 'direct'
      )
    )
    """,
    """
    create table atlas.draft_operation (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      draft_id uuid not null,
      test_case_id uuid not null,
      patch_id uuid,
      client_mutation_id text not null,
      operation_scope text not null,
      source text not null,
      actor_id uuid,
      base_revision bigint not null,
      result_revision bigint not null,
      request_digest text not null,
      before_digest text not null,
      after_digest text not null,
      operations jsonb not null,
      response jsonb not null,
      rationale_summary text,
      created_at timestamptz not null default clock_timestamp(),
      constraint draft_operation_draft_scope_fk foreign key (
        draft_id, test_case_id, tenant_id, project_id
      ) references atlas.workflow_draft (
        id, test_case_id, tenant_id, project_id
      ) on delete restrict,
      constraint draft_operation_mutation_unique unique (
        draft_id, operation_scope, client_mutation_id
      ),
      constraint draft_operation_mutation_id_valid check (
        btrim(client_mutation_id) <> '' and octet_length(client_mutation_id) <= 800
      ),
      constraint draft_operation_scope_valid check (
        operation_scope in ('SEMANTIC', 'LAYOUT')
      ),
      constraint draft_operation_source_valid check (
        source in ('ai', 'human')
      ),
      constraint draft_operation_revision_valid check (
        base_revision >= 0 and result_revision = base_revision + 1
      ),
      constraint draft_operation_request_digest_valid check (
        request_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint draft_operation_before_digest_valid check (
        before_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint draft_operation_after_digest_valid check (
        after_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint draft_operation_operations_valid check (
        jsonb_typeof(operations) in ('array', 'object')
      ),
      constraint draft_operation_scope_shape check (
        (
          operation_scope = 'SEMANTIC'
          and patch_id is not null
          and jsonb_typeof(operations) = 'array'
        ) or (
          operation_scope = 'LAYOUT'
          and patch_id is null
          and jsonb_typeof(operations) = 'object'
        )
      ),
      constraint draft_operation_response_valid check (
        jsonb_typeof(response) = 'object'
      ),
      constraint draft_operation_rationale_valid check (
        rationale_summary is null or (
          btrim(rationale_summary) <> '' and octet_length(rationale_summary) <= 4000
        )
      )
    )
    """,
    """
    create index test_case_project_catalog_idx
      on atlas.test_case (
        tenant_id, project_id, status, created_at desc, id desc
      )
    """,
    """
    create index test_case_project_created_idx
      on atlas.test_case (
        tenant_id, project_id, created_at desc, id desc
      )
    """,
    """
    create index test_case_project_fk_idx
      on atlas.test_case (project_id, tenant_id)
    """,
    """
    create index test_case_active_catalog_idx
      on atlas.test_case (tenant_id, project_id, created_at desc, id desc)
      where status = 'ACTIVE'
    """,
    """
    create index workflow_draft_scope_idx
      on atlas.workflow_draft (tenant_id, project_id, test_case_id)
    """,
    """
    create index workflow_node_draft_scope_idx
      on atlas.workflow_node (tenant_id, project_id, draft_id, node_key)
    """,
    """
    create index workflow_node_draft_fk_idx
      on atlas.workflow_node (draft_id, tenant_id, project_id)
    """,
    """
    create index workflow_edge_draft_scope_idx
      on atlas.workflow_edge (tenant_id, project_id, draft_id, edge_key)
    """,
    """
    create index workflow_edge_draft_fk_idx
      on atlas.workflow_edge (draft_id, tenant_id, project_id)
    """,
    """
    create index workflow_edge_source_node_fk_idx
      on atlas.workflow_edge (
        draft_id, source_node_key, tenant_id, project_id
      )
    """,
    """
    create index workflow_edge_target_node_fk_idx
      on atlas.workflow_edge (
        draft_id, target_node_key, tenant_id, project_id
      )
    """,
    """
    create index draft_operation_history_idx
      on atlas.draft_operation (draft_id, created_at desc, id desc)
    """,
    """
    create index draft_operation_draft_fk_idx
      on atlas.draft_operation (
        draft_id, test_case_id, tenant_id, project_id
      )
    """,
    """
    create unique index draft_operation_patch_unique_idx
      on atlas.draft_operation (draft_id, patch_id)
      where patch_id is not null
    """,
    """
    create function atlas.guard_test_case_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.case_key, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.case_key, old.created_at
      ) then
        raise exception 'test case identity and scope are immutable';
      end if;
      if old.status = 'ARCHIVED' then
        raise exception 'archived test case is immutable';
      end if;
      if old.status = 'ACTIVE' and new.status not in ('ACTIVE', 'ARCHIVED') then
        raise exception 'invalid test case lifecycle transition';
      end if;
      if (new.intent is distinct from old.intent)
        <> (new.intent_digest is distinct from old.intent_digest)
        or (new.intent is distinct from old.intent)
          <> (new.intent_version is distinct from old.intent_version)
        or (new.intent is distinct from old.intent)
          <> (new.intent_version_ref is distinct from old.intent_version_ref)
      then
        raise exception 'test case intent version, reference, content, and digest must change together';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'test case revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_workflow_draft_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.test_case_id,
        new.schema_version, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.test_case_id,
        old.schema_version, old.created_at
      ) then
        raise exception 'workflow draft identity and scope are immutable';
      end if;

      if new.semantic_revision = old.semantic_revision + 1
        and new.layout_revision = old.layout_revision
      then
        if new.layout is distinct from old.layout then
          raise exception 'semantic update cannot change workflow layout';
        end if;
      elsif new.layout_revision = old.layout_revision + 1
        and new.semantic_revision = old.semantic_revision
      then
        if row(
          new.intent_version_ref, new.semantic_digest, new.graph_valid,
          new.validation_issues, new.execution_levels,
          new.matched_required_inputs, new.total_required_inputs
        ) is distinct from row(
          old.intent_version_ref, old.semantic_digest, old.graph_valid,
          old.validation_issues, old.execution_levels,
          old.matched_required_inputs, old.total_required_inputs
        ) then
          raise exception 'layout update cannot change workflow semantics';
        end if;
      else
        raise exception 'workflow draft must increase exactly one revision by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger test_case_guard_update
      before update on atlas.test_case
      for each row execute function atlas.guard_test_case_update()
    """,
    """
    create trigger test_case_set_updated_at
      before update on atlas.test_case
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger workflow_draft_guard_update
      before update on atlas.workflow_draft
      for each row execute function atlas.guard_workflow_draft_update()
    """,
    """
    create trigger workflow_draft_set_updated_at
      before update on atlas.workflow_draft
      for each row execute function atlas.set_updated_at()
    """,
    "alter table atlas.test_case enable row level security",
    "alter table atlas.test_case force row level security",
    "alter table atlas.workflow_draft enable row level security",
    "alter table atlas.workflow_draft force row level security",
    "alter table atlas.workflow_node enable row level security",
    "alter table atlas.workflow_node force row level security",
    "alter table atlas.workflow_edge enable row level security",
    "alter table atlas.workflow_edge force row level security",
    "alter table atlas.draft_operation enable row level security",
    "alter table atlas.draft_operation force row level security",
    """
    create policy test_case_tenant_isolation
      on atlas.test_case for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy workflow_draft_tenant_isolation
      on atlas.workflow_draft for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy workflow_node_tenant_isolation
      on atlas.workflow_node for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy workflow_edge_tenant_isolation
      on atlas.workflow_edge for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy draft_operation_tenant_isolation
      on atlas.draft_operation for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.test_case from atlas_app",
    "revoke all on atlas.workflow_draft from atlas_app",
    "revoke all on atlas.workflow_node from atlas_app",
    "revoke all on atlas.workflow_edge from atlas_app",
    "revoke all on atlas.draft_operation from atlas_app",
    "grant select, insert, update on atlas.test_case to atlas_app",
    "grant select, insert, update on atlas.workflow_draft to atlas_app",
    "grant select, insert, delete on atlas.workflow_node to atlas_app",
    "grant select, insert, delete on atlas.workflow_edge to atlas_app",
    "grant select, insert on atlas.draft_operation to atlas_app",
)


def upgrade() -> None:
    """Create tenant-isolated TestCase authoring storage."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove TestCase authoring storage and lifecycle guards."""

    op.execute("drop table if exists atlas.draft_operation")
    op.execute("drop table if exists atlas.workflow_edge")
    op.execute("drop table if exists atlas.workflow_node")
    op.execute("drop table if exists atlas.workflow_draft")
    op.execute("drop table if exists atlas.test_case")
    op.execute("drop function if exists atlas.guard_workflow_draft_update()")
    op.execute("drop function if exists atlas.guard_test_case_update()")
