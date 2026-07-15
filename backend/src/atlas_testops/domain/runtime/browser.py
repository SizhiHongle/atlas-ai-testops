"""Strict internal contracts for one frozen DebugRun browser execution."""

from __future__ import annotations

from datetime import UTC
from enum import StrEnum
from json import dumps
from re import search
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case import DebugRun, PlanTemplate, TestIR, canonical_digest
from atlas_testops.domain.case.models import DIGEST_PATTERN
from atlas_testops.domain.runtime.models import (
    EvidenceArtifactKind,
    EvidenceIntegrity,
    EvidenceManifest,
    ExecutionContract,
    FinalizeDebugEvidence,
)

BROWSER_EXECUTION_BUNDLE_SCHEMA_VERSION: Literal[
    "atlas.browser-execution-bundle/0.1"
] = "atlas.browser-execution-bundle/0.1"
BROWSER_RUNTIME_REPORT_SCHEMA_VERSION: Literal[
    "atlas.browser-runtime-report/0.1"
] = "atlas.browser-runtime-report/0.1"
BROWSER_CONTEXT_RESTORE_ENVELOPE_VERSION: Literal[
    "atlas.browser-context-restore-envelope/0.1"
] = "atlas.browser-context-restore-envelope/0.1"
BROWSER_CONTEXT_RESTORE_DESCRIPTOR_VERSION: Literal[
    "atlas.browser-context-restore-descriptor/0.1"
] = "atlas.browser-context-restore-descriptor/0.1"

REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,159}$"
TARGET_REF_PATTERN = r"^target_[A-Za-z0-9_-]{20,160}$"
OBSERVATION_REF_PATTERN = r"^observation_[A-Za-z0-9_-]{20,160}$"
PAGE_REF_PATTERN = r"^page_[A-Za-z0-9_-]{20,160}$"
NONCE_PATTERN = r"^[A-Za-z0-9_-]{20,200}$"
BASE64URL_PATTERN = r"^[A-Za-z0-9_-]+={0,2}$"
SESSION_OBJECT_REF_PATTERN = r"^session-vault://[A-Za-z0-9][A-Za-z0-9/_.=-]+$"
CHAIN_START_DIGEST = "sha256:" + "0" * 64

_SENSITIVE_PAYLOAD_FRAGMENTS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "objectref",
        "password",
        "secret",
        "storagestate",
        "token",
    }
)


class BrowserActionKind(StrEnum):
    """Only reviewed browser capabilities can cross the Agent boundary."""

    OBSERVE = "observe"
    OPEN_ROUTE = "open_route"
    ACTIVATE = "activate"
    ENTER_TEXT = "enter_text"
    CHOOSE_OPTION = "choose_option"
    KEYPRESS = "keypress"
    SCROLL = "scroll"
    WAIT_FOR_CONDITION = "wait_for_condition"
    CAPTURE_VIEW = "capture_view"
    EVALUATE_ASSERTION = "evaluate_assertion"


class BrowserActionRisk(StrEnum):
    """Low-cardinality risk used by deterministic policy rules."""

    READ = "read"
    INPUT = "input"
    NAVIGATE = "navigate"
    MUTATION = "mutation"


_ACTION_RISK_RULES = {
    BrowserActionKind.OBSERVE: frozenset({BrowserActionRisk.READ}),
    BrowserActionKind.OPEN_ROUTE: frozenset({BrowserActionRisk.NAVIGATE}),
    BrowserActionKind.ACTIVATE: frozenset(
        {BrowserActionRisk.NAVIGATE, BrowserActionRisk.MUTATION}
    ),
    BrowserActionKind.ENTER_TEXT: frozenset({BrowserActionRisk.INPUT}),
    BrowserActionKind.CHOOSE_OPTION: frozenset({BrowserActionRisk.INPUT}),
    BrowserActionKind.KEYPRESS: frozenset({BrowserActionRisk.INPUT}),
    BrowserActionKind.SCROLL: frozenset({BrowserActionRisk.READ}),
    BrowserActionKind.WAIT_FOR_CONDITION: frozenset({BrowserActionRisk.READ}),
    BrowserActionKind.CAPTURE_VIEW: frozenset({BrowserActionRisk.READ}),
    BrowserActionKind.EVALUATE_ASSERTION: frozenset({BrowserActionRisk.READ}),
}


class BrowserPolicyDecisionKind(StrEnum):
    """The policy engine never infers permission from natural language."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class BrowserRuntimeReportKind(StrEnum):
    """Append-only execution-plane facts reported by the Browser Worker."""

    EXECUTION_STARTED = "execution.started"
    NODE_STARTED = "node.started"
    OBSERVATION_CAPTURED = "observation.captured"
    ACTION_PROPOSED = "action.proposed"
    POLICY_DECIDED = "policy.decided"
    ACTION_EXECUTED = "action.executed"
    ARTIFACT_CAPTURED = "artifact.captured"
    ASSERTION_EVALUATED = "assertion.evaluated"
    NODE_COMPLETED = "node.completed"
    EXECUTION_BLOCKED = "execution.blocked"
    EXECUTION_COMPLETED = "execution.completed"


class BrowserTargetCandidate(FrozenWireModel):
    """One short-lived semantic target derived from the current page revision."""

    target_ref: str = Field(pattern=TARGET_REF_PATTERN)
    element_key: str | None = Field(default=None, max_length=160)
    role: str | None = Field(default=None, max_length=80)
    accessible_name: str | None = Field(default=None, max_length=240)
    confidence: float = Field(ge=0, le=1)
    semantic_fingerprint: str = Field(pattern=DIGEST_PATTERN)


class BrowserObservation(FrozenWireModel):
    """Browser facts captured by the adapter, never an Agent self-report."""

    observation_ref: str = Field(pattern=OBSERVATION_REF_PATTERN)
    page_ref: str = Field(pattern=PAGE_REF_PATTERN)
    page_revision: int = Field(ge=1)
    route_key: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    title: str = Field(default="", max_length=240)
    target_candidates: tuple[BrowserTargetCandidate, ...] = Field(
        default=(),
        max_length=256,
    )
    untrusted_page_summary: str = Field(default="", max_length=2_000)
    next_step_nonce: str = Field(pattern=NONCE_PATTERN)
    observed_at: AwareDatetime

    @field_validator("target_candidates")
    @classmethod
    def require_unique_targets(
        cls,
        values: tuple[BrowserTargetCandidate, ...],
    ) -> tuple[BrowserTargetCandidate, ...]:
        refs = [item.target_ref for item in values]
        if len(refs) != len(set(refs)):
            raise ValueError("targetCandidates must use unique targetRef values")
        return values


class BrowserActionProposal(FrozenWireModel):
    """One structured micro-action proposed inside a frozen plan node."""

    action_id: UUID
    node_id: str = Field(pattern=REFERENCE_PATTERN)
    actor_slot: str = Field(pattern=REFERENCE_PATTERN)
    action: BrowserActionKind
    risk: BrowserActionRisk
    expected_observation_ref: str | None = Field(
        default=None,
        pattern=OBSERVATION_REF_PATTERN,
    )
    expected_page_revision: int | None = Field(default=None, ge=1)
    next_step_nonce: str | None = Field(default=None, pattern=NONCE_PATTERN)
    target_ref: str | None = Field(default=None, pattern=TARGET_REF_PATTERN)
    route_key: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    value_ref: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    option_value: str | None = Field(default=None, min_length=1, max_length=240)
    key: str | None = Field(default=None, min_length=1, max_length=40)
    scroll_delta_y: int | None = Field(default=None, ge=-10_000, le=10_000)
    condition_ref: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    assertion_id: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    safe_summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_action_shape(self) -> Self:
        if self.risk not in _ACTION_RISK_RULES[self.action]:
            raise ValueError("action risk does not match the reviewed action class")
        observation_bound = {
            BrowserActionKind.ACTIVATE,
            BrowserActionKind.ENTER_TEXT,
            BrowserActionKind.CHOOSE_OPTION,
            BrowserActionKind.KEYPRESS,
            BrowserActionKind.SCROLL,
            BrowserActionKind.CAPTURE_VIEW,
        }
        if self.action in observation_bound and any(
            value is None
            for value in (
                self.expected_observation_ref,
                self.expected_page_revision,
                self.next_step_nonce,
            )
        ):
            raise ValueError("page actions must bind an observation, revision, and nonce")
        if self.action in {
            BrowserActionKind.ACTIVATE,
            BrowserActionKind.ENTER_TEXT,
            BrowserActionKind.CHOOSE_OPTION,
        } and self.target_ref is None:
            raise ValueError("targeted actions require targetRef")
        if self.action is BrowserActionKind.OPEN_ROUTE and self.route_key is None:
            raise ValueError("openRoute requires routeKey")
        if self.action is BrowserActionKind.ENTER_TEXT and self.value_ref is None:
            raise ValueError("enterText requires valueRef")
        if self.action is BrowserActionKind.CHOOSE_OPTION and self.option_value is None:
            raise ValueError("chooseOption requires optionValue")
        if self.action is BrowserActionKind.KEYPRESS and self.key is None:
            raise ValueError("keypress requires a reviewed key")
        if self.action is BrowserActionKind.SCROLL and self.scroll_delta_y is None:
            raise ValueError("scroll requires scrollDeltaY")
        if (
            self.action is BrowserActionKind.WAIT_FOR_CONDITION
            and self.condition_ref is None
        ):
            raise ValueError("waitForCondition requires conditionRef")
        if (
            self.action is BrowserActionKind.EVALUATE_ASSERTION
            and self.assertion_id is None
        ):
            raise ValueError("evaluateAssertion requires assertionId")
        action_fields = {
            "target_ref": {
                BrowserActionKind.ACTIVATE,
                BrowserActionKind.ENTER_TEXT,
                BrowserActionKind.CHOOSE_OPTION,
            },
            "route_key": {BrowserActionKind.OPEN_ROUTE},
            "value_ref": {BrowserActionKind.ENTER_TEXT},
            "option_value": {BrowserActionKind.CHOOSE_OPTION},
            "key": {BrowserActionKind.KEYPRESS},
            "scroll_delta_y": {BrowserActionKind.SCROLL},
            "condition_ref": {BrowserActionKind.WAIT_FOR_CONDITION},
            "assertion_id": {BrowserActionKind.EVALUATE_ASSERTION},
        }
        for field_name, allowed_actions in action_fields.items():
            if getattr(self, field_name) is not None and self.action not in allowed_actions:
                raise ValueError(f"{field_name} is not valid for {self.action.value}")
        return self


class BrowserPolicyDecision(FrozenWireModel):
    """Deterministic decision over one exact proposal and frozen policy."""

    action_id: UUID
    decision: BrowserPolicyDecisionKind
    policy_digest: str = Field(pattern=DIGEST_PATTERN)
    matched_rules: tuple[str, ...] = Field(default=(), max_length=32)
    safe_summary: str = Field(min_length=1, max_length=500)
    expires_at: AwareDatetime


class BrowserActionGrant(FrozenWireModel):
    """Single-use, short-lived capability accepted by the adapter."""

    grant_id: UUID
    action_id: UUID
    proposal_digest: str = Field(pattern=DIGEST_PATTERN)
    execution_contract_id: UUID
    execution_contract_digest: str = Field(pattern=DIGEST_PATTERN)
    actor_slot: str = Field(pattern=REFERENCE_PATTERN)
    page_ref: str = Field(pattern=PAGE_REF_PATTERN)
    page_revision: int = Field(ge=1)
    allowed_action: BrowserActionKind
    expires_at: AwareDatetime
    max_executions: Literal[1] = 1
    policy_digest: str = Field(pattern=DIGEST_PATTERN)


class BrowserExecutionReceipt(FrozenWireModel):
    """Adapter fact proving whether one granted action reached Playwright."""

    receipt_id: UUID
    grant_id: UUID
    action_id: UUID
    adapter: BrowserActionKind
    started_at: AwareDatetime
    completed_at: AwareDatetime
    status: Literal["SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"]
    safe_summary: str = Field(min_length=1, max_length=500)
    resulting_page_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("execution receipt cannot complete before it starts")
        return self


class BrowserContextRestoreEnvelope(FrozenWireModel):
    """Encrypted session descriptor safe to carry through Temporal history."""

    schema_version: Literal[
        "atlas.browser-context-restore-envelope/0.1"
    ] = BROWSER_CONTEXT_RESTORE_ENVELOPE_VERSION
    actor_slot: str = Field(pattern=REFERENCE_PATTERN)
    browser_context_ref: str = Field(pattern=r"^bctx_[A-Za-z0-9_-]{32,200}$")
    algorithm: Literal["AES-256-GCM"] = "AES-256-GCM"
    key_version: str = Field(min_length=1, max_length=100, pattern=REFERENCE_PATTERN)
    nonce: str = Field(pattern=BASE64URL_PATTERN)
    ciphertext: str = Field(pattern=BASE64URL_PATTERN, repr=False)
    expires_at: AwareDatetime


class BrowserContextRestoreDescriptor(FrozenWireModel):
    """Worker-only decrypted metadata required to consume a session artifact."""

    schema_version: Literal[
        "atlas.browser-context-restore-descriptor/0.1"
    ] = BROWSER_CONTEXT_RESTORE_DESCRIPTOR_VERSION
    actor_slot: str = Field(pattern=REFERENCE_PATTERN)
    browser_context_ref: str = Field(pattern=r"^bctx_[A-Za-z0-9_-]{32,200}$")
    artifact_id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    lease_id: UUID
    lease_fence: int = Field(ge=1)
    account_id: UUID
    connector_installation_id: UUID
    credential_binding_id: UUID
    allowed_origins: tuple[str, ...] = Field(min_length=1, max_length=16)
    object_ref: str = Field(pattern=SESSION_OBJECT_REF_PATTERN, repr=False)
    object_digest: str = Field(pattern=DIGEST_PATTERN)
    key_version: str = Field(min_length=1, max_length=100, pattern=REFERENCE_PATTERN)
    format_version: Literal["playwright-storage-state/v1"] = (
        "playwright-storage-state/v1"
    )
    expires_at: AwareDatetime


class BrowserExecutionBundle(FrozenWireModel):
    """Complete trusted, secret-free execution view consumed by the Worker."""

    schema_version: Literal[
        "atlas.browser-execution-bundle/0.1"
    ] = BROWSER_EXECUTION_BUNDLE_SCHEMA_VERSION
    execution_contract: ExecutionContract
    test_ir: TestIR
    plan_template: PlanTemplate
    fixture_exports: dict[str, JsonValue]
    restore_envelopes: tuple[BrowserContextRestoreEnvelope, ...]
    issued_at: AwareDatetime

    @model_validator(mode="after")
    def validate_frozen_bindings(self) -> Self:
        contract = self.execution_contract
        if (
            self.test_ir.test_case_id != contract.test_case_id
            or self.test_ir.semantic_revision != contract.semantic_revision
            or self.test_ir.content_digest != contract.test_ir_digest
            or self.plan_template.test_case_id != contract.test_case_id
            or self.plan_template.semantic_revision != contract.semantic_revision
            or self.plan_template.test_ir_digest != contract.test_ir_digest
            or self.plan_template.plan_digest != contract.plan_digest
        ):
            raise ValueError("execution bundle does not match the frozen contract")
        required_exports = set(self.test_ir.fixture.required_exports)
        if not required_exports.issubset(self.fixture_exports):
            raise ValueError("execution bundle is missing required fixture exports")
        actor_refs = {
            item.actor_slot: item.browser_context_ref for item in contract.actors
        }
        envelope_refs = {
            item.actor_slot: item.browser_context_ref for item in self.restore_envelopes
        }
        if actor_refs != envelope_refs:
            raise ValueError("restore envelopes must match every execution actor")
        if self.issued_at < contract.created_at or self.issued_at >= contract.execution_deadline:
            raise ValueError("execution bundle issue time is outside the contract window")
        if any(item.expires_at > contract.execution_deadline for item in self.restore_envelopes):
            raise ValueError("restore envelope cannot outlive the execution contract")
        return self


class AppendBrowserRuntimeReport(FrozenWireModel):
    """Worker report whose canonical chain is independently verified by control plane."""

    schema_version: Literal[
        "atlas.browser-runtime-report/0.1"
    ] = BROWSER_RUNTIME_REPORT_SCHEMA_VERSION
    execution_contract_id: UUID
    execution_contract_digest: str = Field(pattern=DIGEST_PATTERN)
    report_id: UUID
    sequence: int = Field(ge=1, le=10_000_000)
    kind: BrowserRuntimeReportKind
    actor_slot: str | None = Field(default=None, pattern=REFERENCE_PATTERN)
    action_id: UUID | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: AwareDatetime
    previous_chain_digest: str = Field(pattern=DIGEST_PATTERN)
    payload_digest: str = Field(pattern=DIGEST_PATTERN)
    chain_digest: str = Field(pattern=DIGEST_PATTERN)

    @field_validator("payload")
    @classmethod
    def reject_sensitive_payload(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _validate_safe_payload(value)
        return value

    @model_validator(mode="after")
    def validate_digests(self) -> Self:
        if self.payload_digest != canonical_digest(self.payload):
            raise ValueError("payloadDigest must match the report payload")
        if self.chain_digest != browser_report_chain_digest(self):
            raise ValueError("chainDigest must match the canonical report")
        if self.sequence == 1 and self.previous_chain_digest != CHAIN_START_DIGEST:
            raise ValueError("the first report must use the chain start digest")
        _validate_report_semantics(self)
        return self


class BrowserRuntimeReport(FrozenWireModel):
    """Persisted tenant-scoped report returned on idempotent replay."""

    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    debug_run_id: UUID
    value: AppendBrowserRuntimeReport
    recorded_at: AwareDatetime


class BrowserRuntimeTransition(FrozenWireModel):
    """Exact contract reference required for READY and RUNNING transitions."""

    execution_contract_id: UUID
    execution_contract_digest: str = Field(pattern=DIGEST_PATTERN)


class BrowserEvidenceFinalization(FrozenWireModel):
    """Safe combined response after deterministic DebugRun finalization."""

    run: DebugRun
    evidence_manifest: EvidenceManifest


class BrowserFinalizeCommand(FrozenWireModel):
    """Internal wrapper that keeps the trusted finalization body explicit."""

    command: FinalizeDebugEvidence


def browser_action_proposal_digest(proposal: BrowserActionProposal) -> str:
    """Hash one exact proposal before a single-use grant is signed."""

    return canonical_digest(proposal)


def browser_report_chain_digest(report: AppendBrowserRuntimeReport) -> str:
    """Hash the normalized report header and its already-normalized payload digest."""

    return canonical_digest(
        {
            "schemaVersion": report.schema_version,
            "executionContractId": str(report.execution_contract_id),
            "executionContractDigest": report.execution_contract_digest,
            "reportId": str(report.report_id),
            "sequence": report.sequence,
            "kind": report.kind.value,
            "actorSlot": report.actor_slot,
            "actionId": str(report.action_id) if report.action_id is not None else None,
            "occurredAt": report.occurred_at.astimezone(UTC).isoformat().replace(
                "+00:00", "Z"
            ),
            "previousChainDigest": report.previous_chain_digest,
            "payloadDigest": report.payload_digest,
        }
    )


def build_browser_runtime_report(
    *,
    execution_contract_id: UUID,
    execution_contract_digest: str,
    report_id: UUID,
    sequence: int,
    kind: BrowserRuntimeReportKind,
    payload: dict[str, JsonValue],
    occurred_at: AwareDatetime,
    previous_chain_digest: str,
    actor_slot: str | None = None,
    action_id: UUID | None = None,
) -> AppendBrowserRuntimeReport:
    """Build one self-verifying append-only Browser Worker report."""

    payload_digest = canonical_digest(payload)
    provisional = AppendBrowserRuntimeReport.model_construct(
        execution_contract_id=execution_contract_id,
        execution_contract_digest=execution_contract_digest,
        report_id=report_id,
        sequence=sequence,
        kind=kind,
        actor_slot=actor_slot,
        action_id=action_id,
        payload=payload,
        occurred_at=occurred_at,
        previous_chain_digest=previous_chain_digest,
        payload_digest=payload_digest,
        chain_digest=CHAIN_START_DIGEST,
    )
    return AppendBrowserRuntimeReport(
        execution_contract_id=execution_contract_id,
        execution_contract_digest=execution_contract_digest,
        report_id=report_id,
        sequence=sequence,
        kind=kind,
        actor_slot=actor_slot,
        action_id=action_id,
        payload=payload,
        occurred_at=occurred_at,
        previous_chain_digest=previous_chain_digest,
        payload_digest=payload_digest,
        chain_digest=browser_report_chain_digest(provisional),
    )


def _validate_safe_payload(value: JsonValue, *, path: tuple[str, ...] = ()) -> None:
    if len(path) > 6:
        raise ValueError("browser report payload nesting exceeds six levels")
    if isinstance(value, dict):
        if not path and len(dumps(value, ensure_ascii=False).encode()) > 65_536:
            raise ValueError("browser report payload exceeds 64 KiB")
        if len(value) > 64:
            raise ValueError("browser report payload object is too large")
        for key, nested in value.items():
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if any(fragment in normalized for fragment in _SENSITIVE_PAYLOAD_FRAGMENTS):
                joined = ".".join((*path, key))
                raise ValueError(f"browser report payload contains sensitive key: {joined}")
            _validate_safe_payload(nested, path=(*path, key))
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise ValueError("browser report payload array is too large")
        for index, nested in enumerate(value):
            _validate_safe_payload(nested, path=(*path, str(index)))
        return
    if isinstance(value, str):
        joined = ".".join(path) or "payload"
        if len(value) > 2_000:
            raise ValueError(f"browser report payload string is too long at {joined}")
        if "://" in value:
            raise ValueError(f"browser report payload contains an absolute URL at {joined}")
        if search(
            r"(?i)(?:bearer\s+[A-Za-z0-9._~-]{12,}|"
            r"(?:password|secret|token|api[_-]?key)\s*[:=]\s*\S+|"
            r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})",
            value,
        ):
            raise ValueError(f"browser report payload contains secret-like text at {joined}")


_REPORT_PAYLOAD_KEYS: dict[BrowserRuntimeReportKind, frozenset[str]] = {
    BrowserRuntimeReportKind.EXECUTION_STARTED: frozenset(
        {"safeSummary", "planDigest"}
    ),
    BrowserRuntimeReportKind.NODE_STARTED: frozenset(
        {"safeSummary", "nodeId", "nodeKind", "versionRef"}
    ),
    BrowserRuntimeReportKind.OBSERVATION_CAPTURED: frozenset(
        {
            "safeSummary",
            "observationRef",
            "observationDigest",
            "pageRef",
            "pageRevision",
            "routeKey",
            "targetCount",
        }
    ),
    BrowserRuntimeReportKind.ACTION_PROPOSED: frozenset(
        {
            "safeSummary",
            "action",
            "risk",
            "nodeId",
            "targetRef",
            "routeKey",
            "proposalDigest",
        }
    ),
    BrowserRuntimeReportKind.POLICY_DECIDED: frozenset(
        {
            "safeSummary",
            "decision",
            "policyDigest",
            "decisionDigest",
            "matchedRules",
        }
    ),
    BrowserRuntimeReportKind.ACTION_EXECUTED: frozenset(
        {
            "safeSummary",
            "receiptId",
            "receiptDigest",
            "grantId",
            "action",
            "status",
            "resultingPageRevision",
        }
    ),
    BrowserRuntimeReportKind.ARTIFACT_CAPTURED: frozenset(
        {
            "safeSummary",
            "artifactId",
            "artifactInputDigest",
            "kind",
            "contentDigest",
            "sizeBytes",
            "integrity",
        }
    ),
    BrowserRuntimeReportKind.ASSERTION_EVALUATED: frozenset(
        {
            "safeSummary",
            "assertionId",
            "assertionInputDigest",
            "status",
            "expectedDigest",
        }
    ),
    BrowserRuntimeReportKind.NODE_COMPLETED: frozenset(
        {"safeSummary", "nodeId", "assertionResultCount", "artifactCount"}
    ),
    BrowserRuntimeReportKind.EXECUTION_BLOCKED: frozenset(
        {"safeSummary", "failureType"}
    ),
    BrowserRuntimeReportKind.EXECUTION_COMPLETED: frozenset(
        {"safeSummary", "assertionResultCount", "artifactCount"}
    ),
}

_ACTION_REPORT_KINDS = frozenset(
    {
        BrowserRuntimeReportKind.ACTION_PROPOSED,
        BrowserRuntimeReportKind.POLICY_DECIDED,
        BrowserRuntimeReportKind.ACTION_EXECUTED,
    }
)


def _validate_report_semantics(report: AppendBrowserRuntimeReport) -> None:
    expected_keys = _REPORT_PAYLOAD_KEYS[report.kind]
    if set(report.payload) != expected_keys:
        raise ValueError(f"{report.kind.value} payload does not match its frozen shape")
    summary = report.payload.get("safeSummary")
    if not isinstance(summary, str) or not 1 <= len(summary) <= 500:
        raise ValueError("browser report safeSummary is invalid")
    if report.kind in _ACTION_REPORT_KINDS:
        if report.actor_slot is None or report.action_id is None:
            raise ValueError("action reports require actorSlot and actionId")
    elif report.action_id is not None:
        raise ValueError("only action reports may carry actionId")
    if (
        report.kind is BrowserRuntimeReportKind.OBSERVATION_CAPTURED
        and report.actor_slot is None
    ):
        raise ValueError("observation reports require actorSlot")
    if report.kind in {
        BrowserRuntimeReportKind.EXECUTION_STARTED,
        BrowserRuntimeReportKind.EXECUTION_COMPLETED,
        BrowserRuntimeReportKind.EXECUTION_BLOCKED,
    } and report.actor_slot is not None:
        raise ValueError("execution reports cannot carry actorSlot")

    digest_keys = {
        BrowserRuntimeReportKind.EXECUTION_STARTED: ("planDigest",),
        BrowserRuntimeReportKind.POLICY_DECIDED: ("policyDigest",),
        BrowserRuntimeReportKind.ARTIFACT_CAPTURED: ("contentDigest",),
        BrowserRuntimeReportKind.ASSERTION_EVALUATED: ("expectedDigest",),
    }.get(report.kind, ())
    digest_keys = (
        *digest_keys,
        *{
            BrowserRuntimeReportKind.OBSERVATION_CAPTURED: ("observationDigest",),
            BrowserRuntimeReportKind.ACTION_PROPOSED: ("proposalDigest",),
            BrowserRuntimeReportKind.POLICY_DECIDED: ("decisionDigest",),
            BrowserRuntimeReportKind.ACTION_EXECUTED: ("receiptDigest",),
            BrowserRuntimeReportKind.ARTIFACT_CAPTURED: ("artifactInputDigest",),
            BrowserRuntimeReportKind.ASSERTION_EVALUATED: ("assertionInputDigest",),
        }.get(report.kind, ()),
    )
    for key in digest_keys:
        value = report.payload[key]
        if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
            raise ValueError(f"browser report {key} is invalid")

    non_negative_integer_keys = {
        BrowserRuntimeReportKind.OBSERVATION_CAPTURED: (
            "pageRevision",
            "targetCount",
        ),
        BrowserRuntimeReportKind.ACTION_EXECUTED: ("resultingPageRevision",),
        BrowserRuntimeReportKind.ARTIFACT_CAPTURED: ("sizeBytes",),
        BrowserRuntimeReportKind.NODE_COMPLETED: (
            "assertionResultCount",
            "artifactCount",
        ),
        BrowserRuntimeReportKind.EXECUTION_COMPLETED: (
            "assertionResultCount",
            "artifactCount",
        ),
    }.get(report.kind, ())
    for key in non_negative_integer_keys:
        value = report.payload[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"browser report {key} is invalid")

    if report.kind is BrowserRuntimeReportKind.ACTION_PROPOSED:
        action = report.payload["action"]
        risk = report.payload["risk"]
        if not isinstance(action, str) or not isinstance(risk, str):
            raise ValueError("browser report action or risk is invalid")
        parsed_action = BrowserActionKind(action)
        parsed_risk = BrowserActionRisk(risk)
        if parsed_risk not in _ACTION_RISK_RULES[parsed_action]:
            raise ValueError("browser report action risk is invalid")
    elif report.kind is BrowserRuntimeReportKind.POLICY_DECIDED:
        decision = report.payload["decision"]
        if not isinstance(decision, str):
            raise ValueError("browser report policy decision is invalid")
        BrowserPolicyDecisionKind(decision)
        matched_rules = report.payload["matchedRules"]
        if not isinstance(matched_rules, list) or not all(
            isinstance(item, str) and 1 <= len(item) <= 160 for item in matched_rules
        ):
            raise ValueError("browser report matchedRules is invalid")
    elif report.kind is BrowserRuntimeReportKind.ACTION_EXECUTED:
        action = report.payload["action"]
        if not isinstance(action, str):
            raise ValueError("browser report action is invalid")
        BrowserActionKind(action)
        if report.payload["status"] not in {"SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"}:
            raise ValueError("browser report action status is invalid")
    elif report.kind is BrowserRuntimeReportKind.ARTIFACT_CAPTURED:
        artifact_id = report.payload["artifactId"]
        if not isinstance(artifact_id, str):
            raise ValueError("browser report artifactId is invalid")
        try:
            UUID(artifact_id)
        except ValueError as error:
            raise ValueError("browser report artifactId is invalid") from error
        kind = report.payload["kind"]
        integrity = report.payload["integrity"]
        if not isinstance(kind, str) or not isinstance(integrity, str):
            raise ValueError("browser report artifact metadata is invalid")
        EvidenceArtifactKind(kind)
        EvidenceIntegrity(integrity)
    elif report.kind is BrowserRuntimeReportKind.ASSERTION_EVALUATED:
        assertion_id = report.payload["assertionId"]
        if not isinstance(assertion_id, str) or not 1 <= len(assertion_id) <= 160:
            raise ValueError("browser report assertionId is invalid")
        if report.payload["status"] not in {"PASSED", "FAILED", "INCONCLUSIVE"}:
            raise ValueError("browser report assertion status is invalid")
