"""CaseVersion digest, provenance, and immutability invariants."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_testops.domain.case import (
    CaseVersion,
    CaseVersionStatus,
    canonical_digest,
    canonical_workflow_graph,
    case_version_content_digest,
    case_version_ref,
    compile_case,
    semantic_digest,
)
from atlas_testops.domain.case import TestIntent as CaseIntent
from atlas_testops.domain.workflow import WorkflowGraph

CASE_ID = UUID("33333333-3333-4333-8333-333333333333")
DRAFT_ID = UUID("44444444-4444-4444-8444-444444444444")
RUN_ID = UUID("55555555-5555-4555-8555-555555555555")
EVIDENCE_ID = UUID("66666666-6666-4666-8666-666666666666")
AUTHOR_ID = UUID("77777777-7777-4777-8777-777777777777")
REVIEWER_ID = UUID("88888888-8888-4888-8888-888888888888")
EVIDENCE_DIGEST = f"sha256:{'e' * 64}"


def case_version_payload(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> dict[str, object]:
    intent = intent_factory()
    intent_version_ref = "intent.customer-filter@1.0.0"
    compilation = compile_case(
        test_case_id=CASE_ID,
        semantic_revision=7,
        intent_version_ref=intent_version_ref,
        intent=intent,
        graph=valid_graph,
    )
    assert compilation.test_ir is not None
    assert compilation.plan_template is not None
    assert compilation.compiled_digest is not None
    semantic_digest_value = semantic_digest(valid_graph, intent_version_ref)
    intent_digest = canonical_digest(intent)
    content_digest = case_version_content_digest(
        test_case_id=CASE_ID,
        version="1.2.0",
        source_draft_id=DRAFT_ID,
        semantic_revision=7,
        semantic_digest_value=semantic_digest_value,
        intent_version_ref=intent_version_ref,
        intent_digest=intent_digest,
        intent=intent,
        graph=valid_graph,
        test_ir=compilation.test_ir,
        plan_template=compilation.plan_template,
        compiled_digest=compilation.compiled_digest,
        debug_run_id=RUN_ID,
        evidence_manifest_id=EVIDENCE_ID,
        evidence_manifest_digest=EVIDENCE_DIGEST,
    )
    now = datetime.now(UTC)
    return {
        "id": "99999999-9999-4999-8999-999999999999",
        "tenantId": "11111111-1111-4111-8111-111111111111",
        "projectId": "22222222-2222-4222-8222-222222222222",
        "testCaseId": str(CASE_ID),
        "version": "1.2.0",
        "versionRef": case_version_ref(CASE_ID, "1.2.0"),
        "status": CaseVersionStatus.PUBLISHED,
        "sourceDraftId": str(DRAFT_ID),
        "semanticRevision": 7,
        "semanticDigest": semantic_digest_value,
        "intentVersionRef": intent_version_ref,
        "intentDigest": intent_digest,
        "intent": intent.model_dump(mode="json", by_alias=True),
        "graph": valid_graph.model_dump(mode="json", by_alias=True),
        "testIr": compilation.test_ir.model_dump(mode="json", by_alias=True),
        "testIrDigest": compilation.test_ir.content_digest,
        "planTemplate": compilation.plan_template.model_dump(mode="json", by_alias=True),
        "planDigest": compilation.plan_template.plan_digest,
        "compiledDigest": compilation.compiled_digest,
        "contentDigest": content_digest,
        "debugRunId": str(RUN_ID),
        "evidenceManifestId": str(EVIDENCE_ID),
        "evidenceManifestDigest": EVIDENCE_DIGEST,
        "authoredBy": str(AUTHOR_ID),
        "publishedBy": str(REVIEWER_ID),
        "reviewSummary": "Reviewed graph, Oracle coverage, and sealed trial evidence.",
        "publishedAt": now,
        "revision": 1,
        "createdAt": now,
        "updatedAt": now,
    }


def test_case_version_freezes_complete_deterministic_snapshot(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    payload = case_version_payload(valid_graph, intent_factory)
    version = CaseVersion.model_validate(payload)

    assert version.status is CaseVersionStatus.PUBLISHED
    assert version.content_digest == payload["contentDigest"]
    assert version.test_ir.workflow == canonical_workflow_graph(version.graph)


def test_case_version_rejects_content_digest_tampering(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    payload = case_version_payload(valid_graph, intent_factory)

    with pytest.raises(ValidationError, match="contentDigest"):
        CaseVersion.model_validate(
            {
                **payload,
                "contentDigest": f"sha256:{'0' * 64}",
            }
        )


def test_case_version_rejects_self_review(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    payload = case_version_payload(valid_graph, intent_factory)

    with pytest.raises(ValidationError, match="author and publisher"):
        CaseVersion.model_validate({**payload, "publishedBy": str(AUTHOR_ID)})


def test_case_version_rejects_inconsistent_nested_and_retirement_state(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    payload = case_version_payload(valid_graph, intent_factory)
    published_at = payload["publishedAt"]
    assert isinstance(published_at, datetime)
    retirement = {
        "retiredAt": published_at + timedelta(minutes=1),
        "retiredBy": str(REVIEWER_ID),
        "retirementReason": "Superseded by a reviewed replacement.",
    }
    test_ir = payload["testIr"]
    plan_template = payload["planTemplate"]
    assert isinstance(test_ir, dict)
    assert isinstance(plan_template, dict)
    invalid_projections = (
        (
            {"versionRef": case_version_ref(CASE_ID, "1.2.1")},
            "exact CaseVersion reference",
        ),
        ({"intentDigest": f"sha256:{'0' * 64}"}, "intentDigest"),
        ({"semanticDigest": f"sha256:{'0' * 64}"}, "semanticDigest"),
        (
            {"testIr": {**test_ir, "semanticRevision": 8}},
            "Test IR must match",
        ),
        (
            {"planTemplate": {**plan_template, "semanticRevision": 8}},
            "PlanTemplate must match",
        ),
        ({"compiledDigest": f"sha256:{'0' * 64}"}, "compiledDigest"),
        ({"status": CaseVersionStatus.RETIRED}, "complete retirement metadata"),
        ({"retiredAt": retirement["retiredAt"]}, "cannot contain retirement metadata"),
        (
            {
                "status": CaseVersionStatus.RETIRED,
                **retirement,
                "retiredAt": published_at - timedelta(minutes=1),
            },
            "cannot predate",
        ),
    )

    for mutation, message in invalid_projections:
        with pytest.raises(ValidationError, match=message):
            CaseVersion.model_validate({**payload, **mutation})
