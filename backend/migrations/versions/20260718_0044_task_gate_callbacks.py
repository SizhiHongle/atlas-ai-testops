# ruff: noqa: E501
"""Add signed Task Gate callback delivery intents.

Revision ID: 20260718_0044
Revises: 20260718_0043
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0044"
down_revision: str | None = "20260718_0043"
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
        raise exception 'Task Gate callback function owner must bypass row-level security'
          using errcode = '42501';
      end if;
      if not exists (
        select 1
        from pg_catalog.pg_roles role
        where role.rolname = 'atlas_dispatcher'
          and role.rolcanlogin
          and not role.rolsuper
          and not role.rolbypassrls
      ) then
        raise exception 'atlas_dispatcher callback role is not safely provisioned'
          using errcode = '42704';
      end if;
    end;
    $$
    """,
    """
    create table atlas.task_gate_callback_intent (
      event_id uuid primary key,
      task_gate_decision_id uuid not null,
      task_gate_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      manifest_hash text not null,
      gate_decision text not null,
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
      response_status_code smallint,
      delivered_at timestamptz,
      failed_at timestamptz,
      created_at timestamptz not null,
      constraint task_gate_callback_decision_unique unique (
        task_gate_decision_id
      ),
      constraint task_gate_callback_decision_scope_fk foreign key (
        task_gate_decision_id, task_gate_id, task_run_id, tenant_id, project_id
      ) references atlas.task_gate_decision (
        id, task_gate_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint task_gate_callback_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id
      ) references atlas.task_run (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_gate_callback_content_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and gate_decision in ('ACCEPTED', 'REJECTED', 'INCONCLUSIVE')
      ),
      constraint task_gate_callback_delivery_valid check (
        status in ('PENDING', 'CLAIMED', 'RETRY_WAIT', 'DELIVERED', 'FAILED')
        and dispatch_attempts >= 0
        and dispatch_revision >= 0
        and isfinite(created_at)
        and isfinite(available_at)
        and (
          response_status_code is null
          or response_status_code between 100 and 599
        )
        and (
          (
            status = 'PENDING'
            and available_at = created_at
            and claim_token is null and claimed_by is null
            and claimed_at is null and claim_expires_at is null
            and dispatch_attempts = 0 and dispatch_revision = 0
            and last_error_code is null and last_error_at is null
            and response_status_code is null
            and delivered_at is null and failed_at is null
          )
          or (
            status = 'CLAIMED'
            and claim_token is not null
            and claimed_by ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
            and claimed_at is not null and claim_expires_at > claimed_at
            and dispatch_attempts > 0 and dispatch_revision > 0
            and last_error_code is null and last_error_at is null
            and response_status_code is null
            and delivered_at is null and failed_at is null
          )
          or (
            status = 'RETRY_WAIT'
            and claim_token is null and claimed_by is null
            and claimed_at is null and claim_expires_at is null
            and dispatch_attempts > 0 and dispatch_revision > 0
            and last_error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
            and last_error_at is not null and available_at > last_error_at
            and delivered_at is null and failed_at is null
          )
          or (
            status = 'DELIVERED'
            and claim_token is null and claimed_by is null
            and claimed_at is null and claim_expires_at is null
            and dispatch_attempts > 0 and dispatch_revision > 0
            and last_error_code is null and last_error_at is null
            and response_status_code between 200 and 299
            and delivered_at is not null and failed_at is null
          )
          or (
            status = 'FAILED'
            and claim_token is null and claimed_by is null
            and claimed_at is null and claim_expires_at is null
            and dispatch_attempts > 0 and dispatch_revision > 0
            and last_error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'
            and last_error_at is not null and failed_at = last_error_at
            and delivered_at is null
          )
        )
      )
    )
    """,
    """
    create index task_gate_callback_ready_idx
      on atlas.task_gate_callback_intent (
        available_at, created_at, event_id
      )
      where status in ('PENDING', 'RETRY_WAIT', 'CLAIMED')
    """,
    """
    create index task_gate_callback_task_idx
      on atlas.task_gate_callback_intent (
        tenant_id, project_id, task_run_id, created_at desc, event_id desc
      )
    """,
    """
    create function atlas.guard_task_gate_callback_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      decision atlas.task_gate_decision%rowtype;
      stored_run atlas.task_run%rowtype;
    begin
      if atlas.current_tenant_id() is null
        or atlas.current_actor_id() is null
        or new.tenant_id is distinct from atlas.current_tenant_id()
      then
        raise exception 'Task Gate callback creation requires exact tenant and actor context'
          using errcode = '42501';
      end if;
      select * into decision
      from atlas.task_gate_decision value
      where value.id = new.task_gate_decision_id
        and value.task_gate_id = new.task_gate_id
        and value.task_run_id = new.task_run_id
        and value.tenant_id = new.tenant_id
        and value.project_id = new.project_id
      for share;
      select * into stored_run
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
      for share;
      if decision.id is null
        or stored_run.id is null
        or decision.evaluated_by <> atlas.current_actor_id()
        or new.manifest_hash <> stored_run.manifest_hash
        or new.gate_decision <> decision.decision
        or new.created_at <> decision.evaluated_at
        or new.available_at <> new.created_at
        or new.status <> 'PENDING'
        or new.claim_token is not null
        or new.claimed_by is not null
        or new.claimed_at is not null
        or new.claim_expires_at is not null
        or new.dispatch_attempts <> 0
        or new.dispatch_revision <> 0
        or new.last_error_code is not null
        or new.last_error_at is not null
        or new.response_status_code is not null
        or new.delivered_at is not null
        or new.failed_at is not null
      then
        raise exception 'Task Gate callback must exactly mirror its Gate decision'
          using errcode = '55000';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_task_gate_callback_transition()
    returns trigger
    language plpgsql
    security invoker
    set search_path = pg_catalog, atlas
    as $$
    begin
      if tg_op = 'DELETE' then
        raise exception 'Task Gate callback intents cannot be deleted'
          using errcode = '55000';
      end if;
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task Gate callback transitions require atlas_dispatcher'
          using errcode = '42501';
      end if;
      if new.event_id is distinct from old.event_id
        or new.task_gate_decision_id is distinct from old.task_gate_decision_id
        or new.task_gate_id is distinct from old.task_gate_id
        or new.tenant_id is distinct from old.tenant_id
        or new.project_id is distinct from old.project_id
        or new.task_run_id is distinct from old.task_run_id
        or new.manifest_hash is distinct from old.manifest_hash
        or new.gate_decision is distinct from old.gate_decision
        or new.created_at is distinct from old.created_at
      then
        raise exception 'Task Gate callback semantic content is immutable'
          using errcode = '55000';
      end if;
      if not (
        (
          new.status = 'CLAIMED'
          and old.status in ('PENDING', 'RETRY_WAIT', 'CLAIMED')
          and new.dispatch_attempts = old.dispatch_attempts + 1
          and new.dispatch_revision = old.dispatch_revision + 1
        )
        or (
          old.status = 'CLAIMED'
          and new.status in ('RETRY_WAIT', 'DELIVERED', 'FAILED')
          and new.dispatch_attempts = old.dispatch_attempts
          and new.dispatch_revision = old.dispatch_revision + 1
        )
      ) then
        raise exception 'Task Gate callback transition is invalid'
          using errcode = '55000';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_gate_callback_guard_insert
      before insert on atlas.task_gate_callback_intent
      for each row execute function atlas.guard_task_gate_callback_insert()
    """,
    """
    create trigger task_gate_callback_guard_transition
      before update or delete on atlas.task_gate_callback_intent
      for each row execute function atlas.guard_task_gate_callback_transition()
    """,
    "alter table atlas.task_gate_callback_intent enable row level security",
    "alter table atlas.task_gate_callback_intent force row level security",
    """
    create policy task_gate_callback_tenant_isolation
      on atlas.task_gate_callback_intent
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create function atlas.claim_task_gate_callback_intents(
      p_claimed_by text,
      p_limit integer,
      p_lease_seconds integer
    ) returns table (
      event_id uuid,
      tenant_id uuid,
      project_id uuid,
      task_run_id uuid,
      manifest_hash text,
      gate_decision text,
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
      observed_at timestamptz := transaction_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task Gate callback claim requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_claimed_by !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
        or p_limit not between 1 and 100
        or p_lease_seconds not between 5 and 300
      then
        raise exception 'Task Gate callback claim parameters are invalid'
          using errcode = '22023';
      end if;
      return query
      with candidates as (
        select intent.event_id
        from atlas.task_gate_callback_intent intent
        where (
          intent.status in ('PENDING', 'RETRY_WAIT')
          and intent.available_at <= observed_at
        ) or (
          intent.status = 'CLAIMED'
          and intent.claim_expires_at <= observed_at
        )
        order by intent.available_at, intent.created_at, intent.event_id
        limit p_limit
        for update skip locked
      ),
      claimed as (
        update atlas.task_gate_callback_intent intent
        set status = 'CLAIMED',
            available_at = greatest(intent.available_at, observed_at),
            claim_token = gen_random_uuid(),
            claimed_by = p_claimed_by,
            claimed_at = observed_at,
            claim_expires_at = observed_at + make_interval(secs => p_lease_seconds),
            dispatch_attempts = intent.dispatch_attempts + 1,
            dispatch_revision = intent.dispatch_revision + 1,
            last_error_code = null,
            last_error_at = null,
            response_status_code = null
        from candidates
        where intent.event_id = candidates.event_id
        returning intent.*
      )
      select claimed.event_id, claimed.tenant_id, claimed.project_id,
             claimed.task_run_id, claimed.manifest_hash, claimed.gate_decision,
             claimed.claim_token, claimed.dispatch_revision,
             claimed.dispatch_attempts, claimed.claim_expires_at,
             claimed.created_at
      from claimed
      order by claimed.available_at, claimed.created_at, claimed.event_id;
    end;
    $$
    """,
    """
    create function atlas.mark_task_gate_callback_delivered(
      p_event_id uuid,
      p_claim_token uuid,
      p_dispatch_revision bigint,
      p_response_status_code integer
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      observed_at timestamptz := transaction_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task Gate callback completion requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_response_status_code not between 200 and 299 then
        raise exception 'Task Gate callback success status is invalid'
          using errcode = '22023';
      end if;
      update atlas.task_gate_callback_intent
      set status = 'DELIVERED',
          claim_token = null, claimed_by = null,
          claimed_at = null, claim_expires_at = null,
          dispatch_revision = dispatch_revision + 1,
          last_error_code = null, last_error_at = null,
          response_status_code = p_response_status_code,
          delivered_at = observed_at
      where event_id = p_event_id
        and status = 'CLAIMED'
        and claim_token = p_claim_token
        and dispatch_revision = p_dispatch_revision
        and claim_expires_at > observed_at;
      return found;
    end;
    $$
    """,
    """
    create function atlas.retry_task_gate_callback_intent(
      p_event_id uuid,
      p_claim_token uuid,
      p_dispatch_revision bigint,
      p_error_code text,
      p_response_status_code integer,
      p_retry_delay_ms integer
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      observed_at timestamptz := transaction_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task Gate callback retry requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$'
        or (
          p_response_status_code is not null
          and p_response_status_code not between 100 and 599
        )
        or p_retry_delay_ms not between 100 and 3600000
      then
        raise exception 'Task Gate callback retry parameters are invalid'
          using errcode = '22023';
      end if;
      update atlas.task_gate_callback_intent
      set status = 'RETRY_WAIT',
          available_at = observed_at
            + make_interval(secs => p_retry_delay_ms::double precision / 1000),
          claim_token = null, claimed_by = null,
          claimed_at = null, claim_expires_at = null,
          dispatch_revision = dispatch_revision + 1,
          last_error_code = p_error_code,
          last_error_at = observed_at,
          response_status_code = p_response_status_code
      where event_id = p_event_id
        and status = 'CLAIMED'
        and claim_token = p_claim_token
        and dispatch_revision = p_dispatch_revision
        and claim_expires_at > observed_at;
      return found;
    end;
    $$
    """,
    """
    create function atlas.fail_task_gate_callback_intent(
      p_event_id uuid,
      p_claim_token uuid,
      p_dispatch_revision bigint,
      p_error_code text,
      p_response_status_code integer
    ) returns boolean
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      observed_at timestamptz := transaction_timestamp();
    begin
      if session_user <> 'atlas_dispatcher' then
        raise exception 'Task Gate callback failure requires atlas_dispatcher'
          using errcode = '42501';
      end if;
      if p_error_code !~ '^[A-Z][A-Z0-9_]{0,63}$'
        or (
          p_response_status_code is not null
          and p_response_status_code not between 100 and 599
        )
      then
        raise exception 'Task Gate callback failure parameters are invalid'
          using errcode = '22023';
      end if;
      update atlas.task_gate_callback_intent
      set status = 'FAILED',
          claim_token = null, claimed_by = null,
          claimed_at = null, claim_expires_at = null,
          dispatch_revision = dispatch_revision + 1,
          last_error_code = p_error_code,
          last_error_at = observed_at,
          response_status_code = p_response_status_code,
          failed_at = observed_at
      where event_id = p_event_id
        and status = 'CLAIMED'
        and claim_token = p_claim_token
        and dispatch_revision = p_dispatch_revision
        and claim_expires_at > observed_at;
      return found;
    end;
    $$
    """,
    "revoke all on atlas.task_gate_callback_intent from atlas_app, atlas_dispatcher",
    "grant select, insert on atlas.task_gate_callback_intent to atlas_app",
    "revoke all on function atlas.guard_task_gate_callback_insert() from public, atlas_app, atlas_dispatcher",
    "revoke all on function atlas.guard_task_gate_callback_transition() from public, atlas_app, atlas_dispatcher",
    "revoke all on function atlas.claim_task_gate_callback_intents(text, integer, integer) from public, atlas_app",
    "revoke all on function atlas.mark_task_gate_callback_delivered(uuid, uuid, bigint, integer) from public, atlas_app",
    "revoke all on function atlas.retry_task_gate_callback_intent(uuid, uuid, bigint, text, integer, integer) from public, atlas_app",
    "revoke all on function atlas.fail_task_gate_callback_intent(uuid, uuid, bigint, text, integer) from public, atlas_app",
    "grant execute on function atlas.claim_task_gate_callback_intents(text, integer, integer) to atlas_dispatcher",
    "grant execute on function atlas.mark_task_gate_callback_delivered(uuid, uuid, bigint, integer) to atlas_dispatcher",
    "grant execute on function atlas.retry_task_gate_callback_intent(uuid, uuid, bigint, text, integer, integer) to atlas_dispatcher",
    "grant execute on function atlas.fail_task_gate_callback_intent(uuid, uuid, bigint, text, integer) to atlas_dispatcher",
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.task_gate_callback_intent) then
        raise exception 'cannot downgrade while Task Gate callback facts exist';
      end if;
    end;
    $$
    """,
    "revoke execute on function atlas.fail_task_gate_callback_intent(uuid, uuid, bigint, text, integer) from atlas_dispatcher",
    "revoke execute on function atlas.retry_task_gate_callback_intent(uuid, uuid, bigint, text, integer, integer) from atlas_dispatcher",
    "revoke execute on function atlas.mark_task_gate_callback_delivered(uuid, uuid, bigint, integer) from atlas_dispatcher",
    "revoke execute on function atlas.claim_task_gate_callback_intents(text, integer, integer) from atlas_dispatcher",
    "drop table atlas.task_gate_callback_intent",
    "drop function atlas.fail_task_gate_callback_intent(uuid, uuid, bigint, text, integer)",
    "drop function atlas.retry_task_gate_callback_intent(uuid, uuid, bigint, text, integer, integer)",
    "drop function atlas.mark_task_gate_callback_delivered(uuid, uuid, bigint, integer)",
    "drop function atlas.claim_task_gate_callback_intents(text, integer, integer)",
    "drop function atlas.guard_task_gate_callback_transition()",
    "drop function atlas.guard_task_gate_callback_insert()",
)


def upgrade() -> None:
    """Create exact Gate callback intents and dispatcher-only delivery APIs."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Refuse loss after any callback fact exists."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
