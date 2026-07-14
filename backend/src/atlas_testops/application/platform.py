"""Platform 领域应用服务。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.core.contracts import WireModel, new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import decode_cursor, next_time_cursor
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.identity import AccountLease, LeaseReleaseReason
from atlas_testops.domain.platform import (
    CreateEnvironment,
    CreateProject,
    CreateTenant,
    Environment,
    EnvironmentKind,
    EnvironmentPage,
    EnvironmentStatus,
    Project,
    ProjectPage,
    ProjectStatus,
    Tenant,
    TenantStatus,
    UpdateEnvironment,
    UpdateProject,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.connectors import ConnectorRepository
from atlas_testops.infrastructure.repositories.leases import LeaseRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository

IDEMPOTENCY_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class CommandResult[T]:
    """携带幂等重放信息的命令结果。"""

    value: T
    status_code: int
    replayed: bool


class PlatformService:
    """在单个短事务内协调 Platform 事实、审计和 Outbox。"""

    def __init__(
        self,
        database: Database,
        platform_repository: PlatformRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
        lease_repository: LeaseRepository | None = None,
        connector_repository: ConnectorRepository | None = None,
    ) -> None:
        self._database = database
        self._platform = platform_repository or PlatformRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()
        self._leases = lease_repository or LeaseRepository()
        self._connectors = connector_repository or ConnectorRepository()

    async def bootstrap_tenant(
        self,
        command: CreateTenant,
        *,
        request_id: str,
        actor_id: UUID | None = None,
    ) -> Tenant:
        """创建隔离根；该用例只允许 Development Bootstrap 调用。"""

        tenant_id = new_entity_id()
        now = utc_now()
        context = DatabaseContext(
            tenant_id=tenant_id,
            actor_id=actor_id,
            request_id=request_id,
        )
        async with self._database.transaction(context) as connection:
            tenant = await self._platform.create_tenant(
                connection,
                tenant_id=tenant_id,
                command=command,
            )
            if tenant is None:
                raise self._conflict("Tenant Slug 已存在", "请选择另一个 Tenant Slug。")
            await self._audit.append(
                connection,
                tenant_id=tenant.id,
                project_id=None,
                environment_id=None,
                actor_id=actor_id,
                event_type="tenant.created",
                entity_type="tenant",
                entity_id=tenant.id,
                occurred_at=now,
                payload={"slug": tenant.slug},
                request_id=request_id,
            )
            await self._outbox.append(
                connection,
                DomainEvent(
                    tenant_id=tenant.id,
                    aggregate_type="tenant",
                    aggregate_id=tenant.id,
                    event_type="tenant.created",
                    occurred_at=now,
                    payload={"tenantId": str(tenant.id), "slug": tenant.slug},
                ),
            )
            return tenant

    async def get_current_tenant(self, actor: ActorContext) -> Tenant:
        """读取 Actor 所属 Tenant。"""

        async with self._database.transaction(actor.database_context()) as connection:
            tenant = await self._platform.get_tenant(connection, actor.tenant_id)
            if tenant is None:
                raise self._not_found("Tenant 不存在")
            return tenant

    async def create_project(
        self,
        actor: ActorContext,
        command: CreateProject,
        *,
        idempotency_key: str,
    ) -> CommandResult[Project]:
        """创建 Project，并原子写入 Idempotency、Audit 和 Outbox。"""

        if not actor.can_create_project():
            raise self._forbidden("只有组织管理员可以创建 Project。")
        now = utc_now()
        request_hash = hash_request(command.model_dump(mode="json", by_alias=True))
        async with self._database.transaction(actor.database_context()) as connection:
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope="projects.create",
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=Project.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )

            tenant = await self._platform.get_tenant(connection, actor.tenant_id)
            if tenant is None:
                raise self._not_found("Tenant 不存在")
            if tenant.status is TenantStatus.SUSPENDED:
                raise self._conflict("Tenant 已暂停", "暂停状态下不能创建 Project。")

            project = await self._platform.create_project(
                connection,
                project_id=new_entity_id(),
                tenant_id=actor.tenant_id,
                command=command,
            )
            if project is None:
                raise self._conflict(
                    "Project Key 已存在",
                    "同一 Tenant 内的 Project Key 必须唯一。",
                )
            await self._record_project_event(
                connection,
                actor=actor,
                project=project,
                event_type="project.created",
                occurred_at=now,
            )
            response = CachedHttpResponse(
                status_code=201,
                body=self._json_object(project),
            )
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope="projects.create",
                key=idempotency_key,
                request_hash=request_hash,
                response=response,
            )
            return CommandResult(value=project, status_code=201, replayed=False)

    async def list_projects(
        self,
        actor: ActorContext,
        *,
        cursor: str | None,
        limit: int,
    ) -> ProjectPage:
        """读取 Actor Tenant 内的 Project Cursor Page。"""

        decoded = decode_cursor(cursor)
        allowed_project_ids = actor.visible_project_ids()
        if allowed_project_ids == frozenset():
            return ProjectPage(items=())
        async with self._database.transaction(actor.database_context()) as connection:
            records = await self._platform.list_projects(
                connection,
                cursor=decoded,
                limit=limit,
                allowed_project_ids=allowed_project_ids,
            )
        items = records[:limit]
        next_cursor = None
        if len(records) > limit and items:
            last = items[-1]
            next_cursor = next_time_cursor(last.created_at, last.id)
        return ProjectPage(items=items, next_cursor=next_cursor)

    async def get_project(self, actor: ActorContext, project_id: UUID) -> Project:
        """读取单个 Project。"""

        async with self._database.transaction(actor.database_context()) as connection:
            project = await self._platform.get_project(connection, project_id)
            if project is None or not actor.can_read_project(project.id):
                raise self._not_found("Project 不存在")
            return project

    async def update_project(
        self,
        actor: ActorContext,
        project_id: UUID,
        command: UpdateProject,
        *,
        expected_revision: int,
    ) -> Project:
        """以 Revision CAS 更新 Project，并追加变更事实。"""

        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._platform.get_project(connection, project_id)
            if current is None or not actor.can_read_project(project_id):
                raise self._not_found("Project 不存在")
            if not actor.can_manage_project(project_id):
                raise self._forbidden("当前角色不能修改该 Project。")
            project = await self._platform.update_project(
                connection,
                project_id=project_id,
                expected_revision=expected_revision,
                command=command,
            )
            if project is None:
                raise self._revision_conflict(current.revision)
            await self._record_project_event(
                connection,
                actor=actor,
                project=project,
                event_type="project.updated",
                occurred_at=now,
            )
            return project

    async def create_environment(
        self,
        actor: ActorContext,
        project_id: UUID,
        command: CreateEnvironment,
        *,
        idempotency_key: str,
    ) -> CommandResult[Environment]:
        """在 Project 内创建 Environment。"""

        now = utc_now()
        request_payload = {
            "projectId": str(project_id),
            **command.model_dump(mode="json", by_alias=True),
        }
        request_hash = hash_request(request_payload)
        scope = f"projects.{project_id}.environments.create"
        async with self._database.transaction(actor.database_context()) as connection:
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=Environment.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )

            project = await self._platform.get_project(connection, project_id)
            if project is None or not actor.can_read_project(project_id):
                raise self._not_found("Project 不存在")
            if not actor.can_manage_project(project_id):
                raise self._forbidden("当前角色不能管理该 Project 的 Environment。")
            if project.status is ProjectStatus.ARCHIVED:
                raise self._conflict("Project 已归档", "归档 Project 不能创建 Environment。")

            environment = await self._platform.create_environment(
                connection,
                environment_id=new_entity_id(),
                tenant_id=actor.tenant_id,
                project_id=project_id,
                command=command,
            )
            if environment is None:
                raise self._conflict(
                    "Environment Key 已存在",
                    "同一 Project 内的 Environment Key 必须唯一。",
                )
            await self._record_environment_event(
                connection,
                actor=actor,
                environment=environment,
                event_type="environment.created",
                occurred_at=now,
            )
            response = CachedHttpResponse(status_code=201, body=self._json_object(environment))
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                response=response,
            )
            return CommandResult(value=environment, status_code=201, replayed=False)

    async def list_environments(
        self,
        actor: ActorContext,
        project_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> EnvironmentPage:
        """读取一个 Project 的 Environment Cursor Page。"""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            project = await self._platform.get_project(connection, project_id)
            if project is None or not actor.can_read_project(project_id):
                raise self._not_found("Project 不存在")
            records = await self._platform.list_environments(
                connection,
                project_id=project_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        next_cursor = None
        if len(records) > limit and items:
            last = items[-1]
            next_cursor = next_time_cursor(last.created_at, last.id)
        return EnvironmentPage(items=items, next_cursor=next_cursor)

    async def get_environment(
        self,
        actor: ActorContext,
        environment_id: UUID,
    ) -> Environment:
        """读取单个 Environment。"""

        async with self._database.transaction(actor.database_context()) as connection:
            environment = await self._platform.get_environment(connection, environment_id)
            if environment is None or not actor.can_read_project(environment.project_id):
                raise self._not_found("Environment 不存在")
            return environment

    async def update_environment(
        self,
        actor: ActorContext,
        environment_id: UUID,
        command: UpdateEnvironment,
        *,
        expected_revision: int,
    ) -> Environment:
        """以 Revision CAS 更新 Environment。"""

        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._platform.get_environment_for_update(
                connection,
                environment_id,
            )
            if current is None or not actor.can_read_project(current.project_id):
                raise self._not_found("Environment 不存在")
            if not actor.can_manage_project(current.project_id):
                raise self._forbidden("当前角色不能修改该 Environment。")
            if (
                current.kind is EnvironmentKind.PRODUCTION
                and command.allowed_origins is not None
                and any(
                    not origin.startswith("https://")
                    for origin in command.allowed_origins
                )
            ):
                raise ApplicationError(
                    error_code=ErrorCode.INVALID_REQUEST,
                    title="生产 Origin 必须使用 HTTPS",
                    detail="Production Environment 不允许配置 HTTP Origin。",
                    status_code=400,
                )
            if (
                command.allowed_origins is not None
                and await self._connectors.has_origin_dependency(
                    connection,
                    environment_id=environment_id,
                    allowed_origins=command.allowed_origins,
                )
            ):
                raise self._conflict(
                    "Environment Origin 仍被 Connector 使用",
                    "请先重配置或禁用依赖这些 Origin 的 Connector。",
                )
            environment = await self._platform.update_environment(
                connection,
                environment_id=environment_id,
                expected_revision=expected_revision,
                command=command,
            )
            if environment is None:
                raise self._revision_conflict(current.revision)
            if environment.status is EnvironmentStatus.DISABLED:
                revoked = await self._leases.revoke_active(
                    connection,
                    reason=LeaseReleaseReason.ENVIRONMENT_DISABLED,
                    now=now,
                    environment_id=environment.id,
                )
                await self._record_lease_revocations(
                    connection,
                    actor=actor,
                    leases=revoked,
                    occurred_at=now,
                )
            await self._record_environment_event(
                connection,
                actor=actor,
                environment=environment,
                event_type="environment.updated",
                occurred_at=now,
            )
            return environment

    async def _record_project_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        project: Project,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "projectId": str(project.id),
            "projectKey": project.project_key,
            "revision": project.revision,
            "status": project.status.value,
        }
        await self._audit.append(
            connection,
            tenant_id=actor.tenant_id,
            project_id=project.id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="project",
            entity_id=project.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=actor.tenant_id,
                aggregate_type="project",
                aggregate_id=project.id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    async def _record_environment_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        environment: Environment,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "environmentId": str(environment.id),
            "projectId": str(environment.project_id),
            "revision": environment.revision,
            "status": environment.status.value,
        }
        await self._audit.append(
            connection,
            tenant_id=actor.tenant_id,
            project_id=environment.project_id,
            environment_id=environment.id,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="environment",
            entity_id=environment.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=actor.tenant_id,
                aggregate_type="environment",
                aggregate_id=environment.id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    async def _record_lease_revocations(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        leases: tuple[AccountLease, ...],
        occurred_at: datetime,
    ) -> None:
        for lease in leases:
            if lease.release_reason is None:
                raise RuntimeError("revoked lease is missing a release reason")
            payload: dict[str, JsonValue] = {
                "accountId": str(lease.account_id),
                "executionId": lease.execution_id,
                "fencingToken": lease.fencing_token,
                "releaseReason": lease.release_reason.value,
                "status": lease.status.value,
            }
            await self._audit.append(
                connection,
                tenant_id=actor.tenant_id,
                project_id=lease.project_id,
                environment_id=lease.environment_id,
                actor_id=actor.actor_id,
                event_type="account_lease.revoked",
                entity_type="account_lease",
                entity_id=lease.id,
                occurred_at=occurred_at,
                payload=payload,
                request_id=actor.request_id,
            )
            await self._outbox.append(
                connection,
                DomainEvent(
                    tenant_id=actor.tenant_id,
                    aggregate_type="account_lease",
                    aggregate_id=lease.id,
                    event_type="account_lease.revoked",
                    occurred_at=occurred_at,
                    payload=payload,
                ),
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
    def _conflict(title: str, detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title=title,
            detail=detail,
            status_code=409,
        )

    @staticmethod
    def _forbidden(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.FORBIDDEN,
            title="权限不足",
            detail=detail,
            status_code=403,
        )

    @staticmethod
    def _revision_conflict(current_revision: int) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.PRECONDITION_FAILED,
            title="资源 Revision 已变化",
            detail="请读取最新资源后重新提交变更。",
            status_code=412,
            headers={"ETag": format_revision_etag(current_revision)},
        )
