"""账号独占租约、Fencing、Heartbeat、Release 与回收 Repository。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.domain.identity import (
    AccountLease,
    AccountLeaseStatus,
    AcquireAccountLease,
    HeartbeatAccountLease,
    LeaseReleaseReason,
    ReleaseAccountLease,
    TestRole,
    lease_fence_matches,
    lease_is_expired,
)

LEASE_COLUMNS = (
    "id, tenant_id, project_id, environment_id, pool_id, account_id, slot_id, "
    "execution_id, worker_id, account_handle, fencing_token, ttl_seconds, status, "
    "acquired_at, heartbeat_at, expires_at, max_expires_at, released_at, "
    "release_reason, revision, updated_at"
)
ROLE_COLUMNS = (
    "id, tenant_id, project_id, role_key, name, description, capabilities, "
    "status, revision, created_at, updated_at"
)


class LeaseMutationKind(StrEnum):
    """Repository 租约命令的精确结果。"""

    AUTHORIZED = "AUTHORIZED"
    UPDATED = "UPDATED"
    TERMINAL = "TERMINAL"
    EXPIRED = "EXPIRED"
    FENCED = "FENCED"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True, slots=True)
class LeaseMutationResult:
    """让应用层在事务提交后再抛出过期错误。"""

    kind: LeaseMutationKind
    lease: AccountLease | None


class LeaseRepository:
    """以 PostgreSQL 行锁和唯一索引维护账号租约正确性。"""

    async def get_role_by_key(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        role_key: str,
    ) -> TestRole | None:
        """解析 Environment 所属 Project 中的稳定业务角色。"""

        cursor = await connection.execute(
            f"""
            select {ROLE_COLUMNS}
            from atlas.test_role
            where project_id = %s and role_key = %s
            """,
            (project_id, role_key),
        )
        row = await cursor.fetchone()
        return TestRole.model_validate(row) if row is not None else None

    async def get_lease(
        self,
        connection: AsyncConnection[DictRow],
        lease_id: UUID,
    ) -> AccountLease | None:
        """读取当前 Tenant 可见的租约事实。"""

        cursor = await connection.execute(
            f"select {LEASE_COLUMNS} from atlas.account_lease where id = %s",
            (lease_id,),
        )
        row = await cursor.fetchone()
        return AccountLease.model_validate(row) if row is not None else None

    async def authorize_sensitive_use(
        self,
        connection: AsyncConnection[DictRow],
        *,
        lease_id: UUID,
        fencing_token: int,
        now: datetime,
    ) -> LeaseMutationResult:
        """锁定并校验需要 Lease 与最新 Fence 的敏感操作。"""

        lease = await self._get_lease_for_update(connection, lease_id)
        if lease is None:
            return LeaseMutationResult(LeaseMutationKind.NOT_FOUND, None)
        if lease.fencing_token != fencing_token:
            return LeaseMutationResult(LeaseMutationKind.FENCED, lease)
        if lease.status is not AccountLeaseStatus.ACTIVE:
            return LeaseMutationResult(LeaseMutationKind.TERMINAL, lease)
        if lease_is_expired(lease, now):
            expired = await self._expire_locked(connection, lease, now=now)
            return LeaseMutationResult(LeaseMutationKind.EXPIRED, expired)
        epoch = await self._get_account_epoch(connection, lease.account_id)
        if not lease_fence_matches(lease, fencing_token, epoch):
            return LeaseMutationResult(LeaseMutationKind.FENCED, lease)
        return LeaseMutationResult(LeaseMutationKind.AUTHORIZED, lease)

    async def restore_elapsed_cooldowns(
        self,
        connection: AsyncConnection[DictRow],
        *,
        now: datetime,
        environment_id: UUID | None = None,
    ) -> tuple[UUID, ...]:
        """把已完成冷却且健康的账号恢复到 READY。"""

        environment_filter = "and environment_id = %s" if environment_id is not None else ""
        parameters: tuple[object, ...] = (
            (now, environment_id) if environment_id is not None else (now,)
        )
        cursor = await connection.execute(
            f"""
            update atlas.test_account
            set operational_status = 'READY', cooldown_until = null,
                revision = revision + 1
            where operational_status = 'COOLDOWN'
              and cooldown_until <= %s
              and lifecycle_status = 'ACTIVE'
              and health_status = 'HEALTHY'
              and sync_status not in ('CONFLICT', 'TOMBSTONED')
              {environment_filter}
            returning id
            """,
            parameters,
        )
        return tuple(row["id"] for row in await cursor.fetchall())

    async def acquire(
        self,
        connection: AsyncConnection[DictRow],
        *,
        lease_id: UUID,
        tenant_id: UUID,
        account_handle: str,
        role: TestRole,
        command: AcquireAccountLease,
        now: datetime,
    ) -> AccountLease | None:
        """LRU 锁定一个可用 Slot、推进 Epoch 并创建 Active Lease。"""

        requested_methods = [method.value for method in command.requirements.auth_methods]
        scope_cursor = await connection.execute(
            """
            select p.id
            from atlas.account_pool p
            join atlas.test_role r on r.id = p.role_id
            join atlas.environment e on e.id = p.environment_id
            where e.id = %s
              and r.id = %s
              and e.status = 'ACTIVE'
              and r.status = 'ACTIVE'
              and p.status = 'ACTIVE'
              and p.exclusive
            order by p.id
            for share of p, r, e
            """,
            (command.environment_id, role.id),
        )
        if not await scope_cursor.fetchall():
            return None

        connector_cursor = await connection.execute(
            """
            select id
            from atlas.connector_installation
            where environment_id = %s and status = 'ACTIVE'
            order by id
            for share
            """,
            (command.environment_id,),
        )
        if not await connector_cursor.fetchall():
            return None

        cursor = await connection.execute(
            """
            select s.id as slot_id, a.id as account_id, a.pool_id,
                   p.default_ttl_seconds
            from atlas.account_slot s
            join atlas.test_account a on a.id = s.account_id
            join atlas.account_pool p on p.id = a.pool_id
            join atlas.test_role r on r.id = p.role_id
            join atlas.environment e on e.id = p.environment_id
            join atlas.connector_installation ci
              on ci.id = a.connector_installation_id
            where e.id = %s
              and r.id = %s
              and e.status = 'ACTIVE'
              and r.status = 'ACTIVE'
              and p.status = 'ACTIVE'
              and ci.status = 'ACTIVE'
              and p.exclusive
              and s.status = 'ACTIVE'
              and a.lifecycle_status = 'ACTIVE'
              and a.health_status = 'HEALTHY'
              and a.operational_status = 'READY'
              and a.sync_status not in ('CONFLICT', 'TOMBSTONED')
              and (a.cooldown_until is null or a.cooldown_until <= %s)
              and a.labels @> %s
              and exists (
                select 1
                from atlas.credential_binding c
                where c.account_id = a.id
                  and c.status = 'ACTIVE'
                  and (c.expires_at is null or c.expires_at > %s)
              )
              and not exists (
                select 1
                from unnest(%s::text[]) as required(auth_method)
                where not exists (
                  select 1
                  from atlas.credential_binding c
                  where c.account_id = a.id
                    and c.auth_method = required.auth_method
                    and c.status = 'ACTIVE'
                    and (c.expires_at is null or c.expires_at > %s)
                )
              )
              and not exists (
                select 1
                from atlas.account_lease active_lease
                where active_lease.slot_id = s.id
                  and active_lease.status = 'ACTIVE'
            )
            order by a.last_leased_at nulls first, a.id, s.slot_index
            for update of s, a skip locked
            limit 1
            """,
            (
                command.environment_id,
                role.id,
                now,
                Jsonb(command.requirements.label_filter()),
                now,
                requested_methods,
                now,
            ),
        )
        candidate = await cursor.fetchone()
        if candidate is None:
            return None

        active_cursor = await connection.execute(
            """
            select 1
            from atlas.account_lease
            where slot_id = %s and status = 'ACTIVE'
            limit 1
            """,
            (candidate["slot_id"],),
        )
        if await active_cursor.fetchone() is not None:
            return None

        ttl_seconds = command.ttl_seconds or candidate["default_ttl_seconds"]
        expires_at = min(now + timedelta(seconds=ttl_seconds), command.execution_deadline)
        epoch_cursor = await connection.execute(
            """
            update atlas.test_account
            set lease_epoch = lease_epoch + 1,
                last_leased_at = %s,
                revision = revision + 1
            where id = %s
            returning lease_epoch
            """,
            (now, candidate["account_id"]),
        )
        epoch_row = await epoch_cursor.fetchone()
        if epoch_row is None:
            raise RuntimeError("lease candidate account disappeared while locked")

        lease_cursor = await connection.execute(
            f"""
            insert into atlas.account_lease (
              id, tenant_id, project_id, environment_id, pool_id, account_id,
              slot_id, execution_id, worker_id, account_handle, fencing_token,
              ttl_seconds, acquired_at, heartbeat_at, expires_at, max_expires_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s
            )
            returning {LEASE_COLUMNS}
            """,
            (
                lease_id,
                tenant_id,
                role.project_id,
                command.environment_id,
                candidate["pool_id"],
                candidate["account_id"],
                candidate["slot_id"],
                command.execution_id,
                command.worker_id,
                account_handle,
                epoch_row["lease_epoch"],
                ttl_seconds,
                now,
                now,
                expires_at,
                command.execution_deadline,
            ),
        )
        row = await lease_cursor.fetchone()
        if row is None:
            raise RuntimeError("account lease insert returned no row")
        return AccountLease.model_validate(row)

    async def heartbeat(
        self,
        connection: AsyncConnection[DictRow],
        *,
        lease_id: UUID,
        command: HeartbeatAccountLease,
        now: datetime,
    ) -> LeaseMutationResult:
        """锁定 Lease，校验最新 Epoch，并在执行上限内延长 TTL。"""

        lease = await self._get_lease_for_update(connection, lease_id)
        if lease is None:
            return LeaseMutationResult(LeaseMutationKind.NOT_FOUND, None)
        if lease.fencing_token != command.fencing_token:
            return LeaseMutationResult(LeaseMutationKind.FENCED, lease)
        if lease.status is not AccountLeaseStatus.ACTIVE:
            return LeaseMutationResult(LeaseMutationKind.TERMINAL, lease)
        if lease_is_expired(lease, now):
            expired = await self._expire_locked(connection, lease, now=now)
            return LeaseMutationResult(LeaseMutationKind.EXPIRED, expired)

        epoch = await self._get_account_epoch(connection, lease.account_id)
        if not lease_fence_matches(lease, command.fencing_token, epoch):
            return LeaseMutationResult(LeaseMutationKind.FENCED, lease)
        ttl_seconds = command.ttl_seconds or lease.ttl_seconds
        expires_at = min(now + timedelta(seconds=ttl_seconds), lease.max_expires_at)
        if expires_at <= now:
            expired = await self._expire_locked(connection, lease, now=now)
            return LeaseMutationResult(LeaseMutationKind.EXPIRED, expired)
        cursor = await connection.execute(
            f"""
            update atlas.account_lease
            set heartbeat_at = %s, expires_at = %s, revision = revision + 1
            where id = %s and status = 'ACTIVE' and fencing_token = %s
            returning {LEASE_COLUMNS}
            """,
            (now, expires_at, lease.id, command.fencing_token),
        )
        row = await cursor.fetchone()
        if row is None:
            return LeaseMutationResult(LeaseMutationKind.FENCED, lease)
        return LeaseMutationResult(
            LeaseMutationKind.UPDATED,
            AccountLease.model_validate(row),
        )

    async def release(
        self,
        connection: AsyncConnection[DictRow],
        *,
        lease_id: UUID,
        command: ReleaseAccountLease,
        now: datetime,
    ) -> LeaseMutationResult:
        """只让最新 Fence 结束 Active Lease；终态重复请求直接重放。"""

        lease = await self._get_lease_for_update(connection, lease_id)
        if lease is None:
            return LeaseMutationResult(LeaseMutationKind.NOT_FOUND, None)
        if lease.fencing_token != command.fencing_token:
            return LeaseMutationResult(LeaseMutationKind.FENCED, lease)
        if lease.status is not AccountLeaseStatus.ACTIVE:
            return LeaseMutationResult(LeaseMutationKind.TERMINAL, lease)
        if lease_is_expired(lease, now):
            expired = await self._expire_locked(connection, lease, now=now)
            return LeaseMutationResult(LeaseMutationKind.EXPIRED, expired)

        epoch = await self._get_account_epoch(connection, lease.account_id)
        if not lease_fence_matches(lease, command.fencing_token, epoch):
            return LeaseMutationResult(LeaseMutationKind.FENCED, lease)
        cursor = await connection.execute(
            f"""
            update atlas.account_lease
            set status = 'RELEASED', released_at = %s, release_reason = %s,
                revision = revision + 1
            where id = %s and status = 'ACTIVE' and fencing_token = %s
            returning {LEASE_COLUMNS}
            """,
            (now, command.reason, lease.id, command.fencing_token),
        )
        row = await cursor.fetchone()
        if row is None:
            return LeaseMutationResult(LeaseMutationKind.FENCED, lease)
        released = AccountLease.model_validate(row)
        await self._apply_release_account_state(
            connection,
            lease=released,
            reason=command.reason,
            now=now,
        )
        return LeaseMutationResult(LeaseMutationKind.UPDATED, released)

    async def reap_expired(
        self,
        connection: AsyncConnection[DictRow],
        *,
        now: datetime,
        limit: int,
        environment_id: UUID | None = None,
    ) -> tuple[AccountLease, ...]:
        """以 SKIP LOCKED 批量终结越过 TTL 的 Active Lease。"""

        environment_filter = "and environment_id = %s" if environment_id is not None else ""
        parameters: tuple[object, ...]
        if environment_id is None:
            parameters = (now, now, limit, now, LeaseReleaseReason.TTL_EXPIRED)
        else:
            parameters = (
                now,
                now,
                environment_id,
                limit,
                now,
                LeaseReleaseReason.TTL_EXPIRED,
            )
        cursor = await connection.execute(
            f"""
            with candidates as (
              select id
              from atlas.account_lease
              where status = 'ACTIVE'
                and (expires_at <= %s or max_expires_at <= %s)
                {environment_filter}
              order by expires_at, id
              limit %s
              for update skip locked
            )
            update atlas.account_lease lease
            set status = 'EXPIRED', released_at = %s, release_reason = %s,
                revision = lease.revision + 1
            from candidates
            where lease.id = candidates.id
            returning {', '.join(f'lease.{column.strip()}' for column in LEASE_COLUMNS.split(','))}
            """,
            parameters,
        )
        leases = tuple(AccountLease.model_validate(row) for row in await cursor.fetchall())
        for lease in leases:
            await self._mark_account_for_reverification(connection, lease)
        return tuple(sorted(leases, key=lambda item: (item.expires_at, item.id)))

    async def revoke_active(
        self,
        connection: AsyncConnection[DictRow],
        *,
        reason: LeaseReleaseReason,
        now: datetime,
        account_id: UUID | None = None,
        pool_id: UUID | None = None,
        role_id: UUID | None = None,
        environment_id: UUID | None = None,
        connector_installation_id: UUID | None = None,
    ) -> tuple[AccountLease, ...]:
        """管理状态变化时撤销作用域内活动租约并推进账号 Fence。"""

        filters = [
            value is not None
            for value in (
                account_id,
                pool_id,
                role_id,
                environment_id,
                connector_installation_id,
            )
        ]
        if sum(filters) != 1:
            raise ValueError("exactly one lease revocation scope is required")
        if account_id is not None:
            scope_filter = "account_id = %s"
            scope_value = account_id
        elif pool_id is not None:
            scope_filter = "pool_id = %s"
            scope_value = pool_id
        elif role_id is not None:
            scope_filter = (
                "pool_id in (select id from atlas.account_pool where role_id = %s)"
            )
            scope_value = role_id
        elif environment_id is not None:
            scope_filter = "environment_id = %s"
            scope_value = environment_id
        else:
            scope_filter = (
                "account_id in ("
                "select id from atlas.test_account "
                "where connector_installation_id = %s)"
            )
            assert connector_installation_id is not None
            scope_value = connector_installation_id

        cursor = await connection.execute(
            f"""
            select {LEASE_COLUMNS}
            from atlas.account_lease
            where status = 'ACTIVE' and {scope_filter}
            order by id
            for update
            """,
            (scope_value,),
        )
        active = tuple(AccountLease.model_validate(row) for row in await cursor.fetchall())
        revoked: list[AccountLease] = []
        for lease in active:
            update_cursor = await connection.execute(
                f"""
                update atlas.account_lease
                set status = 'REVOKED', released_at = %s, release_reason = %s,
                    revision = revision + 1
                where id = %s and status = 'ACTIVE'
                returning {LEASE_COLUMNS}
                """,
                (now, reason, lease.id),
            )
            row = await update_cursor.fetchone()
            if row is None:
                continue
            terminal = AccountLease.model_validate(row)
            revoked.append(terminal)
            await connection.execute(
                """
                update atlas.test_account
                set lease_epoch = lease_epoch + 1,
                    operational_status = 'VERIFYING', cooldown_until = null,
                    revision = revision + 1
                where id = %s and lease_epoch = %s
                """,
                (terminal.account_id, terminal.fencing_token),
            )
        return tuple(revoked)

    async def _get_lease_for_update(
        self,
        connection: AsyncConnection[DictRow],
        lease_id: UUID,
    ) -> AccountLease | None:
        cursor = await connection.execute(
            f"""
            select {LEASE_COLUMNS}
            from atlas.account_lease
            where id = %s
            for update
            """,
            (lease_id,),
        )
        row = await cursor.fetchone()
        return AccountLease.model_validate(row) if row is not None else None

    @staticmethod
    async def _get_account_epoch(
        connection: AsyncConnection[DictRow],
        account_id: UUID,
    ) -> int:
        cursor = await connection.execute(
            "select lease_epoch from atlas.test_account where id = %s",
            (account_id,),
        )
        row = await cursor.fetchone()
        return int(row["lease_epoch"]) if row is not None else -1

    async def _expire_locked(
        self,
        connection: AsyncConnection[DictRow],
        lease: AccountLease,
        *,
        now: datetime,
    ) -> AccountLease:
        cursor = await connection.execute(
            f"""
            update atlas.account_lease
            set status = 'EXPIRED', released_at = %s, release_reason = %s,
                revision = revision + 1
            where id = %s and status = 'ACTIVE'
            returning {LEASE_COLUMNS}
            """,
            (now, LeaseReleaseReason.TTL_EXPIRED, lease.id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("locked active lease could not be expired")
        expired = AccountLease.model_validate(row)
        await self._mark_account_for_reverification(connection, expired)
        return expired

    @staticmethod
    async def _mark_account_for_reverification(
        connection: AsyncConnection[DictRow],
        lease: AccountLease,
    ) -> None:
        await connection.execute(
            """
            update atlas.test_account
            set health_status = 'DEGRADED', operational_status = 'VERIFYING',
                cooldown_until = null, revision = revision + 1
            where id = %s and lease_epoch = %s
            """,
            (lease.account_id, lease.fencing_token),
        )

    @staticmethod
    async def _apply_release_account_state(
        connection: AsyncConnection[DictRow],
        *,
        lease: AccountLease,
        reason: LeaseReleaseReason,
        now: datetime,
    ) -> None:
        if reason is LeaseReleaseReason.CLEANUP_FAILED:
            await connection.execute(
                """
                update atlas.test_account
                set health_status = 'QUARANTINED',
                    operational_status = 'CLEANUP_FAILED', cooldown_until = null,
                    revision = revision + 1
                where id = %s and lease_epoch = %s
                """,
                (lease.account_id, lease.fencing_token),
            )
            return
        cursor = await connection.execute(
            "select cooldown_seconds from atlas.account_pool where id = %s",
            (lease.pool_id,),
        )
        row = await cursor.fetchone()
        cooldown_seconds = int(row["cooldown_seconds"]) if row is not None else 0
        if cooldown_seconds > 0:
            await connection.execute(
                """
                update atlas.test_account
                set operational_status = 'COOLDOWN', cooldown_until = %s,
                    revision = revision + 1
                where id = %s and lease_epoch = %s
                """,
                (now + timedelta(seconds=cooldown_seconds), lease.account_id, lease.fencing_token),
            )
        else:
            await connection.execute(
                """
                update atlas.test_account
                set operational_status = 'READY', cooldown_until = null,
                    revision = revision + 1
                where id = %s and lease_epoch = %s
                """,
                (lease.account_id, lease.fencing_token),
            )
