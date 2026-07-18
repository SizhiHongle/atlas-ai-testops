"""Crash-safe synchronization of database Schedule desired state to Temporal."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from re import fullmatch
from typing import Protocol

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.application.task_intents import (
    TaskIntentInvariantError,
    TaskIntentRetryPolicy,
    TaskIntentTransientError,
)
from atlas_testops.infrastructure.task_schedules import (
    ClaimedTaskScheduleSyncIntent,
    TaskScheduleSyncIntentRepository,
)


class TaskScheduleDispatcherDatabase(Protocol):
    """Database authority limited to narrow dispatcher functions."""

    def transaction(
        self,
    ) -> AbstractAsyncContextManager[AsyncConnection[DictRow]]: ...


class TaskScheduleSynchronizer(Protocol):
    """Converge one exact desired revision and return Temporal's next fires."""

    async def apply(
        self,
        intent: ClaimedTaskScheduleSyncIntent,
    ) -> tuple[datetime, ...]: ...


@dataclass(frozen=True, slots=True)
class TaskScheduleSyncBatch:
    """Non-sensitive counters for one independently fenced pass."""

    claimed: int
    applied: int
    retried: int
    failed: int
    lease_lost: int


@dataclass(frozen=True, slots=True)
class _SyncOutcome:
    applied: int = 0
    retried: int = 0
    failed: int = 0
    lease_lost: int = 0


class TaskScheduleSyncConsumer:
    """Perform Temporal I/O outside SQL and CAS only the exact claimed revision."""

    def __init__(
        self,
        database: TaskScheduleDispatcherDatabase,
        synchronizer: TaskScheduleSynchronizer,
        *,
        dispatcher_id: str,
        temporal_namespace: str,
        batch_size: int,
        lease_duration: timedelta,
        poll_interval: timedelta,
        retry_policy: TaskIntentRetryPolicy,
        repository: TaskScheduleSyncIntentRepository | None = None,
    ) -> None:
        normalized_id = dispatcher_id.strip()
        normalized_namespace = temporal_namespace.strip()
        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", normalized_id) is None:
            raise ValueError("dispatcher identity is invalid")
        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", normalized_namespace) is None:
            raise ValueError("Temporal namespace is invalid")
        if not 1 <= batch_size <= 100:
            raise ValueError("Schedule sync batch size must be between 1 and 100")
        if not timedelta(seconds=5) <= lease_duration <= timedelta(minutes=5):
            raise ValueError("Schedule sync lease must be between 5 and 300 seconds")
        if not lease_duration.total_seconds().is_integer():
            raise ValueError("Schedule sync lease must use whole seconds")
        if poll_interval <= timedelta(0):
            raise ValueError("Schedule sync poll interval must be positive")
        self._database = database
        self._synchronizer = synchronizer
        self._dispatcher_id = normalized_id
        self._temporal_namespace = normalized_namespace
        self._batch_size = batch_size
        self._lease_duration = lease_duration
        self._poll_interval = poll_interval
        self._retry_policy = retry_policy
        self._repository = repository or TaskScheduleSyncIntentRepository()

    async def run_once(self) -> TaskScheduleSyncBatch:
        """Claim, synchronize concurrently, and persist fenced outcomes."""

        async with self._database.transaction() as connection:
            claimed = await self._repository.claim(
                connection,
                claimed_by=self._dispatcher_id,
                namespace=self._temporal_namespace,
                limit=self._batch_size,
                lease_duration=self._lease_duration,
            )
        outcomes = await asyncio.gather(*(self._apply(intent) for intent in claimed))
        return TaskScheduleSyncBatch(
            claimed=len(claimed),
            applied=sum(item.applied for item in outcomes),
            retried=sum(item.retried for item in outcomes),
            failed=sum(item.failed for item in outcomes),
            lease_lost=sum(item.lease_lost for item in outcomes),
        )

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Poll until shutdown without swallowing cancellation."""

        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval.total_seconds(),
                )
            except TimeoutError:
                continue

    async def _apply(
        self,
        intent: ClaimedTaskScheduleSyncIntent,
    ) -> _SyncOutcome:
        try:
            next_fire_times = await self._synchronizer.apply(intent)
        except TaskIntentInvariantError as error:
            return await self._fail(intent, error.error_code)
        except TaskIntentTransientError as error:
            if intent.dispatch_attempts >= self._retry_policy.max_attempts:
                return await self._fail(intent, "TEMPORAL_SCHEDULE_RETRY_EXHAUSTED")
            return await self._retry(intent, error.error_code)
        except asyncio.CancelledError:
            raise
        except Exception:
            if intent.dispatch_attempts >= self._retry_policy.max_attempts:
                return await self._fail(intent, "SCHEDULE_SYNC_RETRY_EXHAUSTED")
            return await self._retry(intent, "SCHEDULE_SYNC_UNEXPECTED")

        async with self._database.transaction() as connection:
            applied = await self._repository.mark_applied(
                connection,
                intent_id=intent.id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
                next_fire_times=next_fire_times[:5],
            )
        return _SyncOutcome(applied=1) if applied else _SyncOutcome(lease_lost=1)

    async def _retry(
        self,
        intent: ClaimedTaskScheduleSyncIntent,
        error_code: str,
    ) -> _SyncOutcome:
        async with self._database.transaction() as connection:
            applied = await self._repository.retry(
                connection,
                intent_id=intent.id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
                error_code=error_code,
                retry_delay=self._retry_policy.delay_after(intent.dispatch_attempts),
            )
        return _SyncOutcome(retried=1) if applied else _SyncOutcome(lease_lost=1)

    async def _fail(
        self,
        intent: ClaimedTaskScheduleSyncIntent,
        error_code: str,
    ) -> _SyncOutcome:
        async with self._database.transaction() as connection:
            applied = await self._repository.fail(
                connection,
                intent_id=intent.id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
                error_code=error_code,
            )
        return _SyncOutcome(failed=1) if applied else _SyncOutcome(lease_lost=1)


__all__ = [
    "TaskScheduleDispatcherDatabase",
    "TaskScheduleSyncBatch",
    "TaskScheduleSyncConsumer",
    "TaskScheduleSynchronizer",
]
