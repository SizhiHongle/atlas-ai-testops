"""Real Temporal checks for Schedule sync, reserved fire identity, and pause."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from os import environ
from uuid import uuid7

import pytest
from temporalio.client import Client, ScheduleActionExecutionStartWorkflow
from temporalio.worker import Worker
from tests.domain.task.test_schedules import retry_policy

from atlas_testops.application.task_intents import TaskIntentInvariantError
from atlas_testops.application.task_schedule_fires import (
    TaskScheduleFireRequest,
    TaskScheduleFireResult,
    TaskScheduleFireStatus,
)
from atlas_testops.domain.task import (
    TaskScheduleCalendar,
    TaskScheduleCatchupPolicy,
    TaskScheduleOverlapPolicy,
    TaskScheduleStatus,
    task_schedule_temporal_id,
)
from atlas_testops.infrastructure.task_schedules import (
    ClaimedTaskScheduleSyncIntent,
)
from atlas_testops.orchestration.task_schedules import (
    TASK_SCHEDULE_TASK_QUEUE,
    AtlasTaskScheduleTriggerWorkflow,
    TaskScheduleFireActivities,
    TemporalTaskScheduleSynchronizer,
)

TEMPORAL_ADDRESS = environ.get("ATLAS_TEST_TEMPORAL_ADDRESS")
DIGEST = "sha256:" + "a" * 64

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        TEMPORAL_ADDRESS is None,
        reason="ATLAS_TEST_TEMPORAL_ADDRESS is not configured",
    ),
]


def _intent(*, namespace: str) -> ClaimedTaskScheduleSyncIntent:
    now = datetime.now(UTC)
    tenant_id = uuid7()
    schedule_id = uuid7()
    return ClaimedTaskScheduleSyncIntent(
        id=uuid7(),
        tenant_id=tenant_id,
        project_id=uuid7(),
        task_schedule_id=schedule_id,
        schedule_revision=1,
        action="CREATE",
        content_digest=DIGEST,
        temporal_namespace=namespace,
        temporal_schedule_id=task_schedule_temporal_id(
            tenant_id,
            schedule_id,
        ),
        claim_token=uuid7(),
        dispatch_revision=1,
        dispatch_attempts=1,
        claim_expires_at=now + timedelta(minutes=3),
        desired_status=TaskScheduleStatus.ACTIVE,
        task_plan_version_id=uuid7(),
        calendar=TaskScheduleCalendar(minutes=(0,), hours=(2,)),
        time_zone_name="Asia/Shanghai",
        overlap_policy=TaskScheduleOverlapPolicy.QUEUE_ONE,
        catchup_policy=TaskScheduleCatchupPolicy.RUN_ONCE,
        catchup_window_seconds=3_600,
        jitter_seconds=0,
        iteration_id="iteration:temporal",
        retry_policy=retry_policy(),
        created_by=uuid7(),
    )


@pytest.mark.anyio
async def test_real_schedule_sync_fire_pause_resume_and_collision_guard() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    selected = _intent(namespace=client.namespace)
    synchronizer = TemporalTaskScheduleSynchronizer(
        client,
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=5),
        retry_delay=timedelta(milliseconds=100),
    )
    fired: list[TaskScheduleFireRequest] = []
    fired_event = asyncio.Event()

    class FireService:
        async def fire(
            self,
            request: TaskScheduleFireRequest,
        ) -> TaskScheduleFireResult:
            fired.append(request)
            fired_event.set()
            return TaskScheduleFireResult(
                status=TaskScheduleFireStatus.CREATED,
                task_run_id=uuid7(),
                scheduled_fire_time_utc=request.scheduled_fire_time_utc,
            )

    activities = TaskScheduleFireActivities(FireService())
    handle = client.get_schedule_handle(selected.temporal_schedule_id)
    try:
        async with Worker(
            client,
            task_queue=TASK_SCHEDULE_TASK_QUEUE,
            workflows=[AtlasTaskScheduleTriggerWorkflow],
            activities=[activities.fire],
        ):
            next_fires = await synchronizer.apply(selected)
            assert len(next_fires) == 5
            assert next_fires == tuple(sorted(next_fires))

            before = (await handle.describe()).info.num_actions
            await handle.trigger(rpc_timeout=timedelta(seconds=5))
            await asyncio.wait_for(fired_event.wait(), timeout=15)
            request = fired[0]
            assert request.tenant_id == selected.tenant_id
            assert request.project_id == selected.project_id
            assert request.task_schedule_id == selected.task_schedule_id
            assert request.content_digest == selected.content_digest
            assert request.scheduled_fire_time_utc.tzinfo is not None
            assert request.workflow_started_at_utc.tzinfo is not None
            for _ in range(50):
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
                    completed = await workflow_handle.result(rpc_timeout=timedelta(seconds=5))
                    assert completed.status == "CREATED"
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Schedule Workflow did not complete")

            paused = replace(
                selected,
                id=uuid7(),
                schedule_revision=2,
                action="PAUSE",
                claim_token=uuid7(),
                dispatch_revision=2,
                dispatch_attempts=1,
                desired_status=TaskScheduleStatus.PAUSED,
            )
            assert len(await synchronizer.apply(paused)) == 5
            assert (await handle.describe()).schedule.state.paused is True

            resumed = replace(
                paused,
                id=uuid7(),
                schedule_revision=3,
                action="RESUME",
                claim_token=uuid7(),
                dispatch_revision=3,
                desired_status=TaskScheduleStatus.ACTIVE,
            )
            assert len(await synchronizer.apply(resumed)) == 5
            assert (await handle.describe()).schedule.state.paused is False

            with pytest.raises(
                TaskIntentInvariantError,
                match="MEMO_MISMATCH",
            ):
                await synchronizer.apply(
                    replace(
                        resumed,
                        id=uuid7(),
                        content_digest="sha256:" + "b" * 64,
                        claim_token=uuid7(),
                        dispatch_revision=4,
                    )
                )
    finally:
        with suppress(Exception):
            await handle.delete(rpc_timeout=timedelta(seconds=5))
