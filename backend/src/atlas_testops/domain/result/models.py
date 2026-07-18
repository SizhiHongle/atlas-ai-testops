"""Immutable Result truth contracts rooted in one formal UnitAttempt."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal, Self, cast
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import DIGEST_PATTERN

ATTEMPT_SEAL_SCHEMA_VERSION: Literal["attempt-seal/1.0"] = "attempt-seal/1.0"
RESULT_REF_SCHEMA_VERSION: Literal["atlas.result-ref/0.1"] = "atlas.result-ref/0.1"
SIGNATURE_VALUE_PATTERN = r"^base64url:[A-Za-z0-9_-]{86}$"
SAFE_REASON_PATTERN = r"^[A-Z][A-Z0-9_]{1,95}$"
KEY_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$"


class Verdict(StrEnum):
    """Minimal business judgment axis used by Result Center."""

    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUATED = "NOT_EVALUATED"


class OutcomeClass(StrEnum):
    """Stable high-level cause family independent of Verdict."""

    BUSINESS = "BUSINESS"
    DEPENDENCY = "DEPENDENCY"
    PLATFORM = "PLATFORM"
    USER = "USER"
    AUTOMATION = "AUTOMATION"
    POLICY = "POLICY"
    UNKNOWN = "UNKNOWN"


class ResultLifecycle(StrEnum):
    """Result lifecycle axis for one accepted AttemptSeal."""

    SEALED = "SEALED"


class DataHygiene(StrEnum):
    """Side-effect cleanup state captured when the Attempt is sealed."""

    PENDING = "PENDING"
    CLEANED = "CLEANED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    LEAKED = "LEAKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvidenceCompleteness(StrEnum):
    """Required-evidence coverage axis."""

    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvidenceIntegrity(StrEnum):
    """Independent evidence verification axis."""

    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    INVALID = "INVALID"


class ExecutionInfluence(StrEnum):
    """Who influenced the execution actions."""

    AUTONOMOUS = "AUTONOMOUS"
    MANUAL_ASSISTED = "MANUAL_ASSISTED"
    MANUAL_ONLY = "MANUAL_ONLY"


class Stability(StrEnum):
    """Attempt-level placeholder for the later Unit resolution axis."""

    UNKNOWN = "UNKNOWN"
    STABLE = "STABLE"
    INFRA_RECOVERED = "INFRA_RECOVERED"
    FLAKY_SUSPECT = "FLAKY_SUSPECT"
    FLAKY_CONFIRMED = "FLAKY_CONFIRMED"


class AttemptEventChain(FrozenWireModel):
    """Exact terminal head and count of the trusted runtime event chain."""

    head: str = Field(pattern=DIGEST_PATTERN)
    event_count: int = Field(ge=1, le=10_000_000)


class AttemptSealSignature(FrozenWireModel):
    """Frozen signing metadata carried by an AttemptSeal."""

    alg: Literal["EdDSA"] = "EdDSA"
    kid: str = Field(pattern=KEY_ID_PATTERN)
    jcs: Literal[True] = True


class AttemptSealContent(FrozenWireModel):
    """Unsigned canonical content that a trusted runtime signs."""

    schema_version: Literal["attempt-seal/1.0"] = ATTEMPT_SEAL_SCHEMA_VERSION
    seal_id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    manifest_id: UUID
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    unit_key: str = Field(pattern=DIGEST_PATTERN)
    execution_ticket_id: UUID
    execution_ticket_digest: str = Field(pattern=DIGEST_PATTERN)
    oracle_verdict: Verdict
    outcome_class: OutcomeClass
    closure_reason: str = Field(pattern=SAFE_REASON_PATTERN)
    lifecycle: Literal[ResultLifecycle.SEALED] = ResultLifecycle.SEALED
    data_hygiene: DataHygiene
    evidence_completeness: EvidenceCompleteness
    evidence_integrity: EvidenceIntegrity
    execution_influence: ExecutionInfluence
    stability: Stability = Stability.UNKNOWN
    oracle_results_hash: str = Field(pattern=DIGEST_PATTERN)
    artifact_manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    event_chain: AttemptEventChain
    evidence_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    runtime_digest: str = Field(pattern=DIGEST_PATTERN)
    sealed_at: AwareDatetime
    signature: AttemptSealSignature

    @model_validator(mode="after")
    def validate_terminal_truth(self) -> Self:
        """Reject provisional or falsely trusted terminal truth."""

        if self.manifest_id != self.task_run_id:
            raise ValueError("manifestId must equal the TaskRun-backed manifest identity")
        if self.oracle_verdict is Verdict.PENDING:
            raise ValueError("AttemptSeal cannot contain a provisional PENDING Verdict")
        if self.oracle_verdict is Verdict.PASSED and (
            self.evidence_completeness is not EvidenceCompleteness.COMPLETE
            or self.evidence_integrity is not EvidenceIntegrity.VERIFIED
        ):
            raise ValueError("PASSED AttemptSeal requires complete and verified evidence")
        if (
            self.oracle_verdict is Verdict.NOT_EVALUATED
            and self.evidence_completeness is EvidenceCompleteness.COMPLETE
        ):
            raise ValueError("NOT_EVALUATED AttemptSeal cannot claim complete evidence")
        return self


class AttemptSeal(AttemptSealContent):
    """Signed immutable fact accepted as the only Result Center input."""

    signature_value: str = Field(pattern=SIGNATURE_VALUE_PATTERN, repr=False)
    content_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_content_hash(self) -> Self:
        """Require the stored digest to match the exact unsigned JCS content."""

        if self.content_hash != attempt_seal_content_hash(self):
            raise ValueError("contentHash must match the unsigned AttemptSeal content")
        return self


class ResultRef(FrozenWireModel):
    """Opaque idempotent reference returned after one Seal is accepted."""

    schema_version: Literal["atlas.result-ref/0.1"] = RESULT_REF_SCHEMA_VERSION
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    seal_id: UUID
    seal_content_hash: str = Field(pattern=DIGEST_PATTERN)
    created_at: AwareDatetime


class ResultIntegrityIncident(FrozenWireModel):
    """Append-only record of conflicting Seal content for one UnitAttempt."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    accepted_seal_id: UUID
    accepted_content_hash: str = Field(pattern=DIGEST_PATTERN)
    conflicting_seal_id: UUID
    conflicting_content_hash: str = Field(pattern=DIGEST_PATTERN)
    signature_kid: str = Field(pattern=KEY_ID_PATTERN)
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_distinct_hashes(self) -> Self:
        """Require the incident to describe genuinely different content."""

        if self.accepted_content_hash == self.conflicting_content_hash:
            raise ValueError("Result integrity incident requires different content hashes")
        return self


def attempt_seal_signing_body(
    value: AttemptSealContent | AttemptSeal,
) -> dict[str, JsonValue]:
    """Return the exact bounded JSON object covered by hash and signature."""

    return cast(
        dict[str, JsonValue],
        value.model_dump(
            mode="json",
            by_alias=True,
            exclude={"signature_value", "content_hash"},
        ),
    )


def attempt_seal_signing_bytes(value: AttemptSealContent | AttemptSeal) -> bytes:
    """Serialize the frozen ASCII-key contract using its JCS-compatible profile."""

    return json.dumps(
        attempt_seal_signing_body(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def attempt_seal_content_hash(value: AttemptSealContent | AttemptSeal) -> str:
    """Hash the exact unsigned AttemptSeal signing bytes."""

    digest = hashlib.sha256(attempt_seal_signing_bytes(value)).hexdigest()
    return f"sha256:{digest}"
