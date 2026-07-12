"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from atlas_testops import __version__
from atlas_testops.api.router import api_router
from atlas_testops.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an isolated FastAPI application instance."""
    app_settings = settings or get_settings()
    docs_url = "/docs" if app_settings.docs_enabled else None
    openapi_url = "/openapi.json" if app_settings.docs_enabled else None

    application = FastAPI(
        title=app_settings.service_name,
        version=__version__,
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
    )
    application.state.settings = app_settings

    if app_settings.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.cors_origins,
            allow_credentials="*" not in app_settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(api_router, prefix=app_settings.api_v1_prefix)
    return application


app = create_app()
