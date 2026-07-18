"""Task Schedule contract, digest, time-zone, and DST tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_testops.domain.task import (
    CreateTaskSchedule,
    TaskRetryPolicy,
    TaskSchedule,
    TaskScheduleCalendar,
    TaskScheduleCatchupPolicy,
    TaskScheduleOverlapPolicy,
    TaskScheduleStatus,
    TaskScheduleSyncStatus,
    next_task_schedule_fire_times,
    task_retry_policy_digest,
    task_schedule_content_digest,
    task_schedule_temporal_id,
)

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


def uid(value: int) -> UUID:
    """Build stable UUID fixtures."""

    return UUID(int=value)


def retry_policy() -> TaskRetryPolicy:
    """Build the exact bounded retry policy used by schedules."""

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


def create_command() -> CreateTaskSchedule:
    """Create a daily 02:00 Asia/Shanghai command."""

    return CreateTaskSchedule(
        schedule_key="crm.nightly",
        name="CRM 每夜回归",
        calendar=TaskScheduleCalendar(minutes=(0,), hours=(2,)),
        time_zone_name="Asia/Shanghai",
        retry_policy=retry_policy(),
        client_mutation_id="schedule-create-001",
    )


def schedule_payload() -> dict[str, object]:
    """Build one valid persisted Schedule projection."""

    command = create_command()
    schedule_id = uid(4)
    digest = task_schedule_content_digest(
        schedule_id=schedule_id,
        tenant_id=uid(1),
        project_id=uid(2),
        task_plan_version_id=uid(3),
        schedule_key=command.schedule_key,
        name=command.name,
        calendar=command.calendar,
        time_zone_name=command.time_zone_name,
        overlap_policy=command.overlap_policy,
        catchup_policy=command.catchup_policy,
        catchup_window_seconds=command.catchup_window_seconds,
        jitter_seconds=command.jitter_seconds,
        iteration_id=command.iteration_id,
        retry_policy=command.retry_policy,
        temporal_namespace="atlas-task",
    )
    return {
        "id": str(schedule_id),
        "tenantId": str(uid(1)),
        "projectId": str(uid(2)),
        "taskPlanVersionId": str(uid(3)),
        "scheduleKey": command.schedule_key,
        "name": command.name,
        "calendar": command.calendar.model_dump(mode="json", by_alias=True),
        "timeZoneName": command.time_zone_name,
        "overlapPolicy": command.overlap_policy,
        "catchupPolicy": command.catchup_policy,
        "catchupWindowSeconds": command.catchup_window_seconds,
        "jitterSeconds": command.jitter_seconds,
        "iterationId": None,
        "retryPolicy": command.retry_policy.model_dump(mode="json", by_alias=True),
        "temporalNamespace": "atlas-task",
        "temporalScheduleId": task_schedule_temporal_id(uid(1), schedule_id),
        "contentDigest": digest,
        "status": "ACTIVE",
        "pauseReason": None,
        "syncStatus": "SYNCED",
        "syncedRevision": 1,
        "lastSyncErrorCode": None,
        "nextFireTimesUtc": [
            "2026-07-18T18:00:00Z",
            "2026-07-19T18:00:00Z",
        ],
        "createdBy": str(uid(5)),
        "updatedBy": str(uid(5)),
        "revision": 1,
        "createdAt": NOW.isoformat(),
        "updatedAt": NOW.isoformat(),
    }


def test_calendar_normalizes_and_daily_projection_uses_iana_zone() -> None:
    calendar = TaskScheduleCalendar(minutes=(30, 0, 30), hours=(9, 2, 9))
    assert calendar.minutes == (0, 30)
    assert calendar.hours == (2, 9)

    fires = next_task_schedule_fire_times(
        TaskScheduleCalendar(minutes=(0,), hours=(2,)),
        time_zone_name="Asia/Shanghai",
        after=NOW,
    )
    assert fires == (
        datetime(2026, 7, 18, 18, 0, tzinfo=UTC),
        datetime(2026, 7, 19, 18, 0, tzinfo=UTC),
        datetime(2026, 7, 20, 18, 0, tzinfo=UTC),
        datetime(2026, 7, 21, 18, 0, tzinfo=UTC),
        datetime(2026, 7, 22, 18, 0, tzinfo=UTC),
    )


def test_dst_gap_is_skipped_and_fall_back_preserves_both_real_instants() -> None:
    sunday_0230 = TaskScheduleCalendar(
        minutes=(30,),
        hours=(2,),
        iso_days_of_week=(7,),
    )
    spring = next_task_schedule_fire_times(
        sunday_0230,
        time_zone_name="America/New_York",
        after=datetime(2026, 3, 1, 8, 0, tzinfo=UTC),
        count=2,
    )
    assert spring == (
        datetime(2026, 3, 15, 6, 30, tzinfo=UTC),
        datetime(2026, 3, 22, 6, 30, tzinfo=UTC),
    )

    sunday_0130 = TaskScheduleCalendar(
        minutes=(30,),
        hours=(1,),
        iso_days_of_week=(7,),
    )
    fall = next_task_schedule_fire_times(
        sunday_0130,
        time_zone_name="America/New_York",
        after=datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
        count=2,
    )
    assert fall == (
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        datetime(2026, 11, 1, 6, 30, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minutes", (60,)),
        ("hours", (24,)),
        ("days_of_month", (0,)),
        ("months", (13,)),
        ("iso_days_of_week", (0,)),
    ],
)
def test_calendar_rejects_out_of_range_values(
    field: str,
    value: tuple[int, ...],
) -> None:
    with pytest.raises(ValidationError):
        TaskScheduleCalendar.model_validate({field: value})


def test_create_rejects_unknown_zone_and_unbounded_jitter() -> None:
    payload = create_command().model_dump(mode="python")
    with pytest.raises(ValidationError):
        CreateTaskSchedule.model_validate({**payload, "time_zone_name": "Mars/Olympus"})
    with pytest.raises(ValidationError):
        CreateTaskSchedule.model_validate(
            {
                **payload,
                "catchup_window_seconds": 60,
                "jitter_seconds": 60,
            }
        )


def test_impossible_calendar_fails_closed() -> None:
    with pytest.raises(ValueError, match="does not produce enough fires"):
        next_task_schedule_fire_times(
            TaskScheduleCalendar(
                hours=(2,),
                days_of_month=(30,),
                months=(2,),
            ),
            time_zone_name="UTC",
            after=NOW,
        )


def test_schedule_projection_recomputes_digest_and_sync_invariants() -> None:
    schedule = TaskSchedule.model_validate(schedule_payload())
    assert schedule.status is TaskScheduleStatus.ACTIVE
    assert schedule.sync_status is TaskScheduleSyncStatus.SYNCED
    assert schedule.overlap_policy is TaskScheduleOverlapPolicy.QUEUE_ONE
    assert schedule.catchup_policy is TaskScheduleCatchupPolicy.RUN_ONCE

    payload = schedule_payload()
    payload["contentDigest"] = f"sha256:{'f' * 64}"
    with pytest.raises(ValidationError):
        TaskSchedule.model_validate(payload)

    payload = schedule_payload()
    payload["syncStatus"] = "RETRY_WAIT"
    payload["syncedRevision"] = 1
    payload["lastSyncErrorCode"] = None
    with pytest.raises(ValidationError):
        TaskSchedule.model_validate(payload)


def test_paused_schedule_requires_reason_and_active_schedule_forbids_it() -> None:
    payload = schedule_payload()
    payload.update(
        {
            "status": "PAUSED",
            "pauseReason": "维护窗口",
            "syncStatus": "PENDING",
            "syncedRevision": 1,
        }
    )
    payload["revision"] = 2
    paused = TaskSchedule.model_validate(payload)
    assert paused.status is TaskScheduleStatus.PAUSED

    payload["pauseReason"] = None
    with pytest.raises(ValidationError):
        TaskSchedule.model_validate(payload)
