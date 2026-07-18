"""Temporal Schedule synchronization and trusted nominal-fire orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from re import fullmatch
from typing import Any, Protocol, cast
from uuid import UUID

from temporalio import activity, workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleDescription,
    ScheduleHandle,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError as TemporalApplicationError
from temporalio.service import RPCError, RPCStatusCode

from atlas_testops.application.task_intents import (
    TaskIntentInvariantError,
    TaskIntentTransientError,
)
from atlas_testops.core.errors import ApplicationError
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

TASK_SCHEDULE_TRIGGER_WORKFLOW_NAME = "AtlasTaskScheduleTriggerWorkflow"
TASK_SCHEDULE_TASK_QUEUE = "atlas-task-schedule"
TASK_SCHEDULE_FIRE_ACTIVITY = "atlas.fire-task-schedule/0.1"
TASK_SCHEDULE_WORKFLOW_INPUT_SCHEMA = "atlas.task-schedule-workflow-input/0.1"
TASK_SCHEDULE_FIRE_INPUT_SCHEMA = "atlas.task-schedule-fire-input/0.1"
TASK_SCHEDULE_WORKFLOW_RESULT_SCHEMA = "atlas.task-schedule-workflow-result/0.1"
TASK_SCHEDULE_MEMO_KEY = "atlasTaskSchedule"
TASK_SCHEDULE_MEMO_SCHEMA = "atlas.task-schedule-temporal-identity/0.1"

_SCHEDULED_BY_SEARCH_ATTRIBUTE = "TemporalScheduledById"
_SCHEDULED_START_SEARCH_ATTRIBUTE = "TemporalScheduledStartTime"
_SHA256_DIGEST_PATTERN = r"sha256:[0-9a-f]{64}"
_SCHEDULE_WORKFLOW_TIMEOUT = timedelta(minutes=15)
_SCHEDULE_WORKFLOW_TASK_TIMEOUT = timedelta(seconds=10)
_SCHEDULE_FIRE_ACTIVITY_TIMEOUT = timedelta(seconds=30)
_SCHEDULE_FIRE_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=8,
)
_PERMANENT_RPC_ERROR_CODES = {
    RPCStatusCode.INVALID_ARGUMENT: "TEMPORAL_SCHEDULE_INVALID_ARGUMENT",
    RPCStatusCode.PERMISSION_DENIED: "TEMPORAL_SCHEDULE_PERMISSION_DENIED",
    RPCStatusCode.UNAUTHENTICATED: "TEMPORAL_SCHEDULE_UNAUTHENTICATED",
    RPCStatusCode.NOT_FOUND: "TEMPORAL_SCHEDULE_NOT_FOUND",
    RPCStatusCode.FAILED_PRECONDITION: "TEMPORAL_SCHEDULE_FAILED_PRECONDITION",
    RPCStatusCode.OUT_OF_RANGE: "TEMPORAL_SCHEDULE_OUT_OF_RANGE",
    RPCStatusCode.UNIMPLEMENTED: "TEMPORAL_SCHEDULE_UNIMPLEMENTED",
    RPCStatusCode.DATA_LOSS: "TEMPORAL_SCHEDULE_DATA_LOSS",
}
_TRANSIENT_RPC_STATUSES = {
    RPCStatusCode.CANCELLED,
    RPCStatusCode.UNKNOWN,
    RPCStatusCode.DEADLINE_EXCEEDED,
    RPCStatusCode.RESOURCE_EXHAUSTED,
    RPCStatusCode.ABORTED,
    RPCStatusCode.INTERNAL,
    RPCStatusCode.UNAVAILABLE,
}


@dataclass(frozen=True, slots=True)
class TaskScheduleWorkflowInput:
    """Immutable, secret-free identity installed in the Schedule action."""

    tenant_id: str
    project_id: str
    task_schedule_id: str
    content_digest: str
    temporal_schedule_id: str
    schema_version: str = TASK_SCHEDULE_WORKFLOW_INPUT_SCHEMA


@dataclass(frozen=True, slots=True)
class TaskScheduleFireActivityInput:
    """Nominal fire identity derived from Temporal-reserved search attributes."""

    schedule: TaskScheduleWorkflowInput
    scheduled_fire_time_utc: str
    workflow_started_at_utc: str
    schema_version: str = TASK_SCHEDULE_FIRE_INPUT_SCHEMA


@dataclass(frozen=True, slots=True)
class TaskScheduleWorkflowPayload:
    """Bounded Schedule Workflow result without a TaskRun document."""

    status: str
    task_run_id: str | None
    scheduled_fire_time_utc: str
    schema_version: str = TASK_SCHEDULE_WORKFLOW_RESULT_SCHEMA


class TaskScheduleFirePort(Protocol):
    """Database-authoritative Schedule fire boundary used by one Activity."""

    async def fire(self, request: Any) -> Any: ...


class TaskScheduleFireActivities:
    """Convert Temporal payloads and application failures at the Activity edge."""

    def __init__(self, service: TaskScheduleFirePort) -> None:
        self._service = service

    @activity.defn(name=TASK_SCHEDULE_FIRE_ACTIVITY)
    async def fire(
        self,
        request: TaskScheduleFireActivityInput,
    ) -> TaskScheduleWorkflowPayload:
        from atlas_testops.application.task_schedule_fires import (
            TaskScheduleFireInvariantError,
            TaskScheduleFireRequest,
        )

        try:
            scheduled_fire_time, workflow_started_at = _validate_fire_activity_input(request)
            result = await self._service.fire(
                TaskScheduleFireRequest(
                    tenant_id=UUID(request.schedule.tenant_id),
                    project_id=UUID(request.schedule.project_id),
                    task_schedule_id=UUID(request.schedule.task_schedule_id),
                    content_digest=request.schedule.content_digest,
                    scheduled_fire_time_utc=scheduled_fire_time,
                    workflow_started_at_utc=workflow_started_at,
                )
            )
            return TaskScheduleWorkflowPayload(
                status=result.status.value,
                task_run_id=(str(result.task_run_id) if result.task_run_id is not None else None),
                scheduled_fire_time_utc=(
                    result.scheduled_fire_time_utc.astimezone(UTC).isoformat()
                ),
            )
        except TaskScheduleFireInvariantError:
            raise TemporalApplicationError(
                "TASK_SCHEDULE_FIRE_INVARIANT",
                type="TaskScheduleFireInvariantError",
                non_retryable=True,
            ) from None
        except ApplicationError as error:
            raise TemporalApplicationError(
                error.error_code.value,
                type="TaskScheduleFireBusinessError",
                non_retryable=True,
            ) from None
        except TypeError, ValueError:
            raise TemporalApplicationError(
                "TASK_SCHEDULE_FIRE_PAYLOAD_INVALID",
                type="TaskScheduleFirePayloadError",
                non_retryable=True,
            ) from None
        except Exception:
            raise TemporalApplicationError(
                "TASK_SCHEDULE_FIRE_DATABASE_RETRYABLE",
                type="TaskScheduleFireTransientError",
            ) from None


@workflow.defn(name=TASK_SCHEDULE_TRIGGER_WORKFLOW_NAME)
class AtlasTaskScheduleTriggerWorkflow:
    """Translate one Temporal nominal fire into the unified TaskRun compiler."""

    @workflow.run
    async def run(
        self,
        request: TaskScheduleWorkflowInput,
    ) -> TaskScheduleWorkflowPayload:
        try:
            _validate_workflow_input(request)
            info = workflow.info()
            scheduled_by = _search_attribute_value(
                info.typed_search_attributes,
                info.search_attributes,
                _SCHEDULED_BY_SEARCH_ATTRIBUTE,
            )
            if scheduled_by != request.temporal_schedule_id:
                raise ValueError("Temporal Schedule identity is absent or mismatched")
            scheduled_fire_time = _decode_scheduled_fire_time(
                _search_attribute_value(
                    info.typed_search_attributes,
                    info.search_attributes,
                    _SCHEDULED_START_SEARCH_ATTRIBUTE,
                )
            )
            workflow_started_at = info.workflow_start_time.astimezone(UTC)
        except TypeError, ValueError:
            raise TemporalApplicationError(
                "TASK_SCHEDULE_WORKFLOW_CONTEXT_INVALID",
                type="TaskScheduleWorkflowContextError",
                non_retryable=True,
            ) from None

        result = await workflow.execute_activity(
            TASK_SCHEDULE_FIRE_ACTIVITY,
            TaskScheduleFireActivityInput(
                schedule=request,
                scheduled_fire_time_utc=scheduled_fire_time.isoformat(),
                workflow_started_at_utc=workflow_started_at.isoformat(),
            ),
            result_type=TaskScheduleWorkflowPayload,
            start_to_close_timeout=_SCHEDULE_FIRE_ACTIVITY_TIMEOUT,
            retry_policy=_SCHEDULE_FIRE_RETRY_POLICY,
        )
        return cast(TaskScheduleWorkflowPayload, result)


class _TaskScheduleTemporalClient(Protocol):
    """Narrow Schedule client surface used by the privileged dispatcher."""

    @property
    def namespace(self) -> str: ...

    async def create_schedule(
        self,
        id: str,
        schedule: Schedule,
        *,
        memo: Mapping[str, Any] | None = None,
        rpc_timeout: timedelta | None = None,
    ) -> ScheduleHandle: ...

    def get_schedule_handle(self, id: str) -> ScheduleHandle: ...


class TemporalTaskScheduleSynchronizer:
    """Create or verify immutable Temporal Schedules and converge pause state."""

    def __init__(
        self,
        client: Client,
        *,
        rpc_attempts: int = 3,
        rpc_timeout: timedelta = timedelta(seconds=10),
        retry_delay: timedelta = timedelta(milliseconds=250),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 1 <= rpc_attempts <= 5:
            raise ValueError("Temporal Schedule RPC attempts must be between 1 and 5")
        if not timedelta(0) < rpc_timeout <= timedelta(minutes=2) or not timedelta(
            0
        ) <= retry_delay <= timedelta(seconds=5):
            raise ValueError("Temporal Schedule RPC timing is invalid")
        self._client = cast(_TaskScheduleTemporalClient, client)
        self._rpc_attempts = rpc_attempts
        self._rpc_timeout = rpc_timeout
        self._retry_delay = retry_delay
        self._sleep = sleep

    async def apply(
        self,
        intent: ClaimedTaskScheduleSyncIntent,
    ) -> tuple[datetime, ...]:
        """Converge one claimed desired revision with collision verification."""

        expected = _build_schedule(intent, client_namespace=self._client.namespace)
        expected_memo = _schedule_memo_identity(intent)
        last_error: RPCError | None = None
        for attempt in range(self._rpc_attempts):
            try:
                handle = await self._create_or_get(intent, expected, expected_memo)
                description = await handle.describe(rpc_timeout=self._rpc_timeout)
                await _verify_schedule_description(
                    intent,
                    description,
                    expected,
                    expected_memo,
                )
                desired_paused = intent.desired_status is TaskScheduleStatus.PAUSED
                if description.schedule.state.paused != desired_paused:
                    if desired_paused:
                        await handle.pause(
                            note=f"Atlas desired revision {intent.schedule_revision}",
                            rpc_timeout=self._rpc_timeout,
                        )
                    else:
                        await handle.unpause(
                            note=f"Atlas desired revision {intent.schedule_revision}",
                            rpc_timeout=self._rpc_timeout,
                        )
                return _normalize_next_fire_times(description.info.next_action_times)
            except TaskIntentInvariantError:
                raise
            except RPCError as error:
                permanent_code = _PERMANENT_RPC_ERROR_CODES.get(error.status)
                if permanent_code is not None:
                    raise TaskIntentInvariantError(permanent_code) from error
                if error.status not in _TRANSIENT_RPC_STATUSES:
                    raise TaskIntentInvariantError(
                        "TEMPORAL_SCHEDULE_RPC_PROTOCOL_ERROR"
                    ) from error
                last_error = error
                if attempt + 1 < self._rpc_attempts:
                    await self._sleep(self._retry_delay.total_seconds() * (attempt + 1))
        raise TaskIntentTransientError("TEMPORAL_SCHEDULE_RPC_UNAVAILABLE") from last_error

    async def _create_or_get(
        self,
        intent: ClaimedTaskScheduleSyncIntent,
        schedule: Schedule,
        memo: Mapping[str, str],
    ) -> ScheduleHandle:
        try:
            return await self._client.create_schedule(
                intent.temporal_schedule_id,
                schedule,
                memo={TASK_SCHEDULE_MEMO_KEY: dict(memo)},
                rpc_timeout=self._rpc_timeout,
            )
        except ScheduleAlreadyRunningError:
            return self._client.get_schedule_handle(intent.temporal_schedule_id)
        except RPCError as error:
            if error.status is RPCStatusCode.ALREADY_EXISTS:
                return self._client.get_schedule_handle(intent.temporal_schedule_id)
            raise


def _build_schedule(
    intent: ClaimedTaskScheduleSyncIntent,
    *,
    client_namespace: str,
) -> Schedule:
    _validate_sync_intent(intent, client_namespace=client_namespace)
    request = TaskScheduleWorkflowInput(
        tenant_id=str(intent.tenant_id),
        project_id=str(intent.project_id),
        task_schedule_id=str(intent.task_schedule_id),
        content_digest=intent.content_digest,
        temporal_schedule_id=intent.temporal_schedule_id,
    )
    identity = _schedule_memo_identity(intent)
    return Schedule(
        action=ScheduleActionStartWorkflow(
            TASK_SCHEDULE_TRIGGER_WORKFLOW_NAME,
            request,
            id=_schedule_workflow_id(intent.tenant_id, intent.task_schedule_id),
            task_queue=TASK_SCHEDULE_TASK_QUEUE,
            execution_timeout=_SCHEDULE_WORKFLOW_TIMEOUT,
            task_timeout=_SCHEDULE_WORKFLOW_TASK_TIMEOUT,
            memo={TASK_SCHEDULE_MEMO_KEY: identity},
        ),
        spec=ScheduleSpec(
            calendars=[_calendar_spec(intent.calendar)],
            jitter=(timedelta(seconds=intent.jitter_seconds) if intent.jitter_seconds else None),
            time_zone_name=intent.time_zone_name,
        ),
        policy=SchedulePolicy(
            overlap=(
                ScheduleOverlapPolicy.BUFFER_ONE
                if intent.overlap_policy is TaskScheduleOverlapPolicy.QUEUE_ONE
                else ScheduleOverlapPolicy.SKIP
            ),
            catchup_window=timedelta(
                seconds=(
                    intent.catchup_window_seconds
                    if intent.catchup_policy is TaskScheduleCatchupPolicy.RUN_ONCE
                    else 60
                )
            ),
            pause_on_failure=True,
        ),
        state=ScheduleState(
            paused=intent.desired_status is TaskScheduleStatus.PAUSED,
        ),
    )


def _calendar_spec(calendar: TaskScheduleCalendar) -> ScheduleCalendarSpec:
    return ScheduleCalendarSpec(
        second=(_range(0),),
        minute=tuple(_range(value) for value in calendar.minutes),
        hour=tuple(_range(value) for value in calendar.hours),
        day_of_month=(
            tuple(_range(value) for value in calendar.days_of_month)
            if calendar.days_of_month
            else (ScheduleRange(start=1, end=31, step=1),)
        ),
        month=(
            tuple(_range(value) for value in calendar.months)
            if calendar.months
            else (ScheduleRange(start=1, end=12, step=1),)
        ),
        year=(),
        day_of_week=(
            tuple(
                _range(value)
                for value in sorted(
                    0 if selected == 7 else selected for selected in calendar.iso_days_of_week
                )
            )
            if calendar.iso_days_of_week
            else (ScheduleRange(start=0, end=6, step=1),)
        ),
    )


def _range(value: int) -> ScheduleRange:
    return ScheduleRange(start=value, end=value, step=1)


def _schedule_workflow_id(tenant_id: UUID, schedule_id: UUID) -> str:
    return f"atlas-task/schedule-fire/{tenant_id.hex}/{schedule_id.hex}"


def _schedule_memo_identity(
    intent: ClaimedTaskScheduleSyncIntent,
) -> dict[str, str]:
    return {
        "schemaVersion": TASK_SCHEDULE_MEMO_SCHEMA,
        "tenantId": str(intent.tenant_id),
        "projectId": str(intent.project_id),
        "taskScheduleId": str(intent.task_schedule_id),
        "contentDigest": intent.content_digest,
        "temporalNamespace": intent.temporal_namespace,
        "temporalScheduleId": intent.temporal_schedule_id,
    }


async def _verify_schedule_description(
    intent: ClaimedTaskScheduleSyncIntent,
    description: ScheduleDescription,
    expected: Schedule,
    expected_memo: Mapping[str, str],
) -> None:
    if description.id != intent.temporal_schedule_id:
        raise TaskIntentInvariantError("TEMPORAL_SCHEDULE_IDENTITY_MISMATCH")
    memo = await description.memo()
    if memo.get(TASK_SCHEDULE_MEMO_KEY) != dict(expected_memo):
        raise TaskIntentInvariantError("TEMPORAL_SCHEDULE_MEMO_MISMATCH")
    actual = description.schedule
    if actual.spec != expected.spec or actual.policy != expected.policy:
        raise TaskIntentInvariantError("TEMPORAL_SCHEDULE_DEFINITION_MISMATCH")
    if (
        actual.state.limited_actions
        or actual.action.__class__ is not ScheduleActionStartWorkflow
        or expected.action.__class__ is not ScheduleActionStartWorkflow
    ):
        raise TaskIntentInvariantError("TEMPORAL_SCHEDULE_ACTION_MISMATCH")
    actual_action = actual.action
    expected_action = expected.action
    try:
        decoded_args = await description.data_converter.decode(
            cast(Sequence[Any], actual_action.args),
            [TaskScheduleWorkflowInput],
        )
        actual_action_memo = await _decode_payload_mapping(
            description,
            actual_action.memo,
        )
    except Exception as error:
        raise TaskIntentInvariantError("TEMPORAL_SCHEDULE_ACTION_PAYLOAD_INVALID") from error
    if (
        decoded_args != list(expected_action.args)
        or actual_action.workflow != expected_action.workflow
        or actual_action.id != expected_action.id
        or actual_action.task_queue != expected_action.task_queue
        or actual_action.execution_timeout != expected_action.execution_timeout
        or actual_action.run_timeout != expected_action.run_timeout
        or actual_action.task_timeout != expected_action.task_timeout
        or actual_action.retry_policy != expected_action.retry_policy
        or actual_action_memo != expected_action.memo
        or actual_action.typed_search_attributes
        or actual_action.untyped_search_attributes
        or actual_action.headers
        or actual_action.static_summary is not None
        or actual_action.static_details is not None
    ):
        raise TaskIntentInvariantError("TEMPORAL_SCHEDULE_ACTION_MISMATCH")


async def _decode_payload_mapping(
    description: ScheduleDescription,
    values: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if values is None:
        return None
    keys = list(values)
    decoded = await description.data_converter.decode([values[key] for key in keys])
    return dict(zip(keys, decoded, strict=True))


def _normalize_next_fire_times(
    values: Sequence[datetime],
) -> tuple[datetime, ...]:
    normalized = tuple(
        sorted({value.astimezone(UTC) for value in values if value.tzinfo is not None})
    )
    if len(normalized) < 5:
        raise TaskIntentInvariantError("TEMPORAL_SCHEDULE_NEXT_FIRES_INVALID")
    return normalized[:5]


def _validate_sync_intent(
    intent: ClaimedTaskScheduleSyncIntent,
    *,
    client_namespace: str,
) -> None:
    expected_status = (
        TaskScheduleStatus.ACTIVE
        if intent.action in {"CREATE", "RESUME"}
        else TaskScheduleStatus.PAUSED
    )
    if (
        intent.action not in {"CREATE", "PAUSE", "RESUME", "AUTO_PAUSE"}
        or intent.desired_status is not expected_status
        or intent.temporal_namespace != client_namespace
        or intent.temporal_schedule_id
        != task_schedule_temporal_id(intent.tenant_id, intent.task_schedule_id)
        or fullmatch(_SHA256_DIGEST_PATTERN, intent.content_digest) is None
        or intent.schedule_revision < 1
        or intent.dispatch_revision < 1
        or intent.dispatch_attempts < 1
        or intent.claim_expires_at.tzinfo is None
        or intent.jitter_seconds >= intent.catchup_window_seconds
    ):
        raise TaskIntentInvariantError("TASK_SCHEDULE_SYNC_CONTRACT_MISMATCH")


def _validate_workflow_input(request: TaskScheduleWorkflowInput) -> None:
    tenant_id = UUID(request.tenant_id)
    project_id = UUID(request.project_id)
    schedule_id = UUID(request.task_schedule_id)
    if (
        request.schema_version != TASK_SCHEDULE_WORKFLOW_INPUT_SCHEMA
        or str(tenant_id) != request.tenant_id
        or str(project_id) != request.project_id
        or str(schedule_id) != request.task_schedule_id
        or fullmatch(_SHA256_DIGEST_PATTERN, request.content_digest) is None
        or request.temporal_schedule_id != task_schedule_temporal_id(tenant_id, schedule_id)
    ):
        raise ValueError("Task Schedule Workflow input is invalid")


def _validate_fire_activity_input(
    request: TaskScheduleFireActivityInput,
) -> tuple[datetime, datetime]:
    _validate_workflow_input(request.schedule)
    scheduled_fire_time = _decode_scheduled_fire_time(request.scheduled_fire_time_utc)
    workflow_started_at = _decode_scheduled_fire_time(request.workflow_started_at_utc)
    if request.schema_version != TASK_SCHEDULE_FIRE_INPUT_SCHEMA:
        raise ValueError("Task Schedule fire Activity input is invalid")
    return scheduled_fire_time, workflow_started_at


def _search_attribute_value(
    typed_values: Any,
    untyped_values: Mapping[str, Any],
    key: str,
) -> Any:
    for pair in typed_values:
        if pair.key.name == key:
            return pair.value
    value = untyped_values.get(key)
    if isinstance(value, Sequence) and not isinstance(value, str):
        return value[0] if len(value) == 1 else None
    return value


def _decode_scheduled_fire_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        selected = value
    elif isinstance(value, str):
        selected = datetime.fromisoformat(value)
    else:
        raise ValueError("Temporal scheduled start time is missing")
    if selected.tzinfo is None:
        raise ValueError("Temporal scheduled start time is not aware")
    return selected.astimezone(UTC)


__all__ = [
    "TASK_SCHEDULE_FIRE_ACTIVITY",
    "TASK_SCHEDULE_FIRE_INPUT_SCHEMA",
    "TASK_SCHEDULE_MEMO_KEY",
    "TASK_SCHEDULE_MEMO_SCHEMA",
    "TASK_SCHEDULE_TASK_QUEUE",
    "TASK_SCHEDULE_TRIGGER_WORKFLOW_NAME",
    "TASK_SCHEDULE_WORKFLOW_INPUT_SCHEMA",
    "TASK_SCHEDULE_WORKFLOW_RESULT_SCHEMA",
    "AtlasTaskScheduleTriggerWorkflow",
    "TaskScheduleFireActivities",
    "TaskScheduleFireActivityInput",
    "TaskScheduleWorkflowInput",
    "TaskScheduleWorkflowPayload",
    "TemporalTaskScheduleSynchronizer",
]
