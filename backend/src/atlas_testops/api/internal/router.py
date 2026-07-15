"""内部 Worker Router。"""

from fastapi import APIRouter

from atlas_testops.api.internal.browser_runtime import router as browser_runtime_router
from atlas_testops.api.internal.fixtures import router as fixture_router
from atlas_testops.api.internal.leases import router as lease_router
from atlas_testops.api.problem_details import ProblemDetails

internal_api_router = APIRouter(
    responses={
        422: {"description": "请求不符合内部接口契约", "model": ProblemDetails},
        500: {"description": "服务内部错误", "model": ProblemDetails},
    }
)
internal_api_router.include_router(lease_router, tags=["identity-runtime"])
internal_api_router.include_router(fixture_router, tags=["fixture-runtime"])
internal_api_router.include_router(browser_runtime_router, tags=["browser-runtime"])
