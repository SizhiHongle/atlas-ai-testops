"""FailureCluster and append-only Classification contract tests."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.domain.result.test_result_projection_contracts import _resolution
from tests.infrastructure.test_task_run_repository import NOW

from atlas_testops.domain.result import (
    FAILURE_CLASSIFICATION_POLICY_DIGEST,
    FAILURE_CLUSTER_POLICY_DIGEST,
    ClassificationAuthorKind,
    ClassificationConfidence,
    ClassificationJudgmentState,
    DataHygiene,
    EvidenceCompleteness,
    EvidenceIntegrity,
    FailureClassificationRevision,
    FailureClassificationRevisionContent,
    FailureClusterRevision,
    FailureClusterRevisionContent,
    FailureDomain,
    FailureEvidenceKind,
    FailureEvidenceRef,
    OutcomeClass,
    RequestFailureClassificationRevision,
    Stability,
    Verdict,
    failure_classification_revision_hash,
    failure_cluster_fingerprint,
    failure_cluster_revision_hash,
    failure_signal_for,
    is_diagnostic_failure,
    rule_classification_for_signal,
)

_SNAPSHOT_ID = UUID("00000000-0000-7000-8000-000000000031")
_CLUSTER_ID = UUID("00000000-0000-7000-8000-000000000032")
_CLUSTER_REVISION_ID = UUID("00000000-0000-7000-8000-000000000033")
_CLASSIFICATION_ID = UUID("00000000-0000-7000-8000-000000000034")
_CLASSIFICATION_REVISION_ID = UUID("00000000-0000-7000-8000-000000000035")
_ACTOR_ID = UUID("00000000-0000-7000-8000-000000000036")
_OTHER_REF_ID = UUID("00000000-0000-7000-8000-000000000037")
_DIGEST = "sha256:" + "b" * 64


def _cluster_content() -> FailureClusterRevisionContent:
    resolution = _resolution(
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        evidence_integrity=EvidenceIntegrity.VERIFIED,
    )
    signal = failure_signal_for(resolution, None)
    return FailureClusterRevisionContent(
        id=_CLUSTER_REVISION_ID,
        failure_cluster_id=_CLUSTER_ID,
        tenant_id=resolution.tenant_id,
        project_id=resolution.project_id,
        task_run_id=resolution.task_run_id,
        result_snapshot_id=_SNAPSHOT_ID,
        revision=1,
        fingerprint_policy_digest=FAILURE_CLUSTER_POLICY_DIGEST,
        fingerprint=failure_cluster_fingerprint(signal),
        signal=signal,
        affected_unit_resolution_revision_ids=(resolution.id,),
        affected_count=1,
        representative_unit_resolution_revision_id=resolution.id,
        projection_watermark=NOW,
        created_at=NOW,
    )


def _classification_content() -> FailureClassificationRevisionContent:
    cluster = _cluster_content()
    code, hypothesis, confidence, gaps = rule_classification_for_signal(cluster.signal)
    return FailureClassificationRevisionContent(
        id=_CLASSIFICATION_REVISION_ID,
        failure_classification_id=_CLASSIFICATION_ID,
        tenant_id=cluster.tenant_id,
        project_id=cluster.project_id,
        task_run_id=cluster.task_run_id,
        result_snapshot_id=cluster.result_snapshot_id,
        failure_cluster_revision_id=cluster.id,
        revision=1,
        failure_domain=cluster.signal.failure_domain,
        hypothesis_code=code,
        hypothesis=hypothesis,
        confidence=confidence,
        supporting_evidence_refs=(
            FailureEvidenceRef(
                kind=FailureEvidenceKind.UNIT_RESOLUTION,
                ref_id=cluster.representative_unit_resolution_revision_id,
                content_digest=_resolution().input_set_hash,
            ),
        ),
        evidence_gap_codes=gaps,
        judgment_state=ClassificationJudgmentState.RULE_PROPOSED,
        author_kind=ClassificationAuthorKind.SYSTEM_RULE,
        classification_policy_digest=FAILURE_CLASSIFICATION_POLICY_DIGEST,
        client_mutation_id=f"rule:{cluster.id}",
        created_at=NOW,
    )


def test_failure_signal_policy_is_conservative_and_excludes_clean_passes() -> None:
    clean_pass = _resolution(
        effective_verdict=Verdict.PASSED,
        closure_reason="ORACLE_PASSED",
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        evidence_integrity=EvidenceIntegrity.VERIFIED,
        stability=Stability.STABLE,
    )
    assert is_diagnostic_failure(clean_pass, None) is False

    infrastructure = _resolution(
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        evidence_integrity=EvidenceIntegrity.VERIFIED,
    )
    infrastructure_signal = failure_signal_for(infrastructure, None)
    assert infrastructure_signal.failure_domain is FailureDomain.INFRASTRUCTURE
    assert infrastructure_signal.signal_code == "INFRASTRUCTURE_FAILURE"

    cleanup = _resolution(
        data_hygiene=DataHygiene.LEAKED,
        evidence_integrity=EvidenceIntegrity.INVALID,
        outcome_class=OutcomeClass.POLICY,
    )
    cleanup_signal = failure_signal_for(cleanup, None)
    assert cleanup_signal.failure_domain is FailureDomain.CLEANUP
    assert cleanup_signal.signal_code == "CLEANUP_LEAKED"

    partial = _resolution(
        outcome_class=OutcomeClass.DEPENDENCY,
        evidence_completeness=EvidenceCompleteness.PARTIAL,
        evidence_integrity=EvidenceIntegrity.VERIFIED,
    )
    partial_signal = failure_signal_for(partial, None)
    assert partial_signal.failure_domain is FailureDomain.EVIDENCE
    assert partial_signal.signal_code == "EVIDENCE_REQUIRED_PARTIAL"

    unverified = _resolution(
        outcome_class=OutcomeClass.DEPENDENCY,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        evidence_integrity=EvidenceIntegrity.UNVERIFIED,
    )
    unverified_signal = failure_signal_for(unverified, None)
    assert unverified_signal.failure_domain is FailureDomain.EVIDENCE
    assert unverified_signal.signal_code == "EVIDENCE_INTEGRITY_UNVERIFIED"

    unresolved = _resolution(
        outcome_class=OutcomeClass.BUSINESS,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        evidence_integrity=EvidenceIntegrity.VERIFIED,
    )
    unresolved_signal = failure_signal_for(unresolved, None)
    _, _, confidence, gaps = rule_classification_for_signal(unresolved_signal)
    assert unresolved_signal.failure_domain is FailureDomain.UNKNOWN
    assert confidence.numerator == 2_500
    assert gaps == ("PRODUCT_VS_TEST_SPEC_UNRESOLVED",)


def test_cluster_and_classification_hashes_cover_semantic_content() -> None:
    cluster_content = _cluster_content()
    cluster = FailureClusterRevision(
        **cluster_content.model_dump(mode="python"),
        cluster_hash=failure_cluster_revision_hash(cluster_content),
    )
    classification_content = _classification_content()
    classification = FailureClassificationRevision(
        **classification_content.model_dump(mode="python"),
        classification_hash=failure_classification_revision_hash(classification_content),
    )

    assert cluster.cluster_hash == failure_cluster_revision_hash(cluster)
    assert classification.classification_hash == failure_classification_revision_hash(
        classification
    )
    with pytest.raises(ValidationError, match="classificationHash"):
        FailureClassificationRevision.model_validate(
            {
                **classification.model_dump(mode="python", by_alias=False),
                "hypothesis": "Altered persisted judgment.",
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        cast(Any, cluster).affected_count = 2


def test_review_request_rejects_noncanonical_or_unsafe_human_judgments() -> None:
    supporting = FailureEvidenceRef(
        kind=FailureEvidenceKind.UNIT_RESOLUTION,
        ref_id=_resolution().id,
        content_digest=_resolution().input_set_hash,
    )
    contradiction = FailureEvidenceRef(
        kind=FailureEvidenceKind.ATTEMPT_CLOSURE_NOTICE,
        ref_id=_OTHER_REF_ID,
        content_digest=_DIGEST,
    )
    accepted = RequestFailureClassificationRevision(
        expected_revision=1,
        failure_domain=FailureDomain.UNKNOWN,
        hypothesis_code="RULE_REJECTED",
        hypothesis="The proposed rule attribution is not supported.",
        confidence=ClassificationConfidence(numerator=0),
        supporting_evidence_refs=(supporting,),
        contradicting_evidence_refs=(contradiction,),
        evidence_gap_codes=("ROOT_CAUSE_EVIDENCE_MISSING",),
        judgment_state=ClassificationJudgmentState.HUMAN_REJECTED,
        client_mutation_id="review:classification:1",
    )
    assert accepted.judgment_state is ClassificationJudgmentState.HUMAN_REJECTED

    with pytest.raises(ValidationError, match="requires UNKNOWN"):
        RequestFailureClassificationRevision.model_validate(
            {
                **accepted.model_dump(mode="python", by_alias=False),
                "failure_domain": FailureDomain.PRODUCT,
            }
        )
    with pytest.raises(ValidationError, match="unique and sorted"):
        RequestFailureClassificationRevision.model_validate(
            {
                **accepted.model_dump(mode="python", by_alias=False),
                "evidence_gap_codes": (
                    "ROOT_CAUSE_EVIDENCE_MISSING",
                    "AUTOMATION_DETAIL_MISSING",
                ),
            }
        )
