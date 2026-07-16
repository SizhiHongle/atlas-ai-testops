# ruff: noqa: E501
"""Create immutable TaskRun execution hosts and append-only attempt history.

Revision ID: 20260716_0022
Revises: 20260716_0021
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_0022"
down_revision: str | None = "20260716_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create function atlas.task_json_object_size(value jsonb)
    returns integer
    language sql
    immutable
    strict
    set search_path = pg_catalog
    as $$
      select count(*)::integer from jsonb_object_keys(value)
    $$
    """,
    """
    create function atlas.task_uuid_json_array_valid(value jsonb)
    returns boolean
    language sql
    immutable
    set search_path = pg_catalog
    as $$
      select case
        when jsonb_typeof(value) is distinct from 'array' then false
        when jsonb_array_length(value) = 0 then false
        else
          not exists (
            select 1
            from jsonb_array_elements_text(value) item(value)
            where item.value is null
               or item.value !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          )
          and value = (
            select jsonb_agg(to_jsonb(item.value) order by item.value)
            from jsonb_array_elements_text(value) item(value)
          )
          and jsonb_array_length(value) = (
            select count(distinct item.value)
            from jsonb_array_elements_text(value) item(value)
          )
      end
    $$
    """,
    """
    create function atlas.task_policy_digests_valid(value jsonb)
    returns boolean
    language sql
    immutable
    strict
    set search_path = pg_catalog
    as $$
      select case
        when jsonb_typeof(value) is distinct from 'object' then false
        when atlas.task_json_object_size(value) not between 1 and 64 then false
        else not exists (
          select 1
          from jsonb_each_text(value) item(key, digest)
          where item.digest is null
             or item.key !~ '^[a-z][a-z0-9_.-]{1,127}$'
             or item.digest !~ '^sha256:[0-9a-f]{64}$'
        )
      end
    $$
    """,
    """
    create function atlas.task_profile_refs_valid(value jsonb, pinned uuid[])
    returns boolean
    language plpgsql
    immutable
    set search_path = pg_catalog
    as $$
    declare
      profile jsonb;
      original_case_ids uuid[];
      sorted_case_ids uuid[];
    begin
      if pinned is null
        or jsonb_typeof(value) is distinct from 'object'
        or atlas.task_json_object_size(value) <> 1
        or not (value ?& array['caseProfiles'])
        or jsonb_typeof(value -> 'caseProfiles') is distinct from 'array'
        or jsonb_array_length(value -> 'caseProfiles') <> cardinality(pinned)
      then
        return false;
      end if;

      for profile in
        select item.value
        from jsonb_array_elements(value -> 'caseProfiles') item(value)
      loop
        if jsonb_typeof(profile) is distinct from 'object'
          or atlas.task_json_object_size(profile) <> 3
          or not (profile ?& array[
            'caseVersionId',
            'executionContractVersionId',
            'fixtureBlueprintVersionId'
          ])
          or profile ->> 'caseVersionId' is null
          or profile ->> 'caseVersionId' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          or profile ->> 'executionContractVersionId' is null
          or profile ->> 'executionContractVersionId' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          or profile ->> 'fixtureBlueprintVersionId' is null
          or profile ->> 'fixtureBlueprintVersionId' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        then
          return false;
        end if;
      end loop;

      select
        array_agg((item.value ->> 'caseVersionId')::uuid order by item.ordinality),
        array_agg((item.value ->> 'caseVersionId')::uuid order by item.value ->> 'caseVersionId')
      into original_case_ids, sorted_case_ids
      from jsonb_array_elements(value -> 'caseProfiles') with ordinality item(value, ordinality);

      return coalesce(
        original_case_ids = sorted_case_ids
        and sorted_case_ids = pinned
        and cardinality(sorted_case_ids) = (
          select count(distinct item.value ->> 'caseVersionId')
          from jsonb_array_elements(value -> 'caseProfiles') item(value)
        ),
        false
      );
    exception
      when others then
        return false;
    end;
    $$
    """,
    """
    create function atlas.task_manifest_units_valid(value jsonb)
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
        or jsonb_array_length(value) not between 1 and 100000
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
            'ordinal', 'unitKey', 'caseVersionId',
            'executionContractVersionId', 'fixtureBlueprintVersionId',
            'identityProfileVersionId', 'environmentId',
            'browserProfileVersionId', 'dataProfileVersionId',
            'parameterDigest', 'dependencyDigest'
          ])
          or unit ->> 'ordinal' is distinct from position::text
          or unit ->> 'unitKey' is null
          or unit ->> 'unitKey' !~ '^sha256:[0-9a-f]{64}$'
          or unit ->> 'parameterDigest' is null
          or unit ->> 'parameterDigest' !~ '^sha256:[0-9a-f]{64}$'
          or unit ->> 'dependencyDigest' is null
          or unit ->> 'dependencyDigest' !~ '^sha256:[0-9a-f]{64}$'
        then
          return false;
        end if;

        foreach uuid_field in array array[
          'caseVersionId', 'executionContractVersionId',
          'fixtureBlueprintVersionId', 'identityProfileVersionId',
          'environmentId', 'browserProfileVersionId', 'dataProfileVersionId'
        ]
        loop
          if unit ->> uuid_field is null
            or unit ->> uuid_field !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
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
      when others then
        return false;
    end;
    $$
    """,
    """
    create function atlas.task_execution_state_valid(
      lifecycle text,
      quality text,
      hygiene text,
      created_at timestamptz,
      updated_at timestamptz,
      started_at timestamptz,
      finalized_at timestamptz,
      cleanup_resolved_at timestamptz,
      closed_at timestamptz
    ) returns boolean
    language sql
    immutable
    set search_path = pg_catalog
    as $$
      select
        updated_at >= created_at
        and (started_at is null or started_at >= created_at)
        and (finalized_at is null or finalized_at >= created_at)
        and (cleanup_resolved_at is null or cleanup_resolved_at >= created_at)
        and (closed_at is null or closed_at >= created_at)
        and (started_at is null or finalized_at is null or finalized_at >= started_at)
        and (lifecycle <> 'QUEUED' or started_at is null)
        and (
          lifecycle not in ('RUNNING', 'PAUSE_REQUESTED', 'PAUSED')
          or started_at is not null
        )
        and (
          (quality = 'PENDING' and finalized_at is null)
          or (quality <> 'PENDING' and finalized_at is not null)
        )
        and (
          quality = 'PENDING'
          or lifecycle in ('FINALIZING', 'CLOSED')
        )
        and (
          (
            hygiene in ('CLEANED', 'LEAKED')
            and cleanup_resolved_at is not null
          )
          or (
            hygiene not in ('CLEANED', 'LEAKED')
            and cleanup_resolved_at is null
          )
        )
        and (
          (
            lifecycle = 'CLOSED'
            and closed_at is not null
            and quality <> 'PENDING'
          )
          or (lifecycle <> 'CLOSED' and closed_at is null)
        )
        and (closed_at is null or finalized_at is null or closed_at >= finalized_at)
    $$
    """,
    """
    create function atlas.task_lifecycle_transition_valid(
      old_state text,
      new_state text
    ) returns boolean
    language sql
    immutable
    strict
    set search_path = pg_catalog
    as $$
      select
        old_state = new_state
        or (old_state = 'QUEUED' and new_state in ('RUNNING', 'CANCELING', 'FINALIZING'))
        or (
          old_state = 'RUNNING'
          and new_state in ('PAUSE_REQUESTED', 'CANCELING', 'FINALIZING')
        )
        or (
          old_state = 'PAUSE_REQUESTED'
          and new_state in ('RUNNING', 'PAUSED', 'CANCELING', 'FINALIZING')
        )
        or (
          old_state = 'PAUSED'
          and new_state in ('RUNNING', 'CANCELING', 'FINALIZING')
        )
        or (old_state = 'CANCELING' and new_state = 'FINALIZING')
        or (old_state = 'FINALIZING' and new_state = 'CLOSED')
    $$
    """,
    """
    create function atlas.task_hygiene_transition_valid(
      old_state text,
      new_state text
    ) returns boolean
    language sql
    immutable
    strict
    set search_path = pg_catalog
    as $$
      select
        old_state = new_state
        or (old_state = 'PENDING' and new_state = 'RUNNING')
        or (
          old_state = 'RUNNING'
          and new_state in ('CLEANED', 'CLEANUP_FAILED', 'LEAKED')
        )
        or (
          old_state = 'CLEANUP_FAILED'
          and new_state in ('RUNNING', 'LEAKED')
        )
    $$
    """,
    """
    alter table atlas.case_version
      add constraint case_version_task_scope_unique unique (
        id, tenant_id, project_id
      )
    """,
    """
    create table atlas.task_plan (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_key text not null,
      name text not null,
      status text not null default 'ACTIVE',
      created_by uuid not null,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint task_plan_project_scope_fk foreign key (
        project_id, tenant_id
      ) references atlas.project (id, tenant_id) on delete restrict,
      constraint task_plan_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint task_plan_project_key_unique unique (
        tenant_id, project_id, task_key
      ),
      constraint task_plan_key_valid check (
        task_key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){0,7}$'
        and char_length(task_key) between 3 and 160
      ),
      constraint task_plan_name_valid check (
        btrim(name) <> '' and char_length(name) <= 160
      ),
      constraint task_plan_status_valid check (
        status in ('ACTIVE', 'ARCHIVED')
      ),
      constraint task_plan_revision_valid check (revision > 0),
      constraint task_plan_time_order check (updated_at >= created_at)
    )
    """,
    """
    create table atlas.task_plan_version (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_plan_id uuid not null,
      schema_version text not null default 'atlas.task-plan/0.1',
      version text not null,
      version_ref text not null,
      pinned_case_version_ids uuid[] not null,
      matrix jsonb not null,
      profile_refs jsonb not null,
      policy_digests jsonb not null,
      content_digest text not null,
      published_by uuid not null,
      published_at timestamptz not null,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint task_plan_version_plan_scope_fk foreign key (
        task_plan_id, tenant_id, project_id
      ) references atlas.task_plan (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_plan_version_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint task_plan_version_number_unique unique (
        tenant_id, task_plan_id, version
      ),
      constraint task_plan_version_ref_unique unique (
        tenant_id, project_id, version_ref
      ),
      constraint task_plan_version_schema_valid check (
        schema_version = 'atlas.task-plan/0.1'
      ),
      constraint task_plan_version_semver_valid check (
        version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint task_plan_version_ref_valid check (
        version_ref = 'task-plan/' || task_plan_id::text || '@' || version
        and char_length(version_ref) <= 256
      ),
      constraint task_plan_version_pinned_valid check (
        cardinality(pinned_case_version_ids) between 1 and 100000
        and array_position(pinned_case_version_ids, null) is null
        and atlas.task_uuid_json_array_valid(to_jsonb(pinned_case_version_ids))
      ),
      constraint task_plan_version_matrix_valid check (
        jsonb_typeof(matrix) = 'object'
        and atlas.task_json_object_size(matrix) = 4
        and matrix ?& array[
          'environmentIds', 'browserProfileVersionIds',
          'identityProfileVersionIds', 'dataProfileVersionIds'
        ]
        and coalesce(
          atlas.task_uuid_json_array_valid(matrix -> 'environmentIds'), false
        )
        and coalesce(
          atlas.task_uuid_json_array_valid(matrix -> 'browserProfileVersionIds'), false
        )
        and coalesce(
          atlas.task_uuid_json_array_valid(matrix -> 'identityProfileVersionIds'), false
        )
        and coalesce(
          atlas.task_uuid_json_array_valid(matrix -> 'dataProfileVersionIds'), false
        )
        and jsonb_array_length(matrix -> 'environmentIds') <= 1000
        and jsonb_array_length(matrix -> 'browserProfileVersionIds') <= 1000
        and jsonb_array_length(matrix -> 'identityProfileVersionIds') <= 1000
        and jsonb_array_length(matrix -> 'dataProfileVersionIds') <= 1000
      ),
      constraint task_plan_version_profiles_valid check (
        atlas.task_profile_refs_valid(profile_refs, pinned_case_version_ids)
      ),
      constraint task_plan_version_policies_valid check (
        atlas.task_policy_digests_valid(policy_digests)
      ),
      constraint task_plan_version_digest_valid check (
        content_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint task_plan_version_revision_valid check (revision = 1),
      constraint task_plan_version_time_order check (
        created_at <= published_at and published_at <= updated_at
      )
    )
    """,
    """
    create table atlas.task_run (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_plan_version_id uuid not null,
      manifest_hash text not null,
      trigger_source text not null,
      trigger_fingerprint text not null,
      rerun_of_task_run_id uuid,
      lifecycle text not null default 'QUEUED',
      quality text not null default 'PENDING',
      hygiene text not null default 'PENDING',
      requested_by uuid,
      temporal_workflow_id text,
      requested_at timestamptz not null,
      queued_at timestamptz not null,
      started_at timestamptz,
      finalized_at timestamptz,
      cleanup_resolved_at timestamptz,
      closed_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint task_run_plan_version_scope_fk foreign key (
        task_plan_version_id, tenant_id, project_id
      ) references atlas.task_plan_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_run_rerun_scope_fk foreign key (
        rerun_of_task_run_id, tenant_id, project_id
      ) references atlas.task_run (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_run_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint task_run_manifest_scope_unique unique (
        id, tenant_id, project_id, task_plan_version_id, manifest_hash,
        trigger_source, trigger_fingerprint
      ),
      constraint task_run_trigger_unique unique (
        tenant_id, trigger_source, trigger_fingerprint
      ),
      constraint task_run_workflow_unique unique (
        tenant_id, temporal_workflow_id
      ),
      constraint task_run_manifest_hash_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint task_run_trigger_source_valid check (
        trigger_source in ('MANUAL', 'SCHEDULE', 'CI', 'WEBHOOK', 'API')
      ),
      constraint task_run_trigger_fingerprint_valid check (
        trigger_fingerprint ~ '^[A-Za-z0-9][A-Za-z0-9._:@/+=-]+$'
        and char_length(trigger_fingerprint) between 3 and 320
      ),
      constraint task_run_not_self_rerun check (
        rerun_of_task_run_id is null or rerun_of_task_run_id <> id
      ),
      constraint task_run_lifecycle_valid check (
        lifecycle in (
          'QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED',
          'CANCELING', 'FINALIZING', 'CLOSED'
        )
      ),
      constraint task_run_quality_valid check (
        quality in (
          'PENDING', 'PASSED', 'FAILED', 'BLOCKED', 'INCONCLUSIVE',
          'INFRA_ERROR', 'CANCELED'
        )
      ),
      constraint task_run_hygiene_valid check (
        hygiene in (
          'NOT_REQUIRED', 'PENDING', 'RUNNING', 'CLEANED',
          'CLEANUP_FAILED', 'LEAKED'
        )
      ),
      constraint task_run_workflow_id_valid check (
        temporal_workflow_id is null
        or (
          temporal_workflow_id ~ '^atlas-task/[A-Za-z0-9/_-]+$'
          and char_length(temporal_workflow_id) between 12 and 320
        )
      ),
      constraint task_run_revision_valid check (revision > 0),
      constraint task_run_time_order check (
        created_at <= requested_at
        and requested_at <= queued_at
        and (started_at is null or started_at >= queued_at)
      ),
      constraint task_run_state_shape check (
        atlas.task_execution_state_valid(
          lifecycle, quality, hygiene, created_at, updated_at,
          started_at, finalized_at, cleanup_resolved_at, closed_at
        )
      )
    )
    """,
    """
    create table atlas.task_run_manifest (
      task_run_id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_plan_version_id uuid not null,
      schema_version text not null default 'atlas.task-run-manifest/0.1',
      trigger_source text not null,
      trigger_fingerprint text not null,
      iteration_id text,
      units jsonb not null,
      policy_digests jsonb not null,
      compiler_version text not null,
      manifest_hash text not null,
      unit_count integer not null,
      created_at timestamptz not null default clock_timestamp(),
      constraint task_run_manifest_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id, task_plan_version_id,
        manifest_hash, trigger_source, trigger_fingerprint
      ) references atlas.task_run (
        id, tenant_id, project_id, task_plan_version_id,
        manifest_hash, trigger_source, trigger_fingerprint
      ) on delete restrict,
      constraint task_run_manifest_full_scope_unique unique (
        task_run_id, tenant_id, project_id, manifest_hash
      ),
      constraint task_run_manifest_reverse_scope_unique unique (
        task_run_id, tenant_id, project_id, task_plan_version_id,
        manifest_hash, trigger_source, trigger_fingerprint
      ),
      constraint task_run_manifest_schema_valid check (
        schema_version = 'atlas.task-run-manifest/0.1'
      ),
      constraint task_run_manifest_trigger_source_valid check (
        trigger_source in ('MANUAL', 'SCHEDULE', 'CI', 'WEBHOOK', 'API')
      ),
      constraint task_run_manifest_trigger_fingerprint_valid check (
        trigger_fingerprint ~ '^[A-Za-z0-9][A-Za-z0-9._:@/+=-]+$'
        and char_length(trigger_fingerprint) between 3 and 320
      ),
      constraint task_run_manifest_iteration_valid check (
        iteration_id is null
        or (
          iteration_id ~ '^[A-Za-z0-9][A-Za-z0-9._:@/+=-]{2,159}$'
          and char_length(iteration_id) between 3 and 160
        )
      ),
      constraint task_run_manifest_units_valid check (
        atlas.task_manifest_units_valid(units)
        and unit_count = jsonb_array_length(units)
        and unit_count between 1 and 100000
      ),
      constraint task_run_manifest_policies_valid check (
        atlas.task_policy_digests_valid(policy_digests)
      ),
      constraint task_run_manifest_compiler_valid check (
        compiler_version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint task_run_manifest_hash_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
      )
    )
    """,
    """
    alter table atlas.task_run
      add constraint task_run_manifest_reverse_scope_fk foreign key (
        id, tenant_id, project_id, task_plan_version_id,
        manifest_hash, trigger_source, trigger_fingerprint
      ) references atlas.task_run_manifest (
        task_run_id, tenant_id, project_id, task_plan_version_id,
        manifest_hash, trigger_source, trigger_fingerprint
      ) on delete restrict deferrable initially deferred
    """,
    """
    create table atlas.execution_unit (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      manifest_hash text not null,
      ordinal integer not null,
      unit_key text not null,
      case_version_id uuid not null,
      execution_contract_version_id uuid not null,
      fixture_blueprint_version_id uuid not null,
      identity_profile_version_id uuid not null,
      environment_id uuid not null,
      browser_profile_version_id uuid not null,
      data_profile_version_id uuid not null,
      parameter_digest text not null,
      dependency_digest text not null,
      lifecycle text not null default 'QUEUED',
      quality text not null default 'PENDING',
      hygiene text not null default 'PENDING',
      started_at timestamptz,
      finalized_at timestamptz,
      cleanup_resolved_at timestamptz,
      closed_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint execution_unit_manifest_scope_fk foreign key (
        task_run_id, tenant_id, project_id, manifest_hash
      ) references atlas.task_run_manifest (
        task_run_id, tenant_id, project_id, manifest_hash
      ) on delete restrict,
      constraint execution_unit_case_version_scope_fk foreign key (
        case_version_id, tenant_id, project_id
      ) references atlas.case_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint execution_unit_environment_scope_fk foreign key (
        environment_id, tenant_id, project_id
      ) references atlas.environment (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint execution_unit_fixture_blueprint_scope_fk foreign key (
        fixture_blueprint_version_id, tenant_id, project_id
      ) references atlas.data_blueprint_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint execution_unit_full_scope_unique unique (
        id, tenant_id, project_id, task_run_id, manifest_hash,
        unit_key, case_version_id
      ),
      constraint execution_unit_event_scope_unique unique (
        id, task_run_id, tenant_id, project_id
      ),
      constraint execution_unit_run_key_unique unique (
        task_run_id, unit_key
      ),
      constraint execution_unit_run_ordinal_unique unique (
        task_run_id, ordinal
      ),
      constraint execution_unit_ordinal_valid check (ordinal > 0),
      constraint execution_unit_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and unit_key ~ '^sha256:[0-9a-f]{64}$'
        and parameter_digest ~ '^sha256:[0-9a-f]{64}$'
        and dependency_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint execution_unit_lifecycle_valid check (
        lifecycle in (
          'QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED',
          'CANCELING', 'FINALIZING', 'CLOSED'
        )
      ),
      constraint execution_unit_quality_valid check (
        quality in (
          'PENDING', 'PASSED', 'FAILED', 'BLOCKED', 'INCONCLUSIVE',
          'INFRA_ERROR', 'CANCELED'
        )
      ),
      constraint execution_unit_hygiene_valid check (
        hygiene in (
          'NOT_REQUIRED', 'PENDING', 'RUNNING', 'CLEANED',
          'CLEANUP_FAILED', 'LEAKED'
        )
      ),
      constraint execution_unit_revision_valid check (revision > 0),
      constraint execution_unit_state_shape check (
        atlas.task_execution_state_valid(
          lifecycle, quality, hygiene, created_at, updated_at,
          started_at, finalized_at, cleanup_resolved_at, closed_at
        )
      )
    )
    """,
    """
    create table atlas.unit_attempt (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      manifest_hash text not null,
      unit_key text not null,
      case_version_id uuid not null,
      attempt_number integer not null,
      lifecycle text not null default 'QUEUED',
      quality text not null default 'PENDING',
      hygiene text not null default 'PENDING',
      temporal_workflow_id text,
      queued_at timestamptz not null,
      execution_deadline timestamptz not null,
      started_at timestamptz,
      finalized_at timestamptz,
      cleanup_resolved_at timestamptz,
      closed_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint unit_attempt_unit_scope_fk foreign key (
        execution_unit_id, tenant_id, project_id, task_run_id,
        manifest_hash, unit_key, case_version_id
      ) references atlas.execution_unit (
        id, tenant_id, project_id, task_run_id,
        manifest_hash, unit_key, case_version_id
      ) on delete restrict,
      constraint unit_attempt_full_scope_unique unique (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ),
      constraint unit_attempt_number_unique unique (
        execution_unit_id, attempt_number
      ),
      constraint unit_attempt_workflow_unique unique (
        tenant_id, temporal_workflow_id
      ),
      constraint unit_attempt_number_valid check (attempt_number > 0),
      constraint unit_attempt_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and unit_key ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint unit_attempt_lifecycle_valid check (
        lifecycle in (
          'QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED',
          'CANCELING', 'FINALIZING', 'CLOSED'
        )
      ),
      constraint unit_attempt_quality_valid check (
        quality in (
          'PENDING', 'PASSED', 'FAILED', 'BLOCKED', 'INCONCLUSIVE',
          'INFRA_ERROR', 'CANCELED'
        )
      ),
      constraint unit_attempt_hygiene_valid check (
        hygiene in (
          'NOT_REQUIRED', 'PENDING', 'RUNNING', 'CLEANED',
          'CLEANUP_FAILED', 'LEAKED'
        )
      ),
      constraint unit_attempt_workflow_id_valid check (
        temporal_workflow_id is null
        or (
          temporal_workflow_id ~ '^atlas-task/[A-Za-z0-9/_-]+$'
          and char_length(temporal_workflow_id) between 12 and 320
        )
      ),
      constraint unit_attempt_revision_valid check (revision > 0),
      constraint unit_attempt_time_order check (
        queued_at >= created_at
        and execution_deadline > queued_at
        and (started_at is null or started_at >= queued_at)
      ),
      constraint unit_attempt_state_shape check (
        atlas.task_execution_state_valid(
          lifecycle, quality, hygiene, created_at, updated_at,
          started_at, finalized_at, cleanup_resolved_at, closed_at
        )
      )
    )
    """,
    """
    create table atlas.task_run_event (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid,
      unit_attempt_id uuid,
      seq bigint not null,
      event_type text not null,
      lifecycle text not null,
      quality text not null,
      hygiene text not null,
      payload jsonb not null default '{}'::jsonb,
      occurred_at timestamptz not null,
      constraint task_run_event_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id
      ) references atlas.task_run (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_run_event_unit_scope_fk foreign key (
        execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.execution_unit (
        id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint task_run_event_attempt_scope_fk foreign key (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.unit_attempt (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint task_run_event_sequence_unique unique (task_run_id, seq),
      constraint task_run_event_scope_valid check (
        unit_attempt_id is null or execution_unit_id is not null
      ),
      constraint task_run_event_seq_valid check (seq > 0),
      constraint task_run_event_type_valid check (
        event_type ~ '^[a-z][a-z0-9_.-]+$'
        and char_length(event_type) between 3 and 160
      ),
      constraint task_run_event_lifecycle_valid check (
        lifecycle in (
          'QUEUED', 'RUNNING', 'PAUSE_REQUESTED', 'PAUSED',
          'CANCELING', 'FINALIZING', 'CLOSED'
        )
      ),
      constraint task_run_event_quality_valid check (
        quality in (
          'PENDING', 'PASSED', 'FAILED', 'BLOCKED', 'INCONCLUSIVE',
          'INFRA_ERROR', 'CANCELED'
        )
      ),
      constraint task_run_event_hygiene_valid check (
        hygiene in (
          'NOT_REQUIRED', 'PENDING', 'RUNNING', 'CLEANED',
          'CLEANUP_FAILED', 'LEAKED'
        )
      ),
      constraint task_run_event_payload_valid check (
        jsonb_typeof(payload) = 'object'
        and octet_length(payload::text) <= 32768
      )
    )
    """,
    """
    create index task_plan_project_scope_fk_idx
      on atlas.task_plan (project_id, tenant_id)
    """,
    """
    create index task_plan_project_catalog_idx
      on atlas.task_plan (
        tenant_id, project_id, status, updated_at desc, id desc
      )
    """,
    """
    create index task_plan_version_plan_scope_fk_idx
      on atlas.task_plan_version (task_plan_id, tenant_id, project_id)
    """,
    """
    create index task_plan_version_history_idx
      on atlas.task_plan_version (
        tenant_id, project_id, task_plan_id, published_at desc, id desc
      )
    """,
    """
    create index task_run_plan_version_scope_fk_idx
      on atlas.task_run (task_plan_version_id, tenant_id, project_id)
    """,
    """
    create index task_run_rerun_scope_fk_idx
      on atlas.task_run (rerun_of_task_run_id, tenant_id, project_id)
      where rerun_of_task_run_id is not null
    """,
    """
    create index task_run_project_queue_idx
      on atlas.task_run (
        tenant_id, project_id, lifecycle, queued_at desc, id desc
      )
    """,
    """
    create index task_run_manifest_plan_version_idx
      on atlas.task_run_manifest (
        task_plan_version_id, tenant_id, project_id, task_run_id
      )
    """,
    """
    create index execution_unit_manifest_scope_fk_idx
      on atlas.execution_unit (
        task_run_id, tenant_id, project_id, manifest_hash
      )
    """,
    """
    create index execution_unit_case_version_scope_fk_idx
      on atlas.execution_unit (case_version_id, tenant_id, project_id)
    """,
    """
    create index execution_unit_environment_scope_fk_idx
      on atlas.execution_unit (environment_id, tenant_id, project_id)
    """,
    """
    create index execution_unit_fixture_blueprint_scope_fk_idx
      on atlas.execution_unit (
        fixture_blueprint_version_id, tenant_id, project_id
      )
    """,
    """
    create index execution_unit_run_state_idx
      on atlas.execution_unit (
        tenant_id, task_run_id, lifecycle, ordinal, id
      )
    """,
    """
    create index unit_attempt_unit_scope_fk_idx
      on atlas.unit_attempt (
        execution_unit_id, tenant_id, project_id, task_run_id,
        manifest_hash, unit_key, case_version_id
      )
    """,
    """
    create index unit_attempt_run_state_idx
      on atlas.unit_attempt (
        tenant_id, task_run_id, lifecycle, created_at desc, id desc
      )
    """,
    """
    create index task_run_event_unit_scope_fk_idx
      on atlas.task_run_event (
        execution_unit_id, task_run_id, tenant_id, project_id
      ) where execution_unit_id is not null
    """,
    """
    create index task_run_event_attempt_scope_fk_idx
      on atlas.task_run_event (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) where unit_attempt_id is not null
    """,
    """
    create function atlas.guard_task_plan_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.task_key,
        new.created_by, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_key,
        old.created_by, old.created_at
      ) then
        raise exception 'task plan identity and provenance are immutable';
      end if;
      if old.status = 'ARCHIVED' then
        raise exception 'archived task plan is immutable';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'task plan revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_plan_version_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      scoped_case_count bigint;
      requested_environment_count bigint;
      scoped_environment_count bigint;
      requested_fixture_count bigint;
      scoped_fixture_count bigint;
    begin
      select count(*)
      into scoped_case_count
      from atlas.case_version version
      where version.id = any(new.pinned_case_version_ids)
        and version.tenant_id = new.tenant_id
        and version.project_id = new.project_id
        and version.status = 'PUBLISHED';
      if scoped_case_count <> cardinality(new.pinned_case_version_ids) then
        raise exception 'task plan version requires published same-scope case versions';
      end if;

      select
        count(distinct requested.environment_id),
        count(distinct environment.id)
      into requested_environment_count, scoped_environment_count
      from jsonb_array_elements_text(new.matrix -> 'environmentIds')
        requested(environment_id)
      left join atlas.environment environment
        on environment.id::text = requested.environment_id
       and environment.tenant_id = new.tenant_id
       and environment.project_id = new.project_id
       and environment.status = 'ACTIVE'
       and environment.kind in ('TEST', 'STAGING');
      if scoped_environment_count <> requested_environment_count then
        raise exception
          'task plan version requires active same-scope test or staging environments';
      end if;

      select
        count(distinct profile.value ->> 'fixtureBlueprintVersionId'),
        count(distinct version.id)
      into requested_fixture_count, scoped_fixture_count
      from jsonb_array_elements(new.profile_refs -> 'caseProfiles') profile(value)
      left join atlas.data_blueprint_version version
        on version.id::text = profile.value ->> 'fixtureBlueprintVersionId'
       and version.tenant_id = new.tenant_id
       and version.project_id = new.project_id
       and version.status = 'PUBLISHED';
      if scoped_fixture_count <> requested_fixture_count then
        raise exception
          'task plan version requires published same-scope fixture blueprint versions';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_run_manifest_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      plan_pinned_case_version_ids uuid[];
      plan_matrix jsonb;
      plan_profile_refs jsonb;
      plan_policy_digests jsonb;
      unit jsonb;
      case_profile jsonb;
    begin
      select
        version.pinned_case_version_ids,
        version.matrix,
        version.profile_refs,
        version.policy_digests
      into
        plan_pinned_case_version_ids,
        plan_matrix,
        plan_profile_refs,
        plan_policy_digests
      from atlas.task_plan_version version
      where version.id = new.task_plan_version_id
        and version.tenant_id = new.tenant_id
        and version.project_id = new.project_id;
      if not found then
        raise exception
          'task run manifest requires its exact same-scope task plan version';
      end if;

      if not (new.policy_digests @> plan_policy_digests) then
        raise exception
          'task run manifest policy digests must cover its task plan version';
      end if;
      if not coalesce(atlas.task_manifest_units_valid(new.units), false) then
        raise exception
          'task run manifest units must have valid provenance shape';
      end if;

      for unit in
        select item.value
        from jsonb_array_elements(new.units) item(value)
      loop
        if not (
          (unit ->> 'caseVersionId')::uuid
          = any(plan_pinned_case_version_ids)
        ) then
          raise exception
            'task run manifest unit must derive from its task plan version';
        end if;
        if not (
          (plan_matrix -> 'environmentIds') ? (unit ->> 'environmentId')
          and (plan_matrix -> 'browserProfileVersionIds')
            ? (unit ->> 'browserProfileVersionId')
          and (plan_matrix -> 'identityProfileVersionIds')
            ? (unit ->> 'identityProfileVersionId')
          and (plan_matrix -> 'dataProfileVersionIds')
            ? (unit ->> 'dataProfileVersionId')
        ) then
          raise exception
            'task run manifest unit must derive from its task plan version';
        end if;

        select profile.value
        into case_profile
        from jsonb_array_elements(
          plan_profile_refs -> 'caseProfiles'
        ) profile(value)
        where profile.value ->> 'caseVersionId' = unit ->> 'caseVersionId';
        if not found then
          raise exception
            'task run manifest unit must derive from its task plan version';
        end if;
        if row(
          unit ->> 'executionContractVersionId',
          unit ->> 'fixtureBlueprintVersionId'
        ) is distinct from row(
          case_profile ->> 'executionContractVersionId',
          case_profile ->> 'fixtureBlueprintVersionId'
        ) then
          raise exception
            'task run manifest unit must derive from its task plan version';
        end if;
      end loop;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_run_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if new.revision <> 1
        or new.lifecycle <> 'QUEUED'
        or new.quality <> 'PENDING'
        or new.hygiene not in ('PENDING', 'NOT_REQUIRED')
      then
        raise exception 'task run must start queued at revision one with initial hygiene';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_run_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id,
        new.task_plan_version_id, new.manifest_hash,
        new.trigger_source, new.trigger_fingerprint,
        new.rerun_of_task_run_id, new.requested_by,
        new.temporal_workflow_id, new.requested_at, new.queued_at,
        new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id,
        old.task_plan_version_id, old.manifest_hash,
        old.trigger_source, old.trigger_fingerprint,
        old.rerun_of_task_run_id, old.requested_by,
        old.temporal_workflow_id, old.requested_at, old.queued_at,
        old.created_at
      ) then
        raise exception 'task run identity and frozen inputs are immutable';
      end if;
      if not atlas.task_lifecycle_transition_valid(old.lifecycle, new.lifecycle) then
        raise exception 'task run lifecycle transition is invalid';
      end if;
      if not atlas.task_hygiene_transition_valid(old.hygiene, new.hygiene) then
        raise exception 'task run hygiene transition is invalid';
      end if;
      if old.lifecycle = 'CLOSED'
        and row(
          new.lifecycle, new.quality, new.started_at,
          new.finalized_at, new.closed_at
        ) is distinct from row(
          old.lifecycle, old.quality, old.started_at,
          old.finalized_at, old.closed_at
        )
      then
        raise exception
          'closed task run lifecycle, quality, and close milestones are immutable';
      end if;
      if old.quality <> 'PENDING' and new.quality <> old.quality then
        raise exception 'resolved task run quality is immutable';
      end if;
      if (old.started_at is not null and new.started_at is distinct from old.started_at)
        or (old.finalized_at is not null and new.finalized_at is distinct from old.finalized_at)
        or (
          old.cleanup_resolved_at is not null
          and new.cleanup_resolved_at is distinct from old.cleanup_resolved_at
        )
        or (old.closed_at is not null and new.closed_at is distinct from old.closed_at)
      then
        raise exception 'task run milestone timestamps are write-once';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'task run revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_execution_unit_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      manifest_unit jsonb;
    begin
      select manifest.units -> (new.ordinal - 1)
      into manifest_unit
      from atlas.task_run_manifest manifest
      where manifest.task_run_id = new.task_run_id
        and manifest.tenant_id = new.tenant_id
        and manifest.project_id = new.project_id
        and manifest.manifest_hash = new.manifest_hash;

      if not found or manifest_unit is null then
        raise exception 'execution unit requires its exact run manifest entry';
      end if;
      if row(
        manifest_unit ->> 'unitKey',
        manifest_unit ->> 'caseVersionId',
        manifest_unit ->> 'executionContractVersionId',
        manifest_unit ->> 'fixtureBlueprintVersionId',
        manifest_unit ->> 'identityProfileVersionId',
        manifest_unit ->> 'environmentId',
        manifest_unit ->> 'browserProfileVersionId',
        manifest_unit ->> 'dataProfileVersionId',
        manifest_unit ->> 'parameterDigest',
        manifest_unit ->> 'dependencyDigest'
      ) is distinct from row(
        new.unit_key,
        new.case_version_id::text,
        new.execution_contract_version_id::text,
        new.fixture_blueprint_version_id::text,
        new.identity_profile_version_id::text,
        new.environment_id::text,
        new.browser_profile_version_id::text,
        new.data_profile_version_id::text,
        new.parameter_digest,
        new.dependency_digest
      ) then
        raise exception 'execution unit bindings must match its run manifest';
      end if;
      if new.revision <> 1
        or new.lifecycle <> 'QUEUED'
        or new.quality <> 'PENDING'
        or new.hygiene not in ('PENDING', 'NOT_REQUIRED')
      then
        raise exception
          'execution unit must start queued at revision one with initial hygiene';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_execution_unit_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.manifest_hash, new.ordinal, new.unit_key,
        new.case_version_id, new.execution_contract_version_id,
        new.fixture_blueprint_version_id, new.identity_profile_version_id,
        new.environment_id, new.browser_profile_version_id,
        new.data_profile_version_id, new.parameter_digest,
        new.dependency_digest, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.manifest_hash, old.ordinal, old.unit_key,
        old.case_version_id, old.execution_contract_version_id,
        old.fixture_blueprint_version_id, old.identity_profile_version_id,
        old.environment_id, old.browser_profile_version_id,
        old.data_profile_version_id, old.parameter_digest,
        old.dependency_digest, old.created_at
      ) then
        raise exception 'execution unit manifest identity is immutable';
      end if;
      if not atlas.task_lifecycle_transition_valid(old.lifecycle, new.lifecycle) then
        raise exception 'execution unit lifecycle transition is invalid';
      end if;
      if not atlas.task_hygiene_transition_valid(old.hygiene, new.hygiene) then
        raise exception 'execution unit hygiene transition is invalid';
      end if;
      if old.lifecycle = 'CLOSED'
        and row(
          new.lifecycle, new.quality, new.started_at,
          new.finalized_at, new.closed_at
        ) is distinct from row(
          old.lifecycle, old.quality, old.started_at,
          old.finalized_at, old.closed_at
        )
      then
        raise exception
          'closed execution unit lifecycle, quality, and close milestones are immutable';
      end if;
      if old.quality <> 'PENDING' and new.quality <> old.quality then
        raise exception 'resolved execution unit quality is immutable';
      end if;
      if (old.started_at is not null and new.started_at is distinct from old.started_at)
        or (old.finalized_at is not null and new.finalized_at is distinct from old.finalized_at)
        or (
          old.cleanup_resolved_at is not null
          and new.cleanup_resolved_at is distinct from old.cleanup_resolved_at
        )
        or (old.closed_at is not null and new.closed_at is distinct from old.closed_at)
      then
        raise exception 'execution unit milestone timestamps are write-once';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'execution unit revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_unit_attempt_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      expected_attempt integer;
    begin
      perform 1
      from atlas.execution_unit unit
      where unit.id = new.execution_unit_id
        and unit.task_run_id = new.task_run_id
        and unit.tenant_id = new.tenant_id
        and unit.project_id = new.project_id
        and unit.manifest_hash = new.manifest_hash
        and unit.unit_key = new.unit_key
        and unit.case_version_id = new.case_version_id
      for update;
      if not found then
        raise exception 'unit attempt requires its exact execution unit';
      end if;

      if exists (
        select 1
        from atlas.unit_attempt attempt
        where attempt.id = new.id
           or (
             attempt.execution_unit_id = new.execution_unit_id
             and attempt.attempt_number = new.attempt_number
           )
      ) then
        return new;
      end if;

      select coalesce(max(attempt.attempt_number), 0) + 1
      into expected_attempt
      from atlas.unit_attempt attempt
      where attempt.execution_unit_id = new.execution_unit_id;
      if new.attempt_number <> expected_attempt then
        raise exception 'unit attempt number must be gapless';
      end if;
      if new.revision <> 1
        or new.lifecycle <> 'QUEUED'
        or new.quality <> 'PENDING'
        or new.hygiene not in ('PENDING', 'NOT_REQUIRED')
      then
        raise exception
          'unit attempt must start queued at revision one with initial hygiene';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_unit_attempt_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.execution_unit_id, new.manifest_hash, new.unit_key,
        new.case_version_id, new.attempt_number,
        new.temporal_workflow_id, new.queued_at,
        new.execution_deadline, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.execution_unit_id, old.manifest_hash, old.unit_key,
        old.case_version_id, old.attempt_number,
        old.temporal_workflow_id, old.queued_at,
        old.execution_deadline, old.created_at
      ) then
        raise exception 'unit attempt identity and deadline are immutable';
      end if;
      if not atlas.task_lifecycle_transition_valid(old.lifecycle, new.lifecycle) then
        raise exception 'unit attempt lifecycle transition is invalid';
      end if;
      if not atlas.task_hygiene_transition_valid(old.hygiene, new.hygiene) then
        raise exception 'unit attempt hygiene transition is invalid';
      end if;
      if old.lifecycle = 'CLOSED'
        and row(
          new.lifecycle, new.quality, new.started_at,
          new.finalized_at, new.closed_at
        ) is distinct from row(
          old.lifecycle, old.quality, old.started_at,
          old.finalized_at, old.closed_at
        )
      then
        raise exception
          'closed unit attempt lifecycle, quality, and close milestones are immutable';
      end if;
      if old.quality <> 'PENDING' and new.quality <> old.quality then
        raise exception 'resolved unit attempt quality is immutable';
      end if;
      if (old.started_at is not null and new.started_at is distinct from old.started_at)
        or (old.finalized_at is not null and new.finalized_at is distinct from old.finalized_at)
        or (
          old.cleanup_resolved_at is not null
          and new.cleanup_resolved_at is distinct from old.cleanup_resolved_at
        )
        or (old.closed_at is not null and new.closed_at is distinct from old.closed_at)
      then
        raise exception 'unit attempt milestone timestamps are write-once';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'unit attempt revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_run_event_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      expected_seq bigint;
      target_occurred_floor timestamptz;
      target_lifecycle text;
      target_quality text;
      target_hygiene text;
    begin
      select
        run.lifecycle,
        run.quality,
        run.hygiene,
        greatest(
          run.queued_at, run.started_at, run.finalized_at,
          run.closed_at, run.cleanup_resolved_at
        )
      into
        target_lifecycle, target_quality, target_hygiene,
        target_occurred_floor
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
      for update;
      if not found then
        raise exception 'task run event requires its exact task run';
      end if;

      if exists (
        select 1
        from atlas.task_run_event event
        where event.id = new.id
           or (
             event.task_run_id = new.task_run_id
             and event.seq = new.seq
           )
      ) then
        return new;
      end if;

      if new.unit_attempt_id is not null then
        perform 1
        from atlas.execution_unit unit
        where unit.id = new.execution_unit_id
          and unit.task_run_id = new.task_run_id
          and unit.tenant_id = new.tenant_id
          and unit.project_id = new.project_id
        for update;
        if not found then
          raise exception 'task run event unit scope is invalid';
        end if;

        select
          attempt.lifecycle, attempt.quality, attempt.hygiene,
          greatest(
            attempt.queued_at, attempt.started_at, attempt.finalized_at,
            attempt.closed_at,
            attempt.cleanup_resolved_at
          )
        into
          target_lifecycle, target_quality, target_hygiene,
          target_occurred_floor
        from atlas.unit_attempt attempt
        where attempt.id = new.unit_attempt_id
          and attempt.execution_unit_id = new.execution_unit_id
          and attempt.task_run_id = new.task_run_id
          and attempt.tenant_id = new.tenant_id
          and attempt.project_id = new.project_id
        for update;
        if not found then
          raise exception 'task run event attempt scope is invalid';
        end if;
      elsif new.execution_unit_id is not null then
        select
          unit.lifecycle,
          unit.quality,
          unit.hygiene,
          greatest(
            unit.created_at, unit.started_at, unit.finalized_at,
            unit.closed_at, unit.cleanup_resolved_at
          )
        into
          target_lifecycle, target_quality, target_hygiene,
          target_occurred_floor
        from atlas.execution_unit unit
        where unit.id = new.execution_unit_id
          and unit.task_run_id = new.task_run_id
          and unit.tenant_id = new.tenant_id
          and unit.project_id = new.project_id
        for update;
        if not found then
          raise exception 'task run event unit scope is invalid';
        end if;
      end if;

      if row(new.lifecycle, new.quality, new.hygiene)
        is distinct from row(target_lifecycle, target_quality, target_hygiene)
      then
        raise exception 'task run event state must match its narrowest scope';
      end if;
      select coalesce(max(event.seq), 0) + 1
      into expected_seq
      from atlas.task_run_event event
      where event.task_run_id = new.task_run_id;
      if new.seq <> expected_seq then
        raise exception 'task run event sequence must be gapless';
      end if;
      if new.occurred_at < target_occurred_floor then
        raise exception 'task run event cannot predate its narrowest scope';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_plan_guard_update
      before update on atlas.task_plan
      for each row execute function atlas.guard_task_plan_update()
    """,
    """
    create trigger task_plan_set_updated_at
      before update on atlas.task_plan
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger task_plan_prevent_delete
      before delete on atlas.task_plan
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger task_plan_version_prevent_mutation
      before update or delete on atlas.task_plan_version
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger task_plan_version_guard_insert
      before insert on atlas.task_plan_version
      for each row execute function atlas.guard_task_plan_version_insert()
    """,
    """
    create trigger task_run_guard_insert
      before insert on atlas.task_run
      for each row execute function atlas.guard_task_run_insert()
    """,
    """
    create trigger task_run_guard_update
      before update on atlas.task_run
      for each row execute function atlas.guard_task_run_update()
    """,
    """
    create trigger task_run_set_updated_at
      before update on atlas.task_run
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger task_run_prevent_delete
      before delete on atlas.task_run
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger task_run_manifest_guard_insert
      before insert on atlas.task_run_manifest
      for each row execute function atlas.guard_task_run_manifest_insert()
    """,
    """
    create trigger task_run_manifest_prevent_mutation
      before update or delete on atlas.task_run_manifest
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger execution_unit_guard_insert
      before insert on atlas.execution_unit
      for each row execute function atlas.guard_execution_unit_insert()
    """,
    """
    create trigger execution_unit_guard_update
      before update on atlas.execution_unit
      for each row execute function atlas.guard_execution_unit_update()
    """,
    """
    create trigger execution_unit_set_updated_at
      before update on atlas.execution_unit
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger execution_unit_prevent_delete
      before delete on atlas.execution_unit
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger unit_attempt_guard_insert
      before insert on atlas.unit_attempt
      for each row execute function atlas.guard_unit_attempt_insert()
    """,
    """
    create trigger unit_attempt_guard_update
      before update on atlas.unit_attempt
      for each row execute function atlas.guard_unit_attempt_update()
    """,
    """
    create trigger unit_attempt_set_updated_at
      before update on atlas.unit_attempt
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger unit_attempt_prevent_delete
      before delete on atlas.unit_attempt
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger task_run_event_guard_insert
      before insert on atlas.task_run_event
      for each row execute function atlas.guard_task_run_event_insert()
    """,
    """
    create trigger task_run_event_prevent_mutation
      before update or delete on atlas.task_run_event
      for each row execute function atlas.prevent_fact_mutation()
    """,
    "alter table atlas.task_plan enable row level security",
    "alter table atlas.task_plan force row level security",
    "alter table atlas.task_plan_version enable row level security",
    "alter table atlas.task_plan_version force row level security",
    "alter table atlas.task_run enable row level security",
    "alter table atlas.task_run force row level security",
    "alter table atlas.task_run_manifest enable row level security",
    "alter table atlas.task_run_manifest force row level security",
    "alter table atlas.execution_unit enable row level security",
    "alter table atlas.execution_unit force row level security",
    "alter table atlas.unit_attempt enable row level security",
    "alter table atlas.unit_attempt force row level security",
    "alter table atlas.task_run_event enable row level security",
    "alter table atlas.task_run_event force row level security",
    """
    create policy task_plan_tenant_isolation
      on atlas.task_plan for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy task_plan_version_tenant_isolation
      on atlas.task_plan_version for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy task_run_tenant_isolation
      on atlas.task_run for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy task_run_manifest_tenant_isolation
      on atlas.task_run_manifest for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy execution_unit_tenant_isolation
      on atlas.execution_unit for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy unit_attempt_tenant_isolation
      on atlas.unit_attempt for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy task_run_event_tenant_isolation
      on atlas.task_run_event for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.task_plan from atlas_app",
    "revoke all on atlas.task_plan_version from atlas_app",
    "revoke all on atlas.task_run from atlas_app",
    "revoke all on atlas.task_run_manifest from atlas_app",
    "revoke all on atlas.execution_unit from atlas_app",
    "revoke all on atlas.unit_attempt from atlas_app",
    "revoke all on atlas.task_run_event from atlas_app",
    "grant select, insert on atlas.task_plan to atlas_app",
    "grant update (name, status, revision) on atlas.task_plan to atlas_app",
    "grant select, insert on atlas.task_plan_version to atlas_app",
    "grant select, insert on atlas.task_run to atlas_app",
    """
    grant update (
      lifecycle, quality, hygiene, started_at, finalized_at,
      cleanup_resolved_at, closed_at, revision
    ) on atlas.task_run to atlas_app
    """,
    "grant select, insert on atlas.task_run_manifest to atlas_app",
    "grant select, insert on atlas.execution_unit to atlas_app",
    """
    grant update (
      lifecycle, quality, hygiene, started_at, finalized_at,
      cleanup_resolved_at, closed_at, revision
    ) on atlas.execution_unit to atlas_app
    """,
    "grant select, insert on atlas.unit_attempt to atlas_app",
    """
    grant update (
      lifecycle, quality, hygiene, started_at, finalized_at,
      cleanup_resolved_at, closed_at, revision
    ) on atlas.unit_attempt to atlas_app
    """,
    "grant select, insert on atlas.task_run_event to atlas_app",
    "revoke all on function atlas.task_uuid_json_array_valid(jsonb) from public",
    "revoke all on function atlas.task_json_object_size(jsonb) from public",
    "revoke all on function atlas.task_policy_digests_valid(jsonb) from public",
    "revoke all on function atlas.task_profile_refs_valid(jsonb, uuid[]) from public",
    "revoke all on function atlas.task_manifest_units_valid(jsonb) from public",
    """
    revoke all on function atlas.task_execution_state_valid(
      text, text, text, timestamptz, timestamptz, timestamptz,
      timestamptz, timestamptz, timestamptz
    ) from public
    """,
    """
    revoke all on function atlas.task_lifecycle_transition_valid(text, text)
      from public
    """,
    """
    revoke all on function atlas.task_hygiene_transition_valid(text, text)
      from public
    """,
    "grant execute on function atlas.task_uuid_json_array_valid(jsonb) to atlas_app",
    "grant execute on function atlas.task_json_object_size(jsonb) to atlas_app",
    "grant execute on function atlas.task_policy_digests_valid(jsonb) to atlas_app",
    "grant execute on function atlas.task_profile_refs_valid(jsonb, uuid[]) to atlas_app",
    "grant execute on function atlas.task_manifest_units_valid(jsonb) to atlas_app",
    """
    grant execute on function atlas.task_execution_state_valid(
      text, text, text, timestamptz, timestamptz, timestamptz,
      timestamptz, timestamptz, timestamptz
    ) to atlas_app
    """,
    """
    grant execute on function atlas.task_lifecycle_transition_valid(text, text)
      to atlas_app
    """,
    """
    grant execute on function atlas.task_hygiene_transition_valid(text, text)
      to atlas_app
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    alter table atlas.task_run
      drop constraint if exists task_run_manifest_reverse_scope_fk
    """,
    "drop table if exists atlas.task_run_event",
    "drop table if exists atlas.unit_attempt",
    "drop table if exists atlas.execution_unit",
    "drop table if exists atlas.task_run_manifest",
    "drop table if exists atlas.task_run",
    "drop table if exists atlas.task_plan_version",
    "drop table if exists atlas.task_plan",
    "drop function if exists atlas.guard_task_run_event_insert()",
    "drop function if exists atlas.guard_unit_attempt_update()",
    "drop function if exists atlas.guard_unit_attempt_insert()",
    "drop function if exists atlas.guard_execution_unit_update()",
    "drop function if exists atlas.guard_execution_unit_insert()",
    "drop function if exists atlas.guard_task_run_manifest_insert()",
    "drop function if exists atlas.guard_task_run_update()",
    "drop function if exists atlas.guard_task_run_insert()",
    "drop function if exists atlas.guard_task_plan_version_insert()",
    "drop function if exists atlas.guard_task_plan_update()",
    """
    drop function if exists atlas.task_execution_state_valid(
      text, text, text, timestamptz, timestamptz, timestamptz,
      timestamptz, timestamptz, timestamptz
    )
    """,
    "drop function if exists atlas.task_lifecycle_transition_valid(text, text)",
    "drop function if exists atlas.task_hygiene_transition_valid(text, text)",
    "drop function if exists atlas.task_manifest_units_valid(jsonb)",
    "drop function if exists atlas.task_profile_refs_valid(jsonb, uuid[])",
    "drop function if exists atlas.task_policy_digests_valid(jsonb)",
    "drop function if exists atlas.task_uuid_json_array_valid(jsonb)",
    "drop function if exists atlas.task_json_object_size(jsonb)",
    """
    alter table atlas.case_version
      drop constraint if exists case_version_task_scope_unique
    """,
)


def upgrade() -> None:
    """Create tenant-scoped Task execution host facts."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove Task execution hosts in reverse dependency order."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
