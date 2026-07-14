"""ConnectorInstallation 管理与实际 Capability 验证 API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import ConnectorServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.domain.identity import (
    ConnectorInstallation,
    ConnectorInstallationPage,
    CreateConnectorInstallation,
    UpdateConnectorInstallation,
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
EnvironmentIdPath = Annotated[UUID, Path(alias="environmentId")]
ConnectorIdPath = Annotated[UUID, Path(alias="connectorId")]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]

router = APIRouter(
    responses={
        400: {"description": "Connector 策略或请求无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "当前 PlatformRole 无权执行", "model": ProblemDetails},
        404: {"description": "资源不存在或不可见", "model": ProblemDetails},
        409: {"description": "状态、唯一键或幂等冲突", "model": ProblemDetails},
        412: {"description": "Revision 前置条件失败", "model": ProblemDetails},
        503: {"description": "可信 Adapter 未安装", "model": ProblemDetails},
    }
)


@router.post(
    "/connector-installations",
    response_model=ConnectorInstallation,
    status_code=status.HTTP_201_CREATED,
    summary="创建 ConnectorInstallation",
)
async def create_connector_installation(
    command: CreateConnectorInstallation,
    response: Response,
    actor: ActorDependency,
    service: ConnectorServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> ConnectorInstallation:
    """登记安全配置引用；公共响应不回显 configurationRef。"""

    result = await service.create(
        actor,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["Location"] = f"/v1/connector-installations/{result.value.id}"
    response.headers["ETag"] = format_revision_etag(result.value.revision)
    return result.value


@router.get(
    "/environments/{environmentId}/connector-installations",
    response_model=ConnectorInstallationPage,
    summary="列出 ConnectorInstallation",
)
async def list_connector_installations(
    environment_id: EnvironmentIdPath,
    actor: ActorDependency,
    service: ConnectorServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> ConnectorInstallationPage:
    """按稳定 Cursor 列出 Environment 的 Connector。"""

    return await service.list(
        actor,
        environment_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/connector-installations/{connectorId}",
    response_model=ConnectorInstallation,
    summary="读取 ConnectorInstallation",
)
async def get_connector_installation(
    connector_id: ConnectorIdPath,
    response: Response,
    actor: ActorDependency,
    service: ConnectorServiceDependency,
) -> ConnectorInstallation:
    connector = await service.get(actor, connector_id)
    response.headers["ETag"] = format_revision_etag(connector.revision)
    return connector


@router.patch(
    "/connector-installations/{connectorId}",
    response_model=ConnectorInstallation,
    summary="更新 ConnectorInstallation",
)
async def update_connector_installation(
    connector_id: ConnectorIdPath,
    command: UpdateConnectorInstallation,
    response: Response,
    actor: ActorDependency,
    service: ConnectorServiceDependency,
    if_match: IfMatchHeader,
) -> ConnectorInstallation:
    connector = await service.update(
        actor,
        connector_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(connector.revision)
    return connector


@router.post(
    "/connector-installations/{connectorId}:validate",
    response_model=ConnectorInstallation,
    summary="验证 Connector 并刷新 Capability Snapshot",
)
async def validate_connector_installation(
    connector_id: ConnectorIdPath,
    response: Response,
    actor: ActorDependency,
    service: ConnectorServiceDependency,
    if_match: IfMatchHeader,
) -> ConnectorInstallation:
    """事务外执行 Probe，并以请求携带的 Revision CAS 写入结果。"""

    connector = await service.validate(
        actor,
        connector_id,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(connector.revision)
    return connector
