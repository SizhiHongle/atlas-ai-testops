"""Add snapshot-bound FailureCluster and Classification revisions.

Revision ID: 20260718_0038
Revises: 20260718_0037
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260718_0038"
down_revision: str | None = "20260718_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLUSTER_POLICY_DIGEST = (
    "sha256:f9f6251050af03121ea114ee124b9cf70cb29dbec8768703ef6615ac7a6048a4"
)
_CLASSIFICATION_POLICY_DIGEST = (
    "sha256:7f9d3eff8dfe688cf771a5d232302ee3492b892947eb8e39d1cd016dedc0193b"
)


UPGRADE_STATEMENTS = (
    f"""
    create table atlas.failure_cluster_revision (
      id uuid primary key,
      failure_cluster_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      result_snapshot_id uuid not null,
      revision integer not null,
      fingerprint_version text not null,
      fingerprint_policy_digest text not null,
      fingerprint text not null,
      signal jsonb not null,
      affected_unit_resolution_revision_ids uuid[] not null,
      affected_count integer not null,
      representative_unit_resolution_revision_id uuid not null,
      supersedes_cluster_revision_id uuid,
      projection_watermark timestamptz not null,
      created_at timestamptz not null,
      cluster_hash text not null,
      cluster jsonb not null,
      constraint failure_cluster_revision_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id
      ) references atlas.task_run (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint failure_cluster_revision_snapshot_fk foreign key (
        result_snapshot_id
      ) references atlas.task_result_snapshot(id) on delete restrict,
      constraint failure_cluster_revision_representative_fk foreign key (
        representative_unit_resolution_revision_id
      ) references atlas.unit_resolution_revision(id) on delete restrict,
      constraint failure_cluster_revision_predecessor_fk foreign key (
        supersedes_cluster_revision_id
      ) references atlas.failure_cluster_revision(id) on delete restrict,
      constraint failure_cluster_revision_chain_unique unique (
        failure_cluster_id, revision
      ),
      constraint failure_cluster_revision_id_scope_unique unique (
        id, failure_cluster_id, result_snapshot_id, tenant_id, project_id
      ),
      constraint failure_cluster_revision_hash_unique unique (
        tenant_id, cluster_hash
      ),
      constraint failure_cluster_revision_numbers_valid check (
        revision > 0
        and affected_count between 1 and 10000
        and cardinality(affected_unit_resolution_revision_ids) = affected_count
      ),
      constraint failure_cluster_revision_policy_valid check (
        fingerprint_version = '0.1.0'
        and fingerprint_policy_digest = '{_CLUSTER_POLICY_DIGEST}'
      ),
      constraint failure_cluster_revision_digests_valid check (
        fingerprint_policy_digest ~ '^sha256:[0-9a-f]{{64}}$'
        and fingerprint ~ '^sha256:[0-9a-f]{{64}}$'
        and cluster_hash ~ '^sha256:[0-9a-f]{{64}}$'
      ),
      constraint failure_cluster_revision_json_valid check (
        jsonb_typeof(signal) = 'object'
        and jsonb_typeof(cluster) = 'object'
      )
    )
    """,
    """
    create unique index failure_cluster_revision_initial_input_unique
      on atlas.failure_cluster_revision (
        result_snapshot_id, fingerprint, fingerprint_policy_digest
      )
      where revision = 1
    """,
    """
    create index failure_cluster_revision_snapshot_idx
      on atlas.failure_cluster_revision (
        tenant_id, project_id, result_snapshot_id, affected_count desc, id
      )
    """,
    f"""
    create function atlas.guard_failure_cluster_revision_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      source_snapshot atlas.task_result_snapshot%rowtype;
      source_resolution atlas.unit_resolution_revision%rowtype;
      source_hygiene text;
      source_hygiene_id uuid;
      source_position integer;
      expected_domain text;
      expected_signal_code text;
      expected_signal jsonb;
      expected_affected_ids uuid[] := array[]::uuid[];
      source_is_diagnostic boolean;
      previous atlas.failure_cluster_revision%rowtype;
      resolution_id uuid;
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception
          'FailureCluster insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into source_snapshot
      from atlas.task_result_snapshot snapshot
      where snapshot.id = new.result_snapshot_id
        and snapshot.task_run_id = new.task_run_id
        and snapshot.tenant_id = new.tenant_id
        and snapshot.project_id = new.project_id
      for share;
      if not found
        or new.projection_watermark <> source_snapshot.projection_watermark
        or new.created_at <> transaction_timestamp()
        or new.affected_count <> cardinality(new.affected_unit_resolution_revision_ids)
        or new.representative_unit_resolution_revision_id
          <> all(new.affected_unit_resolution_revision_ids)
      then
        raise exception 'FailureCluster requires an exact immutable Snapshot scope';
      end if;

      if (
        select count(*) <> count(distinct item)
        from unnest(new.affected_unit_resolution_revision_ids) item
      ) then
        raise exception 'FailureCluster affected UnitResolution IDs must be unique';
      end if;

      foreach resolution_id in array source_snapshot.unit_resolution_revision_ids loop
        source_position := array_position(
          source_snapshot.unit_resolution_revision_ids,
          resolution_id
        );
        select * into source_resolution
        from atlas.unit_resolution_revision resolution
        where resolution.id = resolution_id
          and resolution.task_run_id = new.task_run_id
          and resolution.tenant_id = new.tenant_id
          and resolution.project_id = new.project_id
          and resolution.manifest_hash = source_snapshot.manifest_hash;
        if not found or source_position is null then
          raise exception
            'FailureCluster input must be an exact Snapshot UnitResolution';
        end if;

        source_hygiene := source_resolution.data_hygiene;
        if source_snapshot.unit_hygiene_resolution_revision_ids is not null then
          source_hygiene_id :=
            source_snapshot.unit_hygiene_resolution_revision_ids[source_position];
          select hygiene.data_hygiene into source_hygiene
          from atlas.unit_hygiene_resolution_revision hygiene
          where hygiene.id = source_hygiene_id
            and hygiene.execution_unit_id = source_resolution.execution_unit_id
            and hygiene.task_run_id = new.task_run_id
            and hygiene.tenant_id = new.tenant_id
            and hygiene.project_id = new.project_id;
          if not found then
            raise exception
              'FailureCluster Hygiene input must match Snapshot ordinal scope';
          end if;
        end if;

        source_is_diagnostic := (
          source_resolution.effective_verdict <> 'PASSED'
          or source_resolution.stability <> 'STABLE'
          or source_hygiene in ('CLEANUP_FAILED', 'LEAKED')
          or source_resolution.evidence_integrity <> 'VERIFIED'
          or source_resolution.evidence_completeness in ('MISSING', 'PARTIAL')
        );

        if source_hygiene = 'LEAKED' then
          expected_domain := 'CLEANUP';
          expected_signal_code := 'CLEANUP_LEAKED';
        elsif source_hygiene = 'CLEANUP_FAILED' then
          expected_domain := 'CLEANUP';
          expected_signal_code := 'CLEANUP_FAILED';
        elsif source_resolution.evidence_integrity = 'INVALID' then
          expected_domain := 'EVIDENCE';
          expected_signal_code := 'EVIDENCE_INTEGRITY_INVALID';
        elsif source_resolution.evidence_completeness = 'MISSING' then
          expected_domain := 'EVIDENCE';
          expected_signal_code := 'EVIDENCE_REQUIRED_MISSING';
        elsif source_resolution.evidence_completeness = 'PARTIAL' then
          expected_domain := 'EVIDENCE';
          expected_signal_code := 'EVIDENCE_REQUIRED_PARTIAL';
        elsif source_resolution.evidence_integrity = 'UNVERIFIED' then
          expected_domain := 'EVIDENCE';
          expected_signal_code := 'EVIDENCE_INTEGRITY_UNVERIFIED';
        elsif source_resolution.outcome_class = 'POLICY' then
          expected_domain := 'POLICY_SECURITY';
          expected_signal_code := 'POLICY_REJECTED';
        elsif source_resolution.outcome_class = 'DEPENDENCY' then
          expected_domain := 'EXTERNAL_DEPENDENCY';
          expected_signal_code := 'DEPENDENCY_FAILURE';
        elsif source_resolution.outcome_class = 'PLATFORM'
          or source_resolution.stability = 'INFRA_RECOVERED'
        then
          expected_domain := 'INFRASTRUCTURE';
          expected_signal_code := 'INFRASTRUCTURE_FAILURE';
        elsif source_resolution.stability in ('FLAKY_SUSPECT', 'FLAKY_CONFIRMED') then
          expected_domain := 'UNKNOWN';
          expected_signal_code := 'FLAKY_SIGNAL';
        elsif source_resolution.outcome_class = 'AUTOMATION' then
          expected_domain := 'UNKNOWN';
          expected_signal_code := 'AUTOMATION_CAUSE_UNRESOLVED';
        elsif source_resolution.outcome_class = 'BUSINESS' then
          expected_domain := 'UNKNOWN';
          expected_signal_code := 'PRODUCT_OR_SPEC_UNRESOLVED';
        elsif source_resolution.outcome_class = 'USER' then
          expected_domain := 'UNKNOWN';
          expected_signal_code := 'USER_OUTCOME_UNRESOLVED';
        else
          expected_domain := 'UNKNOWN';
          expected_signal_code := 'CAUSE_UNKNOWN';
        end if;

        expected_signal := jsonb_build_object(
          'schemaVersion', 'atlas.failure-signal/0.1',
          'failureDomain', expected_domain,
          'signalCode', expected_signal_code,
          'effectiveVerdict', source_resolution.effective_verdict,
          'outcomeClass', source_resolution.outcome_class,
          'closureReason', source_resolution.closure_reason,
          'dataHygiene', source_hygiene,
          'evidenceCompleteness', source_resolution.evidence_completeness,
          'evidenceIntegrity', source_resolution.evidence_integrity,
          'stability', source_resolution.stability
        );
        if source_is_diagnostic and new.signal is not distinct from expected_signal then
          expected_affected_ids := array_append(expected_affected_ids, resolution_id);
        end if;
      end loop;

      if new.affected_unit_resolution_revision_ids is distinct from expected_affected_ids
        or new.representative_unit_resolution_revision_id
          is distinct from expected_affected_ids[1]
      then
        raise exception
          'FailureCluster must contain the exact manifest-ordered signal group';
      end if;

      if new.fingerprint_version <> '0.1.0'
        or new.fingerprint_policy_digest <> '{_CLUSTER_POLICY_DIGEST}'
        or new.fingerprint <> atlas.task_sha256_json(new.signal)
      then
        raise exception 'FailureCluster fingerprint does not match frozen Policy';
      end if;

      if new.revision = 1 then
        if new.supersedes_cluster_revision_id is not null then
          raise exception 'first FailureCluster revision cannot supersede another';
        end if;
      else
        select * into previous
        from atlas.failure_cluster_revision cluster
        where cluster.failure_cluster_id = new.failure_cluster_id
        order by cluster.revision desc
        limit 1
        for share;
        if not found
          or new.revision <> previous.revision + 1
          or new.supersedes_cluster_revision_id <> previous.id
          or new.result_snapshot_id <> previous.result_snapshot_id
          or new.tenant_id <> previous.tenant_id
          or new.project_id <> previous.project_id
          or new.task_run_id <> previous.task_run_id
        then
          raise exception 'FailureCluster revision chain is invalid';
        end if;
      end if;

      if atlas.task_json_object_size(new.cluster) <> 19
        or new.cluster ->> 'schemaVersion'
          <> 'atlas.failure-cluster-revision/0.1'
        or (new.cluster ->> 'id')::uuid is distinct from new.id
        or (new.cluster ->> 'failureClusterId')::uuid
          is distinct from new.failure_cluster_id
        or (new.cluster ->> 'tenantId')::uuid is distinct from new.tenant_id
        or (new.cluster ->> 'projectId')::uuid is distinct from new.project_id
        or (new.cluster ->> 'taskRunId')::uuid is distinct from new.task_run_id
        or (new.cluster ->> 'resultSnapshotId')::uuid
          is distinct from new.result_snapshot_id
        or (new.cluster ->> 'revision')::integer is distinct from new.revision
        or new.cluster ->> 'fingerprintVersion'
          is distinct from new.fingerprint_version
        or new.cluster ->> 'fingerprintPolicyDigest'
          is distinct from new.fingerprint_policy_digest
        or new.cluster ->> 'fingerprint' is distinct from new.fingerprint
        or new.cluster -> 'signal' is distinct from new.signal
        or array(
          select value::uuid
          from jsonb_array_elements_text(
            new.cluster -> 'affectedUnitResolutionRevisionIds'
          )
        ) is distinct from new.affected_unit_resolution_revision_ids
        or (new.cluster ->> 'affectedCount')::integer
          is distinct from new.affected_count
        or (new.cluster ->> 'representativeUnitResolutionRevisionId')::uuid
          is distinct from new.representative_unit_resolution_revision_id
        or (new.cluster ->> 'supersedesClusterRevisionId')::uuid
          is distinct from new.supersedes_cluster_revision_id
        or (new.cluster ->> 'projectionWatermark')::timestamptz
          is distinct from new.projection_watermark
        or (new.cluster ->> 'createdAt')::timestamptz is distinct from new.created_at
        or new.cluster ->> 'clusterHash' is distinct from new.cluster_hash
        or atlas.task_sha256_json(
          new.cluster - array[
            'id', 'failureClusterId', 'revision',
            'supersedesClusterRevisionId', 'createdAt', 'clusterHash'
          ]
        ) is distinct from new.cluster_hash
      then
        raise exception 'FailureCluster persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger failure_cluster_revision_guard_insert
      before insert on atlas.failure_cluster_revision
      for each row execute function atlas.guard_failure_cluster_revision_insert()
    """,
    """
    create trigger failure_cluster_revision_prevent_mutation
      before update or delete on atlas.failure_cluster_revision
      for each row execute function atlas.prevent_fact_mutation()
    """,
    "alter table atlas.failure_cluster_revision enable row level security",
    "alter table atlas.failure_cluster_revision force row level security",
    """
    create policy failure_cluster_revision_tenant_isolation
      on atlas.failure_cluster_revision for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.failure_cluster_revision from atlas_app",
    "grant select, insert on atlas.failure_cluster_revision to atlas_app",
    """
    revoke all on function atlas.guard_failure_cluster_revision_insert()
      from public, atlas_app, atlas_dispatcher
    """,
    f"""
    create table atlas.failure_classification_revision (
      id uuid primary key,
      failure_classification_id uuid not null,
      tenant_id uuid not null,
      project_id uuid not null,
      task_run_id uuid not null,
      result_snapshot_id uuid not null,
      failure_cluster_revision_id uuid not null,
      revision integer not null,
      failure_domain text not null,
      hypothesis_code text not null,
      hypothesis text not null,
      confidence_numerator integer not null,
      supporting_evidence_refs jsonb not null,
      contradicting_evidence_refs jsonb not null,
      evidence_gap_codes text[] not null,
      judgment_state text not null,
      author_kind text not null,
      authored_by uuid,
      model_version_ref text,
      classification_policy_version text not null,
      classification_policy_digest text not null,
      client_mutation_id text not null,
      supersedes_revision_id uuid,
      created_at timestamptz not null,
      classification_hash text not null,
      classification jsonb not null,
      constraint failure_classification_revision_run_scope_fk foreign key (
        task_run_id, tenant_id, project_id
      ) references atlas.task_run (
        id, tenant_id, project_id
      ) on delete restrict,
      constraint failure_classification_revision_snapshot_fk foreign key (
        result_snapshot_id
      ) references atlas.task_result_snapshot(id) on delete restrict,
      constraint failure_classification_revision_cluster_fk foreign key (
        failure_cluster_revision_id
      ) references atlas.failure_cluster_revision(id) on delete restrict,
      constraint failure_classification_revision_predecessor_fk foreign key (
        supersedes_revision_id
      ) references atlas.failure_classification_revision(id) on delete restrict,
      constraint failure_classification_revision_chain_unique unique (
        failure_classification_id, revision
      ),
      constraint failure_classification_revision_mutation_unique unique (
        failure_classification_id, client_mutation_id
      ),
      constraint failure_classification_revision_hash_unique unique (
        tenant_id, classification_hash
      ),
      constraint failure_classification_revision_numbers_valid check (
        revision > 0 and confidence_numerator between 0 and 10000
      ),
      constraint failure_classification_revision_domain_valid check (
        failure_domain in (
          'PRODUCT', 'TEST_SPEC', 'TEST_DATA', 'IDENTITY', 'ENVIRONMENT',
          'INFRASTRUCTURE', 'EXTERNAL_DEPENDENCY', 'AGENT_AUTOMATION',
          'POLICY_SECURITY', 'EVIDENCE', 'CLEANUP', 'UNKNOWN'
        )
      ),
      constraint failure_classification_revision_judgment_valid check (
        judgment_state in (
          'RULE_PROPOSED', 'AI_PROPOSED', 'HUMAN_CONFIRMED',
          'HUMAN_REJECTED', 'HUMAN_REVISED'
        )
        and author_kind in ('SYSTEM_RULE', 'AI_MODEL', 'HUMAN')
      ),
      constraint failure_classification_revision_policy_valid check (
        classification_policy_version = '0.1.0'
        and classification_policy_digest = '{_CLASSIFICATION_POLICY_DIGEST}'
      ),
      constraint failure_classification_revision_strings_valid check (
        hypothesis_code ~ '^[A-Z][A-Z0-9_]{{1,95}}$'
        and char_length(hypothesis) between 1 and 500
        and hypothesis !~ '[[:cntrl:]]'
        and char_length(client_mutation_id) between 8 and 200
        and client_mutation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]*$'
        and (
          model_version_ref is null
          or (
            char_length(model_version_ref) between 3 and 200
            and model_version_ref ~ '^[A-Za-z0-9][A-Za-z0-9._:@/-]*$'
          )
        )
      ),
      constraint failure_classification_revision_digest_valid check (
        classification_policy_digest ~ '^sha256:[0-9a-f]{{64}}$'
        and classification_hash ~ '^sha256:[0-9a-f]{{64}}$'
      ),
      constraint failure_classification_revision_json_valid check (
        jsonb_typeof(supporting_evidence_refs) = 'array'
        and jsonb_array_length(supporting_evidence_refs) between 1 and 256
        and jsonb_typeof(contradicting_evidence_refs) = 'array'
        and jsonb_array_length(contradicting_evidence_refs) between 0 and 256
        and cardinality(evidence_gap_codes) between 0 and 32
        and jsonb_typeof(classification) = 'object'
      )
    )
    """,
    """
    create unique index failure_classification_revision_cluster_initial_unique
      on atlas.failure_classification_revision (failure_cluster_revision_id)
      where revision = 1
    """,
    """
    create index failure_classification_revision_snapshot_idx
      on atlas.failure_classification_revision (
        tenant_id, project_id, result_snapshot_id,
        failure_domain, judgment_state, created_at desc
      )
    """,
    """
    create function atlas.failure_classification_evidence_ref_valid(
      cluster_revision_id uuid,
      evidence_ref jsonb
    )
    returns boolean
    language plpgsql
    stable
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      source_cluster atlas.failure_cluster_revision%rowtype;
      reference_kind text;
      reference_id uuid;
      reference_digest text;
    begin
      if jsonb_typeof(evidence_ref) <> 'object'
        or atlas.task_json_object_size(evidence_ref) <> 3
        or evidence_ref ->> 'kind' not in (
          'UNIT_RESOLUTION', 'UNIT_HYGIENE_RESOLUTION',
          'ATTEMPT_SEAL', 'ATTEMPT_CLOSURE_NOTICE'
        )
        or evidence_ref ->> 'contentDigest' !~ '^sha256:[0-9a-f]{64}$'
      then
        return false;
      end if;
      begin
        reference_id := (evidence_ref ->> 'refId')::uuid;
      exception when others then
        return false;
      end;
      reference_kind := evidence_ref ->> 'kind';
      reference_digest := evidence_ref ->> 'contentDigest';

      select * into source_cluster
      from atlas.failure_cluster_revision cluster
      where cluster.id = cluster_revision_id;
      if not found then
        return false;
      end if;

      if reference_kind = 'UNIT_RESOLUTION' then
        return exists (
          select 1
          from atlas.unit_resolution_revision resolution
          where resolution.id = reference_id
            and resolution.id =
              any(source_cluster.affected_unit_resolution_revision_ids)
            and resolution.input_set_hash = reference_digest
        );
      elsif reference_kind = 'UNIT_HYGIENE_RESOLUTION' then
        return exists (
          select 1
          from atlas.task_result_snapshot snapshot
          join atlas.unit_hygiene_resolution_revision hygiene
            on hygiene.id = reference_id
          join atlas.unit_resolution_revision resolution
            on resolution.execution_unit_id = hygiene.execution_unit_id
           and resolution.id =
             any(source_cluster.affected_unit_resolution_revision_ids)
          where snapshot.id = source_cluster.result_snapshot_id
            and hygiene.id =
              any(snapshot.unit_hygiene_resolution_revision_ids)
            and hygiene.resolution_hash = reference_digest
        );
      elsif reference_kind = 'ATTEMPT_SEAL' then
        return exists (
          select 1
          from atlas.unit_resolution_revision resolution
          join atlas.unit_attempt_result_fact fact
            on fact.seal_id = reference_id
           and fact.seal_id = any(resolution.input_seal_ids)
          where resolution.id =
            any(source_cluster.affected_unit_resolution_revision_ids)
            and fact.content_hash = reference_digest
        );
      else
        return exists (
          select 1
          from atlas.unit_resolution_revision resolution
          join atlas.attempt_closure_notice notice
            on notice.id = reference_id
           and notice.id = any(resolution.input_closure_notice_ids)
          where resolution.id =
            any(source_cluster.affected_unit_resolution_revision_ids)
            and notice.notice_hash = reference_digest
        );
      end if;
    end;
    $$
    """,
    f"""
    create function atlas.guard_failure_classification_revision_insert()
    returns trigger
    language plpgsql
    security definer
    set search_path = pg_catalog, atlas
    as $$
    declare
      source_cluster atlas.failure_cluster_revision%rowtype;
      previous atlas.failure_classification_revision%rowtype;
      evidence_ref jsonb;
      expected_hypothesis text;
      expected_confidence integer;
      expected_gap_codes text[];
    begin
      if atlas.current_tenant_id() is null
        or new.tenant_id <> atlas.current_tenant_id()
      then
        raise exception
          'FailureClassification insertion requires exact tenant context'
          using errcode = '42501';
      end if;

      select * into source_cluster
      from atlas.failure_cluster_revision cluster
      where cluster.id = new.failure_cluster_revision_id
        and cluster.result_snapshot_id = new.result_snapshot_id
        and cluster.task_run_id = new.task_run_id
        and cluster.tenant_id = new.tenant_id
        and cluster.project_id = new.project_id
      for share;
      if not found
        or new.created_at <> transaction_timestamp()
        or new.classification_policy_version <> '0.1.0'
        or new.classification_policy_digest <> '{_CLASSIFICATION_POLICY_DIGEST}'
      then
        raise exception 'FailureClassification requires exact Cluster and Policy scope';
      end if;

      if (
        select count(*) <> count(distinct value)
        from jsonb_array_elements(new.supporting_evidence_refs) item(value)
      ) or (
        select count(*) <> count(distinct value)
        from jsonb_array_elements(new.contradicting_evidence_refs) item(value)
      ) or exists (
        select 1
        from jsonb_array_elements(new.supporting_evidence_refs) supporting(value)
        join jsonb_array_elements(new.contradicting_evidence_refs) contradicting(value)
          on supporting.value = contradicting.value
      ) then
        raise exception 'Classification evidence sets must be unique and disjoint';
      end if;
      if new.supporting_evidence_refs is distinct from (
        select coalesce(
          jsonb_agg(
            value order by
              value ->> 'kind',
              value ->> 'refId',
              value ->> 'contentDigest'
          ),
          '[]'::jsonb
        )
        from jsonb_array_elements(new.supporting_evidence_refs) item(value)
      ) or new.contradicting_evidence_refs is distinct from (
        select coalesce(
          jsonb_agg(
            value order by
              value ->> 'kind',
              value ->> 'refId',
              value ->> 'contentDigest'
          ),
          '[]'::jsonb
        )
        from jsonb_array_elements(new.contradicting_evidence_refs) item(value)
      ) or new.evidence_gap_codes is distinct from array(
        select distinct code
        from unnest(new.evidence_gap_codes) code
        order by code
      ) or exists (
        select 1
        from unnest(new.evidence_gap_codes) code
        where code !~ '^[A-Z0-9_]{{2,96}}$'
          or code !~ '[A-Z0-9]'
      ) then
        raise exception
          'Classification evidence and gap codes must use canonical ordering';
      end if;

      for evidence_ref in
        select value
        from jsonb_array_elements(
          new.supporting_evidence_refs || new.contradicting_evidence_refs
        ) item(value)
      loop
        if not atlas.failure_classification_evidence_ref_valid(
          source_cluster.id,
          evidence_ref
        ) then
          raise exception 'Classification evidence must bind an exact Cluster fact';
        end if;
      end loop;

      if new.revision = 1 then
        if new.supersedes_revision_id is not null
          or new.author_kind <> 'SYSTEM_RULE'
          or new.judgment_state <> 'RULE_PROPOSED'
          or new.authored_by is not null
          or new.model_version_ref is not null
          or new.failure_domain <> source_cluster.signal ->> 'failureDomain'
          or new.hypothesis_code <> source_cluster.signal ->> 'signalCode'
          or not exists (
            select 1
            from jsonb_array_elements(new.supporting_evidence_refs) evidence(value)
            where evidence.value ->> 'kind' = 'UNIT_RESOLUTION'
              and (evidence.value ->> 'refId')::uuid =
                source_cluster.representative_unit_resolution_revision_id
          )
        then
          raise exception 'first Classification must be the exact rule judgment';
        end if;

        case new.hypothesis_code
          when 'CLEANUP_LEAKED' then
            expected_hypothesis :=
              'Cleanup truth contains an explicitly leaked resource.';
            expected_confidence := 10000;
            expected_gap_codes := array[]::text[];
          when 'CLEANUP_FAILED' then
            expected_hypothesis :=
              'Cleanup truth did not reach a terminal successful state.';
            expected_confidence := 10000;
            expected_gap_codes := array[]::text[];
          when 'EVIDENCE_INTEGRITY_INVALID' then
            expected_hypothesis :=
              'Trusted result evidence failed integrity verification.';
            expected_confidence := 10000;
            expected_gap_codes := array[]::text[];
          when 'EVIDENCE_REQUIRED_MISSING' then
            expected_hypothesis :=
              'Required evidence is missing from the trusted result.';
            expected_confidence := 10000;
            expected_gap_codes := array[]::text[];
          when 'EVIDENCE_REQUIRED_PARTIAL' then
            expected_hypothesis :=
              'Required result evidence is only partially complete.';
            expected_confidence := 10000;
            expected_gap_codes := array[]::text[];
          when 'EVIDENCE_INTEGRITY_UNVERIFIED' then
            expected_hypothesis :=
              'Result evidence has not completed integrity verification.';
            expected_confidence := 10000;
            expected_gap_codes := array[]::text[];
          when 'POLICY_REJECTED' then
            expected_hypothesis :=
              'A frozen runtime or security policy rejected the execution.';
            expected_confidence := 9500;
            expected_gap_codes := array[]::text[];
          when 'DEPENDENCY_FAILURE' then
            expected_hypothesis :=
              'The trusted result attributes the outcome to an external dependency.';
            expected_confidence := 9000;
            expected_gap_codes := array['DEPENDENCY_DETAIL_MISSING'];
          when 'INFRASTRUCTURE_FAILURE' then
            expected_hypothesis :=
              'The trusted result indicates an infrastructure failure or recovery.';
            expected_confidence := 9000;
            expected_gap_codes := array['INFRASTRUCTURE_COMPONENT_MISSING'];
          when 'FLAKY_SIGNAL' then
            expected_hypothesis :=
              'Comparable attempts produced an unstable result sequence.';
            expected_confidence := 7500;
            expected_gap_codes := array['ROOT_CAUSE_EVIDENCE_MISSING'];
          when 'AUTOMATION_CAUSE_UNRESOLVED' then
            expected_hypothesis :=
              'Automation-related execution failed without enough evidence for a narrower cause.';
            expected_confidence := 2500;
            expected_gap_codes := array['AUTOMATION_DETAIL_MISSING'];
          when 'PRODUCT_OR_SPEC_UNRESOLVED' then
            expected_hypothesis :=
              'The business assertion failed, but product and test-spec causes remain unresolved.';
            expected_confidence := 2500;
            expected_gap_codes := array['PRODUCT_VS_TEST_SPEC_UNRESOLVED'];
          when 'USER_OUTCOME_UNRESOLVED' then
            expected_hypothesis :=
              'A user-originated outcome prevented a conclusive failure attribution.';
            expected_confidence := 2500;
            expected_gap_codes := array['USER_OUTCOME_DETAIL_MISSING'];
          else
            expected_hypothesis :=
              'Available trusted facts are insufficient for a narrower failure attribution.';
            expected_confidence := 0;
            expected_gap_codes := array['ROOT_CAUSE_EVIDENCE_MISSING'];
        end case;
        if new.hypothesis <> expected_hypothesis
          or new.confidence_numerator <> expected_confidence
          or new.evidence_gap_codes is distinct from expected_gap_codes
        then
          raise exception 'rule Classification does not match frozen Policy';
        end if;
      else
        select * into previous
        from atlas.failure_classification_revision classification
        where classification.failure_classification_id =
          new.failure_classification_id
        order by classification.revision desc
        limit 1
        for share;
        if not found
          or new.revision <> previous.revision + 1
          or new.supersedes_revision_id <> previous.id
          or new.failure_cluster_revision_id <> previous.failure_cluster_revision_id
          or new.result_snapshot_id <> previous.result_snapshot_id
          or new.tenant_id <> previous.tenant_id
          or new.project_id <> previous.project_id
          or new.task_run_id <> previous.task_run_id
          or new.author_kind <> 'HUMAN'
          or new.judgment_state not in (
            'HUMAN_CONFIRMED', 'HUMAN_REJECTED', 'HUMAN_REVISED'
          )
          or new.authored_by is null
          or new.model_version_ref is not null
        then
          raise exception 'human Classification revision chain is invalid';
        end if;
        if new.judgment_state = 'HUMAN_CONFIRMED'
          and (
            new.failure_domain <> previous.failure_domain
            or new.hypothesis_code <> previous.hypothesis_code
            or new.hypothesis <> previous.hypothesis
          )
        then
          raise exception 'HUMAN_CONFIRMED cannot change attribution content';
        end if;
        if new.judgment_state = 'HUMAN_REJECTED'
          and (
            new.failure_domain <> 'UNKNOWN'
            or new.confidence_numerator <> 0
            or jsonb_array_length(new.contradicting_evidence_refs) = 0
          )
        then
          raise exception
            'HUMAN_REJECTED requires UNKNOWN and contradiction evidence';
        end if;
      end if;

      if atlas.task_json_object_size(new.classification) <> 26
        or new.classification ->> 'schemaVersion'
          <> 'atlas.failure-classification-revision/0.1'
        or (new.classification ->> 'id')::uuid is distinct from new.id
        or (new.classification ->> 'failureClassificationId')::uuid
          is distinct from new.failure_classification_id
        or (new.classification ->> 'tenantId')::uuid is distinct from new.tenant_id
        or (new.classification ->> 'projectId')::uuid is distinct from new.project_id
        or (new.classification ->> 'taskRunId')::uuid is distinct from new.task_run_id
        or (new.classification ->> 'resultSnapshotId')::uuid
          is distinct from new.result_snapshot_id
        or (new.classification ->> 'failureClusterRevisionId')::uuid
          is distinct from new.failure_cluster_revision_id
        or (new.classification ->> 'revision')::integer is distinct from new.revision
        or new.classification ->> 'failureDomain' is distinct from new.failure_domain
        or new.classification ->> 'hypothesisCode' is distinct from new.hypothesis_code
        or new.classification ->> 'hypothesis' is distinct from new.hypothesis
        or new.classification -> 'confidence' is distinct from jsonb_build_object(
          'numerator', new.confidence_numerator,
          'denominator', 10000
        )
        or new.classification -> 'supportingEvidenceRefs'
          is distinct from new.supporting_evidence_refs
        or new.classification -> 'contradictingEvidenceRefs'
          is distinct from new.contradicting_evidence_refs
        or array(
          select value
          from jsonb_array_elements_text(
            new.classification -> 'evidenceGapCodes'
          )
        ) is distinct from new.evidence_gap_codes
        or new.classification ->> 'judgmentState'
          is distinct from new.judgment_state
        or new.classification ->> 'authorKind' is distinct from new.author_kind
        or (new.classification ->> 'authoredBy')::uuid is distinct from new.authored_by
        or new.classification ->> 'modelVersionRef'
          is distinct from new.model_version_ref
        or new.classification ->> 'classificationPolicyVersion'
          is distinct from new.classification_policy_version
        or new.classification ->> 'classificationPolicyDigest'
          is distinct from new.classification_policy_digest
        or new.classification ->> 'clientMutationId'
          is distinct from new.client_mutation_id
        or (new.classification ->> 'supersedesRevisionId')::uuid
          is distinct from new.supersedes_revision_id
        or (new.classification ->> 'createdAt')::timestamptz
          is distinct from new.created_at
        or new.classification ->> 'classificationHash'
          is distinct from new.classification_hash
        or atlas.task_sha256_json(
          new.classification - array[
            'id', 'failureClassificationId', 'revision',
            'supersedesRevisionId', 'createdAt', 'classificationHash'
          ]
        ) is distinct from new.classification_hash
      then
        raise exception
          'FailureClassification persisted projection is not canonical';
      end if;
      return new;
    end;
    $$
    """,
    """
    create trigger failure_classification_revision_guard_insert
      before insert on atlas.failure_classification_revision
      for each row execute function
        atlas.guard_failure_classification_revision_insert()
    """,
    """
    create trigger failure_classification_revision_prevent_mutation
      before update or delete on atlas.failure_classification_revision
      for each row execute function atlas.prevent_fact_mutation()
    """,
    "alter table atlas.failure_classification_revision enable row level security",
    "alter table atlas.failure_classification_revision force row level security",
    """
    create policy failure_classification_revision_tenant_isolation
      on atlas.failure_classification_revision for all
      using (tenant_id = (select atlas.current_tenant_id()))
      with check (tenant_id = (select atlas.current_tenant_id()))
    """,
    "revoke all on atlas.failure_classification_revision from atlas_app",
    "grant select, insert on atlas.failure_classification_revision to atlas_app",
    """
    revoke all on function
      atlas.failure_classification_evidence_ref_valid(uuid, jsonb)
      from public, atlas_app, atlas_dispatcher
    """,
    """
    revoke all on function
      atlas.guard_failure_classification_revision_insert()
      from public, atlas_app, atlas_dispatcher
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    do $$
    begin
      if exists (select 1 from atlas.failure_classification_revision)
        or exists (select 1 from atlas.failure_cluster_revision)
      then
        raise exception
          'cannot downgrade while FailureCluster or Classification facts exist';
      end if;
    end;
    $$
    """,
    "drop table atlas.failure_classification_revision",
    "drop function atlas.guard_failure_classification_revision_insert()",
    "drop function atlas.failure_classification_evidence_ref_valid(uuid, jsonb)",
    "drop table atlas.failure_cluster_revision",
    "drop function atlas.guard_failure_cluster_revision_insert()",
)


def upgrade() -> None:
    """Apply the snapshot-bound classification truth layer."""

    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Refuse lossy downgrade when any classification fact exists."""

    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
