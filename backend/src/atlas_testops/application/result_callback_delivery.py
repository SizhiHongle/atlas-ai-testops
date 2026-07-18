"""Crash-safe delivery of signed Task Gate callback events."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from re import fullmatch
from typing import Protocol

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.infrastructure.result_callback_intents import (
    ClaimedTaskGateCallbackIntent,
    TaskGateCallbackIntentRepository,
)


class TaskGateCallbackDispatcherDatabase(Protocol):
    """Dispatcher authority exposing only short database transactions."""

    def transaction(
        self,
    ) -> AbstractAsyncContextManager[AsyncConnection[DictRow]]: ...


class TaskGateCallbackSendStatus(StrEnum):
    """Bounded transport outcomes understood by the durable consumer."""

    DELIVERED = "DELIVERED"
    RETRYABLE = "RETRYABLE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


@dataclass(frozen=True, slots=True)
class TaskGateCallbackSendResult:
    """One safe HTTP outcome without response content or credentials."""

    status: TaskGateCallbackSendStatus
    error_code: str | None = None
    response_status_code: int | None = None

    def __post_init__(self) -> None:
        if self.response_status_code is not None and not (100 <= self.response_status_code <= 599):
            raise ValueError("callback HTTP status code is invalid")
        if self.status is TaskGateCallbackSendStatus.DELIVERED:
            if (
                self.error_code is not None
                or self.response_status_code is None
                or not 200 <= self.response_status_code <= 299
            ):
                raise ValueError("delivered callback result is invalid")
            return
        if self.error_code is None or fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.error_code) is None:
            raise ValueError("failed callback result requires a safe error code")


class TaskGateCallbackSender(Protocol):
    """Perform one bounded delivery attempt for an already leased event."""

    async def deliver(
        self,
        intent: ClaimedTaskGateCallbackIntent,
    ) -> TaskGateCallbackSendResult:
        """Send exactly one HTTP request."""

        ...


@dataclass(frozen=True, slots=True)
class TaskGateCallbackDeliveryBatch:
    """Observable aggregate for one bounded callback polling iteration."""

    claimed: int
    delivered: int
    retried: int
    failed: int
    lease_lost: int


@dataclass(frozen=True, slots=True)
class _DeliveryOutcome:
    delivered: int = 0
    retried: int = 0
    failed: int = 0
    lease_lost: int = 0


class TaskGateCallbackDeliveryConsumer:
    """Claim briefly, call HTTP outside SQL, and fence the final outcome."""

    def __init__(
        self,
        database: TaskGateCallbackDispatcherDatabase,
        sender: TaskGateCallbackSender,
        *,
        dispatcher_id: str,
        batch_size: int,
        lease_duration: timedelta,
        poll_interval: timedelta,
        retry_policy: TaskIntentRetryPolicy,
        repository: TaskGateCallbackIntentRepository | None = None,
    ) -> None:
        normalized_id = dispatcher_id.strip()
        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", normalized_id) is None:
            raise ValueError("Task Gate callback dispatcher identity is invalid")
        if not 1 <= batch_size <= 100:
            raise ValueError("Task Gate callback batch size must be between 1 and 100")
        if not timedelta(seconds=5) <= lease_duration <= timedelta(minutes=5):
            raise ValueError("Task Gate callback lease must be between 5 and 300 seconds")
        if not lease_duration.total_seconds().is_integer():
            raise ValueError("Task Gate callback lease must use whole seconds")
        if poll_interval <= timedelta(0):
            raise ValueError("Task Gate callback poll interval must be positive")
        self._database = database
        self._sender = sender
        self._dispatcher_id = normalized_id
        self._batch_size = batch_size
        self._lease_duration = lease_duration
        self._poll_interval = poll_interval
        self._retry_policy = retry_policy
        self._repository = repository or TaskGateCallbackIntentRepository()

    async def run_once(self) -> TaskGateCallbackDeliveryBatch:
        """Deliver one independently leased batch."""

        async with self._database.transaction() as connection:
            claimed = await self._repository.claim(
                connection,
                claimed_by=self._dispatcher_id,
                limit=self._batch_size,
                lease_duration=self._lease_duration,
            )
        outcomes = await asyncio.gather(*(self._deliver(intent) for intent in claimed))
        return TaskGateCallbackDeliveryBatch(
            claimed=len(claimed),
            delivered=sum(item.delivered for item in outcomes),
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

    async def _deliver(
        self,
        intent: ClaimedTaskGateCallbackIntent,
    ) -> _DeliveryOutcome:
        try:
            result = await self._sender.deliver(intent)
        except asyncio.CancelledError:
            raise
        except Exception:
            if intent.dispatch_attempts >= self._retry_policy.max_attempts:
                return await self._fail(
                    intent,
                    error_code="TASK_GATE_CALLBACK_RETRY_EXHAUSTED",
                    response_status_code=None,
                )
            return await self._retry(
                intent,
                error_code="TASK_GATE_CALLBACK_UNEXPECTED",
                response_status_code=None,
            )

        if result.status is TaskGateCallbackSendStatus.DELIVERED:
            if result.response_status_code is None:
                return await self._fail(
                    intent,
                    error_code="TASK_GATE_CALLBACK_RESULT_INVALID",
                    response_status_code=None,
                )
            async with self._database.transaction() as connection:
                applied = await self._repository.mark_delivered(
                    connection,
                    event_id=intent.event_id,
                    claim_token=intent.claim_token,
                    dispatch_revision=intent.dispatch_revision,
                    response_status_code=result.response_status_code,
                )
            return _DeliveryOutcome(delivered=1) if applied else _DeliveryOutcome(lease_lost=1)

        error_code = result.error_code or "TASK_GATE_CALLBACK_RESULT_INVALID"
        if (
            result.status is TaskGateCallbackSendStatus.PERMANENT_FAILURE
            or intent.dispatch_attempts >= self._retry_policy.max_attempts
        ):
            if (
                result.status is TaskGateCallbackSendStatus.RETRYABLE
                and intent.dispatch_attempts >= self._retry_policy.max_attempts
            ):
                error_code = "TASK_GATE_CALLBACK_RETRY_EXHAUSTED"
            return await self._fail(
                intent,
                error_code=error_code,
                response_status_code=result.response_status_code,
            )
        return await self._retry(
            intent,
            error_code=error_code,
            response_status_code=result.response_status_code,
        )

    async def _retry(
        self,
        intent: ClaimedTaskGateCallbackIntent,
        *,
        error_code: str,
        response_status_code: int | None,
    ) -> _DeliveryOutcome:
        async with self._database.transaction() as connection:
            applied = await self._repository.retry(
                connection,
                event_id=intent.event_id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
                error_code=error_code,
                response_status_code=response_status_code,
                retry_delay=self._retry_policy.delay_after(intent.dispatch_attempts),
            )
        return _DeliveryOutcome(retried=1) if applied else _DeliveryOutcome(lease_lost=1)

    async def _fail(
        self,
        intent: ClaimedTaskGateCallbackIntent,
        *,
        error_code: str,
        response_status_code: int | None,
    ) -> _DeliveryOutcome:
        async with self._database.transaction() as connection:
            applied = await self._repository.fail(
                connection,
                event_id=intent.event_id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
                error_code=error_code,
                response_status_code=response_status_code,
            )
        return _DeliveryOutcome(failed=1) if applied else _DeliveryOutcome(lease_lost=1)


__all__ = [
    "TaskGateCallbackDeliveryBatch",
    "TaskGateCallbackDeliveryConsumer",
    "TaskGateCallbackDispatcherDatabase",
    "TaskGateCallbackSendResult",
    "TaskGateCallbackSendStatus",
    "TaskGateCallbackSender",
]
