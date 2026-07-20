"""Add the bounded latest-frame projection for DebugRun browser visibility.

Revision ID: 20260720_0045
Revises: 20260718_0044
Create Date: 2026-07-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260720_0045"
down_revision: str | None = "20260718_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.debug_live_frame (
      debug_run_id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      execution_contract_id uuid not null,
      frame_revision bigint not null,
      page_revision bigint not null,
      mime_type text not null,
      content_digest text not null,
      size_bytes integer not null,
      payload bytea not null,
      captured_at timestamptz not null,
      recorded_at timestamptz not null,
      constraint debug_live_frame_contract_scope_fk foreign key (
        execution_contract_id, debug_run_id, tenant_id, project_id, environment_id
      ) references atlas.execution_contract (
        id, debug_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint debug_live_frame_content_valid check (
        frame_revision >= 1
        and page_revision >= 1
        and mime_type in ('image/jpeg', 'image/png', 'image/webp')
        and content_digest ~ '^sha256:[0-9a-f]{64}$'
        and size_bytes between 1 and 716800
        and octet_length(payload) = size_bytes
        and isfinite(captured_at)
        and isfinite(recorded_at)
        and recorded_at >= captured_at
      )
    )
    """,
    """
    create function atlas.guard_debug_live_frame_transition()
    returns trigger
    language plpgsql
    security invoker
    set search_path = pg_catalog, atlas
    as $$
    begin
      if tg_op = 'DELETE' then
        raise exception 'Debug live frames cannot be deleted'
          using errcode = '55000';
      end if;
      if old.debug_run_id is distinct from new.debug_run_id
        or old.tenant_id is distinct from new.tenant_id
        or old.project_id is distinct from new.project_id
        or old.environment_id is distinct from new.environment_id
        or old.execution_contract_id is distinct from new.execution_contract_id
        or new.frame_revision <= old.frame_revision
        or new.captured_at < old.captured_at
      then
        raise exception 'Debug live frame scope and revision are immutable'
          using errcode = '55000';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger debug_live_frame_guard_transition
      before update or delete on atlas.debug_live_frame
      for each row execute function atlas.guard_debug_live_frame_transition()
    """,
    "alter table atlas.debug_live_frame enable row level security",
    "alter table atlas.debug_live_frame force row level security",
    """
    create policy debug_live_frame_tenant_isolation
      on atlas.debug_live_frame for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    "revoke all on atlas.debug_live_frame from atlas_app",
    "grant select, insert, update on atlas.debug_live_frame to atlas_app",
)


DOWNGRADE_STATEMENTS = (
    "revoke all on atlas.debug_live_frame from atlas_app",
    "drop policy if exists debug_live_frame_tenant_isolation on atlas.debug_live_frame",
    "drop trigger if exists debug_live_frame_guard_transition on atlas.debug_live_frame",
    "drop table if exists atlas.debug_live_frame",
    "drop function if exists atlas.guard_debug_live_frame_transition()",
)


def upgrade() -> None:
    """Install one bounded, tenant-isolated latest-frame row per DebugRun."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove the transient DebugRun frame projection."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
