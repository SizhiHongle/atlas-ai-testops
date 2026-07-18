"""TaskGateDecision contract and frozen policy tests."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.domain.result.test_failure_classification_contracts import (
    _classification_content,
)
from tests.domain.result.test_result_projection_contracts import _snapshot_content
from tests.infrastructure.test_task_run_repository import NOW

from atlas_testops.domain.result import (
    TASK_GATE_POLICY_DIGEST,
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST,
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION,
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
    ClassificationAuthorKind,
    ClassificationConfidence,
    ClassificationJudgmentState,
    FailureClassificationRevision,
    FailureClassificationRevisionContent,
    FailureDomain,
    ResultPassRate,
    TaskDataHygieneCounts,
    TaskEvidenceCompletenessCounts,
    TaskEvidenceIntegrityCounts,
    TaskExecutionInfluenceCounts,
    TaskGateDecision,
    TaskGateDecisionContent,
    TaskGateReasonCode,
    TaskGateVerdict,
    TaskOutcomeClassCounts,
    TaskResultAxisDistributions,
    TaskResultSnapshot,
    TaskResultSnapshotFinality,
    TaskStabilityCounts,
    TaskVerdictCounts,
    evaluate_task_gate,
    failure_classification_revision_hash,
    task_gate_classification_is_ready,
    task_gate_decision_hash,
    task_result_snapshot_hash,
)

_GATE_DECISION_ID = UUID("00000000-0000-7000-8000-000000000041")
_TASK_GATE_ID = UUID("00000000-0000-7000-8000-000000000042")
_EVALUATOR_ID = UUID("00000000-0000-7000-8000-000000000043")
_HYGIENE_ID = UUID("00000000-0000-7000-8000-000000000044")


def _final_snapshot(*, failed: int = 0) -> TaskResultSnapshot:
    passed = 1 - failed
    content = _snapshot_content(
        schema_version=TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
        finality=TaskResultSnapshotFinality.FULLY_RESOLVED,
        unit_hygiene_resolution_revision_ids=(_HYGIENE_ID,),
        input_hygiene_resolution_set_hash="sha256:" + "c" * 64,
        aggregation_policy_version=TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION,
        aggregation_policy_digest=TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST,
        verdict_counts=TaskVerdictCounts(
            passed=passed,
            failed=failed,
            inconclusive=0,
            not_evaluated=0,
        ),
        axis_distributions=TaskResultAxisDistributions(
            data_hygiene=TaskDataHygieneCounts(
                pending=0,
                cleaned=1,
                cleanup_failed=0,
                leaked=0,
                not_applicable=0,
            ),
            evidence_completeness=TaskEvidenceCompletenessCounts(
                pending=0,
                complete=1,
                partial=0,
                missing=0,
                not_applicable=0,
            ),
            evidence_integrity=TaskEvidenceIntegrityCounts(
                unverified=0,
                verified=1,
                invalid=0,
            ),
            execution_influence=TaskExecutionInfluenceCounts(
                autonomous=1,
                manual_assisted=0,
                manual_only=0,
            ),
            stability=TaskStabilityCounts(
                unknown=0,
                stable=1,
                infra_recovered=0,
                flaky_suspect=0,
                flaky_confirmed=0,
            ),
            outcome_class=TaskOutcomeClassCounts(
                business=1,
                dependency=0,
                platform=0,
                user=0,
                automation=0,
                policy=0,
                unknown=0,
            ),
        ),
        raw_pass_rate=ResultPassRate(numerator=passed, denominator=1),
        trusted_pass_rate=ResultPassRate(numerator=passed, denominator=1),
        autonomous_pass_rate=ResultPassRate(numerator=passed, denominator=1),
        decisive_pass_rate=ResultPassRate(numerator=passed, denominator=1),
    )
    return TaskResultSnapshot(
        **content.model_dump(mode="python"),
        snapshot_hash=task_result_snapshot_hash(content),
    )


def _classification(
    snapshot: TaskResultSnapshot,
    *,
    human: bool,
    confidence: int,
    gaps: tuple[str, ...] = (),
) -> FailureClassificationRevision:
    baseline = _classification_content()
    content = FailureClassificationRevisionContent(
        **{
            **baseline.model_dump(mode="python", by_alias=False),
            "id": UUID("00000000-0000-7000-8000-000000000045"),
            "failure_classification_id": UUID(
                "00000000-0000-7000-8000-000000000046"
            ),
            "tenant_id": snapshot.tenant_id,
            "project_id": snapshot.project_id,
            "task_run_id": snapshot.task_run_id,
            "result_snapshot_id": snapshot.id,
            "revision": 2 if human else 1,
            "failure_domain": FailureDomain.PRODUCT if human else FailureDomain.UNKNOWN,
            "hypothesis_code": (
                "PRODUCT_DEFECT_CONFIRMED" if human else "PRODUCT_OR_SPEC_UNRESOLVED"
            ),
            "hypothesis": (
                "Reviewed evidence attributes the failure to product behavior."
                if human
                else "The business assertion failed without a resolved cause."
            ),
            "confidence": ClassificationConfidence(numerator=confidence),
            "evidence_gap_codes": gaps,
            "judgment_state": (
                ClassificationJudgmentState.HUMAN_REVISED
                if human
                else ClassificationJudgmentState.RULE_PROPOSED
            ),
            "author_kind": (
                ClassificationAuthorKind.HUMAN
                if human
                else ClassificationAuthorKind.SYSTEM_RULE
            ),
            "authored_by": _EVALUATOR_ID if human else None,
            "supersedes_revision_id": (
                UUID("00000000-0000-7000-8000-000000000047") if human else None
            ),
            "client_mutation_id": (
                "review:gate-ready:1" if human else "rule:gate-unready:1"
            ),
        }
    )
    return FailureClassificationRevision(
        **content.model_dump(mode="python"),
        classification_hash=failure_classification_revision_hash(content),
    )


def test_clean_fully_resolved_snapshot_is_accepted() -> None:
    verdict, reasons = evaluate_task_gate(_final_snapshot(), ())

    assert verdict is TaskGateVerdict.ACCEPTED
    assert reasons == ()


def test_quality_or_uncertain_inputs_fail_closed() -> None:
    snapshot_content = _snapshot_content()
    snapshot = TaskResultSnapshot(
        **snapshot_content.model_dump(mode="python"),
        snapshot_hash=task_result_snapshot_hash(snapshot_content),
    )

    verdict, reasons = evaluate_task_gate(snapshot, ())

    assert verdict is TaskGateVerdict.INCONCLUSIVE
    assert {reason.code for reason in reasons} == {
        TaskGateReasonCode.EVIDENCE_INCOMPLETE,
        TaskGateReasonCode.EVIDENCE_INVALID_OR_UNVERIFIED,
        TaskGateReasonCode.INCONCLUSIVE_UNITS,
        TaskGateReasonCode.SNAPSHOT_NOT_FULLY_RESOLVED,
        TaskGateReasonCode.UNSTABLE_EXECUTION,
    }


def test_low_evidence_classification_blocks_rejection_until_human_ready() -> None:
    snapshot = _final_snapshot(failed=1)
    low_evidence = _classification(
        snapshot,
        human=False,
        confidence=2_500,
        gaps=("PRODUCT_VS_TEST_SPEC_UNRESOLVED",),
    )
    reviewed = _classification(snapshot, human=True, confidence=8_500)

    blocked, blocked_reasons = evaluate_task_gate(snapshot, (low_evidence,))
    rejected, rejected_reasons = evaluate_task_gate(snapshot, (reviewed,))

    assert task_gate_classification_is_ready(low_evidence) is False
    assert task_gate_classification_is_ready(reviewed)
    assert blocked is TaskGateVerdict.INCONCLUSIVE
    assert TaskGateReasonCode.CLASSIFICATION_NOT_GATE_READY in {
        reason.code for reason in blocked_reasons
    }
    assert rejected is TaskGateVerdict.REJECTED
    assert len(rejected_reasons) == 1
    assert rejected_reasons[0].code is TaskGateReasonCode.FAILED_UNITS
    assert rejected_reasons[0].count == 1


def test_task_gate_decision_hash_covers_inputs_and_is_frozen() -> None:
    snapshot = _final_snapshot()
    content = TaskGateDecisionContent(
        id=_GATE_DECISION_ID,
        task_gate_id=_TASK_GATE_ID,
        tenant_id=snapshot.tenant_id,
        project_id=snapshot.project_id,
        task_run_id=snapshot.task_run_id,
        result_snapshot_id=snapshot.id,
        result_snapshot_hash=snapshot.snapshot_hash,
        revision=1,
        failure_classification_revision_ids=(),
        classification_set_hash="sha256:" + "d" * 64,
        gate_policy_digest=TASK_GATE_POLICY_DIGEST,
        decision=TaskGateVerdict.ACCEPTED,
        reasons=(),
        evaluated_by=_EVALUATOR_ID,
        client_mutation_id="gate:evaluate:clean:1",
        evaluated_at=NOW,
    )
    decision = TaskGateDecision(
        **content.model_dump(mode="python"),
        decision_hash=task_gate_decision_hash(content),
    )

    assert decision.decision_hash == task_gate_decision_hash(decision)
    with pytest.raises(ValidationError, match="decisionHash"):
        TaskGateDecision.model_validate(
            {
                **decision.model_dump(mode="python", by_alias=False),
                "client_mutation_id": "gate:evaluate:clean:2",
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        cast(Any, decision).decision = TaskGateVerdict.REJECTED
