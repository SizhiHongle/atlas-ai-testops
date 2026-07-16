"""Create scoped, bounded grants for private evidence object reads.

Revision ID: 20260715_0018
Revises: 20260715_0017
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260715_0018"
down_revision: str | None = "20260715_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    alter table atlas.evidence_artifact
      add constraint evidence_artifact_read_scope_unique unique (
        id, tenant_id, project_id, environment_id, debug_run_id,
        execution_contract_id
      )
    """,
    """
    alter table atlas.evidence_artifact
      add constraint evidence_artifact_object_ref_scope_valid check (
        object_ref ~ (
          '^evidence://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/tenants/'
          || replace(tenant_id::text, '-', '')
          || '/projects/' || replace(project_id::text, '-', '')
          || '/environments/' || replace(environment_id::text, '-', '')
          || '/debug-runs/' || replace(debug_run_id::text, '-', '')
          || '/contracts/' || replace(execution_contract_id::text, '-', '')
          || '/artifacts/' || replace(id::text, '-', '')
          || '\\.[a-z0-9]{1,10}$'
        )
      ) not valid
    """,
    """
    create unique index evidence_artifact_object_ref_unique
      on atlas.evidence_artifact (object_ref)
    """,
    """
    create index evidence_artifact_run_idx
      on atlas.evidence_artifact (debug_run_id, captured_at, id)
    """,
    """
    create table atlas.evidence_read_grant (
      id uuid primary key,
      token_hash text not null,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      debug_run_id uuid not null,
      execution_contract_id uuid not null,
      artifact_id uuid not null,
      issued_to_actor_id uuid not null,
      platform_session_id uuid,
      purpose text not null,
      max_reads smallint not null,
      read_count smallint not null default 0,
      created_at timestamptz not null,
      expires_at timestamptz not null,
      last_read_at timestamptz,
      revoked_at timestamptz,
      revision bigint not null default 1,
      constraint evidence_read_grant_token_hash_unique unique (token_hash),
      constraint evidence_read_grant_artifact_scope_fk foreign key (
        artifact_id, tenant_id, project_id, environment_id, debug_run_id,
        execution_contract_id
      ) references atlas.evidence_artifact (
        id, tenant_id, project_id, environment_id, debug_run_id,
        execution_contract_id
      ) on delete restrict,
      constraint evidence_read_grant_session_fk foreign key (
        platform_session_id
      ) references atlas.platform_session (id) on delete restrict,
      constraint evidence_read_grant_token_hash_valid check (
        token_hash ~ '^[0-9a-f]{64}$'
      ),
      constraint evidence_read_grant_purpose_valid check (
        purpose in ('INLINE', 'DOWNLOAD')
      ),
      constraint evidence_read_grant_reads_valid check (
        max_reads between 1 and 32
        and read_count between 0 and max_reads
      ),
      constraint evidence_read_grant_lifetime_valid check (
        expires_at > created_at
        and expires_at <= created_at + interval '120 seconds'
      ),
      constraint evidence_read_grant_read_metadata_valid check (
        (read_count = 0) = (last_read_at is null)
        and (
          last_read_at is null
          or (
            last_read_at >= created_at
            and last_read_at < expires_at
          )
        )
      ),
      constraint evidence_read_grant_revocation_valid check (
        revoked_at is null
        or (
          revoked_at >= created_at
          and (last_read_at is null or revoked_at >= last_read_at)
        )
      ),
      constraint evidence_read_grant_revision_valid check (revision > 0)
    )
    """,
    """
    create index evidence_read_grant_artifact_history_idx
      on atlas.evidence_read_grant (artifact_id, created_at desc, id desc)
    """,
    """
    create index evidence_read_grant_session_fk_idx
      on atlas.evidence_read_grant (platform_session_id)
      where platform_session_id is not null
    """,
    """
    create index evidence_read_grant_active_expiry_idx
      on atlas.evidence_read_grant (tenant_id, expires_at, id)
      where revoked_at is null and read_count < max_reads
    """,
    """
    create function atlas.guard_evidence_read_grant_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      artifact_scope_valid boolean;
      session_scope_valid boolean;
    begin
      if new.read_count <> 0
        or new.last_read_at is not null
        or new.revoked_at is not null
        or new.revision <> 1
      then
        raise exception 'evidence read grant must start unused and active';
      end if;

      select exists (
        select 1
        from atlas.evidence_artifact artifact
        join atlas.evidence_manifest manifest
          on manifest.execution_contract_id = artifact.execution_contract_id
         and manifest.debug_run_id = artifact.debug_run_id
         and manifest.tenant_id = artifact.tenant_id
         and manifest.project_id = artifact.project_id
         and manifest.environment_id = artifact.environment_id
        join atlas.debug_run run
          on run.id = artifact.debug_run_id
         and run.tenant_id = artifact.tenant_id
         and run.project_id = artifact.project_id
         and run.environment_id = artifact.environment_id
        where artifact.id = new.artifact_id
          and artifact.execution_contract_id = new.execution_contract_id
          and artifact.debug_run_id = new.debug_run_id
          and artifact.tenant_id = new.tenant_id
          and artifact.project_id = new.project_id
          and artifact.environment_id = new.environment_id
          and artifact.integrity = 'VERIFIED'
          and run.lifecycle = 'TERMINATED'
          and run.evidence_manifest_id = manifest.id
          and run.evidence_manifest_digest = manifest.manifest_digest
          and exists (
            select 1
            from jsonb_array_elements(manifest.manifest -> 'artifacts') item
            where item ->> 'id' = artifact.id::text
              and item ->> 'integrity' = 'VERIFIED'
          )
      ) into artifact_scope_valid;
      if not artifact_scope_valid then
        raise exception 'evidence read grant requires a finalized verified artifact';
      end if;

      if new.platform_session_id is not null then
        select exists (
          select 1
          from atlas.platform_session session
          where session.id = new.platform_session_id
            and session.user_id = new.issued_to_actor_id
            and session.tenant_id = new.tenant_id
            and session.project_id = new.project_id
            and session.revoked_at is null
            and session.idle_expires_at > new.created_at
            and session.absolute_expires_at > new.created_at
        ) into session_scope_valid;
        if not session_scope_valid then
          raise exception 'evidence read grant session scope is invalid';
        end if;
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_evidence_read_grant_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    begin
      if row(
        new.id, new.token_hash, new.tenant_id, new.project_id,
        new.environment_id, new.debug_run_id, new.execution_contract_id,
        new.artifact_id, new.issued_to_actor_id, new.platform_session_id,
        new.purpose, new.max_reads, new.created_at, new.expires_at
      ) is distinct from row(
        old.id, old.token_hash, old.tenant_id, old.project_id,
        old.environment_id, old.debug_run_id, old.execution_contract_id,
        old.artifact_id, old.issued_to_actor_id, old.platform_session_id,
        old.purpose, old.max_reads, old.created_at, old.expires_at
      ) then
        raise exception 'evidence read grant scope is immutable';
      end if;
      if new.revision <> old.revision + 1 then
        raise exception 'evidence read grant revision must increase by one';
      end if;
      if old.revoked_at is not null then
        raise exception 'revoked evidence read grant is immutable';
      end if;

      if new.revoked_at is not null then
        if new.read_count <> old.read_count
          or new.last_read_at is distinct from old.last_read_at
        then
          raise exception 'evidence read grant revocation cannot consume a read';
        end if;
      elsif new.read_count <> old.read_count + 1
        or new.last_read_at is null
        or new.last_read_at < coalesce(old.last_read_at, old.created_at)
      then
        raise exception 'evidence read grant must consume exactly one read';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger evidence_read_grant_guard_insert
      before insert on atlas.evidence_read_grant
      for each row execute function atlas.guard_evidence_read_grant_insert()
    """,
    """
    create trigger evidence_read_grant_guard_update
      before update on atlas.evidence_read_grant
      for each row execute function atlas.guard_evidence_read_grant_update()
    """,
    "alter table atlas.evidence_read_grant enable row level security",
    "alter table atlas.evidence_read_grant force row level security",
    """
    create policy evidence_read_grant_tenant_isolation
      on atlas.evidence_read_grant for all
      using (
        tenant_id = (select atlas.current_tenant_id())
        and issued_to_actor_id = (select atlas.current_actor_id())
      )
      with check (
        tenant_id = (select atlas.current_tenant_id())
        and issued_to_actor_id = (select atlas.current_actor_id())
      )
    """,
    "revoke all on atlas.evidence_read_grant from atlas_app",
    "grant select, insert on atlas.evidence_read_grant to atlas_app",
    """
    grant update (read_count, last_read_at, revoked_at, revision)
      on atlas.evidence_read_grant to atlas_app
    """,
)


DOWNGRADE_STATEMENTS = (
    "drop policy if exists evidence_read_grant_tenant_isolation on atlas.evidence_read_grant",
    "drop trigger if exists evidence_read_grant_guard_update on atlas.evidence_read_grant",
    "drop trigger if exists evidence_read_grant_guard_insert on atlas.evidence_read_grant",
    "drop table if exists atlas.evidence_read_grant",
    "drop function if exists atlas.guard_evidence_read_grant_update()",
    "drop function if exists atlas.guard_evidence_read_grant_insert()",
    "drop index if exists atlas.evidence_artifact_run_idx",
    "drop index if exists atlas.evidence_artifact_object_ref_unique",
    """
    alter table atlas.evidence_artifact
      drop constraint if exists evidence_artifact_object_ref_scope_valid
    """,
    """
    alter table atlas.evidence_artifact
      drop constraint if exists evidence_artifact_read_scope_unique
    """,
)


def upgrade() -> None:
    """Create private, tenant-scoped evidence read grants."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove evidence read grants and their artifact lookup constraints."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
