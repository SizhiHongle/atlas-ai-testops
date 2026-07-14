"""顶层 API Router。"""

from fastapi import APIRouter

from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.v1.account_health import router as account_health_router
from atlas_testops.api.v1.auth import router as auth_router
from atlas_testops.api.v1.connectors import router as connectors_router
from atlas_testops.api.v1.fixture_assets import router as fixture_assets_router
from atlas_testops.api.v1.fixture_runs import router as fixture_runs_router
from atlas_testops.api.v1.health import router as health_router
from atlas_testops.api.v1.identity import router as identity_router
from atlas_testops.api.v1.platform import router as platform_router

api_router = APIRouter(
    responses={
        422: {"description": "请求不符合接口契约", "model": ProblemDetails},
        500: {"description": "服务内部错误", "model": ProblemDetails},
    }
)
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router, tags=["authentication"])
api_router.include_router(platform_router, tags=["platform"])
api_router.include_router(connectors_router, tags=["connectors"])
api_router.include_router(fixture_assets_router, tags=["fixture-assets"])
api_router.include_router(fixture_runs_router, tags=["fixture-runs"])
api_router.include_router(account_health_router, tags=["account-health"])
api_router.include_router(identity_router, tags=["identity"])
