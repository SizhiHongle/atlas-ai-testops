"""Public Result Center reads, reviews, and Task Gate evaluation."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import (
    ResultClassificationServiceDependency,
    ResultGateServiceDependency,
    ResultQueryServiceDependency,
)
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.domain.result import (
    FailureClassificationRevision,
    FailureClusterPage,
    RequestFailureClassificationRevision,
    RequestTaskGateEvaluation,
    TaskGateDecision,
    TaskResultView,
    UnitResolutionRevision,
)

RunIdPath = Annotated[UUID, Path(alias="runId")]
UnitIdPath = Annotated[UUID, Path(alias="unitId")]
SnapshotIdPath = Annotated[UUID, Path(alias="snapshotId")]
ClassificationIdPath = Annotated[UUID, Path(alias="classificationId")]
SnapshotIdQuery = Annotated[UUID | None, Query(alias="snapshotId")]
ResolutionRevisionQuery = Annotated[int | None, Query(alias="revision", ge=1)]
CursorQuery = Annotated[str | None, Query(max_length=1_024)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
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
        400: {"description": "Result 请求或分页 Cursor 无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        404: {"description": "Result 资源不存在或不可见", "model": ProblemDetails},
    }
)


@router.get(
    "/task-runs/{runId}/result",
    response_model=TaskResultView,
    summary="读取 TaskRun 的 latest 或 exact Result Snapshot",
    responses={304: {"description": "ETag 未变化"}},
)
async def get_task_result(
    run_id: RunIdPath,
    response: Response,
    actor: ActorDependency,
    service: ResultQueryServiceDependency,
    snapshot_id: SnapshotIdQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> TaskResultView | Response:
    result = await service.get_task_result(
        actor,
        run_id,
        snapshot_id=snapshot_id,
    )
    etag = _task_result_etag(result)
    headers = {
        "ETag": etag,
        "Cache-Control": "private, no-cache",
        "X-Result-Snapshot-Id": str(result.result_snapshot.id),
        "X-Projection-Watermark": result.projection_watermark.isoformat(),
    }
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return result


@router.get(
    "/execution-units/{unitId}/resolution",
    response_model=UnitResolutionRevision,
    summary="读取 ExecutionUnit 的 latest 或 exact Resolution Revision",
    responses={304: {"description": "ETag 未变化"}},
)
async def get_unit_resolution(
    unit_id: UnitIdPath,
    response: Response,
    actor: ActorDependency,
    service: ResultQueryServiceDependency,
    revision: ResolutionRevisionQuery = None,
    if_none_match: IfNoneMatchHeader = None,
) -> UnitResolutionRevision | Response:
    resolution = await service.get_unit_resolution(
        actor,
        unit_id,
        revision=revision,
    )
    etag = _digest_etag(
        "unit-resolution",
        resolution.model_dump_json(by_alias=True),
    )
    headers = {
        "ETag": etag,
        "Cache-Control": (
            "private, max-age=31536000, immutable"
            if revision is not None
            else "private, no-cache"
        ),
    }
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return resolution


@router.get(
    "/result-snapshots/{snapshotId}/clusters",
    response_model=FailureClusterPage,
    summary="按稳定 as-of Cursor 列出 Result Snapshot 的 FailureCluster",
    responses={304: {"description": "ETag 未变化"}},
)
async def list_failure_clusters(
    snapshot_id: SnapshotIdPath,
    response: Response,
    actor: ActorDependency,
    service: ResultQueryServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 50,
    if_none_match: IfNoneMatchHeader = None,
) -> FailureClusterPage | Response:
    page = await service.list_snapshot_clusters(
        actor,
        snapshot_id,
        cursor=cursor,
        limit=limit,
    )
    etag = _page_etag(page)
    headers = {
        "ETag": etag,
        "Cache-Control": "private, no-cache",
        "X-Result-Snapshot-Id": str(page.result_snapshot_id),
        "X-Projection-Watermark": page.projection_watermark.isoformat(),
        "X-Result-As-Of": page.as_of.isoformat(),
    }
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return page


@router.post(
    "/failure-classifications/{classificationId}/revisions",
    response_model=FailureClassificationRevision,
    status_code=status.HTTP_201_CREATED,
    summary="追加人工 FailureClassification Revision",
    responses={
        403: {"description": "当前角色不能复核 Result", "model": ProblemDetails},
        409: {"description": "Classification Revision 已变化", "model": ProblemDetails},
    },
)
async def revise_failure_classification(
    classification_id: ClassificationIdPath,
    command: RequestFailureClassificationRevision,
    response: Response,
    actor: ActorDependency,
    service: ResultClassificationServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> FailureClassificationRevision:
    result = await service.revise_classification(
        actor,
        classification_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    response.headers["Location"] = (
        f"/v1/failure-classifications/{classification_id}/revisions"
        f"?revision={result.value.revision}"
    )
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Cache-Control"] = "no-store"
    return result.value


@router.post(
    "/task-gates/evaluations",
    response_model=TaskGateDecision,
    status_code=status.HTTP_201_CREATED,
    summary="对 exact Result Snapshot 追加三值 Task Gate 决策",
    responses={
        403: {"description": "当前角色不能评估 Task Gate", "model": ProblemDetails},
        409: {"description": "Snapshot 或 Classification 尚不可评估", "model": ProblemDetails},
    },
)
async def evaluate_task_gate(
    command: RequestTaskGateEvaluation,
    response: Response,
    actor: ActorDependency,
    service: ResultGateServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskGateDecision:
    result = await service.evaluate(
        actor,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    response.headers["Location"] = (
        f"/v1/task-runs/{result.value.task_run_id}/result"
        f"?snapshotId={result.value.result_snapshot_id}"
    )
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Cache-Control"] = "no-store"
    return result.value


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    return any(candidate.strip() in {"*", etag} for candidate in if_none_match.split(","))


def _task_result_etag(result: TaskResultView) -> str:
    gate_hash = (
        result.task_gate_decision.decision_hash
        if result.task_gate_decision is not None
        else "none"
    )
    return _digest_etag(
        "task-result",
        f"{result.result_snapshot.snapshot_hash}:{gate_hash}",
    )


def _page_etag(page: FailureClusterPage) -> str:
    payload = json.dumps(
        page.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return _digest_etag("failure-clusters", payload)


def _digest_etag(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f'"{prefix}-{digest}"'
