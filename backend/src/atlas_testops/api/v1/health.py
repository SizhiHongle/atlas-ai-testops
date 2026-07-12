"""Service health endpoints."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from atlas_testops import __version__
from atlas_testops.api.dependencies import SettingsDependency

router = APIRouter(prefix="/health")


class HealthResponse(BaseModel):
    """Stable health response contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok", "ready"]
    service: str
    version: str
    environment: str


@router.get("/live", response_model=HealthResponse)
async def liveness(settings: SettingsDependency) -> HealthResponse:
    """Report whether the API process is alive."""
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=__version__,
        environment=settings.environment,
    )


@router.get("/ready", response_model=HealthResponse)
async def readiness(settings: SettingsDependency) -> HealthResponse:
    """Report whether configured service dependencies are ready."""
    return HealthResponse(
        status="ready",
        service=settings.service_name,
        version=__version__,
        environment=settings.environment,
    )
