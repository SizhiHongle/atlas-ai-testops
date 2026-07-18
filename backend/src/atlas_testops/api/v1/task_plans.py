"""Reusable TaskPlan authoring and immutable publication API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import (
    TaskPlanLaunchServiceDependency,
    TaskPlanServiceDependency,
)
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.domain.task import (
    CreateTaskPlan,
    PublishTaskPlanVersion,
    StartTaskPlanVersionRun,
    TaskPlan,
    TaskPlanPage,
    TaskPlanVersion,
    TaskPlanVersionPage,
    TaskRun,
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
ProjectIdPath = Annotated[UUID, Path(alias="projectId")]
TaskPlanIdPath = Annotated[UUID, Path(alias="taskPlanId")]
TaskPlanVersionIdPath = Annotated[UUID, Path(alias="taskPlanVersionId")]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]

router = APIRouter(
    responses={
        400: {"description": "请求或幂等协议无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "当前角色不能编写 TaskPlan", "model": ProblemDetails},
        404: {"description": "Project、TaskPlan 或版本不存在", "model": ProblemDetails},
        409: {
            "description": "唯一键、状态、依赖门禁或幂等冲突",
            "model": ProblemDetails,
        },
    }
)


@router.post(
    "/projects/{projectId}/task-plans",
    response_model=TaskPlan,
    status_code=status.HTTP_201_CREATED,
    summary="创建可复用 TaskPlan",
)
async def create_task_plan(
    project_id: ProjectIdPath,
    command: CreateTaskPlan,
    response: Response,
    actor: ActorDependency,
    service: TaskPlanServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskPlan:
    result = await service.create(
        actor,
        project_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Location"] = f"/v1/task-plans/{result.value.id}"
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    return result.value


@router.get(
    "/projects/{projectId}/task-plans",
    response_model=TaskPlanPage,
    summary="列出 Project 的 TaskPlan",
)
async def list_task_plans(
    project_id: ProjectIdPath,
    actor: ActorDependency,
    service: TaskPlanServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> TaskPlanPage:
    return await service.list_for_project(
        actor,
        project_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/task-plans/{taskPlanId}",
    response_model=TaskPlan,
    summary="读取 TaskPlan",
)
async def get_task_plan(
    task_plan_id: TaskPlanIdPath,
    response: Response,
    actor: ActorDependency,
    service: TaskPlanServiceDependency,
) -> TaskPlan:
    plan = await service.get(actor, task_plan_id)
    response.headers["ETag"] = format_revision_etag(plan.revision)
    return plan


@router.post(
    "/task-plans/{taskPlanId}/versions",
    response_model=TaskPlanVersion,
    status_code=status.HTTP_201_CREATED,
    summary="发布不可变 TaskPlanVersion",
)
async def publish_task_plan_version(
    task_plan_id: TaskPlanIdPath,
    command: PublishTaskPlanVersion,
    response: Response,
    actor: ActorDependency,
    service: TaskPlanServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskPlanVersion:
    result = await service.publish_version(
        actor,
        task_plan_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Location"] = f"/v1/task-plan-versions/{result.value.id}"
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    return result.value


@router.get(
    "/task-plans/{taskPlanId}/versions",
    response_model=TaskPlanVersionPage,
    summary="列出 TaskPlan 的不可变版本",
)
async def list_task_plan_versions(
    task_plan_id: TaskPlanIdPath,
    actor: ActorDependency,
    service: TaskPlanServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> TaskPlanVersionPage:
    return await service.list_versions(
        actor,
        task_plan_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/task-plan-versions/{taskPlanVersionId}",
    response_model=TaskPlanVersion,
    summary="按 ID 读取精确 TaskPlanVersion",
)
async def get_task_plan_version(
    task_plan_version_id: TaskPlanVersionIdPath,
    response: Response,
    actor: ActorDependency,
    service: TaskPlanServiceDependency,
) -> TaskPlanVersion:
    version = await service.get_version(actor, task_plan_version_id)
    response.headers["ETag"] = format_revision_etag(version.revision)
    return version


@router.post(
    "/task-plan-versions/{taskPlanVersionId}:run",
    response_model=TaskRun,
    status_code=status.HTTP_201_CREATED,
    summary="手动启动已发布 TaskPlanVersion",
    responses={
        403: {"description": "当前角色不能运行该 TaskPlan", "model": ProblemDetails},
        409: {
            "description": "策略、矩阵、依赖门禁或幂等输入冲突",
            "model": ProblemDetails,
        },
    },
)
async def launch_task_plan_version(
    task_plan_version_id: TaskPlanVersionIdPath,
    command: StartTaskPlanVersionRun,
    response: Response,
    actor: ActorDependency,
    service: TaskPlanLaunchServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskRun:
    result = await service.launch(
        actor,
        task_plan_version_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Location"] = f"/v1/task-runs/{result.value.id}"
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    return result.value
