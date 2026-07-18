"""Versioned contracts for comparable, snapshot-bound quality insights."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import DIGEST_PATTERN
from atlas_testops.domain.result import TaskGateVerdict

INSIGHT_BRIEF_SCHEMA_VERSION: Literal["atlas.insight-brief/0.1"] = (
    "atlas.insight-brief/0.1"
)
INSIGHT_DATASET_CUT_SCHEMA_VERSION: Literal["atlas.insight-dataset-cut/0.1"] = (
    "atlas.insight-dataset-cut/0.1"
)
INSIGHT_SNAPSHOT_SCHEMA_VERSION: Literal["atlas.insight-snapshot/0.1"] = (
    "atlas.insight-snapshot/0.1"
)
INSIGHT_METRIC_VERSION: Literal["1.0.0"] = "1.0.0"
INSIGHT_METRIC_POLICY_VERSION: Literal["0.1.0"] = "0.1.0"
INSIGHT_MAX_SOURCE_SNAPSHOTS = 20_000
INSIGHT_MINIMUM_SAMPLE: Literal[30] = 30
INSIGHT_WINDOW_DAYS = Literal[7, 30, 90]

NonNegativeCount = Annotated[int, Field(ge=0)]
BasisPoints = Annotated[int, Field(ge=0, le=10_000)]
SignedBasisPoints = Annotated[int, Field(ge=-10_000, le=10_000)]


class InsightMetricKey(StrEnum):
    """Platform-signed V1 metrics; callers cannot submit arbitrary formulas."""

    TRUSTED_PASS_RATE = "quality.trusted_pass_rate"
    AUTONOMOUS_TRUSTED_PASS_RATE = "quality.autonomous_trusted_pass_rate"
    METHOD_HEALTH_RATE = "quality.method_health_rate"


class InsightSampleStatus(StrEnum):
    """Explicit sample sufficiency without converting no-data into zero."""

    NO_DATA = "NO_DATA"
    LOW_SAMPLE = "LOW_SAMPLE"
    ENOUGH = "ENOUGH"


class InsightMetricDefinition(FrozenWireModel):
    """One immutable, platform-owned metric semantic contract."""

    metric_key: InsightMetricKey
    version: Literal["1.0.0"] = INSIGHT_METRIC_VERSION
    grain: Literal["UNIT"] = "UNIT"
    population: Literal["MANIFEST_UNITS"] = "MANIFEST_UNITS"
    aggregation: Literal["RATIO_OF_SUMS"] = "RATIO_OF_SUMS"
    event_time: Literal["QUALITY_FINALIZED_AT"] = "QUALITY_FINALIZED_AT"
    source_finality: Literal["FULLY_RESOLVED_OR_REEVALUATED"] = (
        "FULLY_RESOLVED_OR_REEVALUATED"
    )
    minimum_sample: Literal[30] = INSIGHT_MINIMUM_SAMPLE


class InsightMetricPoint(FrozenWireModel):
    """Exact ratio point with a display-only basis-point projection."""

    metric_key: InsightMetricKey
    metric_version: Literal["1.0.0"] = INSIGHT_METRIC_VERSION
    numerator: NonNegativeCount
    denominator: NonNegativeCount
    basis_points: BasisPoints | None
    sample_status: InsightSampleStatus

    @model_validator(mode="after")
    def validate_ratio(self) -> Self:
        """Keep the exact fraction, display value, and sample status consistent."""

        if self.numerator > self.denominator:
            raise ValueError("Insight metric numerator cannot exceed denominator")
        expected_basis_points = ratio_basis_points(self.numerator, self.denominator)
        if self.basis_points != expected_basis_points:
            raise ValueError("basisPoints must be derived from numerator and denominator")
        expected_status = sample_status(self.denominator)
        if self.sample_status is not expected_status:
            raise ValueError("sampleStatus must match the metric denominator")
        return self


class InsightWindowSummary(FrozenWireModel):
    """Ratio-of-sums metric output for one materialized UTC window."""

    start_at: AwareDatetime
    end_at: AwareDatetime
    task_run_count: NonNegativeCount
    execution_unit_count: NonNegativeCount
    trusted_pass_rate: InsightMetricPoint
    autonomous_trusted_pass_rate: InsightMetricPoint
    method_health_rate: InsightMetricPoint

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        """Require a positive window and one shared Manifest denominator."""

        if self.end_at <= self.start_at:
            raise ValueError("Insight window endAt must follow startAt")
        points = (
            self.trusted_pass_rate,
            self.autonomous_trusted_pass_rate,
            self.method_health_rate,
        )
        if any(point.denominator != self.execution_unit_count for point in points):
            raise ValueError("all Insight ratios must use the Manifest Unit denominator")
        if self.task_run_count == 0 and self.execution_unit_count != 0:
            raise ValueError("executionUnitCount requires at least one TaskRun")
        return self


class InsightMetricDeltas(FrozenWireModel):
    """Signed current-minus-baseline changes in basis points."""

    trusted_pass_rate: SignedBasisPoints | None
    autonomous_trusted_pass_rate: SignedBasisPoints | None
    method_health_rate: SignedBasisPoints | None


class InsightTerrainItem(FrozenWireModel):
    """One TaskPlan quality slice rendered in the existing terrain slots."""

    task_plan_id: UUID
    label: str = Field(min_length=1, max_length=160)
    task_run_count: int = Field(ge=1)
    execution_unit_count: int = Field(ge=1)
    trusted_pass_rate: InsightMetricPoint
    latest_task_run_id: UUID
    latest_result_snapshot_id: UUID

    @model_validator(mode="after")
    def validate_population(self) -> Self:
        """Keep the terrain metric tied to the displayed Unit population."""

        if self.trusted_pass_rate.denominator != self.execution_unit_count:
            raise ValueError("terrain rate denominator must equal executionUnitCount")
        return self


class InsightRiskSignal(FrozenWireModel):
    """Deterministic latest non-accepted Gate observation, without causal claims."""

    task_run_id: UUID
    result_snapshot_id: UUID
    task_plan_id: UUID
    task_plan_name: str = Field(min_length=1, max_length=160)
    gate_decision: TaskGateVerdict
    reason_count: NonNegativeCount
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_risk(self) -> Self:
        """Accepted Gates are not active risk signals."""

        if self.gate_decision is TaskGateVerdict.ACCEPTED:
            raise ValueError("accepted Gate cannot be an active risk signal")
        return self


class InsightDatasetCut(FrozenWireModel):
    """Reproducible source set and authorization fence for one brief."""

    schema_version: Literal["atlas.insight-dataset-cut/0.1"] = (
        INSIGHT_DATASET_CUT_SCHEMA_VERSION
    )
    as_of: AwareDatetime
    source_snapshot_ids: tuple[UUID, ...] = Field(
        max_length=INSIGHT_MAX_SOURCE_SNAPSHOTS,
    )
    source_snapshot_hashes: tuple[str, ...] = Field(
        max_length=INSIGHT_MAX_SOURCE_SNAPSHOTS,
    )
    gate_decision_ids: tuple[UUID, ...] = Field(
        max_length=INSIGHT_MAX_SOURCE_SNAPSHOTS,
    )
    gate_decision_hashes: tuple[str, ...] = Field(
        max_length=INSIGHT_MAX_SOURCE_SNAPSHOTS,
    )
    source_set_digest: str = Field(pattern=DIGEST_PATTERN)
    projection_watermark: AwareDatetime | None = None
    query_hash: str = Field(pattern=DIGEST_PATTERN)
    auth_scope_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        """Require ordered, unique, exact source IDs and matching hashes."""

        if len(self.source_snapshot_ids) != len(self.source_snapshot_hashes):
            raise ValueError("DatasetCut source IDs and hashes must have equal length")
        if len(set(self.source_snapshot_ids)) != len(self.source_snapshot_ids):
            raise ValueError("DatasetCut source Snapshot IDs must be unique")
        if len(self.gate_decision_ids) != len(self.gate_decision_hashes):
            raise ValueError("DatasetCut Gate IDs and hashes must have equal length")
        if len(set(self.gate_decision_ids)) != len(self.gate_decision_ids):
            raise ValueError("DatasetCut Gate decision IDs must be unique")
        if any(
            not _is_digest(value)
            for value in self.source_snapshot_hashes
        ):
            raise ValueError("DatasetCut source Snapshot hashes must be SHA-256")
        if any(not _is_digest(value) for value in self.gate_decision_hashes):
            raise ValueError("DatasetCut Gate decision hashes must be SHA-256")
        expected_digest = insight_source_set_digest(
            self.source_snapshot_ids,
            self.source_snapshot_hashes,
            self.gate_decision_ids,
            self.gate_decision_hashes,
        )
        if self.source_set_digest != expected_digest:
            raise ValueError("sourceSetDigest must bind the ordered source Snapshot set")
        if (
            not self.source_snapshot_ids
            and not self.gate_decision_ids
            and self.projection_watermark is not None
        ):
            raise ValueError("empty DatasetCut cannot claim a projection watermark")
        if (
            self.projection_watermark is not None
            and self.projection_watermark > self.as_of
        ):
            raise ValueError("projectionWatermark cannot exceed asOf")
        return self


class InsightBrief(FrozenWireModel):
    """Comparable current/baseline quality brief over one exact DatasetCut."""

    schema_version: Literal[
        "atlas.insight-brief/0.1",
        "atlas.insight-snapshot/0.1",
    ] = INSIGHT_BRIEF_SCHEMA_VERSION
    tenant_id: UUID
    project_id: UUID
    window_days: INSIGHT_WINDOW_DAYS
    metric_policy_version: Literal["0.1.0"] = INSIGHT_METRIC_POLICY_VERSION
    metric_definitions: tuple[InsightMetricDefinition, ...]
    current: InsightWindowSummary
    baseline: InsightWindowSummary
    deltas: InsightMetricDeltas
    terrain: tuple[InsightTerrainItem, ...] = Field(max_length=4)
    active_risk: InsightRiskSignal | None = None
    dataset_cut: InsightDatasetCut
    generated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_brief(self) -> Self:
        """Require adjacent equal windows, complete catalog, and exact deltas."""

        if self.current.end_at != self.dataset_cut.as_of:
            raise ValueError("current window must end at DatasetCut asOf")
        if self.baseline.end_at != self.current.start_at:
            raise ValueError("baseline and current windows must be adjacent")
        if self.current.end_at - self.current.start_at != (
            self.baseline.end_at - self.baseline.start_at
        ):
            raise ValueError("baseline and current windows must have equal duration")
        if self.generated_at != self.dataset_cut.as_of:
            raise ValueError("generatedAt must equal the reproducible DatasetCut asOf")
        expected_definitions = insight_metric_catalog()
        if self.metric_definitions != expected_definitions:
            raise ValueError("metricDefinitions must equal the platform-signed catalog")
        expected_deltas = InsightMetricDeltas(
            trusted_pass_rate=metric_delta_basis_points(
                self.current.trusted_pass_rate,
                self.baseline.trusted_pass_rate,
            ),
            autonomous_trusted_pass_rate=metric_delta_basis_points(
                self.current.autonomous_trusted_pass_rate,
                self.baseline.autonomous_trusted_pass_rate,
            ),
            method_health_rate=metric_delta_basis_points(
                self.current.method_health_rate,
                self.baseline.method_health_rate,
            ),
        )
        if self.deltas != expected_deltas:
            raise ValueError("Insight deltas must be current minus baseline")
        terrain_ids = [item.task_plan_id for item in self.terrain]
        if len(set(terrain_ids)) != len(terrain_ids):
            raise ValueError("terrain must contain unique TaskPlan slices")
        return self


class RequestInsightSnapshot(FrozenWireModel):
    """Idempotent command to pin one exact quality brief."""

    window_days: INSIGHT_WINDOW_DAYS = 30
    as_of: AwareDatetime | None = None
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class InsightSnapshot(InsightBrief):
    """Immutable pinned brief used for deep links, audit, and export inputs."""

    schema_version: Literal["atlas.insight-snapshot/0.1"] = (
        INSIGHT_SNAPSHOT_SCHEMA_VERSION
    )
    id: UUID
    request_hash: str = Field(pattern=DIGEST_PATTERN)
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    created_by: UUID
    created_at: AwareDatetime
    snapshot_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        """Reject altered pinned content or timestamps outside its knowledge fence."""

        if self.created_at < self.dataset_cut.as_of:
            raise ValueError("InsightSnapshot createdAt cannot predate DatasetCut asOf")
        if self.snapshot_hash != insight_snapshot_hash(self):
            raise ValueError("snapshotHash must match pinned Insight content")
        return self


def insight_metric_catalog() -> tuple[InsightMetricDefinition, ...]:
    """Return the complete platform-signed V1 metric catalog."""

    return tuple(
        InsightMetricDefinition(metric_key=metric_key)
        for metric_key in InsightMetricKey
    )


def ratio_basis_points(numerator: int, denominator: int) -> int | None:
    """Convert an exact ratio to deterministic half-up display basis points."""

    if denominator == 0:
        return None
    return (numerator * 10_000 + denominator // 2) // denominator


def sample_status(denominator: int) -> InsightSampleStatus:
    """Classify sample sufficiency without conflating no-data and low-data."""

    if denominator == 0:
        return InsightSampleStatus.NO_DATA
    if denominator < INSIGHT_MINIMUM_SAMPLE:
        return InsightSampleStatus.LOW_SAMPLE
    return InsightSampleStatus.ENOUGH


def metric_point(
    metric_key: InsightMetricKey,
    *,
    numerator: int,
    denominator: int,
) -> InsightMetricPoint:
    """Build one exact, self-validating metric point."""

    return InsightMetricPoint(
        metric_key=metric_key,
        numerator=numerator,
        denominator=denominator,
        basis_points=ratio_basis_points(numerator, denominator),
        sample_status=sample_status(denominator),
    )


def metric_delta_basis_points(
    current: InsightMetricPoint,
    baseline: InsightMetricPoint,
) -> int | None:
    """Return signed current-minus-baseline change only when both have data."""

    if current.basis_points is None or baseline.basis_points is None:
        return None
    return current.basis_points - baseline.basis_points


def insight_source_set_digest(
    source_snapshot_ids: tuple[UUID, ...],
    source_snapshot_hashes: tuple[str, ...],
    gate_decision_ids: tuple[UUID, ...] = (),
    gate_decision_hashes: tuple[str, ...] = (),
) -> str:
    """Hash an ordered source set using unambiguous fixed-format records."""

    if len(source_snapshot_ids) != len(source_snapshot_hashes):
        raise ValueError("source Snapshot IDs and hashes must have equal length")
    if len(gate_decision_ids) != len(gate_decision_hashes):
        raise ValueError("Gate decision IDs and hashes must have equal length")
    records = [
        f"result:{snapshot_id}:{snapshot_hash}"
        for snapshot_id, snapshot_hash in zip(
            source_snapshot_ids,
            source_snapshot_hashes,
            strict=True,
        )
    ]
    records.extend(
        f"gate:{decision_id}:{decision_hash}"
        for decision_id, decision_hash in zip(
            gate_decision_ids,
            gate_decision_hashes,
            strict=True,
        )
    )
    encoded = "\n".join(records).encode("ascii")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def insight_digest(value: JsonValue) -> str:
    """Hash canonical JSON with the repository-wide deterministic profile."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def insight_snapshot_document(
    value: InsightSnapshot,
) -> dict[str, JsonValue]:
    """Return semantic pinned content independent of storage identity."""

    return cast(
        dict[str, JsonValue],
        value.model_dump(
            mode="json",
            by_alias=True,
            exclude={
                "id",
                "request_hash",
                "client_mutation_id",
                "created_by",
                "created_at",
                "snapshot_hash",
            },
        ),
    )


def insight_snapshot_hash(value: InsightSnapshot) -> str:
    """Hash one pinned InsightSnapshot semantic document."""

    return insight_digest(insight_snapshot_document(value))


def _is_digest(value: str) -> bool:
    return (
        len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


__all__ = [
    "INSIGHT_BRIEF_SCHEMA_VERSION",
    "INSIGHT_DATASET_CUT_SCHEMA_VERSION",
    "INSIGHT_MAX_SOURCE_SNAPSHOTS",
    "INSIGHT_METRIC_POLICY_VERSION",
    "INSIGHT_METRIC_VERSION",
    "INSIGHT_MINIMUM_SAMPLE",
    "INSIGHT_SNAPSHOT_SCHEMA_VERSION",
    "InsightBrief",
    "InsightDatasetCut",
    "InsightMetricDefinition",
    "InsightMetricDeltas",
    "InsightMetricKey",
    "InsightMetricPoint",
    "InsightRiskSignal",
    "InsightSampleStatus",
    "InsightSnapshot",
    "InsightTerrainItem",
    "InsightWindowSummary",
    "RequestInsightSnapshot",
    "insight_digest",
    "insight_metric_catalog",
    "insight_snapshot_document",
    "insight_snapshot_hash",
    "insight_source_set_digest",
    "metric_delta_basis_points",
    "metric_point",
    "ratio_basis_points",
    "sample_status",
]
