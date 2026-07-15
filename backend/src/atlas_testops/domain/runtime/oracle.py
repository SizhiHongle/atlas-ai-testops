"""Pure builders and Oracle rules for trusted DebugRun evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from atlas_testops.core.contracts import new_entity_id
from atlas_testops.domain.case import DebugRun, TestIR, canonical_digest
from atlas_testops.domain.runtime.models import (
    AssertionResult,
    AssertionResultInput,
    AssertionStatus,
    BindDebugExecution,
    EvidenceArtifact,
    EvidenceArtifactInput,
    EvidenceCompleteness,
    EvidenceIntegrity,
    EvidenceManifest,
    ExecutionActorBinding,
    ExecutionContract,
    FinalizeDebugEvidence,
    FixtureExecutionBinding,
    OracleOutcome,
    assertion_result_digest,
    evidence_manifest_digest,
    execution_contract_digest,
)
from atlas_testops.domain.workflow import OracleStrength


def expected_assertion_digest(test_ir: TestIR, assertion_id: str) -> str:
    """Derive the reviewed expected program from the frozen graph node."""

    assertions = {item.assertion_id: item for item in test_ir.assertions}
    assertion = assertions.get(assertion_id)
    if assertion is None:
        raise ValueError("assertion is not part of the frozen Test IR")
    nodes = {item.id: item for item in test_ir.workflow.nodes}
    node = nodes.get(assertion.node_id)
    if node is None:
        raise ValueError("assertion node is missing from the frozen Test IR")
    return canonical_digest(
        {
            "assertionId": assertion.assertion_id,
            "nodeId": assertion.node_id,
            "evaluatorVersionRef": assertion.evaluator_version_ref,
            "strength": assertion.strength.value,
            "params": node.params,
        }
    )


def build_execution_contract(
    *,
    contract_id: UUID,
    run: DebugRun,
    command: BindDebugExecution,
    actors: tuple[ExecutionActorBinding, ...],
    fixture: FixtureExecutionBinding,
    created_at: datetime,
) -> ExecutionContract:
    """Build a self-verifying runtime binding from database-checked facts."""

    provisional = ExecutionContract.model_construct(
        id=contract_id,
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        environment_id=run.environment_id,
        debug_run_id=run.id,
        test_case_id=run.test_case_id,
        semantic_revision=run.semantic_revision,
        test_ir_digest=run.test_ir_digest,
        plan_digest=run.plan_digest,
        compiled_digest=run.compiled_digest,
        actors=actors,
        fixture=fixture,
        browser=command.browser,
        model=command.model,
        tools=command.tools,
        worker_identity=command.worker_identity,
        execution_deadline=run.execution_deadline,
        created_at=created_at,
        content_digest="sha256:" + "0" * 64,
    )
    return ExecutionContract(
        id=contract_id,
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        environment_id=run.environment_id,
        debug_run_id=run.id,
        test_case_id=run.test_case_id,
        semantic_revision=run.semantic_revision,
        test_ir_digest=run.test_ir_digest,
        plan_digest=run.plan_digest,
        compiled_digest=run.compiled_digest,
        actors=actors,
        fixture=fixture,
        browser=command.browser,
        model=command.model,
        tools=command.tools,
        worker_identity=command.worker_identity,
        execution_deadline=run.execution_deadline,
        created_at=created_at,
        content_digest=execution_contract_digest(provisional),
    )


def build_assertion_result(
    *,
    test_ir: TestIR,
    value: AssertionResultInput,
) -> AssertionResult:
    """Validate a worker result against the immutable assertion program."""

    specifications = {item.assertion_id: item for item in test_ir.assertions}
    specification = specifications.get(value.assertion_id)
    if specification is None:
        raise ValueError("assertion result is not declared by the frozen Test IR")
    expected_digest = expected_assertion_digest(test_ir, value.assertion_id)
    if value.expected_digest != expected_digest:
        raise ValueError("assertion expectedDigest does not match the frozen program")
    if value.evaluator_version_ref != specification.evaluator_version_ref:
        raise ValueError("assertion evaluator version does not match the frozen program")
    result_id = new_entity_id()
    provisional = AssertionResult.model_construct(
        id=result_id,
        assertion_id=value.assertion_id,
        node_id=specification.node_id,
        strength=specification.strength,
        status=value.status,
        expected_digest=value.expected_digest,
        actual_safe_summary=value.actual_safe_summary,
        evaluator_version_ref=value.evaluator_version_ref,
        evidence_refs=value.evidence_refs,
        observed_at=value.observed_at,
        duration_ms=value.duration_ms,
        result_digest="sha256:" + "0" * 64,
    )
    return AssertionResult(
        id=result_id,
        assertion_id=value.assertion_id,
        node_id=specification.node_id,
        strength=specification.strength,
        status=value.status,
        expected_digest=value.expected_digest,
        actual_safe_summary=value.actual_safe_summary,
        evaluator_version_ref=value.evaluator_version_ref,
        evidence_refs=value.evidence_refs,
        observed_at=value.observed_at,
        duration_ms=value.duration_ms,
        result_digest=assertion_result_digest(provisional),
    )


def build_evidence_manifest(
    *,
    manifest_id: UUID,
    run: DebugRun,
    contract: ExecutionContract,
    command: FinalizeDebugEvidence,
) -> tuple[EvidenceManifest, tuple[EvidenceArtifactInput, ...]]:
    """Derive a fail-closed result and immutable evidence root."""

    if command.execution_contract_id != contract.id:
        raise ValueError("finalization references a different execution contract")
    if command.execution_contract_digest != contract.content_digest:
        raise ValueError("finalization execution contract digest is stale")
    if command.finalized_at < contract.created_at:
        raise ValueError("evidence finalization cannot predate the execution contract")
    if command.finalized_at > contract.execution_deadline:
        raise ValueError("evidence finalization cannot exceed the execution deadline")
    if any(
        item.observed_at < contract.created_at
        or item.observed_at > command.finalized_at
        for item in command.assertion_results
    ):
        raise ValueError("assertion observation is outside the execution window")
    if any(
        item.captured_at < contract.created_at
        or item.captured_at > command.finalized_at
        for item in command.artifacts
    ):
        raise ValueError("evidence capture is outside the execution window")

    artifacts_by_id = {item.id: item for item in command.artifacts}
    results = tuple(
        build_assertion_result(test_ir=run.test_ir, value=item)
        for item in command.assertion_results
    )
    for result in results:
        unknown = set(result.evidence_refs).difference(artifacts_by_id)
        if unknown:
            raise ValueError("assertion result references an unknown evidence artifact")

    declared = {item.assertion_id: item for item in run.test_ir.assertions}
    present = {item.assertion_id for item in results}
    missing = tuple(sorted(set(declared).difference(present)))
    missing_hard = any(
        declared[assertion_id].strength is OracleStrength.HARD
        for assertion_id in missing
    )
    hard_without_evidence = any(
        item.strength is OracleStrength.HARD and not item.evidence_refs
        for item in results
    )
    optional_missing = bool(missing) or any(not item.evidence_refs for item in results)
    if missing_hard or hard_without_evidence:
        completeness = EvidenceCompleteness.MISSING
    elif optional_missing:
        completeness = EvidenceCompleteness.PARTIAL
    else:
        completeness = EvidenceCompleteness.COMPLETE

    integrity = (
        EvidenceIntegrity.INVALID
        if any(item.integrity is EvidenceIntegrity.INVALID for item in command.artifacts)
        else EvidenceIntegrity.VERIFIED
    )
    hard_results = tuple(
        item for item in results if item.strength is OracleStrength.HARD
    )
    if any(item.status is AssertionStatus.FAILED for item in hard_results):
        outcome = OracleOutcome.FAILED
    elif (
        not hard_results
        or any(item.status is AssertionStatus.INCONCLUSIVE for item in hard_results)
        or completeness is not EvidenceCompleteness.COMPLETE
        or integrity is not EvidenceIntegrity.VERIFIED
    ):
        outcome = OracleOutcome.INCONCLUSIVE
    else:
        outcome = OracleOutcome.PASSED

    artifacts = tuple(
        EvidenceArtifact(
            id=item.id,
            kind=item.kind,
            content_digest=item.content_digest,
            size_bytes=item.size_bytes,
            mime_type=item.mime_type,
            redaction_policy_digest=item.redaction_policy_digest,
            integrity=item.integrity,
            required=item.required,
            captured_at=item.captured_at,
        )
        for item in command.artifacts
    )
    oracle_results_digest = canonical_digest(
        {
            "assertionResults": [
                item.model_dump(mode="json", by_alias=True) for item in results
            ],
            "missingAssertionIds": list(missing),
        }
    )
    artifact_manifest_digest = canonical_digest(
        {
            "artifacts": [
                item.model_dump(mode="json", by_alias=True) for item in artifacts
            ]
        }
    )
    passed_assertions = sum(item.status is AssertionStatus.PASSED for item in results)
    failed_assertions = sum(item.status is AssertionStatus.FAILED for item in results)
    inconclusive_assertions = sum(
        item.status is AssertionStatus.INCONCLUSIVE for item in results
    )
    provisional = EvidenceManifest.model_construct(
        id=manifest_id,
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        environment_id=run.environment_id,
        debug_run_id=run.id,
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
        test_ir_digest=run.test_ir_digest,
        plan_digest=run.plan_digest,
        fixture_run_id=contract.fixture.fixture_run_id,
        fixture_manifest_digest=contract.fixture.fixture_manifest_digest,
        outcome=outcome,
        completeness=completeness,
        integrity=integrity,
        assertion_results=results,
        missing_assertion_ids=missing,
        artifacts=artifacts,
        oracle_results_digest=oracle_results_digest,
        artifact_manifest_digest=artifact_manifest_digest,
        event_chain_head_digest=command.event_chain_head_digest,
        event_count=command.event_count,
        passed_assertions=passed_assertions,
        failed_assertions=failed_assertions,
        inconclusive_assertions=inconclusive_assertions,
        finalized_at=command.finalized_at,
        content_digest="sha256:" + "0" * 64,
    )
    manifest = EvidenceManifest(
        id=manifest_id,
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        environment_id=run.environment_id,
        debug_run_id=run.id,
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
        test_ir_digest=run.test_ir_digest,
        plan_digest=run.plan_digest,
        fixture_run_id=contract.fixture.fixture_run_id,
        fixture_manifest_digest=contract.fixture.fixture_manifest_digest,
        outcome=outcome,
        completeness=completeness,
        integrity=integrity,
        assertion_results=results,
        missing_assertion_ids=missing,
        artifacts=artifacts,
        oracle_results_digest=oracle_results_digest,
        artifact_manifest_digest=artifact_manifest_digest,
        event_chain_head_digest=command.event_chain_head_digest,
        event_count=command.event_count,
        passed_assertions=passed_assertions,
        failed_assertions=failed_assertions,
        inconclusive_assertions=inconclusive_assertions,
        finalized_at=command.finalized_at,
        content_digest=evidence_manifest_digest(provisional),
    )
    return manifest, command.artifacts
