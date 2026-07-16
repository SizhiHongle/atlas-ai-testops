"""Crash-safe delivery of claimed Task Workflow start intents."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from math import isfinite
from re import fullmatch
from typing import Protocol

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.infrastructure.task_intents import (
    ClaimedTaskWorkflowIntent,
    TaskWorkflowIntentRepository,
)


class TaskIntentDispatcherDatabase(Protocol):
    """Database authority limited to trusted Task intent functions."""

    def transaction(
        self,
    ) -> AbstractAsyncContextManager[AsyncConnection[DictRow]]: ...


class TaskIntentStarter(Protocol):
    """Submit one exact claimed intent to its durable workflow engine."""

    async def start(self, intent: ClaimedTaskWorkflowIntent) -> None: ...


class TaskIntentDeliveryError(Exception):
    """Safe classified dispatch error; its code may be persisted."""

    def __init__(self, error_code: str) -> None:
        if fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code) is None:
            raise ValueError("intent delivery error code is invalid")
        super().__init__(error_code)
        self.error_code = error_code


class TaskIntentInvariantError(TaskIntentDeliveryError):
    """Permanent identity or contract mismatch that must fail closed."""


class TaskIntentTransientError(TaskIntentDeliveryError):
    """Ambiguous or unavailable dependency result that may be retried."""


@dataclass(frozen=True, slots=True)
class TaskIntentRetryPolicy:
    """Bounded exponential retry policy driven by the durable attempt count."""

    max_attempts: int = 8
    initial_backoff: timedelta = timedelta(seconds=5)
    maximum_backoff: timedelta = timedelta(minutes=5)
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 64:
            raise ValueError("intent maximum attempts must be between 1 and 64")
        if self.initial_backoff < timedelta(milliseconds=100):
            raise ValueError("intent initial backoff must be at least 100 milliseconds")
        if self.maximum_backoff < self.initial_backoff:
            raise ValueError("intent maximum backoff must cover initial backoff")
        if self.maximum_backoff > timedelta(hours=1):
            raise ValueError("intent maximum backoff must not exceed one hour")
        if not isfinite(self.multiplier) or self.multiplier < 1.0:
            raise ValueError("intent retry multiplier must be at least one")

    def delay_after(self, dispatch_attempts: int) -> timedelta:
        """Return the delay after a one-based durable dispatch attempt."""

        if dispatch_attempts < 1:
            raise ValueError("dispatch attempts must be positive")
        delay_seconds = self.initial_backoff.total_seconds()
        maximum_seconds = self.maximum_backoff.total_seconds()
        for _ in range(dispatch_attempts - 1):
            delay_seconds = min(maximum_seconds, delay_seconds * self.multiplier)
            if delay_seconds >= maximum_seconds:
                break
        return timedelta(seconds=delay_seconds)


@dataclass(frozen=True, slots=True)
class IntentDeliveryBatch:
    """Non-sensitive counters for one claim-and-deliver pass."""

    claimed: int
    started: int
    retried: int
    failed: int
    lease_lost: int


@dataclass(frozen=True, slots=True)
class _IntentOutcome:
    started: int = 0
    retried: int = 0
    failed: int = 0
    lease_lost: int = 0


class TaskWorkflowIntentConsumer:
    """Claim briefly, perform Temporal I/O outside SQL, then CAS the outcome."""

    def __init__(
        self,
        database: TaskIntentDispatcherDatabase,
        starter: TaskIntentStarter,
        *,
        dispatcher_id: str,
        temporal_namespace: str,
        batch_size: int,
        lease_duration: timedelta,
        poll_interval: timedelta,
        retry_policy: TaskIntentRetryPolicy,
        repository: TaskWorkflowIntentRepository | None = None,
    ) -> None:
        normalized_id = dispatcher_id.strip()
        normalized_namespace = temporal_namespace.strip()
        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", normalized_id) is None:
            raise ValueError("dispatcher identity is invalid")
        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", normalized_namespace) is None:
            raise ValueError("Temporal namespace is invalid")
        if not 1 <= batch_size <= 100:
            raise ValueError("intent batch size must be between 1 and 100")
        if not timedelta(seconds=1) <= lease_duration <= timedelta(minutes=15):
            raise ValueError("intent lease duration must be between 1 and 900 seconds")
        if not lease_duration.total_seconds().is_integer():
            raise ValueError("intent lease duration must use whole seconds")
        if poll_interval <= timedelta(0):
            raise ValueError("intent poll interval must be positive")

        self._database = database
        self._starter = starter
        self._dispatcher_id = normalized_id
        self._temporal_namespace = normalized_namespace
        self._batch_size = batch_size
        self._lease_duration = lease_duration
        self._poll_interval = poll_interval
        self._retry_policy = retry_policy
        self._repository = repository or TaskWorkflowIntentRepository()

    async def run_once(self) -> IntentDeliveryBatch:
        """Deliver one independently leased batch and persist only safe outcomes."""

        async with self._database.transaction() as connection:
            claimed = await self._repository.claim(
                connection,
                claimed_by=self._dispatcher_id,
                namespace=self._temporal_namespace,
                limit=self._batch_size,
                lease_duration=self._lease_duration,
            )

        outcomes = await asyncio.gather(*(self._deliver(intent) for intent in claimed))
        return IntentDeliveryBatch(
            claimed=len(claimed),
            started=sum(item.started for item in outcomes),
            retried=sum(item.retried for item in outcomes),
            failed=sum(item.failed for item in outcomes),
            lease_lost=sum(item.lease_lost for item in outcomes),
        )

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Poll until shutdown, while remaining immediately cancellation-safe."""

        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval.total_seconds(),
                )
            except TimeoutError:
                continue

    async def _deliver(self, intent: ClaimedTaskWorkflowIntent) -> _IntentOutcome:
        try:
            await self._starter.start(intent)
        except TaskIntentInvariantError as error:
            return await self._fail(intent, error.error_code)
        except TaskIntentTransientError as error:
            if intent.dispatch_attempts >= self._retry_policy.max_attempts:
                return await self._fail(intent, "TEMPORAL_RETRY_EXHAUSTED")
            return await self._retry(intent, error.error_code)
        except asyncio.CancelledError:
            raise
        except Exception:
            if intent.dispatch_attempts >= self._retry_policy.max_attempts:
                return await self._fail(intent, "INTENT_DISPATCH_RETRY_EXHAUSTED")
            return await self._retry(intent, "INTENT_DISPATCH_UNEXPECTED")

        async with self._database.transaction() as connection:
            applied = await self._repository.mark_started(
                connection,
                intent_id=intent.id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
            )
        return _IntentOutcome(started=1) if applied else _IntentOutcome(lease_lost=1)

    async def _retry(
        self,
        intent: ClaimedTaskWorkflowIntent,
        error_code: str,
    ) -> _IntentOutcome:
        retry_delay = self._retry_policy.delay_after(intent.dispatch_attempts)
        async with self._database.transaction() as connection:
            applied = await self._repository.retry(
                connection,
                intent_id=intent.id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
                error_code=error_code,
                retry_delay=retry_delay,
            )
        return _IntentOutcome(retried=1) if applied else _IntentOutcome(lease_lost=1)

    async def _fail(
        self,
        intent: ClaimedTaskWorkflowIntent,
        error_code: str,
    ) -> _IntentOutcome:
        async with self._database.transaction() as connection:
            applied = await self._repository.fail(
                connection,
                intent_id=intent.id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
                error_code=error_code,
            )
        return _IntentOutcome(failed=1) if applied else _IntentOutcome(lease_lost=1)
