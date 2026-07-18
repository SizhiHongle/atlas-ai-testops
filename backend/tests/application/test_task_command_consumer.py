"""Unit tests for crash-safe TaskRun command Signal delivery."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.application.task_commands import (
    TaskCommandInvariantError,
    TaskCommandTransientError,
    TaskRunCommandIntentConsumer,
)
from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.infrastructure.task_commands import (
    ClaimedTaskRunCommandIntent,
    TaskRunCommandRepository,
)

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _intent(*, attempts: int = 1) -> ClaimedTaskRunCommandIntent:
    tenant_id = UUID(int=2)
    task_run_id = UUID(int=4)
    return ClaimedTaskRunCommandIntent(
        id=UUID(int=1),
        tenant_id=tenant_id,
        project_id=UUID(int=3),
        task_run_id=task_run_id,
        schema_version="atlas.task-run-command/0.2",
        command_type="CANCEL",
        client_mutation_id="cancel-command-001",
        command_digest=DIGEST_A,
        expected_run_revision=2,
        accepted_run_revision=3,
        request_digest=DIGEST_A,
        manifest_hash=DIGEST_B,
        namespace="atlas-task",
        workflow_id=f"atlas-task/run/{tenant_id.hex}/{task_run_id.hex}",
        status="CLAIMED",
        claim_token=UUID(int=5),
        dispatch_revision=2,
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
    def __init__(self, intents: tuple[ClaimedTaskRunCommandIntent, ...]) -> None:
        self.intents = intents
        self.delivered: list[dict[str, object]] = []
        self.retried: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []
        self.applied = True

    async def claim(
        self,
        connection: object,
        **values: object,
    ) -> tuple[ClaimedTaskRunCommandIntent, ...]:
        assert connection is not None
        assert values["claimed_by"] == "dispatcher-1"
        assert values["namespace"] == "atlas-task"
        return self.intents

    async def mark_delivered(self, connection: object, **values: object) -> bool:
        self.delivered.append(values)
        return self.applied

    async def retry(self, connection: object, **values: object) -> bool:
        self.retried.append(values)
        return self.applied

    async def fail(self, connection: object, **values: object) -> bool:
        self.failed.append(values)
        return self.applied


class _Signaler:
    def __init__(self, database: _Database, error: BaseException | None = None) -> None:
        self._database = database
        self._error = error
        self.calls = 0

    async def signal(self, intent: ClaimedTaskRunCommandIntent) -> None:
        assert self._database.active_transactions == 0
        assert intent.status == "CLAIMED"
        self.calls += 1
        if self._error is not None:
            raise self._error


def _consumer(
    database: _Database,
    repository: _Repository,
    signaler: _Signaler,
    *,
    policy: TaskIntentRetryPolicy | None = None,
) -> TaskRunCommandIntentConsumer:
    return TaskRunCommandIntentConsumer(
        database,
        signaler,
        dispatcher_id="dispatcher-1",
        temporal_namespace="atlas-task",
        batch_size=20,
        lease_duration=timedelta(seconds=30),
        poll_interval=timedelta(milliseconds=10),
        retry_policy=policy or TaskIntentRetryPolicy(),
        repository=cast(TaskRunCommandRepository, repository),
    )


@pytest.mark.anyio
async def test_consumer_commits_claim_before_signal_and_acks_by_fence() -> None:
    database = _Database()
    repository = _Repository((_intent(),))
    signaler = _Signaler(database)

    result = await _consumer(database, repository, signaler).run_once()

    assert result.claimed == result.delivered == 1
    assert result.retried == result.failed == result.lease_lost == 0
    assert database.transactions == 2
    assert repository.delivered == [
        {
            "intent_id": UUID(int=1),
            "claim_token": UUID(int=5),
            "dispatch_revision": 2,
        }
    ]


@pytest.mark.anyio
async def test_consumer_retries_transient_signal_with_durable_backoff() -> None:
    database = _Database()
    repository = _Repository((_intent(attempts=3),))
    signaler = _Signaler(database, TaskCommandTransientError("TEMPORAL_RPC_UNAVAILABLE"))
    policy = TaskIntentRetryPolicy(
        initial_backoff=timedelta(seconds=2),
        maximum_backoff=timedelta(seconds=5),
    )

    result = await _consumer(database, repository, signaler, policy=policy).run_once()

    assert result.retried == 1
    assert repository.retried[0]["error_code"] == "TEMPORAL_RPC_UNAVAILABLE"
    assert repository.retried[0]["retry_delay"] == timedelta(seconds=5)


@pytest.mark.anyio
async def test_consumer_fails_invariant_and_exhausted_transient() -> None:
    database = _Database()
    invariant_repository = _Repository((_intent(),))
    invariant = await _consumer(
        database,
        invariant_repository,
        _Signaler(database, TaskCommandInvariantError("COMMAND_CONTRACT_MISMATCH")),
    ).run_once()
    assert invariant.failed == 1
    assert invariant_repository.failed[0]["error_code"] == "COMMAND_CONTRACT_MISMATCH"

    exhausted_repository = _Repository((_intent(attempts=2),))
    exhausted = await _consumer(
        database,
        exhausted_repository,
        _Signaler(database, TaskCommandTransientError("TEMPORAL_RPC_UNAVAILABLE")),
        policy=TaskIntentRetryPolicy(max_attempts=2),
    ).run_once()
    assert exhausted.failed == 1
    assert exhausted_repository.failed[0]["error_code"] == "TEMPORAL_RETRY_EXHAUSTED"


@pytest.mark.anyio
async def test_consumer_sanitizes_unexpected_exception_and_counts_lost_lease() -> None:
    database = _Database()
    repository = _Repository((_intent(),))
    repository.applied = False
    signaler = _Signaler(database, RuntimeError("secret-value-must-not-be-persisted"))

    result = await _consumer(database, repository, signaler).run_once()

    assert result.lease_lost == 1
    assert repository.retried[0]["error_code"] == "COMMAND_DISPATCH_UNEXPECTED"
    assert "secret" not in str(repository.retried[0]).casefold()


@pytest.mark.anyio
async def test_consumer_exhausts_unexpected_failure_and_propagates_cancellation() -> None:
    database = _Database()
    exhausted_repository = _Repository((_intent(attempts=2),))
    exhausted = await _consumer(
        database,
        exhausted_repository,
        _Signaler(database, RuntimeError("sensitive")),
        policy=TaskIntentRetryPolicy(max_attempts=2),
    ).run_once()
    assert exhausted.failed == 1
    assert exhausted_repository.failed[0]["error_code"] == (
        "COMMAND_DISPATCH_RETRY_EXHAUSTED"
    )

    canceled_repository = _Repository((_intent(),))
    with pytest.raises(asyncio.CancelledError):
        await _consumer(
            database,
            canceled_repository,
            _Signaler(database, asyncio.CancelledError()),
        ).run_once()
    assert canceled_repository.retried == []


@pytest.mark.anyio
async def test_run_forever_observes_stop_before_polling() -> None:
    database = _Database()
    repository = _Repository(())
    stop_event = asyncio.Event()
    stop_event.set()

    await _consumer(database, repository, _Signaler(database)).run_forever(stop_event)

    assert database.transactions == 0


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"dispatcher_id": "-invalid"}, "identity"),
        ({"temporal_namespace": "bad namespace"}, "namespace"),
        ({"batch_size": 0}, "batch size"),
        ({"lease_duration": timedelta(milliseconds=1500)}, "whole seconds"),
        ({"lease_duration": timedelta(seconds=0)}, "lease duration"),
        ({"poll_interval": timedelta(0)}, "poll interval"),
    ],
)
def test_consumer_validates_configuration(
    updates: dict[str, object],
    message: str,
) -> None:
    database = _Database()
    values: dict[str, object] = {
        "dispatcher_id": "dispatcher-1",
        "temporal_namespace": "atlas-task",
        "batch_size": 1,
        "lease_duration": timedelta(seconds=1),
        "poll_interval": timedelta(seconds=1),
    }
    values.update(updates)
    with pytest.raises(ValueError, match=message):
        TaskRunCommandIntentConsumer(
            database,
            _Signaler(database),
            dispatcher_id=cast(str, values["dispatcher_id"]),
            temporal_namespace=cast(str, values["temporal_namespace"]),
            batch_size=cast(int, values["batch_size"]),
            lease_duration=cast(timedelta, values["lease_duration"]),
            poll_interval=cast(timedelta, values["poll_interval"]),
            retry_policy=TaskIntentRetryPolicy(),
            repository=cast(TaskRunCommandRepository, _Repository(())),
        )


def test_command_delivery_error_rejects_unsafe_persisted_code() -> None:
    with pytest.raises(ValueError, match="error code"):
        TaskCommandInvariantError("unsafe remote message")
