"""建立平台、隔离、审计和可靠事件基础。

Revision ID: 20260713_0001
Revises:
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    "create schema if not exists atlas",
    """
    create function atlas.current_tenant_id() returns uuid
    language sql stable
    as $$
      select nullif(current_setting('atlas.tenant_id', true), '')::uuid
    $$
    """,
    """
    create function atlas.current_actor_id() returns uuid
    language sql stable
    as $$
      select nullif(current_setting('atlas.actor_id', true), '')::uuid
    $$
    """,
    """
    create function atlas.set_updated_at() returns trigger
    language plpgsql
    as $$
    begin
      new.updated_at = clock_timestamp();
      return new;
    end
    $$
    """,
    """
    create function atlas.prevent_fact_mutation() returns trigger
    language plpgsql
    as $$
    begin
      raise exception 'append-only fact % cannot be changed', tg_table_name
        using errcode = '55000';
    end
    $$
    """,
    """
    create table atlas.tenant (
      id uuid primary key,
      slug text not null,
      name text not null,
      status text not null default 'ACTIVE',
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint tenant_slug_unique unique (slug),
      constraint tenant_slug_format check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
      constraint tenant_name_not_blank check (btrim(name) <> ''),
      constraint tenant_status_valid check (status in ('ACTIVE', 'SUSPENDED')),
      constraint tenant_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.project (
      id uuid primary key,
      tenant_id uuid not null,
      project_key text not null,
      name text not null,
      status text not null default 'ACTIVE',
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint project_tenant_fk foreign key (tenant_id)
        references atlas.tenant (id) on delete restrict,
      constraint project_id_tenant_unique unique (id, tenant_id),
      constraint project_tenant_key_unique unique (tenant_id, project_key),
      constraint project_key_format check (project_key ~ '^[A-Z][A-Z0-9_]{1,31}$'),
      constraint project_name_not_blank check (btrim(name) <> ''),
      constraint project_status_valid check (status in ('ACTIVE', 'ARCHIVED')),
      constraint project_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.environment (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_key text not null,
      name text not null,
      kind text not null,
      status text not null default 'ACTIVE',
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint environment_project_tenant_fk foreign key (project_id, tenant_id)
        references atlas.project (id, tenant_id) on delete restrict,
      constraint environment_id_tenant_unique unique (id, tenant_id),
      constraint environment_project_key_unique unique (tenant_id, project_id, environment_key),
      constraint environment_key_format check (environment_key ~ '^[a-z][a-z0-9-]{1,31}$'),
      constraint environment_name_not_blank check (btrim(name) <> ''),
      constraint environment_kind_valid check (kind in ('TEST', 'STAGING', 'PRODUCTION')),
      constraint environment_status_valid check (status in ('ACTIVE', 'DISABLED')),
      constraint environment_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.audit_event (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid,
      environment_id uuid,
      actor_id uuid,
      event_type text not null,
      entity_type text not null,
      entity_id uuid,
      occurred_at timestamptz not null,
      payload jsonb not null default '{}'::jsonb,
      request_id text not null,
      constraint audit_event_tenant_fk foreign key (tenant_id)
        references atlas.tenant (id) on delete restrict,
      constraint audit_event_project_tenant_fk foreign key (project_id, tenant_id)
        references atlas.project (id, tenant_id) on delete restrict,
      constraint audit_event_environment_tenant_fk foreign key (environment_id, tenant_id)
        references atlas.environment (id, tenant_id) on delete restrict,
      constraint audit_event_type_not_blank check (btrim(event_type) <> ''),
      constraint audit_event_entity_type_not_blank check (btrim(entity_type) <> ''),
      constraint audit_event_request_id_not_blank check (btrim(request_id) <> ''),
      constraint audit_event_payload_object check (jsonb_typeof(payload) = 'object')
    )
    """,
    """
    create table atlas.outbox_event (
      id uuid primary key,
      tenant_id uuid not null,
      aggregate_type text not null,
      aggregate_id uuid not null,
      event_type text not null,
      payload jsonb not null,
      occurred_at timestamptz not null,
      available_at timestamptz not null,
      claimed_at timestamptz,
      claimed_by text,
      processed_at timestamptz,
      attempts integer not null default 0,
      last_error text,
      constraint outbox_event_tenant_fk foreign key (tenant_id)
        references atlas.tenant (id) on delete restrict,
      constraint outbox_event_aggregate_type_not_blank check (btrim(aggregate_type) <> ''),
      constraint outbox_event_type_not_blank check (btrim(event_type) <> ''),
      constraint outbox_event_payload_object check (jsonb_typeof(payload) = 'object'),
      constraint outbox_event_attempts_nonnegative check (attempts >= 0),
      constraint outbox_event_claim_pair check (
        (claimed_at is null and claimed_by is null) or
        (claimed_at is not null and claimed_by is not null)
      )
    )
    """,
    """
    create table atlas.idempotency_record (
      tenant_id uuid not null,
      scope text not null,
      idempotency_key text not null,
      request_hash text not null,
      state text not null default 'PROCESSING',
      status_code integer,
      response_body jsonb,
      created_at timestamptz not null,
      expires_at timestamptz not null,
      primary key (tenant_id, scope, idempotency_key),
      constraint idempotency_record_tenant_fk foreign key (tenant_id)
        references atlas.tenant (id) on delete restrict,
      constraint idempotency_record_scope_not_blank check (btrim(scope) <> ''),
      constraint idempotency_record_key_not_blank check (btrim(idempotency_key) <> ''),
      constraint idempotency_record_request_hash_sha256 check (request_hash ~ '^[0-9a-f]{64}$'),
      constraint idempotency_record_state_valid check (state in ('PROCESSING', 'COMPLETED')),
      constraint idempotency_record_status_valid check (
        status_code is null or status_code between 100 and 599
      ),
      constraint idempotency_record_response_object check (
        response_body is null or jsonb_typeof(response_body) = 'object'
      ),
      constraint idempotency_record_expiry_valid check (expires_at > created_at),
      constraint idempotency_record_completion_valid check (
        (state = 'PROCESSING' and status_code is null and response_body is null) or
        (state = 'COMPLETED' and status_code is not null and response_body is not null)
      )
    )
    """,
    """
    create index project_tenant_status_idx
      on atlas.project (tenant_id, status, created_at desc)
    """,
    """
    create index environment_tenant_project_status_idx
      on atlas.environment (tenant_id, project_id, status)
    """,
    """
    create index audit_event_project_tenant_idx
      on atlas.audit_event (project_id, tenant_id)
      where project_id is not null
    """,
    """
    create index audit_event_environment_tenant_idx
      on atlas.audit_event (environment_id, tenant_id)
      where environment_id is not null
    """,
    """
    create index audit_event_tenant_time_idx
      on atlas.audit_event (tenant_id, occurred_at desc, id)
    """,
    "create index outbox_event_tenant_idx on atlas.outbox_event (tenant_id)",
    """
    create index outbox_event_pending_idx
      on atlas.outbox_event (available_at, occurred_at, id)
      where processed_at is null
    """,
    """
    create index idempotency_record_expiry_idx
      on atlas.idempotency_record (expires_at)
      where state = 'COMPLETED'
    """,
    """
    create trigger tenant_set_updated_at
      before update on atlas.tenant
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger project_set_updated_at
      before update on atlas.project
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger environment_set_updated_at
      before update on atlas.environment
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger audit_event_prevent_update
      before update or delete on atlas.audit_event
      for each row execute function atlas.prevent_fact_mutation()
    """,
    "alter table atlas.tenant enable row level security",
    "alter table atlas.tenant force row level security",
    "alter table atlas.project enable row level security",
    "alter table atlas.project force row level security",
    "alter table atlas.environment enable row level security",
    "alter table atlas.environment force row level security",
    "alter table atlas.audit_event enable row level security",
    "alter table atlas.audit_event force row level security",
    "alter table atlas.outbox_event enable row level security",
    "alter table atlas.outbox_event force row level security",
    "alter table atlas.idempotency_record enable row level security",
    "alter table atlas.idempotency_record force row level security",
    """
    create policy tenant_isolation on atlas.tenant
      for all
      using (id = atlas.current_tenant_id())
      with check (id = atlas.current_tenant_id())
    """,
    """
    create policy project_tenant_isolation on atlas.project
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy environment_tenant_isolation on atlas.environment
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy audit_event_tenant_isolation on atlas.audit_event
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy outbox_event_tenant_isolation on atlas.outbox_event
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy idempotency_record_tenant_isolation on atlas.idempotency_record
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    "grant usage on schema atlas to atlas_app",
    "grant select, insert, update, delete on all tables in schema atlas to atlas_app",
    "grant execute on all functions in schema atlas to atlas_app",
    """
    alter default privileges in schema atlas
      grant select, insert, update, delete on tables to atlas_app
    """,
    "alter default privileges in schema atlas grant execute on functions to atlas_app",
)


def upgrade() -> None:
    """按显式顺序创建 Schema、约束、索引、触发器和 RLS。"""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """仅在所有后续 Migration 已回退后删除 Atlas Schema。"""

    op.execute("drop schema if exists atlas cascade")
