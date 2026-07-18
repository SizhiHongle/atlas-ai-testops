"""Task Worker process assembly and fail-closed lifecycle tests."""

import asyncio
from collections.abc import Sequence
from typing import Any, cast

import pytest
from pydantic import SecretStr

from atlas_testops.application.result_hygiene import ResultHygieneProjectionService
from atlas_testops.application.task_orchestration import TaskUnitExecutionPort
from atlas_testops.core.config import Settings
from atlas_testops.orchestration.tasks import (
    AtlasTaskRunWorkflow,
    AtlasUnitAttemptWorkflow,
)
from atlas_testops.workers import task


def _enabled_settings() -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr("postgresql://atlas_app:secret@postgres/atlas"),
        temporal_address="temporal:7233",
        temporal_namespace="task-namespace",
        task_worker_enabled=True,
        task_run_worker_max_concurrency=3,
        task_attempt_worker_max_concurrency=5,
    )


@pytest.mark.anyio
async def test_disabled_worker_does_not_construct_database_or_temporal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedDatabase:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("disabled Task Worker constructed a database")

    class UnexpectedClient:
        @classmethod
        async def connect(cls, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("disabled Task Worker connected to Temporal")

    monkeypatch.setattr(task, "Database", UnexpectedDatabase)
    monkeypatch.setattr(task, "Client", UnexpectedClient)

    await task.run_worker(Settings(environment="test"))


@pytest.mark.anyio
async def test_enabled_worker_requires_executor_before_external_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnexpectedDatabase:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("missing executor constructed a database")

    class UnexpectedClient:
        @classmethod
        async def connect(cls, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("missing executor connected to Temporal")

    monkeypatch.setattr(task, "Database", UnexpectedDatabase)
    monkeypatch.setattr(task, "Client", UnexpectedClient)

    with pytest.raises(RuntimeError, match="formal TaskUnitExecutionPort"):
        await task.run_worker(_enabled_settings())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"task_run_task_queue": "wrong-root"}, "Task Root queue"),
        ({"task_attempt_task_queue": "wrong-attempt"}, "Task Attempt queue"),
    ],
)
async def test_worker_rechecks_fixed_queue_contract_before_database_construction(
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, object],
    message: str,
) -> None:
    class UnexpectedDatabase:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("invalid queue constructed a database")

    monkeypatch.setattr(task, "Database", UnexpectedDatabase)
    settings = _enabled_settings().model_copy(update=override)

    with pytest.raises(RuntimeError, match=message):
        await task.run_worker(
            settings,
            executor=cast(TaskUnitExecutionPort, object()),
        )


@pytest.mark.anyio
async def test_worker_wires_isolated_queues_and_closes_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, object] = {}
    workers: list[Any] = []
    executor = cast(TaskUnitExecutionPort, object())

    class FakeDatabase:
        def __init__(self, settings: Settings) -> None:
            events.append("database")
            captured["database_settings"] = settings

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("close")

    class FakeService:
        def __init__(
            self,
            database: object,
            *,
            result_hygiene_projection_service: object,
        ) -> None:
            events.append("service")
            captured["service_database"] = database
            captured["result_hygiene_projection_service"] = result_hygiene_projection_service

    class FakeActivities:
        def __init__(self, service: object, received_executor: object) -> None:
            events.append("activities")
            captured["activities_service"] = service
            captured["executor"] = received_executor
            self.load_dispatch_plan = object()
            self.prepare_batch = object()
            self.checkpoint_control = object()
            self.settle_attempt_batch = object()
            self.finish_run = object()
            self.prepare_attempt = object()
            self.begin_attempt = object()
            self.execute_attempt = object()
            self.finish_attempt = object()

    class FakeClient:
        @classmethod
        async def connect(cls, address: str, *, namespace: str) -> object:
            events.append("client")
            captured["temporal"] = (address, namespace)
            return object()

    class FakeWorker:
        def __init__(
            self,
            client: object,
            *,
            task_queue: str,
            workflows: Sequence[type[object]],
            activities: Sequence[object],
            max_concurrent_workflow_tasks: int,
            max_concurrent_activities: int,
        ) -> None:
            events.append(f"worker:{task_queue}")
            self.client = client
            self.task_queue = task_queue
            self.workflows = workflows
            self.activities = activities
            self.max_concurrent_workflow_tasks = max_concurrent_workflow_tasks
            self.max_concurrent_activities = max_concurrent_activities
            workers.append(self)

        async def run(self) -> None:
            events.append(f"run:{self.task_queue}")

    monkeypatch.setattr(task, "Database", FakeDatabase)
    monkeypatch.setattr(task, "TaskWorkerService", FakeService)
    monkeypatch.setattr(task, "TaskOrchestrationActivities", FakeActivities)
    monkeypatch.setattr(task, "Client", FakeClient)
    monkeypatch.setattr(task, "Worker", FakeWorker)

    settings = _enabled_settings()
    await task.run_worker(settings, executor=executor)

    assert captured["database_settings"] is settings
    assert captured["executor"] is executor
    assert isinstance(
        captured["result_hygiene_projection_service"],
        ResultHygieneProjectionService,
    )
    assert captured["temporal"] == ("temporal:7233", "task-namespace")
    assert events[-1] == "close"
    assert events.index("open") < events.index("run:atlas-task-run")
    assert events.index("open") < events.index("run:atlas-unit-attempt")
    assert len(workers) == 2
    root_worker, attempt_worker = workers
    assert root_worker.task_queue == "atlas-task-run"
    assert root_worker.workflows == [AtlasTaskRunWorkflow]
    assert len(root_worker.activities) == 5
    assert root_worker.max_concurrent_workflow_tasks == 3
    assert root_worker.max_concurrent_activities == 3
    assert attempt_worker.task_queue == "atlas-unit-attempt"
    assert attempt_worker.workflows == [AtlasUnitAttemptWorkflow]
    assert len(attempt_worker.activities) == 4
    assert attempt_worker.max_concurrent_workflow_tasks == 5
    assert attempt_worker.max_concurrent_activities == 5


@pytest.mark.anyio
async def test_worker_cancels_peer_and_closes_database_when_one_poller_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    executor = cast(TaskUnitExecutionPort, object())

    class FakeDatabase:
        def __init__(self, _settings: Settings) -> None:
            pass

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("close")

    class FakeService:
        def __init__(
            self,
            _database: object,
            *,
            result_hygiene_projection_service: object,
        ) -> None:
            assert isinstance(
                result_hygiene_projection_service,
                ResultHygieneProjectionService,
            )

    class FakeActivities:
        def __init__(self, _service: object, _executor: object) -> None:
            self.load_dispatch_plan = object()
            self.prepare_batch = object()
            self.checkpoint_control = object()
            self.settle_attempt_batch = object()
            self.finish_run = object()
            self.prepare_attempt = object()
            self.begin_attempt = object()
            self.execute_attempt = object()
            self.finish_attempt = object()

    class FakeClient:
        @classmethod
        async def connect(cls, *_args: object, **_kwargs: object) -> object:
            return object()

    class FakeWorker:
        def __init__(self, _client: object, **kwargs: Any) -> None:
            self.task_queue = cast(str, kwargs["task_queue"])

        async def run(self) -> None:
            if self.task_queue == "atlas-task-run":
                await asyncio.sleep(0)
                raise RuntimeError("root poller failed")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("attempt-canceled")
                raise

    monkeypatch.setattr(task, "Database", FakeDatabase)
    monkeypatch.setattr(task, "TaskWorkerService", FakeService)
    monkeypatch.setattr(task, "TaskOrchestrationActivities", FakeActivities)
    monkeypatch.setattr(task, "Client", FakeClient)
    monkeypatch.setattr(task, "Worker", FakeWorker)

    with pytest.raises(RuntimeError, match="root poller failed"):
        await task.run_worker(_enabled_settings(), executor=executor)

    assert events == ["open", "attempt-canceled", "close"]


def test_main_loads_settings_without_manufacturing_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="test")
    observed: list[Settings] = []

    async def fake_run_worker(received: Settings) -> None:
        observed.append(received)

    monkeypatch.setattr(task, "get_settings", lambda: settings)
    monkeypatch.setattr(task, "run_worker", fake_run_worker)

    task.main()

    assert observed == [settings]
