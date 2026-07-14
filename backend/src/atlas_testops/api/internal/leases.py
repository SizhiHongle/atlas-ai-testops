"""内部 Worker 账号租约 API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import (
    AuthSessionDispatcherDependency,
    CredentialBrokerServiceDependency,
    LeaseServiceDependency,
)
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.domain.identity import (
    AccountLeaseHandle,
    AcquireAccountLease,
    EnsureLoginSession,
    EnsureLoginSessionResult,
    HeartbeatAccountLease,
    IssueSecretGrant,
    ReapedLeaseBatch,
    ReleaseAccountLease,
    SecretGrant,
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
LeaseIdPath = Annotated[UUID, Path(alias="leaseId")]
ReaperLimitQuery = Annotated[int, Query(ge=1, le=500)]

router = APIRouter(
    responses={
        400: {"description": "租约请求或 Execution Deadline 无效", "model": ProblemDetails},
        401: {"description": "缺少可信 Worker 或 Platform 身份", "model": ProblemDetails},
        403: {"description": "运行角色或生产环境策略拒绝请求", "model": ProblemDetails},
        404: {"description": "资源不存在或不可见", "model": ProblemDetails},
        409: {"description": "账号池耗尽、Lease 过期或被 Fencing", "model": ProblemDetails},
        422: {"description": "角色、标签、能力或认证要求无法满足", "model": ProblemDetails},
    }
)


@router.post(
    "/account-leases",
    response_model=AccountLeaseHandle,
    status_code=status.HTTP_201_CREATED,
    summary="申请账号租约",
)
async def acquire_account_lease(
    command: AcquireAccountLease,
    response: Response,
    actor: ActorDependency,
    service: LeaseServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> AccountLeaseHandle:
    """事务选择独占 Slot，并返回不含账号主键和凭证的安全 Handle。"""

    result = await service.acquire(actor, command, idempotency_key=idempotency_key)
    response.status_code = result.status_code
    response.headers["Location"] = f"/internal/v1/account-leases/{result.value.lease_id}"
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return result.value


@router.get(
    "/account-leases/{leaseId}",
    response_model=AccountLeaseHandle,
    summary="读取账号租约",
)
async def get_account_lease(
    lease_id: LeaseIdPath,
    actor: ActorDependency,
    service: LeaseServiceDependency,
) -> AccountLeaseHandle:
    """只返回当前租约控制字段和不透明 Account Handle。"""

    return await service.get(actor, lease_id)


@router.post(
    "/account-leases/{leaseId}:heartbeat",
    response_model=AccountLeaseHandle,
    summary="续租账号租约",
)
async def heartbeat_account_lease(
    lease_id: LeaseIdPath,
    command: HeartbeatAccountLease,
    actor: ActorDependency,
    service: LeaseServiceDependency,
) -> AccountLeaseHandle:
    """只有最新 Fencing Token 可以在 Execution Deadline 内续租。"""

    return await service.heartbeat(actor, lease_id, command)


@router.post(
    "/account-leases/{leaseId}:release",
    response_model=AccountLeaseHandle,
    summary="释放账号租约",
)
async def release_account_lease(
    lease_id: LeaseIdPath,
    command: ReleaseAccountLease,
    response: Response,
    actor: ActorDependency,
    service: LeaseServiceDependency,
) -> AccountLeaseHandle:
    """使用结构化原因幂等释放，重复请求返回同一终态。"""

    result = await service.release(actor, lease_id, command)
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return result.value


@router.post(
    "/account-leases/{leaseId}:issue-secret-grant",
    response_model=SecretGrant,
    status_code=status.HTTP_201_CREATED,
    summary="签发一次性 Secret Grant",
)
async def issue_secret_grant(
    lease_id: LeaseIdPath,
    command: IssueSecretGrant,
    response: Response,
    actor: ActorDependency,
    service: CredentialBrokerServiceDependency,
) -> SecretGrant:
    """只返回短期 Grant Ref；密码与 SecretRef 永不进入 HTTP 响应。"""

    grant = await service.issue(actor, lease_id, command)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return grant


@router.post(
    "/account-leases/{leaseId}:ensure-session",
    response_model=EnsureLoginSessionResult,
    summary="建立或复用浏览器登录会话",
    responses={
        503: {
            "description": "独立 Auth Session Worker、Secret Provider 或 Vault 不可用",
            "model": ProblemDetails,
        }
    },
)
async def ensure_login_session(
    lease_id: LeaseIdPath,
    command: EnsureLoginSession,
    response: Response,
    actor: ActorDependency,
    dispatcher: AuthSessionDispatcherDependency,
) -> EnsureLoginSessionResult:
    """Only return an opaque browser context ref or bounded manual-action ticket."""

    result = await dispatcher.ensure(actor, lease_id, command)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result


@router.post(
    "/account-leases:reap-expired",
    response_model=ReapedLeaseBatch,
    summary="回收过期账号租约",
)
async def reap_expired_account_leases(
    actor: ActorDependency,
    service: LeaseServiceDependency,
    limit: ReaperLimitQuery = 100,
) -> ReapedLeaseBatch:
    """为当前 Tenant 执行单批次 SKIP LOCKED TTL 回收。"""

    return await service.reap_expired(actor, limit=limit)
