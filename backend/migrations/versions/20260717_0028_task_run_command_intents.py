"""Add durable TaskRun control-command delivery.

Revision ID: 20260717_0028
Revises: 20260717_0027
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_0028"
down_revision: str | None = "20260717_0027"
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
        raise exception 'Task command migration owner must be SUPERUSER or BYPASSRLS'
          using errcode = '42501';
      end if;
      if exists (
        select 1
        from pg_catalog.pg_roles role
        where role.rolname = 'atlas_dispatcher'
          and (not role.rolcanlogin or role.rolsuper or role.rolbypassrls)
      ) then
        raise exception 'atlas_dispatcher must be LOGIN, NOSUPERUSER, and NOBYPASSRLS'
          using errcode = '42501';
      end if;
    end;
    $$
    """,
    """
    create table atlas.task_run_command_intent (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      schema_version text not null default 'atlas.task-run-command/0.1',
      command_type text not null,
      client_mutation_id text not null,
      command_digest text not null,
      expected_run_revision bigint not null,
      accepted_run_revision bigint not null,
      request_digest text not null,
      manifest_hash text not null,
      namespace text not null,
      workflow_id text not null,
      status text not null default 'PENDING',
      available_at timestamptz not null,
      claim_token uuid,
      claimed_by text,
      claimed_at timestamptz,
      claim_expires_at timestamptz,
      dispatch_attempts integer not null default 0,
      last_error_code text,
      last_error_at timestamptz,
      signal_delivered_at timestamptz,
      applied_at timestamptz,
      dispatch_failed_at timestamptz,
      dispatch_revision bigint not null default 0,
      created_by uuid,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      constraint task_run_command_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id
      ) references atlas.task_run (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_run_command_mutation_unique unique (
        tenant_id, task_run_id, client_mutation_id
      ),
      constraint task_run_command_digest_unique unique (
        tenant_id, task_run_id, command_digest
      ),
      constraint task_run_command_contract_shape check (
        schema_version = 'atlas.task-run-command/0.1'
        and command_type = 'CANCEL'
        and client_mutation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{7,199}$'
        and command_digest ~ '^sha256:[0-9a-f]{64}$'
        and request_digest ~ '^sha256:[0-9a-f]{64}$'
        and manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and expected_run_revision > 0
        and accepted_run_revision = expected_run_revision + 1
        and namespace ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
        and workflow_id ~ '^atlas-task/[A-Za-z0-9/_-]+$'
        and char_length(workflow_id) between 12 and 320
        and status in (
          'PENDING', 'CLAIMED', 'RETRY_WAIT', 'DELIVERED', 'APPLIED', 'FAILED'
        )
        and dispatch_attempts >= 0
        and dispatch_revision >= 0
        and isfinite(created_at)
        and isfinite(updated_at)
        and updated_at >= created_at
        and isfinite(available_at)
        and (last_error_code is null or last_error_code ~ '^[A-Z][A-Z0-9_]{0,63}$')
        and (last_error_at is null or (isfinite(last_error_at) and last_error_at >= created_at))
        and (
          signal_delivered_at is null
          or (isfinite(signal_delivered_at) and signal_delivered_at >= created_at)
        )
        and (applied_at is null or (isfinite(applied_at) and applied_at >= created_at))
        and (
          dispatch_failed_at is null
          or (isfinite(dispatch_failed_at) and dispatch_failed_at >= created_at)
        )
      ),
      constraint task_run_command_delivery_shape check (
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
          and signal_delivered_at is null
          and applied_at is null
          and dispatch_failed_at is null
          and dispatch_revision = 0
        )
        or (
          status = 'CLAIMED'
          and claim_token is not null
          and claimed_by ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
          and claimed_at is not null
          and isfinite(claimed_at)
          and claim_expires_at is not null
          and isfinite(claim_expires_at)
          and claim_expires_at > claimed_at
          and dispatch_attempts > 0
          and signal_delivered_at is null
          and applied_at is null
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
          and signal_delivered_at is null
          and applied_at is null
          and dispatch_failed_at is null
          and dispatch_revision > 1
        )
        or (
          status = 'DELIVERED'
          and claim_token is null
          and claimed_by is null
          and claimed_at is null
          and claim_expires_at is null
          and dispatch_attempts > 0
          and signal_delivered_at is not null
          and applied_at is null
          and dispatch_failed_at is null
          and dispatch_revision > 1
        )
        or (
          status = 'APPLIED'
          and claim_token is null
          and claimed_by is null
          and claimed_at is null
          and claim_expires_at is null
          and dispatch_attempts > 0
          and applied_at is not null
          and dispatch_failed_at is null
          and dispatch_revision > 1
        )
        or (
          status = 'FAILED'
          and claim_token is null
          and claimed_by is null
          and claimed_at is null
          and claim_expires_at is null
          and dispatch_attempts > 0
          and last_error_code is not null
          and last_error_at is not null
          and applied_at is null
          and dispatch_failed_at = last_error_at
          and dispatch_revision > 1
        )
      )
    )
    """,
    """
    create index task_run_command_dispatch_ready_idx
      on atlas.task_run_command_intent (
        namespace, available_at, created_at, id
      )
      where status in ('PENDING', 'RETRY_WAIT')
    """,
    """
    create index task_run_command_expired_claim_idx
      on atlas.task_run_command_intent (
        namespace, claim_expires_at, created_at, id
      )
      where status = 'CLAIMED'
    """,
    """
    create index task_run_command_run_status_idx
      on atlas.task_run_command_intent (task_run_id, created_at, id)
    """,
    """
    create function atlas.guard_task_run_command_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas, public
    as $$
    declare
      run_row atlas.task_run%rowtype;
      expected_digest text;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
        or new.created_by is distinct from atlas.current_actor_id()
      then
        raise exception 'Task command creation requires exact tenant and actor context'
          using errcode = '42501';
      end if;
      select run.* into run_row
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
      for update;
      if not found then
        raise exception 'Task command TaskRun scope is missing' using errcode = 'P0002';
      end if;
      if run_row.materialization_state <> 'SEALED'
        or run_row.legacy_unsealed
        or run_row.lifecycle <> 'CANCELING'
        or run_row.quality <> 'PENDING'
        or run_row.revision <> new.accepted_run_revision
        or run_row.request_digest <> new.request_digest
        or run_row.manifest_hash <> new.manifest_hash
        or run_row.temporal_namespace <> new.namespace
        or run_row.temporal_workflow_id <> new.workflow_id
      then
        raise exception 'Task command does not match the exact canceling TaskRun'
          using errcode = '55000';
      end if;
      expected_digest := atlas.task_sha256_json(
        jsonb_build_object(
          'schemaVersion', new.schema_version,
          'tenantId', new.tenant_id::text,
          'projectId', new.project_id::text,
          'taskRunId', new.task_run_id::text,
          'commandType', new.command_type,
          'clientMutationId', new.client_mutation_id,
          'expectedRunRevision', new.expected_run_revision,
          'requestDigest', new.request_digest,
          'manifestHash', new.manifest_hash,
          'temporalNamespace', new.namespace,
          'temporalWorkflowId', new.workflow_id
        )
      );
      if new.command_digest <> expected_digest then
        raise exception 'Task command digest mismatch' using errcode = '22000';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_run_command_guard_insert
      before insert on atlas.task_run_command_intent
      for each row execute function atlas.guard_task_run_command_insert()
    """,
    """
    create function atlas.guard_task_run_command_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if (
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.schema_version, new.command_type, new.client_mutation_id,
        new.command_digest, new.expected_run_revision, new.accepted_run_revision,
        new.request_digest, new.manifest_hash, new.namespace, new.workflow_id,
        new.created_by, new.created_at
      ) is distinct from (
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.schema_version, old.command_type, old.client_mutation_id,
        old.command_digest, old.expected_run_revision, old.accepted_run_revision,
        old.request_digest, old.manifest_hash, old.namespace, old.workflow_id,
        old.created_by, old.created_at
      ) then
        raise exception 'Task command immutable identity cannot change' using errcode = '55000';
      end if;
      if new.dispatch_revision <> old.dispatch_revision + 1 then
        raise exception 'Task command dispatch revision must advance once' using errcode = '40001';
      end if;
      if not (
        (old.status in ('PENDING', 'RETRY_WAIT') and new.status = 'CLAIMED')
        or (
          old.status = 'CLAIMED'
          and new.status in ('CLAIMED', 'RETRY_WAIT', 'DELIVERED', 'APPLIED', 'FAILED')
        )
        or (old.status = 'DELIVERED' and new.status = 'APPLIED')
      ) then
        raise exception 'Task command delivery transition is invalid' using errcode = '55000';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_run_command_guard_update
      before update on atlas.task_run_command_intent
      for each row execute function atlas.guard_task_run_command_update()
    """,
    """
    create trigger task_run_command_prevent_delete
      before delete on atlas.task_run_command_intent
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create function atlas.claim_task_run_command_intents(
      p_claimed_by text,
      p_namespace text,
      p_limit integer,
      p_lease_seconds integer
    ) returns setof atlas.task_run_command_intent
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      claimed_at_value timestamptz := transaction_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task command claim requires atlas_dispatcher' using errcode = '42501';
      end if;
      if p_claimed_by !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        or p_namespace !~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
        or p_limit not between 1 and 100
        or p_lease_seconds not between 1 and 900
      then
        raise exception 'Task command claim request is invalid' using errcode = '22023';
      end if;
      return query
      with candidates as (
        select intent.id
        from atlas.task_run_command_intent intent
        where intent.namespace = p_namespace
          and (
            (
              intent.status in ('PENDING', 'RETRY_WAIT')
              and intent.available_at <= claimed_at_value
            )
            or (
              intent.status = 'CLAIMED'
              and intent.claim_expires_at <= claimed_at_value
            )
          )
        order by intent.available_at, intent.created_at, intent.id
        limit p_limit
        for update skip locked
      )
      update atlas.task_run_command_intent intent
      set
        status = 'CLAIMED',
        claim_token = gen_random_uuid(),
        claimed_by = p_claimed_by,
        claimed_at = claimed_at_value,
        claim_expires_at = claimed_at_value + make_interval(secs => p_lease_seconds),
        dispatch_attempts = intent.dispatch_attempts + 1,
        dispatch_revision = intent.dispatch_revision + 1,
        updated_at = claimed_at_value
      from candidates
      where intent.id = candidates.id
      returning intent.*;
    end;
    $$
    """,
    """
    create function atlas.mark_task_run_command_intent_delivered(
      p_intent_id uuid,
      p_claim_token uuid,
      p_expected_dispatch_revision bigint
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      delivered_at_value timestamptz := transaction_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task command delivery requires atlas_dispatcher' using errcode = '42501';
      end if;
      update atlas.task_run_command_intent intent
      set
        status = 'DELIVERED',
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        signal_delivered_at = delivered_at_value,
        dispatch_revision = intent.dispatch_revision + 1,
        updated_at = delivered_at_value
      where intent.id = p_intent_id
        and intent.status = 'CLAIMED'
        and intent.claim_token = p_claim_token
        and intent.dispatch_revision = p_expected_dispatch_revision
        and intent.claim_expires_at > delivered_at_value;
      return found;
    end;
    $$
    """,
    """
    create function atlas.retry_task_run_command_intent(
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
      failed_at_value timestamptz := transaction_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task command retry requires atlas_dispatcher' using errcode = '42501';
      end if;
      if p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$'
        or p_retry_delay_ms not between 100 and 3600000
      then
        raise exception 'Task command retry request is invalid' using errcode = '22023';
      end if;
      update atlas.task_run_command_intent intent
      set
        status = 'RETRY_WAIT',
        available_at = failed_at_value + (p_retry_delay_ms * interval '1 millisecond'),
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        last_error_code = p_error_code,
        last_error_at = failed_at_value,
        dispatch_revision = intent.dispatch_revision + 1,
        updated_at = failed_at_value
      where intent.id = p_intent_id
        and intent.status = 'CLAIMED'
        and intent.claim_token = p_claim_token
        and intent.dispatch_revision = p_expected_dispatch_revision
        and intent.claim_expires_at > failed_at_value;
      return found;
    end;
    $$
    """,
    """
    create function atlas.fail_task_run_command_intent(
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
      failed_at_value timestamptz := transaction_timestamp();
      already_canceled boolean := false;
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task command failure requires atlas_dispatcher' using errcode = '42501';
      end if;
      if p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$' then
        raise exception 'Task command failure code is invalid' using errcode = '22023';
      end if;
      select run.lifecycle = 'CLOSED' and run.quality = 'CANCELED'
      into already_canceled
      from atlas.task_run_command_intent intent
      join atlas.task_run run
        on run.id = intent.task_run_id
       and run.tenant_id = intent.tenant_id
       and run.project_id = intent.project_id
      where intent.id = p_intent_id;
      update atlas.task_run_command_intent intent
      set
        status = case when already_canceled then 'APPLIED' else 'FAILED' end,
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        last_error_code = case when already_canceled then null else p_error_code end,
        last_error_at = case when already_canceled then null else failed_at_value end,
        applied_at = case when already_canceled then failed_at_value else null end,
        dispatch_failed_at = case when already_canceled then null else failed_at_value end,
        dispatch_revision = intent.dispatch_revision + 1,
        updated_at = failed_at_value
      where intent.id = p_intent_id
        and intent.status = 'CLAIMED'
        and intent.claim_token = p_claim_token
        and intent.dispatch_revision = p_expected_dispatch_revision
        and intent.claim_expires_at > failed_at_value;
      return found;
    end;
    $$
    """,
    """
    create function atlas.apply_task_run_cancel_command(
      p_intent_id uuid,
      p_command_digest text
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      applied_at_value timestamptz := transaction_timestamp();
      command_row atlas.task_run_command_intent%rowtype;
    begin
      if atlas.current_tenant_id() is null then
        raise exception 'Task command apply requires tenant context' using errcode = '42501';
      end if;
      select intent.* into command_row
      from atlas.task_run_command_intent intent
      where intent.id = p_intent_id
        and intent.tenant_id = atlas.current_tenant_id()
      for update;
      if not found then
        raise exception 'Task command is missing from the current tenant' using errcode = 'P0002';
      end if;
      if command_row.command_type <> 'CANCEL'
        or command_row.command_digest <> p_command_digest
      then
        raise exception 'Task command apply identity mismatch' using errcode = '55000';
      end if;
      if command_row.status = 'APPLIED' then
        return true;
      end if;
      if command_row.status not in ('CLAIMED', 'DELIVERED') then
        raise exception 'Task command is not deliverable' using errcode = '55000';
      end if;
      perform 1
      from atlas.task_run run
      where run.id = command_row.task_run_id
        and run.tenant_id = command_row.tenant_id
        and run.project_id = command_row.project_id
        and run.lifecycle = 'CLOSED'
        and run.quality = 'CANCELED'
      for share;
      if not found then
        raise exception 'Task command can apply only after canceled Run closure'
          using errcode = '55000';
      end if;
      update atlas.task_run_command_intent intent
      set
        status = 'APPLIED',
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        applied_at = applied_at_value,
        dispatch_revision = intent.dispatch_revision + 1,
        updated_at = applied_at_value
      where intent.id = command_row.id;
      return true;
    end;
    $$
    """,
    "alter table atlas.task_run_command_intent enable row level security",
    "alter table atlas.task_run_command_intent force row level security",
    """
    create policy task_run_command_tenant_access
      on atlas.task_run_command_intent
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.task_run_command_intent from public, atlas_app, atlas_dispatcher",
    "grant select, insert on atlas.task_run_command_intent to atlas_app",
    (
        "revoke all on function atlas.guard_task_run_command_insert() "
        "from public, atlas_app, atlas_dispatcher"
    ),
    (
        "revoke all on function atlas.guard_task_run_command_update() "
        "from public, atlas_app, atlas_dispatcher"
    ),
    (
        "revoke all on function "
        "atlas.claim_task_run_command_intents(text, text, integer, integer) "
        "from public, atlas_app"
    ),
    (
        "revoke all on function "
        "atlas.mark_task_run_command_intent_delivered(uuid, uuid, bigint) "
        "from public, atlas_app"
    ),
    (
        "revoke all on function "
        "atlas.retry_task_run_command_intent(uuid, uuid, bigint, text, integer) "
        "from public, atlas_app"
    ),
    (
        "revoke all on function "
        "atlas.fail_task_run_command_intent(uuid, uuid, bigint, text) "
        "from public, atlas_app"
    ),
    (
        "revoke all on function atlas.apply_task_run_cancel_command(uuid, text) "
        "from public, atlas_dispatcher"
    ),
    (
        "grant execute on function "
        "atlas.claim_task_run_command_intents(text, text, integer, integer) "
        "to atlas_dispatcher"
    ),
    (
        "grant execute on function "
        "atlas.mark_task_run_command_intent_delivered(uuid, uuid, bigint) "
        "to atlas_dispatcher"
    ),
    (
        "grant execute on function "
        "atlas.retry_task_run_command_intent(uuid, uuid, bigint, text, integer) "
        "to atlas_dispatcher"
    ),
    (
        "grant execute on function "
        "atlas.fail_task_run_command_intent(uuid, uuid, bigint, text) "
        "to atlas_dispatcher"
    ),
    (
        "grant execute on function atlas.apply_task_run_cancel_command(uuid, text) "
        "to atlas_app"
    ),
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.task_run_command_intent) then
        raise exception 'cannot downgrade Task command intents after commands were accepted'
          using errcode = '55000';
      end if;
    end;
    $$
    """,
    "drop policy task_run_command_tenant_access on atlas.task_run_command_intent",
    "drop trigger task_run_command_prevent_delete on atlas.task_run_command_intent",
    "drop trigger task_run_command_guard_update on atlas.task_run_command_intent",
    "drop trigger task_run_command_guard_insert on atlas.task_run_command_intent",
    "drop function atlas.apply_task_run_cancel_command(uuid, text)",
    "drop function atlas.fail_task_run_command_intent(uuid, uuid, bigint, text)",
    "drop function atlas.retry_task_run_command_intent(uuid, uuid, bigint, text, integer)",
    "drop function atlas.mark_task_run_command_intent_delivered(uuid, uuid, bigint)",
    "drop function atlas.claim_task_run_command_intents(text, text, integer, integer)",
    "drop function atlas.guard_task_run_command_update()",
    "drop function atlas.guard_task_run_command_insert()",
    "drop table atlas.task_run_command_intent",
)


def upgrade() -> None:
    """Create the durable TaskRun command delivery state machine."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove command delivery only while no command fact would be lost."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
