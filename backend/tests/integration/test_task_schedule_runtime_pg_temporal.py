"""PostgreSQL + Temporal vertical test for one real Schedule-sourced TaskRun."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from os import environ
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid7

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from temporalio.client import (
    Client,
    ScheduleActionExecutionStartWorkflow,
)
from temporalio.worker import Worker
from tests.domain.task.test_schedules import retry_policy
from tests.integration.test_cases_api import (
    RecordingDebugRunDispatcher,
    actor_headers,
)
from tests.integration.test_task_execution_hosts_pg import (
    SeededCaseVersion,
    _build_aggregate,
    _seed_published_case_version,
)
from tests.integration.test_task_plans_api_pg import _insert_profiles

from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.application.task_schedule_fires import (
    TaskScheduleFireService,
)
from atlas_testops.application.task_schedule_sync import (
    TaskScheduleSyncConsumer,
)
from atlas_testops.core.config import Settings
from atlas_testops.domain.task import (
    TASK_RETRY_POLICY_DIGEST_KEY,
    ScheduleTaskRunTrigger,
    TaskScheduleSyncStatus,
    task_run_trigger_fingerprint,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_schedules import (
    TaskScheduleRepository,
)
from atlas_testops.infrastructure.task_intents import (
    TaskIntentDispatcherDatabase,
)
from atlas_testops.main import create_app
from atlas_testops.orchestration.task_schedules import (
    TASK_SCHEDULE_TASK_QUEUE,
    AtlasTaskScheduleTriggerWorkflow,
    TaskScheduleFireActivities,
    TaskScheduleWorkflowPayload,
    TemporalTaskScheduleSynchronizer,
)

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")
TEMPORAL_ADDRESS = environ.get("ATLAS_TEST_TEMPORAL_ADDRESS")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None or TEMPORAL_ADDRESS is None,
        reason="PostgreSQL and Temporal integration endpoints are required",
    ),
]


def _create_schedule(
    settings: Settings,
) -> tuple[SeededCaseVersion, dict[str, str], dict[str, Any]]:
    seeded = _seed_published_case_version(settings)
    aggregate = _build_aggregate(seeded)
    asyncio.run(_insert_profiles(settings, seeded, aggregate))
    headers = actor_headers(str(seeded.tenant_id))
    policy = retry_policy()
    suffix = uuid7().hex[-12:]
    application = create_app(
        settings,
        debug_run_dispatcher=RecordingDebugRunDispatcher(),
    )
    with TestClient(application) as client:
        plan_mutation = f"schedule-runtime-plan-{suffix}"
        created_plan = client.post(
            f"/v1/projects/{seeded.project_id}/task-plans",
            headers={**headers, "Idempotency-Key": plan_mutation},
            json={
                "taskKey": f"schedule.runtime.{suffix}",
                "name": "Schedule runtime integration",
                "clientMutationId": plan_mutation,
            },
        )
        assert created_plan.status_code == 201, created_plan.text

        publish_mutation = f"schedule-runtime-publish-{suffix}"
        published = client.post(
            f"/v1/task-plans/{created_plan.json()['id']}/versions",
            headers={**headers, "Idempotency-Key": publish_mutation},
            json={
                "version": "1.0.0",
                "pinnedCaseVersionIds": [str(seeded.case_version_id)],
                "matrix": aggregate.version.matrix.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "profileRefs": aggregate.version.profile_refs.model_dump(
                    mode="json",
                    by_alias=True,
                ),
                "policyDigests": {
                    **aggregate.version.policy_digests,
                    TASK_RETRY_POLICY_DIGEST_KEY: policy.content_digest,
                },
                "clientMutationId": publish_mutation,
            },
        )
        assert published.status_code == 201, published.text

        create_mutation = f"schedule-runtime-create-{suffix}"
        created = client.post(
            f"/v1/task-plan-versions/{published.json()['id']}/schedules",
            headers={**headers, "Idempotency-Key": create_mutation},
            json={
                "scheduleKey": f"runtime.{suffix}",
                "name": "Runtime Schedule",
                "calendar": {
                    "minutes": [0],
                    "hours": [2],
                    "daysOfMonth": [],
                    "months": [],
                    "isoDaysOfWeek": [],
                },
                "timeZoneName": "Asia/Shanghai",
                "overlapPolicy": "QUEUE_ONE",
                "catchupPolicy": "RUN_ONCE",
                "catchupWindowSeconds": 3600,
                "jitterSeconds": 0,
                "iterationId": "iteration:schedule-runtime",
                "retryPolicy": policy.model_dump(mode="json", by_alias=True),
                "clientMutationId": create_mutation,
            },
        )
        assert created.status_code == 201, created.text
        return seeded, headers, created.json()


async def _sync_and_fire(
    settings: Settings,
    seeded: SeededCaseVersion,
    schedule_json: dict[str, Any],
) -> TaskScheduleWorkflowPayload:
    assert DATABASE_URL is not None
    assert TEMPORAL_ADDRESS is not None
    database = Database(settings)
    dispatcher = TaskIntentDispatcherDatabase(
        _dispatcher_database_url(DATABASE_URL),
        pool_min_size=1,
        pool_max_size=2,
    )
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    synchronizer = TemporalTaskScheduleSynchronizer(
        client,
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=5),
        retry_delay=timedelta(milliseconds=100),
    )
    consumer = TaskScheduleSyncConsumer(
        dispatcher,
        synchronizer,
        dispatcher_id=f"schedule-runtime-{uuid7().hex[-12:]}",
        temporal_namespace=client.namespace,
        batch_size=100,
        lease_duration=timedelta(seconds=60),
        poll_interval=timedelta(milliseconds=100),
        retry_policy=TaskIntentRetryPolicy(
            max_attempts=3,
            initial_backoff=timedelta(milliseconds=100),
            maximum_backoff=timedelta(seconds=1),
        ),
    )
    schedule_id = UUID(schedule_json["id"])
    handle = client.get_schedule_handle(schedule_json["temporalScheduleId"])
    await database.open()
    await dispatcher.open()
    try:
        for _ in range(10):
            await consumer.run_once()
            async with database.transaction(
                DatabaseContext(
                    tenant_id=seeded.tenant_id,
                    actor_id=None,
                    request_id=f"schedule-runtime-read:{schedule_id}",
                )
            ) as connection:
                projection = await TaskScheduleRepository().get(
                    connection,
                    schedule_id,
                )
            assert projection is not None
            if projection.sync_status is TaskScheduleSyncStatus.SYNCED:
                break
        else:
            raise AssertionError("target Schedule did not synchronize")

        activities = TaskScheduleFireActivities(
            TaskScheduleFireService(
                database,
                temporal_namespace=client.namespace,
            )
        )
        before = (await handle.describe()).info.num_actions
        async with Worker(
            client,
            task_queue=TASK_SCHEDULE_TASK_QUEUE,
            workflows=[AtlasTaskScheduleTriggerWorkflow],
            activities=[activities.fire],
        ):
            await handle.trigger(rpc_timeout=timedelta(seconds=5))
            for _ in range(60):
                description = await handle.describe(rpc_timeout=timedelta(seconds=5))
                if description.info.num_actions > before:
                    execution = description.info.recent_actions[-1].action
                    assert isinstance(
                        execution,
                        ScheduleActionExecutionStartWorkflow,
                    )
                    workflow_handle = client.get_workflow_handle_for(
                        AtlasTaskScheduleTriggerWorkflow.run,
                        execution.workflow_id,
                        run_id=execution.first_execution_run_id,
                    )
                    return await workflow_handle.result(rpc_timeout=timedelta(seconds=10))
                await asyncio.sleep(0.1)
        raise AssertionError("Temporal Schedule action did not complete")
    finally:
        with suppress(Exception):
            await handle.delete(rpc_timeout=timedelta(seconds=5))
        await dispatcher.close()
        await database.close()


def _dispatcher_database_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.hostname is None:
        raise ValueError("test database URL has no hostname")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = f"{quote('atlas_dispatcher')}:{quote('atlas_dispatcher')}@{host}"
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def test_real_schedule_dispatch_creates_exact_unified_task_run() -> None:
    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
        temporal_address=TEMPORAL_ADDRESS or "127.0.0.1:7233",
        temporal_namespace="default",
    )
    seeded, headers, schedule_json = _create_schedule(settings)
    result = asyncio.run(_sync_and_fire(settings, seeded, schedule_json))
    assert result.status == "CREATED"
    assert result.task_run_id is not None

    application = create_app(
        settings,
        debug_run_dispatcher=RecordingDebugRunDispatcher(),
    )
    with TestClient(application) as client:
        schedule = client.get(
            f"/v1/schedules/{schedule_json['id']}",
            headers=headers,
        )
        run = client.get(
            f"/v1/task-runs/{result.task_run_id}",
            headers=headers,
        )
    assert schedule.status_code == 200, schedule.text
    assert schedule.json()["syncStatus"] == "SYNCED"
    assert len(schedule.json()["nextFireTimesUtc"]) == 5
    assert run.status_code == 200, run.text
    assert run.json()["triggerSource"] == "SCHEDULE"
    trigger = ScheduleTaskRunTrigger(
        schedule_id=schedule_json["id"],
        scheduled_fire_time_utc=datetime.fromisoformat(result.scheduled_fire_time_utc),
    )
    assert run.json()["triggerFingerprint"] == task_run_trigger_fingerprint(trigger)
    assert run.json()["materializationState"] == "SEALED"
    assert run.json()["materializedUnitCount"] == 1
