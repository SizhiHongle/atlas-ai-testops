"""Versioned Task Schedule contracts and deterministic calendar projection."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case import canonical_digest
from atlas_testops.domain.case.models import DIGEST_PATTERN
from atlas_testops.domain.task.models import (
    CLIENT_MUTATION_ID_PATTERN,
    SAFE_ERROR_CODE_PATTERN,
    TASK_KEY_PATTERN,
    TaskRetryPolicy,
)

TASK_SCHEDULE_SCHEMA_VERSION: Literal["atlas.task-schedule/0.1"] = "atlas.task-schedule/0.1"
TASK_SCHEDULE_CALENDAR_SCHEMA_VERSION: Literal["atlas.task-schedule-calendar/0.1"] = (
    "atlas.task-schedule-calendar/0.1"
)
TIME_ZONE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+){0,7}$"


class TaskScheduleStatus(StrEnum):
    """Desired product state; pausing never mutates an already started TaskRun."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class TaskScheduleSyncStatus(StrEnum):
    """Public projection of durable Temporal Schedule synchronization."""

    PENDING = "PENDING"
    SYNCED = "SYNCED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"


class TaskScheduleOverlapPolicy(StrEnum):
    """Only the two bounded V1 overlap policies are accepted."""

    QUEUE_ONE = "QUEUE_ONE"
    SKIP = "SKIP"


class TaskScheduleCatchupPolicy(StrEnum):
    """Missed fires can be discarded or collapsed to one bounded action."""

    RUN_ONCE = "RUN_ONCE"
    SKIP = "SKIP"


class TaskScheduleCalendar(FrozenWireModel):
    """Structured minute-resolution calendar mapped directly to Temporal."""

    schema_version: Literal["atlas.task-schedule-calendar/0.1"] = (
        TASK_SCHEDULE_CALENDAR_SCHEMA_VERSION
    )
    minutes: tuple[int, ...] = Field(default=(0,), min_length=1, max_length=60)
    hours: tuple[int, ...] = Field(default=(0,), min_length=1, max_length=24)
    days_of_month: tuple[int, ...] = Field(default=(), max_length=31)
    months: tuple[int, ...] = Field(default=(), max_length=12)
    iso_days_of_week: tuple[int, ...] = Field(default=(), max_length=7)

    @field_validator("minutes")
    @classmethod
    def normalize_minutes(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        """Canonicalize minute values."""

        return _bounded_values(values, minimum=0, maximum=59)

    @field_validator("hours")
    @classmethod
    def normalize_hours(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        """Canonicalize hour values."""

        return _bounded_values(values, minimum=0, maximum=23)

    @field_validator("days_of_month")
    @classmethod
    def normalize_days_of_month(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        """Canonicalize optional month-day restrictions."""

        return _bounded_values(values, minimum=1, maximum=31)

    @field_validator("months")
    @classmethod
    def normalize_months(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        """Canonicalize optional month restrictions."""

        return _bounded_values(values, minimum=1, maximum=12)

    @field_validator("iso_days_of_week")
    @classmethod
    def normalize_iso_days(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        """Canonicalize ISO Monday=1 through Sunday=7 restrictions."""

        return _bounded_values(values, minimum=1, maximum=7)


class CreateTaskSchedule(FrozenWireModel):
    """Create one immutable schedule definition for an exact Plan Version."""

    schedule_key: str = Field(min_length=3, max_length=160, pattern=TASK_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    calendar: TaskScheduleCalendar
    time_zone_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=TIME_ZONE_NAME_PATTERN,
    )
    overlap_policy: TaskScheduleOverlapPolicy = TaskScheduleOverlapPolicy.QUEUE_ONE
    catchup_policy: TaskScheduleCatchupPolicy = TaskScheduleCatchupPolicy.RUN_ONCE
    catchup_window_seconds: int = Field(default=3_600, ge=60, le=604_800)
    jitter_seconds: int = Field(default=0, ge=0, le=3_600)
    iteration_id: str | None = Field(default=None, min_length=3, max_length=160)
    retry_policy: TaskRetryPolicy
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=CLIENT_MUTATION_ID_PATTERN,
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """Persist a non-blank display name."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("time_zone_name")
    @classmethod
    def validate_time_zone(cls, value: str) -> str:
        """Require an installed canonical IANA time zone identifier."""

        try:
            zone = ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("timeZoneName must identify an installed IANA zone") from error
        if zone.key != value:
            raise ValueError("timeZoneName must use its canonical IANA key")
        return value

    @model_validator(mode="after")
    def validate_bounded_policy(self) -> Self:
        """Keep jitter below the finite recovery window."""

        if self.jitter_seconds >= self.catchup_window_seconds:
            raise ValueError("jitterSeconds must be below catchupWindowSeconds")
        return self


class RequestTaskSchedulePause(FrozenWireModel):
    """Pause future Schedule actions with an auditable reason."""

    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=CLIENT_MUTATION_ID_PATTERN,
    )
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        """Reject blank operational reasons."""

        normalized = value.strip()
        if len(normalized) < 3:
            raise ValueError("reason must not be blank")
        return normalized


class RequestTaskScheduleResume(RequestTaskSchedulePause):
    """Resume future Schedule actions after explicit review."""


class TaskSchedule(FrozenWireModel):
    """Database-authoritative Task Schedule and Temporal sync projection."""

    schema_version: Literal["atlas.task-schedule/0.1"] = TASK_SCHEDULE_SCHEMA_VERSION
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_plan_version_id: UUID
    schedule_key: str = Field(min_length=3, max_length=160, pattern=TASK_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    calendar: TaskScheduleCalendar
    time_zone_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=TIME_ZONE_NAME_PATTERN,
    )
    overlap_policy: TaskScheduleOverlapPolicy
    catchup_policy: TaskScheduleCatchupPolicy
    catchup_window_seconds: int = Field(ge=60, le=604_800)
    jitter_seconds: int = Field(ge=0, le=3_600)
    iteration_id: str | None = Field(default=None, min_length=3, max_length=160)
    retry_policy: TaskRetryPolicy
    temporal_namespace: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )
    temporal_schedule_id: str = Field(
        min_length=16,
        max_length=320,
        pattern=r"^atlas-task/schedule/[0-9a-f]{32}/[0-9a-f]{32}$",
    )
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    status: TaskScheduleStatus
    pause_reason: str | None = Field(default=None, max_length=500)
    sync_status: TaskScheduleSyncStatus
    synced_revision: int | None = Field(default=None, ge=1)
    last_sync_error_code: str | None = Field(
        default=None,
        pattern=SAFE_ERROR_CODE_PATTERN,
    )
    next_fire_times_utc: tuple[AwareDatetime, ...] = Field(max_length=5)
    created_by: UUID
    updated_by: UUID
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("next_fire_times_utc")
    @classmethod
    def normalize_next_fire_times(
        cls,
        values: tuple[datetime, ...],
    ) -> tuple[datetime, ...]:
        """Keep the Temporal projection ordered, unique, and UTC."""

        normalized = tuple(value.astimezone(UTC) for value in values)
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("nextFireTimesUtc must be sorted and unique")
        return normalized

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        """Recompute immutable content and validate mutable sync state."""

        expected_digest = task_schedule_content_digest(
            schedule_id=self.id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            task_plan_version_id=self.task_plan_version_id,
            schedule_key=self.schedule_key,
            name=self.name,
            calendar=self.calendar,
            time_zone_name=self.time_zone_name,
            overlap_policy=self.overlap_policy,
            catchup_policy=self.catchup_policy,
            catchup_window_seconds=self.catchup_window_seconds,
            jitter_seconds=self.jitter_seconds,
            iteration_id=self.iteration_id,
            retry_policy=self.retry_policy,
            temporal_namespace=self.temporal_namespace,
        )
        if self.content_digest != expected_digest:
            raise ValueError("contentDigest must match the immutable Schedule definition")
        if self.temporal_schedule_id != task_schedule_temporal_id(
            self.tenant_id,
            self.id,
        ):
            raise ValueError("temporalScheduleId must match the Schedule scope")
        if self.jitter_seconds >= self.catchup_window_seconds:
            raise ValueError("jitterSeconds must be below catchupWindowSeconds")
        if self.status is TaskScheduleStatus.ACTIVE and self.pause_reason is not None:
            raise ValueError("active Schedule cannot retain a pauseReason")
        if self.status is TaskScheduleStatus.PAUSED and self.pause_reason is None:
            raise ValueError("paused Schedule requires a pauseReason")
        if self.sync_status is TaskScheduleSyncStatus.SYNCED:
            if self.synced_revision != self.revision or self.last_sync_error_code is not None:
                raise ValueError("synced Schedule must project its exact current revision")
        elif self.synced_revision is not None and self.synced_revision >= self.revision:
            raise ValueError("unsynced Schedule cannot claim the current revision")
        if (
            self.sync_status
            in {
                TaskScheduleSyncStatus.RETRY_WAIT,
                TaskScheduleSyncStatus.FAILED,
            }
            and self.last_sync_error_code is None
        ):
            raise ValueError("failed Schedule synchronization requires a safe error code")
        if (
            self.sync_status is TaskScheduleSyncStatus.PENDING
            and self.last_sync_error_code is not None
        ):
            raise ValueError("pending Schedule synchronization cannot retain an error")
        if self.updated_at < self.created_at:
            raise ValueError("updatedAt cannot predate createdAt")
        return self


class TaskSchedulePage(FrozenWireModel):
    """Keyset page of schedules bound to one TaskPlanVersion."""

    items: tuple[TaskSchedule, ...]
    next_cursor: str | None = None


def task_schedule_temporal_id(tenant_id: UUID, schedule_id: UUID) -> str:
    """Derive one collision-resistant Temporal Schedule identity."""

    return f"atlas-task/schedule/{tenant_id.hex}/{schedule_id.hex}"


def task_schedule_content_digest(
    *,
    schedule_id: UUID,
    tenant_id: UUID,
    project_id: UUID,
    task_plan_version_id: UUID,
    schedule_key: str,
    name: str,
    calendar: TaskScheduleCalendar,
    time_zone_name: str,
    overlap_policy: TaskScheduleOverlapPolicy,
    catchup_policy: TaskScheduleCatchupPolicy,
    catchup_window_seconds: int,
    jitter_seconds: int,
    iteration_id: str | None,
    retry_policy: TaskRetryPolicy,
    temporal_namespace: str,
) -> str:
    """Digest every immutable definition consumed by Temporal and Run compilation."""

    body: dict[str, JsonValue] = {
        "schemaVersion": TASK_SCHEDULE_SCHEMA_VERSION,
        "scheduleId": str(schedule_id),
        "tenantId": str(tenant_id),
        "projectId": str(project_id),
        "taskPlanVersionId": str(task_plan_version_id),
        "scheduleKey": schedule_key,
        "name": name,
        "calendar": calendar.model_dump(mode="json", by_alias=True),
        "timeZoneName": time_zone_name,
        "overlapPolicy": overlap_policy.value,
        "catchupPolicy": catchup_policy.value,
        "catchupWindowSeconds": catchup_window_seconds,
        "jitterSeconds": jitter_seconds,
        "iterationId": iteration_id,
        "retryPolicy": retry_policy.model_dump(mode="json", by_alias=True),
        "temporalNamespace": temporal_namespace,
        "temporalScheduleId": task_schedule_temporal_id(tenant_id, schedule_id),
    }
    return canonical_digest(body)


def next_task_schedule_fire_times(
    calendar: TaskScheduleCalendar,
    *,
    time_zone_name: str,
    after: datetime,
    count: int = 5,
) -> tuple[datetime, ...]:
    """Compute actual UTC instants, skipping DST gaps and preserving both folds."""

    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    if not 1 <= count <= 10:
        raise ValueError("count must be between 1 and 10")
    try:
        zone = ZoneInfo(time_zone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("time zone is unavailable") from error

    selected_after = after.astimezone(UTC)
    local_start = selected_after.astimezone(zone).date()
    allowed_months = set(calendar.months)
    allowed_month_days = set(calendar.days_of_month)
    allowed_iso_days = set(calendar.iso_days_of_week)
    results: list[datetime] = []
    maximum_date = _add_years(local_start, 25)
    candidate_date = local_start
    while candidate_date <= maximum_date and len(results) < count:
        if (
            (not allowed_months or candidate_date.month in allowed_months)
            and (not allowed_month_days or candidate_date.day in allowed_month_days)
            and (not allowed_iso_days or candidate_date.isoweekday() in allowed_iso_days)
        ):
            for hour in calendar.hours:
                for minute in calendar.minutes:
                    naive = datetime.combine(candidate_date, time(hour, minute))
                    for candidate in _valid_utc_folds(naive, zone):
                        if candidate > selected_after and candidate not in results:
                            results.append(candidate)
            results.sort()
            if len(results) >= count:
                break
        candidate_date += timedelta(days=1)
    if len(results) < count:
        raise ValueError("calendar does not produce enough fires within 25 years")
    return tuple(results[:count])


def _valid_utc_folds(naive: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    values: list[datetime] = []
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        utc_value = local.astimezone(UTC)
        round_trip = utc_value.astimezone(zone)
        if (
            round_trip.replace(tzinfo=None) == naive
            and round_trip.fold == fold
            and utc_value not in values
        ):
            values.append(utc_value)
    return tuple(sorted(values))


def _bounded_values(
    values: tuple[int, ...],
    *,
    minimum: int,
    maximum: int,
) -> tuple[int, ...]:
    normalized = tuple(sorted(set(values)))
    if any(type(value) is not int or not minimum <= value <= maximum for value in values):
        raise ValueError(f"calendar values must be between {minimum} and {maximum}")
    return normalized


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


__all__ = [
    "TASK_SCHEDULE_CALENDAR_SCHEMA_VERSION",
    "TASK_SCHEDULE_SCHEMA_VERSION",
    "CreateTaskSchedule",
    "RequestTaskSchedulePause",
    "RequestTaskScheduleResume",
    "TaskSchedule",
    "TaskScheduleCalendar",
    "TaskScheduleCatchupPolicy",
    "TaskScheduleOverlapPolicy",
    "TaskSchedulePage",
    "TaskScheduleStatus",
    "TaskScheduleSyncStatus",
    "next_task_schedule_fire_times",
    "task_schedule_content_digest",
    "task_schedule_temporal_id",
]
