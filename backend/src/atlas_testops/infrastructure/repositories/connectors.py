"""ConnectorInstallation 与实际 Capability 投影 Repository。"""

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.identity import (
    AdapterManifest,
    CapabilityDescriptor,
    ConnectorInstallationRecord,
    ConnectorStatus,
    CreateConnectorInstallation,
    NegotiatedCapabilities,
    ProviderHealth,
    ProviderHealthState,
    UpdateConnectorInstallation,
)

CONNECTOR_COLUMNS = (
    "id, tenant_id, project_id, environment_id, installation_key, name, "
    "adapter_key, mode, configuration_ref, allowed_origins, "
    "required_capabilities, status, health_state, safe_message, "
    "protocol_version, implementation_version, last_validated_at, revision, "
    "created_at, updated_at"
)


class ConnectorRepository:
    """只持久化 Connector 权威记录和协商后的当前能力。"""

    async def create(
        self,
        connection: AsyncConnection[DictRow],
        *,
        connector_id: UUID,
        tenant_id: UUID,
        project_id: UUID,
        command: CreateConnectorInstallation,
    ) -> ConnectorInstallationRecord | None:
        """创建 DRAFT Connector；作用域 Key 冲突时返回 None。"""

        cursor = await connection.execute(
            f"""
            insert into atlas.connector_installation (
              id, tenant_id, project_id, environment_id, installation_key,
              name, adapter_key, mode, configuration_ref, allowed_origins,
              required_capabilities
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            on conflict do nothing
            returning {CONNECTOR_COLUMNS}
            """,
            (
                connector_id,
                tenant_id,
                project_id,
                command.environment_id,
                command.installation_key,
                command.name,
                command.adapter_key,
                command.mode,
                command.configuration_ref,
                list(command.allowed_origins),
                list(command.required_capabilities),
            ),
        )
        row = await cursor.fetchone()
        return self._record(row)

    async def get_record(
        self,
        connection: AsyncConnection[DictRow],
        connector_id: UUID,
    ) -> ConnectorInstallationRecord | None:
        """读取当前 Tenant 可见的内部 Connector 记录。"""

        return await self._get_record(connection, connector_id, lock_clause="")

    async def get_record_for_share(
        self,
        connection: AsyncConnection[DictRow],
        connector_id: UUID,
    ) -> ConnectorInstallationRecord | None:
        """共享锁定 Connector，阻止敏感操作与禁用或重配置交错。"""

        return await self._get_record(connection, connector_id, lock_clause="for share")

    async def get_record_for_update(
        self,
        connection: AsyncConnection[DictRow],
        connector_id: UUID,
    ) -> ConnectorInstallationRecord | None:
        """排他锁定 Connector，供状态变更与验证结果落库。"""

        return await self._get_record(connection, connector_id, lock_clause="for update")

    async def get_for_account_share(
        self,
        connection: AsyncConnection[DictRow],
        account_id: UUID,
    ) -> ConnectorInstallationRecord | None:
        """按账号绑定关系共享锁定 Connector。"""

        cursor = await connection.execute(
            f"""
            select {', '.join(f'c.{item.strip()}' for item in CONNECTOR_COLUMNS.split(','))}
            from atlas.test_account a
            join atlas.connector_installation c
              on c.id = a.connector_installation_id
            where a.id = %s
            for share of c
            """,
            (account_id,),
        )
        return self._record(await cursor.fetchone())

    async def list_records(
        self,
        connection: AsyncConnection[DictRow],
        *,
        environment_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[ConnectorInstallationRecord, ...]:
        """按稳定 Cursor 读取一个 Environment 的 Connector。"""

        if cursor is None:
            parameters: tuple[object, ...] = (environment_id, limit + 1)
            cursor_filter = ""
        else:
            parameters = (
                environment_id,
                cursor.created_at,
                cursor.id,
                limit + 1,
            )
            cursor_filter = "and (created_at, id) < (%s, %s)"
        result = await connection.execute(
            f"""
            select {CONNECTOR_COLUMNS}
            from atlas.connector_installation
            where environment_id = %s {cursor_filter}
            order by created_at desc, id desc
            limit %s
            """,
            parameters,
        )
        return tuple(
            ConnectorInstallationRecord.model_validate(row)
            for row in await result.fetchall()
        )

    async def get_capabilities(
        self,
        connection: AsyncConnection[DictRow],
        connector_id: UUID,
    ) -> tuple[CapabilityDescriptor, ...]:
        """读取 Connector 最近一次验证写入的实际能力。"""

        result = await connection.execute(
            """
            select name, version, mode
            from atlas.connector_capability
            where connector_installation_id = %s
            order by name
            """,
            (connector_id,),
        )
        return tuple(
            CapabilityDescriptor.model_validate(row) for row in await result.fetchall()
        )

    async def has_origin_dependency(
        self,
        connection: AsyncConnection[DictRow],
        *,
        environment_id: UUID,
        allowed_origins: tuple[str, ...],
    ) -> bool:
        """判断 Environment 缩减 Origin 是否会破坏现有 Connector。"""

        cursor = await connection.execute(
            """
            select 1
            from atlas.connector_installation
            where environment_id = %s
              and not allowed_origins <@ %s::text[]
            limit 1
            """,
            (environment_id, list(allowed_origins)),
        )
        return await cursor.fetchone() is not None

    async def get_capabilities_by_connector(
        self,
        connection: AsyncConnection[DictRow],
        connector_ids: tuple[UUID, ...],
    ) -> dict[UUID, tuple[CapabilityDescriptor, ...]]:
        """一次读取 Page 内全部能力，避免逐行查询。"""

        capabilities: dict[UUID, list[CapabilityDescriptor]] = {
            connector_id: [] for connector_id in connector_ids
        }
        if not connector_ids:
            return {}
        result = await connection.execute(
            """
            select connector_installation_id, name, version, mode
            from atlas.connector_capability
            where connector_installation_id = any(%s)
            order by connector_installation_id, name
            """,
            (list(connector_ids),),
        )
        for row in await result.fetchall():
            connector_id = UUID(str(row["connector_installation_id"]))
            capabilities[connector_id].append(
                CapabilityDescriptor(
                    name=row["name"],
                    version=row["version"],
                    mode=row["mode"],
                )
            )
        return {
            connector_id: tuple(items)
            for connector_id, items in capabilities.items()
        }

    async def update(
        self,
        connection: AsyncConnection[DictRow],
        *,
        connector_id: UUID,
        expected_revision: int,
        command: UpdateConnectorInstallation,
    ) -> ConnectorInstallationRecord | None:
        """使用 Revision CAS 更新配置，并让变更后的配置重新验证。"""

        configuration_changed = any(
            value is not None
            for value in (
                command.mode,
                command.configuration_ref,
                command.allowed_origins,
                command.required_capabilities,
            )
        )
        reset_validation = configuration_changed or command.status is ConnectorStatus.DRAFT
        target_status = (
            ConnectorStatus.DISABLED
            if configuration_changed and command.status is ConnectorStatus.DISABLED
            else ConnectorStatus.DRAFT
            if configuration_changed
            else command.status
        )
        cursor = await connection.execute(
            f"""
            update atlas.connector_installation
            set name = coalesce(%s, name),
                mode = coalesce(%s, mode),
                configuration_ref = coalesce(%s, configuration_ref),
                allowed_origins = coalesce(%s, allowed_origins),
                required_capabilities = coalesce(%s, required_capabilities),
                status = coalesce(%s, status),
                health_state = case when %s then null else health_state end,
                safe_message = case when %s then null else safe_message end,
                protocol_version = case when %s then null else protocol_version end,
                implementation_version = case when %s then null else implementation_version end,
                last_validated_at = case when %s then null else last_validated_at end,
                revision = revision + 1
            where id = %s and revision = %s
            returning {CONNECTOR_COLUMNS}
            """,
            (
                command.name,
                command.mode,
                command.configuration_ref,
                (
                    list(command.allowed_origins)
                    if command.allowed_origins is not None
                    else None
                ),
                (
                    list(command.required_capabilities)
                    if command.required_capabilities is not None
                    else None
                ),
                target_status,
                reset_validation,
                reset_validation,
                reset_validation,
                reset_validation,
                reset_validation,
                connector_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if reset_validation:
            await connection.execute(
                "delete from atlas.connector_capability "
                "where connector_installation_id = %s",
                (connector_id,),
            )
        return ConnectorInstallationRecord.model_validate(row)

    async def finalize_validation(
        self,
        connection: AsyncConnection[DictRow],
        *,
        connector_id: UUID,
        expected_revision: int,
        manifest: AdapterManifest,
        health: ProviderHealth,
        negotiated: NegotiatedCapabilities,
        validated_at: datetime,
    ) -> ConnectorInstallationRecord | None:
        """CAS 写入一次外部 Probe 的安全结果和实际能力快照。"""

        target_status = self._status_for_health(health.state)
        cursor = await connection.execute(
            f"""
            update atlas.connector_installation
            set status = %s, health_state = %s, safe_message = %s,
                protocol_version = %s, implementation_version = %s,
                last_validated_at = %s, revision = revision + 1
            where id = %s and revision = %s and status <> 'DISABLED'
            returning {CONNECTOR_COLUMNS}
            """,
            (
                target_status,
                health.state,
                health.safe_message,
                manifest.protocol_version,
                manifest.implementation_version,
                validated_at,
                connector_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        await connection.execute(
            "delete from atlas.connector_capability "
            "where connector_installation_id = %s",
            (connector_id,),
        )
        for capability in negotiated.capabilities:
            await connection.execute(
                """
                insert into atlas.connector_capability (
                  connector_installation_id, tenant_id, project_id,
                  environment_id, name, version, mode, observed_at
                )
                select id, tenant_id, project_id, environment_id, %s, %s, %s, %s
                from atlas.connector_installation
                where id = %s
                """,
                (
                    capability.name,
                    capability.version,
                    capability.mode,
                    validated_at,
                    connector_id,
                ),
            )
        return ConnectorInstallationRecord.model_validate(row)

    async def _get_record(
        self,
        connection: AsyncConnection[DictRow],
        connector_id: UUID,
        *,
        lock_clause: str,
    ) -> ConnectorInstallationRecord | None:
        cursor = await connection.execute(
            f"""
            select {CONNECTOR_COLUMNS}
            from atlas.connector_installation
            where id = %s
            {lock_clause}
            """,
            (connector_id,),
        )
        return self._record(await cursor.fetchone())

    @staticmethod
    def _record(row: DictRow | None) -> ConnectorInstallationRecord | None:
        return ConnectorInstallationRecord.model_validate(row) if row is not None else None

    @staticmethod
    def _status_for_health(state: ProviderHealthState) -> ConnectorStatus:
        if state is ProviderHealthState.HEALTHY:
            return ConnectorStatus.ACTIVE
        if state is ProviderHealthState.DEGRADED:
            return ConnectorStatus.DEGRADED
        return ConnectorStatus.DRAFT
