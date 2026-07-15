"""Reviewed CaseVersion publication and exact-version read API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import CaseVersionServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.domain.case import CaseVersion, CaseVersionPage, PublishCaseVersion

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
CaseIdPath = Annotated[UUID, Path(alias="caseId")]
VersionIdPath = Annotated[UUID, Path(alias="versionId")]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]

router = APIRouter(
    responses={
        400: {"description": "发布请求或幂等协议无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "Reviewer 权限或职责分离校验失败", "model": ProblemDetails},
        404: {
            "description": "TestCase、Draft、DebugRun 或 CaseVersion 不存在",
            "model": ProblemDetails,
        },
        409: {"description": "发布证据、精确绑定、版本号或状态冲突", "model": ProblemDetails},
        412: {"description": "WorkflowDraft Revision 冲突", "model": ProblemDetails},
        422: {"description": "当前 WorkflowDraft 编译门禁未通过", "model": ProblemDetails},
    }
)


@router.post(
    "/test-cases/{caseId}:publish",
    response_model=CaseVersion,
    status_code=status.HTTP_201_CREATED,
    summary="评审并发布不可变 CaseVersion",
)
async def publish_case_version(
    case_id: CaseIdPath,
    command: PublishCaseVersion,
    response: Response,
    actor: ActorDependency,
    service: CaseVersionServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> CaseVersion:
    result = await service.publish(
        actor,
        case_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Location"] = f"/v1/case-versions/{result.value.id}"
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    return result.value


@router.get(
    "/test-cases/{caseId}/versions",
    response_model=CaseVersionPage,
    summary="列出 TestCase 的不可变版本历史",
)
async def list_case_versions(
    case_id: CaseIdPath,
    actor: ActorDependency,
    service: CaseVersionServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> CaseVersionPage:
    return await service.list_for_case(
        actor,
        case_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/case-versions/{versionId}",
    response_model=CaseVersion,
    summary="按 ID 读取精确 CaseVersion",
)
async def get_case_version(
    version_id: VersionIdPath,
    response: Response,
    actor: ActorDependency,
    service: CaseVersionServiceDependency,
) -> CaseVersion:
    version = await service.get(actor, version_id)
    response.headers["ETag"] = format_revision_etag(version.revision)
    return version
