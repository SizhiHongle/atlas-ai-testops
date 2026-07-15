"""Create immutable execution contracts and trusted DebugRun evidence.

Revision ID: 20260715_0016
Revises: 20260715_0015
Create Date: 2026-07-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260715_0016"
down_revision: str | None = "20260715_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    "alter table atlas.debug_run disable trigger debug_run_guard_update",
    """
    update atlas.debug_run
    set lifecycle = 'TERMINATED',
        outcome = 'INCONCLUSIVE',
        evidence_manifest_id = null,
        evidence_manifest_digest = null,
        failure_code = 'LEGACY_RUNTIME_UNVERIFIED',
        failure_detail = 'The pre-P6 active run did not have a frozen execution contract.',
        started_at = coalesce(started_at, requested_at),
        completed_at = clock_timestamp(),
        revision = revision + 1
    where lifecycle in ('BINDING', 'READY', 'RUNNING', 'FINALIZING')
    """,
    """
    update atlas.debug_run
    set outcome = 'INCONCLUSIVE',
        evidence_manifest_id = null,
        evidence_manifest_digest = null,
        failure_code = 'LEGACY_EVIDENCE_UNVERIFIED',
        failure_detail = 'The pre-P6 result did not have a verifiable runtime evidence root.',
        revision = revision + 1
    where lifecycle = 'TERMINATED'
      and outcome = 'PASSED'
    """,
    "alter table atlas.debug_run enable trigger debug_run_guard_update",
    """
    alter table atlas.fixture_manifest
      add constraint fixture_manifest_runtime_scope_unique
      unique (fixture_run_id, tenant_id, project_id, environment_id)
    """,
    """
    alter table atlas.debug_run
      add column execution_contract_id uuid,
      add column execution_contract_digest text
    """,
    """
    create table atlas.execution_contract (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      debug_run_id uuid not null,
      test_case_id uuid not null,
      semantic_revision bigint not null,
      test_ir_digest text not null,
      plan_digest text not null,
      compiled_digest text not null,
      fixture_run_id uuid not null,
      fixture_manifest_digest text not null,
      worker_identity text not null,
      contract jsonb not null,
      contract_digest text not null,
      execution_deadline timestamptz not null,
      created_at timestamptz not null,
      constraint execution_contract_debug_run_scope_fk foreign key (
        debug_run_id, tenant_id, project_id, test_case_id
      ) references atlas.debug_run (
        id, tenant_id, project_id, test_case_id
      ) on delete restrict,
      constraint execution_contract_environment_scope_fk foreign key (
        environment_id, tenant_id, project_id
      ) references atlas.environment (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint execution_contract_fixture_scope_fk foreign key (
        fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.fixture_manifest (
        fixture_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint execution_contract_debug_run_unique unique (debug_run_id),
      constraint execution_contract_full_scope_unique unique (
        id, debug_run_id, tenant_id, project_id, environment_id
      ),
      constraint execution_contract_revision_valid check (semantic_revision > 0),
      constraint execution_contract_digest_valid check (
        test_ir_digest ~ '^sha256:[0-9a-f]{64}$'
        and plan_digest ~ '^sha256:[0-9a-f]{64}$'
        and compiled_digest ~ '^sha256:[0-9a-f]{64}$'
        and fixture_manifest_digest ~ '^sha256:[0-9a-f]{64}$'
        and contract_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint execution_contract_worker_valid check (
        worker_identity ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$'
      ),
      constraint execution_contract_shape check (
        jsonb_typeof(contract) = 'object'
        and contract ->> 'schemaVersion' = 'atlas.execution-contract/0.1'
        and contract ->> 'id' = id::text
        and contract ->> 'tenantId' = tenant_id::text
        and contract ->> 'projectId' = project_id::text
        and contract ->> 'environmentId' = environment_id::text
        and contract ->> 'debugRunId' = debug_run_id::text
        and contract ->> 'testCaseId' = test_case_id::text
        and (contract ->> 'semanticRevision')::bigint = semantic_revision
        and contract ->> 'testIrDigest' = test_ir_digest
        and contract ->> 'planDigest' = plan_digest
        and contract ->> 'compiledDigest' = compiled_digest
        and contract -> 'fixture' ->> 'fixtureRunId' = fixture_run_id::text
        and contract -> 'fixture' ->> 'fixtureManifestDigest'
          = fixture_manifest_digest
        and contract ->> 'workerIdentity' = worker_identity
        and contract ->> 'contentDigest' = contract_digest
        and jsonb_typeof(contract -> 'actors') = 'array'
        and jsonb_array_length(contract -> 'actors') between 1 and 8
      ),
      constraint execution_contract_time_order check (
        created_at < execution_deadline
      )
    )
    """,
    """
    create table atlas.execution_contract_actor_binding (
      execution_contract_id uuid not null,
      debug_run_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      actor_slot text not null,
      role_id uuid not null,
      role_revision bigint not null,
      account_lease_id uuid not null,
      account_handle text not null,
      fencing_token bigint not null,
      browser_context_ref text not null,
      bound_at timestamptz not null,
      primary key (execution_contract_id, actor_slot),
      constraint execution_actor_contract_scope_fk foreign key (
        execution_contract_id, debug_run_id, tenant_id, project_id, environment_id
      ) references atlas.execution_contract (
        id, debug_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint execution_actor_role_scope_fk foreign key (
        role_id, tenant_id, project_id
      ) references atlas.test_role (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint execution_actor_lease_scope_fk foreign key (
        account_lease_id, tenant_id, project_id, environment_id
      ) references atlas.account_lease (
        id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint execution_actor_lease_unique unique (account_lease_id),
      constraint execution_actor_slot_valid check (
        actor_slot ~ '^[A-Za-z_][A-Za-z0-9_.-]{1,79}$'
      ),
      constraint execution_actor_role_revision_valid check (role_revision > 0),
      constraint execution_actor_handle_valid check (
        account_handle ~ '^ah_[A-Za-z0-9_-]{16,128}$'
      ),
      constraint execution_actor_fence_valid check (fencing_token > 0),
      constraint execution_actor_context_ref_valid check (
        browser_context_ref ~ '^bctx_[A-Za-z0-9_-]{32,200}$'
      )
    )
    """,
    """
    create table atlas.assertion_result (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      debug_run_id uuid not null,
      execution_contract_id uuid not null,
      assertion_id text not null,
      node_id text not null,
      strength text not null,
      status text not null,
      expected_digest text not null,
      actual_safe_summary text not null,
      evaluator_version_ref text not null,
      evidence_refs uuid[] not null default '{}',
      observed_at timestamptz not null,
      duration_ms bigint not null,
      result jsonb not null,
      result_digest text not null,
      created_at timestamptz not null,
      constraint assertion_result_contract_scope_fk foreign key (
        execution_contract_id, debug_run_id, tenant_id, project_id, environment_id
      ) references atlas.execution_contract (
        id, debug_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint assertion_result_contract_assertion_unique unique (
        execution_contract_id, assertion_id
      ),
      constraint assertion_result_strength_valid check (
        strength in ('hard', 'soft', 'diagnostic')
      ),
      constraint assertion_result_status_valid check (
        status in ('PASSED', 'FAILED', 'INCONCLUSIVE')
      ),
      constraint assertion_result_digest_valid check (
        expected_digest ~ '^sha256:[0-9a-f]{64}$'
        and result_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint assertion_result_summary_safe check (
        btrim(actual_safe_summary) <> ''
        and octet_length(actual_safe_summary) <= 500
      ),
      constraint assertion_result_duration_valid check (
        duration_ms between 0 and 3600000
      ),
      constraint assertion_result_evidence_refs_valid check (
        cardinality(evidence_refs) <= 64
        and array_position(evidence_refs, null) is null
      ),
      constraint assertion_result_shape check (
        jsonb_typeof(result) = 'object'
        and result ->> 'schemaVersion' = 'atlas.assertion-result/0.1'
        and result ->> 'id' = id::text
        and result ->> 'assertionId' = assertion_id
        and result ->> 'nodeId' = node_id
        and result ->> 'strength' = strength
        and result ->> 'status' = status
        and result ->> 'expectedDigest' = expected_digest
        and result ->> 'actualSafeSummary' = actual_safe_summary
        and result ->> 'evaluatorVersionRef' = evaluator_version_ref
        and result -> 'evidenceRefs' = to_jsonb(evidence_refs)
        and (result ->> 'observedAt')::timestamptz = observed_at
        and (result ->> 'durationMs')::bigint = duration_ms
        and result ->> 'resultDigest' = result_digest
      )
    )
    """,
    """
    create table atlas.evidence_artifact (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      debug_run_id uuid not null,
      execution_contract_id uuid not null,
      kind text not null,
      object_ref text not null,
      content_digest text not null,
      size_bytes bigint not null,
      mime_type text not null,
      redaction_policy_digest text not null,
      integrity text not null,
      required boolean not null,
      captured_at timestamptz not null,
      created_at timestamptz not null,
      constraint evidence_artifact_contract_scope_fk foreign key (
        execution_contract_id, debug_run_id, tenant_id, project_id, environment_id
      ) references atlas.execution_contract (
        id, debug_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint evidence_artifact_contract_id_unique unique (
        execution_contract_id, id
      ),
      constraint evidence_artifact_kind_valid check (
        kind in (
          'SCREENSHOT', 'TRACE', 'DOM_SUMMARY', 'ARIA_SNAPSHOT',
          'NETWORK_SUMMARY', 'CONSOLE_SUMMARY', 'TOOL_INVOCATION'
        )
      ),
      constraint evidence_artifact_object_ref_opaque check (
        object_ref ~ '^evidence://[A-Za-z0-9][A-Za-z0-9/_.=-]+$'
        and octet_length(object_ref) between 19 and 523
      ),
      constraint evidence_artifact_digest_valid check (
        content_digest ~ '^sha256:[0-9a-f]{64}$'
        and redaction_policy_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint evidence_artifact_size_valid check (
        size_bytes between 1 and 10737418240
      ),
      constraint evidence_artifact_mime_valid check (
        mime_type ~ '^[a-z0-9][a-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*$'
        and octet_length(mime_type) <= 160
      ),
      constraint evidence_artifact_integrity_valid check (
        integrity in ('VERIFIED', 'INVALID')
      )
    )
    """,
    """
    create table atlas.evidence_manifest (
      id uuid primary key,
      tenant_id uuid not null,
      project_id uuid not null,
      environment_id uuid not null,
      debug_run_id uuid not null,
      execution_contract_id uuid not null,
      execution_contract_digest text not null,
      test_ir_digest text not null,
      plan_digest text not null,
      fixture_run_id uuid not null,
      fixture_manifest_digest text not null,
      outcome text not null,
      completeness text not null,
      integrity text not null,
      oracle_results_digest text not null,
      artifact_manifest_digest text not null,
      event_chain_head_digest text not null,
      event_count bigint not null,
      passed_assertions integer not null,
      failed_assertions integer not null,
      inconclusive_assertions integer not null,
      manifest jsonb not null,
      manifest_digest text not null,
      finalized_at timestamptz not null,
      created_at timestamptz not null,
      constraint evidence_manifest_contract_scope_fk foreign key (
        execution_contract_id, debug_run_id, tenant_id, project_id, environment_id
      ) references atlas.execution_contract (
        id, debug_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint evidence_manifest_fixture_scope_fk foreign key (
        fixture_run_id, tenant_id, project_id, environment_id
      ) references atlas.fixture_manifest (
        fixture_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      constraint evidence_manifest_full_scope_unique unique (
        id, debug_run_id, tenant_id, project_id, environment_id
      ),
      constraint evidence_manifest_debug_run_unique unique (debug_run_id),
      constraint evidence_manifest_contract_unique unique (execution_contract_id),
      constraint evidence_manifest_digest_valid check (
        execution_contract_digest ~ '^sha256:[0-9a-f]{64}$'
        and test_ir_digest ~ '^sha256:[0-9a-f]{64}$'
        and plan_digest ~ '^sha256:[0-9a-f]{64}$'
        and fixture_manifest_digest ~ '^sha256:[0-9a-f]{64}$'
        and oracle_results_digest ~ '^sha256:[0-9a-f]{64}$'
        and artifact_manifest_digest ~ '^sha256:[0-9a-f]{64}$'
        and event_chain_head_digest ~ '^sha256:[0-9a-f]{64}$'
        and manifest_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      constraint evidence_manifest_outcome_valid check (
        outcome in ('PASSED', 'FAILED', 'INCONCLUSIVE')
      ),
      constraint evidence_manifest_completeness_valid check (
        completeness in ('COMPLETE', 'PARTIAL', 'MISSING')
      ),
      constraint evidence_manifest_integrity_valid check (
        integrity in ('VERIFIED', 'INVALID')
      ),
      constraint evidence_manifest_counts_valid check (
        event_count > 0
        and passed_assertions >= 0
        and failed_assertions >= 0
        and inconclusive_assertions >= 0
      ),
      constraint evidence_manifest_pass_valid check (
        outcome <> 'PASSED'
        or (completeness = 'COMPLETE' and integrity = 'VERIFIED')
      ),
      constraint evidence_manifest_shape check (
        jsonb_typeof(manifest) = 'object'
        and manifest ->> 'schemaVersion' = 'atlas.evidence-manifest/0.1'
        and manifest ->> 'id' = id::text
        and manifest ->> 'tenantId' = tenant_id::text
        and manifest ->> 'projectId' = project_id::text
        and manifest ->> 'environmentId' = environment_id::text
        and manifest ->> 'debugRunId' = debug_run_id::text
        and manifest ->> 'executionContractId' = execution_contract_id::text
        and manifest ->> 'executionContractDigest' = execution_contract_digest
        and manifest ->> 'testIrDigest' = test_ir_digest
        and manifest ->> 'planDigest' = plan_digest
        and manifest ->> 'fixtureRunId' = fixture_run_id::text
        and manifest ->> 'fixtureManifestDigest' = fixture_manifest_digest
        and manifest ->> 'outcome' = outcome
        and manifest ->> 'completeness' = completeness
        and manifest ->> 'integrity' = integrity
        and manifest ->> 'oracleResultsDigest' = oracle_results_digest
        and manifest ->> 'artifactManifestDigest' = artifact_manifest_digest
        and manifest ->> 'eventChainHeadDigest' = event_chain_head_digest
        and (manifest ->> 'eventCount')::bigint = event_count
        and (manifest ->> 'passedAssertions')::integer = passed_assertions
        and (manifest ->> 'failedAssertions')::integer = failed_assertions
        and (manifest ->> 'inconclusiveAssertions')::integer
          = inconclusive_assertions
        and (manifest ->> 'finalizedAt')::timestamptz = finalized_at
        and manifest ->> 'contentDigest' = manifest_digest
        and jsonb_typeof(manifest -> 'assertionResults') = 'array'
        and jsonb_typeof(manifest -> 'missingAssertionIds') = 'array'
        and jsonb_typeof(manifest -> 'artifacts') = 'array'
      )
    )
    """,
    """
    alter table atlas.debug_run
      add constraint debug_run_execution_contract_scope_fk foreign key (
        execution_contract_id, id, tenant_id, project_id, environment_id
      ) references atlas.execution_contract (
        id, debug_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      add constraint debug_run_evidence_manifest_scope_fk foreign key (
        evidence_manifest_id, id, tenant_id, project_id, environment_id
      ) references atlas.evidence_manifest (
        id, debug_run_id, tenant_id, project_id, environment_id
      ) on delete restrict,
      add constraint debug_run_runtime_digest_valid check (
        execution_contract_digest is null
        or execution_contract_digest ~ '^sha256:[0-9a-f]{64}$'
      ),
      add constraint debug_run_runtime_reference_shape check (
        (execution_contract_id is null) = (execution_contract_digest is null)
        and (lifecycle <> 'CREATED' or execution_contract_id is null)
        and (
          lifecycle not in ('BINDING', 'READY', 'RUNNING')
          or execution_contract_id is not null
        )
        and (outcome <> 'PASSED' or execution_contract_id is not null)
      )
    """,
    """
    create function atlas.guard_debug_run_runtime_update()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      contract_record record;
      bound_actor_count bigint;
    begin
      if old.execution_contract_id is not null and row(
        new.execution_contract_id, new.execution_contract_digest
      ) is distinct from row(
        old.execution_contract_id, old.execution_contract_digest
      ) then
        raise exception 'debug run execution contract is immutable';
      end if;
      if old.lifecycle <> 'CREATED' and row(
        new.execution_contract_id, new.execution_contract_digest
      ) is distinct from row(
        old.execution_contract_id, old.execution_contract_digest
      ) then
        raise exception 'execution contract can only bind from created state';
      end if;
      if old.evidence_manifest_id is not null and row(
        new.evidence_manifest_id, new.evidence_manifest_digest
      ) is distinct from row(
        old.evidence_manifest_id, old.evidence_manifest_digest
      ) then
        raise exception 'debug run evidence manifest is immutable';
      end if;
      if old.lifecycle = 'CREATED' and new.lifecycle = 'BINDING' then
        select contract, contract_digest
        into contract_record
        from atlas.execution_contract
        where id = new.execution_contract_id
          and debug_run_id = new.id
          and tenant_id = new.tenant_id
          and project_id = new.project_id
          and environment_id = new.environment_id;
        if not found
          or contract_record.contract_digest <> new.execution_contract_digest
        then
          raise exception 'debug run binding requires an exact execution contract';
        end if;
        select count(*)
        into bound_actor_count
        from atlas.execution_contract_actor_binding binding
        where binding.execution_contract_id = new.execution_contract_id;
        if bound_actor_count
          <> jsonb_array_length(contract_record.contract -> 'actors')
        then
          raise exception 'debug run binding requires every frozen actor binding';
        end if;
      end if;
      if new.evidence_manifest_id is not null and not exists (
        select 1
        from atlas.evidence_manifest manifest
        where manifest.id = new.evidence_manifest_id
          and manifest.debug_run_id = new.id
          and manifest.tenant_id = new.tenant_id
          and manifest.project_id = new.project_id
          and manifest.environment_id = new.environment_id
          and manifest.execution_contract_id = new.execution_contract_id
          and manifest.execution_contract_digest = new.execution_contract_digest
          and manifest.manifest_digest = new.evidence_manifest_digest
          and manifest.test_ir_digest = new.test_ir_digest
          and manifest.plan_digest = new.plan_digest
          and manifest.outcome = new.outcome
      ) then
        raise exception 'debug run requires an exact evidence manifest';
      end if;
      if new.outcome = 'PASSED' and not exists (
        select 1
        from atlas.evidence_manifest manifest
        where manifest.id = new.evidence_manifest_id
          and manifest.debug_run_id = new.id
          and manifest.tenant_id = new.tenant_id
          and manifest.project_id = new.project_id
          and manifest.environment_id = new.environment_id
          and manifest.execution_contract_id = new.execution_contract_id
          and manifest.execution_contract_digest = new.execution_contract_digest
          and manifest.manifest_digest = new.evidence_manifest_digest
          and manifest.test_ir_digest = new.test_ir_digest
          and manifest.plan_digest = new.plan_digest
          and manifest.outcome = 'PASSED'
          and manifest.completeness = 'COMPLETE'
          and manifest.integrity = 'VERIFIED'
      ) then
        raise exception 'passed debug run requires exact verified evidence';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_assertion_result_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      assertion_contract jsonb;
      contract_created_at timestamptz;
      contract_deadline timestamptz;
    begin
      select item, contract.created_at, contract.execution_deadline
      into assertion_contract, contract_created_at, contract_deadline
      from atlas.debug_run run
      join atlas.execution_contract contract
        on contract.id = new.execution_contract_id
       and contract.debug_run_id = run.id,
           jsonb_array_elements(run.test_ir -> 'assertions') as item
      where run.id = new.debug_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
        and run.environment_id = new.environment_id
        and item ->> 'assertionId' = new.assertion_id;
      if not found
        or assertion_contract ->> 'nodeId' <> new.node_id
        or assertion_contract ->> 'strength' <> new.strength
        or assertion_contract ->> 'evaluatorVersionRef'
          <> new.evaluator_version_ref
        or new.observed_at < contract_created_at
        or new.observed_at > contract_deadline
        or new.created_at < new.observed_at
        or new.created_at > contract_deadline
        or cardinality(new.evidence_refs) <> (
          select count(distinct evidence_id)
          from unnest(new.evidence_refs) evidence_id
        )
      then
        raise exception 'assertion result does not match the frozen test ir';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_evidence_artifact_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      contract_created_at timestamptz;
      contract_deadline timestamptz;
    begin
      select created_at, execution_deadline
      into contract_created_at, contract_deadline
      from atlas.execution_contract
      where id = new.execution_contract_id
        and debug_run_id = new.debug_run_id
        and tenant_id = new.tenant_id
        and project_id = new.project_id
        and environment_id = new.environment_id;
      if not found
        or new.captured_at < contract_created_at
        or new.captured_at > contract_deadline
        or new.created_at < new.captured_at
        or new.created_at > contract_deadline
      then
        raise exception 'evidence artifact is outside the execution contract window';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_evidence_manifest_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      run_record record;
      result_count bigint;
      declared_count bigint;
      passed_count bigint;
      failed_count bigint;
      inconclusive_count bigint;
      expected_completeness text;
      expected_integrity text;
      expected_outcome text;
    begin
      select run.lifecycle, run.execution_contract_id,
             run.execution_contract_digest, run.test_ir_digest,
             run.plan_digest, run.test_ir, contract.fixture_run_id,
             contract.fixture_manifest_digest, contract.created_at,
             contract.execution_deadline
      into run_record
      from atlas.debug_run run
      join atlas.execution_contract contract
        on contract.id = new.execution_contract_id
       and contract.debug_run_id = run.id
      where run.id = new.debug_run_id
        and run.tenant_id = new.tenant_id
        and run.project_id = new.project_id
        and run.environment_id = new.environment_id
      for share of run;
      if not found
        or run_record.lifecycle <> 'RUNNING'
        or run_record.execution_contract_id <> new.execution_contract_id
        or run_record.execution_contract_digest <> new.execution_contract_digest
        or run_record.test_ir_digest <> new.test_ir_digest
        or run_record.plan_digest <> new.plan_digest
        or run_record.fixture_run_id <> new.fixture_run_id
        or run_record.fixture_manifest_digest <> new.fixture_manifest_digest
        or new.finalized_at < run_record.created_at
        or new.finalized_at > run_record.execution_deadline
        or new.created_at < new.finalized_at
        or new.created_at > run_record.execution_deadline
      then
        raise exception 'evidence manifest does not match a running debug run';
      end if;

      select jsonb_array_length(run_record.test_ir -> 'assertions')
      into declared_count;
      select count(*),
             count(*) filter (where status = 'PASSED'),
             count(*) filter (where status = 'FAILED'),
             count(*) filter (where status = 'INCONCLUSIVE')
      into result_count, passed_count, failed_count, inconclusive_count
      from atlas.assertion_result
      where execution_contract_id = new.execution_contract_id;
      if result_count <> new.passed_assertions
                       + new.failed_assertions
                       + new.inconclusive_assertions
        or passed_count <> new.passed_assertions
        or failed_count <> new.failed_assertions
        or inconclusive_count <> new.inconclusive_assertions
        or result_count <> jsonb_array_length(new.manifest -> 'assertionResults')
      then
        raise exception 'evidence manifest assertion counts are inconsistent';
      end if;

      if jsonb_array_length(new.manifest -> 'assertionResults') <> (
        select count(distinct item ->> 'id')
        from jsonb_array_elements(new.manifest -> 'assertionResults') item
      ) or exists (
        select 1
        from jsonb_array_elements(new.manifest -> 'assertionResults') item
        where not exists (
          select 1
          from atlas.assertion_result result
          where result.execution_contract_id = new.execution_contract_id
            and result.id = (item ->> 'id')::uuid
            and result.result = item
        )
      ) then
        raise exception 'evidence manifest contains an unknown assertion result';
      end if;
      if jsonb_array_length(new.manifest -> 'artifacts') <> (
        select count(distinct item ->> 'id')
        from jsonb_array_elements(new.manifest -> 'artifacts') item
      ) or exists (
        select 1
        from jsonb_array_elements(new.manifest -> 'artifacts') item
        where not exists (
          select 1
          from atlas.evidence_artifact artifact
          where artifact.execution_contract_id = new.execution_contract_id
            and artifact.id = (item ->> 'id')::uuid
            and artifact.kind = item ->> 'kind'
            and artifact.content_digest = item ->> 'contentDigest'
            and artifact.size_bytes = (item ->> 'sizeBytes')::bigint
            and artifact.mime_type = item ->> 'mimeType'
            and artifact.redaction_policy_digest
              = item ->> 'redactionPolicyDigest'
            and artifact.integrity = item ->> 'integrity'
            and artifact.required = (item ->> 'required')::boolean
            and artifact.captured_at = (item ->> 'capturedAt')::timestamptz
        )
      ) then
        raise exception 'evidence manifest contains an unknown artifact';
      end if;

      if jsonb_array_length(new.manifest -> 'missingAssertionIds') <> (
        select count(distinct assertion_id)
        from jsonb_array_elements_text(
          new.manifest -> 'missingAssertionIds'
        ) missing(assertion_id)
      ) or exists (
        select 1
        from jsonb_array_elements(run_record.test_ir -> 'assertions') spec
        where not exists (
          select 1
          from atlas.assertion_result result
          where result.execution_contract_id = new.execution_contract_id
            and result.assertion_id = spec ->> 'assertionId'
        ) and not exists (
          select 1
          from jsonb_array_elements_text(
            new.manifest -> 'missingAssertionIds'
          ) missing(assertion_id)
          where missing.assertion_id = spec ->> 'assertionId'
        )
      ) or exists (
        select 1
        from jsonb_array_elements_text(
          new.manifest -> 'missingAssertionIds'
        ) missing(assertion_id)
        where not exists (
          select 1
          from jsonb_array_elements(run_record.test_ir -> 'assertions') spec
          where spec ->> 'assertionId' = missing.assertion_id
            and not exists (
              select 1
              from atlas.assertion_result result
              where result.execution_contract_id = new.execution_contract_id
                and result.assertion_id = missing.assertion_id
            )
        )
      ) then
        raise exception 'evidence manifest missing assertion IDs are inconsistent';
      end if;

      if exists (
        select 1
        from atlas.assertion_result result,
             unnest(result.evidence_refs) evidence_id
        where result.execution_contract_id = new.execution_contract_id
          and not exists (
            select 1
            from atlas.evidence_artifact artifact
            where artifact.execution_contract_id = new.execution_contract_id
              and artifact.id = evidence_id
          )
      ) or exists (
        select 1
        from atlas.assertion_result result,
             unnest(result.evidence_refs) evidence_id
        where result.execution_contract_id = new.execution_contract_id
          and not exists (
            select 1
            from jsonb_array_elements(new.manifest -> 'artifacts') item
            where (item ->> 'id')::uuid = evidence_id
          )
      ) then
        raise exception 'assertion evidence reference is absent from the manifest';
      end if;

      if exists (
        select 1
        from jsonb_array_elements(run_record.test_ir -> 'assertions') spec
        where spec ->> 'strength' = 'hard'
          and not exists (
            select 1
            from atlas.assertion_result result
            where result.execution_contract_id = new.execution_contract_id
              and result.assertion_id = spec ->> 'assertionId'
          )
      ) or exists (
        select 1
        from atlas.assertion_result result
        where result.execution_contract_id = new.execution_contract_id
          and result.strength = 'hard'
          and cardinality(result.evidence_refs) = 0
      ) then
        expected_completeness := 'MISSING';
      elsif result_count < declared_count or exists (
        select 1
        from atlas.assertion_result result
        where result.execution_contract_id = new.execution_contract_id
          and cardinality(result.evidence_refs) = 0
      ) then
        expected_completeness := 'PARTIAL';
      else
        expected_completeness := 'COMPLETE';
      end if;

      if exists (
        select 1
        from jsonb_array_elements(new.manifest -> 'artifacts') item
        where item ->> 'integrity' = 'INVALID'
      ) then
        expected_integrity := 'INVALID';
      else
        expected_integrity := 'VERIFIED';
      end if;

      if exists (
        select 1
        from atlas.assertion_result result
        where result.execution_contract_id = new.execution_contract_id
          and result.strength = 'hard'
          and result.status = 'FAILED'
      ) then
        expected_outcome := 'FAILED';
      elsif not exists (
        select 1
        from jsonb_array_elements(run_record.test_ir -> 'assertions') spec
        where spec ->> 'strength' = 'hard'
      ) or exists (
        select 1
        from atlas.assertion_result result
        where result.execution_contract_id = new.execution_contract_id
          and result.strength = 'hard'
          and result.status = 'INCONCLUSIVE'
      ) or expected_completeness <> 'COMPLETE'
        or expected_integrity <> 'VERIFIED'
      then
        expected_outcome := 'INCONCLUSIVE';
      else
        expected_outcome := 'PASSED';
      end if;

      if new.completeness <> expected_completeness
        or new.integrity <> expected_integrity
        or new.outcome <> expected_outcome
      then
        raise exception 'evidence manifest Oracle derivation is inconsistent';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_execution_contract_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      run_record record;
      fixture_record record;
    begin
      select lifecycle, snapshot_status, cancel_requested_at,
             tenant_id, project_id, environment_id, test_case_id,
             semantic_revision, test_ir_digest, plan_digest, compiled_digest,
             execution_deadline, test_ir
      into run_record
      from atlas.debug_run
      where id = new.debug_run_id
      for share;
      if not found
        or run_record.lifecycle <> 'CREATED'
        or run_record.snapshot_status <> 'CURRENT'
        or run_record.cancel_requested_at is not null
        or row(
          run_record.tenant_id, run_record.project_id, run_record.environment_id,
          run_record.test_case_id, run_record.semantic_revision,
          run_record.test_ir_digest, run_record.plan_digest,
          run_record.compiled_digest, run_record.execution_deadline
        ) is distinct from row(
          new.tenant_id, new.project_id, new.environment_id,
          new.test_case_id, new.semantic_revision,
          new.test_ir_digest, new.plan_digest,
          new.compiled_digest, new.execution_deadline
        )
        or (new.contract ->> 'executionDeadline')::timestamptz
          <> new.execution_deadline
        or (new.contract ->> 'createdAt')::timestamptz <> new.created_at
        or jsonb_array_length(new.contract -> 'actors')
          <> jsonb_array_length(run_record.test_ir -> 'actors')
        or exists (
          select 1
          from jsonb_array_elements(new.contract -> 'actors') actor
          where not exists (
            select 1
            from jsonb_array_elements(run_record.test_ir -> 'actors') spec
            where spec ->> 'actorSlot' = actor ->> 'actorSlot'
              and spec ->> 'roleId' = actor ->> 'roleId'
              and spec ->> 'roleKey' = actor ->> 'roleKey'
              and (spec ->> 'roleRevision')::bigint
                = (actor ->> 'roleRevision')::bigint
          )
        )
        or exists (
          select 1
          from jsonb_array_elements(new.contract -> 'actors') actor
          group by actor ->> 'actorSlot'
          having count(*) > 1
        )
        or exists (
          select 1
          from jsonb_array_elements(new.contract -> 'actors') actor
          group by actor ->> 'accountLeaseId'
          having count(*) > 1
        )
      then
        raise exception 'execution contract does not match a current created debug run';
      end if;

      select run.status, run.run_kind, run.environment_id, run.execution_id,
             run.blueprint_version_id, run.execution_deadline,
             manifest.plan_digest, manifest.manifest_digest, manifest.manifest,
             version.content_digest,
             definition.blueprint_key || '@' || version.version as version_ref
      into fixture_record
      from atlas.fixture_run run
      join atlas.fixture_manifest manifest on manifest.fixture_run_id = run.id
      join atlas.data_blueprint_version version
        on version.id = run.blueprint_version_id
      join atlas.data_blueprint_definition definition
        on definition.id = version.blueprint_id
      where run.id = new.fixture_run_id
      for share of run, manifest, version, definition;
      if not found
        or fixture_record.status <> 'READY'
        or fixture_record.run_kind <> 'EXECUTION'
        or fixture_record.environment_id <> new.environment_id
        or fixture_record.execution_id
          <> 'debug-run:' || new.debug_run_id::text
        or fixture_record.execution_deadline < new.execution_deadline
        or fixture_record.manifest_digest <> new.fixture_manifest_digest
        or new.contract -> 'fixture' ->> 'blueprintVersionId'
          <> fixture_record.blueprint_version_id::text
        or new.contract -> 'fixture' ->> 'blueprintVersionRef'
          <> fixture_record.version_ref
        or new.contract -> 'fixture' ->> 'blueprintContentDigest'
          <> fixture_record.content_digest
        or new.contract -> 'fixture' ->> 'fixturePlanDigest'
          <> fixture_record.plan_digest
        or new.contract -> 'fixture' ->> 'blueprintVersionId'
          <> run_record.test_ir -> 'fixture' ->> 'blueprintVersionId'
        or new.contract -> 'fixture' ->> 'blueprintVersionRef'
          <> run_record.test_ir -> 'fixture' ->> 'blueprintVersionRef'
        or new.contract -> 'fixture' ->> 'blueprintContentDigest'
          <> run_record.test_ir -> 'fixture' ->> 'contentDigest'
        or exists (
          select 1
          from jsonb_object_keys(
            run_record.test_ir -> 'fixture' -> 'requiredExports'
          ) as required_export(export_key)
          where not (fixture_record.manifest -> 'exports' ? export_key)
        )
      then
        raise exception 'execution contract fixture binding is stale';
      end if;
      return new;
    end;
    $$
    """,
    """
    create function atlas.guard_execution_actor_binding_insert()
    returns trigger
    language plpgsql
    set search_path = pg_catalog, atlas
    as $$
    declare
      contract_worker text;
      contract_created_at timestamptz;
      contract_deadline timestamptz;
      lease_record record;
      session_valid boolean;
      contract_fixture_run_id uuid;
      actor_contract jsonb;
    begin
      select worker_identity, created_at, execution_deadline, fixture_run_id,
             (
               select item
               from jsonb_array_elements(contract -> 'actors') as item
               where item ->> 'actorSlot' = new.actor_slot
             )
      into contract_worker, contract_created_at, contract_deadline,
           contract_fixture_run_id,
           actor_contract
      from atlas.execution_contract
      where id = new.execution_contract_id
        and debug_run_id = new.debug_run_id
        and tenant_id = new.tenant_id
        and project_id = new.project_id
        and environment_id = new.environment_id;
      if not found then
        raise exception 'execution actor requires a matching contract';
      end if;
      if actor_contract is null
        or actor_contract ->> 'roleId' <> new.role_id::text
        or (actor_contract ->> 'roleRevision')::bigint <> new.role_revision
        or actor_contract ->> 'accountLeaseId' <> new.account_lease_id::text
        or actor_contract ->> 'accountHandle' <> new.account_handle
        or (actor_contract ->> 'fencingToken')::bigint <> new.fencing_token
        or actor_contract ->> 'browserContextRef' <> new.browser_context_ref
        or new.bound_at <> contract_created_at
      then
        raise exception 'execution actor row does not match the frozen contract';
      end if;
      if not exists (
        select 1
        from atlas.fixture_actor_binding binding
        where binding.fixture_run_id = contract_fixture_run_id
          and binding.actor_slot = new.actor_slot
          and binding.account_lease_id = new.account_lease_id
          and binding.fencing_token = new.fencing_token
          and binding.tenant_id = new.tenant_id
          and binding.project_id = new.project_id
          and binding.environment_id = new.environment_id
      ) then
        raise exception 'execution actor does not match the fixture actor binding';
      end if;

      select al.status, al.account_handle, al.fencing_token, al.worker_id,
             al.execution_id,
             al.expires_at, al.max_expires_at, ap.role_id, tr.revision,
             tr.status as role_status
      into lease_record
      from atlas.account_lease al
      join atlas.account_pool ap on ap.id = al.pool_id
      join atlas.test_role tr on tr.id = ap.role_id
      where al.id = new.account_lease_id
        and al.tenant_id = new.tenant_id
        and al.project_id = new.project_id
        and al.environment_id = new.environment_id;
      if not found
        or lease_record.status <> 'ACTIVE'
        or lease_record.account_handle <> new.account_handle
        or lease_record.fencing_token <> new.fencing_token
        or lease_record.worker_id <> contract_worker
        or lease_record.execution_id
          <> 'debug-run:' || new.debug_run_id::text
        or lease_record.expires_at < contract_deadline
        or lease_record.max_expires_at < contract_deadline
        or lease_record.role_id <> new.role_id
        or lease_record.revision <> new.role_revision
        or lease_record.role_status <> 'ACTIVE'
      then
        raise exception 'execution actor lease, role, or fence is stale';
      end if;

      select exists (
        select 1
        from atlas.browser_session_artifact session
        where session.browser_context_ref = new.browser_context_ref
          and session.tenant_id = new.tenant_id
          and session.project_id = new.project_id
          and session.environment_id = new.environment_id
          and session.lease_id = new.account_lease_id
          and session.lease_fence = new.fencing_token
          and session.worker_identity = contract_worker
          and session.status = 'READY'
          and session.expires_at >= contract_deadline
      ) into session_valid;
      if not session_valid then
        raise exception 'execution actor browser session is not ready or is stale';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger debug_run_runtime_guard_update
      before update on atlas.debug_run
      for each row execute function atlas.guard_debug_run_runtime_update()
    """,
    """
    create trigger execution_contract_guard_insert
      before insert on atlas.execution_contract
      for each row execute function atlas.guard_execution_contract_insert()
    """,
    """
    create trigger execution_contract_prevent_mutation
      before update or delete on atlas.execution_contract
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger execution_actor_binding_guard_insert
      before insert on atlas.execution_contract_actor_binding
      for each row execute function atlas.guard_execution_actor_binding_insert()
    """,
    """
    create trigger execution_actor_binding_prevent_mutation
      before update or delete on atlas.execution_contract_actor_binding
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger assertion_result_prevent_mutation
      before update or delete on atlas.assertion_result
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger assertion_result_guard_insert
      before insert on atlas.assertion_result
      for each row execute function atlas.guard_assertion_result_insert()
    """,
    """
    create trigger evidence_artifact_prevent_mutation
      before update or delete on atlas.evidence_artifact
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger evidence_artifact_guard_insert
      before insert on atlas.evidence_artifact
      for each row execute function atlas.guard_evidence_artifact_insert()
    """,
    """
    create trigger evidence_manifest_prevent_mutation
      before update or delete on atlas.evidence_manifest
      for each row execute function atlas.prevent_fact_mutation()
    """,
    """
    create trigger evidence_manifest_guard_insert
      before insert on atlas.evidence_manifest
      for each row execute function atlas.guard_evidence_manifest_insert()
    """,
    """
    create index execution_contract_project_created_idx
      on atlas.execution_contract (
        tenant_id, project_id, created_at desc, id desc
      )
    """,
    """
    create index execution_contract_fixture_scope_fk_idx
      on atlas.execution_contract (
        fixture_run_id, tenant_id, project_id, environment_id
      )
    """,
    """
    create index execution_actor_contract_scope_fk_idx
      on atlas.execution_contract_actor_binding (
        execution_contract_id, debug_run_id, tenant_id, project_id, environment_id
      )
    """,
    """
    create index execution_actor_role_scope_fk_idx
      on atlas.execution_contract_actor_binding (role_id, tenant_id, project_id)
    """,
    """
    create index assertion_result_run_idx
      on atlas.assertion_result (debug_run_id, assertion_id)
    """,
    """
    create index assertion_result_contract_scope_fk_idx
      on atlas.assertion_result (
        execution_contract_id, debug_run_id, tenant_id, project_id, environment_id
      )
    """,
    """
    create index evidence_artifact_contract_created_idx
      on atlas.evidence_artifact (
        execution_contract_id, captured_at, id
      )
    """,
    """
    create index evidence_manifest_project_finalized_idx
      on atlas.evidence_manifest (
        tenant_id, project_id, finalized_at desc, id desc
      )
    """,
    """
    create index evidence_manifest_verified_pass_idx
      on atlas.evidence_manifest (
        tenant_id, project_id, debug_run_id, finalized_at desc
      )
      where outcome = 'PASSED'
        and completeness = 'COMPLETE'
        and integrity = 'VERIFIED'
    """,
    "alter table atlas.execution_contract enable row level security",
    "alter table atlas.execution_contract force row level security",
    "alter table atlas.execution_contract_actor_binding enable row level security",
    "alter table atlas.execution_contract_actor_binding force row level security",
    "alter table atlas.assertion_result enable row level security",
    "alter table atlas.assertion_result force row level security",
    "alter table atlas.evidence_artifact enable row level security",
    "alter table atlas.evidence_artifact force row level security",
    "alter table atlas.evidence_manifest enable row level security",
    "alter table atlas.evidence_manifest force row level security",
    """
    create policy execution_contract_tenant_isolation
      on atlas.execution_contract for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy execution_actor_binding_tenant_isolation
      on atlas.execution_contract_actor_binding for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy assertion_result_tenant_isolation
      on atlas.assertion_result for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy evidence_artifact_tenant_isolation
      on atlas.evidence_artifact for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    """
    create policy evidence_manifest_tenant_isolation
      on atlas.evidence_manifest for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.execution_contract from atlas_app",
    "revoke all on atlas.execution_contract_actor_binding from atlas_app",
    "revoke all on atlas.assertion_result from atlas_app",
    "revoke all on atlas.evidence_artifact from atlas_app",
    "revoke all on atlas.evidence_manifest from atlas_app",
    "grant select, insert on atlas.execution_contract to atlas_app",
    "grant select, insert on atlas.execution_contract_actor_binding to atlas_app",
    "grant select, insert on atlas.assertion_result to atlas_app",
    "grant select, insert on atlas.evidence_artifact to atlas_app",
    "grant select, insert on atlas.evidence_manifest to atlas_app",
)


def upgrade() -> None:
    """Create tenant-isolated immutable runtime and evidence facts."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Remove runtime evidence facts and restore the P4 DebugRun shape."""

    op.execute("drop trigger if exists debug_run_runtime_guard_update on atlas.debug_run")
    op.execute("drop function if exists atlas.guard_debug_run_runtime_update()")
    op.execute("alter table atlas.debug_run disable trigger debug_run_guard_update")
    op.execute(
        """
        update atlas.debug_run
        set outcome = case
              when outcome = 'PASSED' then 'INCONCLUSIVE'
              else outcome
            end,
            evidence_manifest_id = null,
            evidence_manifest_digest = null,
            failure_code = case
              when outcome = 'PASSED' then 'P6_EVIDENCE_REMOVED'
              else failure_code
            end,
            failure_detail = case
              when outcome = 'PASSED'
                then 'P6 evidence was removed by the schema downgrade.'
              else failure_detail
            end,
            revision = revision + 1
        where evidence_manifest_id is not null
        """
    )
    op.execute("alter table atlas.debug_run enable trigger debug_run_guard_update")
    op.execute(
        "alter table atlas.debug_run "
        "drop constraint if exists debug_run_evidence_manifest_scope_fk, "
        "drop constraint if exists debug_run_execution_contract_scope_fk, "
        "drop constraint if exists debug_run_runtime_reference_shape, "
        "drop constraint if exists debug_run_runtime_digest_valid"
    )
    op.execute("drop table if exists atlas.evidence_manifest")
    op.execute("drop table if exists atlas.evidence_artifact")
    op.execute("drop table if exists atlas.assertion_result")
    op.execute("drop table if exists atlas.execution_contract_actor_binding")
    op.execute("drop table if exists atlas.execution_contract")
    op.execute(
        "alter table atlas.debug_run "
        "drop column if exists execution_contract_digest, "
        "drop column if exists execution_contract_id"
    )
    op.execute("drop function if exists atlas.guard_execution_actor_binding_insert()")
    op.execute("drop function if exists atlas.guard_execution_contract_insert()")
    op.execute("drop function if exists atlas.guard_assertion_result_insert()")
    op.execute("drop function if exists atlas.guard_evidence_artifact_insert()")
    op.execute("drop function if exists atlas.guard_evidence_manifest_insert()")
    op.execute(
        "alter table atlas.fixture_manifest "
        "drop constraint if exists fixture_manifest_runtime_scope_unique"
    )
