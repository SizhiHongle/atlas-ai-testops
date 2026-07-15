"""Create immutable CaseVersion snapshots and publication provenance.

Revision ID: 20260715_0015
Revises: 20260715_0014
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260715_0015"
down_revision: str | None = "20260715_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.case_version (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      test_case_id uuid not null,
      schema_version text not null default 'atlas.case-version/0.1',
      version text not null,
      version_ref text not null,
      status text not null default 'PUBLISHED',
      source_draft_id uuid not null,
      semantic_revision bigint not null,
      semantic_digest text not null,
      intent_version_ref text not null,
      intent_digest text not null,
      intent jsonb not null,
      test_ir jsonb not null,
      test_ir_digest text not null,
      plan_template jsonb not null,
      plan_digest text not null,
      compiled_digest text not null,
      content_digest text not null,
      debug_run_id uuid not null,
      evidence_manifest_id uuid not null,
      evidence_manifest_digest text not null,
      authored_by uuid not null,
      published_by uuid not null,
      review_summary text not null,
      published_at timestamptz not null,
      retired_at timestamptz,
      retired_by uuid,
      retirement_reason text,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint case_version_case_scope_fk foreign key (
        test_case_id, tenant_id, project_id
      ) references atlas.test_case (id, tenant_id, project_id) on delete restrict,
      constraint case_version_draft_scope_fk foreign key (
        source_draft_id, test_case_id, tenant_id, project_id
      ) references atlas.workflow_draft (
        id, test_case_id, tenant_id, project_id
      ) on delete restrict,
      constraint case_version_debug_run_scope_fk foreign key (
        debug_run_id, tenant_id, project_id, test_case_id
      ) references atlas.debug_run (
        id, tenant_id, project_id, test_case_id
      ) on delete restrict,
      constraint case_version_full_scope_unique unique (
        id, tenant_id, project_id, test_case_id
      ),
      constraint case_version_number_unique unique (
        tenant_id, project_id, test_case_id, version
      ),
      constraint case_version_ref_unique unique (
        tenant_id, project_id, version_ref
      ),
      constraint case_version_debug_run_unique unique (debug_run_id),
      constraint case_version_schema_valid check (
        schema_version = 'atlas.case-version/0.1'
      ),
      constraint case_version_semver_valid check (
        version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)'
          '(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint case_version_ref_valid check (
        version_ref = 'test-case/' || test_case_id::text || '@' || version
      ),
      constraint case_version_status_valid check (
        status in ('PUBLISHED', 'RETIRED')
      ),
      constraint case_version_revision_valid check (
        semantic_revision > 0 and revision > 0
      ),
      constraint case_version_digest_valid check (
        semantic_digest ~ '^sha256:[0-9a-f]{64}$'
        and intent_digest ~ '^sha256:[0-9a-f]{64}$'
        and test_ir_digest ~ '^sha256:[0-9a-f]{64}$'
        and plan_digest ~ '^sha256:[0-9a-f]{64}$'
        and compiled_digest ~ '^sha256:[0-9a-f]{64}$'
        and content_digest ~ '^sha256:[0-9a-f]{64}$'
        and evidence_manifest_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint case_version_intent_shape check (
        jsonb_typeof(intent) = 'object'
        and intent ->> 'schemaVersion' = 'atlas.test-intent/0.1'
      ),
      constraint case_version_test_ir_shape check (
        jsonb_typeof(test_ir) = 'object'
        and test_ir ->> 'schemaVersion' = 'atlas.test-ir/0.2'
        and test_ir ->> 'testCaseId' = test_case_id::text
        and (test_ir ->> 'semanticRevision')::bigint = semantic_revision
        and test_ir ->> 'intentVersionRef' = intent_version_ref
        and test_ir ->> 'contentDigest' = test_ir_digest
      ),
      constraint case_version_plan_shape check (
        jsonb_typeof(plan_template) = 'object'
        and plan_template ->> 'schemaVersion' = 'atlas.plan-template/0.1'
        and plan_template ->> 'testCaseId' = test_case_id::text
        and (plan_template ->> 'semanticRevision')::bigint = semantic_revision
        and plan_template ->> 'testIrDigest' = test_ir_digest
        and plan_template ->> 'planDigest' = plan_digest
      ),
      constraint case_version_actor_separation check (
        authored_by <> published_by
      ),
      constraint case_version_review_summary_safe check (
        btrim(review_summary) <> '' and octet_length(review_summary) <= 4000
      ),
      constraint case_version_retirement_reason_safe check (
        retirement_reason is null or (
          btrim(retirement_reason) <> ''
          and octet_length(retirement_reason) <= 2000
        )
      ),
      constraint case_version_lifecycle_shape check (
        (
          status = 'PUBLISHED'
          and retired_at is null
          and retired_by is null
          and retirement_reason is null
        ) or (
          status = 'RETIRED'
          and retired_at is not null
          and retired_by is not null
          and retirement_reason is not null
        )
      ),
      constraint case_version_time_order check (
        created_at >= published_at
        and updated_at >= published_at
        and (retired_at is null or retired_at >= published_at)
      )
    )
    """,
    """
    create table atlas.case_version_node (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      test_case_id uuid not null,
      case_version_id uuid not null,
      node_key text not null,
      kind text not null,
      version_ref text not null,
      phase text not null,
      input_ports jsonb not null default '[]'::jsonb,
      output_ports jsonb not null default '[]'::jsonb,
      params jsonb not null default '{}'::jsonb,
      terminal boolean not null default false,
      oracle_strength text,
      created_at timestamptz not null default clock_timestamp(),
      constraint case_version_node_version_scope_fk foreign key (
        case_version_id, tenant_id, project_id, test_case_id
      ) references atlas.case_version (
        id, tenant_id, project_id, test_case_id
      ) on delete restrict,
      constraint case_version_node_key_unique unique (
        case_version_id, node_key
      ),
      constraint case_version_node_edge_scope_unique unique (
        case_version_id, node_key, tenant_id, project_id, test_case_id
      ),
      constraint case_version_node_key_valid check (
        node_key ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$'
      ),
      constraint case_version_node_kind_valid check (
        btrim(kind) <> '' and octet_length(kind) <= 256
      ),
      constraint case_version_node_version_ref_valid check (
        version_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}'
          '@[0-9]+[.][0-9]+[.][0-9]+(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint case_version_node_phase_valid check (
        phase in ('setup', 'identity', 'execute', 'assert', 'cleanup')
      ),
      constraint case_version_node_ports_valid check (
        jsonb_typeof(input_ports) = 'array'
        and jsonb_typeof(output_ports) = 'array'
      ),
      constraint case_version_node_params_valid check (
        jsonb_typeof(params) = 'object'
      ),
      constraint case_version_node_oracle_valid check (
        oracle_strength is null
        or oracle_strength in ('hard', 'soft', 'diagnostic')
      )
    )
    """,
    """
    create table atlas.case_version_edge (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      test_case_id uuid not null,
      case_version_id uuid not null,
      edge_key text not null,
      source_node_key text not null,
      source_port text not null,
      target_node_key text not null,
      target_port text not null,
      semantic_type text not null,
      kind text not null default 'data',
      mapping text not null default 'direct',
      created_at timestamptz not null default clock_timestamp(),
      constraint case_version_edge_version_scope_fk foreign key (
        case_version_id, tenant_id, project_id, test_case_id
      ) references atlas.case_version (
        id, tenant_id, project_id, test_case_id
      ) on delete restrict,
      constraint case_version_edge_source_node_fk foreign key (
        case_version_id, source_node_key, tenant_id, project_id, test_case_id
      ) references atlas.case_version_node (
        case_version_id, node_key, tenant_id, project_id, test_case_id
      ) on delete restrict,
      constraint case_version_edge_target_node_fk foreign key (
        case_version_id, target_node_key, tenant_id, project_id, test_case_id
      ) references atlas.case_version_node (
        case_version_id, node_key, tenant_id, project_id, test_case_id
      ) on delete restrict,
      constraint case_version_edge_key_unique unique (
        case_version_id, edge_key
      ),
      constraint case_version_edge_key_valid check (
        edge_key ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$'
      ),
      constraint case_version_edge_ports_valid check (
        source_port ~ '^[A-Za-z_][A-Za-z0-9_.-]{0,127}$'
        and target_port ~ '^[A-Za-z_][A-Za-z0-9_.-]{0,127}$'
      ),
      constraint case_version_edge_semantic_type_valid check (
        btrim(semantic_type) <> '' and octet_length(semantic_type) <= 512
      ),
      constraint case_version_edge_kind_valid check (
        kind in ('data', 'control')
      ),
      constraint case_version_edge_mapping_valid check (
        mapping = 'direct'
      )
    )
    """,
    """
    create index case_version_history_idx
      on atlas.case_version (
        tenant_id, project_id, test_case_id, published_at desc, id desc
      )
    """,
    """
    create index case_version_published_idx
      on atlas.case_version (
        tenant_id, project_id, test_case_id, published_at desc, id desc
      ) where status = 'PUBLISHED'
    """,
    """
    create index case_version_case_scope_fk_idx
      on atlas.case_version (test_case_id, tenant_id, project_id)
    """,
    """
    create index case_version_draft_scope_fk_idx
      on atlas.case_version (
        source_draft_id, test_case_id, tenant_id, project_id
      )
    """,
    """
    create index case_version_debug_run_scope_fk_idx
      on atlas.case_version (
        debug_run_id, tenant_id, project_id, test_case_id
      )
    """,
    """
    create index case_version_node_scope_idx
      on atlas.case_version_node (
        tenant_id, project_id, test_case_id, case_version_id, node_key
      )
    """,
    """
    create index case_version_node_version_scope_fk_idx
      on atlas.case_version_node (
        case_version_id, tenant_id, project_id, test_case_id
      )
    """,
    """
    create index case_version_edge_scope_idx
      on atlas.case_version_edge (
        tenant_id, project_id, test_case_id, case_version_id, edge_key
      )
    """,
    """
    create index case_version_edge_version_scope_fk_idx
      on atlas.case_version_edge (
        case_version_id, tenant_id, project_id, test_case_id
      )
    """,
    """
    create index case_version_edge_source_node_fk_idx
      on atlas.case_version_edge (
        case_version_id, source_node_key, tenant_id, project_id, test_case_id
      )
    """,
    """
    create index case_version_edge_target_node_fk_idx
      on atlas.case_version_edge (
        case_version_id, target_node_key, tenant_id, project_id, test_case_id
      )
    """,
    """
    create function atlas.guard_case_version_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.test_case_id,
        new.schema_version, new.version, new.version_ref,
        new.source_draft_id, new.semantic_revision, new.semantic_digest,
        new.intent_version_ref, new.intent_digest, new.intent,
        new.test_ir, new.test_ir_digest,
        new.plan_template, new.plan_digest, new.compiled_digest,
        new.content_digest, new.debug_run_id,
        new.evidence_manifest_id, new.evidence_manifest_digest,
        new.authored_by, new.published_by, new.review_summary,
        new.published_at, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.test_case_id,
        old.schema_version, old.version, old.version_ref,
        old.source_draft_id, old.semantic_revision, old.semantic_digest,
        old.intent_version_ref, old.intent_digest, old.intent,
        old.test_ir, old.test_ir_digest,
        old.plan_template, old.plan_digest, old.compiled_digest,
        old.content_digest, old.debug_run_id,
        old.evidence_manifest_id, old.evidence_manifest_digest,
        old.authored_by, old.published_by, old.review_summary,
        old.published_at, old.created_at
      ) then
        raise exception 'published case version content and provenance are immutable';
      end if;
      if old.status <> 'PUBLISHED' or new.status <> 'RETIRED' then
        raise exception 'case version only supports PUBLISHED to RETIRED';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'case version revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger case_version_guard_update
      before update on atlas.case_version
      for each row execute function atlas.guard_case_version_update()
    """,
    """
    create trigger case_version_prevent_delete
      before delete on atlas.case_version
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger case_version_set_updated_at
      before update on atlas.case_version
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger case_version_node_prevent_mutation
      before update or delete on atlas.case_version_node
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger case_version_edge_prevent_mutation
      before update or delete on atlas.case_version_edge
      for each row execute function atlas.prevent_fact_mutation()
    """,
    "alter table atlas.case_version enable row level security",
    "alter table atlas.case_version force row level security",
    "alter table atlas.case_version_node enable row level security",
    "alter table atlas.case_version_node force row level security",
    "alter table atlas.case_version_edge enable row level security",
    "alter table atlas.case_version_edge force row level security",
    """
    create policy case_version_tenant_isolation
      on atlas.case_version for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy case_version_node_tenant_isolation
      on atlas.case_version_node for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy case_version_edge_tenant_isolation
      on atlas.case_version_edge for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.case_version from atlas_app",
    "revoke all on atlas.case_version_node from atlas_app",
    "revoke all on atlas.case_version_edge from atlas_app",
    "grant select, insert, update on atlas.case_version to atlas_app",
    "grant select, insert on atlas.case_version_node to atlas_app",
    "grant select, insert on atlas.case_version_edge to atlas_app",
)


def upgrade() -> None:
    """Create tenant-isolated immutable CaseVersion storage."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove CaseVersion snapshots and lifecycle guards."""

    op.execute("drop table if exists atlas.case_version_edge")
    op.execute("drop table if exists atlas.case_version_node")
    op.execute("drop table if exists atlas.case_version")
    op.execute("drop function if exists atlas.guard_case_version_update()")
