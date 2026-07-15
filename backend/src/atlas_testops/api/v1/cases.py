"""TestCase catalog and WorkflowDraft authoring API."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import CaseServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.domain.case import (
    CreateTestCase,
    LayoutPatch,
    TestCase,
    TestCasePage,
    WorkflowDraftSnapshot,
    WorkflowPatch,
    WorkflowPatchPreview,
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
ProjectIdPath = Annotated[UUID, Path(alias="projectId")]
CaseIdPath = Annotated[UUID, Path(alias="caseId")]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]

router = APIRouter(
    responses={
        400: {"description": "请求或幂等协议无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "当前 PlatformRole 无权执行", "model": ProblemDetails},
        404: {"description": "TestCase 或 WorkflowDraft 不存在", "model": ProblemDetails},
        409: {"description": "唯一键、状态或幂等冲突", "model": ProblemDetails},
        412: {"description": "WorkflowDraft Revision 冲突", "model": ProblemDetails},
        422: {"description": "Patch 或 WorkflowGraph 结构无效", "model": ProblemDetails},
    }
)


def _set_draft_headers(
    response: Response,
    draft: WorkflowDraftSnapshot,
    *,
    primary: Literal["semantic", "layout"],
) -> None:
    semantic_etag = format_revision_etag(draft.semantic_revision)
    layout_etag = format_revision_etag(draft.layout_revision)
    response.headers["ETag"] = semantic_etag if primary == "semantic" else layout_etag
    response.headers["X-Semantic-ETag"] = semantic_etag
    response.headers["X-Layout-ETag"] = layout_etag


@router.post(
    "/projects/{projectId}/test-cases",
    response_model=TestCase,
    status_code=status.HTTP_201_CREATED,
    summary="创建 TestCase 与初始 WorkflowDraft",
)
async def create_test_case(
    project_id: ProjectIdPath,
    command: CreateTestCase,
    response: Response,
    actor: ActorDependency,
    service: CaseServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> TestCase:
    result = await service.create_case(
        actor,
        project_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Location"] = f"/v1/test-cases/{result.value.id}"
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    return result.value


@router.get(
    "/projects/{projectId}/test-cases",
    response_model=TestCasePage,
    summary="列出 TestCase Catalog",
)
async def list_test_cases(
    project_id: ProjectIdPath,
    actor: ActorDependency,
    service: CaseServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> TestCasePage:
    return await service.list_cases(
        actor,
        project_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/test-cases/{caseId}",
    response_model=TestCase,
    summary="读取 TestCase",
)
async def get_test_case(
    case_id: CaseIdPath,
    response: Response,
    actor: ActorDependency,
    service: CaseServiceDependency,
) -> TestCase:
    case = await service.get_case(actor, case_id)
    response.headers["ETag"] = format_revision_etag(case.revision)
    return case


@router.get(
    "/test-cases/{caseId}/workflow-draft",
    response_model=WorkflowDraftSnapshot,
    summary="读取当前 WorkflowDraft",
)
async def get_workflow_draft(
    case_id: CaseIdPath,
    response: Response,
    actor: ActorDependency,
    service: CaseServiceDependency,
) -> WorkflowDraftSnapshot:
    draft = await service.get_draft(actor, case_id)
    _set_draft_headers(response, draft, primary="semantic")
    return draft


@router.post(
    "/test-cases/{caseId}/workflow-draft/patches:validate",
    response_model=WorkflowPatchPreview,
    summary="预检 WorkflowPatch",
)
async def validate_workflow_patch(
    case_id: CaseIdPath,
    patch: WorkflowPatch,
    response: Response,
    actor: ActorDependency,
    service: CaseServiceDependency,
) -> WorkflowPatchPreview:
    preview = await service.preview_patch(actor, case_id, patch)
    response.headers["ETag"] = format_revision_etag(patch.base_semantic_revision)
    return preview


@router.post(
    "/test-cases/{caseId}/workflow-draft/patches:apply",
    response_model=WorkflowDraftSnapshot,
    summary="原子应用 WorkflowPatch",
)
async def apply_workflow_patch(
    case_id: CaseIdPath,
    patch: WorkflowPatch,
    response: Response,
    actor: ActorDependency,
    service: CaseServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> WorkflowDraftSnapshot:
    result = await service.apply_patch(
        actor,
        case_id,
        patch,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    _set_draft_headers(response, result.value, primary="semantic")
    return result.value


@router.patch(
    "/test-cases/{caseId}/workflow-draft/layout",
    response_model=WorkflowDraftSnapshot,
    summary="更新 WorkflowDraft 布局",
)
async def update_workflow_layout(
    case_id: CaseIdPath,
    patch: LayoutPatch,
    response: Response,
    actor: ActorDependency,
    service: CaseServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> WorkflowDraftSnapshot:
    result = await service.update_layout(
        actor,
        case_id,
        patch,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    _set_draft_headers(response, result.value, primary="layout")
    return result.value
