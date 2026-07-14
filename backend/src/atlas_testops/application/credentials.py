"""Secret Grant 签发、原子消费、Adapter 调用与安全审计服务。"""

from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from re import fullmatch
from secrets import token_urlsafe
from typing import Never
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.account_health import (
    AccountHealthService,
    decide_health_failure,
    health_failure_outcome,
    map_adapter_health_failure,
)
from atlas_testops.application.ports.providers import AdapterContext, AdapterOperationError
from atlas_testops.application.ports.secrets import (
    PasswordSecretScope,
    SecretProvider,
    SecretProviderError,
)
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.identity import (
    AccountHealthCheckStatus,
    AccountHealthCheckTrigger,
    AccountHealthFailureCode,
    AccountLease,
    AccountSource,
    AccountStateTransitionReason,
    AdapterError,
    AdapterErrorCode,
    ConnectorInstallationRecord,
    ConnectorStatus,
    CredentialPurpose,
    IssueSecretGrant,
    LeaseReleaseReason,
    ProviderCapability,
    RedeemSecretGrant,
    SecretGrant,
    SecretGrantReceipt,
    SecretGrantRecord,
)
from atlas_testops.domain.platform import Environment, EnvironmentKind, EnvironmentStatus
from atlas_testops.infrastructure.adapters.generic_password import GenericPasswordAdapter
from atlas_testops.infrastructure.adapters.registry import (
    AdapterNotRegisteredError,
    AdapterRegistry,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.account_health import AccountHealthRepository
from atlas_testops.infrastructure.repositories.connectors import ConnectorRepository
from atlas_testops.infrastructure.repositories.leases import (
    LeaseMutationKind,
    LeaseRepository,
)
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.secret_grants import (
    CredentialSecretAccess,
    SecretGrantClaimKind,
    SecretGrantRepository,
)

DEFAULT_SECRET_GRANT_TTL = timedelta(seconds=60)


class CredentialBrokerService:
    """保证 Grant 可撤销、不可重放，且秘密不进入 HTTP 或持久化事实。"""

    def __init__(
        self,
        database: Database,
        *,
        grant_repository: SecretGrantRepository | None = None,
        lease_repository: LeaseRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        secret_provider: SecretProvider | None = None,
        password_adapter: GenericPasswordAdapter | None = None,
        adapter_registry: AdapterRegistry | None = None,
        connector_repository: ConnectorRepository | None = None,
        account_health_repository: AccountHealthRepository | None = None,
        grant_ttl: timedelta = DEFAULT_SECRET_GRANT_TTL,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if grant_ttl <= timedelta(0) or grant_ttl > timedelta(minutes=5):
            raise ValueError("grant_ttl must be between zero and five minutes")
        self._database = database
        self._grants = grant_repository or SecretGrantRepository()
        self._leases = lease_repository or LeaseRepository()
        self._platform = platform_repository or PlatformRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._secret_provider = secret_provider
        self._password_adapter = password_adapter
        self._adapter_registry = adapter_registry
        self._connectors = connector_repository or ConnectorRepository()
        self._account_health = account_health_repository or AccountHealthRepository()
        self._grant_ttl = grant_ttl
        self._clock = clock

    async def issue(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: IssueSecretGrant,
    ) -> SecretGrant:
        """签发 Grant，并为可见 Lease 记录结构化拒绝事实。"""

        try:
            return await self._issue(actor, lease_id, command)
        except ApplicationError as error:
            await self._record_issue_rejection(
                actor,
                lease_id=lease_id,
                command=command,
                error=error,
            )
            raise

    async def _issue(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: IssueSecretGrant,
    ) -> SecretGrant:
        """校验 Origin、Lease、Fence、Worker 和 Credential 后签发短期 Grant。"""

        if (
            command.purpose is CredentialPurpose.ROTATE_CREDENTIAL
            and not actor.is_organization_admin()
        ):
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="凭证轮换权限不足",
                detail="只有组织管理员可以申请 ROTATE_CREDENTIAL Grant。",
                status_code=403,
            )
        now = self._clock()
        grant_ref = f"sgr_{token_urlsafe(32)}"
        token_hash = self.hash_grant_ref(grant_ref)
        record: SecretGrantRecord | None = None
        lease_expired = False
        async with self._database.transaction(actor.database_context()) as connection:
            visible = await self._leases.get_lease(connection, lease_id)
            self._require_lease_access(actor, visible)
            assert visible is not None
            environment = await self._platform.get_environment_for_share(
                connection,
                visible.environment_id,
            )
            self._require_environment_policy(actor, environment, command.allowed_origins)
            connector = await self._connectors.get_for_account_share(
                connection,
                visible.account_id,
            )
            await self._require_connector_policy(
                connection,
                environment=environment,
                connector=connector,
                origins=command.allowed_origins,
            )
            assert connector is not None
            outcome = await self._leases.authorize_sensitive_use(
                connection,
                lease_id=lease_id,
                fencing_token=command.fencing_token,
                now=now,
            )
            if outcome.kind is LeaseMutationKind.EXPIRED:
                assert outcome.lease is not None
                await self.record_lease_event(
                    connection,
                    actor=actor,
                    lease=outcome.lease,
                    event_type="account_lease.expired",
                    occurred_at=now,
                )
                lease_expired = True
            elif outcome.kind in {
                LeaseMutationKind.FENCED,
                LeaseMutationKind.TERMINAL,
            }:
                self._raise_lease_fenced()
            elif outcome.kind is LeaseMutationKind.NOT_FOUND:
                raise self._not_found("AccountLease 不存在。")
            else:
                assert outcome.kind is LeaseMutationKind.AUTHORIZED
                assert outcome.lease is not None
                lease = outcome.lease
                if command.worker_identity != lease.worker_id:
                    self._raise_lease_fenced()
                expires_at = min(now + self._grant_ttl, lease.expires_at)
                if expires_at <= now:
                    lease_expired = True
                else:
                    record = await self._grants.issue(
                        connection,
                        grant_id=new_entity_id(),
                        token_hash=token_hash,
                        lease=lease,
                        connector=connector,
                        command=command,
                        issued_at=now,
                        expires_at=expires_at,
                    )
                    if record is None:
                        raise ApplicationError(
                            error_code=ErrorCode.CREDENTIAL_EXPIRED,
                            title="Credential 不可用",
                            detail="当前 Lease 没有满足用途的有效 PASSWORD Credential。",
                            status_code=409,
                        )
                    await self.record_grant_event(
                        connection,
                        actor=actor,
                        grant=record,
                        event_type="secret_grant.issued",
                        occurred_at=now,
                    )
        if lease_expired:
            self._raise_lease_expired()
        assert record is not None
        return record.to_grant(grant_ref)

    async def redeem_password(
        self,
        actor: ActorContext,
        grant_ref: str,
        command: RedeemSecretGrant,
    ) -> SecretGrantReceipt:
        """消费 Grant，并审计所有可定位 Grant 的拒绝尝试。"""

        try:
            return await self._redeem_password(actor, grant_ref, command)
        except ApplicationError as error:
            await self._record_redemption_rejection(
                actor,
                grant_ref=grant_ref,
                error=error,
            )
            raise

    async def _redeem_password(
        self,
        actor: ActorContext,
        grant_ref: str,
        command: RedeemSecretGrant,
    ) -> SecretGrantReceipt:
        """先原子消费 Grant，再在事务外通过闭包执行 Password Adapter。"""

        if self._secret_provider is None or (
            self._password_adapter is None and self._adapter_registry is None
        ):
            raise ApplicationError(
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                title="Credential Provider 未配置",
                detail="当前进程没有配置可用的 Secret Provider 与 Password Adapter。",
                status_code=503,
            )
        if fullmatch(r"sgr_[A-Za-z0-9_-]{32,200}", grant_ref) is None:
            raise self._not_found("SecretGrant 不存在。")
        now = self._clock()
        token_hash = self.hash_grant_ref(grant_ref)
        claim_kind: SecretGrantClaimKind | None = None
        claimed: SecretGrantRecord | None = None
        access = None
        account_handle: str | None = None
        password_adapter: GenericPasswordAdapter | None = None
        lease_expired = False
        async with self._database.transaction(actor.database_context()) as connection:
            expected = await self._grants.get_by_token_hash(connection, token_hash)
            self._require_grant_access(actor, expected)
            assert expected is not None
            environment = await self._platform.get_environment_for_share(
                connection,
                expected.environment_id,
            )
            self._require_environment_policy(actor, environment, (command.origin,))
            connector = (
                await self._connectors.get_record_for_share(
                    connection,
                    expected.connector_installation_id,
                )
                if expected.connector_installation_id is not None
                else None
            )
            await self._require_connector_policy(
                connection,
                environment=environment,
                connector=connector,
                origins=(command.origin,),
            )
            assert connector is not None
            password_adapter = self._resolve_password_adapter(connector)
            outcome = await self._leases.authorize_sensitive_use(
                connection,
                lease_id=expected.lease_id,
                fencing_token=expected.fencing_token,
                now=now,
            )
            if outcome.kind is LeaseMutationKind.EXPIRED:
                assert outcome.lease is not None
                await self.record_lease_event(
                    connection,
                    actor=actor,
                    lease=outcome.lease,
                    event_type="account_lease.expired",
                    occurred_at=now,
                )
                lease_expired = True
            elif outcome.kind in {
                LeaseMutationKind.FENCED,
                LeaseMutationKind.TERMINAL,
                LeaseMutationKind.NOT_FOUND,
            }:
                claim_kind = SecretGrantClaimKind.FENCED
            else:
                assert outcome.kind is LeaseMutationKind.AUTHORIZED
                assert outcome.lease is not None
                account_handle = outcome.lease.account_handle
                claim = await self._grants.claim(
                    connection,
                    expected=expected,
                    lease=outcome.lease,
                    connector=connector,
                    command=command,
                    now=now,
                )
                claim_kind = claim.kind
                claimed = claim.grant
                access = claim.access
                if claim.kind is SecretGrantClaimKind.REDEEMED:
                    assert claimed is not None
                    await self.record_grant_event(
                        connection,
                        actor=actor,
                        grant=claimed,
                        event_type="secret_grant.redeemed",
                        occurred_at=now,
                    )
                elif (
                    claim.kind
                    in {
                        SecretGrantClaimKind.EXPIRED,
                        SecretGrantClaimKind.CREDENTIAL_UNAVAILABLE,
                        SecretGrantClaimKind.CONNECTOR_UNAVAILABLE,
                    }
                    and claimed is not None
                ):
                    await self.record_grant_event(
                        connection,
                        actor=actor,
                        grant=claimed,
                        event_type="secret_grant.terminated",
                        occurred_at=now,
                    )
        if lease_expired:
            self._raise_lease_expired()
        assert claim_kind is not None
        if claim_kind is not SecretGrantClaimKind.REDEEMED:
            self._raise_claim_error(claim_kind)
        assert claimed is not None
        assert access is not None
        assert account_handle is not None
        assert password_adapter is not None
        context = AdapterContext.for_password_operation(
            tenant_id=claimed.tenant_id,
            project_id=claimed.project_id,
            environment_id=claimed.environment_id,
            origin=command.origin,
            request_id=actor.request_id,
            secret_scope=PasswordSecretScope(
                provider=self._secret_provider,
                secret_ref=access.secret_ref,
                secret_version=access.secret_version,
            ),
        )
        try:
            authentication = await password_adapter.authenticate(
                context=context,
                account_handle=account_handle,
            )
        except SecretProviderError:
            error = AdapterError(
                code=AdapterErrorCode.PROVIDER_UNAVAILABLE,
                category="secret_provider",
                operation="password_login",
                safe_message="credential material is unavailable",
                retryable=True,
                request_id=actor.request_id,
            )
            await self.record_runtime_health_failure(
                actor,
                claimed,
                access=access,
                origin=command.origin,
                failure_code=AccountHealthFailureCode.SECRET_UNAVAILABLE,
            )
            await self._record_adapter_result(
                actor,
                claimed,
                adapter_key=password_adapter.manifest().adapter_key,
                error=error,
            )
            self._raise_adapter_error(error)
        except AdapterOperationError as operation_error:
            await self.record_runtime_health_failure(
                actor,
                claimed,
                access=access,
                origin=command.origin,
                failure_code=map_adapter_health_failure(operation_error),
            )
            await self._record_adapter_result(
                actor,
                claimed,
                adapter_key=password_adapter.manifest().adapter_key,
                error=operation_error.error,
            )
            self._raise_adapter_error(operation_error.error)
        identity_failure = self.authenticated_identity_failure(
            access,
            connector_installation_id=claimed.connector_installation_id,
            provider_subject=authentication.provider_subject,
            role_keys=authentication.role_keys,
        )
        if identity_failure is not None:
            await self.record_runtime_health_failure(
                actor,
                claimed,
                access=access,
                origin=command.origin,
                failure_code=identity_failure,
            )
            error = AdapterError(
                code=AdapterErrorCode.AUTHENTICATION_FAILED,
                category="identity_policy",
                operation="password_login",
                safe_message=("authenticated identity does not match the verified account policy"),
                retryable=False,
                request_id=actor.request_id,
            )
            await self._record_adapter_result(
                actor,
                claimed,
                adapter_key=password_adapter.manifest().adapter_key,
                error=error,
            )
            self._raise_adapter_error(error)
        completed_at = self._clock()
        await self._record_adapter_result(
            actor,
            claimed,
            adapter_key=password_adapter.manifest().adapter_key,
            error=None,
        )
        return SecretGrantReceipt(
            grant_id=claimed.id,
            adapter_key=password_adapter.manifest().adapter_key,
            capability=ProviderCapability.AUTH_PASSWORD.value,
            origin=command.origin,
            completed_at=completed_at,
        )

    @staticmethod
    def authenticated_identity_failure(
        access: CredentialSecretAccess,
        *,
        connector_installation_id: UUID | None,
        provider_subject: str,
        role_keys: tuple[str, ...],
    ) -> AccountHealthFailureCode | None:
        """Recheck the verified identity fingerprint and role on every login."""

        if connector_installation_id is None or access.identity_fingerprint is None:
            return AccountHealthFailureCode.IDENTITY_MISMATCH
        if access.account_source is not AccountSource.ATLAS_MANAGED and (
            access.external_subject_id is None
            or not compare_digest(
                access.external_subject_id,
                provider_subject,
            )
        ):
            return AccountHealthFailureCode.IDENTITY_MISMATCH
        fingerprint = AccountHealthService.identity_fingerprint(
            connector_installation_id,
            provider_subject,
        )
        if not compare_digest(access.identity_fingerprint, fingerprint):
            return AccountHealthFailureCode.IDENTITY_MISMATCH
        if access.role_key not in role_keys:
            return AccountHealthFailureCode.ROLE_DRIFT
        return None

    async def record_runtime_health_failure(
        self,
        actor: ActorContext,
        grant: SecretGrantRecord,
        *,
        access: CredentialSecretAccess,
        origin: str,
        failure_code: AccountHealthFailureCode,
    ) -> None:
        """Revoke the Lease and append health facts after authentication failure."""

        now = self._clock()
        outcome = health_failure_outcome(failure_code)
        async with self._database.transaction(actor.database_context()) as connection:
            connector = (
                await self._connectors.get_record_for_share(
                    connection,
                    grant.connector_installation_id,
                )
                if grant.connector_installation_id is not None
                else None
            )
            if connector is None:
                return
            revoked = await self._leases.revoke_active(
                connection,
                reason=LeaseReleaseReason.AUTH_FAILED,
                now=now,
                account_id=grant.account_id,
            )
            for lease in revoked:
                await self.record_lease_event(
                    connection,
                    actor=actor,
                    lease=lease,
                    event_type="account_lease.revoked",
                    occurred_at=now,
                )
            snapshot = await self._account_health.get_verification_snapshot_for_update(
                connection,
                grant.account_id,
                now=now,
            )
            if (
                snapshot is None
                or snapshot.connector_installation_id != connector.id
                or snapshot.credential_binding_id != grant.credential_binding_id
                or snapshot.secret_version != access.secret_version
            ):
                return
            await self._account_health.expire_running_checks(
                connection,
                account_id=grant.account_id,
                now=now,
            )
            if await self._account_health.has_running_check(
                connection,
                account_id=grant.account_id,
            ):
                return
            decision = decide_health_failure(
                current_failures=snapshot.state.consecutive_health_failures,
                threshold=snapshot.health_failure_threshold,
                retry_cooldown_seconds=snapshot.health_retry_cooldown_seconds,
                code=failure_code,
                now=now,
            )
            check = await self._account_health.create_check(
                connection,
                check_id=new_entity_id(),
                snapshot=snapshot,
                account_revision=snapshot.account_revision,
                origin=origin,
                trigger=AccountHealthCheckTrigger.AUTH_FAILURE,
                actor_id=actor.actor_id,
                request_id=actor.request_id,
                started_at=now,
                expires_at=now + timedelta(minutes=1),
            )
            after = await self._account_health.finalize_failure(
                connection,
                account_id=grant.account_id,
                expected_revision=snapshot.account_revision,
                health_status=decision.health_status,
                operational_status=decision.operational_status,
                cooldown_until=decision.cooldown_until,
                consecutive_health_failures=decision.consecutive_health_failures,
                now=now,
            )
            if after is None:
                raise RuntimeError("runtime health failure lost its account revision")
            terminal = await self._account_health.finish_check(
                connection,
                check_id=check.id,
                status=AccountHealthCheckStatus.FAILED,
                result_health_status=decision.health_status,
                failure_code=failure_code,
                retryable=outcome.retryable,
                safe_summary=outcome.safe_summary,
                finished_at=now,
            )
            if terminal is None:
                raise RuntimeError("runtime health check could not be finalized")
            await self._account_health.append_transition(
                connection,
                tenant_id=snapshot.tenant_id,
                project_id=snapshot.project_id,
                environment_id=snapshot.environment_id,
                account_id=snapshot.account_id,
                health_check_id=terminal.id,
                reason=(
                    decision.reason
                    if decision.reason is not AccountStateTransitionReason.VERIFICATION_FAILED
                    else AccountStateTransitionReason.RUNTIME_AUTH_FAILED
                ),
                before=snapshot.state,
                after=after,
                safe_summary=outcome.safe_summary,
                actor_id=actor.actor_id,
                request_id=actor.request_id,
                occurred_at=now,
            )
            payload: dict[str, JsonValue] = {
                "accountId": str(grant.account_id),
                "connectorInstallationId": str(connector.id),
                "healthCheckId": str(terminal.id),
                "trigger": terminal.trigger.value,
                "status": terminal.status.value,
                "failureCode": failure_code.value,
                "retryable": outcome.retryable,
            }
            await self._audit.append(
                connection,
                tenant_id=grant.tenant_id,
                project_id=grant.project_id,
                environment_id=grant.environment_id,
                actor_id=actor.actor_id,
                event_type="test_account.health_check.failed",
                entity_type="account_health_check",
                entity_id=terminal.id,
                occurred_at=now,
                payload=payload,
                request_id=actor.request_id,
            )
            await self._outbox.append(
                connection,
                DomainEvent(
                    tenant_id=grant.tenant_id,
                    aggregate_type="test_account",
                    aggregate_id=grant.account_id,
                    event_type="test_account.health_check.failed",
                    occurred_at=now,
                    payload=payload,
                ),
            )

    async def _record_issue_rejection(
        self,
        actor: ActorContext,
        *,
        lease_id: UUID,
        command: IssueSecretGrant,
        error: ApplicationError,
    ) -> None:
        now = self._clock()
        async with self._database.transaction(actor.database_context()) as connection:
            lease = await self._leases.get_lease(connection, lease_id)
            if lease is None or not actor.can_operate_project(lease.project_id):
                return
            payload: dict[str, JsonValue] = {
                "leaseId": str(lease.id),
                "fencingToken": command.fencing_token,
                "purpose": command.purpose.value,
                "errorCode": error.error_code.value,
            }
            await self._audit.append(
                connection,
                tenant_id=actor.tenant_id,
                project_id=lease.project_id,
                environment_id=lease.environment_id,
                actor_id=actor.actor_id,
                event_type="secret_grant.rejected",
                entity_type="account_lease",
                entity_id=lease.id,
                occurred_at=now,
                payload=payload,
                request_id=actor.request_id,
            )
            await self._outbox.append(
                connection,
                DomainEvent(
                    tenant_id=actor.tenant_id,
                    aggregate_type="account_lease",
                    aggregate_id=lease.id,
                    event_type="secret_grant.rejected",
                    occurred_at=now,
                    payload=payload,
                ),
            )

    async def _record_redemption_rejection(
        self,
        actor: ActorContext,
        *,
        grant_ref: str,
        error: ApplicationError,
    ) -> None:
        if fullmatch(r"sgr_[A-Za-z0-9_-]{32,200}", grant_ref) is None:
            return
        now = self._clock()
        async with self._database.transaction(actor.database_context()) as connection:
            grant = await self._grants.get_by_token_hash(
                connection,
                self.hash_grant_ref(grant_ref),
            )
            if grant is None or not actor.can_operate_project(grant.project_id):
                return
            await self.record_grant_event(
                connection,
                actor=actor,
                grant=grant,
                event_type="secret_grant.redemption_rejected",
                occurred_at=now,
                extra={"errorCode": error.error_code.value},
            )

    async def _record_adapter_result(
        self,
        actor: ActorContext,
        grant: SecretGrantRecord,
        *,
        adapter_key: str,
        error: AdapterError | None,
    ) -> None:
        now = self._clock()
        event_type = (
            "secret_grant.adapter_succeeded" if error is None else "secret_grant.adapter_failed"
        )
        extra: dict[str, JsonValue] = {
            "adapterKey": adapter_key,
            "capability": ProviderCapability.AUTH_PASSWORD.value,
        }
        if error is not None:
            extra.update(
                {
                    "errorCode": error.code.value,
                    "operation": error.operation,
                    "retryable": error.retryable,
                }
            )
        async with self._database.transaction(actor.database_context()) as connection:
            await self.record_grant_event(
                connection,
                actor=actor,
                grant=grant,
                event_type=event_type,
                occurred_at=now,
                extra=extra,
            )

    async def record_grant_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        grant: SecretGrantRecord,
        event_type: str,
        occurred_at: datetime,
        extra: dict[str, JsonValue] | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "leaseId": str(grant.lease_id),
            "connectorInstallationId": (
                str(grant.connector_installation_id)
                if grant.connector_installation_id is not None
                else None
            ),
            "fencingToken": grant.fencing_token,
            "purpose": grant.purpose.value,
            "status": grant.status.value,
        }
        if grant.termination_reason is not None:
            payload["terminationReason"] = grant.termination_reason.value
        if extra is not None:
            payload.update(extra)
        await self._audit.append(
            connection,
            tenant_id=actor.tenant_id,
            project_id=grant.project_id,
            environment_id=grant.environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
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
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    async def record_lease_event(
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

    @staticmethod
    def _require_environment_policy(
        actor: ActorContext,
        environment: Environment | None,
        origins: tuple[str, ...],
    ) -> None:
        if environment is None or not actor.can_operate_project(environment.project_id):
            raise CredentialBrokerService._not_found("Environment 不存在。")
        if environment.status is not EnvironmentStatus.ACTIVE:
            raise ApplicationError(
                error_code=ErrorCode.SECRET_GRANT_REVOKED,
                title="Environment 已禁用",
                detail="禁用 Environment 不允许签发或消费 Secret Grant。",
                status_code=409,
            )
        if environment.kind is EnvironmentKind.PRODUCTION:
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="生产 Secret Grant 默认禁用",
                detail="Production Environment 需要独立审批与工作负载策略。",
                status_code=403,
            )
        if not set(origins).issubset(environment.allowed_origins):
            raise ApplicationError(
                error_code=ErrorCode.ORIGIN_NOT_ALLOWED,
                title="Origin 不在允许列表",
                detail="Secret Grant 只能绑定 Environment 已登记的精确 Origin。",
                status_code=403,
            )

    async def _require_connector_policy(
        self,
        connection: AsyncConnection[DictRow],
        *,
        environment: Environment | None,
        connector: ConnectorInstallationRecord | None,
        origins: tuple[str, ...],
    ) -> None:
        """要求 Grant 始终绑定 ACTIVE Connector 与实际密码认证能力。"""

        if (
            environment is None
            or connector is None
            or connector.environment_id != environment.id
            or connector.project_id != environment.project_id
        ):
            raise ApplicationError(
                error_code=ErrorCode.SECRET_GRANT_REVOKED,
                title="Connector 不可用",
                detail="账号没有绑定当前 Environment 的有效 Connector。",
                status_code=409,
            )
        if connector.status is not ConnectorStatus.ACTIVE:
            raise ApplicationError(
                error_code=ErrorCode.SECRET_GRANT_REVOKED,
                title="Connector 不可用",
                detail="Connector 未验证、已降级或已禁用。",
                status_code=409,
            )
        if not set(origins).issubset(connector.allowed_origins):
            raise ApplicationError(
                error_code=ErrorCode.ORIGIN_NOT_ALLOWED,
                title="Origin 不在 Connector 允许列表",
                detail="Secret Grant Origin 必须由 Connector 显式允许。",
                status_code=403,
            )
        capabilities = await self._connectors.get_capabilities(
            connection,
            connector.id,
        )
        if ProviderCapability.AUTH_PASSWORD not in {capability.name for capability in capabilities}:
            raise ApplicationError(
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                title="Connector Capability 不可用",
                detail="Connector 没有协商出 auth.password Capability。",
                status_code=503,
            )

    def _resolve_password_adapter(
        self,
        connector: ConnectorInstallationRecord,
    ) -> GenericPasswordAdapter:
        """解析与 Connector Key 一致且实现密码认证的可信 Adapter。"""

        if self._password_adapter is not None:
            if self._password_adapter.manifest().adapter_key != connector.adapter_key:
                self._raise_adapter_unavailable()
            return self._password_adapter
        assert self._adapter_registry is not None
        try:
            adapter = self._adapter_registry.resolve(connector)
        except AdapterNotRegisteredError:
            self._raise_adapter_unavailable()
        if not isinstance(adapter, GenericPasswordAdapter):
            self._raise_adapter_unavailable()
        return adapter

    @staticmethod
    def _raise_adapter_unavailable() -> Never:
        raise ApplicationError(
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            title="Credential Provider 未配置",
            detail="Connector 没有可执行 auth.password 的可信 Adapter。",
            status_code=503,
        )

    @staticmethod
    def _require_lease_access(actor: ActorContext, lease: AccountLease | None) -> None:
        if lease is None or not actor.can_operate_project(lease.project_id):
            raise CredentialBrokerService._not_found("AccountLease 不存在。")

    @staticmethod
    def _require_grant_access(
        actor: ActorContext,
        grant: SecretGrantRecord | None,
    ) -> None:
        if grant is None or not actor.can_operate_project(grant.project_id):
            raise CredentialBrokerService._not_found("SecretGrant 不存在。")

    @staticmethod
    def hash_grant_ref(grant_ref: str) -> str:
        """数据库仅保存 Grant Ref 的 SHA-256 Hash。"""

        return sha256(grant_ref.encode("utf-8")).hexdigest()

    @staticmethod
    def _raise_claim_error(kind: SecretGrantClaimKind) -> None:
        if kind is SecretGrantClaimKind.EXPIRED:
            raise ApplicationError(
                error_code=ErrorCode.SECRET_GRANT_EXPIRED,
                title="Secret Grant 已过期",
                detail="一次性 Secret Grant 已超过服务端 TTL。",
                status_code=409,
            )
        if kind is SecretGrantClaimKind.REPLAYED:
            raise ApplicationError(
                error_code=ErrorCode.SECRET_GRANT_REPLAYED,
                title="Secret Grant 已消费",
                detail="同一个 Secret Grant 不能重复消费。",
                status_code=409,
            )
        if kind is SecretGrantClaimKind.ORIGIN_DENIED:
            raise ApplicationError(
                error_code=ErrorCode.ORIGIN_NOT_ALLOWED,
                title="Origin 不匹配",
                detail="消费 Origin 与 Secret Grant 绑定范围不一致。",
                status_code=403,
            )
        if kind is SecretGrantClaimKind.CREDENTIAL_UNAVAILABLE:
            raise ApplicationError(
                error_code=ErrorCode.CREDENTIAL_EXPIRED,
                title="Credential 不可用",
                detail="Credential 已过期、撤销或无法用于本次认证。",
                status_code=409,
            )
        if kind is SecretGrantClaimKind.CONNECTOR_UNAVAILABLE:
            raise ApplicationError(
                error_code=ErrorCode.SECRET_GRANT_REVOKED,
                title="Secret Grant 已撤销",
                detail="Connector 或账号绑定关系已经变化。",
                status_code=409,
            )
        if kind in {SecretGrantClaimKind.REVOKED, SecretGrantClaimKind.FENCED}:
            raise ApplicationError(
                error_code=ErrorCode.SECRET_GRANT_REVOKED,
                title="Secret Grant 已撤销",
                detail="Lease、Fence 或 Credential 状态已经使 Grant 失效。",
                status_code=409,
            )
        raise CredentialBrokerService._not_found("SecretGrant 不存在。")

    @staticmethod
    def _raise_adapter_error(error: AdapterError) -> None:
        if error.code in {
            AdapterErrorCode.PROVIDER_UNAVAILABLE,
            AdapterErrorCode.NETWORK_TIMEOUT,
            AdapterErrorCode.RATE_LIMITED,
        }:
            raise ApplicationError(
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                title="Provider 暂不可用",
                detail=error.safe_message,
                status_code=503,
            )
        raise ApplicationError(
            error_code=ErrorCode.AUTHENTICATION_FAILED,
            title="Provider 认证失败",
            detail=error.safe_message,
            status_code=409,
        )

    @staticmethod
    def _not_found(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="资源不存在",
            detail=detail,
            status_code=404,
        )

    @staticmethod
    def _raise_lease_fenced() -> None:
        raise ApplicationError(
            error_code=ErrorCode.LEASE_FENCED,
            title="租约已被 Fencing",
            detail="Lease 或 fencingToken 不是账号当前可接受的活动租约。",
            status_code=409,
        )

    @staticmethod
    def _raise_lease_expired() -> None:
        raise ApplicationError(
            error_code=ErrorCode.LEASE_EXPIRED,
            title="租约已过期",
            detail="Lease 已越过 TTL 或 Execution Deadline，请重新申请。",
            status_code=409,
        )
