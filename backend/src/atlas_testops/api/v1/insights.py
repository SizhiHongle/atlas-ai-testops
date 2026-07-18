"""Public comparable quality brief and pinned InsightSnapshot APIs."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import InsightServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.domain.insight import (
    InsightBrief,
    InsightSnapshot,
    RequestInsightSnapshot,
    insight_digest,
)

ProjectIdPath = Annotated[UUID, Path(alias="projectId")]
SnapshotIdPath = Annotated[UUID, Path(alias="snapshotId")]
WindowDaysQuery = Annotated[int, Query(alias="windowDays", ge=7, le=90)]
AsOfQuery = Annotated[datetime | None, Query(alias="asOf")]
IfNoneMatchHeader = Annotated[
    str | None,
    Header(alias="If-None-Match", max_length=256),
]
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]

router = APIRouter(
    responses={
        400: {"description": "Insight 窗口或 asOf 无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        404: {"description": "Project 或 InsightSnapshot 不存在", "model": ProblemDetails},
    }
)


@router.get(
    "/projects/{projectId}/insights/brief",
    response_model=InsightBrief,
    summary="预览可比 current/baseline 质量简报",
    responses={304: {"description": "ETag 未变化"}},
)
async def preview_insight_brief(
    project_id: ProjectIdPath,
    response: Response,
    actor: ActorDependency,
    service: InsightServiceDependency,
    window_days: WindowDaysQuery = 30,
    as_of: AsOfQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> InsightBrief | Response:
    brief = await service.preview(
        actor,
        project_id,
        window_days=window_days,
        as_of=as_of,
    )
    etag = _brief_etag(brief)
    headers = _insight_headers(
        etag=etag,
        brief=brief,
        cache_control="private, no-cache",
    )
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return brief


@router.post(
    "/projects/{projectId}/insight-snapshots",
    response_model=InsightSnapshot,
    status_code=status.HTTP_201_CREATED,
    summary="固定 exact DatasetCut 的不可变 InsightSnapshot",
    responses={
        403: {"description": "当前身份不能固定洞察", "model": ProblemDetails},
        409: {"description": "幂等身份与既有 Snapshot 冲突", "model": ProblemDetails},
    },
)
async def pin_insight_snapshot(
    project_id: ProjectIdPath,
    command: RequestInsightSnapshot,
    response: Response,
    actor: ActorDependency,
    service: InsightServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> InsightSnapshot:
    result = await service.pin_snapshot(
        actor,
        project_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers.update(
        _insight_headers(
            etag=f'"{result.value.snapshot_hash}"',
            brief=result.value,
            cache_control="private, no-cache",
        )
    )
    response.headers["Location"] = f"/v1/insight-snapshots/{result.value.id}"
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return result.value


@router.get(
    "/insight-snapshots/{snapshotId}",
    response_model=InsightSnapshot,
    summary="读取一个 exact pinned InsightSnapshot",
    responses={304: {"description": "ETag 未变化"}},
)
async def get_insight_snapshot(
    snapshot_id: SnapshotIdPath,
    response: Response,
    actor: ActorDependency,
    service: InsightServiceDependency,
    if_none_match: IfNoneMatchHeader = None,
) -> InsightSnapshot | Response:
    snapshot = await service.get_snapshot(actor, snapshot_id)
    etag = f'"{snapshot.snapshot_hash}"'
    headers = _insight_headers(
        etag=etag,
        brief=snapshot,
        cache_control="private, max-age=31536000, immutable",
    )
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return snapshot


def _brief_etag(brief: InsightBrief) -> str:
    document = brief.model_dump(mode="json", by_alias=True)
    return f'"{insight_digest(document)}"'


def _insight_headers(
    *,
    etag: str,
    brief: InsightBrief,
    cache_control: str,
) -> dict[str, str]:
    headers = {
        "ETag": etag,
        "Cache-Control": cache_control,
        "X-Insight-As-Of": brief.dataset_cut.as_of.isoformat(),
        "X-Dataset-Cut-Digest": brief.dataset_cut.source_set_digest,
        "X-Insight-Query-Hash": brief.dataset_cut.query_hash,
    }
    if brief.dataset_cut.projection_watermark is not None:
        headers["X-Projection-Watermark"] = (
            brief.dataset_cut.projection_watermark.isoformat()
        )
    return headers


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    return any(candidate.strip() in {"*", etag} for candidate in if_none_match.split(","))


__all__ = ["router"]
