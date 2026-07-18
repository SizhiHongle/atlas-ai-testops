"""Snapshot-bound, append-only Task Gate decision contracts."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import DIGEST_PATTERN
from atlas_testops.domain.result.classification import (
    ClassificationAuthorKind,
    ClassificationJudgmentState,
    FailureClassificationRevision,
    FailureClusterRevision,
    FailureDomain,
)
from atlas_testops.domain.result.projections import (
    TaskResultSnapshot,
    TaskResultSnapshotFinality,
)

TASK_GATE_DECISION_SCHEMA_VERSION: Literal["atlas.task-gate-decision/0.1"] = (
    "atlas.task-gate-decision/0.1"
)
TASK_GATE_CLASSIFICATION_SET_SCHEMA_VERSION: Literal[
    "atlas.task-gate-classification-set/0.1"
] = "atlas.task-gate-classification-set/0.1"
TASK_GATE_POLICY_VERSION: Literal["0.1.0"] = "0.1.0"
TASK_GATE_MUTATION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
TASK_GATE_HUMAN_CONFIDENCE_MINIMUM = 7_000
TASK_GATE_RULE_CONFIDENCE_MINIMUM = 9_500

NonNegativeCount = Annotated[int, Field(ge=0)]


class TaskGateVerdict(StrEnum):
    """Three-valued release decision; uncertainty never becomes acceptance."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class TaskGateReasonCode(StrEnum):
    """Bounded, stable reasons emitted by the frozen strict Gate policy."""

    CLASSIFICATION_NOT_GATE_READY = "CLASSIFICATION_NOT_GATE_READY"
    DATA_HYGIENE_UNRESOLVED = "DATA_HYGIENE_UNRESOLVED"
    DATA_LEAK = "DATA_LEAK"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    EVIDENCE_INVALID_OR_UNVERIFIED = "EVIDENCE_INVALID_OR_UNVERIFIED"
    FAILED_UNITS = "FAILED_UNITS"
    INCONCLUSIVE_UNITS = "INCONCLUSIVE_UNITS"
    MANUAL_INFLUENCE = "MANUAL_INFLUENCE"
    NOT_EVALUATED_UNITS = "NOT_EVALUATED_UNITS"
    SNAPSHOT_NOT_FULLY_RESOLVED = "SNAPSHOT_NOT_FULLY_RESOLVED"
    UNSTABLE_EXECUTION = "UNSTABLE_EXECUTION"


class TaskGateReason(FrozenWireModel):
    """One machine-readable reason and the exact affected count."""

    code: TaskGateReasonCode
    count: int = Field(ge=1)


class TaskGateClassificationInput(FrozenWireModel):
    """Exact current Cluster and Classification revisions consumed by Gate."""

    failure_cluster_revision_id: UUID
    cluster_hash: str = Field(pattern=DIGEST_PATTERN)
    failure_classification_revision_id: UUID
    classification_revision: int = Field(ge=1)
    classification_hash: str = Field(pattern=DIGEST_PATTERN)


class RequestTaskGateEvaluation(FrozenWireModel):
    """Idempotent request to evaluate one exact immutable Result Snapshot."""

    result_snapshot_id: UUID
    gate_policy_version: Literal["0.1.0"] = TASK_GATE_POLICY_VERSION
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=TASK_GATE_MUTATION_PATTERN,
    )


class TaskGateDecisionContent(FrozenWireModel):
    """One immutable Gate evaluation over exact Snapshot and Classification facts."""

    schema_version: Literal["atlas.task-gate-decision/0.1"] = (
        TASK_GATE_DECISION_SCHEMA_VERSION
    )
    id: UUID
    task_gate_id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    result_snapshot_id: UUID
    result_snapshot_hash: str = Field(pattern=DIGEST_PATTERN)
    revision: int = Field(ge=1)
    failure_classification_revision_ids: tuple[UUID, ...] = Field(max_length=10_000)
    classification_set_hash: str = Field(pattern=DIGEST_PATTERN)
    gate_policy_version: Literal["0.1.0"] = TASK_GATE_POLICY_VERSION
    gate_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    decision: TaskGateVerdict
    reasons: tuple[TaskGateReason, ...] = Field(max_length=len(TaskGateReasonCode))
    evaluated_by: UUID
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=TASK_GATE_MUTATION_PATTERN,
    )
    supersedes_gate_decision_id: UUID | None = None
    evaluated_at: AwareDatetime

    @field_validator("failure_classification_revision_ids")
    @classmethod
    def require_unique_classification_revisions(
        cls,
        values: tuple[UUID, ...],
    ) -> tuple[UUID, ...]:
        """A Gate consumes each exact Classification revision at most once."""

        if len(values) != len(set(values)):
            raise ValueError("failureClassificationRevisionIds must be unique")
        return values

    @field_validator("reasons")
    @classmethod
    def require_canonical_reasons(
        cls,
        values: tuple[TaskGateReason, ...],
    ) -> tuple[TaskGateReason, ...]:
        """Keep reason ordering stable for hashing and API comparisons."""

        codes = tuple(reason.code.value for reason in values)
        if len(codes) != len(set(codes)):
            raise ValueError("Task Gate reason codes must be unique")
        if codes != tuple(sorted(codes)):
            raise ValueError("Task Gate reasons must be canonically ordered")
        return values

    @model_validator(mode="after")
    def validate_decision_content(self) -> Self:
        """Enforce frozen policy, append-only lineage, and verdict shape."""

        if self.gate_policy_digest != TASK_GATE_POLICY_DIGEST:
            raise ValueError("gatePolicyDigest must match the frozen Task Gate Policy")
        if self.revision == 1 and self.supersedes_gate_decision_id is not None:
            raise ValueError("first TaskGateDecision cannot supersede another")
        if self.revision > 1 and self.supersedes_gate_decision_id is None:
            raise ValueError("later TaskGateDecision requires its predecessor")
        if self.decision is TaskGateVerdict.ACCEPTED and self.reasons:
            raise ValueError("ACCEPTED TaskGateDecision cannot contain blocking reasons")
        if self.decision is not TaskGateVerdict.ACCEPTED and not self.reasons:
            raise ValueError("non-accepted TaskGateDecision requires at least one reason")
        return self


class TaskGateDecision(TaskGateDecisionContent):
    """Hashed immutable Gate fact."""

    decision_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_decision_hash(self) -> Self:
        """Reject altered Gate inputs, policy, reasons, or evaluator."""

        if self.decision_hash != task_gate_decision_hash(self):
            raise ValueError("decisionHash must match TaskGateDecision semantic content")
        return self


def task_gate_digest(value: dict[str, JsonValue]) -> str:
    """Hash one deterministic Gate document."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def task_gate_classification_set_hash(
    *,
    result_snapshot_id: UUID,
    inputs: tuple[TaskGateClassificationInput, ...],
) -> str:
    """Hash the canonically ordered exact Classification set."""

    return task_gate_digest(
        {
            "schemaVersion": TASK_GATE_CLASSIFICATION_SET_SCHEMA_VERSION,
            "resultSnapshotId": str(result_snapshot_id),
            "inputs": [
                {
                    "ordinal": ordinal,
                    **item.model_dump(mode="json", by_alias=True),
                }
                for ordinal, item in enumerate(inputs, start=1)
            ],
        }
    )


def task_gate_decision_document(
    value: TaskGateDecisionContent | TaskGateDecision,
) -> dict[str, JsonValue]:
    """Return the exact persisted TaskGateDecision document."""

    return cast(
        dict[str, JsonValue],
        value.model_dump(mode="json", by_alias=True),
    )


def task_gate_decision_hash(
    value: TaskGateDecisionContent | TaskGateDecision,
) -> str:
    """Hash complete Gate semantics while excluding append-only storage identity."""

    body = task_gate_decision_document(value)
    for field in (
        "id",
        "taskGateId",
        "revision",
        "supersedesGateDecisionId",
        "evaluatedAt",
        "decisionHash",
    ):
        body.pop(field, None)
    return task_gate_digest(body)


def task_gate_classification_is_ready(
    classification: FailureClassificationRevision,
) -> bool:
    """Return whether the frozen Gate policy may consume one judgment."""

    if classification.failure_domain is FailureDomain.UNKNOWN:
        return False
    if classification.evidence_gap_codes:
        return False
    if (
        classification.author_kind is ClassificationAuthorKind.SYSTEM_RULE
        and classification.judgment_state is ClassificationJudgmentState.RULE_PROPOSED
    ):
        return (
            classification.failure_domain
            in {
                FailureDomain.CLEANUP,
                FailureDomain.EVIDENCE,
                FailureDomain.POLICY_SECURITY,
            }
            and classification.confidence.numerator
            >= TASK_GATE_RULE_CONFIDENCE_MINIMUM
        )
    return (
        classification.author_kind is ClassificationAuthorKind.HUMAN
        and classification.judgment_state
        in {
            ClassificationJudgmentState.HUMAN_CONFIRMED,
            ClassificationJudgmentState.HUMAN_REVISED,
        }
        and classification.confidence.numerator
        >= TASK_GATE_HUMAN_CONFIDENCE_MINIMUM
    )


def evaluate_task_gate(
    snapshot: TaskResultSnapshot,
    classifications: tuple[FailureClassificationRevision, ...],
) -> tuple[TaskGateVerdict, tuple[TaskGateReason, ...]]:
    """Apply the frozen strict Gate policy to exact immutable inputs."""

    if len({item.id for item in classifications}) != len(classifications):
        raise ValueError("Task Gate classifications must be unique")
    if any(item.result_snapshot_id != snapshot.id for item in classifications):
        raise ValueError("Task Gate classifications must bind the exact Snapshot")

    reasons: dict[TaskGateReasonCode, int] = {}

    def add(code: TaskGateReasonCode, count: int) -> None:
        if count > 0:
            reasons[code] = count

    if snapshot.finality is TaskResultSnapshotFinality.QUALITY_FINAL:
        add(TaskGateReasonCode.SNAPSHOT_NOT_FULLY_RESOLVED, 1)
    add(
        TaskGateReasonCode.INCONCLUSIVE_UNITS,
        snapshot.verdict_counts.inconclusive,
    )
    add(
        TaskGateReasonCode.NOT_EVALUATED_UNITS,
        snapshot.verdict_counts.not_evaluated,
    )
    add(
        TaskGateReasonCode.DATA_HYGIENE_UNRESOLVED,
        snapshot.axis_distributions.data_hygiene.pending
        + snapshot.axis_distributions.data_hygiene.cleanup_failed,
    )
    add(
        TaskGateReasonCode.EVIDENCE_INCOMPLETE,
        snapshot.axis_distributions.evidence_completeness.pending
        + snapshot.axis_distributions.evidence_completeness.partial
        + snapshot.axis_distributions.evidence_completeness.missing,
    )
    add(
        TaskGateReasonCode.EVIDENCE_INVALID_OR_UNVERIFIED,
        snapshot.axis_distributions.evidence_integrity.unverified
        + snapshot.axis_distributions.evidence_integrity.invalid,
    )
    add(
        TaskGateReasonCode.MANUAL_INFLUENCE,
        snapshot.axis_distributions.execution_influence.manual_assisted
        + snapshot.axis_distributions.execution_influence.manual_only,
    )
    add(
        TaskGateReasonCode.UNSTABLE_EXECUTION,
        snapshot.axis_distributions.stability.unknown
        + snapshot.axis_distributions.stability.infra_recovered
        + snapshot.axis_distributions.stability.flaky_suspect
        + snapshot.axis_distributions.stability.flaky_confirmed,
    )
    add(
        TaskGateReasonCode.CLASSIFICATION_NOT_GATE_READY,
        sum(not task_gate_classification_is_ready(item) for item in classifications),
    )
    add(TaskGateReasonCode.FAILED_UNITS, snapshot.verdict_counts.failed)
    add(
        TaskGateReasonCode.DATA_LEAK,
        snapshot.axis_distributions.data_hygiene.leaked,
    )

    inconclusive_codes = {
        TaskGateReasonCode.CLASSIFICATION_NOT_GATE_READY,
        TaskGateReasonCode.DATA_HYGIENE_UNRESOLVED,
        TaskGateReasonCode.EVIDENCE_INCOMPLETE,
        TaskGateReasonCode.EVIDENCE_INVALID_OR_UNVERIFIED,
        TaskGateReasonCode.INCONCLUSIVE_UNITS,
        TaskGateReasonCode.MANUAL_INFLUENCE,
        TaskGateReasonCode.NOT_EVALUATED_UNITS,
        TaskGateReasonCode.SNAPSHOT_NOT_FULLY_RESOLVED,
        TaskGateReasonCode.UNSTABLE_EXECUTION,
    }
    if inconclusive_codes.intersection(reasons):
        verdict = TaskGateVerdict.INCONCLUSIVE
    elif {
        TaskGateReasonCode.FAILED_UNITS,
        TaskGateReasonCode.DATA_LEAK,
    }.intersection(reasons):
        verdict = TaskGateVerdict.REJECTED
    else:
        verdict = TaskGateVerdict.ACCEPTED
    return (
        verdict,
        tuple(
            TaskGateReason(code=code, count=count)
            for code, count in sorted(reasons.items(), key=lambda item: item[0].value)
        ),
    )


TASK_GATE_POLICY_DIGEST = task_gate_digest(
    {
        "schemaVersion": "atlas.task-gate-policy/0.1",
        "policyVersion": TASK_GATE_POLICY_VERSION,
        "snapshotBinding": "EXACT_IMMUTABLE_SNAPSHOT",
        "classificationBinding": "LATEST_COMPLETE_CLUSTER_SET",
        "classificationReadiness": {
            "humanJudgments": ["HUMAN_CONFIRMED", "HUMAN_REVISED"],
            "humanConfidenceMinimum": TASK_GATE_HUMAN_CONFIDENCE_MINIMUM,
            "ruleDomains": ["CLEANUP", "EVIDENCE", "POLICY_SECURITY"],
            "ruleConfidenceMinimum": TASK_GATE_RULE_CONFIDENCE_MINIMUM,
            "unknown": "INCONCLUSIVE",
            "evidenceGaps": "INCONCLUSIVE",
        },
        "acceptedRequires": [
            "FULLY_RESOLVED_OR_REEVALUATED",
            "ALL_UNITS_PASSED",
            "TERMINAL_HYGIENE_WITHOUT_LEAK",
            "COMPLETE_VERIFIED_EVIDENCE",
            "AUTONOMOUS_EXECUTION",
            "STABLE_EXECUTION",
        ],
        "rejectedBy": ["FAILED_UNIT", "DATA_LEAK"],
        "uncertainty": "INCONCLUSIVE",
        "historyMutation": "FORBIDDEN",
    }
)


def task_gate_classification_inputs(
    pairs: tuple[
        tuple[FailureClusterRevision, FailureClassificationRevision],
        ...,
    ],
) -> tuple[TaskGateClassificationInput, ...]:
    """Convert repository pairs into the exact canonical Gate hash inputs."""

    return tuple(
        TaskGateClassificationInput(
            failure_cluster_revision_id=cluster.id,
            cluster_hash=cluster.cluster_hash,
            failure_classification_revision_id=classification.id,
            classification_revision=classification.revision,
            classification_hash=classification.classification_hash,
        )
        for cluster, classification in pairs
    )


__all__ = [
    "TASK_GATE_CLASSIFICATION_SET_SCHEMA_VERSION",
    "TASK_GATE_DECISION_SCHEMA_VERSION",
    "TASK_GATE_HUMAN_CONFIDENCE_MINIMUM",
    "TASK_GATE_POLICY_DIGEST",
    "TASK_GATE_POLICY_VERSION",
    "TASK_GATE_RULE_CONFIDENCE_MINIMUM",
    "RequestTaskGateEvaluation",
    "TaskGateClassificationInput",
    "TaskGateDecision",
    "TaskGateDecisionContent",
    "TaskGateReason",
    "TaskGateReasonCode",
    "TaskGateVerdict",
    "evaluate_task_gate",
    "task_gate_classification_inputs",
    "task_gate_classification_is_ready",
    "task_gate_classification_set_hash",
    "task_gate_decision_document",
    "task_gate_decision_hash",
    "task_gate_digest",
]
