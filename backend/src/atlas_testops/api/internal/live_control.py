"""Machine-authenticated live-control gateway for the database-free Browser Worker."""

from dataclasses import dataclass
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from atlas_testops.api.dependencies import DatabaseDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.application.live_control import LiveControlService
from atlas_testops.core.contracts import utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.runtime import (
    AcknowledgeLiveControl,
    CompleteLiveActionGrant,
    ConsumeLiveActionGrant,
    HeartbeatLiveControl,
    InitializeLiveSession,
    LiveActionGrant,
    ReapedLiveControlBatch,
    RequestLiveActionGrant,
    UnitAttemptLiveSnapshot,
)
from atlas_testops.infrastructure.browser_auth import (
    AUTHORIZATION_HEADER,
    CONTENT_DIGEST_HEADER,
    NONCE_HEADER,
    PERMIT_HEADER,
    TENANT_HEADER,
    TIMESTAMP_HEADER,
    WORKER_HEADER,
    BrowserRuntimeAuthenticationError,
    BrowserRuntimePermitClaims,
    BrowserRuntimePermitSigner,
    BrowserRuntimeRequestSigner,
)

AttemptIdPath = Annotated[UUID, Path(alias="attemptId")]
GrantIdPath = Annotated[UUID, Path(alias="grantId")]
ReaperLimitQuery = Annotated[int, Query(ge=1, le=500)]


@dataclass(frozen=True, slots=True)
class LiveRuntimeActor:
    """Exact Worker/UnitAttempt authority after HMAC and Permit verification."""

    claims: BrowserRuntimePermitClaims

    @property
    def tenant_id(self) -> UUID:
        return self.claims.tenant_id

    @property
    def worker_identity(self) -> str:
        return self.claims.worker_identity


async def get_live_runtime_actor(
    request: Request,
    attempt_id: AttemptIdPath,
) -> LiveRuntimeActor:
    """Authenticate a signed request and bind the Permit to the exact Attempt."""

    request_signer = cast(
        BrowserRuntimeRequestSigner | None,
        request.app.state.browser_runtime_request_signer,
    )
    permit_signer = cast(
        BrowserRuntimePermitSigner | None,
        request.app.state.browser_runtime_permit_signer,
    )
    if request_signer is None or permit_signer is None:
        raise ApplicationError(
            error_code=ErrorCode.DEBUG_RUNTIME_UNAVAILABLE,
            title="Live Runtime 内部协议未配置",
            detail="当前 API 实例未启用 Browser Runtime 机器身份验证。",
            status_code=503,
        )
    body = await request.body()
    supplied_headers = {
        header: request.headers.get(header, "")
        for header in (
            AUTHORIZATION_HEADER,
            PERMIT_HEADER,
            TENANT_HEADER,
            WORKER_HEADER,
            TIMESTAMP_HEADER,
            NONCE_HEADER,
            CONTENT_DIGEST_HEADER,
        )
    }
    try:
        identity = request_signer.verify_headers(
            method=request.method,
            path=request.url.path,
            body=body,
            headers=supplied_headers,
            now=utc_now(),
        )
        claims = permit_signer.verify(identity.permit, now=utc_now())
    except BrowserRuntimeAuthenticationError as error:
        raise _authentication_failed() from error
    if (
        claims.tenant_id != identity.tenant_id
        or claims.worker_identity != identity.worker_identity
        or claims.run_id != attempt_id
    ):
        raise _authentication_failed()
    return LiveRuntimeActor(claims)


def get_live_control_service(
    database: DatabaseDependency,
) -> LiveControlService:
    return LiveControlService(database)


LiveRuntimeActorDependency = Annotated[
    LiveRuntimeActor,
    Depends(get_live_runtime_actor),
]
LiveControlRuntimeServiceDependency = Annotated[
    LiveControlService,
    Depends(get_live_control_service),
]

router = APIRouter(
    responses={
        401: {"description": "Worker request 或 execution permit 无效", "model": ProblemDetails},
        404: {"description": "LiveSession 或 ActionGrant 不存在", "model": ProblemDetails},
        409: {"description": "Epoch/Fence、Safe Point 或单次消费冲突", "model": ProblemDetails},
        503: {"description": "Browser Runtime 安全依赖未配置", "model": ProblemDetails},
    }
)


@router.put(
    "/unit-attempts/{attemptId}/live-session",
    response_model=UnitAttemptLiveSnapshot,
    summary="建立或精确重放 UnitAttempt LiveSession",
)
async def initialize_live_session(
    attempt_id: AttemptIdPath,
    command: InitializeLiveSession,
    response: Response,
    actor: LiveRuntimeActorDependency,
    service: LiveControlRuntimeServiceDependency,
) -> UnitAttemptLiveSnapshot:
    _prevent_storage(response)
    return await service.initialize(
        actor.tenant_id,
        attempt_id,
        command,
        worker_identity=actor.worker_identity,
    )


@router.post(
    "/unit-attempts/{attemptId}/live-control:heartbeat",
    response_model=UnitAttemptLiveSnapshot,
    summary="续租当前 Agent controller 且不改变 Epoch/Fence",
)
async def heartbeat_live_control(
    attempt_id: AttemptIdPath,
    command: HeartbeatLiveControl,
    response: Response,
    actor: LiveRuntimeActorDependency,
    service: LiveControlRuntimeServiceDependency,
) -> UnitAttemptLiveSnapshot:
    _prevent_storage(response)
    return await service.heartbeat(
        actor.tenant_id,
        attempt_id,
        command,
        worker_identity=actor.worker_identity,
    )


@router.post(
    "/unit-attempts/{attemptId}/live-control:acknowledge",
    response_model=UnitAttemptLiveSnapshot,
    summary="确认 Safe Point 或 reconcile 并原子交换 Epoch/Fence",
)
async def acknowledge_live_control(
    attempt_id: AttemptIdPath,
    command: AcknowledgeLiveControl,
    response: Response,
    actor: LiveRuntimeActorDependency,
    service: LiveControlRuntimeServiceDependency,
) -> UnitAttemptLiveSnapshot:
    _prevent_storage(response)
    return await service.acknowledge(
        actor.tenant_id,
        attempt_id,
        command,
        worker_identity=actor.worker_identity,
    )


@router.post(
    "/unit-attempts/{attemptId}/action-grants",
    response_model=LiveActionGrant,
    summary="签发持久化、单次、Epoch/Fence 绑定的 ActionGrant",
)
async def issue_live_action_grant(
    attempt_id: AttemptIdPath,
    command: RequestLiveActionGrant,
    response: Response,
    actor: LiveRuntimeActorDependency,
    service: LiveControlRuntimeServiceDependency,
) -> LiveActionGrant:
    _prevent_storage(response)
    return await service.issue_action_grant(
        actor.tenant_id,
        attempt_id,
        command,
        worker_identity=actor.worker_identity,
    )


@router.get(
    "/unit-attempts/{attemptId}/action-grants/{grantId}",
    response_model=LiveActionGrant,
    summary="恢复 ActionGrant 的单次消费与回执状态",
)
async def get_live_action_grant(
    attempt_id: AttemptIdPath,
    grant_id: GrantIdPath,
    response: Response,
    actor: LiveRuntimeActorDependency,
    service: LiveControlRuntimeServiceDependency,
) -> LiveActionGrant:
    _prevent_storage(response)
    return await service.get_action_grant(
        actor.tenant_id,
        attempt_id,
        grant_id,
    )


@router.post(
    "/unit-attempts/{attemptId}/action-grants/{grantId}:consume",
    response_model=LiveActionGrant,
    summary="在 Playwright 副作用前原子消费 ActionGrant",
)
async def consume_live_action_grant(
    attempt_id: AttemptIdPath,
    grant_id: GrantIdPath,
    command: ConsumeLiveActionGrant,
    response: Response,
    actor: LiveRuntimeActorDependency,
    service: LiveControlRuntimeServiceDependency,
) -> LiveActionGrant:
    _prevent_storage(response)
    return await service.consume_action_grant(
        actor.tenant_id,
        attempt_id,
        grant_id,
        command,
    )


@router.post(
    "/unit-attempts/{attemptId}/action-grants/{grantId}:complete",
    response_model=LiveActionGrant,
    summary="写入 ExecutionReceipt 并推进 Browser Revision",
)
async def complete_live_action_grant(
    attempt_id: AttemptIdPath,
    grant_id: GrantIdPath,
    command: CompleteLiveActionGrant,
    response: Response,
    actor: LiveRuntimeActorDependency,
    service: LiveControlRuntimeServiceDependency,
) -> LiveActionGrant:
    _prevent_storage(response)
    return await service.complete_action_grant(
        actor.tenant_id,
        attempt_id,
        grant_id,
        command,
    )


@router.post(
    "/live-control:reap-expired",
    response_model=ReapedLiveControlBatch,
    summary="回收当前 Tenant 的过期 Live controller",
)
async def reap_expired_live_control(
    actor: ActorDependency,
    service: LiveControlRuntimeServiceDependency,
    limit: ReaperLimitQuery = 100,
) -> ReapedLiveControlBatch:
    return await service.reap_expired(actor, limit=limit)


def _prevent_storage(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _authentication_failed() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.AUTHENTICATION_FAILED,
        title="Live Runtime 机器身份验证失败",
        detail="Worker request signature 或 UnitAttempt execution permit 无效。",
        status_code=401,
        headers={"WWW-Authenticate": "Atlas-HMAC"},
    )


__all__ = ["router"]
