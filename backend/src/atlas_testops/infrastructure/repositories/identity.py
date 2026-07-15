"""测试角色、账号池、账号与凭证引用 PostgreSQL Repository。"""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.identity import (
    AccountAvailabilityReason,
    AccountHealth,
    AccountLifecycle,
    AccountOperationalStatus,
    AccountPool,
    AccountPoolCapacity,
    AccountPoolStatus,
    AccountStateReason,
    AccountSyncStatus,
    CreateAccountPool,
    CreateTestAccount,
    CreateTestRole,
    TestAccount,
    TestRole,
    UpdateAccountPool,
    UpdateTestAccount,
    UpdateTestRole,
    account_availability_reason,
)

ROLE_COLUMNS = (
    "id, tenant_id, project_id, role_key, name, description, capabilities, "
    "status, revision, created_at, updated_at"
)
POOL_COLUMNS = (
    "id, tenant_id, project_id, environment_id, role_id, pool_key, name, exclusive, "
    "default_ttl_seconds, cooldown_seconds, health_failure_threshold, "
    "health_retry_cooldown_seconds, status, revision, created_at, updated_at"
)
ACCOUNT_COLUMNS = (
    "a.id, a.tenant_id, a.project_id, a.environment_id, a.pool_id, "
    "a.connector_installation_id, a.account_key, a.source, a.external_subject_id, "
    "a.login_hint_masked, a.lifecycle_status, "
    "a.health_status, a.operational_status, a.sync_status, a.cooldown_until, "
    "a.consecutive_health_failures, a.last_health_checked_at, "
    "a.last_health_succeeded_at, "
    "a.lease_epoch, a.labels, a.last_leased_at, a.revision, a.created_at, a.updated_at"
)
ACCOUNT_GROUP_COLUMNS = ACCOUNT_COLUMNS + ", p.status, r.status, e.status"


class IdentityRepository:
    """只处理身份目录事实和安全投影，不处理权限或事件。"""

    async def create_role(
        self,
        connection: AsyncConnection[DictRow],
        *,
        role_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        command: CreateTestRole,
    ) -> TestRole | None:
        """创建 TestRole；作用域内 role_key 冲突时返回 None。"""

        cursor = await connection.execute(
            f"""
            insert into atlas.test_role (
              id, tenant_id, project_id, role_key, name, description, capabilities
            ) values (%s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning {ROLE_COLUMNS}
            """,
            (
                role_id,
                tenant_id,
                project_id,
                command.role_key,
                command.name,
                command.description,
                list(command.capabilities),
            ),
        )
        row = await cursor.fetchone()
        return TestRole.model_validate(row) if row is not None else None

    async def get_role(
        self,
        connection: AsyncConnection[DictRow],
        role_id: UUID,
        *,
        for_share: bool = False,
    ) -> TestRole | None:
        """读取当前 Tenant 可见的 TestRole。"""

        lock_clause = "for share" if for_share else ""
        cursor = await connection.execute(
            f"select {ROLE_COLUMNS} from atlas.test_role where id = %s {lock_clause}",
            (role_id,),
        )
        row = await cursor.fetchone()
        return TestRole.model_validate(row) if row is not None else None

    async def list_roles(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[TestRole, ...]:
        """按稳定 Cursor 列出 Project 的 TestRole。"""

        if cursor is None:
            result = await connection.execute(
                f"""
                select {ROLE_COLUMNS}
                from atlas.test_role
                where project_id = %s
                order by created_at desc, id desc
                limit %s
                """,
                (project_id, limit + 1),
            )
        else:
            result = await connection.execute(
                f"""
                select {ROLE_COLUMNS}
                from atlas.test_role
                where project_id = %s and (created_at, id) < (%s, %s)
                order by created_at desc, id desc
                limit %s
                """,
                (project_id, cursor.created_at, cursor.id, limit + 1),
            )
        return tuple(TestRole.model_validate(row) for row in await result.fetchall())

    async def update_role(
        self,
        connection: AsyncConnection[DictRow],
        *,
        role_id: UUID,
        expected_revision: int,
        command: UpdateTestRole,
    ) -> TestRole | None:
        """使用 Revision CAS 更新 TestRole。"""

        cursor = await connection.execute(
            f"""
            update atlas.test_role
            set name = coalesce(%s, name),
                description = coalesce(%s, description),
                capabilities = coalesce(%s, capabilities),
                status = coalesce(%s, status),
                revision = revision + 1
            where id = %s and revision = %s
            returning {ROLE_COLUMNS}
            """,
            (
                command.name,
                command.description,
                list(command.capabilities) if command.capabilities is not None else None,
                command.status,
                role_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return TestRole.model_validate(row) if row is not None else None

    async def create_pool(
        self,
        connection: AsyncConnection[DictRow],
        *,
        pool_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        environment_id: UUID,
        command: CreateAccountPool,
    ) -> AccountPool | None:
        """创建 AccountPool；作用域内 pool_key 冲突时返回 None。"""

        cursor = await connection.execute(
            f"""
            insert into atlas.account_pool (
              id, tenant_id, project_id, environment_id, role_id, pool_key, name,
              exclusive, default_ttl_seconds, cooldown_seconds,
              health_failure_threshold, health_retry_cooldown_seconds
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning {POOL_COLUMNS}
            """,
            (
                pool_id,
                tenant_id,
                project_id,
                environment_id,
                command.role_id,
                command.pool_key,
                command.name,
                command.exclusive,
                command.default_ttl_seconds,
                command.cooldown_seconds,
                command.health_failure_threshold,
                command.health_retry_cooldown_seconds,
            ),
        )
        row = await cursor.fetchone()
        return AccountPool.model_validate(row) if row is not None else None

    async def get_pool(
        self,
        connection: AsyncConnection[DictRow],
        pool_id: UUID,
    ) -> AccountPool | None:
        """读取当前 Tenant 可见的 AccountPool。"""

        cursor = await connection.execute(
            f"select {POOL_COLUMNS} from atlas.account_pool where id = %s",
            (pool_id,),
        )
        row = await cursor.fetchone()
        return AccountPool.model_validate(row) if row is not None else None

    async def list_pools(
        self,
        connection: AsyncConnection[DictRow],
        *,
        environment_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[AccountPool, ...]:
        """按稳定 Cursor 列出 Environment 的 AccountPool。"""

        if cursor is None:
            result = await connection.execute(
                f"""
                select {POOL_COLUMNS}
                from atlas.account_pool
                where environment_id = %s
                order by created_at desc, id desc
                limit %s
                """,
                (environment_id, limit + 1),
            )
        else:
            result = await connection.execute(
                f"""
                select {POOL_COLUMNS}
                from atlas.account_pool
                where environment_id = %s and (created_at, id) < (%s, %s)
                order by created_at desc, id desc
                limit %s
                """,
                (environment_id, cursor.created_at, cursor.id, limit + 1),
            )
        return tuple(AccountPool.model_validate(row) for row in await result.fetchall())

    async def update_pool(
        self,
        connection: AsyncConnection[DictRow],
        *,
        pool_id: UUID,
        expected_revision: int,
        command: UpdateAccountPool,
    ) -> AccountPool | None:
        """使用 Revision CAS 更新 AccountPool。"""

        cursor = await connection.execute(
            f"""
            update atlas.account_pool
            set name = coalesce(%s, name),
                default_ttl_seconds = coalesce(%s, default_ttl_seconds),
                cooldown_seconds = coalesce(%s, cooldown_seconds),
                health_failure_threshold = coalesce(%s, health_failure_threshold),
                health_retry_cooldown_seconds = coalesce(
                  %s, health_retry_cooldown_seconds
                ),
                status = coalesce(%s, status),
                revision = revision + 1
            where id = %s and revision = %s
            returning {POOL_COLUMNS}
            """,
            (
                command.name,
                command.default_ttl_seconds,
                command.cooldown_seconds,
                command.health_failure_threshold,
                command.health_retry_cooldown_seconds,
                command.status,
                pool_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return AccountPool.model_validate(row) if row is not None else None

    async def create_account(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        slot_id: UUID,
        credential_ids: tuple[UUID, ...],
        pool: AccountPool,
        command: CreateTestAccount,
        now: datetime,
    ) -> TestAccount | None:
        """原子写入账号、初始独占 Slot 和 CredentialBinding 引用。"""

        account_cursor = await connection.execute(
            """
            insert into atlas.test_account (
              id, tenant_id, project_id, environment_id, pool_id,
              connector_installation_id, account_key, source,
              external_subject_id, login_hint_masked, labels
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning id
            """,
            (
                account_id,
                pool.tenant_id,
                pool.project_id,
                pool.environment_id,
                pool.id,
                command.connector_installation_id,
                command.account_key,
                command.source,
                command.external_subject_id,
                command.login_hint_masked,
                Jsonb(command.labels),
            ),
        )
        if await account_cursor.fetchone() is None:
            return None

        await connection.execute(
            """
            insert into atlas.account_slot (
              id, tenant_id, project_id, environment_id, account_id, slot_index
            ) values (%s, %s, %s, %s, %s, 1)
            """,
            (slot_id, pool.tenant_id, pool.project_id, pool.environment_id, account_id),
        )
        for credential_id, credential in zip(
            credential_ids,
            command.credentials,
            strict=True,
        ):
            await connection.execute(
                """
                insert into atlas.credential_binding (
                  id, tenant_id, project_id, environment_id, account_id,
                  auth_method, purpose, secret_ref, secret_version, expires_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    credential_id,
                    pool.tenant_id,
                    pool.project_id,
                    pool.environment_id,
                    account_id,
                    credential.auth_method,
                    credential.purpose,
                    credential.secret_ref,
                    credential.secret_version,
                    credential.expires_at,
                ),
            )
        return await self.get_account(connection, account_id, now=now)

    async def get_account(
        self,
        connection: AsyncConnection[DictRow],
        account_id: UUID,
        *,
        now: datetime,
    ) -> TestAccount | None:
        """读取 TestAccount 的非敏感实时可用性投影。"""

        cursor = await connection.execute(
            self._account_select("a.id = %s"),
            (now, now, now, account_id),
        )
        row = await cursor.fetchone()
        return self._project_account(row, now=now) if row is not None else None

    async def list_accounts(
        self,
        connection: AsyncConnection[DictRow],
        *,
        pool_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
        now: datetime,
    ) -> tuple[TestAccount, ...]:
        """按稳定 Cursor 列出 AccountPool 的安全账号投影。"""

        if cursor is None:
            where = "a.pool_id = %s"
            parameters: tuple[object, ...] = (now, now, now, pool_id, limit + 1)
        else:
            where = "a.pool_id = %s and (a.created_at, a.id) < (%s, %s)"
            parameters = (
                now,
                now,
                now,
                pool_id,
                cursor.created_at,
                cursor.id,
                limit + 1,
            )
        result = await connection.execute(
            f"""
            {self._account_select(where)}
            order by a.created_at desc, a.id desc
            limit %s
            """,
            parameters,
        )
        return tuple(self._project_account(row, now=now) for row in await result.fetchall())

    async def update_account(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        expected_revision: int,
        command: UpdateTestAccount,
        now: datetime,
    ) -> TestAccount | None:
        """使用 Revision CAS 更新 TestAccount 非敏感字段。"""

        cursor = await connection.execute(
            """
            update atlas.test_account
            set connector_installation_id = coalesce(%s, connector_installation_id),
                login_hint_masked = coalesce(%s, login_hint_masked),
                labels = coalesce(%s, labels),
                lifecycle_status = coalesce(%s, lifecycle_status),
                health_status = case
                  when (%s)::uuid is not null
                    and (%s)::uuid is distinct from connector_installation_id
                    then case
                      when health_status = 'QUARANTINED' then health_status
                      else 'UNKNOWN'
                    end
                  else health_status
                end,
                operational_status = case
                  when (%s)::uuid is not null
                    and (%s)::uuid is distinct from connector_installation_id
                    then 'VERIFYING'
                  else operational_status
                end,
                cooldown_until = case
                  when (%s)::uuid is not null
                    and (%s)::uuid is distinct from connector_installation_id
                    then null
                  else cooldown_until
                end,
                consecutive_health_failures = case
                  when (%s)::uuid is not null
                    and (%s)::uuid is distinct from connector_installation_id
                    then case
                      when health_status = 'QUARANTINED'
                        then consecutive_health_failures
                      else 0
                    end
                  else consecutive_health_failures
                end,
                identity_fingerprint = case
                  when (%s)::uuid is not null
                    and (%s)::uuid is distinct from connector_installation_id
                    then null
                  else identity_fingerprint
                end,
                last_health_checked_at = case
                  when (%s)::uuid is not null
                    and (%s)::uuid is distinct from connector_installation_id
                    then null
                  else last_health_checked_at
                end,
                last_health_succeeded_at = case
                  when (%s)::uuid is not null
                    and (%s)::uuid is distinct from connector_installation_id
                    then null
                  else last_health_succeeded_at
                end,
                revision = revision + 1
            where id = %s and revision = %s
            returning id
            """,
            (
                command.connector_installation_id,
                command.login_hint_masked,
                Jsonb(command.labels) if command.labels is not None else None,
                command.lifecycle_status,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                command.connector_installation_id,
                account_id,
                expected_revision,
            ),
        )
        if await cursor.fetchone() is None:
            return None
        if command.lifecycle_status is AccountLifecycle.RETIRED:
            await connection.execute(
                """
                update atlas.credential_binding
                set status = 'REVOKED', revision = revision + 1
                where account_id = %s and status = 'ACTIVE'
                """,
                (account_id,),
            )
            await connection.execute(
                """
                update atlas.account_slot set status = 'DISABLED'
                where account_id = %s and status = 'ACTIVE'
                """,
                (account_id,),
            )
        return await self.get_account(connection, account_id, now=now)

    async def quarantine_account(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        expected_revision: int,
        command: AccountStateReason,
        now: datetime,
    ) -> TestAccount | None:
        """隔离账号；原因由应用层写入不可变审计。"""

        del command
        cursor = await connection.execute(
            """
            update atlas.test_account
            set health_status = 'QUARANTINED',
                operational_status = 'VERIFYING',
                cooldown_until = null,
                revision = revision + 1
            where id = %s and revision = %s and health_status <> 'QUARANTINED'
            returning id
            """,
            (account_id, expected_revision),
        )
        if await cursor.fetchone() is None:
            return None
        return await self.get_account(connection, account_id, now=now)

    async def restore_account(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        expected_revision: int,
        command: AccountStateReason,
        now: datetime,
    ) -> TestAccount | None:
        """解除隔离后回到 UNKNOWN / VERIFYING，重新验证前不可租用。"""

        del command
        cursor = await connection.execute(
            """
            update atlas.test_account
            set health_status = 'UNKNOWN',
                operational_status = 'VERIFYING',
                cooldown_until = null,
                revision = revision + 1
            where id = %s and revision = %s and health_status = 'QUARANTINED'
            returning id
            """,
            (account_id, expected_revision),
        )
        if await cursor.fetchone() is None:
            return None
        return await self.get_account(connection, account_id, now=now)

    async def get_capacity(
        self,
        connection: AsyncConnection[DictRow],
        *,
        pool_id: UUID,
        now: datetime,
    ) -> AccountPoolCapacity:
        """聚合当前池的 Slot 与正交状态容量。"""

        cursor = await connection.execute(
            """
            select
              %s::uuid as pool_id,
              count(s.id)::integer as total_slots,
              count(s.id) filter (
                where p.status = 'ACTIVE'
                  and r.status = 'ACTIVE'
                  and e.status = 'ACTIVE'
                  and ci.status = 'ACTIVE'
                  and a.lifecycle_status = 'ACTIVE'
                  and a.health_status = 'HEALTHY'
                  and a.operational_status = 'READY'
                  and a.sync_status not in ('CONFLICT', 'TOMBSTONED')
                  and (a.cooldown_until is null or a.cooldown_until <= %s)
                  and s.status = 'ACTIVE'
                  and exists (
                    select 1 from atlas.credential_binding c
                    where c.account_id = a.id and c.status = 'ACTIVE'
                      and (c.expires_at is null or c.expires_at > %s)
                  )
                  and active_lease.id is null
              )::integer as available_slots,
              count(s.id) filter (
                where active_lease.id is not null
              )::integer as leased_slots,
              count(distinct a.id) filter (
                where a.operational_status = 'COOLDOWN'
                  and a.cooldown_until > %s
              )::integer as cooldown_accounts,
              count(distinct a.id) filter (
                where a.health_status = 'QUARANTINED'
              )::integer as quarantined_accounts,
              count(distinct a.id) filter (
                where a.lifecycle_status <> 'ACTIVE'
                   or a.health_status = 'UNKNOWN'
                   or a.operational_status = 'VERIFYING'
              )::integer as unverified_accounts
            from atlas.account_pool p
            join atlas.test_role r on r.id = p.role_id
            join atlas.environment e on e.id = p.environment_id
            left join atlas.test_account a on a.pool_id = p.id
            left join atlas.connector_installation ci
              on ci.id = a.connector_installation_id
            left join atlas.account_slot s on s.account_id = a.id
            left join atlas.account_lease active_lease
              on active_lease.slot_id = s.id
             and active_lease.status = 'ACTIVE'
             and active_lease.expires_at > %s
            where p.id = %s
            group by p.id
            """,
            (pool_id, now, now, now, now, pool_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return AccountPoolCapacity(
                pool_id=pool_id,
                total_slots=0,
                available_slots=0,
                leased_slots=0,
                cooldown_accounts=0,
                quarantined_accounts=0,
                unverified_accounts=0,
            )
        return AccountPoolCapacity.model_validate(row)

    @staticmethod
    def _account_select(where: str) -> str:
        return f"""
            select {ACCOUNT_COLUMNS}, p.status as pool_status,
              r.status as role_status, e.status as environment_status,
              ci.status as connector_status,
              coalesce(
                array_agg(distinct c.auth_method order by c.auth_method)
                  filter (
                    where c.status = 'ACTIVE'
                      and (c.expires_at is null or c.expires_at > %s)
                  ),
                array[]::text[]
              ) as auth_methods,
              bool_or(
                c.status = 'ACTIVE' and (c.expires_at is null or c.expires_at > %s)
              ) as credential_valid,
              bool_or(s.status = 'ACTIVE') as slot_available,
              bool_or(active_lease.id is not null) as active_lease
            from atlas.test_account a
            join atlas.account_pool p on p.id = a.pool_id
            join atlas.test_role r on r.id = p.role_id
            join atlas.environment e on e.id = p.environment_id
            left join atlas.connector_installation ci
              on ci.id = a.connector_installation_id
            left join atlas.credential_binding c on c.account_id = a.id
            left join atlas.account_slot s on s.account_id = a.id
            left join atlas.account_lease active_lease
              on active_lease.slot_id = s.id
             and active_lease.status = 'ACTIVE'
             and active_lease.expires_at > %s
            where {where}
            group by {ACCOUNT_GROUP_COLUMNS}, ci.status
        """

    @staticmethod
    def _project_account(row: DictRow, *, now: datetime) -> TestAccount:
        pool_context_enabled = (
            row["pool_status"] == "ACTIVE"
            and row["role_status"] == "ACTIVE"
            and row["environment_status"] == "ACTIVE"
        )
        reason = account_availability_reason(
            pool_status=(
                AccountPoolStatus.ACTIVE if pool_context_enabled else AccountPoolStatus.DISABLED
            ),
            lifecycle_status=AccountLifecycle(row["lifecycle_status"]),
            health_status=AccountHealth(row["health_status"]),
            operational_status=AccountOperationalStatus(row["operational_status"]),
            sync_status=AccountSyncStatus(row["sync_status"]),
            cooldown_until=row["cooldown_until"],
            credential_valid=bool(row["credential_valid"]),
            slot_available=bool(row["slot_available"]),
            active_lease=bool(row["active_lease"]),
            now=now,
            connector_available=row["connector_status"] == "ACTIVE",
        )
        payload = dict(row)
        payload.pop("pool_status", None)
        payload.pop("role_status", None)
        payload.pop("environment_status", None)
        payload.pop("connector_status", None)
        payload.pop("slot_available", None)
        payload.pop("active_lease", None)
        payload["credential_valid"] = bool(row["credential_valid"])
        payload["available"] = reason is AccountAvailabilityReason.AVAILABLE
        payload["availability_reason"] = reason
        return TestAccount.model_validate(payload)
