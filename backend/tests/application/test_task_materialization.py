"""Recoverable TaskRun materialization consumer tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from psycopg.errors import CheckViolation

from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.application.task_materialization import (
    TaskMaterializationConsumer,
)
from atlas_testops.infrastructure.task_materialization import (
    ClaimedTaskMaterializationPartition,
)


def _partition(*, attempts: int = 1) -> ClaimedTaskMaterializationPartition:
    return ClaimedTaskMaterializationPartition(
        id=UUID(int=1),
        tenant_id=UUID(int=2),
        project_id=UUID(int=3),
        task_run_id=UUID(int=4),
        manifest_hash="sha256:" + "a" * 64,
        partition_index=0,
        first_ordinal=1,
        last_ordinal=64,
        status="CLAIMED",
        claim_token=UUID(int=5),
        revision=2,
        materialization_attempts=attempts,
        claim_expires_at=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class _Database:
    def __init__(self) -> None:
        self.transactions = 0
        self.active = 0

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[object]:
        self.transactions += 1
        self.active += 1
        try:
            yield object()
        finally:
            self.active -= 1


class _Repository:
    def __init__(
        self,
        *,
        claimed: tuple[ClaimedTaskMaterializationPartition, ...],
        complete_result: bool = True,
        complete_error: Exception | None = None,
    ) -> None:
        self.claimed = claimed
        self.complete_result = complete_result
        self.complete_error = complete_error
        self.calls: list[str] = []

    async def claim(self, _connection: object, **_kwargs: object) -> tuple[
        ClaimedTaskMaterializationPartition, ...
    ]:
        self.calls.append("claim")
        return self.claimed

    async def complete(self, _connection: object, **_kwargs: object) -> bool:
        self.calls.append("complete")
        if self.complete_error is not None:
            raise self.complete_error
        return self.complete_result

    async def retry(self, _connection: object, **_kwargs: object) -> bool:
        self.calls.append("retry")
        return True

    async def fail(self, _connection: object, **_kwargs: object) -> bool:
        self.calls.append("fail")
        return True


def _consumer(
    database: _Database,
    repository: _Repository,
    *,
    max_attempts: int = 3,
) -> TaskMaterializationConsumer:
    return TaskMaterializationConsumer(
        database,  # type: ignore[arg-type]
        dispatcher_id="materializer-test",
        batch_size=8,
        lease_duration=timedelta(seconds=30),
        poll_interval=timedelta(seconds=1),
        retry_policy=TaskIntentRetryPolicy(max_attempts=max_attempts),
        repository=repository,  # type: ignore[arg-type]
    )


@pytest.mark.anyio
async def test_consumer_completes_claimed_partitions_in_independent_transactions() -> None:
    database = _Database()
    repository = _Repository(claimed=(_partition(),))

    result = await _consumer(database, repository).run_once()

    assert result.claimed == 1
    assert result.completed == 1
    assert result.retried == result.failed == result.lease_lost == 0
    assert repository.calls == ["claim", "complete"]
    assert database.transactions == 2
    assert database.active == 0


@pytest.mark.anyio
async def test_consumer_retries_unknown_transient_failure_with_safe_code() -> None:
    database = _Database()
    repository = _Repository(
        claimed=(_partition(),),
        complete_error=RuntimeError("secret database detail"),
    )

    result = await _consumer(database, repository).run_once()

    assert result.retried == 1
    assert result.failed == 0
    assert repository.calls == ["claim", "complete", "retry"]
    assert database.active == 0


@pytest.mark.anyio
async def test_consumer_fails_permanent_or_exhausted_partition_without_replay() -> None:
    for partition, error in (
        (_partition(), CheckViolation()),
        (_partition(attempts=3), RuntimeError("still unavailable")),
    ):
        database = _Database()
        repository = _Repository(
            claimed=(partition,),
            complete_error=error,
        )

        result = await _consumer(database, repository).run_once()

        assert result.failed == 1
        assert result.retried == 0
        assert repository.calls == ["claim", "complete", "fail"]
        assert database.active == 0


@pytest.mark.anyio
async def test_consumer_reports_claim_loss_without_retrying_completed_work() -> None:
    database = _Database()
    repository = _Repository(
        claimed=(_partition(),),
        complete_result=False,
    )

    result = await _consumer(database, repository).run_once()

    assert result.lease_lost == 1
    assert result.completed == result.retried == result.failed == 0
    assert repository.calls == ["claim", "complete"]
