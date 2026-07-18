"""Append-only Cleanup truth connecting Task Attempts to Fixture runs."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self, cast
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import DIGEST_PATTERN
from atlas_testops.domain.result.models import DataHygiene
from atlas_testops.domain.result.projections import result_projection_digest

ATTEMPT_FIXTURE_BINDING_SCHEMA_VERSION: Literal["atlas.attempt-fixture-binding/0.1"] = (
    "atlas.attempt-fixture-binding/0.1"
)
UNIT_HYGIENE_RESOLUTION_REVISION_SCHEMA_VERSION: Literal[
    "atlas.unit-hygiene-resolution-revision/0.1"
] = "atlas.unit-hygiene-resolution-revision/0.1"
UNIT_HYGIENE_RESOLUTION_POLICY_VERSION: Literal["0.1.0"] = "0.1.0"
TASK_ATTEMPT_FIXTURE_EXECUTION_PREFIX = "unit-attempt:"


class UnitHygieneInputSource(StrEnum):
    """Trusted source used to resolve one closed Attempt's cleanup state."""

    FIXTURE_RUN = "FIXTURE_RUN"
    EXPLICIT_NOT_REQUIRED = "EXPLICIT_NOT_REQUIRED"


class AttemptFixtureBindingContent(FrozenWireModel):
    """Exact immutable association created before a Task Attempt uses a Fixture."""

    schema_version: Literal["atlas.attempt-fixture-binding/0.1"] = (
        ATTEMPT_FIXTURE_BINDING_SCHEMA_VERSION
    )
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    fixture_run_id: UUID
    fixture_blueprint_version_id: UUID
    environment_id: UUID
    fixture_plan_digest: str = Field(pattern=DIGEST_PATTERN)
    created_at: AwareDatetime


class AttemptFixtureBinding(AttemptFixtureBindingContent):
    """Hashed append-only proof of the Attempt-to-Fixture authority bridge."""

    binding_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_binding_hash(self) -> Self:
        """Reject a binding whose canonical content was altered."""

        if self.binding_hash != attempt_fixture_binding_hash(self):
            raise ValueError("bindingHash must match AttemptFixtureBinding content")
        return self


class UnitHygieneResolutionInput(FrozenWireModel):
    """Frozen cleanup observation for one closed physical Attempt."""

    unit_attempt_id: UUID
    attempt_number: int = Field(ge=1, le=100)
    source: UnitHygieneInputSource
    data_hygiene: DataHygiene
    fixture_binding_id: UUID | None = None
    fixture_run_id: UUID | None = None
    fixture_run_revision: int | None = Field(default=None, ge=1)
    fixture_run_status: str | None = Field(
        default=None,
        pattern=r"^[A-Z][A-Z0-9_]{2,63}$",
    )
    cleanup_generation: int | None = Field(default=None, ge=0)
    fixture_plan_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    fixture_manifest_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    resource_state_hash: str = Field(pattern=DIGEST_PATTERN)
    resource_count: int = Field(ge=0)
    cleaned_resource_count: int = Field(ge=0)
    leaked_resource_count: int = Field(ge=0)
    unresolved_resource_count: int = Field(ge=0)
    exhausted_reconcile_count: int = Field(ge=0)
    unresolved_reconcile_count: int = Field(ge=0)
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_source_shape(self) -> Self:
        """Keep Fixture and explicit no-cleanup observations unambiguous."""

        counts = (
            self.cleaned_resource_count
            + self.leaked_resource_count
            + self.unresolved_resource_count
        )
        if counts != self.resource_count:
            raise ValueError("resource cleanup counts must conserve resourceCount")
        fixture_fields = (
            self.fixture_binding_id,
            self.fixture_run_id,
            self.fixture_run_revision,
            self.fixture_run_status,
            self.cleanup_generation,
            self.fixture_plan_digest,
        )
        if self.source is UnitHygieneInputSource.FIXTURE_RUN:
            if any(value is None for value in fixture_fields):
                raise ValueError("FIXTURE_RUN hygiene input requires exact Fixture provenance")
        elif (
            any(value is not None for value in fixture_fields)
            or self.fixture_manifest_digest is not None
            or self.data_hygiene is not DataHygiene.NOT_APPLICABLE
            or self.resource_count
            or self.exhausted_reconcile_count
            or self.unresolved_reconcile_count
        ):
            raise ValueError("EXPLICIT_NOT_REQUIRED hygiene input cannot claim Fixture facts")
        if self.data_hygiene is DataHygiene.CLEANED and (
            self.leaked_resource_count
            or self.unresolved_resource_count
            or self.exhausted_reconcile_count
            or self.unresolved_reconcile_count
        ):
            raise ValueError("CLEANED hygiene input cannot contain unresolved cleanup facts")
        if self.data_hygiene is DataHygiene.LEAKED and not (
            self.leaked_resource_count or self.exhausted_reconcile_count
        ):
            raise ValueError("LEAKED hygiene input requires an explicit leaked fact")
        return self


class UnitHygieneResolutionRevisionContent(FrozenWireModel):
    """Semantic Unit cleanup interpretation independent of its content hash."""

    schema_version: Literal["atlas.unit-hygiene-resolution-revision/0.1"] = (
        UNIT_HYGIENE_RESOLUTION_REVISION_SCHEMA_VERSION
    )
    id: UUID
    unit_hygiene_resolution_id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    manifest_hash: str = Field(pattern=DIGEST_PATTERN)
    unit_key: str = Field(pattern=DIGEST_PATTERN)
    revision: int = Field(ge=1)
    inputs: tuple[UnitHygieneResolutionInput, ...] = Field(min_length=1, max_length=100)
    input_set_hash: str = Field(pattern=DIGEST_PATTERN)
    data_hygiene: DataHygiene
    resolution_policy_version: Literal["0.1.0"] = UNIT_HYGIENE_RESOLUTION_POLICY_VERSION
    resolution_policy_digest: str = Field(pattern=DIGEST_PATTERN)
    supersedes_revision_id: UUID | None = None
    projection_watermark: AwareDatetime
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_resolution_content(self) -> Self:
        """Enforce exact Attempt coverage, aggregation, policy, and lineage."""

        numbers = tuple(item.attempt_number for item in self.inputs)
        if numbers != tuple(range(1, len(self.inputs) + 1)):
            raise ValueError("hygiene inputs must cover a gapless Attempt sequence")
        if len({item.unit_attempt_id for item in self.inputs}) != len(self.inputs):
            raise ValueError("hygiene inputs must identify unique UnitAttempts")
        if self.input_set_hash != unit_hygiene_input_set_hash(
            execution_unit_id=self.execution_unit_id,
            manifest_hash=self.manifest_hash,
            inputs=self.inputs,
        ):
            raise ValueError("inputSetHash must match the exact hygiene inputs")
        if self.data_hygiene is not resolve_unit_data_hygiene(self.inputs):
            raise ValueError("dataHygiene must match the frozen Hygiene Policy")
        if self.resolution_policy_digest != UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST:
            raise ValueError("resolutionPolicyDigest must match the Hygiene Policy")
        if self.revision == 1 and self.supersedes_revision_id is not None:
            raise ValueError("first Hygiene Resolution cannot supersede another")
        if self.revision > 1 and self.supersedes_revision_id is None:
            raise ValueError("later Hygiene Resolution requires its predecessor")
        expected_watermark = max(item.observed_at for item in self.inputs)
        if self.projection_watermark != expected_watermark:
            raise ValueError("projectionWatermark must match the latest cleanup input")
        if self.created_at < self.projection_watermark:
            raise ValueError("createdAt cannot predate projectionWatermark")
        return self


class UnitHygieneResolutionRevision(UnitHygieneResolutionRevisionContent):
    """Immutable hashed cleanup interpretation over every closed UnitAttempt."""

    resolution_hash: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_resolution_hash(self) -> Self:
        """Reject an altered persisted Hygiene Resolution."""

        if self.resolution_hash != unit_hygiene_resolution_hash(self):
            raise ValueError("resolutionHash must match Hygiene Resolution content")
        return self


def task_attempt_fixture_execution_id(unit_attempt_id: UUID) -> str:
    """Return the only executionId that may bind a FixtureRun to a Task Attempt."""

    return f"{TASK_ATTEMPT_FIXTURE_EXECUTION_PREFIX}{unit_attempt_id}"


def parse_task_attempt_fixture_execution_id(value: str) -> UUID | None:
    """Parse the formal Task Fixture execution identity without accepting aliases."""

    if not value.startswith(TASK_ATTEMPT_FIXTURE_EXECUTION_PREFIX):
        return None
    raw_id = value.removeprefix(TASK_ATTEMPT_FIXTURE_EXECUTION_PREFIX)
    try:
        attempt_id = UUID(raw_id)
    except ValueError:
        return None
    return attempt_id if task_attempt_fixture_execution_id(attempt_id) == value else None


def attempt_fixture_binding_hash(
    value: AttemptFixtureBindingContent | AttemptFixtureBinding,
) -> str:
    """Hash the complete immutable Attempt-to-Fixture binding content."""

    body = cast(
        dict[str, JsonValue],
        value.model_dump(
            mode="json",
            by_alias=True,
            exclude={"binding_hash"},
        ),
    )
    return result_projection_digest(body)


def unit_hygiene_input_set_hash(
    *,
    execution_unit_id: UUID,
    manifest_hash: str,
    inputs: tuple[UnitHygieneResolutionInput, ...],
) -> str:
    """Hash the gapless Attempt cleanup inputs in physical execution order."""

    return result_projection_digest(
        {
            "schemaVersion": "atlas.unit-hygiene-input-set/0.1",
            "executionUnitId": str(execution_unit_id),
            "manifestHash": manifest_hash,
            "inputs": [item.model_dump(mode="json", by_alias=True) for item in inputs],
        }
    )


def resolve_unit_data_hygiene(
    inputs: tuple[UnitHygieneResolutionInput, ...],
) -> DataHygiene:
    """Aggregate every Attempt without allowing a later clean retry to hide a leak."""

    states = {item.data_hygiene for item in inputs}
    if DataHygiene.LEAKED in states:
        return DataHygiene.LEAKED
    if DataHygiene.CLEANUP_FAILED in states:
        return DataHygiene.CLEANUP_FAILED
    if DataHygiene.PENDING in states:
        return DataHygiene.PENDING
    if states == {DataHygiene.NOT_APPLICABLE}:
        return DataHygiene.NOT_APPLICABLE
    return DataHygiene.CLEANED


def unit_hygiene_resolution_hash(
    value: UnitHygieneResolutionRevisionContent | UnitHygieneResolutionRevision,
) -> str:
    """Hash reproducible cleanup semantics while excluding revision identity."""

    body = cast(
        dict[str, JsonValue],
        value.model_dump(
            mode="json",
            by_alias=True,
            exclude={
                "id",
                "unit_hygiene_resolution_id",
                "revision",
                "supersedes_revision_id",
                "created_at",
                "resolution_hash",
            },
        ),
    )
    return result_projection_digest(body)


UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST = result_projection_digest(
    {
        "schemaVersion": "atlas.unit-hygiene-resolution-policy/0.1",
        "policyVersion": UNIT_HYGIENE_RESOLUTION_POLICY_VERSION,
        "coverage": "EVERY_CLOSED_ATTEMPT_IN_GAPLESS_ATTEMPT_ORDER",
        "unboundAttempt": "ONLY_EXPLICIT_NOT_REQUIRED_IS_ACCEPTED",
        "precedence": [
            "LEAKED",
            "CLEANUP_FAILED",
            "PENDING",
            "CLEANED",
            "NOT_APPLICABLE",
        ],
        "mixedCleanedAndNotApplicable": "CLEANED",
    }
)


__all__ = [
    "ATTEMPT_FIXTURE_BINDING_SCHEMA_VERSION",
    "TASK_ATTEMPT_FIXTURE_EXECUTION_PREFIX",
    "UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST",
    "UNIT_HYGIENE_RESOLUTION_POLICY_VERSION",
    "UNIT_HYGIENE_RESOLUTION_REVISION_SCHEMA_VERSION",
    "AttemptFixtureBinding",
    "AttemptFixtureBindingContent",
    "UnitHygieneInputSource",
    "UnitHygieneResolutionInput",
    "UnitHygieneResolutionRevision",
    "UnitHygieneResolutionRevisionContent",
    "attempt_fixture_binding_hash",
    "parse_task_attempt_fixture_execution_id",
    "resolve_unit_data_hygiene",
    "task_attempt_fixture_execution_id",
    "unit_hygiene_input_set_hash",
    "unit_hygiene_resolution_hash",
]
