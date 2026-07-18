"""Public asynchronous UnitAttempt live-control API."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Path, Response, status

from atlas_testops.api.dependencies import LiveControlServiceDependency
from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.api.security import ActorDependency
from atlas_testops.core.concurrency import (
    format_control_epoch_etag,
    parse_control_epoch_etag,
)
from atlas_testops.domain.runtime import (
    LiveControlCommand,
    RequestLiveControl,
    UnitAttemptLiveSnapshot,
)

AttemptIdPath = Annotated[UUID, Path(alias="attemptId")]
CommandIdPath = Annotated[UUID, Path(alias="commandId")]
IfMatchHeader = Annotated[str, Header(alias="If-Match", max_length=80)]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]

router = APIRouter(
    responses={
        401: {"description": "缺少有效身份", "model": ProblemDetails},
        404: {"description": "UnitAttempt LiveSession 不存在或不可见", "model": ProblemDetails},
    }
)
COMMAND_RESPONSES: dict[int | str, dict[str, object]] = {
    403: {"description": "当前角色或 Environment 不能控制该 UnitAttempt"},
    409: {"description": "Control Epoch、Lease、Safe Point 或状态冲突"},
    422: {"description": "控制命令不符合契约"},
}


@router.get(
    "/unit-attempts/{attemptId}/snapshot",
    response_model=UnitAttemptLiveSnapshot,
    summary="读取正式 UnitAttempt LiveSnapshot",
)
async def get_unit_attempt_live_snapshot(
    attempt_id: AttemptIdPath,
    response: Response,
    actor: ActorDependency,
    service: LiveControlServiceDependency,
) -> UnitAttemptLiveSnapshot:
    snapshot = await service.get_snapshot(actor, attempt_id)
    _set_snapshot_headers(response, snapshot)
    return snapshot


@router.post(
    "/unit-attempts/{attemptId}/pause",
    response_model=LiveControlCommand,
    status_code=status.HTTP_202_ACCEPTED,
    summary="请求 UnitAttempt 在 Action Safe Point 暂停",
    responses=COMMAND_RESPONSES,
)
async def pause_unit_attempt(
    attempt_id: AttemptIdPath,
    command: RequestLiveControl,
    response: Response,
    actor: ActorDependency,
    service: LiveControlServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> LiveControlCommand:
    result = await service.pause(
        actor,
        attempt_id,
        command,
        expected_control_epoch=parse_control_epoch_etag(if_match),
        idempotency_key=idempotency_key,
    )
    _set_command_headers(response, result.value, result.replayed)
    return result.value


@router.post(
    "/unit-attempts/{attemptId}/resume",
    response_model=LiveControlCommand,
    status_code=status.HTTP_202_ACCEPTED,
    summary="请求恢复 Agent controller",
    responses=COMMAND_RESPONSES,
)
async def resume_unit_attempt(
    attempt_id: AttemptIdPath,
    command: RequestLiveControl,
    response: Response,
    actor: ActorDependency,
    service: LiveControlServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> LiveControlCommand:
    result = await service.resume(
        actor,
        attempt_id,
        command,
        expected_control_epoch=parse_control_epoch_etag(if_match),
        idempotency_key=idempotency_key,
    )
    _set_command_headers(response, result.value, result.replayed)
    return result.value


@router.post(
    "/unit-attempts/{attemptId}/takeover",
    response_model=LiveControlCommand,
    status_code=status.HTTP_202_ACCEPTED,
    summary="请求 quiesce 后安全交接给 Human controller",
    responses=COMMAND_RESPONSES,
)
async def takeover_unit_attempt(
    attempt_id: AttemptIdPath,
    command: RequestLiveControl,
    response: Response,
    actor: ActorDependency,
    service: LiveControlServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> LiveControlCommand:
    result = await service.takeover(
        actor,
        attempt_id,
        command,
        expected_control_epoch=parse_control_epoch_etag(if_match),
        idempotency_key=idempotency_key,
    )
    _set_command_headers(response, result.value, result.replayed)
    return result.value


@router.post(
    "/unit-attempts/{attemptId}/return",
    response_model=LiveControlCommand,
    status_code=status.HTTP_202_ACCEPTED,
    summary="请求 reconcile 后交还 Agent controller",
    responses=COMMAND_RESPONSES,
)
async def return_unit_attempt_control(
    attempt_id: AttemptIdPath,
    command: RequestLiveControl,
    response: Response,
    actor: ActorDependency,
    service: LiveControlServiceDependency,
    if_match: IfMatchHeader,
    idempotency_key: IdempotencyKeyHeader,
) -> LiveControlCommand:
    result = await service.return_control(
        actor,
        attempt_id,
        command,
        expected_control_epoch=parse_control_epoch_etag(if_match),
        idempotency_key=idempotency_key,
    )
    _set_command_headers(response, result.value, result.replayed)
    return result.value


@router.get(
    "/unit-attempts/{attemptId}/commands/{commandId}",
    response_model=LiveControlCommand,
    summary="读取 LiveControlCommand 状态",
)
async def get_unit_attempt_live_command(
    attempt_id: AttemptIdPath,
    command_id: CommandIdPath,
    actor: ActorDependency,
    service: LiveControlServiceDependency,
) -> LiveControlCommand:
    return await service.get_command(
        actor,
        unit_attempt_id=attempt_id,
        command_id=command_id,
    )


def _set_snapshot_headers(
    response: Response,
    snapshot: UnitAttemptLiveSnapshot,
) -> None:
    response.headers["ETag"] = format_control_epoch_etag(
        snapshot.session.control_epoch
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


def _set_command_headers(
    response: Response,
    command: LiveControlCommand,
    replayed: bool,
) -> None:
    response.headers["ETag"] = format_control_epoch_etag(
        command.expected_control_epoch
    )
    response.headers["Location"] = (
        f"/v1/unit-attempts/{command.unit_attempt_id}/commands/{command.id}"
    )
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
__all__ = ["router"]
