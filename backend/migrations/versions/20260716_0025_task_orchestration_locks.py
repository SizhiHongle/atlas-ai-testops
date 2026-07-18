# ruff: noqa: E501
"""Add tenant-scoped Task orchestration row locking.

Revision ID: 20260716_0025
Revises: 20260716_0024
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_0025"
down_revision: str | None = "20260716_0024"
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
        raise exception 'Task orchestration lock function owner must bypass row-level security'
          using errcode = '42501';
      end if;
    end;
    $$
    """,
    """
    create function atlas.lock_task_execution_chain(
      p_task_run_id uuid,
      p_execution_unit_id uuid default null,
      p_unit_attempt_id uuid default null
    ) returns void
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_tenant_id uuid;
      stored_project_id uuid;
      stored_manifest_hash text;
      stored_unit_key text;
      stored_case_version_id uuid;
    begin
      if atlas.current_tenant_id() is null then
        raise exception 'Task execution locking requires tenant context'
          using errcode = '42501';
      end if;
      if p_unit_attempt_id is not null and p_execution_unit_id is null then
        raise exception 'UnitAttempt locking requires its ExecutionUnit identity'
          using errcode = '22023';
      end if;

      select run.tenant_id, run.project_id, run.manifest_hash
      into stored_tenant_id, stored_project_id, stored_manifest_hash
      from atlas.task_run run
      where run.id = p_task_run_id
        and run.tenant_id = atlas.current_tenant_id()
        and run.materialization_state = 'SEALED'
        and not run.legacy_unsealed
      for update;
      if not found then
        raise exception 'Task execution chain is missing from the current sealed tenant scope'
          using errcode = 'P0002';
      end if;

      if p_execution_unit_id is not null then
        select unit.unit_key, unit.case_version_id
        into stored_unit_key, stored_case_version_id
        from atlas.execution_unit unit
        where unit.id = p_execution_unit_id
          and unit.task_run_id = p_task_run_id
          and unit.tenant_id = stored_tenant_id
          and unit.project_id = stored_project_id
          and unit.manifest_hash = stored_manifest_hash
        for update;
        if not found then
          raise exception 'Task execution chain is missing from the current sealed tenant scope'
            using errcode = 'P0002';
        end if;
      end if;

      if p_unit_attempt_id is not null then
        perform 1
        from atlas.unit_attempt attempt
        where attempt.id = p_unit_attempt_id
          and attempt.execution_unit_id = p_execution_unit_id
          and attempt.task_run_id = p_task_run_id
          and attempt.tenant_id = stored_tenant_id
          and attempt.project_id = stored_project_id
          and attempt.manifest_hash = stored_manifest_hash
          and attempt.unit_key = stored_unit_key
          and attempt.case_version_id = stored_case_version_id
        for update;
        if not found then
          raise exception 'Task execution chain is missing from the current sealed tenant scope'
            using errcode = 'P0002';
        end if;
      end if;
    end;
    $$
    """,
    "revoke all on function atlas.lock_task_execution_chain(uuid, uuid, uuid) from public, atlas_dispatcher",
    "grant execute on function atlas.lock_task_execution_chain(uuid, uuid, uuid) to atlas_app",
)


DOWNGRADE_STATEMENTS = (
    "revoke all on function atlas.lock_task_execution_chain(uuid, uuid, uuid) from atlas_app",
    "drop function if exists atlas.lock_task_execution_chain(uuid, uuid, uuid)",
)


def upgrade() -> None:
    """Add one least-privilege Run-to-Attempt locking boundary."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove the orchestration-only locking boundary."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
