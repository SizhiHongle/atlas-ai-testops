"""Dedicated Auth Session Worker assembly tests with process doubles."""

from collections.abc import Sequence
from typing import ClassVar, cast

import pytest
from pydantic import SecretStr

from atlas_testops.application.ports.sessions import SessionArtifactVault
from atlas_testops.core.config import AuthSessionWorkerSettings, Settings
from atlas_testops.workers import auth_session


class FakeDatabase:
    instances: ClassVar[list[FakeDatabase]] = []

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.opened = False
        self.closed = False
        self.instances.append(self)

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True


class FakeClient:
    calls: ClassVar[list[tuple[str, str]]] = []

    @classmethod
    async def connect(cls, address: str, *, namespace: str) -> object:
        cls.calls.append((address, namespace))
        return object()


class FakeWorker:
    instances: ClassVar[list[FakeWorker]] = []

    def __init__(
        self,
        client: object,
        *,
        task_queue: str,
        workflows: Sequence[type[object]],
        activities: Sequence[object],
        max_concurrent_activities: int,
    ) -> None:
        self.client = client
        self.task_queue = task_queue
        self.workflows = workflows
        self.activities = activities
        self.max_concurrent_activities = max_concurrent_activities
        self.ran = False
        self.instances.append(self)

    async def run(self) -> None:
        self.ran = True


def runtime_settings() -> Settings:
    return Settings(
        environment="test",
        database_url=SecretStr("postgresql://atlas_app:atlas_app@postgres/atlas"),
        auth_session_worker_max_concurrency=3,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("inject_vault", [False, True])
async def test_run_worker_assembles_isolated_queue_and_closes_database(
    monkeypatch: pytest.MonkeyPatch,
    inject_vault: bool,
) -> None:
    FakeDatabase.instances.clear()
    FakeClient.calls.clear()
    FakeWorker.instances.clear()
    built_vault = cast(SessionArtifactVault, object())
    build_calls = 0

    async def fake_build(
        settings: AuthSessionWorkerSettings,
    ) -> SessionArtifactVault:
        nonlocal build_calls
        assert settings.environment == "test"
        build_calls += 1
        return built_vault

    monkeypatch.setattr(auth_session, "Database", FakeDatabase)
    monkeypatch.setattr(auth_session, "Client", FakeClient)
    monkeypatch.setattr(auth_session, "Worker", FakeWorker)
    monkeypatch.setattr(
        auth_session,
        "build_optional_session_artifact_vault",
        fake_build,
    )

    await auth_session.run_worker(
        runtime_settings(),
        AuthSessionWorkerSettings(environment="test"),
        session_vault=built_vault if inject_vault else None,
    )

    assert build_calls == (0 if inject_vault else 1)
    assert len(FakeDatabase.instances) == 1
    assert FakeDatabase.instances[0].opened
    assert FakeDatabase.instances[0].closed
    assert FakeClient.calls == [("127.0.0.1:7233", "default")]
    assert len(FakeWorker.instances) == 1
    worker = FakeWorker.instances[0]
    assert worker.ran
    assert worker.task_queue == "atlas-auth-session"
    assert worker.max_concurrent_activities == 3
    assert len(worker.workflows) == 2
    assert len(worker.activities) == 2


def test_worker_main_loads_process_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = runtime_settings()
    worker_settings = AuthSessionWorkerSettings(environment="test")
    observed: list[tuple[Settings, AuthSessionWorkerSettings]] = []

    async def fake_run_worker(
        received_settings: Settings,
        received_worker_settings: AuthSessionWorkerSettings,
        **_kwargs: object,
    ) -> None:
        observed.append((received_settings, received_worker_settings))

    monkeypatch.setattr(auth_session, "get_settings", lambda: settings)
    monkeypatch.setattr(
        auth_session,
        "AuthSessionWorkerSettings",
        lambda: worker_settings,
    )
    monkeypatch.setattr(auth_session, "run_worker", fake_run_worker)

    auth_session.main()

    assert observed == [(settings, worker_settings)]
