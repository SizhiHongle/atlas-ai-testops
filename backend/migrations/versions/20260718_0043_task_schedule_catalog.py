# ruff: noqa: E501
"""Add database-authoritative Task Schedule catalog and fenced Temporal sync.

Revision ID: 20260718_0043
Revises: 20260718_0042
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0043"
down_revision: str | None = "20260718_0042"
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
        raise exception 'Task Schedule function owner must bypass row-level security'
          using errcode = '42501';
      end if;
      if not exists (
        select 1
        from pg_catalog.pg_roles role
        where role.rolname = 'atlas_dispatcher'
      ) then
        raise exception 'atlas_dispatcher must exist before Task Schedule migration'
          using errcode = '42704';
      end if;
    end;
    $$
    """,
    """
    create function atlas.task_schedule_integer_array_valid(
      value jsonb,
      minimum_value integer,
      maximum_value integer,
      maximum_count integer,
      allow_empty boolean
    ) returns boolean
    language plpgsql
    immutable
    set search_path = pg_catalog
    as $$
    declare
      item jsonb;
      numeric_value numeric;
      previous_value integer;
    begin
      if jsonb_typeof(value) is distinct from 'array'
        or jsonb_array_length(value) > maximum_count
        or (not allow_empty and jsonb_array_length(value) = 0)
      then
        return false;
      end if;
      for item in select element from jsonb_array_elements(value) element
      loop
        if jsonb_typeof(item) is distinct from 'number' then
          return false;
        end if;
        numeric_value := (item #>> '{}')::numeric;
        if trunc(numeric_value) <> numeric_value
          or numeric_value not between minimum_value and maximum_value
          or (previous_value is not null and numeric_value::integer <= previous_value)
        then
          return false;
        end if;
        previous_value := numeric_value::integer;
      end loop;
      return true;
    exception
      when others then return false;
    end;
    $$
    """,
    """
    create function atlas.task_schedule_calendar_valid(value jsonb)
    returns boolean
    language sql
    immutable
    set search_path = pg_catalog, atlas
    as $$
      select
        jsonb_typeof(value) = 'object'
        and value ?& array[
          'schemaVersion', 'minutes', 'hours', 'daysOfMonth', 'months',
          'isoDaysOfWeek'
        ]
        and value - array[
          'schemaVersion', 'minutes', 'hours', 'daysOfMonth', 'months',
          'isoDaysOfWeek'
        ] = '{}'::jsonb
        and value ->> 'schemaVersion' = 'atlas.task-schedule-calendar/0.1'
        and atlas.task_schedule_integer_array_valid(
          value -> 'minutes', 0, 59, 60, false
        )
        and atlas.task_schedule_integer_array_valid(
          value -> 'hours', 0, 23, 24, false
        )
        and atlas.task_schedule_integer_array_valid(
          value -> 'daysOfMonth', 1, 31, 31, true
        )
        and atlas.task_schedule_integer_array_valid(
          value -> 'months', 1, 12, 12, true
        )
        and atlas.task_schedule_integer_array_valid(
          value -> 'isoDaysOfWeek', 1, 7, 7, true
        )
    $$
    """,
    """
    create function atlas.task_schedule_retry_policy_valid(value jsonb)
    returns boolean
    language plpgsql
    immutable
    set search_path = pg_catalog, atlas
    as $$
    begin
      return
        jsonb_typeof(value) = 'object'
        and value ?& array[
          'schemaVersion', 'infraRetryAttempts', 'maxTotalInfraRetries',
          'initialBackoffSeconds', 'maximumBackoffSeconds', 'jitterPercent',
          'contentDigest'
        ]
        and value - array[
          'schemaVersion', 'infraRetryAttempts', 'maxTotalInfraRetries',
          'initialBackoffSeconds', 'maximumBackoffSeconds', 'jitterPercent',
          'contentDigest'
        ] = '{}'::jsonb
        and value ->> 'schemaVersion' = 'atlas.task-retry-policy/0.1'
        and (value ->> 'infraRetryAttempts')::integer between 0 and 4
        and (value ->> 'maxTotalInfraRetries')::integer between 0 and 256
        and (value ->> 'initialBackoffSeconds')::integer between 1 and 300
        and (value ->> 'maximumBackoffSeconds')::integer between 1 and 3600
        and (value ->> 'jitterPercent')::integer between 0 and 50
        and (value ->> 'maximumBackoffSeconds')::integer
          >= (value ->> 'initialBackoffSeconds')::integer
        and value ->> 'contentDigest' ~ '^sha256:[0-9a-f]{64}$'
        and value ->> 'contentDigest'
          = atlas.task_sha256_json(value - 'contentDigest');
    exception
      when others then return false;
    end;
    $$
    """,
    """
    create table atlas.task_schedule (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_plan_version_id uuid not null,
      schema_version text not null default 'atlas.task-schedule/0.1',
      schedule_key text not null,
      name text not null,
      calendar jsonb not null,
      time_zone_name text not null,
      overlap_policy text not null,
      catchup_policy text not null,
      catchup_window_seconds integer not null,
      jitter_seconds integer not null,
      iteration_id text,
      retry_policy jsonb not null,
      temporal_namespace text not null,
      temporal_schedule_id text not null,
      content_digest text not null,
      status text not null default 'ACTIVE',
      pause_reason text,
      sync_status text not null default 'PENDING',
      synced_revision bigint,
      last_sync_error_code text,
      next_fire_times_utc timestamptz[] not null default '{}'::timestamptz[],
      created_by uuid not null,
      updated_by uuid not null,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint task_schedule_plan_version_scope_fk foreign key (
        task_plan_version_id, tenant_id, project_id
      ) references atlas.task_plan_version (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_schedule_full_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint task_schedule_project_key_unique unique (
        tenant_id, project_id, schedule_key
      ),
      constraint task_schedule_temporal_identity_unique unique (
        temporal_namespace, temporal_schedule_id
      ),
      constraint task_schedule_schema_valid check (
        schema_version = 'atlas.task-schedule/0.1'
      ),
      constraint task_schedule_key_valid check (
        schedule_key ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+){0,7}$'
        and char_length(schedule_key) between 3 and 160
      ),
      constraint task_schedule_name_valid check (
        btrim(name) <> '' and char_length(name) <= 160
      ),
      constraint task_schedule_calendar_valid check (
        atlas.task_schedule_calendar_valid(calendar)
      ),
      constraint task_schedule_time_zone_valid check (
        time_zone_name ~ '^[A-Za-z0-9][A-Za-z0-9._+-]*(/[A-Za-z0-9._+-]+){0,7}$'
        and char_length(time_zone_name) <= 128
      ),
      constraint task_schedule_policy_valid check (
        overlap_policy in ('QUEUE_ONE', 'SKIP')
        and catchup_policy in ('RUN_ONCE', 'SKIP')
        and catchup_window_seconds between 60 and 604800
        and jitter_seconds between 0 and 3600
        and jitter_seconds < catchup_window_seconds
        and atlas.task_schedule_retry_policy_valid(retry_policy)
      ),
      constraint task_schedule_iteration_valid check (
        iteration_id is null
        or (
          char_length(iteration_id) between 3 and 160
          and iteration_id ~ '^[A-Za-z0-9][A-Za-z0-9._:@/+=-]{2,159}$'
        )
      ),
      constraint task_schedule_temporal_identity_valid check (
        temporal_namespace ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
        and temporal_schedule_id =
          'atlas-task/schedule/' || replace(tenant_id::text, '-', '') || '/'
          || replace(id::text, '-', '')
      ),
      constraint task_schedule_digest_valid check (
        content_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint task_schedule_status_valid check (
        (status = 'ACTIVE' and pause_reason is null)
        or (
          status = 'PAUSED'
          and btrim(pause_reason) <> ''
          and char_length(pause_reason) <= 500
        )
      ),
      constraint task_schedule_sync_valid check (
        sync_status in ('PENDING', 'SYNCED', 'RETRY_WAIT', 'FAILED')
        and revision > 0
        and (
          (
            sync_status = 'SYNCED'
            and synced_revision = revision
            and last_sync_error_code is null
          )
          or (
            sync_status = 'PENDING'
            and (synced_revision is null or synced_revision < revision)
            and last_sync_error_code is null
          )
          or (
            sync_status in ('RETRY_WAIT', 'FAILED')
            and (synced_revision is null or synced_revision < revision)
            and last_sync_error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
          )
        )
      ),
      constraint task_schedule_next_fires_valid check (
        cardinality(next_fire_times_utc) between 0 and 5
        and array_position(next_fire_times_utc, null) is null
      ),
      constraint task_schedule_time_order check (
        isfinite(created_at) and isfinite(updated_at) and updated_at >= created_at
      )
    )
    """,
    """
    create table atlas.task_schedule_sync_intent (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_schedule_id uuid not null,
      schedule_revision bigint not null,
      action text not null,
      content_digest text not null,
      temporal_namespace text not null,
      temporal_schedule_id text not null,
      status text not null default 'PENDING',
      available_at timestamptz not null,
      claim_token uuid,
      claimed_by text,
      claimed_at timestamptz,
      claim_expires_at timestamptz,
      dispatch_attempts integer not null default 0,
      dispatch_revision bigint not null default 0,
      last_error_code text,
      last_error_at timestamptz,
      applied_at timestamptz,
      failed_at timestamptz,
      superseded_at timestamptz,
      created_at timestamptz not null,
      constraint task_schedule_sync_scope_fk foreign key (
        task_schedule_id, tenant_id, project_id
      ) references atlas.task_schedule (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_schedule_sync_revision_unique unique (
        tenant_id, task_schedule_id, schedule_revision
      ),
      constraint task_schedule_sync_action_valid check (
        action in ('CREATE', 'PAUSE', 'RESUME', 'AUTO_PAUSE')
      ),
      constraint task_schedule_sync_identity_valid check (
        schedule_revision > 0
        and content_digest ~ '^sha256:[0-9a-f]{64}$'
        and temporal_namespace ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
        and temporal_schedule_id =
          'atlas-task/schedule/' || replace(tenant_id::text, '-', '') || '/'
          || replace(task_schedule_id::text, '-', '')
      ),
      constraint task_schedule_sync_delivery_valid check (
        status in (
          'PENDING', 'CLAIMED', 'RETRY_WAIT', 'APPLIED', 'FAILED',
          'SUPERSEDED'
        )
        and dispatch_attempts >= 0
        and dispatch_revision >= 0
        and isfinite(created_at)
        and isfinite(available_at)
        and (
          (last_error_code is null and last_error_at is null)
          or (
            last_error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
            and last_error_at is not null
          )
        )
        and (
          (
            status = 'PENDING'
            and claim_token is null and claimed_by is null
            and claimed_at is null and claim_expires_at is null
            and dispatch_attempts = 0 and dispatch_revision = 0
            and last_error_code is null and applied_at is null
            and failed_at is null and superseded_at is null
          )
          or (
            status = 'CLAIMED'
            and claim_token is not null
            and claimed_by ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
            and claimed_at is not null and claim_expires_at > claimed_at
            and dispatch_attempts > 0 and dispatch_revision > 0
            and applied_at is null and failed_at is null and superseded_at is null
          )
          or (
            status = 'RETRY_WAIT'
            and claim_token is null and claimed_by is null
            and claimed_at is null and claim_expires_at is null
            and dispatch_attempts > 0 and dispatch_revision > 0
            and last_error_code is not null and available_at > last_error_at
            and applied_at is null and failed_at is null and superseded_at is null
          )
          or (
            status = 'APPLIED'
            and claim_token is null and claimed_by is null
            and claimed_at is null and claim_expires_at is null
            and dispatch_attempts > 0 and dispatch_revision > 0
            and applied_at is not null and failed_at is null and superseded_at is null
          )
          or (
            status = 'FAILED'
            and claim_token is null and claimed_by is null
            and claimed_at is null and claim_expires_at is null
            and dispatch_attempts > 0 and dispatch_revision > 0
            and last_error_code is not null and failed_at = last_error_at
            and applied_at is null and superseded_at is null
          )
          or (
            status = 'SUPERSEDED'
            and claim_token is null and claimed_by is null
            and claimed_at is null and claim_expires_at is null
            and applied_at is null and failed_at is null
            and superseded_at is not null
          )
        )
      )
    )
    """,
    """
    create or replace function atlas.guard_task_schedule_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      version_row atlas.task_plan_version%rowtype;
      plan_status text;
      project_status text;
      expected_digest text;
    begin
      if atlas.current_tenant_id() is null
        or atlas.current_actor_id() is null
        or new.tenant_id is distinct from atlas.current_tenant_id()
      then
        raise exception 'Task Schedule creation requires exact tenant and actor context'
          using errcode = '42501';
      end if;
      select * into version_row
      from atlas.task_plan_version version
      where version.id = new.task_plan_version_id
        and version.tenant_id = new.tenant_id
        and version.project_id = new.project_id
      for share;
      select plan.status into plan_status
      from atlas.task_plan plan
      where plan.id = version_row.task_plan_id
        and plan.tenant_id = new.tenant_id
        and plan.project_id = new.project_id
      for share;
      select project.status into project_status
      from atlas.project project
      where project.id = new.project_id and project.tenant_id = new.tenant_id
      for share;
      if version_row.id is null or plan_status <> 'ACTIVE' or project_status <> 'ACTIVE' then
        raise exception 'Task Schedule requires an active exact TaskPlanVersion scope'
          using errcode = '55000';
      end if;
      if exists (
        select 1
        from jsonb_array_elements_text(
          version_row.matrix -> 'environmentIds'
        ) environment_ref(value)
        join atlas.environment environment
          on environment.id = environment_ref.value::uuid
         and environment.tenant_id = new.tenant_id
         and environment.project_id = new.project_id
        where environment.kind = 'PRODUCTION'
      ) then
        raise exception 'Task Schedule cannot target a production environment'
          using errcode = '55000';
      end if;
      if version_row.policy_digests ->> 'infra-retry'
        is distinct from new.retry_policy ->> 'contentDigest'
      then
        raise exception 'Task Schedule retry policy does not match Plan Version'
          using errcode = '55000';
      end if;
      expected_digest := atlas.task_sha256_json(jsonb_build_object(
        'schemaVersion', new.schema_version,
        'scheduleId', new.id::text,
        'tenantId', new.tenant_id::text,
        'projectId', new.project_id::text,
        'taskPlanVersionId', new.task_plan_version_id::text,
        'scheduleKey', new.schedule_key,
        'name', new.name,
        'calendar', new.calendar,
        'timeZoneName', new.time_zone_name,
        'overlapPolicy', new.overlap_policy,
        'catchupPolicy', new.catchup_policy,
        'catchupWindowSeconds', new.catchup_window_seconds,
        'jitterSeconds', new.jitter_seconds,
        'iterationId', new.iteration_id,
        'retryPolicy', new.retry_policy,
        'temporalNamespace', new.temporal_namespace,
        'temporalScheduleId', new.temporal_schedule_id
      ));
      if new.content_digest is distinct from expected_digest
        or new.created_by is distinct from atlas.current_actor_id()
        or new.updated_by is distinct from atlas.current_actor_id()
        or new.status <> 'ACTIVE'
        or new.pause_reason is not null
        or new.sync_status <> 'PENDING'
        or new.synced_revision is not null
        or new.last_sync_error_code is not null
        or new.revision <> 1
      then
        raise exception 'Task Schedule initial fact is not canonical'
          using errcode = '23514';
      end if;
      return new;
    end;
    $$
    """,
    """
    create or replace function atlas.guard_task_schedule_update()
    returns trigger
    language plpgsql
    security invoker
    set search_path = pg_catalog, atlas
    as $$
    begin
      if current_user = pg_catalog.pg_get_userbyid(
        (select relowner from pg_catalog.pg_class where oid = 'atlas.task_schedule'::regclass)
      ) then
        return new;
      end if;
      if atlas.current_tenant_id() is null
        or atlas.current_actor_id() is null
        or old.tenant_id is distinct from atlas.current_tenant_id()
      then
        raise exception 'Task Schedule mutation requires exact tenant and actor context'
          using errcode = '42501';
      end if;
      if row(
        new.id, new.tenant_id, new.project_id, new.task_plan_version_id,
        new.schema_version, new.schedule_key, new.name, new.calendar,
        new.time_zone_name, new.overlap_policy, new.catchup_policy,
        new.catchup_window_seconds, new.jitter_seconds, new.iteration_id,
        new.retry_policy, new.temporal_namespace, new.temporal_schedule_id,
        new.content_digest, new.created_by, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_plan_version_id,
        old.schema_version, old.schedule_key, old.name, old.calendar,
        old.time_zone_name, old.overlap_policy, old.catchup_policy,
        old.catchup_window_seconds, old.jitter_seconds, old.iteration_id,
        old.retry_policy, old.temporal_namespace, old.temporal_schedule_id,
        old.content_digest, old.created_by, old.created_at
      )
        or new.status = old.status
        or (old.status, new.status) not in (('ACTIVE', 'PAUSED'), ('PAUSED', 'ACTIVE'))
        or new.revision <> old.revision + 1
        or new.sync_status <> 'PENDING'
        or new.synced_revision is distinct from old.synced_revision
        or new.last_sync_error_code is not null
        or new.next_fire_times_utc is distinct from old.next_fire_times_utc
        or new.updated_by is distinct from atlas.current_actor_id()
        or new.updated_at < old.updated_at
        or (
          new.status = 'ACTIVE'
          and exists (
            select 1
            from atlas.task_plan_version version
            cross join lateral jsonb_array_elements_text(
              version.matrix -> 'environmentIds'
            ) environment_ref(value)
            join atlas.environment environment
              on environment.id = environment_ref.value::uuid
             and environment.tenant_id = new.tenant_id
             and environment.project_id = new.project_id
            where version.id = new.task_plan_version_id
              and version.tenant_id = new.tenant_id
              and version.project_id = new.project_id
              and environment.kind = 'PRODUCTION'
          )
        )
      then
        raise exception 'Task Schedule mutation is outside the pause/resume state machine'
          using errcode = '23514';
      end if;
      return new;
    end;
    $$
    """,
    """
    create or replace function atlas.guard_task_schedule_sync_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored atlas.task_schedule%rowtype;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id is distinct from atlas.current_tenant_id()
      then
        raise exception 'Task Schedule sync intent requires tenant context'
          using errcode = '42501';
      end if;
      select * into stored
      from atlas.task_schedule schedule
      where schedule.id = new.task_schedule_id
        and schedule.tenant_id = new.tenant_id
        and schedule.project_id = new.project_id
      for share;
      if stored.id is null
        or new.schedule_revision is distinct from stored.revision
        or new.content_digest is distinct from stored.content_digest
        or new.temporal_namespace is distinct from stored.temporal_namespace
        or new.temporal_schedule_id is distinct from stored.temporal_schedule_id
        or (new.action in ('CREATE', 'RESUME') and stored.status <> 'ACTIVE')
        or (new.action in ('PAUSE', 'AUTO_PAUSE') and stored.status <> 'PAUSED')
      then
        raise exception 'Task Schedule sync intent is not bound to desired state'
          using errcode = '23514';
      end if;
      new.status := 'PENDING';
      new.available_at := new.created_at;
      new.claim_token := null;
      new.claimed_by := null;
      new.claimed_at := null;
      new.claim_expires_at := null;
      new.dispatch_attempts := 0;
      new.dispatch_revision := 0;
      new.last_error_code := null;
      new.last_error_at := null;
      new.applied_at := null;
      new.failed_at := null;
      new.superseded_at := null;
      return new;
    end;
    $$
    """,
    """
    create trigger task_schedule_guard_insert
      before insert on atlas.task_schedule
      for each row execute function atlas.guard_task_schedule_insert()
    """,
    """
    create trigger task_schedule_guard_update
      before update on atlas.task_schedule
      for each row execute function atlas.guard_task_schedule_update()
    """,
    """
    create trigger task_schedule_prevent_delete
      before delete on atlas.task_schedule
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger task_schedule_sync_guard_insert
      before insert on atlas.task_schedule_sync_intent
      for each row execute function atlas.guard_task_schedule_sync_insert()
    """,
    """
    create trigger task_schedule_sync_prevent_delete
      before delete on atlas.task_schedule_sync_intent
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create index task_schedule_version_list_idx
      on atlas.task_schedule (
        tenant_id, task_plan_version_id, updated_at desc, id desc
      )
    """,
    """
    create index task_schedule_sync_ready_idx
      on atlas.task_schedule_sync_intent (
        temporal_namespace, available_at, created_at, id
      ) where status in ('PENDING', 'RETRY_WAIT')
    """,
    """
    create index task_schedule_sync_expired_idx
      on atlas.task_schedule_sync_intent (
        temporal_namespace, claim_expires_at, created_at, id
      ) where status = 'CLAIMED'
    """,
    "alter table atlas.task_schedule enable row level security",
    "alter table atlas.task_schedule force row level security",
    "alter table atlas.task_schedule_sync_intent enable row level security",
    "alter table atlas.task_schedule_sync_intent force row level security",
    """
    create policy task_schedule_tenant_isolation
      on atlas.task_schedule
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy task_schedule_sync_tenant_isolation
      on atlas.task_schedule_sync_intent
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create function atlas.claim_task_schedule_sync_intents(
      p_claimed_by text,
      p_namespace text,
      p_limit integer,
      p_lease_seconds integer
    ) returns table (
      id uuid,
      tenant_id uuid,
      project_id uuid,
      task_schedule_id uuid,
      schedule_revision bigint,
      action text,
      content_digest text,
      temporal_namespace text,
      temporal_schedule_id text,
      claim_token uuid,
      dispatch_revision bigint,
      dispatch_attempts integer,
      claim_expires_at timestamptz,
      desired_status text,
      task_plan_version_id uuid,
      calendar jsonb,
      time_zone_name text,
      overlap_policy text,
      catchup_policy text,
      catchup_window_seconds integer,
      jitter_seconds integer,
      iteration_id text,
      retry_policy jsonb,
      created_by uuid
    )
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      observed_at timestamptz := transaction_timestamp();
    begin
      if p_claimed_by !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        or p_namespace !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
        or p_limit not between 1 and 100
        or p_lease_seconds not between 5 and 300
      then
        raise exception 'Task Schedule sync claim parameters are invalid'
          using errcode = '22023';
      end if;

      update atlas.task_schedule_sync_intent intent
      set
        status = 'SUPERSEDED',
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        superseded_at = observed_at,
        dispatch_revision = intent.dispatch_revision + 1
      from atlas.task_schedule schedule
      where schedule.id = intent.task_schedule_id
        and schedule.revision > intent.schedule_revision
        and intent.status in ('PENDING', 'CLAIMED', 'RETRY_WAIT');

      return query
      with candidates as (
        select intent.id
        from atlas.task_schedule_sync_intent intent
        where intent.temporal_namespace = p_namespace
          and (
            (
              intent.status in ('PENDING', 'RETRY_WAIT')
              and intent.available_at <= observed_at
            )
            or (
              intent.status = 'CLAIMED'
              and intent.claim_expires_at <= observed_at
            )
          )
        order by intent.available_at, intent.created_at, intent.id
        limit p_limit
        for update skip locked
      ),
      claimed as (
        update atlas.task_schedule_sync_intent intent
        set
          status = 'CLAIMED',
          available_at = greatest(intent.available_at, observed_at),
          claim_token = gen_random_uuid(),
          claimed_by = p_claimed_by,
          claimed_at = observed_at,
          claim_expires_at = observed_at
            + make_interval(secs => p_lease_seconds),
          dispatch_attempts = intent.dispatch_attempts + 1,
          dispatch_revision = intent.dispatch_revision + 1,
          last_error_code = null,
          last_error_at = null
        from candidates
        where intent.id = candidates.id
        returning intent.*
      )
      select
        claimed.id,
        claimed.tenant_id,
        claimed.project_id,
        claimed.task_schedule_id,
        claimed.schedule_revision,
        claimed.action,
        claimed.content_digest,
        claimed.temporal_namespace,
        claimed.temporal_schedule_id,
        claimed.claim_token,
        claimed.dispatch_revision,
        claimed.dispatch_attempts,
        claimed.claim_expires_at,
        schedule.status,
        schedule.task_plan_version_id,
        schedule.calendar,
        schedule.time_zone_name,
        schedule.overlap_policy,
        schedule.catchup_policy,
        schedule.catchup_window_seconds,
        schedule.jitter_seconds,
        schedule.iteration_id,
        schedule.retry_policy,
        schedule.created_by
      from claimed
      join atlas.task_schedule schedule
        on schedule.id = claimed.task_schedule_id
       and schedule.tenant_id = claimed.tenant_id
       and schedule.project_id = claimed.project_id
       and schedule.revision = claimed.schedule_revision
      order by claimed.available_at, claimed.created_at, claimed.id;
    end;
    $$
    """,
    """
    create function atlas.mark_task_schedule_sync_applied(
      p_intent_id uuid,
      p_claim_token uuid,
      p_dispatch_revision bigint,
      p_next_fire_times timestamptz[]
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      intent atlas.task_schedule_sync_intent%rowtype;
      observed_at timestamptz := transaction_timestamp();
    begin
      if cardinality(p_next_fire_times) not between 0 and 5
        or array_position(p_next_fire_times, null) is not null
      then
        raise exception 'Task Schedule next-fire projection is invalid'
          using errcode = '22023';
      end if;
      select * into intent
      from atlas.task_schedule_sync_intent value
      where value.id = p_intent_id
      for update;
      if intent.id is null
        or intent.status <> 'CLAIMED'
        or intent.claim_token is distinct from p_claim_token
        or intent.dispatch_revision <> p_dispatch_revision
        or intent.claim_expires_at <= observed_at
      then
        return false;
      end if;
      if not exists (
        select 1 from atlas.task_schedule schedule
        where schedule.id = intent.task_schedule_id
          and schedule.revision = intent.schedule_revision
      ) then
        update atlas.task_schedule_sync_intent
        set status = 'SUPERSEDED', claim_token = null, claimed_by = null,
            claimed_at = null, claim_expires_at = null,
            superseded_at = observed_at,
            dispatch_revision = dispatch_revision + 1
        where id = intent.id;
        return false;
      end if;
      update atlas.task_schedule_sync_intent
      set status = 'APPLIED', claim_token = null, claimed_by = null,
          claimed_at = null, claim_expires_at = null, applied_at = observed_at,
          dispatch_revision = dispatch_revision + 1
      where id = intent.id;
      update atlas.task_schedule
      set sync_status = 'SYNCED',
          synced_revision = revision,
          last_sync_error_code = null,
          next_fire_times_utc = p_next_fire_times,
          updated_at = greatest(updated_at, observed_at)
      where id = intent.task_schedule_id
        and revision = intent.schedule_revision;
      return found;
    end;
    $$
    """,
    """
    create function atlas.retry_task_schedule_sync_intent(
      p_intent_id uuid,
      p_claim_token uuid,
      p_dispatch_revision bigint,
      p_error_code text,
      p_retry_delay_ms integer
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      updated_schedule_id uuid;
      updated_schedule_revision bigint;
      observed_at timestamptz := transaction_timestamp();
    begin
      if p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$'
        or p_retry_delay_ms not between 100 and 3600000
      then
        raise exception 'Task Schedule retry parameters are invalid'
          using errcode = '22023';
      end if;
      update atlas.task_schedule_sync_intent
      set status = 'RETRY_WAIT',
          available_at = observed_at
            + make_interval(secs => p_retry_delay_ms::double precision / 1000),
          claim_token = null, claimed_by = null, claimed_at = null,
          claim_expires_at = null, last_error_code = p_error_code,
          last_error_at = observed_at,
          dispatch_revision = dispatch_revision + 1
      where id = p_intent_id and status = 'CLAIMED'
        and claim_token = p_claim_token
        and dispatch_revision = p_dispatch_revision
        and claim_expires_at > observed_at
      returning task_schedule_id, schedule_revision
      into updated_schedule_id, updated_schedule_revision;
      if updated_schedule_id is null then
        return false;
      end if;
      update atlas.task_schedule
      set sync_status = 'RETRY_WAIT',
          last_sync_error_code = p_error_code,
          updated_at = greatest(updated_at, observed_at)
      where id = updated_schedule_id and revision = updated_schedule_revision;
      return true;
    end;
    $$
    """,
    """
    create function atlas.fail_task_schedule_sync_intent(
      p_intent_id uuid,
      p_claim_token uuid,
      p_dispatch_revision bigint,
      p_error_code text
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      updated_schedule_id uuid;
      updated_schedule_revision bigint;
      observed_at timestamptz := transaction_timestamp();
    begin
      if p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$' then
        raise exception 'Task Schedule failure code is invalid'
          using errcode = '22023';
      end if;
      update atlas.task_schedule_sync_intent
      set status = 'FAILED', claim_token = null, claimed_by = null,
          claimed_at = null, claim_expires_at = null,
          last_error_code = p_error_code, last_error_at = observed_at,
          failed_at = observed_at, dispatch_revision = dispatch_revision + 1
      where id = p_intent_id and status = 'CLAIMED'
        and claim_token = p_claim_token
        and dispatch_revision = p_dispatch_revision
        and claim_expires_at > observed_at
      returning task_schedule_id, schedule_revision
      into updated_schedule_id, updated_schedule_revision;
      if updated_schedule_id is null then
        return false;
      end if;
      update atlas.task_schedule
      set sync_status = 'FAILED',
          last_sync_error_code = p_error_code,
          updated_at = greatest(updated_at, observed_at)
      where id = updated_schedule_id and revision = updated_schedule_revision;
      return true;
    end;
    $$
    """,
    """
    create function atlas.auto_pause_task_schedules_for_production_environment()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      schedule_row atlas.task_schedule%rowtype;
      observed_at timestamptz := transaction_timestamp();
      request_id text := coalesce(
        current_setting('app.request_id', true),
        'environment-production-reclassification'
      );
    begin
      if new.kind <> 'PRODUCTION' or old.kind = 'PRODUCTION' then
        return new;
      end if;
      for schedule_row in
        update atlas.task_schedule schedule
        set status = 'PAUSED',
            pause_reason = 'ENVIRONMENT_RECLASSIFIED_AS_PRODUCTION',
            sync_status = 'PENDING',
            last_sync_error_code = null,
            updated_by = atlas.current_actor_id(),
            revision = schedule.revision + 1,
            updated_at = greatest(schedule.updated_at, observed_at)
        from atlas.task_plan_version version
        where version.id = schedule.task_plan_version_id
          and schedule.tenant_id = new.tenant_id
          and schedule.project_id = new.project_id
          and schedule.status = 'ACTIVE'
          and version.matrix -> 'environmentIds' ? new.id::text
        returning schedule.*
      loop
        insert into atlas.task_schedule_sync_intent (
          id, tenant_id, project_id, task_schedule_id, schedule_revision,
          action, content_digest, temporal_namespace, temporal_schedule_id,
          available_at, created_at
        ) values (
          gen_random_uuid(), schedule_row.tenant_id, schedule_row.project_id,
          schedule_row.id, schedule_row.revision, 'AUTO_PAUSE',
          schedule_row.content_digest, schedule_row.temporal_namespace,
          schedule_row.temporal_schedule_id, observed_at, observed_at
        );
        insert into atlas.audit_event (
          id, tenant_id, project_id, environment_id, actor_id, event_type,
          entity_type, entity_id, occurred_at, payload, request_id
        ) values (
          gen_random_uuid(), schedule_row.tenant_id, schedule_row.project_id,
          new.id, atlas.current_actor_id(), 'task_schedule.auto_paused',
          'task_schedule', schedule_row.id, observed_at,
          jsonb_build_object(
            'taskScheduleId', schedule_row.id::text,
            'environmentId', new.id::text,
            'reason', schedule_row.pause_reason,
            'revision', schedule_row.revision
          ),
          request_id
        );
        insert into atlas.outbox_event (
          id, tenant_id, aggregate_type, aggregate_id, event_type, payload,
          occurred_at, available_at
        ) values (
          gen_random_uuid(), schedule_row.tenant_id, 'task_schedule',
          schedule_row.id, 'task_schedule.auto_paused',
          jsonb_build_object(
            'taskScheduleId', schedule_row.id::text,
            'environmentId', new.id::text,
            'reason', schedule_row.pause_reason,
            'revision', schedule_row.revision
          ),
          observed_at, observed_at
        );
      end loop;
      return new;
    end;
    $$
    """,
    """
    create trigger environment_auto_pause_task_schedules
      after update of kind on atlas.environment
      for each row
      when (old.kind is distinct from new.kind)
      execute function atlas.auto_pause_task_schedules_for_production_environment()
    """,
    "revoke all on atlas.task_schedule from atlas_app, atlas_dispatcher",
    "revoke all on atlas.task_schedule_sync_intent from atlas_app, atlas_dispatcher",
    "grant select, insert, update on atlas.task_schedule to atlas_app",
    "grant select, insert on atlas.task_schedule_sync_intent to atlas_app",
    "revoke all on function atlas.guard_task_schedule_insert() from public, atlas_app, atlas_dispatcher",
    "revoke all on function atlas.guard_task_schedule_update() from public, atlas_app, atlas_dispatcher",
    "revoke all on function atlas.guard_task_schedule_sync_insert() from public, atlas_app, atlas_dispatcher",
    "revoke all on function atlas.claim_task_schedule_sync_intents(text, text, integer, integer) from public, atlas_app",
    "revoke all on function atlas.mark_task_schedule_sync_applied(uuid, uuid, bigint, timestamptz[]) from public, atlas_app",
    "revoke all on function atlas.retry_task_schedule_sync_intent(uuid, uuid, bigint, text, integer) from public, atlas_app",
    "revoke all on function atlas.fail_task_schedule_sync_intent(uuid, uuid, bigint, text) from public, atlas_app",
    "revoke all on function atlas.auto_pause_task_schedules_for_production_environment() from public, atlas_app, atlas_dispatcher",
    "grant execute on function atlas.claim_task_schedule_sync_intents(text, text, integer, integer) to atlas_dispatcher",
    "grant execute on function atlas.mark_task_schedule_sync_applied(uuid, uuid, bigint, timestamptz[]) to atlas_dispatcher",
    "grant execute on function atlas.retry_task_schedule_sync_intent(uuid, uuid, bigint, text, integer) to atlas_dispatcher",
    "grant execute on function atlas.fail_task_schedule_sync_intent(uuid, uuid, bigint, text) to atlas_dispatcher",
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.task_schedule)
        or exists (select 1 from atlas.task_schedule_sync_intent)
      then
        raise exception 'cannot downgrade while Task Schedule facts exist'
          using errcode = '55000';
      end if;
    end;
    $$
    """,
    "drop trigger if exists environment_auto_pause_task_schedules on atlas.environment",
    "drop function if exists atlas.auto_pause_task_schedules_for_production_environment()",
    "drop function if exists atlas.fail_task_schedule_sync_intent(uuid, uuid, bigint, text)",
    "drop function if exists atlas.retry_task_schedule_sync_intent(uuid, uuid, bigint, text, integer)",
    "drop function if exists atlas.mark_task_schedule_sync_applied(uuid, uuid, bigint, timestamptz[])",
    "drop function if exists atlas.claim_task_schedule_sync_intents(text, text, integer, integer)",
    "drop table if exists atlas.task_schedule_sync_intent",
    "drop table if exists atlas.task_schedule",
    "drop function if exists atlas.guard_task_schedule_sync_insert()",
    "drop function if exists atlas.guard_task_schedule_update()",
    "drop function if exists atlas.guard_task_schedule_insert()",
    "drop function if exists atlas.task_schedule_retry_policy_valid(jsonb)",
    "drop function if exists atlas.task_schedule_calendar_valid(jsonb)",
    "drop function if exists atlas.task_schedule_integer_array_valid(jsonb, integer, integer, integer, boolean)",
)


def upgrade() -> None:
    """Create Schedule catalog, desired-state facts, and narrow dispatcher API."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove Schedule support only when no durable fact would be lost."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
