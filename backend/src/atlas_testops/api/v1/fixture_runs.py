"""Durable FixtureRun preparation, manifest, ledger, and release API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Response, status

from atlas_testops.api.dependencies import FixtureRunServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.domain.fixture import (
    FixtureManifestRecord,
    FixtureResourcePage,
    FixtureRun,
    FixtureRunDetail,
    StartFixtureRun,
)

ProjectIdPath = Annotated[UUID, Path(alias="projectId")]
RunIdPath = Annotated[UUID, Path(alias="runId")]
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]

router = APIRouter(
    responses={
        400: {"description": "FixtureRun 输入或绑定无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "当前 PlatformRole 无权执行", "model": ProblemDetails},
        404: {"description": "FixtureRun 或关联资产不存在", "model": ProblemDetails},
        409: {"description": "资产、租约、状态或幂等冲突", "model": ProblemDetails},
        503: {"description": "Fixture Worker 或 Connector 不可用", "model": ProblemDetails},
    }
)


@router.post(
    "/projects/{projectId}/fixture-runs",
    response_model=FixtureRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="启动 FixtureRun",
)
async def start_fixture_run(
    project_id: ProjectIdPath,
    command: StartFixtureRun,
    response: Response,
    actor: ActorDependency,
    service: FixtureRunServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> FixtureRun:
    result = await service.start(
        actor,
        project_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Location"] = f"/v1/fixture-runs/{result.value.id}"
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    return result.value


@router.get(
    "/fixture-runs/{runId}",
    response_model=FixtureRunDetail,
    summary="读取 FixtureRun 详情",
)
async def get_fixture_run(
    run_id: RunIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureRunServiceDependency,
) -> FixtureRunDetail:
    detail = await service.get_detail(actor, run_id)
    response.headers["ETag"] = format_revision_etag(detail.run.revision)
    return detail


@router.get(
    "/fixture-runs/{runId}/manifest",
    response_model=FixtureManifestRecord,
    summary="读取 FixtureManifest",
)
async def get_fixture_manifest(
    run_id: RunIdPath,
    actor: ActorDependency,
    service: FixtureRunServiceDependency,
) -> FixtureManifestRecord:
    return await service.get_manifest(actor, run_id)


@router.get(
    "/fixture-runs/{runId}/resources",
    response_model=FixtureResourcePage,
    summary="读取 Fixture Resource Ledger",
)
async def list_fixture_resources(
    run_id: RunIdPath,
    actor: ActorDependency,
    service: FixtureRunServiceDependency,
) -> FixtureResourcePage:
    return await service.list_resources(actor, run_id)


@router.post(
    "/fixture-runs/{runId}:release",
    response_model=FixtureRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="请求释放 FixtureRun",
)
async def release_fixture_run(
    run_id: RunIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureRunServiceDependency,
) -> FixtureRun:
    run = await service.release(actor, run_id)
    response.headers["Location"] = f"/v1/fixture-runs/{run.id}"
    response.headers["ETag"] = format_revision_etag(run.revision)
    return run
