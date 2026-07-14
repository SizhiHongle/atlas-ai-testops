"""建立测试角色、账号池、账号槽与凭证引用目录。

Revision ID: 20260713_0004
Revises: 20260713_0003
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_0004"
down_revision: str | None = "20260713_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create unique index environment_full_scope_unique
      on atlas.environment (id, tenant_id, project_id)
    """,
    """
    create table atlas.test_role (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      role_key text not null,
      name text not null,
      description text not null default '',
      capabilities text[] not null default '{}',
      status text not null default 'ACTIVE',
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint test_role_project_fk foreign key (project_id, tenant_id)
        references atlas.project (id, tenant_id) on delete restrict,
      constraint test_role_full_scope_unique unique (id, tenant_id, project_id),
      constraint test_role_scope_key_unique unique (tenant_id, project_id, role_key),
      constraint test_role_key_format check (
        role_key ~ '^[a-z][a-z0-9._-]{1,63}$'
      ),
      constraint test_role_name_not_blank check (btrim(name) <> ''),
      constraint test_role_description_size check (octet_length(description) <= 4000),
      constraint test_role_capability_count check (cardinality(capabilities) <= 64),
      constraint test_role_capability_not_null check (
        array_position(capabilities, null) is null
      ),
      constraint test_role_status_valid check (status in ('ACTIVE', 'DISABLED')),
      constraint test_role_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.account_pool (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      role_id uuid not null,
      pool_key text not null,
      name text not null,
      exclusive boolean not null default true,
      default_ttl_seconds integer not null default 1800,
      cooldown_seconds integer not null default 60,
      status text not null default 'ACTIVE',
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint account_pool_environment_scope_fk
        foreign key (environment_id, tenant_id, project_id)
        references atlas.environment (id, tenant_id, project_id) on delete restrict,
      constraint account_pool_role_scope_fk
        foreign key (role_id, tenant_id, project_id)
        references atlas.test_role (id, tenant_id, project_id) on delete restrict,
      constraint account_pool_full_scope_unique
        unique (id, tenant_id, project_id, environment_id),
      constraint account_pool_scope_key_unique
        unique (tenant_id, project_id, environment_id, pool_key),
      constraint account_pool_key_format check (
        pool_key ~ '^[a-z][a-z0-9._-]{1,63}$'
      ),
      constraint account_pool_name_not_blank check (btrim(name) <> ''),
      constraint account_pool_mvp_exclusive check (exclusive),
      constraint account_pool_ttl_range check (
        default_ttl_seconds between 300 and 7200
      ),
      constraint account_pool_cooldown_range check (
        cooldown_seconds between 0 and 86400
      ),
      constraint account_pool_status_valid check (status in ('ACTIVE', 'DISABLED')),
      constraint account_pool_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.test_account (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      pool_id uuid not null,
      account_key text not null,
      source text not null,
      external_subject_id text,
      login_hint_masked text not null,
      lifecycle_status text not null default 'DRAFT',
      health_status text not null default 'UNKNOWN',
      operational_status text not null default 'VERIFYING',
      sync_status text not null default 'NOT_APPLICABLE',
      cooldown_until timestamptz,
      lease_epoch bigint not null default 0,
      labels jsonb not null default '{}'::jsonb,
      last_leased_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint test_account_pool_scope_fk
        foreign key (pool_id, tenant_id, project_id, environment_id)
        references atlas.account_pool (id, tenant_id, project_id, environment_id)
        on delete restrict,
      constraint test_account_full_scope_unique
        unique (id, tenant_id, project_id, environment_id),
      constraint test_account_pool_key_unique unique (pool_id, account_key),
      constraint test_account_key_format check (
        account_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'
      ),
      constraint test_account_source_valid check (
        source in ('ATLAS_MANAGED', 'EXTERNAL_SYNCED', 'EXTERNAL_DELEGATED')
      ),
      constraint test_account_external_subject_required check (
        (source = 'ATLAS_MANAGED' and external_subject_id is null) or
        (source <> 'ATLAS_MANAGED' and btrim(external_subject_id) <> '')
      ),
      constraint test_account_login_hint_masked check (
        btrim(login_hint_masked) <> '' and position('*' in login_hint_masked) > 0
      ),
      constraint test_account_lifecycle_valid check (
        lifecycle_status in (
          'DRAFT', 'PROVISIONING', 'ACTIVE', 'SUSPENDED', 'RETIRING', 'RETIRED'
        )
      ),
      constraint test_account_health_valid check (
        health_status in ('UNKNOWN', 'HEALTHY', 'DEGRADED', 'QUARANTINED')
      ),
      constraint test_account_operational_valid check (
        operational_status in ('VERIFYING', 'READY', 'COOLDOWN', 'CLEANUP_FAILED')
      ),
      constraint test_account_sync_valid check (
        sync_status in ('NOT_APPLICABLE', 'IN_SYNC', 'CONFLICT', 'TOMBSTONED')
      ),
      constraint test_account_lease_epoch_nonnegative check (lease_epoch >= 0),
      constraint test_account_labels_object check (jsonb_typeof(labels) = 'object'),
      constraint test_account_labels_size check (octet_length(labels::text) <= 8192),
      constraint test_account_labels_sensitive_keys check (
        not (labels ?| array[
          'password', 'token', 'cookie', 'authorization', 'otp', 'secret',
          'totp_seed', 'storage_state'
        ])
      ),
      constraint test_account_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.credential_binding (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      account_id uuid not null,
      auth_method text not null,
      purpose text not null default 'LOGIN',
      secret_ref text not null,
      secret_version text not null,
      status text not null default 'ACTIVE',
      expires_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint credential_binding_account_scope_fk
        foreign key (account_id, tenant_id, project_id, environment_id)
        references atlas.test_account (id, tenant_id, project_id, environment_id)
        on delete restrict,
      constraint credential_binding_auth_method_valid check (
        auth_method in (
          'PASSWORD', 'OAUTH2', 'OIDC', 'SAML_SSO', 'TOTP', 'MANUAL_BOOTSTRAP'
        )
      ),
      constraint credential_binding_purpose_valid check (
        purpose in ('LOGIN', 'REFRESH_SESSION', 'ROTATE_CREDENTIAL')
      ),
      constraint credential_binding_secret_ref_opaque check (
        secret_ref ~ '^sec_[A-Za-z0-9_-]{8,200}$'
      ),
      constraint credential_binding_secret_version_not_blank check (
        btrim(secret_version) <> ''
      ),
      constraint credential_binding_status_valid check (
        status in ('ACTIVE', 'EXPIRED', 'REVOKED')
      ),
      constraint credential_binding_expiry_valid check (
        expires_at is null or expires_at > created_at
      ),
      constraint credential_binding_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.account_slot (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      account_id uuid not null,
      slot_index smallint not null default 1,
      status text not null default 'ACTIVE',
      created_at timestamptz not null default clock_timestamp(),
      constraint account_slot_account_scope_fk
        foreign key (account_id, tenant_id, project_id, environment_id)
        references atlas.test_account (id, tenant_id, project_id, environment_id)
        on delete restrict,
      constraint account_slot_full_scope_unique
        unique (id, account_id, tenant_id, project_id, environment_id),
      constraint account_slot_account_index_unique unique (account_id, slot_index),
      constraint account_slot_index_positive check (slot_index > 0),
      constraint account_slot_status_valid check (status in ('ACTIVE', 'DISABLED'))
    )
    """,
    """
    create unique index test_account_external_subject_unique
      on atlas.test_account (
        tenant_id, project_id, environment_id, source, external_subject_id
      )
      where external_subject_id is not null
    """,
    """
    create unique index credential_binding_active_method_unique
      on atlas.credential_binding (account_id, auth_method, purpose)
      where status = 'ACTIVE'
    """,
    """
    create index test_role_project_status_idx
      on atlas.test_role (tenant_id, project_id, status, created_at desc, id desc)
    """,
    """
    create index account_pool_environment_status_idx
      on atlas.account_pool (
        tenant_id, project_id, environment_id, status, created_at desc, id desc
      )
    """,
    """
    create index account_pool_role_scope_idx
      on atlas.account_pool (role_id, tenant_id, project_id)
    """,
    """
    create index test_account_pool_state_idx
      on atlas.test_account (
        tenant_id, project_id, environment_id, pool_id,
        lifecycle_status, health_status, operational_status
      )
    """,
    """
    create index test_account_cooldown_idx
      on atlas.test_account (cooldown_until)
      where operational_status = 'COOLDOWN'
    """,
    """
    create index credential_binding_account_status_idx
      on atlas.credential_binding (account_id, status, expires_at)
    """,
    """
    create index account_slot_scope_status_idx
      on atlas.account_slot (tenant_id, project_id, environment_id, status)
    """,
    """
    create trigger test_role_set_updated_at
      before update on atlas.test_role
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger account_pool_set_updated_at
      before update on atlas.account_pool
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger test_account_set_updated_at
      before update on atlas.test_account
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger credential_binding_set_updated_at
      before update on atlas.credential_binding
      for each row execute function atlas.set_updated_at()
    """,
    "alter table atlas.test_role enable row level security",
    "alter table atlas.test_role force row level security",
    "alter table atlas.account_pool enable row level security",
    "alter table atlas.account_pool force row level security",
    "alter table atlas.test_account enable row level security",
    "alter table atlas.test_account force row level security",
    "alter table atlas.credential_binding enable row level security",
    "alter table atlas.credential_binding force row level security",
    "alter table atlas.account_slot enable row level security",
    "alter table atlas.account_slot force row level security",
    """
    create policy test_role_tenant_isolation on atlas.test_role
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy account_pool_tenant_isolation on atlas.account_pool
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy test_account_tenant_isolation on atlas.test_account
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy credential_binding_tenant_isolation on atlas.credential_binding
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy account_slot_tenant_isolation on atlas.account_slot
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    "grant select, insert, update, delete on atlas.test_role to atlas_app",
    "grant select, insert, update, delete on atlas.account_pool to atlas_app",
    "grant select, insert, update, delete on atlas.test_account to atlas_app",
    "grant select, insert, update, delete on atlas.credential_binding to atlas_app",
    "grant select, insert, update, delete on atlas.account_slot to atlas_app",
)


def upgrade() -> None:
    """创建测试身份目录、作用域约束、索引、触发器和 RLS。"""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """按依赖逆序移除测试身份目录。"""

    op.execute("drop table if exists atlas.account_slot")
    op.execute("drop table if exists atlas.credential_binding")
    op.execute("drop table if exists atlas.test_account")
    op.execute("drop table if exists atlas.account_pool")
    op.execute("drop table if exists atlas.test_role")
    op.execute("drop index if exists atlas.environment_full_scope_unique")
