"""Add Task-to-Fixture binding and append-only Unit Hygiene truth.

Revision ID: 20260718_0035
Revises: 20260718_0034
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0035"
down_revision: str | None = "20260718_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.attempt_fixture_binding (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      fixture_run_id uuid not null,
      fixture_blueprint_version_id uuid not null,
      environment_id uuid not null,
      fixture_plan_digest text not null,
      created_at timestamptz not null,
      binding_hash text not null,
      binding jsonb not null,
      constraint attempt_fixture_binding_attempt_scope_fk foreign key (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.unit_attempt (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint attempt_fixture_binding_fixture_scope_fk foreign key (
        fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.fixture_run (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint attempt_fixture_binding_attempt_unique unique (unit_attempt_id),
      constraint attempt_fixture_binding_fixture_unique unique (fixture_run_id),
      constraint attempt_fixture_binding_full_scope_unique unique (
        id, unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ),
      constraint attempt_fixture_binding_digest_valid check (
        fixture_plan_digest ~ '^sha256:[0-9a-f]{64}$'
        and binding_hash ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint attempt_fixture_binding_json_valid check (
        jsonb_typeof(binding) = 'object'
      )
    )
    """,
    """
    create table atlas.unit_hygiene_resolution_revision (
      id uuid primary key,
      unit_hygiene_resolution_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      manifest_hash text not null,
      unit_key text not null,
      revision integer not null,
      inputs jsonb not null,
      input_set_hash text not null,
      data_hygiene text not null,
      resolution_policy_version text not null,
      resolution_policy_digest text not null,
      supersedes_revision_id uuid,
      projection_watermark timestamptz not null,
      created_at timestamptz not null,
      resolution_hash text not null,
      resolution jsonb not null,
      constraint unit_hygiene_resolution_unit_scope_fk foreign key (
        execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.execution_unit (
        id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint unit_hygiene_resolution_supersedes_fk foreign key (
        supersedes_revision_id
      ) references atlas.unit_hygiene_resolution_revision(id) on delete restrict,
      constraint unit_hygiene_resolution_revision_unique unique (
        execution_unit_id, revision
      ),
      constraint unit_hygiene_resolution_input_unique unique (
        execution_unit_id, input_set_hash, resolution_policy_digest
      ),
      constraint unit_hygiene_resolution_hash_unique unique (
        tenant_id, resolution_hash
      ),
      constraint unit_hygiene_resolution_full_scope_unique unique (
        id, execution_unit_id, task_run_id, tenant_id, project_id, revision
      ),
      constraint unit_hygiene_resolution_revision_valid check (
        revision >= 1
        and (
          (revision = 1 and supersedes_revision_id is null)
          or
          (revision > 1 and supersedes_revision_id is not null)
        )
      ),
      constraint unit_hygiene_resolution_inputs_valid check (
        jsonb_typeof(inputs) = 'array'
        and jsonb_array_length(inputs) between 1 and 100
      ),
      constraint unit_hygiene_resolution_state_valid check (
        data_hygiene in (
          'PENDING', 'CLEANED', 'CLEANUP_FAILED', 'LEAKED', 'NOT_APPLICABLE'
        )
      ),
      constraint unit_hygiene_resolution_policy_valid check (
        resolution_policy_version = '0.1.0'
        and resolution_policy_digest =
          'sha256:e8ad3538745d89b0a9516846a4d42a4cb9703ca5d964b894d29679a14f952e37'
      ),
      constraint unit_hygiene_resolution_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and unit_key ~ '^sha256:[0-9a-f]{64}$'
        and input_set_hash ~ '^sha256:[0-9a-f]{64}$'
        and resolution_policy_digest ~ '^sha256:[0-9a-f]{64}$'
        and resolution_hash ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint unit_hygiene_resolution_time_valid check (
        created_at >= projection_watermark
      ),
      constraint unit_hygiene_resolution_json_valid check (
        jsonb_typeof(resolution) = 'object'
      )
    )
    """,
    """
    create function atlas.guard_attempt_fixture_binding_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_attempt atlas.unit_attempt%rowtype;
      stored_unit atlas.execution_unit%rowtype;
      stored_fixture atlas.fixture_run%rowtype;
      expected_execution_id text;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'AttemptFixtureBinding insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into stored_attempt
      from atlas.unit_attempt attempt
      where attempt.id = new.unit_attempt_id
        and attempt.execution_unit_id = new.execution_unit_id
        and attempt.task_run_id = new.task_run_id
        and attempt.tenant_id = new.tenant_id
        and attempt.project_id = new.project_id
      for share;
      select * into stored_unit
      from atlas.execution_unit unit
      where unit.id = new.execution_unit_id
        and unit.task_run_id = new.task_run_id
        and unit.tenant_id = new.tenant_id
        and unit.project_id = new.project_id
      for share;
      select * into stored_fixture
      from atlas.fixture_run fixture
      where fixture.id = new.fixture_run_id
        and fixture.tenant_id = new.tenant_id
        and fixture.project_id = new.project_id
        and fixture.environment_id = new.environment_id
      for share;
      expected_execution_id := 'unit-attempt:' || new.unit_attempt_id::text;

      if stored_attempt.id is null
        or stored_unit.id is null
        or stored_fixture.id is null
        or stored_fixture.run_kind <> 'EXECUTION'
        or stored_fixture.execution_id <> expected_execution_id
        or stored_fixture.blueprint_version_id
          <> stored_unit.fixture_blueprint_version_id
        or not exists (
          select 1
          from atlas.data_blueprint_version blueprint
          where blueprint.id = stored_fixture.blueprint_version_id
            and blueprint.tenant_id = stored_fixture.tenant_id
            and blueprint.project_id = stored_fixture.project_id
            and blueprint.plan_digest = stored_fixture.plan_digest
        )
        or stored_fixture.environment_id <> stored_unit.environment_id
        or new.fixture_blueprint_version_id
          <> stored_fixture.blueprint_version_id
        or new.fixture_plan_digest <> stored_fixture.plan_digest
        or new.created_at <> stored_fixture.requested_at
      then
        raise exception 'AttemptFixtureBinding scope or frozen Fixture is invalid';
      end if;

      if atlas.task_json_has_sensitive_keys(new.binding)
        or atlas.task_json_object_size(new.binding) <> 13
        or not (
          new.binding ?& array[
            'schemaVersion', 'id', 'tenantId', 'projectId', 'taskRunId',
            'executionUnitId', 'unitAttemptId', 'fixtureRunId',
            'fixtureBlueprintVersionId', 'environmentId',
            'fixturePlanDigest', 'createdAt', 'bindingHash'
          ]
        )
        or new.binding ->> 'schemaVersion'
          is distinct from 'atlas.attempt-fixture-binding/0.1'
        or new.binding ->> 'id' is distinct from new.id::text
        or new.binding ->> 'tenantId' is distinct from new.tenant_id::text
        or new.binding ->> 'projectId' is distinct from new.project_id::text
        or new.binding ->> 'taskRunId' is distinct from new.task_run_id::text
        or new.binding ->> 'executionUnitId'
          is distinct from new.execution_unit_id::text
        or new.binding ->> 'unitAttemptId'
          is distinct from new.unit_attempt_id::text
        or new.binding ->> 'fixtureRunId'
          is distinct from new.fixture_run_id::text
        or new.binding ->> 'fixtureBlueprintVersionId'
          is distinct from new.fixture_blueprint_version_id::text
        or new.binding ->> 'environmentId'
          is distinct from new.environment_id::text
        or new.binding ->> 'fixturePlanDigest'
          is distinct from new.fixture_plan_digest
        or (new.binding ->> 'createdAt')::timestamptz
          is distinct from new.created_at
        or new.binding ->> 'bindingHash' is distinct from new.binding_hash
        or atlas.task_sha256_json(new.binding - 'bindingHash')
          is distinct from new.binding_hash
      then
        raise exception 'AttemptFixtureBinding persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_unit_hygiene_resolution_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_unit atlas.execution_unit%rowtype;
      previous atlas.unit_hygiene_resolution_revision%rowtype;
      input_value jsonb;
      stored_attempt atlas.unit_attempt%rowtype;
      stored_binding atlas.attempt_fixture_binding%rowtype;
      stored_fixture atlas.fixture_run%rowtype;
      expected_manifest_digest text;
      expected_resource_hash text;
      expected_hygiene text;
      expected_watermark timestamptz;
      expected_input_hash text;
      expected_resource_count integer;
      expected_cleaned_count integer;
      expected_leaked_count integer;
      expected_unresolved_count integer;
      expected_exhausted_count integer;
      expected_reconcile_pending_count integer;
      closed_attempt_count integer;
      input_index integer := 0;
      saw_leaked boolean := false;
      saw_cleanup_failed boolean := false;
      saw_pending boolean := false;
      saw_cleaned boolean := false;
      saw_not_applicable boolean := false;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'UnitHygieneResolution insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into stored_unit
      from atlas.execution_unit unit
      where unit.id = new.execution_unit_id
        and unit.task_run_id = new.task_run_id
        and unit.tenant_id = new.tenant_id
        and unit.project_id = new.project_id
        and unit.manifest_hash = new.manifest_hash
        and unit.unit_key = new.unit_key
      for update;
      if stored_unit.id is null then
        raise exception 'UnitHygieneResolution requires the exact ExecutionUnit';
      end if;

      select count(*) into closed_attempt_count
      from atlas.unit_attempt attempt
      where attempt.execution_unit_id = new.execution_unit_id
        and attempt.lifecycle = 'CLOSED';
      if closed_attempt_count <> jsonb_array_length(new.inputs)
        or closed_attempt_count < 1
      then
        raise exception 'UnitHygieneResolution closed Attempt coverage is invalid';
      end if;

      select * into previous
      from atlas.unit_hygiene_resolution_revision candidate
      where candidate.execution_unit_id = new.execution_unit_id
      order by candidate.revision desc
      limit 1;
      if found then
        if new.revision <> previous.revision + 1
          or new.supersedes_revision_id <> previous.id
          or new.unit_hygiene_resolution_id
            <> previous.unit_hygiene_resolution_id
        then
          raise exception 'UnitHygieneResolution revision chain is invalid';
        end if;
      elsif new.revision <> 1
        or new.supersedes_revision_id is not null
      then
        raise exception 'UnitHygieneResolution first revision is invalid';
      end if;

      for input_value in
        select value from jsonb_array_elements(new.inputs)
      loop
        input_index := input_index + 1;
        if jsonb_typeof(input_value) <> 'object'
          or atlas.task_json_has_sensitive_keys(input_value)
          or atlas.task_json_object_size(input_value) <> 19
          or not (
            input_value ?& array[
              'unitAttemptId', 'attemptNumber', 'source', 'dataHygiene',
              'fixtureBindingId', 'fixtureRunId', 'fixtureRunRevision',
              'fixtureRunStatus', 'cleanupGeneration', 'fixturePlanDigest',
              'fixtureManifestDigest', 'resourceStateHash', 'resourceCount',
              'cleanedResourceCount', 'leakedResourceCount',
              'unresolvedResourceCount', 'exhaustedReconcileCount',
              'unresolvedReconcileCount', 'observedAt'
            ]
          )
        then
          raise exception 'UnitHygieneResolution input is not canonical';
        end if;

        select * into stored_attempt
        from atlas.unit_attempt attempt
        where attempt.id = (input_value ->> 'unitAttemptId')::uuid
          and attempt.execution_unit_id = new.execution_unit_id
          and attempt.task_run_id = new.task_run_id
          and attempt.tenant_id = new.tenant_id
          and attempt.project_id = new.project_id
          and attempt.lifecycle = 'CLOSED';
        if stored_attempt.id is null
          or stored_attempt.attempt_number <> input_index
          or (input_value ->> 'attemptNumber')::integer <> input_index
        then
          raise exception 'UnitHygieneResolution Attempt order is invalid';
        end if;

        if input_value ->> 'source' = 'EXPLICIT_NOT_REQUIRED' then
          if stored_attempt.hygiene <> 'NOT_REQUIRED'
            or exists (
              select 1 from atlas.attempt_fixture_binding binding
              where binding.unit_attempt_id = stored_attempt.id
            )
            or input_value ->> 'dataHygiene' <> 'NOT_APPLICABLE'
            or input_value -> 'fixtureBindingId' <> 'null'::jsonb
            or input_value -> 'fixtureRunId' <> 'null'::jsonb
            or input_value -> 'fixtureRunRevision' <> 'null'::jsonb
            or input_value -> 'fixtureRunStatus' <> 'null'::jsonb
            or input_value -> 'cleanupGeneration' <> 'null'::jsonb
            or input_value -> 'fixturePlanDigest' <> 'null'::jsonb
            or input_value -> 'fixtureManifestDigest' <> 'null'::jsonb
            or (input_value ->> 'resourceCount')::integer <> 0
            or (input_value ->> 'cleanedResourceCount')::integer <> 0
            or (input_value ->> 'leakedResourceCount')::integer <> 0
            or (input_value ->> 'unresolvedResourceCount')::integer <> 0
            or (input_value ->> 'exhaustedReconcileCount')::integer <> 0
            or (input_value ->> 'unresolvedReconcileCount')::integer <> 0
            or (input_value ->> 'observedAt')::timestamptz
              <> coalesce(
                stored_attempt.cleanup_resolved_at,
                stored_attempt.updated_at
              )
            or input_value ->> 'resourceStateHash'
              <> atlas.task_sha256_json(
                jsonb_build_object(
                  'schemaVersion', 'atlas.explicit-no-cleanup/0.1',
                  'unitAttemptId', stored_attempt.id::text,
                  'attemptNumber', stored_attempt.attempt_number,
                  'hygiene', stored_attempt.hygiene,
                  'updatedAt', to_jsonb(stored_attempt.updated_at)
                )
              )
          then
            raise exception 'explicit no-cleanup input is invalid';
          end if;
          saw_not_applicable := true;
        elsif input_value ->> 'source' = 'FIXTURE_RUN' then
          select * into stored_binding
          from atlas.attempt_fixture_binding binding
          where binding.id = (input_value ->> 'fixtureBindingId')::uuid
            and binding.unit_attempt_id = stored_attempt.id
            and binding.execution_unit_id = new.execution_unit_id;
          select * into stored_fixture
          from atlas.fixture_run fixture
          where fixture.id = stored_binding.fixture_run_id;
          if stored_binding.id is null or stored_fixture.id is null then
            raise exception 'Fixture cleanup input has no exact binding';
          end if;

          select manifest.manifest_digest into expected_manifest_digest
          from atlas.fixture_manifest manifest
          where manifest.fixture_run_id = stored_fixture.id;
          select
            count(*) filter (where ownership = 'CREATED'),
            count(*) filter (
              where ownership = 'CREATED' and status = 'CLEANED'
            ),
            count(*) filter (
              where ownership = 'CREATED' and status = 'LEAKED'
            ),
            count(*) filter (
              where ownership = 'CREATED'
                and status not in ('CLEANED', 'LEAKED')
            )
          into
            expected_resource_count,
            expected_cleaned_count,
            expected_leaked_count,
            expected_unresolved_count
          from atlas.resource_record resource
          where resource.fixture_run_id = stored_fixture.id;
          select
            count(*) filter (where reconcile_state = 'EXHAUSTED'),
            count(*) filter (
              where reconcile_state in ('PENDING', 'RUNNING', 'INCONCLUSIVE')
            )
          into expected_exhausted_count, expected_reconcile_pending_count
          from atlas.data_node_run node
          where node.fixture_run_id = stored_fixture.id
            and node.status = 'OUTCOME_UNCERTAIN';

          expected_hygiene := case stored_fixture.cleanup_state
            when 'NOT_REQUIRED' then 'NOT_APPLICABLE'
            when 'CLEANED' then 'CLEANED'
            when 'LEAKED' then 'LEAKED'
            else 'PENDING'
          end;
          expected_resource_hash := atlas.task_sha256_json(
            jsonb_build_object(
              'schemaVersion', 'atlas.fixture-cleanup-observation/0.1',
              'fixtureRunId', stored_fixture.id::text,
              'fixtureRunRevision', stored_fixture.revision,
              'cleanupGeneration', stored_fixture.cleanup_generation,
              'cleanupState', stored_fixture.cleanup_state,
              'resources', coalesce(
                (
                  select jsonb_agg(
                    jsonb_build_object(
                      'id', resource.id::text,
                      'fixtureRunId', resource.fixture_run_id::text,
                      'dataNodeRunId', resource.data_node_run_id::text,
                      'connectorInstallationId',
                        resource.connector_installation_id::text,
                      'resourceHandle', resource.resource_handle,
                      'resourceType', resource.resource_type,
                      'ownership', resource.ownership,
                      'status', resource.status,
                      'expiresAt', to_jsonb(resource.expires_at),
                      'cleanupGeneration', resource.cleanup_generation,
                      'nextCleanupAt', to_jsonb(resource.next_cleanup_at),
                      'createdAt', to_jsonb(resource.created_at),
                      'cleanedAt', to_jsonb(resource.cleaned_at),
                      'revision', resource.revision,
                      'updatedAt', to_jsonb(resource.updated_at)
                    )
                    order by resource.created_at, resource.id
                  )
                  from atlas.resource_record resource
                  where resource.fixture_run_id = stored_fixture.id
                ),
                '[]'::jsonb
              ),
              'cleanupAttempts', coalesce(
                (
                  select jsonb_agg(
                    jsonb_build_object(
                      'id', attempt.id::text,
                      'fixtureRunId', attempt.fixture_run_id::text,
                      'resourceRecordId', attempt.resource_record_id::text,
                      'cleanupGeneration', attempt.cleanup_generation,
                      'status', attempt.status,
                      'workerIdentity', attempt.worker_identity,
                      'failureCategory', attempt.failure_category,
                      'failureCode', attempt.failure_code,
                      'failureDetail', attempt.failure_detail,
                      'providerRequestId', attempt.provider_request_id,
                      'startedAt', to_jsonb(attempt.started_at),
                      'finishedAt', to_jsonb(attempt.finished_at),
                      'updatedAt', to_jsonb(attempt.updated_at)
                    )
                    order by attempt.started_at, attempt.id
                  )
                  from atlas.resource_cleanup_attempt attempt
                  where attempt.fixture_run_id = stored_fixture.id
                ),
                '[]'::jsonb
              ),
              'uncertainNodes', coalesce(
                (
                  select jsonb_agg(
                    jsonb_build_object(
                      'id', node.id::text,
                      'nodeId', node.node_id,
                      'status', node.status,
                      'reconcileState', node.reconcile_state,
                      'reconcileAttemptCount', node.reconcile_attempt_count,
                      'revision', node.revision,
                      'updatedAt', to_jsonb(node.updated_at)
                    )
                    order by node.execution_level, node.node_id
                  )
                  from atlas.data_node_run node
                  where node.fixture_run_id = stored_fixture.id
                    and node.status = 'OUTCOME_UNCERTAIN'
                ),
                '[]'::jsonb
              )
            )
          );
          if stored_fixture.tenant_id <> new.tenant_id
            or stored_fixture.project_id <> new.project_id
            or stored_binding.fixture_plan_digest <> stored_fixture.plan_digest
            or input_value ->> 'fixtureRunId'
              <> stored_fixture.id::text
            or (input_value ->> 'fixtureRunRevision')::bigint
              <> stored_fixture.revision
            or input_value ->> 'fixtureRunStatus'
              <> stored_fixture.status
            or (input_value ->> 'cleanupGeneration')::integer
              <> stored_fixture.cleanup_generation
            or input_value ->> 'fixturePlanDigest'
              <> stored_fixture.plan_digest
            or input_value ->> 'fixtureManifestDigest'
              is distinct from expected_manifest_digest
            or input_value ->> 'resourceStateHash'
              <> expected_resource_hash
            or (input_value ->> 'resourceCount')::integer
              <> expected_resource_count
            or (input_value ->> 'cleanedResourceCount')::integer
              <> expected_cleaned_count
            or (input_value ->> 'leakedResourceCount')::integer
              <> expected_leaked_count
            or (input_value ->> 'unresolvedResourceCount')::integer
              <> expected_unresolved_count
            or (input_value ->> 'exhaustedReconcileCount')::integer
              <> expected_exhausted_count
            or (input_value ->> 'unresolvedReconcileCount')::integer
              <> expected_reconcile_pending_count
            or (input_value ->> 'observedAt')::timestamptz
              <> stored_fixture.updated_at
            or input_value ->> 'dataHygiene' <> expected_hygiene
          then
            raise exception 'Fixture cleanup input does not match current ledger';
          end if;
          saw_leaked := saw_leaked or expected_hygiene = 'LEAKED';
          saw_pending := saw_pending or expected_hygiene = 'PENDING';
          saw_cleaned := saw_cleaned or expected_hygiene = 'CLEANED';
          saw_not_applicable :=
            saw_not_applicable or expected_hygiene = 'NOT_APPLICABLE';
        else
          raise exception 'UnitHygieneResolution input source is invalid';
        end if;
      end loop;

      expected_hygiene := case
        when saw_leaked then 'LEAKED'
        when saw_cleanup_failed then 'CLEANUP_FAILED'
        when saw_pending then 'PENDING'
        when saw_cleaned then 'CLEANED'
        when saw_not_applicable then 'NOT_APPLICABLE'
        else null
      end;
      select max((value ->> 'observedAt')::timestamptz)
      into expected_watermark
      from jsonb_array_elements(new.inputs);
      expected_input_hash := atlas.task_sha256_json(
        jsonb_build_object(
          'schemaVersion', 'atlas.unit-hygiene-input-set/0.1',
          'executionUnitId', new.execution_unit_id::text,
          'manifestHash', new.manifest_hash,
          'inputs', new.inputs
        )
      );
      if new.data_hygiene <> expected_hygiene
        or new.projection_watermark <> expected_watermark
        or new.input_set_hash <> expected_input_hash
      then
        raise exception 'UnitHygieneResolution aggregation is invalid';
      end if;

      if atlas.task_json_has_sensitive_keys(new.resolution)
        or atlas.task_json_object_size(new.resolution) <> 19
        or not (
          new.resolution ?& array[
            'schemaVersion', 'id', 'unitHygieneResolutionId',
            'tenantId', 'projectId', 'taskRunId', 'executionUnitId',
            'manifestHash', 'unitKey', 'revision', 'inputs', 'inputSetHash',
            'dataHygiene', 'resolutionPolicyVersion',
            'resolutionPolicyDigest', 'supersedesRevisionId',
            'projectionWatermark', 'createdAt', 'resolutionHash'
          ]
        )
        or new.resolution ->> 'schemaVersion'
          is distinct from 'atlas.unit-hygiene-resolution-revision/0.1'
        or new.resolution ->> 'id' is distinct from new.id::text
        or new.resolution ->> 'unitHygieneResolutionId'
          is distinct from new.unit_hygiene_resolution_id::text
        or new.resolution ->> 'tenantId' is distinct from new.tenant_id::text
        or new.resolution ->> 'projectId' is distinct from new.project_id::text
        or new.resolution ->> 'taskRunId' is distinct from new.task_run_id::text
        or new.resolution ->> 'executionUnitId'
          is distinct from new.execution_unit_id::text
        or new.resolution ->> 'manifestHash'
          is distinct from new.manifest_hash
        or new.resolution ->> 'unitKey' is distinct from new.unit_key
        or (new.resolution ->> 'revision')::integer
          is distinct from new.revision
        or new.resolution -> 'inputs' is distinct from new.inputs
        or new.resolution ->> 'inputSetHash'
          is distinct from new.input_set_hash
        or new.resolution ->> 'dataHygiene'
          is distinct from new.data_hygiene
        or new.resolution ->> 'resolutionPolicyVersion'
          is distinct from new.resolution_policy_version
        or new.resolution ->> 'resolutionPolicyDigest'
          is distinct from new.resolution_policy_digest
        or (new.resolution ->> 'supersedesRevisionId')::uuid
          is distinct from new.supersedes_revision_id
        or (new.resolution ->> 'projectionWatermark')::timestamptz
          is distinct from new.projection_watermark
        or (new.resolution ->> 'createdAt')::timestamptz
          is distinct from new.created_at
        or new.resolution ->> 'resolutionHash'
          is distinct from new.resolution_hash
        or atlas.task_sha256_json(
          new.resolution - array[
            'id', 'unitHygieneResolutionId', 'revision',
            'supersedesRevisionId', 'createdAt', 'resolutionHash'
          ]::text[]
        ) is distinct from new.resolution_hash
      then
        raise exception 'UnitHygieneResolution persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger attempt_fixture_binding_guard_insert
      before insert on atlas.attempt_fixture_binding
      for each row execute function atlas.guard_attempt_fixture_binding_insert()
    """,
    """
    create trigger attempt_fixture_binding_prevent_mutation
      before update or delete on atlas.attempt_fixture_binding
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger unit_hygiene_resolution_guard_insert
      before insert on atlas.unit_hygiene_resolution_revision
      for each row execute function atlas.guard_unit_hygiene_resolution_insert()
    """,
    """
    create trigger unit_hygiene_resolution_prevent_mutation
      before update or delete on atlas.unit_hygiene_resolution_revision
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create index attempt_fixture_binding_unit_idx
      on atlas.attempt_fixture_binding (
        tenant_id, project_id, execution_unit_id, unit_attempt_id
      )
    """,
    """
    create index unit_hygiene_resolution_latest_idx
      on atlas.unit_hygiene_resolution_revision (
        tenant_id, project_id, execution_unit_id, revision desc
      )
    """,
    """
    create index unit_hygiene_resolution_task_idx
      on atlas.unit_hygiene_resolution_revision (
        tenant_id, project_id, task_run_id, execution_unit_id, revision desc
      )
    """,
    "alter table atlas.attempt_fixture_binding enable row level security",
    "alter table atlas.attempt_fixture_binding force row level security",
    """
    create policy attempt_fixture_binding_tenant_isolation
      on atlas.attempt_fixture_binding for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "alter table atlas.unit_hygiene_resolution_revision enable row level security",
    "alter table atlas.unit_hygiene_resolution_revision force row level security",
    """
    create policy unit_hygiene_resolution_tenant_isolation
      on atlas.unit_hygiene_resolution_revision for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.attempt_fixture_binding from atlas_app",
    "grant select, insert on atlas.attempt_fixture_binding to atlas_app",
    "revoke all on atlas.unit_hygiene_resolution_revision from atlas_app",
    "grant select, insert on atlas.unit_hygiene_resolution_revision to atlas_app",
    """
    revoke all on function atlas.guard_attempt_fixture_binding_insert()
      from public, atlas_app, atlas_dispatcher
    """,
    """
    revoke all on function atlas.guard_unit_hygiene_resolution_insert()
      from public, atlas_app, atlas_dispatcher
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (
        select 1 from atlas.unit_hygiene_resolution_revision limit 1
      ) or exists (
        select 1 from atlas.attempt_fixture_binding limit 1
      ) then
        raise exception 'cannot downgrade while Task Fixture Hygiene facts exist';
      end if;
    end;
    $$
    """,
    "drop table if exists atlas.unit_hygiene_resolution_revision",
    "drop table if exists atlas.attempt_fixture_binding",
    "drop function if exists atlas.guard_unit_hygiene_resolution_insert()",
    "drop function if exists atlas.guard_attempt_fixture_binding_insert()",
)


def upgrade() -> None:
    """Apply immutable Task Fixture binding and Unit Hygiene truth."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove only empty Cleanup truth tables."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
