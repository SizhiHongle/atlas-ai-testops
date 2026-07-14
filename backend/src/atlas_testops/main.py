"""FastAPI 应用入口。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from temporalio.client import Client

from atlas_testops import __version__
from atlas_testops.api.internal.router import internal_api_router
from atlas_testops.api.middleware import request_context_middleware
from atlas_testops.api.problem_details import register_exception_handlers
from atlas_testops.api.router import api_router
from atlas_testops.application.ports.secrets import SecretProvider
from atlas_testops.application.session_dispatcher import AuthSessionDispatcher
from atlas_testops.core.config import Settings, get_settings
from atlas_testops.infrastructure.adapters.registry import AdapterRegistry
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.passwords import PasswordService
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
                workflow_timeout=timedelta(
                    seconds=settings.auth_session_workflow_timeout_seconds
                ),
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

    application.middleware("http")(request_context_middleware)
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


app = create_app()
