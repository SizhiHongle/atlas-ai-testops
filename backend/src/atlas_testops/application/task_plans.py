"""Application service for reusable TaskPlan authoring and publication."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import CheckViolation, ForeignKeyViolation, RaiseException
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.contracts import WireModel, new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import decode_cursor, next_time_cursor
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.platform import Project, ProjectStatus
from atlas_testops.domain.task import (
    CreateTaskPlan,
    PublishTaskPlanVersion,
    TaskPlan,
    TaskPlanPage,
    TaskPlanStatus,
    TaskPlanVersion,
    TaskPlanVersionPage,
    task_plan_version_content_digest,
    task_plan_version_ref,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableFactConflictError,
    TaskRunRepository,
)

TASK_PLAN_IDEMPOTENCY_TTL = timedelta(hours=24)


class TaskPlanService:
    """Authorize TaskPlan catalog writes and immutable version publication."""

    def __init__(
        self,
        database: Database,
        *,
        task_repository: TaskRunRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
    ) -> None:
        self._database = database
        self._tasks = task_repository or TaskRunRepository()
        self._platform = platform_repository or PlatformRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()

    async def create(
        self,
        actor: ActorContext,
        project_id: UUID,
        command: CreateTaskPlan,
        *,
        idempotency_key: str,
    ) -> CommandResult[TaskPlan]:
        """Create one stable TaskPlan root in an active Project."""

        self._require_matching_mutation_key(
            idempotency_key,
            command.client_mutation_id,
        )
        now = utc_now()
        request_payload: dict[str, JsonValue] = {
            "projectId": str(project_id),
            **self._json_object(command),
        }
        request_hash = hash_request(request_payload)
        scope = f"projects.{project_id}.task-plans.create"
        async with self._database.transaction(actor.database_context()) as connection:
            project = await self._require_project(
                connection,
                actor,
                project_id,
                for_share=True,
            )
            operator_id = self._require_operator(actor, project.id)
            self._require_active_project(project)
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=TASK_PLAN_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=TaskPlan.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )

            plan = TaskPlan(
                id=new_entity_id(),
                tenant_id=actor.tenant_id,
                project_id=project.id,
                task_key=command.task_key,
                name=command.name,
                status=TaskPlanStatus.ACTIVE,
                created_by=operator_id,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            try:
                result = await self._tasks.create_task_plan(connection, plan)
            except ImmutableFactConflictError as error:
                raise self._conflict(
                    "同一 Project 内的 taskKey 已存在，或该标识保存了不同内容。"
                ) from error
            stored = result.fact
            await self._record_event(
                connection,
                actor=actor,
                aggregate_id=stored.id,
                aggregate_type="task_plan",
                event_type="task_plan.created",
                project_id=stored.project_id,
                occurred_at=now,
                payload={
                    "projectId": str(stored.project_id),
                    "taskKey": stored.task_key,
                    "name": stored.name,
                    "status": stored.status.value,
                },
            )
            response = CachedHttpResponse(
                status_code=201,
                body=self._json_object(stored),
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

    async def list_for_project(
        self,
        actor: ActorContext,
        project_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> TaskPlanPage:
        """List the visible TaskPlan catalog with stable keyset pagination."""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_project(connection, actor, project_id)
            records = await self._tasks.list_task_plans(
                connection,
                project_id=project_id,
                cursor=decoded,
                limit=limit + 1,
            )
        next_cursor = (
            next_time_cursor(records[limit - 1].updated_at, records[limit - 1].id)
            if len(records) > limit
            else None
        )
        return TaskPlanPage(items=records[:limit], next_cursor=next_cursor)

    async def get(self, actor: ActorContext, task_plan_id: UUID) -> TaskPlan:
        """Read one TaskPlan without leaking cross-tenant identities."""

        async with self._database.transaction(actor.database_context()) as connection:
            return await self._require_plan(connection, actor, task_plan_id)

    async def publish_version(
        self,
        actor: ActorContext,
        task_plan_id: UUID,
        command: PublishTaskPlanVersion,
        *,
        idempotency_key: str,
    ) -> CommandResult[TaskPlanVersion]:
        """Publish an immutable exact TaskPlanVersion under database validation."""

        self._require_matching_mutation_key(
            idempotency_key,
            command.client_mutation_id,
        )
        now = utc_now()
        request_payload: dict[str, JsonValue] = {
            "taskPlanId": str(task_plan_id),
            **self._json_object(command),
        }
        request_hash = hash_request(request_payload)
        scope = f"task-plans.{task_plan_id}.versions.publish"
        try:
            async with self._database.transaction(actor.database_context()) as connection:
                candidate = await self._require_plan(connection, actor, task_plan_id)
                project = await self._require_project(
                    connection,
                    actor,
                    candidate.project_id,
                    for_share=True,
                )
                plan = await self._require_plan(
                    connection,
                    actor,
                    task_plan_id,
                    for_share=True,
                )
                publisher_id = self._require_operator(actor, plan.project_id)
                self._require_active_project(project)
                if plan.status is not TaskPlanStatus.ACTIVE:
                    raise self._conflict("已归档 TaskPlan 不能发布新版本。")

                reservation = await self._idempotency.reserve(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    now=now,
                    ttl=TASK_PLAN_IDEMPOTENCY_TTL,
                )
                if reservation.cached_response is not None:
                    return CommandResult(
                        value=TaskPlanVersion.model_validate(
                            reservation.cached_response.body
                        ),
                        status_code=reservation.cached_response.status_code,
                        replayed=True,
                    )

                content_digest = task_plan_version_content_digest(
                    tenant_id=plan.tenant_id,
                    project_id=plan.project_id,
                    task_plan_id=plan.id,
                    version=command.version,
                    pinned_case_version_ids=command.pinned_case_version_ids,
                    matrix=command.matrix,
                    profile_refs=command.profile_refs,
                    policy_digests=command.policy_digests,
                )
                version = TaskPlanVersion(
                    id=new_entity_id(),
                    tenant_id=plan.tenant_id,
                    project_id=plan.project_id,
                    task_plan_id=plan.id,
                    version=command.version,
                    version_ref=task_plan_version_ref(plan.id, command.version),
                    pinned_case_version_ids=command.pinned_case_version_ids,
                    matrix=command.matrix,
                    profile_refs=command.profile_refs,
                    policy_digests=command.policy_digests,
                    content_digest=content_digest,
                    published_by=publisher_id,
                    published_at=now,
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
                try:
                    result = await self._tasks.create_task_plan_version(
                        connection,
                        version,
                    )
                except ImmutableFactConflictError as error:
                    raise self._conflict(
                        "该 TaskPlan 版本号已存在，或不可变标识保存了不同内容。"
                    ) from error
                stored = result.fact
                await self._record_event(
                    connection,
                    actor=actor,
                    aggregate_id=stored.id,
                    aggregate_type="task_plan_version",
                    event_type="task_plan_version.published",
                    project_id=stored.project_id,
                    occurred_at=now,
                    payload={
                        "taskPlanId": str(stored.task_plan_id),
                        "taskPlanVersionId": str(stored.id),
                        "version": stored.version,
                        "versionRef": stored.version_ref,
                        "contentDigest": stored.content_digest,
                        "caseCount": len(stored.pinned_case_version_ids),
                    },
                )
                response = CachedHttpResponse(
                    status_code=201,
                    body=self._json_object(stored),
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
        except (CheckViolation, ForeignKeyViolation, RaiseException) as error:
            raise self._conflict(
                "TaskPlanVersion 的 Case、Profile、Fixture、Environment "
                "或策略引用未通过发布门禁。"
            ) from error

    async def list_versions(
        self,
        actor: ActorContext,
        task_plan_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> TaskPlanVersionPage:
        """List immutable TaskPlanVersion history with keyset pagination."""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_plan(connection, actor, task_plan_id)
            records = await self._tasks.list_task_plan_versions(
                connection,
                task_plan_id=task_plan_id,
                cursor=decoded,
                limit=limit + 1,
            )
        next_cursor = (
            next_time_cursor(records[limit - 1].published_at, records[limit - 1].id)
            if len(records) > limit
            else None
        )
        return TaskPlanVersionPage(items=records[:limit], next_cursor=next_cursor)

    async def get_version(
        self,
        actor: ActorContext,
        task_plan_version_id: UUID,
    ) -> TaskPlanVersion:
        """Read one exact TaskPlanVersion and verify parent visibility."""

        async with self._database.transaction(actor.database_context()) as connection:
            version = await self._tasks.get_task_plan_version(
                connection,
                task_plan_version_id,
            )
            if version is None or not actor.can_read_project(version.project_id):
                raise self._not_found("TaskPlanVersion 不存在。")
            plan = await self._tasks.get_task_plan(connection, version.task_plan_id)
            if (
                plan is None
                or plan.tenant_id != version.tenant_id
                or plan.project_id != version.project_id
            ):
                raise self._not_found("TaskPlanVersion 不存在。")
            return version

    async def _require_project(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        project_id: UUID,
        *,
        for_share: bool = False,
    ) -> Project:
        if for_share:
            project = await self._platform.get_project_for_share(
                connection,
                project_id,
            )
        else:
            project = await self._platform.get_project(
                connection,
                project_id,
            )
        if project is None or not actor.can_read_project(project_id):
            raise self._not_found("Project 不存在。")
        return project

    async def _require_plan(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        task_plan_id: UUID,
        *,
        for_share: bool = False,
    ) -> TaskPlan:
        plan = await self._tasks.get_task_plan(
            connection,
            task_plan_id,
            for_share=for_share,
        )
        if plan is None or not actor.can_read_project(plan.project_id):
            raise self._not_found("TaskPlan 不存在。")
        return plan

    async def _record_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        aggregate_id: UUID,
        aggregate_type: str,
        event_type: str,
        project_id: UUID,
        occurred_at: datetime,
        payload: dict[str, JsonValue],
    ) -> None:
        await self._audit.append(
            connection,
            tenant_id=actor.tenant_id,
            project_id=project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type=aggregate_type,
            entity_id=aggregate_id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=actor.tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    @staticmethod
    def _require_operator(actor: ActorContext, project_id: UUID) -> UUID:
        if not actor.can_operate_project(project_id):
            raise TaskPlanService._forbidden(
                "当前角色没有该 Project 的 TaskPlan 编写权限。"
            )
        if actor.actor_id is None:
            raise TaskPlanService._forbidden("TaskPlan 写操作需要可审计的 Actor。")
        return actor.actor_id

    @staticmethod
    def _require_active_project(project: Project) -> None:
        if project.status is not ProjectStatus.ACTIVE:
            raise TaskPlanService._conflict(
                "只有活动 Project 可以创建或发布 TaskPlan。"
            )

    @staticmethod
    def _require_matching_mutation_key(header: str, command_key: str) -> None:
        if header != command_key:
            raise ApplicationError(
                error_code=ErrorCode.INVALID_REQUEST,
                title="幂等标识不一致",
                detail="Idempotency-Key 必须与 clientMutationId 完全一致。",
                status_code=400,
            )

    @staticmethod
    def _json_object(model: WireModel) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], model.model_dump(mode="json", by_alias=True))

    @staticmethod
    def _not_found(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="资源不存在",
            detail=detail,
            status_code=404,
        )

    @staticmethod
    def _forbidden(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.FORBIDDEN,
            title="TaskPlan 操作被拒绝",
            detail=detail,
            status_code=403,
        )

    @staticmethod
    def _conflict(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title="TaskPlan 操作冲突",
            detail=detail,
            status_code=409,
        )
