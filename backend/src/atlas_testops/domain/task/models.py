"""Immutable Task execution hierarchy and manifest contracts."""

from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum
from math import isfinite
from re import fullmatch
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import (
    DIGEST_PATTERN,
    SemanticVersion,
    canonical_digest,
)

TASK_PLAN_SCHEMA_VERSION: Literal["atlas.task-plan/0.1"] = "atlas.task-plan/0.1"
TASK_RUN_MANIFEST_SCHEMA_VERSION: Literal["atlas.task-run-manifest/0.1"] = (
    "atlas.task-run-manifest/0.1"
)
TASK_EXECUTION_EVENT_SCHEMA_VERSION: Literal["atlas.execution-event/0.1"] = (
    "atlas.execution-event/0.1"
)
TASK_RUN_REQUEST_SCHEMA_VERSION: Literal["atlas.task-run-request/0.1"] = (
    "atlas.task-run-request/0.1"
)

TASK_KEY_PATTERN = r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,7}$"
REFERENCE_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@/+=-]{2,319}$"
POLICY_KEY_PATTERN = r"^[a-z][a-z0-9_.-]{1,127}$"
TEMPORAL_WORKFLOW_ID_PATTERN = r"^atlas-task/[A-Za-z0-9/_-]+$"
TEMPORAL_NAMESPACE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9_.-]+$"
TASK_EXECUTION_EVENT_PAYLOAD_MAX_BYTES = 32_768

PolicyKey = Annotated[str, StringConstraints(pattern=POLICY_KEY_PATTERN)]
Sha256Digest = Annotated[str, StringConstraints(pattern=DIGEST_PATTERN)]


def _postgres_jsonb_text(value: JsonValue) -> str:
    """Render the PostgreSQL jsonb text form used by the payload CHECK."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("PostgreSQL jsonb strings cannot contain U+0000")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("PostgreSQL jsonb strings must be valid UTF-8") from error
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("PostgreSQL jsonb numbers must be finite")
        # PostgreSQL stores JSON numbers as numeric and expands exponent notation.
        # Decimal(str(...)) mirrors that representation. It only overcounts -0.0
        # by one byte, which keeps the size guard conservative.
        return format(Decimal(str(value)), "f")
    if isinstance(value, list):
        return "[" + ", ".join(_postgres_jsonb_text(item) for item in value) + "]"
    if isinstance(value, dict):
        rendered_items = (
            f"{_postgres_jsonb_text(key)}: {_postgres_jsonb_text(item)}"
            for key, item in value.items()
        )
        return "{" + ", ".join(rendered_items) + "}"
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


class TaskPlanStatus(StrEnum):
    """Lifecycle of a stable TaskPlan identity."""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class TaskTriggerSource(StrEnum):
    """Bounded trigger origins used in TaskRun idempotency scope."""

    MANUAL = "MANUAL"
    SCHEDULE = "SCHEDULE"
    CI = "CI"
    WEBHOOK = "WEBHOOK"
    API = "API"


class TaskMaterializationState(StrEnum):
    """Completeness gate for a TaskRun aggregate before dispatch."""

    MATERIALIZING = "MATERIALIZING"
    SEALED = "SEALED"


class ExecutionLifecycle(StrEnum):
    """Shared TaskRun, ExecutionUnit, and UnitAttempt lifecycle axis."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    CANCELING = "CANCELING"
    FINALIZING = "FINALIZING"
    CLOSED = "CLOSED"


class ExecutionQuality(StrEnum):
    """Shared Oracle-owned result axis independent of lifecycle."""

    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"
    INFRA_ERROR = "INFRA_ERROR"
    CANCELED = "CANCELED"


class ExecutionHygiene(StrEnum):
    """Shared resource-cleanup axis independent of result quality."""

    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    CLEANED = "CLEANED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    LEAKED = "LEAKED"


class TaskPlan(FrozenWireModel):
    """Stable identity for a versioned reusable task plan."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_key: str = Field(min_length=3, max_length=160, pattern=TASK_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    status: TaskPlanStatus
    created_by: UUID
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        """Reject projections whose update time predates creation."""

        if self.updated_at < self.created_at:
            raise ValueError("updatedAt cannot predate createdAt")
        return self


class TaskMatrixDefinition(FrozenWireModel):
    """Explicit V1 matrix axes without executable expressions."""

    environment_ids: tuple[UUID, ...] = Field(min_length=1, max_length=1_000)
    browser_profile_version_ids: tuple[UUID, ...] = Field(
        min_length=1,
        max_length=1_000,
    )
    identity_profile_version_ids: tuple[UUID, ...] = Field(
        min_length=1,
        max_length=1_000,
    )
    data_profile_version_ids: tuple[UUID, ...] = Field(
        min_length=1,
        max_length=1_000,
    )

    @field_validator(
        "environment_ids",
        "browser_profile_version_ids",
        "identity_profile_version_ids",
        "data_profile_version_ids",
    )
    @classmethod
    def normalize_axis(cls, values: tuple[UUID, ...]) -> tuple[UUID, ...]:
        """Canonicalize every matrix axis to a deterministic UUID order."""

        return tuple(sorted(set(values), key=str))


class CaseExecutionProfileRef(FrozenWireModel):
    """Typed immutable reference slots for one pinned CaseVersion."""

    case_version_id: UUID
    execution_profile_version_id: UUID
    fixture_blueprint_version_id: UUID


class TaskProfileRefs(FrozenWireModel):
    """Structured per-case profiles with no user-provided code or expressions."""

    case_profiles: tuple[CaseExecutionProfileRef, ...] = Field(
        min_length=1,
        max_length=100_000,
    )

    @field_validator("case_profiles")
    @classmethod
    def normalize_case_profiles(
        cls,
        values: tuple[CaseExecutionProfileRef, ...],
    ) -> tuple[CaseExecutionProfileRef, ...]:
        """Require one profile binding per CaseVersion and sort it canonically."""

        case_ids = [item.case_version_id for item in values]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("profileRefs must bind each CaseVersion exactly once")
        return tuple(sorted(values, key=lambda item: str(item.case_version_id)))


def task_plan_version_ref(task_plan_id: UUID, version: str) -> str:
    """Build the exact reference for one immutable TaskPlanVersion."""

    return f"task-plan/{task_plan_id}@{version}"


def task_run_workflow_id(*, tenant_id: UUID, task_run_id: UUID) -> str:
    """Derive the namespace-global Temporal identity for one TaskRun."""

    return f"atlas-task/run/{tenant_id.hex}/{task_run_id.hex}"


def unit_attempt_workflow_id(*, tenant_id: UUID, unit_attempt_id: UUID) -> str:
    """Derive the namespace-global Temporal identity for one UnitAttempt."""

    return f"atlas-task/attempt/{tenant_id.hex}/{unit_attempt_id.hex}"


def task_plan_version_content_digest(
    *,
    tenant_id: UUID,
    project_id: UUID,
    task_plan_id: UUID,
    version: str,
    pinned_case_version_ids: tuple[UUID, ...],
    matrix: TaskMatrixDefinition,
    profile_refs: TaskProfileRefs,
    policy_digests: dict[str, str],
) -> str:
    """Digest every published TaskPlanVersion execution input."""

    body: dict[str, JsonValue] = {
        "schemaVersion": TASK_PLAN_SCHEMA_VERSION,
        "tenantId": str(tenant_id),
        "projectId": str(project_id),
        "taskPlanId": str(task_plan_id),
        "version": version,
        "versionRef": task_plan_version_ref(task_plan_id, version),
        "pinnedCaseVersionIds": [
            str(value) for value in sorted(set(pinned_case_version_ids), key=str)
        ],
        "matrix": matrix.model_dump(mode="json", by_alias=True),
        "profileRefs": profile_refs.model_dump(mode="json", by_alias=True),
        "policyDigests": dict(sorted(policy_digests.items())),
    }
    return canonical_digest(body)


class TaskPlanVersion(FrozenWireModel):
    """Published immutable TaskPlan snapshot consumed only by exact ID."""

    schema_version: Literal["atlas.task-plan/0.1"] = TASK_PLAN_SCHEMA_VERSION
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_plan_id: UUID
    version: SemanticVersion
    version_ref: str = Field(min_length=1, max_length=256)
    pinned_case_version_ids: tuple[UUID, ...] = Field(
        min_length=1,
        max_length=100_000,
    )
    matrix: TaskMatrixDefinition
    profile_refs: TaskProfileRefs
    policy_digests: dict[PolicyKey, Sha256Digest] = Field(
        min_length=1,
        max_length=64,
        json_schema_extra={"additionalProperties": False},
    )
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    published_by: UUID
    published_at: AwareDatetime
    revision: Literal[1]
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("pinned_case_version_ids")
    @classmethod
    def normalize_pinned_cases(cls, values: tuple[UUID, ...]) -> tuple[UUID, ...]:
        """Canonicalize the pinned CaseVersion set."""

        return tuple(sorted(set(values), key=str))

    @field_validator("policy_digests")
    @classmethod
    def normalize_policy_digests(cls, values: dict[str, str]) -> dict[str, str]:
        """Validate safe policy names and canonical SHA-256 values."""

        for key, value in values.items():
            if fullmatch(POLICY_KEY_PATTERN, key) is None:
                raise ValueError("policyDigests contains an invalid policy key")
            if fullmatch(DIGEST_PATTERN, value) is None:
                raise ValueError("policyDigests values must be SHA-256 digests")
        return dict(sorted(values.items()))

    @model_validator(mode="after")
    def validate_published_snapshot(self) -> Self:
        """Keep profile bindings, exact reference, digest, and times consistent."""

        if self.version_ref != task_plan_version_ref(self.task_plan_id, self.version):
            raise ValueError("versionRef must be the exact TaskPlanVersion reference")
        profile_case_ids = tuple(item.case_version_id for item in self.profile_refs.case_profiles)
        if profile_case_ids != self.pinned_case_version_ids:
            raise ValueError("profileRefs must match pinnedCaseVersionIds exactly")
        expected_digest = task_plan_version_content_digest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            task_plan_id=self.task_plan_id,
            version=self.version,
            pinned_case_version_ids=self.pinned_case_version_ids,
            matrix=self.matrix,
            profile_refs=self.profile_refs,
            policy_digests=self.policy_digests,
        )
        if self.content_digest != expected_digest:
            raise ValueError("contentDigest must match the published TaskPlanVersion")
        if not self.created_at <= self.published_at <= self.updated_at:
            raise ValueError("publishedAt must be between createdAt and updatedAt")
        return self


def execution_unit_key(
    *,
    case_version_id: UUID,
    environment_id: UUID,
    browser_profile_version_id: UUID,
    identity_profile_version_id: UUID,
    data_profile_version_id: UUID,
    parameter_digest: str,
) -> str:
    """Derive a stable logical Unit key from the documented matrix identity."""

    return canonical_digest(
        {
            "caseVersionId": str(case_version_id),
            "environmentId": str(environment_id),
            "browserProfileVersionId": str(browser_profile_version_id),
            "identityProfileVersionId": str(identity_profile_version_id),
            "dataProfileVersionId": str(data_profile_version_id),
            "parameterDigest": parameter_digest,
        }
    )


def execution_unit_dependency_digest(
    *,
    case_version_id: UUID,
    execution_profile_version_id: UUID,
    fixture_blueprint_version_id: UUID,
    identity_profile_version_id: UUID,
    environment_id: UUID,
    browser_profile_version_id: UUID,
    data_profile_version_id: UUID,
) -> str:
    """Digest every pinned dependency reference bound to one Unit."""

    return canonical_digest(
        {
            "caseVersionId": str(case_version_id),
            "executionProfileVersionId": str(execution_profile_version_id),
            "fixtureBlueprintVersionId": str(fixture_blueprint_version_id),
            "identityProfileVersionId": str(identity_profile_version_id),
            "environmentId": str(environment_id),
            "browserProfileVersionId": str(browser_profile_version_id),
            "dataProfileVersionId": str(data_profile_version_id),
        }
    )


class ExecutionUnitManifest(FrozenWireModel):
    """One exact CaseVersion and matrix cell frozen into a Run Manifest."""

    ordinal: int = Field(ge=1)
    unit_key: str = Field(pattern=DIGEST_PATTERN)
    case_version_id: UUID
    execution_profile_version_id: UUID
    fixture_blueprint_version_id: UUID
    identity_profile_version_id: UUID
    environment_id: UUID
    browser_profile_version_id: UUID
    data_profile_version_id: UUID
    parameter_digest: str = Field(pattern=DIGEST_PATTERN)
    dependency_digest: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_derived_digests(self) -> Self:
        """Reject a Unit whose logical key or dependency digest was tampered with."""

        expected_key = execution_unit_key(
            case_version_id=self.case_version_id,
            environment_id=self.environment_id,
            browser_profile_version_id=self.browser_profile_version_id,
            identity_profile_version_id=self.identity_profile_version_id,
            data_profile_version_id=self.data_profile_version_id,
            parameter_digest=self.parameter_digest,
        )
        if self.unit_key != expected_key:
            raise ValueError("unitKey must match the frozen matrix identity")
        expected_dependency_digest = execution_unit_dependency_digest(
            case_version_id=self.case_version_id,
            execution_profile_version_id=self.execution_profile_version_id,
            fixture_blueprint_version_id=self.fixture_blueprint_version_id,
            identity_profile_version_id=self.identity_profile_version_id,
            environment_id=self.environment_id,
            browser_profile_version_id=self.browser_profile_version_id,
            data_profile_version_id=self.data_profile_version_id,
        )
        if self.dependency_digest != expected_dependency_digest:
            raise ValueError("dependencyDigest must match all pinned Unit references")
        return self


def task_run_manifest_hash(
    *,
    task_run_id: UUID,
    task_plan_version_id: UUID,
    trigger_source: TaskTriggerSource,
    trigger_fingerprint: str,
    tenant_id: UUID,
    project_id: UUID,
    iteration_id: str | None,
    units: tuple[ExecutionUnitManifest, ...],
    policy_digests: dict[str, str],
    compiler_version: str,
) -> str:
    """Recompute the canonical hash of a complete immutable Run Manifest."""

    body: dict[str, JsonValue] = {
        "schemaVersion": TASK_RUN_MANIFEST_SCHEMA_VERSION,
        "taskRunId": str(task_run_id),
        "taskPlanVersionId": str(task_plan_version_id),
        "triggerSource": trigger_source.value,
        "triggerFingerprint": trigger_fingerprint,
        "tenantId": str(tenant_id),
        "projectId": str(project_id),
        "iterationId": iteration_id,
        "units": [
            unit.model_dump(mode="json", by_alias=True)
            for unit in sorted(units, key=lambda item: item.unit_key)
        ],
        "policyDigests": dict(sorted(policy_digests.items())),
        "compilerVersion": compiler_version,
    }
    return canonical_digest(body)


class TaskRunManifest(FrozenWireModel):
    """Complete immutable execution input for one TaskRun."""

    schema_version: Literal["atlas.task-run-manifest/0.1"] = TASK_RUN_MANIFEST_SCHEMA_VERSION
    task_run_id: UUID
    task_plan_version_id: UUID
    trigger_source: TaskTriggerSource
    trigger_fingerprint: str = Field(
        min_length=3,
        max_length=320,
        pattern=REFERENCE_KEY_PATTERN,
    )
    tenant_id: UUID
    project_id: UUID
    iteration_id: str | None = Field(
        default=None,
        min_length=3,
        max_length=160,
        pattern=REFERENCE_KEY_PATTERN,
    )
    units: tuple[ExecutionUnitManifest, ...] = Field(
        min_length=1,
        max_length=100_000,
    )
    policy_digests: dict[PolicyKey, Sha256Digest] = Field(
        min_length=1,
        max_length=64,
        json_schema_extra={"additionalProperties": False},
    )
    compiler_version: SemanticVersion
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)

    @field_validator("policy_digests")
    @classmethod
    def normalize_policy_digests(cls, values: dict[str, str]) -> dict[str, str]:
        """Canonicalize validated policy digests."""

        for key, value in values.items():
            if fullmatch(POLICY_KEY_PATTERN, key) is None:
                raise ValueError("policyDigests contains an invalid policy key")
            if fullmatch(DIGEST_PATTERN, value) is None:
                raise ValueError("policyDigests values must be SHA-256 digests")
        return dict(sorted(values.items()))

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        """Require unique sorted Units, contiguous ordinals, and a valid hash."""

        unit_keys = tuple(unit.unit_key for unit in self.units)
        if len(set(unit_keys)) != len(unit_keys):
            raise ValueError("Run Manifest unitKey values must be unique")
        if unit_keys != tuple(sorted(unit_keys)):
            raise ValueError("Run Manifest units must be sorted by unitKey")
        if tuple(unit.ordinal for unit in self.units) != tuple(range(1, len(self.units) + 1)):
            raise ValueError("Run Manifest Unit ordinals must be contiguous")
        expected_hash = task_run_manifest_hash(
            task_run_id=self.task_run_id,
            task_plan_version_id=self.task_plan_version_id,
            trigger_source=self.trigger_source,
            trigger_fingerprint=self.trigger_fingerprint,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            iteration_id=self.iteration_id,
            units=self.units,
            policy_digests=self.policy_digests,
            compiler_version=self.compiler_version,
        )
        if self.manifest_hash != expected_hash:
            raise ValueError("manifestHash must match the complete Run Manifest")
        return self

    def recompute_manifest_hash(self) -> str:
        """Return the canonical hash without trusting the stored hash field."""

        return task_run_manifest_hash(
            task_run_id=self.task_run_id,
            task_plan_version_id=self.task_plan_version_id,
            trigger_source=self.trigger_source,
            trigger_fingerprint=self.trigger_fingerprint,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            iteration_id=self.iteration_id,
            units=self.units,
            policy_digests=self.policy_digests,
            compiler_version=self.compiler_version,
        )

    def recompute_request_digest(self) -> str:
        """Return stable logical input identity without physical Run values."""

        return task_run_request_digest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            task_plan_version_id=self.task_plan_version_id,
            trigger_source=self.trigger_source,
            trigger_fingerprint=self.trigger_fingerprint,
            iteration_id=self.iteration_id,
            units=self.units,
            policy_digests=self.policy_digests,
            compiler_version=self.compiler_version,
        )


def task_run_request_digest(
    *,
    tenant_id: UUID,
    project_id: UUID,
    task_plan_version_id: UUID,
    trigger_source: TaskTriggerSource,
    trigger_fingerprint: str,
    iteration_id: str | None,
    units: tuple[ExecutionUnitManifest, ...],
    policy_digests: dict[str, str],
    compiler_version: str,
) -> str:
    """Digest logical trigger inputs while excluding generated ids and times."""

    body: dict[str, JsonValue] = {
        "schemaVersion": TASK_RUN_REQUEST_SCHEMA_VERSION,
        "tenantId": str(tenant_id),
        "projectId": str(project_id),
        "taskPlanVersionId": str(task_plan_version_id),
        "triggerSource": trigger_source.value,
        "triggerFingerprint": trigger_fingerprint,
        "iterationId": iteration_id,
        "units": [
            unit.model_dump(mode="json", by_alias=True)
            for unit in sorted(units, key=lambda item: item.unit_key)
        ],
        "policyDigests": dict(sorted(policy_digests.items())),
        "compilerVersion": compiler_version,
    }
    return canonical_digest(body)


_RESOLVED_HYGIENE = {
    ExecutionHygiene.CLEANED,
    ExecutionHygiene.LEAKED,
}


def _validate_execution_projection(
    *,
    lifecycle: ExecutionLifecycle,
    quality: ExecutionQuality,
    hygiene: ExecutionHygiene,
    created_at: AwareDatetime,
    updated_at: AwareDatetime,
    started_at: AwareDatetime | None,
    finalized_at: AwareDatetime | None,
    cleanup_resolved_at: AwareDatetime | None,
    closed_at: AwareDatetime | None,
) -> None:
    """Validate the independent axes and their explicit milestone times."""

    if updated_at < created_at:
        raise ValueError("updatedAt cannot predate createdAt")
    for field_name, value in (
        ("startedAt", started_at),
        ("finalizedAt", finalized_at),
        ("cleanupResolvedAt", cleanup_resolved_at),
        ("closedAt", closed_at),
    ):
        if value is not None and value < created_at:
            raise ValueError(f"{field_name} cannot predate createdAt")
    if started_at is not None and finalized_at is not None and finalized_at < started_at:
        raise ValueError("finalizedAt cannot predate startedAt")
    if lifecycle is ExecutionLifecycle.QUEUED and started_at is not None:
        raise ValueError("QUEUED execution cannot have startedAt")
    if (
        lifecycle
        in {
            ExecutionLifecycle.RUNNING,
            ExecutionLifecycle.PAUSE_REQUESTED,
            ExecutionLifecycle.PAUSED,
        }
        and started_at is None
    ):
        raise ValueError("active execution requires startedAt")
    if quality is ExecutionQuality.PENDING:
        if finalized_at is not None:
            raise ValueError("PENDING quality cannot have finalizedAt")
    elif finalized_at is None:
        raise ValueError("resolved quality requires finalizedAt")
    if quality is not ExecutionQuality.PENDING and lifecycle not in {
        ExecutionLifecycle.FINALIZING,
        ExecutionLifecycle.CLOSED,
    }:
        raise ValueError("quality can resolve only while FINALIZING or CLOSED")
    if hygiene in _RESOLVED_HYGIENE:
        if cleanup_resolved_at is None:
            raise ValueError("resolved Hygiene requires cleanupResolvedAt")
    elif cleanup_resolved_at is not None:
        raise ValueError("unresolved Hygiene cannot have cleanupResolvedAt")
    if lifecycle is ExecutionLifecycle.CLOSED:
        if closed_at is None:
            raise ValueError("CLOSED execution requires closedAt")
        if quality is ExecutionQuality.PENDING:
            raise ValueError("CLOSED execution requires resolved Quality")
    elif closed_at is not None:
        raise ValueError("only CLOSED execution can have closedAt")
    if closed_at is not None and finalized_at is not None and closed_at < finalized_at:
        raise ValueError("closedAt cannot predate finalizedAt")


class TaskRun(FrozenWireModel):
    """Durable execution batch bound to one immutable Run Manifest."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "required": ["materializationState"],
                        "properties": {
                            "materializationState": {"const": "SEALED"}
                        },
                    },
                    "then": {
                        "required": [
                            "requestDigest",
                            "materializedUnitCount",
                            "materializedFirstAttemptCount",
                            "materializationSealedAt",
                            "temporalNamespace",
                            "temporalWorkflowId",
                        ],
                        "properties": {
                            "requestDigest": {"type": "string"},
                            "materializedUnitCount": {"type": "integer"},
                            "materializedFirstAttemptCount": {"type": "integer"},
                            "materializationSealedAt": {
                                "type": "string",
                                "format": "date-time",
                            },
                            "temporalNamespace": {"type": "string"},
                            "temporalWorkflowId": {"type": "string"},
                        },
                    },
                    "else": {
                        "properties": {
                            "materializationState": {"const": "MATERIALIZING"},
                            "materializedUnitCount": {"type": "null"},
                            "materializedFirstAttemptCount": {"type": "null"},
                            "materializationSealedAt": {"type": "null"},
                        }
                    },
                },
                {
                    "oneOf": [
                        {
                            "required": ["temporalNamespace", "temporalWorkflowId"],
                            "properties": {
                                "temporalNamespace": {"type": "string"},
                                "temporalWorkflowId": {"type": "string"},
                            },
                        },
                        {
                            "properties": {
                                "temporalNamespace": {"type": "null"},
                                "temporalWorkflowId": {"type": "null"},
                            }
                        },
                    ]
                },
            ],
            "x-atlas-invariants": [
                "SEALED requires materializedUnitCount == materializedFirstAttemptCount.",
                "materializationSealedAt must be between createdAt and updatedAt.",
                "When present, temporalWorkflowId is deterministically derived "
                "from tenantId and id.",
            ],
        }
    )

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_plan_version_id: UUID
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    trigger_source: TaskTriggerSource
    trigger_fingerprint: str = Field(
        min_length=3,
        max_length=320,
        pattern=REFERENCE_KEY_PATTERN,
    )
    request_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    materialization_state: TaskMaterializationState = TaskMaterializationState.MATERIALIZING
    materialized_unit_count: int | None = Field(default=None, ge=1)
    materialized_first_attempt_count: int | None = Field(default=None, ge=1)
    materialization_sealed_at: AwareDatetime | None = None
    rerun_of_task_run_id: UUID | None = None
    lifecycle: ExecutionLifecycle
    quality: ExecutionQuality
    hygiene: ExecutionHygiene
    requested_by: UUID | None
    temporal_namespace: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=TEMPORAL_NAMESPACE_PATTERN,
    )
    temporal_workflow_id: str | None = Field(
        default=None,
        min_length=12,
        max_length=320,
        pattern=TEMPORAL_WORKFLOW_ID_PATTERN,
    )
    requested_at: AwareDatetime
    queued_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finalized_at: AwareDatetime | None = None
    cleanup_resolved_at: AwareDatetime | None = None
    closed_at: AwareDatetime | None = None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        """Validate scope-independent TaskRun state and provenance."""

        if self.rerun_of_task_run_id == self.id:
            raise ValueError("rerunOfTaskRunId cannot reference the same TaskRun")
        if (self.temporal_namespace is None) != (self.temporal_workflow_id is None):
            raise ValueError("temporalNamespace and temporalWorkflowId must be set together")
        if self.temporal_workflow_id is not None and self.temporal_workflow_id != (
            task_run_workflow_id(tenant_id=self.tenant_id, task_run_id=self.id)
        ):
            raise ValueError("temporalWorkflowId must match the deterministic TaskRun identity")
        if self.materialization_state is TaskMaterializationState.MATERIALIZING:
            if any(
                value is not None
                for value in (
                    self.materialized_unit_count,
                    self.materialized_first_attempt_count,
                    self.materialization_sealed_at,
                )
            ):
                raise ValueError("MATERIALIZING TaskRun cannot contain seal facts")
        else:
            if self.request_digest is None:
                raise ValueError("SEALED TaskRun requires requestDigest")
            if self.temporal_workflow_id is None:
                raise ValueError("SEALED TaskRun requires a Temporal Workflow identity")
            if (
                self.materialized_unit_count is None
                or self.materialized_first_attempt_count is None
                or self.materialized_unit_count != self.materialized_first_attempt_count
                or self.materialization_sealed_at is None
            ):
                raise ValueError("SEALED TaskRun requires complete matching materialization counts")
            if not self.created_at <= self.materialization_sealed_at <= self.updated_at:
                raise ValueError("materializationSealedAt must be between createdAt and updatedAt")
        if not self.created_at <= self.requested_at <= self.queued_at:
            raise ValueError("requestedAt and queuedAt must follow createdAt")
        if self.started_at is not None and self.started_at < self.queued_at:
            raise ValueError("startedAt cannot predate queuedAt")
        _validate_execution_projection(
            lifecycle=self.lifecycle,
            quality=self.quality,
            hygiene=self.hygiene,
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            finalized_at=self.finalized_at,
            cleanup_resolved_at=self.cleanup_resolved_at,
            closed_at=self.closed_at,
        )
        return self


class ExecutionUnit(FrozenWireModel):
    """Logical immutable manifest cell whose retries append UnitAttempts."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    ordinal: int = Field(ge=1)
    unit_key: str = Field(pattern=DIGEST_PATTERN)
    case_version_id: UUID
    execution_profile_version_id: UUID
    fixture_blueprint_version_id: UUID
    identity_profile_version_id: UUID
    environment_id: UUID
    browser_profile_version_id: UUID
    data_profile_version_id: UUID
    parameter_digest: str = Field(pattern=DIGEST_PATTERN)
    dependency_digest: str = Field(pattern=DIGEST_PATTERN)
    lifecycle: ExecutionLifecycle
    quality: ExecutionQuality
    hygiene: ExecutionHygiene
    started_at: AwareDatetime | None = None
    finalized_at: AwareDatetime | None = None
    cleanup_resolved_at: AwareDatetime | None = None
    closed_at: AwareDatetime | None = None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        """Validate frozen Unit bindings, digests, and three-axis state."""

        expected_unit_key = execution_unit_key(
            case_version_id=self.case_version_id,
            environment_id=self.environment_id,
            browser_profile_version_id=self.browser_profile_version_id,
            identity_profile_version_id=self.identity_profile_version_id,
            data_profile_version_id=self.data_profile_version_id,
            parameter_digest=self.parameter_digest,
        )
        if self.unit_key != expected_unit_key:
            raise ValueError("unitKey must match the frozen matrix identity")
        expected_dependency_digest = execution_unit_dependency_digest(
            case_version_id=self.case_version_id,
            execution_profile_version_id=self.execution_profile_version_id,
            fixture_blueprint_version_id=self.fixture_blueprint_version_id,
            identity_profile_version_id=self.identity_profile_version_id,
            environment_id=self.environment_id,
            browser_profile_version_id=self.browser_profile_version_id,
            data_profile_version_id=self.data_profile_version_id,
        )
        if self.dependency_digest != expected_dependency_digest:
            raise ValueError("dependencyDigest must match all exact Unit dependencies")
        _validate_execution_projection(
            lifecycle=self.lifecycle,
            quality=self.quality,
            hygiene=self.hygiene,
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            finalized_at=self.finalized_at,
            cleanup_resolved_at=self.cleanup_resolved_at,
            closed_at=self.closed_at,
        )
        return self


class UnitAttempt(FrozenWireModel):
    """One append-only physical execution attempt for an ExecutionUnit."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "oneOf": [
                        {
                            "required": ["temporalNamespace", "temporalWorkflowId"],
                            "properties": {
                                "temporalNamespace": {"type": "string"},
                                "temporalWorkflowId": {"type": "string"},
                            },
                        },
                        {
                            "properties": {
                                "temporalNamespace": {"type": "null"},
                                "temporalWorkflowId": {"type": "null"},
                            }
                        },
                    ]
                }
            ],
            "x-atlas-invariants": [
                "When present, temporalWorkflowId is deterministically derived "
                "from tenantId and id."
            ],
        }
    )

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    unit_key: str = Field(pattern=DIGEST_PATTERN)
    case_version_id: UUID
    attempt_number: int = Field(ge=1)
    lifecycle: ExecutionLifecycle
    quality: ExecutionQuality
    hygiene: ExecutionHygiene
    temporal_namespace: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=TEMPORAL_NAMESPACE_PATTERN,
    )
    temporal_workflow_id: str | None = Field(
        default=None,
        min_length=12,
        max_length=320,
        pattern=TEMPORAL_WORKFLOW_ID_PATTERN,
    )
    queued_at: AwareDatetime
    execution_deadline: AwareDatetime
    started_at: AwareDatetime | None = None
    finalized_at: AwareDatetime | None = None
    cleanup_resolved_at: AwareDatetime | None = None
    closed_at: AwareDatetime | None = None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        """Validate attempt ordering inputs, deadline, and three-axis state."""

        if (self.temporal_namespace is None) != (self.temporal_workflow_id is None):
            raise ValueError("temporalNamespace and temporalWorkflowId must be set together")
        if self.temporal_workflow_id is not None and self.temporal_workflow_id != (
            unit_attempt_workflow_id(
                tenant_id=self.tenant_id,
                unit_attempt_id=self.id,
            )
        ):
            raise ValueError("temporalWorkflowId must match the deterministic UnitAttempt identity")
        if self.queued_at < self.created_at:
            raise ValueError("queuedAt cannot predate createdAt")
        if self.execution_deadline <= self.queued_at:
            raise ValueError("executionDeadline must follow queuedAt")
        if self.started_at is not None and self.started_at < self.queued_at:
            raise ValueError("startedAt cannot predate queuedAt")
        _validate_execution_projection(
            lifecycle=self.lifecycle,
            quality=self.quality,
            hygiene=self.hygiene,
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            finalized_at=self.finalized_at,
            cleanup_resolved_at=self.cleanup_resolved_at,
            closed_at=self.closed_at,
        )
        return self


class TaskExecutionEvent(FrozenWireModel):
    """Append-only monotonic event projection for Task execution replay."""

    schema_version: Literal["atlas.execution-event/0.1"] = TASK_EXECUTION_EVENT_SCHEMA_VERSION
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID | None = None
    unit_attempt_id: UUID | None = None
    seq: int = Field(ge=1)
    event_type: str = Field(
        min_length=3,
        max_length=160,
        pattern=EVENT_TYPE_PATTERN,
    )
    lifecycle: ExecutionLifecycle
    quality: ExecutionQuality
    hygiene: ExecutionHygiene
    payload: dict[str, JsonValue] = Field(
        default_factory=dict,
        json_schema_extra={
            "x-atlas-max-serialized-bytes": TASK_EXECUTION_EVENT_PAYLOAD_MAX_BYTES,
        },
    )
    occurred_at: AwareDatetime

    @field_validator("payload")
    @classmethod
    def validate_payload_size(cls, values: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Bound the PostgreSQL jsonb text stored in the event ledger."""

        try:
            serialized = _postgres_jsonb_text(values).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("payload must be PostgreSQL jsonb-compatible") from error
        if len(serialized) > TASK_EXECUTION_EVENT_PAYLOAD_MAX_BYTES:
            raise ValueError(
                "payload PostgreSQL jsonb text must not exceed "
                f"{TASK_EXECUTION_EVENT_PAYLOAD_MAX_BYTES} bytes"
            )
        return values

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        """Require Unit scope whenever an event identifies a UnitAttempt."""

        if self.unit_attempt_id is not None and self.execution_unit_id is None:
            raise ValueError("unitAttemptId requires executionUnitId scope")
        return self
