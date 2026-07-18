"""Durable Task Gate callback Consumer tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid7

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.application.result_callback_delivery import (
    TaskGateCallbackDeliveryConsumer,
    TaskGateCallbackSendResult,
    TaskGateCallbackSendStatus,
)
from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.domain.result import TaskGateVerdict
from atlas_testops.infrastructure.result_callback_intents import (
    ClaimedTaskGateCallbackIntent,
    TaskGateCallbackIntentRepository,
)

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def _intent(*, attempts: int = 1) -> ClaimedTaskGateCallbackIntent:
    return ClaimedTaskGateCallbackIntent(
        event_id=uuid7(),
        tenant_id=uuid7(),
        project_id=uuid7(),
        task_run_id=uuid7(),
        manifest_hash=DIGEST,
        gate_decision=TaskGateVerdict.INCONCLUSIVE,
        claim_token=uuid7(),
        dispatch_revision=attempts,
        dispatch_attempts=attempts,
        claim_expires_at=NOW + timedelta(minutes=1),
        created_at=NOW,
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
        intents: tuple[ClaimedTaskGateCallbackIntent, ...],
    ) -> None:
        self.intents = intents
        self.delivered: list[dict[str, object]] = []
        self.retried: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []
        self.applied = True

    async def claim(
        self,
        _connection: object,
        **values: object,
    ) -> tuple[ClaimedTaskGateCallbackIntent, ...]:
        assert values["claimed_by"] == "callback-dispatcher"
        assert values["limit"] == 20
        return self.intents

    async def mark_delivered(
        self,
        _connection: object,
        **values: object,
    ) -> bool:
        self.delivered.append(values)
        return self.applied

    async def retry(self, _connection: object, **values: object) -> bool:
        self.retried.append(values)
        return self.applied

    async def fail(self, _connection: object, **values: object) -> bool:
        self.failed.append(values)
        return self.applied


class _Sender:
    def __init__(
        self,
        database: _Database,
        result: TaskGateCallbackSendResult | Exception,
    ) -> None:
        self._database = database
        self._result = result
        self.calls = 0

    async def deliver(
        self,
        _intent: ClaimedTaskGateCallbackIntent,
    ) -> TaskGateCallbackSendResult:
        assert self._database.active_transactions == 0
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _consumer(
    database: _Database,
    repository: _Repository,
    sender: _Sender,
    *,
    policy: TaskIntentRetryPolicy | None = None,
) -> TaskGateCallbackDeliveryConsumer:
    return TaskGateCallbackDeliveryConsumer(
        database,
        sender,
        dispatcher_id="callback-dispatcher",
        batch_size=20,
        lease_duration=timedelta(seconds=30),
        poll_interval=timedelta(milliseconds=10),
        retry_policy=policy or TaskIntentRetryPolicy(),
        repository=cast(TaskGateCallbackIntentRepository, repository),
    )


@pytest.mark.anyio
async def test_consumer_delivers_outside_sql_and_fences_success() -> None:
    database = _Database()
    claimed = _intent()
    repository = _Repository((claimed,))
    sender = _Sender(
        database,
        TaskGateCallbackSendResult(
            status=TaskGateCallbackSendStatus.DELIVERED,
            response_status_code=204,
        ),
    )

    result = await _consumer(database, repository, sender).run_once()

    assert result.claimed == result.delivered == 1
    assert result.retried == result.failed == result.lease_lost == 0
    assert database.transactions == 2
    assert sender.calls == 1
    assert repository.delivered == [
        {
            "event_id": claimed.event_id,
            "claim_token": claimed.claim_token,
            "dispatch_revision": claimed.dispatch_revision,
            "response_status_code": 204,
        }
    ]


@pytest.mark.anyio
async def test_consumer_classifies_retry_permanent_exhaustion_and_lost_lease() -> None:
    database = _Database()

    retry_repository = _Repository((_intent(attempts=2),))
    retried = await _consumer(
        database,
        retry_repository,
        _Sender(
            database,
            TaskGateCallbackSendResult(
                status=TaskGateCallbackSendStatus.RETRYABLE,
                error_code="TASK_GATE_CALLBACK_HTTP_RETRYABLE",
                response_status_code=503,
            ),
        ),
        policy=TaskIntentRetryPolicy(
            initial_backoff=timedelta(seconds=2),
            maximum_backoff=timedelta(seconds=5),
        ),
    ).run_once()
    assert retried.retried == 1
    assert retry_repository.retried[0]["retry_delay"] == timedelta(seconds=4)

    permanent_repository = _Repository((_intent(),))
    permanent = await _consumer(
        database,
        permanent_repository,
        _Sender(
            database,
            TaskGateCallbackSendResult(
                status=TaskGateCallbackSendStatus.PERMANENT_FAILURE,
                error_code="TASK_GATE_CALLBACK_HTTP_REJECTED",
                response_status_code=400,
            ),
        ),
    ).run_once()
    assert permanent.failed == 1
    assert permanent_repository.failed[0]["error_code"] == ("TASK_GATE_CALLBACK_HTTP_REJECTED")

    exhausted_repository = _Repository((_intent(attempts=2),))
    exhausted = await _consumer(
        database,
        exhausted_repository,
        _Sender(
            database,
            TaskGateCallbackSendResult(
                status=TaskGateCallbackSendStatus.RETRYABLE,
                error_code="TASK_GATE_CALLBACK_TRANSPORT_ERROR",
            ),
        ),
        policy=TaskIntentRetryPolicy(max_attempts=2),
    ).run_once()
    assert exhausted.failed == 1
    assert exhausted_repository.failed[0]["error_code"] == ("TASK_GATE_CALLBACK_RETRY_EXHAUSTED")

    lost_repository = _Repository((_intent(),))
    lost_repository.applied = False
    lost = await _consumer(
        database,
        lost_repository,
        _Sender(database, RuntimeError("secret-value")),
    ).run_once()
    assert lost.lease_lost == 1
    assert lost_repository.retried[0]["error_code"] == ("TASK_GATE_CALLBACK_UNEXPECTED")
    assert "secret" not in str(lost_repository.retried[0]).casefold()


@pytest.mark.anyio
async def test_consumer_polling_and_configuration_are_bounded() -> None:
    database = _Database()
    repository = _Repository(())
    sender = _Sender(
        database,
        TaskGateCallbackSendResult(
            status=TaskGateCallbackSendStatus.DELIVERED,
            response_status_code=204,
        ),
    )
    stop = asyncio.Event()

    async def stop_after_two_polls() -> None:
        while database.transactions < 2:
            await asyncio.sleep(0)
        stop.set()

    await asyncio.gather(
        _consumer(database, repository, sender).run_forever(stop),
        stop_after_two_polls(),
    )
    assert database.transactions >= 2

    with pytest.raises(ValueError, match="identity"):
        TaskGateCallbackDeliveryConsumer(
            database,
            sender,
            dispatcher_id="-invalid",
            batch_size=1,
            lease_duration=timedelta(seconds=30),
            poll_interval=timedelta(seconds=1),
            retry_policy=TaskIntentRetryPolicy(),
        )
    with pytest.raises(ValueError, match="batch size"):
        TaskGateCallbackDeliveryConsumer(
            database,
            sender,
            dispatcher_id="valid",
            batch_size=0,
            lease_duration=timedelta(seconds=30),
            poll_interval=timedelta(seconds=1),
            retry_policy=TaskIntentRetryPolicy(),
        )
    with pytest.raises(ValueError, match="whole seconds"):
        TaskGateCallbackDeliveryConsumer(
            database,
            sender,
            dispatcher_id="valid",
            batch_size=1,
            lease_duration=timedelta(seconds=5.5),
            poll_interval=timedelta(seconds=1),
            retry_policy=TaskIntentRetryPolicy(),
        )
