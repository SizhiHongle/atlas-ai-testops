"""WorkflowDraft DebugRun control-plane API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import DebugRunServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.domain.case import (
    DebugRun,
    DebugRunEventPage,
    DebugRunPage,
    RequestDebugRunCancel,
    StartDebugRun,
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
CaseIdPath = Annotated[UUID, Path(alias="caseId")]
RunIdPath = Annotated[UUID, Path(alias="runId")]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
AfterSeqQuery = Annotated[int, Query(alias="afterSeq", ge=0)]

router = APIRouter(
    responses={
        400: {"description": "DebugRun 请求或幂等协议无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "角色或环境策略拒绝执行", "model": ProblemDetails},
        404: {
            "description": "TestCase、Draft、Environment 或 DebugRun 不存在",
            "model": ProblemDetails,
        },
        409: {"description": "DebugRun 状态冲突", "model": ProblemDetails},
        412: {"description": "Draft 或 DebugRun Revision 冲突", "model": ProblemDetails},
        422: {"description": "WorkflowDraft 编译门禁未通过", "model": ProblemDetails},
        503: {"description": "受信任的 Browser Runtime 未配置或不可用", "model": ProblemDetails},
    }
)


@router.post(
    "/test-cases/{caseId}/workflow-draft/debug-runs",
    response_model=DebugRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="冻结 WorkflowDraft 并启动 DebugRun",
)
async def start_debug_run(
    case_id: CaseIdPath,
    command: StartDebugRun,
    response: Response,
    actor: ActorDependency,
    service: DebugRunServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> DebugRun:
    result = await service.start(
        actor,
        case_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Location"] = f"/v1/debug-runs/{result.value.id}"
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    return result.value


@router.get(
    "/test-cases/{caseId}/debug-runs",
    response_model=DebugRunPage,
    summary="列出 TestCase 的 DebugRun 快照",
)
async def list_debug_runs(
    case_id: CaseIdPath,
    actor: ActorDependency,
    service: DebugRunServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> DebugRunPage:
    return await service.list_for_case(
        actor,
        case_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/debug-runs/{runId}",
    response_model=DebugRun,
    summary="读取冻结的 DebugRun 快照",
)
async def get_debug_run(
    run_id: RunIdPath,
    response: Response,
    actor: ActorDependency,
    service: DebugRunServiceDependency,
) -> DebugRun:
    run = await service.get(actor, run_id)
    response.headers["ETag"] = format_revision_etag(run.revision)
    return run


@router.get(
    "/debug-runs/{runId}/events",
    response_model=DebugRunEventPage,
    summary="增量读取 DebugRun 单调事件",
)
async def list_debug_run_events(
    run_id: RunIdPath,
    actor: ActorDependency,
    service: DebugRunServiceDependency,
    after_seq: AfterSeqQuery = 0,
    limit: LimitQuery = 100,
) -> DebugRunEventPage:
    return await service.list_events(
        actor,
        run_id,
        after_seq=after_seq,
        limit=limit,
    )


@router.post(
    "/debug-runs/{runId}:cancel",
    response_model=DebugRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="请求取消 DebugRun",
)
async def cancel_debug_run(
    run_id: RunIdPath,
    command: RequestDebugRunCancel,
    response: Response,
    actor: ActorDependency,
    service: DebugRunServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> DebugRun:
    result = await service.request_cancel(
        actor,
        run_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    return result.value
