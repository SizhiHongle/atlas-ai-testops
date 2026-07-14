"""Internal bounded recovery entry points for Fixture Cleanup."""

from typing import Annotated

from fastapi import APIRouter, Query

from atlas_testops.api.dependencies import FixtureRunServiceDependency
from atlas_testops.api.security import ActorDependency
from atlas_testops.domain.fixture import FixtureCleanupSweepBatch

SweepLimitQuery = Annotated[int, Query(ge=1, le=100)]
WorkerIdentityQuery = Annotated[
    str,
    Query(
        alias="workerIdentity",
        min_length=3,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$",
    ),
]

router = APIRouter()


@router.post(
    "/fixture-cleanup:sweep",
    response_model=FixtureCleanupSweepBatch,
    summary="执行一批 Fixture Cleanup Reconcile 与孤儿扫描",
)
async def sweep_fixture_cleanup(
    actor: ActorDependency,
    service: FixtureRunServiceDependency,
    worker_identity: WorkerIdentityQuery = "fixture-sweeper",
    limit: SweepLimitQuery = 50,
) -> FixtureCleanupSweepBatch:
    return await service.sweep_cleanup(
        actor,
        worker_identity=worker_identity,
        limit=limit,
    )
