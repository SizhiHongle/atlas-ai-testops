"""Create test account health checks and state transition facts.

Revision ID: 20260713_0008
Revises: 20260713_0007
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_0008"
down_revision: str | None = "20260713_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    alter table atlas.account_pool
      add column health_failure_threshold integer not null default 3,
      add column health_retry_cooldown_seconds integer not null default 300,
      add constraint account_pool_health_failure_threshold_range check (
        health_failure_threshold between 1 and 20
      ),
      add constraint account_pool_health_retry_cooldown_range check (
        health_retry_cooldown_seconds between 0 and 86400
      )
    """,
    """
    alter table atlas.test_account
      add column consecutive_health_failures integer not null default 0,
      add column last_health_checked_at timestamptz,
      add column last_health_succeeded_at timestamptz,
      add column identity_fingerprint text,
      add constraint test_account_health_failures_nonnegative check (
        consecutive_health_failures >= 0
      ),
      add constraint test_account_identity_fingerprint_format check (
        identity_fingerprint is null
        or identity_fingerprint ~ '^sha256:[a-f0-9]{64}$'
      ),
      add constraint test_account_health_timestamps_order check (
        last_health_succeeded_at is null
        or (
          last_health_checked_at is not null
          and last_health_succeeded_at <= last_health_checked_at
        )
      )
    """,
    """
    update atlas.test_account
    set health_status = 'UNKNOWN', operational_status = 'VERIFYING',
        cooldown_until = null, revision = revision + 1
    where health_status = 'HEALTHY'
    """,
    """
    alter table atlas.test_account
      add constraint test_account_healthy_verification_required check (
        health_status <> 'HEALTHY'
        or (
          identity_fingerprint is not null
          and last_health_checked_at is not null
          and last_health_succeeded_at is not null
          and consecutive_health_failures = 0
        )
      )
    """,
    """
    alter table atlas.credential_binding
      add constraint credential_binding_health_scope_unique unique (
        id, tenant_id, project_id, environment_id, account_id
      )
    """,
    """
    create table atlas.account_health_check (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      account_id uuid not null,
      connector_installation_id uuid not null,
      credential_binding_id uuid not null,
      trigger text not null,
      status text not null default 'RUNNING',
      origin text not null,
      role_key text not null,
      account_revision bigint not null,
      connector_revision bigint not null,
      credential_revision bigint not null,
      result_health_status text,
      failure_code text,
      retryable boolean,
      safe_summary text not null,
      actor_id uuid,
      request_id text not null,
      started_at timestamptz not null,
      finished_at timestamptz,
      expires_at timestamptz not null,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint account_health_check_account_scope_fk foreign key (
        account_id, tenant_id, project_id, environment_id
      ) references atlas.test_account (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint account_health_check_connector_scope_fk foreign key (
        connector_installation_id, tenant_id, project_id, environment_id
      ) references atlas.connector_installation (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint account_health_check_credential_scope_fk foreign key (
        credential_binding_id, tenant_id, project_id, environment_id, account_id
      ) references atlas.credential_binding (
        id, tenant_id, project_id, environment_id, account_id
      ) on delete restrict,
      constraint account_health_check_full_scope_unique unique (
        id, tenant_id, project_id, environment_id, account_id
      ),
      constraint account_health_check_trigger_valid check (trigger in (
        'MANUAL', 'RESTORE', 'LEASE_EXPIRED', 'LEASE_AUTH', 'AUTH_FAILURE',
        'CREDENTIAL_CHANGED', 'RECONCILE'
      )),
      constraint account_health_check_status_valid check (
        status in ('RUNNING', 'SUCCEEDED', 'FAILED', 'STALE')
      ),
      constraint account_health_check_origin_valid check (
        atlas.valid_http_origins(array[origin])
      ),
      constraint account_health_check_role_key_format check (
        role_key ~ '^[a-z][a-z0-9._-]{1,63}$'
      ),
      constraint account_health_check_snapshot_revisions_positive check (
        account_revision > 0
        and connector_revision > 0
        and credential_revision > 0
      ),
      constraint account_health_check_result_health_valid check (
        result_health_status is null
        or result_health_status in ('HEALTHY', 'DEGRADED', 'QUARANTINED')
      ),
      constraint account_health_check_failure_code_valid check (
        failure_code is null or failure_code in (
          'AUTHENTICATION_FAILED', 'CREDENTIAL_EXPIRED', 'ACCOUNT_LOCKED',
          'IDENTITY_MISMATCH', 'ROLE_DRIFT', 'RATE_LIMITED',
          'PROVIDER_UNAVAILABLE', 'NETWORK_TIMEOUT', 'MANUAL_ACTION_REQUIRED',
          'CAPABILITY_UNSUPPORTED', 'SECRET_UNAVAILABLE', 'STALE_SNAPSHOT',
          'INTERNAL_ERROR'
        )
      ),
      constraint account_health_check_safe_summary_valid check (
        btrim(safe_summary) <> '' and octet_length(safe_summary) <= 500
      ),
      constraint account_health_check_request_id_valid check (
        btrim(request_id) <> '' and octet_length(request_id) <= 200
      ),
      constraint account_health_check_time_window_valid check (
        expires_at > started_at
        and (finished_at is null or finished_at >= started_at)
      ),
      constraint account_health_check_terminal_metadata check (
        (
          status = 'RUNNING'
          and finished_at is null
          and result_health_status is null
          and failure_code is null
          and retryable is null
        ) or (
          status = 'SUCCEEDED'
          and finished_at is not null
          and result_health_status = 'HEALTHY'
          and failure_code is null
          and retryable = false
        ) or (
          status = 'FAILED'
          and finished_at is not null
          and result_health_status in ('DEGRADED', 'QUARANTINED')
          and failure_code is not null
          and retryable is not null
        ) or (
          status = 'STALE'
          and finished_at is not null
          and result_health_status is null
          and failure_code = 'STALE_SNAPSHOT'
          and retryable = true
        )
      ),
      constraint account_health_check_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.account_state_transition (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      account_id uuid not null,
      health_check_id uuid,
      reason text not null,
      from_lifecycle_status text not null,
      to_lifecycle_status text not null,
      from_health_status text not null,
      to_health_status text not null,
      from_operational_status text not null,
      to_operational_status text not null,
      from_sync_status text not null,
      to_sync_status text not null,
      from_cooldown_until timestamptz,
      to_cooldown_until timestamptz,
      safe_summary text not null,
      actor_id uuid,
      request_id text not null,
      occurred_at timestamptz not null,
      constraint account_state_transition_account_scope_fk foreign key (
        account_id, tenant_id, project_id, environment_id
      ) references atlas.test_account (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint account_state_transition_health_check_scope_fk foreign key (
        health_check_id, tenant_id, project_id, environment_id, account_id
      ) references atlas.account_health_check (
        id, tenant_id, project_id, environment_id, account_id
      ) on delete restrict,
      constraint account_state_transition_reason_valid check (reason in (
        'VERIFICATION_STARTED', 'VERIFICATION_SUCCEEDED',
        'VERIFICATION_FAILED', 'FAILURE_THRESHOLD_REACHED',
        'IDENTITY_MISMATCH', 'ROLE_DRIFT', 'ACCOUNT_LOCKED',
        'MANUAL_QUARANTINE', 'MANUAL_RESTORE', 'LEASE_EXPIRED',
        'LEASE_RELEASED', 'CLEANUP_FAILED', 'MANAGEMENT_REVOCATION',
        'RUNTIME_AUTH_SUCCEEDED', 'RUNTIME_AUTH_FAILED'
      )),
      constraint account_state_transition_lifecycle_valid check (
        from_lifecycle_status in (
          'DRAFT', 'PROVISIONING', 'ACTIVE', 'SUSPENDED', 'RETIRING', 'RETIRED'
        ) and to_lifecycle_status in (
          'DRAFT', 'PROVISIONING', 'ACTIVE', 'SUSPENDED', 'RETIRING', 'RETIRED'
        )
      ),
      constraint account_state_transition_health_valid check (
        from_health_status in ('UNKNOWN', 'HEALTHY', 'DEGRADED', 'QUARANTINED')
        and to_health_status in ('UNKNOWN', 'HEALTHY', 'DEGRADED', 'QUARANTINED')
      ),
      constraint account_state_transition_operational_valid check (
        from_operational_status in ('VERIFYING', 'READY', 'COOLDOWN', 'CLEANUP_FAILED')
        and to_operational_status in ('VERIFYING', 'READY', 'COOLDOWN', 'CLEANUP_FAILED')
      ),
      constraint account_state_transition_sync_valid check (
        from_sync_status in ('NOT_APPLICABLE', 'IN_SYNC', 'CONFLICT', 'TOMBSTONED')
        and to_sync_status in ('NOT_APPLICABLE', 'IN_SYNC', 'CONFLICT', 'TOMBSTONED')
      ),
      constraint account_state_transition_changed check (
        row(
          from_lifecycle_status, from_health_status, from_operational_status,
          from_sync_status, from_cooldown_until
        ) is distinct from row(
          to_lifecycle_status, to_health_status, to_operational_status,
          to_sync_status, to_cooldown_until
        )
      ),
      constraint account_state_transition_summary_valid check (
        btrim(safe_summary) <> '' and octet_length(safe_summary) <= 500
      ),
      constraint account_state_transition_request_id_valid check (
        btrim(request_id) <> '' and octet_length(request_id) <= 200
      )
    )
    """,
    """
    create unique index account_health_check_single_running_idx
      on atlas.account_health_check (account_id)
      where status = 'RUNNING'
    """,
    """
    create index account_health_check_account_history_idx
      on atlas.account_health_check (
        tenant_id, project_id, account_id, created_at desc, id desc
      )
    """,
    """
    create index account_health_check_connector_status_idx
      on atlas.account_health_check (
        connector_installation_id, status, expires_at, id
      )
    """,
    """
    create index account_state_transition_account_history_idx
      on atlas.account_state_transition (
        tenant_id, project_id, account_id, occurred_at desc, id desc
      )
    """,
    """
    create index test_account_health_retry_idx
      on atlas.test_account (
        tenant_id, project_id, environment_id, health_status,
        operational_status, cooldown_until
      )
      where health_status <> 'HEALTHY' or operational_status <> 'READY'
    """,
    """
    create function atlas.guard_account_health_check_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if old.status <> 'RUNNING' then
        raise exception 'terminal account health check is immutable';
      end if;
      if new.status = 'RUNNING' then
        raise exception 'running account health check cannot be mutated';
      end if;
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.account_id, new.connector_installation_id,
        new.credential_binding_id, new.trigger, new.origin, new.role_key,
        new.account_revision, new.connector_revision, new.credential_revision,
        new.actor_id, new.request_id, new.started_at, new.expires_at,
        new.created_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.account_id, old.connector_installation_id,
        old.credential_binding_id, old.trigger, old.origin, old.role_key,
        old.account_revision, old.connector_revision, old.credential_revision,
        old.actor_id, old.request_id, old.started_at, old.expires_at,
        old.created_at
      ) then
        raise exception 'account health check scope and snapshot are immutable';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'account health check revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.reject_account_state_transition_mutation()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      raise exception 'account state transition is immutable';
    end;
    $$
    """,
    """
    create trigger account_health_check_guard_update
      before update on atlas.account_health_check
      for each row execute function atlas.guard_account_health_check_update()
    """,
    """
    create trigger account_health_check_set_updated_at
      before update on atlas.account_health_check
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger account_state_transition_guard_mutation
      before update or delete on atlas.account_state_transition
      for each row execute function atlas.reject_account_state_transition_mutation()
    """,
    "alter table atlas.account_health_check enable row level security",
    "alter table atlas.account_health_check force row level security",
    "alter table atlas.account_state_transition enable row level security",
    "alter table atlas.account_state_transition force row level security",
    """
    create policy account_health_check_tenant_isolation
      on atlas.account_health_check
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy account_state_transition_tenant_isolation
      on atlas.account_state_transition
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    "grant select, insert, update on atlas.account_health_check to atlas_app",
    "grant select, insert on atlas.account_state_transition to atlas_app",
)


def upgrade() -> None:
    """Create health policies, immutable facts, and tenant RLS."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove health facts and restore the 0007 identity schema."""

    op.execute(
        "drop trigger if exists account_state_transition_guard_mutation "
        "on atlas.account_state_transition"
    )
    op.execute(
        "drop trigger if exists account_health_check_set_updated_at on atlas.account_health_check"
    )
    op.execute(
        "drop trigger if exists account_health_check_guard_update on atlas.account_health_check"
    )
    op.execute("drop function if exists atlas.reject_account_state_transition_mutation()")
    op.execute("drop function if exists atlas.guard_account_health_check_update()")
    op.execute("drop index if exists atlas.test_account_health_retry_idx")
    op.execute("drop table if exists atlas.account_state_transition")
    op.execute("drop table if exists atlas.account_health_check")
    op.execute(
        "alter table atlas.credential_binding "
        "drop constraint if exists credential_binding_health_scope_unique"
    )
    op.execute(
        "alter table atlas.test_account "
        "drop constraint if exists test_account_healthy_verification_required, "
        "drop constraint if exists test_account_health_timestamps_order, "
        "drop constraint if exists test_account_identity_fingerprint_format, "
        "drop constraint if exists test_account_health_failures_nonnegative, "
        "drop column if exists identity_fingerprint, "
        "drop column if exists last_health_succeeded_at, "
        "drop column if exists last_health_checked_at, "
        "drop column if exists consecutive_health_failures"
    )
    op.execute(
        "alter table atlas.account_pool "
        "drop constraint if exists account_pool_health_retry_cooldown_range, "
        "drop constraint if exists account_pool_health_failure_threshold_range, "
        "drop column if exists health_retry_cooldown_seconds, "
        "drop column if exists health_failure_threshold"
    )
