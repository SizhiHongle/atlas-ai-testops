"""Versioned DataAtom and DataBlueprint control-plane API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import FixtureAssetServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.domain.fixture import (
    CompileBlueprintResponse,
    CreateDataAtom,
    CreateDataAtomVersion,
    CreateDataBlueprint,
    CreateDataBlueprintVersion,
    DataAtomDefinition,
    DataAtomPage,
    DataAtomVersion,
    DataAtomVersionPage,
    DataBlueprintDefinition,
    DataBlueprintPage,
    DataBlueprintVersion,
    DataBlueprintVersionPage,
    UpdateDataAtom,
    UpdateDataAtomVersion,
    UpdateDataBlueprint,
    UpdateDataBlueprintVersion,
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
ProjectIdPath = Annotated[UUID, Path(alias="projectId")]
AtomIdPath = Annotated[UUID, Path(alias="atomId")]
BlueprintIdPath = Annotated[UUID, Path(alias="blueprintId")]
VersionIdPath = Annotated[UUID, Path(alias="versionId")]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]

router = APIRouter(
    responses={
        400: {"description": "请求或契约语义无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "当前 PlatformRole 无权执行", "model": ProblemDetails},
        404: {"description": "资产不存在或不可见", "model": ProblemDetails},
        409: {"description": "状态、唯一键、幂等或发布门禁冲突", "model": ProblemDetails},
        412: {"description": "Revision 前置条件失败", "model": ProblemDetails},
    }
)


def _set_headers(response: Response, path: str, revision: int) -> None:
    response.headers["Location"] = path
    response.headers["ETag"] = format_revision_etag(revision)


@router.post(
    "/projects/{projectId}/data-atoms",
    response_model=DataAtomDefinition,
    status_code=status.HTTP_201_CREATED,
    summary="创建 DataAtom 定义",
)
async def create_data_atom(
    project_id: ProjectIdPath,
    command: CreateDataAtom,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> DataAtomDefinition:
    result = await service.create_atom_definition(
        actor,
        project_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    _set_headers(response, f"/v1/data-atoms/{result.value.id}", result.value.revision)
    return result.value


@router.get(
    "/projects/{projectId}/data-atoms",
    response_model=DataAtomPage,
    summary="列出 DataAtom Catalog",
)
async def list_data_atoms(
    project_id: ProjectIdPath,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> DataAtomPage:
    return await service.list_atom_definitions(
        actor,
        project_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/data-atoms/{atomId}",
    response_model=DataAtomDefinition,
    summary="读取 DataAtom 定义",
)
async def get_data_atom(
    atom_id: AtomIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
) -> DataAtomDefinition:
    atom = await service.get_atom_definition(actor, atom_id)
    response.headers["ETag"] = format_revision_etag(atom.revision)
    return atom


@router.patch(
    "/data-atoms/{atomId}",
    response_model=DataAtomDefinition,
    summary="更新 DataAtom 定义",
)
async def update_data_atom(
    atom_id: AtomIdPath,
    command: UpdateDataAtom,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    if_match: IfMatchHeader,
) -> DataAtomDefinition:
    atom = await service.update_atom_definition(
        actor,
        atom_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(atom.revision)
    return atom


@router.post(
    "/data-atoms/{atomId}/versions",
    response_model=DataAtomVersion,
    status_code=status.HTTP_201_CREATED,
    summary="创建 DataAtomVersion",
)
async def create_data_atom_version(
    atom_id: AtomIdPath,
    command: CreateDataAtomVersion,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> DataAtomVersion:
    result = await service.create_atom_version(
        actor,
        atom_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    _set_headers(
        response,
        f"/v1/data-atom-versions/{result.value.id}",
        result.value.revision,
    )
    return result.value


@router.get(
    "/data-atoms/{atomId}/versions",
    response_model=DataAtomVersionPage,
    summary="列出 DataAtomVersion",
)
async def list_data_atom_versions(
    atom_id: AtomIdPath,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> DataAtomVersionPage:
    return await service.list_atom_versions(
        actor,
        atom_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/data-atom-versions/{versionId}",
    response_model=DataAtomVersion,
    summary="读取 DataAtomVersion",
)
async def get_data_atom_version(
    version_id: VersionIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
) -> DataAtomVersion:
    version = await service.get_atom_version(actor, version_id)
    response.headers["ETag"] = format_revision_etag(version.revision)
    return version


@router.patch(
    "/data-atom-versions/{versionId}",
    response_model=DataAtomVersion,
    summary="更新 DataAtomVersion 草稿",
)
async def update_data_atom_version(
    version_id: VersionIdPath,
    command: UpdateDataAtomVersion,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    if_match: IfMatchHeader,
) -> DataAtomVersion:
    version = await service.update_atom_version(
        actor,
        version_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(version.revision)
    return version


@router.post(
    "/data-atom-versions/{versionId}:validate",
    response_model=DataAtomVersion,
    summary="静态验证 DataAtomVersion",
)
async def validate_data_atom_version(
    version_id: VersionIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    if_match: IfMatchHeader,
) -> DataAtomVersion:
    version = await service.validate_atom_version(
        actor,
        version_id,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(version.revision)
    return version


@router.post(
    "/data-atom-versions/{versionId}:publish",
    response_model=DataAtomVersion,
    summary="发布 DataAtomVersion",
)
async def publish_data_atom_version(
    version_id: VersionIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    if_match: IfMatchHeader,
) -> DataAtomVersion:
    version = await service.publish_atom_version(
        actor,
        version_id,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(version.revision)
    return version


@router.post(
    "/projects/{projectId}/data-blueprints",
    response_model=DataBlueprintDefinition,
    status_code=status.HTTP_201_CREATED,
    summary="创建 DataBlueprint 定义",
)
async def create_data_blueprint(
    project_id: ProjectIdPath,
    command: CreateDataBlueprint,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> DataBlueprintDefinition:
    result = await service.create_blueprint_definition(
        actor,
        project_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    _set_headers(
        response,
        f"/v1/data-blueprints/{result.value.id}",
        result.value.revision,
    )
    return result.value


@router.get(
    "/projects/{projectId}/data-blueprints",
    response_model=DataBlueprintPage,
    summary="列出 DataBlueprint Catalog",
)
async def list_data_blueprints(
    project_id: ProjectIdPath,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> DataBlueprintPage:
    return await service.list_blueprint_definitions(
        actor,
        project_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/data-blueprints/{blueprintId}",
    response_model=DataBlueprintDefinition,
    summary="读取 DataBlueprint 定义",
)
async def get_data_blueprint(
    blueprint_id: BlueprintIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
) -> DataBlueprintDefinition:
    blueprint = await service.get_blueprint_definition(actor, blueprint_id)
    response.headers["ETag"] = format_revision_etag(blueprint.revision)
    return blueprint


@router.patch(
    "/data-blueprints/{blueprintId}",
    response_model=DataBlueprintDefinition,
    summary="更新 DataBlueprint 定义",
)
async def update_data_blueprint(
    blueprint_id: BlueprintIdPath,
    command: UpdateDataBlueprint,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    if_match: IfMatchHeader,
) -> DataBlueprintDefinition:
    blueprint = await service.update_blueprint_definition(
        actor,
        blueprint_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(blueprint.revision)
    return blueprint


@router.post(
    "/data-blueprints/{blueprintId}/versions",
    response_model=DataBlueprintVersion,
    status_code=status.HTTP_201_CREATED,
    summary="创建 DataBlueprintVersion",
)
async def create_data_blueprint_version(
    blueprint_id: BlueprintIdPath,
    command: CreateDataBlueprintVersion,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> DataBlueprintVersion:
    result = await service.create_blueprint_version(
        actor,
        blueprint_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    _set_headers(
        response,
        f"/v1/data-blueprint-versions/{result.value.id}",
        result.value.revision,
    )
    return result.value


@router.get(
    "/data-blueprints/{blueprintId}/versions",
    response_model=DataBlueprintVersionPage,
    summary="列出 DataBlueprintVersion",
)
async def list_data_blueprint_versions(
    blueprint_id: BlueprintIdPath,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> DataBlueprintVersionPage:
    return await service.list_blueprint_versions(
        actor,
        blueprint_id,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/data-blueprint-versions/{versionId}",
    response_model=DataBlueprintVersion,
    summary="读取 DataBlueprintVersion",
)
async def get_data_blueprint_version(
    version_id: VersionIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
) -> DataBlueprintVersion:
    version = await service.get_blueprint_version(actor, version_id)
    response.headers["ETag"] = format_revision_etag(version.revision)
    return version


@router.patch(
    "/data-blueprint-versions/{versionId}",
    response_model=DataBlueprintVersion,
    summary="更新 DataBlueprintVersion 草稿",
)
async def update_data_blueprint_version(
    version_id: VersionIdPath,
    command: UpdateDataBlueprintVersion,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    if_match: IfMatchHeader,
) -> DataBlueprintVersion:
    version = await service.update_blueprint_version(
        actor,
        version_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(version.revision)
    return version


@router.post(
    "/data-blueprint-versions/{versionId}:compile",
    response_model=CompileBlueprintResponse,
    summary="静态编译 DataBlueprintVersion",
)
async def compile_data_blueprint_version(
    version_id: VersionIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    if_match: IfMatchHeader,
) -> CompileBlueprintResponse:
    result = await service.compile_blueprint_version(
        actor,
        version_id,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(result.version.revision)
    return result


@router.post(
    "/data-blueprint-versions/{versionId}:publish",
    response_model=DataBlueprintVersion,
    summary="发布 DataBlueprintVersion",
)
async def publish_data_blueprint_version(
    version_id: VersionIdPath,
    response: Response,
    actor: ActorDependency,
    service: FixtureAssetServiceDependency,
    if_match: IfMatchHeader,
) -> DataBlueprintVersion:
    version = await service.publish_blueprint_version(
        actor,
        version_id,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(version.revision)
    return version
