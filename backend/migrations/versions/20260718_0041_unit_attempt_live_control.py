"""Add fenced UnitAttempt live-control facts and persistent ActionGrants.

Revision ID: 20260718_0041
Revises: 20260718_0040
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0041"
down_revision: str | None = "20260718_0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.live_session (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      execution_ticket_id uuid not null,
      execution_ticket_digest text not null,
      browser_session_id text not null,
      schema_version text not null default 'atlas.live-session/0.1',
      state text not null,
      control_epoch bigint not null,
      fencing_token bigint not null,
      browser_revision bigint not null,
      human_influenced boolean not null default false,
      revision bigint not null default 1,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      closed_at timestamptz,
      constraint live_session_attempt_scope_fk foreign key (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.unit_attempt (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint live_session_ticket_scope_fk foreign key (
        execution_ticket_id, tenant_id, project_id, unit_attempt_id
      ) references atlas.task_unit_execution_ticket (
        id, tenant_id, project_id, unit_attempt_id
      ) on delete restrict,
      constraint live_session_attempt_unique unique (unit_attempt_id),
      constraint live_session_scope_unique unique (
        id, tenant_id, project_id, task_run_id, execution_unit_id, unit_attempt_id
      ),
      constraint live_session_schema_valid check (
        schema_version = 'atlas.live-session/0.1'
      ),
      constraint live_session_state_valid check (
        state in (
          'AGENT_CONTROLLED', 'QUIESCING', 'PAUSED', 'RESUME_REQUESTED',
          'HUMAN_CONTROLLED', 'RECONCILING', 'NO_CONTROLLER', 'CLOSED'
        )
      ),
      constraint live_session_numbers_valid check (
        control_epoch > 0 and fencing_token > 0
        and browser_revision > 0 and revision > 0
      ),
      constraint live_session_digest_valid check (
        execution_ticket_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint live_session_browser_id_valid check (
        browser_session_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{1,199}$'
      ),
      constraint live_session_closed_valid check (
        (state = 'CLOSED') = (closed_at is not null)
      ),
      constraint live_session_time_valid check (
        updated_at >= created_at
        and (closed_at is null or closed_at >= created_at)
      )
    )
    """,
    """
    create table atlas.control_lease (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      live_session_id uuid not null,
      schema_version text not null default 'atlas.control-lease/0.1',
      owner_type text not null,
      owner_id text not null,
      control_epoch bigint not null,
      fencing_token bigint not null,
      state text not null,
      expires_at timestamptz not null,
      reason text not null,
      created_by uuid,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      released_at timestamptz,
      constraint control_lease_session_scope_fk foreign key (
        live_session_id, tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id
      ) references atlas.live_session (
        id, tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id
      ) on delete restrict,
      constraint control_lease_scope_unique unique (
        id, tenant_id, project_id, live_session_id
      ),
      constraint control_lease_epoch_unique unique (
        live_session_id, control_epoch
      ),
      constraint control_lease_fence_unique unique (
        live_session_id, fencing_token
      ),
      constraint control_lease_schema_valid check (
        schema_version = 'atlas.control-lease/0.1'
      ),
      constraint control_lease_owner_valid check (
        owner_type in ('AGENT', 'HUMAN')
        and owner_id ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,199}$'
      ),
      constraint control_lease_state_valid check (
        state in ('ACTIVE', 'REVOKING', 'EXPIRED', 'RELEASED')
      ),
      constraint control_lease_numbers_valid check (
        control_epoch > 0 and fencing_token > 0
      ),
      constraint control_lease_reason_valid check (
        char_length(reason) between 1 and 500
        and reason !~ '[[:cntrl:]]'
      ),
      constraint control_lease_terminal_valid check (
        (state in ('EXPIRED', 'RELEASED')) = (released_at is not null)
      ),
      constraint control_lease_time_valid check (
        expires_at > created_at and updated_at >= created_at
        and (released_at is null or released_at >= created_at)
      )
    )
    """,
    """
    create unique index control_lease_one_current_idx
      on atlas.control_lease (live_session_id)
      where state in ('ACTIVE', 'REVOKING')
    """,
    """
    create table atlas.live_control_command (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      live_session_id uuid not null,
      schema_version text not null default 'atlas.live-control-command/0.1',
      command_type text not null,
      client_mutation_id text not null,
      reason text not null,
      requested_ttl_sec integer,
      expected_control_epoch bigint not null,
      accepted_session_revision bigint not null,
      status text not null default 'PENDING',
      requested_by uuid,
      created_at timestamptz not null,
      updated_at timestamptz not null,
      applied_at timestamptz,
      resulting_control_epoch bigint,
      resulting_fencing_token bigint,
      checkpoint_digest text,
      constraint live_control_command_session_scope_fk foreign key (
        live_session_id, tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id
      ) references atlas.live_session (
        id, tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id
      ) on delete restrict,
      constraint live_control_command_mutation_unique unique (
        tenant_id, live_session_id, client_mutation_id
      ),
      constraint live_control_command_scope_unique unique (
        id, tenant_id, project_id, live_session_id
      ),
      constraint live_control_command_schema_valid check (
        schema_version = 'atlas.live-control-command/0.1'
      ),
      constraint live_control_command_type_valid check (
        command_type in ('PAUSE', 'RESUME', 'TAKEOVER', 'RETURN')
      ),
      constraint live_control_command_status_valid check (
        status in ('PENDING', 'APPLIED', 'REJECTED')
      ),
      constraint live_control_command_mutation_valid check (
        char_length(client_mutation_id) between 8 and 200
        and client_mutation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
      ),
      constraint live_control_command_reason_valid check (
        char_length(reason) between 1 and 500
        and reason !~ '[[:cntrl:]]'
      ),
      constraint live_control_command_numbers_valid check (
        expected_control_epoch > 0 and accepted_session_revision > 0
        and (
          requested_ttl_sec is null
          or requested_ttl_sec between 30 and 900
        )
      ),
      constraint live_control_command_completion_valid check (
        (
          status = 'APPLIED'
          and applied_at is not null
          and resulting_control_epoch is not null
          and resulting_fencing_token is not null
          and checkpoint_digest ~ '^sha256:[0-9a-f]{64}$'
        )
        or (
          status <> 'APPLIED'
          and applied_at is null
          and resulting_control_epoch is null
          and resulting_fencing_token is null
          and checkpoint_digest is null
        )
      ),
      constraint live_control_command_time_valid check (
        updated_at >= created_at
        and (applied_at is null or applied_at >= created_at)
      )
    )
    """,
    """
    create unique index live_control_command_one_pending_idx
      on atlas.live_control_command (live_session_id)
      where status = 'PENDING'
    """,
    """
    create table atlas.live_action_grant (
      grant_id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      live_session_id uuid not null,
      control_lease_id uuid not null,
      schema_version text not null default 'atlas.live-action-grant/0.1',
      action_id uuid not null,
      proposal_digest text not null,
      browser_session_id text not null,
      page_id text not null,
      page_revision bigint not null,
      control_epoch bigint not null,
      fencing_token bigint not null,
      owner_type text not null,
      owner_id text not null,
      allowed_adapter text not null,
      expires_at timestamptz not null,
      max_executions integer not null default 1,
      policy_digest text not null,
      state text not null default 'ISSUED',
      created_at timestamptz not null,
      consumed_at timestamptz,
      completed_at timestamptz,
      revoked_at timestamptz,
      receipt_id uuid,
      execution_status text,
      resulting_page_revision bigint,
      constraint live_action_grant_session_scope_fk foreign key (
        live_session_id, tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id
      ) references atlas.live_session (
        id, tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id
      ) on delete restrict,
      constraint live_action_grant_lease_scope_fk foreign key (
        control_lease_id, tenant_id, project_id, live_session_id
      ) references atlas.control_lease (
        id, tenant_id, project_id, live_session_id
      ) on delete restrict,
      constraint live_action_grant_action_unique unique (
        unit_attempt_id, action_id
      ),
      constraint live_action_grant_scope_unique unique (
        grant_id, tenant_id, project_id, live_session_id
      ),
      constraint live_action_grant_schema_valid check (
        schema_version = 'atlas.live-action-grant/0.1'
      ),
      constraint live_action_grant_digests_valid check (
        proposal_digest ~ '^sha256:[0-9a-f]{64}$'
        and policy_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint live_action_grant_identifiers_valid check (
        browser_session_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{1,199}$'
        and page_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{1,199}$'
        and allowed_adapter ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{1,199}$'
        and owner_id ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,199}$'
      ),
      constraint live_action_grant_owner_valid check (
        owner_type in ('AGENT', 'HUMAN')
      ),
      constraint live_action_grant_state_valid check (
        state in ('ISSUED', 'CONSUMED', 'COMPLETED', 'REVOKED')
      ),
      constraint live_action_grant_execution_status_valid check (
        execution_status is null
        or execution_status in ('SUCCEEDED', 'FAILED', 'OUTCOME_UNKNOWN')
      ),
      constraint live_action_grant_numbers_valid check (
        page_revision > 0 and control_epoch > 0 and fencing_token > 0
        and max_executions = 1
        and (resulting_page_revision is null or resulting_page_revision > 0)
      ),
      constraint live_action_grant_lifecycle_valid check (
        (
          state = 'ISSUED'
          and consumed_at is null and completed_at is null and revoked_at is null
          and receipt_id is null and execution_status is null
          and resulting_page_revision is null
        )
        or (
          state = 'CONSUMED'
          and consumed_at is not null and completed_at is null and revoked_at is null
          and receipt_id is null and execution_status is null
          and resulting_page_revision is null
        )
        or (
          state = 'COMPLETED'
          and consumed_at is not null and completed_at is not null and revoked_at is null
          and receipt_id is not null and execution_status is not null
          and resulting_page_revision is not null
        )
        or (
          state = 'REVOKED'
          and consumed_at is null and completed_at is null and revoked_at is not null
          and receipt_id is null and execution_status is null
          and resulting_page_revision is null
        )
      ),
      constraint live_action_grant_time_valid check (
        expires_at > created_at
        and (consumed_at is null or consumed_at >= created_at)
        and (completed_at is null or completed_at >= consumed_at)
        and (revoked_at is null or revoked_at >= created_at)
      )
    )
    """,
    """
    create index live_action_grant_inflight_idx
      on atlas.live_action_grant (live_session_id, state, expires_at)
      where state in ('ISSUED', 'CONSUMED')
    """,
    """
    create table atlas.live_control_event (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      live_session_id uuid not null,
      seq bigint not null,
      event_type text not null,
      control_epoch bigint not null,
      fencing_token bigint not null,
      payload jsonb not null default '{}'::jsonb,
      occurred_at timestamptz not null,
      constraint live_control_event_session_scope_fk foreign key (
        live_session_id, tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id
      ) references atlas.live_session (
        id, tenant_id, project_id, task_run_id,
        execution_unit_id, unit_attempt_id
      ) on delete restrict,
      constraint live_control_event_sequence_unique unique (
        live_session_id, seq
      ),
      constraint live_control_event_type_valid check (
        event_type ~ '^[a-z][a-z0-9_.-]+$'
        and char_length(event_type) between 3 and 160
      ),
      constraint live_control_event_numbers_valid check (
        seq > 0 and control_epoch > 0 and fencing_token > 0
      ),
      constraint live_control_event_payload_valid check (
        jsonb_typeof(payload) = 'object'
        and octet_length(payload::text) <= 32768
      )
    )
    """,
    """
    create function atlas.guard_live_session_update()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.execution_unit_id, new.unit_attempt_id, new.execution_ticket_id,
        new.execution_ticket_digest, new.browser_session_id, new.schema_version,
        new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.execution_unit_id, old.unit_attempt_id, old.execution_ticket_id,
        old.execution_ticket_digest, old.browser_session_id, old.schema_version,
        old.created_at
      ) then
        raise exception 'LiveSession immutable scope cannot change';
      end if;
      if old.state = 'CLOSED' then
        raise exception 'CLOSED LiveSession is immutable';
      end if;
      if new.revision <> old.revision + 1
        or new.updated_at <> transaction_timestamp()
        or new.browser_revision < old.browser_revision
        or (old.human_influenced and not new.human_influenced)
      then
        raise exception 'LiveSession revision, browser revision, or influence is invalid';
      end if;
      if (
        new.control_epoch = old.control_epoch
        and new.fencing_token = old.fencing_token
      ) or (
        new.control_epoch = old.control_epoch + 1
        and new.fencing_token = old.fencing_token + 1
      ) then
        null;
      else
        raise exception 'LiveSession epoch and fence must advance together by one';
      end if;
      if not (
        new.state = old.state
        or (
          old.state = 'AGENT_CONTROLLED'
          and new.state in ('QUIESCING', 'NO_CONTROLLER', 'CLOSED')
        )
        or (
          old.state = 'QUIESCING'
          and new.state in ('PAUSED', 'HUMAN_CONTROLLED', 'NO_CONTROLLER', 'CLOSED')
        )
        or (
          old.state in ('PAUSED', 'NO_CONTROLLER')
          and new.state in ('RESUME_REQUESTED', 'CLOSED')
        )
        or (
          old.state = 'RESUME_REQUESTED'
          and new.state in ('AGENT_CONTROLLED', 'NO_CONTROLLER', 'CLOSED')
        )
        or (
          old.state = 'HUMAN_CONTROLLED'
          and new.state in ('RECONCILING', 'NO_CONTROLLER', 'CLOSED')
        )
        or (
          old.state = 'RECONCILING'
          and new.state in ('AGENT_CONTROLLED', 'NO_CONTROLLER', 'CLOSED')
        )
      ) then
        raise exception 'LiveSession state transition is invalid';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_control_lease_update()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.execution_unit_id, new.unit_attempt_id, new.live_session_id,
        new.schema_version, new.owner_type, new.owner_id, new.control_epoch,
        new.fencing_token, new.reason, new.created_by, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.execution_unit_id, old.unit_attempt_id, old.live_session_id,
        old.schema_version, old.owner_type, old.owner_id, old.control_epoch,
        old.fencing_token, old.reason, old.created_by, old.created_at
      ) then
        raise exception 'ControlLease immutable authority cannot change';
      end if;
      if new.updated_at <> transaction_timestamp() then
        raise exception 'ControlLease transition is invalid';
      end if;
      if old.state = 'ACTIVE' and new.state = 'ACTIVE' then
        if new.expires_at <= old.expires_at or new.released_at is not null then
          raise exception 'ControlLease heartbeat must only extend expiry';
        end if;
      elsif (
        (old.state = 'ACTIVE' and new.state in ('REVOKING', 'EXPIRED', 'RELEASED'))
        or (old.state = 'REVOKING' and new.state in ('EXPIRED', 'RELEASED'))
      ) then
        if new.expires_at <> old.expires_at then
          raise exception 'ControlLease transition cannot change expiry';
        end if;
      else
        raise exception 'ControlLease transition is invalid';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_live_control_command_update()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.task_run_id,
        new.execution_unit_id, new.unit_attempt_id, new.live_session_id,
        new.schema_version, new.command_type, new.client_mutation_id,
        new.reason, new.requested_ttl_sec, new.expected_control_epoch,
        new.accepted_session_revision, new.requested_by, new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.task_run_id,
        old.execution_unit_id, old.unit_attempt_id, old.live_session_id,
        old.schema_version, old.command_type, old.client_mutation_id,
        old.reason, old.requested_ttl_sec, old.expected_control_epoch,
        old.accepted_session_revision, old.requested_by, old.created_at
      ) or old.status <> 'PENDING'
        or new.status not in ('APPLIED', 'REJECTED')
        or new.updated_at <> transaction_timestamp()
      then
        raise exception 'LiveControlCommand transition is invalid';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_live_action_grant_update()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.grant_id, new.tenant_id, new.project_id, new.task_run_id,
        new.execution_unit_id, new.unit_attempt_id, new.live_session_id,
        new.control_lease_id, new.schema_version, new.action_id,
        new.proposal_digest, new.browser_session_id, new.page_id,
        new.page_revision, new.control_epoch, new.fencing_token,
        new.owner_type, new.owner_id, new.allowed_adapter, new.expires_at,
        new.max_executions, new.policy_digest, new.created_at
      ) is distinct from row(
        old.grant_id, old.tenant_id, old.project_id, old.task_run_id,
        old.execution_unit_id, old.unit_attempt_id, old.live_session_id,
        old.control_lease_id, old.schema_version, old.action_id,
        old.proposal_digest, old.browser_session_id, old.page_id,
        old.page_revision, old.control_epoch, old.fencing_token,
        old.owner_type, old.owner_id, old.allowed_adapter, old.expires_at,
        old.max_executions, old.policy_digest, old.created_at
      ) or not (
        (old.state = 'ISSUED' and new.state in ('CONSUMED', 'REVOKED'))
        or (old.state = 'CONSUMED' and new.state = 'COMPLETED')
      ) then
        raise exception 'LiveActionGrant transition is invalid';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.prevent_live_control_fact_mutation()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    begin
      raise exception 'live control fact cannot be deleted or rewritten';
    end;
    $$
    """,
    """
    create function atlas.guard_live_control_result_influence()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    begin
      if new.execution_influence = 'AUTONOMOUS' and exists (
        select 1
        from atlas.live_session session
        where session.unit_attempt_id = new.unit_attempt_id
          and session.tenant_id = new.tenant_id
          and session.project_id = new.project_id
          and session.human_influenced
      ) then
        raise exception 'human-influenced execution cannot be sealed as AUTONOMOUS';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger live_session_guard_update
      before update on atlas.live_session
      for each row execute function atlas.guard_live_session_update()
    """,
    """
    create trigger live_session_prevent_delete
      before delete on atlas.live_session
      for each row execute function atlas.prevent_live_control_fact_mutation()
    """,
    """
    create trigger control_lease_guard_update
      before update on atlas.control_lease
      for each row execute function atlas.guard_control_lease_update()
    """,
    """
    create trigger control_lease_prevent_delete
      before delete on atlas.control_lease
      for each row execute function atlas.prevent_live_control_fact_mutation()
    """,
    """
    create trigger live_control_command_guard_update
      before update on atlas.live_control_command
      for each row execute function atlas.guard_live_control_command_update()
    """,
    """
    create trigger live_control_command_prevent_delete
      before delete on atlas.live_control_command
      for each row execute function atlas.prevent_live_control_fact_mutation()
    """,
    """
    create trigger live_action_grant_guard_update
      before update on atlas.live_action_grant
      for each row execute function atlas.guard_live_action_grant_update()
    """,
    """
    create trigger live_action_grant_prevent_delete
      before delete on atlas.live_action_grant
      for each row execute function atlas.prevent_live_control_fact_mutation()
    """,
    """
    create trigger live_control_event_prevent_mutation
      before update or delete on atlas.live_control_event
      for each row execute function atlas.prevent_live_control_fact_mutation()
    """,
    """
    create trigger unit_attempt_result_fact_live_influence_guard
      before insert on atlas.unit_attempt_result_fact
      for each row execute function atlas.guard_live_control_result_influence()
    """,
    "alter table atlas.live_session enable row level security",
    "alter table atlas.live_session force row level security",
    "alter table atlas.control_lease enable row level security",
    "alter table atlas.control_lease force row level security",
    "alter table atlas.live_control_command enable row level security",
    "alter table atlas.live_control_command force row level security",
    "alter table atlas.live_action_grant enable row level security",
    "alter table atlas.live_action_grant force row level security",
    "alter table atlas.live_control_event enable row level security",
    "alter table atlas.live_control_event force row level security",
    """
    create policy live_session_tenant_isolation on atlas.live_session for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy control_lease_tenant_isolation on atlas.control_lease for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy live_control_command_tenant_isolation
      on atlas.live_control_command for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy live_action_grant_tenant_isolation
      on atlas.live_action_grant for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy live_control_event_tenant_isolation
      on atlas.live_control_event for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.live_session from atlas_app",
    "revoke all on atlas.control_lease from atlas_app",
    "revoke all on atlas.live_control_command from atlas_app",
    "revoke all on atlas.live_action_grant from atlas_app",
    "revoke all on atlas.live_control_event from atlas_app",
    "grant select, insert on atlas.live_session to atlas_app",
    """
    grant update (
      state, control_epoch, fencing_token, browser_revision,
      human_influenced, revision, updated_at, closed_at
    ) on atlas.live_session to atlas_app
    """,
    "grant select, insert on atlas.control_lease to atlas_app",
    """
    grant update (state, expires_at, updated_at, released_at)
      on atlas.control_lease to atlas_app
    """,
    "grant select, insert on atlas.live_control_command to atlas_app",
    """
    grant update (
      status, updated_at, applied_at, resulting_control_epoch,
      resulting_fencing_token, checkpoint_digest
    ) on atlas.live_control_command to atlas_app
    """,
    "grant select, insert on atlas.live_action_grant to atlas_app",
    """
    grant update (
      state, consumed_at, completed_at, revoked_at, receipt_id,
      execution_status, resulting_page_revision
    ) on atlas.live_action_grant to atlas_app
    """,
    "grant select, insert on atlas.live_control_event to atlas_app",
    "revoke all on function atlas.guard_live_session_update() from public",
    "revoke all on function atlas.guard_control_lease_update() from public",
    "revoke all on function atlas.guard_live_control_command_update() from public",
    "revoke all on function atlas.guard_live_action_grant_update() from public",
    "revoke all on function atlas.prevent_live_control_fact_mutation() from public",
    "revoke all on function atlas.guard_live_control_result_influence() from public",
)

DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.live_session limit 1)
        or exists (select 1 from atlas.control_lease limit 1)
        or exists (select 1 from atlas.live_control_command limit 1)
        or exists (select 1 from atlas.live_action_grant limit 1)
        or exists (select 1 from atlas.live_control_event limit 1)
      then
        raise exception 'cannot downgrade while live-control facts exist';
      end if;
    end;
    $$
    """,
    """
    drop trigger if exists unit_attempt_result_fact_live_influence_guard
      on atlas.unit_attempt_result_fact
    """,
    "drop table if exists atlas.live_control_event",
    "drop table if exists atlas.live_action_grant",
    "drop table if exists atlas.live_control_command",
    "drop table if exists atlas.control_lease",
    "drop table if exists atlas.live_session",
    "drop function if exists atlas.guard_live_control_result_influence()",
    "drop function if exists atlas.prevent_live_control_fact_mutation()",
    "drop function if exists atlas.guard_live_action_grant_update()",
    "drop function if exists atlas.guard_live_control_command_update()",
    "drop function if exists atlas.guard_control_lease_update()",
    "drop function if exists atlas.guard_live_session_update()",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
