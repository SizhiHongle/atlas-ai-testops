"""Test account login, identity, and role health application service."""

from asyncio import timeout
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from typing import cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.ports.providers import (
    AdapterContext,
    AdapterOperationError,
)
from atlas_testops.application.ports.secrets import (
    PasswordSecretScope,
    SecretProvider,
    SecretProviderError,
)
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import decode_cursor, next_time_cursor
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.identity import (
    AccountHealth,
    AccountHealthCheck,
    AccountHealthCheckPage,
    AccountHealthCheckStatus,
    AccountHealthCheckTrigger,
    AccountHealthFailureCode,
    AccountHealthVerification,
    AccountLifecycle,
    AccountOperationalStatus,
    AccountSource,
    AccountStateTransition,
    AccountStateTransitionPage,
    AccountStateTransitionReason,
    AdapterErrorCode,
    ConnectorInstallationRecord,
    ConnectorStatus,
    ProviderCapability,
    VerifyTestAccount,
)
from atlas_testops.domain.platform import EnvironmentKind, EnvironmentStatus
from atlas_testops.infrastructure.adapters.generic_password import GenericPasswordAdapter
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
    AccountVerificationSnapshot,
)
from atlas_testops.infrastructure.repositories.connectors import ConnectorRepository
from atlas_testops.infrastructure.repositories.identity import IdentityRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository

ACCOUNT_HEALTH_IDEMPOTENCY_TTL = timedelta(hours=24)
type HealthHistoryItem = AccountHealthCheck | AccountStateTransition


@dataclass(frozen=True, slots=True)
class AccountHealthCommandResult:
    """Carry a health command result with idempotent replay metadata."""

    value: AccountHealthVerification
    status_code: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class HealthProbeOutcome:
    """Represent the safe low-cardinality outcome of an external probe."""

    succeeded: bool
    identity_fingerprint: str | None
    failure_code: AccountHealthFailureCode | None
    retryable: bool
    safe_summary: str


@dataclass(frozen=True, slots=True)
class HealthFailureState:
    """Represent the target account state derived from a health failure."""

    health_status: AccountHealth
    operational_status: AccountOperationalStatus
    cooldown_until: datetime | None
    consecutive_health_failures: int
    reason: AccountStateTransitionReason


def decide_health_failure(
    *,
    current_failures: int,
    threshold: int,
    retry_cooldown_seconds: int,
    code: AccountHealthFailureCode,
    now: datetime,
) -> HealthFailureState:
    """Derive isolation policy while separating account and infrastructure faults."""

    immediate_reasons = {
        AccountHealthFailureCode.IDENTITY_MISMATCH: (
            AccountStateTransitionReason.IDENTITY_MISMATCH
        ),
        AccountHealthFailureCode.ROLE_DRIFT: AccountStateTransitionReason.ROLE_DRIFT,
        AccountHealthFailureCode.ACCOUNT_LOCKED: (AccountStateTransitionReason.ACCOUNT_LOCKED),
        AccountHealthFailureCode.MANUAL_ACTION_REQUIRED: (
            AccountStateTransitionReason.VERIFICATION_FAILED
        ),
    }
    account_failures = {
        AccountHealthFailureCode.AUTHENTICATION_FAILED,
        AccountHealthFailureCode.CREDENTIAL_EXPIRED,
        AccountHealthFailureCode.ACCOUNT_LOCKED,
        AccountHealthFailureCode.IDENTITY_MISMATCH,
        AccountHealthFailureCode.ROLE_DRIFT,
        AccountHealthFailureCode.MANUAL_ACTION_REQUIRED,
    }
    failures = current_failures + (1 if code in account_failures else 0)
    immediate = code in immediate_reasons
    threshold_reached = failures >= threshold
    if immediate or threshold_reached:
        return HealthFailureState(
            health_status=AccountHealth.QUARANTINED,
            operational_status=AccountOperationalStatus.VERIFYING,
            cooldown_until=None,
            consecutive_health_failures=failures,
            reason=immediate_reasons.get(
                code,
                AccountStateTransitionReason.FAILURE_THRESHOLD_REACHED,
            ),
        )
    cooldown_until = now + timedelta(seconds=retry_cooldown_seconds)
    return HealthFailureState(
        health_status=AccountHealth.DEGRADED,
        operational_status=(
            AccountOperationalStatus.COOLDOWN
            if retry_cooldown_seconds > 0
            else AccountOperationalStatus.VERIFYING
        ),
        cooldown_until=cooldown_until if retry_cooldown_seconds > 0 else None,
        consecutive_health_failures=failures,
        reason=AccountStateTransitionReason.VERIFICATION_FAILED,
    )


def health_failure_outcome(code: AccountHealthFailureCode) -> HealthProbeOutcome:
    """Map a stable failure code to a fixed safe summary and retry policy."""

    summaries = {
        AccountHealthFailureCode.AUTHENTICATION_FAILED: "Provider 拒绝账号登录。",
        AccountHealthFailureCode.CREDENTIAL_EXPIRED: "登录 Credential 已过期。",
        AccountHealthFailureCode.ACCOUNT_LOCKED: "Provider 账号已锁定。",
        AccountHealthFailureCode.IDENTITY_MISMATCH: "登录身份与账号绑定不一致。",
        AccountHealthFailureCode.ROLE_DRIFT: "Provider 角色与账号池角色不一致。",
        AccountHealthFailureCode.RATE_LIMITED: "Provider 暂时限制健康检查请求。",
        AccountHealthFailureCode.PROVIDER_UNAVAILABLE: "Provider 暂时不可用。",
        AccountHealthFailureCode.NETWORK_TIMEOUT: "Provider 健康检查超时。",
        AccountHealthFailureCode.MANUAL_ACTION_REQUIRED: "账号需要人工处理。",
        AccountHealthFailureCode.CAPABILITY_UNSUPPORTED: "Adapter 不支持账号健康检查。",
        AccountHealthFailureCode.SECRET_UNAVAILABLE: "Credential 材料暂时不可用。",
        AccountHealthFailureCode.INTERNAL_ERROR: "Adapter 健康检查执行失败。",
        AccountHealthFailureCode.STALE_SNAPSHOT: "健康检查依赖已变化。",
    }
    retryable = code in {
        AccountHealthFailureCode.RATE_LIMITED,
        AccountHealthFailureCode.PROVIDER_UNAVAILABLE,
        AccountHealthFailureCode.NETWORK_TIMEOUT,
        AccountHealthFailureCode.SECRET_UNAVAILABLE,
        AccountHealthFailureCode.INTERNAL_ERROR,
        AccountHealthFailureCode.STALE_SNAPSHOT,
    }
    return HealthProbeOutcome(
        succeeded=False,
        identity_fingerprint=None,
        failure_code=code,
        retryable=retryable,
        safe_summary=summaries[code],
    )


def map_adapter_health_failure(error: AdapterOperationError) -> AccountHealthFailureCode:
    """Map an Adapter error to a stable account health classification."""

    mapping = {
        AdapterErrorCode.AUTHENTICATION_FAILED: (AccountHealthFailureCode.AUTHENTICATION_FAILED),
        AdapterErrorCode.CREDENTIAL_EXPIRED: AccountHealthFailureCode.CREDENTIAL_EXPIRED,
        AdapterErrorCode.ACCOUNT_LOCKED: AccountHealthFailureCode.ACCOUNT_LOCKED,
        AdapterErrorCode.RATE_LIMITED: AccountHealthFailureCode.RATE_LIMITED,
        AdapterErrorCode.PROVIDER_UNAVAILABLE: (AccountHealthFailureCode.PROVIDER_UNAVAILABLE),
        AdapterErrorCode.NETWORK_TIMEOUT: AccountHealthFailureCode.NETWORK_TIMEOUT,
        AdapterErrorCode.MANUAL_ACTION_REQUIRED: (AccountHealthFailureCode.MANUAL_ACTION_REQUIRED),
        AdapterErrorCode.CAPABILITY_UNSUPPORTED: (AccountHealthFailureCode.CAPABILITY_UNSUPPORTED),
        AdapterErrorCode.CONFIGURATION_INVALID: (AccountHealthFailureCode.PROVIDER_UNAVAILABLE),
        AdapterErrorCode.INTERNAL_ERROR: AccountHealthFailureCode.INTERNAL_ERROR,
    }
    return mapping[error.error.code]


class AccountHealthService:
    """Coordinate health checks with short transactions, revision CAS, and secrets."""

    def __init__(
        self,
        database: Database,
        *,
        adapter_registry: AdapterRegistry,
        secret_provider: SecretProvider | None,
        verification_timeout: timedelta = timedelta(seconds=30),
        attempt_ttl: timedelta = timedelta(minutes=2),
        health_repository: AccountHealthRepository | None = None,
        identity_repository: IdentityRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        connector_repository: ConnectorRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
    ) -> None:
        if verification_timeout.total_seconds() <= 0:
            raise ValueError("verification_timeout must be positive")
        if attempt_ttl <= verification_timeout:
            raise ValueError("attempt_ttl must exceed verification_timeout")
        self._database = database
        self._registry = adapter_registry
        self._secret_provider = secret_provider
        self._verification_timeout = verification_timeout
        self._attempt_ttl = attempt_ttl
        self._health = health_repository or AccountHealthRepository()
        self._identity = identity_repository or IdentityRepository()
        self._platform = platform_repository or PlatformRepository()
        self._connectors = connector_repository or ConnectorRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()

    async def verify(
        self,
        actor: ActorContext,
        account_id: UUID,
        command: VerifyTestAccount,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> AccountHealthCommandResult:
        """Start a check, log in outside the transaction, and apply with snapshot CAS."""

        if self._secret_provider is None:
            raise ApplicationError(
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                title="Secret Provider 未配置",
                detail="当前进程不能执行测试账号健康检查。",
                status_code=503,
            )
        started_at = utc_now()
        request_hash = hash_request(
            {
                "command": command.model_dump(mode="json", by_alias=True),
                "expectedRevision": expected_revision,
            }
        )
        scope = f"test-accounts.{account_id}.verify"
        check_id = new_entity_id()
        async with self._database.transaction(actor.database_context()) as connection:
            account_projection = await self._identity.get_account(
                connection,
                account_id,
                now=started_at,
            )
            if account_projection is None or not actor.can_read_project(
                account_projection.project_id
            ):
                raise self._not_found()
            if not actor.can_manage_project(account_projection.project_id):
                raise self._forbidden()
            environment = await self._platform.get_environment_for_share(
                connection,
                account_projection.environment_id,
            )
            if environment is None:
                raise self._not_found()
            connector_id = account_projection.connector_installation_id
            connector_candidate = (
                await self._connectors.get_record_for_share(connection, connector_id)
                if connector_id is not None
                else None
            )
            snapshot_candidate = await self._health.get_verification_snapshot_for_update(
                connection,
                account_id,
                now=started_at,
            )
            if snapshot_candidate is None:
                raise self._not_found()
            if (
                connector_candidate is None
                or snapshot_candidate.connector_installation_id != connector_candidate.id
                or snapshot_candidate.environment_id != environment.id
            ):
                raise self._revision_conflict(snapshot_candidate.account_revision)
            connector = connector_candidate
            snapshot = snapshot_candidate

            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=started_at,
                ttl=ACCOUNT_HEALTH_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return AccountHealthCommandResult(
                    value=AccountHealthVerification.model_validate(
                        reservation.cached_response.body
                    ),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            self._require_preflight(
                snapshot,
                connector=connector,
                origin=command.origin,
                expected_revision=expected_revision,
            )
            await self._health.expire_running_checks(
                connection,
                account_id=account_id,
                now=started_at,
            )
            if await self._health.has_running_check(connection, account_id=account_id):
                raise ApplicationError(
                    error_code=ErrorCode.CONFLICT,
                    title="健康检查正在执行",
                    detail="同一个 TestAccount 同时只能执行一个健康检查。",
                    status_code=409,
                    headers={"Retry-After": "1"},
                )
            before = snapshot.state
            verifying = await self._health.mark_account_verifying(
                connection,
                account_id=account_id,
                expected_revision=snapshot.account_revision,
            )
            if verifying is None:
                raise self._revision_conflict(snapshot.account_revision)
            running = await self._health.create_check(
                connection,
                check_id=check_id,
                snapshot=snapshot,
                account_revision=verifying.revision,
                origin=command.origin,
                trigger=AccountHealthCheckTrigger.MANUAL,
                actor_id=actor.actor_id,
                request_id=actor.request_id,
                started_at=started_at,
                expires_at=started_at + self._attempt_ttl,
            )
            await self._health.append_transition(
                connection,
                tenant_id=snapshot.tenant_id,
                project_id=snapshot.project_id,
                environment_id=snapshot.environment_id,
                account_id=snapshot.account_id,
                health_check_id=running.id,
                reason=AccountStateTransitionReason.VERIFICATION_STARTED,
                before=before,
                after=verifying,
                safe_summary="账号进入健康检查状态。",
                actor_id=actor.actor_id,
                request_id=actor.request_id,
                occurred_at=started_at,
            )
            await self._record_event(
                connection,
                actor=actor,
                snapshot=snapshot,
                check=running,
                event_type="test_account.health_check.started",
                occurred_at=started_at,
            )

        outcome = await self._execute_probe(
            actor,
            snapshot=snapshot,
            connector=connector,
            origin=command.origin,
        )
        return await self._finalize(
            actor,
            check_id=check_id,
            initial_snapshot=snapshot,
            outcome=outcome,
            scope=scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )

    async def list_checks(
        self,
        actor: ActorContext,
        account_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> AccountHealthCheckPage:
        """List visible health checks with a stable cursor."""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_readable_account(connection, actor, account_id)
            records = await self._health.list_checks(
                connection,
                account_id=account_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        return AccountHealthCheckPage(
            items=items,
            next_cursor=self._next_cursor(items, has_more=len(records) > limit),
        )

    async def list_transitions(
        self,
        actor: ActorContext,
        account_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> AccountStateTransitionPage:
        """List account state transition facts with a stable cursor."""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_readable_account(connection, actor, account_id)
            records = await self._health.list_transitions(
                connection,
                account_id=account_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        return AccountStateTransitionPage(
            items=items,
            next_cursor=self._next_cursor(items, has_more=len(records) > limit),
        )

    async def _execute_probe(
        self,
        actor: ActorContext,
        *,
        snapshot: AccountVerificationSnapshot,
        connector: ConnectorInstallationRecord,
        origin: str,
    ) -> HealthProbeOutcome:
        """Consume a secret and run a bounded Adapter login outside a transaction."""

        assert self._secret_provider is not None
        assert snapshot.secret_ref is not None
        assert snapshot.secret_version is not None
        try:
            resolved = self._registry.resolve(connector)
            if not isinstance(resolved, GenericPasswordAdapter):
                return health_failure_outcome(AccountHealthFailureCode.CAPABILITY_UNSUPPORTED)
            context = AdapterContext.for_password_operation(
                tenant_id=snapshot.tenant_id,
                project_id=snapshot.project_id,
                environment_id=snapshot.environment_id,
                origin=origin,
                request_id=actor.request_id,
                secret_scope=PasswordSecretScope(
                    provider=self._secret_provider,
                    secret_ref=snapshot.secret_ref,
                    secret_version=snapshot.secret_version,
                ),
            )
            async with timeout(self._verification_timeout.total_seconds()):
                result = await resolved.authenticate(
                    context=context,
                    account_handle=self.verification_account_handle(snapshot.account_id),
                )
        except TimeoutError:
            return health_failure_outcome(AccountHealthFailureCode.NETWORK_TIMEOUT)
        except SecretProviderError:
            return health_failure_outcome(AccountHealthFailureCode.SECRET_UNAVAILABLE)
        except AdapterNotRegisteredError:
            return health_failure_outcome(AccountHealthFailureCode.PROVIDER_UNAVAILABLE)
        except AdapterOperationError as error:
            return health_failure_outcome(map_adapter_health_failure(error))
        except Exception:
            return health_failure_outcome(AccountHealthFailureCode.INTERNAL_ERROR)

        fingerprint = self.identity_fingerprint(
            snapshot.connector_installation_id,
            result.provider_subject,
        )
        if snapshot.source is not AccountSource.ATLAS_MANAGED:
            expected_subject = snapshot.external_subject_id
            if expected_subject is None or not compare_digest(
                expected_subject,
                result.provider_subject,
            ):
                return health_failure_outcome(AccountHealthFailureCode.IDENTITY_MISMATCH)
        if snapshot.identity_fingerprint is not None and not compare_digest(
            snapshot.identity_fingerprint,
            fingerprint,
        ):
            return health_failure_outcome(AccountHealthFailureCode.IDENTITY_MISMATCH)
        if snapshot.role_key not in result.role_keys:
            return health_failure_outcome(AccountHealthFailureCode.ROLE_DRIFT)
        return HealthProbeOutcome(
            succeeded=True,
            identity_fingerprint=fingerprint,
            failure_code=None,
            retryable=False,
            safe_summary="账号登录、身份和角色健康检查通过。",
        )

    async def _finalize(
        self,
        actor: ActorContext,
        *,
        check_id: UUID,
        initial_snapshot: AccountVerificationSnapshot,
        outcome: HealthProbeOutcome,
        scope: str,
        idempotency_key: str,
        request_hash: str,
    ) -> AccountHealthCommandResult:
        """Relock dependencies and atomically persist the result with revision CAS."""

        completed_at = utc_now()
        async with self._database.transaction(actor.database_context()) as connection:
            check = await self._health.get_check_for_update(connection, check_id)
            if check is None:
                raise RuntimeError("account health check disappeared before finalization")
            current = await self._health.get_verification_snapshot_for_update(
                connection,
                initial_snapshot.account_id,
                now=completed_at,
            )
            if current is None:
                raise RuntimeError("test account disappeared before health finalization")
            if self._snapshot_is_stale(
                check,
                initial=initial_snapshot,
                current=current,
                completed_at=completed_at,
            ):
                if check.status is AccountHealthCheckStatus.STALE:
                    stale_terminal = check
                elif check.status is AccountHealthCheckStatus.RUNNING:
                    finished_stale = await self._health.finish_check(
                        connection,
                        check_id=check.id,
                        status=AccountHealthCheckStatus.STALE,
                        result_health_status=None,
                        failure_code=AccountHealthFailureCode.STALE_SNAPSHOT,
                        retryable=True,
                        safe_summary="健康检查依赖已变化，结果未应用。",
                        finished_at=completed_at,
                    )
                    if finished_stale is None:
                        raise RuntimeError("running health check could not become stale")
                    stale_terminal = finished_stale
                else:
                    raise RuntimeError("account health check was finalized unexpectedly")
                account = await self._identity.get_account(
                    connection,
                    current.account_id,
                    now=completed_at,
                )
                if account is None:
                    raise RuntimeError("test account projection is unavailable")
                result = AccountHealthVerification(
                    check=stale_terminal,
                    account=account,
                )
                await self._record_event(
                    connection,
                    actor=actor,
                    snapshot=current,
                    check=stale_terminal,
                    event_type="test_account.health_check.stale",
                    occurred_at=completed_at,
                )
                await self._complete_idempotency(
                    connection,
                    actor=actor,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    result=result,
                )
                return AccountHealthCommandResult(result, 201, False)

            before = current.state
            if outcome.succeeded:
                assert outcome.identity_fingerprint is not None
                after = await self._health.finalize_success(
                    connection,
                    account_id=current.account_id,
                    expected_revision=check.account_revision,
                    identity_fingerprint=outcome.identity_fingerprint,
                    now=completed_at,
                )
                status = AccountHealthCheckStatus.SUCCEEDED
                failure_code = None
                result_health = AccountHealth.HEALTHY
                reason = AccountStateTransitionReason.VERIFICATION_SUCCEEDED
                event_type = "test_account.health_check.succeeded"
            else:
                assert outcome.failure_code is not None
                decision = decide_health_failure(
                    current_failures=current.state.consecutive_health_failures,
                    threshold=current.health_failure_threshold,
                    retry_cooldown_seconds=current.health_retry_cooldown_seconds,
                    code=outcome.failure_code,
                    now=completed_at,
                )
                after = await self._health.finalize_failure(
                    connection,
                    account_id=current.account_id,
                    expected_revision=check.account_revision,
                    health_status=decision.health_status,
                    operational_status=decision.operational_status,
                    cooldown_until=decision.cooldown_until,
                    consecutive_health_failures=decision.consecutive_health_failures,
                    now=completed_at,
                )
                status = AccountHealthCheckStatus.FAILED
                failure_code = outcome.failure_code
                result_health = decision.health_status
                reason = decision.reason
                event_type = "test_account.health_check.failed"
            if after is None:
                raise RuntimeError("account health result lost its revision fence")
            terminal = await self._health.finish_check(
                connection,
                check_id=check.id,
                status=status,
                result_health_status=result_health,
                failure_code=failure_code,
                retryable=outcome.retryable,
                safe_summary=outcome.safe_summary,
                finished_at=completed_at,
            )
            if terminal is None:
                raise RuntimeError("running health check could not be finalized")
            await self._health.append_transition(
                connection,
                tenant_id=current.tenant_id,
                project_id=current.project_id,
                environment_id=current.environment_id,
                account_id=current.account_id,
                health_check_id=terminal.id,
                reason=reason,
                before=before,
                after=after,
                safe_summary=outcome.safe_summary,
                actor_id=actor.actor_id,
                request_id=actor.request_id,
                occurred_at=completed_at,
            )
            account = await self._identity.get_account(
                connection,
                current.account_id,
                now=completed_at,
            )
            if account is None:
                raise RuntimeError("test account projection is unavailable")
            result = AccountHealthVerification(check=terminal, account=account)
            await self._record_event(
                connection,
                actor=actor,
                snapshot=current,
                check=terminal,
                event_type=event_type,
                occurred_at=completed_at,
            )
            await self._complete_idempotency(
                connection,
                actor=actor,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                result=result,
            )
            return AccountHealthCommandResult(result, 201, False)

    @staticmethod
    def _snapshot_is_stale(
        check: AccountHealthCheck,
        *,
        initial: AccountVerificationSnapshot,
        current: AccountVerificationSnapshot,
        completed_at: datetime,
    ) -> bool:
        required = {
            ProviderCapability.AUTH_PASSWORD,
            ProviderCapability.ACCOUNT_READ,
        }
        return any(
            (
                check.status is not AccountHealthCheckStatus.RUNNING,
                completed_at >= check.expires_at,
                current.account_revision != check.account_revision,
                current.connector_installation_id != initial.connector_installation_id,
                current.connector_revision != initial.connector_revision,
                current.connector_status is not ConnectorStatus.ACTIVE,
                current.credential_binding_id != initial.credential_binding_id,
                current.credential_revision != initial.credential_revision,
                current.secret_version != initial.secret_version,
                current.active_lease,
                check.origin not in current.environment_origins,
                check.origin not in current.connector_origins,
                not required.issubset(current.connector_capabilities),
            )
        )

    def _require_preflight(
        self,
        snapshot: AccountVerificationSnapshot,
        *,
        connector: ConnectorInstallationRecord,
        origin: str,
        expected_revision: int,
    ) -> None:
        if snapshot.account_revision != expected_revision:
            raise self._revision_conflict(snapshot.account_revision)
        if snapshot.state.lifecycle_status in {
            AccountLifecycle.RETIRING,
            AccountLifecycle.RETIRED,
        }:
            raise ApplicationError(
                error_code=ErrorCode.CONFLICT,
                title="账号生命周期不允许验证",
                detail="RETIRING 或 RETIRED 账号不能执行健康检查。",
                status_code=409,
            )
        if snapshot.active_lease:
            raise ApplicationError(
                error_code=ErrorCode.CONFLICT,
                title="账号正在租用",
                detail="存在 Active Lease 的账号不能执行管理健康检查。",
                status_code=409,
            )
        if (
            snapshot.environment_status is not EnvironmentStatus.ACTIVE
            or snapshot.environment_kind is EnvironmentKind.PRODUCTION
        ):
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="Environment 不允许执行账号验证",
                detail="只有非 Production 的 ACTIVE Environment 可以执行登录探针。",
                status_code=403,
            )
        if connector.status is not ConnectorStatus.ACTIVE:
            raise ApplicationError(
                error_code=ErrorCode.CONFLICT,
                title="Connector 不可用",
                detail="账号健康检查要求 ACTIVE Connector。",
                status_code=409,
            )
        if origin not in snapshot.environment_origins or origin not in connector.allowed_origins:
            raise ApplicationError(
                error_code=ErrorCode.ORIGIN_NOT_ALLOWED,
                title="Origin 不在允许列表",
                detail="健康检查 Origin 必须同时由 Environment 和 Connector 允许。",
                status_code=403,
            )
        required = {
            ProviderCapability.AUTH_PASSWORD,
            ProviderCapability.ACCOUNT_READ,
        }
        if not required.issubset(snapshot.connector_capabilities):
            raise ApplicationError(
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                title="Connector Capability 不足",
                detail="账号健康检查要求 auth.password 与 account.read 实际能力。",
                status_code=503,
            )
        if not self._registry.supports(connector.adapter_key):
            raise ApplicationError(
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                title="Provider Adapter 未配置",
                detail="当前部署没有安装 Connector 对应的可信 Adapter。",
                status_code=503,
            )
        if (
            snapshot.credential_binding_id is None
            or snapshot.credential_revision is None
            or snapshot.secret_ref is None
            or snapshot.secret_version is None
        ):
            raise ApplicationError(
                error_code=ErrorCode.CREDENTIAL_EXPIRED,
                title="登录 Credential 不可用",
                detail="账号没有可用于健康检查的 Active PASSWORD LOGIN Credential。",
                status_code=409,
            )

    async def _require_readable_account(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        account_id: UUID,
    ) -> None:
        account = await self._identity.get_account(connection, account_id, now=utc_now())
        if account is None or not actor.can_read_project(account.project_id):
            raise self._not_found()

    async def _record_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        snapshot: AccountVerificationSnapshot,
        check: AccountHealthCheck,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "accountId": str(snapshot.account_id),
            "connectorInstallationId": str(check.connector_installation_id),
            "healthCheckId": str(check.id),
            "trigger": check.trigger.value,
            "status": check.status.value,
            "roleKey": check.role_key,
        }
        if check.result_health_status is not None:
            payload["resultHealthStatus"] = check.result_health_status.value
        if check.failure_code is not None:
            payload["failureCode"] = check.failure_code.value
        if check.retryable is not None:
            payload["retryable"] = check.retryable
        await self._audit.append(
            connection,
            tenant_id=snapshot.tenant_id,
            project_id=snapshot.project_id,
            environment_id=snapshot.environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="account_health_check",
            entity_id=check.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=snapshot.tenant_id,
                aggregate_type="test_account",
                aggregate_id=snapshot.account_id,
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
        result: AccountHealthVerification,
    ) -> None:
        body = cast(
            dict[str, JsonValue],
            result.model_dump(mode="json", by_alias=True),
        )
        await self._idempotency.complete(
            connection,
            tenant_id=actor.tenant_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response=CachedHttpResponse(status_code=201, body=body),
        )

    @staticmethod
    def identity_fingerprint(connector_id: UUID | None, provider_subject: str) -> str:
        """Create an irreversible Connector-scoped Provider identity fingerprint."""

        if connector_id is None:
            raise ValueError("connector_id is required for identity fingerprint")
        normalized = provider_subject.strip()
        digest = sha256(f"{connector_id}\x00{normalized}".encode()).hexdigest()
        return f"sha256:{digest}"

    @staticmethod
    def verification_account_handle(account_id: UUID) -> str:
        """Create a stable internal handle that never enters an HTTP response."""

        return f"health_{account_id.hex}"

    @staticmethod
    def _next_cursor(
        items: tuple[HealthHistoryItem, ...],
        *,
        has_more: bool,
    ) -> str | None:
        if not has_more or not items:
            return None
        last = items[-1]
        occurred_at = last.created_at if isinstance(last, AccountHealthCheck) else last.occurred_at
        return next_time_cursor(occurred_at, last.id)

    @staticmethod
    def _not_found() -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="资源不存在",
            detail="TestAccount 不存在。",
            status_code=404,
        )

    @staticmethod
    def _forbidden() -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.FORBIDDEN,
            title="没有操作权限",
            detail="当前角色不能执行测试账号健康检查。",
            status_code=403,
        )

    @staticmethod
    def _revision_conflict(current_revision: int) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.PRECONDITION_FAILED,
            title="Revision 已变化",
            detail="TestAccount 已被其他请求修改，请重新读取后再验证。",
            status_code=412,
            headers={"ETag": format_revision_etag(current_revision)},
        )
