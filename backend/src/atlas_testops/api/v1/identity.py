"""测试角色、账号池与测试账号管理 API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import IdentityServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.domain.identity import (
    AccountPool,
    AccountPoolCapacity,
    AccountPoolPage,
    AccountStateReason,
    CreateAccountPool,
    CreateTestAccount,
    CreateTestRole,
    TestAccount,
    TestAccountPage,
    TestRole,
    TestRolePage,
    UpdateAccountPool,
    UpdateTestAccount,
    UpdateTestRole,
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
ProjectIdPath = Annotated[UUID, Path(alias="projectId")]
EnvironmentIdPath = Annotated[UUID, Path(alias="environmentId")]
RoleIdPath = Annotated[UUID, Path(alias="roleId")]
PoolIdPath = Annotated[UUID, Path(alias="poolId")]
AccountIdPath = Annotated[UUID, Path(alias="accountId")]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]

router = APIRouter(
    responses={
        400: {"description": "请求语义或 Revision 无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "当前 PlatformRole 无权执行", "model": ProblemDetails},
        404: {"description": "资源不存在或不可见", "model": ProblemDetails},
        409: {"description": "唯一键、幂等或状态转换冲突", "model": ProblemDetails},
        412: {"description": "Revision 前置条件失败", "model": ProblemDetails},
    }
)


def _set_resource_headers(response: Response, location: str, revision: int) -> None:
    response.headers["Location"] = location
    response.headers["ETag"] = format_revision_etag(revision)


def _set_command_headers(response: Response, *, replayed: bool) -> None:
    response.headers["Idempotency-Replayed"] = str(replayed).lower()


@router.post(
    "/projects/{projectId}/test-roles",
    response_model=TestRole,
    status_code=status.HTTP_201_CREATED,
    summary="创建 TestRole",
)
async def create_test_role(
    project_id: ProjectIdPath,
    command: CreateTestRole,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> TestRole:
    """创建用例可引用、但不授予 Atlas 权限的业务角色。"""

    result = await service.create_role(
        actor,
        project_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    _set_command_headers(response, replayed=result.replayed)
    _set_resource_headers(response, f"/v1/test-roles/{result.value.id}", result.value.revision)
    return result.value


@router.get(
    "/projects/{projectId}/test-roles",
    response_model=TestRolePage,
    summary="列出 TestRole",
)
async def list_test_roles(
    project_id: ProjectIdPath,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> TestRolePage:
    """按稳定 Cursor 列出 Project 的业务角色。"""

    return await service.list_roles(actor, project_id, cursor=cursor, limit=limit)


@router.get("/test-roles/{roleId}", response_model=TestRole, summary="读取 TestRole")
async def get_test_role(
    role_id: RoleIdPath,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
) -> TestRole:
    role = await service.get_role(actor, role_id)
    response.headers["ETag"] = format_revision_etag(role.revision)
    return role


@router.patch("/test-roles/{roleId}", response_model=TestRole, summary="更新 TestRole")
async def update_test_role(
    role_id: RoleIdPath,
    command: UpdateTestRole,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    if_match: IfMatchHeader,
) -> TestRole:
    role = await service.update_role(
        actor,
        role_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(role.revision)
    return role


@router.post(
    "/environments/{environmentId}/account-pools",
    response_model=AccountPool,
    status_code=status.HTTP_201_CREATED,
    summary="创建 AccountPool",
)
async def create_account_pool(
    environment_id: EnvironmentIdPath,
    command: CreateAccountPool,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> AccountPool:
    """在 Environment 中创建绑定 TestRole 的独占账号池。"""

    result = await service.create_pool(
        actor,
        environment_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    _set_command_headers(response, replayed=result.replayed)
    _set_resource_headers(
        response,
        f"/v1/account-pools/{result.value.id}",
        result.value.revision,
    )
    return result.value


@router.get(
    "/environments/{environmentId}/account-pools",
    response_model=AccountPoolPage,
    summary="列出 AccountPool",
)
async def list_account_pools(
    environment_id: EnvironmentIdPath,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> AccountPoolPage:
    """按稳定 Cursor 列出 Environment 的账号池。"""

    return await service.list_pools(actor, environment_id, cursor=cursor, limit=limit)


@router.get(
    "/account-pools/{poolId}",
    response_model=AccountPool,
    summary="读取 AccountPool",
)
async def get_account_pool(
    pool_id: PoolIdPath,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
) -> AccountPool:
    pool = await service.get_pool(actor, pool_id)
    response.headers["ETag"] = format_revision_etag(pool.revision)
    return pool


@router.patch(
    "/account-pools/{poolId}",
    response_model=AccountPool,
    summary="更新 AccountPool",
)
async def update_account_pool(
    pool_id: PoolIdPath,
    command: UpdateAccountPool,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    if_match: IfMatchHeader,
) -> AccountPool:
    pool = await service.update_pool(
        actor,
        pool_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(pool.revision)
    return pool


@router.get(
    "/account-pools/{poolId}/capacity",
    response_model=AccountPoolCapacity,
    summary="读取 AccountPool 容量",
)
async def get_account_pool_capacity(
    pool_id: PoolIdPath,
    actor: ActorDependency,
    service: IdentityServiceDependency,
) -> AccountPoolCapacity:
    """实时计算可用、租用、冷却、隔离与未验证容量。"""

    return await service.get_capacity(actor, pool_id)


@router.post(
    "/account-pools/{poolId}/accounts",
    response_model=TestAccount,
    status_code=status.HTTP_201_CREATED,
    summary="导入 TestAccount",
)
async def create_test_account(
    pool_id: PoolIdPath,
    command: CreateTestAccount,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
) -> TestAccount:
    """原子导入账号元数据、独占 Slot 与不可兑换的 SecretRef。"""

    result = await service.create_account(
        actor,
        pool_id,
        command,
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    _set_command_headers(response, replayed=result.replayed)
    _set_resource_headers(
        response,
        f"/v1/test-accounts/{result.value.id}",
        result.value.revision,
    )
    return result.value


@router.get(
    "/account-pools/{poolId}/accounts",
    response_model=TestAccountPage,
    summary="列出 TestAccount",
)
async def list_test_accounts(
    pool_id: PoolIdPath,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> TestAccountPage:
    """只返回脱敏账号提示和计算可用性，不返回 CredentialRef。"""

    return await service.list_accounts(actor, pool_id, cursor=cursor, limit=limit)


@router.get(
    "/test-accounts/{accountId}",
    response_model=TestAccount,
    summary="读取 TestAccount",
)
async def get_test_account(
    account_id: AccountIdPath,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
) -> TestAccount:
    account = await service.get_account(actor, account_id)
    response.headers["ETag"] = format_revision_etag(account.revision)
    return account


@router.patch(
    "/test-accounts/{accountId}",
    response_model=TestAccount,
    summary="更新 TestAccount",
)
async def update_test_account(
    account_id: AccountIdPath,
    command: UpdateTestAccount,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    if_match: IfMatchHeader,
) -> TestAccount:
    account = await service.update_account(
        actor,
        account_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(account.revision)
    return account


@router.post(
    "/test-accounts/{accountId}:quarantine",
    response_model=TestAccount,
    summary="隔离 TestAccount",
)
async def quarantine_test_account(
    account_id: AccountIdPath,
    command: AccountStateReason,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    if_match: IfMatchHeader,
) -> TestAccount:
    account = await service.quarantine_account(
        actor,
        account_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(account.revision)
    return account


@router.post(
    "/test-accounts/{accountId}:restore",
    response_model=TestAccount,
    summary="恢复 TestAccount",
)
async def restore_test_account(
    account_id: AccountIdPath,
    command: AccountStateReason,
    response: Response,
    actor: ActorDependency,
    service: IdentityServiceDependency,
    if_match: IfMatchHeader,
) -> TestAccount:
    account = await service.restore_account(
        actor,
        account_id,
        command,
        expected_revision=parse_revision_etag(if_match),
    )
    response.headers["ETag"] = format_revision_etag(account.revision)
    return account
