"""Database-free Browser Worker process wiring and fail-closed tests."""

from __future__ import annotations

import logging
from base64 import b64encode
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import pytest

from atlas_testops.application.ports.browser_runtime import BrowserExecutionEngine
from atlas_testops.application.ports.sessions import SessionArtifactVault
from atlas_testops.core.config import BrowserWorkerSettings
from atlas_testops.domain.runtime import BrowserActionKind
from atlas_testops.infrastructure.adapters.playwright_browser import (
    BrowserArtifactWriter,
    BrowserOperationRegistry,
    BrowserRouteRegistry,
    BrowserToolCatalog,
)
from atlas_testops.infrastructure.browser_auth import BrowserRuntimeRequestSigner
from atlas_testops.orchestration.browser import (
    BrowserExecutionActivities,
    BrowserExecutionWorkflow,
    CloseableBrowserRuntimeGateway,
)
from atlas_testops.workers import browser

REQUEST_KEY_BASE64 = b64encode(b"r" * 32).decode("ascii")
ENVELOPE_KEY_BASE64 = b64encode(b"e" * 32).decode("ascii")


def configured_settings(**overrides: object) -> BrowserWorkerSettings:
    """Build a complete configuration whose digests match executable rules."""

    defaults = BrowserWorkerSettings(environment="test")
    allowed_actions = frozenset(
        BrowserActionKind(item) for item in defaults.browser_allowed_actions
    )
    catalog = BrowserToolCatalog.reviewed(
        catalog_ref="browser-tools@1.0.0",
        policy_bundle_ref="browser-policy@1.0.0",
        allowed_actions=allowed_actions,
    )
    values: dict[str, object] = {
        "environment": "test",
        "temporal_address": "temporal.internal:7233",
        "temporal_namespace": "atlas-test",
        "browser_runtime_task_queue": "atlas-browser-test",
        "browser_runtime_http_timeout_seconds": 7,
        "browser_runtime_api_base_url": "https://control.example.test/",
        "browser_runtime_worker_identity": "browser-worker-test",
        "browser_runtime_request_hmac_key_base64": REQUEST_KEY_BASE64,
        "browser_context_envelope_key_base64": ENVELOPE_KEY_BASE64,
        "browser_context_envelope_key_version": "browser-context@1",
        "browser_revision": "playwright@1/chromium@1",
        "browser_headless": True,
        "browser_worker_max_concurrency": 3,
        "browser_action_timeout_seconds": 11,
        "browser_tool_catalog_ref": catalog.catalog_ref,
        "browser_policy_bundle_ref": catalog.policy_bundle_ref,
        "browser_mcp_server_manifest_digest": catalog.mcp_server_manifest_digest,
        "browser_tool_schema_digest": catalog.tool_schema_digest,
        "browser_policy_digest": catalog.policy_digest,
    }
    values.update(overrides)
    return BrowserWorkerSettings.model_validate(values)


def test_gateway_factory_builds_exact_run_scoped_signed_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    gateway = cast(CloseableBrowserRuntimeGateway, object())

    def fake_gateway(**kwargs: object) -> CloseableBrowserRuntimeGateway:
        captured.update(kwargs)
        return gateway

    signer = BrowserRuntimeRequestSigner.from_base64_key(REQUEST_KEY_BASE64)
    monkeypatch.setattr(browser, "HttpBrowserRuntimeGateway", fake_gateway)
    factory = browser.HttpBrowserRuntimeGatewayFactory(
        api_base_url="https://control.example.test",
        request_signer=signer,
        timeout=timedelta(seconds=9),
        allow_insecure_http=True,
    )
    tenant_id = uuid4()

    created = factory.create(
        tenant_id=tenant_id,
        worker_identity="browser-worker-test",
        execution_permit="permit-token",
    )

    assert created is gateway
    assert captured == {
        "api_base_url": "https://control.example.test",
        "tenant_id": tenant_id,
        "worker_identity": "browser-worker-test",
        "execution_permit": "permit-token",
        "request_signer": signer,
        "timeout": timedelta(seconds=9),
        "allow_insecure_http": True,
    }


@pytest.mark.anyio
@pytest.mark.parametrize("inject_vault", [False, True])
async def test_run_worker_assembles_temporal_with_injected_engine(
    monkeypatch: pytest.MonkeyPatch,
    inject_vault: bool,
) -> None:
    connections: list[tuple[str, str]] = []
    captured: dict[str, Any] = {}
    built_vault = cast(SessionArtifactVault, object())
    supplied_engine = cast(BrowserExecutionEngine, object())
    build_calls: list[BrowserWorkerSettings] = []

    class FakeClient:
        @classmethod
        async def connect(cls, address: str, *, namespace: str) -> object:
            connections.append((address, namespace))
            return object()

    class FakeWorker:
        def __init__(
            self,
            client: object,
            *,
            task_queue: str,
            workflows: Sequence[type[object]],
            activities: Sequence[object],
            max_concurrent_activities: int,
        ) -> None:
            captured.update(
                client=client,
                task_queue=task_queue,
                workflows=workflows,
                activities=activities,
                max_concurrent_activities=max_concurrent_activities,
            )

        async def run(self) -> None:
            captured["ran"] = True

    async def fake_build(settings: BrowserWorkerSettings) -> SessionArtifactVault:
        build_calls.append(settings)
        return built_vault

    monkeypatch.setattr(browser, "Client", FakeClient)
    monkeypatch.setattr(browser, "Worker", FakeWorker)
    monkeypatch.setattr(browser, "build_optional_session_artifact_vault", fake_build)
    settings = configured_settings()

    await browser.run_worker(
        settings,
        session_vault=built_vault if inject_vault else None,
        engine=supplied_engine,
    )

    assert build_calls == ([] if inject_vault else [settings])
    assert connections == [("temporal.internal:7233", "atlas-test")]
    assert captured["task_queue"] == "atlas-browser-test"
    assert captured["workflows"] == [BrowserExecutionWorkflow]
    assert captured["max_concurrent_activities"] == 3
    assert captured["ran"] is True
    activities = cast(Sequence[object], captured["activities"])
    assert len(activities) == 1
    execute = cast(Any, activities[0])
    activity_owner = cast(BrowserExecutionActivities, execute.__self__)
    assert activity_owner._engine is supplied_engine
    gateway_factory = cast(
        browser.HttpBrowserRuntimeGatewayFactory,
        activity_owner._gateway_factory,
    )
    assert gateway_factory._allow_insecure_http


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("environment", "api_base_url", "allow_insecure_http"),
    [
        ("local", "http://control.example.test", True),
        ("test", "http://control.example.test", True),
        ("development", "http://control.example.test", True),
        ("staging", "https://control.example.test", False),
        ("production", "https://control.example.test", False),
    ],
)
async def test_run_worker_passes_environment_transport_policy_to_gateway_factory(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    api_base_url: str,
    allow_insecure_http: bool,
) -> None:
    factory_arguments: dict[str, object] = {}

    class CapturingFactory:
        def __init__(self, **kwargs: object) -> None:
            factory_arguments.update(kwargs)

    class FakeClient:
        @classmethod
        async def connect(cls, _address: str, *, namespace: str) -> object:
            assert namespace == "atlas-test"
            return object()

    class FakeWorker:
        def __init__(self, _client: object, **_kwargs: object) -> None:
            pass

        async def run(self) -> None:
            pass

    monkeypatch.setattr(browser, "HttpBrowserRuntimeGatewayFactory", CapturingFactory)
    monkeypatch.setattr(browser, "Client", FakeClient)
    monkeypatch.setattr(browser, "Worker", FakeWorker)

    await browser.run_worker(
        configured_settings(
            environment=environment,
            browser_runtime_api_base_url=api_base_url,
        ),
        session_vault=cast(SessionArtifactVault, object()),
        engine=cast(BrowserExecutionEngine, object()),
    )

    assert factory_arguments["api_base_url"] == api_base_url
    assert factory_arguments["allow_insecure_http"] is allow_insecure_http


@pytest.mark.anyio
async def test_run_worker_builds_default_engine_and_closes_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    runtime_arguments: dict[str, object] = {}
    engine_arguments: dict[str, object] = {}
    worker_arguments: dict[str, object] = {}
    vault = cast(SessionArtifactVault, object())
    route_registry = BrowserRouteRegistry()
    operation_registry = BrowserOperationRegistry()
    artifact_writer = cast(BrowserArtifactWriter, object())

    class FakeRuntime:
        def __init__(self, **kwargs: object) -> None:
            runtime_arguments.update(kwargs)

        async def close(self) -> None:
            events.append("runtime-close")

    class FakeEngine:
        def __init__(self, **kwargs: object) -> None:
            engine_arguments.update(kwargs)

    class FakeClient:
        @classmethod
        async def connect(cls, address: str, *, namespace: str) -> object:
            events.append(f"connect:{address}:{namespace}")
            return object()

    class FakeWorker:
        def __init__(self, client: object, **kwargs: object) -> None:
            worker_arguments.update(kwargs)

        async def run(self) -> None:
            events.append("worker-run")

    monkeypatch.setattr(browser, "PlaywrightExecutionRuntime", FakeRuntime)
    monkeypatch.setattr(browser, "PlaywrightBrowserExecutionEngine", FakeEngine)
    monkeypatch.setattr(browser, "Client", FakeClient)
    monkeypatch.setattr(browser, "Worker", FakeWorker)
    settings = configured_settings()

    await browser.run_worker(
        settings,
        session_vault=vault,
        route_registry=route_registry,
        operation_registry=operation_registry,
        artifact_writer=artifact_writer,
    )

    assert runtime_arguments == {
        "revision": "playwright@1/chromium@1",
        "headless": True,
        "maximum_concurrency": 3,
    }
    assert engine_arguments["runtime"].__class__ is FakeRuntime
    assert engine_arguments["session_vault"] is vault
    assert engine_arguments["route_registry"] is route_registry
    assert engine_arguments["operation_registry"] is operation_registry
    assert engine_arguments["artifact_writer"] is artifact_writer
    assert engine_arguments["action_timeout"] == timedelta(seconds=11)
    catalog = cast(BrowserToolCatalog, engine_arguments["tool_catalog"])
    assert catalog.catalog_ref == "browser-tools@1.0.0"
    assert catalog.policy_bundle_ref == "browser-policy@1.0.0"
    assert events == [
        "connect:temporal.internal:7233:atlas-test",
        "worker-run",
        "runtime-close",
    ]
    assert worker_arguments["task_queue"] == "atlas-browser-test"


@pytest.mark.anyio
async def test_run_worker_closes_owned_runtime_when_temporal_worker_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_closed = False

    class FakeRuntime:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def close(self) -> None:
            nonlocal runtime_closed
            runtime_closed = True

    class FakeEngine:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class FakeClient:
        @classmethod
        async def connect(cls, _address: str, *, namespace: str) -> object:
            assert namespace == "atlas-test"
            return object()

    class FailingWorker:
        def __init__(self, _client: object, **_kwargs: object) -> None:
            pass

        async def run(self) -> None:
            raise RuntimeError("Temporal stopped")

    monkeypatch.setattr(browser, "PlaywrightExecutionRuntime", FakeRuntime)
    monkeypatch.setattr(browser, "PlaywrightBrowserExecutionEngine", FakeEngine)
    monkeypatch.setattr(browser, "Client", FakeClient)
    monkeypatch.setattr(browser, "Worker", FailingWorker)

    with pytest.raises(RuntimeError, match="Temporal stopped"):
        await browser.run_worker(
            configured_settings(),
            session_vault=cast(SessionArtifactVault, object()),
        )

    assert runtime_closed


@pytest.mark.anyio
async def test_run_worker_fails_closed_before_temporal_without_runtime_config() -> None:
    settings = BrowserWorkerSettings(environment="test")

    with pytest.raises(ValueError, match="runtime is not configured"):
        await browser.run_worker(settings)


@pytest.mark.anyio
async def test_run_worker_fails_closed_for_incomplete_constructed_config() -> None:
    settings = BrowserWorkerSettings.model_construct(
        environment="test",
        browser_runtime_api_base_url="https://control.example.test",
    )

    with pytest.raises(RuntimeError, match="configuration is incomplete"):
        await browser.run_worker(settings)


@pytest.mark.anyio
async def test_run_worker_fails_closed_when_session_vault_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_calls = 0

    async def fake_build(_settings: BrowserWorkerSettings) -> None:
        nonlocal build_calls
        build_calls += 1
        return None

    class UnexpectedClient:
        @classmethod
        async def connect(cls, _address: str, *, namespace: str) -> object:
            pytest.fail(f"Temporal must not be contacted (namespace={namespace})")

    monkeypatch.setattr(browser, "build_optional_session_artifact_vault", fake_build)
    monkeypatch.setattr(browser, "Client", UnexpectedClient)

    with pytest.raises(ValueError, match="SessionArtifact Vault is not configured"):
        await browser.run_worker(
            configured_settings(),
            engine=cast(BrowserExecutionEngine, object()),
        )

    assert build_calls == 1


def test_browser_worker_has_no_control_plane_database_dependency() -> None:
    assert not hasattr(browser, "Database")
    assert "database_url" not in BrowserWorkerSettings.model_fields


def test_main_loads_worker_settings_configures_logging_and_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = configured_settings(log_level="DEBUG")
    observed: list[BrowserWorkerSettings] = []
    log_levels: list[object] = []

    async def fake_run_worker(received: BrowserWorkerSettings, **_kwargs: object) -> None:
        observed.append(received)

    monkeypatch.setattr(browser, "BrowserWorkerSettings", lambda: settings)
    monkeypatch.setattr(browser, "run_worker", fake_run_worker)
    monkeypatch.setattr(
        logging,
        "basicConfig",
        lambda *, level: log_levels.append(level),
    )

    browser.main()

    assert observed == [settings]
    assert log_levels == ["DEBUG"]


def test_configured_settings_normalizes_api_origin() -> None:
    settings = configured_settings()

    assert settings.browser_runtime_configured
    assert settings.browser_runtime_api_base_url == "https://control.example.test"
    assert settings.browser_runtime_worker_identity == "browser-worker-test"
    assert settings.browser_worker_max_concurrency == 3
