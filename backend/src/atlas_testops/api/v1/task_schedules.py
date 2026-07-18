"""Task Schedule catalog and future-trigger desired-state commands."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import TaskScheduleServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.domain.task import (
    CreateTaskSchedule,
    RequestTaskSchedulePause,
    RequestTaskScheduleResume,
    TaskSchedule,
    TaskSchedulePage,
)

TaskPlanVersionIdPath = Annotated[UUID, Path(alias="taskPlanVersionId")]
ScheduleIdPath = Annotated[UUID, Path(alias="scheduleId")]
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]

router = APIRouter(
    responses={
        400: {"description": "Schedule 请求或分页 Cursor 无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        404: {"description": "Schedule 或 TaskPlanVersion 不存在", "model": ProblemDetails},
    }
)


@router.post(
    "/task-plan-versions/{taskPlanVersionId}/schedules",
    response_model=TaskSchedule,
    status_code=status.HTTP_201_CREATED,
    summary="为 exact TaskPlanVersion 创建 Temporal Schedule desired state",
    responses={
        403: {"description": "当前角色不能管理 Schedule", "model": ProblemDetails},
        409: {
            "description": "Schedule Key、环境、策略或依赖门禁冲突",
            "model": ProblemDetails,
        },
    },
)
async def create_task_schedule(
    task_plan_version_id: TaskPlanVersionIdPath,
    command: CreateTaskSchedule,
    response: Response,
    actor: ActorDependency,
    service: TaskScheduleServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskSchedule:
    result = await service.create(
        actor,
        task_plan_version_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    response.headers["Location"] = f"/v1/schedules/{result.value.id}"
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return result.value


@router.get(
    "/task-plan-versions/{taskPlanVersionId}/schedules",
    response_model=TaskSchedulePage,
    summary="列出 exact TaskPlanVersion 的 Schedule",
)
async def list_task_schedules(
    task_plan_version_id: TaskPlanVersionIdPath,
    actor: ActorDependency,
    service: TaskScheduleServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> TaskSchedulePage:
    return await service.list_for_version(
        actor,
        task_plan_version_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/schedules/{scheduleId}",
    response_model=TaskSchedule,
    summary="读取 Schedule desired state 与 Temporal sync 投影",
)
async def get_task_schedule(
    schedule_id: ScheduleIdPath,
    response: Response,
    actor: ActorDependency,
    service: TaskScheduleServiceDependency,
) -> TaskSchedule:
    schedule = await service.get(actor, schedule_id)
    response.headers["ETag"] = format_revision_etag(schedule.revision)
    return schedule


@router.post(
    "/schedules/{scheduleId}:pause",
    response_model=TaskSchedule,
    status_code=status.HTTP_202_ACCEPTED,
    summary="暂停 Schedule 的未来触发，不影响已启动 TaskRun",
    responses={
        403: {"description": "当前角色不能管理 Schedule", "model": ProblemDetails},
        409: {"description": "Schedule 当前状态不允许暂停", "model": ProblemDetails},
        412: {"description": "Schedule Revision 已变化", "model": ProblemDetails},
    },
)
async def pause_task_schedule(
    schedule_id: ScheduleIdPath,
    command: RequestTaskSchedulePause,
    response: Response,
    actor: ActorDependency,
    service: TaskScheduleServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskSchedule:
    result = await service.pause(
        actor,
        schedule_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    response.headers["Location"] = f"/v1/schedules/{result.value.id}"
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return result.value


@router.post(
    "/schedules/{scheduleId}:resume",
    response_model=TaskSchedule,
    status_code=status.HTTP_202_ACCEPTED,
    summary="恢复 Schedule 的未来触发",
    responses={
        403: {"description": "当前角色不能管理 Schedule", "model": ProblemDetails},
        409: {"description": "Schedule 当前状态不允许恢复", "model": ProblemDetails},
        412: {"description": "Schedule Revision 已变化", "model": ProblemDetails},
    },
)
async def resume_task_schedule(
    schedule_id: ScheduleIdPath,
    command: RequestTaskScheduleResume,
    response: Response,
    actor: ActorDependency,
    service: TaskScheduleServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskSchedule:
    result = await service.resume(
        actor,
        schedule_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    response.headers["Location"] = f"/v1/schedules/{result.value.id}"
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return result.value


__all__ = ["router"]
