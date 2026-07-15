"""DebugRun lifecycle, outcome, evidence, and staleness invariants."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_testops.domain.case import (
    DebugRun,
    DebugRunLifecycle,
    DebugRunOutcome,
    DebugRunSnapshotStatus,
    compile_case,
)
from atlas_testops.domain.case import TestIntent as CaseIntent
from atlas_testops.domain.workflow import WorkflowGraph

CASE_ID = UUID("33333333-3333-4333-8333-333333333333")
DIGEST = f"sha256:{'d' * 64}"


def debug_run_payload(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> dict[str, object]:
    compilation = compile_case(
        test_case_id=CASE_ID,
        semantic_revision=7,
        intent_version_ref="intent.customer-filter@1.0.0",
        intent=intent_factory(),
        graph=valid_graph,
    )
    assert compilation.test_ir is not None
    assert compilation.plan_template is not None
    assert compilation.compiled_digest is not None
    now = datetime.now(UTC)
    return {
        "id": "44444444-4444-4444-8444-444444444444",
        "tenantId": "11111111-1111-4111-8111-111111111111",
        "projectId": "22222222-2222-4222-8222-222222222222",
        "environmentId": "55555555-5555-4555-8555-555555555555",
        "testCaseId": str(CASE_ID),
        "draftId": "66666666-6666-4666-8666-666666666666",
        "semanticRevision": 7,
        "semanticDigest": DIGEST,
        "compiledDigest": compilation.compiled_digest,
        "testIr": compilation.test_ir.model_dump(mode="json", by_alias=True),
        "testIrDigest": compilation.test_ir.content_digest,
        "planTemplate": compilation.plan_template.model_dump(mode="json", by_alias=True),
        "planDigest": compilation.plan_template.plan_digest,
        "lifecycle": DebugRunLifecycle.CREATED,
        "outcome": DebugRunOutcome.NOT_SET,
        "snapshotStatus": DebugRunSnapshotStatus.CURRENT,
        "temporalWorkflowId": "atlas-debug/tenant/run",
        "requestedBy": "77777777-7777-4777-8777-777777777777",
        "executionDeadline": now + timedelta(minutes=10),
        "requestedAt": now,
        "revision": 1,
        "createdAt": now,
        "updatedAt": now,
    }


def test_pass_requires_terminated_lifecycle_and_sealed_evidence(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    payload = debug_run_payload(valid_graph, intent_factory)
    assert DebugRun.model_validate(payload).outcome is DebugRunOutcome.NOT_SET

    completed = {
        **payload,
        "lifecycle": DebugRunLifecycle.TERMINATED,
        "outcome": DebugRunOutcome.PASSED,
        "startedAt": payload["requestedAt"],
        "completedAt": payload["requestedAt"],
    }
    with pytest.raises(ValidationError, match="PASSED DebugRun requires sealed evidence"):
        DebugRun.model_validate(completed)

    sealed = DebugRun.model_validate(
        {
            **completed,
            "executionContractId": "99999999-9999-4999-8999-999999999999",
            "executionContractDigest": DIGEST,
            "evidenceManifestId": "88888888-8888-4888-8888-888888888888",
            "evidenceManifestDigest": DIGEST,
        }
    )
    assert sealed.outcome is DebugRunOutcome.PASSED


def test_outdated_and_cancel_projections_require_complete_audit_fields(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    payload = debug_run_payload(valid_graph, intent_factory)

    with pytest.raises(ValidationError, match="outdated DebugRun requires outdatedAt"):
        DebugRun.model_validate({**payload, "snapshotStatus": DebugRunSnapshotStatus.OUTDATED})
    with pytest.raises(
        ValidationError,
        match="cancel request timestamp and actor must be paired",
    ):
        DebugRun.model_validate({**payload, "cancelRequestedAt": payload["requestedAt"]})


def test_debug_run_rejects_every_inconsistent_runtime_projection(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    payload = debug_run_payload(valid_graph, intent_factory)
    contract = {
        "executionContractId": "99999999-9999-4999-8999-999999999999",
        "executionContractDigest": DIGEST,
    }
    evidence = {
        "evidenceManifestId": "88888888-8888-4888-8888-888888888888",
        "evidenceManifestDigest": DIGEST,
    }
    terminated = {
        "lifecycle": DebugRunLifecycle.TERMINATED,
        "outcome": DebugRunOutcome.FAILED,
        "startedAt": payload["requestedAt"],
        "completedAt": payload["requestedAt"],
    }
    invalid_projections = (
        ({"testIrDigest": DIGEST}, "Test IR"),
        ({"planDigest": DIGEST}, "PlanTemplate"),
        ({"compiledDigest": DIGEST}, "compiledDigest"),
        ({"outcome": DebugRunOutcome.FAILED}, "only a terminated"),
        (
            {
                "lifecycle": DebugRunLifecycle.TERMINATED,
                "outcome": DebugRunOutcome.FAILED,
                "startedAt": payload["requestedAt"],
            },
            "completedAt",
        ),
        (
            {"lifecycle": DebugRunLifecycle.FINALIZING},
            "requires startedAt",
        ),
        ({"outdatedAt": payload["requestedAt"]}, "current DebugRun"),
        (
            {"evidenceManifestId": evidence["evidenceManifestId"]},
            "evidence manifest reference",
        ),
        (
            {"executionContractId": contract["executionContractId"]},
            "execution contract reference",
        ),
        (
            {
                "lifecycle": DebugRunLifecycle.BINDING,
                "startedAt": payload["requestedAt"],
            },
            "active DebugRun",
        ),
        ({**contract}, "created DebugRun"),
        (
            {
                **terminated,
                "outcome": DebugRunOutcome.PASSED,
                **evidence,
            },
            "PASSED DebugRun requires an execution contract",
        ),
        (
            {
                "lifecycle": DebugRunLifecycle.FINALIZING,
                "startedAt": payload["requestedAt"],
                **contract,
                **evidence,
            },
            "only a terminated DebugRun",
        ),
        ({**terminated, "failureCode": "RUNTIME_FAILED"}, "must be paired"),
        (
            {
                "failureCode": "RUNTIME_FAILED",
                "failureDetail": "Runtime failed safely.",
            },
            "only valid while finalizing",
        ),
        (
            {
                **terminated,
                "outcome": DebugRunOutcome.PASSED,
                **contract,
                **evidence,
                "failureCode": "ORACLE_FAILED",
                "failureDetail": "Oracle failure cannot accompany a pass.",
            },
            "cannot include failure metadata",
        ),
    )

    for mutation, message in invalid_projections:
        with pytest.raises(ValidationError, match=message):
            DebugRun.model_validate({**payload, **mutation})
