"""ConnectorInstallation 管理、验证与实际 Capability 投影服务。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Never, cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.ports.providers import AdapterContext, AdapterOperationError
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.core.contracts import WireModel, new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import decode_cursor, next_time_cursor
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.identity import (
    AccountLease,
    AccountStateTransitionReason,
    AdapterManifest,
    CapabilityRequirement,
    ConnectorInstallation,
    ConnectorInstallationPage,
    ConnectorInstallationRecord,
    ConnectorMode,
    ConnectorStatus,
    CreateConnectorInstallation,
    LeaseReleaseReason,
    NegotiatedCapabilities,
    ProviderCapability,
    ProviderHealth,
    ProviderHealthState,
    UpdateConnectorInstallation,
)
from atlas_testops.domain.platform import Environment, EnvironmentKind, EnvironmentStatus
from atlas_testops.infrastructure.adapters.registry import (
    AdapterNotRegisteredError,
    AdapterRegistry,
)
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
)
from atlas_testops.infrastructure.repositories.connectors import ConnectorRepository
from atlas_testops.infrastructure.repositories.leases import LeaseRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository

CONNECTOR_IDEMPOTENCY_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ConnectorCommandResult:
    """携带 Connector 创建命令的幂等重放信息。"""

    value: ConnectorInstallation
    status_code: int
    replayed: bool


class ConnectorService:
    """协调 Connector 权限、验证快照、审计、Outbox 与 Lease 失效。"""

    def __init__(
        self,
        database: Database,
        registry: AdapterRegistry,
        *,
        connector_repository: ConnectorRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        lease_repository: LeaseRepository | None = None,
        account_health_repository: AccountHealthRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
    ) -> None:
        self._database = database
        self._registry = registry
        self._connectors = connector_repository or ConnectorRepository()
        self._platform = platform_repository or PlatformRepository()
        self._leases = lease_repository or LeaseRepository()
        self._account_health = account_health_repository or AccountHealthRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()

    async def create(
        self,
        actor: ActorContext,
        command: CreateConnectorInstallation,
        *,
        idempotency_key: str,
    ) -> ConnectorCommandResult:
        """创建不暴露配置引用的 DRAFT Connector。"""

        if not self._registry.supports(command.adapter_key):
            self._raise_adapter_unavailable(command.adapter_key)
        now = utc_now()
        request_hash = hash_request(command.model_dump(mode="json", by_alias=True))
        scope = f"environments.{command.environment_id}.connectors.create"
        async with self._database.transaction(actor.database_context()) as connection:
            environment = await self._platform.get_environment_for_share(
                connection,
                command.environment_id,
            )
            self._require_environment(actor, environment, manage=True)
            assert environment is not None
            self._validate_policy(
                environment,
                mode=command.mode,
                origins=command.allowed_origins,
                capabilities=command.required_capabilities,
            )
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=CONNECTOR_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return ConnectorCommandResult(
                    value=ConnectorInstallation.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            record = await self._connectors.create(
                connection,
                connector_id=new_entity_id(),
                tenant_id=actor.tenant_id,
                project_id=environment.project_id,
                command=command,
            )
            if record is None:
                raise self._conflict(
                    "Connector Key 已存在",
                    "同一 Environment 内的 installationKey 必须唯一。",
                )
            connector = record.to_public(())
            await self._record_connector_event(
                connection,
                actor=actor,
                connector=connector,
                event_type="connector_installation.created",
                occurred_at=now,
            )
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                response=CachedHttpResponse(
                    status_code=201,
                    body=self._json_object(connector),
                ),
            )
            return ConnectorCommandResult(
                value=connector,
                status_code=201,
                replayed=False,
            )

    async def list(
        self,
        actor: ActorContext,
        environment_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> ConnectorInstallationPage:
        """列出 Environment 中不含配置引用的 Connector。"""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            environment = await self._platform.get_environment(connection, environment_id)
            self._require_environment(actor, environment, manage=False)
            records = await self._connectors.list_records(
                connection,
                environment_id=environment_id,
                cursor=decoded,
                limit=limit,
            )
            page_records = records[:limit]
            capability_map = await self._connectors.get_capabilities_by_connector(
                connection,
                tuple(record.id for record in page_records),
            )
        items = tuple(
            record.to_public(capability_map.get(record.id, ())) for record in page_records
        )
        next_cursor = None
        if len(records) > limit and items:
            next_cursor = next_time_cursor(items[-1].created_at, items[-1].id)
        return ConnectorInstallationPage(items=items, next_cursor=next_cursor)

    async def get(
        self,
        actor: ActorContext,
        connector_id: UUID,
    ) -> ConnectorInstallation:
        """读取单个 Connector 安全投影。"""

        async with self._database.transaction(actor.database_context()) as connection:
            record = await self._connectors.get_record(connection, connector_id)
            self._require_connector(actor, record, manage=False)
            assert record is not None
            capabilities = await self._connectors.get_capabilities(connection, record.id)
            return record.to_public(capabilities)

    async def update(
        self,
        actor: ActorContext,
        connector_id: UUID,
        command: UpdateConnectorInstallation,
        *,
        expected_revision: int,
    ) -> ConnectorInstallation:
        """以 Revision CAS 更新 Connector，并撤销失去安全前提的 Lease。"""

        now = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            snapshot = await self._connectors.get_record(connection, connector_id)
            self._require_connector(actor, snapshot, manage=True)
            assert snapshot is not None
            environment = await self._platform.get_environment_for_share(
                connection,
                snapshot.environment_id,
            )
            self._require_environment(actor, environment, manage=True)
            assert environment is not None
            current = await self._connectors.get_record_for_update(
                connection,
                connector_id,
            )
            if current is None:
                raise self._not_found("ConnectorInstallation 不存在。")
            if current.revision != expected_revision:
                raise self._revision_conflict(current.revision)
            effective_mode = command.mode or current.mode
            effective_origins = command.allowed_origins or current.allowed_origins
            effective_capabilities = command.required_capabilities or current.required_capabilities
            self._validate_policy(
                environment,
                mode=effective_mode,
                origins=effective_origins,
                capabilities=effective_capabilities,
            )
            updated = await self._connectors.update(
                connection,
                connector_id=connector_id,
                expected_revision=expected_revision,
                command=command,
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            if self._requires_revocation(current, updated):
                await self._invalidate_connector_dependents(
                    connection,
                    actor=actor,
                    connector_id=connector_id,
                    occurred_at=now,
                )
            capabilities = await self._connectors.get_capabilities(connection, updated.id)
            connector = updated.to_public(capabilities)
            await self._record_connector_event(
                connection,
                actor=actor,
                connector=connector,
                event_type="connector_installation.updated",
                occurred_at=now,
            )
            return connector

    async def validate(
        self,
        actor: ActorContext,
        connector_id: UUID,
        *,
        expected_revision: int,
    ) -> ConnectorInstallation:
        """在事务外 Probe，再以原 Revision CAS 原子写入验证快照。"""

        snapshot = await self._validation_snapshot(
            actor,
            connector_id,
            expected_revision=expected_revision,
        )
        manifest, health, negotiated = await self._probe(snapshot, actor.request_id)
        validated_at = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            environment = await self._platform.get_environment_for_share(
                connection,
                snapshot.environment_id,
            )
            self._require_environment(actor, environment, manage=True)
            assert environment is not None
            if environment.status is not EnvironmentStatus.ACTIVE:
                raise self._conflict(
                    "Environment 不可用",
                    "只有 ACTIVE Environment 可以验证 Connector。",
                )
            self._validate_policy(
                environment,
                mode=snapshot.mode,
                origins=snapshot.allowed_origins,
                capabilities=snapshot.required_capabilities,
            )
            current = await self._connectors.get_record_for_update(
                connection,
                connector_id,
            )
            self._require_connector(actor, current, manage=True)
            assert current is not None
            if current.status is ConnectorStatus.DISABLED:
                raise self._conflict(
                    "Connector 已禁用",
                    "请先把 Connector 调整为 DRAFT 后再验证。",
                )
            if current.revision != expected_revision:
                raise self._revision_conflict(current.revision)
            updated = await self._connectors.finalize_validation(
                connection,
                connector_id=connector_id,
                expected_revision=expected_revision,
                manifest=manifest,
                health=health,
                negotiated=negotiated,
                validated_at=validated_at,
            )
            if updated is None:
                raise self._revision_conflict(current.revision)
            if self._requires_revocation(current, updated):
                await self._invalidate_connector_dependents(
                    connection,
                    actor=actor,
                    connector_id=connector_id,
                    occurred_at=validated_at,
                )
            capabilities = await self._connectors.get_capabilities(connection, updated.id)
            connector = updated.to_public(capabilities)
            await self._record_connector_event(
                connection,
                actor=actor,
                connector=connector,
                event_type="connector_installation.validated",
                occurred_at=validated_at,
            )
            return connector

    async def _validation_snapshot(
        self,
        actor: ActorContext,
        connector_id: UUID,
        *,
        expected_revision: int,
    ) -> ConnectorInstallationRecord:
        """短事务读取验证输入，不跨外部 I/O 持有连接或行锁。"""

        async with self._database.transaction(actor.database_context()) as connection:
            record = await self._connectors.get_record(connection, connector_id)
            self._require_connector(actor, record, manage=True)
            assert record is not None
            if record.status is ConnectorStatus.DISABLED:
                raise self._conflict(
                    "Connector 已禁用",
                    "请先把 Connector 调整为 DRAFT 后再验证。",
                )
            if record.revision != expected_revision:
                raise self._revision_conflict(record.revision)
            return record

    async def _probe(
        self,
        connector: ConnectorInstallationRecord,
        request_id: str,
    ) -> tuple[AdapterManifest, ProviderHealth, NegotiatedCapabilities]:
        """调用可信 Adapter，且只把低基数安全结果带回数据库。"""

        try:
            adapter = self._registry.resolve(connector)
        except AdapterNotRegisteredError:
            self._raise_adapter_unavailable(connector.adapter_key)
        manifest = adapter.manifest()
        context = AdapterContext(
            tenant_id=connector.tenant_id,
            project_id=connector.project_id,
            environment_id=connector.environment_id,
            origin=connector.allowed_origins[0],
            request_id=request_id,
        )
        try:
            health = await adapter.probe(context)
            if health.state is ProviderHealthState.UNAVAILABLE:
                return manifest, health, NegotiatedCapabilities(capabilities=())
            negotiated = await adapter.negotiate(
                context,
                CapabilityRequirement(required=connector.required_capabilities),
            )
            if connector.mode is ConnectorMode.OBSERVE_ONLY:
                observe_capabilities = {
                    ProviderCapability.ACCOUNT_DISCOVER,
                    ProviderCapability.ACCOUNT_READ,
                }
                negotiated = NegotiatedCapabilities(
                    capabilities=tuple(
                        item
                        for item in negotiated.capabilities
                        if item.name in observe_capabilities
                    )
                )
            available = {item.name for item in negotiated.capabilities}
            if not set(connector.required_capabilities).issubset(available):
                return (
                    manifest,
                    ProviderHealth(
                        state=ProviderHealthState.UNAVAILABLE,
                        safe_message="adapter did not negotiate every required capability",
                    ),
                    NegotiatedCapabilities(capabilities=()),
                )
            return manifest, health, negotiated
        except AdapterOperationError as error:
            return (
                manifest,
                ProviderHealth(
                    state=ProviderHealthState.UNAVAILABLE,
                    safe_message=error.error.safe_message,
                ),
                NegotiatedCapabilities(capabilities=()),
            )
        except Exception:
            return (
                manifest,
                ProviderHealth(
                    state=ProviderHealthState.UNAVAILABLE,
                    safe_message="connector validation failed safely",
                ),
                NegotiatedCapabilities(capabilities=()),
            )

    async def _record_connector_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        connector: ConnectorInstallation,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "environmentId": str(connector.environment_id),
            "installationKey": connector.installation_key,
            "adapterKey": connector.adapter_key,
            "mode": connector.mode.value,
            "status": connector.status.value,
            "revision": connector.revision,
            "capabilities": [item.name.value for item in connector.negotiated_capabilities],
        }
        if connector.health_state is not None:
            payload["healthState"] = connector.health_state.value
        await self._audit.append(
            connection,
            tenant_id=actor.tenant_id,
            project_id=connector.project_id,
            environment_id=connector.environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="connector_installation",
            entity_id=connector.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=actor.tenant_id,
                aggregate_type="connector_installation",
                aggregate_id=connector.id,
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
            payload: dict[str, JsonValue] = {
                "accountId": str(lease.account_id),
                "executionId": lease.execution_id,
                "fencingToken": lease.fencing_token,
                "releaseReason": LeaseReleaseReason.CONNECTOR_DISABLED.value,
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

    async def _invalidate_connector_dependents(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        connector_id: UUID,
        occurred_at: datetime,
    ) -> None:
        """Fence active leases and require every dependent account to verify again."""

        revoked = await self._leases.revoke_active(
            connection,
            reason=LeaseReleaseReason.CONNECTOR_DISABLED,
            now=occurred_at,
            connector_installation_id=connector_id,
        )
        await self._record_lease_revocations(
            connection,
            actor=actor,
            leases=revoked,
            occurred_at=occurred_at,
        )
        changes = await self._account_health.invalidate_connector_accounts(
            connection,
            connector_id=connector_id,
        )
        for change in changes:
            await self._account_health.append_transition(
                connection,
                tenant_id=change.tenant_id,
                project_id=change.project_id,
                environment_id=change.environment_id,
                account_id=change.account_id,
                health_check_id=None,
                reason=AccountStateTransitionReason.MANAGEMENT_REVOCATION,
                before=change.before,
                after=change.after,
                safe_summary="Connector 状态或配置变化，账号需要重新验证。",
                actor_id=actor.actor_id,
                request_id=actor.request_id,
                occurred_at=occurred_at,
            )

    @staticmethod
    def _requires_revocation(
        current: ConnectorInstallationRecord,
        updated: ConnectorInstallationRecord,
    ) -> bool:
        """离开 ACTIVE 或替换安全配置时撤销该 Connector 的 Lease。"""

        configuration_changed = (
            current.mode != updated.mode
            or current.configuration_ref != updated.configuration_ref
            or current.allowed_origins != updated.allowed_origins
            or current.required_capabilities != updated.required_capabilities
        )
        return configuration_changed or (
            current.status is ConnectorStatus.ACTIVE
            and updated.status is not ConnectorStatus.ACTIVE
        )

    @staticmethod
    def _validate_policy(
        environment: Environment,
        *,
        mode: ConnectorMode,
        origins: tuple[str, ...],
        capabilities: tuple[ProviderCapability, ...],
    ) -> None:
        if not set(origins).issubset(environment.allowed_origins):
            raise ApplicationError(
                error_code=ErrorCode.ORIGIN_NOT_ALLOWED,
                title="Connector Origin 不在允许列表",
                detail="Connector Origin 必须是 Environment Origin 的子集。",
                status_code=400,
            )
        if (
            environment.kind is EnvironmentKind.PRODUCTION
            and mode is not ConnectorMode.OBSERVE_ONLY
        ):
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="生产 Connector 模式受限",
                detail="Production Environment 只允许 OBSERVE_ONLY Connector。",
                status_code=403,
            )
        if mode is ConnectorMode.OBSERVE_ONLY and any(
            capability
            not in {
                ProviderCapability.ACCOUNT_DISCOVER,
                ProviderCapability.ACCOUNT_READ,
            }
            for capability in capabilities
        ):
            raise ApplicationError(
                error_code=ErrorCode.INVALID_REQUEST,
                title="Connector Capability 与模式冲突",
                detail="OBSERVE_ONLY Connector 不能要求写入或认证能力。",
                status_code=400,
            )

    @staticmethod
    def _require_environment(
        actor: ActorContext,
        environment: Environment | None,
        *,
        manage: bool,
    ) -> None:
        if environment is None or not actor.can_read_project(environment.project_id):
            raise ConnectorService._not_found("Environment 不存在。")
        if manage and not actor.can_manage_project(environment.project_id):
            raise ConnectorService._forbidden("当前角色不能管理该 Connector。")

    @staticmethod
    def _require_connector(
        actor: ActorContext,
        connector: ConnectorInstallationRecord | None,
        *,
        manage: bool,
    ) -> None:
        if connector is None or not actor.can_read_project(connector.project_id):
            raise ConnectorService._not_found("ConnectorInstallation 不存在。")
        if manage and not actor.can_manage_project(connector.project_id):
            raise ConnectorService._forbidden("当前角色不能管理该 Connector。")

    @staticmethod
    def _raise_adapter_unavailable(adapter_key: str) -> Never:
        raise ApplicationError(
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            title="Adapter 未安装",
            detail=f"当前部署没有安装 {adapter_key} Adapter。",
            status_code=503,
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
            detail="Connector 已被其他请求修改，请重新读取后再验证或提交。",
            status_code=412,
            headers={"ETag": format_revision_etag(current_revision)},
        )
