"""Real PostgreSQL coverage for public TaskRun query projections."""

import asyncio
from uuid import UUID

import pytest
from pydantic import SecretStr
from tests.integration.test_task_execution_hosts_pg import (
    DATABASE_URL,
    SeededCaseVersion,
    _build_aggregate,
    _seed_published_case_version,
)
from tests.integration.test_task_orchestration_pg import _persist_sealed_aggregate

from atlas_testops.application.access import ActorContext
from atlas_testops.application.task_runs import TaskRunQueryService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.infrastructure.database import Database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


def test_task_run_queries_respect_parent_scope_and_tenant_rls() -> None:
    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=4,
    )
    seeded = _seed_published_case_version(settings)
    asyncio.run(_exercise_queries(settings, seeded))


async def _exercise_queries(settings: Settings, seeded: SeededCaseVersion) -> None:
    aggregate = _build_aggregate(seeded)
    database = Database(settings)
    await database.open()
    try:
        aggregate = await _persist_sealed_aggregate(database, aggregate)
        service = TaskRunQueryService(database)
        actor = ActorContext(
            tenant_id=aggregate.run.tenant_id,
            actor_id=aggregate.run.requested_by,
            request_id="task-run-query-pg",
            development_override=True,
        )

        page = await service.list_for_project(
            actor,
            aggregate.run.project_id,
            cursor=None,
            limit=25,
        )
        assert page.items == (aggregate.run,)
        assert page.next_cursor is None
        with pytest.raises(ApplicationError) as invisible_project:
            await service.list_for_project(
                ActorContext(
                    tenant_id=aggregate.run.tenant_id,
                    actor_id=None,
                    request_id="task-run-query-hidden-project-pg",
                ),
                aggregate.run.project_id,
                cursor=None,
                limit=25,
            )
        assert invisible_project.value.error_code is ErrorCode.NOT_FOUND
        assert await service.get(actor, aggregate.run.id) == aggregate.run
        assert await service.get_manifest(actor, aggregate.run.id) == aggregate.manifest

        units = await service.list_units(
            actor,
            aggregate.run.id,
            after_ordinal=0,
            limit=25,
        )
        assert units.items == (aggregate.unit,)
        attempts = await service.list_attempts(
            actor,
            aggregate.run.id,
            aggregate.unit.id,
            after_attempt_number=0,
            limit=25,
        )
        assert attempts.items == (aggregate.attempt,)
        events = await service.list_events(
            actor,
            aggregate.run.id,
            after_seq=0,
            limit=25,
        )
        assert events.items == ()

        other_tenant_actor = ActorContext(
            tenant_id=UUID(int=999_999),
            actor_id=None,
            request_id="task-run-query-cross-tenant-pg",
            development_override=True,
        )
        with pytest.raises(ApplicationError) as hidden:
            await service.get(other_tenant_actor, aggregate.run.id)
        assert hidden.value.error_code is ErrorCode.NOT_FOUND
    finally:
        await database.close()
