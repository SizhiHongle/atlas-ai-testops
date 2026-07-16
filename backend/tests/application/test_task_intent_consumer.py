"""Unit tests for crash-safe Task Workflow intent delivery."""

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

from atlas_testops.application.task_intents import (
    TaskIntentInvariantError,
    TaskIntentRetryPolicy,
    TaskIntentTransientError,
    TaskWorkflowIntentConsumer,
)
from atlas_testops.infrastructure.task_intents import (
    ClaimedTaskWorkflowIntent,
    TaskWorkflowIntentRepository,
)

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def _intent(*, attempts: int = 1) -> ClaimedTaskWorkflowIntent:
    tenant_id = UUID(int=2)
    task_run_id = UUID(int=4)
    return ClaimedTaskWorkflowIntent(
        id=UUID(int=1),
        tenant_id=tenant_id,
        project_id=UUID(int=3),
        task_run_id=task_run_id,
        owner_kind="TASK_RUN",
        owner_id=task_run_id,
        namespace="atlas-task",
        workflow_id=f"atlas-task/run/{tenant_id.hex}/{task_run_id.hex}",
        request_digest=DIGEST,
        manifest_hash=DIGEST,
        workflow_type="AtlasTaskRunWorkflow",
        task_queue="atlas-task-run",
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
    def __init__(self, intents: tuple[ClaimedTaskWorkflowIntent, ...]) -> None:
        self.intents = intents
        self.started: list[dict[str, object]] = []
        self.retried: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []
        self.applied = True

    async def claim(
        self,
        connection: object,
        **values: object,
    ) -> tuple[ClaimedTaskWorkflowIntent, ...]:
        assert connection is not None
        assert values["claimed_by"] == "dispatcher-1"
        assert values["namespace"] == "atlas-task"
        return self.intents

    async def mark_started(self, connection: object, **values: object) -> bool:
        self.started.append(values)
        return self.applied

    async def retry(self, connection: object, **values: object) -> bool:
        self.retried.append(values)
        return self.applied

    async def fail(self, connection: object, **values: object) -> bool:
        self.failed.append(values)
        return self.applied


class _Starter:
    def __init__(self, database: _Database, error: Exception | None = None) -> None:
        self._database = database
        self._error = error
        self.calls = 0

    async def start(self, intent: ClaimedTaskWorkflowIntent) -> None:
        assert self._database.active_transactions == 0
        assert intent.status == "CLAIMED"
        self.calls += 1
        if self._error is not None:
            raise self._error


def _consumer(
    database: _Database,
    repository: _Repository,
    starter: _Starter,
    *,
    policy: TaskIntentRetryPolicy | None = None,
) -> TaskWorkflowIntentConsumer:
    return TaskWorkflowIntentConsumer(
        database,
        starter,
        dispatcher_id="dispatcher-1",
        temporal_namespace="atlas-task",
        batch_size=20,
        lease_duration=timedelta(seconds=30),
        poll_interval=timedelta(milliseconds=10),
        retry_policy=policy or TaskIntentRetryPolicy(),
        repository=cast(TaskWorkflowIntentRepository, repository),
    )


@pytest.mark.anyio
async def test_consumer_commits_claim_before_start_and_acks_by_token() -> None:
    database = _Database()
    repository = _Repository((_intent(),))
    starter = _Starter(database)

    result = await _consumer(database, repository, starter).run_once()

    assert result.claimed == result.started == 1
    assert result.retried == result.failed == result.lease_lost == 0
    assert database.transactions == 2
    assert repository.started == [
        {
            "intent_id": UUID(int=1),
            "claim_token": UUID(int=5),
            "dispatch_revision": 2,
        }
    ]


@pytest.mark.anyio
async def test_consumer_retries_transient_error_with_durable_backoff() -> None:
    database = _Database()
    repository = _Repository((_intent(attempts=3),))
    starter = _Starter(database, TaskIntentTransientError("TEMPORAL_RPC_UNAVAILABLE"))
    policy = TaskIntentRetryPolicy(
        initial_backoff=timedelta(seconds=2),
        maximum_backoff=timedelta(seconds=5),
    )

    result = await _consumer(database, repository, starter, policy=policy).run_once()

    assert result.retried == 1
    assert repository.retried[0]["error_code"] == "TEMPORAL_RPC_UNAVAILABLE"
    assert repository.retried[0]["retry_delay"] == timedelta(seconds=5)


@pytest.mark.anyio
async def test_consumer_fails_invariants_and_exhausted_transients() -> None:
    database = _Database()
    invariant_repository = _Repository((_intent(),))
    invariant_starter = _Starter(
        database,
        TaskIntentInvariantError("TEMPORAL_WORKFLOW_MEMO_MISMATCH"),
    )
    invariant = await _consumer(
        database,
        invariant_repository,
        invariant_starter,
    ).run_once()
    assert invariant.failed == 1
    assert invariant_repository.failed[0]["error_code"] == (
        "TEMPORAL_WORKFLOW_MEMO_MISMATCH"
    )

    exhausted_repository = _Repository((_intent(attempts=2),))
    exhausted_starter = _Starter(
        database,
        TaskIntentTransientError("TEMPORAL_RPC_UNAVAILABLE"),
    )
    exhausted = await _consumer(
        database,
        exhausted_repository,
        exhausted_starter,
        policy=TaskIntentRetryPolicy(max_attempts=2),
    ).run_once()
    assert exhausted.failed == 1
    assert exhausted_repository.failed[0]["error_code"] == "TEMPORAL_RETRY_EXHAUSTED"


@pytest.mark.anyio
async def test_consumer_never_persists_exception_text_and_counts_lost_lease() -> None:
    database = _Database()
    repository = _Repository((_intent(),))
    repository.applied = False
    starter = _Starter(database, RuntimeError("secret-value-must-not-be-persisted"))

    result = await _consumer(database, repository, starter).run_once()

    assert result.lease_lost == 1
    assert repository.retried[0]["error_code"] == "INTENT_DISPATCH_UNEXPECTED"
    assert "secret" not in str(repository.retried[0]).lower()


@pytest.mark.anyio
async def test_run_forever_does_not_poll_after_stop() -> None:
    database = _Database()
    repository = _Repository(())
    starter = _Starter(database)
    stop_event = asyncio.Event()
    stop_event.set()

    await _consumer(database, repository, starter).run_forever(stop_event)

    assert database.transactions == 0


def test_retry_policy_and_consumer_validate_configuration() -> None:
    with pytest.raises(ValueError, match="maximum attempts"):
        TaskIntentRetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="100 milliseconds"):
        TaskIntentRetryPolicy(initial_backoff=timedelta(milliseconds=99))
    with pytest.raises(ValueError, match="maximum backoff"):
        TaskIntentRetryPolicy(
            initial_backoff=timedelta(seconds=2),
            maximum_backoff=timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="dispatch attempts"):
        TaskIntentRetryPolicy().delay_after(0)

    database = _Database()
    repository = _Repository(())
    starter = _Starter(database)
    with pytest.raises(ValueError, match="identity"):
        TaskWorkflowIntentConsumer(
            database,
            starter,
            dispatcher_id="-invalid",
            temporal_namespace="atlas-task",
            batch_size=1,
            lease_duration=timedelta(seconds=1),
            poll_interval=timedelta(seconds=1),
            retry_policy=TaskIntentRetryPolicy(),
            repository=cast(TaskWorkflowIntentRepository, repository),
        )
    with pytest.raises(ValueError, match="error code"):
        TaskIntentInvariantError("unsafe error text")
