"""Add immutable AttemptSeal Result truth facts.

Revision ID: 20260718_0032
Revises: 20260717_0031
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0032"
down_revision: str | None = "20260717_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    create table atlas.unit_attempt_result_fact (
      seal_id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      manifest_id uuid not null,
      manifest_hash text not null,
      unit_key text not null,
      execution_ticket_id uuid not null,
      execution_ticket_digest text not null,
      oracle_verdict text not null,
      outcome_class text not null,
      closure_reason text not null,
      lifecycle text not null,
      data_hygiene text not null,
      evidence_completeness text not null,
      evidence_integrity text not null,
      execution_influence text not null,
      stability text not null,
      oracle_results_hash text not null,
      artifact_manifest_hash text not null,
      event_chain_head text not null,
      event_count bigint not null,
      evidence_policy_digest text not null,
      runtime_digest text not null,
      signature_alg text not null,
      signature_kid text not null,
      signature_value text not null,
      content_hash text not null,
      seal jsonb not null,
      sealed_at timestamptz not null,
      accepted_at timestamptz not null,
      constraint unit_attempt_result_fact_attempt_scope_fk foreign key (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.unit_attempt (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint unit_attempt_result_fact_ticket_scope_fk foreign key (
        execution_ticket_id, tenant_id, project_id, unit_attempt_id
      ) references atlas.task_unit_execution_ticket (
        id, tenant_id, project_id, unit_attempt_id
      ) on delete restrict,
      constraint unit_attempt_result_fact_full_scope_unique unique (
        seal_id, unit_attempt_id, execution_unit_id, task_run_id,
        tenant_id, project_id, content_hash
      ),
      constraint unit_attempt_result_fact_ref_scope_unique unique (
        seal_id, unit_attempt_id, execution_unit_id, task_run_id,
        tenant_id, project_id, content_hash, accepted_at
      ),
      constraint unit_attempt_result_fact_attempt_unique unique (unit_attempt_id),
      constraint unit_attempt_result_fact_hash_unique unique (
        tenant_id, content_hash
      ),
      constraint unit_attempt_result_fact_identity_valid check (
        manifest_id = task_run_id
      ),
      constraint unit_attempt_result_fact_digest_valid check (
        manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and unit_key ~ '^sha256:[0-9a-f]{64}$'
        and execution_ticket_digest ~ '^sha256:[0-9a-f]{64}$'
        and oracle_results_hash ~ '^sha256:[0-9a-f]{64}$'
        and artifact_manifest_hash ~ '^sha256:[0-9a-f]{64}$'
        and event_chain_head ~ '^sha256:[0-9a-f]{64}$'
        and evidence_policy_digest ~ '^sha256:[0-9a-f]{64}$'
        and runtime_digest ~ '^sha256:[0-9a-f]{64}$'
        and content_hash ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint unit_attempt_result_fact_verdict_valid check (
        oracle_verdict in ('PASSED', 'FAILED', 'INCONCLUSIVE', 'NOT_EVALUATED')
      ),
      constraint unit_attempt_result_fact_outcome_valid check (
        outcome_class in (
          'BUSINESS', 'DEPENDENCY', 'PLATFORM', 'USER',
          'AUTOMATION', 'POLICY', 'UNKNOWN'
        )
      ),
      constraint unit_attempt_result_fact_reason_valid check (
        closure_reason ~ '^[A-Z][A-Z0-9_]{1,95}$'
      ),
      constraint unit_attempt_result_fact_axes_valid check (
        lifecycle = 'SEALED'
        and data_hygiene in (
          'PENDING', 'CLEANED', 'CLEANUP_FAILED', 'LEAKED', 'NOT_APPLICABLE'
        )
        and evidence_completeness in (
          'PENDING', 'COMPLETE', 'PARTIAL', 'MISSING', 'NOT_APPLICABLE'
        )
        and evidence_integrity in ('UNVERIFIED', 'VERIFIED', 'INVALID')
        and execution_influence in (
          'AUTONOMOUS', 'MANUAL_ASSISTED', 'MANUAL_ONLY'
        )
        and stability in (
          'UNKNOWN', 'STABLE', 'INFRA_RECOVERED',
          'FLAKY_SUSPECT', 'FLAKY_CONFIRMED'
        )
      ),
      constraint unit_attempt_result_fact_pass_valid check (
        oracle_verdict <> 'PASSED'
        or (
          evidence_completeness = 'COMPLETE'
          and evidence_integrity = 'VERIFIED'
        )
      ),
      constraint unit_attempt_result_fact_not_evaluated_valid check (
        oracle_verdict <> 'NOT_EVALUATED'
        or evidence_completeness <> 'COMPLETE'
      ),
      constraint unit_attempt_result_fact_event_valid check (
        event_count between 1 and 10000000
      ),
      constraint unit_attempt_result_fact_signature_valid check (
        signature_alg = 'EdDSA'
        and signature_kid ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
        and signature_value ~ '^base64url:[A-Za-z0-9_-]{86}$'
      ),
      constraint unit_attempt_result_fact_time_valid check (
        accepted_at >= sealed_at
      ),
      constraint unit_attempt_result_fact_seal_object check (
        jsonb_typeof(seal) = 'object'
      )
    )
    """,
    """
    create table atlas.result_ref (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      seal_id uuid not null,
      seal_content_hash text not null,
      created_at timestamptz not null,
      constraint result_ref_fact_scope_fk foreign key (
        seal_id, unit_attempt_id, execution_unit_id,
        task_run_id, tenant_id, project_id, seal_content_hash, created_at
      ) references atlas.unit_attempt_result_fact (
        seal_id, unit_attempt_id, execution_unit_id,
        task_run_id, tenant_id, project_id, content_hash, accepted_at
      ) on delete restrict,
      constraint result_ref_attempt_unique unique (unit_attempt_id),
      constraint result_ref_seal_unique unique (seal_id),
      constraint result_ref_scope_unique unique (
        id, unit_attempt_id, tenant_id, project_id
      ),
      constraint result_ref_digest_valid check (
        seal_content_hash ~ '^sha256:[0-9a-f]{64}$'
      )
    )
    """,
    """
    create table atlas.result_integrity_incident (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      execution_unit_id uuid not null,
      unit_attempt_id uuid not null,
      accepted_seal_id uuid not null,
      accepted_content_hash text not null,
      conflicting_seal_id uuid not null,
      conflicting_content_hash text not null,
      signature_kid text not null,
      observed_at timestamptz not null,
      constraint result_integrity_incident_attempt_scope_fk foreign key (
        unit_attempt_id, execution_unit_id, task_run_id, tenant_id, project_id
      ) references atlas.unit_attempt (
        id, execution_unit_id, task_run_id, tenant_id, project_id
      ) on delete restrict,
      constraint result_integrity_incident_accepted_fact_fk foreign key (
        accepted_seal_id, unit_attempt_id, execution_unit_id,
        task_run_id, tenant_id, project_id, accepted_content_hash
      ) references atlas.unit_attempt_result_fact (
        seal_id, unit_attempt_id, execution_unit_id,
        task_run_id, tenant_id, project_id, content_hash
      ) on delete restrict,
      constraint result_integrity_incident_conflict_unique unique (
        unit_attempt_id, conflicting_content_hash
      ),
      constraint result_integrity_incident_digest_valid check (
        accepted_content_hash ~ '^sha256:[0-9a-f]{64}$'
        and conflicting_content_hash ~ '^sha256:[0-9a-f]{64}$'
        and accepted_content_hash <> conflicting_content_hash
      ),
      constraint result_integrity_incident_key_valid check (
        signature_kid ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$'
      )
    )
    """,
    """
    create function atlas.guard_unit_attempt_result_fact_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      stored_attempt atlas.unit_attempt%rowtype;
      stored_ticket atlas.task_unit_execution_ticket%rowtype;
      stored_manifest atlas.task_run_manifest%rowtype;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception 'AttemptSeal insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into stored_attempt
      from atlas.unit_attempt attempt
      where attempt.id = new.unit_attempt_id
        and attempt.execution_unit_id = new.execution_unit_id
        and attempt.task_run_id = new.task_run_id
        and attempt.tenant_id = new.tenant_id
        and attempt.project_id = new.project_id
      for update;
      if not found
        or stored_attempt.lifecycle <> 'RUNNING'
        or stored_attempt.manifest_hash <> new.manifest_hash
        or stored_attempt.unit_key <> new.unit_key
        or stored_attempt.started_at is null
        or new.sealed_at < stored_attempt.started_at
        or new.sealed_at > stored_attempt.execution_deadline
        or new.accepted_at <> transaction_timestamp()
        or new.data_hygiene <> (case stored_attempt.hygiene
          when 'NOT_REQUIRED' then 'NOT_APPLICABLE'
          when 'PENDING' then 'PENDING'
          when 'RUNNING' then 'PENDING'
          when 'CLEANED' then 'CLEANED'
          when 'CLEANUP_FAILED' then 'CLEANUP_FAILED'
          when 'LEAKED' then 'LEAKED'
          else null
        end)
      then
        raise exception 'AttemptSeal requires the exact active UnitAttempt';
      end if;

      select * into stored_ticket
      from atlas.task_unit_execution_ticket ticket
      where ticket.id = new.execution_ticket_id
        and ticket.unit_attempt_id = new.unit_attempt_id
        and ticket.tenant_id = new.tenant_id;
      if not found
        or stored_ticket.project_id <> new.project_id
        or stored_ticket.task_run_id <> new.task_run_id
        or stored_ticket.execution_unit_id <> new.execution_unit_id
        or stored_ticket.ticket_digest <> new.execution_ticket_digest
        or stored_ticket.manifest_hash <> new.manifest_hash
        or stored_ticket.unit_key <> new.unit_key
      then
        raise exception 'AttemptSeal execution ticket binding is invalid';
      end if;

      select * into stored_manifest
      from atlas.task_run_manifest manifest
      where manifest.task_run_id = new.task_run_id
        and manifest.tenant_id = new.tenant_id
        and manifest.project_id = new.project_id
        and manifest.manifest_hash = new.manifest_hash;
      if not found
        or not exists (
          select 1
          from jsonb_each_text(stored_manifest.policy_digests) policy
          where policy.value = new.evidence_policy_digest
        )
        or new.runtime_digest <> atlas.task_sha256_json(
          jsonb_build_object(
            'schemaVersion', 'atlas.formal-attempt-runtime/0.1',
            'executionTicketId', stored_ticket.id::text,
            'executionTicketDigest', stored_ticket.ticket_digest,
            'testIrDigest', stored_ticket.test_ir_digest,
            'planDigest', stored_ticket.plan_digest,
            'compiledDigest', stored_ticket.compiled_digest,
            'executionProfileDigest', stored_ticket.execution_profile_digest,
            'identityProfileDigest', stored_ticket.identity_profile_digest,
            'browserProfileDigest', stored_ticket.browser_profile_digest,
            'dataProfileDigest', stored_ticket.data_profile_digest,
            'fixtureBlueprintDigest', stored_ticket.fixture_blueprint_digest,
            'environmentId', stored_ticket.environment_id::text,
            'environmentRevision', stored_ticket.environment_revision
          )
        )
      then
        raise exception 'AttemptSeal policy or runtime binding is invalid';
      end if;

      if atlas.task_json_has_sensitive_keys(new.seal)
        or atlas.task_json_object_size(new.seal) <> 30
        or new.seal ->> 'schemaVersion' <> 'attempt-seal/1.0'
        or new.seal ->> 'sealId' <> new.seal_id::text
        or new.seal ->> 'tenantId' <> new.tenant_id::text
        or new.seal ->> 'projectId' <> new.project_id::text
        or new.seal ->> 'taskRunId' <> new.task_run_id::text
        or new.seal ->> 'executionUnitId' <> new.execution_unit_id::text
        or new.seal ->> 'unitAttemptId' <> new.unit_attempt_id::text
        or new.seal ->> 'manifestId' <> new.manifest_id::text
        or new.seal ->> 'manifestHash' <> new.manifest_hash
        or new.seal ->> 'unitKey' <> new.unit_key
        or new.seal ->> 'executionTicketId' <> new.execution_ticket_id::text
        or new.seal ->> 'executionTicketDigest' <> new.execution_ticket_digest
        or new.seal ->> 'oracleVerdict' <> new.oracle_verdict
        or new.seal ->> 'outcomeClass' <> new.outcome_class
        or new.seal ->> 'closureReason' <> new.closure_reason
        or new.seal ->> 'lifecycle' <> new.lifecycle
        or new.seal ->> 'dataHygiene' <> new.data_hygiene
        or new.seal ->> 'evidenceCompleteness' <> new.evidence_completeness
        or new.seal ->> 'evidenceIntegrity' <> new.evidence_integrity
        or new.seal ->> 'executionInfluence' <> new.execution_influence
        or new.seal ->> 'stability' <> new.stability
        or new.seal ->> 'oracleResultsHash' <> new.oracle_results_hash
        or new.seal ->> 'artifactManifestHash' <> new.artifact_manifest_hash
        or jsonb_typeof(new.seal -> 'eventChain') <> 'object'
        or atlas.task_json_object_size(new.seal -> 'eventChain') <> 2
        or new.seal #>> '{eventChain,head}' <> new.event_chain_head
        or (new.seal #>> '{eventChain,eventCount}')::bigint <> new.event_count
        or new.seal ->> 'evidencePolicyDigest' <> new.evidence_policy_digest
        or new.seal ->> 'runtimeDigest' <> new.runtime_digest
        or jsonb_typeof(new.seal -> 'signature') <> 'object'
        or atlas.task_json_object_size(new.seal -> 'signature') <> 3
        or new.seal #>> '{signature,alg}' <> new.signature_alg
        or new.seal #>> '{signature,kid}' <> new.signature_kid
        or (new.seal #>> '{signature,jcs}')::boolean is not true
        or new.seal ->> 'signatureValue' <> new.signature_value
        or new.seal ->> 'contentHash' <> new.content_hash
        or (new.seal ->> 'sealedAt')::timestamptz <> new.sealed_at
        or atlas.task_sha256_json(
          new.seal - 'signatureValue' - 'contentHash'
        ) <> new.content_hash
      then
        raise exception 'AttemptSeal persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger unit_attempt_result_fact_guard_insert
      before insert on atlas.unit_attempt_result_fact
      for each row execute function atlas.guard_unit_attempt_result_fact_insert()
    """,
    """
    create trigger unit_attempt_result_fact_prevent_mutation
      before update or delete on atlas.unit_attempt_result_fact
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger result_ref_prevent_mutation
      before update or delete on atlas.result_ref
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger result_integrity_incident_prevent_mutation
      before update or delete on atlas.result_integrity_incident
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create index unit_attempt_result_fact_task_idx
      on atlas.unit_attempt_result_fact (
        tenant_id, project_id, task_run_id, accepted_at, seal_id
      )
    """,
    """
    create index result_integrity_incident_task_idx
      on atlas.result_integrity_incident (
        tenant_id, project_id, task_run_id, observed_at, id
      )
    """,
    "alter table atlas.unit_attempt_result_fact enable row level security",
    "alter table atlas.unit_attempt_result_fact force row level security",
    "alter table atlas.result_ref enable row level security",
    "alter table atlas.result_ref force row level security",
    "alter table atlas.result_integrity_incident enable row level security",
    "alter table atlas.result_integrity_incident force row level security",
    """
    create policy unit_attempt_result_fact_tenant_isolation
      on atlas.unit_attempt_result_fact for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy result_ref_tenant_isolation
      on atlas.result_ref for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy result_integrity_incident_tenant_isolation
      on atlas.result_integrity_incident for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.unit_attempt_result_fact from atlas_app",
    "revoke all on atlas.result_ref from atlas_app",
    "revoke all on atlas.result_integrity_incident from atlas_app",
    "grant select, insert on atlas.unit_attempt_result_fact to atlas_app",
    "grant select, insert on atlas.result_ref to atlas_app",
    "grant select, insert on atlas.result_integrity_incident to atlas_app",
    """
    revoke all on function atlas.guard_unit_attempt_result_fact_insert()
      from public, atlas_app, atlas_dispatcher
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.unit_attempt_result_fact limit 1)
        or exists (select 1 from atlas.result_integrity_incident limit 1)
      then
        raise exception 'cannot downgrade while AttemptSeal Result facts exist';
      end if;
    end;
    $$
    """,
    "drop table if exists atlas.result_integrity_incident",
    "drop table if exists atlas.result_ref",
    "drop table if exists atlas.unit_attempt_result_fact",
    "drop function if exists atlas.guard_unit_attempt_result_fact_insert()",
)


def upgrade() -> None:
    """Apply the AttemptSeal truth schema atomically."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove the schema only when no immutable Result truth would be lost."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
