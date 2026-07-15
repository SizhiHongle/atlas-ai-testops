"""Fail-closed Browser report transition checks in the control plane."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid7

import pytest
from tests.domain.runtime.test_browser_protocol import DIGEST_A, _payload

from atlas_testops.application.debug_runtime import DebugRuntimeService
from atlas_testops.core.errors import ApplicationError
from atlas_testops.domain.runtime import (
    CHAIN_START_DIGEST,
    AppendBrowserRuntimeReport,
    BrowserRuntimeReportKind,
    build_browser_runtime_report,
)
from atlas_testops.infrastructure.database import Database


def _report(
    kind: BrowserRuntimeReportKind,
    *,
    sequence: int,
    previous_chain_digest: str,
    action_id: UUID | None = None,
    payload_updates: dict[str, object] | None = None,
) -> AppendBrowserRuntimeReport:
    payload = _payload(kind)
    if payload_updates is not None:
        payload.update(payload_updates)
    return build_browser_runtime_report(
        execution_contract_id=UUID("70000000-0000-4000-8000-000000000001"),
        execution_contract_digest=DIGEST_A,
        report_id=uuid7(),
        sequence=sequence,
        kind=kind,
        payload=payload,  # type: ignore[arg-type]
        occurred_at=datetime.now(UTC) + timedelta(milliseconds=sequence),
        previous_chain_digest=previous_chain_digest,
        actor_slot=(
            "operator"
            if kind
            in {
                BrowserRuntimeReportKind.OBSERVATION_CAPTURED,
                BrowserRuntimeReportKind.ACTION_PROPOSED,
                BrowserRuntimeReportKind.POLICY_DECIDED,
                BrowserRuntimeReportKind.ACTION_EXECUTED,
            }
            else None
        ),
        action_id=(
            action_id
            if kind
            in {
                BrowserRuntimeReportKind.ACTION_PROPOSED,
                BrowserRuntimeReportKind.POLICY_DECIDED,
                BrowserRuntimeReportKind.ACTION_EXECUTED,
            }
            else None
        ),
    )


def _service() -> DebugRuntimeService:
    return DebugRuntimeService(cast(Database, object()))


def test_action_proposal_requires_immediate_policy_decision() -> None:
    action_id = uuid7()
    proposal = _report(
        BrowserRuntimeReportKind.ACTION_PROPOSED,
        sequence=2,
        previous_chain_digest=CHAIN_START_DIGEST,
        action_id=action_id,
    )
    observation = _report(
        BrowserRuntimeReportKind.OBSERVATION_CAPTURED,
        sequence=3,
        previous_chain_digest=proposal.chain_digest,
    )

    with pytest.raises(ApplicationError):
        _service()._require_action_report_transition(
            proposal,
            observation,
            action_proposal=None,
        )


def test_action_receipt_requires_matching_allow_and_proposal() -> None:
    action_id = uuid7()
    proposal = _report(
        BrowserRuntimeReportKind.ACTION_PROPOSED,
        sequence=2,
        previous_chain_digest=CHAIN_START_DIGEST,
        action_id=action_id,
    )
    policy = _report(
        BrowserRuntimeReportKind.POLICY_DECIDED,
        sequence=3,
        previous_chain_digest=proposal.chain_digest,
        action_id=action_id,
    )
    receipt = _report(
        BrowserRuntimeReportKind.ACTION_EXECUTED,
        sequence=4,
        previous_chain_digest=policy.chain_digest,
        action_id=uuid7(),
    )

    with pytest.raises(ApplicationError):
        _service()._require_action_report_transition(
            policy,
            receipt,
            action_proposal=proposal,
        )


def test_action_receipt_accepts_exact_proposal_and_allow() -> None:
    action_id = uuid7()
    proposal = _report(
        BrowserRuntimeReportKind.ACTION_PROPOSED,
        sequence=2,
        previous_chain_digest=CHAIN_START_DIGEST,
        action_id=action_id,
    )
    policy = _report(
        BrowserRuntimeReportKind.POLICY_DECIDED,
        sequence=3,
        previous_chain_digest=proposal.chain_digest,
        action_id=action_id,
    )
    receipt = _report(
        BrowserRuntimeReportKind.ACTION_EXECUTED,
        sequence=4,
        previous_chain_digest=policy.chain_digest,
        action_id=action_id,
    )

    _service()._require_action_report_transition(
        policy,
        receipt,
        action_proposal=proposal,
    )


def test_policy_can_fail_closed_to_blocked_without_fabricating_receipt() -> None:
    action_id = uuid7()
    denied = _report(
        BrowserRuntimeReportKind.POLICY_DECIDED,
        sequence=3,
        previous_chain_digest=CHAIN_START_DIGEST,
        action_id=action_id,
        payload_updates={"decision": "DENY"},
    )
    allowed = _report(
        BrowserRuntimeReportKind.POLICY_DECIDED,
        sequence=3,
        previous_chain_digest=CHAIN_START_DIGEST,
        action_id=action_id,
    )
    blocked = _report(
        BrowserRuntimeReportKind.EXECUTION_BLOCKED,
        sequence=4,
        previous_chain_digest=denied.chain_digest,
    )

    _service()._require_action_report_transition(
        denied,
        blocked,
        action_proposal=None,
    )
    _service()._require_action_report_transition(
        allowed,
        blocked,
        action_proposal=None,
    )
