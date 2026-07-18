"""HTTP contract tests for Task Schedule catalog and desired-state commands."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from fastapi.testclient import TestClient

from atlas_testops.api.dependencies import get_task_schedule_service
from atlas_testops.api.security import get_actor
from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.application.task_schedules import TaskScheduleService
from atlas_testops.core.config import Settings
from atlas_testops.domain.task import (
    CreateTaskSchedule,
    RequestTaskSchedulePause,
    RequestTaskScheduleResume,
    TaskRetryPolicy,
    TaskSchedule,
    TaskScheduleCalendar,
    TaskScheduleCatchupPolicy,
    TaskScheduleOverlapPolicy,
    TaskSchedulePage,
    TaskScheduleStatus,
    TaskScheduleSyncStatus,
    task_retry_policy_digest,
    task_schedule_content_digest,
    task_schedule_temporal_id,
)
from atlas_testops.main import create_app

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


def uid(value: int) -> UUID:
    return UUID(int=value)


def retry_policy() -> TaskRetryPolicy:
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


def schedule(*, status: TaskScheduleStatus = TaskScheduleStatus.ACTIVE) -> TaskSchedule:
    schedule_id = uid(4)
    calendar = TaskScheduleCalendar(minutes=(0,), hours=(2,))
    policy = retry_policy()
    digest = task_schedule_content_digest(
        schedule_id=schedule_id,
        tenant_id=uid(1),
        project_id=uid(2),
        task_plan_version_id=uid(3),
        schedule_key="crm.nightly",
        name="CRM nightly",
        calendar=calendar,
        time_zone_name="Asia/Shanghai",
        overlap_policy=TaskScheduleOverlapPolicy.QUEUE_ONE,
        catchup_policy=TaskScheduleCatchupPolicy.RUN_ONCE,
        catchup_window_seconds=3_600,
        jitter_seconds=0,
        iteration_id=None,
        retry_policy=policy,
        temporal_namespace="atlas-task",
    )
    paused = status is TaskScheduleStatus.PAUSED
    return TaskSchedule(
        id=schedule_id,
        tenant_id=uid(1),
        project_id=uid(2),
        task_plan_version_id=uid(3),
        schedule_key="crm.nightly",
        name="CRM nightly",
        calendar=calendar,
        time_zone_name="Asia/Shanghai",
        overlap_policy=TaskScheduleOverlapPolicy.QUEUE_ONE,
        catchup_policy=TaskScheduleCatchupPolicy.RUN_ONCE,
        catchup_window_seconds=3_600,
        jitter_seconds=0,
        retry_policy=policy,
        temporal_namespace="atlas-task",
        temporal_schedule_id=task_schedule_temporal_id(uid(1), schedule_id),
        content_digest=digest,
        status=status,
        pause_reason="维护窗口" if paused else None,
        sync_status=TaskScheduleSyncStatus.PENDING,
        synced_revision=None,
        next_fire_times_utc=(datetime(2026, 7, 18, 18, 0, tzinfo=UTC),),
        created_by=uid(5),
        updated_by=uid(5),
        revision=2 if paused else 1,
        created_at=NOW,
        updated_at=NOW,
    )


class RecordingScheduleService:
    def __init__(self) -> None:
        self.active = schedule()
        self.paused = schedule(status=TaskScheduleStatus.PAUSED)
        self.calls: list[tuple[object, ...]] = []

    async def create(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskSchedule]:
        self.calls.append(("create", *args, kwargs))
        return CommandResult(value=self.active, status_code=201, replayed=False)

    async def list_for_version(
        self,
        *args: object,
        **kwargs: object,
    ) -> TaskSchedulePage:
        self.calls.append(("list", *args, kwargs))
        return TaskSchedulePage(items=(self.active,))

    async def get(self, *args: object) -> TaskSchedule:
        self.calls.append(("get", *args))
        return self.active

    async def pause(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskSchedule]:
        self.calls.append(("pause", *args, kwargs))
        return CommandResult(value=self.paused, status_code=202, replayed=False)

    async def resume(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskSchedule]:
        self.calls.append(("resume", *args, kwargs))
        return CommandResult(
            value=self.active.model_copy(update={"revision": 3}),
            status_code=202,
            replayed=False,
        )


def test_schedule_routes_preserve_headers_and_pause_semantics() -> None:
    service = RecordingScheduleService()
    actor = ActorContext(
        tenant_id=service.active.tenant_id,
        actor_id=service.active.created_by,
        request_id="task-schedule-api-test",
        development_override=True,
    )
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = lambda: actor
    app.dependency_overrides[get_task_schedule_service] = lambda: cast(
        TaskScheduleService,
        service,
    )
    create = CreateTaskSchedule(
        schedule_key="crm.nightly",
        name="CRM nightly",
        calendar=service.active.calendar,
        time_zone_name=service.active.time_zone_name,
        retry_policy=retry_policy(),
        client_mutation_id="schedule-create-001",
    )
    pause = RequestTaskSchedulePause(
        client_mutation_id="schedule-pause-001",
        reason="维护窗口",
    )
    resume = RequestTaskScheduleResume(
        client_mutation_id="schedule-resume-001",
        reason="维护完成",
    )

    with TestClient(app) as client:
        missing_key = client.post(
            f"/v1/task-plan-versions/{service.active.task_plan_version_id}/schedules",
            json=create.model_dump(mode="json", by_alias=True),
        )
        created = client.post(
            f"/v1/task-plan-versions/{service.active.task_plan_version_id}/schedules",
            headers={"Idempotency-Key": create.client_mutation_id},
            json=create.model_dump(mode="json", by_alias=True),
        )
        listed = client.get(
            f"/v1/task-plan-versions/{service.active.task_plan_version_id}/schedules?limit=7"
        )
        loaded = client.get(f"/v1/schedules/{service.active.id}")
        paused = client.post(
            f"/v1/schedules/{service.active.id}:pause",
            headers={
                "If-Match": '"revision-1"',
                "Idempotency-Key": pause.client_mutation_id,
            },
            json=pause.model_dump(mode="json", by_alias=True),
        )
        resumed = client.post(
            f"/v1/schedules/{service.active.id}:resume",
            headers={
                "If-Match": '"revision-2"',
                "Idempotency-Key": resume.client_mutation_id,
            },
            json=resume.model_dump(mode="json", by_alias=True),
        )

    assert missing_key.status_code == 422
    assert created.status_code == 201
    assert created.headers["etag"] == '"revision-1"'
    assert created.headers["location"].endswith(str(service.active.id))
    assert listed.json()["items"][0]["scheduleKey"] == "crm.nightly"
    assert loaded.headers["etag"] == '"revision-1"'
    assert paused.status_code == 202
    assert paused.json()["status"] == "PAUSED"
    assert paused.headers["etag"] == '"revision-2"'
    assert resumed.status_code == 202
    assert resumed.headers["etag"] == '"revision-3"'
    assert [call[0] for call in service.calls] == [
        "create",
        "list",
        "get",
        "pause",
        "resume",
    ]
    assert service.calls[0][-1] == {"idempotency_key": create.client_mutation_id}
    assert service.calls[1][-1] == {"cursor": None, "limit": 7}
    assert service.calls[3][-1] == {
        "expected_revision": 1,
        "idempotency_key": pause.client_mutation_id,
    }
    assert service.calls[4][-1] == {
        "expected_revision": 2,
        "idempotency_key": resume.client_mutation_id,
    }


def test_schedule_commands_require_if_match_and_idempotency_headers() -> None:
    service = RecordingScheduleService()
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = lambda: ActorContext(
        tenant_id=service.active.tenant_id,
        actor_id=service.active.created_by,
        request_id="task-schedule-api-test",
        development_override=True,
    )
    app.dependency_overrides[get_task_schedule_service] = lambda: cast(
        TaskScheduleService,
        service,
    )
    command = RequestTaskSchedulePause(
        client_mutation_id="schedule-pause-002",
        reason="维护窗口",
    )
    with TestClient(app) as client:
        response = client.post(
            f"/v1/schedules/{service.active.id}:pause",
            json=command.model_dump(mode="json", by_alias=True),
        )
    assert response.status_code == 422
    assert service.calls == []
