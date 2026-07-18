"""Task hierarchy digest, scope, ordering, and three-axis invariants."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from atlas_testops.domain.task import (
    TASK_EXECUTION_EVENT_SCHEMA_VERSION,
    TASK_PLAN_SCHEMA_VERSION,
    TASK_RUN_MANIFEST_SCHEMA_VERSION,
    CaseExecutionProfileRef,
    CITaskRunTrigger,
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    ExecutionUnitManifest,
    PublishTaskPlanVersion,
    RequestTaskRunInfraFailureRerun,
    ScheduleTaskRunTrigger,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskMatrixDefinition,
    TaskPlan,
    TaskPlanStatus,
    TaskPlanVersion,
    TaskProfileRefs,
    TaskRetryPolicy,
    TaskRun,
    TaskRunManifest,
    TaskRunRerunSelectionMode,
    TaskTriggerSource,
    TriggerTaskPlanVersionRun,
    UnitAttempt,
    WebhookTaskRunTrigger,
    execution_unit_dependency_digest,
    execution_unit_key,
    task_plan_version_content_digest,
    task_plan_version_ref,
    task_retry_policy_digest,
    task_run_infra_rerun_trigger_fingerprint,
    task_run_manifest_hash,
    task_run_trigger_fingerprint,
    task_run_workflow_id,
    unit_attempt_workflow_id,
)

NOW = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
POLICY_DIGEST = f"sha256:{'a' * 64}"
PARAMETER_DIGEST = f"sha256:{'b' * 64}"
MANIFEST_DIGEST = f"sha256:{'c' * 64}"


def uid(value: int) -> UUID:
    """Return compact deterministic UUIDs for contract fixtures."""

    return UUID(int=value)


def matrix() -> TaskMatrixDefinition:
    """Build one canonical non-empty V1 matrix."""

    return TaskMatrixDefinition(
        environment_ids=(uid(11),),
        browser_profile_version_ids=(uid(12),),
        identity_profile_version_ids=(uid(13),),
        data_profile_version_ids=(uid(14),),
    )


def profile_refs(*case_version_ids: UUID) -> TaskProfileRefs:
    """Bind every pinned CaseVersion to exact runtime dependencies."""

    return TaskProfileRefs(
        case_profiles=tuple(
            CaseExecutionProfileRef(
                case_version_id=case_version_id,
                execution_profile_version_id=uid(100 + index),
                fixture_blueprint_version_id=uid(200 + index),
            )
            for index, case_version_id in enumerate(case_version_ids, start=1)
        )
    )


def retry_policy() -> TaskRetryPolicy:
    """Build one canonical infrastructure retry policy."""

    digest = task_retry_policy_digest(
        infra_retry_attempts=1,
        max_total_infra_retries=8,
        initial_backoff_seconds=2,
        maximum_backoff_seconds=30,
        jitter_percent=10,
    )
    return TaskRetryPolicy(
        infra_retry_attempts=1,
        max_total_infra_retries=8,
        initial_backoff_seconds=2,
        maximum_backoff_seconds=30,
        jitter_percent=10,
        content_digest=digest,
    )


def task_plan_version_payload() -> dict[str, object]:
    """Build a valid published TaskPlanVersion wire payload."""

    tenant_id = uid(1)
    project_id = uid(2)
    task_plan_id = uid(3)
    case_ids = (uid(31), uid(32))
    task_matrix = matrix()
    profiles = profile_refs(*case_ids)
    policies = {"gate": POLICY_DIGEST, "retry": f"sha256:{'d' * 64}"}
    digest = task_plan_version_content_digest(
        tenant_id=tenant_id,
        project_id=project_id,
        task_plan_id=task_plan_id,
        version="1.2.0",
        pinned_case_version_ids=case_ids,
        matrix=task_matrix,
        profile_refs=profiles,
        policy_digests=policies,
    )
    return {
        "schemaVersion": TASK_PLAN_SCHEMA_VERSION,
        "id": str(uid(4)),
        "tenantId": str(tenant_id),
        "projectId": str(project_id),
        "taskPlanId": str(task_plan_id),
        "version": "1.2.0",
        "versionRef": task_plan_version_ref(task_plan_id, "1.2.0"),
        "pinnedCaseVersionIds": [str(value) for value in reversed(case_ids)],
        "matrix": task_matrix.model_dump(mode="json", by_alias=True),
        "profileRefs": profiles.model_dump(mode="json", by_alias=True),
        "policyDigests": policies,
        "contentDigest": digest,
        "publishedBy": str(uid(5)),
        "publishedAt": NOW + timedelta(minutes=1),
        "revision": 1,
        "createdAt": NOW,
        "updatedAt": NOW + timedelta(minutes=1),
    }


def unit_manifest_payload(*, case_version_id: UUID, ordinal: int) -> dict[str, object]:
    """Build one exact Unit binding with both derived digests."""

    execution_profile_version_id = uid(1000 + ordinal)
    fixture_blueprint_version_id = uid(1100 + ordinal)
    identity_profile_version_id = uid(1200 + ordinal)
    environment_id = uid(1300 + ordinal)
    browser_profile_version_id = uid(1400 + ordinal)
    data_profile_version_id = uid(1500 + ordinal)
    unit_key = execution_unit_key(
        case_version_id=case_version_id,
        environment_id=environment_id,
        browser_profile_version_id=browser_profile_version_id,
        identity_profile_version_id=identity_profile_version_id,
        data_profile_version_id=data_profile_version_id,
        parameter_digest=PARAMETER_DIGEST,
    )
    dependency_digest = execution_unit_dependency_digest(
        case_version_id=case_version_id,
        execution_profile_version_id=execution_profile_version_id,
        fixture_blueprint_version_id=fixture_blueprint_version_id,
        identity_profile_version_id=identity_profile_version_id,
        environment_id=environment_id,
        browser_profile_version_id=browser_profile_version_id,
        data_profile_version_id=data_profile_version_id,
    )
    return {
        "ordinal": ordinal,
        "unitKey": unit_key,
        "caseVersionId": str(case_version_id),
        "executionProfileVersionId": str(execution_profile_version_id),
        "fixtureBlueprintVersionId": str(fixture_blueprint_version_id),
        "identityProfileVersionId": str(identity_profile_version_id),
        "environmentId": str(environment_id),
        "browserProfileVersionId": str(browser_profile_version_id),
        "dataProfileVersionId": str(data_profile_version_id),
        "parameterDigest": PARAMETER_DIGEST,
        "dependencyDigest": dependency_digest,
    }


def ordered_units() -> tuple[ExecutionUnitManifest, ...]:
    """Build two Units ordered by key with contiguous canonical ordinals."""

    payloads = [
        unit_manifest_payload(case_version_id=uid(301), ordinal=1),
        unit_manifest_payload(case_version_id=uid(302), ordinal=2),
    ]
    payloads.sort(key=lambda item: str(item["unitKey"]))
    for ordinal, payload in enumerate(payloads, start=1):
        payload["ordinal"] = ordinal
    return tuple(ExecutionUnitManifest.model_validate(payload) for payload in payloads)


def run_manifest_payload(
    *,
    units: tuple[ExecutionUnitManifest, ...] | None = None,
) -> dict[str, object]:
    """Build a valid immutable TaskRunManifest wire payload."""

    frozen_units = units or ordered_units()
    task_run_id = uid(401)
    task_plan_version_id = uid(402)
    retry_digest = task_retry_policy_digest(
        infra_retry_attempts=2,
        max_total_infra_retries=16,
        initial_backoff_seconds=5,
        maximum_backoff_seconds=60,
        jitter_percent=20,
    )
    retry_policy = TaskRetryPolicy(
        infra_retry_attempts=2,
        max_total_infra_retries=16,
        initial_backoff_seconds=5,
        maximum_backoff_seconds=60,
        jitter_percent=20,
        content_digest=retry_digest,
    )
    policies = {"gate": POLICY_DIGEST, "infra-retry": retry_digest}
    manifest_hash = task_run_manifest_hash(
        task_run_id=task_run_id,
        task_plan_version_id=task_plan_version_id,
        trigger_source=TaskTriggerSource.CI,
        trigger_fingerprint="gha:crm:build-8421:job-test:0",
        tenant_id=uid(1),
        project_id=uid(2),
        iteration_id="2026.07",
        units=frozen_units,
        policy_digests=policies,
        compiler_version="0.1.0",
        schema_version=TASK_RUN_MANIFEST_SCHEMA_VERSION,
        retry_policy=retry_policy,
    )
    return {
        "schemaVersion": TASK_RUN_MANIFEST_SCHEMA_VERSION,
        "taskRunId": str(task_run_id),
        "taskPlanVersionId": str(task_plan_version_id),
        "triggerSource": TaskTriggerSource.CI,
        "triggerFingerprint": "gha:crm:build-8421:job-test:0",
        "tenantId": str(uid(1)),
        "projectId": str(uid(2)),
        "iterationId": "2026.07",
        "units": [unit.model_dump(mode="json", by_alias=True) for unit in frozen_units],
        "policyDigests": policies,
        "retryPolicy": retry_policy.model_dump(mode="json", by_alias=True),
        "compilerVersion": "0.1.0",
        "manifestHash": manifest_hash,
    }


def execution_unit_payload(unit: ExecutionUnitManifest) -> dict[str, object]:
    """Build a QUEUED ExecutionUnit from its immutable manifest cell."""

    return {
        "id": str(uid(501)),
        "tenantId": str(uid(1)),
        "projectId": str(uid(2)),
        "taskRunId": str(uid(401)),
        "manifestHash": MANIFEST_DIGEST,
        **unit.model_dump(mode="json", by_alias=True),
        "lifecycle": ExecutionLifecycle.QUEUED,
        "quality": ExecutionQuality.PENDING,
        "hygiene": ExecutionHygiene.PENDING,
        "revision": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def unit_attempt_payload(*, attempt_number: int = 1) -> dict[str, object]:
    """Build one queued physical UnitAttempt."""

    unit = ordered_units()[0]
    return {
        "id": str(uid(600 + attempt_number)),
        "tenantId": str(uid(1)),
        "projectId": str(uid(2)),
        "taskRunId": str(uid(401)),
        "executionUnitId": str(uid(501)),
        "manifestHash": MANIFEST_DIGEST,
        "unitKey": unit.unit_key,
        "caseVersionId": str(unit.case_version_id),
        "attemptNumber": attempt_number,
        "lifecycle": ExecutionLifecycle.QUEUED,
        "quality": ExecutionQuality.PENDING,
        "hygiene": ExecutionHygiene.PENDING,
        "queuedAt": NOW,
        "executionDeadline": NOW + timedelta(minutes=15),
        "revision": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def test_shared_axis_values_match_the_frozen_task_contract() -> None:
    assert tuple(ExecutionLifecycle) == (
        ExecutionLifecycle.QUEUED,
        ExecutionLifecycle.RUNNING,
        ExecutionLifecycle.PAUSE_REQUESTED,
        ExecutionLifecycle.PAUSED,
        ExecutionLifecycle.CANCELING,
        ExecutionLifecycle.FINALIZING,
        ExecutionLifecycle.CLOSED,
    )
    assert {item.value for item in ExecutionQuality} == {
        "PENDING",
        "PASSED",
        "FAILED",
        "BLOCKED",
        "INCONCLUSIVE",
        "INFRA_ERROR",
        "CANCELED",
    }
    assert {item.value for item in ExecutionHygiene} == {
        "NOT_REQUIRED",
        "PENDING",
        "RUNNING",
        "CLEANED",
        "CLEANUP_FAILED",
        "LEAKED",
    }


def test_task_plan_and_version_are_frozen_canonical_facts() -> None:
    plan = TaskPlan(
        id=uid(3),
        tenant_id=uid(1),
        project_id=uid(2),
        task_key="crm.nightly-regression",
        name="CRM nightly regression",
        status=TaskPlanStatus.ACTIVE,
        created_by=uid(5),
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    version = TaskPlanVersion.model_validate(task_plan_version_payload())

    assert version.pinned_case_version_ids == (uid(31), uid(32))
    assert version.content_digest == task_plan_version_payload()["contentDigest"]
    assert version.model_dump(mode="json", by_alias=True)["schemaVersion"] == (
        TASK_PLAN_SCHEMA_VERSION
    )
    with pytest.raises(ValidationError, match="frozen"):
        plan.name = "Changed"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ({"versionRef": "task-plan/invalid@1.2.0"}, "exact TaskPlanVersion"),
        ({"contentDigest": f"sha256:{'0' * 64}"}, "contentDigest"),
        (
            {
                "profileRefs": profile_refs(uid(31)).model_dump(
                    mode="json",
                    by_alias=True,
                )
            },
            "match pinnedCaseVersionIds",
        ),
        ({"publishedAt": NOW - timedelta(seconds=1)}, "publishedAt"),
    ),
)
def test_task_plan_version_rejects_inconsistent_snapshot(
    mutation: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        TaskPlanVersion.model_validate({**task_plan_version_payload(), **mutation})


def test_task_plan_version_revision_is_the_immutable_database_revision() -> None:
    schema = TaskPlanVersion.model_json_schema(by_alias=True)

    assert schema["properties"]["revision"]["const"] == 1
    with pytest.raises(ValidationError, match="Input should be 1"):
        TaskPlanVersion.model_validate({**task_plan_version_payload(), "revision": 2})


def test_task_plan_version_rejects_unknown_or_non_digest_policy_values() -> None:
    payload = task_plan_version_payload()
    with pytest.raises(ValidationError, match="String should match pattern"):
        TaskPlanVersion.model_validate(
            {**payload, "policyDigests": {"Unsafe Policy": POLICY_DIGEST}}
        )
    with pytest.raises(ValidationError, match="String should match pattern"):
        TaskPlanVersion.model_validate({**payload, "policyDigests": {"gate": "latest"}})


def test_publish_task_plan_version_normalizes_and_requires_exact_case_coverage() -> None:
    payload = task_plan_version_payload()
    command = PublishTaskPlanVersion.model_validate(
        {
            "version": payload["version"],
            "pinnedCaseVersionIds": payload["pinnedCaseVersionIds"],
            "matrix": payload["matrix"],
            "profileRefs": payload["profileRefs"],
            "policyDigests": payload["policyDigests"],
            "clientMutationId": "publish-task-plan-001",
        }
    )

    assert command.pinned_case_version_ids == (uid(31), uid(32))
    with pytest.raises(ValidationError, match="match pinnedCaseVersionIds"):
        PublishTaskPlanVersion.model_validate(
            {
                **command.model_dump(mode="json", by_alias=True),
                "profileRefs": profile_refs(uid(31)).model_dump(
                    mode="json",
                    by_alias=True,
                ),
            }
        )


def test_policy_digest_json_schema_constrains_keys_and_values() -> None:
    expected_key_pattern = r"^[a-z][a-z0-9_.-]{1,127}$"
    expected_digest_pattern = r"^sha256:[0-9a-f]{64}$"

    for model in (TaskPlanVersion, TaskRunManifest):
        policy_schema = model.model_json_schema(by_alias=True)["properties"]["policyDigests"]
        assert policy_schema["minProperties"] == 1
        assert policy_schema["maxProperties"] == 64
        assert policy_schema["additionalProperties"] is False
        assert policy_schema["patternProperties"][expected_key_pattern]["pattern"] == (
            expected_digest_pattern
        )
        validator = Draft202012Validator(policy_schema)
        assert list(validator.iter_errors({"gate": POLICY_DIGEST})) == []
        assert list(validator.iter_errors({"Unsafe Policy": POLICY_DIGEST}))
        assert list(validator.iter_errors({"gate": "latest"}))


def test_unit_manifest_rejects_derived_digest_tampering() -> None:
    payload = unit_manifest_payload(case_version_id=uid(301), ordinal=1)
    unit = ExecutionUnitManifest.model_validate(payload)
    assert unit.unit_key == payload["unitKey"]

    with pytest.raises(ValidationError, match="unitKey"):
        ExecutionUnitManifest.model_validate({**payload, "unitKey": f"sha256:{'0' * 64}"})
    with pytest.raises(ValidationError, match="dependencyDigest"):
        ExecutionUnitManifest.model_validate({**payload, "dependencyDigest": f"sha256:{'0' * 64}"})


def test_run_manifest_hash_is_recomputable_and_covers_all_units() -> None:
    manifest = TaskRunManifest.model_validate(run_manifest_payload())

    assert manifest.manifest_hash == manifest.recompute_manifest_hash()
    assert tuple(unit.ordinal for unit in manifest.units) == (1, 2)
    assert tuple(unit.unit_key for unit in manifest.units) == tuple(
        sorted(unit.unit_key for unit in manifest.units)
    )

    with pytest.raises(ValidationError, match="manifestHash"):
        TaskRunManifest.model_validate(
            {**run_manifest_payload(), "manifestHash": f"sha256:{'0' * 64}"}
        )


def test_run_request_digest_excludes_generated_run_identity() -> None:
    first = TaskRunManifest.model_validate(run_manifest_payload())
    second_run_id = uid(499)
    second_hash = task_run_manifest_hash(
        task_run_id=second_run_id,
        task_plan_version_id=first.task_plan_version_id,
        trigger_source=first.trigger_source,
        trigger_fingerprint=first.trigger_fingerprint,
        tenant_id=first.tenant_id,
        project_id=first.project_id,
        iteration_id=first.iteration_id,
        units=first.units,
        policy_digests=first.policy_digests,
        compiler_version=first.compiler_version,
        schema_version=first.schema_version,
        retry_policy=first.retry_policy,
    )
    second = TaskRunManifest(
        **first.model_dump(mode="python", exclude={"task_run_id", "manifest_hash"}),
        task_run_id=second_run_id,
        manifest_hash=second_hash,
    )

    assert first.manifest_hash != second.manifest_hash
    assert first.recompute_request_digest() == second.recompute_request_digest()


def test_run_manifest_rejects_duplicate_unsorted_or_non_contiguous_units() -> None:
    units = ordered_units()

    duplicate_units = (units[0], units[0].model_copy(update={"ordinal": 2}))
    with pytest.raises(ValidationError, match="unitKey values must be unique"):
        TaskRunManifest.model_validate(run_manifest_payload(units=duplicate_units))

    reversed_units = tuple(reversed(units))
    with pytest.raises(ValidationError, match="sorted by unitKey"):
        TaskRunManifest.model_validate(run_manifest_payload(units=reversed_units))

    non_contiguous = (units[0], units[1].model_copy(update={"ordinal": 3}))
    with pytest.raises(ValidationError, match="ordinals must be contiguous"):
        TaskRunManifest.model_validate(run_manifest_payload(units=non_contiguous))


def test_execution_unit_preserves_every_exact_manifest_binding() -> None:
    manifest_unit = ordered_units()[0]
    unit = ExecutionUnit.model_validate(execution_unit_payload(manifest_unit))

    assert unit.ordinal == manifest_unit.ordinal
    assert unit.execution_profile_version_id == (manifest_unit.execution_profile_version_id)
    assert unit.fixture_blueprint_version_id == manifest_unit.fixture_blueprint_version_id
    assert unit.dependency_digest == manifest_unit.dependency_digest

    with pytest.raises(ValidationError, match="dependencyDigest"):
        ExecutionUnit.model_validate(
            {
                **execution_unit_payload(manifest_unit),
                "fixtureBlueprintVersionId": str(uid(9999)),
            }
        )


def test_task_run_requires_ordered_request_queue_and_runtime_times() -> None:
    payload: dict[str, object] = {
        "id": str(uid(401)),
        "tenantId": str(uid(1)),
        "projectId": str(uid(2)),
        "taskPlanVersionId": str(uid(4)),
        "manifestHash": MANIFEST_DIGEST,
        "triggerSource": TaskTriggerSource.MANUAL,
        "triggerFingerprint": "manual:request-123",
        "lifecycle": ExecutionLifecycle.QUEUED,
        "quality": ExecutionQuality.PENDING,
        "hygiene": ExecutionHygiene.PENDING,
        "requestedBy": str(uid(5)),
        "requestedAt": NOW,
        "queuedAt": NOW + timedelta(seconds=1),
        "revision": 1,
        "createdAt": NOW,
        "updatedAt": NOW + timedelta(seconds=1),
    }
    task_run = TaskRun.model_validate(payload)
    assert task_run.trigger_source is TaskTriggerSource.MANUAL

    with pytest.raises(ValidationError, match="requestedAt and queuedAt"):
        TaskRun.model_validate({**payload, "queuedAt": NOW - timedelta(seconds=1)})
    with pytest.raises(ValidationError, match="cannot reference the same"):
        TaskRun.model_validate({**payload, "rerunOfTaskRunId": str(uid(401))})
    with pytest.raises(ValidationError, match="requires rerunOfTaskRunId"):
        TaskRun.model_validate(
            {
                **payload,
                "rerunSelectionMode": TaskRunRerunSelectionMode.INFRA_FAILURES,
            }
        )


def test_infra_rerun_request_has_stable_parent_scoped_trigger_identity() -> None:
    request = RequestTaskRunInfraFailureRerun(
        client_mutation_id="infra-rerun-domain-001"
    )
    first = task_run_infra_rerun_trigger_fingerprint(
        parent_task_run_id=uid(401),
        client_mutation_id=request.client_mutation_id,
    )
    replay = task_run_infra_rerun_trigger_fingerprint(
        parent_task_run_id=uid(401),
        client_mutation_id=request.client_mutation_id,
    )
    other = task_run_infra_rerun_trigger_fingerprint(
        parent_task_run_id=uid(402),
        client_mutation_id=request.client_mutation_id,
    )

    assert first == replay
    assert first.startswith(f"api:infra-rerun:{uid(401)}:")
    assert other != first


def test_task_run_seal_requires_complete_counts_and_deterministic_workflow_identity() -> None:
    workflow_id = task_run_workflow_id(tenant_id=uid(1), task_run_id=uid(401))
    payload: dict[str, object] = {
        "id": str(uid(401)),
        "tenantId": str(uid(1)),
        "projectId": str(uid(2)),
        "taskPlanVersionId": str(uid(4)),
        "manifestHash": MANIFEST_DIGEST,
        "triggerSource": TaskTriggerSource.MANUAL,
        "triggerFingerprint": "manual:request-123",
        "requestDigest": POLICY_DIGEST,
        "materializationState": TaskMaterializationState.SEALED,
        "materializedUnitCount": 2,
        "materializedFirstAttemptCount": 2,
        "materializationSealedAt": NOW + timedelta(seconds=1),
        "lifecycle": ExecutionLifecycle.QUEUED,
        "quality": ExecutionQuality.PENDING,
        "hygiene": ExecutionHygiene.PENDING,
        "requestedBy": str(uid(5)),
        "temporalNamespace": "atlas-prod",
        "temporalWorkflowId": workflow_id,
        "requestedAt": NOW,
        "queuedAt": NOW + timedelta(seconds=1),
        "revision": 2,
        "createdAt": NOW,
        "updatedAt": NOW + timedelta(seconds=1),
    }

    sealed = TaskRun.model_validate(payload)
    assert sealed.materialization_state is TaskMaterializationState.SEALED

    with pytest.raises(ValidationError, match="matching materialization counts"):
        TaskRun.model_validate({**payload, "materializedFirstAttemptCount": 1})
    with pytest.raises(ValidationError, match="deterministic TaskRun identity"):
        TaskRun.model_validate({**payload, "temporalWorkflowId": "atlas-task/run/wrong"})


def test_task_run_schema_exposes_seal_and_temporal_pair_constraints() -> None:
    schema = TaskRun.model_json_schema(by_alias=True)
    validator = Draft202012Validator(schema)
    base: dict[str, object] = {
        "id": str(uid(401)),
        "tenantId": str(uid(1)),
        "projectId": str(uid(2)),
        "taskPlanVersionId": str(uid(4)),
        "manifestHash": MANIFEST_DIGEST,
        "triggerSource": TaskTriggerSource.MANUAL,
        "triggerFingerprint": "manual:request-123",
        "materializationState": TaskMaterializationState.SEALED,
        "lifecycle": ExecutionLifecycle.QUEUED,
        "quality": ExecutionQuality.PENDING,
        "hygiene": ExecutionHygiene.PENDING,
        "requestedBy": str(uid(5)),
        "requestedAt": NOW,
        "queuedAt": NOW,
        "revision": 1,
        "createdAt": NOW,
        "updatedAt": NOW,
    }

    assert tuple(validator.iter_errors(base))
    assert tuple(
        validator.iter_errors(
            {
                **base,
                "materializationState": TaskMaterializationState.MATERIALIZING,
                "materializedUnitCount": 1,
            }
        )
    )
    assert "materializedUnitCount == materializedFirstAttemptCount" in (
        schema["x-atlas-invariants"][0]
    )


@pytest.mark.parametrize(
    "hygiene",
    (
        ExecutionHygiene.PENDING,
        ExecutionHygiene.RUNNING,
        ExecutionHygiene.CLEANUP_FAILED,
    ),
)
def test_closed_execution_allows_unresolved_hygiene(
    hygiene: ExecutionHygiene,
) -> None:
    unit = ordered_units()[0]
    base = execution_unit_payload(unit)
    finalized_at = NOW + timedelta(minutes=2)
    closed_at = NOW + timedelta(minutes=4)
    closed = {
        **base,
        "lifecycle": ExecutionLifecycle.CLOSED,
        "quality": ExecutionQuality.PASSED,
        "hygiene": hygiene,
        "startedAt": NOW + timedelta(minutes=1),
        "finalizedAt": finalized_at,
        "closedAt": closed_at,
        "updatedAt": closed_at,
    }
    projection = ExecutionUnit.model_validate(closed)
    assert projection.quality is ExecutionQuality.PASSED
    assert projection.hygiene is hygiene
    assert projection.cleanup_resolved_at is None


def test_closed_execution_allows_cleanup_to_resolve_after_close() -> None:
    unit = ordered_units()[0]
    base = execution_unit_payload(unit)
    finalized_at = NOW + timedelta(minutes=2)
    closed_at = NOW + timedelta(minutes=3)
    cleanup_at = NOW + timedelta(minutes=4)

    projection = ExecutionUnit.model_validate(
        {
            **base,
            "lifecycle": ExecutionLifecycle.CLOSED,
            "quality": ExecutionQuality.PASSED,
            "hygiene": ExecutionHygiene.CLEANED,
            "startedAt": NOW + timedelta(minutes=1),
            "finalizedAt": finalized_at,
            "cleanupResolvedAt": cleanup_at,
            "closedAt": closed_at,
            "updatedAt": cleanup_at,
        }
    )

    assert projection.closed_at == closed_at
    assert projection.cleanup_resolved_at == cleanup_at


def test_closed_execution_requires_resolved_quality() -> None:
    unit = ordered_units()[0]
    closed_at = NOW + timedelta(minutes=4)

    with pytest.raises(ValidationError, match="requires resolved Quality"):
        ExecutionUnit.model_validate(
            {
                **execution_unit_payload(unit),
                "lifecycle": ExecutionLifecycle.CLOSED,
                "quality": ExecutionQuality.PENDING,
                "hygiene": ExecutionHygiene.PENDING,
                "finalizedAt": None,
                "closedAt": closed_at,
                "updatedAt": closed_at,
            }
        )


def test_quality_and_hygiene_milestones_stay_independent() -> None:
    unit = ordered_units()[0]
    payload = execution_unit_payload(unit)
    finalizing = ExecutionUnit.model_validate(
        {
            **payload,
            "lifecycle": ExecutionLifecycle.FINALIZING,
            "quality": ExecutionQuality.FAILED,
            "hygiene": ExecutionHygiene.RUNNING,
            "startedAt": NOW + timedelta(minutes=1),
            "finalizedAt": NOW + timedelta(minutes=2),
            "updatedAt": NOW + timedelta(minutes=2),
        }
    )
    assert finalizing.quality is ExecutionQuality.FAILED
    assert finalizing.cleanup_resolved_at is None

    with pytest.raises(ValidationError, match="resolved quality requires finalizedAt"):
        ExecutionUnit.model_validate(
            {
                **payload,
                "lifecycle": ExecutionLifecycle.FINALIZING,
                "quality": ExecutionQuality.FAILED,
            }
        )
    with pytest.raises(ValidationError, match="resolved Hygiene"):
        ExecutionUnit.model_validate(
            {
                **payload,
                "lifecycle": ExecutionLifecycle.FINALIZING,
                "hygiene": ExecutionHygiene.CLEANED,
            }
        )
    with pytest.raises(ValidationError, match="unresolved Hygiene"):
        ExecutionUnit.model_validate(
            {
                **payload,
                "lifecycle": ExecutionLifecycle.FINALIZING,
                "hygiene": ExecutionHygiene.CLEANUP_FAILED,
                "cleanupResolvedAt": NOW + timedelta(minutes=2),
            }
        )


def test_unit_attempt_retries_append_new_attempt_numbers() -> None:
    first = UnitAttempt.model_validate(unit_attempt_payload(attempt_number=1))
    retry = UnitAttempt.model_validate(unit_attempt_payload(attempt_number=2))

    assert first.execution_unit_id == retry.execution_unit_id
    assert first.id != retry.id
    assert (first.attempt_number, retry.attempt_number) == (1, 2)

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        UnitAttempt.model_validate(unit_attempt_payload(attempt_number=0))


def test_unit_attempt_requires_ordered_queue_deadline_and_start_times() -> None:
    payload = unit_attempt_payload()
    with pytest.raises(ValidationError, match="executionDeadline must follow queuedAt"):
        UnitAttempt.model_validate({**payload, "executionDeadline": NOW})
    with pytest.raises(ValidationError, match="startedAt cannot predate queuedAt"):
        UnitAttempt.model_validate(
            {
                **payload,
                "lifecycle": ExecutionLifecycle.RUNNING,
                "startedAt": NOW - timedelta(seconds=1),
            }
        )


def test_unit_attempt_temporal_identity_is_namespace_bound_and_deterministic() -> None:
    payload = unit_attempt_payload()
    attempt_id = UUID(str(payload["id"]))
    workflow_id = unit_attempt_workflow_id(
        tenant_id=uid(1),
        unit_attempt_id=attempt_id,
    )

    attempt = UnitAttempt.model_validate(
        {
            **payload,
            "temporalNamespace": "atlas-prod",
            "temporalWorkflowId": workflow_id,
        }
    )
    assert attempt.temporal_workflow_id == workflow_id

    with pytest.raises(ValidationError, match="must be set together"):
        UnitAttempt.model_validate({**payload, "temporalNamespace": "atlas-prod"})
    with pytest.raises(ValidationError, match="deterministic UnitAttempt identity"):
        UnitAttempt.model_validate(
            {
                **payload,
                "temporalNamespace": "atlas-prod",
                "temporalWorkflowId": "atlas-task/attempt/wrong",
            }
        )


def test_unit_attempt_schema_requires_complete_temporal_identity_pair() -> None:
    schema = UnitAttempt.model_json_schema(by_alias=True)
    payload = unit_attempt_payload()
    payload["temporalNamespace"] = "atlas-prod"

    errors = tuple(Draft202012Validator(schema).iter_errors(payload))

    assert errors
    assert "deterministically derived" in schema["x-atlas-invariants"][0]


def test_task_execution_event_requires_complete_attempt_scope() -> None:
    payload: dict[str, object] = {
        "id": str(uid(701)),
        "tenantId": str(uid(1)),
        "projectId": str(uid(2)),
        "taskRunId": str(uid(401)),
        "executionUnitId": str(uid(501)),
        "unitAttemptId": str(uid(601)),
        "seq": 1,
        "eventType": "task.unit-attempt.queued",
        "lifecycle": ExecutionLifecycle.QUEUED,
        "quality": ExecutionQuality.PENDING,
        "hygiene": ExecutionHygiene.PENDING,
        "payload": {"attemptNumber": 1},
        "occurredAt": NOW,
    }
    event = TaskExecutionEvent.model_validate(payload)
    assert event.schema_version == TASK_EXECUTION_EVENT_SCHEMA_VERSION
    assert event.seq == 1
    assert event.model_dump(mode="json", by_alias=True)["unitAttemptId"] == str(uid(601))

    with pytest.raises(ValidationError, match="requires executionUnitId scope"):
        TaskExecutionEvent.model_validate({**payload, "executionUnitId": None})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskExecutionEvent.model_validate({**payload, "secret": "must-not-pass"})


def test_task_execution_event_rejects_payload_over_utf8_byte_limit() -> None:
    payload: dict[str, object] = {
        "id": str(uid(702)),
        "tenantId": str(uid(1)),
        "projectId": str(uid(2)),
        "taskRunId": str(uid(401)),
        "seq": 2,
        "eventType": "task.projection.updated",
        "lifecycle": ExecutionLifecycle.RUNNING,
        "quality": ExecutionQuality.PENDING,
        "hygiene": ExecutionHygiene.RUNNING,
        "payload": {"detail": "界" * 11_000},
        "occurredAt": NOW,
    }

    with pytest.raises(ValidationError, match="must not exceed 32768 bytes"):
        TaskExecutionEvent.model_validate(payload)


def test_task_execution_event_uses_postgresql_json_text_size() -> None:
    event_payload = {str(index): "" for index in range(2_850)}
    compact_size = len(
        json.dumps(
            event_payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    postgresql_text_size = len(
        json.dumps(
            event_payload,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )
    assert compact_size < 32_768 < postgresql_text_size

    with pytest.raises(ValidationError, match="must not exceed 32768 bytes"):
        TaskExecutionEvent.model_validate(
            {
                "id": str(uid(703)),
                "tenantId": str(uid(1)),
                "projectId": str(uid(2)),
                "taskRunId": str(uid(401)),
                "seq": 3,
                "eventType": "task.projection.updated",
                "lifecycle": ExecutionLifecycle.RUNNING,
                "quality": ExecutionQuality.PENDING,
                "hygiene": ExecutionHygiene.RUNNING,
                "payload": event_payload,
                "occurredAt": NOW,
            }
        )


def test_task_execution_event_accounts_for_postgresql_numeric_expansion() -> None:
    event_payload = {str(index): 1e20 for index in range(1_200)}
    python_json_size = len(
        json.dumps(
            event_payload,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    )
    postgresql_text_size = len(
        (
            "{"
            + ", ".join(f"{json.dumps(key)}: 100000000000000000000" for key in event_payload)
            + "}"
        ).encode("utf-8")
    )
    assert python_json_size < 32_768 < postgresql_text_size

    with pytest.raises(ValidationError, match="must not exceed 32768 bytes"):
        TaskExecutionEvent.model_validate(
            {
                "id": str(uid(704)),
                "tenantId": str(uid(1)),
                "projectId": str(uid(2)),
                "taskRunId": str(uid(401)),
                "seq": 4,
                "eventType": "task.projection.updated",
                "lifecycle": ExecutionLifecycle.RUNNING,
                "quality": ExecutionQuality.PENDING,
                "hygiene": ExecutionHygiene.RUNNING,
                "payload": event_payload,
                "occurredAt": NOW,
            }
        )


@pytest.mark.parametrize("invalid_text", ["contains\x00nul", "lone-surrogate:\ud800"])
def test_task_execution_event_rejects_postgresql_incompatible_text(
    invalid_text: str,
) -> None:
    with pytest.raises(ValidationError, match="PostgreSQL jsonb-compatible"):
        TaskExecutionEvent.model_validate(
            {
                "id": str(uid(705)),
                "tenantId": str(uid(1)),
                "projectId": str(uid(2)),
                "taskRunId": str(uid(401)),
                "seq": 5,
                "eventType": "task.projection.updated",
                "lifecycle": ExecutionLifecycle.RUNNING,
                "quality": ExecutionQuality.PENDING,
                "hygiene": ExecutionHygiene.RUNNING,
                "payload": {"detail": invalid_text},
                "occurredAt": NOW,
            }
        )


def test_task_execution_event_json_schema_exposes_version_and_payload_limit() -> None:
    schema = TaskExecutionEvent.model_json_schema(by_alias=True)

    assert schema["properties"]["schemaVersion"] == {
        "const": TASK_EXECUTION_EVENT_SCHEMA_VERSION,
        "default": TASK_EXECUTION_EVENT_SCHEMA_VERSION,
        "title": "Schemaversion",
        "type": "string",
    }
    assert schema["properties"]["payload"]["x-atlas-max-serialized-bytes"] == 32_768


def test_schedule_trigger_fingerprint_uses_canonical_utc_fire_identity() -> None:
    utc_trigger = ScheduleTaskRunTrigger(
        schedule_id="crm.nightly",
        scheduled_fire_time_utc=datetime(2026, 7, 18, 2, 0, tzinfo=UTC),
    )
    offset_trigger = ScheduleTaskRunTrigger(
        schedule_id="crm.nightly",
        scheduled_fire_time_utc=datetime.fromisoformat("2026-07-18T10:00:00+08:00"),
    )

    assert offset_trigger.scheduled_fire_time_utc == utc_trigger.scheduled_fire_time_utc
    assert task_run_trigger_fingerprint(offset_trigger) == task_run_trigger_fingerprint(
        utc_trigger
    )


def test_ci_trigger_identity_excludes_display_metadata_but_includes_rerun() -> None:
    first = CITaskRunTrigger(
        provider="github",
        pipeline_run_id="build-8421",
        job_id="test",
        commit_sha="abcdef1",
        branch="main",
    )
    metadata_changed = first.model_copy(
        update={"commit_sha": "1234567", "branch": "release"}
    )
    rerun = first.model_copy(update={"rerun_index": 1})

    assert task_run_trigger_fingerprint(first) == task_run_trigger_fingerprint(
        metadata_changed
    )
    assert task_run_trigger_fingerprint(first) != task_run_trigger_fingerprint(rerun)


def test_webhook_trigger_and_launch_contract_are_strict_and_versioned() -> None:
    trigger = WebhookTaskRunTrigger(
        source_key="gitlab",
        delivery_id="delivery-001",
        event_type="pipeline.completed",
    )
    command = TriggerTaskPlanVersionRun(
        task_plan_version_id=uid(3),
        client_mutation_id="webhook-trigger-001",
        trigger=trigger,
        retry_policy=retry_policy(),
    )

    assert command.schema_version == "atlas.task-run-trigger/0.1"
    assert task_run_trigger_fingerprint(trigger).startswith("webhook:gitlab:")
    with pytest.raises(ValidationError, match="String should match pattern"):
        WebhookTaskRunTrigger(
            source_key="gitlab",
            delivery_id="delivery\nforged",
        )
