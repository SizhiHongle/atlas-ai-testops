"""Shared FastAPI dependencies."""

from typing import Annotated, cast

from fastapi import Depends, Request

from atlas_testops.core.config import Settings


def get_app_settings(request: Request) -> Settings:
    """Return settings attached by the application factory."""
    return cast(Settings, request.app.state.settings)


SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
