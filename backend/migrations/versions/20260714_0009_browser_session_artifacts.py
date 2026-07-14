"""Create encrypted browser session artifact metadata and manual auth tickets.

Revision ID: 20260714_0009
Revises: 20260713_0008
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260714_0009"
down_revision: str | None = "20260713_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create function atlas.valid_session_auth_strength(methods text[])
    returns boolean
    language sql
    immutable
    set search_path = pg_catalog, atlas
    as $$
      select cardinality(methods) <= 6
        and array_position(methods, null) is null
        and count(*) = count(distinct method)
        and coalesce(bool_and(method in (
          'PASSWORD', 'OAUTH2', 'OIDC', 'SAML_SSO', 'TOTP', 'MANUAL_BOOTSTRAP'
        )), true)
      from unnest(methods) as item(method)
    $$
    """,
    """
    create table atlas.browser_session_artifact (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      lease_id uuid not null,
      account_id uuid not null,
      connector_installation_id uuid not null,
      credential_binding_id uuid not null,
      lease_fence bigint not null,
      worker_identity text not null,
      browser_context_ref text not null,
      allowed_origins text[] not null,
      auth_strength text[] not null default '{}',
      status text not null default 'CREATING',
      object_ref text not null,
      object_digest text,
      object_size_bytes bigint,
      key_version text,
      format_version text not null default 'playwright-storage-state/v1',
      refreshable boolean not null default false,
      account_revision bigint not null,
      connector_revision bigint not null,
      credential_revision bigint not null,
      safe_summary text not null,
      failure_code text,
      created_at timestamptz not null,
      attempt_expires_at timestamptz not null,
      ready_at timestamptz,
      expires_at timestamptz not null,
      terminated_at timestamptz,
      termination_reason text,
      cleanup_claimed_at timestamptz,
      cleanup_worker_identity text,
      destroyed_at timestamptz,
      revision bigint not null default 1,
      updated_at timestamptz not null default clock_timestamp(),
      constraint browser_session_artifact_lease_scope_fk foreign key (
        lease_id, account_id, tenant_id, project_id, environment_id,
        lease_fence, worker_identity
      ) references atlas.account_lease (
        id, account_id, tenant_id, project_id, environment_id,
        fencing_token, worker_id
      ) on delete restrict,
      constraint browser_session_artifact_account_scope_fk foreign key (
        account_id, tenant_id, project_id, environment_id
      ) references atlas.test_account (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint browser_session_artifact_connector_scope_fk foreign key (
        connector_installation_id, tenant_id, project_id, environment_id
      ) references atlas.connector_installation (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint browser_session_artifact_credential_scope_fk foreign key (
        credential_binding_id, tenant_id, project_id, environment_id, account_id
      ) references atlas.credential_binding (
        id, tenant_id, project_id, environment_id, account_id
      ) on delete restrict,
      constraint browser_session_artifact_context_ref_unique unique (
        browser_context_ref
      ),
      constraint browser_session_artifact_full_scope_unique unique (
        id, tenant_id, project_id, environment_id, lease_id
      ),
      constraint browser_session_artifact_fence_positive check (lease_fence > 0),
      constraint browser_session_artifact_worker_format check (
        worker_identity ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$'
      ),
      constraint browser_session_artifact_context_ref_format check (
        browser_context_ref ~ '^bctx_[A-Za-z0-9_-]{32,200}$'
      ),
      constraint browser_session_artifact_origins_valid check (
        cardinality(allowed_origins) between 1 and 16
        and atlas.valid_http_origins(allowed_origins)
      ),
      constraint browser_session_artifact_auth_strength_valid check (
        atlas.valid_session_auth_strength(auth_strength)
      ),
      constraint browser_session_artifact_status_valid check (
        status in (
          'CREATING', 'READY', 'REVOKED', 'EXPIRED', 'FAILED',
          'DESTROYING', 'DESTROYED'
        )
      ),
      constraint browser_session_artifact_object_ref_opaque check (
        object_ref ~ '^session-vault://[A-Za-z0-9][A-Za-z0-9/_.=-]+$'
        and octet_length(object_ref) between 24 and 528
      ),
      constraint browser_session_artifact_object_digest_format check (
        object_digest is null or object_digest ~ '^sha256:[a-f0-9]{64}$'
      ),
      constraint browser_session_artifact_object_size_positive check (
        object_size_bytes is null or object_size_bytes > 0
      ),
      constraint browser_session_artifact_key_version_format check (
        key_version is null
        or key_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$'
      ),
      constraint browser_session_artifact_object_metadata_complete check (
        (
          object_digest is null
          and object_size_bytes is null
          and key_version is null
        ) or (
          object_digest is not null
          and object_size_bytes is not null
          and key_version is not null
        )
      ),
      constraint browser_session_artifact_format_version_valid check (
        format_version = 'playwright-storage-state/v1'
      ),
      constraint browser_session_artifact_not_refreshable check (not refreshable),
      constraint browser_session_artifact_snapshot_revisions_positive check (
        account_revision > 0
        and connector_revision > 0
        and credential_revision > 0
      ),
      constraint browser_session_artifact_safe_summary_valid check (
        btrim(safe_summary) <> '' and octet_length(safe_summary) <= 500
      ),
      constraint browser_session_artifact_failure_code_valid check (
        failure_code is null or failure_code in (
          'AUTHENTICATION_FAILED', 'CREDENTIAL_EXPIRED', 'ACCOUNT_LOCKED',
          'IDENTITY_MISMATCH', 'ROLE_DRIFT', 'RATE_LIMITED',
          'PROVIDER_UNAVAILABLE', 'NETWORK_TIMEOUT', 'MANUAL_ACTION_REQUIRED',
          'SECRET_UNAVAILABLE',
          'STORAGE_UNAVAILABLE', 'CAPABILITY_UNSUPPORTED', 'STALE_SNAPSHOT',
          'INTERNAL_ERROR'
        )
      ),
      constraint browser_session_artifact_termination_reason_valid check (
        termination_reason is null or termination_reason in (
          'LEASE_TERMINATED', 'ACCOUNT_UNAVAILABLE', 'CREDENTIAL_UNAVAILABLE',
          'CONNECTOR_UNAVAILABLE', 'SUPERSEDED', 'TTL_EXPIRED',
          'CREATION_FAILED', 'STALE_SNAPSHOT', 'MANUAL'
        )
      ),
      constraint browser_session_artifact_time_window_valid check (
        created_at < attempt_expires_at
        and attempt_expires_at <= expires_at
        and (ready_at is null or ready_at >= created_at)
        and (terminated_at is null or terminated_at >= created_at)
        and (cleanup_claimed_at is null or cleanup_claimed_at >= created_at)
        and (
          destroyed_at is null
          or (terminated_at is not null and destroyed_at >= terminated_at)
        )
      ),
      constraint browser_session_artifact_cleanup_worker_format check (
        cleanup_worker_identity is null
        or cleanup_worker_identity ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$'
      ),
      constraint browser_session_artifact_state_metadata check (
        (
          status = 'CREATING'
          and cardinality(auth_strength) = 0
          and object_digest is null
          and ready_at is null
          and failure_code is null
          and terminated_at is null
          and termination_reason is null
          and cleanup_claimed_at is null
          and cleanup_worker_identity is null
          and destroyed_at is null
        ) or (
          status = 'READY'
          and cardinality(auth_strength) between 1 and 6
          and object_digest is not null
          and ready_at is not null
          and failure_code is null
          and terminated_at is null
          and termination_reason is null
          and cleanup_claimed_at is null
          and cleanup_worker_identity is null
          and destroyed_at is null
        ) or (
          status = 'FAILED'
          and failure_code is not null
          and ready_at is null
          and terminated_at is not null
          and termination_reason in ('CREATION_FAILED', 'STALE_SNAPSHOT')
          and cleanup_claimed_at is null
          and cleanup_worker_identity is null
          and destroyed_at is null
        ) or (
          status in ('REVOKED', 'EXPIRED')
          and failure_code is null
          and terminated_at is not null
          and termination_reason is not null
          and cleanup_claimed_at is null
          and cleanup_worker_identity is null
          and destroyed_at is null
          and (
            (ready_at is null and object_digest is null)
            or (
              ready_at is not null
              and object_digest is not null
              and cardinality(auth_strength) between 1 and 6
            )
          )
        ) or (
          status = 'DESTROYING'
          and terminated_at is not null
          and termination_reason is not null
          and cleanup_claimed_at is not null
          and cleanup_worker_identity is not null
          and destroyed_at is null
        ) or (
          status = 'DESTROYED'
          and terminated_at is not null
          and termination_reason is not null
          and cleanup_claimed_at is not null
          and cleanup_worker_identity is not null
          and destroyed_at is not null
        )
      ),
      constraint browser_session_artifact_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.auth_action_ticket (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      lease_id uuid not null,
      account_id uuid not null,
      connector_installation_id uuid not null,
      lease_fence bigint not null,
      worker_identity text not null,
      allowed_origins text[] not null,
      auth_method text not null,
      reason text not null,
      status text not null default 'OPEN',
      safe_reason text not null,
      created_at timestamptz not null,
      expires_at timestamptz not null,
      completed_at timestamptz,
      terminated_at timestamptz,
      revision bigint not null default 1,
      updated_at timestamptz not null default clock_timestamp(),
      constraint auth_action_ticket_lease_scope_fk foreign key (
        lease_id, account_id, tenant_id, project_id, environment_id,
        lease_fence, worker_identity
      ) references atlas.account_lease (
        id, account_id, tenant_id, project_id, environment_id,
        fencing_token, worker_id
      ) on delete restrict,
      constraint auth_action_ticket_account_scope_fk foreign key (
        account_id, tenant_id, project_id, environment_id
      ) references atlas.test_account (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint auth_action_ticket_connector_scope_fk foreign key (
        connector_installation_id, tenant_id, project_id, environment_id
      ) references atlas.connector_installation (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint auth_action_ticket_full_scope_unique unique (
        id, tenant_id, project_id, environment_id, lease_id
      ),
      constraint auth_action_ticket_fence_positive check (lease_fence > 0),
      constraint auth_action_ticket_worker_format check (
        worker_identity ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$'
      ),
      constraint auth_action_ticket_origins_valid check (
        cardinality(allowed_origins) between 1 and 16
        and atlas.valid_http_origins(allowed_origins)
      ),
      constraint auth_action_ticket_auth_method_valid check (
        auth_method in (
          'PASSWORD', 'OAUTH2', 'OIDC', 'SAML_SSO', 'TOTP', 'MANUAL_BOOTSTRAP'
        )
      ),
      constraint auth_action_ticket_reason_valid check (
        reason in (
          'AUTH_METHOD_REQUIRES_MANUAL', 'MFA_REQUIRED',
          'DEVICE_TRUST_REQUIRED', 'PROVIDER_CHALLENGE'
        )
      ),
      constraint auth_action_ticket_status_valid check (
        status in ('OPEN', 'COMPLETED', 'CANCELLED', 'EXPIRED')
      ),
      constraint auth_action_ticket_safe_reason_valid check (
        btrim(safe_reason) <> '' and octet_length(safe_reason) <= 500
      ),
      constraint auth_action_ticket_time_window_valid check (
        expires_at > created_at
        and (completed_at is null or completed_at >= created_at)
        and (terminated_at is null or terminated_at >= created_at)
      ),
      constraint auth_action_ticket_state_metadata check (
        (
          status = 'OPEN'
          and completed_at is null
          and terminated_at is null
        ) or (
          status = 'COMPLETED'
          and completed_at is not null
          and terminated_at is null
        ) or (
          status in ('CANCELLED', 'EXPIRED')
          and completed_at is null
          and terminated_at is not null
        )
      ),
      constraint auth_action_ticket_revision_positive check (revision > 0)
    )
    """,
    """
    create unique index browser_session_artifact_one_live_per_lease
      on atlas.browser_session_artifact (lease_id)
      where status in ('CREATING', 'READY')
    """,
    """
    create index browser_session_artifact_expiry_idx
      on atlas.browser_session_artifact (tenant_id, expires_at, id)
      where status in (
        'CREATING', 'READY', 'REVOKED', 'EXPIRED', 'FAILED', 'DESTROYING'
      )
    """,
    """
    create index browser_session_artifact_lease_history_idx
      on atlas.browser_session_artifact (lease_id, created_at desc, id desc)
    """,
    """
    create index browser_session_artifact_account_status_idx
      on atlas.browser_session_artifact (account_id, status, expires_at, id)
    """,
    """
    create unique index auth_action_ticket_one_open_per_lease
      on atlas.auth_action_ticket (lease_id)
      where status = 'OPEN'
    """,
    """
    create index auth_action_ticket_expiry_idx
      on atlas.auth_action_ticket (tenant_id, expires_at, id)
      where status = 'OPEN'
    """,
    """
    create function atlas.guard_browser_session_artifact_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if not (
        (old.status = 'CREATING' and new.status in (
          'READY', 'FAILED', 'REVOKED', 'EXPIRED'
        ))
        or (old.status = 'READY' and new.status in ('REVOKED', 'EXPIRED'))
        or (
          old.status in ('REVOKED', 'EXPIRED', 'FAILED')
          and new.status = 'DESTROYING'
        )
        or (old.status = 'DESTROYING' and new.status in ('DESTROYING', 'DESTROYED'))
      ) then
        raise exception 'invalid browser session artifact transition';
      end if;
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.lease_id, new.account_id, new.connector_installation_id,
        new.credential_binding_id, new.lease_fence, new.worker_identity,
        new.browser_context_ref, new.allowed_origins, new.object_ref,
        new.format_version, new.refreshable, new.account_revision,
        new.connector_revision, new.credential_revision, new.created_at,
        new.attempt_expires_at, new.expires_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.lease_id, old.account_id, old.connector_installation_id,
        old.credential_binding_id, old.lease_fence, old.worker_identity,
        old.browser_context_ref, old.allowed_origins, old.object_ref,
        old.format_version, old.refreshable, old.account_revision,
        old.connector_revision, old.credential_revision, old.created_at,
        old.attempt_expires_at, old.expires_at
      ) then
        raise exception 'browser session artifact scope and snapshot are immutable';
      end if;
      if old.status <> 'CREATING' and row(
        new.auth_strength, new.object_digest, new.object_size_bytes,
        new.key_version, new.ready_at, new.failure_code, new.safe_summary
      ) is distinct from row(
        old.auth_strength, old.object_digest, old.object_size_bytes,
        old.key_version, old.ready_at, old.failure_code, old.safe_summary
      ) then
        raise exception 'sealed browser session artifact metadata is immutable';
      end if;
      if old.terminated_at is not null and row(
        new.terminated_at, new.termination_reason
      ) is distinct from row(
        old.terminated_at, old.termination_reason
      ) then
        raise exception 'browser session artifact termination is immutable';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'browser session artifact revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_auth_action_ticket_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if old.status <> 'OPEN' or new.status not in ('COMPLETED', 'CANCELLED', 'EXPIRED') then
        raise exception 'invalid auth action ticket transition';
      end if;
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.lease_id, new.account_id, new.connector_installation_id,
        new.lease_fence, new.worker_identity, new.allowed_origins,
        new.auth_method, new.reason, new.safe_reason, new.created_at,
        new.expires_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.lease_id, old.account_id, old.connector_installation_id,
        old.lease_fence, old.worker_identity, old.allowed_origins,
        old.auth_method, old.reason, old.safe_reason, old.created_at,
        old.expires_at
      ) then
        raise exception 'auth action ticket scope is immutable';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'auth action ticket revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.revoke_sessions_for_lease()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if old.status = 'ACTIVE' and new.status <> 'ACTIVE' then
        update atlas.browser_session_artifact
        set status = case when new.status = 'EXPIRED' then 'EXPIRED' else 'REVOKED' end,
            terminated_at = clock_timestamp(),
            termination_reason = case
              when new.status = 'EXPIRED' then 'TTL_EXPIRED'
              else 'LEASE_TERMINATED'
            end,
            revision = revision + 1
        where lease_id = new.id and status in ('CREATING', 'READY');
        update atlas.auth_action_ticket
        set status = case when new.status = 'EXPIRED' then 'EXPIRED' else 'CANCELLED' end,
            terminated_at = clock_timestamp(), revision = revision + 1
        where lease_id = new.id and status = 'OPEN';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.revoke_sessions_for_credential()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if new.status <> 'ACTIVE'
         or new.secret_ref is distinct from old.secret_ref
         or new.secret_version is distinct from old.secret_version then
        update atlas.browser_session_artifact
        set status = 'REVOKED', terminated_at = clock_timestamp(),
            termination_reason = 'CREDENTIAL_UNAVAILABLE', revision = revision + 1
        where credential_binding_id = new.id and status in ('CREATING', 'READY');
        update atlas.auth_action_ticket
        set status = 'CANCELLED', terminated_at = clock_timestamp(),
            revision = revision + 1
        where account_id = new.account_id and status = 'OPEN';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.revoke_sessions_for_connector()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if new.status <> 'ACTIVE'
         or row(
           new.mode, new.configuration_ref, new.allowed_origins,
           new.required_capabilities
         ) is distinct from row(
           old.mode, old.configuration_ref, old.allowed_origins,
           old.required_capabilities
         ) then
        update atlas.browser_session_artifact
        set status = 'REVOKED', terminated_at = clock_timestamp(),
            termination_reason = 'CONNECTOR_UNAVAILABLE', revision = revision + 1
        where connector_installation_id = new.id
          and status in ('CREATING', 'READY');
        update atlas.auth_action_ticket
        set status = 'CANCELLED', terminated_at = clock_timestamp(),
            revision = revision + 1
        where connector_installation_id = new.id and status = 'OPEN';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.revoke_sessions_for_account()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.lifecycle_status, new.health_status, new.operational_status,
        new.sync_status
      ) is distinct from row(
        old.lifecycle_status, old.health_status, old.operational_status,
        old.sync_status
      ) and (
        new.lifecycle_status <> 'ACTIVE'
        or new.health_status <> 'HEALTHY'
        or new.operational_status <> 'READY'
        or new.sync_status in ('CONFLICT', 'TOMBSTONED')
      ) then
        update atlas.browser_session_artifact
        set status = 'REVOKED', terminated_at = clock_timestamp(),
            termination_reason = 'ACCOUNT_UNAVAILABLE', revision = revision + 1
        where account_id = new.id and status in ('CREATING', 'READY');
        update atlas.auth_action_ticket
        set status = 'CANCELLED', terminated_at = clock_timestamp(),
            revision = revision + 1
        where account_id = new.id and status = 'OPEN';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger browser_session_artifact_guard_update
      before update on atlas.browser_session_artifact
      for each row execute function atlas.guard_browser_session_artifact_update()
    """,
    """
    create trigger browser_session_artifact_set_updated_at
      before update on atlas.browser_session_artifact
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger auth_action_ticket_guard_update
      before update on atlas.auth_action_ticket
      for each row execute function atlas.guard_auth_action_ticket_update()
    """,
    """
    create trigger auth_action_ticket_set_updated_at
      before update on atlas.auth_action_ticket
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger account_lease_revoke_sessions
      after update of status on atlas.account_lease
      for each row execute function atlas.revoke_sessions_for_lease()
    """,
    """
    create trigger credential_revoke_sessions
      after update of status, secret_ref, secret_version on atlas.credential_binding
      for each row execute function atlas.revoke_sessions_for_credential()
    """,
    """
    create trigger connector_revoke_sessions
      after update of status, mode, configuration_ref, allowed_origins,
        required_capabilities on atlas.connector_installation
      for each row execute function atlas.revoke_sessions_for_connector()
    """,
    """
    create trigger account_revoke_sessions
      after update of lifecycle_status, health_status, operational_status,
        sync_status on atlas.test_account
      for each row execute function atlas.revoke_sessions_for_account()
    """,
    "alter table atlas.browser_session_artifact enable row level security",
    "alter table atlas.browser_session_artifact force row level security",
    "alter table atlas.auth_action_ticket enable row level security",
    "alter table atlas.auth_action_ticket force row level security",
    """
    create policy browser_session_artifact_tenant_isolation
      on atlas.browser_session_artifact
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy auth_action_ticket_tenant_isolation
      on atlas.auth_action_ticket
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    "grant select, insert, update on atlas.browser_session_artifact to atlas_app",
    "grant select, insert, update on atlas.auth_action_ticket to atlas_app",
)


def upgrade() -> None:
    """Create session metadata, revocation triggers, indexes, and tenant RLS."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove session artifacts and restore the 0008 identity schema."""

    op.execute("drop trigger if exists account_revoke_sessions on atlas.test_account")
    op.execute(
        "drop trigger if exists connector_revoke_sessions "
        "on atlas.connector_installation"
    )
    op.execute(
        "drop trigger if exists credential_revoke_sessions "
        "on atlas.credential_binding"
    )
    op.execute(
        "drop trigger if exists account_lease_revoke_sessions "
        "on atlas.account_lease"
    )
    op.execute("drop table if exists atlas.auth_action_ticket")
    op.execute("drop table if exists atlas.browser_session_artifact")
    op.execute("drop function if exists atlas.revoke_sessions_for_account()")
    op.execute("drop function if exists atlas.revoke_sessions_for_connector()")
    op.execute("drop function if exists atlas.revoke_sessions_for_credential()")
    op.execute("drop function if exists atlas.revoke_sessions_for_lease()")
    op.execute("drop function if exists atlas.guard_auth_action_ticket_update()")
    op.execute("drop function if exists atlas.guard_browser_session_artifact_update()")
    op.execute("drop function if exists atlas.valid_session_auth_strength(text[])")
