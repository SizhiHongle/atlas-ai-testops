"""Unit tests for deterministic task aggregate persistence and replay handling."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from atlas_testops.domain.task import (
    CaseExecutionProfileRef,
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    ExecutionUnitManifest,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskMatrixDefinition,
    TaskPlan,
    TaskPlanStatus,
    TaskPlanVersion,
    TaskProfileRefs,
    TaskRun,
    TaskRunManifest,
    TaskTriggerSource,
    UnitAttempt,
    execution_unit_dependency_digest,
    execution_unit_key,
    task_plan_version_content_digest,
    task_plan_version_ref,
    task_run_manifest_hash,
    task_run_workflow_id,
    unit_attempt_workflow_id,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    MAX_INITIAL_EXECUTION_UNITS,
    ImmutableCreateKind,
    ImmutableFactConflictError,
    TaskRunRepository,
)

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
POLICY_DIGEST = f"sha256:{'a' * 64}"
OTHER_POLICY_DIGEST = f"sha256:{'c' * 64}"
PARAMETER_DIGEST = f"sha256:{'b' * 64}"
DEFAULT_TASK_RUN_ID = UUID(int=100)


def uid(value: int) -> UUID:
    return UUID(int=value)


class StubCursor:
    """Return deterministic rows without emulating psycopg internals."""

    def __init__(
        self,
        *,
        row: DictRow | None = None,
        rows: tuple[DictRow, ...] = (),
    ) -> None:
        self._row = row
        self._rows = rows

    async def fetchone(self) -> DictRow | None:
        return self._row

    async def fetchall(self) -> list[DictRow]:
        return list(self._rows)


class StubConnection:
    """Record statement order and return preloaded cursors."""

    def __init__(self, *cursors: StubCursor) -> None:
        self._cursors = list(cursors)
        self.calls: list[tuple[str, Sequence[object] | None]] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> StubCursor:
        self.calls.append((query, params))
        return self._cursors.pop(0)


def _row(model: object) -> DictRow:
    return cast(DictRow, model.model_dump(mode="python"))  # type: ignore[attr-defined]


def _version_row(version: TaskPlanVersion) -> DictRow:
    return _row(version)


def _manifest_row(manifest: TaskRunManifest) -> DictRow:
    return _row(manifest)


def _retry_run_row(
    run: TaskRun,
    *,
    materialization_state: TaskMaterializationState = TaskMaterializationState.SEALED,
    lifecycle: ExecutionLifecycle = ExecutionLifecycle.QUEUED,
) -> DictRow:
    return cast(
        DictRow,
        {
            "materialization_state": materialization_state,
            "task_run_lifecycle": lifecycle,
            "temporal_namespace": run.temporal_namespace,
        },
    )


def _retry_unit_row(
    *,
    unit_lifecycle: ExecutionLifecycle = ExecutionLifecycle.QUEUED,
    previous_lifecycle: ExecutionLifecycle = ExecutionLifecycle.CLOSED,
    previous_quality: ExecutionQuality = ExecutionQuality.INFRA_ERROR,
) -> DictRow:
    return cast(
        DictRow,
        {
            "execution_unit_lifecycle": unit_lifecycle,
            "previous_attempt_lifecycle": previous_lifecycle,
            "previous_attempt_quality": previous_quality,
        },
    )


def _task_plan() -> TaskPlan:
    return TaskPlan(
        id=uid(3),
        tenant_id=uid(1),
        project_id=uid(2),
        task_key="crm.nightly",
        name="CRM nightly",
        status=TaskPlanStatus.ACTIVE,
        created_by=uid(4),
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _task_plan_version() -> TaskPlanVersion:
    plan = _task_plan()
    case_version_id = uid(30)
    matrix = TaskMatrixDefinition(
        environment_ids=(uid(40),),
        browser_profile_version_ids=(uid(41),),
        identity_profile_version_ids=(uid(42),),
        data_profile_version_ids=(uid(43),),
    )
    profile_refs = TaskProfileRefs(
        case_profiles=(
            CaseExecutionProfileRef(
                case_version_id=case_version_id,
                execution_profile_version_id=uid(44),
                fixture_blueprint_version_id=uid(45),
            ),
        )
    )
    digest = task_plan_version_content_digest(
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        task_plan_id=plan.id,
        version="1.0.0",
        pinned_case_version_ids=(case_version_id,),
        matrix=matrix,
        profile_refs=profile_refs,
        policy_digests={"gate": POLICY_DIGEST},
    )
    return TaskPlanVersion(
        id=uid(5),
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        task_plan_id=plan.id,
        version="1.0.0",
        version_ref=task_plan_version_ref(plan.id, "1.0.0"),
        pinned_case_version_ids=(case_version_id,),
        matrix=matrix,
        profile_refs=profile_refs,
        policy_digests={"gate": POLICY_DIGEST},
        content_digest=digest,
        published_by=uid(6),
        published_at=NOW,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _manifest_unit(*, case_version_id: UUID, seed: int) -> ExecutionUnitManifest:
    execution_profile_version_id = uid(seed + 1)
    fixture_blueprint_version_id = uid(seed + 2)
    identity_profile_version_id = uid(seed + 3)
    environment_id = uid(seed + 4)
    browser_profile_version_id = uid(seed + 5)
    data_profile_version_id = uid(seed + 6)
    return ExecutionUnitManifest(
        ordinal=1,
        unit_key=execution_unit_key(
            case_version_id=case_version_id,
            environment_id=environment_id,
            browser_profile_version_id=browser_profile_version_id,
            identity_profile_version_id=identity_profile_version_id,
            data_profile_version_id=data_profile_version_id,
            parameter_digest=PARAMETER_DIGEST,
        ),
        case_version_id=case_version_id,
        execution_profile_version_id=execution_profile_version_id,
        fixture_blueprint_version_id=fixture_blueprint_version_id,
        identity_profile_version_id=identity_profile_version_id,
        environment_id=environment_id,
        browser_profile_version_id=browser_profile_version_id,
        data_profile_version_id=data_profile_version_id,
        parameter_digest=PARAMETER_DIGEST,
        dependency_digest=execution_unit_dependency_digest(
            case_version_id=case_version_id,
            execution_profile_version_id=execution_profile_version_id,
            fixture_blueprint_version_id=fixture_blueprint_version_id,
            identity_profile_version_id=identity_profile_version_id,
            environment_id=environment_id,
            browser_profile_version_id=browser_profile_version_id,
            data_profile_version_id=data_profile_version_id,
        ),
    )


def _aggregate(
    *,
    task_run_id: UUID = DEFAULT_TASK_RUN_ID,
    unit_count: int = 2,
    policy_digests: dict[str, str] | None = None,
) -> tuple[
    TaskRun,
    TaskRunManifest,
    tuple[ExecutionUnit, ...],
    tuple[UnitAttempt, ...],
]:
    resolved_policy_digests = {"gate": POLICY_DIGEST} if policy_digests is None else policy_digests
    manifest_units = sorted(
        (
            _manifest_unit(
                case_version_id=uid(70 + index),
                seed=700 + (index * 10),
            )
            for index in range(unit_count)
        ),
        key=lambda unit: unit.unit_key,
    )
    frozen_units = tuple(
        unit.model_copy(update={"ordinal": ordinal})
        for ordinal, unit in enumerate(manifest_units, start=1)
    )
    manifest_hash = task_run_manifest_hash(
        task_run_id=task_run_id,
        task_plan_version_id=uid(5),
        trigger_source=TaskTriggerSource.CI,
        trigger_fingerprint="gha:atlas:build-100",
        tenant_id=uid(1),
        project_id=uid(2),
        iteration_id="iteration:2026-07",
        units=frozen_units,
        policy_digests=resolved_policy_digests,
        compiler_version="0.1.0",
    )
    manifest = TaskRunManifest(
        task_run_id=task_run_id,
        task_plan_version_id=uid(5),
        trigger_source=TaskTriggerSource.CI,
        trigger_fingerprint="gha:atlas:build-100",
        tenant_id=uid(1),
        project_id=uid(2),
        iteration_id="iteration:2026-07",
        units=frozen_units,
        policy_digests=resolved_policy_digests,
        compiler_version="0.1.0",
        manifest_hash=manifest_hash,
    )
    run = TaskRun(
        id=task_run_id,
        tenant_id=uid(1),
        project_id=uid(2),
        task_plan_version_id=uid(5),
        manifest_hash=manifest_hash,
        trigger_source=TaskTriggerSource.CI,
        trigger_fingerprint="gha:atlas:build-100",
        request_digest=manifest.recompute_request_digest(),
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        requested_by=uid(6),
        temporal_namespace="default",
        temporal_workflow_id=task_run_workflow_id(
            tenant_id=uid(1),
            task_run_id=task_run_id,
        ),
        requested_at=NOW,
        queued_at=NOW,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    units = tuple(
        ExecutionUnit(
            id=uid(200 + manifest_unit.ordinal),
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            manifest_hash=manifest_hash,
            lifecycle=ExecutionLifecycle.QUEUED,
            quality=ExecutionQuality.PENDING,
            hygiene=ExecutionHygiene.PENDING,
            revision=1,
            created_at=NOW,
            updated_at=NOW,
            **manifest_unit.model_dump(mode="python"),
        )
        for manifest_unit in frozen_units
    )
    attempts = tuple(
        UnitAttempt(
            id=uid(300 + unit.ordinal),
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            execution_unit_id=unit.id,
            manifest_hash=manifest_hash,
            unit_key=unit.unit_key,
            case_version_id=unit.case_version_id,
            attempt_number=1,
            lifecycle=ExecutionLifecycle.QUEUED,
            quality=ExecutionQuality.PENDING,
            hygiene=ExecutionHygiene.PENDING,
            temporal_namespace=run.temporal_namespace,
            temporal_workflow_id=unit_attempt_workflow_id(
                tenant_id=run.tenant_id,
                unit_attempt_id=uid(300 + unit.ordinal),
            ),
            queued_at=NOW,
            execution_deadline=NOW + timedelta(minutes=15),
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        )
        for unit in units
    )
    return run, manifest, units, attempts


def _task_plan_version_for_manifest(
    manifest: TaskRunManifest,
    *,
    pinned_case_version_ids: tuple[UUID, ...] | None = None,
    matrix: TaskMatrixDefinition | None = None,
    profile_refs: TaskProfileRefs | None = None,
    policy_digests: dict[str, str] | None = None,
) -> TaskPlanVersion:
    plan = _task_plan()
    resolved_pinned_case_ids = (
        tuple(unit.case_version_id for unit in manifest.units)
        if pinned_case_version_ids is None
        else pinned_case_version_ids
    )
    resolved_matrix = matrix or TaskMatrixDefinition(
        environment_ids=tuple(unit.environment_id for unit in manifest.units),
        browser_profile_version_ids=tuple(
            unit.browser_profile_version_id for unit in manifest.units
        ),
        identity_profile_version_ids=tuple(
            unit.identity_profile_version_id for unit in manifest.units
        ),
        data_profile_version_ids=tuple(unit.data_profile_version_id for unit in manifest.units),
    )
    resolved_profile_refs = profile_refs or TaskProfileRefs(
        case_profiles=tuple(
            CaseExecutionProfileRef(
                case_version_id=unit.case_version_id,
                execution_profile_version_id=unit.execution_profile_version_id,
                fixture_blueprint_version_id=unit.fixture_blueprint_version_id,
            )
            for unit in manifest.units
            if unit.case_version_id in resolved_pinned_case_ids
        )
    )
    resolved_policy_digests = (
        dict(manifest.policy_digests) if policy_digests is None else policy_digests
    )
    digest = task_plan_version_content_digest(
        tenant_id=manifest.tenant_id,
        project_id=manifest.project_id,
        task_plan_id=plan.id,
        version="1.0.0",
        pinned_case_version_ids=resolved_pinned_case_ids,
        matrix=resolved_matrix,
        profile_refs=resolved_profile_refs,
        policy_digests=resolved_policy_digests,
    )
    return TaskPlanVersion(
        id=manifest.task_plan_version_id,
        tenant_id=manifest.tenant_id,
        project_id=manifest.project_id,
        task_plan_id=plan.id,
        version="1.0.0",
        version_ref=task_plan_version_ref(plan.id, "1.0.0"),
        pinned_case_version_ids=resolved_pinned_case_ids,
        matrix=resolved_matrix,
        profile_refs=resolved_profile_refs,
        policy_digests=resolved_policy_digests,
        content_digest=digest,
        published_by=uid(6),
        published_at=NOW,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _event(
    run: TaskRun,
    *,
    payload: dict[str, JsonValue] | None = None,
) -> TaskExecutionEvent:
    return TaskExecutionEvent(
        id=uid(900),
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        task_run_id=run.id,
        seq=1,
        event_type="task.queued",
        lifecycle=run.lifecycle,
        quality=run.quality,
        hygiene=run.hygiene,
        payload=payload or {},
        occurred_at=NOW,
    )


def _sealed_run(run: TaskRun, *, unit_count: int) -> TaskRun:
    """Project the row returned by the trusted materialization seal function."""

    return run.model_copy(
        update={
            "materialization_state": TaskMaterializationState.SEALED,
            "materialized_unit_count": unit_count,
            "materialized_first_attempt_count": unit_count,
            "materialization_sealed_at": NOW,
            "revision": run.revision + 1,
            "updated_at": NOW,
        }
    )


@pytest.mark.anyio
async def test_create_plan_and_version_serialize_immutable_snapshots() -> None:
    plan = _task_plan()
    version = _task_plan_version()
    connection = StubConnection(
        StubCursor(row=_row(plan)),
        StubCursor(row=_version_row(version)),
    )
    repository = TaskRunRepository()

    plan_result = await repository.create_task_plan(
        cast(AsyncConnection[DictRow], connection),
        plan,
    )
    version_result = await repository.create_task_plan_version(
        cast(AsyncConnection[DictRow], connection),
        version,
    )

    assert plan_result.kind is ImmutableCreateKind.CREATED
    assert version_result.fact == version
    version_params = connection.calls[1][1]
    assert version_params is not None
    assert version_params[7] == list(version.pinned_case_version_ids)
    assert isinstance(version_params[8], Jsonb)
    assert isinstance(version_params[9], Jsonb)
    assert isinstance(version_params[10], Jsonb)


@pytest.mark.anyio
async def test_plan_and_version_exact_replay_return_existing_but_mutation_conflicts() -> None:
    plan = _task_plan()
    version = _task_plan_version()
    repository = TaskRunRepository()
    exact = StubConnection(
        StubCursor(row=None),
        StubCursor(row=_row(plan)),
        StubCursor(row=None),
        StubCursor(row=_version_row(version)),
    )

    plan_result = await repository.create_task_plan(
        cast(AsyncConnection[DictRow], exact),
        plan,
    )
    version_result = await repository.create_task_plan_version(
        cast(AsyncConnection[DictRow], exact),
        version,
    )

    assert plan_result.kind is ImmutableCreateKind.EXISTING
    assert version_result.kind is ImmutableCreateKind.EXISTING

    conflict = StubConnection(StubCursor(row=None), StubCursor(row=_row(plan)))
    with pytest.raises(ImmutableFactConflictError, match="different content"):
        await repository.create_task_plan(
            cast(AsyncConnection[DictRow], conflict),
            plan.model_copy(update={"name": "Changed"}),
        )


@pytest.mark.anyio
async def test_create_run_uses_fixed_root_manifest_unit_attempt_order() -> None:
    run, manifest, units, attempts = _aggregate()
    sealed_run = _sealed_run(run, unit_count=len(units))
    version = _task_plan_version_for_manifest(manifest)
    connection = StubConnection(
        StubCursor(row=_version_row(version)),
        StubCursor(row=_row(run)),
        StubCursor(row=_manifest_row(manifest)),
        StubCursor(),
        StubCursor(),
        StubCursor(),
        StubCursor(),
        StubCursor(row=_row(sealed_run)),
    )
    repository = TaskRunRepository()

    result = await repository.create_run(
        cast(AsyncConnection[DictRow], connection),
        task_run=run,
        manifest=manifest,
        units=tuple(reversed(units)),
        first_attempts=tuple(reversed(attempts)),
    )

    assert result.kind is ImmutableCreateKind.CREATED
    assert result.task_run == sealed_run
    assert result.manifest == manifest
    statements = [" ".join(query.split()).casefold() for query, _ in connection.calls]
    assert "from atlas.task_plan_version" in statements[0]
    assert "insert into atlas.task_run " in statements[1]
    assert "insert into atlas.task_run_manifest" in statements[2]
    assert all("insert into atlas.execution_unit" in query for query in statements[3:5])
    assert all("insert into atlas.unit_attempt" in query for query in statements[5:7])
    assert "seal_task_run_materialization" in statements[7]
    unit_params = [connection.calls[index][1] for index in (3, 4)]
    attempt_params = [connection.calls[index][1] for index in (5, 6)]
    assert [params[6] for params in unit_params if params is not None] == [1, 2]
    assert [params[8] for params in attempt_params if params is not None] == [1, 1]
    assert all(
        "advisory" not in statement and "update " not in statement for statement in statements
    )


@pytest.mark.anyio
async def test_create_run_limits_synchronous_initial_materialization_before_sql() -> None:
    assert MAX_INITIAL_EXECUTION_UNITS == 64
    run, manifest, units, attempts = _aggregate(unit_count=MAX_INITIAL_EXECUTION_UNITS)
    version = _task_plan_version_for_manifest(manifest)
    within_limit = StubConnection(
        StubCursor(row=_version_row(version)),
        StubCursor(row=_row(run)),
        StubCursor(row=_manifest_row(manifest)),
        *(StubCursor() for _ in range(MAX_INITIAL_EXECUTION_UNITS * 2)),
        StubCursor(row=_row(_sealed_run(run, unit_count=len(units)))),
    )

    result = await TaskRunRepository().create_run(
        cast(AsyncConnection[DictRow], within_limit),
        task_run=run,
        manifest=manifest,
        units=units,
        first_attempts=attempts,
    )

    assert result.kind is ImmutableCreateKind.CREATED
    assert "from atlas.task_plan_version" in within_limit.calls[0][0]
    assert "insert into atlas.task_run" in within_limit.calls[1][0]

    oversized_run, oversized_manifest, oversized_units, oversized_attempts = _aggregate(
        unit_count=MAX_INITIAL_EXECUTION_UNITS + 1
    )
    oversized = StubConnection()
    with pytest.raises(ValueError, match=r"P5-00B1.*limited to 64 ExecutionUnits"):
        await TaskRunRepository().create_run(
            cast(AsyncConnection[DictRow], oversized),
            task_run=oversized_run,
            manifest=oversized_manifest,
            units=oversized_units,
            first_attempts=oversized_attempts,
        )
    assert oversized.calls == []


@pytest.mark.anyio
async def test_create_run_fails_closed_when_task_plan_version_is_unavailable() -> None:
    run, manifest, units, attempts = _aggregate()
    connection = StubConnection(StubCursor(row=None))

    with pytest.raises(ValueError, match="TaskPlanVersion is missing or outside"):
        await TaskRunRepository().create_run(
            cast(AsyncConnection[DictRow], connection),
            task_run=run,
            manifest=manifest,
            units=units,
            first_attempts=attempts,
        )

    assert len(connection.calls) == 1
    assert "from atlas.task_plan_version" in connection.calls[0][0]


@pytest.mark.anyio
async def test_create_run_rejects_plan_policy_drift_before_run_insert() -> None:
    run, manifest, units, attempts = _aggregate()
    version = _task_plan_version_for_manifest(
        manifest,
        policy_digests={"gate": OTHER_POLICY_DIGEST},
    )
    connection = StubConnection(StubCursor(row=_version_row(version)))

    with pytest.raises(ValueError, match="policyDigests must cover every stored"):
        await TaskRunRepository().create_run(
            cast(AsyncConnection[DictRow], connection),
            task_run=run,
            manifest=manifest,
            units=units,
            first_attempts=attempts,
        )

    assert len(connection.calls) == 1
    assert all("insert into atlas.task_run " not in query for query, _ in connection.calls)


@pytest.mark.anyio
async def test_create_run_allows_additional_resolved_manifest_policy_digests() -> None:
    run, manifest, units, attempts = _aggregate(
        unit_count=1,
        policy_digests={
            "gate": POLICY_DIGEST,
            "browser.image": OTHER_POLICY_DIGEST,
        },
    )
    version = _task_plan_version_for_manifest(
        manifest,
        policy_digests={"gate": POLICY_DIGEST},
    )
    connection = StubConnection(
        StubCursor(row=_version_row(version)),
        StubCursor(row=_row(run)),
        StubCursor(row=_manifest_row(manifest)),
        StubCursor(),
        StubCursor(),
        StubCursor(row=_row(_sealed_run(run, unit_count=len(units)))),
    )

    result = await TaskRunRepository().create_run(
        cast(AsyncConnection[DictRow], connection),
        task_run=run,
        manifest=manifest,
        units=units,
        first_attempts=attempts,
    )

    assert result.kind is ImmutableCreateKind.CREATED
    assert result.manifest.policy_digests["browser.image"] == OTHER_POLICY_DIGEST


@pytest.mark.anyio
async def test_create_run_rejects_unpinned_manifest_case_before_run_insert() -> None:
    run, manifest, units, attempts = _aggregate()
    version = _task_plan_version_for_manifest(
        manifest,
        pinned_case_version_ids=(manifest.units[0].case_version_id,),
    )
    connection = StubConnection(StubCursor(row=_version_row(version)))

    with pytest.raises(ValueError, match="caseVersionId is not pinned"):
        await TaskRunRepository().create_run(
            cast(AsyncConnection[DictRow], connection),
            task_run=run,
            manifest=manifest,
            units=units,
            first_attempts=attempts,
        )

    assert len(connection.calls) == 1
    assert all("insert into atlas.task_run " not in query for query, _ in connection.calls)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("profile_field", "error_field"),
    (
        ("execution_profile_version_id", "executionProfileVersionId"),
        ("fixture_blueprint_version_id", "fixtureBlueprintVersionId"),
    ),
)
async def test_create_run_rejects_profile_drift_before_run_insert(
    profile_field: str,
    error_field: str,
) -> None:
    run, manifest, units, attempts = _aggregate()
    base_version = _task_plan_version_for_manifest(manifest)
    profiles = list(base_version.profile_refs.case_profiles)
    profiles[0] = profiles[0].model_copy(update={profile_field: uid(9999)})
    version = _task_plan_version_for_manifest(
        manifest,
        profile_refs=TaskProfileRefs(case_profiles=tuple(profiles)),
    )
    connection = StubConnection(StubCursor(row=_version_row(version)))

    with pytest.raises(ValueError, match=error_field):
        await TaskRunRepository().create_run(
            cast(AsyncConnection[DictRow], connection),
            task_run=run,
            manifest=manifest,
            units=units,
            first_attempts=attempts,
        )

    assert len(connection.calls) == 1
    assert all("insert into atlas.task_run " not in query for query, _ in connection.calls)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("matrix_field", "error_field"),
    (
        ("environment_ids", "environmentId"),
        ("browser_profile_version_ids", "browserProfileVersionId"),
        ("identity_profile_version_ids", "identityProfileVersionId"),
        ("data_profile_version_ids", "dataProfileVersionId"),
    ),
)
async def test_create_run_rejects_matrix_escape_before_run_insert(
    matrix_field: str,
    error_field: str,
) -> None:
    run, manifest, units, attempts = _aggregate()
    base_version = _task_plan_version_for_manifest(manifest)
    matrix_values = {
        "environment_ids": base_version.matrix.environment_ids,
        "browser_profile_version_ids": base_version.matrix.browser_profile_version_ids,
        "identity_profile_version_ids": base_version.matrix.identity_profile_version_ids,
        "data_profile_version_ids": base_version.matrix.data_profile_version_ids,
    }
    matrix_values[matrix_field] = getattr(base_version.matrix, matrix_field)[:1]
    version = _task_plan_version_for_manifest(
        manifest,
        matrix=TaskMatrixDefinition.model_validate(matrix_values),
    )
    connection = StubConnection(StubCursor(row=_version_row(version)))

    with pytest.raises(ValueError, match=error_field):
        await TaskRunRepository().create_run(
            cast(AsyncConnection[DictRow], connection),
            task_run=run,
            manifest=manifest,
            units=units,
            first_attempts=attempts,
        )

    assert len(connection.calls) == 1
    assert all("insert into atlas.task_run " not in query for query, _ in connection.calls)


@pytest.mark.anyio
async def test_trigger_collision_uses_stable_request_digest_without_child_inserts() -> None:
    stored_run, stored_manifest, _, _ = _aggregate()
    run, manifest, units, attempts = _aggregate(task_run_id=uid(101))
    run = run.model_copy(update={"requested_by": uid(999)})
    version = _task_plan_version_for_manifest(manifest)
    connection = StubConnection(
        StubCursor(row=_version_row(version)),
        StubCursor(row=None),
        StubCursor(row=_row(stored_run)),
        StubCursor(row=_manifest_row(stored_manifest)),
    )

    result = await TaskRunRepository().create_run(
        cast(AsyncConnection[DictRow], connection),
        task_run=run,
        manifest=manifest,
        units=units,
        first_attempts=attempts,
    )

    assert result.kind is ImmutableCreateKind.EXISTING
    assert result.task_run == stored_run
    assert result.manifest == stored_manifest
    assert len(connection.calls) == 4
    assert "trigger_source = %s" in connection.calls[2][0]
    assert "on conflict (tenant_id, trigger_source, trigger_fingerprint)" in (
        " ".join(connection.calls[1][0].split())
    )

@pytest.mark.anyio
async def test_trigger_collision_with_different_request_digest_is_immutable_conflict() -> None:
    stored_run, stored_manifest, _, _ = _aggregate()
    run, manifest, units, attempts = _aggregate(
        task_run_id=uid(102),
        policy_digests={"gate": OTHER_POLICY_DIGEST},
    )
    version = _task_plan_version_for_manifest(manifest)
    connection = StubConnection(
        StubCursor(row=_version_row(version)),
        StubCursor(row=None),
        StubCursor(row=_row(stored_run)),
        StubCursor(row=_manifest_row(stored_manifest)),
    )

    with pytest.raises(ImmutableFactConflictError, match="different immutable run input"):
        await TaskRunRepository().create_run(
            cast(AsyncConnection[DictRow], connection),
            task_run=run,
            manifest=manifest,
            units=units,
            first_attempts=attempts,
        )

    assert len(connection.calls) == 4


@pytest.mark.anyio
async def test_trigger_collision_with_different_rerun_lineage_is_immutable_conflict() -> None:
    stored_run, stored_manifest, _, _ = _aggregate()
    run, manifest, units, attempts = _aggregate(task_run_id=uid(102))
    run = run.model_copy(update={"rerun_of_task_run_id": uid(9999)})
    version = _task_plan_version_for_manifest(manifest)
    connection = StubConnection(
        StubCursor(row=_version_row(version)),
        StubCursor(row=None),
        StubCursor(row=_row(stored_run)),
        StubCursor(row=_manifest_row(stored_manifest)),
    )

    with pytest.raises(ImmutableFactConflictError, match="different immutable run input"):
        await TaskRunRepository().create_run(
            cast(AsyncConnection[DictRow], connection),
            task_run=run,
            manifest=manifest,
            units=units,
            first_attempts=attempts,
        )

    assert len(connection.calls) == 4


@pytest.mark.anyio
async def test_create_attempt_and_event_allow_only_exact_replay() -> None:
    run, _, units, attempts = _aggregate()
    attempt_id = uid(999)
    attempt = attempts[0].model_copy(
        update={
            "attempt_number": 2,
            "id": attempt_id,
            "temporal_workflow_id": unit_attempt_workflow_id(
                tenant_id=attempts[0].tenant_id,
                unit_attempt_id=attempt_id,
            ),
        }
    )
    event = _event(run)
    repository = TaskRunRepository()
    created = StubConnection(
        StubCursor(row=None),
        StubCursor(row=_retry_run_row(run)),
        StubCursor(row=_retry_unit_row()),
        StubCursor(row=_row(attempt)),
        StubCursor(row=_row(event)),
    )

    attempt_created = await repository.create_attempt(
        cast(AsyncConnection[DictRow], created),
        attempt,
    )
    event_created = await repository.append_event(
        cast(AsyncConnection[DictRow], created),
        event,
    )
    assert attempt_created.kind is ImmutableCreateKind.CREATED
    assert event_created.kind is ImmutableCreateKind.CREATED

    replay = StubConnection(
        StubCursor(row=_row(attempt)),
        StubCursor(row=None),
        StubCursor(row=_row(event)),
    )
    assert (
        await repository.create_attempt(
            cast(AsyncConnection[DictRow], replay),
            attempt,
        )
    ).kind is ImmutableCreateKind.EXISTING
    assert (
        await repository.append_event(
            cast(AsyncConnection[DictRow], replay),
            event,
        )
    ).kind is ImmutableCreateKind.EXISTING

    conflicting_event = _event(run, payload={"different": True})
    conflict = StubConnection(
        StubCursor(row=None),
        StubCursor(row=_row(event)),
    )
    with pytest.raises(ImmutableFactConflictError, match="different immutable content"):
        await repository.append_event(
            cast(AsyncConnection[DictRow], conflict),
            conflicting_event,
        )
    assert units[0].task_run_id == attempt.task_run_id


@pytest.mark.anyio
async def test_advanced_attempt_replays_creation_without_requiring_stale_state() -> None:
    _, _, _, attempts = _aggregate()
    requested = attempts[0]
    advanced = requested.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": NOW + timedelta(seconds=1),
            "revision": 2,
            "updated_at": NOW + timedelta(seconds=1),
        }
    )
    connection = StubConnection(
        StubCursor(row=_row(advanced)),
    )

    result = await TaskRunRepository().create_attempt(
        cast(AsyncConnection[DictRow], connection),
        requested,
    )

    assert result.kind is ImmutableCreateKind.EXISTING
    assert result.fact == advanced


@pytest.mark.anyio
async def test_attempt_number_collision_is_an_explicit_immutable_conflict() -> None:
    _, _, _, attempts = _aggregate()
    candidate_id = uid(991)
    candidate = attempts[0].model_copy(
        update={
            "id": candidate_id,
            "attempt_number": 2,
            "temporal_workflow_id": unit_attempt_workflow_id(
                tenant_id=attempts[0].tenant_id,
                unit_attempt_id=candidate_id,
            ),
        }
    )
    stored_id = uid(992)
    stored = candidate.model_copy(
        update={
            "id": stored_id,
            "temporal_workflow_id": unit_attempt_workflow_id(
                tenant_id=candidate.tenant_id,
                unit_attempt_id=stored_id,
            ),
        }
    )
    connection = StubConnection(
        StubCursor(row=_row(stored)),
    )

    with pytest.raises(ImmutableFactConflictError, match="different immutable content"):
        await TaskRunRepository().create_attempt(
            cast(AsyncConnection[DictRow], connection),
            candidate,
        )

    query, params = connection.calls[0]
    assert "execution_unit_id = %s and attempt_number = %s" in query
    assert params is not None
    assert params == (candidate.execution_unit_id, candidate.attempt_number)


@pytest.mark.anyio
async def test_new_retry_requires_sealed_dispatchable_parent_and_closed_attempt() -> None:
    run, _, units, attempts = _aggregate()
    attempt_id = uid(993)
    candidate = attempts[0].model_copy(
        update={
            "id": attempt_id,
            "attempt_number": 2,
            "temporal_workflow_id": unit_attempt_workflow_id(
                tenant_id=attempts[0].tenant_id,
                unit_attempt_id=attempt_id,
            ),
        }
    )

    unsealed = StubConnection(
        StubCursor(row=None),
        StubCursor(
            row=_retry_run_row(
                run,
                materialization_state=TaskMaterializationState.MATERIALIZING,
            )
        ),
    )
    with pytest.raises(ValueError, match="SEALED TaskRun"):
        await TaskRunRepository().create_attempt(
            cast(AsyncConnection[DictRow], unsealed),
            candidate,
        )

    unfinished = StubConnection(
        StubCursor(row=None),
        StubCursor(row=_retry_run_row(run)),
        StubCursor(
            row=_retry_unit_row(
                previous_lifecycle=ExecutionLifecycle.RUNNING,
                previous_quality=ExecutionQuality.PENDING,
            )
        ),
    )
    with pytest.raises(ValueError, match="closed retryable previous Attempt"):
        await TaskRunRepository().create_attempt(
            cast(AsyncConnection[DictRow], unfinished),
            candidate,
        )
    assert all("for update" not in query.casefold() for query, _ in unfinished.calls)
    assert units[0].id == candidate.execution_unit_id


@pytest.mark.anyio
async def test_repository_reads_roots_units_attempts_and_event_pages() -> None:
    plan = _task_plan()
    version = _task_plan_version()
    run, manifest, units, attempts = _aggregate()
    event = _event(run)
    connection = StubConnection(
        StubCursor(row=_row(plan)),
        StubCursor(row=_version_row(version)),
        StubCursor(row=_row(run)),
        StubCursor(row=_manifest_row(manifest)),
        StubCursor(row=_row(units[0])),
        StubCursor(rows=tuple(_row(unit) for unit in units)),
        StubCursor(row=_row(attempts[0])),
        StubCursor(rows=tuple(_row(attempt) for attempt in attempts)),
        StubCursor(rows=(_row(event),)),
    )
    repository = TaskRunRepository()
    database = cast(AsyncConnection[DictRow], connection)

    assert await repository.get_task_plan(database, plan.id) == plan
    assert await repository.get_task_plan_version(database, version.id) == version
    assert await repository.get_run(database, run.id) == run
    assert await repository.get_manifest(database, run.id) == manifest
    assert await repository.get_unit(database, units[0].id) == units[0]
    assert await repository.list_units(database, run.id) == units
    assert await repository.get_attempt(database, attempts[0].id) == attempts[0]
    assert await repository.list_attempts(database, units[0].id) == attempts
    assert await repository.list_events(
        database,
        task_run_id=run.id,
        after_seq=0,
        limit=50,
    ) == (event,)
    assert "order by ordinal, id" in connection.calls[5][0]
    assert "order by attempt_number, id" in connection.calls[7][0]
    assert connection.calls[8][1] == (run.id, 0, 50)


def test_initial_aggregate_requires_one_matching_attempt_per_manifest_unit() -> None:
    run, manifest, units, _ = _aggregate()
    with pytest.raises(ValueError, match="one initial UnitAttempt"):
        TaskRunRepository._validate_initial_aggregate(
            task_run=run,
            manifest=manifest,
            units=units,
            first_attempts=(),
        )
