"""测试身份目录、账号池与账号资源应用服务。"""

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
from atlas_testops.domain.identity import (
    AccountHealth,
    AccountLease,
    AccountLifecycle,
    AccountPool,
    AccountPoolCapacity,
    AccountPoolPage,
    AccountPoolStatus,
    AccountSource,
    AccountStateReason,
    AccountStateTransitionReason,
    ConnectorInstallationRecord,
    ConnectorMode,
    ConnectorStatus,
    CreateAccountPool,
    CreateTestAccount,
    CreateTestRole,
    CredentialAuthMethod,
    LeaseReleaseReason,
    ProviderCapability,
    TestAccount,
    TestAccountPage,
    TestRole,
    TestRolePage,
    TestRoleStatus,
    UpdateAccountPool,
    UpdateTestAccount,
    UpdateTestRole,
)
from atlas_testops.domain.platform import Environment, Project
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.account_health import (
    AccountHealthRepository,
    AccountStateRecord,
)
from atlas_testops.infrastructure.repositories.connectors import ConnectorRepository
from atlas_testops.infrastructure.repositories.identity import IdentityRepository
from atlas_testops.infrastructure.repositories.leases import LeaseRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository

IDENTITY_IDEMPOTENCY_TTL = timedelta(hours=24)
type CursorEntity = TestRole | AccountPool | TestAccount


@dataclass(frozen=True, slots=True)
class IdentityCommandResult[T]:
    """携带身份管理命令的幂等重放信息。"""

    value: T
    status_code: int
    replayed: bool


class IdentityService:
    """协调身份目录权限、持久化、审计、Outbox 与幂等。"""

    def __init__(
        self,
        database: Database,
        identity_repository: IdentityRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
        lease_repository: LeaseRepository | None = None,
        connector_repository: ConnectorRepository | None = None,
        account_health_repository: AccountHealthRepository | None = None,
    ) -> None:
        self._database = database
        self._identity = identity_repository or IdentityRepository()
        self._platform = platform_repository or PlatformRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()
        self._leases = lease_repository or LeaseRepository()
        self._connectors = connector_repository or ConnectorRepository()
        self._account_health = account_health_repository or AccountHealthRepository()

    async def create_role(
        self,
        actor: ActorContext,
        project_id: UUID,
        command: CreateTestRole,
        *,
        idempotency_key: str,
    ) -> IdentityCommandResult[TestRole]:
        """幂等创建用例可引用的业务角色。"""

        now = utc_now()
        request_hash = hash_request(command.model_dump(mode="json", by_alias=True))
        scope = f"projects.{project_id}.test-roles.create"
        async with self._database.transaction(actor.database_context()) as connection:
            project = await self._require_project(connection, actor, project_id, manage=True)
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=IDENTITY_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return IdentityCommandResult(
                    value=TestRole.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            role = await self._identity.create_role(
                connection,
                role_id=new_entity_id(),
                tenant_id=actor.tenant_id,
                project_id=project.id,
                command=command,
            )
            if role is None:
                raise self._conflict(
                    "Role Key 已存在",
                    "同一 Project 内的 TestRole roleKey 必须唯一。",
                )
            await self._record_event(
                connection,
                actor=actor,
                project_id=project.id,
                environment_id=None,
                entity_type="test_role",
                entity_id=role.id,
                event_type="test_role.created",
                payload={
                    "roleKey": role.role_key,
                    "capabilities": list(role.capabilities),
                },
                occurred_at=now,
            )
            await self._complete_idempotency(
                connection,
                actor=actor,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                value=role,
                status_code=201,
            )
            return IdentityCommandResult(value=role, status_code=201, replayed=False)

    async def list_roles(
        self,
        actor: ActorContext,
        project_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> TestRolePage:
        """列出当前 Actor 可见 Project 的 TestRole。"""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_project(connection, actor, project_id, manage=False)
            records = await self._identity.list_roles(
                connection,
                project_id=project_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        return TestRolePage(
            items=items,
            next_cursor=self._next_cursor(items, has_more=len(records) > limit),
        )

    async def get_role(self, actor: ActorContext, role_id: UUID) -> TestRole:
        """读取单个 TestRole，并隐藏无权访问的 Project。"""

        async with self._database.transaction(actor.database_context()) as connection:
            role = await self._identity.get_role(connection, role_id)
            if role is None or not actor.can_read_project(role.project_id):
                raise self._not_found("TestRole 不存在。")
            return role

    async def update_role(
        self,
        actor: ActorContext,
        role_id: UUID,
        command: UpdateTestRole,
        *,
        expected_revision: int,
    ) -> TestRole:
        """以 Revision CAS 更新 TestRole。"""

        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._identity.get_role(connection, role_id)
            if current is None or not actor.can_read_project(current.project_id):
                raise self._not_found("TestRole 不存在。")
            if not actor.can_manage_project(current.project_id):
                raise self._forbidden("当前角色不能修改 TestRole。")
            role = await self._identity.update_role(
                connection,
                role_id=role_id,
                expected_revision=expected_revision,
                command=command,
            )
            if role is None:
                raise self._revision_conflict(current.revision)
            if role.status is TestRoleStatus.DISABLED:
                revoked = await self._leases.revoke_active(
                    connection,
                    reason=LeaseReleaseReason.ROLE_DISABLED,
                    now=now,
                    role_id=role.id,
                )
                await self._record_lease_revocations(
                    connection,
                    actor=actor,
                    leases=revoked,
                    occurred_at=now,
                )
            await self._record_event(
                connection,
                actor=actor,
                project_id=role.project_id,
                environment_id=None,
                entity_type="test_role",
                entity_id=role.id,
                event_type="test_role.updated",
                payload={"revision": role.revision, "status": role.status.value},
                occurred_at=now,
            )
            return role

    async def create_pool(
        self,
        actor: ActorContext,
        environment_id: UUID,
        command: CreateAccountPool,
        *,
        idempotency_key: str,
    ) -> IdentityCommandResult[AccountPool]:
        """幂等创建 Environment / TestRole 绑定的 AccountPool。"""

        now = utc_now()
        request_hash = hash_request(command.model_dump(mode="json", by_alias=True))
        scope = f"environments.{environment_id}.account-pools.create"
        async with self._database.transaction(actor.database_context()) as connection:
            environment = await self._require_environment(
                connection,
                actor,
                environment_id,
                manage=True,
            )
            role = await self._identity.get_role(connection, command.role_id)
            if role is None or role.project_id != environment.project_id:
                raise self._not_found("TestRole 不存在于当前 Environment 的 Project。")
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=IDENTITY_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return IdentityCommandResult(
                    value=AccountPool.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            pool = await self._identity.create_pool(
                connection,
                pool_id=new_entity_id(),
                tenant_id=actor.tenant_id,
                project_id=environment.project_id,
                environment_id=environment.id,
                command=command,
            )
            if pool is None:
                raise self._conflict(
                    "Pool Key 已存在",
                    "同一 Environment 内的 AccountPool poolKey 必须唯一。",
                )
            await self._record_event(
                connection,
                actor=actor,
                project_id=pool.project_id,
                environment_id=pool.environment_id,
                entity_type="account_pool",
                entity_id=pool.id,
                event_type="account_pool.created",
                payload={"poolKey": pool.pool_key, "roleId": str(pool.role_id)},
                occurred_at=now,
            )
            await self._complete_idempotency(
                connection,
                actor=actor,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                value=pool,
                status_code=201,
            )
            return IdentityCommandResult(value=pool, status_code=201, replayed=False)

    async def list_pools(
        self,
        actor: ActorContext,
        environment_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> AccountPoolPage:
        """列出 Environment 的账号池。"""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_environment(connection, actor, environment_id, manage=False)
            records = await self._identity.list_pools(
                connection,
                environment_id=environment_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        return AccountPoolPage(
            items=items,
            next_cursor=self._next_cursor(items, has_more=len(records) > limit),
        )

    async def get_pool(self, actor: ActorContext, pool_id: UUID) -> AccountPool:
        """读取单个 AccountPool。"""

        async with self._database.transaction(actor.database_context()) as connection:
            pool = await self._identity.get_pool(connection, pool_id)
            if pool is None or not actor.can_read_project(pool.project_id):
                raise self._not_found("AccountPool 不存在。")
            return pool

    async def update_pool(
        self,
        actor: ActorContext,
        pool_id: UUID,
        command: UpdateAccountPool,
        *,
        expected_revision: int,
    ) -> AccountPool:
        """以 Revision CAS 更新 AccountPool。"""

        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._identity.get_pool(connection, pool_id)
            if current is None or not actor.can_read_project(current.project_id):
                raise self._not_found("AccountPool 不存在。")
            if not actor.can_manage_project(current.project_id):
                raise self._forbidden("当前角色不能修改 AccountPool。")
            pool = await self._identity.update_pool(
                connection,
                pool_id=pool_id,
                expected_revision=expected_revision,
                command=command,
            )
            if pool is None:
                raise self._revision_conflict(current.revision)
            if pool.status is AccountPoolStatus.DISABLED:
                revoked = await self._leases.revoke_active(
                    connection,
                    reason=LeaseReleaseReason.POOL_DISABLED,
                    now=now,
                    pool_id=pool.id,
                )
                await self._record_lease_revocations(
                    connection,
                    actor=actor,
                    leases=revoked,
                    occurred_at=now,
                )
            await self._record_event(
                connection,
                actor=actor,
                project_id=pool.project_id,
                environment_id=pool.environment_id,
                entity_type="account_pool",
                entity_id=pool.id,
                event_type="account_pool.updated",
                payload={"revision": pool.revision, "status": pool.status.value},
                occurred_at=now,
            )
            return pool

    async def create_account(
        self,
        actor: ActorContext,
        pool_id: UUID,
        command: CreateTestAccount,
        *,
        idempotency_key: str,
    ) -> IdentityCommandResult[TestAccount]:
        """幂等导入账号元数据、初始 Slot 与 SecretRef。"""

        now = utc_now()
        if any(
            credential.expires_at is not None and credential.expires_at <= now
            for credential in command.credentials
        ):
            raise ApplicationError(
                error_code=ErrorCode.INVALID_REQUEST,
                title="Credential 已过期",
                detail="CredentialBinding expiresAt 必须晚于当前时间。",
                status_code=400,
            )
        request_hash = hash_request(command.model_dump(mode="json", by_alias=True))
        scope = f"account-pools.{pool_id}.accounts.create"
        async with self._database.transaction(actor.database_context()) as connection:
            pool = await self._identity.get_pool(connection, pool_id)
            if pool is None or not actor.can_read_project(pool.project_id):
                raise self._not_found("AccountPool 不存在。")
            if not actor.can_manage_project(pool.project_id):
                raise self._forbidden("当前角色不能向 AccountPool 导入账号。")
            if pool.status is AccountPoolStatus.DISABLED:
                raise self._conflict("AccountPool 已禁用", "禁用账号池不能导入新账号。")
            environment = await self._platform.get_environment_for_share(
                connection,
                pool.environment_id,
            )
            if environment is None:
                raise self._not_found("Environment 不存在。")
            connector = await self._require_active_connector(
                connection,
                connector_id=command.connector_installation_id,
                project_id=pool.project_id,
                environment_id=pool.environment_id,
                source=command.source,
                auth_methods=tuple(item.auth_method for item in command.credentials),
            )
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=IDENTITY_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return IdentityCommandResult(
                    value=TestAccount.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            account = await self._identity.create_account(
                connection,
                account_id=new_entity_id(),
                slot_id=new_entity_id(),
                credential_ids=tuple(new_entity_id() for _ in command.credentials),
                pool=pool,
                command=command,
                now=now,
            )
            if account is None:
                raise self._conflict(
                    "TestAccount 已存在",
                    "账号 Key 或外部 Subject 在当前作用域中已经存在。",
                )
            await self._record_event(
                connection,
                actor=actor,
                project_id=account.project_id,
                environment_id=account.environment_id,
                entity_type="test_account",
                entity_id=account.id,
                event_type="test_account.created",
                payload={
                    "accountKey": account.account_key,
                    "poolId": str(account.pool_id),
                    "connectorInstallationId": str(connector.id),
                    "source": account.source.value,
                    "authMethods": [method.value for method in account.auth_methods],
                },
                occurred_at=now,
            )
            await self._complete_idempotency(
                connection,
                actor=actor,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                value=account,
                status_code=201,
            )
            return IdentityCommandResult(value=account, status_code=201, replayed=False)

    async def list_accounts(
        self,
        actor: ActorContext,
        pool_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> TestAccountPage:
        """列出账号池中的非敏感账号投影。"""

        now = utc_now()
        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            pool = await self._identity.get_pool(connection, pool_id)
            if pool is None or not actor.can_read_project(pool.project_id):
                raise self._not_found("AccountPool 不存在。")
            records = await self._identity.list_accounts(
                connection,
                pool_id=pool_id,
                cursor=decoded,
                limit=limit,
                now=now,
            )
        items = records[:limit]
        return TestAccountPage(
            items=items,
            next_cursor=self._next_cursor(items, has_more=len(records) > limit),
        )

    async def get_account(self, actor: ActorContext, account_id: UUID) -> TestAccount:
        """读取单个 TestAccount 的安全投影。"""

        async with self._database.transaction(actor.database_context()) as connection:
            account = await self._identity.get_account(connection, account_id, now=utc_now())
            if account is None or not actor.can_read_project(account.project_id):
                raise self._not_found("TestAccount 不存在。")
            return account

    async def update_account(
        self,
        actor: ActorContext,
        account_id: UUID,
        command: UpdateTestAccount,
        *,
        expected_revision: int,
    ) -> TestAccount:
        """以 Revision CAS 修改账号非敏感字段和受控生命周期。"""

        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._identity.get_account(connection, account_id, now=now)
            if current is None or not actor.can_read_project(current.project_id):
                raise self._not_found("TestAccount 不存在。")
            if not actor.can_manage_project(current.project_id):
                raise self._forbidden("当前角色不能修改 TestAccount。")
            self._validate_lifecycle_transition(current, command)
            connector_rebound = (
                command.connector_installation_id is not None
                and command.connector_installation_id != current.connector_installation_id
            )
            if connector_rebound:
                environment = await self._platform.get_environment_for_share(
                    connection,
                    current.environment_id,
                )
                if environment is None:
                    raise self._not_found("Environment 不存在。")
                assert command.connector_installation_id is not None
                await self._require_active_connector(
                    connection,
                    connector_id=command.connector_installation_id,
                    project_id=current.project_id,
                    environment_id=current.environment_id,
                    source=current.source,
                    auth_methods=current.auth_methods,
                )
            revoked: tuple[AccountLease, ...] = ()
            effective_revision = expected_revision
            revocation_reason: LeaseReleaseReason | None = None
            if command.lifecycle_status is AccountLifecycle.SUSPENDED:
                revocation_reason = LeaseReleaseReason.ACCOUNT_SUSPENDED
            elif command.lifecycle_status in {
                AccountLifecycle.RETIRING,
                AccountLifecycle.RETIRED,
            }:
                revocation_reason = LeaseReleaseReason.ACCOUNT_RETIRED
            elif connector_rebound:
                revocation_reason = LeaseReleaseReason.CONNECTOR_REBOUND
            if revocation_reason is not None:
                if current.revision != expected_revision:
                    raise self._revision_conflict(current.revision)
                revoked = await self._leases.revoke_active(
                    connection,
                    reason=revocation_reason,
                    now=now,
                    account_id=account_id,
                )
                effective_revision += len(revoked)
                await self._record_lease_revocations(
                    connection,
                    actor=actor,
                    leases=revoked,
                    occurred_at=now,
                )
            account = await self._identity.update_account(
                connection,
                account_id=account_id,
                expected_revision=effective_revision,
                command=command,
                now=now,
            )
            if account is None:
                latest = await self._identity.get_account(connection, account_id, now=now)
                latest_revision = (
                    max(1, latest.revision - len(revoked))
                    if latest is not None
                    else current.revision
                )
                raise self._revision_conflict(latest_revision)
            await self._record_account_transition(
                connection,
                actor=actor,
                before=current,
                after=account,
                reason=AccountStateTransitionReason.MANAGEMENT_REVOCATION,
                safe_summary="账号管理字段导致正交状态变化。",
                occurred_at=now,
            )
            await self._record_event(
                connection,
                actor=actor,
                project_id=account.project_id,
                environment_id=account.environment_id,
                entity_type="test_account",
                entity_id=account.id,
                event_type="test_account.updated",
                payload={
                    "revision": account.revision,
                    "lifecycleStatus": account.lifecycle_status.value,
                    "connectorInstallationId": (
                        str(account.connector_installation_id)
                        if account.connector_installation_id is not None
                        else None
                    ),
                },
                occurred_at=now,
            )
            return account

    async def quarantine_account(
        self,
        actor: ActorContext,
        account_id: UUID,
        command: AccountStateReason,
        *,
        expected_revision: int,
    ) -> TestAccount:
        """隔离账号；后续 Lease 与 Session 必须拒绝使用。"""

        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._identity.get_account(connection, account_id, now=now)
            if current is None or not actor.can_read_project(current.project_id):
                raise self._not_found("TestAccount 不存在。")
            if not actor.can_manage_project(current.project_id):
                raise self._forbidden("当前角色不能隔离 TestAccount。")
            if current.health_status is AccountHealth.QUARANTINED:
                revoked = await self._leases.revoke_active(
                    connection,
                    reason=LeaseReleaseReason.ACCOUNT_QUARANTINED,
                    now=now,
                    account_id=account_id,
                )
                await self._record_lease_revocations(
                    connection,
                    actor=actor,
                    leases=revoked,
                    occurred_at=now,
                )
                refreshed = await self._identity.get_account(connection, account_id, now=now)
                if refreshed is None:
                    raise RuntimeError("quarantined account disappeared while locked")
                return refreshed
            if current.revision != expected_revision:
                raise self._revision_conflict(current.revision)
            revoked = await self._leases.revoke_active(
                connection,
                reason=LeaseReleaseReason.ACCOUNT_QUARANTINED,
                now=now,
                account_id=account_id,
            )
            await self._record_lease_revocations(
                connection,
                actor=actor,
                leases=revoked,
                occurred_at=now,
            )
            account = await self._identity.quarantine_account(
                connection,
                account_id=account_id,
                expected_revision=expected_revision + len(revoked),
                command=command,
                now=now,
            )
            if account is None:
                latest = await self._identity.get_account(connection, account_id, now=now)
                latest_revision = (
                    max(1, latest.revision - len(revoked))
                    if latest is not None
                    else current.revision
                )
                raise self._revision_conflict(latest_revision)
            await self._record_account_transition(
                connection,
                actor=actor,
                before=current,
                after=account,
                reason=AccountStateTransitionReason.MANUAL_QUARANTINE,
                safe_summary="账号被管理员隔离。",
                occurred_at=now,
            )
            await self._record_event(
                connection,
                actor=actor,
                project_id=account.project_id,
                environment_id=account.environment_id,
                entity_type="test_account",
                entity_id=account.id,
                event_type="test_account.quarantined",
                payload={"reason": command.reason},
                occurred_at=now,
            )
            return account

    async def restore_account(
        self,
        actor: ActorContext,
        account_id: UUID,
        command: AccountStateReason,
        *,
        expected_revision: int,
    ) -> TestAccount:
        """解除隔离但保持 UNKNOWN，必须重新验证后才能调度。"""

        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._identity.get_account(connection, account_id, now=now)
            if current is None or not actor.can_read_project(current.project_id):
                raise self._not_found("TestAccount 不存在。")
            if not actor.can_manage_project(current.project_id):
                raise self._forbidden("当前角色不能恢复 TestAccount。")
            if current.health_status is not AccountHealth.QUARANTINED:
                raise self._conflict("账号未被隔离", "只有 QUARANTINED 账号可以恢复。")
            account = await self._identity.restore_account(
                connection,
                account_id=account_id,
                expected_revision=expected_revision,
                command=command,
                now=now,
            )
            if account is None:
                raise self._revision_conflict(current.revision)
            await self._record_account_transition(
                connection,
                actor=actor,
                before=current,
                after=account,
                reason=AccountStateTransitionReason.MANUAL_RESTORE,
                safe_summary="账号已解除隔离，等待重新验证。",
                occurred_at=now,
            )
            await self._record_event(
                connection,
                actor=actor,
                project_id=account.project_id,
                environment_id=account.environment_id,
                entity_type="test_account",
                entity_id=account.id,
                event_type="test_account.restored",
                payload={"reason": command.reason},
                occurred_at=now,
            )
            return account

    async def get_capacity(
        self,
        actor: ActorContext,
        pool_id: UUID,
    ) -> AccountPoolCapacity:
        """读取 AccountPool 的实时容量，不缓存可用状态。"""

        async with self._database.transaction(actor.database_context()) as connection:
            pool = await self._identity.get_pool(connection, pool_id)
            if pool is None or not actor.can_read_project(pool.project_id):
                raise self._not_found("AccountPool 不存在。")
            return await self._identity.get_capacity(connection, pool_id=pool.id, now=utc_now())

    async def _require_project(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        project_id: UUID,
        *,
        manage: bool,
    ) -> Project:
        project = await self._platform.get_project(connection, project_id)
        if project is None or not actor.can_read_project(project_id):
            raise self._not_found("Project 不存在。")
        if manage and not actor.can_manage_project(project_id):
            raise self._forbidden("当前角色不能管理该 Project 的测试身份。")
        return project

    async def _require_environment(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        environment_id: UUID,
        *,
        manage: bool,
    ) -> Environment:
        environment = await self._platform.get_environment(connection, environment_id)
        if environment is None or not actor.can_read_project(environment.project_id):
            raise self._not_found("Environment 不存在。")
        if manage and not actor.can_manage_project(environment.project_id):
            raise self._forbidden("当前角色不能管理该 Environment 的账号池。")
        return environment

    async def _require_active_connector(
        self,
        connection: AsyncConnection[DictRow],
        *,
        connector_id: UUID,
        project_id: UUID,
        environment_id: UUID,
        source: AccountSource,
        auth_methods: tuple[CredentialAuthMethod, ...],
    ) -> ConnectorInstallationRecord:
        """锁定账号绑定的 Connector，并验证作用域、模式和实际认证能力。"""

        connector = await self._connectors.get_record_for_share(
            connection,
            connector_id,
        )
        if (
            connector is None
            or connector.project_id != project_id
            or connector.environment_id != environment_id
        ):
            raise self._not_found("ConnectorInstallation 不存在。")
        if connector.status is not ConnectorStatus.ACTIVE:
            raise self._conflict(
                "Connector 不可用",
                "测试账号只能绑定已经验证且处于 ACTIVE 的 Connector。",
            )
        if source is AccountSource.ATLAS_MANAGED and connector.mode is ConnectorMode.OBSERVE_ONLY:
            raise self._conflict(
                "Connector 模式不允许托管账号",
                "ATLAS_MANAGED 账号不能绑定 OBSERVE_ONLY Connector。",
            )
        capabilities = await self._connectors.get_capabilities(connection, connector.id)
        available = {capability.name for capability in capabilities}
        required = {
            self._provider_capability_for_auth_method(auth_method) for auth_method in auth_methods
        }
        if not required.issubset(available):
            raise self._conflict(
                "Connector 认证能力不足",
                "Connector 的实际 Capability Snapshot 不支持账号认证方式。",
            )
        return connector

    @staticmethod
    def _provider_capability_for_auth_method(
        auth_method: CredentialAuthMethod,
    ) -> ProviderCapability:
        mapping = {
            CredentialAuthMethod.PASSWORD: ProviderCapability.AUTH_PASSWORD,
            CredentialAuthMethod.OAUTH2: ProviderCapability.AUTH_OAUTH2,
            CredentialAuthMethod.OIDC: ProviderCapability.AUTH_OIDC,
            CredentialAuthMethod.SAML_SSO: ProviderCapability.AUTH_SAML_SSO,
            CredentialAuthMethod.TOTP: ProviderCapability.AUTH_MFA_TOTP,
            CredentialAuthMethod.MANUAL_BOOTSTRAP: (ProviderCapability.AUTH_MANUAL_BOOTSTRAP),
        }
        return mapping[auth_method]

    async def _record_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        project_id: UUID,
        environment_id: UUID | None,
        entity_type: str,
        entity_id: UUID,
        event_type: str,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> None:
        await self._audit.append(
            connection,
            tenant_id=actor.tenant_id,
            project_id=project_id,
            environment_id=environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=actor.tenant_id,
                aggregate_type=entity_type,
                aggregate_id=entity_id,
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
            await self._record_event(
                connection,
                actor=actor,
                project_id=lease.project_id,
                environment_id=lease.environment_id,
                entity_type="account_lease",
                entity_id=lease.id,
                event_type="account_lease.revoked",
                payload={
                    "accountId": str(lease.account_id),
                    "executionId": lease.execution_id,
                    "fencingToken": lease.fencing_token,
                    "releaseReason": lease.release_reason.value,
                    "status": lease.status.value,
                },
                occurred_at=occurred_at,
            )

    async def _record_account_transition(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        before: TestAccount,
        after: TestAccount,
        reason: AccountStateTransitionReason,
        safe_summary: str,
        occurred_at: datetime,
    ) -> None:
        """Append an immutable secret-free snapshot for a management state change."""

        await self._account_health.append_transition(
            connection,
            tenant_id=after.tenant_id,
            project_id=after.project_id,
            environment_id=after.environment_id,
            account_id=after.id,
            health_check_id=None,
            reason=reason,
            before=self._state_record(before),
            after=self._state_record(after),
            safe_summary=safe_summary,
            actor_id=actor.actor_id,
            request_id=actor.request_id,
            occurred_at=occurred_at,
        )

    @staticmethod
    def _state_record(account: TestAccount) -> AccountStateRecord:
        return AccountStateRecord(
            lifecycle_status=account.lifecycle_status,
            health_status=account.health_status,
            operational_status=account.operational_status,
            sync_status=account.sync_status,
            cooldown_until=account.cooldown_until,
            consecutive_health_failures=account.consecutive_health_failures,
            revision=account.revision,
        )

    async def _complete_idempotency(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        scope: str,
        key: str,
        request_hash: str,
        value: WireModel,
        status_code: int,
    ) -> None:
        await self._idempotency.complete(
            connection,
            tenant_id=actor.tenant_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response=CachedHttpResponse(
                status_code=status_code,
                body=self._json_object(value),
            ),
        )

    @staticmethod
    def _next_cursor(items: tuple[CursorEntity, ...], *, has_more: bool) -> str | None:
        if not has_more or not items:
            return None
        last = items[-1]
        return next_time_cursor(last.created_at, last.id)

    @staticmethod
    def _validate_lifecycle_transition(
        current: TestAccount,
        command: UpdateTestAccount,
    ) -> None:
        target = command.lifecycle_status
        if target is None or target is current.lifecycle_status:
            return
        allowed: dict[AccountLifecycle, frozenset[AccountLifecycle]] = {
            AccountLifecycle.DRAFT: frozenset(
                {
                    AccountLifecycle.PROVISIONING,
                    AccountLifecycle.ACTIVE,
                    AccountLifecycle.SUSPENDED,
                    AccountLifecycle.RETIRING,
                    AccountLifecycle.RETIRED,
                }
            ),
            AccountLifecycle.PROVISIONING: frozenset(
                {
                    AccountLifecycle.ACTIVE,
                    AccountLifecycle.SUSPENDED,
                    AccountLifecycle.RETIRING,
                }
            ),
            AccountLifecycle.ACTIVE: frozenset(
                {AccountLifecycle.SUSPENDED, AccountLifecycle.RETIRING}
            ),
            AccountLifecycle.SUSPENDED: frozenset(
                {
                    AccountLifecycle.ACTIVE,
                    AccountLifecycle.RETIRING,
                    AccountLifecycle.RETIRED,
                }
            ),
            AccountLifecycle.RETIRING: frozenset({AccountLifecycle.RETIRED}),
            AccountLifecycle.RETIRED: frozenset(),
        }
        if target not in allowed[current.lifecycle_status]:
            raise ApplicationError(
                error_code=ErrorCode.CONFLICT,
                title="账号生命周期转换无效",
                detail=(f"不能把账号从 {current.lifecycle_status.value} 转换为 {target.value}。"),
                status_code=409,
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
            title="没有操作权限",
            detail=detail,
            status_code=403,
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
    def _revision_conflict(current_revision: int) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.PRECONDITION_FAILED,
            title="Revision 已变化",
            detail="资源已经被其他请求修改，请重新读取后再提交。",
            status_code=412,
            headers={"ETag": format_revision_etag(current_revision)},
        )
