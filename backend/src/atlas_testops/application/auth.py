"""Atlas 平台用户认证、Session 与 Actor Context 应用服务。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.core.config import Settings
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.auth import (
    AuthenticationMethod,
    BootstrapPrincipal,
    BootstrapPrincipalCommand,
    LoginCommand,
    PlatformMembership,
    PlatformRole,
    PlatformSessionView,
    PlatformUserStatus,
)
from atlas_testops.domain.events import DomainEvent
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.passwords import PasswordService
from atlas_testops.infrastructure.repositories.auth import AuthRepository, LoginCandidate
from atlas_testops.infrastructure.repositories.platform import PlatformRepository


@dataclass(frozen=True, slots=True)
class LoginResult:
    """API Adapter 设置 Cookie 所需的内部登录结果。"""

    session: PlatformSessionView
    token: str
    max_age_seconds: int | None


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    """经数据库和实时成员关系共同校验的请求身份。"""

    session: PlatformSessionView
    actor: ActorContext


def session_token_hash(token: str) -> str:
    """数据库只保存不可逆 Session Token 摘要。"""

    return sha256(token.encode()).hexdigest()


def user_agent_hash(user_agent: str | None) -> str | None:
    """只保留客户端标识摘要，避免长期保存完整 User-Agent。"""

    if user_agent is None or not user_agent.strip():
        return None
    return sha256(user_agent.strip().encode()).hexdigest()


class AuthService:
    """协调密码校验、授权复核、Session、审计和 Outbox。"""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        password_service: PasswordService,
        auth_repository: AuthRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self._database = database
        self._settings = settings
        self._passwords = password_service
        self._auth = auth_repository or AuthRepository()
        self._platform = platform_repository or PlatformRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def bootstrap_principal(
        self,
        command: BootstrapPrincipalCommand,
        *,
        request_id: str,
    ) -> BootstrapPrincipal:
        """为已有 Tenant 创建首个组织管理员，仅供 Development 调用。"""

        context = DatabaseContext(tenant_id=command.tenant_id, request_id=request_id)
        async with self._database.transaction(context) as connection:
            tenant = await self._platform.get_tenant(connection, command.tenant_id)
            project = await self._platform.get_project(connection, command.project_id)
            if tenant is None or project is None:
                raise self._not_found("Tenant 或默认 Project 不存在。")

        password_hash = await self._passwords.hash_password_async(
            command.password.get_secret_value()
        )
        user_id = new_entity_id()
        membership_id = new_entity_id()
        now = utc_now()
        async with self._database.transaction(context) as connection:
            tenant = await self._platform.get_tenant(connection, command.tenant_id)
            project = await self._platform.get_project(connection, command.project_id)
            if tenant is None or project is None:
                raise self._not_found("Tenant 或默认 Project 不存在。")
            created = await self._auth.create_bootstrap_principal(
                connection,
                user_id=user_id,
                membership_id=membership_id,
                command=command,
                password_hash=password_hash,
            )
            if created is None:
                raise self._conflict(
                    "平台用户已存在",
                    "该邮箱已绑定 Atlas PlatformPrincipal。",
                )
            user, membership = created
            payload: dict[str, JsonValue] = {
                "userId": str(user.id),
                "membershipId": str(membership.id),
                "role": membership.role.value,
            }
            await self._audit.append(
                connection,
                tenant_id=command.tenant_id,
                project_id=command.project_id,
                environment_id=None,
                actor_id=user.id,
                event_type="platform_user.bootstrapped",
                entity_type="platform_user",
                entity_id=user.id,
                occurred_at=now,
                payload=payload,
                request_id=request_id,
            )
            await self._outbox.append(
                connection,
                DomainEvent(
                    tenant_id=command.tenant_id,
                    aggregate_type="platform_user",
                    aggregate_id=user.id,
                    event_type="platform_user.bootstrapped",
                    occurred_at=now,
                    payload=payload,
                ),
            )
            return BootstrapPrincipal(user=user, membership=membership)

    async def login(
        self,
        command: LoginCommand,
        *,
        request_id: str,
        user_agent: str | None,
    ) -> LoginResult:
        """校验 Workspace、成员关系和密码后签发 Opaque Session。"""

        now = utc_now()
        context = DatabaseContext(tenant_id=command.tenant_id, request_id=request_id)
        tenant = None
        project = None
        candidate: LoginCandidate | None = None
        async with self._database.transaction(context) as connection:
            tenant = await self._platform.get_tenant(connection, command.tenant_id)
            project = await self._platform.get_project(connection, command.project_id)
            if tenant is not None and project is not None:
                candidate = await self._auth.find_login_candidate(
                    connection,
                    tenant_id=command.tenant_id,
                    project_id=command.project_id,
                    email=command.email,
                )

        password_hash = (
            candidate.password_hash if candidate is not None else self._passwords.dummy_hash
        )
        verification = await self._passwords.verify_password_async(
            password_hash,
            command.password.get_secret_value(),
        )
        locked = (
            candidate is not None
            and candidate.locked_until is not None
            and candidate.locked_until > now
        )
        valid = (
            verification.valid
            and not locked
            and candidate is not None
            and candidate.user.status is PlatformUserStatus.ACTIVE
            and tenant is not None
            and project is not None
        )
        if not valid:
            if candidate is not None and tenant is not None and project is not None:
                await self._record_login_rejection(
                    context=context,
                    candidate=candidate,
                    project_id=command.project_id,
                    request_id=request_id,
                    now=now,
                    increment_failure=not locked,
                )
            raise self._authentication_failed()

        assert candidate is not None
        assert tenant is not None
        assert project is not None
        replacement_hash = None
        if verification.needs_rehash:
            replacement_hash = await self._passwords.hash_password_async(
                command.password.get_secret_value()
            )

        token = token_urlsafe(32)
        token_hash = session_token_hash(token)
        absolute_expires_at = self._absolute_expiry(now, command.remember)
        idle_expires_at = min(
            now + self._idle_duration(command.remember),
            absolute_expires_at,
        )
        session_id = new_entity_id()
        memberships = candidate.memberships
        async with self._database.transaction(context) as connection:
            tenant = await self._platform.get_tenant(connection, command.tenant_id)
            project = await self._platform.get_project(connection, command.project_id)
            completed = (
                tenant is not None
                and project is not None
                and await self._auth.complete_login(
                    connection,
                    candidate=candidate,
                    tenant_id=command.tenant_id,
                    project_id=command.project_id,
                    replacement_password_hash=replacement_hash,
                    session_id=session_id,
                    token_hash=token_hash,
                    user_agent_hash=user_agent_hash(user_agent),
                    remembered=command.remember,
                    created_at=now,
                    idle_expires_at=idle_expires_at,
                    absolute_expires_at=absolute_expires_at,
                )
            )
            if not completed or tenant is None or project is None:
                raise self._authentication_failed()
            memberships = await self._auth.list_active_memberships(
                connection,
                tenant_id=command.tenant_id,
                project_id=command.project_id,
                user_id=candidate.user.id,
            )
            payload: dict[str, JsonValue] = {
                "sessionId": str(session_id),
                "userId": str(candidate.user.id),
                "projectId": str(project.id),
                "authenticationMethod": "PASSWORD",
            }
            await self._record_session_event(
                connection,
                tenant_id=command.tenant_id,
                project_id=command.project_id,
                user_id=candidate.user.id,
                session_id=session_id,
                event_type="platform_session.created",
                occurred_at=now,
                request_id=request_id,
                payload=payload,
            )

        session = PlatformSessionView(
            user=candidate.user,
            tenant=tenant,
            project=project,
            roles=self._roles(memberships),
            authentication_method=AuthenticationMethod.PASSWORD,
            expires_at=absolute_expires_at,
        )
        max_age = (
            int((absolute_expires_at - now).total_seconds()) if command.remember else None
        )
        return LoginResult(session=session, token=token, max_age_seconds=max_age)

    async def resolve_session(
        self,
        token: str,
        *,
        request_id: str,
    ) -> ResolvedSession:
        """解析 Cookie，并实时复核 User、Membership、Tenant 与 Project。"""

        if not 20 <= len(token) <= 512:
            raise self._authentication_required()
        token_hash = session_token_hash(token)
        now = utc_now()
        identity = None
        async with self._database.session_transaction(
            token_hash=token_hash,
            request_id=request_id,
        ) as connection:
            identity = await self._auth.get_session_identity(
                connection,
                token_hash=token_hash,
                now=now,
            )
            if identity is not None and now - identity.last_seen_at >= timedelta(
                seconds=self._settings.session_touch_interval_seconds
            ):
                await self._auth.touch_session(
                    connection,
                    token_hash=token_hash,
                    seen_at=now,
                    idle_expires_at=min(
                        now + self._idle_duration(identity.remembered),
                        identity.absolute_expires_at,
                    ),
                )
        if identity is None:
            raise self._authentication_required()

        context = DatabaseContext(
            tenant_id=identity.tenant_id,
            actor_id=identity.user.id,
            request_id=request_id,
        )
        async with self._database.transaction(context) as connection:
            tenant = await self._platform.get_tenant(connection, identity.tenant_id)
            project = await self._platform.get_project(connection, identity.project_id)
            memberships = await self._auth.list_active_memberships(
                connection,
                tenant_id=identity.tenant_id,
                project_id=identity.project_id,
                user_id=identity.user.id,
            )
            authorization_revoked = tenant is None or project is None or not memberships
            if authorization_revoked:
                await self._auth.revoke_session(
                    connection,
                    token_hash=token_hash,
                    revoked_at=now,
                )
        if authorization_revoked:
            raise self._authentication_required()
        assert tenant is not None
        assert project is not None

        roles = self._roles(memberships)
        return ResolvedSession(
            session=PlatformSessionView(
                user=identity.user,
                tenant=tenant,
                project=project,
                roles=roles,
                authentication_method=identity.authentication_method,
                expires_at=identity.absolute_expires_at,
            ),
            actor=ActorContext(
                tenant_id=identity.tenant_id,
                actor_id=identity.user.id,
                request_id=request_id,
                current_project_id=identity.project_id,
                grants=tuple(
                    AccessGrant(role=membership.role, project_id=membership.project_id)
                    for membership in memberships
                ),
            ),
        )

    async def logout(self, token: str, *, request_id: str) -> bool:
        """撤销当前 Session；重复退出保持幂等。"""

        if not 20 <= len(token) <= 512:
            return False
        token_hash = session_token_hash(token)
        now = utc_now()
        async with self._database.session_transaction(
            token_hash=token_hash,
            request_id=request_id,
        ) as connection:
            identity = await self._auth.get_session_identity(
                connection,
                token_hash=token_hash,
                now=now,
            )
        if identity is None:
            return False

        context = DatabaseContext(
            tenant_id=identity.tenant_id,
            actor_id=identity.user.id,
            request_id=request_id,
        )
        async with self._database.transaction(context) as connection:
            revoked = await self._auth.revoke_session(
                connection,
                token_hash=token_hash,
                revoked_at=now,
            )
            if not revoked:
                return False
            payload: dict[str, JsonValue] = {
                "sessionId": str(identity.session_id),
                "userId": str(identity.user.id),
                "projectId": str(identity.project_id),
            }
            await self._record_session_event(
                connection,
                tenant_id=identity.tenant_id,
                project_id=identity.project_id,
                user_id=identity.user.id,
                session_id=identity.session_id,
                event_type="platform_session.revoked",
                occurred_at=now,
                request_id=request_id,
                payload=payload,
            )
            return True

    async def _record_login_rejection(
        self,
        *,
        context: DatabaseContext,
        candidate: LoginCandidate,
        project_id: UUID,
        request_id: str,
        now: datetime,
        increment_failure: bool,
    ) -> None:
        async with self._database.transaction(context) as connection:
            failed_attempts = candidate.failed_attempts
            if increment_failure:
                failed_attempts = await self._auth.record_failed_login(
                    connection,
                    user_id=candidate.user.id,
                    maximum_failures=self._settings.password_max_failures,
                    locked_until=now
                    + timedelta(minutes=self._settings.password_lock_minutes),
                )
            await self._audit.append(
                connection,
                tenant_id=context.tenant_id,
                project_id=project_id,
                environment_id=None,
                actor_id=candidate.user.id,
                event_type="platform_login.rejected",
                entity_type="platform_user",
                entity_id=candidate.user.id,
                occurred_at=now,
                payload={
                    "reason": "LOCKED" if not increment_failure else "INVALID_CREDENTIALS",
                    "failedAttempts": failed_attempts,
                },
                request_id=request_id,
            )

    async def _record_session_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        project_id: UUID,
        user_id: UUID,
        session_id: UUID,
        event_type: str,
        occurred_at: datetime,
        request_id: str,
        payload: dict[str, JsonValue],
    ) -> None:
        await self._audit.append(
            connection,
            tenant_id=tenant_id,
            project_id=project_id,
            environment_id=None,
            actor_id=user_id,
            event_type=event_type,
            entity_type="platform_session",
            entity_id=session_id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=tenant_id,
                aggregate_type="platform_session",
                aggregate_id=session_id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    def _idle_duration(self, remembered: bool) -> timedelta:
        if remembered:
            return timedelta(hours=self._settings.remembered_session_idle_hours)
        return timedelta(minutes=self._settings.session_idle_minutes)

    def _absolute_expiry(self, now: datetime, remembered: bool) -> datetime:
        if remembered:
            return now + timedelta(days=self._settings.remembered_session_days)
        return now + timedelta(hours=self._settings.session_absolute_hours)

    @staticmethod
    def _roles(
        memberships: tuple[PlatformMembership, ...],
    ) -> tuple[PlatformRole, ...]:
        roles = {membership.role for membership in memberships}
        return tuple(sorted(roles, key=lambda role: role.value))

    @staticmethod
    def _authentication_required() -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.AUTHENTICATION_REQUIRED,
            title="需要登录",
            detail="Session 不存在、已过期或授权已经撤销。",
            status_code=401,
        )

    @staticmethod
    def _authentication_failed() -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.AUTHENTICATION_FAILED,
            title="登录失败",
            detail="账号、密码或测试空间不正确。",
            status_code=401,
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
    def _conflict(title: str, detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title=title,
            detail=detail,
            status_code=409,
        )
