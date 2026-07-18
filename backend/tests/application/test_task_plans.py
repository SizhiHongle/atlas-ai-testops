"""Application tests for TaskPlan catalog authoring and publication."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.infrastructure.test_task_run_repository import (
    NOW,
    _task_plan_version,
    uid,
)

from atlas_testops.application.access import ActorContext
from atlas_testops.application.task_plans import TaskPlanService
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.platform import Project, ProjectStatus
from atlas_testops.domain.task import (
    CreateTaskPlan,
    PublishTaskPlanVersion,
    TaskPlan,
    TaskPlanVersion,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    IdempotencyReservation,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableCreateResult,
    TaskRunRepository,
)

ACTOR_ID = uid(6)


class RecordingDatabase:
    def __init__(self) -> None:
        self.contexts: list[DatabaseContext] = []

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        yield cast(AsyncConnection[DictRow], object())


class RecordingPlatformRepository:
    def __init__(self, project: Project) -> None:
        self.project = project

    async def get_project(
        self,
        _connection: object,
        project_id: UUID,
    ) -> Project | None:
        return self.project if project_id == self.project.id else None

    async def get_project_for_share(
        self,
        _connection: object,
        project_id: UUID,
    ) -> Project | None:
        return await self.get_project(_connection, project_id)


class RecordingTaskRepository:
    def __init__(self) -> None:
        self.plans: list[TaskPlan] = []
        self.versions: list[TaskPlanVersion] = []
        self.plan_list_calls: list[tuple[UUID, TimeCursor | None, int]] = []
        self.version_list_calls: list[tuple[UUID, TimeCursor | None, int]] = []

    async def create_task_plan(
        self,
        _connection: object,
        plan: TaskPlan,
    ) -> ImmutableCreateResult[TaskPlan]:
        self.plans.append(plan)
        return ImmutableCreateResult(ImmutableCreateKind.CREATED, plan)

    async def get_task_plan(
        self,
        _connection: object,
        task_plan_id: UUID,
        *,
        for_share: bool = False,
    ) -> TaskPlan | None:
        del for_share
        return next((item for item in self.plans if item.id == task_plan_id), None)

    async def list_task_plans(
        self,
        _connection: object,
        *,
        project_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[TaskPlan, ...]:
        self.plan_list_calls.append((project_id, cursor, limit))
        return tuple(item for item in self.plans if item.project_id == project_id)[:limit]

    async def create_task_plan_version(
        self,
        _connection: object,
        version: TaskPlanVersion,
    ) -> ImmutableCreateResult[TaskPlanVersion]:
        self.versions.append(version)
        return ImmutableCreateResult(ImmutableCreateKind.CREATED, version)

    async def get_task_plan_version(
        self,
        _connection: object,
        task_plan_version_id: UUID,
    ) -> TaskPlanVersion | None:
        return next(
            (item for item in self.versions if item.id == task_plan_version_id),
            None,
        )

    async def list_task_plan_versions(
        self,
        _connection: object,
        *,
        task_plan_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[TaskPlanVersion, ...]:
        self.version_list_calls.append((task_plan_id, cursor, limit))
        return tuple(
            item for item in self.versions if item.task_plan_id == task_plan_id
        )[:limit]


class RecordingIdempotencyRepository:
    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], CachedHttpResponse] = {}

    async def reserve(
        self,
        _connection: object,
        *,
        scope: str,
        key: str,
        **_values: object,
    ) -> IdempotencyReservation:
        cached = self.responses.get((scope, key))
        return IdempotencyReservation(
            acquired=cached is None,
            cached_response=cached,
        )

    async def complete(
        self,
        _connection: object,
        *,
        scope: str,
        key: str,
        response: CachedHttpResponse,
        **_values: object,
    ) -> None:
        self.responses[(scope, key)] = response


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def append(
        self,
        _connection: object,
        event: object = None,
        **values: Any,
    ) -> None:
        self.calls.append(values or {"event": event})


def _project(*, status: ProjectStatus = ProjectStatus.ACTIVE) -> Project:
    return Project(
        id=uid(2),
        tenant_id=uid(1),
        project_key="ATLAS",
        name="Atlas",
        status=status,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _actor(*, allowed: bool = True, actor_id: UUID | None = ACTOR_ID) -> ActorContext:
    return ActorContext(
        tenant_id=uid(1),
        actor_id=actor_id,
        request_id="task-plan-service-test",
        development_override=allowed,
    )


def _service(
    *,
    project: Project | None = None,
) -> tuple[
    TaskPlanService,
    RecordingTaskRepository,
    RecordingIdempotencyRepository,
    RecordingSink,
    RecordingSink,
]:
    tasks = RecordingTaskRepository()
    idempotency = RecordingIdempotencyRepository()
    audit = RecordingSink()
    outbox = RecordingSink()
    service = TaskPlanService(
        cast(Database, RecordingDatabase()),
        task_repository=cast(TaskRunRepository, tasks),
        platform_repository=cast(
            PlatformRepository,
            RecordingPlatformRepository(project or _project()),
        ),
        audit_repository=cast(AuditRepository, audit),
        outbox_repository=cast(OutboxRepository, outbox),
        idempotency_repository=cast(IdempotencyRepository, idempotency),
    )
    return service, tasks, idempotency, audit, outbox


def _publish_command() -> PublishTaskPlanVersion:
    version = _task_plan_version()
    return PublishTaskPlanVersion(
        version=version.version,
        pinned_case_version_ids=version.pinned_case_version_ids,
        matrix=version.matrix,
        profile_refs=version.profile_refs,
        policy_digests=version.policy_digests,
        client_mutation_id="publish-task-plan-001",
    )


@pytest.mark.anyio
async def test_create_publish_and_read_task_plan_catalog() -> None:
    service, tasks, _idempotency, audit, outbox = _service()
    actor = _actor()
    create = CreateTaskPlan(
        task_key="crm.nightly",
        name="CRM nightly",
        client_mutation_id="create-task-plan-001",
    )

    created = await service.create(
        actor,
        uid(2),
        create,
        idempotency_key=create.client_mutation_id,
    )
    assert created.status_code == 201
    assert not created.replayed
    assert created.value.created_by == actor.actor_id

    replayed = await service.create(
        actor,
        uid(2),
        create,
        idempotency_key=create.client_mutation_id,
    )
    assert replayed.replayed
    assert replayed.value == created.value
    assert len(tasks.plans) == 1

    page = await service.list_for_project(
        actor,
        uid(2),
        cursor=None,
        limit=25,
    )
    assert page.items == (created.value,)
    assert tasks.plan_list_calls == [(uid(2), None, 26)]
    assert await service.get(actor, created.value.id) == created.value

    published = await service.publish_version(
        actor,
        created.value.id,
        _publish_command(),
        idempotency_key="publish-task-plan-001",
    )
    assert published.status_code == 201
    assert published.value.task_plan_id == created.value.id
    assert published.value.content_digest == (
        published.value.model_copy().content_digest
    )

    history = await service.list_versions(
        actor,
        created.value.id,
        cursor=None,
        limit=25,
    )
    assert history.items == (published.value,)
    assert tasks.version_list_calls == [(created.value.id, None, 26)]
    assert await service.get_version(actor, published.value.id) == published.value
    assert len(audit.calls) == 2
    assert len(outbox.calls) == 2


@pytest.mark.anyio
async def test_task_plan_writes_require_matching_key_operator_and_active_project() -> None:
    service, _tasks, _idempotency, _audit, _outbox = _service()
    command = CreateTaskPlan(
        task_key="crm.nightly",
        name="CRM nightly",
        client_mutation_id="create-task-plan-001",
    )

    with pytest.raises(ApplicationError) as mismatch:
        await service.create(
            _actor(),
            uid(2),
            command,
            idempotency_key="different-task-key",
        )
    assert mismatch.value.error_code is ErrorCode.INVALID_REQUEST

    with pytest.raises(ApplicationError) as forbidden:
        await service.create(
            _actor(actor_id=None),
            uid(2),
            command,
            idempotency_key=command.client_mutation_id,
        )
    assert forbidden.value.error_code is ErrorCode.FORBIDDEN

    archived, _tasks, _idempotency, _audit, _outbox = _service(
        project=_project(status=ProjectStatus.ARCHIVED)
    )
    with pytest.raises(ApplicationError) as inactive:
        await archived.create(
            _actor(),
            uid(2),
            command,
            idempotency_key=command.client_mutation_id,
        )
    assert inactive.value.error_code is ErrorCode.CONFLICT
