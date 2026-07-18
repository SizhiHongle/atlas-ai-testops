"""Temporal Task Schedule orchestration and Activity boundary tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleDescription,
    ScheduleOverlapPolicy,
    ScheduleState,
)
from temporalio.exceptions import ApplicationError as TemporalApplicationError
from temporalio.service import RPCError, RPCStatusCode
from tests.domain.task.test_schedules import retry_policy, uid

from atlas_testops.application.task_intents import (
    TaskIntentInvariantError,
    TaskIntentTransientError,
)
from atlas_testops.application.task_schedule_fires import (
    TaskScheduleFireInvariantError,
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
    TASK_SCHEDULE_MEMO_KEY,
    TASK_SCHEDULE_MEMO_SCHEMA,
    TASK_SCHEDULE_TASK_QUEUE,
    TASK_SCHEDULE_TRIGGER_WORKFLOW_NAME,
    TASK_SCHEDULE_WORKFLOW_INPUT_SCHEMA,
    TaskScheduleFireActivities,
    TaskScheduleFireActivityInput,
    TaskScheduleWorkflowInput,
    TemporalTaskScheduleSynchronizer,
)

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
NEXT_FIRES = tuple(NOW + timedelta(days=value) for value in range(1, 6))
DIGEST = "sha256:" + "a" * 64


def intent(**updates: Any) -> ClaimedTaskScheduleSyncIntent:
    """Build one exact claimed Schedule revision."""

    values: dict[str, Any] = {
        "id": uid(10),
        "tenant_id": uid(1),
        "project_id": uid(2),
        "task_schedule_id": uid(4),
        "schedule_revision": 2,
        "action": "PAUSE",
        "content_digest": DIGEST,
        "temporal_namespace": "atlas-task",
        "temporal_schedule_id": task_schedule_temporal_id(uid(1), uid(4)),
        "claim_token": uid(11),
        "dispatch_revision": 3,
        "dispatch_attempts": 1,
        "claim_expires_at": NOW + timedelta(minutes=3),
        "desired_status": TaskScheduleStatus.PAUSED,
        "task_plan_version_id": uid(3),
        "calendar": TaskScheduleCalendar(
            minutes=(0, 30),
            hours=(2, 9),
            iso_days_of_week=(1, 7),
        ),
        "time_zone_name": "Asia/Shanghai",
        "overlap_policy": TaskScheduleOverlapPolicy.QUEUE_ONE,
        "catchup_policy": TaskScheduleCatchupPolicy.RUN_ONCE,
        "catchup_window_seconds": 3_600,
        "jitter_seconds": 30,
        "iteration_id": "iteration:nightly",
        "retry_policy": retry_policy(),
        "created_by": uid(5),
    }
    values.update(updates)
    return ClaimedTaskScheduleSyncIntent(**values)


class _Converter:
    async def decode(
        self,
        payloads: list[Any] | tuple[Any, ...],
        _type_hints: list[type[Any]] | None = None,
    ) -> list[Any]:
        return list(payloads)


class _Description:
    def __init__(
        self,
        schedule_id: str,
        schedule: Schedule,
        memo: dict[str, Any],
    ) -> None:
        self.id = schedule_id
        self.schedule = schedule
        self.info = SimpleNamespace(next_action_times=NEXT_FIRES)
        self.data_converter = _Converter()
        self._memo = memo

    async def memo(self) -> dict[str, Any]:
        return self._memo


class _Handle:
    def __init__(self, description: _Description) -> None:
        self.description = description
        self.pause_notes: list[str | None] = []
        self.unpause_notes: list[str | None] = []

    async def describe(self, **options: Any) -> ScheduleDescription:
        assert options["rpc_timeout"] == timedelta(seconds=2)
        return cast(ScheduleDescription, self.description)

    async def pause(self, **options: Any) -> None:
        assert options["rpc_timeout"] == timedelta(seconds=2)
        self.pause_notes.append(options.get("note"))
        self.description.schedule.state.paused = True

    async def unpause(self, **options: Any) -> None:
        assert options["rpc_timeout"] == timedelta(seconds=2)
        self.unpause_notes.append(options.get("note"))
        self.description.schedule.state.paused = False


class _Client:
    namespace = "atlas-task"

    def __init__(
        self,
        *,
        force_observed_paused: bool | None = None,
        memo_override: dict[str, Any] | None = None,
        errors: list[Exception] | None = None,
    ) -> None:
        self.force_observed_paused = force_observed_paused
        self.memo_override = memo_override
        self.errors = errors or []
        self.calls: list[tuple[str, Schedule, dict[str, Any]]] = []
        self.handle: _Handle | None = None

    async def create_schedule(
        self,
        schedule_id: str,
        schedule: Schedule,
        **options: Any,
    ) -> _Handle:
        if self.errors:
            raise self.errors.pop(0)
        memo = options["memo"]
        self.calls.append((schedule_id, schedule, options))
        observed = schedule
        if self.force_observed_paused is not None:
            observed = replace(
                schedule,
                state=ScheduleState(paused=self.force_observed_paused),
            )
        description = _Description(
            schedule_id,
            observed,
            self.memo_override if self.memo_override is not None else memo,
        )
        self.handle = _Handle(description)
        return self.handle

    def get_schedule_handle(self, _schedule_id: str) -> _Handle:
        assert self.handle is not None
        return self.handle


@pytest.mark.anyio
async def test_synchronizer_uses_structured_calendar_and_converges_pause() -> None:
    client = _Client(force_observed_paused=False)
    synchronizer = TemporalTaskScheduleSynchronizer(
        cast(Client, client),
        rpc_timeout=timedelta(seconds=2),
    )

    next_fires = await synchronizer.apply(intent())

    assert next_fires == NEXT_FIRES
    schedule_id, schedule, options = client.calls[0]
    assert schedule_id == task_schedule_temporal_id(uid(1), uid(4))
    assert options["rpc_timeout"] == timedelta(seconds=2)
    memo = options["memo"][TASK_SCHEDULE_MEMO_KEY]
    assert memo["schemaVersion"] == TASK_SCHEDULE_MEMO_SCHEMA
    assert memo["contentDigest"] == DIGEST
    assert schedule.spec.time_zone_name == "Asia/Shanghai"
    assert schedule.spec.cron_expressions == []
    assert schedule.spec.calendars[0].minute[1].start == 30
    assert tuple(item.start for item in schedule.spec.calendars[0].day_of_week) == (
        0,
        1,
    )
    assert schedule.policy.overlap is ScheduleOverlapPolicy.BUFFER_ONE
    assert schedule.policy.catchup_window == timedelta(hours=1)
    assert schedule.policy.pause_on_failure is True
    assert isinstance(schedule.action, ScheduleActionStartWorkflow)
    assert schedule.action.workflow == TASK_SCHEDULE_TRIGGER_WORKFLOW_NAME
    assert schedule.action.task_queue == TASK_SCHEDULE_TASK_QUEUE
    request = cast(TaskScheduleWorkflowInput, schedule.action.args[0])
    assert request.schema_version == TASK_SCHEDULE_WORKFLOW_INPUT_SCHEMA
    assert request.content_digest == DIGEST
    assert client.handle is not None
    assert client.handle.pause_notes == ["Atlas desired revision 2"]


@pytest.mark.anyio
async def test_synchronizer_maps_skip_policy_and_rejects_collision() -> None:
    active = intent(
        action="CREATE",
        schedule_revision=1,
        desired_status=TaskScheduleStatus.ACTIVE,
        overlap_policy=TaskScheduleOverlapPolicy.SKIP,
        catchup_policy=TaskScheduleCatchupPolicy.SKIP,
        jitter_seconds=0,
    )
    client = _Client()
    await TemporalTaskScheduleSynchronizer(
        cast(Client, client),
        rpc_timeout=timedelta(seconds=2),
    ).apply(active)
    schedule = client.calls[0][1]
    assert schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
    assert schedule.policy.catchup_window == timedelta(seconds=60)
    assert schedule.spec.jitter is None

    collided = _Client(memo_override={TASK_SCHEDULE_MEMO_KEY: {}})
    with pytest.raises(TaskIntentInvariantError, match="MEMO_MISMATCH"):
        await TemporalTaskScheduleSynchronizer(
            cast(Client, collided),
            rpc_timeout=timedelta(seconds=2),
        ).apply(active)


@pytest.mark.anyio
async def test_synchronizer_retries_transient_rpc_and_classifies_permanent_rpc() -> None:
    transient = RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
    client = _Client(errors=[transient])
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    await TemporalTaskScheduleSynchronizer(
        cast(Client, client),
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=2),
        retry_delay=timedelta(milliseconds=100),
        sleep=record_sleep,
    ).apply(intent())
    assert delays == [0.1]

    unavailable = _Client(errors=[transient, transient])
    with pytest.raises(
        TaskIntentTransientError,
        match="TEMPORAL_SCHEDULE_RPC_UNAVAILABLE",
    ):
        await TemporalTaskScheduleSynchronizer(
            cast(Client, unavailable),
            rpc_attempts=2,
            rpc_timeout=timedelta(seconds=2),
            retry_delay=timedelta(0),
        ).apply(intent())

    forbidden = _Client(errors=[RPCError("forbidden", RPCStatusCode.PERMISSION_DENIED, b"")])
    with pytest.raises(
        TaskIntentInvariantError,
        match="TEMPORAL_SCHEDULE_PERMISSION_DENIED",
    ):
        await TemporalTaskScheduleSynchronizer(
            cast(Client, forbidden),
            rpc_timeout=timedelta(seconds=2),
        ).apply(intent())


@pytest.mark.anyio
async def test_synchronizer_fails_closed_on_claim_contract_mismatch() -> None:
    client = _Client()
    with pytest.raises(TaskIntentInvariantError, match="CONTRACT_MISMATCH"):
        await TemporalTaskScheduleSynchronizer(
            cast(Client, client),
            rpc_timeout=timedelta(seconds=2),
        ).apply(intent(temporal_namespace="wrong"))
    assert client.calls == []


@pytest.mark.anyio
async def test_fire_activity_converts_exact_identity_and_safe_failures() -> None:
    captured: list[Any] = []

    class Service:
        async def fire(self, request: Any) -> TaskScheduleFireResult:
            captured.append(request)
            return TaskScheduleFireResult(
                status=TaskScheduleFireStatus.CREATED,
                task_run_id=uid(20),
                scheduled_fire_time_utc=NOW,
            )

    request = TaskScheduleFireActivityInput(
        schedule=TaskScheduleWorkflowInput(
            tenant_id=str(uid(1)),
            project_id=str(uid(2)),
            task_schedule_id=str(uid(4)),
            content_digest=DIGEST,
            temporal_schedule_id=task_schedule_temporal_id(uid(1), uid(4)),
        ),
        scheduled_fire_time_utc=NOW.isoformat(),
        workflow_started_at_utc=(NOW + timedelta(seconds=1)).isoformat(),
    )
    result = await TaskScheduleFireActivities(Service()).fire(request)
    assert result.status == "CREATED"
    assert result.task_run_id == str(uid(20))
    assert captured[0].task_schedule_id == uid(4)
    assert captured[0].workflow_started_at_utc == NOW + timedelta(seconds=1)

    class Rejected:
        async def fire(self, _request: Any) -> Any:
            raise TaskScheduleFireInvariantError("mismatch")

    with pytest.raises(TemporalApplicationError) as rejected:
        await TaskScheduleFireActivities(Rejected()).fire(request)
    assert rejected.value.non_retryable is True
    assert rejected.value.message == "TASK_SCHEDULE_FIRE_INVARIANT"
