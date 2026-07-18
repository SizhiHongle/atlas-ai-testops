"""Add reproducible, immutable quality Insight snapshots.

Revision ID: 20260718_0040
Revises: 20260718_0039
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0040"
down_revision: str | None = "20260718_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.insight_snapshot (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      window_days integer not null,
      request_hash text not null,
      client_mutation_id text not null,
      as_of timestamptz not null,
      baseline_start_at timestamptz not null,
      current_start_at timestamptz not null,
      current_end_at timestamptz not null,
      source_snapshot_ids uuid[] not null,
      source_snapshot_hashes text[] not null,
      source_set_digest text not null,
      gate_decision_ids uuid[] not null,
      gate_decision_hashes text[] not null,
      projection_watermark timestamptz,
      query_hash text not null,
      auth_scope_hash text not null,
      created_by uuid not null,
      created_at timestamptz not null,
      snapshot_hash text not null,
      snapshot jsonb not null,
      constraint insight_snapshot_project_scope_fk foreign key (
        project_id, tenant_id
      ) references atlas.project (id, tenant_id) on delete restrict,
      constraint insight_snapshot_mutation_unique unique (
        tenant_id, project_id, client_mutation_id
      ),
      constraint insight_snapshot_scope_unique unique (
        id, tenant_id, project_id
      ),
      constraint insight_snapshot_window_valid check (
        window_days in (7, 30, 90)
        and current_end_at = as_of
        and current_start_at =
          current_end_at - make_interval(days => window_days)
        and baseline_start_at =
          current_start_at - make_interval(days => window_days)
      ),
      constraint insight_snapshot_source_count_valid check (
        cardinality(source_snapshot_ids) between 0 and 20000
        and cardinality(source_snapshot_ids) =
          cardinality(source_snapshot_hashes)
        and cardinality(gate_decision_ids) between 0 and 20000
        and cardinality(gate_decision_ids) =
          cardinality(gate_decision_hashes)
        and array_position(source_snapshot_ids, null) is null
        and array_position(source_snapshot_hashes, null) is null
        and array_position(gate_decision_ids, null) is null
        and array_position(gate_decision_hashes, null) is null
      ),
      constraint insight_snapshot_strings_valid check (
        char_length(client_mutation_id) between 8 and 200
        and client_mutation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
      ),
      constraint insight_snapshot_digests_valid check (
        request_hash ~ '^sha256:[0-9a-f]{64}$'
        and source_set_digest ~ '^sha256:[0-9a-f]{64}$'
        and query_hash ~ '^sha256:[0-9a-f]{64}$'
        and auth_scope_hash ~ '^sha256:[0-9a-f]{64}$'
        and snapshot_hash ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint insight_snapshot_time_valid check (
        as_of <= created_at
        and (
          projection_watermark is null
          or projection_watermark <= as_of
        )
      ),
      constraint insight_snapshot_json_valid check (
        jsonb_typeof(snapshot) = 'object'
      )
    )
    """,
    """
    create function atlas.guard_insight_snapshot_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      expected_source_ids uuid[];
      expected_source_hashes text[];
      expected_gate_ids uuid[];
      expected_gate_hashes text[];
      expected_watermark timestamptz;
      result_records text;
      gate_records text;
      expected_source_set_digest text;
      snapshot_projection_watermark timestamptz;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'InsightSnapshot insertion requires exact tenant context'
          using errcode = '42501';
      end if;
      if new.created_at <> transaction_timestamp() then
        raise exception 'InsightSnapshot createdAt must use database transaction time';
      end if;

      perform 1
      from atlas.project project
      where project.id = new.project_id
        and project.tenant_id = new.tenant_id;
      if not found then
        raise exception 'InsightSnapshot requires an exact visible Project';
      end if;

      with eligible as (
        select distinct on (source.task_run_id)
          source.id,
          source.task_run_id,
          source.revision,
          source.snapshot_hash,
          source.projection_watermark,
          run.finalized_at as quality_finalized_at
        from atlas.task_result_snapshot source
        join atlas.task_run run
          on run.id = source.task_run_id
         and run.tenant_id = source.tenant_id
         and run.project_id = source.project_id
        where source.tenant_id = new.tenant_id
          and source.project_id = new.project_id
          and source.finality in ('FULLY_RESOLVED', 'REEVALUATED')
          and run.finalized_at is not null
          and source.created_at <= new.as_of
          and run.finalized_at >= new.baseline_start_at
          and run.finalized_at <= new.as_of
        order by source.task_run_id, source.revision desc
      ),
      ordered as (
        select
          eligible.*,
          row_number() over (
            order by
              eligible.quality_finalized_at,
              eligible.task_run_id,
              eligible.revision
          ) as ordinal,
          gate.id as gate_id,
          gate.decision_hash as gate_hash,
          gate.evaluated_at as gate_evaluated_at
        from eligible
        left join lateral (
          select
            decision.id,
            decision.decision_hash,
            decision.evaluated_at
          from atlas.task_gate_decision decision
          where decision.result_snapshot_id = eligible.id
            and decision.evaluated_at <= new.as_of
          order by decision.revision desc
          limit 1
        ) gate on true
      )
      select
        coalesce(
          array_agg(id order by ordinal),
          '{}'::uuid[]
        ),
        coalesce(
          array_agg(snapshot_hash order by ordinal),
          '{}'::text[]
        ),
        coalesce(
          array_agg(gate_id order by ordinal)
            filter (where gate_id is not null),
          '{}'::uuid[]
        ),
        coalesce(
          array_agg(gate_hash order by ordinal)
            filter (where gate_hash is not null),
          '{}'::text[]
        ),
        greatest(
          max(projection_watermark),
          max(gate_evaluated_at)
        ),
        coalesce(
          string_agg(
            'result:' || id::text || ':' || snapshot_hash,
            E'\n' order by ordinal
          ),
          ''
        ),
        coalesce(
          string_agg(
            'gate:' || gate_id::text || ':' || gate_hash,
            E'\n' order by ordinal
          ) filter (where gate_id is not null),
          ''
        )
      into
        expected_source_ids,
        expected_source_hashes,
        expected_gate_ids,
        expected_gate_hashes,
        expected_watermark,
        result_records,
        gate_records
      from ordered;

      if new.source_snapshot_ids is distinct from expected_source_ids
        or new.source_snapshot_hashes is distinct from expected_source_hashes
        or new.gate_decision_ids is distinct from expected_gate_ids
        or new.gate_decision_hashes is distinct from expected_gate_hashes
        or new.projection_watermark is distinct from expected_watermark
      then
        raise exception 'InsightSnapshot DatasetCut is stale or incomplete';
      end if;

      expected_source_set_digest :=
        'sha256:' || encode(
          public.digest(
            convert_to(
              case
                when result_records = '' then gate_records
                when gate_records = '' then result_records
                else result_records || E'\n' || gate_records
              end,
              'UTF8'
            ),
            'sha256'
          ),
          'hex'
        );
      if new.source_set_digest <> expected_source_set_digest then
        raise exception 'InsightSnapshot sourceSetDigest is not canonical';
      end if;

      if jsonb_typeof(new.snapshot -> 'metricDefinitions') <> 'array'
        or jsonb_array_length(new.snapshot -> 'metricDefinitions') <> 3
        or jsonb_typeof(new.snapshot -> 'current') <> 'object'
        or jsonb_typeof(new.snapshot -> 'baseline') <> 'object'
        or jsonb_typeof(new.snapshot -> 'deltas') <> 'object'
        or jsonb_typeof(new.snapshot -> 'datasetCut') <> 'object'
        or jsonb_typeof(new.snapshot -> 'terrain') <> 'array'
        or jsonb_array_length(new.snapshot -> 'terrain') > 4
        or new.snapshot ->> 'schemaVersion'
          <> 'atlas.insight-snapshot/0.1'
        or (new.snapshot ->> 'id')::uuid <> new.id
        or (new.snapshot ->> 'tenantId')::uuid <> new.tenant_id
        or (new.snapshot ->> 'projectId')::uuid <> new.project_id
        or (new.snapshot ->> 'windowDays')::integer <> new.window_days
        or new.snapshot ->> 'requestHash' <> new.request_hash
        or new.snapshot ->> 'clientMutationId' <> new.client_mutation_id
        or (new.snapshot ->> 'createdBy')::uuid <> new.created_by
        or (new.snapshot ->> 'createdAt')::timestamptz <> new.created_at
        or new.snapshot ->> 'snapshotHash' <> new.snapshot_hash
        or (new.snapshot #>> '{datasetCut,asOf}')::timestamptz <> new.as_of
        or new.snapshot #> '{datasetCut,sourceSnapshotIds}'
          is distinct from to_jsonb(new.source_snapshot_ids)
        or new.snapshot #> '{datasetCut,sourceSnapshotHashes}'
          is distinct from to_jsonb(new.source_snapshot_hashes)
        or new.snapshot #> '{datasetCut,gateDecisionIds}'
          is distinct from to_jsonb(new.gate_decision_ids)
        or new.snapshot #> '{datasetCut,gateDecisionHashes}'
          is distinct from to_jsonb(new.gate_decision_hashes)
        or new.snapshot #>> '{datasetCut,sourceSetDigest}'
          <> new.source_set_digest
        or new.snapshot #>> '{datasetCut,queryHash}' <> new.query_hash
        or new.snapshot #>> '{datasetCut,authScopeHash}' <> new.auth_scope_hash
        or (new.snapshot #>> '{baseline,startAt}')::timestamptz
          <> new.baseline_start_at
        or (new.snapshot #>> '{current,startAt}')::timestamptz
          <> new.current_start_at
        or (new.snapshot #>> '{current,endAt}')::timestamptz
          <> new.current_end_at
      then
        raise exception 'InsightSnapshot persisted projection is inconsistent';
      end if;

      snapshot_projection_watermark :=
        nullif(
          new.snapshot #>> '{datasetCut,projectionWatermark}',
          ''
        )::timestamptz;
      if snapshot_projection_watermark
          is distinct from new.projection_watermark
        or atlas.task_sha256_json(
          new.snapshot - array[
            'id', 'requestHash', 'clientMutationId',
            'createdBy', 'createdAt', 'snapshotHash'
          ]::text[]
        ) <> new.snapshot_hash
      then
        raise exception 'InsightSnapshot document hash is not canonical';
      end if;

      return new;
    end;
    $$
    """,
    """
    create trigger insight_snapshot_guard_insert
      before insert on atlas.insight_snapshot
      for each row execute function atlas.guard_insight_snapshot_insert()
    """,
    """
    create trigger insight_snapshot_prevent_mutation
      before update or delete on atlas.insight_snapshot
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create index insight_snapshot_project_latest_idx
      on atlas.insight_snapshot (
        tenant_id, project_id, created_at desc, id desc
      )
    """,
    """
    create index insight_snapshot_watermark_idx
      on atlas.insight_snapshot (
        tenant_id, project_id, projection_watermark desc, id desc
      )
    """,
    """
    create index insight_snapshot_hash_idx
      on atlas.insight_snapshot (tenant_id, snapshot_hash)
    """,
    "alter table atlas.insight_snapshot enable row level security",
    "alter table atlas.insight_snapshot force row level security",
    """
    create policy insight_snapshot_tenant_isolation
      on atlas.insight_snapshot for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.insight_snapshot from atlas_app",
    "grant select, insert on atlas.insight_snapshot to atlas_app",
    """
    revoke all on function atlas.guard_insight_snapshot_insert()
      from public, atlas_app, atlas_dispatcher
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.insight_snapshot limit 1) then
        raise exception 'cannot downgrade while InsightSnapshot facts exist';
      end if;
    end;
    $$
    """,
    "drop table if exists atlas.insight_snapshot",
    "drop function if exists atlas.guard_insight_snapshot_insert()",
)


def upgrade() -> None:
    """Apply the immutable InsightSnapshot table and source guards."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove only an empty InsightSnapshot projection table."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
