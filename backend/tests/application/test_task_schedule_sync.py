"""Crash-safe Task Schedule synchronization consumer tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import cast

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.orchestration.test_task_schedule_orchestration import (
    NEXT_FIRES,
    intent,
)

from atlas_testops.application.task_intents import (
    TaskIntentInvariantError,
    TaskIntentRetryPolicy,
    TaskIntentTransientError,
)
from atlas_testops.application.task_schedule_sync import (
    TaskScheduleSyncConsumer,
)
from atlas_testops.infrastructure.task_schedules import (
    ClaimedTaskScheduleSyncIntent,
    TaskScheduleSyncIntentRepository,
)


class _Database:
    def __init__(self) -> None:
        self.active_transactions = 0
        self.transactions = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.transactions += 1
        self.active_transactions += 1
        try:
            yield cast(AsyncConnection[DictRow], object())
        finally:
            self.active_transactions -= 1


class _Repository:
    def __init__(
        self,
        intents: tuple[ClaimedTaskScheduleSyncIntent, ...],
    ) -> None:
        self.intents = intents
        self.applied_calls: list[dict[str, object]] = []
        self.retried: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []
        self.applied = True

    async def claim(
        self,
        connection: object,
        **values: object,
    ) -> tuple[ClaimedTaskScheduleSyncIntent, ...]:
        assert connection is not None
        assert values["claimed_by"] == "schedule-dispatcher"
        assert values["namespace"] == "atlas-task"
        return self.intents

    async def mark_applied(
        self,
        _connection: object,
        **values: object,
    ) -> bool:
        self.applied_calls.append(values)
        return self.applied

    async def retry(self, _connection: object, **values: object) -> bool:
        self.retried.append(values)
        return self.applied

    async def fail(self, _connection: object, **values: object) -> bool:
        self.failed.append(values)
        return self.applied


class _Synchronizer:
    def __init__(
        self,
        database: _Database,
        error: Exception | None = None,
    ) -> None:
        self._database = database
        self._error = error
        self.calls = 0

    async def apply(
        self,
        claimed: ClaimedTaskScheduleSyncIntent,
    ) -> tuple[datetime, ...]:
        assert self._database.active_transactions == 0
        assert claimed.dispatch_attempts > 0
        self.calls += 1
        if self._error is not None:
            raise self._error
        return NEXT_FIRES


def _consumer(
    database: _Database,
    repository: _Repository,
    synchronizer: _Synchronizer,
    *,
    policy: TaskIntentRetryPolicy | None = None,
    temporal_namespace: str = "atlas-task",
    batch_size: int = 20,
    lease_duration: timedelta = timedelta(seconds=30),
    poll_interval: timedelta = timedelta(milliseconds=10),
) -> TaskScheduleSyncConsumer:
    return TaskScheduleSyncConsumer(
        database,
        synchronizer,
        dispatcher_id="schedule-dispatcher",
        temporal_namespace=temporal_namespace,
        batch_size=batch_size,
        lease_duration=lease_duration,
        poll_interval=poll_interval,
        retry_policy=policy or TaskIntentRetryPolicy(),
        repository=cast(TaskScheduleSyncIntentRepository, repository),
    )


@pytest.mark.anyio
async def test_consumer_commits_claim_before_temporal_and_acks_exact_revision() -> None:
    database = _Database()
    repository = _Repository((intent(),))
    synchronizer = _Synchronizer(database)

    result = await _consumer(database, repository, synchronizer).run_once()

    assert result.claimed == result.applied == 1
    assert result.retried == result.failed == result.lease_lost == 0
    assert database.transactions == 2
    assert repository.applied_calls == [
        {
            "intent_id": intent().id,
            "claim_token": intent().claim_token,
            "dispatch_revision": intent().dispatch_revision,
            "next_fire_times": NEXT_FIRES,
        }
    ]


@pytest.mark.anyio
async def test_consumer_classifies_retry_failure_exhaustion_and_lost_lease() -> None:
    database = _Database()
    transient_repository = _Repository((intent(dispatch_attempts=2),))
    transient = await _consumer(
        database,
        transient_repository,
        _Synchronizer(
            database,
            TaskIntentTransientError("TEMPORAL_SCHEDULE_RPC_UNAVAILABLE"),
        ),
        policy=TaskIntentRetryPolicy(
            initial_backoff=timedelta(seconds=2),
            maximum_backoff=timedelta(seconds=5),
        ),
    ).run_once()
    assert transient.retried == 1
    assert transient_repository.retried[0]["retry_delay"] == timedelta(seconds=4)

    invariant_repository = _Repository((intent(),))
    invariant = await _consumer(
        database,
        invariant_repository,
        _Synchronizer(
            database,
            TaskIntentInvariantError("TEMPORAL_SCHEDULE_MEMO_MISMATCH"),
        ),
    ).run_once()
    assert invariant.failed == 1
    assert invariant_repository.failed[0]["error_code"] == ("TEMPORAL_SCHEDULE_MEMO_MISMATCH")

    exhausted_repository = _Repository((intent(dispatch_attempts=2),))
    exhausted = await _consumer(
        database,
        exhausted_repository,
        _Synchronizer(
            database,
            TaskIntentTransientError("TEMPORAL_SCHEDULE_RPC_UNAVAILABLE"),
        ),
        policy=TaskIntentRetryPolicy(max_attempts=2),
    ).run_once()
    assert exhausted.failed == 1
    assert exhausted_repository.failed[0]["error_code"] == ("TEMPORAL_SCHEDULE_RETRY_EXHAUSTED")

    lost_repository = _Repository((intent(),))
    lost_repository.applied = False
    lost = await _consumer(
        database,
        lost_repository,
        _Synchronizer(database, RuntimeError("secret-value")),
    ).run_once()
    assert lost.lease_lost == 1
    assert lost_repository.retried[0]["error_code"] == ("SCHEDULE_SYNC_UNEXPECTED")
    assert "secret" not in str(lost_repository.retried[0]).lower()


@pytest.mark.anyio
async def test_consumer_run_forever_and_configuration_fail_closed() -> None:
    database = _Database()
    repository = _Repository(())
    synchronizer = _Synchronizer(database)
    stop = asyncio.Event()
    stop.set()
    await _consumer(database, repository, synchronizer).run_forever(stop)
    assert database.transactions == 0

    with pytest.raises(ValueError, match="identity"):
        TaskScheduleSyncConsumer(
            database,
            synchronizer,
            dispatcher_id="-invalid",
            temporal_namespace="atlas-task",
            batch_size=1,
            lease_duration=timedelta(seconds=30),
            poll_interval=timedelta(seconds=1),
            retry_policy=TaskIntentRetryPolicy(),
            repository=cast(TaskScheduleSyncIntentRepository, repository),
        )
    with pytest.raises(ValueError, match="between 5 and 300"):
        TaskScheduleSyncConsumer(
            database,
            synchronizer,
            dispatcher_id="schedule-dispatcher",
            temporal_namespace="atlas-task",
            batch_size=1,
            lease_duration=timedelta(seconds=1),
            poll_interval=timedelta(seconds=1),
            retry_policy=TaskIntentRetryPolicy(),
            repository=cast(TaskScheduleSyncIntentRepository, repository),
        )


@pytest.mark.anyio
async def test_consumer_poll_timeout_retries_until_stop() -> None:
    database = _Database()
    repository = _Repository(())
    synchronizer = _Synchronizer(database)
    stop = asyncio.Event()

    async def stop_after_two_polls() -> None:
        while database.transactions < 2:
            await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(
        _consumer(database, repository, synchronizer).run_forever(stop),
        stop_after_two_polls(),
    )

    assert database.transactions >= 2


def test_consumer_rejects_invalid_schedule_sync_settings() -> None:
    database = _Database()
    repository = _Repository(())
    synchronizer = _Synchronizer(database)

    with pytest.raises(ValueError, match="namespace"):
        _consumer(
            database,
            repository,
            synchronizer,
            temporal_namespace="invalid namespace",
        )
    with pytest.raises(ValueError, match="batch size"):
        _consumer(database, repository, synchronizer, batch_size=0)
    with pytest.raises(ValueError, match="whole seconds"):
        _consumer(
            database,
            repository,
            synchronizer,
            lease_duration=timedelta(seconds=5.5),
        )
    with pytest.raises(ValueError, match="poll interval"):
        _consumer(
            database,
            repository,
            synchronizer,
            poll_interval=timedelta(0),
        )
