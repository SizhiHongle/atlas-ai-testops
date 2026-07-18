"""Trusted bridge tests from Fixture Cleanup to Unit Hygiene revisions."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.application.test_result_projection import _closed_attempt, _retry_attempt
from tests.infrastructure.test_task_run_repository import NOW, _aggregate

from atlas_testops.application.result_hygiene import (
    ResultHygieneProjectionError,
    ResultHygieneProjectionService,
)
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.fixture import (
    FixtureCleanupState,
    FixtureResourcePage,
    FixtureRun,
    FixtureRunDetail,
    FixtureRunKind,
    FixtureRunStatus,
    ResourceOwnership,
    ResourceRecord,
    ResourceRecordStatus,
)
from atlas_testops.domain.result import (
    AttemptFixtureBinding,
    DataHygiene,
    UnitHygieneResolutionRevision,
    task_attempt_fixture_execution_id,
)
from atlas_testops.domain.task import ExecutionQuality, ExecutionUnit, UnitAttempt

_DIGEST = "sha256:" + "a" * 64


class _TaskRepository:
    def __init__(self, unit: ExecutionUnit, attempts: tuple[UnitAttempt, ...]) -> None:
        self.unit = unit
        self.attempts = list(attempts)

    async def get_attempt(
        self,
        _connection: object,
        attempt_id: UUID,
    ) -> UnitAttempt | None:
        return next((item for item in self.attempts if item.id == attempt_id), None)

    async def get_unit(
        self,
        _connection: object,
        unit_id: UUID,
    ) -> ExecutionUnit | None:
        return self.unit if self.unit.id == unit_id else None

    async def list_attempts(
        self,
        _connection: object,
        unit_id: UUID,
    ) -> tuple[UnitAttempt, ...]:
        return tuple(self.attempts) if self.unit.id == unit_id else ()

    async def lock_execution_chain(
        self,
        _connection: object,
        *,
        task_run_id: UUID,
        execution_unit_id: UUID | None = None,
        unit_attempt_id: UUID | None = None,
    ) -> None:
        assert task_run_id == self.unit.task_run_id
        assert execution_unit_id in {None, self.unit.id}
        assert unit_attempt_id is None


class _ResultRepository:
    def __init__(self) -> None:
        self.bindings: list[AttemptFixtureBinding] = []
        self.resolutions: list[UnitHygieneResolutionRevision] = []

    async def get_attempt_fixture_binding(
        self,
        _connection: object,
        attempt_id: UUID,
    ) -> AttemptFixtureBinding | None:
        return next(
            (item for item in self.bindings if item.unit_attempt_id == attempt_id),
            None,
        )

    async def get_fixture_binding_by_run(
        self,
        _connection: object,
        fixture_run_id: UUID,
    ) -> AttemptFixtureBinding | None:
        return next(
            (item for item in self.bindings if item.fixture_run_id == fixture_run_id),
            None,
        )

    async def list_fixture_bindings_for_unit(
        self,
        _connection: object,
        execution_unit_id: UUID,
    ) -> tuple[AttemptFixtureBinding, ...]:
        return tuple(item for item in self.bindings if item.execution_unit_id == execution_unit_id)

    async def get_latest_hygiene_resolution(
        self,
        _connection: object,
        execution_unit_id: UUID,
    ) -> UnitHygieneResolutionRevision | None:
        matches = [item for item in self.resolutions if item.execution_unit_id == execution_unit_id]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def insert_attempt_fixture_binding(
        self,
        _connection: object,
        binding: AttemptFixtureBinding,
    ) -> None:
        self.bindings.append(binding)

    async def insert_hygiene_resolution(
        self,
        _connection: object,
        resolution: UnitHygieneResolutionRevision,
    ) -> None:
        self.resolutions.append(resolution)


class _FixtureRepository:
    def __init__(self, runs: tuple[FixtureRun, ...]) -> None:
        self.runs = {item.id: item for item in runs}
        self.resources: dict[UUID, FixtureResourcePage] = {}

    async def get_run(
        self,
        _connection: object,
        run_id: UUID,
    ) -> FixtureRun | None:
        return self.runs.get(run_id)

    async def get_detail(
        self,
        _connection: object,
        run_id: UUID,
    ) -> FixtureRunDetail | None:
        run = self.runs.get(run_id)
        return (
            FixtureRunDetail(
                run=run,
                actor_bindings=(),
                nodes=(),
                attempts=(),
            )
            if run is not None
            else None
        )

    async def list_resources(
        self,
        _connection: object,
        run_id: UUID,
    ) -> FixtureResourcePage:
        return self.resources.get(run_id, FixtureResourcePage(items=()))

    async def get_manifest(self, _connection: object, _run_id: UUID) -> None:
        return None


class _Outbox:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def append(self, _connection: object, event: DomainEvent) -> None:
        self.events.append(event)


def _fixture_run(
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    *,
    cleanup_state: FixtureCleanupState,
    status: FixtureRunStatus,
    revision: int,
) -> FixtureRun:
    return FixtureRun(
        id=uuid4(),
        tenant_id=unit.tenant_id,
        project_id=unit.project_id,
        environment_id=unit.environment_id,
        blueprint_version_id=unit.fixture_blueprint_version_id,
        run_kind=FixtureRunKind.EXECUTION,
        execution_id=task_attempt_fixture_execution_id(attempt.id),
        plan_digest=_DIGEST,
        input_digest=_DIGEST,
        status=status,
        cleanup_state=cleanup_state,
        temporal_workflow_id=f"fixture-task-{attempt.id}",
        requested_by=None,
        execution_deadline=NOW + timedelta(hours=1),
        requested_at=NOW,
        cleanup_generation=1,
        revision=revision,
        updated_at=NOW + timedelta(minutes=revision),
    )


def _resource(run: FixtureRun, status: ResourceRecordStatus) -> ResourceRecord:
    return ResourceRecord(
        id=uuid4(),
        fixture_run_id=run.id,
        data_node_run_id=uuid4(),
        connector_installation_id=uuid4(),
        resource_handle="fr_abcdefghijklmnop",
        resource_type="crm.customer",
        ownership=ResourceOwnership.CREATED,
        status=status,
        expires_at=NOW + timedelta(hours=1),
        cleanup_generation=run.cleanup_generation,
        created_at=NOW,
        cleaned_at=(run.updated_at if status is ResourceRecordStatus.CLEANED else None),
        revision=run.revision,
        updated_at=run.updated_at,
    )


@pytest.mark.anyio
async def test_later_clean_retry_cannot_hide_earlier_fixture_leak() -> None:
    _, _, units, attempts = _aggregate(unit_count=1)
    unit = units[0]
    first = _closed_attempt(attempts[0], quality=ExecutionQuality.INFRA_ERROR)
    second = _retry_attempt(first, quality=ExecutionQuality.PASSED)
    leaked_run = _fixture_run(
        unit,
        first,
        cleanup_state=FixtureCleanupState.LEAKED,
        status=FixtureRunStatus.CLEANUP_FAILED,
        revision=4,
    )
    cleaned_run = _fixture_run(
        unit,
        second,
        cleanup_state=FixtureCleanupState.CLEANED,
        status=FixtureRunStatus.RELEASED,
        revision=5,
    )
    tasks = _TaskRepository(unit, (first,))
    results = _ResultRepository()
    fixtures = _FixtureRepository((leaked_run, cleaned_run))
    fixtures.resources[leaked_run.id] = FixtureResourcePage(
        items=(_resource(leaked_run, ResourceRecordStatus.LEAKED),)
    )
    fixtures.resources[cleaned_run.id] = FixtureResourcePage(
        items=(_resource(cleaned_run, ResourceRecordStatus.CLEANED),)
    )
    outbox = _Outbox()
    service = ResultHygieneProjectionService(
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        fixture_repository=cast(Any, fixtures),
        outbox_repository=cast(Any, outbox),
    )
    connection = cast(AsyncConnection[DictRow], object())

    first_binding = await service.bind_fixture_run(
        connection,
        fixture_run=leaked_run,
        created_at=leaked_run.updated_at,
    )
    tasks.attempts.append(second)
    second_binding = await service.bind_fixture_run(
        connection,
        fixture_run=cleaned_run,
        created_at=cleaned_run.updated_at,
    )
    replay = await service.project_fixture_cleanup(
        connection,
        fixture_run_id=cleaned_run.id,
        created_at=cleaned_run.updated_at,
    )

    assert first_binding is not None
    assert second_binding is not None
    assert replay is not None
    assert replay.data_hygiene is DataHygiene.LEAKED
    assert len(results.resolutions) == 2
    assert [item.data_hygiene for item in replay.inputs] == [
        DataHygiene.LEAKED,
        DataHygiene.CLEANED,
    ]
    assert [event.event_type for event in outbox.events] == [
        "unit_attempt.fixture_bound",
        "unit.hygiene_resolved",
        "unit_attempt.fixture_bound",
        "unit.hygiene_resolved",
    ]


@pytest.mark.anyio
async def test_binding_rejects_wrong_fixture_scope() -> None:
    _, _, units, attempts = _aggregate(unit_count=1)
    unit = units[0]
    closed = _closed_attempt(attempts[0], quality=ExecutionQuality.FAILED)
    fixture_run = _fixture_run(
        unit,
        closed,
        cleanup_state=FixtureCleanupState.NOT_REQUIRED,
        status=FixtureRunStatus.RELEASED,
        revision=2,
    ).model_copy(update={"environment_id": uuid4()})
    service = ResultHygieneProjectionService(
        result_repository=cast(Any, _ResultRepository()),
        task_repository=cast(Any, _TaskRepository(unit, (closed,))),
        fixture_repository=cast(Any, _FixtureRepository((fixture_run,))),
        outbox_repository=cast(Any, _Outbox()),
    )

    with pytest.raises(
        ResultHygieneProjectionError,
        match="RESULT_FIXTURE_BINDING_SCOPE_INVALID",
    ):
        await service.bind_fixture_run(
            cast(AsyncConnection[DictRow], object()),
            fixture_run=fixture_run,
            created_at=fixture_run.updated_at,
        )
