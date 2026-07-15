"""Immutable DebugRun snapshot and control-plane wire contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import (
    DIGEST_PATTERN,
    PlanTemplate,
    TestIR,
    canonical_digest,
)

SAFE_ERROR_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{2,79}$"


class DebugRunLifecycle(StrEnum):
    """Durable runtime lifecycle kept separate from the test outcome."""

    CREATED = "CREATED"
    BINDING = "BINDING"
    READY = "READY"
    RUNNING = "RUNNING"
    FINALIZING = "FINALIZING"
    TERMINATED = "TERMINATED"


class DebugRunOutcome(StrEnum):
    """Independent Oracle-owned result classification."""

    NOT_SET = "NOT_SET"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INFRA_ERROR = "INFRA_ERROR"
    CANCELED = "CANCELED"


class DebugRunSnapshotStatus(StrEnum):
    """Whether the frozen run still matches the current draft semantics."""

    CURRENT = "CURRENT"
    OUTDATED = "OUTDATED"


class StartDebugRun(FrozenWireModel):
    """Request one bounded run of an exact WorkflowDraft semantic revision."""

    environment_id: UUID
    base_semantic_revision: int = Field(ge=1)
    execution_deadline: AwareDatetime


class RequestDebugRunCancel(FrozenWireModel):
    """Idempotent, auditable request to stop one durable DebugRun."""

    client_mutation_id: str = Field(min_length=8, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class DebugRun(FrozenWireModel):
    """Safe projection containing one immutable compiled Draft snapshot."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    test_case_id: UUID
    draft_id: UUID
    semantic_revision: int = Field(ge=1)
    semantic_digest: str = Field(pattern=DIGEST_PATTERN)
    compiled_digest: str = Field(pattern=DIGEST_PATTERN)
    test_ir: TestIR
    test_ir_digest: str = Field(pattern=DIGEST_PATTERN)
    plan_template: PlanTemplate
    plan_digest: str = Field(pattern=DIGEST_PATTERN)
    lifecycle: DebugRunLifecycle
    outcome: DebugRunOutcome
    snapshot_status: DebugRunSnapshotStatus
    temporal_workflow_id: str = Field(
        min_length=20,
        max_length=320,
        pattern=r"^atlas-debug/[A-Za-z0-9/_-]+$",
    )
    requested_by: UUID | None
    execution_deadline: AwareDatetime
    execution_contract_id: UUID | None = None
    execution_contract_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    evidence_manifest_id: UUID | None = None
    evidence_manifest_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    failure_code: str | None = Field(default=None, pattern=SAFE_ERROR_CODE_PATTERN)
    failure_detail: str | None = Field(default=None, max_length=500)
    cancel_requested_at: AwareDatetime | None = None
    cancel_requested_by: UUID | None = None
    requested_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    outdated_at: AwareDatetime | None = None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_state_shape(self) -> Self:
        """Keep lifecycle, outcome, evidence, and staleness orthogonal."""

        if (
            self.test_ir.test_case_id != self.test_case_id
            or self.test_ir.semantic_revision != self.semantic_revision
            or self.test_ir.content_digest != self.test_ir_digest
        ):
            raise ValueError("Test IR must match the frozen DebugRun snapshot")
        if (
            self.plan_template.test_case_id != self.test_case_id
            or self.plan_template.semantic_revision != self.semantic_revision
            or self.plan_template.test_ir_digest != self.test_ir_digest
            or self.plan_template.plan_digest != self.plan_digest
        ):
            raise ValueError("PlanTemplate must match the frozen DebugRun snapshot")
        expected_compiled_digest = canonical_digest(
            {
                "testIrDigest": self.test_ir_digest,
                "planDigest": self.plan_digest,
            }
        )
        if self.compiled_digest != expected_compiled_digest:
            raise ValueError("compiledDigest must match Test IR and PlanTemplate")
        terminated = self.lifecycle is DebugRunLifecycle.TERMINATED
        if terminated != (self.outcome is not DebugRunOutcome.NOT_SET):
            raise ValueError("only a terminated DebugRun can have an outcome")
        if terminated != (self.completed_at is not None):
            raise ValueError("terminated DebugRun requires completedAt")
        started = self.started_at is not None
        if (self.lifecycle is DebugRunLifecycle.CREATED) == started:
            raise ValueError("non-created DebugRun requires startedAt")
        if self.snapshot_status is DebugRunSnapshotStatus.OUTDATED:
            if self.outdated_at is None:
                raise ValueError("outdated DebugRun requires outdatedAt")
        elif self.outdated_at is not None:
            raise ValueError("current DebugRun cannot have outdatedAt")
        evidence_values = (self.evidence_manifest_id, self.evidence_manifest_digest)
        if any(value is not None for value in evidence_values) and not all(
            value is not None for value in evidence_values
        ):
            raise ValueError("evidence manifest reference must be complete")
        if self.outcome is DebugRunOutcome.PASSED and not all(
            value is not None for value in evidence_values
        ):
            raise ValueError("PASSED DebugRun requires sealed evidence")
        contract_values = (
            self.execution_contract_id,
            self.execution_contract_digest,
        )
        if any(value is not None for value in contract_values) and not all(
            value is not None for value in contract_values
        ):
            raise ValueError("execution contract reference must be complete")
        if self.lifecycle in {
            DebugRunLifecycle.BINDING,
            DebugRunLifecycle.READY,
            DebugRunLifecycle.RUNNING,
        } and not all(value is not None for value in contract_values):
            raise ValueError("active DebugRun requires an execution contract")
        if self.lifecycle is DebugRunLifecycle.CREATED and any(
            value is not None for value in contract_values
        ):
            raise ValueError("created DebugRun cannot already reference a contract")
        if self.outcome is DebugRunOutcome.PASSED and not all(
            value is not None for value in contract_values
        ):
            raise ValueError("PASSED DebugRun requires an execution contract")
        if any(value is not None for value in evidence_values) and not terminated:
            raise ValueError("only a terminated DebugRun can reference sealed evidence")
        failure_values = (self.failure_code, self.failure_detail)
        if any(value is not None for value in failure_values) and not all(
            value is not None for value in failure_values
        ):
            raise ValueError("failure code and detail must be paired")
        if any(value is not None for value in failure_values) and self.lifecycle not in {
            DebugRunLifecycle.FINALIZING,
            DebugRunLifecycle.TERMINATED,
        }:
            raise ValueError("failure metadata is only valid while finalizing")
        if self.outcome is DebugRunOutcome.PASSED and any(
            value is not None for value in failure_values
        ):
            raise ValueError("PASSED DebugRun cannot include failure metadata")
        if (self.cancel_requested_at is None) != (self.cancel_requested_by is None):
            raise ValueError("cancel request timestamp and actor must be paired")
        return self


class DebugRunEvent(FrozenWireModel):
    """Append-only, monotonic event projection for reliable UI replay."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    test_case_id: UUID
    debug_run_id: UUID
    seq: int = Field(ge=1)
    event_type: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z][a-z0-9_.-]+$",
    )
    lifecycle: DebugRunLifecycle
    outcome: DebugRunOutcome
    snapshot_status: DebugRunSnapshotStatus
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: AwareDatetime


class DebugRunPage(FrozenWireModel):
    """Cursor page of DebugRuns for one TestCase."""

    items: tuple[DebugRun, ...]
    next_cursor: str | None = None


class DebugRunEventPage(FrozenWireModel):
    """Monotonic event page resumed with afterSeq."""

    items: tuple[DebugRunEvent, ...]
    next_after_seq: int | None = Field(default=None, ge=1)
