"""Contract tests for append-only Attempt Fixture and Unit Hygiene truth."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from tests.infrastructure.test_task_run_repository import NOW

from atlas_testops.domain.result import (
    UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST,
    AttemptFixtureBinding,
    AttemptFixtureBindingContent,
    DataHygiene,
    UnitHygieneInputSource,
    UnitHygieneResolutionInput,
    UnitHygieneResolutionRevision,
    UnitHygieneResolutionRevisionContent,
    attempt_fixture_binding_hash,
    parse_task_attempt_fixture_execution_id,
    resolve_unit_data_hygiene,
    task_attempt_fixture_execution_id,
    unit_hygiene_input_set_hash,
    unit_hygiene_resolution_hash,
)

_DIGEST = "sha256:" + "a" * 64


def _fixture_input(
    *,
    attempt_number: int,
    hygiene: DataHygiene,
) -> UnitHygieneResolutionInput:
    resource_count = int(hygiene is not DataHygiene.NOT_APPLICABLE)
    return UnitHygieneResolutionInput(
        unit_attempt_id=uuid4(),
        attempt_number=attempt_number,
        source=UnitHygieneInputSource.FIXTURE_RUN,
        data_hygiene=hygiene,
        fixture_binding_id=uuid4(),
        fixture_run_id=uuid4(),
        fixture_run_revision=4,
        fixture_run_status=("CLEANUP_FAILED" if hygiene is DataHygiene.LEAKED else "RELEASED"),
        cleanup_generation=1,
        fixture_plan_digest=_DIGEST,
        fixture_manifest_digest=_DIGEST,
        resource_state_hash="sha256:" + str(attempt_number) * 64,
        resource_count=resource_count,
        cleaned_resource_count=int(hygiene is DataHygiene.CLEANED),
        leaked_resource_count=int(hygiene is DataHygiene.LEAKED),
        unresolved_resource_count=int(hygiene is DataHygiene.PENDING),
        exhausted_reconcile_count=0,
        unresolved_reconcile_count=0,
        observed_at=NOW + timedelta(minutes=attempt_number),
    )


def test_attempt_fixture_binding_hash_and_execution_identity_are_exact() -> None:
    attempt_id = uuid4()
    content = AttemptFixtureBindingContent(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        task_run_id=uuid4(),
        execution_unit_id=uuid4(),
        unit_attempt_id=attempt_id,
        fixture_run_id=uuid4(),
        fixture_blueprint_version_id=uuid4(),
        environment_id=uuid4(),
        fixture_plan_digest=_DIGEST,
        created_at=NOW,
    )
    binding = AttemptFixtureBinding(
        **content.model_dump(mode="python"),
        binding_hash=attempt_fixture_binding_hash(content),
    )

    execution_id = task_attempt_fixture_execution_id(attempt_id)
    assert parse_task_attempt_fixture_execution_id(execution_id) == attempt_id
    assert parse_task_attempt_fixture_execution_id(f"unit-attempt:{attempt_id.hex}") is None
    assert binding.binding_hash == attempt_fixture_binding_hash(binding)

    with pytest.raises(ValidationError, match="bindingHash"):
        AttemptFixtureBinding(
            **content.model_dump(mode="python"),
            binding_hash="sha256:" + "f" * 64,
        )


def test_unit_hygiene_preserves_leak_from_an_earlier_attempt() -> None:
    unit_id = uuid4()
    inputs = (
        _fixture_input(attempt_number=1, hygiene=DataHygiene.LEAKED),
        _fixture_input(attempt_number=2, hygiene=DataHygiene.CLEANED),
    )
    content = UnitHygieneResolutionRevisionContent(
        id=uuid4(),
        unit_hygiene_resolution_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        task_run_id=uuid4(),
        execution_unit_id=unit_id,
        manifest_hash=_DIGEST,
        unit_key=_DIGEST,
        revision=1,
        inputs=inputs,
        input_set_hash=unit_hygiene_input_set_hash(
            execution_unit_id=unit_id,
            manifest_hash=_DIGEST,
            inputs=inputs,
        ),
        data_hygiene=DataHygiene.LEAKED,
        resolution_policy_digest=UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST,
        projection_watermark=inputs[-1].observed_at,
        created_at=inputs[-1].observed_at,
    )
    resolution = UnitHygieneResolutionRevision(
        **content.model_dump(mode="python"),
        resolution_hash=unit_hygiene_resolution_hash(content),
    )

    assert resolve_unit_data_hygiene(inputs) is DataHygiene.LEAKED
    assert resolution.data_hygiene is DataHygiene.LEAKED
    assert resolution.resolution_hash == unit_hygiene_resolution_hash(resolution)


def test_mixed_cleaned_and_not_applicable_resolves_cleaned() -> None:
    cleaned = _fixture_input(attempt_number=1, hygiene=DataHygiene.CLEANED)
    not_required = UnitHygieneResolutionInput(
        unit_attempt_id=uuid4(),
        attempt_number=2,
        source=UnitHygieneInputSource.EXPLICIT_NOT_REQUIRED,
        data_hygiene=DataHygiene.NOT_APPLICABLE,
        resource_state_hash=_DIGEST,
        resource_count=0,
        cleaned_resource_count=0,
        leaked_resource_count=0,
        unresolved_resource_count=0,
        exhausted_reconcile_count=0,
        unresolved_reconcile_count=0,
        observed_at=NOW + timedelta(minutes=2),
    )

    assert resolve_unit_data_hygiene((cleaned, not_required)) is DataHygiene.CLEANED


def test_hygiene_input_rejects_non_conserving_resource_counts() -> None:
    with pytest.raises(ValidationError, match="conserve"):
        UnitHygieneResolutionInput(
            **{
                **_fixture_input(
                    attempt_number=1,
                    hygiene=DataHygiene.CLEANED,
                ).model_dump(mode="python", by_alias=False),
                "resource_count": 2,
            }
        )
