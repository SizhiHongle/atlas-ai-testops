"""TaskRun reads and durable control-command acceptance."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import (
    TaskPlanLaunchServiceDependency,
    TaskRunCommandServiceDependency,
    TaskRunQueryServiceDependency,
    TaskRunRerunServiceDependency,
)
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.domain.task import (
    ExecutionUnitPage,
    RequestTaskRunCancel,
    RequestTaskRunInfraFailureRerun,
    RequestTaskRunPause,
    RequestTaskRunResume,
    TaskExecutionEventPage,
    TaskRun,
    TaskRunCommandIntent,
    TaskRunManifest,
    TaskRunPage,
    TriggerTaskPlanVersionRun,
    UnitAttemptPage,
)

ProjectIdPath = Annotated[UUID, Path(alias="projectId")]
RunIdPath = Annotated[UUID, Path(alias="runId")]
UnitIdPath = Annotated[UUID, Path(alias="unitId")]
CommandIdPath = Annotated[UUID, Path(alias="commandId")]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]
AfterOrdinalQuery = Annotated[int, Query(alias="afterOrdinal", ge=0)]
AfterAttemptNumberQuery = Annotated[int, Query(alias="afterAttemptNumber", ge=0)]
AfterSeqQuery = Annotated[int, Query(alias="afterSeq", ge=0)]

router = APIRouter(
    responses={
        400: {"description": "分页 Cursor 无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        404: {"description": "TaskRun 或 ExecutionUnit 不存在", "model": ProblemDetails},
    }
)


@router.post(
    "/task-runs",
    response_model=TaskRun,
    status_code=status.HTTP_201_CREATED,
    summary="通过 Schedule、CI 或 Webhook 幂等触发 TaskRun",
    responses={
        403: {"description": "当前角色不能运行该 TaskPlan", "model": ProblemDetails},
        409: {
            "description": "触发身份、策略、矩阵或依赖门禁冲突",
            "model": ProblemDetails,
        },
    },
)
async def trigger_task_run(
    command: TriggerTaskPlanVersionRun,
    response: Response,
    actor: ActorDependency,
    service: TaskPlanLaunchServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskRun:
    result = await service.trigger(
        actor,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    response.headers["Location"] = f"/v1/task-runs/{result.value.id}"
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return result.value


@router.get(
    "/projects/{projectId}/task-runs",
    response_model=TaskRunPage,
    summary="列出 Project 的 TaskRun",
)
async def list_task_runs(
    project_id: ProjectIdPath,
    actor: ActorDependency,
    service: TaskRunQueryServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> TaskRunPage:
    return await service.list_for_project(
        actor,
        project_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/task-runs/{runId}",
    response_model=TaskRun,
    summary="读取 TaskRun 状态快照",
)
async def get_task_run(
    run_id: RunIdPath,
    response: Response,
    actor: ActorDependency,
    service: TaskRunQueryServiceDependency,
) -> TaskRun:
    run = await service.get(actor, run_id)
    response.headers["ETag"] = format_revision_etag(run.revision)
    return run


@router.post(
    "/task-runs/{runId}:cancel",
    response_model=TaskRunCommandIntent,
    status_code=status.HTTP_202_ACCEPTED,
    summary="可靠请求取消 TaskRun",
    responses={
        403: {"description": "当前角色不能控制该 TaskRun", "model": ProblemDetails},
        409: {"description": "TaskRun 状态或幂等键冲突", "model": ProblemDetails},
        412: {"description": "TaskRun Revision 前置条件失败", "model": ProblemDetails},
    },
)
async def cancel_task_run(
    run_id: RunIdPath,
    command: RequestTaskRunCancel,
    response: Response,
    actor: ActorDependency,
    service: TaskRunCommandServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskRunCommandIntent:
    result = await service.cancel(
        actor,
        run_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["ETag"] = format_revision_etag(
        result.value.accepted_run_revision
    )
    response.headers["Location"] = (
        f"/v1/task-runs/{run_id}/commands/{result.value.id}"
    )
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return result.value


@router.post(
    "/task-runs/{runId}:pause",
    response_model=TaskRunCommandIntent,
    status_code=status.HTTP_202_ACCEPTED,
    summary="在安全批次边界暂停 TaskRun 派发",
    responses={
        403: {"description": "当前角色不能控制该 TaskRun", "model": ProblemDetails},
        409: {"description": "TaskRun 状态或幂等键冲突", "model": ProblemDetails},
        412: {"description": "TaskRun Revision 前置条件失败", "model": ProblemDetails},
    },
)
async def pause_task_run(
    run_id: RunIdPath,
    command: RequestTaskRunPause,
    response: Response,
    actor: ActorDependency,
    service: TaskRunCommandServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskRunCommandIntent:
    result = await service.pause(
        actor,
        run_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    _set_command_response(response, result.value, result.status_code, result.replayed)
    return result.value


@router.post(
    "/task-runs/{runId}:resume",
    response_model=TaskRunCommandIntent,
    status_code=status.HTTP_202_ACCEPTED,
    summary="继续派发已暂停的 TaskRun",
    responses={
        403: {"description": "当前角色不能控制该 TaskRun", "model": ProblemDetails},
        409: {"description": "TaskRun 状态或幂等键冲突", "model": ProblemDetails},
        412: {"description": "TaskRun Revision 前置条件失败", "model": ProblemDetails},
    },
)
async def resume_task_run(
    run_id: RunIdPath,
    command: RequestTaskRunResume,
    response: Response,
    actor: ActorDependency,
    service: TaskRunCommandServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskRunCommandIntent:
    result = await service.resume(
        actor,
        run_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    _set_command_response(response, result.value, result.status_code, result.replayed)
    return result.value


@router.post(
    "/task-runs/{runId}:rerun-infra-failures",
    response_model=TaskRun,
    status_code=status.HTTP_201_CREATED,
    summary="创建仅包含环境失败单元的子 TaskRun",
    responses={
        403: {"description": "当前角色不能运行该 TaskRun", "model": ProblemDetails},
        409: {"description": "父 TaskRun 状态或环境失败选择冲突", "model": ProblemDetails},
        412: {"description": "父 TaskRun Revision 前置条件失败", "model": ProblemDetails},
    },
)
async def rerun_task_run_infrastructure_failures(
    run_id: RunIdPath,
    command: RequestTaskRunInfraFailureRerun,
    response: Response,
    actor: ActorDependency,
    service: TaskRunRerunServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> TaskRun:
    result = await service.rerun_infrastructure_failures(
        actor,
        run_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    response.headers["Location"] = f"/v1/task-runs/{result.value.id}"
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    return result.value


@router.get(
    "/task-runs/{runId}/commands/{commandId}",
    response_model=TaskRunCommandIntent,
    summary="读取 TaskRun 控制命令状态",
)
async def get_task_run_command(
    run_id: RunIdPath,
    command_id: CommandIdPath,
    actor: ActorDependency,
    service: TaskRunCommandServiceDependency,
) -> TaskRunCommandIntent:
    return await service.get(
        actor,
        task_run_id=run_id,
        command_id=command_id,
    )


@router.get(
    "/task-runs/{runId}/manifest",
    response_model=TaskRunManifest,
    summary="读取 TaskRun 的不可变 Run Manifest",
)
async def get_task_run_manifest(
    run_id: RunIdPath,
    actor: ActorDependency,
    service: TaskRunQueryServiceDependency,
) -> TaskRunManifest:
    return await service.get_manifest(actor, run_id)


@router.get(
    "/task-runs/{runId}/units",
    response_model=ExecutionUnitPage,
    summary="按 Manifest 顺序列出 ExecutionUnit",
)
async def list_execution_units(
    run_id: RunIdPath,
    actor: ActorDependency,
    service: TaskRunQueryServiceDependency,
    after_ordinal: AfterOrdinalQuery = 0,
    limit: LimitQuery = 50,
) -> ExecutionUnitPage:
    return await service.list_units(
        actor,
        run_id,
        after_ordinal=after_ordinal,
        limit=limit,
    )


@router.get(
    "/task-runs/{runId}/units/{unitId}/attempts",
    response_model=UnitAttemptPage,
    summary="列出 ExecutionUnit 的 UnitAttempt",
)
async def list_unit_attempts(
    run_id: RunIdPath,
    unit_id: UnitIdPath,
    actor: ActorDependency,
    service: TaskRunQueryServiceDependency,
    after_attempt_number: AfterAttemptNumberQuery = 0,
    limit: LimitQuery = 50,
) -> UnitAttemptPage:
    return await service.list_attempts(
        actor,
        run_id,
        unit_id,
        after_attempt_number=after_attempt_number,
        limit=limit,
    )


@router.get(
    "/task-runs/{runId}/events",
    response_model=TaskExecutionEventPage,
    summary="增量读取 TaskRun 单调事件",
)
async def list_task_run_events(
    run_id: RunIdPath,
    actor: ActorDependency,
    service: TaskRunQueryServiceDependency,
    after_seq: AfterSeqQuery = 0,
    limit: LimitQuery = 100,
) -> TaskExecutionEventPage:
    return await service.list_events(
        actor,
        run_id,
        after_seq=after_seq,
        limit=limit,
    )


def _set_command_response(
    response: Response,
    command: TaskRunCommandIntent,
    status_code: int,
    replayed: bool,
) -> None:
    response.status_code = status_code
    response.headers["ETag"] = format_revision_etag(command.accepted_run_revision)
    response.headers["Location"] = (
        f"/v1/task-runs/{command.task_run_id}/commands/{command.id}"
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
