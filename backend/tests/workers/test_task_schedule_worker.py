"""Task Schedule Worker process assembly tests."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from pydantic import SecretStr

from atlas_testops.core.config import Settings
from atlas_testops.orchestration.task_schedules import TASK_SCHEDULE_TASK_QUEUE
from atlas_testops.workers import task_schedule


@pytest.mark.anyio
async def test_disabled_worker_constructs_no_database_or_temporal_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedDatabase:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("disabled Schedule Worker constructed a database")

    monkeypatch.setattr(task_schedule, "Database", UnexpectedDatabase)
    await task_schedule.run_worker(Settings(environment="test"))


@pytest.mark.anyio
async def test_enabled_worker_rejects_missing_database_and_untrusted_queue() -> None:
    missing_database = Settings(environment="test").model_copy(
        update={"task_schedule_worker_enabled": True}
    )
    with pytest.raises(RuntimeError, match="no API database DSN"):
        await task_schedule.run_worker(missing_database)

    untrusted_queue = Settings(
        environment="test",
        database_url=SecretStr("postgresql://atlas_app:secret@postgres/atlas"),
    ).model_copy(
        update={
            "task_schedule_worker_enabled": True,
            "task_schedule_task_queue": "untrusted-queue",
        }
    )
    with pytest.raises(RuntimeError, match="trusted workflow contract"):
        await task_schedule.run_worker(untrusted_queue)


@pytest.mark.anyio
async def test_worker_wires_fixed_queue_activity_database_and_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, Any] = {}
    settings = Settings(
        environment="test",
        database_url=SecretStr("postgresql://atlas_app:secret@postgres/atlas"),
        task_schedule_worker_enabled=True,
        task_schedule_worker_max_concurrency=7,
        temporal_address="temporal:7233",
        temporal_namespace="atlas-task",
    )

    class FakeDatabase:
        def __init__(self, selected: Settings) -> None:
            events.append("database")
            captured["database_settings"] = selected

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("close")

    class FakeService:
        def __init__(
            self,
            database: object,
            *,
            temporal_namespace: str,
        ) -> None:
            events.append("service")
            captured["service_database"] = database
            captured["service_namespace"] = temporal_namespace

    class FakeActivities:
        def __init__(self, service: object) -> None:
            events.append("activities")
            captured["activity_service"] = service

        async def fire(self, _request: object) -> object:
            return object()

    class FakeClient:
        @classmethod
        async def connect(cls, address: str, *, namespace: str) -> object:
            events.append("client")
            captured["client_options"] = (address, namespace)
            return object()

    class FakeWorker:
        def __init__(
            self,
            client: object,
            **options: object,
        ) -> None:
            events.append("worker")
            captured["worker_client"] = client
            captured["worker_options"] = options

        async def run(self) -> None:
            events.append("run")

    monkeypatch.setattr(task_schedule, "Database", FakeDatabase)
    monkeypatch.setattr(task_schedule, "TaskScheduleFireService", FakeService)
    monkeypatch.setattr(task_schedule, "TaskScheduleFireActivities", FakeActivities)
    monkeypatch.setattr(task_schedule, "Client", FakeClient)
    monkeypatch.setattr(task_schedule, "Worker", FakeWorker)

    await task_schedule.run_worker(settings)

    assert events == [
        "database",
        "service",
        "activities",
        "client",
        "worker",
        "open",
        "run",
        "close",
    ]
    assert captured["database_settings"] is settings
    assert captured["service_namespace"] == "atlas-task"
    assert captured["client_options"] == ("temporal:7233", "atlas-task")
    options = captured["worker_options"]
    assert options["task_queue"] == TASK_SCHEDULE_TASK_QUEUE
    assert options["max_concurrent_workflow_tasks"] == 7
    assert options["max_concurrent_activities"] == 7
    assert len(options["workflows"]) == 1
    assert len(options["activities"]) == 1


@pytest.mark.anyio
async def test_worker_closes_database_when_temporal_worker_exits_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    settings = Settings(
        environment="test",
        database_url=SecretStr("postgresql://atlas_app:secret@postgres/atlas"),
        task_schedule_worker_enabled=True,
    )

    class FakeDatabase:
        def __init__(self, _settings: Settings) -> None:
            pass

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("close")

    class FakeService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class FakeActivities:
        def __init__(self, _service: object) -> None:
            pass

        async def fire(self, _request: object) -> object:
            return object()

    class FakeClient:
        @classmethod
        async def connect(cls, *_args: object, **_kwargs: object) -> object:
            return object()

    class FailingWorker:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def run(self) -> None:
            raise RuntimeError("worker stopped")

    monkeypatch.setattr(task_schedule, "Database", FakeDatabase)
    monkeypatch.setattr(task_schedule, "TaskScheduleFireService", FakeService)
    monkeypatch.setattr(task_schedule, "TaskScheduleFireActivities", FakeActivities)
    monkeypatch.setattr(task_schedule, "Client", FakeClient)
    monkeypatch.setattr(task_schedule, "Worker", FailingWorker)

    with pytest.raises(RuntimeError, match="worker stopped"):
        await task_schedule.run_worker(settings)
    assert events == ["open", "close"]


def test_main_loads_settings_configures_logging_and_runs_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    settings = Settings(environment="test", log_level="DEBUG")

    async def fake_run_worker(selected: Settings) -> None:
        events.append(selected)

    def fake_basic_config(*, level: str) -> None:
        events.append(level)

    monkeypatch.setattr(task_schedule, "get_settings", lambda: settings)
    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)
    monkeypatch.setattr(task_schedule, "run_worker", fake_run_worker)

    task_schedule.main()

    assert events == ["DEBUG", settings]
