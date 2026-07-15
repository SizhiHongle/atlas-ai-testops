"""Immutable contracts for trusted browser execution and evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self, cast
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import DIGEST_PATTERN, canonical_digest
from atlas_testops.domain.workflow import ExactVersionRef, OracleStrength

EXECUTION_CONTRACT_SCHEMA_VERSION: Literal["atlas.execution-contract/0.1"] = (
    "atlas.execution-contract/0.1"
)
ASSERTION_RESULT_SCHEMA_VERSION: Literal["atlas.assertion-result/0.1"] = (
    "atlas.assertion-result/0.1"
)
EVIDENCE_MANIFEST_SCHEMA_VERSION: Literal["atlas.evidence-manifest/0.1"] = (
    "atlas.evidence-manifest/0.1"
)

REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,255}$"
WORKER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$"
BROWSER_CONTEXT_REF_PATTERN = r"^bctx_[A-Za-z0-9_-]{32,200}$"
ACCOUNT_HANDLE_PATTERN = r"^ah_[A-Za-z0-9_-]{16,128}$"
EVIDENCE_OBJECT_REF_PATTERN = r"^evidence://[A-Za-z0-9][A-Za-z0-9/_.=-]{7,511}$"


class AssertionStatus(StrEnum):
    """Deterministic Oracle decision for one frozen assertion."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class OracleOutcome(StrEnum):
    """Case-level outcome derived only from frozen assertion results."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceArtifactKind(StrEnum):
    """Bounded artifact classes accepted by the evidence service."""

    SCREENSHOT = "SCREENSHOT"
    TRACE = "TRACE"
    DOM_SUMMARY = "DOM_SUMMARY"
    ARIA_SNAPSHOT = "ARIA_SNAPSHOT"
    NETWORK_SUMMARY = "NETWORK_SUMMARY"
    CONSOLE_SUMMARY = "CONSOLE_SUMMARY"
    TOOL_INVOCATION = "TOOL_INVOCATION"


class EvidenceCompleteness(StrEnum):
    """Whether every frozen Oracle has its required evidence."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"


class EvidenceIntegrity(StrEnum):
    """Independent verification state for referenced artifact bytes."""

    VERIFIED = "VERIFIED"
    INVALID = "INVALID"


class Viewport(FrozenWireModel):
    """Frozen browser viewport used by one execution."""

    width: int = Field(ge=320, le=7680)
    height: int = Field(ge=320, le=4320)
    device_scale_factor: float = Field(default=1.0, ge=0.5, le=4.0)


class ExecutionActorBinding(FrozenWireModel):
    """Exact role, lease fence, and opaque login session for one actor slot."""

    actor_slot: str = Field(
        min_length=2,
        max_length=80,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{1,79}$",
    )
    role_id: UUID
    role_key: str = Field(
        min_length=2,
        max_length=80,
        pattern=r"^[a-z][a-z0-9._-]{1,79}$",
    )
    role_revision: int = Field(ge=1)
    account_lease_id: UUID
    account_handle: str = Field(pattern=ACCOUNT_HANDLE_PATTERN)
    fencing_token: int = Field(ge=1)
    browser_context_ref: str = Field(pattern=BROWSER_CONTEXT_REF_PATTERN)


class FixtureExecutionBinding(FrozenWireModel):
    """Exact ready FixtureRun and immutable export manifest binding."""

    fixture_run_id: UUID
    blueprint_version_id: UUID
    blueprint_version_ref: ExactVersionRef
    blueprint_content_digest: str = Field(pattern=DIGEST_PATTERN)
    fixture_plan_digest: str = Field(pattern=DIGEST_PATTERN)
    fixture_manifest_digest: str = Field(pattern=DIGEST_PATTERN)


class BrowserExecutionProfile(FrozenWireModel):
    """Reviewed browser binary and deterministic locale settings."""

    engine: Literal["chromium"] = "chromium"
    revision: str = Field(min_length=1, max_length=160, pattern=REFERENCE_PATTERN)
    viewport: Viewport
    locale: str = Field(
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$",
    )
    timezone: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_+./-]+$",
    )


class ModelExecutionProfile(FrozenWireModel):
    """Frozen model and Prompt identities without provider secrets."""

    model_profile_ref: ExactVersionRef
    prompt_bundle_ref: ExactVersionRef
    reasoning_policy_ref: ExactVersionRef


class ToolExecutionProfile(FrozenWireModel):
    """Frozen tool catalog, MCP manifests, and policy bundle digests."""

    tool_catalog_ref: ExactVersionRef
    mcp_server_manifest_digest: str = Field(pattern=DIGEST_PATTERN)
    tool_schema_digest: str = Field(pattern=DIGEST_PATTERN)
    policy_bundle_ref: ExactVersionRef
    policy_digest: str = Field(pattern=DIGEST_PATTERN)


class BindExecutionActor(FrozenWireModel):
    """Runtime-provided opaque session binding verified against PostgreSQL facts."""

    actor_slot: str = Field(
        min_length=2,
        max_length=80,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{1,79}$",
    )
    account_lease_id: UUID
    fencing_token: int = Field(ge=1)
    browser_context_ref: str = Field(pattern=BROWSER_CONTEXT_REF_PATTERN)


class BindDebugExecution(FrozenWireModel):
    """Trusted Case Runtime command that supplies reviewed runtime versions."""

    worker_identity: str = Field(
        min_length=3,
        max_length=160,
        pattern=WORKER_ID_PATTERN,
    )
    fixture_run_id: UUID
    actors: tuple[BindExecutionActor, ...] = Field(min_length=1, max_length=8)
    browser: BrowserExecutionProfile
    model: ModelExecutionProfile
    tools: ToolExecutionProfile

    @field_validator("actors")
    @classmethod
    def require_unique_actor_bindings(
        cls,
        values: tuple[BindExecutionActor, ...],
    ) -> tuple[BindExecutionActor, ...]:
        slots = [item.actor_slot for item in values]
        leases = [item.account_lease_id for item in values]
        if len(slots) != len(set(slots)) or len(leases) != len(set(leases)):
            raise ValueError("actor slots and account leases must be unique")
        return tuple(sorted(values, key=lambda item: item.actor_slot))


class ExecutionContract(FrozenWireModel):
    """Complete immutable binding consumed by one DebugRun browser execution."""

    schema_version: Literal["atlas.execution-contract/0.1"] = (
        EXECUTION_CONTRACT_SCHEMA_VERSION
    )
    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    debug_run_id: UUID
    test_case_id: UUID
    semantic_revision: int = Field(ge=1)
    test_ir_digest: str = Field(pattern=DIGEST_PATTERN)
    plan_digest: str = Field(pattern=DIGEST_PATTERN)
    compiled_digest: str = Field(pattern=DIGEST_PATTERN)
    actors: tuple[ExecutionActorBinding, ...] = Field(min_length=1, max_length=8)
    fixture: FixtureExecutionBinding
    browser: BrowserExecutionProfile
    model: ModelExecutionProfile
    tools: ToolExecutionProfile
    worker_identity: str = Field(
        min_length=3,
        max_length=160,
        pattern=WORKER_ID_PATTERN,
    )
    execution_deadline: AwareDatetime
    created_at: AwareDatetime
    content_digest: str = Field(pattern=DIGEST_PATTERN)

    @field_validator("actors")
    @classmethod
    def normalize_actors(
        cls,
        values: tuple[ExecutionActorBinding, ...],
    ) -> tuple[ExecutionActorBinding, ...]:
        slots = [item.actor_slot for item in values]
        leases = [item.account_lease_id for item in values]
        if len(slots) != len(set(slots)) or len(leases) != len(set(leases)):
            raise ValueError("execution actors must use unique slots and leases")
        return tuple(sorted(values, key=lambda item: item.actor_slot))

    @model_validator(mode="after")
    def validate_digest_and_time(self) -> Self:
        if self.created_at >= self.execution_deadline:
            raise ValueError("execution contract must predate its deadline")
        if self.content_digest != execution_contract_digest(self):
            raise ValueError("contentDigest must match the complete execution contract")
        return self


class AssertionResultInput(FrozenWireModel):
    """Untrusted wire input checked against one frozen Test IR assertion."""

    assertion_id: str = Field(min_length=3, max_length=160, pattern=REFERENCE_PATTERN)
    status: AssertionStatus
    expected_digest: str = Field(pattern=DIGEST_PATTERN)
    actual_safe_summary: str = Field(min_length=1, max_length=500)
    evaluator_version_ref: ExactVersionRef
    evidence_refs: tuple[UUID, ...] = Field(default=(), max_length=64)
    observed_at: AwareDatetime
    duration_ms: int = Field(ge=0, le=3_600_000)

    @field_validator("evidence_refs")
    @classmethod
    def normalize_evidence_refs(cls, values: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(values) != len(set(values)):
            raise ValueError("evidenceRefs must not contain duplicates")
        return tuple(sorted(values, key=str))


class AssertionResult(FrozenWireModel):
    """Oracle result validated against an immutable assertion specification."""

    schema_version: Literal["atlas.assertion-result/0.1"] = (
        ASSERTION_RESULT_SCHEMA_VERSION
    )
    id: UUID
    assertion_id: str = Field(min_length=3, max_length=160, pattern=REFERENCE_PATTERN)
    node_id: str = Field(min_length=1, max_length=128, pattern=REFERENCE_PATTERN)
    strength: OracleStrength
    status: AssertionStatus
    expected_digest: str = Field(pattern=DIGEST_PATTERN)
    actual_safe_summary: str = Field(min_length=1, max_length=500)
    evaluator_version_ref: ExactVersionRef
    evidence_refs: tuple[UUID, ...] = Field(default=(), max_length=64)
    observed_at: AwareDatetime
    duration_ms: int = Field(ge=0, le=3_600_000)
    result_digest: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_result_digest(self) -> Self:
        if self.result_digest != assertion_result_digest(self):
            raise ValueError("resultDigest must match the assertion result")
        return self


class EvidenceArtifactInput(FrozenWireModel):
    """Evidence Service metadata after capture-time redaction and hashing."""

    id: UUID
    kind: EvidenceArtifactKind
    object_ref: str = Field(pattern=EVIDENCE_OBJECT_REF_PATTERN, repr=False)
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    size_bytes: int = Field(ge=1, le=10 * 1024 * 1024 * 1024)
    mime_type: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z0-9][a-z0-9.+-]*/[A-Za-z0-9][A-Za-z0-9.+-]*$",
    )
    redaction_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    integrity: EvidenceIntegrity
    required: bool = False
    captured_at: AwareDatetime


class EvidenceArtifact(FrozenWireModel):
    """Safe artifact manifest entry without storage location or signed URL."""

    id: UUID
    kind: EvidenceArtifactKind
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    size_bytes: int = Field(ge=1, le=10 * 1024 * 1024 * 1024)
    mime_type: str = Field(min_length=3, max_length=160)
    redaction_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    integrity: EvidenceIntegrity
    required: bool
    captured_at: AwareDatetime


class FinalizeDebugEvidence(FrozenWireModel):
    """Trusted finalization command containing only reviewed evidence metadata."""

    execution_contract_id: UUID
    execution_contract_digest: str = Field(pattern=DIGEST_PATTERN)
    assertion_results: tuple[AssertionResultInput, ...] = Field(
        default=(),
        max_length=256,
    )
    artifacts: tuple[EvidenceArtifactInput, ...] = Field(default=(), max_length=512)
    event_chain_head_digest: str = Field(pattern=DIGEST_PATTERN)
    event_count: int = Field(ge=1, le=10_000_000)
    finalized_at: AwareDatetime

    @field_validator("assertion_results")
    @classmethod
    def require_unique_assertions(
        cls,
        values: tuple[AssertionResultInput, ...],
    ) -> tuple[AssertionResultInput, ...]:
        ids = [item.assertion_id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("assertionResults must use unique assertion IDs")
        return tuple(sorted(values, key=lambda item: item.assertion_id))

    @field_validator("artifacts")
    @classmethod
    def require_unique_artifacts(
        cls,
        values: tuple[EvidenceArtifactInput, ...],
    ) -> tuple[EvidenceArtifactInput, ...]:
        ids = [item.id for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError("artifacts must use unique IDs")
        return tuple(sorted(values, key=lambda item: str(item.id)))


class EvidenceManifest(FrozenWireModel):
    """Immutable evidence root used to authorize a DebugRun result."""

    schema_version: Literal["atlas.evidence-manifest/0.1"] = (
        EVIDENCE_MANIFEST_SCHEMA_VERSION
    )
    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    debug_run_id: UUID
    execution_contract_id: UUID
    execution_contract_digest: str = Field(pattern=DIGEST_PATTERN)
    test_ir_digest: str = Field(pattern=DIGEST_PATTERN)
    plan_digest: str = Field(pattern=DIGEST_PATTERN)
    fixture_run_id: UUID
    fixture_manifest_digest: str = Field(pattern=DIGEST_PATTERN)
    outcome: OracleOutcome
    completeness: EvidenceCompleteness
    integrity: EvidenceIntegrity
    assertion_results: tuple[AssertionResult, ...]
    missing_assertion_ids: tuple[str, ...]
    artifacts: tuple[EvidenceArtifact, ...]
    oracle_results_digest: str = Field(pattern=DIGEST_PATTERN)
    artifact_manifest_digest: str = Field(pattern=DIGEST_PATTERN)
    event_chain_head_digest: str = Field(pattern=DIGEST_PATTERN)
    event_count: int = Field(ge=1, le=10_000_000)
    passed_assertions: int = Field(ge=0)
    failed_assertions: int = Field(ge=0)
    inconclusive_assertions: int = Field(ge=0)
    finalized_at: AwareDatetime
    content_digest: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_manifest_shape(self) -> Self:
        result_ids = [item.assertion_id for item in self.assertion_results]
        artifact_ids = [item.id for item in self.artifacts]
        if result_ids != sorted(result_ids) or len(result_ids) != len(set(result_ids)):
            raise ValueError("assertionResults must be sorted and unique")
        if artifact_ids != sorted(artifact_ids, key=str) or len(artifact_ids) != len(
            set(artifact_ids)
        ):
            raise ValueError("artifacts must be sorted and unique")
        counts = {
            AssertionStatus.PASSED: self.passed_assertions,
            AssertionStatus.FAILED: self.failed_assertions,
            AssertionStatus.INCONCLUSIVE: self.inconclusive_assertions,
        }
        if any(
            sum(item.status is status for item in self.assertion_results) != expected
            for status, expected in counts.items()
        ):
            raise ValueError("assertion counts must match assertionResults")
        if self.oracle_results_digest != canonical_digest(
            {
                "assertionResults": [
                    item.model_dump(mode="json", by_alias=True)
                    for item in self.assertion_results
                ],
                "missingAssertionIds": list(self.missing_assertion_ids),
            }
        ):
            raise ValueError("oracleResultsDigest must match assertion results")
        if self.artifact_manifest_digest != canonical_digest(
            {
                "artifacts": [
                    item.model_dump(mode="json", by_alias=True) for item in self.artifacts
                ]
            }
        ):
            raise ValueError("artifactManifestDigest must match artifacts")
        if self.content_digest != evidence_manifest_digest(self):
            raise ValueError("contentDigest must match the evidence manifest")
        if self.outcome is OracleOutcome.PASSED and (
            self.completeness is not EvidenceCompleteness.COMPLETE
            or self.integrity is not EvidenceIntegrity.VERIFIED
            or self.missing_assertion_ids
        ):
            raise ValueError("PASSED evidence must be complete and verified")
        return self


def execution_contract_digest(contract: ExecutionContract) -> str:
    """Hash the complete execution binding except its self-referential digest."""

    body = contract.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    return canonical_digest(body)


def assertion_result_digest(result: AssertionResult) -> str:
    """Hash one immutable Oracle result except its self-referential digest."""

    body = result.model_dump(mode="json", by_alias=True, exclude={"result_digest"})
    return canonical_digest(body)


def evidence_manifest_digest(manifest: EvidenceManifest) -> str:
    """Hash one complete evidence root except its self-referential digest."""

    body = manifest.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
    return canonical_digest(body)


def json_body(value: FrozenWireModel) -> dict[str, JsonValue]:
    """Return a typed camel-case JSON object for persistence helpers."""

    return cast(
        dict[str, JsonValue],
        value.model_dump(mode="json", by_alias=True),
    )
