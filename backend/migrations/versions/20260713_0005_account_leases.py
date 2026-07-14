"""建立独占账号租约、Fencing 与过期回收约束。

Revision ID: 20260713_0005
Revises: 20260713_0004
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_0005"
down_revision: str | None = "20260713_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    alter table atlas.test_account
      add constraint test_account_lease_scope_unique
      unique (id, pool_id, tenant_id, project_id, environment_id)
    """,
    """
    create table atlas.account_lease (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      pool_id uuid not null,
      account_id uuid not null,
      slot_id uuid not null,
      execution_id text not null,
      worker_id text not null,
      account_handle text not null,
      fencing_token bigint not null,
      ttl_seconds integer not null,
      status text not null default 'ACTIVE',
      acquired_at timestamptz not null,
      heartbeat_at timestamptz not null,
      expires_at timestamptz not null,
      max_expires_at timestamptz not null,
      released_at timestamptz,
      release_reason text,
      revision bigint not null default 1,
      updated_at timestamptz not null default clock_timestamp(),
      constraint account_lease_pool_scope_fk
        foreign key (pool_id, tenant_id, project_id, environment_id)
        references atlas.account_pool (id, tenant_id, project_id, environment_id)
        on delete restrict,
      constraint account_lease_account_scope_fk
        foreign key (account_id, pool_id, tenant_id, project_id, environment_id)
        references atlas.test_account (
          id, pool_id, tenant_id, project_id, environment_id
        ) on delete restrict,
      constraint account_lease_slot_scope_fk
        foreign key (slot_id, account_id, tenant_id, project_id, environment_id)
        references atlas.account_slot (
          id, account_id, tenant_id, project_id, environment_id
        ) on delete restrict,
      constraint account_lease_handle_unique unique (tenant_id, account_handle),
      constraint account_lease_account_fence_unique
        unique (account_id, fencing_token),
      constraint account_lease_execution_format check (
        execution_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$'
      ),
      constraint account_lease_worker_format check (
        worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$'
      ),
      constraint account_lease_handle_format check (
        account_handle ~ '^ah_[A-Za-z0-9_-]{16,128}$'
      ),
      constraint account_lease_fence_positive check (fencing_token > 0),
      constraint account_lease_ttl_range check (ttl_seconds between 300 and 7200),
      constraint account_lease_status_valid check (
        status in ('ACTIVE', 'RELEASED', 'EXPIRED', 'REVOKED')
      ),
      constraint account_lease_release_reason_valid check (
        release_reason is null or release_reason in (
          'COMPLETED', 'CANCELLED', 'WORKER_SHUTDOWN', 'AUTH_FAILED',
          'CLEANUP_FAILED', 'MANUAL', 'TTL_EXPIRED', 'ACCOUNT_QUARANTINED',
          'ACCOUNT_SUSPENDED',
          'ACCOUNT_RETIRED', 'POOL_DISABLED', 'ROLE_DISABLED',
          'ENVIRONMENT_DISABLED'
        )
      ),
      constraint account_lease_time_order check (
        acquired_at <= heartbeat_at
        and acquired_at < expires_at
        and expires_at <= max_expires_at
      ),
      constraint account_lease_terminal_metadata check (
        (status = 'ACTIVE' and released_at is null and release_reason is null)
        or
        (status <> 'ACTIVE' and released_at is not null and release_reason is not null)
      ),
      constraint account_lease_revision_positive check (revision > 0)
    )
    """,
    """
    create unique index account_lease_one_active_per_slot
      on atlas.account_lease (slot_id)
      where status = 'ACTIVE'
    """,
    """
    create index account_lease_expiry_reaper_idx
      on atlas.account_lease (tenant_id, expires_at, id)
      where status = 'ACTIVE'
    """,
    """
    create index account_lease_execution_idx
      on atlas.account_lease (tenant_id, project_id, execution_id, acquired_at desc)
    """,
    """
    create index account_lease_account_history_idx
      on atlas.account_lease (account_id, acquired_at desc, id desc)
    """,
    """
    create function atlas.guard_account_lease_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if old.status <> 'ACTIVE' then
        raise exception 'terminal account lease is immutable';
      end if;
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id, new.pool_id,
        new.account_id, new.slot_id, new.execution_id, new.worker_id,
        new.account_handle, new.fencing_token, new.ttl_seconds,
        new.acquired_at, new.max_expires_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id, old.pool_id,
        old.account_id, old.slot_id, old.execution_id, old.worker_id,
        old.account_handle, old.fencing_token, old.ttl_seconds,
        old.acquired_at, old.max_expires_at
      ) then
        raise exception 'account lease identity and fence are immutable';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'account lease revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger account_lease_guard_update
      before update on atlas.account_lease
      for each row execute function atlas.guard_account_lease_update()
    """,
    """
    create trigger account_lease_set_updated_at
      before update on atlas.account_lease
      for each row execute function atlas.set_updated_at()
    """,
    "alter table atlas.account_lease enable row level security",
    "alter table atlas.account_lease force row level security",
    """
    create policy account_lease_tenant_isolation on atlas.account_lease
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    "grant select, insert, update on atlas.account_lease to atlas_app",
)


def upgrade() -> None:
    """创建租约事实、独占约束、热索引、更新守卫和 RLS。"""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """移除租约事实并恢复 P2-01 账号约束。"""

    op.execute("drop table if exists atlas.account_lease")
    op.execute("drop function if exists atlas.guard_account_lease_update()")
    op.execute(
        "alter table atlas.test_account "
        "drop constraint if exists test_account_lease_scope_unique"
    )
