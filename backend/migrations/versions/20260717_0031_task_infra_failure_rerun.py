"""Add database-proven child TaskRuns for manual infrastructure-failure reruns.

Revision ID: 20260717_0031
Revises: 20260717_0030
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_0031"
down_revision: str | None = "20260717_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    "alter table atlas.task_run add column rerun_selection_mode text",
    """
    alter table atlas.task_run
    add constraint task_run_rerun_selection_mode_valid check (
      rerun_selection_mode is null
      or (
        rerun_of_task_run_id is not null
        and rerun_selection_mode = 'INFRA_FAILURES'
      )
    )
    """,
    """
    create function atlas.guard_task_run_rerun_selection_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if new.rerun_selection_mode is distinct from old.rerun_selection_mode then
        raise exception 'task run rerun selection mode is immutable';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_run_rerun_selection_guard_update
    before update of rerun_selection_mode on atlas.task_run
    for each row execute function atlas.guard_task_run_rerun_selection_update()
    """,
    """
    create function atlas.guard_task_run_infra_rerun_manifest_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      child_run atlas.task_run%rowtype;
      parent_run atlas.task_run%rowtype;
      parent_manifest atlas.task_run_manifest%rowtype;
      expected_units jsonb;
    begin
      select * into child_run
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
      for share;
      if not found then
        raise exception 'rerun manifest requires its child TaskRun';
      end if;
      if child_run.rerun_selection_mode is null then
        return new;
      end if;
      if child_run.rerun_selection_mode <> 'INFRA_FAILURES'
        or child_run.rerun_of_task_run_id is null
        or child_run.trigger_source <> 'API'
        or child_run.trigger_fingerprint not like 'api:infra-rerun:%'
        or child_run.materialization_state <> 'MATERIALIZING'
        or row(child_run.lifecycle, child_run.quality)
          is distinct from row('QUEUED'::text, 'PENDING'::text)
      then
        raise exception 'infrastructure rerun child TaskRun identity is invalid';
      end if;

      select * into parent_run
      from atlas.task_run run
      where run.id = child_run.rerun_of_task_run_id
        and run.tenant_id = child_run.tenant_id
        and run.project_id = child_run.project_id
      for share;
      if not found
        or parent_run.materialization_state <> 'SEALED'
        or parent_run.legacy_unsealed
        or parent_run.lifecycle <> 'CLOSED'
      then
        raise exception 'infrastructure rerun requires one closed sealed parent TaskRun';
      end if;

      select * into parent_manifest
      from atlas.task_run_manifest manifest
      where manifest.task_run_id = parent_run.id
        and manifest.tenant_id = parent_run.tenant_id
        and manifest.project_id = parent_run.project_id;
      if not found
        or new.task_plan_version_id <> parent_manifest.task_plan_version_id
        or new.schema_version <> parent_manifest.schema_version
        or new.iteration_id is distinct from parent_manifest.iteration_id
        or new.policy_digests is distinct from parent_manifest.policy_digests
        or new.retry_policy is distinct from parent_manifest.retry_policy
        or new.compiler_version <> parent_manifest.compiler_version
      then
        raise exception 'infrastructure rerun must preserve parent frozen configuration';
      end if;

      select jsonb_agg(
        jsonb_build_object(
          'ordinal', selected.ordinal,
          'unitKey', selected.unit_key,
          'caseVersionId', selected.case_version_id::text,
          'executionProfileVersionId',
            selected.execution_profile_version_id::text,
          'fixtureBlueprintVersionId',
            selected.fixture_blueprint_version_id::text,
          'identityProfileVersionId',
            selected.identity_profile_version_id::text,
          'environmentId', selected.environment_id::text,
          'browserProfileVersionId',
            selected.browser_profile_version_id::text,
          'dataProfileVersionId', selected.data_profile_version_id::text,
          'parameterDigest', selected.parameter_digest,
          'dependencyDigest', selected.dependency_digest
        )
        order by selected.ordinal
      )
      into expected_units
      from (
        select
          row_number() over (order by unit.unit_key)::integer as ordinal,
          unit.unit_key,
          unit.case_version_id,
          unit.execution_profile_version_id,
          unit.fixture_blueprint_version_id,
          unit.identity_profile_version_id,
          unit.environment_id,
          unit.browser_profile_version_id,
          unit.data_profile_version_id,
          unit.parameter_digest,
          unit.dependency_digest
        from atlas.execution_unit unit
        where unit.task_run_id = parent_run.id
          and unit.tenant_id = parent_run.tenant_id
          and unit.project_id = parent_run.project_id
          and unit.lifecycle = 'CLOSED'
          and unit.quality = 'INFRA_ERROR'
      ) selected;
      if expected_units is null or new.units is distinct from expected_units then
        raise exception
          'infrastructure rerun manifest must contain every and only failed infrastructure Unit';
      end if;
      return new;
    end;
    $$
    """,
    """
    revoke all on function atlas.guard_task_run_infra_rerun_manifest_insert()
      from public, atlas_app, atlas_dispatcher
    """,
    """
    create trigger task_run_manifest_rerun_guard_insert
    before insert on atlas.task_run_manifest
    for each row execute function atlas.guard_task_run_infra_rerun_manifest_insert()
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (
        select 1
        from atlas.task_run
        where rerun_selection_mode is not null
      ) then
        raise exception
          'cannot downgrade while infrastructure rerun TaskRun facts exist'
          using errcode = '55000';
      end if;
    end;
    $$
    """,
    "drop trigger task_run_manifest_rerun_guard_insert on atlas.task_run_manifest",
    "drop function atlas.guard_task_run_infra_rerun_manifest_insert()",
    "drop trigger task_run_rerun_selection_guard_update on atlas.task_run",
    "drop function atlas.guard_task_run_rerun_selection_update()",
    "alter table atlas.task_run drop constraint task_run_rerun_selection_mode_valid",
    "alter table atlas.task_run drop column rerun_selection_mode",
)


def upgrade() -> None:
    """Require PostgreSQL to prove every manual infrastructure rerun selection."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove rerun support only when no child TaskRun fact would be lost."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
