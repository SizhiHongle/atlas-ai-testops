"""账号租约应用服务，负责权限、幂等、事务、审计与可靠事件。"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.core.contracts import WireModel, new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.identity import (
    AccountLease,
    AccountLeaseHandle,
    AccountLeaseStatus,
    AcquireAccountLease,
    HeartbeatAccountLease,
    ReapedLeaseBatch,
    ReleaseAccountLease,
    SecretGrantRecord,
    TestRoleStatus,
)
from atlas_testops.domain.platform import Environment, EnvironmentKind, EnvironmentStatus
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.leases import (
    LeaseMutationKind,
    LeaseMutationResult,
    LeaseRepository,
)
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.secret_grants import SecretGrantRepository

LEASE_IDEMPOTENCY_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class LeaseCommandResult:
    """携带租约命令的 HTTP 状态与重放信息。"""

    value: AccountLeaseHandle
    status_code: int
    replayed: bool


class LeaseService:
    """协调账号租约的安全边界和 PostgreSQL 正确性协议。"""

    def __init__(
        self,
        database: Database,
        lease_repository: LeaseRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
        grant_repository: SecretGrantRepository | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._database = database
        self._leases = lease_repository or LeaseRepository()
        self._platform = platform_repository or PlatformRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()
        self._grants = grant_repository or SecretGrantRepository()
        self._clock = clock

    async def acquire(
        self,
        actor: ActorContext,
        command: AcquireAccountLease,
        *,
        idempotency_key: str,
    ) -> LeaseCommandResult:
        """幂等申请满足角色、标签和认证要求的独占账号槽。"""

        now = self._clock()
        if command.execution_deadline <= now:
            raise ApplicationError(
                error_code=ErrorCode.INVALID_REQUEST,
                title="Execution 已结束",
                detail="executionDeadline 必须晚于服务端当前时间。",
                status_code=400,
            )
        request_hash = hash_request(command.model_dump(mode="json", by_alias=True))
        scope = f"executions.{command.execution_id}.account-leases.acquire"
        exhausted_after_commit = False
        acquired_handle: AccountLeaseHandle | None = None
        async with self._database.transaction(actor.database_context()) as connection:
            environment = await self._require_environment(
                connection,
                actor,
                command.environment_id,
            )
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=LEASE_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return LeaseCommandResult(
                    value=AccountLeaseHandle.model_validate(
                        reservation.cached_response.body
                    ),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            self._reject_production_environment(environment)
            role = await self._leases.get_role_by_key(
                connection,
                project_id=environment.project_id,
                role_key=command.role_key,
            )
            if role is None or role.status is not TestRoleStatus.ACTIVE:
                raise self._constraint_unsatisfied(
                    "当前 Project 没有可调度的目标 TestRole。"
                )
            if not set(command.requirements.capabilities).issubset(role.capabilities):
                raise self._constraint_unsatisfied(
                    "TestRole 不满足请求的全部 capability。"
                )

            await self._leases.restore_elapsed_cooldowns(
                connection,
                now=now,
                environment_id=environment.id,
            )
            expired = await self._leases.reap_expired(
                connection,
                now=now,
                limit=100,
                environment_id=environment.id,
            )
            for expired_lease in expired:
                await self._record_lease_event(
                    connection,
                    actor=actor,
                    lease=expired_lease,
                    event_type="account_lease.expired",
                    occurred_at=now,
                )

            lease = await self._leases.acquire(
                connection,
                lease_id=new_entity_id(),
                tenant_id=actor.tenant_id,
                account_handle=f"ah_{new_entity_id().hex}",
                role=role,
                command=command,
                now=now,
            )
            if lease is None:
                cancelled = await self._idempotency.cancel(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                )
                if not cancelled:
                    raise RuntimeError("lease idempotency reservation could not be cancelled")
                exhausted_after_commit = True
            else:
                await self._record_lease_event(
                    connection,
                    actor=actor,
                    lease=lease,
                    event_type="account_lease.acquired",
                    occurred_at=now,
                )
                acquired_handle = lease.to_handle()
                await self._complete_idempotency(
                    connection,
                    actor=actor,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    value=acquired_handle,
                    status_code=201,
                )
        if exhausted_after_commit:
            raise ApplicationError(
                error_code=ErrorCode.POOL_EXHAUSTED,
                title="账号池容量已耗尽",
                detail="没有满足角色、标签、认证方式和健康条件的空闲账号槽。",
                status_code=409,
                headers={"Retry-After": "1"},
            )
        assert acquired_handle is not None
        return LeaseCommandResult(value=acquired_handle, status_code=201, replayed=False)

    async def get(self, actor: ActorContext, lease_id: UUID) -> AccountLeaseHandle:
        """读取当前 Worker 可见的安全 Lease Handle。"""

        async with self._database.transaction(actor.database_context()) as connection:
            lease = await self._leases.get_lease(connection, lease_id)
            self._require_lease_access(actor, lease)
            assert lease is not None
            return lease.to_handle()

    async def heartbeat(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: HeartbeatAccountLease,
    ) -> AccountLeaseHandle:
        """续租并确保错误路径不会回滚已经持久化的过期终态。"""

        now = self._clock()
        outcome: LeaseMutationResult
        expired_after_commit = False
        async with self._database.transaction(actor.database_context()) as connection:
            visible = await self._leases.get_lease(connection, lease_id)
            self._require_lease_access(actor, visible)
            outcome = await self._leases.heartbeat(
                connection,
                lease_id=lease_id,
                command=command,
                now=now,
            )
            if outcome.kind is LeaseMutationKind.EXPIRED:
                assert outcome.lease is not None
                await self._record_lease_event(
                    connection,
                    actor=actor,
                    lease=outcome.lease,
                    event_type="account_lease.expired",
                    occurred_at=now,
                )
                expired_after_commit = True
            elif outcome.kind in {
                LeaseMutationKind.FENCED,
                LeaseMutationKind.TERMINAL,
            }:
                self._raise_fenced()
            elif outcome.kind is LeaseMutationKind.NOT_FOUND:
                raise self._not_found()
        if expired_after_commit:
            self._raise_expired()
        assert outcome.lease is not None
        return outcome.lease.to_handle()

    async def release(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: ReleaseAccountLease,
    ) -> LeaseCommandResult:
        """幂等释放租约；旧 Fence 不能影响后续新租约。"""

        now = self._clock()
        outcome: LeaseMutationResult
        expired_after_commit = False
        replayed = False
        async with self._database.transaction(actor.database_context()) as connection:
            visible = await self._leases.get_lease(connection, lease_id)
            self._require_lease_access(actor, visible)
            outcome = await self._leases.release(
                connection,
                lease_id=lease_id,
                command=command,
                now=now,
            )
            if outcome.kind is LeaseMutationKind.EXPIRED:
                assert outcome.lease is not None
                await self._record_lease_event(
                    connection,
                    actor=actor,
                    lease=outcome.lease,
                    event_type="account_lease.expired",
                    occurred_at=now,
                )
                expired_after_commit = True
            elif outcome.kind is LeaseMutationKind.TERMINAL:
                assert outcome.lease is not None
                if outcome.lease.status is AccountLeaseStatus.RELEASED:
                    replayed = True
                elif outcome.lease.status is AccountLeaseStatus.EXPIRED:
                    expired_after_commit = True
                else:
                    self._raise_fenced()
            elif outcome.kind is LeaseMutationKind.FENCED:
                self._raise_fenced()
            elif outcome.kind is LeaseMutationKind.NOT_FOUND:
                raise self._not_found()
            else:
                assert outcome.lease is not None
                await self._record_lease_event(
                    connection,
                    actor=actor,
                    lease=outcome.lease,
                    event_type="account_lease.released",
                    occurred_at=now,
                )
        if expired_after_commit:
            self._raise_expired()
        assert outcome.lease is not None
        return LeaseCommandResult(
            value=outcome.lease.to_handle(),
            status_code=200,
            replayed=replayed,
        )

    async def reap_expired(
        self,
        actor: ActorContext,
        *,
        limit: int,
    ) -> ReapedLeaseBatch:
        """为当前 Tenant 回收一批过期 Lease，并触发账号安全复核。"""

        if not actor.is_organization_admin():
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="没有回收权限",
                detail="只有组织管理员或内部 Reconciler 可以执行租约回收。",
                status_code=403,
            )
        now = self._clock()
        async with self._database.transaction(actor.database_context()) as connection:
            await self._leases.restore_elapsed_cooldowns(connection, now=now)
            expired_grants = await self._grants.reap_expired(
                connection,
                now=now,
                limit=limit,
            )
            for grant in expired_grants:
                await self._record_grant_event(
                    connection,
                    actor=actor,
                    grant=grant,
                    occurred_at=now,
                )
            expired = await self._leases.reap_expired(
                connection,
                now=now,
                limit=limit,
            )
            for lease in expired:
                await self._record_lease_event(
                    connection,
                    actor=actor,
                    lease=lease,
                    event_type="account_lease.expired",
                    occurred_at=now,
                )
        return ReapedLeaseBatch(reaped=len(expired), observed_at=now)

    async def _record_grant_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        grant: SecretGrantRecord,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "leaseId": str(grant.lease_id),
            "fencingToken": grant.fencing_token,
            "purpose": grant.purpose.value,
            "status": grant.status.value,
        }
        if grant.termination_reason is not None:
            payload["terminationReason"] = grant.termination_reason.value
        await self._audit.append(
            connection,
            tenant_id=actor.tenant_id,
            project_id=grant.project_id,
            environment_id=grant.environment_id,
            actor_id=actor.actor_id,
            event_type="secret_grant.expired",
            entity_type="secret_grant",
            entity_id=grant.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=actor.tenant_id,
                aggregate_type="secret_grant",
                aggregate_id=grant.id,
                event_type="secret_grant.expired",
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    async def _require_environment(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        environment_id: UUID,
    ) -> Environment:
        environment = await self._platform.get_environment(connection, environment_id)
        if environment is None or not actor.can_read_project(environment.project_id):
            raise self._not_found("Environment 或目标 TestRole 不存在。")
        if not actor.can_operate_project(environment.project_id):
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="没有租约操作权限",
                detail="当前 PlatformRole 不能操作该 Project 的测试账号租约。",
                status_code=403,
            )
        if environment.status is not EnvironmentStatus.ACTIVE:
            raise self._constraint_unsatisfied("Environment 已禁用，不能申请新租约。")
        return environment

    @staticmethod
    def _reject_production_environment(environment: Environment) -> None:
        if environment.kind is EnvironmentKind.PRODUCTION:
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="生产身份租约默认禁用",
                detail="生产 Environment 需要独立策略、审批和网络允许列表。",
                status_code=403,
            )

    @staticmethod
    def _require_lease_access(
        actor: ActorContext,
        lease: AccountLease | None,
    ) -> None:
        if lease is None or not actor.can_operate_project(lease.project_id):
            raise LeaseService._not_found()

    async def _record_lease_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        lease: AccountLease,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "accountId": str(lease.account_id),
            "executionId": lease.execution_id,
            "fencingToken": lease.fencing_token,
            "status": lease.status.value,
        }
        if lease.release_reason is not None:
            payload["releaseReason"] = lease.release_reason.value
        await self._audit.append(
            connection,
            tenant_id=actor.tenant_id,
            project_id=lease.project_id,
            environment_id=lease.environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
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
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
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
                body=cast(
                    dict[str, JsonValue],
                    value.model_dump(mode="json", by_alias=True),
                ),
            ),
        )

    @staticmethod
    def _constraint_unsatisfied(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.CONSTRAINT_UNSATISFIED,
            title="租约条件无法满足",
            detail=detail,
            status_code=422,
        )

    @staticmethod
    def _not_found(detail: str = "AccountLease 不存在。") -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="资源不存在",
            detail=detail,
            status_code=404,
        )

    @staticmethod
    def _raise_fenced() -> None:
        raise ApplicationError(
            error_code=ErrorCode.LEASE_FENCED,
            title="租约已被 Fencing",
            detail="leaseId 或 fencingToken 不是账号当前可接受的活动租约。",
            status_code=409,
        )

    @staticmethod
    def _raise_expired() -> None:
        raise ApplicationError(
            error_code=ErrorCode.LEASE_EXPIRED,
            title="租约已过期",
            detail="租约已经越过 TTL 或 Execution Deadline，请重新申请。",
            status_code=409,
        )
