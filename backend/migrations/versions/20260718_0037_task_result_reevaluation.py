"""Add explicit command-bound REEVALUATED Task result revisions.

Revision ID: 20260718_0037
Revises: 20260718_0036
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0037"
down_revision: str | None = "20260718_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REEVALUATED_POLICY_DIGEST = (
    "sha256:45fb0eabd4c8bfe01b76871e4803b08dab7eda8924ea98912dd9907270fcb219"
)


UPGRADE_STATEMENTS = (
    f"""
    create table atlas.task_result_reevaluation_command (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      source_snapshot_id uuid not null,
      target_policy_version text not null,
      target_policy_digest text not null,
      client_mutation_id text not null,
      requested_by uuid,
      requested_at timestamptz not null,
      command_hash text not null,
      command jsonb not null,
      constraint task_result_reevaluation_command_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id
      ) references atlas.task_run (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint task_result_reevaluation_command_source_fk foreign key (
        source_snapshot_id
      ) references atlas.task_result_snapshot(id) on delete restrict,
      constraint task_result_reevaluation_command_mutation_unique unique (
        task_run_id, client_mutation_id
      ),
      constraint task_result_reevaluation_command_hash_unique unique (
        tenant_id, command_hash
      ),
      constraint task_result_reevaluation_command_policy_valid check (
        target_policy_version = '0.3.0'
        and target_policy_digest = '{_REEVALUATED_POLICY_DIGEST}'
      ),
      constraint task_result_reevaluation_command_digest_valid check (
        target_policy_digest ~ '^sha256:[0-9a-f]{{64}}$'
        and command_hash ~ '^sha256:[0-9a-f]{{64}}$'
      ),
      constraint task_result_reevaluation_command_mutation_valid check (
        char_length(client_mutation_id) between 8 and 200
        and client_mutation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
      ),
      constraint task_result_reevaluation_command_json_valid check (
        jsonb_typeof(command) = 'object'
      )
    )
    """,
    f"""
    create function atlas.guard_task_result_reevaluation_command_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      source atlas.task_result_snapshot%rowtype;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception
          'TaskResultReevaluationCommand insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into source
      from atlas.task_result_snapshot snapshot
      where snapshot.id = new.source_snapshot_id
        and snapshot.task_run_id = new.task_run_id
        and snapshot.tenant_id = new.tenant_id
        and snapshot.project_id = new.project_id
      for share;
      if not found
        or source.finality <> 'FULLY_RESOLVED'
        or new.target_policy_version <> '0.3.0'
        or new.target_policy_digest <> '{_REEVALUATED_POLICY_DIGEST}'
        or new.requested_at <> transaction_timestamp()
      then
        raise exception
          'TaskResultReevaluationCommand requires an exact Full source and Policy';
      end if;

      if atlas.task_json_object_size(new.command) <> 12
        or new.command ->> 'schemaVersion'
          <> 'atlas.task-result-reevaluation-command/0.1'
        or (new.command ->> 'id')::uuid is distinct from new.id
        or (new.command ->> 'tenantId')::uuid is distinct from new.tenant_id
        or (new.command ->> 'projectId')::uuid is distinct from new.project_id
        or (new.command ->> 'taskRunId')::uuid is distinct from new.task_run_id
        or (new.command ->> 'sourceSnapshotId')::uuid
          is distinct from new.source_snapshot_id
        or new.command ->> 'targetPolicyVersion'
          is distinct from new.target_policy_version
        or new.command ->> 'targetPolicyDigest'
          is distinct from new.target_policy_digest
        or new.command ->> 'clientMutationId'
          is distinct from new.client_mutation_id
        or (new.command ->> 'requestedBy')::uuid
          is distinct from new.requested_by
        or (new.command ->> 'requestedAt')::timestamptz
          is distinct from new.requested_at
        or new.command ->> 'commandHash' is distinct from new.command_hash
        or atlas.task_sha256_json(new.command - 'commandHash')
          is distinct from new.command_hash
      then
        raise exception
          'TaskResultReevaluationCommand persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_result_reevaluation_command_guard_insert
      before insert on atlas.task_result_reevaluation_command
      for each row execute function
        atlas.guard_task_result_reevaluation_command_insert()
    """,
    """
    create trigger task_result_reevaluation_command_prevent_mutation
      before update or delete on atlas.task_result_reevaluation_command
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create index task_result_reevaluation_command_source_idx
      on atlas.task_result_reevaluation_command (
        tenant_id, project_id, source_snapshot_id, requested_at desc
      )
    """,
    "alter table atlas.task_result_reevaluation_command enable row level security",
    "alter table atlas.task_result_reevaluation_command force row level security",
    """
    create policy task_result_reevaluation_command_tenant_isolation
      on atlas.task_result_reevaluation_command for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.task_result_reevaluation_command from atlas_app",
    "grant select, insert on atlas.task_result_reevaluation_command to atlas_app",
    """
    revoke all on function
      atlas.guard_task_result_reevaluation_command_insert()
      from public, atlas_app, atlas_dispatcher
    """,
    "drop trigger task_result_snapshot_guard_insert on atlas.task_result_snapshot",
    """
    alter table atlas.task_result_snapshot
      drop constraint task_result_snapshot_finality_valid,
      drop constraint task_result_snapshot_coverage_valid,
      drop constraint task_result_snapshot_policy_valid,
      add column reevaluation_source_snapshot_id uuid,
      add column reevaluation_command_id uuid,
      add constraint task_result_snapshot_reevaluation_source_fk foreign key (
        reevaluation_source_snapshot_id
      ) references atlas.task_result_snapshot(id) on delete restrict,
      add constraint task_result_snapshot_reevaluation_command_fk foreign key (
        reevaluation_command_id
      ) references atlas.task_result_reevaluation_command(id) on delete restrict
    """,
    f"""
    alter table atlas.task_result_snapshot
      add constraint task_result_snapshot_finality_valid check (
        finality in ('QUALITY_FINAL', 'FULLY_RESOLVED', 'REEVALUATED')
      ),
      add constraint task_result_snapshot_coverage_valid check (
        manifest_count between 1 and 100000
        and cardinality(unit_resolution_revision_ids) = manifest_count
        and (
          (
            finality = 'QUALITY_FINAL'
            and unit_hygiene_resolution_revision_ids is null
            and input_hygiene_resolution_set_hash is null
            and reevaluation_source_snapshot_id is null
            and reevaluation_command_id is null
          )
          or
          (
            finality = 'FULLY_RESOLVED'
            and cardinality(unit_hygiene_resolution_revision_ids) = manifest_count
            and input_hygiene_resolution_set_hash is not null
            and reevaluation_source_snapshot_id is null
            and reevaluation_command_id is null
          )
          or
          (
            finality = 'REEVALUATED'
            and cardinality(unit_hygiene_resolution_revision_ids) = manifest_count
            and input_hygiene_resolution_set_hash is not null
            and reevaluation_source_snapshot_id is not null
            and reevaluation_command_id is not null
          )
        )
      ),
      add constraint task_result_snapshot_policy_valid check (
        (
          finality = 'QUALITY_FINAL'
          and aggregation_policy_version = '0.1.0'
          and aggregation_policy_digest =
            'sha256:f047f7c9925cce522ccf743a0dcaf69d89f9a5d60a6856ab7654de971be8951e'
        )
        or
        (
          finality = 'FULLY_RESOLVED'
          and aggregation_policy_version = '0.2.0'
          and aggregation_policy_digest =
            'sha256:e4a7985c6a76073cc78179e57330f373d7baa6eb25246c8932ee3fac71dcf759'
        )
        or
        (
          finality = 'REEVALUATED'
          and aggregation_policy_version = '0.3.0'
          and aggregation_policy_digest = '{_REEVALUATED_POLICY_DIGEST}'
        )
      )
    """,
    """
    create unique index task_result_snapshot_reevaluated_input_unique
      on atlas.task_result_snapshot (
        task_run_id, reevaluation_source_snapshot_id,
        aggregation_policy_digest, finality
      )
      where finality = 'REEVALUATED'
    """,
    """
    create function atlas.guard_task_result_snapshot_v3_phase_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      previous atlas.task_result_snapshot%rowtype;
    begin
      select * into previous
      from atlas.task_result_snapshot candidate
      where candidate.task_run_id = new.task_run_id
      order by candidate.revision desc
      limit 1;
      if found then
        if new.revision <> previous.revision + 1
          or new.supersedes_snapshot_id <> previous.id
          or (
            previous.finality in ('FULLY_RESOLVED', 'REEVALUATED')
            and new.finality = 'QUALITY_FINAL'
          )
          or (
            previous.finality = 'REEVALUATED'
            and new.finality = 'FULLY_RESOLVED'
          )
        then
          raise exception 'TaskResultSnapshot v3 revision phase is invalid';
        end if;
      elsif new.revision <> 1
        or new.supersedes_snapshot_id is not null
        or new.finality <> 'QUALITY_FINAL'
      then
        raise exception 'TaskResultSnapshot v3 first revision is invalid';
      end if;
      return new;
    end;
    $$
    """,
    f"""
    create function atlas.guard_task_result_snapshot_reevaluated_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_run atlas.task_run%rowtype;
      source atlas.task_result_snapshot%rowtype;
      command atlas.task_result_reevaluation_command%rowtype;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'REEVALUATED insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into stored_run
      from atlas.task_run run
      where run.id = new.task_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
      for update;
      if not found
        or stored_run.lifecycle <> 'CLOSED'
        or stored_run.closed_at is null
        or stored_run.materialization_state <> 'SEALED'
        or stored_run.manifest_hash <> new.manifest_hash
        or stored_run.materialized_unit_count <> new.manifest_count
        or new.created_at <> transaction_timestamp()
      then
        raise exception 'REEVALUATED requires the exact closed TaskRun';
      end if;

      select * into source
      from atlas.task_result_snapshot snapshot
      where snapshot.id = new.reevaluation_source_snapshot_id
        and snapshot.task_run_id = new.task_run_id
        and snapshot.tenant_id = new.tenant_id
        and snapshot.project_id = new.project_id
      for share;
      if not found
        or source.finality <> 'FULLY_RESOLVED'
        or source.manifest_hash <> new.manifest_hash
      then
        raise exception 'REEVALUATED requires its exact Full source Snapshot';
      end if;

      select * into command
      from atlas.task_result_reevaluation_command request
      where request.id = new.reevaluation_command_id
        and request.task_run_id = new.task_run_id
        and request.tenant_id = new.tenant_id
        and request.project_id = new.project_id
      for share;
      if not found
        or command.source_snapshot_id <> source.id
        or command.target_policy_version <> '0.3.0'
        or command.target_policy_digest <> '{_REEVALUATED_POLICY_DIGEST}'
        or command.requested_at <> new.created_at
      then
        raise exception 'REEVALUATED requires its exact explicit command';
      end if;

      if new.finality <> 'REEVALUATED'
        or new.aggregation_policy_version <> '0.3.0'
        or new.aggregation_policy_digest <> '{_REEVALUATED_POLICY_DIGEST}'
        or new.unit_resolution_revision_ids
          is distinct from source.unit_resolution_revision_ids
        or new.input_resolution_set_hash
          is distinct from source.input_resolution_set_hash
        or new.unit_hygiene_resolution_revision_ids
          is distinct from source.unit_hygiene_resolution_revision_ids
        or new.input_hygiene_resolution_set_hash
          is distinct from source.input_hygiene_resolution_set_hash
        or new.projection_watermark is distinct from source.projection_watermark
        or new.manifest_count is distinct from source.manifest_count
        or new.verdict_counts is distinct from source.verdict_counts
        or new.axis_distributions is distinct from source.axis_distributions
        or new.raw_pass_rate is distinct from source.raw_pass_rate
        or new.trusted_pass_rate is distinct from source.trusted_pass_rate
        or new.autonomous_pass_rate is distinct from source.autonomous_pass_rate
        or new.decisive_pass_rate is distinct from source.decisive_pass_rate
      then
        raise exception
          'REEVALUATED must preserve the exact Full inputs and distributions';
      end if;

      if atlas.task_json_object_size(new.snapshot) <> 27
        or new.snapshot ->> 'schemaVersion'
          <> 'atlas.task-result-snapshot/0.3'
        or (new.snapshot ->> 'id')::uuid is distinct from new.id
        or (new.snapshot ->> 'tenantId')::uuid is distinct from new.tenant_id
        or (new.snapshot ->> 'projectId')::uuid is distinct from new.project_id
        or (new.snapshot ->> 'taskRunId')::uuid is distinct from new.task_run_id
        or new.snapshot ->> 'manifestHash' is distinct from new.manifest_hash
        or (new.snapshot ->> 'revision')::integer is distinct from new.revision
        or new.snapshot ->> 'finality' is distinct from new.finality
        or new.snapshot -> 'unitResolutionRevisionIds'
          is distinct from to_jsonb(new.unit_resolution_revision_ids)
        or new.snapshot ->> 'inputResolutionSetHash'
          is distinct from new.input_resolution_set_hash
        or new.snapshot -> 'unitHygieneResolutionRevisionIds'
          is distinct from to_jsonb(new.unit_hygiene_resolution_revision_ids)
        or new.snapshot ->> 'inputHygieneResolutionSetHash'
          is distinct from new.input_hygiene_resolution_set_hash
        or (new.snapshot ->> 'reevaluationSourceSnapshotId')::uuid
          is distinct from new.reevaluation_source_snapshot_id
        or (new.snapshot ->> 'reevaluationCommandId')::uuid
          is distinct from new.reevaluation_command_id
        or new.snapshot ->> 'aggregationPolicyVersion'
          is distinct from new.aggregation_policy_version
        or new.snapshot ->> 'aggregationPolicyDigest'
          is distinct from new.aggregation_policy_digest
        or (new.snapshot ->> 'projectionWatermark')::timestamptz
          is distinct from new.projection_watermark
        or (new.snapshot ->> 'manifestCount')::integer
          is distinct from new.manifest_count
        or new.snapshot -> 'verdictCounts' is distinct from new.verdict_counts
        or new.snapshot -> 'axisDistributions'
          is distinct from new.axis_distributions
        or new.snapshot -> 'rawPassRate' is distinct from new.raw_pass_rate
        or new.snapshot -> 'trustedPassRate'
          is distinct from new.trusted_pass_rate
        or new.snapshot -> 'autonomousPassRate'
          is distinct from new.autonomous_pass_rate
        or new.snapshot -> 'decisivePassRate'
          is distinct from new.decisive_pass_rate
        or (new.snapshot ->> 'supersedesSnapshotId')::uuid
          is distinct from new.supersedes_snapshot_id
        or (new.snapshot ->> 'createdAt')::timestamptz
          is distinct from new.created_at
        or new.snapshot ->> 'snapshotHash' is distinct from new.snapshot_hash
        or atlas.task_sha256_json(
          new.snapshot - array[
            'id', 'revision', 'supersedesSnapshotId',
            'reevaluationCommandId', 'createdAt', 'snapshotHash'
          ]::text[]
        ) is distinct from new.snapshot_hash
      then
        raise exception 'REEVALUATED persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger task_result_snapshot_00_phase_guard
      before insert on atlas.task_result_snapshot
      for each row execute function
        atlas.guard_task_result_snapshot_v3_phase_insert()
    """,
    """
    create trigger task_result_snapshot_guard_insert
      before insert on atlas.task_result_snapshot
      for each row
      when (new.finality <> 'REEVALUATED')
      execute function atlas.guard_task_result_snapshot_v2_insert()
    """,
    """
    create trigger task_result_snapshot_reevaluated_guard_insert
      before insert on atlas.task_result_snapshot
      for each row
      when (new.finality = 'REEVALUATED')
      execute function atlas.guard_task_result_snapshot_reevaluated_insert()
    """,
    """
    revoke all on function atlas.guard_task_result_snapshot_v3_phase_insert()
      from public, atlas_app, atlas_dispatcher
    """,
    """
    revoke all on function
      atlas.guard_task_result_snapshot_reevaluated_insert()
      from public, atlas_app, atlas_dispatcher
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (
        select 1
        from atlas.task_result_snapshot
        where finality = 'REEVALUATED'
           or reevaluation_source_snapshot_id is not null
           or reevaluation_command_id is not null
        limit 1
      ) or exists (
        select 1
        from atlas.task_result_reevaluation_command
        limit 1
      ) then
        raise exception
          'cannot downgrade while REEVALUATED Snapshot or command facts exist';
      end if;
    end;
    $$;
    """,
    "drop trigger task_result_snapshot_reevaluated_guard_insert on atlas.task_result_snapshot",
    "drop trigger task_result_snapshot_guard_insert on atlas.task_result_snapshot",
    "drop trigger task_result_snapshot_00_phase_guard on atlas.task_result_snapshot",
    "drop function atlas.guard_task_result_snapshot_reevaluated_insert()",
    "drop function atlas.guard_task_result_snapshot_v3_phase_insert()",
    "drop index atlas.task_result_snapshot_reevaluated_input_unique",
    """
    alter table atlas.task_result_snapshot
      drop constraint task_result_snapshot_finality_valid,
      drop constraint task_result_snapshot_coverage_valid,
      drop constraint task_result_snapshot_policy_valid,
      drop constraint task_result_snapshot_reevaluation_command_fk,
      drop constraint task_result_snapshot_reevaluation_source_fk,
      drop column reevaluation_command_id,
      drop column reevaluation_source_snapshot_id
    """,
    """
    alter table atlas.task_result_snapshot
      add constraint task_result_snapshot_finality_valid check (
        finality in ('QUALITY_FINAL', 'FULLY_RESOLVED')
      ),
      add constraint task_result_snapshot_coverage_valid check (
        manifest_count between 1 and 100000
        and cardinality(unit_resolution_revision_ids) = manifest_count
        and (
          (
            finality = 'QUALITY_FINAL'
            and unit_hygiene_resolution_revision_ids is null
            and input_hygiene_resolution_set_hash is null
          )
          or
          (
            finality = 'FULLY_RESOLVED'
            and cardinality(unit_hygiene_resolution_revision_ids) = manifest_count
            and input_hygiene_resolution_set_hash is not null
          )
        )
      ),
      add constraint task_result_snapshot_policy_valid check (
        (
          finality = 'QUALITY_FINAL'
          and aggregation_policy_version = '0.1.0'
          and aggregation_policy_digest =
            'sha256:f047f7c9925cce522ccf743a0dcaf69d89f9a5d60a6856ab7654de971be8951e'
        )
        or
        (
          finality = 'FULLY_RESOLVED'
          and aggregation_policy_version = '0.2.0'
          and aggregation_policy_digest =
            'sha256:e4a7985c6a76073cc78179e57330f373d7baa6eb25246c8932ee3fac71dcf759'
        )
      )
    """,
    """
    create trigger task_result_snapshot_guard_insert
      before insert on atlas.task_result_snapshot
      for each row execute function atlas.guard_task_result_snapshot_v2_insert()
    """,
    "drop table atlas.task_result_reevaluation_command",
    "drop function atlas.guard_task_result_reevaluation_command_insert()",
)


def upgrade() -> None:
    """Apply explicit command-bound Task result re-evaluation."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove only an unpopulated REEVALUATED extension."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
