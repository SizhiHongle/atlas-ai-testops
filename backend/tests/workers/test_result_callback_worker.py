"""Task Gate callback process assembly tests."""

from __future__ import annotations

from base64 import b64encode
from typing import Any

import pytest
from pydantic import SecretStr

from atlas_testops.core.config import TaskGateCallbackWorkerSettings
from atlas_testops.workers import result_callback

KEY = b64encode(b"k" * 32).decode()


def _settings() -> TaskGateCallbackWorkerSettings:
    return TaskGateCallbackWorkerSettings(
        environment="test",
        task_gate_callback_delivery_enabled=True,
        task_dispatcher_database_url=SecretStr(
            "postgresql://atlas_dispatcher:secret@postgres/atlas"
        ),
        task_gate_callback_url="https://callbacks.test/task-gates",
        task_gate_callback_hmac_key_base64=SecretStr(KEY),
        task_gate_callback_worker_identity="callback-worker-test",
        task_gate_callback_batch_size=7,
        task_gate_callback_lease_seconds=30,
        task_gate_callback_http_timeout_seconds=5,
    )


@pytest.mark.anyio
async def test_disabled_consumer_constructs_no_database_or_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedDatabase:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("disabled callback Consumer constructed a database")

    monkeypatch.setattr(
        result_callback,
        "TaskIntentDispatcherDatabase",
        UnexpectedDatabase,
    )
    await result_callback.run_consumer(TaskGateCallbackWorkerSettings(environment="test"))


@pytest.mark.anyio
async def test_consumer_wires_isolated_authority_signer_sender_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, Any] = {}
    settings = _settings()

    class FakeDatabase:
        def __init__(self, url: str, **options: object) -> None:
            events.append("database")
            captured["database_url"] = url
            captured["database_options"] = options

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("database-close")

    class FakeSigner:
        @classmethod
        def from_base64_key(
            cls,
            key: str,
            **options: object,
        ) -> object:
            events.append("signer")
            captured["key"] = key
            captured["signer_options"] = options
            return object()

    class FakeSender:
        def __init__(self, **options: object) -> None:
            events.append("sender")
            captured["sender_options"] = options

        async def aclose(self) -> None:
            events.append("sender-close")

    class FakeConsumer:
        def __init__(
            self,
            database: object,
            sender: object,
            **options: object,
        ) -> None:
            events.append("consumer")
            captured["consumer_database"] = database
            captured["consumer_sender"] = sender
            captured["consumer_options"] = options

        async def run_forever(self, stop_event: object) -> None:
            events.append("run")
            captured["stop_event"] = stop_event

    monkeypatch.setattr(
        result_callback,
        "TaskIntentDispatcherDatabase",
        FakeDatabase,
    )
    monkeypatch.setattr(result_callback, "TaskGateCallbackSigner", FakeSigner)
    monkeypatch.setattr(
        result_callback,
        "HttpTaskGateCallbackSender",
        FakeSender,
    )
    monkeypatch.setattr(
        result_callback,
        "TaskGateCallbackDeliveryConsumer",
        FakeConsumer,
    )

    await result_callback.run_consumer(settings)

    assert events == [
        "database",
        "signer",
        "sender",
        "consumer",
        "open",
        "run",
        "sender-close",
        "database-close",
    ]
    assert captured["database_url"].startswith("postgresql://atlas_dispatcher:")
    assert captured["key"] == KEY
    assert captured["sender_options"]["callback_url"] == ("https://callbacks.test/task-gates")
    assert captured["consumer_options"]["dispatcher_id"] == ("callback-worker-test")
    assert captured["consumer_options"]["batch_size"] == 7


@pytest.mark.anyio
async def test_consumer_closes_sender_and_database_after_poll_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeDatabase:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("database-close")

    class FakeSigner:
        @classmethod
        def from_base64_key(
            cls,
            *_args: object,
            **_kwargs: object,
        ) -> object:
            return object()

    class FakeSender:
        def __init__(self, **_options: object) -> None:
            pass

        async def aclose(self) -> None:
            events.append("sender-close")

    class FailingConsumer:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run_forever(self, _stop_event: object) -> None:
            raise RuntimeError("callback poll failed")

    monkeypatch.setattr(
        result_callback,
        "TaskIntentDispatcherDatabase",
        FakeDatabase,
    )
    monkeypatch.setattr(result_callback, "TaskGateCallbackSigner", FakeSigner)
    monkeypatch.setattr(
        result_callback,
        "HttpTaskGateCallbackSender",
        FakeSender,
    )
    monkeypatch.setattr(
        result_callback,
        "TaskGateCallbackDeliveryConsumer",
        FailingConsumer,
    )

    with pytest.raises(RuntimeError, match="callback poll failed"):
        await result_callback.run_consumer(_settings())

    assert events == ["open", "sender-close", "database-close"]


@pytest.mark.anyio
async def test_enabled_consumer_rechecks_required_process_config() -> None:
    missing_dsn = TaskGateCallbackWorkerSettings.model_construct(
        environment="test",
        task_gate_callback_delivery_enabled=True,
        task_dispatcher_database_url=None,
        task_gate_callback_url="https://callbacks.test/task-gates",
        task_gate_callback_hmac_key_base64=SecretStr(KEY),
    )
    with pytest.raises(RuntimeError, match="no dispatcher DSN"):
        await result_callback.run_consumer(missing_dsn)

    missing_signer = TaskGateCallbackWorkerSettings.model_construct(
        environment="test",
        task_gate_callback_delivery_enabled=True,
        task_dispatcher_database_url=SecretStr(
            "postgresql://atlas_dispatcher:secret@postgres/atlas"
        ),
        task_gate_callback_url=None,
        task_gate_callback_hmac_key_base64=None,
    )
    with pytest.raises(RuntimeError, match="incomplete signing config"):
        await result_callback.run_consumer(missing_signer)


def test_main_loads_settings_and_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = TaskGateCallbackWorkerSettings(environment="test")
    called: list[TaskGateCallbackWorkerSettings] = []

    async def fake_run_consumer(
        value: TaskGateCallbackWorkerSettings,
    ) -> None:
        called.append(value)

    monkeypatch.setattr(
        result_callback,
        "TaskGateCallbackWorkerSettings",
        lambda: settings,
    )
    monkeypatch.setattr(result_callback, "run_consumer", fake_run_consumer)

    result_callback.main()

    assert called == [settings]
