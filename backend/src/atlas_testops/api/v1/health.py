"""服务健康检查。"""

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import Field

from atlas_testops import __version__
from atlas_testops.api.dependencies import OptionalDatabaseDependency, SettingsDependency
from atlas_testops.core.contracts import FrozenWireModel

router = APIRouter(prefix="/health")


class DependencyCheck(FrozenWireModel):
    """单个依赖的 readiness 结果。"""

    name: str
    status: Literal["ready", "disabled", "not_ready"]


class HealthResponse(FrozenWireModel):
    """稳定的健康检查协议。"""

    status: Literal["ok", "ready", "not_ready"]
    service: str
    version: str
    environment: str
    checks: tuple[DependencyCheck, ...] = Field(default_factory=tuple)


@router.get("/live", response_model=HealthResponse)
async def liveness(settings: SettingsDependency) -> HealthResponse:
    """只报告 API 进程是否存活，不访问外部依赖。"""
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=__version__,
        environment=settings.environment,
    )


@router.get("/ready", response_model=HealthResponse)
async def readiness(
    settings: SettingsDependency,
    database: OptionalDatabaseDependency,
    response: Response,
) -> HealthResponse:
    """检查已配置依赖，失败时阻止流量进入当前实例。"""

    database_status: Literal["ready", "disabled", "not_ready"] = "disabled"
    if database is not None:
        try:
            await database.check()
            database_status = "ready"
        except Exception:
            database_status = "not_ready"

    ready = database_status != "not_ready"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ready" if ready else "not_ready",
        service=settings.service_name,
        version=__version__,
        environment=settings.environment,
        checks=(DependencyCheck(name="database", status=database_status),),
    )
