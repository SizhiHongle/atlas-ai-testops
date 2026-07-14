"""Auth session coordination with short transactions and encrypted artifacts."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Never
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.account_health import (
    health_failure_outcome,
    map_adapter_health_failure,
)
from atlas_testops.application.credentials import CredentialBrokerService
from atlas_testops.application.ports.providers import AdapterContext, AdapterOperationError
from atlas_testops.application.ports.secrets import (
    PasswordSecretScope,
    SecretProvider,
    SecretProviderError,
)
from atlas_testops.application.ports.sessions import (
    SealedSessionArtifact,
    SessionArtifactScope,
    SessionArtifactVault,
)
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.identity import (
    AccountHealthFailureCode,
    AccountLease,
    AdapterErrorCode,
    ConnectorInstallationRecord,
    ConnectorStatus,
    CredentialAuthMethod,
    CredentialPurpose,
    EnsureLoginSession,
    EnsureLoginSessionResult,
    IssueSecretGrant,
    LoginSessionManualAction,
    LoginSessionReady,
    ManualActionReason,
    ManualActionTicketRecord,
    ProviderCapability,
    RedeemSecretGrant,
    SecretGrantRecord,
    SessionArtifactFailureCode,
    SessionArtifactRecord,
    SessionArtifactStatus,
    SessionArtifactTerminationReason,
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
from atlas_testops.infrastructure.repositories.connectors import ConnectorRepository
from atlas_testops.infrastructure.repositories.leases import LeaseMutationKind, LeaseRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.secret_grants import (
    CredentialSecretAccess,
    SecretGrantClaimKind,
    SecretGrantRepository,
)
from atlas_testops.infrastructure.repositories.sessions import (
    SessionDependencySnapshot,
    SessionRepository,
)
from atlas_testops.infrastructure.session_vault import SessionVaultError


@dataclass(frozen=True, slots=True)
class _AutomaticSessionWork:
    artifact: SessionArtifactRecord
    snapshot: SessionDependencySnapshot
    connector: ConnectorInstallationRecord
    grant: SecretGrantRecord
    adapter: GenericPasswordAdapter
    account_handle: str
    access: CredentialSecretAccess = field(repr=False)


@dataclass(frozen=True, slots=True)
class _LeaseExpired:
    lease: AccountLease


type _PreparationResult = (
    _AutomaticSessionWork
    | _LeaseExpired
    | SessionArtifactRecord
    | LoginSessionReady
    | LoginSessionManualAction
)


AUTH_METHOD_CAPABILITY = {
    CredentialAuthMethod.PASSWORD: ProviderCapability.AUTH_PASSWORD,
    CredentialAuthMethod.OAUTH2: ProviderCapability.AUTH_OAUTH2,
    CredentialAuthMethod.OIDC: ProviderCapability.AUTH_OIDC,
    CredentialAuthMethod.SAML_SSO: ProviderCapability.AUTH_SAML_SSO,
    CredentialAuthMethod.TOTP: ProviderCapability.AUTH_MFA_TOTP,
    CredentialAuthMethod.MANUAL_BOOTSTRAP: ProviderCapability.AUTH_MANUAL_BOOTSTRAP,
}

SESSION_FAILURE_BY_HEALTH = {
    AccountHealthFailureCode.AUTHENTICATION_FAILED: (
        SessionArtifactFailureCode.AUTHENTICATION_FAILED
    ),
    AccountHealthFailureCode.CREDENTIAL_EXPIRED: (
        SessionArtifactFailureCode.CREDENTIAL_EXPIRED
    ),
    AccountHealthFailureCode.ACCOUNT_LOCKED: SessionArtifactFailureCode.ACCOUNT_LOCKED,
    AccountHealthFailureCode.IDENTITY_MISMATCH: (
        SessionArtifactFailureCode.IDENTITY_MISMATCH
    ),
    AccountHealthFailureCode.ROLE_DRIFT: SessionArtifactFailureCode.ROLE_DRIFT,
    AccountHealthFailureCode.RATE_LIMITED: SessionArtifactFailureCode.RATE_LIMITED,
    AccountHealthFailureCode.PROVIDER_UNAVAILABLE: (
        SessionArtifactFailureCode.PROVIDER_UNAVAILABLE
    ),
    AccountHealthFailureCode.NETWORK_TIMEOUT: SessionArtifactFailureCode.NETWORK_TIMEOUT,
    AccountHealthFailureCode.MANUAL_ACTION_REQUIRED: (
        SessionArtifactFailureCode.MANUAL_ACTION_REQUIRED
    ),
    AccountHealthFailureCode.CAPABILITY_UNSUPPORTED: (
        SessionArtifactFailureCode.CAPABILITY_UNSUPPORTED
    ),
    AccountHealthFailureCode.SECRET_UNAVAILABLE: (
        SessionArtifactFailureCode.SECRET_UNAVAILABLE
    ),
    AccountHealthFailureCode.STALE_SNAPSHOT: SessionArtifactFailureCode.STALE_SNAPSHOT,
    AccountHealthFailureCode.INTERNAL_ERROR: SessionArtifactFailureCode.INTERNAL_ERROR,
}


class AuthSessionService:
    """Create or reuse one encrypted login session for an active lease fence."""

    def __init__(
        self,
        database: Database,
        *,
        adapter_registry: AdapterRegistry,
        secret_provider: SecretProvider | None,
        session_vault: SessionArtifactVault | None,
        session_ttl: timedelta = timedelta(minutes=15),
        creation_timeout: timedelta = timedelta(seconds=45),
        attempt_ttl: timedelta = timedelta(minutes=2),
        manual_ticket_ttl: timedelta = timedelta(minutes=10),
        grant_ttl: timedelta = timedelta(seconds=60),
        session_repository: SessionRepository | None = None,
        lease_repository: LeaseRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        connector_repository: ConnectorRepository | None = None,
        grant_repository: SecretGrantRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        credential_broker: CredentialBrokerService | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not timedelta(minutes=1) <= session_ttl <= timedelta(hours=1):
            raise ValueError("session_ttl must be between one minute and one hour")
        if creation_timeout <= timedelta(0) or creation_timeout >= attempt_ttl:
            raise ValueError("creation_timeout must be positive and less than attempt_ttl")
        if not timedelta(minutes=1) <= manual_ticket_ttl <= timedelta(hours=1):
            raise ValueError("manual_ticket_ttl must be between one minute and one hour")
        if not timedelta(seconds=30) <= grant_ttl <= timedelta(minutes=5):
            raise ValueError("grant_ttl must be between 30 seconds and five minutes")
        self._database = database
        self._registry = adapter_registry
        self._secret_provider = secret_provider
        self._vault = session_vault
        self._session_ttl = session_ttl
        self._creation_timeout = creation_timeout
        self._attempt_ttl = attempt_ttl
        self._manual_ticket_ttl = manual_ticket_ttl
        self._grant_ttl = grant_ttl
        self._sessions = session_repository or SessionRepository()
        self._leases = lease_repository or LeaseRepository()
        self._platform = platform_repository or PlatformRepository()
        self._connectors = connector_repository or ConnectorRepository()
        self._grants = grant_repository or SecretGrantRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._clock = clock
        self._credential_broker = credential_broker or CredentialBrokerService(
            database,
            grant_repository=self._grants,
            lease_repository=self._leases,
            platform_repository=self._platform,
            audit_repository=self._audit,
            outbox_repository=self._outbox,
            secret_provider=secret_provider,
            adapter_registry=adapter_registry,
            connector_repository=self._connectors,
            grant_ttl=grant_ttl,
            clock=clock,
        )

    async def ensure(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
    ) -> EnsureLoginSessionResult:
        """Return a ready safe ref or a bounded manual-action ticket."""

        prepared = await self._prepare(actor, lease_id, command)
        if isinstance(prepared, _LeaseExpired):
            self._raise_lease_expired()
        if isinstance(prepared, (LoginSessionReady, LoginSessionManualAction)):
            return prepared
        if isinstance(prepared, SessionArtifactRecord):
            return await self._wait_for_existing(actor, prepared, command)
        return await self._execute_automatic(actor, prepared, command)

    async def _prepare(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
    ) -> _PreparationResult:
        now = self._clock()
        expired: _LeaseExpired | None = None
        prepared: _PreparationResult | None = None
        async with self._database.transaction(actor.database_context()) as connection:
            visible = await self._leases.get_lease(connection, lease_id)
            self._require_lease_access(actor, visible)
            assert visible is not None
            environment = await self._platform.get_environment_for_share(
                connection,
                visible.environment_id,
            )
            self._require_environment(environment, command.allowed_origins)
            connector = await self._connectors.get_for_account_share(
                connection,
                visible.account_id,
            )
            self._require_connector(connector, command.allowed_origins)
            assert connector is not None
            capabilities = await self._connectors.get_capabilities(connection, connector.id)
            required_capability = AUTH_METHOD_CAPABILITY[command.auth_method]
            if required_capability not in {item.name for item in capabilities}:
                raise ApplicationError(
                    error_code=ErrorCode.CONSTRAINT_UNSATISFIED,
                    title="认证能力不可用",
                    detail="Connector 未协商出请求的认证能力。",
                    status_code=422,
                )
            outcome = await self._leases.authorize_sensitive_use(
                connection,
                lease_id=visible.id,
                fencing_token=command.fencing_token,
                now=now,
            )
            if outcome.kind is LeaseMutationKind.EXPIRED:
                assert outcome.lease is not None
                await self._credential_broker.record_lease_event(
                    connection,
                    actor=actor,
                    lease=outcome.lease,
                    event_type="account_lease.expired",
                    occurred_at=now,
                )
                expired = _LeaseExpired(outcome.lease)
            elif outcome.kind in {
                LeaseMutationKind.FENCED,
                LeaseMutationKind.TERMINAL,
            }:
                self._raise_lease_fenced()
            elif outcome.kind is LeaseMutationKind.NOT_FOUND:
                self._raise_not_found()
            else:
                assert outcome.kind is LeaseMutationKind.AUTHORIZED
                assert outcome.lease is not None
                lease = outcome.lease
                if command.worker_identity != lease.worker_id:
                    self._raise_lease_fenced()
                await self._sessions.expire_stale_for_lease(
                    connection,
                    lease_id=lease.id,
                    now=now,
                )
                if command.auth_method is not CredentialAuthMethod.PASSWORD:
                    prepared = await self._create_manual_ticket_locked(
                        connection,
                        actor=actor,
                        lease=lease,
                        connector=connector,
                        command=command,
                        reason=ManualActionReason.AUTH_METHOD_REQUIRES_MANUAL,
                        safe_reason="requested authentication method requires manual action",
                        now=now,
                    )
                else:
                    prepared = await self._prepare_password_locked(
                        connection,
                        actor=actor,
                        lease=lease,
                        connector=connector,
                        command=command,
                        now=now,
                    )
        if expired is not None:
            return expired
        assert prepared is not None
        return prepared

    async def _prepare_password_locked(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        lease: AccountLease,
        connector: ConnectorInstallationRecord,
        command: EnsureLoginSession,
        now: datetime,
    ) -> _AutomaticSessionWork | SessionArtifactRecord | LoginSessionReady:
        if self._secret_provider is None or self._vault is None:
            raise ApplicationError(
                error_code=ErrorCode.SESSION_UNAVAILABLE,
                title="Auth Session Worker 未配置",
                detail="当前执行进程没有配置 Secret Provider 或 Session Vault。",
                status_code=503,
            )
        live = await self._sessions.get_live_for_lease(connection, lease.id)
        if live is not None:
            if live.allowed_origins != command.allowed_origins:
                if live.status is SessionArtifactStatus.CREATING:
                    raise ApplicationError(
                        error_code=ErrorCode.CONFLICT,
                        title="Session 创建范围冲突",
                        detail="同一 Lease 正在为不同 Origin 范围创建 Session。",
                        status_code=409,
                    )
                await self._sessions.revoke_live_for_lease(
                    connection,
                    lease_id=lease.id,
                    reason=SessionArtifactTerminationReason.SUPERSEDED,
                    now=now,
                )
            elif (
                live.status is SessionArtifactStatus.READY
                and CredentialAuthMethod.PASSWORD in live.auth_strength
            ):
                return live.to_ready_result()
            elif live.status is SessionArtifactStatus.CREATING:
                return live
            else:
                await self._sessions.revoke_live_for_lease(
                    connection,
                    lease_id=lease.id,
                    reason=SessionArtifactTerminationReason.SUPERSEDED,
                    now=now,
                )
        try:
            adapter = self._registry.resolve(connector)
        except AdapterNotRegisteredError:
            self._raise_adapter_unavailable()
        if not isinstance(adapter, GenericPasswordAdapter):
            self._raise_adapter_unavailable()
        snapshot = await self._sessions.get_dependency_snapshot(
            connection,
            lease=lease,
            connector_installation_id=connector.id,
            credential_binding_id=None,
            auth_method=CredentialAuthMethod.PASSWORD,
            now=now,
        )
        if snapshot is None:
            raise ApplicationError(
                error_code=ErrorCode.CREDENTIAL_EXPIRED,
                title="Credential 不可用",
                detail="Lease 没有可用于登录的健康 PASSWORD Credential。",
                status_code=409,
            )
        session_expires_at = min(
            now + timedelta(seconds=command.ttl_seconds)
            if command.ttl_seconds is not None
            else now + self._session_ttl,
            lease.expires_at,
            lease.max_expires_at,
        )
        attempt_expires_at = min(now + self._attempt_ttl, session_expires_at)
        artifact_id = new_entity_id()
        object_ref = self._vault.object_ref_for(
            tenant_id=lease.tenant_id,
            artifact_id=artifact_id,
        )
        artifact = await self._sessions.reserve(
            connection,
            artifact_id=artifact_id,
            browser_context_ref=f"bctx_{token_urlsafe(32)}",
            object_ref=object_ref,
            lease=lease,
            connector_installation_id=connector.id,
            snapshot=snapshot,
            allowed_origins=command.allowed_origins,
            created_at=now,
            attempt_expires_at=attempt_expires_at,
            expires_at=session_expires_at,
        )
        if artifact is None:
            concurrent = await self._sessions.get_live_for_lease(
                connection,
                lease.id,
            )
            if concurrent is None:
                raise RuntimeError("session reservation conflict has no live artifact")
            return concurrent
        issue_command = IssueSecretGrant(
            fencing_token=lease.fencing_token,
            purpose=CredentialPurpose.LOGIN,
            worker_identity=lease.worker_id,
            allowed_origins=command.allowed_origins,
        )
        grant = await self._grants.issue(
            connection,
            grant_id=new_entity_id(),
            token_hash=sha256(token_urlsafe(48).encode()).hexdigest(),
            lease=lease,
            connector=connector,
            command=issue_command,
            issued_at=now,
            expires_at=min(now + self._grant_ttl, lease.expires_at),
        )
        if grant is None or grant.credential_binding_id != snapshot.credential_binding_id:
            raise RuntimeError("session reservation credential changed while locked")
        await self._credential_broker.record_grant_event(
            connection,
            actor=actor,
            grant=grant,
            event_type="secret_grant.issued",
            occurred_at=now,
        )
        claim = await self._grants.claim(
            connection,
            expected=grant,
            lease=lease,
            connector=connector,
            command=RedeemSecretGrant(
                worker_identity=lease.worker_id,
                origin=command.allowed_origins[0],
            ),
            now=now,
        )
        if claim.kind is not SecretGrantClaimKind.REDEEMED or claim.access is None:
            raise RuntimeError("newly issued session secret grant could not be redeemed")
        assert claim.grant is not None
        await self._credential_broker.record_grant_event(
            connection,
            actor=actor,
            grant=claim.grant,
            event_type="secret_grant.redeemed",
            occurred_at=now,
        )
        await self._record_artifact_event(
            connection,
            actor=actor,
            artifact=artifact,
            event_type="browser_session_artifact.creating",
            occurred_at=now,
        )
        return _AutomaticSessionWork(
            artifact=artifact,
            snapshot=snapshot,
            connector=connector,
            grant=claim.grant,
            adapter=adapter,
            account_handle=lease.account_handle,
            access=claim.access,
        )

    async def _execute_automatic(
        self,
        actor: ActorContext,
        work: _AutomaticSessionWork,
        command: EnsureLoginSession,
    ) -> EnsureLoginSessionResult:
        assert self._secret_provider is not None
        assert self._vault is not None
        vault = self._vault
        object_ref = work.artifact.object_ref
        assert object_ref is not None
        context = AdapterContext.for_password_operation(
            tenant_id=work.artifact.tenant_id,
            project_id=work.artifact.project_id,
            environment_id=work.artifact.environment_id,
            origin=command.allowed_origins[0],
            request_id=actor.request_id,
            secret_scope=PasswordSecretScope(
                provider=self._secret_provider,
                secret_ref=work.access.secret_ref,
                secret_version=work.access.secret_version,
            ),
        )
        session = None
        try:
            async with asyncio.timeout(self._creation_timeout.total_seconds()):
                session = await work.adapter.establish_session(
                    context=context,
                    account_handle=work.account_handle,
                )
                identity_failure = CredentialBrokerService.authenticated_identity_failure(
                    work.access,
                    connector_installation_id=work.connector.id,
                    provider_subject=session.provider_subject,
                    role_keys=session.role_keys,
                )
                if identity_failure is not None:
                    session.discard()
                    await self._record_failure(
                        actor,
                        work,
                        failure_code=SESSION_FAILURE_BY_HEALTH[identity_failure],
                        health_failure=identity_failure,
                        safe_summary=health_failure_outcome(identity_failure).safe_summary,
                        auth_strength=session.auth_strength,
                    )
                    self._raise_authentication_failed()
                scope = self._artifact_scope(work.artifact)

                async def seal(plaintext: memoryview) -> SealedSessionArtifact:
                    return await vault.seal(
                        object_ref=object_ref,
                        scope=scope,
                        plaintext=plaintext,
                    )

                sealed = await session.with_storage_state(seal)
        except SecretProviderError:
            health_failure = AccountHealthFailureCode.SECRET_UNAVAILABLE
            await self._record_failure(
                actor,
                work,
                failure_code=SessionArtifactFailureCode.SECRET_UNAVAILABLE,
                health_failure=health_failure,
                safe_summary=health_failure_outcome(health_failure).safe_summary,
            )
            self._raise_provider_unavailable("Credential 材料暂时不可用。")
        except AdapterOperationError as error:
            if error.error.code is AdapterErrorCode.MANUAL_ACTION_REQUIRED:
                await self._record_failure(
                    actor,
                    work,
                    failure_code=SessionArtifactFailureCode.MANUAL_ACTION_REQUIRED,
                    health_failure=None,
                    safe_summary="provider requires a bounded manual authentication action",
                )
                return await self._create_manual_after_challenge(actor, work, command)
            health_failure = map_adapter_health_failure(error)
            await self._record_failure(
                actor,
                work,
                failure_code=SESSION_FAILURE_BY_HEALTH[health_failure],
                health_failure=health_failure,
                safe_summary=health_failure_outcome(health_failure).safe_summary,
            )
            self._raise_adapter_error(error)
        except TimeoutError:
            health_failure = AccountHealthFailureCode.NETWORK_TIMEOUT
            await self._record_failure(
                actor,
                work,
                failure_code=SessionArtifactFailureCode.NETWORK_TIMEOUT,
                health_failure=health_failure,
                safe_summary=health_failure_outcome(health_failure).safe_summary,
            )
            self._raise_provider_unavailable("Provider 登录流程超时。")
        except SessionVaultError:
            await self._record_failure(
                actor,
                work,
                failure_code=SessionArtifactFailureCode.STORAGE_UNAVAILABLE,
                health_failure=None,
                safe_summary="encrypted session storage is unavailable",
                auth_strength=session.auth_strength if session is not None else (),
            )
            self._raise_session_unavailable()
        except ApplicationError:
            raise
        except Exception as error:
            await self._record_failure(
                actor,
                work,
                failure_code=SessionArtifactFailureCode.INTERNAL_ERROR,
                health_failure=None,
                safe_summary="auth session worker failed",
                auth_strength=session.auth_strength if session is not None else (),
            )
            raise ApplicationError(
                error_code=ErrorCode.INTERNAL_ERROR,
                title="Auth Session Worker 执行失败",
                detail="登录会话未建立，安全清理已进入后台队列。",
                status_code=500,
            ) from error
        ready = await self._finalize_ready(actor, work, sealed)
        if ready is None:
            await self._record_failure(
                actor,
                work,
                failure_code=SessionArtifactFailureCode.STALE_SNAPSHOT,
                health_failure=None,
                safe_summary="session dependencies changed before publication",
                sealed=sealed,
                auth_strength=session.auth_strength if session is not None else (),
            )
            raise ApplicationError(
                error_code=ErrorCode.PRECONDITION_FAILED,
                title="Session 依赖已变化",
                detail="Lease、账号、Credential 或 Connector 在登录期间发生变化。",
                status_code=409,
            )
        return ready.to_ready_result()

    async def _finalize_ready(
        self,
        actor: ActorContext,
        work: _AutomaticSessionWork,
        sealed: SealedSessionArtifact,
    ) -> SessionArtifactRecord | None:
        now = self._clock()
        async with self._database.transaction(actor.database_context()) as connection:
            ready = await self._sessions.finalize_ready(
                connection,
                artifact=work.artifact,
                sealed=sealed,
                auth_strength=(CredentialAuthMethod.PASSWORD,),
                now=now,
            )
            if ready is not None:
                await self._record_artifact_event(
                    connection,
                    actor=actor,
                    artifact=ready,
                    event_type="browser_session_artifact.ready",
                    occurred_at=now,
                )
            return ready

    async def _record_failure(
        self,
        actor: ActorContext,
        work: _AutomaticSessionWork,
        *,
        failure_code: SessionArtifactFailureCode,
        health_failure: AccountHealthFailureCode | None,
        safe_summary: str,
        sealed: SealedSessionArtifact | None = None,
        auth_strength: tuple[CredentialAuthMethod, ...] = (),
    ) -> SessionArtifactRecord | None:
        now = self._clock()
        termination_reason = (
            SessionArtifactTerminationReason.STALE_SNAPSHOT
            if failure_code is SessionArtifactFailureCode.STALE_SNAPSHOT
            else SessionArtifactTerminationReason.CREATION_FAILED
        )
        async with self._database.transaction(actor.database_context()) as connection:
            failed = await self._sessions.fail_creation(
                connection,
                artifact_id=work.artifact.id,
                failure_code=failure_code,
                termination_reason=termination_reason,
                safe_summary=safe_summary,
                now=now,
                sealed=sealed,
                auth_strength=auth_strength,
            )
            if failed is not None:
                await self._record_artifact_event(
                    connection,
                    actor=actor,
                    artifact=failed,
                    event_type="browser_session_artifact.failed",
                    occurred_at=now,
                )
        if health_failure is not None:
            await self._credential_broker.record_runtime_health_failure(
                actor,
                work.grant,
                access=work.access,
                origin=work.artifact.allowed_origins[0],
                failure_code=health_failure,
            )
        return failed

    async def _create_manual_after_challenge(
        self,
        actor: ActorContext,
        work: _AutomaticSessionWork,
        command: EnsureLoginSession,
    ) -> LoginSessionManualAction:
        now = self._clock()
        async with self._database.transaction(actor.database_context()) as connection:
            outcome = await self._leases.authorize_sensitive_use(
                connection,
                lease_id=work.artifact.lease_id,
                fencing_token=work.artifact.lease_fence,
                now=now,
            )
            if outcome.kind is not LeaseMutationKind.AUTHORIZED or outcome.lease is None:
                self._raise_lease_fenced()
            connector = await self._connectors.get_for_account_share(
                connection,
                outcome.lease.account_id,
            )
            self._require_connector(connector, command.allowed_origins)
            assert connector is not None
            if connector.id != work.connector.id:
                raise ApplicationError(
                    error_code=ErrorCode.PRECONDITION_FAILED,
                    title="Connector 依赖已变化",
                    detail="Provider Challenge 期间账号绑定的 Connector 已变化。",
                    status_code=409,
                )
            capabilities = await self._connectors.get_capabilities(connection, connector.id)
            if ProviderCapability.AUTH_PASSWORD not in {
                item.name for item in capabilities
            }:
                raise ApplicationError(
                    error_code=ErrorCode.CONSTRAINT_UNSATISFIED,
                    title="认证能力不可用",
                    detail="Connector 不再提供密码认证能力。",
                    status_code=422,
                )
            return await self._create_manual_ticket_locked(
                connection,
                actor=actor,
                lease=outcome.lease,
                connector=connector,
                command=command,
                reason=ManualActionReason.PROVIDER_CHALLENGE,
                safe_reason="provider requires a bounded manual authentication action",
                now=now,
            )

    async def _create_manual_ticket_locked(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        lease: AccountLease,
        connector: ConnectorInstallationRecord,
        command: EnsureLoginSession,
        reason: ManualActionReason,
        safe_reason: str,
        now: datetime,
    ) -> LoginSessionManualAction:
        await self._sessions.expire_ticket_for_lease(
            connection,
            lease_id=lease.id,
            now=now,
        )
        existing = await self._sessions.get_open_ticket_for_lease(
            connection,
            lease.id,
        )
        if existing is not None:
            if (
                existing.lease_fence == lease.fencing_token
                and existing.worker_identity == lease.worker_id
                and existing.allowed_origins == command.allowed_origins
                and existing.auth_method is command.auth_method
            ):
                return existing.to_manual_result()
            await self._sessions.cancel_open_ticket(
                connection,
                lease_id=lease.id,
                now=now,
            )
        ticket = await self._sessions.create_manual_ticket(
            connection,
            ticket_id=new_entity_id(),
            lease=lease,
            connector_installation_id=connector.id,
            allowed_origins=command.allowed_origins,
            auth_method=command.auth_method,
            reason=reason,
            safe_reason=safe_reason,
            created_at=now,
            expires_at=min(now + self._manual_ticket_ttl, lease.expires_at),
        )
        if ticket is None:
            ticket = await self._sessions.get_open_ticket_for_lease(
                connection,
                lease.id,
            )
        if ticket is None:
            raise RuntimeError("manual action ticket reservation failed")
        await self._record_ticket_event(
            connection,
            actor=actor,
            ticket=ticket,
            event_type="auth_action_ticket.created",
            occurred_at=now,
        )
        return ticket.to_manual_result()

    async def _wait_for_existing(
        self,
        actor: ActorContext,
        artifact: SessionArtifactRecord,
        command: EnsureLoginSession,
    ) -> LoginSessionReady:
        try:
            async with asyncio.timeout(self._attempt_ttl.total_seconds()):
                while True:
                    await asyncio.sleep(0.05)
                    async with self._database.transaction(
                        actor.database_context()
                    ) as connection:
                        current = await self._sessions.get_by_id(connection, artifact.id)
                    if current is None:
                        self._raise_session_unavailable()
                    if current.status is SessionArtifactStatus.READY:
                        if current.allowed_origins != command.allowed_origins:
                            raise ApplicationError(
                                error_code=ErrorCode.CONFLICT,
                                title="Session Origin 范围冲突",
                                detail="已建立 Session 的 Origin 范围与请求不一致。",
                                status_code=409,
                            )
                        return current.to_ready_result()
                    if current.status is not SessionArtifactStatus.CREATING:
                        self._raise_session_unavailable()
        except TimeoutError:
            raise ApplicationError(
                error_code=ErrorCode.SESSION_CREATION_IN_PROGRESS,
                title="Session 仍在创建",
                detail="同一 Lease 的 Auth Session Worker 尚未完成。",
                status_code=409,
                headers={"Retry-After": "1"},
            ) from None

    async def _record_artifact_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        artifact: SessionArtifactRecord,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "leaseId": str(artifact.lease_id),
            "fencingToken": artifact.lease_fence,
            "status": artifact.status.value,
            "expiresAt": artifact.expires_at.isoformat(),
        }
        if artifact.failure_code is not None:
            payload["failureCode"] = artifact.failure_code.value
        if artifact.termination_reason is not None:
            payload["terminationReason"] = artifact.termination_reason.value
        await self._audit.append(
            connection,
            tenant_id=artifact.tenant_id,
            project_id=artifact.project_id,
            environment_id=artifact.environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="browser_session_artifact",
            entity_id=artifact.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=artifact.tenant_id,
                aggregate_type="browser_session_artifact",
                aggregate_id=artifact.id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    async def _record_ticket_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        ticket: ManualActionTicketRecord,
        event_type: str,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "leaseId": str(ticket.lease_id),
            "fencingToken": ticket.lease_fence,
            "authMethod": ticket.auth_method.value,
            "reason": ticket.reason.value,
            "status": ticket.status.value,
            "expiresAt": ticket.expires_at.isoformat(),
        }
        await self._audit.append(
            connection,
            tenant_id=ticket.tenant_id,
            project_id=ticket.project_id,
            environment_id=ticket.environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="auth_action_ticket",
            entity_id=ticket.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=ticket.tenant_id,
                aggregate_type="auth_action_ticket",
                aggregate_id=ticket.id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    @staticmethod
    def _artifact_scope(artifact: SessionArtifactRecord) -> SessionArtifactScope:
        return SessionArtifactScope(
            artifact_id=artifact.id,
            tenant_id=artifact.tenant_id,
            project_id=artifact.project_id,
            environment_id=artifact.environment_id,
            lease_id=artifact.lease_id,
            lease_fence=artifact.lease_fence,
            account_id=artifact.account_id,
            connector_installation_id=artifact.connector_installation_id,
            credential_binding_id=artifact.credential_binding_id,
            allowed_origins=artifact.allowed_origins,
            format_version=artifact.format_version,
        )

    @staticmethod
    def _require_lease_access(actor: ActorContext, lease: AccountLease | None) -> None:
        if lease is None or not actor.can_operate_project(lease.project_id):
            AuthSessionService._raise_not_found()

    @staticmethod
    def _require_environment(
        environment: Environment | None,
        origins: tuple[str, ...],
    ) -> None:
        if environment is None:
            AuthSessionService._raise_not_found()
        assert environment is not None
        if environment.status is not EnvironmentStatus.ACTIVE:
            raise ApplicationError(
                error_code=ErrorCode.CONSTRAINT_UNSATISFIED,
                title="Environment 已禁用",
                detail="禁用的 Environment 不能建立登录 Session。",
                status_code=409,
            )
        if environment.kind is EnvironmentKind.PRODUCTION:
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="生产 Session 默认禁用",
                detail="生产 Environment 需要独立审批与执行策略。",
                status_code=403,
            )
        if not set(origins).issubset(environment.allowed_origins):
            raise ApplicationError(
                error_code=ErrorCode.ORIGIN_NOT_ALLOWED,
                title="Origin 不在 Environment 允许列表",
                detail="Session Origin 必须由 Environment 显式允许。",
                status_code=403,
            )

    @staticmethod
    def _require_connector(
        connector: ConnectorInstallationRecord | None,
        origins: tuple[str, ...],
    ) -> None:
        if connector is None:
            raise ApplicationError(
                error_code=ErrorCode.SESSION_UNAVAILABLE,
                title="Connector 不可用",
                detail="TestAccount 没有可用的 Connector 绑定。",
                status_code=503,
            )
        if connector.status is not ConnectorStatus.ACTIVE:
            raise ApplicationError(
                error_code=ErrorCode.SESSION_UNAVAILABLE,
                title="Connector 不可用",
                detail="Connector 未处于已验证的 ACTIVE 状态。",
                status_code=503,
            )
        if not set(origins).issubset(connector.allowed_origins):
            raise ApplicationError(
                error_code=ErrorCode.ORIGIN_NOT_ALLOWED,
                title="Origin 不在 Connector 允许列表",
                detail="Session Origin 必须由 Connector 显式允许。",
                status_code=403,
            )

    @staticmethod
    def _raise_adapter_error(error: AdapterOperationError) -> Never:
        if error.error.code in {
            AdapterErrorCode.AUTHENTICATION_FAILED,
            AdapterErrorCode.CREDENTIAL_EXPIRED,
            AdapterErrorCode.ACCOUNT_LOCKED,
        }:
            AuthSessionService._raise_authentication_failed()
        if error.error.code is AdapterErrorCode.CAPABILITY_UNSUPPORTED:
            raise ApplicationError(
                error_code=ErrorCode.CONSTRAINT_UNSATISFIED,
                title="Session Adapter 能力不可用",
                detail=error.error.safe_message,
                status_code=422,
            )
        AuthSessionService._raise_provider_unavailable(error.error.safe_message)

    @staticmethod
    def _raise_not_found() -> Never:
        raise ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="AccountLease 不存在",
            detail="AccountLease 不存在或当前身份不可见。",
            status_code=404,
        )

    @staticmethod
    def _raise_lease_expired() -> Never:
        raise ApplicationError(
            error_code=ErrorCode.LEASE_EXPIRED,
            title="AccountLease 已过期",
            detail="Lease 已越过服务端 TTL，不能建立 Session。",
            status_code=409,
        )

    @staticmethod
    def _raise_lease_fenced() -> Never:
        raise ApplicationError(
            error_code=ErrorCode.LEASE_FENCED,
            title="AccountLease 已被 Fencing",
            detail="Lease、Worker 或最新 Fencing Token 不匹配。",
            status_code=409,
        )

    @staticmethod
    def _raise_adapter_unavailable() -> Never:
        raise ApplicationError(
            error_code=ErrorCode.SESSION_UNAVAILABLE,
            title="Session Adapter 未配置",
            detail="Connector 没有可建立浏览器 Session 的可信 Adapter。",
            status_code=503,
        )

    @staticmethod
    def _raise_authentication_failed() -> Never:
        raise ApplicationError(
            error_code=ErrorCode.AUTHENTICATION_FAILED,
            title="登录身份验证失败",
            detail="Provider 登录身份或业务角色不符合 Lease 策略。",
            status_code=409,
        )

    @staticmethod
    def _raise_provider_unavailable(detail: str) -> Never:
        raise ApplicationError(
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            title="Provider 暂不可用",
            detail=detail,
            status_code=503,
        )

    @staticmethod
    def _raise_session_unavailable() -> Never:
        raise ApplicationError(
            error_code=ErrorCode.SESSION_UNAVAILABLE,
            title="Session Artifact 不可用",
            detail="登录 Session 未能建立或已经失效。",
            status_code=503,
        )
