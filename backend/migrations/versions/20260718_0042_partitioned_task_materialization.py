"""Add recoverable TaskRun materialization partitions and widen the sealed limit.

Revision ID: 20260718_0042
Revises: 20260718_0041
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0042"
down_revision: str | None = "20260718_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _manifest_units_validator(maximum_units: int) -> str:
    return f"""
    create or replace function atlas.task_manifest_units_v2_valid(value jsonb)
    returns boolean
    language plpgsql
    immutable
    set search_path = pg_catalog
    as $$
    declare
      unit jsonb;
      position bigint;
      previous_key text;
      current_key text;
      uuid_field text;
    begin
      if jsonb_typeof(value) is distinct from 'array'
        or jsonb_array_length(value) not between 1 and {maximum_units}
      then
        return false;
      end if;

      for unit, position in
        select item.value, item.ordinality
        from jsonb_array_elements(value) with ordinality item(value, ordinality)
      loop
        if jsonb_typeof(unit) is distinct from 'object'
          or atlas.task_json_object_size(unit) <> 11
          or not (unit ?& array[
            'ordinal', 'unitKey', 'caseVersionId', 'executionProfileVersionId',
            'fixtureBlueprintVersionId', 'identityProfileVersionId', 'environmentId',
            'browserProfileVersionId', 'dataProfileVersionId', 'parameterDigest',
            'dependencyDigest'
          ])
          or unit ->> 'ordinal' is distinct from position::text
          or unit ->> 'unitKey' is null
          or unit ->> 'unitKey' !~ '^sha256:[0-9a-f]{{64}}$'
          or unit ->> 'parameterDigest' is null
          or unit ->> 'parameterDigest' !~ '^sha256:[0-9a-f]{{64}}$'
          or unit ->> 'dependencyDigest' is null
          or unit ->> 'dependencyDigest' !~ '^sha256:[0-9a-f]{{64}}$'
        then
          return false;
        end if;

        foreach uuid_field in array array[
          'caseVersionId', 'executionProfileVersionId', 'fixtureBlueprintVersionId',
          'identityProfileVersionId', 'environmentId', 'browserProfileVersionId',
          'dataProfileVersionId'
        ]
        loop
          if unit ->> uuid_field is null
            or unit ->> uuid_field
              !~ '{_UUID_PATTERN}'
          then
            return false;
          end if;
        end loop;

        current_key := unit ->> 'unitKey';
        if previous_key is not null and current_key <= previous_key then
          return false;
        end if;
        previous_key := current_key;
      end loop;
      return true;
    exception
      when others then return false;
    end;
    $$
    """


def _seal_function(maximum_units: int, *, require_partitions: bool) -> str:
    partition_guard = ""
    if require_partitions:
        partition_guard = """
      if stored_manifest.unit_count > 64 then
        if not exists (
          select 1
          from atlas.task_run_materialization_partition partition
          where partition.task_run_id = stored_run.id
        ) or exists (
          select 1
          from atlas.task_run_materialization_partition partition
          where partition.task_run_id = stored_run.id
            and (
              partition.status <> 'COMPLETED'
              or partition.first_ordinal <> partition.partition_index * 64 + 1
              or partition.last_ordinal <> least(
                (partition.partition_index + 1) * 64,
                stored_manifest.unit_count
              )
              or partition.materialized_unit_count
                <> partition.last_ordinal - partition.first_ordinal + 1
              or partition.materialized_first_attempt_count
                <> partition.materialized_unit_count
            )
        ) or (
          select count(*)::integer
          from atlas.task_run_materialization_partition partition
          where partition.task_run_id = stored_run.id
        ) <> ceiling(stored_manifest.unit_count / 64.0)::integer
        then
          raise exception 'task run seal requires every materialization partition checkpoint'
            using errcode = '55000';
        end if;
      end if;
        """
    return f"""
    create or replace function atlas.seal_task_run_materialization(
      p_task_run_id uuid,
      p_expected_revision bigint
    ) returns setof atlas.task_run
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_run atlas.task_run%rowtype;
      stored_manifest atlas.task_run_manifest%rowtype;
      updated_run atlas.task_run%rowtype;
      stored_unit_count integer;
      stored_attempt_count integer;
      stored_first_attempt_count integer;
      admissible_unit_count integer;
      expected_manifest_hash text;
      expected_request_digest text;
    begin
      if atlas.current_tenant_id() is null then
        raise exception 'task materialization requires tenant context'
          using errcode = '42501';
      end if;
      select * into stored_run
      from atlas.task_run run
      where run.id = p_task_run_id
        and run.tenant_id = atlas.current_tenant_id()
      for update;
      if not found then
        raise exception 'task run is missing from the current tenant'
          using errcode = 'P0002';
      end if;
      if stored_run.revision <> p_expected_revision then
        raise exception 'task run revision conflict'
          using errcode = '40001';
      end if;
      if stored_run.legacy_unsealed
        or stored_run.materialization_state <> 'MATERIALIZING'
        or stored_run.request_digest is null
      then
        raise exception 'task run is not eligible for materialization seal'
          using errcode = '55000';
      end if;

      select * into stored_manifest
      from atlas.task_run_manifest manifest
      where manifest.task_run_id = stored_run.id
        and manifest.tenant_id = stored_run.tenant_id
        and manifest.project_id = stored_run.project_id;
      if not found or stored_manifest.unit_count not between 1 and {maximum_units} then
        raise exception 'task run seal requires one bounded manifest'
          using errcode = '55000';
      end if;
      {partition_guard}

      expected_manifest_hash := atlas.task_sha256_json(jsonb_build_object(
        'schemaVersion', stored_manifest.schema_version,
        'taskRunId', stored_manifest.task_run_id::text,
        'taskPlanVersionId', stored_manifest.task_plan_version_id::text,
        'triggerSource', stored_manifest.trigger_source,
        'triggerFingerprint', stored_manifest.trigger_fingerprint,
        'tenantId', stored_manifest.tenant_id::text,
        'projectId', stored_manifest.project_id::text,
        'iterationId', stored_manifest.iteration_id,
        'units', stored_manifest.units,
        'policyDigests', stored_manifest.policy_digests,
        'compilerVersion', stored_manifest.compiler_version
      ));
      expected_request_digest := atlas.task_sha256_json(jsonb_build_object(
        'schemaVersion', 'atlas.task-run-request/0.1',
        'tenantId', stored_manifest.tenant_id::text,
        'projectId', stored_manifest.project_id::text,
        'taskPlanVersionId', stored_manifest.task_plan_version_id::text,
        'triggerSource', stored_manifest.trigger_source,
        'triggerFingerprint', stored_manifest.trigger_fingerprint,
        'iterationId', stored_manifest.iteration_id,
        'units', stored_manifest.units,
        'policyDigests', stored_manifest.policy_digests,
        'compilerVersion', stored_manifest.compiler_version
      ));
      if row(stored_run.manifest_hash, stored_manifest.manifest_hash)
        is distinct from row(expected_manifest_hash, expected_manifest_hash)
        or stored_run.request_digest is distinct from expected_request_digest
      then
        raise exception 'task run seal rejected non-canonical digests'
          using errcode = '55000';
      end if;

      select count(*)::integer into stored_unit_count
      from atlas.execution_unit unit
      where unit.task_run_id = stored_run.id;
      select
        count(*)::integer,
        count(*) filter (where attempt.attempt_number = 1)::integer
      into stored_attempt_count, stored_first_attempt_count
      from atlas.unit_attempt attempt
      where attempt.task_run_id = stored_run.id;
      if stored_unit_count <> stored_manifest.unit_count
        or stored_attempt_count <> stored_manifest.unit_count
        or stored_first_attempt_count <> stored_manifest.unit_count
      then
        raise exception 'task run seal requires every Unit and exactly its first Attempt'
          using errcode = '55000';
      end if;

      select count(*)::integer into admissible_unit_count
      from atlas.execution_unit unit
      join atlas.case_version case_version
        on case_version.id = unit.case_version_id
       and case_version.tenant_id = unit.tenant_id
       and case_version.project_id = unit.project_id
       and case_version.status = 'PUBLISHED'
      join atlas.execution_profile_version execution_profile
        on execution_profile.id = unit.execution_profile_version_id
       and execution_profile.case_version_id = unit.case_version_id
       and execution_profile.tenant_id = unit.tenant_id
       and execution_profile.project_id = unit.project_id
       and execution_profile.status = 'PUBLISHED'
       and execution_profile.case_content_digest = case_version.content_digest
      join atlas.identity_profile_version identity_profile
        on identity_profile.id = unit.identity_profile_version_id
       and identity_profile.case_version_id = unit.case_version_id
       and identity_profile.tenant_id = unit.tenant_id
       and identity_profile.project_id = unit.project_id
       and identity_profile.status = 'PUBLISHED'
       and identity_profile.case_content_digest = case_version.content_digest
       and identity_profile.content_digest =
         atlas.task_identity_profile_content_digest(identity_profile.id)
      join atlas.browser_profile_version browser_profile
        on browser_profile.id = unit.browser_profile_version_id
       and browser_profile.tenant_id = unit.tenant_id
       and browser_profile.project_id = unit.project_id
       and browser_profile.status = 'PUBLISHED'
      join atlas.data_profile_version data_profile
        on data_profile.id = unit.data_profile_version_id
       and data_profile.blueprint_version_id = unit.fixture_blueprint_version_id
       and data_profile.tenant_id = unit.tenant_id
       and data_profile.project_id = unit.project_id
       and data_profile.status = 'PUBLISHED'
      join atlas.data_blueprint_version blueprint
        on blueprint.id = unit.fixture_blueprint_version_id
       and blueprint.tenant_id = unit.tenant_id
       and blueprint.project_id = unit.project_id
       and blueprint.status = 'PUBLISHED'
       and blueprint.content_digest = data_profile.blueprint_content_digest
       and blueprint.plan_digest = data_profile.plan_digest
      join atlas.environment environment
        on environment.id = unit.environment_id
       and environment.tenant_id = unit.tenant_id
       and environment.project_id = unit.project_id
       and environment.status = 'ACTIVE'
       and environment.kind in ('TEST', 'STAGING')
      where unit.task_run_id = stored_run.id;
      if admissible_unit_count <> stored_manifest.unit_count then
        raise exception 'task run seal rejected stale or non-published dependencies'
          using errcode = '55000';
      end if;

      if exists (
        select 1
        from atlas.execution_unit unit
        join atlas.identity_profile_actor_binding binding
          on binding.identity_profile_version_id = unit.identity_profile_version_id
        left join atlas.test_role role
          on role.id = binding.role_id
         and role.tenant_id = binding.tenant_id
         and role.project_id = binding.project_id
        where unit.task_run_id = stored_run.id
          and (
            role.id is null
            or role.status <> 'ACTIVE'
            or row(role.role_key, role.revision, role.capabilities)
              is distinct from row(
                binding.role_key, binding.role_revision, binding.capabilities
              )
          )
      ) then
        raise exception 'task run seal rejected stale Identity role bindings'
          using errcode = '55000';
      end if;

      if not exists (
        select 1
        from atlas.task_workflow_identity_registry identity
        where identity.namespace = stored_run.temporal_namespace
          and identity.workflow_id = stored_run.temporal_workflow_id
          and identity.owner_kind = 'TASK_RUN'
          and identity.owner_id = stored_run.id
          and identity.tenant_id = stored_run.tenant_id
          and identity.project_id = stored_run.project_id
          and identity.task_run_id = stored_run.id
          and identity.request_digest = stored_run.request_digest
      ) then
        raise exception 'task run seal requires its exact Workflow identity registry row'
          using errcode = '55000';
      end if;

      update atlas.task_run run
      set
        materialization_state = 'SEALED',
        materialized_unit_count = stored_unit_count,
        materialized_first_attempt_count = stored_first_attempt_count,
        materialization_sealed_at = clock_timestamp(),
        revision = run.revision + 1
      where run.id = stored_run.id
      returning * into updated_run;

      insert into atlas.task_workflow_start_intent (
        id, tenant_id, project_id, task_run_id,
        owner_kind, owner_id, namespace, workflow_id,
        request_digest, workflow_type, task_queue, status, created_at
      ) values (
        updated_run.id,
        updated_run.tenant_id,
        updated_run.project_id,
        updated_run.id,
        'TASK_RUN',
        updated_run.id,
        updated_run.temporal_namespace,
        updated_run.temporal_workflow_id,
        updated_run.request_digest,
        'AtlasTaskRunWorkflow',
        'atlas-task-run',
        'PENDING',
        updated_run.materialization_sealed_at
      );
      return next updated_run;
      return;
    end;
    $$
    """


UPGRADE_STATEMENTS = (
    """
    do $$
    begin
      if not exists (
        select 1 from pg_roles
        where rolname = 'atlas_dispatcher'
          and rolcanlogin
          and not rolsuper
          and not rolbypassrls
      ) then
        raise exception
          'atlas_dispatcher LOGIN NOSUPERUSER NOBYPASSRLS role is required'
          using errcode = '55000';
      end if;
    end;
    $$
    """,
    """
    create table atlas.task_run_materialization_partition (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      manifest_hash text not null,
      partition_index integer not null,
      first_ordinal integer not null,
      last_ordinal integer not null,
      status text not null default 'PENDING',
      available_at timestamptz not null,
      claim_token uuid,
      claimed_by text,
      claimed_at timestamptz,
      claim_expires_at timestamptz,
      materialization_attempts integer not null default 0,
      materialized_unit_count integer,
      materialized_first_attempt_count integer,
      last_error_code text,
      last_error_at timestamptz,
      completed_at timestamptz,
      failed_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint task_run_materialization_partition_manifest_fk foreign key (
        task_run_id, tenant_id, project_id, manifest_hash
      ) references atlas.task_run_manifest (
        task_run_id, tenant_id, project_id, manifest_hash
      ) on delete restrict,
      constraint task_run_materialization_partition_run_index_unique unique (
        task_run_id, partition_index
      ),
      constraint task_run_materialization_partition_run_range_unique unique (
        task_run_id, first_ordinal, last_ordinal
      ),
      constraint task_run_materialization_partition_scope_unique unique (
        id, tenant_id, project_id, task_run_id
      ),
      constraint task_run_materialization_partition_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint task_run_materialization_partition_range_valid check (
        partition_index between 0 and 1562
        and first_ordinal = partition_index * 64 + 1
        and last_ordinal between first_ordinal and first_ordinal + 63
        and last_ordinal <= 100000
      ),
      constraint task_run_materialization_partition_status_valid check (
        status in ('PENDING', 'CLAIMED', 'RETRY_WAIT', 'COMPLETED', 'FAILED')
      ),
      constraint task_run_materialization_partition_attempts_valid check (
        materialization_attempts between 0 and 64 and revision > 0
      ),
      constraint task_run_materialization_partition_error_valid check (
        last_error_code is null
        or last_error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
      ),
      constraint task_run_materialization_partition_state_valid check (
        (
          status in ('PENDING', 'RETRY_WAIT')
          and claim_token is null
          and claimed_by is null
          and claimed_at is null
          and claim_expires_at is null
          and materialized_unit_count is null
          and materialized_first_attempt_count is null
          and completed_at is null
          and failed_at is null
        ) or (
          status = 'CLAIMED'
          and claim_token is not null
          and claimed_by is not null
          and claimed_at is not null
          and claim_expires_at > claimed_at
          and materialized_unit_count is null
          and materialized_first_attempt_count is null
          and completed_at is null
          and failed_at is null
        ) or (
          status = 'COMPLETED'
          and claim_token is null
          and claimed_by is null
          and claimed_at is null
          and claim_expires_at is null
          and materialized_unit_count = last_ordinal - first_ordinal + 1
          and materialized_first_attempt_count = materialized_unit_count
          and completed_at is not null
          and failed_at is null
        ) or (
          status = 'FAILED'
          and claim_token is null
          and claimed_by is null
          and claimed_at is null
          and claim_expires_at is null
          and materialized_unit_count is null
          and materialized_first_attempt_count is null
          and completed_at is null
          and failed_at is not null
        )
      ),
      constraint task_run_materialization_partition_time_valid check (
        available_at >= created_at
        and updated_at >= created_at
        and (last_error_at is null or last_error_at >= created_at)
        and (completed_at is null or completed_at >= created_at)
        and (failed_at is null or failed_at >= created_at)
      )
    )
    """,
    """
    create function atlas.guard_task_run_materialization_partition_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_unit_count integer;
      stored_state text;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'materialization partition insertion requires exact tenant context'
          using errcode = '42501';
      end if;
      select manifest.unit_count, run.materialization_state
      into stored_unit_count, stored_state
      from atlas.task_run_manifest manifest
      join atlas.task_run run on run.id = manifest.task_run_id
      where manifest.task_run_id = new.task_run_id
        and manifest.tenant_id = new.tenant_id
        and manifest.project_id = new.project_id
        and manifest.manifest_hash = new.manifest_hash
      for share of run;
      if not found
        or stored_state <> 'MATERIALIZING'
        or stored_unit_count <= 64
        or new.first_ordinal <> new.partition_index * 64 + 1
        or new.last_ordinal <> least(
          (new.partition_index + 1) * 64,
          stored_unit_count
        )
        or new.status <> 'PENDING'
        or new.materialization_attempts <> 0
        or new.revision <> 1
      then
        raise exception 'materialization partition does not match its large Run Manifest'
          using errcode = '55000';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_run_materialization_partition_update()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'materialization partition updates require atlas_dispatcher'
          using errcode = '42501';
      end if;
      if row(
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.manifest_hash, new.partition_index, new.first_ordinal,
        new.last_ordinal, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.manifest_hash, old.partition_index, old.first_ordinal,
        old.last_ordinal, old.created_at
      ) or new.revision <> old.revision + 1
      then
        raise exception 'materialization partition identity is immutable'
          using errcode = '55000';
      end if;
      if not (
        (
          old.status in ('PENDING', 'RETRY_WAIT')
          and new.status = 'CLAIMED'
        ) or (
          old.status = 'CLAIMED'
          and (
            (old.claim_expires_at <= clock_timestamp() and new.status = 'CLAIMED')
            or new.status in ('RETRY_WAIT', 'COMPLETED', 'FAILED')
          )
        )
      ) then
        raise exception 'materialization partition state transition is invalid'
          using errcode = '55000';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_run_materialization_partition_guard_insert
      before insert on atlas.task_run_materialization_partition
      for each row execute function
        atlas.guard_task_run_materialization_partition_insert()
    """,
    """
    create trigger task_run_materialization_partition_guard_update
      before update or delete on atlas.task_run_materialization_partition
      for each row execute function
        atlas.guard_task_run_materialization_partition_update()
    """,
    "alter table atlas.task_run_materialization_partition enable row level security",
    "alter table atlas.task_run_materialization_partition force row level security",
    """
    create policy task_run_materialization_partition_tenant_policy
      on atlas.task_run_materialization_partition
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy task_run_materialization_partition_dispatcher_policy
      on atlas.task_run_materialization_partition
      using (session_user = 'atlas_dispatcher')
      with check (session_user = 'atlas_dispatcher')
    """,
    """
    create index task_run_materialization_partition_claim_idx
      on atlas.task_run_materialization_partition (
        status, available_at, claim_expires_at, created_at, id
      ) where status in ('PENDING', 'RETRY_WAIT', 'CLAIMED')
    """,
    """
    create index task_run_materialization_partition_run_status_idx
      on atlas.task_run_materialization_partition (
        task_run_id, status, partition_index
      )
    """,
    _manifest_units_validator(100000),
    "alter table atlas.task_run_manifest "
    "drop constraint task_run_manifest_units_v2_valid",
    """
    alter table atlas.task_run_manifest
      add constraint task_run_manifest_units_v2_valid check (
        atlas.task_manifest_units_v2_valid(units)
        and unit_count = jsonb_array_length(units)
        and unit_count between 1 and 100000
      ) not valid
    """,
    "alter table atlas.task_run_manifest "
    "validate constraint task_run_manifest_units_v2_valid",
    "alter table atlas.task_run drop constraint task_run_materialization_valid",
    """
    alter table atlas.task_run
      add constraint task_run_materialization_valid check (
        materialization_state in ('MATERIALIZING', 'SEALED')
        and (
          (
            materialization_state = 'MATERIALIZING'
            and materialized_unit_count is null
            and materialized_first_attempt_count is null
            and materialization_sealed_at is null
          ) or (
            materialization_state = 'SEALED'
            and not legacy_unsealed
            and request_digest is not null
            and materialized_unit_count between 1 and 100000
            and materialized_first_attempt_count = materialized_unit_count
            and materialization_sealed_at between created_at and updated_at
          )
        )
      ) not valid
    """,
    "alter table atlas.task_run validate constraint task_run_materialization_valid",
    _seal_function(100000, require_partitions=True),
    """
    create function atlas.claim_task_run_materialization_partitions(
      p_claimed_by text,
      p_limit integer,
      p_lease_seconds integer
    ) returns table (
      id uuid,
      tenant_id uuid,
      project_id uuid,
      task_run_id uuid,
      manifest_hash text,
      partition_index integer,
      first_ordinal integer,
      last_ordinal integer,
      status text,
      claim_token uuid,
      revision bigint,
      materialization_attempts integer,
      claim_expires_at timestamptz,
      created_at timestamptz
    )
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      claimed_at_value timestamptz := clock_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'materialization partition claim requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_claimed_by is null
        or p_claimed_by !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        or p_limit is null
        or p_limit not between 1 and 100
        or p_lease_seconds is null
        or p_lease_seconds not between 1 and 900
      then
        raise exception 'materialization partition claim input is invalid'
          using errcode = '22023';
      end if;

      return query
      with candidates as (
        select partition.id
        from atlas.task_run_materialization_partition partition
        where (
          partition.status in ('PENDING', 'RETRY_WAIT')
          and partition.available_at <= claimed_at_value
        ) or (
          partition.status = 'CLAIMED'
          and partition.claim_expires_at <= claimed_at_value
        )
        order by
          case
            when partition.status = 'CLAIMED' then partition.claim_expires_at
            else partition.available_at
          end,
          partition.created_at,
          partition.id
        limit p_limit
        for update skip locked
      ), claimed as (
        update atlas.task_run_materialization_partition partition
        set
          status = 'CLAIMED',
          claim_token = gen_random_uuid(),
          claimed_by = p_claimed_by,
          claimed_at = claimed_at_value,
          claim_expires_at = claimed_at_value
            + make_interval(secs => p_lease_seconds),
          materialization_attempts = partition.materialization_attempts + 1,
          revision = partition.revision + 1,
          updated_at = claimed_at_value
        from candidates
        where partition.id = candidates.id
        returning partition.*
      )
      select
        claimed.id,
        claimed.tenant_id,
        claimed.project_id,
        claimed.task_run_id,
        claimed.manifest_hash,
        claimed.partition_index,
        claimed.first_ordinal,
        claimed.last_ordinal,
        claimed.status,
        claimed.claim_token,
        claimed.revision,
        claimed.materialization_attempts,
        claimed.claim_expires_at,
        claimed.created_at
      from claimed
      order by claimed.created_at, claimed.id;
    end;
    $$
    """,
    """
    create function atlas.complete_task_run_materialization_partition(
      p_partition_id uuid,
      p_claim_token uuid,
      p_expected_revision bigint,
      p_claimed_by text
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_partition atlas.task_run_materialization_partition%rowtype;
      stored_run atlas.task_run%rowtype;
      stored_manifest atlas.task_run_manifest%rowtype;
      manifest_unit jsonb;
      unit_position bigint;
      unit_id uuid;
      attempt_id uuid;
      materialized_at_value timestamptz := clock_timestamp();
      expected_count integer;
      sealed_run atlas.task_run%rowtype;
      did_seal boolean := false;
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'materialization partition completion requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      select * into stored_partition
      from atlas.task_run_materialization_partition partition
      where partition.id = p_partition_id
      for update;
      if not found
        or stored_partition.status <> 'CLAIMED'
        or stored_partition.claim_token is distinct from p_claim_token
        or stored_partition.revision <> p_expected_revision
        or stored_partition.claimed_by is distinct from p_claimed_by
        or stored_partition.claim_expires_at <= materialized_at_value
      then
        return null;
      end if;

      perform set_config(
        'atlas.tenant_id',
        stored_partition.tenant_id::text,
        true
      );
      select * into stored_run
      from atlas.task_run run
      where run.id = stored_partition.task_run_id
        and run.tenant_id = stored_partition.tenant_id
        and run.project_id = stored_partition.project_id
      for update;
      select * into stored_manifest
      from atlas.task_run_manifest manifest
      where manifest.task_run_id = stored_partition.task_run_id
        and manifest.tenant_id = stored_partition.tenant_id
        and manifest.project_id = stored_partition.project_id
        and manifest.manifest_hash = stored_partition.manifest_hash;
      expected_count :=
        stored_partition.last_ordinal - stored_partition.first_ordinal + 1;
      if stored_run.id is null
        or stored_run.materialization_state <> 'MATERIALIZING'
        or stored_run.legacy_unsealed
        or stored_manifest.task_run_id is null
        or stored_manifest.unit_count <= 64
        or stored_partition.last_ordinal <> least(
          (stored_partition.partition_index + 1) * 64,
          stored_manifest.unit_count
        )
        or exists (
          select 1
          from atlas.execution_unit unit
          where unit.task_run_id = stored_run.id
            and unit.ordinal between
              stored_partition.first_ordinal and stored_partition.last_ordinal
        )
      then
        raise exception 'materialization partition facts are not eligible'
          using errcode = '55000';
      end if;

      for manifest_unit, unit_position in
        select item.value, item.ordinality
        from jsonb_array_elements(stored_manifest.units)
          with ordinality item(value, ordinality)
        where item.ordinality between
          stored_partition.first_ordinal and stored_partition.last_ordinal
        order by item.ordinality
      loop
        unit_id := gen_random_uuid();
        attempt_id := gen_random_uuid();
        insert into atlas.execution_unit (
          id, tenant_id, project_id, task_run_id, manifest_hash, unit_key,
          ordinal, case_version_id, execution_profile_version_id,
          fixture_blueprint_version_id, identity_profile_version_id,
          environment_id, browser_profile_version_id,
          data_profile_version_id, parameter_digest, dependency_digest,
          lifecycle, quality, hygiene, revision, created_at, updated_at
        ) values (
          unit_id,
          stored_run.tenant_id,
          stored_run.project_id,
          stored_run.id,
          stored_run.manifest_hash,
          manifest_unit ->> 'unitKey',
          unit_position::integer,
          (manifest_unit ->> 'caseVersionId')::uuid,
          (manifest_unit ->> 'executionProfileVersionId')::uuid,
          (manifest_unit ->> 'fixtureBlueprintVersionId')::uuid,
          (manifest_unit ->> 'identityProfileVersionId')::uuid,
          (manifest_unit ->> 'environmentId')::uuid,
          (manifest_unit ->> 'browserProfileVersionId')::uuid,
          (manifest_unit ->> 'dataProfileVersionId')::uuid,
          manifest_unit ->> 'parameterDigest',
          manifest_unit ->> 'dependencyDigest',
          'QUEUED',
          'PENDING',
          'PENDING',
          1,
          materialized_at_value,
          materialized_at_value
        );
        insert into atlas.unit_attempt (
          id, tenant_id, project_id, task_run_id, execution_unit_id,
          manifest_hash, unit_key, case_version_id, attempt_number,
          lifecycle, quality, hygiene, temporal_namespace,
          temporal_workflow_id, queued_at, execution_deadline,
          revision, created_at, updated_at
        ) values (
          attempt_id,
          stored_run.tenant_id,
          stored_run.project_id,
          stored_run.id,
          unit_id,
          stored_run.manifest_hash,
          manifest_unit ->> 'unitKey',
          (manifest_unit ->> 'caseVersionId')::uuid,
          1,
          'QUEUED',
          'PENDING',
          'PENDING',
          stored_run.temporal_namespace,
          'atlas-task/attempt/'
            || replace(stored_run.tenant_id::text, '-', '')
            || '/'
            || replace(attempt_id::text, '-', ''),
          materialized_at_value,
          materialized_at_value + interval '30 days',
          1,
          materialized_at_value,
          materialized_at_value
        );
      end loop;

      if (
        select count(*)::integer
        from atlas.execution_unit unit
        where unit.task_run_id = stored_run.id
          and unit.ordinal between
            stored_partition.first_ordinal and stored_partition.last_ordinal
      ) <> expected_count then
        raise exception 'materialization partition did not create exact Unit coverage'
          using errcode = '55000';
      end if;

      update atlas.task_run_materialization_partition partition
      set
        status = 'COMPLETED',
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        materialized_unit_count = expected_count,
        materialized_first_attempt_count = expected_count,
        completed_at = materialized_at_value,
        revision = partition.revision + 1,
        updated_at = materialized_at_value
      where partition.id = stored_partition.id;

      if not exists (
        select 1
        from atlas.task_run_materialization_partition partition
        where partition.task_run_id = stored_run.id
          and partition.status <> 'COMPLETED'
      ) then
        select * into sealed_run
        from atlas.seal_task_run_materialization(
          stored_run.id,
          stored_run.revision
        );
        did_seal := sealed_run.id is not null;
      end if;
      return did_seal;
    end;
    $$
    """,
    """
    create function atlas.retry_task_run_materialization_partition(
      p_partition_id uuid,
      p_claim_token uuid,
      p_expected_revision bigint,
      p_claimed_by text,
      p_error_code text,
      p_retry_delay_ms integer
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      affected_count integer;
      failed_at_value timestamptz := clock_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'materialization partition retry requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_error_code is null
        or p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$'
        or p_retry_delay_ms is null
        or p_retry_delay_ms not between 100 and 3600000
      then
        raise exception 'materialization partition retry input is invalid'
          using errcode = '22023';
      end if;
      update atlas.task_run_materialization_partition partition
      set
        status = 'RETRY_WAIT',
        available_at = failed_at_value
          + make_interval(secs => p_retry_delay_ms / 1000.0),
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        last_error_code = p_error_code,
        last_error_at = failed_at_value,
        revision = partition.revision + 1,
        updated_at = failed_at_value
      where partition.id = p_partition_id
        and partition.status = 'CLAIMED'
        and partition.claim_token = p_claim_token
        and partition.revision = p_expected_revision
        and partition.claimed_by = p_claimed_by
        and partition.claim_expires_at > failed_at_value;
      get diagnostics affected_count = row_count;
      return affected_count = 1;
    end;
    $$
    """,
    """
    create function atlas.fail_task_run_materialization_partition(
      p_partition_id uuid,
      p_claim_token uuid,
      p_expected_revision bigint,
      p_claimed_by text,
      p_error_code text
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      affected_count integer;
      failed_at_value timestamptz := clock_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'materialization partition failure requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_error_code is null
        or p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$'
      then
        raise exception 'materialization partition failure input is invalid'
          using errcode = '22023';
      end if;
      update atlas.task_run_materialization_partition partition
      set
        status = 'FAILED',
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        last_error_code = p_error_code,
        last_error_at = failed_at_value,
        failed_at = failed_at_value,
        revision = partition.revision + 1,
        updated_at = failed_at_value
      where partition.id = p_partition_id
        and partition.status = 'CLAIMED'
        and partition.claim_token = p_claim_token
        and partition.revision = p_expected_revision
        and partition.claimed_by = p_claimed_by
        and partition.claim_expires_at > failed_at_value;
      get diagnostics affected_count = row_count;
      return affected_count = 1;
    end;
    $$
    """,
    "grant usage on schema atlas to atlas_dispatcher",
    "grant select, insert on atlas.task_run_materialization_partition to atlas_app",
    "revoke update, delete, truncate, references, trigger "
    "on atlas.task_run_materialization_partition from atlas_app",
    "revoke all on atlas.task_run_materialization_partition from atlas_dispatcher",
    "revoke all on function "
    "atlas.claim_task_run_materialization_partitions(text, integer, integer) "
    "from public, atlas_app",
    "revoke all on function "
    "atlas.complete_task_run_materialization_partition(uuid, uuid, bigint, text) "
    "from public, atlas_app",
    "revoke all on function "
    "atlas.retry_task_run_materialization_partition("
    "uuid, uuid, bigint, text, text, integer) from public, atlas_app",
    "revoke all on function "
    "atlas.fail_task_run_materialization_partition(uuid, uuid, bigint, text, text) "
    "from public, atlas_app",
    "grant execute on function "
    "atlas.claim_task_run_materialization_partitions(text, integer, integer) "
    "to atlas_dispatcher",
    "grant execute on function "
    "atlas.complete_task_run_materialization_partition(uuid, uuid, bigint, text) "
    "to atlas_dispatcher",
    "grant execute on function "
    "atlas.retry_task_run_materialization_partition("
    "uuid, uuid, bigint, text, text, integer) to atlas_dispatcher",
    "grant execute on function "
    "atlas.fail_task_run_materialization_partition(uuid, uuid, bigint, text, text) "
    "to atlas_dispatcher",
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      perform set_config('row_security', 'off', true);
      if exists (
        select 1 from atlas.task_run_materialization_partition
      ) or exists (
        select 1 from atlas.task_run_manifest where unit_count > 64
      ) or exists (
        select 1 from atlas.task_run
        where materialized_unit_count > 64
      ) then
        raise exception
          'cannot downgrade while partitioned TaskRun materialization facts exist'
          using errcode = '55000';
      end if;
    end;
    $$
    """,
    "revoke execute on function "
    "atlas.fail_task_run_materialization_partition(uuid, uuid, bigint, text, text) "
    "from atlas_dispatcher",
    "revoke execute on function "
    "atlas.retry_task_run_materialization_partition("
    "uuid, uuid, bigint, text, text, integer) from atlas_dispatcher",
    "revoke execute on function "
    "atlas.complete_task_run_materialization_partition(uuid, uuid, bigint, text) "
    "from atlas_dispatcher",
    "revoke execute on function "
    "atlas.claim_task_run_materialization_partitions(text, integer, integer) "
    "from atlas_dispatcher",
    "drop function atlas.fail_task_run_materialization_partition("
    "uuid, uuid, bigint, text, text)",
    "drop function atlas.retry_task_run_materialization_partition("
    "uuid, uuid, bigint, text, text, integer)",
    "drop function atlas.complete_task_run_materialization_partition("
    "uuid, uuid, bigint, text)",
    "drop function atlas.claim_task_run_materialization_partitions("
    "text, integer, integer)",
    "alter table atlas.task_run drop constraint task_run_materialization_valid",
    """
    alter table atlas.task_run
      add constraint task_run_materialization_valid check (
        materialization_state in ('MATERIALIZING', 'SEALED')
        and (
          (
            materialization_state = 'MATERIALIZING'
            and materialized_unit_count is null
            and materialized_first_attempt_count is null
            and materialization_sealed_at is null
          ) or (
            materialization_state = 'SEALED'
            and not legacy_unsealed
            and request_digest is not null
            and materialized_unit_count between 1 and 64
            and materialized_first_attempt_count = materialized_unit_count
            and materialization_sealed_at between created_at and updated_at
          )
        )
      )
    """,
    "alter table atlas.task_run_manifest "
    "drop constraint task_run_manifest_units_v2_valid",
    _manifest_units_validator(64),
    """
    alter table atlas.task_run_manifest
      add constraint task_run_manifest_units_v2_valid check (
        atlas.task_manifest_units_v2_valid(units)
        and unit_count = jsonb_array_length(units)
        and unit_count between 1 and 64
      )
    """,
    _seal_function(64, require_partitions=False),
    "drop table atlas.task_run_materialization_partition",
    "drop function atlas.guard_task_run_materialization_partition_update()",
    "drop function atlas.guard_task_run_materialization_partition_insert()",
)


def upgrade() -> None:
    """Install recoverable 64-Unit materialization partitions up to 100,000 Units."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Restore the synchronous ceiling only when no partition facts would be lost."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
