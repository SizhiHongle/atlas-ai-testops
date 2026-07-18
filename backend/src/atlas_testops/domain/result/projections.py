"""Append-only Result projection contracts derived from terminal Attempt facts."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, JsonValue, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import DIGEST_PATTERN
from atlas_testops.domain.result.models import (
    SAFE_REASON_PATTERN,
    DataHygiene,
    EvidenceCompleteness,
    EvidenceIntegrity,
    ExecutionInfluence,
    OutcomeClass,
    Stability,
    Verdict,
)

if TYPE_CHECKING:
    from atlas_testops.domain.result.hygiene import UnitHygieneResolutionRevision

ATTEMPT_CLOSURE_NOTICE_SCHEMA_VERSION: Literal["atlas.attempt-closure-notice/0.1"] = (
    "atlas.attempt-closure-notice/0.1"
)
UNIT_RESOLUTION_REVISION_SCHEMA_VERSION: Literal["atlas.unit-resolution-revision/0.1"] = (
    "atlas.unit-resolution-revision/0.1"
)
UNIT_RESOLUTION_POLICY_VERSION: Literal["0.1.0"] = "0.1.0"
TASK_RESULT_SNAPSHOT_SCHEMA_VERSION: Literal["atlas.task-result-snapshot/0.1"] = (
    "atlas.task-result-snapshot/0.1"
)
TASK_RESULT_SNAPSHOT_POLICY_VERSION: Literal["0.1.0"] = "0.1.0"
TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION: Literal["atlas.task-result-snapshot/0.2"] = (
    "atlas.task-result-snapshot/0.2"
)
TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION: Literal["0.2.0"] = "0.2.0"
TASK_RESULT_SNAPSHOT_REEVALUATED_SCHEMA_VERSION: Literal["atlas.task-result-snapshot/0.3"] = (
    "atlas.task-result-snapshot/0.3"
)
TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION: Literal["0.3.0"] = "0.3.0"
TASK_RESULT_REEVALUATION_COMMAND_SCHEMA_VERSION: Literal[
    "atlas.task-result-reevaluation-command/0.1"
] = "atlas.task-result-reevaluation-command/0.1"

NonNegativeCount = Annotated[int, Field(ge=0)]


class AttemptClosureSourceStatus(StrEnum):
    """Trusted execution status used when an Attempt cannot produce a Seal."""

    FINISHED_UNSEALED = "FINISHED_UNSEALED"
    FAILED = "FAILED"
    INFRA_ERROR = "INFRA_ERROR"
    INCONCLUSIVE = "INCONCLUSIVE"
    CANCELED = "CANCELED"


class TaskResultSnapshotFinality(StrEnum):
    """Explicit stability level of one immutable Task result projection."""

    PROVISIONAL = "PROVISIONAL"
    QUALITY_FINAL = "QUALITY_FINAL"
    FULLY_RESOLVED = "FULLY_RESOLVED"
    REEVALUATED = "REEVALUATED"


class ResultPassRate(FrozenWireModel):
    """Exact rational pass rate without floating-point or rounding ambiguity."""

    numerator: NonNegativeCount
    denominator: NonNegativeCount

    @model_validator(mode="after")
    def validate_fraction(self) -> Self:
        """Keep every published rate inside the closed interval from zero to one."""

        if self.numerator > self.denominator:
            raise ValueError("pass rate numerator cannot exceed denominator")
        return self


class TaskVerdictCounts(FrozenWireModel):
    """Manifest-conserving final Verdict counts."""

    passed: NonNegativeCount
    failed: NonNegativeCount
    inconclusive: NonNegativeCount
    not_evaluated: NonNegativeCount


class TaskDataHygieneCounts(FrozenWireModel):
    """Task-level distribution of the DataHygiene axis."""

    pending: NonNegativeCount
    cleaned: NonNegativeCount
    cleanup_failed: NonNegativeCount
    leaked: NonNegativeCount
    not_applicable: NonNegativeCount


class TaskEvidenceCompletenessCounts(FrozenWireModel):
    """Task-level distribution of the EvidenceCompleteness axis."""

    pending: NonNegativeCount
    complete: NonNegativeCount
    partial: NonNegativeCount
    missing: NonNegativeCount
    not_applicable: NonNegativeCount


class TaskEvidenceIntegrityCounts(FrozenWireModel):
    """Task-level distribution of the EvidenceIntegrity axis."""

    unverified: NonNegativeCount
    verified: NonNegativeCount
    invalid: NonNegativeCount


class TaskExecutionInfluenceCounts(FrozenWireModel):
    """Task-level distribution of the ExecutionInfluence axis."""

    autonomous: NonNegativeCount
    manual_assisted: NonNegativeCount
    manual_only: NonNegativeCount


class TaskStabilityCounts(FrozenWireModel):
    """Task-level distribution of the Stability axis."""

    unknown: NonNegativeCount
    stable: NonNegativeCount
    infra_recovered: NonNegativeCount
    flaky_suspect: NonNegativeCount
    flaky_confirmed: NonNegativeCount


class TaskOutcomeClassCounts(FrozenWireModel):
    """Task-level distribution of the OutcomeClass axis."""

    business: NonNegativeCount
    dependency: NonNegativeCount
    platform: NonNegativeCount
    user: NonNegativeCount
    automation: NonNegativeCount
    policy: NonNegativeCount
    unknown: NonNegativeCount


class RequestTaskResultReevaluation(FrozenWireModel):
    """Idempotent request to apply the current frozen Policy to one Snapshot."""

    source_snapshot_id: UUID
    target_policy_version: Literal["0.3.0"] = TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class TaskResultReevaluationCommandContent(FrozenWireModel):
    """Explicit immutable request to reinterpret one exact final Snapshot."""

    schema_version: Literal["atlas.task-result-reevaluation-command/0.1"] = (
        TASK_RESULT_REEVALUATION_COMMAND_SCHEMA_VERSION
    )
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    source_snapshot_id: UUID
    target_policy_version: Literal["0.3.0"] = TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION
    target_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    requested_by: UUID | None = None
    requested_at: AwareDatetime

    @model_validator(mode="after")
    def validate_target_policy(self) -> Self:
        """Allow only the frozen server-owned re-evaluation policy."""

        if self.target_policy_digest != TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST:
            raise ValueError("targetPolicyDigest must match the frozen re-evaluation Policy")
        return self


class TaskResultReevaluationCommand(TaskResultReevaluationCommandContent):
    """Hashed command fact proving that re-evaluation was explicitly requested."""

    command_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_command_hash(self) -> Self:
        """Reject altered command scope, policy, requester, or mutation identity."""

        if self.command_hash != task_result_reevaluation_command_hash(self):
            raise ValueError("commandHash must match TaskResultReevaluationCommand content")
        return self


class TaskResultAxisDistributions(FrozenWireModel):
    """Complete fixed-cardinality axis distributions for one Task snapshot."""

    data_hygiene: TaskDataHygieneCounts
    evidence_completeness: TaskEvidenceCompletenessCounts
    evidence_integrity: TaskEvidenceIntegrityCounts
    execution_influence: TaskExecutionInfluenceCounts
    stability: TaskStabilityCounts
    outcome_class: TaskOutcomeClassCounts

    def totals(self) -> tuple[int, ...]:
        """Return each axis total in a fixed validation order."""

        return (
            sum(self.data_hygiene.model_dump().values()),
            sum(self.evidence_completeness.model_dump().values()),
            sum(self.evidence_integrity.model_dump().values()),
            sum(self.execution_influence.model_dump().values()),
            sum(self.stability.model_dump().values()),
            sum(self.outcome_class.model_dump().values()),
        )


class AttemptClosureNoticeContent(FrozenWireModel):
    """Canonical content proving why one terminal Attempt has no AttemptSeal."""

    schema_version: Literal["atlas.attempt-closure-notice/0.1"] = (
        ATTEMPT_CLOSURE_NOTICE_SCHEMA_VERSION
    )
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    unit_key: str = Field(pattern=DIGEST_PATTERN)
    attempt_number: int = Field(ge=1)
    source_status: AttemptClosureSourceStatus
    verdict: Verdict
    outcome_class: OutcomeClass
    closure_reason: str = Field(pattern=SAFE_REASON_PATTERN)
    data_hygiene: DataHygiene
    evidence_completeness: EvidenceCompleteness
    evidence_integrity: EvidenceIntegrity
    execution_influence: ExecutionInfluence
    closed_at: AwareDatetime
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_terminal_closure(self) -> Self:
        """Prevent a no-Seal closure from manufacturing a business judgment."""

        if self.verdict not in {Verdict.INCONCLUSIVE, Verdict.NOT_EVALUATED}:
            raise ValueError("AttemptClosureNotice can only be INCONCLUSIVE or NOT_EVALUATED")
        if self.evidence_integrity is not EvidenceIntegrity.UNVERIFIED:
            raise ValueError("AttemptClosureNotice evidence must remain UNVERIFIED")
        expected_completeness = (
            EvidenceCompleteness.NOT_APPLICABLE
            if self.verdict is Verdict.NOT_EVALUATED
            else EvidenceCompleteness.MISSING
        )
        if self.evidence_completeness is not expected_completeness:
            raise ValueError("AttemptClosureNotice evidence completeness must match its Verdict")
        if self.execution_influence is not ExecutionInfluence.AUTONOMOUS:
            raise ValueError("AttemptClosureNotice cannot infer manual execution influence")
        expected_outcome_class = {
            AttemptClosureSourceStatus.CANCELED: OutcomeClass.USER,
            AttemptClosureSourceStatus.INFRA_ERROR: OutcomeClass.PLATFORM,
            AttemptClosureSourceStatus.FINISHED_UNSEALED: OutcomeClass.AUTOMATION,
            AttemptClosureSourceStatus.FAILED: OutcomeClass.AUTOMATION,
            AttemptClosureSourceStatus.INCONCLUSIVE: OutcomeClass.UNKNOWN,
        }[self.source_status]
        if self.outcome_class is not expected_outcome_class:
            raise ValueError("AttemptClosureNotice OutcomeClass must match sourceStatus")
        if (
            self.source_status is AttemptClosureSourceStatus.INFRA_ERROR
            and self.verdict is not Verdict.INCONCLUSIVE
        ):
            raise ValueError("INFRA_ERROR closure must be INCONCLUSIVE")
        if (
            self.source_status is not AttemptClosureSourceStatus.CANCELED
            and self.verdict is not Verdict.INCONCLUSIVE
        ):
            raise ValueError("only CANCELED closure can be NOT_EVALUATED")
        if self.created_at < self.closed_at:
            raise ValueError("AttemptClosureNotice createdAt cannot predate closedAt")
        return self


class AttemptClosureNotice(AttemptClosureNoticeContent):
    """Immutable hashed terminal fact used only when no AttemptSeal exists."""

    notice_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_notice_hash(self) -> Self:
        """Require the stored digest to match the complete closure content."""

        if self.notice_hash != attempt_closure_notice_hash(self):
            raise ValueError("noticeHash must match AttemptClosureNotice content")
        return self


class UnitResolutionRevision(FrozenWireModel):
    """One append-only interpretation of every terminal Attempt for a Unit."""

    schema_version: Literal["atlas.unit-resolution-revision/0.1"] = (
        UNIT_RESOLUTION_REVISION_SCHEMA_VERSION
    )
    id: UUID
    unit_resolution_id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    unit_key: str = Field(pattern=DIGEST_PATTERN)
    revision: int = Field(ge=1)
    input_seal_ids: tuple[UUID, ...] = Field(max_length=100)
    input_closure_notice_ids: tuple[UUID, ...] = Field(max_length=100)
    input_set_hash: str = Field(pattern=DIGEST_PATTERN)
    effective_verdict: Verdict
    outcome_class: OutcomeClass
    closure_reason: str = Field(pattern=SAFE_REASON_PATTERN)
    data_hygiene: DataHygiene
    evidence_completeness: EvidenceCompleteness
    evidence_integrity: EvidenceIntegrity
    execution_influence: ExecutionInfluence
    stability: Stability
    decisive_unit_attempt_id: UUID
    decisive_attempt_number: int = Field(ge=1)
    resolution_policy_version: Literal["0.1.0"] = UNIT_RESOLUTION_POLICY_VERSION
    resolution_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    supersedes_revision_id: UUID | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_revision_chain(self) -> Self:
        """Require a non-provisional result and a well-formed append-only chain."""

        if self.effective_verdict is Verdict.PENDING:
            raise ValueError("UnitResolutionRevision cannot contain PENDING")
        if not self.input_seal_ids and not self.input_closure_notice_ids:
            raise ValueError("UnitResolutionRevision requires at least one input fact")
        if len(set(self.input_seal_ids)) != len(self.input_seal_ids):
            raise ValueError("inputSealIds must be unique")
        if len(set(self.input_closure_notice_ids)) != len(self.input_closure_notice_ids):
            raise ValueError("inputClosureNoticeIds must be unique")
        if self.revision == 1 and self.supersedes_revision_id is not None:
            raise ValueError("first UnitResolutionRevision cannot supersede another")
        if self.revision > 1 and self.supersedes_revision_id is None:
            raise ValueError("later UnitResolutionRevision requires its predecessor")
        if self.resolution_policy_digest != UNIT_RESOLUTION_POLICY_DIGEST:
            raise ValueError("resolutionPolicyDigest must match the frozen Resolution Policy")
        if self.effective_verdict is Verdict.PASSED and (
            self.evidence_completeness is not EvidenceCompleteness.COMPLETE
            or self.evidence_integrity is not EvidenceIntegrity.VERIFIED
        ):
            raise ValueError("PASSED UnitResolutionRevision requires complete verified evidence")
        return self


class TaskResultSnapshotContent(FrozenWireModel):
    """Semantic Task projection content independent of its content hash."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "required": ["finality"],
                        "properties": {
                            "finality": {"const": "QUALITY_FINAL"},
                        },
                    },
                    "then": {
                        "properties": {
                            "schemaVersion": {
                                "const": "atlas.task-result-snapshot/0.1",
                            },
                            "aggregationPolicyVersion": {"const": "0.1.0"},
                            "unitHygieneResolutionRevisionIds": {"type": "null"},
                            "inputHygieneResolutionSetHash": {"type": "null"},
                            "reevaluationSourceSnapshotId": {"type": "null"},
                            "reevaluationCommandId": {"type": "null"},
                        }
                    },
                },
                {
                    "if": {
                        "required": ["finality"],
                        "properties": {
                            "finality": {"const": "FULLY_RESOLVED"},
                        },
                    },
                    "then": {
                        "required": [
                            "unitHygieneResolutionRevisionIds",
                            "inputHygieneResolutionSetHash",
                        ],
                        "properties": {
                            "schemaVersion": {
                                "const": "atlas.task-result-snapshot/0.2",
                            },
                            "aggregationPolicyVersion": {"const": "0.2.0"},
                            "unitHygieneResolutionRevisionIds": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 100000,
                                "items": {
                                    "type": "string",
                                    "format": "uuid",
                                },
                            },
                            "inputHygieneResolutionSetHash": {
                                "type": "string",
                                "pattern": DIGEST_PATTERN,
                            },
                            "reevaluationSourceSnapshotId": {"type": "null"},
                            "reevaluationCommandId": {"type": "null"},
                        },
                    },
                },
                {
                    "if": {
                        "required": ["finality"],
                        "properties": {
                            "finality": {"const": "REEVALUATED"},
                        },
                    },
                    "then": {
                        "required": [
                            "unitHygieneResolutionRevisionIds",
                            "inputHygieneResolutionSetHash",
                            "reevaluationSourceSnapshotId",
                            "reevaluationCommandId",
                        ],
                        "properties": {
                            "schemaVersion": {
                                "const": "atlas.task-result-snapshot/0.3",
                            },
                            "aggregationPolicyVersion": {"const": "0.3.0"},
                            "unitHygieneResolutionRevisionIds": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 100000,
                                "items": {
                                    "type": "string",
                                    "format": "uuid",
                                },
                            },
                            "inputHygieneResolutionSetHash": {
                                "type": "string",
                                "pattern": DIGEST_PATTERN,
                            },
                            "reevaluationSourceSnapshotId": {
                                "type": "string",
                                "format": "uuid",
                            },
                            "reevaluationCommandId": {
                                "type": "string",
                                "format": "uuid",
                            },
                        },
                    },
                },
            ]
        }
    )

    schema_version: Literal[
        "atlas.task-result-snapshot/0.1",
        "atlas.task-result-snapshot/0.2",
        "atlas.task-result-snapshot/0.3",
    ] = TASK_RESULT_SNAPSHOT_SCHEMA_VERSION
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    revision: int = Field(ge=1)
    finality: Literal[
        TaskResultSnapshotFinality.QUALITY_FINAL,
        TaskResultSnapshotFinality.FULLY_RESOLVED,
        TaskResultSnapshotFinality.REEVALUATED,
    ] = TaskResultSnapshotFinality.QUALITY_FINAL
    unit_resolution_revision_ids: tuple[UUID, ...] = Field(
        min_length=1,
        max_length=100_000,
    )
    input_resolution_set_hash: str = Field(pattern=DIGEST_PATTERN)
    unit_hygiene_resolution_revision_ids: tuple[UUID, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=100_000,
    )
    input_hygiene_resolution_set_hash: str | None = Field(
        default=None,
        pattern=DIGEST_PATTERN,
    )
    reevaluation_source_snapshot_id: UUID | None = None
    reevaluation_command_id: UUID | None = None
    aggregation_policy_version: Literal["0.1.0", "0.2.0", "0.3.0"] = (
        TASK_RESULT_SNAPSHOT_POLICY_VERSION
    )
    aggregation_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    projection_watermark: AwareDatetime
    manifest_count: int = Field(ge=1, le=100_000)
    verdict_counts: TaskVerdictCounts
    axis_distributions: TaskResultAxisDistributions
    raw_pass_rate: ResultPassRate
    trusted_pass_rate: ResultPassRate
    autonomous_pass_rate: ResultPassRate
    decisive_pass_rate: ResultPassRate
    supersedes_snapshot_id: UUID | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_snapshot_content(self) -> Self:
        """Enforce exact coverage, rates, policy, and append-only lineage."""

        if len(self.unit_resolution_revision_ids) != self.manifest_count:
            raise ValueError("unitResolutionRevisionIds must exactly cover manifestCount")
        if len(set(self.unit_resolution_revision_ids)) != self.manifest_count:
            raise ValueError("unitResolutionRevisionIds must be unique")
        verdict_total = sum(self.verdict_counts.model_dump().values())
        if verdict_total != self.manifest_count:
            raise ValueError("Verdict counts must conserve manifestCount")
        if any(total != self.manifest_count for total in self.axis_distributions.totals()):
            raise ValueError("every axis distribution must conserve manifestCount")
        if self.revision == 1 and self.supersedes_snapshot_id is not None:
            raise ValueError("first TaskResultSnapshot cannot supersede another")
        if self.revision > 1 and self.supersedes_snapshot_id is None:
            raise ValueError("later TaskResultSnapshot requires its predecessor")
        expected_policy = {
            TaskResultSnapshotFinality.QUALITY_FINAL: TASK_RESULT_SNAPSHOT_POLICY_DIGEST,
            TaskResultSnapshotFinality.FULLY_RESOLVED: (
                TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST
            ),
            TaskResultSnapshotFinality.REEVALUATED: (
                TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST
            ),
        }[self.finality]
        if self.aggregation_policy_digest != expected_policy:
            raise ValueError("aggregationPolicyDigest must match the frozen Snapshot Policy")
        if self.finality is TaskResultSnapshotFinality.QUALITY_FINAL:
            if (
                self.schema_version != TASK_RESULT_SNAPSHOT_SCHEMA_VERSION
                or self.aggregation_policy_version != TASK_RESULT_SNAPSHOT_POLICY_VERSION
                or self.unit_hygiene_resolution_revision_ids is not None
                or self.input_hygiene_resolution_set_hash is not None
                or self.reevaluation_source_snapshot_id is not None
                or self.reevaluation_command_id is not None
            ):
                raise ValueError("QUALITY_FINAL must preserve the 0.1 Snapshot shape")
        elif self.finality is TaskResultSnapshotFinality.FULLY_RESOLVED:
            if (
                self.schema_version != TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION
                or self.aggregation_policy_version
                != TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION
                or self.unit_hygiene_resolution_revision_ids is None
                or self.input_hygiene_resolution_set_hash is None
                or self.reevaluation_source_snapshot_id is not None
                or self.reevaluation_command_id is not None
            ):
                raise ValueError("FULLY_RESOLVED requires the complete 0.2 Hygiene input shape")
        elif (
            self.schema_version != TASK_RESULT_SNAPSHOT_REEVALUATED_SCHEMA_VERSION
            or self.aggregation_policy_version != TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION
            or self.unit_hygiene_resolution_revision_ids is None
            or self.input_hygiene_resolution_set_hash is None
            or self.reevaluation_source_snapshot_id is None
            or self.reevaluation_command_id is None
        ):
            raise ValueError("REEVALUATED requires the complete 0.3 command-bound input shape")
        if self.finality is not TaskResultSnapshotFinality.QUALITY_FINAL:
            assert self.unit_hygiene_resolution_revision_ids is not None
            if len(self.unit_hygiene_resolution_revision_ids) != self.manifest_count:
                raise ValueError(
                    "unitHygieneResolutionRevisionIds must exactly cover manifestCount"
                )
            if len(set(self.unit_hygiene_resolution_revision_ids)) != self.manifest_count:
                raise ValueError("unitHygieneResolutionRevisionIds must be unique")
            if (
                self.axis_distributions.data_hygiene.pending
                or self.axis_distributions.data_hygiene.cleanup_failed
            ):
                raise ValueError("FULLY_RESOLVED cannot contain unresolved Hygiene")
        expected_rates = (
            ResultPassRate(
                numerator=self.verdict_counts.passed,
                denominator=self.manifest_count,
            ),
            ResultPassRate(
                numerator=self.trusted_pass_rate.numerator,
                denominator=self.manifest_count,
            ),
            ResultPassRate(
                numerator=self.autonomous_pass_rate.numerator,
                denominator=self.manifest_count,
            ),
            ResultPassRate(
                numerator=self.verdict_counts.passed,
                denominator=(self.verdict_counts.passed + self.verdict_counts.failed),
            ),
        )
        actual_rates = (
            self.raw_pass_rate,
            self.trusted_pass_rate,
            self.autonomous_pass_rate,
            self.decisive_pass_rate,
        )
        if actual_rates != expected_rates:
            raise ValueError("pass rates must match the frozen Snapshot Policy")
        if (
            self.trusted_pass_rate.numerator > self.verdict_counts.passed
            or self.autonomous_pass_rate.numerator > self.verdict_counts.passed
        ):
            raise ValueError("qualified pass counts cannot exceed passed Verdicts")
        if self.created_at < self.projection_watermark:
            raise ValueError("createdAt cannot predate projectionWatermark")
        return self


class TaskResultSnapshot(TaskResultSnapshotContent):
    """Immutable hashed Task-level projection over exact UnitResolution revisions."""

    snapshot_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_snapshot_hash(self) -> Self:
        """Reject any Task result whose semantic content was altered."""

        if self.snapshot_hash != task_result_snapshot_hash(self):
            raise ValueError("snapshotHash must match semantic Snapshot content")
        return self


def attempt_closure_notice_body(
    value: AttemptClosureNoticeContent | AttemptClosureNotice,
) -> dict[str, JsonValue]:
    """Return the exact closure content covered by noticeHash."""

    return cast(
        dict[str, JsonValue],
        value.model_dump(
            mode="json",
            by_alias=True,
            exclude={"notice_hash"},
        ),
    )


def attempt_closure_notice_hash(
    value: AttemptClosureNoticeContent | AttemptClosureNotice,
) -> str:
    """Hash one complete closure notice using the repository canonical profile."""

    encoded = json.dumps(
        attempt_closure_notice_body(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def result_projection_digest(value: dict[str, JsonValue]) -> str:
    """Hash deterministic projection input or policy content."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def task_result_resolution_set_hash(
    *,
    task_run_id: UUID,
    manifest_hash: str,
    resolutions: tuple[UnitResolutionRevision, ...],
) -> str:
    """Hash Manifest-ordered UnitResolution roots without rereading mutable inputs."""

    return result_projection_digest(
        {
            "schemaVersion": "atlas.task-result-resolution-set/0.1",
            "taskRunId": str(task_run_id),
            "manifestHash": manifest_hash,
            "inputs": [
                {
                    "ordinal": ordinal,
                    "executionUnitId": str(resolution.execution_unit_id),
                    "unitResolutionRevisionId": str(resolution.id),
                    "revision": resolution.revision,
                    "inputSetHash": resolution.input_set_hash,
                }
                for ordinal, resolution in enumerate(resolutions, start=1)
            ],
        }
    )


def task_result_hygiene_resolution_set_hash(
    *,
    task_run_id: UUID,
    manifest_hash: str,
    resolutions: tuple[UnitHygieneResolutionRevision, ...],
) -> str:
    """Hash Manifest-ordered Unit Hygiene roots and their frozen policies."""

    return result_projection_digest(
        {
            "schemaVersion": "atlas.task-result-hygiene-resolution-set/0.1",
            "taskRunId": str(task_run_id),
            "manifestHash": manifest_hash,
            "inputs": [
                {
                    "ordinal": ordinal,
                    "executionUnitId": str(resolution.execution_unit_id),
                    "unitHygieneResolutionRevisionId": str(resolution.id),
                    "revision": resolution.revision,
                    "inputSetHash": resolution.input_set_hash,
                    "dataHygiene": resolution.data_hygiene.value,
                    "resolutionPolicyDigest": resolution.resolution_policy_digest,
                    "resolutionHash": resolution.resolution_hash,
                }
                for ordinal, resolution in enumerate(resolutions, start=1)
            ],
        }
    )


def task_result_snapshot_document(
    value: TaskResultSnapshotContent | TaskResultSnapshot,
) -> dict[str, JsonValue]:
    """Return the version-exact persisted Snapshot document."""

    document = cast(
        dict[str, JsonValue],
        value.model_dump(mode="json", by_alias=True),
    )
    if value.finality is TaskResultSnapshotFinality.QUALITY_FINAL:
        document.pop("unitHygieneResolutionRevisionIds", None)
        document.pop("inputHygieneResolutionSetHash", None)
        document.pop("reevaluationSourceSnapshotId", None)
        document.pop("reevaluationCommandId", None)
    elif value.finality is TaskResultSnapshotFinality.FULLY_RESOLVED:
        document.pop("reevaluationSourceSnapshotId", None)
        document.pop("reevaluationCommandId", None)
    return document


def task_result_snapshot_hash(
    value: TaskResultSnapshotContent | TaskResultSnapshot,
) -> str:
    """Hash only reproducible Snapshot semantics, excluding identity and write time."""

    body = task_result_snapshot_document(value)
    for field in (
        "id",
        "revision",
        "supersedesSnapshotId",
        "reevaluationCommandId",
        "createdAt",
        "snapshotHash",
    ):
        body.pop(field, None)
    return result_projection_digest(body)


def task_result_reevaluation_command_body(
    value: TaskResultReevaluationCommandContent | TaskResultReevaluationCommand,
) -> dict[str, JsonValue]:
    """Return the exact immutable request content covered by commandHash."""

    return cast(
        dict[str, JsonValue],
        value.model_dump(
            mode="json",
            by_alias=True,
            exclude={"command_hash"},
        ),
    )


def task_result_reevaluation_command_hash(
    value: TaskResultReevaluationCommandContent | TaskResultReevaluationCommand,
) -> str:
    """Hash one explicit re-evaluation command using the canonical profile."""

    return result_projection_digest(task_result_reevaluation_command_body(value))


UNIT_RESOLUTION_POLICY_DIGEST = result_projection_digest(
    {
        "schemaVersion": "atlas.unit-resolution-policy/0.1",
        "policyVersion": UNIT_RESOLUTION_POLICY_VERSION,
        "invalidOrUnverifiedEvidence": "INCONCLUSIVE",
        "closureNoticeVerdicts": ["INCONCLUSIVE", "NOT_EVALUATED"],
        "retryResolution": "LATEST_COMPARABLE_ATTEMPT_WITH_HISTORY",
        "stabilityRules": [
            "SINGLE_DECISIVE_STABLE",
            "FAILED_THEN_PASSED_FLAKY_SUSPECT",
            "INFRA_THEN_PASSED_INFRA_RECOVERED",
            "IDENTICAL_FAILURE_STABLE",
        ],
    }
)

TASK_RESULT_SNAPSHOT_POLICY_DIGEST = result_projection_digest(
    {
        "schemaVersion": "atlas.task-result-snapshot-policy/0.1",
        "policyVersion": TASK_RESULT_SNAPSHOT_POLICY_VERSION,
        "inputProjection": "LATEST_UNIT_RESOLUTION_REVISION_BY_MANIFEST_ORDINAL",
        "finality": "QUALITY_FINAL_AFTER_EXACT_MANIFEST_COVERAGE",
        "passRates": {
            "raw": "PASSED_OVER_MANIFEST",
            "trusted": "COMPLETE_VERIFIED_PASSED_OVER_MANIFEST",
            "autonomous": "AUTONOMOUS_PASSED_OVER_MANIFEST",
            "decisive": "PASSED_OVER_PASSED_PLUS_FAILED",
        },
        "inputRoot": "CLOSURE_NOTICE_COMPATIBLE_RESOLUTION_SET",
    }
)

TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST = result_projection_digest(
    {
        "schemaVersion": "atlas.task-result-snapshot-policy/0.2",
        "policyVersion": TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION,
        "qualityInputProjection": "LATEST_UNIT_RESOLUTION_REVISION_BY_MANIFEST_ORDINAL",
        "hygieneInputProjection": ("LATEST_UNIT_HYGIENE_RESOLUTION_REVISION_BY_MANIFEST_ORDINAL"),
        "finality": "FULLY_RESOLVED_AFTER_TERMINAL_HYGIENE_COVERAGE",
        "terminalHygiene": ["CLEANED", "LEAKED", "NOT_APPLICABLE"],
        "axisOverlay": "DATA_HYGIENE_FROM_HYGIENE_RESOLUTION",
        "otherAxes": "PRESERVE_QUALITY_RESOLUTION",
        "passRates": {
            "raw": "PASSED_OVER_MANIFEST",
            "trusted": "COMPLETE_VERIFIED_PASSED_OVER_MANIFEST",
            "autonomous": "AUTONOMOUS_PASSED_OVER_MANIFEST",
            "decisive": "PASSED_OVER_PASSED_PLUS_FAILED",
        },
        "inputRoot": "QUALITY_AND_HYGIENE_RESOLUTION_SETS",
    }
)

TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST = result_projection_digest(
    {
        "schemaVersion": "atlas.task-result-snapshot-policy/0.3",
        "policyVersion": TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION,
        "sourceProjection": "EXACT_FULLY_RESOLVED_SNAPSHOT",
        "trigger": "EXPLICIT_REEVALUATION_COMMAND_ONLY",
        "facts": "PRESERVE_SOURCE_RESOLUTION_AND_HYGIENE_ROOTS",
        "axisDistributions": "PRESERVE_SOURCE",
        "passRates": {
            "raw": "PASSED_OVER_MANIFEST",
            "trusted": "COMPLETE_VERIFIED_PASSED_OVER_MANIFEST",
            "autonomous": "AUTONOMOUS_PASSED_OVER_MANIFEST",
            "decisive": "PASSED_OVER_PASSED_PLUS_FAILED",
        },
        "inputRoot": "SOURCE_FULLY_RESOLVED_SNAPSHOT",
        "history": "POLICY_PUBLICATION_DOES_NOT_AUTO_REEVALUATE",
    }
)


__all__ = [
    "ATTEMPT_CLOSURE_NOTICE_SCHEMA_VERSION",
    "TASK_RESULT_REEVALUATION_COMMAND_SCHEMA_VERSION",
    "TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST",
    "TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION",
    "TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION",
    "TASK_RESULT_SNAPSHOT_POLICY_DIGEST",
    "TASK_RESULT_SNAPSHOT_POLICY_VERSION",
    "TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST",
    "TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION",
    "TASK_RESULT_SNAPSHOT_REEVALUATED_SCHEMA_VERSION",
    "TASK_RESULT_SNAPSHOT_SCHEMA_VERSION",
    "UNIT_RESOLUTION_POLICY_DIGEST",
    "UNIT_RESOLUTION_POLICY_VERSION",
    "UNIT_RESOLUTION_REVISION_SCHEMA_VERSION",
    "AttemptClosureNotice",
    "AttemptClosureNoticeContent",
    "AttemptClosureSourceStatus",
    "RequestTaskResultReevaluation",
    "ResultPassRate",
    "TaskDataHygieneCounts",
    "TaskEvidenceCompletenessCounts",
    "TaskEvidenceIntegrityCounts",
    "TaskExecutionInfluenceCounts",
    "TaskOutcomeClassCounts",
    "TaskResultAxisDistributions",
    "TaskResultReevaluationCommand",
    "TaskResultReevaluationCommandContent",
    "TaskResultSnapshot",
    "TaskResultSnapshotContent",
    "TaskResultSnapshotFinality",
    "TaskStabilityCounts",
    "TaskVerdictCounts",
    "UnitResolutionRevision",
    "attempt_closure_notice_body",
    "attempt_closure_notice_hash",
    "result_projection_digest",
    "task_result_hygiene_resolution_set_hash",
    "task_result_reevaluation_command_body",
    "task_result_reevaluation_command_hash",
    "task_result_resolution_set_hash",
    "task_result_snapshot_document",
    "task_result_snapshot_hash",
]
