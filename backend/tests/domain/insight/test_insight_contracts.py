"""Comparable Insight metric and immutable DatasetCut contract tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from pydantic import ValidationError
from tests.infrastructure.test_task_run_repository import NOW

from atlas_testops.application.access import ActorContext
from atlas_testops.application.insights import _compile_brief, _pin_brief
from atlas_testops.domain.insight import (
    InsightDatasetCut,
    InsightMetricKey,
    InsightSampleStatus,
    insight_snapshot_hash,
    insight_source_set_digest,
    metric_point,
)
from atlas_testops.domain.result import (
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST,
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION,
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
    ResultPassRate,
    TaskDataHygieneCounts,
    TaskEvidenceCompletenessCounts,
    TaskEvidenceIntegrityCounts,
    TaskExecutionInfluenceCounts,
    TaskOutcomeClassCounts,
    TaskResultAxisDistributions,
    TaskResultSnapshot,
    TaskResultSnapshotContent,
    TaskResultSnapshotFinality,
    TaskStabilityCounts,
    TaskVerdictCounts,
    task_result_snapshot_hash,
)
from atlas_testops.infrastructure.repositories.insights import InsightSourceRecord

TENANT_ID = UUID("00000000-0000-7000-8000-000000000101")
PROJECT_ID = UUID("00000000-0000-7000-8000-000000000102")
ACTOR_ID = UUID("00000000-0000-7000-8000-000000000103")
PLAN_A = UUID("00000000-0000-7000-8000-000000000104")
PLAN_B = UUID("00000000-0000-7000-8000-000000000105")


def _id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"atlas-insight-test:{name}")


def _snapshot(
    name: str,
    *,
    event_time: datetime,
    manifest_count: int,
    trusted_passed: int,
    stable: int,
) -> TaskResultSnapshot:
    unit_resolution_ids = tuple(
        _id(f"{name}:resolution:{index}") for index in range(manifest_count)
    )
    hygiene_ids = tuple(
        _id(f"{name}:hygiene:{index}") for index in range(manifest_count)
    )
    inconclusive = manifest_count - trusted_passed
    content = TaskResultSnapshotContent(
        schema_version=TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
        id=_id(f"{name}:snapshot"),
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        task_run_id=_id(f"{name}:run"),
        manifest_hash="sha256:" + "1" * 64,
        revision=1,
        finality=TaskResultSnapshotFinality.FULLY_RESOLVED,
        unit_resolution_revision_ids=unit_resolution_ids,
        input_resolution_set_hash="sha256:" + "2" * 64,
        unit_hygiene_resolution_revision_ids=hygiene_ids,
        input_hygiene_resolution_set_hash="sha256:" + "3" * 64,
        aggregation_policy_version=(
            TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION
        ),
        aggregation_policy_digest=(
            TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST
        ),
        projection_watermark=event_time,
        manifest_count=manifest_count,
        verdict_counts=TaskVerdictCounts(
            passed=trusted_passed,
            failed=0,
            inconclusive=inconclusive,
            not_evaluated=0,
        ),
        axis_distributions=TaskResultAxisDistributions(
            data_hygiene=TaskDataHygieneCounts(
                pending=0,
                cleaned=manifest_count,
                cleanup_failed=0,
                leaked=0,
                not_applicable=0,
            ),
            evidence_completeness=TaskEvidenceCompletenessCounts(
                pending=0,
                complete=trusted_passed,
                partial=0,
                missing=inconclusive,
                not_applicable=0,
            ),
            evidence_integrity=TaskEvidenceIntegrityCounts(
                unverified=inconclusive,
                verified=trusted_passed,
                invalid=0,
            ),
            execution_influence=TaskExecutionInfluenceCounts(
                autonomous=manifest_count,
                manual_assisted=0,
                manual_only=0,
            ),
            stability=TaskStabilityCounts(
                unknown=manifest_count - stable,
                stable=stable,
                infra_recovered=0,
                flaky_suspect=0,
                flaky_confirmed=0,
            ),
            outcome_class=TaskOutcomeClassCounts(
                business=trusted_passed,
                dependency=0,
                platform=inconclusive,
                user=0,
                automation=0,
                policy=0,
                unknown=0,
            ),
        ),
        raw_pass_rate=ResultPassRate(
            numerator=trusted_passed,
            denominator=manifest_count,
        ),
        trusted_pass_rate=ResultPassRate(
            numerator=trusted_passed,
            denominator=manifest_count,
        ),
        autonomous_pass_rate=ResultPassRate(
            numerator=trusted_passed,
            denominator=manifest_count,
        ),
        decisive_pass_rate=ResultPassRate(
            numerator=trusted_passed,
            denominator=trusted_passed,
        ),
        created_at=event_time,
    )
    return TaskResultSnapshot(
        **content.model_dump(mode="python"),
        snapshot_hash=task_result_snapshot_hash(content),
    )


def _source(
    name: str,
    *,
    days_ago: int,
    manifest_count: int,
    trusted_passed: int,
    stable: int,
    plan_id: UUID,
) -> InsightSourceRecord:
    quality_finalized_at = NOW - timedelta(days=days_ago)
    return InsightSourceRecord(
        snapshot=_snapshot(
            name,
            event_time=quality_finalized_at,
            manifest_count=manifest_count,
            trusted_passed=trusted_passed,
            stable=stable,
        ),
        quality_finalized_at=quality_finalized_at,
        task_plan_id=plan_id,
        task_plan_name="客户权限" if plan_id == PLAN_A else "来访关系",
        gate_decision=None,
    )


def _actor() -> ActorContext:
    return ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        request_id="insight-contract",
        development_override=True,
    )


def test_metric_point_preserves_no_data_and_sample_sufficiency() -> None:
    no_data = metric_point(
        InsightMetricKey.TRUSTED_PASS_RATE,
        numerator=0,
        denominator=0,
    )
    low = metric_point(
        InsightMetricKey.TRUSTED_PASS_RATE,
        numerator=1,
        denominator=3,
    )
    enough = metric_point(
        InsightMetricKey.TRUSTED_PASS_RATE,
        numerator=29,
        denominator=30,
    )

    assert no_data.basis_points is None
    assert no_data.sample_status is InsightSampleStatus.NO_DATA
    assert low.basis_points == 3_333
    assert low.sample_status is InsightSampleStatus.LOW_SAMPLE
    assert enough.sample_status is InsightSampleStatus.ENOUGH


def test_brief_uses_ratio_of_sums_and_is_permutation_invariant() -> None:
    sources = (
        _source(
            "large-failure",
            days_ago=2,
            manifest_count=9,
            trusted_passed=0,
            stable=3,
            plan_id=PLAN_A,
        ),
        _source(
            "small-pass",
            days_ago=1,
            manifest_count=1,
            trusted_passed=1,
            stable=1,
            plan_id=PLAN_B,
        ),
        _source(
            "baseline",
            days_ago=35,
            manifest_count=5,
            trusted_passed=4,
            stable=5,
            plan_id=PLAN_A,
        ),
    )

    first = _compile_brief(
        actor=_actor(),
        project_id=PROJECT_ID,
        window_days=30,
        as_of=NOW,
        sources=sources,
    )
    reversed_brief = _compile_brief(
        actor=_actor(),
        project_id=PROJECT_ID,
        window_days=30,
        as_of=NOW,
        sources=tuple(reversed(sources)),
    )

    assert first == reversed_brief
    assert first.current.execution_unit_count == 10
    assert first.current.trusted_pass_rate.numerator == 1
    assert first.current.trusted_pass_rate.basis_points == 1_000
    assert first.baseline.trusted_pass_rate.basis_points == 8_000
    assert first.deltas.trusted_pass_rate == -7_000
    assert [item.label for item in first.terrain] == ["客户权限", "来访关系"]


def test_dataset_cut_and_pinned_snapshot_hash_cover_exact_sources() -> None:
    source = _source(
        "pin",
        days_ago=1,
        manifest_count=2,
        trusted_passed=1,
        stable=1,
        plan_id=PLAN_A,
    )
    brief = _compile_brief(
        actor=_actor(),
        project_id=PROJECT_ID,
        window_days=30,
        as_of=NOW,
        sources=(source,),
    )
    snapshot = _pin_brief(
        brief,
        request_hash="sha256:" + "a" * 64,
        client_mutation_id="insight:pin:contract:1",
        created_by=ACTOR_ID,
        created_at=NOW,
    )

    assert snapshot.snapshot_hash == insight_snapshot_hash(snapshot)
    assert snapshot.dataset_cut.source_set_digest == insight_source_set_digest(
        (source.snapshot.id,),
        (source.snapshot.snapshot_hash,),
    )
    with pytest.raises(ValidationError, match="sourceSetDigest"):
        InsightDatasetCut.model_validate(
            {
                **snapshot.dataset_cut.model_dump(
                    mode="python",
                    by_alias=False,
                ),
                "source_snapshot_hashes": ("sha256:" + "f" * 64,),
            }
        )
    with pytest.raises(ValidationError, match="snapshotHash"):
        type(snapshot).model_validate(
            {
                **snapshot.model_dump(mode="python", by_alias=False),
                "snapshot_hash": "sha256:" + "f" * 64,
            }
        )
    with pytest.raises(ValidationError, match="frozen"):
        cast(Any, snapshot).window_days = 7
