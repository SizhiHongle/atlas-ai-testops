# ruff: noqa: E501
"""Add durable, fenced Task Workflow start-intent delivery.

Revision ID: 20260716_0024
Revises: 20260716_0023
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_0024"
down_revision: str | None = "20260716_0023"
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
        raise exception 'Task Workflow dispatcher function owner must bypass row-level security'
          using errcode = '42501';
      end if;
      if not exists (
        select 1
        from pg_catalog.pg_roles role
        where role.rolname = 'atlas_dispatcher'
      ) then
        raise exception 'atlas_dispatcher must be provisioned before Task Workflow delivery migration'
          using errcode = '42704';
      end if;
      if exists (
        select 1
        from pg_catalog.pg_roles role
        where role.rolname = 'atlas_dispatcher'
          and (
            not role.rolcanlogin
            or role.rolsuper
            or role.rolbypassrls
          )
      ) then
        raise exception 'atlas_dispatcher must be LOGIN, NOSUPERUSER, and NOBYPASSRLS'
          using errcode = '42501';
      end if;
    end;
    $$
    """,
    "drop trigger task_workflow_start_intent_prevent_mutation on atlas.task_workflow_start_intent",
    "drop index atlas.task_workflow_start_intent_pending_idx",
    """
    alter table atlas.task_workflow_start_intent
      drop constraint task_workflow_start_intent_shape,
      add column manifest_hash text,
      add column available_at timestamptz,
      add column claim_token uuid,
      add column claimed_by text,
      add column claimed_at timestamptz,
      add column claim_expires_at timestamptz,
      add column dispatch_attempts integer not null default 0,
      add column last_error_code text,
      add column last_error_at timestamptz,
      add column workflow_started_at timestamptz,
      add column dispatch_failed_at timestamptz,
      add column dispatch_revision bigint not null default 0
    """,
    """
    update atlas.task_workflow_start_intent intent
    set
      manifest_hash = run.manifest_hash,
      available_at = intent.created_at
    from atlas.task_run run
    where run.id = intent.task_run_id
      and run.tenant_id = intent.tenant_id
      and run.project_id = intent.project_id
    """,
    """
    do $$
    begin
      if exists (
        select 1
        from atlas.task_workflow_start_intent intent
        where intent.manifest_hash is null
          or intent.available_at is null
      ) then
        raise exception 'Task Workflow start-intent delivery backfill is incomplete'
          using errcode = '55000';
      end if;
    end;
    $$
    """,
    """
    alter table atlas.task_workflow_start_intent
      alter column manifest_hash set not null,
      alter column available_at set not null,
      add constraint task_workflow_start_intent_manifest_scope_fk foreign key (
        task_run_id, tenant_id, project_id, manifest_hash
      ) references atlas.task_run_manifest (
        task_run_id, tenant_id, project_id, manifest_hash
      ) on delete restrict,
      add constraint task_workflow_start_intent_shape check (
        namespace ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
        and workflow_id ~ '^atlas-task/[A-Za-z0-9/_-]+$'
        and char_length(workflow_id) between 12 and 320
        and request_digest ~ '^sha256:[0-9a-f]{64}$'
        and manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and owner_kind in ('TASK_RUN', 'UNIT_ATTEMPT')
        and (
          (
            owner_kind = 'TASK_RUN'
            and workflow_type = 'AtlasTaskRunWorkflow'
            and task_queue = 'atlas-task-run'
          )
          or (
            owner_kind = 'UNIT_ATTEMPT'
            and workflow_type = 'AtlasUnitAttemptWorkflow'
            and task_queue = 'atlas-unit-attempt'
          )
        )
        and status in ('PENDING', 'CLAIMED', 'RETRY_WAIT', 'STARTED', 'FAILED')
        and dispatch_attempts >= 0
        and dispatch_revision >= 0
        and isfinite(created_at)
        and isfinite(available_at)
        and (
          (last_error_code is null and last_error_at is null)
          or (
            last_error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
            and last_error_at is not null
            and isfinite(last_error_at)
          )
        )
        and (
          (
            status = 'PENDING'
            and available_at = created_at
            and claim_token is null
            and claimed_by is null
            and claimed_at is null
            and claim_expires_at is null
            and dispatch_attempts = 0
            and last_error_code is null
            and last_error_at is null
            and workflow_started_at is null
            and dispatch_failed_at is null
            and dispatch_revision = 0
          )
          or (
            status = 'CLAIMED'
            and available_at >= created_at
            and claim_token is not null
            and claimed_by ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
            and claimed_at is not null
            and isfinite(claimed_at)
            and claimed_at >= created_at
            and claim_expires_at is not null
            and isfinite(claim_expires_at)
            and claim_expires_at > claimed_at
            and dispatch_attempts > 0
            and (last_error_at is null or last_error_at <= claimed_at)
            and workflow_started_at is null
            and dispatch_failed_at is null
            and dispatch_revision > 0
          )
          or (
            status = 'RETRY_WAIT'
            and available_at > last_error_at
            and claim_token is null
            and claimed_by is null
            and claimed_at is null
            and claim_expires_at is null
            and dispatch_attempts > 0
            and last_error_code is not null
            and last_error_at is not null
            and workflow_started_at is null
            and dispatch_failed_at is null
            and dispatch_revision > 1
          )
          or (
            status = 'STARTED'
            and available_at >= created_at
            and claim_token is null
            and claimed_by is null
            and claimed_at is null
            and claim_expires_at is null
            and dispatch_attempts > 0
            and workflow_started_at is not null
            and isfinite(workflow_started_at)
            and workflow_started_at >= created_at
            and (last_error_at is null or last_error_at <= workflow_started_at)
            and dispatch_failed_at is null
            and dispatch_revision > 1
          )
          or (
            status = 'FAILED'
            and available_at >= created_at
            and claim_token is null
            and claimed_by is null
            and claimed_at is null
            and claim_expires_at is null
            and dispatch_attempts > 0
            and last_error_code is not null
            and last_error_at is not null
            and workflow_started_at is null
            and dispatch_failed_at = last_error_at
            and isfinite(dispatch_failed_at)
            and dispatch_revision > 1
          )
        )
      )
    """,
    """
    create index task_workflow_start_intent_dispatch_ready_idx
      on atlas.task_workflow_start_intent (
        available_at, created_at, id
      ) where status in ('PENDING', 'RETRY_WAIT')
    """,
    """
    create index task_workflow_start_intent_expired_claim_idx
      on atlas.task_workflow_start_intent (
        claim_expires_at, created_at, id
      ) where status = 'CLAIMED'
    """,
    """
    create function atlas.guard_task_workflow_start_intent_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_manifest_hash text;
    begin
      select run.manifest_hash
      into stored_manifest_hash
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id;

      if stored_manifest_hash is null then
        raise exception 'Task Workflow start intent requires an exact TaskRun scope'
          using errcode = '23503';
      end if;
      if new.manifest_hash is not null
        and new.manifest_hash is distinct from stored_manifest_hash
      then
        raise exception 'Task Workflow start intent manifest is not authoritative'
          using errcode = '23514';
      end if;

      new.manifest_hash := stored_manifest_hash;
      new.available_at := new.created_at;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_workflow_start_intent_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if tg_op = 'DELETE' then
        raise exception 'Task Workflow start intents cannot be deleted'
          using errcode = '55000';
      end if;

      if row(
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.owner_kind, new.owner_id, new.namespace, new.workflow_id,
        new.request_digest, new.manifest_hash, new.workflow_type,
        new.task_queue, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.owner_kind, old.owner_id, old.namespace, old.workflow_id,
        old.request_digest, old.manifest_hash, old.workflow_type,
        old.task_queue, old.created_at
      ) then
        raise exception 'Task Workflow start-intent identity is immutable'
          using errcode = '55000';
      end if;
      if new.dispatch_revision is distinct from old.dispatch_revision + 1 then
        raise exception 'Task Workflow start-intent revision must advance exactly once'
          using errcode = '40001';
      end if;

      if new.status = 'CLAIMED'
        and old.status in ('PENDING', 'RETRY_WAIT', 'CLAIMED')
      then
        if old.status = 'CLAIMED'
          and clock_timestamp() < old.claim_expires_at
        then
          raise exception 'Task Workflow start-intent claim is still active'
            using errcode = '40001';
        end if;
        if new.available_at is distinct from old.available_at
          or new.dispatch_attempts is distinct from old.dispatch_attempts + 1
          or new.last_error_code is distinct from old.last_error_code
          or new.last_error_at is distinct from old.last_error_at
          or new.workflow_started_at is distinct from old.workflow_started_at
          or new.dispatch_failed_at is distinct from old.dispatch_failed_at
        then
          raise exception 'Task Workflow start-intent claim mutated non-claim state'
            using errcode = '55000';
        end if;
      elsif old.status = 'CLAIMED' and new.status = 'RETRY_WAIT' then
        if new.available_at <= old.claimed_at
          or new.dispatch_attempts is distinct from old.dispatch_attempts
          or new.workflow_started_at is not null
          or new.dispatch_failed_at is not null
          or new.last_error_code is null
          or new.last_error_at is null
        then
          raise exception 'Task Workflow start-intent retry transition is invalid'
            using errcode = '55000';
        end if;
      elsif old.status = 'CLAIMED' and new.status = 'STARTED' then
        if new.available_at is distinct from old.available_at
          or new.dispatch_attempts is distinct from old.dispatch_attempts
          or new.last_error_code is distinct from old.last_error_code
          or new.last_error_at is distinct from old.last_error_at
          or new.workflow_started_at is null
          or new.dispatch_failed_at is not null
        then
          raise exception 'Task Workflow start-intent started transition is invalid'
            using errcode = '55000';
        end if;
      elsif old.status = 'CLAIMED' and new.status = 'FAILED' then
        if new.available_at is distinct from old.available_at
          or new.dispatch_attempts is distinct from old.dispatch_attempts
          or new.last_error_code is null
          or new.last_error_at is null
          or new.workflow_started_at is not null
          or new.dispatch_failed_at is null
        then
          raise exception 'Task Workflow start-intent failed transition is invalid'
            using errcode = '55000';
        end if;
      else
        raise exception 'Task Workflow start-intent transition is invalid'
          using errcode = '55000';
      end if;

      return new;
    end;
    $$
    """,
    """
    create trigger task_workflow_start_intent_guard_insert
      before insert on atlas.task_workflow_start_intent
      for each row execute function atlas.guard_task_workflow_start_intent_insert()
    """,
    """
    create trigger task_workflow_start_intent_guard_update
      before update or delete on atlas.task_workflow_start_intent
      for each row execute function atlas.guard_task_workflow_start_intent_update()
    """,
    """
    create policy task_workflow_start_intent_dispatcher_access
      on atlas.task_workflow_start_intent for all
      using (session_user = 'atlas_dispatcher')
      with check (session_user = 'atlas_dispatcher')
    """,
    """
    create function atlas.claim_task_workflow_start_intents(
      p_claimed_by text,
      p_namespace text,
      p_limit integer,
      p_lease_seconds integer
    ) returns table (
      id uuid,
      tenant_id uuid,
      project_id uuid,
      task_run_id uuid,
      owner_kind text,
      owner_id uuid,
      namespace text,
      workflow_id text,
      request_digest text,
      manifest_hash text,
      workflow_type text,
      task_queue text,
      status text,
      claim_token uuid,
      dispatch_revision bigint,
      dispatch_attempts integer,
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
        raise exception 'Task Workflow start-intent claim requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_claimed_by is null
        or p_claimed_by !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
      then
        raise exception 'Task Workflow start-intent dispatcher identity is invalid'
          using errcode = '22023';
      end if;
      if p_namespace is null
        or p_namespace !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
      then
        raise exception 'Task Workflow start-intent namespace is invalid'
          using errcode = '22023';
      end if;
      if p_limit is null or p_limit not between 1 and 100 then
        raise exception 'Task Workflow start-intent claim limit is invalid'
          using errcode = '22023';
      end if;
      if p_lease_seconds is null or p_lease_seconds not between 1 and 900 then
        raise exception 'Task Workflow start-intent lease duration is invalid'
          using errcode = '22023';
      end if;

      return query
      with candidates as (
        select intent.id
        from atlas.task_workflow_start_intent intent
        where intent.owner_kind = 'TASK_RUN'
          and intent.workflow_type = 'AtlasTaskRunWorkflow'
          and intent.task_queue = 'atlas-task-run'
          and intent.namespace = p_namespace
          and (
            (
              intent.status in ('PENDING', 'RETRY_WAIT')
              and intent.available_at <= claimed_at_value
            ) or (
              intent.status = 'CLAIMED'
              and intent.claim_expires_at <= claimed_at_value
            )
          )
        order by
          case
            when intent.status = 'CLAIMED' then intent.claim_expires_at
            else intent.available_at
          end,
          intent.created_at,
          intent.id
        limit p_limit
        for update skip locked
      ), claimed as (
        update atlas.task_workflow_start_intent intent
        set
          status = 'CLAIMED',
          claim_token = gen_random_uuid(),
          claimed_by = p_claimed_by,
          claimed_at = claimed_at_value,
          claim_expires_at = claimed_at_value
            + make_interval(secs => p_lease_seconds),
          dispatch_attempts = intent.dispatch_attempts + 1,
          dispatch_revision = intent.dispatch_revision + 1
        from candidates
        where intent.id = candidates.id
        returning intent.*
      )
      select
        claimed.id,
        claimed.tenant_id,
        claimed.project_id,
        claimed.task_run_id,
        claimed.owner_kind,
        claimed.owner_id,
        claimed.namespace,
        claimed.workflow_id,
        claimed.request_digest,
        claimed.manifest_hash,
        claimed.workflow_type,
        claimed.task_queue,
        claimed.status,
        claimed.claim_token,
        claimed.dispatch_revision,
        claimed.dispatch_attempts,
        claimed.claim_expires_at,
        claimed.created_at
      from claimed
      order by claimed.created_at, claimed.id;
    end;
    $$
    """,
    """
    create function atlas.mark_task_workflow_start_intent_started(
      p_intent_id uuid,
      p_claim_token uuid,
      p_expected_dispatch_revision bigint
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      affected_count integer;
      acknowledged_at_value timestamptz := clock_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task Workflow start-intent acknowledgement requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_intent_id is null
        or p_claim_token is null
        or p_expected_dispatch_revision is null
        or p_expected_dispatch_revision < 1
      then
        raise exception 'Task Workflow start-intent acknowledgement is invalid'
          using errcode = '22023';
      end if;

      update atlas.task_workflow_start_intent intent
      set
        status = 'STARTED',
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        workflow_started_at = acknowledged_at_value,
        dispatch_revision = intent.dispatch_revision + 1
      where intent.id = p_intent_id
        and intent.status = 'CLAIMED'
        and intent.claim_token = p_claim_token
        and intent.dispatch_revision = p_expected_dispatch_revision
        and intent.claim_expires_at > acknowledged_at_value;
      get diagnostics affected_count = row_count;
      return affected_count = 1;
    end;
    $$
    """,
    """
    create function atlas.retry_task_workflow_start_intent(
      p_intent_id uuid,
      p_claim_token uuid,
      p_expected_dispatch_revision bigint,
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
        raise exception 'Task Workflow start-intent retry requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_intent_id is null
        or p_claim_token is null
        or p_expected_dispatch_revision is null
        or p_expected_dispatch_revision < 1
        or p_error_code is null
        or p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$'
        or p_retry_delay_ms is null
        or p_retry_delay_ms not between 100 and 3600000
      then
        raise exception 'Task Workflow start-intent retry request is invalid'
          using errcode = '22023';
      end if;

      update atlas.task_workflow_start_intent intent
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
        dispatch_revision = intent.dispatch_revision + 1
      where intent.id = p_intent_id
        and intent.status = 'CLAIMED'
        and intent.claim_token = p_claim_token
        and intent.dispatch_revision = p_expected_dispatch_revision
        and intent.claim_expires_at > failed_at_value;
      get diagnostics affected_count = row_count;
      return affected_count = 1;
    end;
    $$
    """,
    """
    create function atlas.fail_task_workflow_start_intent(
      p_intent_id uuid,
      p_claim_token uuid,
      p_expected_dispatch_revision bigint,
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
        raise exception 'Task Workflow start-intent failure requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_intent_id is null
        or p_claim_token is null
        or p_expected_dispatch_revision is null
        or p_expected_dispatch_revision < 1
        or p_error_code is null
        or p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$'
      then
        raise exception 'Task Workflow start-intent failure request is invalid'
          using errcode = '22023';
      end if;

      update atlas.task_workflow_start_intent intent
      set
        status = 'FAILED',
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        last_error_code = p_error_code,
        last_error_at = failed_at_value,
        dispatch_failed_at = failed_at_value,
        dispatch_revision = intent.dispatch_revision + 1
      where intent.id = p_intent_id
        and intent.status = 'CLAIMED'
        and intent.claim_token = p_claim_token
        and intent.dispatch_revision = p_expected_dispatch_revision
        and intent.claim_expires_at > failed_at_value;
      get diagnostics affected_count = row_count;
      return affected_count = 1;
    end;
    $$
    """,
    "grant usage on schema atlas to atlas_dispatcher",
    "revoke all on atlas.task_workflow_start_intent from atlas_dispatcher",
    "revoke insert, update, delete, truncate, references, trigger on atlas.task_workflow_start_intent from atlas_app",
    "revoke all on function atlas.guard_task_workflow_start_intent_insert() from public, atlas_app, atlas_dispatcher",
    "revoke all on function atlas.guard_task_workflow_start_intent_update() from public, atlas_app, atlas_dispatcher",
    "revoke all on function atlas.claim_task_workflow_start_intents(text, text, integer, integer) from public, atlas_app",
    "revoke all on function atlas.mark_task_workflow_start_intent_started(uuid, uuid, bigint) from public, atlas_app",
    "revoke all on function atlas.retry_task_workflow_start_intent(uuid, uuid, bigint, text, integer) from public, atlas_app",
    "revoke all on function atlas.fail_task_workflow_start_intent(uuid, uuid, bigint, text) from public, atlas_app",
    "grant execute on function atlas.claim_task_workflow_start_intents(text, text, integer, integer) to atlas_dispatcher",
    "grant execute on function atlas.mark_task_workflow_start_intent_started(uuid, uuid, bigint) to atlas_dispatcher",
    "grant execute on function atlas.retry_task_workflow_start_intent(uuid, uuid, bigint, text, integer) to atlas_dispatcher",
    "grant execute on function atlas.fail_task_workflow_start_intent(uuid, uuid, bigint, text) to atlas_dispatcher",
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      perform set_config('row_security', 'off', true);
      if exists (
        select 1
        from atlas.task_workflow_start_intent intent
        where intent.status <> 'PENDING'
          or intent.dispatch_attempts <> 0
          or intent.dispatch_revision <> 0
          or intent.available_at is distinct from intent.created_at
          or intent.claim_token is not null
          or intent.claimed_by is not null
          or intent.claimed_at is not null
          or intent.claim_expires_at is not null
          or intent.last_error_code is not null
          or intent.last_error_at is not null
          or intent.workflow_started_at is not null
          or intent.dispatch_failed_at is not null
      ) then
        raise exception 'cannot downgrade Task Workflow start-intent delivery after dispatch began'
          using errcode = '55000';
      end if;
    end;
    $$
    """,
    "drop function if exists atlas.claim_task_workflow_start_intents(text, text, integer, integer)",
    "drop function if exists atlas.mark_task_workflow_start_intent_started(uuid, uuid, bigint)",
    "drop function if exists atlas.mark_task_workflow_start_intent_started(uuid, uuid, bigint, timestamptz)",
    "drop function if exists atlas.retry_task_workflow_start_intent(uuid, uuid, bigint, text, integer)",
    "drop function if exists atlas.retry_task_workflow_start_intent(uuid, uuid, bigint, text, timestamptz)",
    "drop function if exists atlas.fail_task_workflow_start_intent(uuid, uuid, bigint, text)",
    "drop policy task_workflow_start_intent_dispatcher_access on atlas.task_workflow_start_intent",
    "drop trigger task_workflow_start_intent_guard_insert on atlas.task_workflow_start_intent",
    "drop trigger task_workflow_start_intent_guard_update on atlas.task_workflow_start_intent",
    "drop function atlas.guard_task_workflow_start_intent_insert()",
    "drop function atlas.guard_task_workflow_start_intent_update()",
    "revoke usage on schema atlas from atlas_dispatcher",
    "drop index atlas.task_workflow_start_intent_dispatch_ready_idx",
    "drop index atlas.task_workflow_start_intent_expired_claim_idx",
    """
    alter table atlas.task_workflow_start_intent
      drop constraint task_workflow_start_intent_shape,
      drop constraint task_workflow_start_intent_manifest_scope_fk,
      drop column manifest_hash,
      drop column available_at,
      drop column claim_token,
      drop column claimed_by,
      drop column claimed_at,
      drop column claim_expires_at,
      drop column dispatch_attempts,
      drop column last_error_code,
      drop column last_error_at,
      drop column workflow_started_at,
      drop column dispatch_failed_at,
      drop column dispatch_revision,
      add constraint task_workflow_start_intent_shape check (
        owner_kind in ('TASK_RUN', 'UNIT_ATTEMPT')
        and request_digest ~ '^sha256:[0-9a-f]{64}$'
        and workflow_type in ('AtlasTaskRunWorkflow', 'AtlasUnitAttemptWorkflow')
        and task_queue ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
        and status = 'PENDING'
      )
    """,
    """
    create index task_workflow_start_intent_pending_idx
      on atlas.task_workflow_start_intent (
        tenant_id, project_id, created_at, id
      ) where status = 'PENDING'
    """,
    """
    create trigger task_workflow_start_intent_prevent_mutation
      before update or delete on atlas.task_workflow_start_intent
      for each row execute function atlas.prevent_fact_mutation()
    """,
)


def upgrade() -> None:
    """Add durable, cross-tenant Task Workflow intent delivery."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Restore the append-only P5-00B1 intent contract when dispatch never began."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
