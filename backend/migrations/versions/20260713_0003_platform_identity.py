"""建立平台主体、成员关系、密码凭据和 Session。

Revision ID: 20260713_0003
Revises: 20260713_0002
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_0003"
down_revision: str | None = "20260713_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create function atlas.current_session_hash() returns text
    language sql stable
    as $$
      select nullif(current_setting('atlas.session_hash', true), '')
    $$
    """,
    """
    create table atlas.platform_user (
      id uuid primary key,
      email text not null,
      display_name text not null,
      status text not null default 'ACTIVE',
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint platform_user_email_unique unique (email),
      constraint platform_user_email_normalized check (email = lower(btrim(email))),
      constraint platform_user_email_format check (
        email ~ '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$'
      ),
      constraint platform_user_display_name_not_blank check (btrim(display_name) <> ''),
      constraint platform_user_status_valid check (status in ('ACTIVE', 'DISABLED')),
      constraint platform_user_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.password_credential (
      user_id uuid primary key,
      password_hash text not null,
      failed_attempts integer not null default 0,
      locked_until timestamptz,
      password_changed_at timestamptz not null default clock_timestamp(),
      revision bigint not null default 1,
      constraint password_credential_user_fk foreign key (user_id)
        references atlas.platform_user (id) on delete cascade,
      constraint password_credential_argon2id check (password_hash like '$argon2id$%'),
      constraint password_credential_failed_nonnegative check (failed_attempts >= 0),
      constraint password_credential_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.platform_membership (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid,
      user_id uuid not null,
      role text not null,
      status text not null default 'ACTIVE',
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint platform_membership_tenant_fk foreign key (tenant_id)
        references atlas.tenant (id) on delete restrict,
      constraint platform_membership_project_tenant_fk foreign key (project_id, tenant_id)
        references atlas.project (id, tenant_id) on delete restrict,
      constraint platform_membership_user_fk foreign key (user_id)
        references atlas.platform_user (id) on delete restrict,
      constraint platform_membership_role_valid check (
        role in (
          'ORG_ADMIN', 'PROJECT_ADMIN', 'COMPONENT_MAINTAINER',
          'CASE_AUTHOR', 'CASE_REVIEWER', 'RUN_OPERATOR', 'OBSERVER'
        )
      ),
      constraint platform_membership_scope_valid check (
        (role = 'ORG_ADMIN' and project_id is null) or
        (role <> 'ORG_ADMIN' and project_id is not null)
      ),
      constraint platform_membership_status_valid check (status in ('ACTIVE', 'REVOKED')),
      constraint platform_membership_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.platform_session (
      id uuid primary key,
      token_hash text not null,
      user_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      auth_method text not null,
      remembered boolean not null default false,
      user_agent_hash text,
      created_at timestamptz not null,
      last_seen_at timestamptz not null,
      idle_expires_at timestamptz not null,
      absolute_expires_at timestamptz not null,
      revoked_at timestamptz,
      constraint platform_session_token_hash_unique unique (token_hash),
      constraint platform_session_user_fk foreign key (user_id)
        references atlas.platform_user (id) on delete restrict,
      constraint platform_session_tenant_fk foreign key (tenant_id)
        references atlas.tenant (id) on delete restrict,
      constraint platform_session_project_tenant_fk foreign key (project_id, tenant_id)
        references atlas.project (id, tenant_id) on delete restrict,
      constraint platform_session_auth_method_valid check (auth_method in ('PASSWORD', 'FEISHU')),
      constraint platform_session_token_hash_sha256 check (token_hash ~ '^[0-9a-f]{64}$'),
      constraint platform_session_user_agent_hash_sha256 check (
        user_agent_hash is null or user_agent_hash ~ '^[0-9a-f]{64}$'
      ),
      constraint platform_session_idle_expiry_valid check (
        idle_expires_at > created_at and idle_expires_at <= absolute_expires_at
      ),
      constraint platform_session_absolute_expiry_valid check (absolute_expires_at > created_at),
      constraint platform_session_revocation_valid check (
        revoked_at is null or revoked_at >= created_at
      )
    )
    """,
    """
    create unique index platform_membership_org_active_unique
      on atlas.platform_membership (tenant_id, user_id, role)
      where project_id is null and status = 'ACTIVE'
    """,
    """
    create unique index platform_membership_project_active_unique
      on atlas.platform_membership (tenant_id, project_id, user_id, role)
      where project_id is not null and status = 'ACTIVE'
    """,
    """
    create index platform_membership_user_active_idx
      on atlas.platform_membership (user_id, tenant_id, project_id)
      where status = 'ACTIVE'
    """,
    """
    create index platform_membership_project_tenant_idx
      on atlas.platform_membership (project_id, tenant_id)
      where project_id is not null
    """,
    """
    create index platform_session_user_active_idx
      on atlas.platform_session (user_id, absolute_expires_at)
      where revoked_at is null
    """,
    """
    create index platform_session_expiry_idx
      on atlas.platform_session (idle_expires_at, absolute_expires_at)
      where revoked_at is null
    """,
    """
    create trigger platform_user_set_updated_at
      before update on atlas.platform_user
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger platform_membership_set_updated_at
      before update on atlas.platform_membership
      for each row execute function atlas.set_updated_at()
    """,
    "alter table atlas.platform_membership enable row level security",
    "alter table atlas.platform_membership force row level security",
    "alter table atlas.platform_session enable row level security",
    "alter table atlas.platform_session force row level security",
    """
    create policy platform_membership_tenant_isolation on atlas.platform_membership
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy platform_session_select on atlas.platform_session
      for select
      using (
        tenant_id = atlas.current_tenant_id() or
        token_hash = atlas.current_session_hash()
      )
    """,
    """
    create policy platform_session_insert on atlas.platform_session
      for insert
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy platform_session_update on atlas.platform_session
      for update
      using (
        tenant_id = atlas.current_tenant_id() or
        token_hash = atlas.current_session_hash()
      )
      with check (
        tenant_id = atlas.current_tenant_id() or
        token_hash = atlas.current_session_hash()
      )
    """,
    "grant select, insert, update, delete on atlas.platform_user to atlas_app",
    "grant select, insert, update, delete on atlas.password_credential to atlas_app",
    "grant select, insert, update, delete on atlas.platform_membership to atlas_app",
    "revoke update, delete on atlas.platform_session from atlas_app",
    "grant select, insert on atlas.platform_session to atlas_app",
    """
    grant update (last_seen_at, idle_expires_at, revoked_at)
      on atlas.platform_session to atlas_app
    """,
    "grant execute on function atlas.current_session_hash() to atlas_app",
)


def upgrade() -> None:
    """创建平台身份表、索引、约束、RLS 与最小列权限。"""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """按依赖逆序移除平台身份基础。"""

    op.execute("drop table if exists atlas.platform_session")
    op.execute("drop table if exists atlas.platform_membership")
    op.execute("drop table if exists atlas.password_credential")
    op.execute("drop table if exists atlas.platform_user")
    op.execute("drop function if exists atlas.current_session_hash()")
