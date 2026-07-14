"""建立 Environment Origin 策略与一次性 Secret Grant 账本。

Revision ID: 20260713_0006
Revises: 20260713_0005
Create Date: 2026-07-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260713_0006"
down_revision: str | None = "20260713_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create function atlas.valid_http_origins(origins text[])
    returns boolean
    language sql
    immutable
    set search_path = pg_catalog, atlas
    as $$
      select cardinality(origins) <= 32
        and array_position(origins, null) is null
        and count(*) = count(distinct origin)
        and coalesce(bool_and(
          length(origin) between 8 and 2048
          and origin = lower(origin)
          and origin ~ (
            '^https?://' ||
            '([[][0-9a-f:.]+[]]|[a-z0-9]([a-z0-9.-]*[a-z0-9])?)' ||
            '(:[1-9][0-9]{0,4})?$'
          )
          and origin !~ '[.][.]'
          and origin !~ '://[.-]'
          and origin !~ '([.]-|-[.])'
          and origin !~ '[-.](:[0-9]+)?$'
          and origin !~ '^http://.*[:]80$'
          and origin !~ '^https://.*[:]443$'
          and coalesce(
            (substring(origin from ':([0-9]+)$'))::integer between 1 and 65535,
            true
          )
        ), true)
      from unnest(origins) as item(origin)
    $$
    """,
    """
    alter table atlas.environment
      add column allowed_origins text[] not null default '{}',
      add constraint environment_allowed_origins_valid
        check (atlas.valid_http_origins(allowed_origins))
    """,
    """
    create function atlas.production_origins_are_https(origins text[])
    returns boolean
    language sql
    immutable
    set search_path = pg_catalog, atlas
    as $$
      select coalesce(bool_and(origin ~ '^https://'), true)
      from unnest(origins) as item(origin)
    $$
    """,
    """
    alter table atlas.environment
      add constraint environment_production_origins_https check (
        kind <> 'PRODUCTION'
        or atlas.production_origins_are_https(allowed_origins)
      )
    """,
    """
    alter table atlas.account_lease
      add constraint account_lease_grant_scope_unique unique (
        id, account_id, tenant_id, project_id, environment_id,
        fencing_token, worker_id
      )
    """,
    """
    alter table atlas.credential_binding
      add constraint credential_binding_grant_scope_unique unique (
        id, account_id, tenant_id, project_id, environment_id, purpose
      )
    """,
    """
    create table atlas.secret_grant (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      lease_id uuid not null,
      account_id uuid not null,
      credential_binding_id uuid not null,
      fencing_token bigint not null,
      purpose text not null,
      worker_identity text not null,
      token_hash text not null,
      allowed_origins text[] not null,
      status text not null default 'ISSUED',
      issued_at timestamptz not null,
      expires_at timestamptz not null,
      redeemed_at timestamptz,
      terminated_at timestamptz,
      termination_reason text,
      revision bigint not null default 1,
      updated_at timestamptz not null default clock_timestamp(),
      constraint secret_grant_lease_scope_fk foreign key (
        lease_id, account_id, tenant_id, project_id, environment_id,
        fencing_token, worker_identity
      ) references atlas.account_lease (
        id, account_id, tenant_id, project_id, environment_id,
        fencing_token, worker_id
      ) on delete restrict,
      constraint secret_grant_credential_scope_fk foreign key (
        credential_binding_id, account_id, tenant_id, project_id,
        environment_id, purpose
      ) references atlas.credential_binding (
        id, account_id, tenant_id, project_id, environment_id, purpose
      ) on delete restrict,
      constraint secret_grant_token_hash_unique unique (token_hash),
      constraint secret_grant_token_hash_sha256 check (
        token_hash ~ '^[0-9a-f]{64}$'
      ),
      constraint secret_grant_fence_positive check (fencing_token > 0),
      constraint secret_grant_purpose_valid check (
        purpose in ('LOGIN', 'REFRESH_SESSION', 'ROTATE_CREDENTIAL')
      ),
      constraint secret_grant_worker_format check (
        worker_identity ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$'
      ),
      constraint secret_grant_origins_valid check (
        cardinality(allowed_origins) between 1 and 16
        and atlas.valid_http_origins(allowed_origins)
      ),
      constraint secret_grant_status_valid check (
        status in ('ISSUED', 'REDEEMED', 'REVOKED', 'EXPIRED')
      ),
      constraint secret_grant_expiry_valid check (expires_at > issued_at),
      constraint secret_grant_terminal_metadata check (
        (
          status = 'ISSUED'
          and redeemed_at is null
          and terminated_at is null
          and termination_reason is null
        ) or (
          status = 'REDEEMED'
          and redeemed_at is not null
          and terminated_at is null
          and termination_reason is null
        ) or (
          status in ('REVOKED', 'EXPIRED')
          and redeemed_at is null
          and terminated_at is not null
          and termination_reason is not null
        )
      ),
      constraint secret_grant_termination_reason_valid check (
        termination_reason is null or termination_reason in (
          'REPLACED', 'LEASE_TERMINATED', 'EXPIRED', 'CREDENTIAL_UNAVAILABLE'
        )
      ),
      constraint secret_grant_revision_positive check (revision > 0)
    )
    """,
    """
    create unique index secret_grant_one_issued_per_binding
      on atlas.secret_grant (
        lease_id, credential_binding_id, purpose, worker_identity
      ) where status = 'ISSUED'
    """,
    """
    create index secret_grant_expiry_idx
      on atlas.secret_grant (tenant_id, expires_at, id)
      where status = 'ISSUED'
    """,
    """
    create index secret_grant_lease_history_idx
      on atlas.secret_grant (lease_id, issued_at desc, id desc)
    """,
    """
    create function atlas.guard_secret_grant_update()
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
    """,
    """
    create function atlas.revoke_secret_grants_for_lease()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if old.status = 'ACTIVE' and new.status <> 'ACTIVE' then
        update atlas.secret_grant
        set status = 'REVOKED', terminated_at = clock_timestamp(),
            termination_reason = 'LEASE_TERMINATED', revision = revision + 1
        where lease_id = new.id and status = 'ISSUED';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.revoke_secret_grants_for_credential()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if old.status = 'ACTIVE' and new.status <> 'ACTIVE' then
        update atlas.secret_grant
        set status = 'REVOKED', terminated_at = clock_timestamp(),
            termination_reason = 'CREDENTIAL_UNAVAILABLE', revision = revision + 1
        where credential_binding_id = new.id and status = 'ISSUED';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger secret_grant_guard_update
      before update on atlas.secret_grant
      for each row execute function atlas.guard_secret_grant_update()
    """,
    """
    create trigger secret_grant_set_updated_at
      before update on atlas.secret_grant
      for each row execute function atlas.set_updated_at()
    """,
    """
    create trigger account_lease_revoke_secret_grants
      after update of status on atlas.account_lease
      for each row execute function atlas.revoke_secret_grants_for_lease()
    """,
    """
    create trigger credential_revoke_secret_grants
      after update of status on atlas.credential_binding
      for each row execute function atlas.revoke_secret_grants_for_credential()
    """,
    "alter table atlas.secret_grant enable row level security",
    "alter table atlas.secret_grant force row level security",
    """
    create policy secret_grant_tenant_isolation on atlas.secret_grant
      for all
      using (tenant_id = atlas.current_tenant_id())
      with check (tenant_id = atlas.current_tenant_id())
    """,
    "grant select, insert, update on atlas.secret_grant to atlas_app",
)


def upgrade() -> None:
    """创建 Origin Policy、一次性 Grant、撤销 Trigger 和 RLS。"""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """移除 Secret Grant，并恢复 P2-02 的 Environment 与 Lease 结构。"""

    op.execute(
        "drop trigger if exists credential_revoke_secret_grants "
        "on atlas.credential_binding"
    )
    op.execute(
        "drop trigger if exists account_lease_revoke_secret_grants "
        "on atlas.account_lease"
    )
    op.execute("drop table if exists atlas.secret_grant")
    op.execute("drop function if exists atlas.revoke_secret_grants_for_credential()")
    op.execute("drop function if exists atlas.revoke_secret_grants_for_lease()")
    op.execute("drop function if exists atlas.guard_secret_grant_update()")
    op.execute(
        "alter table atlas.credential_binding "
        "drop constraint if exists credential_binding_grant_scope_unique"
    )
    op.execute(
        "alter table atlas.account_lease "
        "drop constraint if exists account_lease_grant_scope_unique"
    )
    op.execute(
        "alter table atlas.environment "
        "drop constraint if exists environment_production_origins_https, "
        "drop constraint if exists environment_allowed_origins_valid, "
        "drop column if exists allowed_origins"
    )
    op.execute("drop function if exists atlas.production_origins_are_https(text[])")
    op.execute("drop function if exists atlas.valid_http_origins(text[])")
