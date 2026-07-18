"""顶层 API Router。"""

from fastapi import APIRouter

from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.v1.account_health import router as account_health_router
from atlas_testops.api.v1.auth import router as auth_router
from atlas_testops.api.v1.case_versions import router as case_versions_router
from atlas_testops.api.v1.cases import router as cases_router
from atlas_testops.api.v1.connectors import router as connectors_router
from atlas_testops.api.v1.debug_runs import router as debug_runs_router
from atlas_testops.api.v1.evidence import router as evidence_router
from atlas_testops.api.v1.fixture_assets import router as fixture_assets_router
from atlas_testops.api.v1.fixture_runs import router as fixture_runs_router
from atlas_testops.api.v1.health import router as health_router
from atlas_testops.api.v1.identity import router as identity_router
from atlas_testops.api.v1.insights import router as insights_router
from atlas_testops.api.v1.live import router as live_router
from atlas_testops.api.v1.platform import router as platform_router
from atlas_testops.api.v1.results import router as results_router
from atlas_testops.api.v1.task_plans import router as task_plans_router
from atlas_testops.api.v1.task_runs import router as task_runs_router
from atlas_testops.api.v1.unit_attempts import router as unit_attempts_router

api_router = APIRouter(
    responses={
        422: {"description": "请求不符合接口契约", "model": ProblemDetails},
        500: {"description": "服务内部错误", "model": ProblemDetails},
    }
)
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router, tags=["authentication"])
api_router.include_router(platform_router, tags=["platform"])
api_router.include_router(cases_router, tags=["test-cases"])
api_router.include_router(case_versions_router, tags=["case-versions"])
api_router.include_router(debug_runs_router, tags=["debug-runs"])
api_router.include_router(task_plans_router, tags=["task-plans"])
api_router.include_router(task_runs_router, tags=["task-runs"])
api_router.include_router(unit_attempts_router, tags=["unit-attempt-live-control"])
api_router.include_router(results_router, tags=["results"])
api_router.include_router(insights_router, tags=["insights"])
api_router.include_router(evidence_router, tags=["evidence"])
api_router.include_router(live_router, tags=["debug-live"])
api_router.include_router(connectors_router, tags=["connectors"])
api_router.include_router(fixture_assets_router, tags=["fixture-assets"])
api_router.include_router(fixture_runs_router, tags=["fixture-runs"])
api_router.include_router(account_health_router, tags=["account-health"])
api_router.include_router(identity_router, tags=["identity"])
