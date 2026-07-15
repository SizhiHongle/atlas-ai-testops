"""Immutable CaseVersion publication contracts and digest rules."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import (
    DIGEST_PATTERN,
    PlanTemplate,
    SemanticVersion,
    TestIntent,
    TestIR,
    canonical_digest,
)
from atlas_testops.domain.case.patches import canonical_workflow_graph, semantic_digest
from atlas_testops.domain.workflow import ExactVersionRef, WorkflowGraph

CASE_VERSION_SCHEMA_VERSION: Literal["atlas.case-version/0.1"] = (
    "atlas.case-version/0.1"
)


class CaseVersionStatus(StrEnum):
    """Lifecycle of one immutable published case snapshot."""

    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class PublishCaseVersion(FrozenWireModel):
    """Reviewer command that publishes one exact current Draft snapshot."""

    client_mutation_id: str = Field(min_length=8, max_length=200)
    version: SemanticVersion
    base_semantic_revision: int = Field(ge=1)
    debug_run_id: UUID
    review_summary: str = Field(min_length=1, max_length=1_000)


def case_version_ref(test_case_id: UUID, version: str) -> str:
    """Build the only supported exact reference for a CaseVersion."""

    return f"test-case/{test_case_id}@{version}"


def case_version_content_digest(
    *,
    test_case_id: UUID,
    version: str,
    source_draft_id: UUID,
    semantic_revision: int,
    semantic_digest_value: str,
    intent_version_ref: str,
    intent_digest: str,
    intent: TestIntent,
    graph: WorkflowGraph,
    test_ir: TestIR,
    plan_template: PlanTemplate,
    compiled_digest: str,
    debug_run_id: UUID,
    evidence_manifest_id: UUID,
    evidence_manifest_digest: str,
) -> str:
    """Digest every frozen execution input and publication evidence reference."""

    body: dict[str, JsonValue] = {
        "schemaVersion": CASE_VERSION_SCHEMA_VERSION,
        "testCaseId": str(test_case_id),
        "version": version,
        "versionRef": case_version_ref(test_case_id, version),
        "sourceDraftId": str(source_draft_id),
        "semanticRevision": semantic_revision,
        "semanticDigest": semantic_digest_value,
        "intentVersionRef": intent_version_ref,
        "intentDigest": intent_digest,
        "intent": intent.model_dump(mode="json", by_alias=True),
        "workflow": canonical_workflow_graph(graph).model_dump(
            mode="json",
            by_alias=True,
        ),
        "testIr": test_ir.model_dump(mode="json", by_alias=True),
        "testIrDigest": test_ir.content_digest,
        "planTemplate": plan_template.model_dump(mode="json", by_alias=True),
        "planDigest": plan_template.plan_digest,
        "compiledDigest": compiled_digest,
        "debugRunId": str(debug_run_id),
        "evidenceManifestId": str(evidence_manifest_id),
        "evidenceManifestDigest": evidence_manifest_digest,
    }
    return canonical_digest(body)


class CaseVersion(FrozenWireModel):
    """Published TestCase snapshot consumed later only by exact ID."""

    schema_version: Literal["atlas.case-version/0.1"] = CASE_VERSION_SCHEMA_VERSION
    id: UUID
    tenant_id: UUID
    project_id: UUID
    test_case_id: UUID
    version: SemanticVersion
    version_ref: ExactVersionRef
    status: CaseVersionStatus
    source_draft_id: UUID
    semantic_revision: int = Field(ge=1)
    semantic_digest: str = Field(pattern=DIGEST_PATTERN)
    intent_version_ref: ExactVersionRef
    intent_digest: str = Field(pattern=DIGEST_PATTERN)
    intent: TestIntent
    graph: WorkflowGraph
    test_ir: TestIR
    test_ir_digest: str = Field(pattern=DIGEST_PATTERN)
    plan_template: PlanTemplate
    plan_digest: str = Field(pattern=DIGEST_PATTERN)
    compiled_digest: str = Field(pattern=DIGEST_PATTERN)
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    debug_run_id: UUID
    evidence_manifest_id: UUID
    evidence_manifest_digest: str = Field(pattern=DIGEST_PATTERN)
    authored_by: UUID
    published_by: UUID
    review_summary: str = Field(min_length=1, max_length=1_000)
    published_at: AwareDatetime
    retired_at: AwareDatetime | None = None
    retired_by: UUID | None = None
    retirement_reason: str | None = Field(default=None, min_length=1, max_length=500)
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_frozen_snapshot(self) -> Self:
        """Reject projections whose nested snapshots or digests disagree."""

        normalized_graph = canonical_workflow_graph(self.graph)
        if self.version_ref != case_version_ref(self.test_case_id, self.version):
            raise ValueError("versionRef must be the exact CaseVersion reference")
        if self.authored_by == self.published_by:
            raise ValueError("CaseVersion author and publisher must be different actors")
        if canonical_digest(self.intent) != self.intent_digest:
            raise ValueError("intentDigest must match the frozen Test Intent")
        if semantic_digest(normalized_graph, self.intent_version_ref) != self.semantic_digest:
            raise ValueError("semanticDigest must match the frozen graph and intent reference")
        if (
            self.test_ir.test_case_id != self.test_case_id
            or self.test_ir.semantic_revision != self.semantic_revision
            or self.test_ir.intent_version_ref != self.intent_version_ref
            or self.test_ir.workflow != normalized_graph
            or self.test_ir.content_digest != self.test_ir_digest
        ):
            raise ValueError("Test IR must match the frozen CaseVersion semantics")
        if (
            self.plan_template.test_case_id != self.test_case_id
            or self.plan_template.semantic_revision != self.semantic_revision
            or self.plan_template.test_ir_digest != self.test_ir_digest
            or self.plan_template.plan_digest != self.plan_digest
        ):
            raise ValueError("PlanTemplate must match the frozen CaseVersion Test IR")
        expected_compiled_digest = canonical_digest(
            {
                "testIrDigest": self.test_ir_digest,
                "planDigest": self.plan_digest,
            }
        )
        if self.compiled_digest != expected_compiled_digest:
            raise ValueError("compiledDigest must match Test IR and PlanTemplate")
        expected_content_digest = case_version_content_digest(
            test_case_id=self.test_case_id,
            version=self.version,
            source_draft_id=self.source_draft_id,
            semantic_revision=self.semantic_revision,
            semantic_digest_value=self.semantic_digest,
            intent_version_ref=self.intent_version_ref,
            intent_digest=self.intent_digest,
            intent=self.intent,
            graph=normalized_graph,
            test_ir=self.test_ir,
            plan_template=self.plan_template,
            compiled_digest=self.compiled_digest,
            debug_run_id=self.debug_run_id,
            evidence_manifest_id=self.evidence_manifest_id,
            evidence_manifest_digest=self.evidence_manifest_digest,
        )
        if self.content_digest != expected_content_digest:
            raise ValueError("contentDigest must match the complete frozen CaseVersion")
        retired = self.status is CaseVersionStatus.RETIRED
        retirement_fields = (
            self.retired_at,
            self.retired_by,
            self.retirement_reason,
        )
        if retired != all(value is not None for value in retirement_fields):
            raise ValueError("retired CaseVersion requires complete retirement metadata")
        if not retired and any(value is not None for value in retirement_fields):
            raise ValueError("published CaseVersion cannot contain retirement metadata")
        if self.retired_at is not None and self.retired_at < self.published_at:
            raise ValueError("retiredAt cannot predate publishedAt")
        return self


class CaseVersionPage(FrozenWireModel):
    """Cursor page of immutable CaseVersion history."""

    items: tuple[CaseVersion, ...]
    next_cursor: str | None = None
