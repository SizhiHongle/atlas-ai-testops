"""Fixture Worker process wiring tests."""

from typing import Any

import pytest
from pydantic import SecretStr

from atlas_testops.core.config import Settings
from atlas_testops.workers import fixture


@pytest.mark.anyio
async def test_fixture_worker_wires_database_temporal_and_all_activities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured: dict[str, Any] = {}

    class FakeDatabase:
        def __init__(self, settings: Settings) -> None:
            events.append(f"database:{settings.environment}")

        async def open(self) -> None:
            events.append("open")

        async def close(self) -> None:
            events.append("close")

    class FakeClient:
        @classmethod
        async def connect(cls, address: str, *, namespace: str) -> object:
            events.append(f"client:{address}:{namespace}")
            return object()

    class FakeWorker:
        def __init__(self, client: object, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def run(self) -> None:
            events.append("run")

    monkeypatch.setattr(fixture, "Database", FakeDatabase)
    monkeypatch.setattr(fixture, "Client", FakeClient)
    monkeypatch.setattr(fixture, "Worker", FakeWorker)
    settings = Settings(
        environment="test",
        database_url=SecretStr("postgresql://ignored:ignored@localhost/ignored"),
        fixture_task_queue="fixture-test-queue",
        fixture_worker_max_concurrency=3,
    )

    await fixture.run_worker(settings)

    assert events == [
        "database:test",
        "client:127.0.0.1:7233:default",
        "open",
        "run",
        "close",
    ]
    assert captured["task_queue"] == "fixture-test-queue"
    assert captured["max_concurrent_activities"] == 3
    assert len(captured["workflows"]) == 3
    assert len(captured["activities"]) == 10


def test_fixture_worker_main_loads_settings_and_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(environment="test")
    called: list[Settings] = []

    async def fake_run_worker(value: Settings) -> None:
        called.append(value)

    monkeypatch.setattr(fixture, "get_settings", lambda: settings)
    monkeypatch.setattr(fixture, "run_worker", fake_run_worker)

    fixture.main()

    assert called == [settings]
