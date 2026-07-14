"""Tenant、Project 与 Environment 公共 API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import PlatformServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import (
    ActorDependency,
    ActorHeader,
    DevelopmentModeDependency,
    parse_optional_actor_header,
)
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.core.request_context import get_request_id
from atlas_testops.domain.platform import (
    CreateEnvironment,
    CreateProject,
    CreateTenant,
    Environment,
    EnvironmentPage,
    Project,
    ProjectPage,
    Tenant,
    UpdateEnvironment,
    UpdateProject,
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
ProjectIdPath = Annotated[UUID, Path(alias="projectId")]
EnvironmentIdPath = Annotated[UUID, Path(alias="environmentId")]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]

router = APIRouter(
    responses={
        400: {"description": "请求语义无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "当前身份或环境无权执行", "model": ProblemDetails},
        404: {"description": "资源不存在或对当前 Tenant 不可见", "model": ProblemDetails},
        409: {"description": "唯一键或幂等冲突", "model": ProblemDetails},
        412: {"description": "Revision 前置条件失败", "model": ProblemDetails},
    }
)


def _set_resource_headers(response: Response, resource_path: str, revision: int) -> None:
    """集中设置可并发更新资源的定位与 Revision。"""

    response.headers["Location"] = resource_path
    response.headers["ETag"] = format_revision_etag(revision)


@router.post(
    "/tenants",
    response_model=Tenant,
    status_code=status.HTTP_201_CREATED,
    summary="创建开发期 Tenant",
)
async def bootstrap_tenant(
    command: CreateTenant,
    response: Response,
    development_mode: DevelopmentModeDependency,
    service: PlatformServiceDependency,
    actor_header: ActorHeader = None,
) -> Tenant:
    """在 Local、Test 或 Development 环境创建隔离根。"""

    del development_mode
    tenant = await service.bootstrap_tenant(
        command,
        request_id=get_request_id(),
        actor_id=parse_optional_actor_header(actor_header),
    )
    _set_resource_headers(response, "/v1/tenants/current", tenant.revision)
    return tenant


@router.get(
    "/tenants/current",
    response_model=Tenant,
    summary="读取当前 Tenant",
)
async def get_current_tenant(
    response: Response,
    actor: ActorDependency,
    service: PlatformServiceDependency,
) -> Tenant:
    """返回当前开发期 Actor 所属 Tenant。"""

    tenant = await service.get_current_tenant(actor)
    response.headers["ETag"] = format_revision_etag(tenant.revision)
    return tenant


@router.post(
    "/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
    summary="创建 Project",
)
async def create_project(
    command: CreateProject,
    response: Response,
    actor: ActorDependency,
    service: PlatformServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> Project:
    """幂等创建当前 Tenant 下的 Project。"""

    result = await service.create_project(
        actor,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    _set_resource_headers(response, f"/v1/projects/{result.value.id}", result.value.revision)
    return result.value


@router.get("/projects", response_model=ProjectPage, summary="列出 Project")
async def list_projects(
    actor: ActorDependency,
    service: PlatformServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> ProjectPage:
    """使用不透明 Cursor 列出当前 Tenant 的 Project。"""

    return await service.list_projects(actor, cursor=cursor, limit=limit)


@router.get("/projects/{projectId}", response_model=Project, summary="读取 Project")
async def get_project(
    project_id: ProjectIdPath,
    response: Response,
    actor: ActorDependency,
    service: PlatformServiceDependency,
) -> Project:
    """读取当前 Tenant 可见的 Project。"""

    project = await service.get_project(actor, project_id)
    response.headers["ETag"] = format_revision_etag(project.revision)
    return project


@router.patch("/projects/{projectId}", response_model=Project, summary="更新 Project")
async def update_project(
    project_id: ProjectIdPath,
    command: UpdateProject,
    response: Response,
    actor: ActorDependency,
    service: PlatformServiceDependency,
    if_match: IfMatchHeader,
) -> Project:
    """仅在 If-Match Revision 仍为当前值时更新 Project。"""

    project = await service.update_project(
        actor,
        project_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(project.revision)
    return project


@router.post(
    "/projects/{projectId}/environments",
    response_model=Environment,
    status_code=status.HTTP_201_CREATED,
    summary="创建 Environment",
)
async def create_environment(
    project_id: ProjectIdPath,
    command: CreateEnvironment,
    response: Response,
    actor: ActorDependency,
    service: PlatformServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> Environment:
    """幂等创建 Project 下的 Environment。"""

    result = await service.create_environment(
        actor,
        project_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    _set_resource_headers(
        response,
        f"/v1/environments/{result.value.id}",
        result.value.revision,
    )
    return result.value


@router.get(
    "/projects/{projectId}/environments",
    response_model=EnvironmentPage,
    summary="列出 Environment",
)
async def list_environments(
    project_id: ProjectIdPath,
    actor: ActorDependency,
    service: PlatformServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> EnvironmentPage:
    """使用不透明 Cursor 列出 Project 的 Environment。"""

    return await service.list_environments(
        actor,
        project_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/environments/{environmentId}",
    response_model=Environment,
    summary="读取 Environment",
)
async def get_environment(
    environment_id: EnvironmentIdPath,
    response: Response,
    actor: ActorDependency,
    service: PlatformServiceDependency,
) -> Environment:
    """读取当前 Tenant 可见的 Environment。"""

    environment = await service.get_environment(actor, environment_id)
    response.headers["ETag"] = format_revision_etag(environment.revision)
    return environment


@router.patch(
    "/environments/{environmentId}",
    response_model=Environment,
    summary="更新 Environment",
)
async def update_environment(
    environment_id: EnvironmentIdPath,
    command: UpdateEnvironment,
    response: Response,
    actor: ActorDependency,
    service: PlatformServiceDependency,
    if_match: IfMatchHeader,
) -> Environment:
    """仅在 If-Match Revision 仍为当前值时更新 Environment。"""

    environment = await service.update_environment(
        actor,
        environment_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(environment.revision)
    return environment
