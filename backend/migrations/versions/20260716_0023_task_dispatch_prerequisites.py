# ruff: noqa: E501
"""Add fail-closed Task dispatch profiles and concurrency prerequisites.

Revision ID: 20260716_0023
Revises: 20260716_0022
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_0023"
down_revision: str | None = "20260716_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    "create extension if not exists pgcrypto",
    """
    create function atlas.task_canonical_json(value jsonb)
    returns text
    language plpgsql
    immutable
    strict
    set search_path = pg_catalog
    as $$
    declare
      result text;
    begin
      case jsonb_typeof(value)
        when 'object' then
          select '{' || coalesce(
            string_agg(
              to_jsonb(item.key)::text || ':' || atlas.task_canonical_json(item.value),
              ',' order by item.key collate "C"
            ),
            ''
          ) || '}'
          into result
          from jsonb_each(value) item(key, value);
          return result;
        when 'array' then
          select '[' || coalesce(
            string_agg(
              atlas.task_canonical_json(item.value),
              ',' order by item.ordinality
            ),
            ''
          ) || ']'
          into result
          from jsonb_array_elements(value) with ordinality item(value, ordinality);
          return result;
        else
          return value::text;
      end case;
    end;
    $$
    """,
    """
    create function atlas.task_sha256_json(value jsonb)
    returns text
    language sql
    immutable
    strict
    set search_path = pg_catalog
    as $$
      select 'sha256:' || encode(
        public.digest(
          convert_to(atlas.task_canonical_json(value), 'UTF8'),
          'sha256'
        ),
        'hex'
      )
    $$
    """,
    """
    create function atlas.task_json_has_sensitive_keys(input_value jsonb)
    returns boolean
    language plpgsql
    immutable
    strict
    set search_path = pg_catalog
    as $$
    declare
      item record;
      normalized_key text;
    begin
      if jsonb_typeof(input_value) = 'object' then
        for item in
          select entry.key, entry.value
          from jsonb_each(input_value) entry(key, value)
        loop
          normalized_key := lower(regexp_replace(item.key, '[^A-Za-z0-9]+', '', 'g'));
          if normalized_key ~ '(password|passwd|secret|credential|authorization|cookie|session|token|totp|otp|account|lease|login)'
            or normalized_key in ('apikey', 'accesskey', 'privatekey', 'storagestate')
          then
            return true;
          end if;
          if atlas.task_json_has_sensitive_keys(item.value) then
            return true;
          end if;
        end loop;
      elsif jsonb_typeof(input_value) = 'array' then
        for item in
          select element.value
          from jsonb_array_elements(input_value) element(value)
        loop
          if atlas.task_json_has_sensitive_keys(item.value) then
            return true;
          end if;
        end loop;
      end if;
      return false;
    end;
    $$
    """,
    """
    create function atlas.task_profile_lifecycle_valid(
      status text,
      published_at timestamptz,
      deprecated_at timestamptz,
      revoked_at timestamptz,
      created_at timestamptz,
      updated_at timestamptz
    ) returns boolean
    language sql
    immutable
    set search_path = pg_catalog
    as $$
      select
        status in ('PUBLISHED', 'DEPRECATED', 'REVOKED')
        and created_at <= published_at
        and published_at <= updated_at
        and (deprecated_at is null or deprecated_at between published_at and updated_at)
        and (revoked_at is null or revoked_at between published_at and updated_at)
        and (deprecated_at is null or revoked_at is null or deprecated_at <= revoked_at)
        and (
          (status = 'PUBLISHED' and deprecated_at is null and revoked_at is null)
          or (status = 'DEPRECATED' and deprecated_at is not null and revoked_at is null)
          or (status = 'REVOKED' and revoked_at is not null)
        )
    $$
    """,
    """
    create function atlas.task_profile_ref_valid(
      version_ref text,
      kind text,
      profile_key text,
      version text
    ) returns boolean
    language sql
    immutable
    strict
    set search_path = pg_catalog
    as $$
      select
        version_ref = kind || '-profile/' || profile_key || '@' || version
        and octet_length(version_ref) <= 256
    $$
    """,
    """
    create function atlas.task_profile_refs_v2_valid(value jsonb, pinned uuid[])
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
        select item.value from jsonb_array_elements(value -> 'caseProfiles') item(value)
      loop
        if jsonb_typeof(profile) is distinct from 'object'
          or atlas.task_json_object_size(profile) <> 3
          or not (profile ?& array[
            'caseVersionId', 'executionProfileVersionId', 'fixtureBlueprintVersionId'
          ])
          or profile ->> 'caseVersionId' is null
          or profile ->> 'caseVersionId' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
          or profile ->> 'executionProfileVersionId' is null
          or profile ->> 'executionProfileVersionId' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
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
      when others then return false;
    end;
    $$
    """,
    """
    create function atlas.task_manifest_units_v2_valid(value jsonb)
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
        or jsonb_array_length(value) not between 1 and 64
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
          or unit ->> 'unitKey' !~ '^sha256:[0-9a-f]{64}$'
          or unit ->> 'parameterDigest' is null
          or unit ->> 'parameterDigest' !~ '^sha256:[0-9a-f]{64}$'
          or unit ->> 'dependencyDigest' is null
          or unit ->> 'dependencyDigest' !~ '^sha256:[0-9a-f]{64}$'
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
      when others then return false;
    end;
    $$
    """,
    """
    create table atlas.execution_profile_version (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      case_version_id uuid not null,
      schema_version text not null default 'atlas.execution-profile/0.1',
      profile_key text not null,
      version text not null,
      version_ref text not null,
      status text not null default 'PUBLISHED',
      case_content_digest text not null,
      test_ir_digest text not null,
      plan_digest text not null,
      compiled_digest text not null,
      model jsonb not null,
      tools jsonb not null,
      supported_features text[] not null default '{}',
      content_digest text not null,
      published_by uuid not null,
      published_at timestamptz not null,
      deprecated_at timestamptz,
      revoked_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint execution_profile_case_scope_fk foreign key (
        case_version_id, tenant_id, project_id
      ) references atlas.case_version (id, tenant_id, project_id) on delete restrict,
      constraint execution_profile_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint execution_profile_case_scope_unique unique (
        id, case_version_id, tenant_id, project_id
      ),
      constraint execution_profile_version_unique unique (
        tenant_id, project_id, profile_key, version
      ),
      constraint execution_profile_ref_unique unique (
        tenant_id, project_id, version_ref
      ),
      constraint execution_profile_schema_valid check (
        schema_version = 'atlas.execution-profile/0.1'
      ),
      constraint execution_profile_key_valid check (
        profile_key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){0,7}$'
        and char_length(profile_key) between 3 and 80
      ),
      constraint execution_profile_semver_valid check (
        version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint execution_profile_ref_valid check (
        atlas.task_profile_ref_valid(version_ref, 'execution', profile_key, version)
      ),
      constraint execution_profile_digest_valid check (
        case_content_digest ~ '^sha256:[0-9a-f]{64}$'
        and test_ir_digest ~ '^sha256:[0-9a-f]{64}$'
        and plan_digest ~ '^sha256:[0-9a-f]{64}$'
        and compiled_digest ~ '^sha256:[0-9a-f]{64}$'
        and content_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint execution_profile_contract_valid check (
        jsonb_typeof(model) = 'object'
        and jsonb_typeof(tools) = 'object'
        and not atlas.task_json_has_sensitive_keys(model)
        and not atlas.task_json_has_sensitive_keys(tools)
        and cardinality(supported_features) <= 64
        and array_position(supported_features, null) is null
      ),
      constraint execution_profile_lifecycle_valid check (
        atlas.task_profile_lifecycle_valid(
          status, published_at, deprecated_at, revoked_at, created_at, updated_at
        )
      ),
      constraint execution_profile_revision_valid check (revision > 0)
    )
    """,
    """
    create table atlas.identity_profile_version (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      case_version_id uuid not null,
      schema_version text not null default 'atlas.identity-profile/0.1',
      profile_key text not null,
      version text not null,
      version_ref text not null,
      status text not null default 'PUBLISHED',
      case_content_digest text not null,
      content_digest text not null,
      published_by uuid not null,
      published_at timestamptz not null,
      deprecated_at timestamptz,
      revoked_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint identity_profile_case_scope_fk foreign key (
        case_version_id, tenant_id, project_id
      ) references atlas.case_version (id, tenant_id, project_id) on delete restrict,
      constraint identity_profile_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint identity_profile_case_scope_unique unique (
        id, case_version_id, tenant_id, project_id
      ),
      constraint identity_profile_version_unique unique (
        tenant_id, project_id, profile_key, version
      ),
      constraint identity_profile_ref_unique unique (
        tenant_id, project_id, version_ref
      ),
      constraint identity_profile_schema_valid check (
        schema_version = 'atlas.identity-profile/0.1'
      ),
      constraint identity_profile_key_valid check (
        profile_key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){0,7}$'
        and char_length(profile_key) between 3 and 80
      ),
      constraint identity_profile_semver_valid check (
        version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint identity_profile_ref_valid check (
        atlas.task_profile_ref_valid(version_ref, 'identity', profile_key, version)
      ),
      constraint identity_profile_digest_valid check (
        case_content_digest ~ '^sha256:[0-9a-f]{64}$'
        and content_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint identity_profile_lifecycle_valid check (
        atlas.task_profile_lifecycle_valid(
          status, published_at, deprecated_at, revoked_at, created_at, updated_at
        )
      ),
      constraint identity_profile_revision_valid check (revision > 0)
    )
    """,
    """
    create table atlas.identity_profile_actor_binding (
      identity_profile_version_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      actor_slot text not null,
      ordinal integer not null,
      role_id uuid not null,
      role_key text not null,
      role_revision bigint not null,
      capabilities text[] not null default '{}',
      constraint identity_profile_actor_binding_pk primary key (
        identity_profile_version_id, actor_slot
      ),
      constraint identity_profile_actor_profile_scope_fk foreign key (
        identity_profile_version_id, tenant_id, project_id
      ) references atlas.identity_profile_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint identity_profile_actor_role_scope_fk foreign key (
        role_id, tenant_id, project_id
      ) references atlas.test_role (id, tenant_id, project_id) on delete restrict,
      constraint identity_profile_actor_ordinal_unique unique (
        identity_profile_version_id, ordinal
      ),
      constraint identity_profile_actor_slot_valid check (
        actor_slot ~ '^[A-Za-z_][A-Za-z0-9_.-]{1,79}$'
      ),
      constraint identity_profile_actor_ordinal_valid check (ordinal between 1 and 8),
      constraint identity_profile_actor_role_valid check (
        role_key ~ '^[a-z][a-z0-9._-]{1,79}$'
        and role_revision > 0
        and cardinality(capabilities) <= 64
        and array_position(capabilities, null) is null
      )
    )
    """,
    """
    create table atlas.browser_profile_version (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      schema_version text not null default 'atlas.browser-profile/0.1',
      profile_key text not null,
      version text not null,
      version_ref text not null,
      status text not null default 'PUBLISHED',
      engine text not null default 'chromium',
      browser_revision text not null,
      viewport jsonb not null,
      locale text not null,
      timezone text not null,
      runtime_image_digest text,
      capability_digest text,
      content_digest text not null,
      published_by uuid not null,
      published_at timestamptz not null,
      deprecated_at timestamptz,
      revoked_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint browser_profile_project_scope_fk foreign key (
        project_id, tenant_id
      ) references atlas.project (id, tenant_id) on delete restrict,
      constraint browser_profile_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint browser_profile_version_unique unique (
        tenant_id, project_id, profile_key, version
      ),
      constraint browser_profile_ref_unique unique (
        tenant_id, project_id, version_ref
      ),
      constraint browser_profile_schema_valid check (
        schema_version = 'atlas.browser-profile/0.1'
      ),
      constraint browser_profile_key_valid check (
        profile_key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){0,7}$'
        and char_length(profile_key) between 3 and 80
      ),
      constraint browser_profile_semver_valid check (
        version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint browser_profile_ref_valid check (
        atlas.task_profile_ref_valid(version_ref, 'browser', profile_key, version)
      ),
      constraint browser_profile_browser_valid check (
        engine = 'chromium'
        and browser_revision ~ '^[A-Za-z0-9][A-Za-z0-9._:@/+=-]{0,159}$'
        and jsonb_typeof(viewport) = 'object'
        and atlas.task_json_object_size(viewport) = 3
        and viewport ?& array['width', 'height', 'deviceScaleFactor']
        and (viewport ->> 'width')::integer between 320 and 7680
        and (viewport ->> 'height')::integer between 320 and 4320
        and (viewport ->> 'deviceScaleFactor')::numeric between 0.5 and 4.0
        and locale ~ '^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$'
        and timezone ~ '^[A-Za-z0-9_+./-]+$'
      ),
      constraint browser_profile_attestation_valid check (
        (runtime_image_digest is not null or capability_digest is not null)
        and (runtime_image_digest is null or runtime_image_digest ~ '^sha256:[0-9a-f]{64}$')
        and (capability_digest is null or capability_digest ~ '^sha256:[0-9a-f]{64}$')
        and content_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint browser_profile_lifecycle_valid check (
        atlas.task_profile_lifecycle_valid(
          status, published_at, deprecated_at, revoked_at, created_at, updated_at
        )
      ),
      constraint browser_profile_revision_valid check (revision > 0)
    )
    """,
    """
    create table atlas.data_profile_version (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      blueprint_version_id uuid not null,
      schema_version text not null default 'atlas.data-profile/0.1',
      profile_key text not null,
      version text not null,
      version_ref text not null,
      status text not null default 'PUBLISHED',
      blueprint_version_ref text not null,
      blueprint_content_digest text not null,
      plan_digest text not null,
      run_inputs jsonb not null,
      input_digest text not null,
      content_digest text not null,
      published_by uuid not null,
      published_at timestamptz not null,
      deprecated_at timestamptz,
      revoked_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint data_profile_blueprint_scope_fk foreign key (
        blueprint_version_id, tenant_id, project_id
      ) references atlas.data_blueprint_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint data_profile_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint data_profile_blueprint_scope_unique unique (
        id, blueprint_version_id, tenant_id, project_id
      ),
      constraint data_profile_version_unique unique (
        tenant_id, project_id, profile_key, version
      ),
      constraint data_profile_ref_unique unique (
        tenant_id, project_id, version_ref
      ),
      constraint data_profile_schema_valid check (
        schema_version = 'atlas.data-profile/0.1'
      ),
      constraint data_profile_key_valid check (
        profile_key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){0,7}$'
        and char_length(profile_key) between 3 and 80
      ),
      constraint data_profile_semver_valid check (
        version ~ '^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$'
      ),
      constraint data_profile_ref_valid check (
        atlas.task_profile_ref_valid(version_ref, 'data', profile_key, version)
      ),
      constraint data_profile_blueprint_ref_valid check (
        btrim(blueprint_version_ref) <> '' and octet_length(blueprint_version_ref) <= 256
      ),
      constraint data_profile_contract_valid check (
        blueprint_content_digest ~ '^sha256:[0-9a-f]{64}$'
        and plan_digest ~ '^sha256:[0-9a-f]{64}$'
        and input_digest ~ '^sha256:[0-9a-f]{64}$'
        and content_digest ~ '^sha256:[0-9a-f]{64}$'
        and jsonb_typeof(run_inputs) = 'object'
        and atlas.task_json_object_size(run_inputs) <= 128
        and not atlas.task_json_has_sensitive_keys(run_inputs)
      ),
      constraint data_profile_lifecycle_valid check (
        atlas.task_profile_lifecycle_valid(
          status, published_at, deprecated_at, revoked_at, created_at, updated_at
        )
      ),
      constraint data_profile_revision_valid check (revision > 0)
    )
    """,
)

UPGRADE_STATEMENTS += (
    """
    create function atlas.task_profile_content_digest(
      schema_version text,
      tenant_id uuid,
      project_id uuid,
      profile_key text,
      version text,
      version_ref text,
      contract jsonb
    ) returns text
    language sql
    immutable
    strict
    set search_path = pg_catalog
    as $$
      select atlas.task_sha256_json(jsonb_build_object(
        'schemaVersion', schema_version,
        'tenantId', tenant_id::text,
        'projectId', project_id::text,
        'profileKey', profile_key,
        'version', version,
        'versionRef', version_ref,
        'contract', contract
      ))
    $$
    """,
    """
    create function atlas.task_identity_profile_content_digest(profile_id uuid)
    returns text
    language plpgsql
    stable
    set search_path = pg_catalog, atlas
    as $$
    declare
      profile atlas.identity_profile_version%rowtype;
      actor_contract jsonb;
      actor_count integer;
    begin
      select * into profile
      from atlas.identity_profile_version stored
      where stored.id = profile_id;
      if not found then
        return null;
      end if;

      select
        jsonb_agg(
          jsonb_build_object(
            'actorSlot', actor.actor_slot,
            'roleId', actor.role_id::text,
            'roleKey', actor.role_key,
            'roleRevision', actor.role_revision,
            'capabilities', to_jsonb(actor.capabilities)
          ) order by actor.actor_slot collate "C"
        ),
        count(*)::integer
      into actor_contract, actor_count
      from atlas.identity_profile_actor_binding actor
      where actor.identity_profile_version_id = profile.id;

      if actor_count not between 1 and 8 then
        return null;
      end if;
      return atlas.task_profile_content_digest(
        profile.schema_version,
        profile.tenant_id,
        profile.project_id,
        profile.profile_key,
        profile.version,
        profile.version_ref,
        jsonb_build_object(
          'caseVersionId', profile.case_version_id::text,
          'caseContentDigest', profile.case_content_digest,
          'actors', actor_contract
        )
      );
    end;
    $$
    """,
    """
    create function atlas.guard_execution_profile_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_case atlas.case_version%rowtype;
      expected_digest text;
    begin
      if new.revision <> 1 then
        raise exception 'execution profile must be published at revision one';
      end if;
      select * into stored_case
      from atlas.case_version version
      where version.id = new.case_version_id
        and version.tenant_id = new.tenant_id
        and version.project_id = new.project_id
        and version.status = 'PUBLISHED';
      if not found
        or row(
          new.case_content_digest,
          new.test_ir_digest,
          new.plan_digest,
          new.compiled_digest
        ) is distinct from row(
          stored_case.content_digest,
          stored_case.test_ir_digest,
          stored_case.plan_digest,
          stored_case.compiled_digest
        )
      then
        raise exception 'execution profile requires exact published CaseVersion digests';
      end if;
      expected_digest := atlas.task_profile_content_digest(
        new.schema_version,
        new.tenant_id,
        new.project_id,
        new.profile_key,
        new.version,
        new.version_ref,
        jsonb_build_object(
          'caseVersionId', new.case_version_id::text,
          'caseContentDigest', new.case_content_digest,
          'testIrDigest', new.test_ir_digest,
          'planDigest', new.plan_digest,
          'compiledDigest', new.compiled_digest,
          'model', new.model,
          'tools', new.tools,
          'supportedFeatures', to_jsonb(new.supported_features)
        )
      );
      if new.content_digest is distinct from expected_digest then
        raise exception 'execution profile content digest is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_identity_profile_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_case_digest text;
    begin
      if new.revision <> 1 then
        raise exception 'identity profile must be published at revision one';
      end if;
      select version.content_digest into stored_case_digest
      from atlas.case_version version
      where version.id = new.case_version_id
        and version.tenant_id = new.tenant_id
        and version.project_id = new.project_id
        and version.status = 'PUBLISHED';
      if not found or new.case_content_digest is distinct from stored_case_digest then
        raise exception 'identity profile requires the exact published CaseVersion digest';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_identity_profile_actor_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      expected_ordinal integer;
      stored_profile_digest text;
      current_profile_digest text;
      stored_role_key text;
      stored_role_revision bigint;
      stored_capabilities text[];
    begin
      select profile.content_digest into stored_profile_digest
      from atlas.identity_profile_version profile
      where profile.id = new.identity_profile_version_id
        and profile.tenant_id = new.tenant_id
        and profile.project_id = new.project_id
        and profile.status = 'PUBLISHED';
      if not found then
        raise exception 'identity actor requires its exact published profile';
      end if;
      current_profile_digest := atlas.task_identity_profile_content_digest(
        new.identity_profile_version_id
      );
      if current_profile_digest is not null
        and current_profile_digest = stored_profile_digest
      then
        raise exception 'identity profile actor bindings are already finalized';
      end if;

      select role.role_key, role.revision, role.capabilities
      into stored_role_key, stored_role_revision, stored_capabilities
      from atlas.test_role role
      where role.id = new.role_id
        and role.tenant_id = new.tenant_id
        and role.project_id = new.project_id
        and role.status = 'ACTIVE';
      if not found
        or row(new.role_key, new.role_revision, new.capabilities)
          is distinct from row(stored_role_key, stored_role_revision, stored_capabilities)
      then
        raise exception 'identity actor requires the exact active TestRole snapshot';
      end if;

      select count(*)::integer + 1 into expected_ordinal
      from atlas.identity_profile_actor_binding actor
      where actor.identity_profile_version_id = new.identity_profile_version_id;
      if new.ordinal <> expected_ordinal
        or new.ordinal > 8
        or exists (
          select 1
          from atlas.identity_profile_actor_binding actor
          where actor.identity_profile_version_id = new.identity_profile_version_id
            and actor.actor_slot >= new.actor_slot collate "C"
        )
      then
        raise exception 'identity actor bindings must be inserted in canonical slot order';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_browser_profile_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      expected_digest text;
    begin
      if new.revision <> 1 then
        raise exception 'browser profile must be published at revision one';
      end if;
      expected_digest := atlas.task_profile_content_digest(
        new.schema_version,
        new.tenant_id,
        new.project_id,
        new.profile_key,
        new.version,
        new.version_ref,
        jsonb_build_object(
          'engine', new.engine,
          'revision', new.browser_revision,
          'viewport', new.viewport,
          'locale', new.locale,
          'timezone', new.timezone,
          'runtimeImageDigest', new.runtime_image_digest,
          'capabilityDigest', new.capability_digest
        )
      );
      if new.content_digest is distinct from expected_digest then
        raise exception 'browser profile content digest is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_data_profile_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_ref text;
      stored_content_digest text;
      stored_plan_digest text;
      expected_digest text;
    begin
      if new.revision <> 1 then
        raise exception 'data profile must be published at revision one';
      end if;
      select
        definition.blueprint_key || '@' || version.version,
        version.content_digest,
        version.plan_digest
      into stored_ref, stored_content_digest, stored_plan_digest
      from atlas.data_blueprint_version version
      join atlas.data_blueprint_definition definition
        on definition.id = version.blueprint_id
       and definition.tenant_id = version.tenant_id
       and definition.project_id = version.project_id
      where version.id = new.blueprint_version_id
        and version.tenant_id = new.tenant_id
        and version.project_id = new.project_id
        and version.status = 'PUBLISHED'
        and definition.status = 'ACTIVE';
      if not found
        or row(
          new.blueprint_version_ref,
          new.blueprint_content_digest,
          new.plan_digest
        ) is distinct from row(stored_ref, stored_content_digest, stored_plan_digest)
      then
        raise exception 'data profile requires the exact published Fixture blueprint';
      end if;
      if new.input_digest is distinct from atlas.task_sha256_json(new.run_inputs) then
        raise exception 'data profile input digest is not canonical';
      end if;
      expected_digest := atlas.task_profile_content_digest(
        new.schema_version,
        new.tenant_id,
        new.project_id,
        new.profile_key,
        new.version,
        new.version_ref,
        jsonb_build_object(
          'blueprintVersionId', new.blueprint_version_id::text,
          'blueprintVersionRef', new.blueprint_version_ref,
          'blueprintContentDigest', new.blueprint_content_digest,
          'planDigest', new.plan_digest,
          'runInputs', new.run_inputs,
          'inputDigest', new.input_digest
        )
      );
      if new.content_digest is distinct from expected_digest then
        raise exception 'data profile content digest is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_profile_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog
    as $$
    begin
      if (to_jsonb(new) - 'status' - 'deprecated_at' - 'revoked_at' - 'revision' - 'updated_at')
        is distinct from
        (to_jsonb(old) - 'status' - 'deprecated_at' - 'revoked_at' - 'revision' - 'updated_at')
      then
        raise exception 'task profile identity and content are immutable';
      end if;
      if not (
        new.status = old.status
        or (old.status = 'PUBLISHED' and new.status in ('DEPRECATED', 'REVOKED'))
        or (old.status = 'DEPRECATED' and new.status = 'REVOKED')
      ) then
        raise exception 'task profile status transition is invalid';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'task profile revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger execution_profile_guard_insert
      before insert on atlas.execution_profile_version
      for each row execute function atlas.guard_execution_profile_insert()
    """,
    """
    create trigger execution_profile_guard_update
      before update on atlas.execution_profile_version
      for each row execute function atlas.guard_task_profile_update()
    """,
    """
    create trigger execution_profile_set_updated_at
      before update on atlas.execution_profile_version
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger execution_profile_prevent_delete
      before delete on atlas.execution_profile_version
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger identity_profile_guard_insert
      before insert on atlas.identity_profile_version
      for each row execute function atlas.guard_identity_profile_insert()
    """,
    """
    create trigger identity_profile_guard_update
      before update on atlas.identity_profile_version
      for each row execute function atlas.guard_task_profile_update()
    """,
    """
    create trigger identity_profile_set_updated_at
      before update on atlas.identity_profile_version
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger identity_profile_prevent_delete
      before delete on atlas.identity_profile_version
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger identity_profile_actor_guard_insert
      before insert on atlas.identity_profile_actor_binding
      for each row execute function atlas.guard_identity_profile_actor_insert()
    """,
    """
    create trigger identity_profile_actor_prevent_mutation
      before update or delete on atlas.identity_profile_actor_binding
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger browser_profile_guard_insert
      before insert on atlas.browser_profile_version
      for each row execute function atlas.guard_browser_profile_insert()
    """,
    """
    create trigger browser_profile_guard_update
      before update on atlas.browser_profile_version
      for each row execute function atlas.guard_task_profile_update()
    """,
    """
    create trigger browser_profile_set_updated_at
      before update on atlas.browser_profile_version
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger browser_profile_prevent_delete
      before delete on atlas.browser_profile_version
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger data_profile_guard_insert
      before insert on atlas.data_profile_version
      for each row execute function atlas.guard_data_profile_insert()
    """,
    """
    create trigger data_profile_guard_update
      before update on atlas.data_profile_version
      for each row execute function atlas.guard_task_profile_update()
    """,
    """
    create trigger data_profile_set_updated_at
      before update on atlas.data_profile_version
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger data_profile_prevent_delete
      before delete on atlas.data_profile_version
      for each row execute function atlas.prevent_fact_mutation()
    """,
    "create index execution_profile_case_scope_fk_idx on atlas.execution_profile_version (case_version_id, tenant_id, project_id)",
    "create index identity_profile_case_scope_fk_idx on atlas.identity_profile_version (case_version_id, tenant_id, project_id)",
    "create index identity_profile_actor_role_scope_fk_idx on atlas.identity_profile_actor_binding (role_id, tenant_id, project_id)",
    "create index data_profile_blueprint_scope_fk_idx on atlas.data_profile_version (blueprint_version_id, tenant_id, project_id)",
    "create index execution_profile_admission_idx on atlas.execution_profile_version (tenant_id, project_id, status, case_version_id, id)",
    "create index identity_profile_admission_idx on atlas.identity_profile_version (tenant_id, project_id, status, case_version_id, id)",
    "create index browser_profile_admission_idx on atlas.browser_profile_version (tenant_id, project_id, status, id)",
    "create index data_profile_admission_idx on atlas.data_profile_version (tenant_id, project_id, status, blueprint_version_id, id)",
)

UPGRADE_STATEMENTS += (
    "alter table atlas.task_plan_version drop constraint task_plan_version_profiles_valid",
    """
    alter table atlas.task_plan_version
      add constraint task_plan_version_profiles_v2_valid check (
        atlas.task_profile_refs_v2_valid(profile_refs, pinned_case_version_ids)
      ) not valid
    """,
    "alter table atlas.task_run_manifest drop constraint task_run_manifest_units_valid",
    """
    alter table atlas.task_run_manifest
      add constraint task_run_manifest_units_v2_valid check (
        atlas.task_manifest_units_v2_valid(units)
        and unit_count = jsonb_array_length(units)
        and unit_count between 1 and 64
      ) not valid
    """,
    """
    alter table atlas.task_run
      add column request_digest text,
      add column materialization_state text,
      add column materialized_unit_count integer,
      add column materialized_first_attempt_count integer,
      add column materialization_sealed_at timestamptz,
      add column temporal_namespace text,
      add column legacy_unsealed boolean
    """,
    "alter table atlas.task_run disable trigger task_run_guard_update",
    "alter table atlas.task_run disable trigger task_run_set_updated_at",
    """
    update atlas.task_run
    set
      materialization_state = 'MATERIALIZING',
      legacy_unsealed = true,
      temporal_namespace = case
        when temporal_workflow_id is null then null
        else 'atlas-task'
      end
    """,
    "alter table atlas.task_run enable trigger task_run_set_updated_at",
    "alter table atlas.task_run enable trigger task_run_guard_update",
    """
    alter table atlas.task_run
      alter column materialization_state set default 'MATERIALIZING',
      alter column materialization_state set not null,
      alter column legacy_unsealed set default false,
      alter column legacy_unsealed set not null
    """,
    """
    alter table atlas.task_run
      add constraint task_run_request_digest_valid check (
        request_digest is null or request_digest ~ '^sha256:[0-9a-f]{64}$'
      ) not valid,
      add constraint task_run_materialization_valid check (
        materialization_state in ('MATERIALIZING', 'SEALED')
        and (
          (
            materialization_state = 'MATERIALIZING'
            and materialized_unit_count is null
            and materialized_first_attempt_count is null
            and materialization_sealed_at is null
          )
          or (
            materialization_state = 'SEALED'
            and not legacy_unsealed
            and request_digest is not null
            and materialized_unit_count between 1 and 64
            and materialized_first_attempt_count = materialized_unit_count
            and materialization_sealed_at between created_at and updated_at
          )
        )
      ) not valid,
      add constraint task_run_temporal_identity_pair check (
        (temporal_namespace is null) = (temporal_workflow_id is null)
        and (
          temporal_namespace is null
          or (
            temporal_namespace ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
            and temporal_workflow_id =
              'atlas-task/run/' || replace(tenant_id::text, '-', '') || '/'
              || replace(id::text, '-', '')
          )
          or legacy_unsealed
        )
      ) not valid
    """,
    """
    create index task_run_trigger_request_digest_idx
      on atlas.task_run (
        tenant_id, trigger_source, trigger_fingerprint, request_digest
      ) where request_digest is not null
    """,
    """
    alter table atlas.execution_unit
      rename column execution_contract_version_id to execution_profile_version_id
    """,
    """
    alter table atlas.execution_unit
      add constraint execution_unit_execution_profile_scope_fk foreign key (
        execution_profile_version_id, case_version_id, tenant_id, project_id
      ) references atlas.execution_profile_version (
        id, case_version_id, tenant_id, project_id
      ) on delete restrict not valid,
      add constraint execution_unit_identity_profile_scope_fk foreign key (
        identity_profile_version_id, case_version_id, tenant_id, project_id
      ) references atlas.identity_profile_version (
        id, case_version_id, tenant_id, project_id
      ) on delete restrict not valid,
      add constraint execution_unit_browser_profile_scope_fk foreign key (
        browser_profile_version_id, tenant_id, project_id
      ) references atlas.browser_profile_version (
        id, tenant_id, project_id
      ) on delete restrict not valid,
      add constraint execution_unit_data_profile_scope_fk foreign key (
        data_profile_version_id, fixture_blueprint_version_id,
        tenant_id, project_id
      ) references atlas.data_profile_version (
        id, blueprint_version_id, tenant_id, project_id
      ) on delete restrict not valid
    """,
    """
    create index execution_unit_execution_profile_scope_fk_idx
      on atlas.execution_unit (
        execution_profile_version_id, case_version_id, tenant_id, project_id
      )
    """,
    """
    create index execution_unit_identity_profile_scope_fk_idx
      on atlas.execution_unit (
        identity_profile_version_id, case_version_id, tenant_id, project_id
      )
    """,
    """
    create index execution_unit_browser_profile_scope_fk_idx
      on atlas.execution_unit (
        browser_profile_version_id, tenant_id, project_id
      )
    """,
    """
    create index execution_unit_data_profile_scope_fk_idx
      on atlas.execution_unit (
        data_profile_version_id, fixture_blueprint_version_id,
        tenant_id, project_id
      )
    """,
    "alter table atlas.unit_attempt add column temporal_namespace text",
    "alter table atlas.unit_attempt disable trigger unit_attempt_guard_update",
    "alter table atlas.unit_attempt disable trigger unit_attempt_set_updated_at",
    """
    update atlas.unit_attempt
    set temporal_namespace = 'atlas-task'
    where temporal_workflow_id is not null
    """,
    "alter table atlas.unit_attempt enable trigger unit_attempt_set_updated_at",
    "alter table atlas.unit_attempt enable trigger unit_attempt_guard_update",
    """
    alter table atlas.unit_attempt
      add constraint unit_attempt_temporal_identity_pair check (
        (temporal_namespace is null) = (temporal_workflow_id is null)
        and (
          temporal_namespace is null
          or temporal_workflow_id =
            'atlas-task/attempt/' || replace(tenant_id::text, '-', '') || '/'
            || replace(id::text, '-', '')
        )
      ) not valid
    """,
    """
    create table atlas.task_workflow_identity_registry (
      namespace text not null,
      workflow_id text not null,
      owner_kind text not null,
      owner_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid,
      unit_attempt_id uuid,
      request_digest text,
      created_at timestamptz not null default clock_timestamp(),
      constraint task_workflow_identity_registry_pk primary key (
        namespace, workflow_id
      ),
      constraint task_workflow_identity_owner_unique unique (
        owner_kind, owner_id
      ),
      constraint task_workflow_identity_exact_unique unique (
        namespace, workflow_id, owner_kind, owner_id,
        tenant_id, project_id, task_run_id
      ),
      constraint task_workflow_identity_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id
      ) references atlas.task_run (id, tenant_id, project_id) on delete restrict,
      constraint task_workflow_identity_unit_scope_fk foreign key (
        execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.execution_unit (
        id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint task_workflow_identity_attempt_scope_fk foreign key (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.unit_attempt (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint task_workflow_identity_shape check (
        namespace ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
        and workflow_id ~ '^atlas-task/[A-Za-z0-9/_-]+$'
        and char_length(workflow_id) between 12 and 320
        and owner_kind in ('TASK_RUN', 'UNIT_ATTEMPT')
        and (request_digest is null or request_digest ~ '^sha256:[0-9a-f]{64}$')
        and (
          (
            owner_kind = 'TASK_RUN'
            and owner_id = task_run_id
            and execution_unit_id is null
            and unit_attempt_id is null
          )
          or (
            owner_kind = 'UNIT_ATTEMPT'
            and owner_id = unit_attempt_id
            and execution_unit_id is not null
            and unit_attempt_id is not null
          )
        )
      )
    )
    """,
    """
    do $$
    begin
      if exists (
        select 1
        from (
          select temporal_namespace as namespace, temporal_workflow_id as workflow_id
          from atlas.task_run
          where temporal_workflow_id is not null
          union all
          select temporal_namespace, temporal_workflow_id
          from atlas.unit_attempt
          where temporal_workflow_id is not null
        ) identity
        group by identity.namespace, identity.workflow_id
        having count(*) > 1
      ) then
        raise exception 'existing Task workflow identities conflict across owners';
      end if;
    end;
    $$
    """,
    """
    insert into atlas.task_workflow_identity_registry (
      namespace, workflow_id, owner_kind, owner_id,
      tenant_id, project_id, task_run_id,
      execution_unit_id, unit_attempt_id, request_digest, created_at
    )
    select
      run.temporal_namespace,
      run.temporal_workflow_id,
      'TASK_RUN',
      run.id,
      run.tenant_id,
      run.project_id,
      run.id,
      null,
      null,
      run.request_digest,
      run.created_at
    from atlas.task_run run
    where run.temporal_workflow_id is not null
    union all
    select
      attempt.temporal_namespace,
      attempt.temporal_workflow_id,
      'UNIT_ATTEMPT',
      attempt.id,
      attempt.tenant_id,
      attempt.project_id,
      attempt.task_run_id,
      attempt.execution_unit_id,
      attempt.id,
      run.request_digest,
      attempt.created_at
    from atlas.unit_attempt attempt
    join atlas.task_run run on run.id = attempt.task_run_id
    where attempt.temporal_workflow_id is not null
    """,
    """
    create index task_workflow_identity_run_scope_fk_idx
      on atlas.task_workflow_identity_registry (
        task_run_id, tenant_id, project_id
      )
    """,
    """
    create index task_workflow_identity_unit_scope_fk_idx
      on atlas.task_workflow_identity_registry (
        execution_unit_id, task_run_id, tenant_id, project_id
      ) where execution_unit_id is not null
    """,
    """
    create index task_workflow_identity_attempt_scope_fk_idx
      on atlas.task_workflow_identity_registry (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) where unit_attempt_id is not null
    """,
    """
    create table atlas.task_workflow_start_intent (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      owner_kind text not null,
      owner_id uuid not null,
      namespace text not null,
      workflow_id text not null,
      request_digest text not null,
      workflow_type text not null,
      task_queue text not null,
      status text not null default 'PENDING',
      created_at timestamptz not null default clock_timestamp(),
      constraint task_workflow_start_intent_owner_unique unique (
        owner_kind, owner_id
      ),
      constraint task_workflow_start_intent_identity_unique unique (
        namespace, workflow_id
      ),
      constraint task_workflow_start_intent_registry_fk foreign key (
        namespace, workflow_id, owner_kind, owner_id,
        tenant_id, project_id, task_run_id
      ) references atlas.task_workflow_identity_registry (
        namespace, workflow_id, owner_kind, owner_id,
        tenant_id, project_id, task_run_id
      ) on delete restrict,
      constraint task_workflow_start_intent_shape check (
        owner_kind in ('TASK_RUN', 'UNIT_ATTEMPT')
        and request_digest ~ '^sha256:[0-9a-f]{64}$'
        and workflow_type in ('AtlasTaskRunWorkflow', 'AtlasUnitAttemptWorkflow')
        and task_queue ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
        and status = 'PENDING'
      )
    )
    """,
    """
    create index task_workflow_start_intent_pending_idx
      on atlas.task_workflow_start_intent (
        tenant_id, project_id, created_at, id
      ) where status = 'PENDING'
    """,
)

UPGRADE_STATEMENTS += (
    "alter table atlas.execution_profile_version enable row level security",
    "alter table atlas.execution_profile_version force row level security",
    "alter table atlas.identity_profile_version enable row level security",
    "alter table atlas.identity_profile_version force row level security",
    "alter table atlas.identity_profile_actor_binding enable row level security",
    "alter table atlas.identity_profile_actor_binding force row level security",
    "alter table atlas.browser_profile_version enable row level security",
    "alter table atlas.browser_profile_version force row level security",
    "alter table atlas.data_profile_version enable row level security",
    "alter table atlas.data_profile_version force row level security",
    "alter table atlas.task_workflow_identity_registry enable row level security",
    "alter table atlas.task_workflow_identity_registry force row level security",
    "alter table atlas.task_workflow_start_intent enable row level security",
    "alter table atlas.task_workflow_start_intent force row level security",
    """
    create policy execution_profile_tenant_isolation
      on atlas.execution_profile_version for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy identity_profile_tenant_isolation
      on atlas.identity_profile_version for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy identity_profile_actor_tenant_isolation
      on atlas.identity_profile_actor_binding for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy browser_profile_tenant_isolation
      on atlas.browser_profile_version for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy data_profile_tenant_isolation
      on atlas.data_profile_version for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy task_workflow_identity_tenant_isolation
      on atlas.task_workflow_identity_registry for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy task_workflow_start_intent_tenant_isolation
      on atlas.task_workflow_start_intent for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create index task_workflow_identity_tenant_idx
      on atlas.task_workflow_identity_registry (
        tenant_id, project_id, task_run_id, owner_kind, owner_id
      )
    """,
    "revoke all on atlas.execution_profile_version from atlas_app",
    "revoke all on atlas.identity_profile_version from atlas_app",
    "revoke all on atlas.identity_profile_actor_binding from atlas_app",
    "revoke all on atlas.browser_profile_version from atlas_app",
    "revoke all on atlas.data_profile_version from atlas_app",
    "revoke all on atlas.task_workflow_identity_registry from atlas_app",
    "revoke all on atlas.task_workflow_start_intent from atlas_app",
    "grant select, insert on atlas.execution_profile_version to atlas_app",
    "grant select, insert on atlas.identity_profile_version to atlas_app",
    "grant select, insert on atlas.identity_profile_actor_binding to atlas_app",
    "grant select, insert on atlas.browser_profile_version to atlas_app",
    "grant select, insert on atlas.data_profile_version to atlas_app",
    "grant select on atlas.task_workflow_identity_registry to atlas_app",
    "grant select on atlas.task_workflow_start_intent to atlas_app",
    """
    revoke update (
      lifecycle, quality, hygiene, started_at, finalized_at,
      cleanup_resolved_at, closed_at, revision
    ) on atlas.task_run from atlas_app
    """,
    """
    revoke update (
      lifecycle, quality, hygiene, started_at, finalized_at,
      cleanup_resolved_at, closed_at, revision
    ) on atlas.execution_unit from atlas_app
    """,
    """
    revoke update (
      lifecycle, quality, hygiene, started_at, finalized_at,
      cleanup_resolved_at, closed_at, revision
    ) on atlas.unit_attempt from atlas_app
    """,
    "alter function atlas.guard_task_run_event_insert() security definer",
    "revoke all on function atlas.register_task_run_workflow_identity() from public",
    "revoke all on function atlas.register_unit_attempt_workflow_identity() from public",
    "revoke all on function atlas.seal_task_run_materialization(uuid, bigint) from public",
    """
    revoke all on function atlas.transition_task_run_state(
      uuid, bigint, text, text, text,
      timestamptz, timestamptz, timestamptz, timestamptz
    ) from public
    """,
    """
    revoke all on function atlas.transition_execution_unit_state(
      uuid, uuid, bigint, text, text, text,
      timestamptz, timestamptz, timestamptz, timestamptz
    ) from public
    """,
    """
    revoke all on function atlas.transition_unit_attempt_state(
      uuid, uuid, uuid, bigint, text, text, text,
      timestamptz, timestamptz, timestamptz, timestamptz
    ) from public
    """,
    "grant execute on function atlas.seal_task_run_materialization(uuid, bigint) to atlas_app",
    """
    grant execute on function atlas.transition_task_run_state(
      uuid, bigint, text, text, text,
      timestamptz, timestamptz, timestamptz, timestamptz
    ) to atlas_app
    """,
    """
    grant execute on function atlas.transition_execution_unit_state(
      uuid, uuid, bigint, text, text, text,
      timestamptz, timestamptz, timestamptz, timestamptz
    ) to atlas_app
    """,
    """
    grant execute on function atlas.transition_unit_attempt_state(
      uuid, uuid, uuid, bigint, text, text, text,
      timestamptz, timestamptz, timestamptz, timestamptz
    ) to atlas_app
    """,
    "revoke all on function atlas.task_canonical_json(jsonb) from public",
    "revoke all on function atlas.task_sha256_json(jsonb) from public",
    "revoke all on function atlas.task_json_has_sensitive_keys(jsonb) from public",
    """
    revoke all on function atlas.task_profile_lifecycle_valid(
      text, timestamptz, timestamptz, timestamptz, timestamptz, timestamptz
    ) from public
    """,
    "revoke all on function atlas.task_profile_ref_valid(text, text, text, text) from public",
    "revoke all on function atlas.task_profile_refs_v2_valid(jsonb, uuid[]) from public",
    "revoke all on function atlas.task_manifest_units_v2_valid(jsonb) from public",
    """
    revoke all on function atlas.task_profile_content_digest(
      text, uuid, uuid, text, text, text, jsonb
    ) from public
    """,
    "revoke all on function atlas.task_identity_profile_content_digest(uuid) from public",
    "grant execute on function atlas.task_canonical_json(jsonb) to atlas_app",
    "grant execute on function atlas.task_sha256_json(jsonb) to atlas_app",
    "grant execute on function atlas.task_json_has_sensitive_keys(jsonb) to atlas_app",
    """
    grant execute on function atlas.task_profile_lifecycle_valid(
      text, timestamptz, timestamptz, timestamptz, timestamptz, timestamptz
    ) to atlas_app
    """,
    "grant execute on function atlas.task_profile_ref_valid(text, text, text, text) to atlas_app",
    "grant execute on function atlas.task_profile_refs_v2_valid(jsonb, uuid[]) to atlas_app",
    "grant execute on function atlas.task_manifest_units_v2_valid(jsonb) to atlas_app",
    """
    grant execute on function atlas.task_profile_content_digest(
      text, uuid, uuid, text, text, text, jsonb
    ) to atlas_app
    """,
    "grant execute on function atlas.task_identity_profile_content_digest(uuid) to atlas_app",
)

UPGRADE_STATEMENTS += (
    """
    create function atlas.seal_task_run_materialization(
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
      if not found or stored_manifest.unit_count not between 1 and 64 then
        raise exception 'task run seal requires one bounded manifest'
          using errcode = '55000';
      end if;

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
       and identity_profile.content_digest = atlas.task_identity_profile_content_digest(identity_profile.id)
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
    """,
    """
    create function atlas.transition_task_run_state(
      p_task_run_id uuid,
      p_expected_revision bigint,
      p_lifecycle text,
      p_quality text,
      p_hygiene text,
      p_started_at timestamptz,
      p_finalized_at timestamptz,
      p_cleanup_resolved_at timestamptz,
      p_closed_at timestamptz
    ) returns setof atlas.task_run
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_revision bigint;
      stored_materialization_state text;
      stored_legacy_unsealed boolean;
    begin
      if atlas.current_tenant_id() is null then
        raise exception 'task transition requires tenant context' using errcode = '42501';
      end if;
      select run.revision, run.materialization_state, run.legacy_unsealed
      into stored_revision, stored_materialization_state, stored_legacy_unsealed
      from atlas.task_run run
      where run.id = p_task_run_id
        and run.tenant_id = atlas.current_tenant_id()
      for update;
      if not found then
        raise exception 'task run is missing from the current tenant' using errcode = 'P0002';
      end if;
      if stored_legacy_unsealed or stored_materialization_state <> 'SEALED' then
        raise exception 'task state transitions require a sealed non-legacy TaskRun'
          using errcode = '55000';
      end if;
      if stored_revision <> p_expected_revision then
        raise exception 'task run revision conflict' using errcode = '40001';
      end if;
      return query
      update atlas.task_run run
      set
        lifecycle = p_lifecycle,
        quality = p_quality,
        hygiene = p_hygiene,
        started_at = p_started_at,
        finalized_at = p_finalized_at,
        cleanup_resolved_at = p_cleanup_resolved_at,
        closed_at = p_closed_at,
        revision = run.revision + 1
      where run.id = p_task_run_id
        and run.tenant_id = atlas.current_tenant_id()
      returning run.*;
    end;
    $$
    """,
    """
    create function atlas.transition_execution_unit_state(
      p_task_run_id uuid,
      p_execution_unit_id uuid,
      p_expected_revision bigint,
      p_lifecycle text,
      p_quality text,
      p_hygiene text,
      p_started_at timestamptz,
      p_finalized_at timestamptz,
      p_cleanup_resolved_at timestamptz,
      p_closed_at timestamptz
    ) returns setof atlas.execution_unit
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_revision bigint;
    begin
      if atlas.current_tenant_id() is null then
        raise exception 'task transition requires tenant context' using errcode = '42501';
      end if;
      perform 1
      from atlas.task_run run
      where run.id = p_task_run_id
        and run.tenant_id = atlas.current_tenant_id()
        and run.materialization_state = 'SEALED'
        and not run.legacy_unsealed
      for update;
      if not found then
        raise exception 'task run is missing from the current tenant' using errcode = 'P0002';
      end if;
      select unit.revision into stored_revision
      from atlas.execution_unit unit
      where unit.id = p_execution_unit_id
        and unit.task_run_id = p_task_run_id
        and unit.tenant_id = atlas.current_tenant_id()
      for update;
      if not found then
        raise exception 'execution unit is missing from the TaskRun' using errcode = 'P0002';
      end if;
      if stored_revision <> p_expected_revision then
        raise exception 'execution unit revision conflict' using errcode = '40001';
      end if;
      return query
      update atlas.execution_unit unit
      set
        lifecycle = p_lifecycle,
        quality = p_quality,
        hygiene = p_hygiene,
        started_at = p_started_at,
        finalized_at = p_finalized_at,
        cleanup_resolved_at = p_cleanup_resolved_at,
        closed_at = p_closed_at,
        revision = unit.revision + 1
      where unit.id = p_execution_unit_id
        and unit.task_run_id = p_task_run_id
        and unit.tenant_id = atlas.current_tenant_id()
      returning unit.*;
    end;
    $$
    """,
    """
    create function atlas.transition_unit_attempt_state(
      p_task_run_id uuid,
      p_execution_unit_id uuid,
      p_unit_attempt_id uuid,
      p_expected_revision bigint,
      p_lifecycle text,
      p_quality text,
      p_hygiene text,
      p_started_at timestamptz,
      p_finalized_at timestamptz,
      p_cleanup_resolved_at timestamptz,
      p_closed_at timestamptz
    ) returns setof atlas.unit_attempt
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_revision bigint;
    begin
      if atlas.current_tenant_id() is null then
        raise exception 'task transition requires tenant context' using errcode = '42501';
      end if;
      perform 1
      from atlas.task_run run
      where run.id = p_task_run_id
        and run.tenant_id = atlas.current_tenant_id()
        and run.materialization_state = 'SEALED'
        and not run.legacy_unsealed
      for update;
      if not found then
        raise exception 'task run is missing from the current tenant' using errcode = 'P0002';
      end if;
      perform 1
      from atlas.execution_unit unit
      where unit.id = p_execution_unit_id
        and unit.task_run_id = p_task_run_id
        and unit.tenant_id = atlas.current_tenant_id()
      for update;
      if not found then
        raise exception 'execution unit is missing from the TaskRun' using errcode = 'P0002';
      end if;
      select attempt.revision into stored_revision
      from atlas.unit_attempt attempt
      where attempt.id = p_unit_attempt_id
        and attempt.execution_unit_id = p_execution_unit_id
        and attempt.task_run_id = p_task_run_id
        and attempt.tenant_id = atlas.current_tenant_id()
      for update;
      if not found then
        raise exception 'unit attempt is missing from the ExecutionUnit' using errcode = 'P0002';
      end if;
      if stored_revision <> p_expected_revision then
        raise exception 'unit attempt revision conflict' using errcode = '40001';
      end if;
      return query
      update atlas.unit_attempt attempt
      set
        lifecycle = p_lifecycle,
        quality = p_quality,
        hygiene = p_hygiene,
        started_at = p_started_at,
        finalized_at = p_finalized_at,
        cleanup_resolved_at = p_cleanup_resolved_at,
        closed_at = p_closed_at,
        revision = attempt.revision + 1
      where attempt.id = p_unit_attempt_id
        and attempt.execution_unit_id = p_execution_unit_id
        and attempt.task_run_id = p_task_run_id
        and attempt.tenant_id = atlas.current_tenant_id()
      returning attempt.*;
    end;
    $$
    """,
)

UPGRADE_STATEMENTS += (
    """
    create function atlas.guard_task_run_v2_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id,
        new.task_plan_version_id, new.manifest_hash,
        new.trigger_source, new.trigger_fingerprint,
        new.request_digest, new.rerun_of_task_run_id, new.requested_by,
        new.temporal_namespace, new.temporal_workflow_id,
        new.requested_at, new.queued_at, new.created_at,
        new.legacy_unsealed
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id,
        old.task_plan_version_id, old.manifest_hash,
        old.trigger_source, old.trigger_fingerprint,
        old.request_digest, old.rerun_of_task_run_id, old.requested_by,
        old.temporal_namespace, old.temporal_workflow_id,
        old.requested_at, old.queued_at, old.created_at,
        old.legacy_unsealed
      ) then
        raise exception 'task run identity and frozen inputs are immutable';
      end if;
      if not (
        new.materialization_state = old.materialization_state
        or (
          old.materialization_state = 'MATERIALIZING'
          and new.materialization_state = 'SEALED'
          and not old.legacy_unsealed
        )
      ) then
        raise exception 'task run materialization transition is invalid';
      end if;
      if new.materialization_state = old.materialization_state
        and row(
          new.materialized_unit_count,
          new.materialized_first_attempt_count,
          new.materialization_sealed_at
        ) is distinct from row(
          old.materialized_unit_count,
          old.materialized_first_attempt_count,
          old.materialization_sealed_at
        )
      then
        raise exception 'task run seal facts are immutable';
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
        raise exception 'closed task run lifecycle, quality, and close milestones are immutable';
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
    create function atlas.guard_execution_unit_v2_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.manifest_hash, new.ordinal, new.unit_key,
        new.case_version_id, new.execution_profile_version_id,
        new.fixture_blueprint_version_id, new.identity_profile_version_id,
        new.environment_id, new.browser_profile_version_id,
        new.data_profile_version_id, new.parameter_digest,
        new.dependency_digest, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.manifest_hash, old.ordinal, old.unit_key,
        old.case_version_id, old.execution_profile_version_id,
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
        raise exception 'closed execution unit lifecycle, quality, and close milestones are immutable';
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
    create function atlas.guard_unit_attempt_v2_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      expected_attempt integer;
      parent_legacy boolean;
      parent_request_digest text;
      parent_materialization_state text;
      parent_lifecycle text;
      parent_namespace text;
      parent_unit_lifecycle text;
      previous_lifecycle text;
      previous_quality text;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'unit attempt insertion requires exact tenant context'
          using errcode = '42501';
      end if;
      select
        run.legacy_unsealed,
        run.request_digest,
        run.materialization_state,
        run.lifecycle,
        run.temporal_namespace
      into
        parent_legacy,
        parent_request_digest,
        parent_materialization_state,
        parent_lifecycle,
        parent_namespace
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
      for update;
      if not found or parent_legacy or parent_request_digest is null then
        raise exception 'unit attempt requires a non-legacy TaskRun';
      end if;

      select unit.lifecycle into parent_unit_lifecycle
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
        select 1 from atlas.unit_attempt attempt
        where attempt.id = new.id
           or (
             attempt.execution_unit_id = new.execution_unit_id
             and attempt.attempt_number = new.attempt_number
           )
      ) then
        return new;
      end if;
      if new.temporal_namespace is distinct from parent_namespace
        or new.temporal_namespace is null
        or new.temporal_workflow_id is distinct from (
          'atlas-task/attempt/' || replace(new.tenant_id::text, '-', '') || '/'
          || replace(new.id::text, '-', '')
        )
      then
        raise exception 'unit attempt requires its deterministic Temporal identity in the TaskRun namespace';
      end if;
      select coalesce(max(attempt.attempt_number), 0) + 1
      into expected_attempt
      from atlas.unit_attempt attempt
      where attempt.execution_unit_id = new.execution_unit_id;
      if new.attempt_number <> expected_attempt then
        raise exception 'unit attempt number must be gapless';
      end if;
      if new.attempt_number > 1 then
        if parent_materialization_state <> 'SEALED'
          or parent_lifecycle not in ('QUEUED', 'RUNNING')
          or parent_unit_lifecycle not in ('QUEUED', 'RUNNING')
        then
          raise exception 'unit retry requires a sealed dispatchable TaskRun and ExecutionUnit';
        end if;
        select attempt.lifecycle, attempt.quality
        into previous_lifecycle, previous_quality
        from atlas.unit_attempt attempt
        where attempt.execution_unit_id = new.execution_unit_id
          and attempt.attempt_number = new.attempt_number - 1;
        if not found
          or previous_lifecycle <> 'CLOSED'
          or previous_quality not in (
            'FAILED', 'BLOCKED', 'INCONCLUSIVE', 'INFRA_ERROR', 'CANCELED'
          )
        then
          raise exception 'unit retry requires one closed retryable previous Attempt';
        end if;
      end if;
      if new.revision <> 1
        or new.lifecycle <> 'QUEUED'
        or new.quality <> 'PENDING'
        or new.hygiene not in ('PENDING', 'NOT_REQUIRED')
      then
        raise exception 'unit attempt must start queued at revision one with initial hygiene';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_unit_attempt_v2_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.execution_unit_id, new.manifest_hash, new.unit_key,
        new.case_version_id, new.attempt_number,
        new.temporal_namespace, new.temporal_workflow_id, new.queued_at,
        new.execution_deadline, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.execution_unit_id, old.manifest_hash, old.unit_key,
        old.case_version_id, old.attempt_number,
        old.temporal_namespace, old.temporal_workflow_id, old.queued_at,
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
        raise exception 'closed unit attempt lifecycle, quality, and close milestones are immutable';
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
    create function atlas.register_task_run_workflow_identity()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    begin
      if new.tenant_id is distinct from atlas.current_tenant_id() then
        raise exception 'task workflow identity tenant does not match session tenant'
          using errcode = '42501';
      end if;
      insert into atlas.task_workflow_identity_registry (
        namespace, workflow_id, owner_kind, owner_id,
        tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id, request_digest, created_at
      ) values (
        new.temporal_namespace, new.temporal_workflow_id, 'TASK_RUN', new.id,
        new.tenant_id, new.project_id, new.id,
        null, null, new.request_digest, new.created_at
      );
      return new;
    end;
    $$
    """,
    """
    create function atlas.register_unit_attempt_workflow_identity()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_request_digest text;
    begin
      if new.tenant_id is distinct from atlas.current_tenant_id() then
        raise exception 'task workflow identity tenant does not match session tenant'
          using errcode = '42501';
      end if;
      select run.request_digest into stored_request_digest
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id;
      if stored_request_digest is null then
        raise exception 'unit attempt workflow identity requires TaskRun request digest';
      end if;
      insert into atlas.task_workflow_identity_registry (
        namespace, workflow_id, owner_kind, owner_id,
        tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id, request_digest, created_at
      ) values (
        new.temporal_namespace, new.temporal_workflow_id, 'UNIT_ATTEMPT', new.id,
        new.tenant_id, new.project_id, new.task_run_id,
        new.execution_unit_id, new.id, stored_request_digest, new.created_at
      );
      return new;
    end;
    $$
    """,
    """
    create trigger task_run_register_workflow_identity
      after insert on atlas.task_run
      for each row execute function atlas.register_task_run_workflow_identity()
    """,
    """
    create trigger unit_attempt_register_workflow_identity
      after insert on atlas.unit_attempt
      for each row execute function atlas.register_unit_attempt_workflow_identity()
    """,
    """
    create trigger task_workflow_identity_prevent_mutation
      before update or delete on atlas.task_workflow_identity_registry
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger task_workflow_start_intent_prevent_mutation
      before update or delete on atlas.task_workflow_start_intent
      for each row execute function atlas.prevent_fact_mutation()
    """,
)

UPGRADE_STATEMENTS += (
    """
    create function atlas.guard_task_plan_version_v2_insert()
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
      requested_profile_count bigint;
      scoped_profile_count bigint;
      expected_digest text;
    begin
      if not coalesce(
        atlas.task_profile_refs_v2_valid(new.profile_refs, new.pinned_case_version_ids),
        false
      ) then
        raise exception 'task plan version requires v2 execution profile references';
      end if;

      select count(*) into scoped_case_count
      from atlas.case_version version
      where version.id = any(new.pinned_case_version_ids)
        and version.tenant_id = new.tenant_id
        and version.project_id = new.project_id
        and version.status = 'PUBLISHED';
      if scoped_case_count <> cardinality(new.pinned_case_version_ids) then
        raise exception 'task plan version requires published same-scope case versions';
      end if;

      select count(*), count(profile.id)
      into requested_profile_count, scoped_profile_count
      from jsonb_array_elements(new.profile_refs -> 'caseProfiles') requested(value)
      left join atlas.execution_profile_version profile
        on profile.id::text = requested.value ->> 'executionProfileVersionId'
       and profile.case_version_id::text = requested.value ->> 'caseVersionId'
       and profile.tenant_id = new.tenant_id
       and profile.project_id = new.project_id
       and profile.status = 'PUBLISHED';
      if requested_profile_count <> scoped_profile_count then
        raise exception 'task plan version requires exact published Execution profiles';
      end if;

      select
        count(distinct requested.environment_id),
        count(distinct environment.id)
      into requested_environment_count, scoped_environment_count
      from jsonb_array_elements_text(new.matrix -> 'environmentIds') requested(environment_id)
      left join atlas.environment environment
        on environment.id::text = requested.environment_id
       and environment.tenant_id = new.tenant_id
       and environment.project_id = new.project_id
       and environment.status = 'ACTIVE'
       and environment.kind in ('TEST', 'STAGING');
      if scoped_environment_count <> requested_environment_count then
        raise exception 'task plan version requires active same-scope test or staging environments';
      end if;

      select count(*), count(profile.id)
      into requested_profile_count, scoped_profile_count
      from jsonb_array_elements_text(new.matrix -> 'browserProfileVersionIds') requested(profile_id)
      left join atlas.browser_profile_version profile
        on profile.id::text = requested.profile_id
       and profile.tenant_id = new.tenant_id
       and profile.project_id = new.project_id
       and profile.status = 'PUBLISHED';
      if requested_profile_count <> scoped_profile_count then
        raise exception 'task plan version requires published same-scope Browser profiles';
      end if;

      select count(*), count(profile.id)
      into requested_profile_count, scoped_profile_count
      from jsonb_array_elements_text(new.matrix -> 'identityProfileVersionIds') requested(profile_id)
      left join atlas.identity_profile_version profile
        on profile.id::text = requested.profile_id
       and profile.tenant_id = new.tenant_id
       and profile.project_id = new.project_id
       and profile.status = 'PUBLISHED'
       and profile.content_digest = atlas.task_identity_profile_content_digest(profile.id);
      if requested_profile_count <> scoped_profile_count then
        raise exception 'task plan version requires complete published Identity profiles';
      end if;

      select count(*), count(profile.id)
      into requested_profile_count, scoped_profile_count
      from jsonb_array_elements_text(new.matrix -> 'dataProfileVersionIds') requested(profile_id)
      left join atlas.data_profile_version profile
        on profile.id::text = requested.profile_id
       and profile.tenant_id = new.tenant_id
       and profile.project_id = new.project_id
       and profile.status = 'PUBLISHED';
      if requested_profile_count <> scoped_profile_count then
        raise exception 'task plan version requires published same-scope Data profiles';
      end if;

      select
        count(distinct requested.value ->> 'fixtureBlueprintVersionId'),
        count(distinct version.id)
      into requested_fixture_count, scoped_fixture_count
      from jsonb_array_elements(new.profile_refs -> 'caseProfiles') requested(value)
      left join atlas.data_blueprint_version version
        on version.id::text = requested.value ->> 'fixtureBlueprintVersionId'
       and version.tenant_id = new.tenant_id
       and version.project_id = new.project_id
       and version.status = 'PUBLISHED';
      if scoped_fixture_count <> requested_fixture_count then
        raise exception 'task plan version requires published same-scope fixture blueprint versions';
      end if;

      expected_digest := atlas.task_sha256_json(jsonb_build_object(
        'schemaVersion', new.schema_version,
        'tenantId', new.tenant_id::text,
        'projectId', new.project_id::text,
        'taskPlanId', new.task_plan_id::text,
        'version', new.version,
        'versionRef', new.version_ref,
        'pinnedCaseVersionIds', to_jsonb(new.pinned_case_version_ids),
        'matrix', new.matrix,
        'profileRefs', new.profile_refs,
        'policyDigests', new.policy_digests
      ));
      if new.content_digest is distinct from expected_digest then
        raise exception 'task plan version content digest is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_run_v2_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      plan_profile_refs jsonb;
      plan_pinned_cases uuid[];
    begin
      select version.profile_refs, version.pinned_case_version_ids
      into plan_profile_refs, plan_pinned_cases
      from atlas.task_plan_version version
      where version.id = new.task_plan_version_id
        and version.tenant_id = new.tenant_id
        and version.project_id = new.project_id;
      if not found
        or not coalesce(
          atlas.task_profile_refs_v2_valid(plan_profile_refs, plan_pinned_cases), false
        )
      then
        raise exception 'task run requires a dispatch-ready v2 TaskPlanVersion';
      end if;
      if new.revision <> 1
        or new.lifecycle <> 'QUEUED'
        or new.quality <> 'PENDING'
        or new.hygiene not in ('PENDING', 'NOT_REQUIRED')
        or new.request_digest is null
        or new.materialization_state <> 'MATERIALIZING'
        or new.materialized_unit_count is not null
        or new.materialized_first_attempt_count is not null
        or new.materialization_sealed_at is not null
        or new.legacy_unsealed
      then
        raise exception 'task run must start as a non-legacy materializing aggregate';
      end if;
      if new.temporal_namespace is null
        or new.temporal_workflow_id is distinct from (
          'atlas-task/run/' || replace(new.tenant_id::text, '-', '') || '/'
          || replace(new.id::text, '-', '')
        )
      then
        raise exception 'task run requires its deterministic Temporal identity';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_run_manifest_v2_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_run atlas.task_run%rowtype;
      plan_pinned_case_version_ids uuid[];
      plan_matrix jsonb;
      plan_profile_refs jsonb;
      plan_policy_digests jsonb;
      unit jsonb;
      case_profile jsonb;
      expected_unit_key text;
      expected_dependency_digest text;
      expected_manifest_hash text;
      expected_request_digest text;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'task run manifest insertion requires exact tenant context'
          using errcode = '42501';
      end if;
      select * into stored_run
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
      for update;
      if not found
        or stored_run.legacy_unsealed
        or stored_run.materialization_state <> 'MATERIALIZING'
      then
        raise exception 'task run manifest requires a current materializing TaskRun';
      end if;

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
        raise exception 'task run manifest requires its exact same-scope task plan version';
      end if;
      if not (new.policy_digests @> plan_policy_digests) then
        raise exception 'task run manifest policy digests must cover its task plan version';
      end if;
      if not coalesce(atlas.task_manifest_units_v2_valid(new.units), false) then
        raise exception 'task run manifest units must use valid v2 provenance';
      end if;

      for unit in select item.value from jsonb_array_elements(new.units) item(value)
      loop
        if not ((unit ->> 'caseVersionId')::uuid = any(plan_pinned_case_version_ids))
          or not (
            (plan_matrix -> 'environmentIds') ? (unit ->> 'environmentId')
            and (plan_matrix -> 'browserProfileVersionIds') ? (unit ->> 'browserProfileVersionId')
            and (plan_matrix -> 'identityProfileVersionIds') ? (unit ->> 'identityProfileVersionId')
            and (plan_matrix -> 'dataProfileVersionIds') ? (unit ->> 'dataProfileVersionId')
          )
        then
          raise exception 'task run manifest unit must derive from its task plan version';
        end if;

        select profile.value into case_profile
        from jsonb_array_elements(plan_profile_refs -> 'caseProfiles') profile(value)
        where profile.value ->> 'caseVersionId' = unit ->> 'caseVersionId';
        if not found
          or row(
            unit ->> 'executionProfileVersionId',
            unit ->> 'fixtureBlueprintVersionId'
          ) is distinct from row(
            case_profile ->> 'executionProfileVersionId',
            case_profile ->> 'fixtureBlueprintVersionId'
          )
        then
          raise exception 'task run manifest unit must derive from its task plan version';
        end if;

        expected_unit_key := atlas.task_sha256_json(jsonb_build_object(
          'caseVersionId', unit ->> 'caseVersionId',
          'environmentId', unit ->> 'environmentId',
          'browserProfileVersionId', unit ->> 'browserProfileVersionId',
          'identityProfileVersionId', unit ->> 'identityProfileVersionId',
          'dataProfileVersionId', unit ->> 'dataProfileVersionId',
          'parameterDigest', unit ->> 'parameterDigest'
        ));
        expected_dependency_digest := atlas.task_sha256_json(jsonb_build_object(
          'caseVersionId', unit ->> 'caseVersionId',
          'executionProfileVersionId', unit ->> 'executionProfileVersionId',
          'fixtureBlueprintVersionId', unit ->> 'fixtureBlueprintVersionId',
          'identityProfileVersionId', unit ->> 'identityProfileVersionId',
          'environmentId', unit ->> 'environmentId',
          'browserProfileVersionId', unit ->> 'browserProfileVersionId',
          'dataProfileVersionId', unit ->> 'dataProfileVersionId'
        ));
        if row(unit ->> 'unitKey', unit ->> 'dependencyDigest')
          is distinct from row(expected_unit_key, expected_dependency_digest)
        then
          raise exception 'task run manifest contains non-canonical Unit digests';
        end if;
      end loop;

      expected_manifest_hash := atlas.task_sha256_json(jsonb_build_object(
        'schemaVersion', new.schema_version,
        'taskRunId', new.task_run_id::text,
        'taskPlanVersionId', new.task_plan_version_id::text,
        'triggerSource', new.trigger_source,
        'triggerFingerprint', new.trigger_fingerprint,
        'tenantId', new.tenant_id::text,
        'projectId', new.project_id::text,
        'iterationId', new.iteration_id,
        'units', new.units,
        'policyDigests', new.policy_digests,
        'compilerVersion', new.compiler_version
      ));
      expected_request_digest := atlas.task_sha256_json(jsonb_build_object(
        'schemaVersion', 'atlas.task-run-request/0.1',
        'tenantId', new.tenant_id::text,
        'projectId', new.project_id::text,
        'taskPlanVersionId', new.task_plan_version_id::text,
        'triggerSource', new.trigger_source,
        'triggerFingerprint', new.trigger_fingerprint,
        'iterationId', new.iteration_id,
        'units', new.units,
        'policyDigests', new.policy_digests,
        'compilerVersion', new.compiler_version
      ));
      if new.manifest_hash is distinct from expected_manifest_hash
        or stored_run.manifest_hash is distinct from expected_manifest_hash
        or stored_run.request_digest is distinct from expected_request_digest
      then
        raise exception 'task run manifest or request digest is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_execution_unit_v2_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      manifest_unit jsonb;
      parent_state text;
      parent_legacy boolean;
    begin
      select manifest.units -> (new.ordinal - 1), run.materialization_state, run.legacy_unsealed
      into manifest_unit, parent_state, parent_legacy
      from atlas.task_run_manifest manifest
      join atlas.task_run run on run.id = manifest.task_run_id
      where manifest.task_run_id = new.task_run_id
        and manifest.tenant_id = new.tenant_id
        and manifest.project_id = new.project_id
        and manifest.manifest_hash = new.manifest_hash;
      if not found
        or manifest_unit is null
        or parent_legacy
        or parent_state <> 'MATERIALIZING'
      then
        raise exception 'execution unit requires its current v2 run manifest entry';
      end if;
      if row(
        manifest_unit ->> 'unitKey',
        manifest_unit ->> 'caseVersionId',
        manifest_unit ->> 'executionProfileVersionId',
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
        new.execution_profile_version_id::text,
        new.fixture_blueprint_version_id::text,
        new.identity_profile_version_id::text,
        new.environment_id::text,
        new.browser_profile_version_id::text,
        new.data_profile_version_id::text,
        new.parameter_digest,
        new.dependency_digest
      ) then
        raise exception 'execution unit bindings must match its v2 run manifest';
      end if;
      if new.revision <> 1
        or new.lifecycle <> 'QUEUED'
        or new.quality <> 'PENDING'
        or new.hygiene not in ('PENDING', 'NOT_REQUIRED')
      then
        raise exception 'execution unit must start queued at revision one with initial hygiene';
      end if;
      return new;
    end;
    $$
    """,
)

UPGRADE_STATEMENTS += (
    "drop trigger task_plan_version_guard_insert on atlas.task_plan_version",
    """
    create trigger task_plan_version_guard_insert
      before insert on atlas.task_plan_version
      for each row execute function atlas.guard_task_plan_version_v2_insert()
    """,
    "drop trigger task_run_guard_insert on atlas.task_run",
    """
    create trigger task_run_guard_insert
      before insert on atlas.task_run
      for each row execute function atlas.guard_task_run_v2_insert()
    """,
    "drop trigger task_run_guard_update on atlas.task_run",
    """
    create trigger task_run_guard_update
      before update on atlas.task_run
      for each row execute function atlas.guard_task_run_v2_update()
    """,
    "drop trigger task_run_manifest_guard_insert on atlas.task_run_manifest",
    """
    create trigger task_run_manifest_guard_insert
      before insert on atlas.task_run_manifest
      for each row execute function atlas.guard_task_run_manifest_v2_insert()
    """,
    "drop trigger execution_unit_guard_insert on atlas.execution_unit",
    """
    create trigger execution_unit_guard_insert
      before insert on atlas.execution_unit
      for each row execute function atlas.guard_execution_unit_v2_insert()
    """,
    "drop trigger execution_unit_guard_update on atlas.execution_unit",
    """
    create trigger execution_unit_guard_update
      before update on atlas.execution_unit
      for each row execute function atlas.guard_execution_unit_v2_update()
    """,
    "drop trigger unit_attempt_guard_insert on atlas.unit_attempt",
    """
    create trigger unit_attempt_guard_insert
      before insert on atlas.unit_attempt
      for each row execute function atlas.guard_unit_attempt_v2_insert()
    """,
    "drop trigger unit_attempt_guard_update on atlas.unit_attempt",
    """
    create trigger unit_attempt_guard_update
      before update on atlas.unit_attempt
      for each row execute function atlas.guard_unit_attempt_v2_update()
    """,
)

# Privilege statements are declared near their related policies above, but all
# referenced trusted functions must exist before PostgreSQL can REVOKE/GRANT
# them. Keep execution ordering explicit without duplicating the SQL strings.
_security_start = next(
    index
    for index, statement in enumerate(UPGRADE_STATEMENTS)
    if statement.strip().startswith(
        "alter table atlas.execution_profile_version enable row level security"
    )
)
_trusted_function_start = next(
    index
    for index, statement in enumerate(UPGRADE_STATEMENTS)
    if statement.strip().startswith(
        "create function atlas.seal_task_run_materialization"
    )
)
_security_statements = UPGRADE_STATEMENTS[_security_start:_trusted_function_start]
UPGRADE_STATEMENTS = (
    UPGRADE_STATEMENTS[:_security_start]
    + UPGRADE_STATEMENTS[_trusted_function_start:]
    + _security_statements
)

DOWNGRADE_STATEMENTS = (
    "drop trigger if exists task_plan_version_guard_insert on atlas.task_plan_version",
    """
    create trigger task_plan_version_guard_insert
      before insert on atlas.task_plan_version
      for each row execute function atlas.guard_task_plan_version_insert()
    """,
    "drop trigger if exists task_run_guard_insert on atlas.task_run",
    """
    create trigger task_run_guard_insert
      before insert on atlas.task_run
      for each row execute function atlas.guard_task_run_insert()
    """,
    "drop trigger if exists task_run_guard_update on atlas.task_run",
    """
    create trigger task_run_guard_update
      before update on atlas.task_run
      for each row execute function atlas.guard_task_run_update()
    """,
    "drop trigger if exists task_run_manifest_guard_insert on atlas.task_run_manifest",
    """
    create trigger task_run_manifest_guard_insert
      before insert on atlas.task_run_manifest
      for each row execute function atlas.guard_task_run_manifest_insert()
    """,
    "drop trigger if exists execution_unit_guard_insert on atlas.execution_unit",
    """
    create trigger execution_unit_guard_insert
      before insert on atlas.execution_unit
      for each row execute function atlas.guard_execution_unit_insert()
    """,
    "drop trigger if exists execution_unit_guard_update on atlas.execution_unit",
    """
    create trigger execution_unit_guard_update
      before update on atlas.execution_unit
      for each row execute function atlas.guard_execution_unit_update()
    """,
    "drop trigger if exists unit_attempt_guard_insert on atlas.unit_attempt",
    """
    create trigger unit_attempt_guard_insert
      before insert on atlas.unit_attempt
      for each row execute function atlas.guard_unit_attempt_insert()
    """,
    "drop trigger if exists unit_attempt_guard_update on atlas.unit_attempt",
    """
    create trigger unit_attempt_guard_update
      before update on atlas.unit_attempt
      for each row execute function atlas.guard_unit_attempt_update()
    """,
    "drop trigger if exists task_run_register_workflow_identity on atlas.task_run",
    "drop trigger if exists unit_attempt_register_workflow_identity on atlas.unit_attempt",
    "drop function if exists atlas.register_task_run_workflow_identity()",
    "drop function if exists atlas.register_unit_attempt_workflow_identity()",
    "drop function if exists atlas.seal_task_run_materialization(uuid, bigint)",
    """
    drop function if exists atlas.transition_task_run_state(
      uuid, bigint, text, text, text,
      timestamptz, timestamptz, timestamptz, timestamptz
    )
    """,
    """
    drop function if exists atlas.transition_execution_unit_state(
      uuid, uuid, bigint, text, text, text,
      timestamptz, timestamptz, timestamptz, timestamptz
    )
    """,
    """
    drop function if exists atlas.transition_unit_attempt_state(
      uuid, uuid, uuid, bigint, text, text, text,
      timestamptz, timestamptz, timestamptz, timestamptz
    )
    """,
    "drop function if exists atlas.guard_task_plan_version_v2_insert()",
    "drop function if exists atlas.guard_task_run_v2_insert()",
    "drop function if exists atlas.guard_task_run_v2_update()",
    "drop function if exists atlas.guard_task_run_manifest_v2_insert()",
    "drop function if exists atlas.guard_execution_unit_v2_insert()",
    "drop function if exists atlas.guard_execution_unit_v2_update()",
    "drop function if exists atlas.guard_unit_attempt_v2_insert()",
    "drop function if exists atlas.guard_unit_attempt_v2_update()",
    "drop table if exists atlas.task_workflow_start_intent",
    "drop table if exists atlas.task_workflow_identity_registry",
    """
    alter table atlas.task_plan_version
      drop constraint if exists task_plan_version_profiles_v2_valid
    """,
    "alter table atlas.task_plan_version disable trigger task_plan_version_prevent_mutation",
    """
    update atlas.task_plan_version version
    set profile_refs = jsonb_set(
      version.profile_refs,
      '{caseProfiles}',
      (
        select jsonb_agg(
          case
            when profile.value ? 'executionProfileVersionId' then
              (profile.value - 'executionProfileVersionId')
              || jsonb_build_object(
                'executionContractVersionId',
                profile.value -> 'executionProfileVersionId'
              )
            else profile.value
          end
          order by profile.ordinality
        )
        from jsonb_array_elements(version.profile_refs -> 'caseProfiles')
          with ordinality profile(value, ordinality)
      )
    )
    where jsonb_typeof(version.profile_refs -> 'caseProfiles') = 'array'
      and exists (
        select 1
        from jsonb_array_elements(version.profile_refs -> 'caseProfiles') profile(value)
        where profile.value ? 'executionProfileVersionId'
      )
    """,
    "alter table atlas.task_plan_version enable trigger task_plan_version_prevent_mutation",
    """
    alter table atlas.task_plan_version
      add constraint task_plan_version_profiles_valid check (
        atlas.task_profile_refs_valid(profile_refs, pinned_case_version_ids)
      )
    """,
    """
    alter table atlas.task_run_manifest
      drop constraint if exists task_run_manifest_units_v2_valid
    """,
    "alter table atlas.task_run_manifest disable trigger task_run_manifest_prevent_mutation",
    """
    update atlas.task_run_manifest manifest
    set units = (
      select jsonb_agg(
        case
          when unit.value ? 'executionProfileVersionId' then
            (unit.value - 'executionProfileVersionId')
            || jsonb_build_object(
              'executionContractVersionId',
              unit.value -> 'executionProfileVersionId'
            )
          else unit.value
        end
        order by unit.ordinality
      )
      from jsonb_array_elements(manifest.units)
        with ordinality unit(value, ordinality)
    )
    where jsonb_typeof(manifest.units) = 'array'
      and exists (
        select 1
        from jsonb_array_elements(manifest.units) unit(value)
        where unit.value ? 'executionProfileVersionId'
      )
    """,
    "alter table atlas.task_run_manifest enable trigger task_run_manifest_prevent_mutation",
    """
    alter table atlas.task_run_manifest
      add constraint task_run_manifest_units_valid check (
        atlas.task_manifest_units_valid(units)
        and unit_count = jsonb_array_length(units)
        and unit_count between 1 and 100000
      )
    """,
    """
    alter table atlas.execution_unit
      drop constraint if exists execution_unit_execution_profile_scope_fk,
      drop constraint if exists execution_unit_identity_profile_scope_fk,
      drop constraint if exists execution_unit_browser_profile_scope_fk,
      drop constraint if exists execution_unit_data_profile_scope_fk
    """,
    "drop index if exists atlas.execution_unit_execution_profile_scope_fk_idx",
    "drop index if exists atlas.execution_unit_identity_profile_scope_fk_idx",
    "drop index if exists atlas.execution_unit_browser_profile_scope_fk_idx",
    "drop index if exists atlas.execution_unit_data_profile_scope_fk_idx",
    "drop function if exists atlas.task_identity_profile_content_digest(uuid)",
    "drop table if exists atlas.identity_profile_actor_binding",
    "drop table if exists atlas.execution_profile_version",
    "drop table if exists atlas.identity_profile_version",
    "drop table if exists atlas.browser_profile_version",
    "drop table if exists atlas.data_profile_version",
    """
    alter table atlas.execution_unit
      rename column execution_profile_version_id to execution_contract_version_id
    """,
    """
    alter table atlas.unit_attempt
      drop constraint if exists unit_attempt_temporal_identity_pair,
      drop column if exists temporal_namespace
    """,
    "drop index if exists atlas.task_run_trigger_request_digest_idx",
    """
    alter table atlas.task_run
      drop constraint if exists task_run_request_digest_valid,
      drop constraint if exists task_run_materialization_valid,
      drop constraint if exists task_run_temporal_identity_pair,
      drop column if exists request_digest,
      drop column if exists materialization_state,
      drop column if exists materialized_unit_count,
      drop column if exists materialized_first_attempt_count,
      drop column if exists materialization_sealed_at,
      drop column if exists temporal_namespace,
      drop column if exists legacy_unsealed
    """,
    "alter function atlas.guard_task_run_event_insert() security invoker",
    """
    grant update (
      lifecycle, quality, hygiene, started_at, finalized_at,
      cleanup_resolved_at, closed_at, revision
    ) on atlas.task_run to atlas_app
    """,
    """
    grant update (
      lifecycle, quality, hygiene, started_at, finalized_at,
      cleanup_resolved_at, closed_at, revision
    ) on atlas.execution_unit to atlas_app
    """,
    """
    grant update (
      lifecycle, quality, hygiene, started_at, finalized_at,
      cleanup_resolved_at, closed_at, revision
    ) on atlas.unit_attempt to atlas_app
    """,
    "drop function if exists atlas.guard_execution_profile_insert()",
    "drop function if exists atlas.guard_identity_profile_insert()",
    "drop function if exists atlas.guard_identity_profile_actor_insert()",
    "drop function if exists atlas.guard_browser_profile_insert()",
    "drop function if exists atlas.guard_data_profile_insert()",
    "drop function if exists atlas.guard_task_profile_update()",
    """
    drop function if exists atlas.task_profile_content_digest(
      text, uuid, uuid, text, text, text, jsonb
    )
    """,
    "drop function if exists atlas.task_manifest_units_v2_valid(jsonb)",
    "drop function if exists atlas.task_profile_refs_v2_valid(jsonb, uuid[])",
    "drop function if exists atlas.task_profile_ref_valid(text, text, text, text)",
    """
    drop function if exists atlas.task_profile_lifecycle_valid(
      text, timestamptz, timestamptz, timestamptz, timestamptz, timestamptz
    )
    """,
    "drop function if exists atlas.task_json_has_sensitive_keys(jsonb)",
    "drop function if exists atlas.task_sha256_json(jsonb)",
    "drop function if exists atlas.task_canonical_json(jsonb)",
)


def upgrade() -> None:
    """Add Task dispatch prerequisite facts and trusted transitions."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove Task dispatch prerequisites."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
