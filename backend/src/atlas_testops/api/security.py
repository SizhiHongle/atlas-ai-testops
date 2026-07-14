"""开发期 Actor Context 与认证边界。"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from atlas_testops.api.dependencies import (
    OptionalDatabaseDependency,
    PasswordServiceDependency,
    SettingsDependency,
)
from atlas_testops.application.access import ActorContext
from atlas_testops.application.auth import AuthService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.request_context import get_request_id

TENANT_HEADER = "X-Atlas-Tenant-ID"
ACTOR_HEADER = "X-Atlas-Actor-ID"
DEVELOPMENT_ENVIRONMENTS = frozenset({"local", "test", "development"})

TenantHeader = Annotated[str | None, Header(alias=TENANT_HEADER)]
ActorHeader = Annotated[str | None, Header(alias=ACTOR_HEADER)]


def require_development_mode(settings: SettingsDependency) -> Settings:
    """只在非生产环境开放 Bootstrap Header，避免伪身份进入正式环境。"""

    if settings.environment not in DEVELOPMENT_ENVIRONMENTS:
        raise ApplicationError(
            error_code=ErrorCode.FORBIDDEN,
            title="Development Bootstrap 已禁用",
            detail="该环境必须使用正式 Session 认证。",
            status_code=403,
        )
    return settings


def parse_optional_actor_header(value: str | None) -> UUID | None:
    """解析可选 Actor Header，并保持错误协议稳定。"""

    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise ApplicationError(
            error_code=ErrorCode.INVALID_REQUEST,
            title="开发期身份格式无效",
            detail=f"{ACTOR_HEADER} 必须是 UUID。",
            status_code=400,
        ) from None


def require_trusted_origin(request: Request, settings: Settings) -> None:
    """Cookie 认证的写请求只接受显式允许的前端 Origin。"""

    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("Origin")
    if origin is None:
        if settings.environment in DEVELOPMENT_ENVIRONMENTS:
            return
        raise ApplicationError(
            error_code=ErrorCode.FORBIDDEN,
            title="请求来源不可信",
            detail="正式环境的写请求必须携带受信任 Origin。",
            status_code=403,
        )
    allowed = {item.rstrip("/") for item in settings.cors_origins}
    if "*" not in allowed and origin.rstrip("/") not in allowed:
        raise ApplicationError(
            error_code=ErrorCode.FORBIDDEN,
            title="请求来源不可信",
            detail="当前 Origin 不在 Atlas 允许列表中。",
            status_code=403,
        )


def get_development_actor(
    settings: SettingsDependency,
    tenant_header: TenantHeader = None,
    actor_header: ActorHeader = None,
) -> ActorContext:
    """从受环境保护的 Header 构造可信 Actor Context。"""

    require_development_mode(settings)
    if tenant_header is None:
        raise ApplicationError(
            error_code=ErrorCode.AUTHENTICATION_REQUIRED,
            title="缺少开发期身份",
            detail=f"请提供 {TENANT_HEADER}；正式 Session 将在 P1-02 接入。",
            status_code=401,
        )

    try:
        tenant_id = UUID(tenant_header)
    except ValueError:
        raise ApplicationError(
            error_code=ErrorCode.INVALID_REQUEST,
            title="开发期身份格式无效",
            detail=f"{TENANT_HEADER} 必须是 UUID。",
            status_code=400,
        ) from None

    return ActorContext(
        tenant_id=tenant_id,
        actor_id=parse_optional_actor_header(actor_header),
        request_id=get_request_id(),
        development_override=True,
    )


DevelopmentModeDependency = Annotated[Settings, Depends(require_development_mode)]


async def get_actor(
    request: Request,
    settings: SettingsDependency,
    database: OptionalDatabaseDependency,
    password_service: PasswordServiceDependency,
    tenant_header: TenantHeader = None,
    actor_header: ActorHeader = None,
) -> ActorContext:
    """优先解析正式 Session，仅在开发环境回退 Bootstrap Header。"""

    token = request.cookies.get(settings.session_cookie_name)
    if token is not None:
        if database is None:
            raise ApplicationError(
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                title="认证存储不可用",
                detail="当前进程没有配置 Session 数据库。",
                status_code=503,
            )
        require_trusted_origin(request, settings)
        return (
            await AuthService(database, settings, password_service).resolve_session(
                token,
                request_id=get_request_id(),
            )
        ).actor
    return get_development_actor(settings, tenant_header, actor_header)


ActorDependency = Annotated[ActorContext, Depends(get_actor)]
