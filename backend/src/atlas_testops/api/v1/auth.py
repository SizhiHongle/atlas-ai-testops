"""Atlas 平台用户登录、Session 与 Development Bootstrap API。"""

from fastapi import APIRouter, Request, Response, status

from atlas_testops.api.dependencies import AuthServiceDependency, SettingsDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import DevelopmentModeDependency, require_trusted_origin
from atlas_testops.application.auth import LoginResult
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.request_context import get_request_id
from atlas_testops.domain.auth import (
    BootstrapPrincipal,
    BootstrapPrincipalCommand,
    LoginCommand,
    PlatformSessionView,
)

router = APIRouter(
    responses={
        400: {"description": "请求语义无效", "model": ProblemDetails},
        401: {"description": "登录失败或 Session 无效", "model": ProblemDetails},
        403: {"description": "当前环境或 Origin 不允许", "model": ProblemDetails},
        404: {"description": "Bootstrap 目标不存在", "model": ProblemDetails},
        409: {"description": "PlatformPrincipal 已存在", "model": ProblemDetails},
    }
)


def _set_session_cookie(
    response: Response,
    settings: SettingsDependency,
    result: LoginResult,
) -> None:
    """只通过 HttpOnly Cookie 传递 Opaque Session Token。"""

    response.set_cookie(
        key=settings.session_cookie_name,
        value=result.token,
        max_age=result.max_age_seconds,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _clear_session_cookie(response: Response, settings: SettingsDependency) -> None:
    """使用与签发时一致的 Cookie 属性清理客户端状态。"""

    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"


def _require_session_token(request: Request, settings: SettingsDependency) -> str:
    token = request.cookies.get(settings.session_cookie_name)
    if token is None:
        raise ApplicationError(
            error_code=ErrorCode.AUTHENTICATION_REQUIRED,
            title="需要登录",
            detail="请求没有 Atlas Session Cookie。",
            status_code=401,
        )
    return token


@router.post(
    "/auth/bootstrap",
    response_model=BootstrapPrincipal,
    status_code=status.HTTP_201_CREATED,
    summary="创建开发期平台管理员",
)
async def bootstrap_principal(
    command: BootstrapPrincipalCommand,
    development_mode: DevelopmentModeDependency,
    service: AuthServiceDependency,
) -> BootstrapPrincipal:
    """为已存在的 Tenant / Project 创建首个 ORG_ADMIN。"""

    del development_mode
    return await service.bootstrap_principal(command, request_id=get_request_id())


@router.post(
    "/auth/login",
    response_model=PlatformSessionView,
    summary="登录 Atlas 测试空间",
)
async def login(
    command: LoginCommand,
    request: Request,
    response: Response,
    settings: SettingsDependency,
    service: AuthServiceDependency,
) -> PlatformSessionView:
    """验证平台用户密码并签发 HttpOnly Opaque Session。"""

    require_trusted_origin(request, settings)
    result = await service.login(
        command,
        request_id=get_request_id(),
        user_agent=request.headers.get("User-Agent"),
    )
    _set_session_cookie(response, settings, result)
    return result.session


@router.get("/session", response_model=PlatformSessionView, summary="读取当前 Session")
async def current_session(
    request: Request,
    response: Response,
    settings: SettingsDependency,
    service: AuthServiceDependency,
) -> PlatformSessionView:
    """实时复核主体、成员关系和 Workspace Context。"""

    token = _require_session_token(request, settings)
    resolved = await service.resolve_session(token, request_id=get_request_id())
    response.headers["Cache-Control"] = "no-store"
    return resolved.session


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="退出当前 Session",
)
async def logout(
    request: Request,
    response: Response,
    settings: SettingsDependency,
    service: AuthServiceDependency,
) -> None:
    """服务端撤销 Session，并始终清除客户端 Cookie。"""

    require_trusted_origin(request, settings)
    token = request.cookies.get(settings.session_cookie_name)
    if token is not None:
        await service.logout(token, request_id=get_request_id())
    _clear_session_cookie(response, settings)
