"""Snapshot-bound failure clustering and append-only classification contracts."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import DIGEST_PATTERN
from atlas_testops.domain.result.hygiene import UnitHygieneResolutionRevision
from atlas_testops.domain.result.models import (
    SAFE_REASON_PATTERN,
    DataHygiene,
    EvidenceCompleteness,
    EvidenceIntegrity,
    OutcomeClass,
    Stability,
    Verdict,
)
from atlas_testops.domain.result.projections import UnitResolutionRevision

FAILURE_CLUSTER_REVISION_SCHEMA_VERSION: Literal["atlas.failure-cluster-revision/0.1"] = (
    "atlas.failure-cluster-revision/0.1"
)
FAILURE_CLASSIFICATION_REVISION_SCHEMA_VERSION: Literal[
    "atlas.failure-classification-revision/0.1"
] = "atlas.failure-classification-revision/0.1"
FAILURE_SIGNAL_SCHEMA_VERSION: Literal["atlas.failure-signal/0.1"] = (
    "atlas.failure-signal/0.1"
)
FAILURE_CLUSTER_POLICY_VERSION: Literal["0.1.0"] = "0.1.0"
FAILURE_CLASSIFICATION_POLICY_VERSION: Literal["0.1.0"] = "0.1.0"
FAILURE_FINGERPRINT_VERSION: Literal["0.1.0"] = "0.1.0"
CLASSIFICATION_MUTATION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
SAFE_HYPOTHESIS_PATTERN = r"^[^\x00-\x1f\x7f]{1,500}$"

NonNegativeCount = Annotated[int, Field(ge=0)]


class FailureDomain(StrEnum):
    """Stable bounded taxonomy used for triage and later Gate policy."""

    PRODUCT = "PRODUCT"
    TEST_SPEC = "TEST_SPEC"
    TEST_DATA = "TEST_DATA"
    IDENTITY = "IDENTITY"
    ENVIRONMENT = "ENVIRONMENT"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    EXTERNAL_DEPENDENCY = "EXTERNAL_DEPENDENCY"
    AGENT_AUTOMATION = "AGENT_AUTOMATION"
    POLICY_SECURITY = "POLICY_SECURITY"
    EVIDENCE = "EVIDENCE"
    CLEANUP = "CLEANUP"
    UNKNOWN = "UNKNOWN"


class ClassificationJudgmentState(StrEnum):
    """How one immutable classification judgment was produced or reviewed."""

    RULE_PROPOSED = "RULE_PROPOSED"
    AI_PROPOSED = "AI_PROPOSED"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    HUMAN_REVISED = "HUMAN_REVISED"


class ClassificationAuthorKind(StrEnum):
    """Bounded author authority without persisting hidden model reasoning."""

    SYSTEM_RULE = "SYSTEM_RULE"
    AI_MODEL = "AI_MODEL"
    HUMAN = "HUMAN"


class FailureEvidenceKind(StrEnum):
    """Immutable Result facts accepted as classification evidence."""

    UNIT_RESOLUTION = "UNIT_RESOLUTION"
    UNIT_HYGIENE_RESOLUTION = "UNIT_HYGIENE_RESOLUTION"
    ATTEMPT_SEAL = "ATTEMPT_SEAL"
    ATTEMPT_CLOSURE_NOTICE = "ATTEMPT_CLOSURE_NOTICE"


class ClassificationConfidence(FrozenWireModel):
    """Exact confidence fraction with a fixed basis-point denominator."""

    numerator: int = Field(ge=0, le=10_000)
    denominator: Literal[10_000] = 10_000


class FailureEvidenceRef(FrozenWireModel):
    """Typed immutable source reference used to support or contradict a judgment."""

    kind: FailureEvidenceKind
    ref_id: UUID
    content_digest: str = Field(pattern=DIGEST_PATTERN)

    def sort_key(self) -> tuple[str, str, str]:
        """Return the only canonical ordering for evidence references."""

        return (self.kind.value, str(self.ref_id), self.content_digest)


class FailureSignal(FrozenWireModel):
    """Deterministic bounded signal used by the first clustering policy."""

    schema_version: Literal["atlas.failure-signal/0.1"] = FAILURE_SIGNAL_SCHEMA_VERSION
    failure_domain: FailureDomain
    signal_code: str = Field(pattern=SAFE_REASON_PATTERN)
    effective_verdict: Verdict
    outcome_class: OutcomeClass
    closure_reason: str = Field(pattern=SAFE_REASON_PATTERN)
    data_hygiene: DataHygiene
    evidence_completeness: EvidenceCompleteness
    evidence_integrity: EvidenceIntegrity
    stability: Stability


class FailureClusterRevisionContent(FrozenWireModel):
    """One immutable snapshot-bound deterministic failure cluster."""

    schema_version: Literal["atlas.failure-cluster-revision/0.1"] = (
        FAILURE_CLUSTER_REVISION_SCHEMA_VERSION
    )
    id: UUID
    failure_cluster_id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    result_snapshot_id: UUID
    revision: int = Field(ge=1)
    fingerprint_version: Literal["0.1.0"] = FAILURE_FINGERPRINT_VERSION
    fingerprint_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    fingerprint: str = Field(pattern=DIGEST_PATTERN)
    signal: FailureSignal
    affected_unit_resolution_revision_ids: tuple[UUID, ...] = Field(
        min_length=1,
        max_length=10_000,
    )
    affected_count: int = Field(ge=1, le=10_000)
    representative_unit_resolution_revision_id: UUID
    supersedes_cluster_revision_id: UUID | None = None
    projection_watermark: AwareDatetime
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_cluster_content(self) -> Self:
        """Enforce deterministic inputs, fingerprint, lineage, and watermark."""

        if len(set(self.affected_unit_resolution_revision_ids)) != len(
            self.affected_unit_resolution_revision_ids
        ):
            raise ValueError("affected UnitResolution revision IDs must be unique")
        if self.affected_count != len(self.affected_unit_resolution_revision_ids):
            raise ValueError("affectedCount must match the exact affected revision set")
        if (
            self.representative_unit_resolution_revision_id
            not in self.affected_unit_resolution_revision_ids
        ):
            raise ValueError("representative UnitResolution must belong to the cluster")
        if self.fingerprint_policy_digest != FAILURE_CLUSTER_POLICY_DIGEST:
            raise ValueError("fingerprintPolicyDigest must match the frozen Cluster Policy")
        if self.fingerprint != failure_cluster_fingerprint(self.signal):
            raise ValueError("fingerprint must match the exact deterministic FailureSignal")
        if self.revision == 1 and self.supersedes_cluster_revision_id is not None:
            raise ValueError("first FailureCluster revision cannot supersede another")
        if self.revision > 1 and self.supersedes_cluster_revision_id is None:
            raise ValueError("later FailureCluster revision requires its predecessor")
        if self.created_at < self.projection_watermark:
            raise ValueError("FailureCluster createdAt cannot predate its source watermark")
        return self


class FailureClusterRevision(FailureClusterRevisionContent):
    """Hashed immutable cluster projection over exact Snapshot inputs."""

    cluster_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_cluster_hash(self) -> Self:
        """Reject a cluster whose persisted semantic projection was altered."""

        if self.cluster_hash != failure_cluster_revision_hash(self):
            raise ValueError("clusterHash must match FailureCluster semantic content")
        return self


class FailureClassificationRevisionContent(FrozenWireModel):
    """One append-only auditable judgment over an exact FailureCluster revision."""

    schema_version: Literal["atlas.failure-classification-revision/0.1"] = (
        FAILURE_CLASSIFICATION_REVISION_SCHEMA_VERSION
    )
    id: UUID
    failure_classification_id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    result_snapshot_id: UUID
    failure_cluster_revision_id: UUID
    revision: int = Field(ge=1)
    failure_domain: FailureDomain
    hypothesis_code: str = Field(pattern=SAFE_REASON_PATTERN)
    hypothesis: str = Field(
        min_length=1,
        max_length=500,
        pattern=SAFE_HYPOTHESIS_PATTERN,
    )
    confidence: ClassificationConfidence
    supporting_evidence_refs: tuple[FailureEvidenceRef, ...] = Field(
        min_length=1,
        max_length=256,
    )
    contradicting_evidence_refs: tuple[FailureEvidenceRef, ...] = Field(
        default=(),
        max_length=256,
    )
    evidence_gap_codes: tuple[str, ...] = Field(default=(), max_length=32)
    judgment_state: ClassificationJudgmentState
    author_kind: ClassificationAuthorKind
    authored_by: UUID | None = None
    model_version_ref: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$",
    )
    classification_policy_version: Literal["0.1.0"] = (
        FAILURE_CLASSIFICATION_POLICY_VERSION
    )
    classification_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=CLASSIFICATION_MUTATION_PATTERN,
    )
    supersedes_revision_id: UUID | None = None
    created_at: AwareDatetime

    @field_validator("supporting_evidence_refs", "contradicting_evidence_refs")
    @classmethod
    def require_canonical_evidence(
        cls,
        values: tuple[FailureEvidenceRef, ...],
    ) -> tuple[FailureEvidenceRef, ...]:
        """Require evidence references to be unique and canonically ordered."""

        keys = tuple(item.sort_key() for item in values)
        if len(keys) != len(set(keys)):
            raise ValueError("classification evidence references must be unique")
        if keys != tuple(sorted(keys)):
            raise ValueError("classification evidence references must be canonically ordered")
        return values

    @field_validator("evidence_gap_codes")
    @classmethod
    def require_canonical_gap_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Keep evidence gap codes safe, unique, and stable for hashing."""

        if any(
            len(value) < 2
            or len(value) > 96
            or value != value.upper()
            or not value.replace("_", "").isalnum()
            for value in values
        ):
            raise ValueError("evidenceGapCodes must use bounded uppercase codes")
        if len(values) != len(set(values)) or values != tuple(sorted(values)):
            raise ValueError("evidenceGapCodes must be unique and sorted")
        return values

    @model_validator(mode="after")
    def validate_classification_content(self) -> Self:
        """Enforce author authority, evidence separation, policy, and lineage."""

        supporting = {item.sort_key() for item in self.supporting_evidence_refs}
        contradicting = {item.sort_key() for item in self.contradicting_evidence_refs}
        if supporting.intersection(contradicting):
            raise ValueError("supporting and contradicting evidence cannot overlap")
        if self.classification_policy_digest != FAILURE_CLASSIFICATION_POLICY_DIGEST:
            raise ValueError("classificationPolicyDigest must match the frozen Policy")
        if self.revision == 1 and self.supersedes_revision_id is not None:
            raise ValueError("first Classification revision cannot supersede another")
        if self.revision > 1 and self.supersedes_revision_id is None:
            raise ValueError("later Classification revision requires its predecessor")

        if self.author_kind is ClassificationAuthorKind.SYSTEM_RULE:
            if (
                self.judgment_state is not ClassificationJudgmentState.RULE_PROPOSED
                or self.authored_by is not None
                or self.model_version_ref is not None
            ):
                raise ValueError("SYSTEM_RULE requires an unauthored RULE_PROPOSED judgment")
        elif self.author_kind is ClassificationAuthorKind.AI_MODEL:
            if (
                self.judgment_state is not ClassificationJudgmentState.AI_PROPOSED
                or self.authored_by is not None
                or self.model_version_ref is None
            ):
                raise ValueError("AI_MODEL requires an AI_PROPOSED judgment and modelVersionRef")
        elif (
            self.judgment_state
            not in {
                ClassificationJudgmentState.HUMAN_CONFIRMED,
                ClassificationJudgmentState.HUMAN_REJECTED,
                ClassificationJudgmentState.HUMAN_REVISED,
            }
            or self.authored_by is None
            or self.model_version_ref is not None
        ):
            raise ValueError("HUMAN judgment requires an actor and no modelVersionRef")

        if self.judgment_state is ClassificationJudgmentState.HUMAN_REJECTED and (
            self.failure_domain is not FailureDomain.UNKNOWN
            or self.confidence.numerator != 0
            or not self.contradicting_evidence_refs
        ):
            raise ValueError(
                "HUMAN_REJECTED requires UNKNOWN, zero confidence, and contradiction evidence"
            )
        return self


class FailureClassificationRevision(FailureClassificationRevisionContent):
    """Hashed immutable classification judgment."""

    classification_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_classification_hash(self) -> Self:
        """Reject a classification whose semantic content was altered."""

        if self.classification_hash != failure_classification_revision_hash(self):
            raise ValueError(
                "classificationHash must match FailureClassification semantic content"
            )
        return self


class RequestFailureClassificationRevision(FrozenWireModel):
    """Idempotent human review request over one exact Classification revision."""

    expected_revision: int = Field(ge=1)
    failure_domain: FailureDomain
    hypothesis_code: str = Field(pattern=SAFE_REASON_PATTERN)
    hypothesis: str = Field(
        min_length=1,
        max_length=500,
        pattern=SAFE_HYPOTHESIS_PATTERN,
    )
    confidence: ClassificationConfidence
    supporting_evidence_refs: tuple[FailureEvidenceRef, ...] = Field(
        min_length=1,
        max_length=256,
    )
    contradicting_evidence_refs: tuple[FailureEvidenceRef, ...] = Field(
        default=(),
        max_length=256,
    )
    evidence_gap_codes: tuple[str, ...] = Field(default=(), max_length=32)
    judgment_state: Literal[
        ClassificationJudgmentState.HUMAN_CONFIRMED,
        ClassificationJudgmentState.HUMAN_REJECTED,
        ClassificationJudgmentState.HUMAN_REVISED,
    ]
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=CLASSIFICATION_MUTATION_PATTERN,
    )

    @field_validator("supporting_evidence_refs", "contradicting_evidence_refs")
    @classmethod
    def require_canonical_evidence(
        cls,
        values: tuple[FailureEvidenceRef, ...],
    ) -> tuple[FailureEvidenceRef, ...]:
        """Reject non-canonical review evidence before application processing."""

        keys = tuple(item.sort_key() for item in values)
        if len(keys) != len(set(keys)):
            raise ValueError("classification evidence references must be unique")
        if keys != tuple(sorted(keys)):
            raise ValueError("classification evidence references must be canonically ordered")
        return values

    @field_validator("evidence_gap_codes")
    @classmethod
    def require_canonical_gap_codes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject unsafe or unstable review gap codes at the request boundary."""

        if any(
            len(value) < 2
            or len(value) > 96
            or value != value.upper()
            or not value.replace("_", "").isalnum()
            for value in values
        ):
            raise ValueError("evidenceGapCodes must use bounded uppercase codes")
        if len(values) != len(set(values)) or values != tuple(sorted(values)):
            raise ValueError("evidenceGapCodes must be unique and sorted")
        return values

    @model_validator(mode="after")
    def validate_review_judgment(self) -> Self:
        """Keep evidence sets disjoint and rejected judgments conservative."""

        supporting = {item.sort_key() for item in self.supporting_evidence_refs}
        contradicting = {item.sort_key() for item in self.contradicting_evidence_refs}
        if supporting.intersection(contradicting):
            raise ValueError("supporting and contradicting evidence cannot overlap")
        if self.judgment_state is ClassificationJudgmentState.HUMAN_REJECTED and (
            self.failure_domain is not FailureDomain.UNKNOWN
            or self.confidence.numerator != 0
            or not self.contradicting_evidence_refs
        ):
            raise ValueError(
                "HUMAN_REJECTED requires UNKNOWN, zero confidence, and contradiction evidence"
            )
        return self


def result_classification_digest(value: dict[str, JsonValue]) -> str:
    """Hash one deterministic classification document."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def failure_cluster_fingerprint(signal: FailureSignal) -> str:
    """Hash only the stable normalized signal, independent of Snapshot identity."""

    return result_classification_digest(
        cast(
            dict[str, JsonValue],
            signal.model_dump(mode="json", by_alias=True),
        )
    )


def failure_cluster_revision_document(
    value: FailureClusterRevisionContent | FailureClusterRevision,
) -> dict[str, JsonValue]:
    """Return the exact persisted FailureCluster revision document."""

    return cast(
        dict[str, JsonValue],
        value.model_dump(mode="json", by_alias=True),
    )


def failure_cluster_revision_hash(
    value: FailureClusterRevisionContent | FailureClusterRevision,
) -> str:
    """Hash reproducible cluster semantics while excluding lineage identity."""

    body = failure_cluster_revision_document(value)
    for field in (
        "id",
        "failureClusterId",
        "revision",
        "supersedesClusterRevisionId",
        "createdAt",
        "clusterHash",
    ):
        body.pop(field, None)
    return result_classification_digest(body)


def failure_classification_revision_document(
    value: FailureClassificationRevisionContent | FailureClassificationRevision,
) -> dict[str, JsonValue]:
    """Return the exact persisted FailureClassification revision document."""

    return cast(
        dict[str, JsonValue],
        value.model_dump(mode="json", by_alias=True),
    )


def failure_classification_revision_hash(
    value: FailureClassificationRevisionContent | FailureClassificationRevision,
) -> str:
    """Hash the complete judgment while excluding revision storage identity."""

    body = failure_classification_revision_document(value)
    for field in (
        "id",
        "failureClassificationId",
        "revision",
        "supersedesRevisionId",
        "createdAt",
        "classificationHash",
    ):
        body.pop(field, None)
    return result_classification_digest(body)


def is_diagnostic_failure(
    resolution: UnitResolutionRevision,
    hygiene: UnitHygieneResolutionRevision | None,
) -> bool:
    """Return whether one exact Snapshot input needs failure triage."""

    effective_hygiene = hygiene.data_hygiene if hygiene is not None else resolution.data_hygiene
    return (
        resolution.effective_verdict is not Verdict.PASSED
        or resolution.stability is not Stability.STABLE
        or effective_hygiene in {DataHygiene.CLEANUP_FAILED, DataHygiene.LEAKED}
        or resolution.evidence_integrity is not EvidenceIntegrity.VERIFIED
        or resolution.evidence_completeness
        in {EvidenceCompleteness.MISSING, EvidenceCompleteness.PARTIAL}
    )


def failure_signal_for(
    resolution: UnitResolutionRevision,
    hygiene: UnitHygieneResolutionRevision | None,
) -> FailureSignal:
    """Derive the conservative first failure taxonomy from immutable Result axes."""

    effective_hygiene = hygiene.data_hygiene if hygiene is not None else resolution.data_hygiene
    domain: FailureDomain
    signal_code: str
    if effective_hygiene is DataHygiene.LEAKED:
        domain, signal_code = FailureDomain.CLEANUP, "CLEANUP_LEAKED"
    elif effective_hygiene is DataHygiene.CLEANUP_FAILED:
        domain, signal_code = FailureDomain.CLEANUP, "CLEANUP_FAILED"
    elif resolution.evidence_integrity is EvidenceIntegrity.INVALID:
        domain, signal_code = FailureDomain.EVIDENCE, "EVIDENCE_INTEGRITY_INVALID"
    elif resolution.evidence_completeness is EvidenceCompleteness.MISSING:
        domain, signal_code = FailureDomain.EVIDENCE, "EVIDENCE_REQUIRED_MISSING"
    elif resolution.evidence_completeness is EvidenceCompleteness.PARTIAL:
        domain, signal_code = FailureDomain.EVIDENCE, "EVIDENCE_REQUIRED_PARTIAL"
    elif resolution.evidence_integrity is EvidenceIntegrity.UNVERIFIED:
        domain, signal_code = FailureDomain.EVIDENCE, "EVIDENCE_INTEGRITY_UNVERIFIED"
    elif resolution.outcome_class is OutcomeClass.POLICY:
        domain, signal_code = FailureDomain.POLICY_SECURITY, "POLICY_REJECTED"
    elif resolution.outcome_class is OutcomeClass.DEPENDENCY:
        domain, signal_code = FailureDomain.EXTERNAL_DEPENDENCY, "DEPENDENCY_FAILURE"
    elif (
        resolution.outcome_class is OutcomeClass.PLATFORM
        or resolution.stability is Stability.INFRA_RECOVERED
    ):
        domain, signal_code = FailureDomain.INFRASTRUCTURE, "INFRASTRUCTURE_FAILURE"
    elif resolution.stability in {Stability.FLAKY_SUSPECT, Stability.FLAKY_CONFIRMED}:
        domain, signal_code = FailureDomain.UNKNOWN, "FLAKY_SIGNAL"
    elif resolution.outcome_class is OutcomeClass.AUTOMATION:
        domain, signal_code = FailureDomain.UNKNOWN, "AUTOMATION_CAUSE_UNRESOLVED"
    elif resolution.outcome_class is OutcomeClass.BUSINESS:
        domain, signal_code = FailureDomain.UNKNOWN, "PRODUCT_OR_SPEC_UNRESOLVED"
    elif resolution.outcome_class is OutcomeClass.USER:
        domain, signal_code = FailureDomain.UNKNOWN, "USER_OUTCOME_UNRESOLVED"
    else:
        domain, signal_code = FailureDomain.UNKNOWN, "CAUSE_UNKNOWN"
    return FailureSignal(
        failure_domain=domain,
        signal_code=signal_code,
        effective_verdict=resolution.effective_verdict,
        outcome_class=resolution.outcome_class,
        closure_reason=resolution.closure_reason,
        data_hygiene=effective_hygiene,
        evidence_completeness=resolution.evidence_completeness,
        evidence_integrity=resolution.evidence_integrity,
        stability=resolution.stability,
    )


def rule_classification_for_signal(
    signal: FailureSignal,
) -> tuple[str, str, ClassificationConfidence, tuple[str, ...]]:
    """Return the frozen conservative hypothesis, confidence, and evidence gaps."""

    hypotheses: dict[str, tuple[str, int, tuple[str, ...]]] = {
        "CLEANUP_LEAKED": (
            "Cleanup truth contains an explicitly leaked resource.",
            10_000,
            (),
        ),
        "CLEANUP_FAILED": (
            "Cleanup truth did not reach a terminal successful state.",
            10_000,
            (),
        ),
        "EVIDENCE_INTEGRITY_INVALID": (
            "Trusted result evidence failed integrity verification.",
            10_000,
            (),
        ),
        "EVIDENCE_REQUIRED_MISSING": (
            "Required evidence is missing from the trusted result.",
            10_000,
            (),
        ),
        "EVIDENCE_REQUIRED_PARTIAL": (
            "Required result evidence is only partially complete.",
            10_000,
            (),
        ),
        "EVIDENCE_INTEGRITY_UNVERIFIED": (
            "Result evidence has not completed integrity verification.",
            10_000,
            (),
        ),
        "POLICY_REJECTED": (
            "A frozen runtime or security policy rejected the execution.",
            9_500,
            (),
        ),
        "DEPENDENCY_FAILURE": (
            "The trusted result attributes the outcome to an external dependency.",
            9_000,
            ("DEPENDENCY_DETAIL_MISSING",),
        ),
        "INFRASTRUCTURE_FAILURE": (
            "The trusted result indicates an infrastructure failure or recovery.",
            9_000,
            ("INFRASTRUCTURE_COMPONENT_MISSING",),
        ),
        "FLAKY_SIGNAL": (
            "Comparable attempts produced an unstable result sequence.",
            7_500,
            ("ROOT_CAUSE_EVIDENCE_MISSING",),
        ),
        "AUTOMATION_CAUSE_UNRESOLVED": (
            "Automation-related execution failed without enough evidence for a narrower cause.",
            2_500,
            ("AUTOMATION_DETAIL_MISSING",),
        ),
        "PRODUCT_OR_SPEC_UNRESOLVED": (
            "The business assertion failed, but product and test-spec causes remain unresolved.",
            2_500,
            ("PRODUCT_VS_TEST_SPEC_UNRESOLVED",),
        ),
        "USER_OUTCOME_UNRESOLVED": (
            "A user-originated outcome prevented a conclusive failure attribution.",
            2_500,
            ("USER_OUTCOME_DETAIL_MISSING",),
        ),
        "CAUSE_UNKNOWN": (
            "Available trusted facts are insufficient for a narrower failure attribution.",
            0,
            ("ROOT_CAUSE_EVIDENCE_MISSING",),
        ),
    }
    hypothesis, numerator, gaps = hypotheses[signal.signal_code]
    return (
        signal.signal_code,
        hypothesis,
        ClassificationConfidence(numerator=numerator),
        tuple(sorted(gaps)),
    )


FAILURE_CLUSTER_POLICY_DIGEST = result_classification_digest(
    {
        "schemaVersion": "atlas.failure-cluster-policy/0.1",
        "policyVersion": FAILURE_CLUSTER_POLICY_VERSION,
        "snapshotBinding": "EXACT_RESULT_SNAPSHOT_ID",
        "eligibility": [
            "NON_PASSED",
            "NON_STABLE",
            "CLEANUP_FAILED_OR_LEAKED",
            "EVIDENCE_NOT_VERIFIED_OR_INCOMPLETE",
        ],
        "fingerprint": "EXACT_NORMALIZED_FAILURE_SIGNAL",
        "representative": "FIRST_MANIFEST_ORDINAL",
        "semanticMerge": "DISABLED",
    }
)

FAILURE_CLASSIFICATION_POLICY_DIGEST = result_classification_digest(
    {
        "schemaVersion": "atlas.failure-classification-policy/0.1",
        "policyVersion": FAILURE_CLASSIFICATION_POLICY_VERSION,
        "ruleOrder": [
            "CLEANUP",
            "EVIDENCE",
            "POLICY_SECURITY",
            "EXTERNAL_DEPENDENCY",
            "INFRASTRUCTURE",
            "FLAKY_SIGNAL",
            "UNKNOWN",
        ],
        "lowEvidence": "UNKNOWN",
        "supportingEvidence": "AT_LEAST_ONE_IMMUTABLE_RESULT_FACT",
        "verdictMutation": "FORBIDDEN",
        "hiddenReasoningPersistence": "FORBIDDEN",
    }
)


__all__ = [
    "FAILURE_CLASSIFICATION_POLICY_DIGEST",
    "FAILURE_CLASSIFICATION_POLICY_VERSION",
    "FAILURE_CLASSIFICATION_REVISION_SCHEMA_VERSION",
    "FAILURE_CLUSTER_POLICY_DIGEST",
    "FAILURE_CLUSTER_POLICY_VERSION",
    "FAILURE_CLUSTER_REVISION_SCHEMA_VERSION",
    "FAILURE_FINGERPRINT_VERSION",
    "ClassificationAuthorKind",
    "ClassificationConfidence",
    "ClassificationJudgmentState",
    "FailureClassificationRevision",
    "FailureClassificationRevisionContent",
    "FailureClusterRevision",
    "FailureClusterRevisionContent",
    "FailureDomain",
    "FailureEvidenceKind",
    "FailureEvidenceRef",
    "FailureSignal",
    "RequestFailureClassificationRevision",
    "failure_classification_revision_document",
    "failure_classification_revision_hash",
    "failure_cluster_fingerprint",
    "failure_cluster_revision_document",
    "failure_cluster_revision_hash",
    "failure_signal_for",
    "is_diagnostic_failure",
    "result_classification_digest",
    "rule_classification_for_signal",
]
