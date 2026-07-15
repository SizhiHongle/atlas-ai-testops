"""FastAPI 应用入口。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from temporalio.client import Client

from atlas_testops import __version__
from atlas_testops.api.internal.router import internal_api_router
from atlas_testops.api.middleware import (
    browser_runtime_body_limit_middleware,
    request_context_middleware,
)
from atlas_testops.api.problem_details import register_exception_handlers
from atlas_testops.api.router import api_router
from atlas_testops.application.debug_run_dispatcher import DebugRunDispatcher
from atlas_testops.application.fixture_dispatcher import FixtureRunDispatcher
from atlas_testops.application.ports.browser_runtime import BrowserContextEnvelopeCodec
from atlas_testops.application.ports.secrets import SecretProvider
from atlas_testops.application.session_dispatcher import AuthSessionDispatcher
from atlas_testops.core.config import Settings, get_settings
from atlas_testops.infrastructure.adapters.fixture_registry import FixtureOperationRegistry
from atlas_testops.infrastructure.adapters.registry import AdapterRegistry
from atlas_testops.infrastructure.browser_auth import (
    BrowserRuntimePermitSigner,
    BrowserRuntimeRequestSigner,
)
from atlas_testops.infrastructure.browser_envelope import AesGcmBrowserContextEnvelopeCodec
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.passwords import PasswordService
from atlas_testops.orchestration.browser import TemporalBrowserExecutionDispatcher
from atlas_testops.orchestration.fixtures import TemporalFixtureRunDispatcher
from atlas_testops.orchestration.sessions import TemporalAuthSessionDispatcher


@asynccontextmanager
async def application_lifespan(application: FastAPI) -> AsyncIterator[None]:
    """按进程生命周期管理连接池，避免资源在请求间反复创建。"""

    settings: Settings = application.state.settings
    database = Database(settings) if settings.database_url_value is not None else None
    application.state.database = database
    if database is not None:
        await database.open()
    try:
        if (
            settings.auth_session_dispatch_enabled
            and application.state.auth_session_dispatcher is None
        ):
            temporal_client = await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
            )
            application.state.auth_session_dispatcher = TemporalAuthSessionDispatcher(
                temporal_client,
                task_queue=settings.auth_session_task_queue,
                workflow_timeout=timedelta(seconds=settings.auth_session_workflow_timeout_seconds),
            )
        if settings.fixture_dispatch_enabled and application.state.fixture_run_dispatcher is None:
            temporal_client = await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
            )
            application.state.fixture_run_dispatcher = TemporalFixtureRunDispatcher(
                temporal_client,
                task_queue=settings.fixture_task_queue,
                activity_timeout=timedelta(seconds=settings.fixture_activity_timeout_seconds),
                cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
            )
        if (
            settings.browser_runtime_enabled
            and application.state.browser_execution_dispatcher is None
        ):
            temporal_client = await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
            )
            permit_signer = application.state.browser_runtime_permit_signer
            if not isinstance(permit_signer, BrowserRuntimePermitSigner):
                raise RuntimeError("Browser Runtime permit signer is unavailable")
            application.state.browser_execution_dispatcher = (
                TemporalBrowserExecutionDispatcher(
                    temporal_client,
                    task_queue=settings.browser_runtime_task_queue,
                    worker_identity=settings.browser_runtime_worker_identity,
                    permit_signer=permit_signer,
                    activity_timeout=timedelta(
                        seconds=settings.browser_runtime_activity_timeout_seconds
                    ),
                    heartbeat_timeout=timedelta(
                        seconds=settings.browser_runtime_heartbeat_timeout_seconds
                    ),
                    permit_ttl=timedelta(
                        seconds=settings.browser_runtime_permit_ttl_seconds
                    ),
                )
            )
        yield
    finally:
        if database is not None:
            await database.close()


def create_app(
    settings: Settings | None = None,
    *,
    adapter_registry: AdapterRegistry | None = None,
    secret_provider: SecretProvider | None = None,
    auth_session_dispatcher: AuthSessionDispatcher | None = None,
    fixture_operation_registry: FixtureOperationRegistry | None = None,
    fixture_run_dispatcher: FixtureRunDispatcher | None = None,
    debug_run_dispatcher: DebugRunDispatcher | None = None,
    browser_runtime_permit_signer: BrowserRuntimePermitSigner | None = None,
    browser_runtime_request_signer: BrowserRuntimeRequestSigner | None = None,
    browser_context_envelope_codec: BrowserContextEnvelopeCodec | None = None,
    browser_execution_dispatcher: TemporalBrowserExecutionDispatcher | None = None,
) -> FastAPI:
    """创建相互隔离、便于测试的 FastAPI 实例。"""
    app_settings = settings or get_settings()
    docs_url = "/docs" if app_settings.docs_enabled else None
    openapi_url = "/openapi.json" if app_settings.docs_enabled else None

    application = FastAPI(
        title=app_settings.service_name,
        version=__version__,
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
        lifespan=application_lifespan,
    )
    application.state.settings = app_settings
    application.state.database = None
    application.state.password_service = PasswordService(
        maximum_concurrency=app_settings.password_hash_concurrency
    )
    application.state.adapter_registry = adapter_registry or AdapterRegistry.from_settings(
        app_settings
    )
    application.state.secret_provider = secret_provider
    application.state.auth_session_dispatcher = auth_session_dispatcher
    application.state.fixture_operation_registry = (
        fixture_operation_registry or FixtureOperationRegistry.from_settings(app_settings)
    )
    application.state.fixture_run_dispatcher = fixture_run_dispatcher
    application.state.debug_run_dispatcher = debug_run_dispatcher
    configured_permit_signer, configured_request_signer, configured_envelope_codec = (
        _browser_runtime_security(app_settings)
    )
    application.state.browser_runtime_permit_signer = (
        browser_runtime_permit_signer or configured_permit_signer
    )
    application.state.browser_runtime_request_signer = (
        browser_runtime_request_signer or configured_request_signer
    )
    application.state.browser_context_envelope_codec = (
        browser_context_envelope_codec or configured_envelope_codec
    )
    application.state.browser_execution_dispatcher = browser_execution_dispatcher

    application.middleware("http")(request_context_middleware)
    application.middleware("http")(browser_runtime_body_limit_middleware)
    register_exception_handlers(application)

    if app_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.cors_origins,
            allow_credentials="*" not in app_settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(api_router, prefix=app_settings.api_v1_prefix)
    application.include_router(internal_api_router, prefix="/internal/v1")
    return application


def _browser_runtime_security(
    settings: Settings,
) -> tuple[
    BrowserRuntimePermitSigner | None,
    BrowserRuntimeRequestSigner | None,
    BrowserContextEnvelopeCodec | None,
]:
    """Construct Browser Runtime security only when the feature is explicitly enabled."""

    if not settings.browser_runtime_enabled:
        return None, None, None
    permit_key = settings.browser_runtime_permit_key_base64
    request_key = settings.browser_runtime_request_hmac_key_base64
    envelope_key = settings.browser_context_envelope_key_base64
    envelope_key_version = settings.browser_context_envelope_key_version
    if (
        permit_key is None
        or request_key is None
        or envelope_key is None
        or envelope_key_version is None
    ):
        raise RuntimeError("enabled browser runtime security is incomplete")
    permit_signer = BrowserRuntimePermitSigner.from_base64_key(
        permit_key.get_secret_value(),
        maximum_lifetime=timedelta(seconds=settings.browser_runtime_permit_ttl_seconds),
    )
    request_signer = BrowserRuntimeRequestSigner.from_base64_key(
        request_key.get_secret_value(),
        maximum_clock_skew=timedelta(
            seconds=settings.browser_runtime_request_clock_skew_seconds
        ),
    )
    envelope_codec = AesGcmBrowserContextEnvelopeCodec.from_base64_key(
        envelope_key.get_secret_value(),
        key_version=envelope_key_version,
    )
    return permit_signer, request_signer, envelope_codec


app = create_app()
