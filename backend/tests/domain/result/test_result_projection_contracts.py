"""ClosureNotice and UnitResolutionRevision contract tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.infrastructure.test_task_run_repository import NOW

from atlas_testops.domain.result import (
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST,
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION,
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
    TASK_RESULT_SNAPSHOT_POLICY_DIGEST,
    TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
    TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION,
    TASK_RESULT_SNAPSHOT_REEVALUATED_SCHEMA_VERSION,
    UNIT_RESOLUTION_POLICY_DIGEST,
    AttemptClosureNotice,
    AttemptClosureNoticeContent,
    AttemptClosureSourceStatus,
    DataHygiene,
    EvidenceCompleteness,
    EvidenceIntegrity,
    ExecutionInfluence,
    OutcomeClass,
    ResultPassRate,
    Stability,
    TaskDataHygieneCounts,
    TaskEvidenceCompletenessCounts,
    TaskEvidenceIntegrityCounts,
    TaskExecutionInfluenceCounts,
    TaskOutcomeClassCounts,
    TaskResultAxisDistributions,
    TaskResultReevaluationCommand,
    TaskResultReevaluationCommandContent,
    TaskResultSnapshot,
    TaskResultSnapshotContent,
    TaskResultSnapshotFinality,
    TaskStabilityCounts,
    TaskVerdictCounts,
    UnitResolutionRevision,
    Verdict,
    attempt_closure_notice_hash,
    task_result_reevaluation_command_hash,
    task_result_snapshot_document,
    task_result_snapshot_hash,
)

_DIGEST = "sha256:" + "a" * 64
_TENANT_ID = UUID("00000000-0000-7000-8000-000000000001")
_PROJECT_ID = UUID("00000000-0000-7000-8000-000000000002")
_TASK_RUN_ID = UUID("00000000-0000-7000-8000-000000000003")
_UNIT_ID = UUID("00000000-0000-7000-8000-000000000004")
_ATTEMPT_ID = UUID("00000000-0000-7000-8000-000000000005")
_CLOSURE_ID = UUID("00000000-0000-7000-8000-000000000006")
_RESOLUTION_ID = UUID("00000000-0000-7000-8000-000000000007")
_REVISION_ID = UUID("00000000-0000-7000-8000-000000000008")
_SNAPSHOT_ID = UUID("00000000-0000-7000-8000-000000000009")
_HYGIENE_REVISION_ID = UUID("00000000-0000-7000-8000-000000000010")
_COMMAND_ID = UUID("00000000-0000-7000-8000-000000000011")


def _notice_content(**overrides: object) -> AttemptClosureNoticeContent:
    values: dict[str, object] = {
        "id": _CLOSURE_ID,
        "tenant_id": _TENANT_ID,
        "project_id": _PROJECT_ID,
        "task_run_id": _TASK_RUN_ID,
        "execution_unit_id": _UNIT_ID,
        "unit_attempt_id": _ATTEMPT_ID,
        "manifest_hash": _DIGEST,
        "unit_key": _DIGEST,
        "attempt_number": 1,
        "source_status": AttemptClosureSourceStatus.INFRA_ERROR,
        "verdict": Verdict.INCONCLUSIVE,
        "outcome_class": OutcomeClass.PLATFORM,
        "closure_reason": "TASK_BROWSER_HOST_UNAVAILABLE",
        "data_hygiene": DataHygiene.NOT_APPLICABLE,
        "evidence_completeness": EvidenceCompleteness.MISSING,
        "evidence_integrity": EvidenceIntegrity.UNVERIFIED,
        "execution_influence": ExecutionInfluence.AUTONOMOUS,
        "closed_at": NOW,
        "created_at": NOW,
    }
    values.update(overrides)
    return AttemptClosureNoticeContent.model_validate(values)


def _notice(**overrides: object) -> AttemptClosureNotice:
    content = _notice_content(**overrides)
    return AttemptClosureNotice(
        **content.model_dump(mode="python"),
        notice_hash=attempt_closure_notice_hash(content),
    )


def _resolution(**overrides: object) -> UnitResolutionRevision:
    values: dict[str, object] = {
        "id": _REVISION_ID,
        "unit_resolution_id": _RESOLUTION_ID,
        "tenant_id": _TENANT_ID,
        "project_id": _PROJECT_ID,
        "task_run_id": _TASK_RUN_ID,
        "execution_unit_id": _UNIT_ID,
        "manifest_hash": _DIGEST,
        "unit_key": _DIGEST,
        "revision": 1,
        "input_seal_ids": (),
        "input_closure_notice_ids": (_CLOSURE_ID,),
        "input_set_hash": _DIGEST,
        "effective_verdict": Verdict.INCONCLUSIVE,
        "outcome_class": OutcomeClass.PLATFORM,
        "closure_reason": "TASK_BROWSER_HOST_UNAVAILABLE",
        "data_hygiene": DataHygiene.NOT_APPLICABLE,
        "evidence_completeness": EvidenceCompleteness.MISSING,
        "evidence_integrity": EvidenceIntegrity.UNVERIFIED,
        "execution_influence": ExecutionInfluence.AUTONOMOUS,
        "stability": Stability.UNKNOWN,
        "decisive_unit_attempt_id": _ATTEMPT_ID,
        "decisive_attempt_number": 1,
        "resolution_policy_digest": UNIT_RESOLUTION_POLICY_DIGEST,
        "created_at": NOW,
    }
    values.update(overrides)
    return UnitResolutionRevision.model_validate(values)


def _snapshot_content(**overrides: object) -> TaskResultSnapshotContent:
    values: dict[str, object] = {
        "id": _SNAPSHOT_ID,
        "tenant_id": _TENANT_ID,
        "project_id": _PROJECT_ID,
        "task_run_id": _TASK_RUN_ID,
        "manifest_hash": _DIGEST,
        "revision": 1,
        "finality": TaskResultSnapshotFinality.QUALITY_FINAL,
        "unit_resolution_revision_ids": (_REVISION_ID,),
        "input_resolution_set_hash": _DIGEST,
        "aggregation_policy_digest": TASK_RESULT_SNAPSHOT_POLICY_DIGEST,
        "projection_watermark": NOW,
        "manifest_count": 1,
        "verdict_counts": TaskVerdictCounts(
            passed=0,
            failed=0,
            inconclusive=1,
            not_evaluated=0,
        ),
        "axis_distributions": TaskResultAxisDistributions(
            data_hygiene=TaskDataHygieneCounts(
                pending=0,
                cleaned=0,
                cleanup_failed=0,
                leaked=0,
                not_applicable=1,
            ),
            evidence_completeness=TaskEvidenceCompletenessCounts(
                pending=0,
                complete=0,
                partial=0,
                missing=1,
                not_applicable=0,
            ),
            evidence_integrity=TaskEvidenceIntegrityCounts(
                unverified=1,
                verified=0,
                invalid=0,
            ),
            execution_influence=TaskExecutionInfluenceCounts(
                autonomous=1,
                manual_assisted=0,
                manual_only=0,
            ),
            stability=TaskStabilityCounts(
                unknown=1,
                stable=0,
                infra_recovered=0,
                flaky_suspect=0,
                flaky_confirmed=0,
            ),
            outcome_class=TaskOutcomeClassCounts(
                business=0,
                dependency=0,
                platform=1,
                user=0,
                automation=0,
                policy=0,
                unknown=0,
            ),
        ),
        "raw_pass_rate": ResultPassRate(numerator=0, denominator=1),
        "trusted_pass_rate": ResultPassRate(numerator=0, denominator=1),
        "autonomous_pass_rate": ResultPassRate(numerator=0, denominator=1),
        "decisive_pass_rate": ResultPassRate(numerator=0, denominator=0),
        "created_at": NOW,
    }
    values.update(overrides)
    return TaskResultSnapshotContent.model_validate(values)


def _snapshot(**overrides: object) -> TaskResultSnapshot:
    content = _snapshot_content(**overrides)
    return TaskResultSnapshot(
        **content.model_dump(mode="python"),
        snapshot_hash=task_result_snapshot_hash(content),
    )


def test_closure_notice_has_stable_hash_and_is_frozen() -> None:
    notice = _notice()

    assert notice.notice_hash == attempt_closure_notice_hash(notice)
    with pytest.raises(ValidationError, match="frozen"):
        cast(Any, notice).verdict = Verdict.FAILED


@pytest.mark.parametrize(
    ("overrides", "error"),
    (
        ({"verdict": Verdict.PASSED}, "INCONCLUSIVE or NOT_EVALUATED"),
        (
            {"evidence_integrity": EvidenceIntegrity.VERIFIED},
            "evidence must remain UNVERIFIED",
        ),
        (
            {"evidence_completeness": EvidenceCompleteness.COMPLETE},
            "completeness must match",
        ),
        (
            {"execution_influence": ExecutionInfluence.MANUAL_ONLY},
            "cannot infer manual",
        ),
        (
            {
                "source_status": AttemptClosureSourceStatus.CANCELED,
                "outcome_class": OutcomeClass.PLATFORM,
            },
            "OutcomeClass must match sourceStatus",
        ),
        (
            {
                "source_status": AttemptClosureSourceStatus.FAILED,
                "outcome_class": OutcomeClass.UNKNOWN,
            },
            "OutcomeClass must match sourceStatus",
        ),
        (
            {
                "source_status": AttemptClosureSourceStatus.FINISHED_UNSEALED,
                "verdict": Verdict.NOT_EVALUATED,
                "outcome_class": OutcomeClass.AUTOMATION,
                "evidence_completeness": EvidenceCompleteness.NOT_APPLICABLE,
            },
            "only CANCELED",
        ),
        ({"created_at": NOW - timedelta(seconds=1)}, "cannot predate"),
    ),
)
def test_closure_notice_rejects_manufactured_or_incoherent_truth(
    overrides: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        _notice_content(**overrides)


def test_closure_notice_rejects_tampered_hash() -> None:
    content = _notice_content()

    with pytest.raises(ValidationError, match="noticeHash must match"):
        AttemptClosureNotice(
            **content.model_dump(mode="python"),
            notice_hash="sha256:" + "f" * 64,
        )


@pytest.mark.parametrize(
    ("overrides", "error"),
    (
        ({"effective_verdict": Verdict.PENDING}, "cannot contain PENDING"),
        (
            {"input_closure_notice_ids": ()},
            "requires at least one input fact",
        ),
        (
            {"input_closure_notice_ids": (_CLOSURE_ID, _CLOSURE_ID)},
            "inputClosureNoticeIds must be unique",
        ),
        (
            {"input_seal_ids": (_CLOSURE_ID, _CLOSURE_ID)},
            "inputSealIds must be unique",
        ),
        (
            {"supersedes_revision_id": _REVISION_ID},
            "first UnitResolutionRevision cannot supersede",
        ),
        (
            {"revision": 2},
            "later UnitResolutionRevision requires its predecessor",
        ),
        (
            {
                "effective_verdict": Verdict.PASSED,
                "evidence_completeness": EvidenceCompleteness.MISSING,
                "evidence_integrity": EvidenceIntegrity.UNVERIFIED,
            },
            "PASSED UnitResolutionRevision requires complete verified evidence",
        ),
        (
            {"resolution_policy_digest": "sha256:" + "f" * 64},
            "must match the frozen Resolution Policy",
        ),
    ),
)
def test_resolution_revision_rejects_invalid_terminal_projection(
    overrides: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        _resolution(**overrides)


def test_later_resolution_requires_and_accepts_exact_predecessor_shape() -> None:
    predecessor = _resolution()
    later = _resolution(
        id=UUID("00000000-0000-7000-8000-000000000009"),
        revision=2,
        supersedes_revision_id=predecessor.id,
    )

    assert later.revision == 2
    assert later.supersedes_revision_id == predecessor.id


def test_task_snapshot_conserves_manifest_and_has_stable_semantic_hash() -> None:
    snapshot = _snapshot()

    assert snapshot.snapshot_hash == task_result_snapshot_hash(snapshot)
    with pytest.raises(ValidationError, match="frozen"):
        cast(Any, snapshot).manifest_count = 2


def test_fully_resolved_snapshot_binds_terminal_hygiene_without_rewriting_v1() -> None:
    quality = _snapshot()
    content = _snapshot_content(
        schema_version=TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
        revision=2,
        finality=TaskResultSnapshotFinality.FULLY_RESOLVED,
        unit_hygiene_resolution_revision_ids=(_HYGIENE_REVISION_ID,),
        input_hygiene_resolution_set_hash=_DIGEST,
        aggregation_policy_version=(TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION),
        aggregation_policy_digest=(TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST),
        supersedes_snapshot_id=quality.id,
    )
    snapshot = TaskResultSnapshot(
        **content.model_dump(mode="python"),
        snapshot_hash=task_result_snapshot_hash(content),
    )

    assert len(task_result_snapshot_document(quality)) == 23
    assert "unitHygieneResolutionRevisionIds" not in (task_result_snapshot_document(quality))
    assert len(task_result_snapshot_document(snapshot)) == 25
    assert snapshot.finality is TaskResultSnapshotFinality.FULLY_RESOLVED
    assert snapshot.snapshot_hash == task_result_snapshot_hash(snapshot)


def test_reevaluated_snapshot_binds_explicit_command_and_preserves_source_truth() -> None:
    full = _snapshot(
        schema_version=TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
        id=UUID("00000000-0000-7000-8000-000000000014"),
        revision=2,
        finality=TaskResultSnapshotFinality.FULLY_RESOLVED,
        unit_hygiene_resolution_revision_ids=(_HYGIENE_REVISION_ID,),
        input_hygiene_resolution_set_hash=_DIGEST,
        aggregation_policy_version=TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION,
        aggregation_policy_digest=TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST,
        supersedes_snapshot_id=_SNAPSHOT_ID,
    )
    content = _snapshot_content(
        schema_version=TASK_RESULT_SNAPSHOT_REEVALUATED_SCHEMA_VERSION,
        id=UUID("00000000-0000-7000-8000-000000000012"),
        revision=3,
        finality=TaskResultSnapshotFinality.REEVALUATED,
        unit_hygiene_resolution_revision_ids=full.unit_hygiene_resolution_revision_ids,
        input_hygiene_resolution_set_hash=full.input_hygiene_resolution_set_hash,
        reevaluation_source_snapshot_id=full.id,
        reevaluation_command_id=_COMMAND_ID,
        aggregation_policy_version=TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION,
        aggregation_policy_digest=TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
        supersedes_snapshot_id=full.id,
    )
    snapshot = TaskResultSnapshot(
        **content.model_dump(mode="python"),
        snapshot_hash=task_result_snapshot_hash(content),
    )
    equivalent_command = content.model_copy(
        update={"reevaluation_command_id": UUID("00000000-0000-7000-8000-000000000013")}
    )

    assert len(task_result_snapshot_document(snapshot)) == 27
    assert snapshot.reevaluation_source_snapshot_id == full.id
    assert task_result_snapshot_hash(equivalent_command) == snapshot.snapshot_hash


def test_reevaluation_command_hash_covers_explicit_source_policy_and_actor() -> None:
    content = TaskResultReevaluationCommandContent(
        id=_COMMAND_ID,
        tenant_id=_TENANT_ID,
        project_id=_PROJECT_ID,
        task_run_id=_TASK_RUN_ID,
        source_snapshot_id=_SNAPSHOT_ID,
        target_policy_digest=TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
        client_mutation_id="reevaluate-result-001",
        requested_by=_ATTEMPT_ID,
        requested_at=NOW,
    )
    command = TaskResultReevaluationCommand(
        **content.model_dump(mode="python"),
        command_hash=task_result_reevaluation_command_hash(content),
    )

    assert command.command_hash == task_result_reevaluation_command_hash(command)
    with pytest.raises(ValidationError, match="commandHash must match"):
        TaskResultReevaluationCommand(
            **content.model_dump(mode="python"),
            command_hash="sha256:" + "f" * 64,
        )


@pytest.mark.parametrize(
    ("overrides", "error"),
    (
        (
            {
                "schema_version": TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
            },
            "QUALITY_FINAL must preserve",
        ),
        (
            {
                "schema_version": (TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION),
                "finality": TaskResultSnapshotFinality.FULLY_RESOLVED,
                "aggregation_policy_version": (TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION),
                "aggregation_policy_digest": (TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST),
            },
            "complete 0.2 Hygiene input shape",
        ),
        (
            {
                "schema_version": TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
                "revision": 2,
                "finality": TaskResultSnapshotFinality.FULLY_RESOLVED,
                "unit_hygiene_resolution_revision_ids": (_HYGIENE_REVISION_ID,),
                "input_hygiene_resolution_set_hash": _DIGEST,
                "aggregation_policy_version": (TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION),
                "aggregation_policy_digest": (TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST),
                "supersedes_snapshot_id": _SNAPSHOT_ID,
                "axis_distributions": TaskResultAxisDistributions(
                    data_hygiene=TaskDataHygieneCounts(
                        pending=1,
                        cleaned=0,
                        cleanup_failed=0,
                        leaked=0,
                        not_applicable=0,
                    ),
                    evidence_completeness=TaskEvidenceCompletenessCounts(
                        pending=0,
                        complete=0,
                        partial=0,
                        missing=1,
                        not_applicable=0,
                    ),
                    evidence_integrity=TaskEvidenceIntegrityCounts(
                        unverified=1,
                        verified=0,
                        invalid=0,
                    ),
                    execution_influence=TaskExecutionInfluenceCounts(
                        autonomous=1,
                        manual_assisted=0,
                        manual_only=0,
                    ),
                    stability=TaskStabilityCounts(
                        unknown=1,
                        stable=0,
                        infra_recovered=0,
                        flaky_suspect=0,
                        flaky_confirmed=0,
                    ),
                    outcome_class=TaskOutcomeClassCounts(
                        business=0,
                        dependency=0,
                        platform=1,
                        user=0,
                        automation=0,
                        policy=0,
                        unknown=0,
                    ),
                ),
            },
            "cannot contain unresolved Hygiene",
        ),
    ),
)
def test_task_snapshot_rejects_cross_version_or_unresolved_hygiene(
    overrides: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        _snapshot_content(**overrides)


@pytest.mark.parametrize(
    ("overrides", "error"),
    (
        (
            {
                "unit_resolution_revision_ids": (),
            },
            "at least 1",
        ),
        (
            {
                "verdict_counts": TaskVerdictCounts(
                    passed=0,
                    failed=0,
                    inconclusive=0,
                    not_evaluated=0,
                )
            },
            "conserve manifestCount",
        ),
        (
            {
                "raw_pass_rate": ResultPassRate(
                    numerator=0,
                    denominator=0,
                )
            },
            "frozen Snapshot Policy",
        ),
        (
            {
                "revision": 2,
            },
            "requires its predecessor",
        ),
        (
            {
                "aggregation_policy_digest": "sha256:" + "f" * 64,
            },
            "frozen Snapshot Policy",
        ),
        (
            {
                "created_at": NOW - timedelta(seconds=1),
            },
            "cannot predate projectionWatermark",
        ),
    ),
)
def test_task_snapshot_rejects_incomplete_or_incoherent_content(
    overrides: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        _snapshot_content(**overrides)


def test_task_snapshot_rejects_tampered_semantic_hash() -> None:
    content = _snapshot_content()

    with pytest.raises(ValidationError, match="snapshotHash must match"):
        TaskResultSnapshot(
            **content.model_dump(mode="python"),
            snapshot_hash="sha256:" + "f" * 64,
        )
