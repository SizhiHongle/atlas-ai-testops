"""Finalized evidence manifests and short-lived verified object reads."""

from re import fullmatch
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import EvidenceServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.runtime import (
    EvidenceManifest,
    EvidenceReadGrant,
    EvidenceReadPurpose,
    IssueEvidenceReadGrant,
)
from atlas_testops.domain.runtime.evidence import EVIDENCE_READ_TOKEN_PATTERN

RunIdPath = Annotated[UUID, Path(alias="runId")]
ArtifactIdPath = Annotated[UUID, Path(alias="artifactId")]
EvidenceAuthorizationHeader = Annotated[
    str | None,
    Header(alias="Authorization"),
]
ReadPurposeQuery = Annotated[
    EvidenceReadPurpose,
    Query(alias="purpose"),
]

router = APIRouter(
    responses={
        401: {"description": "身份或 Evidence Read Grant 无效", "model": ProblemDetails},
        403: {"description": "当前身份不能签发读取授权", "model": ProblemDetails},
        404: {"description": "DebugRun 或 Evidence Artifact 不存在", "model": ProblemDetails},
        409: {"description": "Evidence 未封存或完整性失败", "model": ProblemDetails},
        503: {"description": "Evidence Object Store 未配置或不可用", "model": ProblemDetails},
    }
)


@router.get(
    "/debug-runs/{runId}/evidence",
    response_model=EvidenceManifest,
    summary="读取 DebugRun 的不可变 EvidenceManifest",
)
async def get_debug_run_evidence(
    run_id: RunIdPath,
    response: Response,
    actor: ActorDependency,
    service: EvidenceServiceDependency,
) -> EvidenceManifest:
    response.headers.update(_private_no_store_headers())
    return await service.get_manifest(actor, run_id)


@router.post(
    "/debug-runs/{runId}/evidence/{artifactId}/read-tokens",
    response_model=EvidenceReadGrant,
    status_code=status.HTTP_201_CREATED,
    summary="签发用户与 Session 绑定的短期 Evidence Read Grant",
)
async def issue_evidence_read_grant(
    run_id: RunIdPath,
    artifact_id: ArtifactIdPath,
    command: IssueEvidenceReadGrant,
    response: Response,
    actor: ActorDependency,
    service: EvidenceServiceDependency,
) -> EvidenceReadGrant:
    response.headers.update(_private_no_store_headers())
    return await service.issue_read_grant(actor, run_id, artifact_id, command)


@router.get(
    "/evidence/artifacts/{artifactId}/content",
    response_model=None,
    response_class=Response,
    responses={
        200: {
            "description": "完整校验后的 Evidence 字节",
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/webp": {"schema": {"type": "string", "format": "binary"}},
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
    summary="使用短期 Read Grant 读取并重新校验 Evidence 字节",
)
async def read_evidence_content(
    artifact_id: ArtifactIdPath,
    actor: ActorDependency,
    service: EvidenceServiceDependency,
    authorization: EvidenceAuthorizationHeader = None,
    purpose: ReadPurposeQuery = EvidenceReadPurpose.INLINE,
) -> Response:
    read_token = _parse_read_authorization(authorization)
    content = await service.read_content(
        actor,
        artifact_id,
        read_token=read_token,
        purpose=purpose,
    )
    headers = _private_no_store_headers()
    headers.update(
        {
            "Content-Disposition": (
                f'{content.content_disposition}; filename="{content.filename}"'
            ),
            "Content-Length": str(len(content.payload)),
            "Content-Security-Policy": "sandbox; default-src 'none'",
        }
    )
    return Response(
        content=content.payload,
        media_type=content.mime_type,
        headers=headers,
    )


def _parse_read_authorization(value: str | None) -> str:
    try:
        scheme, token = (value or "").split(" ", 1)
    except ValueError:
        raise _invalid_grant() from None
    if (
        scheme != "Atlas-Evidence"
        or token != token.strip()
        or fullmatch(EVIDENCE_READ_TOKEN_PATTERN, token) is None
    ):
        raise _invalid_grant()
    return token


def _invalid_grant() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.AUTHENTICATION_FAILED,
        title="Evidence Read Grant 无效",
        detail="请提供有效的 Atlas-Evidence Authorization Header。",
        status_code=401,
        headers={"WWW-Authenticate": "Atlas-Evidence"},
    )


def _private_no_store_headers() -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store, max-age=0",
        "Pragma": "no-cache",
        "Vary": "Cookie, Authorization, Origin",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "same-origin",
    }
