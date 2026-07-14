"""建立 ConnectorInstallation 与实际 Capability 投影。

Revision ID: 20260713_0007
Revises: 20260713_0006
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_0007"
down_revision: str | None = "20260713_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create function atlas.valid_provider_capabilities(capabilities text[])
    returns boolean
    language sql
    immutable
    set search_path = pg_catalog, atlas
    as $$
      select cardinality(capabilities) between 1 and 64
        and array_position(capabilities, null) is null
        and count(*) = count(distinct capability)
        and coalesce(bool_and(capability in (
          'account.discover', 'account.read', 'account.provision',
          'auth.password', 'auth.oauth2', 'auth.oidc', 'auth.saml_sso',
          'auth.mfa.totp', 'auth.manual_bootstrap'
        )), false)
      from unnest(capabilities) as item(capability)
    $$
    """,
    """
    create table atlas.connector_installation (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      installation_key text not null,
      name text not null,
      adapter_key text not null,
      mode text not null,
      configuration_ref text not null,
      allowed_origins text[] not null,
      required_capabilities text[] not null,
      status text not null default 'DRAFT',
      health_state text,
      safe_message text,
      protocol_version text,
      implementation_version text,
      last_validated_at timestamptz,
      revision bigint not null default 1,
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      constraint connector_environment_scope_fk foreign key (
        environment_id, tenant_id, project_id
      ) references atlas.environment (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint connector_full_scope_unique unique (
        id, tenant_id, project_id, environment_id
      ),
      constraint connector_scope_key_unique unique (
        tenant_id, project_id, environment_id, installation_key
      ),
      constraint connector_key_format check (
        installation_key ~ '^[a-z][a-z0-9._-]{1,63}$'
      ),
      constraint connector_name_not_blank check (btrim(name) <> ''),
      constraint connector_adapter_key_format check (
        adapter_key ~ '^[a-z][a-z0-9-]{1,63}$'
      ),
      constraint connector_mode_valid check (mode in (
        'OBSERVE_ONLY', 'MANAGED_TEST_ACCOUNTS', 'HYBRID', 'FEDERATED_SESSION'
      )),
      constraint connector_configuration_ref_opaque check (
        configuration_ref ~ '^cfg_[A-Za-z0-9_-]{8,200}$'
      ),
      constraint connector_origins_valid check (
        cardinality(allowed_origins) between 1 and 16
        and atlas.valid_http_origins(allowed_origins)
      ),
      constraint connector_capabilities_valid check (
        atlas.valid_provider_capabilities(required_capabilities)
      ),
      constraint connector_observe_only_capabilities check (
        mode <> 'OBSERVE_ONLY'
        or required_capabilities <@ array['account.discover', 'account.read']::text[]
      ),
      constraint connector_status_valid check (
        status in ('DRAFT', 'ACTIVE', 'DEGRADED', 'DISABLED')
      ),
      constraint connector_health_state_valid check (
        health_state is null or health_state in ('HEALTHY', 'DEGRADED', 'UNAVAILABLE')
      ),
      constraint connector_safe_message_size check (
        safe_message is null or (
          btrim(safe_message) <> '' and octet_length(safe_message) <= 500
        )
      ),
      constraint connector_protocol_version_format check (
        protocol_version is null or protocol_version ~ '^[1-9][0-9]*[.][0-9]+$'
      ),
      constraint connector_implementation_version_format check (
        implementation_version is null
        or implementation_version ~ '^[0-9]+[.][0-9]+[.][0-9]+$'
      ),
      constraint connector_validation_metadata check (
        status not in ('ACTIVE', 'DEGRADED')
        or (
          health_state is not null
          and safe_message is not null
          and last_validated_at is not null
        )
      ),
      constraint connector_revision_positive check (revision > 0)
    )
    """,
    """
    create table atlas.connector_capability (
      connector_installation_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      name text not null,
      version text not null,
      mode text not null,
      observed_at timestamptz not null,
      primary key (connector_installation_id, name),
      constraint connector_capability_scope_fk foreign key (
        connector_installation_id, tenant_id, project_id, environment_id
      ) references atlas.connector_installation (
        id, tenant_id, project_id, environment_id
      ) on delete cascade,
      constraint connector_capability_name_valid check (
        atlas.valid_provider_capabilities(array[name])
      ),
      constraint connector_capability_version_format check (
        version ~ '^[1-9][0-9]*[.][0-9]+$'
      ),
      constraint connector_capability_mode_valid check (
        mode in ('native_api', 'browser', 'webhook', 'polling', 'manual')
      )
    )
    """,
    """
    alter table atlas.test_account
      add column connector_installation_id uuid,
      add constraint test_account_connector_scope_fk foreign key (
        connector_installation_id, tenant_id, project_id, environment_id
      ) references atlas.connector_installation (
        id, tenant_id, project_id, environment_id
      ) on delete restrict
    """,
    "drop index atlas.test_account_external_subject_unique",
    """
    create unique index test_account_external_subject_connector_unique
      on atlas.test_account (connector_installation_id, external_subject_id)
      where connector_installation_id is not null
        and external_subject_id is not null
    """,
    """
    create unique index test_account_external_subject_legacy_unique
      on atlas.test_account (
        tenant_id, project_id, environment_id, source, external_subject_id
      ) where connector_installation_id is null
        and external_subject_id is not null
    """,
    """
    create index test_account_connector_state_idx
      on atlas.test_account (
        connector_installation_id, lifecycle_status, health_status,
        operational_status, id
      ) where connector_installation_id is not null
    """,
    """
    alter table atlas.account_lease
      drop constraint account_lease_release_reason_valid,
      add constraint account_lease_release_reason_valid check (
        release_reason is null or release_reason in (
          'COMPLETED', 'CANCELLED', 'WORKER_SHUTDOWN', 'AUTH_FAILED',
          'CLEANUP_FAILED', 'MANUAL', 'TTL_EXPIRED', 'ACCOUNT_QUARANTINED',
          'ACCOUNT_SUSPENDED', 'ACCOUNT_RETIRED', 'POOL_DISABLED',
          'ROLE_DISABLED', 'ENVIRONMENT_DISABLED', 'CONNECTOR_DISABLED',
          'CONNECTOR_REBOUND'
        )
      )
    """,
    """
    alter table atlas.secret_grant
      drop constraint secret_grant_termination_reason_valid,
      add constraint secret_grant_termination_reason_valid check (
        termination_reason is null or termination_reason in (
          'REPLACED', 'LEASE_TERMINATED', 'EXPIRED', 'CREDENTIAL_UNAVAILABLE',
          'CONNECTOR_UNAVAILABLE'
        )
      )
    """,
    """
    update atlas.secret_grant
    set status = 'REVOKED', terminated_at = clock_timestamp(),
        termination_reason = 'CONNECTOR_UNAVAILABLE', revision = revision + 1
    where status = 'ISSUED'
    """,
    """
    alter table atlas.secret_grant
      add column connector_installation_id uuid,
      add constraint secret_grant_connector_scope_fk foreign key (
        connector_installation_id, tenant_id, project_id, environment_id
      ) references atlas.connector_installation (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      add constraint secret_grant_issued_connector_required check (
        status <> 'ISSUED' or connector_installation_id is not null
      )
    """,
    """
    create index secret_grant_connector_status_idx
      on atlas.secret_grant (connector_installation_id, status, expires_at)
      where connector_installation_id is not null
    """,
    """
    create function atlas.enforce_connector_environment_policy()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      environment_kind text;
      environment_origins text[];
    begin
      select kind, allowed_origins
      into environment_kind, environment_origins
      from atlas.environment
      where id = new.environment_id
        and tenant_id = new.tenant_id
        and project_id = new.project_id;
      if not found then
        return new;
      end if;
      if not new.allowed_origins <@ environment_origins then
        raise exception 'connector origins must be a subset of environment origins';
      end if;
      if environment_kind = 'PRODUCTION' and new.mode <> 'OBSERVE_ONLY' then
        raise exception 'production connectors must use observe-only mode';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_connector_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.installation_key, new.adapter_key
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.installation_key, old.adapter_key
      ) then
        raise exception 'connector identity and scope are immutable';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'connector revision must increase by one';
      end if;
      if row(
        new.mode, new.configuration_ref, new.allowed_origins,
        new.required_capabilities
      ) is distinct from row(
        old.mode, old.configuration_ref, old.allowed_origins,
        old.required_capabilities
      ) and new.status not in ('DRAFT', 'DISABLED') then
        raise exception 'connector configuration changes require revalidation';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_environment_connector_origins()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if new.allowed_origins is distinct from old.allowed_origins
         and exists (
           select 1
           from atlas.connector_installation connector
           where connector.environment_id = new.id
             and not connector.allowed_origins <@ new.allowed_origins
         ) then
        raise exception 'environment origins still have connector dependencies';
      end if;
      return new;
    end;
    $$
    """,
    """
    create or replace function atlas.guard_secret_grant_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if old.status <> 'ISSUED' then
        raise exception 'terminal secret grant is immutable';
      end if;
      if new.status = 'ISSUED' then
        raise exception 'issued secret grant cannot be mutated';
      end if;
      if row(
        new.id, new.tenant_id, new.project_id, new.environment_id,
        new.lease_id, new.account_id, new.credential_binding_id,
        new.connector_installation_id, new.fencing_token, new.purpose,
        new.worker_identity, new.token_hash, new.allowed_origins,
        new.issued_at, new.expires_at
      ) is distinct from row(
        old.id, old.tenant_id, old.project_id, old.environment_id,
        old.lease_id, old.account_id, old.credential_binding_id,
        old.connector_installation_id, old.fencing_token, old.purpose,
        old.worker_identity, old.token_hash, old.allowed_origins,
        old.issued_at, old.expires_at
      ) then
        raise exception 'secret grant scope and token hash are immutable';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'secret grant revision must increase by one';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.revoke_secret_grants_for_connector()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if old.status = 'ACTIVE' and new.status <> 'ACTIVE' then
        update atlas.secret_grant
        set status = 'REVOKED', terminated_at = clock_timestamp(),
            termination_reason = 'CONNECTOR_UNAVAILABLE',
            revision = revision + 1
        where connector_installation_id = new.id and status = 'ISSUED';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger connector_environment_policy
      before insert or update on atlas.connector_installation
      for each row execute function atlas.enforce_connector_environment_policy()
    """,
    """
    create trigger environment_connector_origin_guard
      before update of allowed_origins on atlas.environment
      for each row execute function atlas.guard_environment_connector_origins()
    """,
    """
    create trigger connector_guard_update
      before update on atlas.connector_installation
      for each row execute function atlas.guard_connector_update()
    """,
    """
    create trigger connector_set_updated_at
      before update on atlas.connector_installation
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger connector_revoke_secret_grants
      after update of status on atlas.connector_installation
      for each row execute function atlas.revoke_secret_grants_for_connector()
    """,
    """
    create index connector_environment_status_idx
      on atlas.connector_installation (
        tenant_id, project_id, environment_id, status, created_at desc, id desc
      )
    """,
    """
    create index connector_adapter_status_idx
      on atlas.connector_installation (adapter_key, status, id)
    """,
    "alter table atlas.connector_installation enable row level security",
    "alter table atlas.connector_installation force row level security",
    "alter table atlas.connector_capability enable row level security",
    "alter table atlas.connector_capability force row level security",
    """
    create policy connector_installation_tenant_isolation
      on atlas.connector_installation
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    create policy connector_capability_tenant_isolation
      on atlas.connector_capability
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    """
    grant select, insert, update, delete
      on atlas.connector_installation to atlas_app
    """,
    """
    grant select, insert, update, delete
      on atlas.connector_capability to atlas_app
    """,
)


def upgrade() -> None:
    """创建 Connector 权威记录、能力投影、账号关联和 Grant 约束。"""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """移除 Connector 结构并恢复 0006 的 Grant 与账号约束。"""

    op.execute(
        "drop trigger if exists environment_connector_origin_guard "
        "on atlas.environment"
    )
    op.execute(
        "drop trigger if exists connector_revoke_secret_grants "
        "on atlas.connector_installation"
    )
    op.execute(
        "drop trigger if exists connector_set_updated_at "
        "on atlas.connector_installation"
    )
    op.execute(
        "drop trigger if exists connector_guard_update "
        "on atlas.connector_installation"
    )
    op.execute(
        "drop trigger if exists connector_environment_policy "
        "on atlas.connector_installation"
    )
    op.execute("drop function if exists atlas.revoke_secret_grants_for_connector()")
    op.execute("drop function if exists atlas.guard_connector_update()")
    op.execute("drop function if exists atlas.guard_environment_connector_origins()")
    op.execute("drop function if exists atlas.enforce_connector_environment_policy()")

    op.execute(
        "update atlas.account_lease set release_reason = 'ENVIRONMENT_DISABLED' "
        "where release_reason in ('CONNECTOR_DISABLED', 'CONNECTOR_REBOUND')"
    )
    op.execute(
        "alter table atlas.account_lease "
        "drop constraint if exists account_lease_release_reason_valid, "
        "add constraint account_lease_release_reason_valid check ("
        "release_reason is null or release_reason in ("
        "'COMPLETED', 'CANCELLED', 'WORKER_SHUTDOWN', 'AUTH_FAILED', "
        "'CLEANUP_FAILED', 'MANUAL', 'TTL_EXPIRED', 'ACCOUNT_QUARANTINED', "
        "'ACCOUNT_SUSPENDED', 'ACCOUNT_RETIRED', 'POOL_DISABLED', "
        "'ROLE_DISABLED', 'ENVIRONMENT_DISABLED'))"
    )

    op.execute("drop trigger if exists secret_grant_guard_update on atlas.secret_grant")
    op.execute(
        "update atlas.secret_grant set termination_reason = 'CREDENTIAL_UNAVAILABLE' "
        "where termination_reason = 'CONNECTOR_UNAVAILABLE'"
    )
    op.execute(
        "alter table atlas.secret_grant "
        "drop constraint if exists secret_grant_issued_connector_required, "
        "drop constraint if exists secret_grant_connector_scope_fk, "
        "drop constraint if exists secret_grant_termination_reason_valid"
    )
    op.execute("drop index if exists atlas.secret_grant_connector_status_idx")
    op.execute(
        "alter table atlas.secret_grant "
        "drop column if exists connector_installation_id, "
        "add constraint secret_grant_termination_reason_valid check ("
        "termination_reason is null or termination_reason in ("
        "'REPLACED', 'LEASE_TERMINATED', 'EXPIRED', 'CREDENTIAL_UNAVAILABLE'))"
    )
    op.execute(
        """
        create or replace function atlas.guard_secret_grant_update()
        returns trigger
        language plpgsql
        set search_path = pg_catalog, atlas
        as $$
        begin
          if old.status <> 'ISSUED' then
            raise exception 'terminal secret grant is immutable';
          end if;
          if new.status = 'ISSUED' then
            raise exception 'issued secret grant cannot be mutated';
          end if;
          if row(
            new.id, new.tenant_id, new.project_id, new.environment_id,
            new.lease_id, new.account_id, new.credential_binding_id,
            new.fencing_token, new.purpose, new.worker_identity,
            new.token_hash, new.allowed_origins, new.issued_at, new.expires_at
          ) is distinct from row(
            old.id, old.tenant_id, old.project_id, old.environment_id,
            old.lease_id, old.account_id, old.credential_binding_id,
            old.fencing_token, old.purpose, old.worker_identity,
            old.token_hash, old.allowed_origins, old.issued_at, old.expires_at
          ) then
            raise exception 'secret grant scope and token hash are immutable';
          end if;
          if new.revision <> old.revision + 1 then
            raise exception 'secret grant revision must increase by one';
          end if;
          return new;
        end;
        $$
        """
    )
    op.execute(
        "create trigger secret_grant_guard_update before update "
        "on atlas.secret_grant for each row "
        "execute function atlas.guard_secret_grant_update()"
    )

    op.execute("drop index if exists atlas.test_account_connector_state_idx")
    op.execute(
        "drop index if exists atlas.test_account_external_subject_connector_unique"
    )
    op.execute(
        "drop index if exists atlas.test_account_external_subject_legacy_unique"
    )
    op.execute(
        "alter table atlas.test_account "
        "drop constraint if exists test_account_connector_scope_fk, "
        "drop column if exists connector_installation_id"
    )
    op.execute(
        "create unique index test_account_external_subject_unique "
        "on atlas.test_account ("
        "tenant_id, project_id, environment_id, source, external_subject_id) "
        "where external_subject_id is not null"
    )
    op.execute("drop table if exists atlas.connector_capability")
    op.execute("drop table if exists atlas.connector_installation")
    op.execute("drop function if exists atlas.valid_provider_capabilities(text[])")
