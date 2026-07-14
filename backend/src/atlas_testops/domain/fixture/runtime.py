"""Runtime contracts for durable fixture execution and resource accounting."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from jsonschema import Draft202012Validator
from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel, WireModel
from atlas_testops.domain.fixture.models import (
    CleanupPolicy,
    CompiledFixturePlan,
    DataAtomContract,
    DataClassification,
    FixtureManifest,
    PortDirection,
    ResourceOwnership,
)

EXECUTION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$"
WORKER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$"
RESOURCE_HANDLE_PATTERN = r"^fr_[A-Za-z0-9_-]{16,128}$"
SAFE_ERROR_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{2,79}$"


class FixtureRunKind(StrEnum):
    """Why a fixture plan is being executed."""

    VALIDATION = "VALIDATION"
    EXECUTION = "EXECUTION"


class FixtureRunStatus(StrEnum):
    """Durable lifecycle of one fixture execution."""

    REQUESTED = "REQUESTED"
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    CLEANING = "CLEANING"
    RELEASED = "RELEASED"
    CLEANUP_FAILED = "CLEANUP_FAILED"


class FixtureRunTerminalIntent(StrEnum):
    """Terminal preparation outcome preserved while cleanup is in progress."""

    RELEASED = "RELEASED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class FixtureCleanupState(StrEnum):
    """Cleanup projection kept separate from the preparation result."""

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CLEANED = "CLEANED"
    LEAKED = "LEAKED"


class DataNodeRunStatus(StrEnum):
    """Logical node state independent of individual provider attempts."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"


class DataNodeAttemptStatus(StrEnum):
    """One real connector invocation attempt."""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"


class FixtureReconcileState(StrEnum):
    """Independent projection for resolving an uncertain create outcome."""

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    FOUND = "FOUND"
    ABSENT = "ABSENT"
    INCONCLUSIVE = "INCONCLUSIVE"
    EXHAUSTED = "EXHAUSTED"


class FixtureReconcileDisposition(StrEnum):
    """Explicit provider answer for one reviewed reconciliation query."""

    FOUND = "FOUND"
    ABSENT = "ABSENT"
    INCONCLUSIVE = "INCONCLUSIVE"


class DataNodeReconcileAttemptStatus(StrEnum):
    """Append-oriented outcome of one read-only reconcile operation."""

    RUNNING = "RUNNING"
    FOUND = "FOUND"
    ABSENT = "ABSENT"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"


class ResourceRecordStatus(StrEnum):
    """Resource ledger lifecycle used by cleanup and leak detection."""

    ACTIVE = "ACTIVE"
    CLEANUP_PENDING = "CLEANUP_PENDING"
    CLEANING = "CLEANING"
    CLEANED = "CLEANED"
    LEAKED = "LEAKED"
    BLOCKED_BY_CHILD = "BLOCKED_BY_CHILD"
    ORPHAN_SUSPECTED = "ORPHAN_SUSPECTED"


class ResourceCleanupAttemptStatus(StrEnum):
    """Durable outcome of one provider cleanup invocation."""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNCERTAIN = "OUTCOME_UNCERTAIN"


class FixtureFailureCategory(StrEnum):
    """Low-cardinality failure category safe for APIs and events."""

    VALIDATION = "VALIDATION"
    POLICY = "POLICY"
    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    TRANSIENT = "TRANSIENT"
    UNCERTAIN = "UNCERTAIN"
    CLEANUP = "CLEANUP"
    INFRASTRUCTURE = "INFRASTRUCTURE"


class ValidationEvidenceKind(StrEnum):
    """Independent runtime and cleanup proofs bound to frozen asset digests."""

    RUNTIME = "RUNTIME"
    CLEANUP = "CLEANUP"


class ValidationEvidenceSubject(StrEnum):
    """Version type proven by one validation run."""

    ATOM_VERSION = "ATOM_VERSION"
    BLUEPRINT_VERSION = "BLUEPRINT_VERSION"


class FixtureActorLeaseBinding(FrozenWireModel):
    """Caller-provided actor slot bound to an existing fenced account lease."""

    actor_slot: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{1,79}$")
    account_lease_id: UUID
    fencing_token: int = Field(ge=1)


class StartFixtureRun(WireModel):
    """Create one exact, input-frozen fixture execution."""

    run_kind: FixtureRunKind = FixtureRunKind.VALIDATION
    blueprint_version_id: UUID
    environment_id: UUID
    execution_id: str = Field(min_length=3, max_length=160, pattern=EXECUTION_ID_PATTERN)
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    actor_bindings: tuple[FixtureActorLeaseBinding, ...] = Field(min_length=1, max_length=32)
    execution_deadline: AwareDatetime

    @field_validator("actor_bindings")
    @classmethod
    def unique_actor_slots(
        cls,
        values: tuple[FixtureActorLeaseBinding, ...],
    ) -> tuple[FixtureActorLeaseBinding, ...]:
        slots = [item.actor_slot for item in values]
        lease_ids = [item.account_lease_id for item in values]
        if len(set(slots)) != len(slots):
            raise ValueError("actorBindings must use unique actor slots")
        if len(set(lease_ids)) != len(lease_ids):
            raise ValueError("one account lease cannot satisfy multiple actor slots")
        return tuple(sorted(values, key=lambda item: item.actor_slot))


class FixtureRun(FrozenWireModel):
    """Public safe projection of one durable fixture run."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    blueprint_version_id: UUID
    run_kind: FixtureRunKind
    execution_id: str
    plan_digest: str
    input_digest: str
    status: FixtureRunStatus
    cleanup_state: FixtureCleanupState
    terminal_intent: FixtureRunTerminalIntent | None = None
    temporal_workflow_id: str
    requested_by: UUID | None
    failure_category: FixtureFailureCategory | None = None
    failure_code: str | None = Field(default=None, pattern=SAFE_ERROR_CODE_PATTERN)
    failure_detail: str | None = Field(default=None, max_length=500)
    execution_deadline: AwareDatetime
    requested_at: AwareDatetime
    cancel_requested_at: AwareDatetime | None = None
    cancel_requested_by: UUID | None = None
    cleanup_generation: int = Field(default=0, ge=0, le=1_000)
    started_at: AwareDatetime | None = None
    ready_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    released_at: AwareDatetime | None = None
    revision: int = Field(ge=1)
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_failure_shape(self) -> Self:
        values = (self.failure_category, self.failure_code, self.failure_detail)
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError("fixture failure metadata must be complete")
        return self


class FixtureRunRecord(FixtureRun):
    """Internal run snapshot consumed only inside the Fixture Worker."""

    compiled_plan: CompiledFixturePlan = Field(repr=False)
    run_inputs: dict[str, JsonValue] = Field(repr=False)
    cleanup_policy: CleanupPolicy


class FixtureActorBinding(FrozenWireModel):
    """Frozen, safe actor slot binding visible in run diagnostics."""

    fixture_run_id: UUID
    actor_slot: str
    account_lease_id: UUID
    fencing_token: int = Field(ge=1)
    connector_installation_id: UUID
    bound_at: AwareDatetime


class FixtureActorBindingRecord(FixtureActorBinding):
    """Worker-only lease and connector projection."""

    account_handle: str = Field(repr=False)
    lease_worker_id: str
    lease_status: str
    lease_expires_at: AwareDatetime
    connector_adapter_key: str
    connector_configuration_ref: str = Field(repr=False)
    connector_status: str
    connector_revision: int = Field(ge=1)


class DataNodeRun(FrozenWireModel):
    """Public state for one logical node in a fixture run."""

    id: UUID
    fixture_run_id: UUID
    node_id: str
    atom_version_id: UUID
    actor_slot: str
    execution_level: int = Field(ge=0)
    status: DataNodeRunStatus
    attempt_count: int = Field(ge=0)
    reconcile_state: FixtureReconcileState = FixtureReconcileState.NOT_REQUIRED
    reconcile_attempt_count: int = Field(default=0, ge=0, le=32)
    next_reconcile_at: AwareDatetime | None = None
    output_digest: str | None = None
    failure_category: FixtureFailureCategory | None = None
    failure_code: str | None = Field(default=None, pattern=SAFE_ERROR_CODE_PATTERN)
    failure_detail: str | None = Field(default=None, max_length=500)
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    revision: int = Field(ge=1)
    updated_at: AwareDatetime


class DataNodeRunRecord(DataNodeRun):
    """Worker-only node snapshot including protected input and output values."""

    atom_id: UUID
    logical_idempotency_key: str = Field(repr=False)
    inputs: dict[str, JsonValue] | None = Field(default=None, repr=False)
    outputs: dict[str, JsonValue] | None = Field(default=None, repr=False)


class DataNodeAttempt(FrozenWireModel):
    """Append-oriented safe summary of one provider call."""

    id: UUID
    fixture_run_id: UUID
    data_node_run_id: UUID
    attempt_number: int = Field(ge=1, le=32)
    status: DataNodeAttemptStatus
    failure_category: FixtureFailureCategory | None = None
    failure_code: str | None = Field(default=None, pattern=SAFE_ERROR_CODE_PATTERN)
    failure_detail: str | None = Field(default=None, max_length=500)
    provider_request_id: str | None = Field(default=None, max_length=200)
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class DataNodeReconcileAttempt(FrozenWireModel):
    """Safe history for one exact reconciliation query."""

    id: UUID
    fixture_run_id: UUID
    data_node_run_id: UUID
    attempt_number: int = Field(ge=1, le=32)
    status: DataNodeReconcileAttemptStatus
    failure_category: FixtureFailureCategory | None = None
    failure_code: str | None = Field(default=None, pattern=SAFE_ERROR_CODE_PATTERN)
    failure_detail: str | None = Field(default=None, max_length=500)
    provider_request_id: str | None = Field(default=None, max_length=200)
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class ResourceRecord(FrozenWireModel):
    """Public ledger projection that never reveals the provider locator."""

    id: UUID
    fixture_run_id: UUID
    data_node_run_id: UUID
    connector_installation_id: UUID
    resource_handle: str = Field(pattern=RESOURCE_HANDLE_PATTERN)
    resource_type: str
    ownership: ResourceOwnership
    status: ResourceRecordStatus
    expires_at: AwareDatetime
    cleanup_generation: int = Field(ge=0)
    next_cleanup_at: AwareDatetime | None = None
    created_at: AwareDatetime
    cleaned_at: AwareDatetime | None = None
    revision: int = Field(ge=1)
    updated_at: AwareDatetime


class ResourceRecordInternal(ResourceRecord):
    """Worker-only resource locator used by reviewed cleanup operations."""

    data_node_attempt_id: UUID
    opaque_ref: str = Field(min_length=1, max_length=2_000, repr=False)
    cleanup_operation_key: str
    cleanup_operation_version: str


class ResourceCleanupAttempt(FrozenWireModel):
    """Safe append-only cleanup attempt without the provider locator."""

    id: UUID
    fixture_run_id: UUID
    resource_record_id: UUID
    cleanup_generation: int = Field(ge=1, le=1_000)
    status: ResourceCleanupAttemptStatus
    worker_identity: str = Field(min_length=3, max_length=160, pattern=WORKER_ID_PATTERN)
    failure_category: FixtureFailureCategory | None = None
    failure_code: str | None = Field(default=None, pattern=SAFE_ERROR_CODE_PATTERN)
    failure_detail: str | None = Field(default=None, max_length=500)
    provider_request_id: str | None = Field(default=None, max_length=200)
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    updated_at: AwareDatetime


class FixtureResourcePage(FrozenWireModel):
    items: tuple[ResourceRecord, ...]
    cleanup_attempts: tuple[ResourceCleanupAttempt, ...] = ()


class FixtureRunDetail(FrozenWireModel):
    run: FixtureRun
    actor_bindings: tuple[FixtureActorBinding, ...]
    nodes: tuple[DataNodeRun, ...]
    attempts: tuple[DataNodeAttempt, ...]
    reconcile_attempts: tuple[DataNodeReconcileAttempt, ...] = ()


class FixtureManifestRecord(FrozenWireModel):
    """Immutable manifest fact created only after every node succeeds."""

    fixture_run_id: UUID
    manifest: FixtureManifest
    manifest_digest: str
    created_at: AwareDatetime


class FixtureValidationEvidence(FrozenWireModel):
    """Append-only proof bound to an exact version digest and fixture run."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    fixture_run_id: UUID
    kind: ValidationEvidenceKind
    subject: ValidationEvidenceSubject
    subject_version_id: UUID
    subject_digest: str
    passed: bool
    safe_summary: str
    observed_at: AwareDatetime


class FixtureWorkerPlan(FrozenWireModel):
    """Minimal secret-free plan projection returned to Temporal history."""

    fixture_run_id: UUID
    execution_levels: tuple[tuple[str, ...], ...]
    cleanup_order: tuple[str, ...]
    execution_deadline: AwareDatetime


class FixtureNodeActivityResult(FrozenWireModel):
    """Small deterministic result returned from a node Activity."""

    node_id: str
    status: DataNodeRunStatus
    failure_category: FixtureFailureCategory | None = None
    failure_code: str | None = None


class FixtureReleaseResult(FrozenWireModel):
    """Safe summary returned after a normal release attempt."""

    fixture_run_id: UUID
    status: FixtureRunStatus
    cleanup_state: FixtureCleanupState
    cleaned_resources: int = Field(ge=0)
    leaked_resources: int = Field(ge=0)


class FixtureOperationResult(FrozenWireModel):
    """Structured provider result without raw request or response payloads."""

    outputs: dict[str, JsonValue]
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=200)


class FixtureReconcileResult(FrozenWireModel):
    """Explicit, schema-validated result of a read-only reconcile operation."""

    disposition: FixtureReconcileDisposition
    outputs: dict[str, JsonValue] = Field(default_factory=dict)
    provider_request_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_disposition_shape(self) -> Self:
        if self.disposition is FixtureReconcileDisposition.FOUND and not self.outputs:
            raise ValueError("FOUND reconcile result requires outputs")
        if self.disposition is not FixtureReconcileDisposition.FOUND and self.outputs:
            raise ValueError("only FOUND reconcile result may contain outputs")
        return self


class FixtureCleanupSweepBatch(FrozenWireModel):
    """Bounded, tenant-scoped result of one independent cleanup sweep."""

    reconciled_found: int = Field(ge=0)
    reconciled_absent: int = Field(ge=0)
    reconciled_inconclusive: int = Field(ge=0)
    cleanup_claimed: int = Field(ge=0)
    cleaned_resources: int = Field(ge=0)
    retry_scheduled: int = Field(ge=0)
    leaked_resources: int = Field(ge=0)
    finalized_runs: int = Field(ge=0)
    observed_at: AwareDatetime


def canonical_json_digest(value: object) -> str:
    """Digest a JSON-compatible value with stable key ordering."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def stable_node_idempotency_key(
    *,
    environment_id: UUID,
    blueprint_version_id: UUID,
    execution_id: str,
    node_id: str,
) -> str:
    """Create a stable provider key that does not change across attempts."""

    material = "\n".join((str(environment_id), str(blueprint_version_id), execution_id, node_id))
    return "fix_" + hashlib.sha256(material.encode()).hexdigest()


def validate_run_inputs(schema: dict[str, JsonValue], inputs: dict[str, JsonValue]) -> None:
    """Validate one run input object against the frozen blueprint schema."""

    errors = sorted(
        Draft202012Validator(schema).iter_errors(inputs),
        key=lambda item: list(item.path),
    )
    if errors:
        raise ValueError(f"run inputs do not match blueprint schema: {errors[0].message}")


def validate_operation_outputs(
    contract: DataAtomContract,
    outputs: dict[str, JsonValue],
) -> None:
    """Reject missing, extra, or schema-invalid provider output fields."""

    output_ports = {
        port.key: port for port in contract.ports if port.direction is PortDirection.OUTPUT
    }
    unknown = sorted(set(outputs) - set(output_ports))
    if unknown:
        raise ValueError(f"operation returned undeclared output ports: {', '.join(unknown)}")
    for key, port in output_ports.items():
        if key not in outputs:
            raise ValueError(f"operation did not return required output port: {key}")
        errors = list(Draft202012Validator(port.json_schema).iter_errors(outputs[key]))
        if errors:
            raise ValueError(
                f"operation output {key} failed schema validation: {errors[0].message}"
            )


def validate_operation_inputs(
    contract: DataAtomContract,
    inputs: dict[str, JsonValue],
) -> None:
    """Reject missing, extra, or schema-invalid provider input fields."""

    input_ports = {
        port.key: port for port in contract.ports if port.direction is PortDirection.INPUT
    }
    unknown = sorted(set(inputs) - set(input_ports))
    if unknown:
        raise ValueError(f"operation received undeclared input ports: {', '.join(unknown)}")
    for key, port in input_ports.items():
        if key not in inputs:
            if port.required:
                raise ValueError(f"operation did not receive required input port: {key}")
            continue
        errors = list(Draft202012Validator(port.json_schema).iter_errors(inputs[key]))
        if errors:
            raise ValueError(f"operation input {key} failed schema validation: {errors[0].message}")


def build_fixture_manifest(
    *,
    fixture_run_id: UUID,
    blueprint_version_id: UUID,
    plan: CompiledFixturePlan,
    node_outputs: dict[str, dict[str, JsonValue]],
) -> FixtureManifest:
    """Project only explicit, non-sensitive blueprint exports into a manifest."""

    exports: dict[str, JsonValue] = {}
    for item in plan.exports:
        if item.classification is DataClassification.SENSITIVE:
            raise ValueError("sensitive fixture values cannot enter a manifest")
        source = node_outputs.get(item.source_node_id)
        if source is None or item.source_port not in source:
            raise ValueError(f"manifest export source is unavailable: {item.name}")
        exports[item.name] = source[item.source_port]
    return FixtureManifest(
        fixture_run_id=fixture_run_id,
        blueprint_version_id=blueprint_version_id,
        plan_digest=plan.plan_digest,
        exports=exports,
    )


def pointer_value(document: JsonValue, pointer: str) -> JsonValue:
    """Resolve an RFC 6901 JSON Pointer without evaluating expressions."""

    current: JsonValue = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"run input pointer does not exist: {pointer}")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or int(token) >= len(current):
                raise ValueError(f"run input pointer does not exist: {pointer}")
            current = current[int(token)]
        else:
            raise ValueError(f"run input pointer does not exist: {pointer}")
    return current


def ensure_future_deadline(deadline: datetime, now: datetime) -> None:
    """Keep run deadlines finite and strictly in the future."""

    if deadline <= now:
        raise ValueError("executionDeadline must be in the future")
