"""Machine-authenticated control-plane API for the database-free Browser Worker."""

from dataclasses import dataclass
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response

from atlas_testops.api.dependencies import DatabaseDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.application.debug_runtime import DebugRuntimeService
from atlas_testops.application.ports.browser_runtime import BrowserContextEnvelopeCodec
from atlas_testops.core.contracts import utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import DebugRun
from atlas_testops.domain.runtime import (
    AppendBrowserRuntimeReport,
    BrowserEvidenceFinalization,
    BrowserExecutionBundle,
    BrowserFinalizeCommand,
    BrowserRuntimeReport,
    BrowserRuntimeTransition,
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

RunIdPath = Annotated[UUID, Path(alias="runId")]


@dataclass(frozen=True, slots=True)
class BrowserRuntimeActor:
    """Exact Worker/run authority after request and permit verification."""

    claims: BrowserRuntimePermitClaims

    @property
    def tenant_id(self) -> UUID:
        return self.claims.tenant_id

    @property
    def worker_identity(self) -> str:
        return self.claims.worker_identity


async def get_browser_runtime_actor(
    request: Request,
    run_id: RunIdPath,
) -> BrowserRuntimeActor:
    """Authenticate a signed request and bind its short-lived permit to the path."""

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
            title="Browser Runtime 内部协议未配置",
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
    now = utc_now()
    try:
        identity = request_signer.verify_headers(
            method=request.method,
            path=request.url.path,
            body=body,
            headers=supplied_headers,
            now=now,
        )
        claims = permit_signer.verify(identity.permit, now=now)
    except BrowserRuntimeAuthenticationError as error:
        raise _authentication_failed() from error
    if (
        claims.tenant_id != identity.tenant_id
        or claims.worker_identity != identity.worker_identity
        or claims.run_id != run_id
    ):
        raise _authentication_failed()
    return BrowserRuntimeActor(claims=claims)


def get_debug_runtime_service(
    request: Request,
    database: DatabaseDependency,
) -> DebugRuntimeService:
    """Create a request-scoped trusted writer with the configured envelope codec."""

    codec = cast(
        BrowserContextEnvelopeCodec | None,
        request.app.state.browser_context_envelope_codec,
    )
    if codec is None:
        raise ApplicationError(
            error_code=ErrorCode.DEBUG_RUNTIME_UNAVAILABLE,
            title="Browser Context Envelope 未配置",
            detail="当前 API 实例不能安全投递 BrowserContext restore metadata。",
            status_code=503,
        )
    return DebugRuntimeService(database, browser_context_envelope_codec=codec)


BrowserRuntimeActorDependency = Annotated[
    BrowserRuntimeActor,
    Depends(get_browser_runtime_actor),
]
DebugRuntimeServiceDependency = Annotated[
    DebugRuntimeService,
    Depends(get_debug_runtime_service),
]

router = APIRouter(
    responses={
        401: {"description": "Worker request 或 execution permit 无效", "model": ProblemDetails},
        409: {"description": "Runtime lifecycle 或 report chain 冲突", "model": ProblemDetails},
        503: {"description": "Browser Runtime 安全依赖未配置", "model": ProblemDetails},
    }
)


@router.get(
    "/debug-runs/{runId}/browser-execution",
    response_model=BrowserExecutionBundle,
    summary="读取已绑定 DebugRun 的受信 Browser 执行包",
)
async def get_browser_execution_bundle(
    run_id: RunIdPath,
    response: Response,
    actor: BrowserRuntimeActorDependency,
    service: DebugRuntimeServiceDependency,
) -> BrowserExecutionBundle:
    _prevent_storage(response)
    return await service.get_browser_execution_bundle(
        actor.tenant_id,
        run_id,
        worker_identity=actor.worker_identity,
    )


@router.post(
    "/debug-runs/{runId}/browser-execution:ready",
    response_model=DebugRun,
    summary="确认 Browser Worker 已取得完整执行包",
)
async def mark_browser_execution_ready(
    run_id: RunIdPath,
    command: BrowserRuntimeTransition,
    response: Response,
    actor: BrowserRuntimeActorDependency,
    service: DebugRuntimeServiceDependency,
) -> DebugRun:
    _prevent_storage(response)
    return await service.mark_ready(
        actor.tenant_id,
        run_id,
        execution_contract_id=command.execution_contract_id,
        execution_contract_digest=command.execution_contract_digest,
        worker_identity=actor.worker_identity,
    )


@router.post(
    "/debug-runs/{runId}/browser-execution:start",
    response_model=DebugRun,
    summary="在任何 Browser 副作用前推进 DebugRun 到 RUNNING",
)
async def start_browser_execution(
    run_id: RunIdPath,
    command: BrowserRuntimeTransition,
    response: Response,
    actor: BrowserRuntimeActorDependency,
    service: DebugRuntimeServiceDependency,
) -> DebugRun:
    _prevent_storage(response)
    return await service.start_execution(
        actor.tenant_id,
        run_id,
        execution_contract_id=command.execution_contract_id,
        execution_contract_digest=command.execution_contract_digest,
        worker_identity=actor.worker_identity,
    )


@router.post(
    "/debug-runs/{runId}/browser-reports",
    response_model=BrowserRuntimeReport,
    summary="单调追加一个 Browser Runtime 事实",
)
async def append_browser_runtime_report(
    run_id: RunIdPath,
    command: AppendBrowserRuntimeReport,
    response: Response,
    actor: BrowserRuntimeActorDependency,
    service: DebugRuntimeServiceDependency,
) -> BrowserRuntimeReport:
    _prevent_storage(response)
    return await service.append_browser_report(
        actor.tenant_id,
        run_id,
        worker_identity=actor.worker_identity,
        report=command,
    )


@router.post(
    "/debug-runs/{runId}/browser-execution:finalize-evidence",
    response_model=BrowserEvidenceFinalization,
    summary="用报告链、Oracle 输入和 Artifact receipt 完成证据封存",
)
async def finalize_browser_evidence(
    run_id: RunIdPath,
    wrapper: BrowserFinalizeCommand,
    response: Response,
    actor: BrowserRuntimeActorDependency,
    service: DebugRuntimeServiceDependency,
) -> BrowserEvidenceFinalization:
    _prevent_storage(response)
    run, evidence_manifest = await service.finalize_evidence(
        actor.tenant_id,
        run_id,
        wrapper.command,
        worker_identity=actor.worker_identity,
    )
    return BrowserEvidenceFinalization(
        run=run,
        evidence_manifest=evidence_manifest,
    )


def _prevent_storage(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _authentication_failed() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.AUTHENTICATION_FAILED,
        title="Browser Runtime 机器身份验证失败",
        detail="Worker request signature 或 execution permit 无效。",
        status_code=401,
        headers={"WWW-Authenticate": "Atlas-HMAC"},
    )
