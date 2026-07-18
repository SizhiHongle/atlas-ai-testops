"""Add durable TaskRun pause and resume commands.

Revision ID: 20260717_0029
Revises: 20260717_0028
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_0029"
down_revision: str | None = "20260717_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    "alter table atlas.task_run_command_intent "
    "add column superseded_at timestamptz",
    "alter table atlas.task_run_command_intent "
    "add column superseded_by_command_id uuid",
    "alter table atlas.task_run_command_intent "
    "alter column schema_version set default 'atlas.task-run-command/0.2'",
    "alter table atlas.task_run_command_intent "
    "drop constraint task_run_command_contract_shape",
    "alter table atlas.task_run_command_intent "
    "drop constraint task_run_command_delivery_shape",
    """
    alter table atlas.task_run_command_intent
    add constraint task_run_command_contract_shape check (
      (
        (
          schema_version = 'atlas.task-run-command/0.1'
          and command_type = 'CANCEL'
        )
        or (
          schema_version = 'atlas.task-run-command/0.2'
          and command_type in ('CANCEL', 'PAUSE', 'RESUME')
        )
      )
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
        'PENDING', 'CLAIMED', 'RETRY_WAIT', 'DELIVERED',
        'APPLIED', 'FAILED', 'SUPERSEDED'
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
      and (
        superseded_at is null
        or (isfinite(superseded_at) and superseded_at >= created_at)
      )
      and (
        superseded_by_command_id is null
        or superseded_by_command_id <> id
      )
    )
    """,
    """
    alter table atlas.task_run_command_intent
    add constraint task_run_command_delivery_shape check (
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
        and superseded_at is null
        and superseded_by_command_id is null
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
        and superseded_at is null
        and superseded_by_command_id is null
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
        and superseded_at is null
        and superseded_by_command_id is null
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
        and superseded_at is null
        and superseded_by_command_id is null
        and dispatch_revision > 1
      )
      or (
        status = 'APPLIED'
        and claim_token is null
        and claimed_by is null
        and claimed_at is null
        and claim_expires_at is null
        and dispatch_attempts >= 0
        and applied_at is not null
        and dispatch_failed_at is null
        and superseded_at is null
        and superseded_by_command_id is null
        and dispatch_revision > 0
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
        and superseded_at is null
        and superseded_by_command_id is null
        and dispatch_revision > 1
      )
      or (
        status = 'SUPERSEDED'
        and claim_token is null
        and claimed_by is null
        and claimed_at is null
        and claim_expires_at is null
        and applied_at is null
        and dispatch_failed_at is null
        and superseded_at is not null
        and superseded_by_command_id is not null
        and dispatch_revision > 0
      )
    )
    """,
    """
    create unique index task_run_command_open_pause_resume_idx
      on atlas.task_run_command_intent (tenant_id, task_run_id)
      where command_type in ('PAUSE', 'RESUME')
        and status in ('PENDING', 'CLAIMED', 'RETRY_WAIT', 'DELIVERED')
    """,
    """
    create or replace function atlas.guard_task_run_command_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas, public
    as $$
    declare
      run_row atlas.task_run%rowtype;
      expected_digest text;
      expected_lifecycle text;
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
      expected_lifecycle := case new.command_type
        when 'CANCEL' then 'CANCELING'
        when 'PAUSE' then 'PAUSE_REQUESTED'
        when 'RESUME' then 'PAUSED'
        else null
      end;
      if run_row.materialization_state <> 'SEALED'
        or run_row.legacy_unsealed
        or expected_lifecycle is null
        or run_row.lifecycle <> expected_lifecycle
        or run_row.quality <> 'PENDING'
        or run_row.revision <> new.accepted_run_revision
        or run_row.request_digest <> new.request_digest
        or run_row.manifest_hash <> new.manifest_hash
        or run_row.temporal_namespace <> new.namespace
        or run_row.temporal_workflow_id <> new.workflow_id
      then
        raise exception 'Task command does not match the exact controlled TaskRun'
          using errcode = '55000';
      end if;
      if not (
        (new.schema_version = 'atlas.task-run-command/0.1' and new.command_type = 'CANCEL')
        or (
          new.schema_version = 'atlas.task-run-command/0.2'
          and new.command_type in ('CANCEL', 'PAUSE', 'RESUME')
        )
      ) then
        raise exception 'Task command schema and type are incompatible'
          using errcode = '22000';
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
    create or replace function atlas.guard_task_run_command_update()
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
          and new.status in (
            'CLAIMED', 'RETRY_WAIT', 'DELIVERED', 'APPLIED', 'FAILED', 'SUPERSEDED'
          )
        )
        or (old.status = 'DELIVERED' and new.status in ('APPLIED', 'SUPERSEDED'))
        or (
          old.status in ('PENDING', 'RETRY_WAIT')
          and new.status in ('APPLIED', 'SUPERSEDED')
        )
      ) then
        raise exception 'Task command delivery transition is invalid' using errcode = '55000';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.apply_task_run_pause_command(
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
      if command_row.command_type <> 'PAUSE'
        or command_row.command_digest <> p_command_digest
      then
        raise exception 'Task pause command identity mismatch' using errcode = '55000';
      end if;
      if command_row.status = 'APPLIED' then
        return true;
      end if;
      if command_row.status not in ('PENDING', 'CLAIMED', 'RETRY_WAIT', 'DELIVERED') then
        raise exception 'Task pause command is not applicable' using errcode = '55000';
      end if;
      perform 1
      from atlas.task_run run
      where run.id = command_row.task_run_id
        and run.tenant_id = command_row.tenant_id
        and run.project_id = command_row.project_id
        and run.lifecycle = 'PAUSED'
        and run.quality = 'PENDING'
      for share;
      if not found then
        raise exception 'Task pause command requires a paused Run' using errcode = '55000';
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
    """
    create function atlas.apply_task_run_resume_command(
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
      if command_row.command_type <> 'RESUME'
        or command_row.command_digest <> p_command_digest
      then
        raise exception 'Task resume command identity mismatch' using errcode = '55000';
      end if;
      if command_row.status = 'APPLIED' then
        return true;
      end if;
      if command_row.status not in ('PENDING', 'CLAIMED', 'RETRY_WAIT', 'DELIVERED') then
        raise exception 'Task resume command is not applicable' using errcode = '55000';
      end if;
      perform 1
      from atlas.task_run run
      where run.id = command_row.task_run_id
        and run.tenant_id = command_row.tenant_id
        and run.project_id = command_row.project_id
        and run.lifecycle = 'RUNNING'
        and run.quality = 'PENDING'
      for share;
      if not found then
        raise exception 'Task resume command requires a running Run' using errcode = '55000';
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
    """
    create function atlas.supersede_task_run_commands(
      p_task_run_id uuid,
      p_superseding_command_id uuid
    ) returns integer
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      superseded_at_value timestamptz := transaction_timestamp();
      affected integer;
    begin
      if atlas.current_tenant_id() is null then
        raise exception 'Task command supersession requires tenant context'
          using errcode = '42501';
      end if;
      perform 1
      from atlas.task_run_command_intent command
      join atlas.task_run run
        on run.id = command.task_run_id
       and run.tenant_id = command.tenant_id
       and run.project_id = command.project_id
      where command.id = p_superseding_command_id
        and command.task_run_id = p_task_run_id
        and command.tenant_id = atlas.current_tenant_id()
        and command.command_type = 'CANCEL'
        and run.lifecycle = 'CANCELING'
      for share of command, run;
      if not found then
        raise exception 'Task command supersession requires exact cancel command'
          using errcode = '55000';
      end if;
      update atlas.task_run_command_intent command
      set
        status = 'SUPERSEDED',
        claim_token = null,
        claimed_by = null,
        claimed_at = null,
        claim_expires_at = null,
        superseded_at = superseded_at_value,
        superseded_by_command_id = p_superseding_command_id,
        dispatch_revision = command.dispatch_revision + 1,
        updated_at = superseded_at_value
      where command.tenant_id = atlas.current_tenant_id()
        and command.task_run_id = p_task_run_id
        and command.id <> p_superseding_command_id
        and command.command_type in ('PAUSE', 'RESUME')
        and command.status in ('PENDING', 'CLAIMED', 'RETRY_WAIT', 'DELIVERED');
      get diagnostics affected = row_count;
      return affected;
    end;
    $$
    """,
    (
        "revoke all on function atlas.apply_task_run_pause_command(uuid, text) "
        "from public, atlas_dispatcher"
    ),
    (
        "revoke all on function atlas.apply_task_run_resume_command(uuid, text) "
        "from public, atlas_dispatcher"
    ),
    (
        "revoke all on function atlas.supersede_task_run_commands(uuid, uuid) "
        "from public, atlas_dispatcher"
    ),
    (
        "grant execute on function atlas.apply_task_run_pause_command(uuid, text) "
        "to atlas_app"
    ),
    (
        "grant execute on function atlas.apply_task_run_resume_command(uuid, text) "
        "to atlas_app"
    ),
    (
        "grant execute on function atlas.supersede_task_run_commands(uuid, uuid) "
        "to atlas_app"
    ),
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (
        select 1
        from atlas.task_run_command_intent
        where schema_version <> 'atlas.task-run-command/0.1'
          or command_type <> 'CANCEL'
          or status = 'SUPERSEDED'
          or superseded_at is not null
          or superseded_by_command_id is not null
      ) then
        raise exception 'cannot downgrade Task pause/resume after v0.2 commands were accepted'
          using errcode = '55000';
      end if;
    end;
    $$
    """,
    "drop index atlas.task_run_command_open_pause_resume_idx",
    "drop function atlas.supersede_task_run_commands(uuid, uuid)",
    "drop function atlas.apply_task_run_resume_command(uuid, text)",
    "drop function atlas.apply_task_run_pause_command(uuid, text)",
    "alter table atlas.task_run_command_intent "
    "drop constraint task_run_command_delivery_shape",
    "alter table atlas.task_run_command_intent "
    "drop constraint task_run_command_contract_shape",
    """
    create or replace function atlas.guard_task_run_command_update()
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
    "alter table atlas.task_run_command_intent drop column superseded_by_command_id",
    "alter table atlas.task_run_command_intent drop column superseded_at",
    "alter table atlas.task_run_command_intent "
    "alter column schema_version set default 'atlas.task-run-command/0.1'",
    """
    alter table atlas.task_run_command_intent
    add constraint task_run_command_contract_shape check (
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
    )
    """,
    """
    alter table atlas.task_run_command_intent
    add constraint task_run_command_delivery_shape check (
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
    """,
)


def upgrade() -> None:
    """Extend durable Task commands with batch-safe Pause and Resume."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove Pause/Resume only when no v0.2 command fact would be lost."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
