"""Test account health verification and state history API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Query, Response, status

from atlas_testops.api.dependencies import AccountHealthServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import format_revision_etag, parse_revision_etag
from atlas_testops.domain.identity import (
    AccountHealthCheckPage,
    AccountHealthVerification,
    AccountStateTransitionPage,
    VerifyTestAccount,
)

IdempotencyKeyHeader = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=200),
]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=64)]
AccountIdPath = Annotated[UUID, Path(alias="accountId")]
CursorQuery = Annotated[str | None, Query(max_length=512)]
LimitQuery = Annotated[int, Query(ge=1, le=100)]

router = APIRouter(
    responses={
        400: {"description": "请求语义或 Revision 无效", "model": ProblemDetails},
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        403: {"description": "权限、Production 或 Origin 策略拒绝", "model": ProblemDetails},
        404: {"description": "TestAccount 不存在或不可见", "model": ProblemDetails},
        409: {"description": "账号状态、Lease 或并发检查冲突", "model": ProblemDetails},
        412: {"description": "TestAccount Revision 已变化", "model": ProblemDetails},
        503: {"description": "Secret Provider、Adapter 或能力不可用", "model": ProblemDetails},
    }
)


@router.post(
    "/test-accounts/{accountId}:verify",
    response_model=AccountHealthVerification,
    status_code=status.HTTP_201_CREATED,
    summary="验证 TestAccount 登录身份与角色健康",
)
async def verify_test_account(
    account_id: AccountIdPath,
    command: VerifyTestAccount,
    response: Response,
    actor: ActorDependency,
    service: AccountHealthServiceDependency,
    idempotency_key: IdempotencyKeyHeader,
    if_match: IfMatchHeader,
) -> AccountHealthVerification:
    """Run an out-of-transaction Provider probe and apply it with revision CAS."""

    result = await service.verify(
        actor,
        account_id,
        command,
        expected_revision=parse_revision_etag(if_match),
        idempotency_key=idempotency_key,
    )
    response.status_code = result.status_code
    response.headers["Idempotency-Replayed"] = str(result.replayed).lower()
    response.headers["ETag"] = format_revision_etag(result.value.account.revision)
    response.headers["Location"] = f"/v1/test-accounts/{account_id}/health-checks"
    return result.value


@router.get(
    "/test-accounts/{accountId}/health-checks",
    response_model=AccountHealthCheckPage,
    summary="列出 TestAccount 健康检查",
)
async def list_test_account_health_checks(
    account_id: AccountIdPath,
    actor: ActorDependency,
    service: AccountHealthServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> AccountHealthCheckPage:
    """Return safe summaries and stable classifications without Provider payloads."""

    return await service.list_checks(actor, account_id, cursor=cursor, limit=limit)


@router.get(
    "/test-accounts/{accountId}/state-transitions",
    response_model=AccountStateTransitionPage,
    summary="列出 TestAccount 状态迁移",
)
async def list_test_account_state_transitions(
    account_id: AccountIdPath,
    actor: ActorDependency,
    service: AccountHealthServiceDependency,
    cursor: CursorQuery = None,
    limit: LimitQuery = 25,
) -> AccountStateTransitionPage:
    """Return immutable before-and-after snapshots of orthogonal account state."""

    return await service.list_transitions(
        actor,
        account_id,
        cursor=cursor,
        limit=limit,
    )
