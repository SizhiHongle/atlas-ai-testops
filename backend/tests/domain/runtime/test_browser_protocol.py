"""Browser execution wire protocol, hash chain, and envelope invariants."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest
from pydantic import ValidationError
from tests.domain.case.factories import build_intent_factory, build_valid_graph
from tests.domain.case.test_runtime_evidence import _contract, _run

from atlas_testops.domain.case import TestIntent as CaseIntent
from atlas_testops.domain.runtime import (
    CHAIN_START_DIGEST,
    AppendBrowserRuntimeReport,
    BrowserActionKind,
    BrowserActionProposal,
    BrowserActionRisk,
    BrowserContextRestoreDescriptor,
    BrowserExecutionBundle,
    BrowserPolicyDecisionKind,
    BrowserRuntimeReportKind,
    build_browser_runtime_report,
)
from atlas_testops.domain.workflow import WorkflowGraph
from atlas_testops.infrastructure.browser_envelope import (
    AesGcmBrowserContextEnvelopeCodec,
    BrowserContextEnvelopeError,
)

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _runtime(
    graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> tuple[BrowserExecutionBundle, AesGcmBrowserContextEnvelopeCodec]:
    run = _run(graph, intent_factory)
    contract = _contract(run, run.requested_at + timedelta(seconds=1))
    descriptor = BrowserContextRestoreDescriptor(
        actor_slot="operator",
        browser_context_ref="bctx_" + "x" * 40,
        artifact_id=UUID("91000000-0000-4000-8000-000000000009"),
        tenant_id=contract.tenant_id,
        project_id=contract.project_id,
        environment_id=contract.environment_id,
        lease_id=contract.actors[0].account_lease_id,
        lease_fence=contract.actors[0].fencing_token,
        account_id=UUID("92000000-0000-4000-8000-000000000009"),
        connector_installation_id=UUID("93000000-0000-4000-8000-000000000009"),
        credential_binding_id=UUID("94000000-0000-4000-8000-000000000009"),
        allowed_origins=("https://example.test",),
        object_ref="session-vault://atlas/session/object",
        object_digest=DIGEST_A,
        key_version="session-v1",
        expires_at=contract.execution_deadline,
    )
    codec = AesGcmBrowserContextEnvelopeCodec(b"e" * 32, key_version="envelope-v1")
    envelope = codec.seal(descriptor, contract=contract)
    return (
        BrowserExecutionBundle(
            execution_contract=contract,
            test_ir=run.test_ir,
            plan_template=run.plan_template,
            fixture_exports={"customerId": "customer-42"},
            restore_envelopes=(envelope,),
            issued_at=contract.created_at + timedelta(milliseconds=1),
        ),
        codec,
    )


def _payload(kind: BrowserRuntimeReportKind) -> dict[str, object]:
    values: dict[BrowserRuntimeReportKind, dict[str, object]] = {
        BrowserRuntimeReportKind.EXECUTION_STARTED: {
            "safeSummary": "browser execution started",
            "planDigest": DIGEST_A,
        },
        BrowserRuntimeReportKind.NODE_STARTED: {
            "safeSummary": "frozen plan node started",
            "nodeId": "filter-agent",
            "nodeKind": "agent",
            "versionRef": "agent.semantic-filter@1.0.0",
        },
        BrowserRuntimeReportKind.OBSERVATION_CAPTURED: {
            "safeSummary": "browser observation captured",
            "observationRef": "observation_" + "o" * 24,
            "observationDigest": DIGEST_A,
            "pageRef": "page_" + "p" * 24,
            "pageRevision": 1,
            "routeKey": "customer.list",
            "targetCount": 2,
        },
        BrowserRuntimeReportKind.ACTION_PROPOSED: {
            "safeSummary": "structured browser action proposed",
            "action": "activate",
            "risk": "mutation",
            "nodeId": "filter-agent",
            "targetRef": "target_" + "t" * 24,
            "routeKey": None,
            "proposalDigest": DIGEST_A,
        },
        BrowserRuntimeReportKind.POLICY_DECIDED: {
            "safeSummary": "policy allowed the action",
            "decision": BrowserPolicyDecisionKind.ALLOW.value,
            "policyDigest": DIGEST_A,
            "decisionDigest": DIGEST_B,
            "matchedRules": ["tool.catalog"],
        },
        BrowserRuntimeReportKind.ACTION_EXECUTED: {
            "safeSummary": "browser action completed",
            "receiptId": str(uuid7()),
            "receiptDigest": DIGEST_A,
            "grantId": str(uuid7()),
            "action": "activate",
            "status": "SUCCEEDED",
            "resultingPageRevision": 2,
        },
        BrowserRuntimeReportKind.ARTIFACT_CAPTURED: {
            "safeSummary": "verified browser evidence artifact captured",
            "artifactId": str(uuid7()),
            "artifactInputDigest": DIGEST_B,
            "kind": "SCREENSHOT",
            "contentDigest": DIGEST_A,
            "sizeBytes": 100,
            "integrity": "VERIFIED",
        },
        BrowserRuntimeReportKind.ASSERTION_EVALUATED: {
            "safeSummary": "browser assertion evaluated",
            "assertionId": "assert.customer-visible",
            "assertionInputDigest": DIGEST_A,
            "status": "PASSED",
            "expectedDigest": DIGEST_B,
        },
        BrowserRuntimeReportKind.NODE_COMPLETED: {
            "safeSummary": "frozen plan node completed",
            "nodeId": "filter-agent",
            "assertionResultCount": 1,
            "artifactCount": 1,
        },
        BrowserRuntimeReportKind.EXECUTION_BLOCKED: {
            "safeSummary": "browser execution stopped before verified completion",
            "failureType": "BrowserExecutionError",
        },
        BrowserRuntimeReportKind.EXECUTION_COMPLETED: {
            "safeSummary": "browser execution reached evidence finalization",
            "assertionResultCount": 1,
            "artifactCount": 1,
        },
    }
    return values[kind]


def test_bundle_and_envelope_are_exact_and_tamper_evident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="valid base64"):
        AesGcmBrowserContextEnvelopeCodec.from_base64_key(
            "not-base64!",
            key_version="envelope-v1",
        )
    bundle, codec = _runtime(build_valid_graph(), build_intent_factory())
    envelope = bundle.restore_envelopes[0]
    descriptor = codec.open(envelope, contract=bundle.execution_contract)
    assert descriptor.object_ref.startswith("session-vault://")
    assert descriptor.actor_slot == "operator"

    tampered = envelope.model_copy(update={"ciphertext": "A" + envelope.ciphertext[1:]})
    with pytest.raises(BrowserContextEnvelopeError, match="authentication"):
        codec.open(tampered, contract=bundle.execution_contract)
    with pytest.raises(BrowserContextEnvelopeError, match="stale"):
        AesGcmBrowserContextEnvelopeCodec(
            b"e" * 32,
            key_version="envelope-v2",
        ).open(envelope, contract=bundle.execution_contract)

    monkeypatch.setattr(
        "atlas_testops.infrastructure.browser_envelope.utc_now",
        lambda: bundle.execution_contract.execution_deadline,
    )
    with pytest.raises(BrowserContextEnvelopeError, match="does not match"):
        codec.open(envelope, contract=bundle.execution_contract)


def test_report_chain_has_typed_payloads_and_normalized_utc() -> None:
    contract_id = uuid7()
    action_id = uuid7()
    occurred_at = datetime.now(UTC)
    chain = CHAIN_START_DIGEST
    reports: list[AppendBrowserRuntimeReport] = []
    kinds = (
        BrowserRuntimeReportKind.EXECUTION_STARTED,
        BrowserRuntimeReportKind.NODE_STARTED,
        BrowserRuntimeReportKind.OBSERVATION_CAPTURED,
        BrowserRuntimeReportKind.ACTION_PROPOSED,
        BrowserRuntimeReportKind.POLICY_DECIDED,
        BrowserRuntimeReportKind.ACTION_EXECUTED,
        BrowserRuntimeReportKind.ARTIFACT_CAPTURED,
        BrowserRuntimeReportKind.ASSERTION_EVALUATED,
        BrowserRuntimeReportKind.NODE_COMPLETED,
        BrowserRuntimeReportKind.EXECUTION_COMPLETED,
    )
    for sequence, kind in enumerate(kinds, start=1):
        is_action = kind in {
            BrowserRuntimeReportKind.ACTION_PROPOSED,
            BrowserRuntimeReportKind.POLICY_DECIDED,
            BrowserRuntimeReportKind.ACTION_EXECUTED,
        }
        actor_slot = (
            "operator"
            if is_action or kind is BrowserRuntimeReportKind.OBSERVATION_CAPTURED
            else None
        )
        report = build_browser_runtime_report(
            execution_contract_id=contract_id,
            execution_contract_digest=DIGEST_A,
            report_id=uuid7(),
            sequence=sequence,
            kind=kind,
            payload=_payload(kind),  # type: ignore[arg-type]
            occurred_at=occurred_at,
            previous_chain_digest=chain,
            actor_slot=actor_slot,
            action_id=action_id if is_action else None,
        )
        reports.append(report)
        chain = report.chain_digest
    assert reports[-1].sequence == 10
    assert reports[-1].previous_chain_digest == reports[-2].chain_digest

    utc_report = reports[0]
    offset_report = build_browser_runtime_report(
        execution_contract_id=utc_report.execution_contract_id,
        execution_contract_digest=utc_report.execution_contract_digest,
        report_id=utc_report.report_id,
        sequence=1,
        kind=utc_report.kind,
        payload=utc_report.payload,
        occurred_at=utc_report.occurred_at.astimezone(timezone(timedelta(hours=8))),
        previous_chain_digest=CHAIN_START_DIGEST,
    )
    assert offset_report.chain_digest == utc_report.chain_digest


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"risk": BrowserActionRisk.READ}, "risk"),
        ({"target_ref": None}, "targetRef"),
        ({"route_key": "unexpected.route"}, "route_key"),
    ],
)
def test_action_proposal_rejects_spoofed_or_irrelevant_fields(
    updates: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "action_id": uuid7(),
        "node_id": "filter-agent",
        "actor_slot": "operator",
        "action": BrowserActionKind.ACTIVATE,
        "risk": BrowserActionRisk.MUTATION,
        "expected_observation_ref": "observation_" + "o" * 24,
        "expected_page_revision": 1,
        "next_step_nonce": "n" * 24,
        "target_ref": "target_" + "t" * 24,
        "safe_summary": "activate the observed primary action",
    }
    values.update(updates)
    with pytest.raises(ValidationError, match=message):
        BrowserActionProposal.model_validate(values)


@pytest.mark.parametrize(
    "payload",
    [
        {"safeSummary": "token=super-sensitive-value"},
        {"safeSummary": "https://secret.example.test"},
        {"safeSummary": "x" * 70_000},
        {"safeSummary": "ok", "password": "value"},
    ],
)
def test_report_payload_rejects_secret_like_or_unbounded_values(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        build_browser_runtime_report(
            execution_contract_id=uuid7(),
            execution_contract_digest=DIGEST_A,
            report_id=uuid7(),
            sequence=1,
            kind=BrowserRuntimeReportKind.EXECUTION_STARTED,
            payload=payload,  # type: ignore[arg-type]
            occurred_at=datetime.now(UTC),
            previous_chain_digest=CHAIN_START_DIGEST,
        )


@pytest.mark.parametrize(
    ("kind", "key", "value"),
    [
        (BrowserRuntimeReportKind.ARTIFACT_CAPTURED, "artifactId", "not-a-uuid"),
        (BrowserRuntimeReportKind.ARTIFACT_CAPTURED, "kind", "ARBITRARY"),
        (BrowserRuntimeReportKind.ARTIFACT_CAPTURED, "integrity", "TRUST_ME"),
        (BrowserRuntimeReportKind.ASSERTION_EVALUATED, "assertionId", 42),
    ],
)
def test_evidence_reports_reject_untyped_identifiers_and_metadata(
    kind: BrowserRuntimeReportKind,
    key: str,
    value: object,
) -> None:
    payload = _payload(kind)
    payload[key] = value
    with pytest.raises(ValidationError):
        build_browser_runtime_report(
            execution_contract_id=uuid7(),
            execution_contract_digest=DIGEST_A,
            report_id=uuid7(),
            sequence=2,
            kind=kind,
            payload=payload,  # type: ignore[arg-type]
            occurred_at=datetime.now(UTC),
            previous_chain_digest=DIGEST_B,
        )


@pytest.mark.parametrize(
    "matched_rule",
    ["策略.允许", " leading-space", "rule with space", "r" * 161],
)
def test_policy_report_rejects_non_reference_matched_rules(
    matched_rule: str,
) -> None:
    payload = _payload(BrowserRuntimeReportKind.POLICY_DECIDED)
    payload["matchedRules"] = [matched_rule]

    with pytest.raises(ValidationError, match="matchedRules"):
        build_browser_runtime_report(
            execution_contract_id=uuid7(),
            execution_contract_digest=DIGEST_A,
            report_id=uuid7(),
            sequence=2,
            kind=BrowserRuntimeReportKind.POLICY_DECIDED,
            actor_slot="operator",
            action_id=uuid7(),
            payload=payload,  # type: ignore[arg-type]
            occurred_at=datetime.now(UTC),
            previous_chain_digest=DIGEST_B,
        )


@pytest.mark.parametrize(
    ("kind", "key", "value"),
    [
        (BrowserRuntimeReportKind.NODE_STARTED, "nodeId", "node\ncontrol"),
        (BrowserRuntimeReportKind.NODE_STARTED, "nodeKind", "节点"),
        (BrowserRuntimeReportKind.NODE_STARTED, "versionRef", "v" * 161),
        (
            BrowserRuntimeReportKind.OBSERVATION_CAPTURED,
            "observationRef",
            "observation_short",
        ),
        (BrowserRuntimeReportKind.OBSERVATION_CAPTURED, "pageRef", "page_short"),
        (BrowserRuntimeReportKind.OBSERVATION_CAPTURED, "routeKey", "bad route"),
        (BrowserRuntimeReportKind.ACTION_PROPOSED, "nodeId", "node\x00control"),
        (BrowserRuntimeReportKind.ACTION_PROPOSED, "targetRef", "target_short"),
        (BrowserRuntimeReportKind.ACTION_PROPOSED, "routeKey", "route 🚫"),
        (BrowserRuntimeReportKind.ACTION_EXECUTED, "receiptId", "not-a-uuid"),
        (BrowserRuntimeReportKind.ACTION_EXECUTED, "grantId", "not-a-uuid"),
        (BrowserRuntimeReportKind.ASSERTION_EVALUATED, "assertionId", "断言"),
        (BrowserRuntimeReportKind.NODE_COMPLETED, "nodeId", "node with spaces"),
        (BrowserRuntimeReportKind.EXECUTION_BLOCKED, "failureType", "bad\nvalue"),
    ],
)
def test_live_projected_report_fields_reject_unbounded_identifiers(
    kind: BrowserRuntimeReportKind,
    key: str,
    value: object,
) -> None:
    payload = _payload(kind)
    payload[key] = value
    is_action = kind in {
        BrowserRuntimeReportKind.ACTION_PROPOSED,
        BrowserRuntimeReportKind.POLICY_DECIDED,
        BrowserRuntimeReportKind.ACTION_EXECUTED,
    }
    actor_slot = (
        "operator"
        if is_action or kind is BrowserRuntimeReportKind.OBSERVATION_CAPTURED
        else None
    )

    with pytest.raises(ValidationError, match=key):
        build_browser_runtime_report(
            execution_contract_id=uuid7(),
            execution_contract_digest=DIGEST_A,
            report_id=uuid7(),
            sequence=2,
            kind=kind,
            actor_slot=actor_slot,
            action_id=uuid7() if is_action else None,
            payload=payload,  # type: ignore[arg-type]
            occurred_at=datetime.now(UTC),
            previous_chain_digest=DIGEST_B,
        )


def test_bundle_rejects_missing_fixture_export() -> None:
    bundle, _codec = _runtime(build_valid_graph(), build_intent_factory())
    with pytest.raises(ValidationError, match="fixture exports"):
        BrowserExecutionBundle(
            execution_contract=bundle.execution_contract,
            test_ir=bundle.test_ir,
            plan_template=bundle.plan_template,
            fixture_exports={},
            restore_envelopes=bundle.restore_envelopes,
            issued_at=bundle.issued_at,
        )
