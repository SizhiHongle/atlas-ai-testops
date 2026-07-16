"""Task Workflow Intent Consumer process wiring tests."""

import asyncio
from typing import Any

import pytest
from pydantic import SecretStr

from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.core.config import TaskIntentConsumerSettings
from atlas_testops.workers import task_intent


def _enabled_settings() -> TaskIntentConsumerSettings:
    return TaskIntentConsumerSettings(
        environment="test",
        task_intent_consumption_enabled=True,
        task_dispatcher_database_url=SecretStr(
            "postgresql://atlas_dispatcher:secret@postgres/atlas"
        ),
        task_dispatcher_database_pool_min_size=2,
        task_dispatcher_database_pool_max_size=5,
        task_dispatcher_database_connect_timeout_seconds=7,
        task_dispatcher_database_statement_timeout_ms=8_000,
        temporal_address="temporal:7233",
        task_intent_temporal_namespace="task-namespace",
        task_intent_worker_identity="task-dispatcher-test",
        task_intent_poll_interval_seconds=2,
        task_intent_lease_seconds=45,
        task_intent_batch_size=12,
        task_intent_max_attempts=6,
        task_intent_retry_initial_seconds=3,
        task_intent_retry_maximum_seconds=90,
        task_intent_rpc_attempts=2,
        task_intent_rpc_timeout_seconds=8,
        task_intent_rpc_retry_delay_seconds=0.5,
    )


@pytest.mark.anyio
async def test_disabled_consumer_does_not_construct_privileged_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedDatabase:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("disabled consumer constructed the dispatcher database")

    monkeypatch.setattr(
        task_intent,
        "TaskIntentDispatcherDatabase",
        UnexpectedDatabase,
    )

    await task_intent.run_consumer(TaskIntentConsumerSettings(environment="test"))


@pytest.mark.anyio
async def test_consumer_wires_isolated_database_temporal_and_retry_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, Any] = {}

    class FakeDatabase:
        def __init__(self, database_url: str, **kwargs: object) -> None:
            events.append("database")
            captured["database_url"] = database_url
            captured["database_options"] = kwargs

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("close")

    class FakeClient:
        @classmethod
        async def connect(cls, address: str, *, namespace: str) -> object:
            events.append("client")
            captured["temporal"] = (address, namespace)
            return object()

    class FakeStarter:
        def __init__(self, client: object, **kwargs: object) -> None:
            events.append("starter")
            captured["starter_client"] = client
            captured["starter_options"] = kwargs

    class FakeConsumer:
        def __init__(
            self,
            database: object,
            starter: object,
            **kwargs: object,
        ) -> None:
            events.append("consumer")
            captured["consumer_database"] = database
            captured["consumer_starter"] = starter
            captured["consumer_options"] = kwargs

        async def run_forever(self, stop_event: object) -> None:
            events.append("run")
            captured["stop_event"] = stop_event

    monkeypatch.setattr(task_intent, "TaskIntentDispatcherDatabase", FakeDatabase)
    monkeypatch.setattr(task_intent, "Client", FakeClient)
    monkeypatch.setattr(task_intent, "TemporalTaskIntentStarter", FakeStarter)
    monkeypatch.setattr(task_intent, "TaskWorkflowIntentConsumer", FakeConsumer)
    settings = _enabled_settings()

    await task_intent.run_consumer(settings)

    assert events == [
        "database",
        "client",
        "starter",
        "consumer",
        "open",
        "run",
        "close",
    ]
    assert captured["database_url"] == (
        "postgresql://atlas_dispatcher:secret@postgres/atlas"
    )
    assert captured["database_options"] == {
        "pool_min_size": 2,
        "pool_max_size": 5,
        "connect_timeout_seconds": 7.0,
        "statement_timeout_ms": 8_000,
    }
    assert captured["temporal"] == ("temporal:7233", "task-namespace")
    starter_options = captured["starter_options"]
    assert starter_options["rpc_attempts"] == 2
    assert starter_options["rpc_timeout"].total_seconds() == 8
    assert starter_options["retry_delay"].total_seconds() == 0.5
    consumer_options = captured["consumer_options"]
    assert consumer_options["dispatcher_id"] == "task-dispatcher-test"
    assert consumer_options["temporal_namespace"] == "task-namespace"
    assert consumer_options["batch_size"] == 12
    assert consumer_options["lease_duration"].total_seconds() == 45
    assert consumer_options["poll_interval"].total_seconds() == 2
    retry_policy = consumer_options["retry_policy"]
    assert isinstance(retry_policy, TaskIntentRetryPolicy)
    assert retry_policy.max_attempts == 6
    assert retry_policy.initial_backoff.total_seconds() == 3
    assert retry_policy.maximum_backoff.total_seconds() == 90
    assert captured["stop_event"].is_set()


@pytest.mark.anyio
async def test_consumer_closes_database_when_polling_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeDatabase:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("close")

    class FakeClient:
        @classmethod
        async def connect(cls, *_args: object, **_kwargs: object) -> object:
            return object()

    class FakeStarter:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class FailingConsumer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run_forever(self, _stop_event: object) -> None:
            raise RuntimeError("poll failed")

    monkeypatch.setattr(task_intent, "TaskIntentDispatcherDatabase", FakeDatabase)
    monkeypatch.setattr(task_intent, "Client", FakeClient)
    monkeypatch.setattr(task_intent, "TemporalTaskIntentStarter", FakeStarter)
    monkeypatch.setattr(task_intent, "TaskWorkflowIntentConsumer", FailingConsumer)
    stop_event = asyncio.Event()

    with pytest.raises(RuntimeError, match="poll failed"):
        await task_intent.run_consumer(_enabled_settings(), stop_event=stop_event)

    assert events == ["open", "close"]
    assert stop_event.is_set()


@pytest.mark.anyio
async def test_enabled_consumer_rechecks_missing_authority_and_queue_contract() -> None:
    missing_dsn = TaskIntentConsumerSettings.model_construct(
        environment="test",
        task_intent_consumption_enabled=True,
        task_dispatcher_database_url=None,
    )
    with pytest.raises(RuntimeError, match="no dispatcher DSN"):
        await task_intent.run_consumer(missing_dsn)

    wrong_queue = TaskIntentConsumerSettings.model_construct(
        environment="test",
        task_intent_consumption_enabled=True,
        task_dispatcher_database_url=SecretStr(
            "postgresql://atlas_dispatcher:secret@postgres/atlas"
        ),
        task_intent_task_queue="untrusted-queue",
    )
    with pytest.raises(RuntimeError, match="trusted workflow contract"):
        await task_intent.run_consumer(wrong_queue)


def test_main_loads_process_settings_and_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = TaskIntentConsumerSettings(environment="test")
    called: list[TaskIntentConsumerSettings] = []

    async def fake_run_consumer(value: TaskIntentConsumerSettings) -> None:
        called.append(value)

    monkeypatch.setattr(task_intent, "TaskIntentConsumerSettings", lambda: settings)
    monkeypatch.setattr(task_intent, "run_consumer", fake_run_consumer)

    task_intent.main()

    assert called == [settings]
