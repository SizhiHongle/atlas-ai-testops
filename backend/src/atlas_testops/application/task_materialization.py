"""Recoverable consumer for large TaskRun materialization checkpoints."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from re import fullmatch
from typing import Protocol

from psycopg import AsyncConnection, Error
from psycopg.rows import DictRow

from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.infrastructure.task_materialization import (
    ClaimedTaskMaterializationPartition,
    TaskMaterializationPartitionRepository,
)


class TaskMaterializationDispatcherDatabase(Protocol):
    """Database authority authenticated only as atlas_dispatcher."""

    def transaction(
        self,
    ) -> AbstractAsyncContextManager[AsyncConnection[DictRow]]: ...


@dataclass(frozen=True, slots=True)
class TaskMaterializationBatch:
    """Safe counters for one claim-and-materialize pass."""

    claimed: int
    completed: int
    retried: int
    failed: int
    lease_lost: int


@dataclass(frozen=True, slots=True)
class _PartitionOutcome:
    completed: int = 0
    retried: int = 0
    failed: int = 0
    lease_lost: int = 0


class TaskMaterializationConsumer:
    """Materialize each bounded partition atomically and resume after crashes."""

    def __init__(
        self,
        database: TaskMaterializationDispatcherDatabase,
        *,
        dispatcher_id: str,
        batch_size: int,
        lease_duration: timedelta,
        poll_interval: timedelta,
        retry_policy: TaskIntentRetryPolicy,
        repository: TaskMaterializationPartitionRepository | None = None,
    ) -> None:
        normalized_id = dispatcher_id.strip()
        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", normalized_id) is None:
            raise ValueError("materialization dispatcher identity is invalid")
        if not 1 <= batch_size <= 100:
            raise ValueError("materialization batch size must be between 1 and 100")
        if not timedelta(seconds=1) <= lease_duration <= timedelta(minutes=15):
            raise ValueError("materialization lease must be between 1 and 900 seconds")
        if not lease_duration.total_seconds().is_integer():
            raise ValueError("materialization lease must use whole seconds")
        if poll_interval <= timedelta(0) or poll_interval >= lease_duration:
            raise ValueError("materialization poll interval must be positive and below lease")
        self._database = database
        self._dispatcher_id = normalized_id
        self._batch_size = batch_size
        self._lease_duration = lease_duration
        self._poll_interval = poll_interval
        self._retry_policy = retry_policy
        self._repository = repository or TaskMaterializationPartitionRepository()

    async def run_once(self) -> TaskMaterializationBatch:
        """Claim briefly, materialize each partition, and persist a safe outcome."""

        async with self._database.transaction() as connection:
            claimed = await self._repository.claim(
                connection,
                claimed_by=self._dispatcher_id,
                limit=self._batch_size,
                lease_duration=self._lease_duration,
            )
        outcomes = await asyncio.gather(
            *(self._materialize(partition) for partition in claimed)
        )
        return TaskMaterializationBatch(
            claimed=len(claimed),
            completed=sum(item.completed for item in outcomes),
            retried=sum(item.retried for item in outcomes),
            failed=sum(item.failed for item in outcomes),
            lease_lost=sum(item.lease_lost for item in outcomes),
        )

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Poll until shutdown without retaining a database transaction."""

        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval.total_seconds(),
                )
            except TimeoutError:
                continue

    async def _materialize(
        self,
        partition: ClaimedTaskMaterializationPartition,
    ) -> _PartitionOutcome:
        try:
            async with self._database.transaction() as connection:
                applied = await self._repository.complete(
                    connection,
                    partition=partition,
                    claimed_by=self._dispatcher_id,
                )
            return (
                _PartitionOutcome(completed=1)
                if applied
                else _PartitionOutcome(lease_lost=1)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if _is_permanent_database_error(error):
                return await self._fail(partition, "TASK_MATERIALIZATION_INVARIANT")
            if partition.materialization_attempts >= self._retry_policy.max_attempts:
                return await self._fail(partition, "TASK_MATERIALIZATION_RETRY_EXHAUSTED")
            return await self._retry(partition, "TASK_MATERIALIZATION_TRANSIENT")

    async def _retry(
        self,
        partition: ClaimedTaskMaterializationPartition,
        error_code: str,
    ) -> _PartitionOutcome:
        delay = self._retry_policy.delay_after(partition.materialization_attempts)
        async with self._database.transaction() as connection:
            applied = await self._repository.retry(
                connection,
                partition=partition,
                claimed_by=self._dispatcher_id,
                error_code=error_code,
                retry_delay=delay,
            )
        return (
            _PartitionOutcome(retried=1)
            if applied
            else _PartitionOutcome(lease_lost=1)
        )

    async def _fail(
        self,
        partition: ClaimedTaskMaterializationPartition,
        error_code: str,
    ) -> _PartitionOutcome:
        async with self._database.transaction() as connection:
            applied = await self._repository.fail(
                connection,
                partition=partition,
                claimed_by=self._dispatcher_id,
                error_code=error_code,
            )
        return (
            _PartitionOutcome(failed=1)
            if applied
            else _PartitionOutcome(lease_lost=1)
        )


def _is_permanent_database_error(error: BaseException) -> bool:
    """Classify only explicit contract and authorization failures as permanent."""

    if not isinstance(error, Error):
        return False
    sqlstate = error.sqlstate
    return sqlstate is not None and (
        sqlstate.startswith(("22", "23", "42"))
        or sqlstate in {"42501", "55000", "P0002"}
    )


__all__ = [
    "TaskMaterializationBatch",
    "TaskMaterializationConsumer",
]
