"""Task Schedule catalog, desired-state commands, and durable sync facts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    RaiseException,
    UniqueViolation,
)
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import decode_cursor, next_time_cursor
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.task import (
    CreateTaskSchedule,
    RequestTaskSchedulePause,
    RequestTaskScheduleResume,
    TaskSchedule,
    TaskSchedulePage,
    TaskScheduleStatus,
    TaskScheduleSyncStatus,
    next_task_schedule_fire_times,
    task_schedule_content_digest,
    task_schedule_temporal_id,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository
from atlas_testops.infrastructure.repositories.task_schedules import (
    TaskScheduleRepository,
)

TASK_SCHEDULE_IDEMPOTENCY_TTL = timedelta(hours=24)


class TaskScheduleService:
    """Persist Schedule intent without holding a transaction across Temporal RPC."""

    def __init__(
        self,
        database: Database,
        *,
        temporal_namespace: str,
        schedule_repository: TaskScheduleRepository | None = None,
        task_repository: TaskRunRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
    ) -> None:
        self._database = database
        self._temporal_namespace = temporal_namespace
        self._schedules = schedule_repository or TaskScheduleRepository()
        self._tasks = task_repository or TaskRunRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()

    async def create(
        self,
        actor: ActorContext,
        task_plan_version_id: UUID,
        command: CreateTaskSchedule,
        *,
        idempotency_key: str,
    ) -> CommandResult[TaskSchedule]:
        """Create one active desired Schedule and its first sync intent."""

        _require_matching_key(idempotency_key, command.client_mutation_id)
        request_payload: dict[str, JsonValue] = {
            "taskPlanVersionId": str(task_plan_version_id),
            **_json_object(command),
        }
        request_hash = hash_request(request_payload)
        scope = f"task-plan-versions.{task_plan_version_id}.schedules.create"
        try:
            async with self._database.transaction(actor.database_context()) as connection:
                version = await self._tasks.get_task_plan_version(
                    connection,
                    task_plan_version_id,
                )
                if (
                    version is None
                    or version.tenant_id != actor.tenant_id
                    or not actor.can_read_project(version.project_id)
                ):
                    raise _not_found()
                operator_id = _require_operator(actor, version.project_id)
                now = await _database_now(connection)
                reservation = await self._idempotency.reserve(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    now=now,
                    ttl=TASK_SCHEDULE_IDEMPOTENCY_TTL,
                )
                if reservation.cached_response is not None:
                    return CommandResult(
                        value=TaskSchedule.model_validate(reservation.cached_response.body),
                        status_code=reservation.cached_response.status_code,
                        replayed=True,
                    )
                try:
                    next_fires = next_task_schedule_fire_times(
                        command.calendar,
                        time_zone_name=command.time_zone_name,
                        after=now,
                    )
                except ValueError as error:
                    raise _invalid(str(error)) from error
                schedule_id = new_entity_id()
                content_digest = task_schedule_content_digest(
                    schedule_id=schedule_id,
                    tenant_id=version.tenant_id,
                    project_id=version.project_id,
                    task_plan_version_id=version.id,
                    schedule_key=command.schedule_key,
                    name=command.name,
                    calendar=command.calendar,
                    time_zone_name=command.time_zone_name,
                    overlap_policy=command.overlap_policy,
                    catchup_policy=command.catchup_policy,
                    catchup_window_seconds=command.catchup_window_seconds,
                    jitter_seconds=command.jitter_seconds,
                    iteration_id=command.iteration_id,
                    retry_policy=command.retry_policy,
                    temporal_namespace=self._temporal_namespace,
                )
                schedule = TaskSchedule(
                    id=schedule_id,
                    tenant_id=version.tenant_id,
                    project_id=version.project_id,
                    task_plan_version_id=version.id,
                    schedule_key=command.schedule_key,
                    name=command.name,
                    calendar=command.calendar,
                    time_zone_name=command.time_zone_name,
                    overlap_policy=command.overlap_policy,
                    catchup_policy=command.catchup_policy,
                    catchup_window_seconds=command.catchup_window_seconds,
                    jitter_seconds=command.jitter_seconds,
                    iteration_id=command.iteration_id,
                    retry_policy=command.retry_policy,
                    temporal_namespace=self._temporal_namespace,
                    temporal_schedule_id=task_schedule_temporal_id(
                        version.tenant_id,
                        schedule_id,
                    ),
                    content_digest=content_digest,
                    status=TaskScheduleStatus.ACTIVE,
                    pause_reason=None,
                    sync_status=TaskScheduleSyncStatus.PENDING,
                    synced_revision=None,
                    last_sync_error_code=None,
                    next_fire_times_utc=next_fires,
                    created_by=operator_id,
                    updated_by=operator_id,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
                stored = await self._schedules.create(connection, schedule)
                await self._schedules.insert_sync_intent(
                    connection,
                    intent_id=new_entity_id(),
                    schedule=stored,
                    action="CREATE",
                    created_at=now,
                )
                await self._record_event(
                    connection,
                    actor=actor,
                    schedule=stored,
                    event_type="task_schedule.created",
                    occurred_at=now,
                )
                response = CachedHttpResponse(
                    status_code=201,
                    body=_json_object(stored),
                )
                await self._idempotency.complete(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    response=response,
                )
                return CommandResult(value=stored, status_code=201, replayed=False)
        except ApplicationError:
            raise
        except (CheckViolation, ForeignKeyViolation, RaiseException, UniqueViolation) as error:
            raise _conflict(
                "Schedule Key、TaskPlanVersion、Environment 或策略未通过数据库门禁。"
            ) from error

    async def get(
        self,
        actor: ActorContext,
        schedule_id: UUID,
    ) -> TaskSchedule:
        """Read one current desired/synchronized Schedule projection."""

        async with self._database.transaction(actor.database_context()) as connection:
            schedule = await self._schedules.get(connection, schedule_id)
            return _require_visible(actor, schedule)

    async def list_for_version(
        self,
        actor: ActorContext,
        task_plan_version_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> TaskSchedulePage:
        """List schedules under one exact immutable Plan Version."""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            version = await self._tasks.get_task_plan_version(
                connection,
                task_plan_version_id,
            )
            if (
                version is None
                or version.tenant_id != actor.tenant_id
                or not actor.can_read_project(version.project_id)
            ):
                raise _not_found()
            records = await self._schedules.list_for_version(
                connection,
                task_plan_version_id=task_plan_version_id,
                cursor=decoded,
                limit=limit + 1,
            )
        items = records[:limit]
        next_cursor = (
            next_time_cursor(items[-1].updated_at, items[-1].id)
            if len(records) > limit and items
            else None
        )
        return TaskSchedulePage(items=items, next_cursor=next_cursor)

    async def pause(
        self,
        actor: ActorContext,
        schedule_id: UUID,
        command: RequestTaskSchedulePause,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[TaskSchedule]:
        """Pause only future actions and enqueue exact desired revision sync."""

        return await self._transition(
            actor,
            schedule_id,
            command=command,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            old_status=TaskScheduleStatus.ACTIVE,
            new_status=TaskScheduleStatus.PAUSED,
            action="PAUSE",
            pause_reason=command.reason,
        )

    async def resume(
        self,
        actor: ActorContext,
        schedule_id: UUID,
        command: RequestTaskScheduleResume,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[TaskSchedule]:
        """Resume future actions without touching already existing TaskRuns."""

        return await self._transition(
            actor,
            schedule_id,
            command=command,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            old_status=TaskScheduleStatus.PAUSED,
            new_status=TaskScheduleStatus.ACTIVE,
            action="RESUME",
            pause_reason=None,
        )

    async def _transition(
        self,
        actor: ActorContext,
        schedule_id: UUID,
        *,
        command: RequestTaskSchedulePause | RequestTaskScheduleResume,
        expected_revision: int,
        idempotency_key: str,
        old_status: TaskScheduleStatus,
        new_status: TaskScheduleStatus,
        action: str,
        pause_reason: str | None,
    ) -> CommandResult[TaskSchedule]:
        _require_matching_key(idempotency_key, command.client_mutation_id)
        request_payload: dict[str, JsonValue] = {
            "taskScheduleId": str(schedule_id),
            "expectedRevision": expected_revision,
            "action": action,
            **_json_object(command),
        }
        request_hash = hash_request(request_payload)
        scope = f"task-schedules.{schedule_id}.commands"
        try:
            async with self._database.transaction(actor.database_context()) as connection:
                current = _require_visible(
                    actor,
                    await self._schedules.get(
                        connection,
                        schedule_id,
                        for_update=True,
                    ),
                )
                operator_id = _require_operator(actor, current.project_id)
                now = await _database_now(connection)
                reservation = await self._idempotency.reserve(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    now=now,
                    ttl=TASK_SCHEDULE_IDEMPOTENCY_TTL,
                )
                if reservation.cached_response is not None:
                    return CommandResult(
                        value=TaskSchedule.model_validate(reservation.cached_response.body),
                        status_code=reservation.cached_response.status_code,
                        replayed=True,
                    )
                if current.revision != expected_revision:
                    raise _precondition_failed()
                if current.status is not old_status:
                    raise _conflict(f"Schedule 只有处于 {old_status.value} 时才能执行 {action}。")
                updated = await self._schedules.transition_status(
                    connection,
                    schedule_id=schedule_id,
                    expected_revision=expected_revision,
                    old_status=old_status,
                    new_status=new_status,
                    pause_reason=pause_reason,
                    updated_by=operator_id,
                    updated_at=now,
                )
                if updated is None:
                    raise _precondition_failed()
                await self._schedules.insert_sync_intent(
                    connection,
                    intent_id=new_entity_id(),
                    schedule=updated,
                    action=action,
                    created_at=now,
                )
                await self._record_event(
                    connection,
                    actor=actor,
                    schedule=updated,
                    event_type=f"task_schedule.{action.lower()}_requested",
                    occurred_at=now,
                )
                response = CachedHttpResponse(
                    status_code=202,
                    body=_json_object(updated),
                )
                await self._idempotency.complete(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    response=response,
                )
                return CommandResult(value=updated, status_code=202, replayed=False)
        except ApplicationError:
            raise
        except (CheckViolation, ForeignKeyViolation, RaiseException, UniqueViolation) as error:
            raise _conflict("Schedule desired-state command 未通过数据库门禁。") from error

    async def _record_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        schedule: TaskSchedule,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "taskScheduleId": str(schedule.id),
            "taskPlanVersionId": str(schedule.task_plan_version_id),
            "scheduleKey": schedule.schedule_key,
            "status": schedule.status.value,
            "syncStatus": schedule.sync_status.value,
            "revision": schedule.revision,
            "contentDigest": schedule.content_digest,
            "pauseReason": schedule.pause_reason,
        }
        await self._audit.append(
            connection,
            tenant_id=schedule.tenant_id,
            project_id=schedule.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="task_schedule",
            entity_id=schedule.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=schedule.tenant_id,
                aggregate_type="task_schedule",
                aggregate_id=schedule.id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )


async def _database_now(connection: AsyncConnection[DictRow]) -> datetime:
    cursor = await connection.execute("select transaction_timestamp() as observed_at")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("database transaction timestamp is unavailable")
    return datetime.fromisoformat(str(row["observed_at"]))


def _require_operator(actor: ActorContext, project_id: UUID) -> UUID:
    if actor.actor_id is None or not actor.can_operate_project(project_id):
        raise _forbidden()
    return actor.actor_id


def _require_visible(
    actor: ActorContext,
    schedule: TaskSchedule | None,
) -> TaskSchedule:
    if (
        schedule is None
        or schedule.tenant_id != actor.tenant_id
        or not actor.can_read_project(schedule.project_id)
    ):
        raise _not_found()
    return schedule


def _require_matching_key(idempotency_key: str, client_mutation_id: str) -> None:
    if idempotency_key != client_mutation_id:
        raise _invalid("Idempotency-Key 必须与 clientMutationId 完全一致。")


def _json_object(value: object) -> dict[str, JsonValue]:
    payload = cast(
        dict[str, JsonValue],
        value.model_dump(mode="json", by_alias=True),  # type: ignore[attr-defined]
    )
    return payload


def _invalid(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="Task Schedule 请求无效",
        detail=detail,
        status_code=400,
    )


def _not_found() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="Task Schedule 不存在",
        detail="未找到可访问的 Task Schedule 或 TaskPlanVersion。",
        status_code=404,
    )


def _forbidden() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="没有 Task Schedule 权限",
        detail="当前角色不能管理该 Project 的 Task Schedule。",
        status_code=403,
    )


def _conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="Task Schedule 冲突",
        detail=detail,
        status_code=409,
    )


def _precondition_failed() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.PRECONDITION_FAILED,
        title="Task Schedule Revision 已变化",
        detail="请刷新 Schedule 状态并使用最新 ETag 重试。",
        status_code=412,
    )


__all__ = ["TASK_SCHEDULE_IDEMPOTENCY_TTL", "TaskScheduleService"]
