"""平台用户、成员关系、凭据与 Session 的 PostgreSQL Repository。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.auth import (
    AuthenticationMethod,
    BootstrapPrincipalCommand,
    PlatformMembership,
    PlatformUser,
    PlatformUserStatus,
)

USER_COLUMNS = "id, email, display_name, status, revision, created_at, updated_at"
QUALIFIED_USER_COLUMNS = (
    "u.id, u.email, u.display_name, u.status, u.revision, u.created_at, u.updated_at"
)
MEMBERSHIP_COLUMNS = (
    "id, tenant_id, project_id, user_id, role, status, revision, created_at, updated_at"
)


@dataclass(frozen=True, slots=True)
class LoginCandidate:
    """只在认证应用服务内部短时存在的密码校验材料。"""

    user: PlatformUser
    password_hash: str
    failed_attempts: int
    locked_until: datetime | None
    memberships: tuple[PlatformMembership, ...]


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    """由不可逆 Token Hash 定位的 Session 身份。"""

    session_id: UUID
    user: PlatformUser
    tenant_id: UUID
    project_id: UUID
    authentication_method: AuthenticationMethod
    remembered: bool
    idle_expires_at: datetime
    absolute_expires_at: datetime
    last_seen_at: datetime


class AuthRepository:
    """只实现认证事实持久化，不处理 Cookie 或权限策略。"""

    async def create_bootstrap_principal(
        self,
        connection: AsyncConnection[DictRow],
        *,
        user_id: UUID,
        membership_id: UUID,
        command: BootstrapPrincipalCommand,
        password_hash: str,
    ) -> tuple[PlatformUser, PlatformMembership] | None:
        """创建首个组织管理员；邮箱已存在时返回 None。"""

        user_cursor = await connection.execute(
            f"""
            insert into atlas.platform_user (id, email, display_name)
            values (%s, %s, %s)
            on conflict (email) do nothing
            returning {USER_COLUMNS}
            """,
            (user_id, command.email, command.display_name),
        )
        user_row = await user_cursor.fetchone()
        if user_row is None:
            return None

        await connection.execute(
            """
            insert into atlas.password_credential (user_id, password_hash)
            values (%s, %s)
            """,
            (user_id, password_hash),
        )
        membership_cursor = await connection.execute(
            f"""
            insert into atlas.platform_membership (
              id, tenant_id, project_id, user_id, role
            ) values (%s, %s, null, %s, 'ORG_ADMIN')
            returning {MEMBERSHIP_COLUMNS}
            """,
            (membership_id, command.tenant_id, user_id),
        )
        membership_row = await membership_cursor.fetchone()
        if membership_row is None:
            raise RuntimeError("bootstrap membership was not returned")
        return (
            PlatformUser.model_validate(user_row),
            PlatformMembership.model_validate(membership_row),
        )

    async def find_login_candidate(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        project_id: UUID,
        email: str,
    ) -> LoginCandidate | None:
        """只为指定 Workspace 返回具备有效授权的登录候选。"""

        candidate_cursor = await connection.execute(
            f"""
            select {QUALIFIED_USER_COLUMNS},
                   c.password_hash, c.failed_attempts, c.locked_until
            from atlas.platform_user u
            join atlas.password_credential c on c.user_id = u.id
            where u.email = %s
            """,
            (email,),
        )
        row = await candidate_cursor.fetchone()
        if row is None:
            return None

        memberships = await self.list_active_memberships(
            connection,
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=row["id"],
        )
        if not memberships:
            return None
        return LoginCandidate(
            user=PlatformUser.model_validate(
                {
                    "id": row["id"],
                    "email": row["email"],
                    "display_name": row["display_name"],
                    "status": row["status"],
                    "revision": row["revision"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            ),
            password_hash=row["password_hash"],
            failed_attempts=row["failed_attempts"],
            locked_until=row["locked_until"],
            memberships=memberships,
        )

    async def list_active_memberships(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        project_id: UUID,
        user_id: UUID,
    ) -> tuple[PlatformMembership, ...]:
        """加载组织管理员授权和当前 Project 的全部有效角色。"""

        cursor = await connection.execute(
            f"""
            select {MEMBERSHIP_COLUMNS}
            from atlas.platform_membership
            where tenant_id = %s and user_id = %s and status = 'ACTIVE'
              and (project_id is null or project_id = %s)
            order by role, id
            """,
            (tenant_id, user_id, project_id),
        )
        return tuple(
            PlatformMembership.model_validate(row) for row in await cursor.fetchall()
        )

    async def record_failed_login(
        self,
        connection: AsyncConnection[DictRow],
        *,
        user_id: UUID,
        maximum_failures: int,
        locked_until: datetime,
    ) -> int:
        """原子累计失败次数，并在达到门槛时设置临时锁定。"""

        cursor = await connection.execute(
            """
            update atlas.password_credential
            set failed_attempts = failed_attempts + 1,
                locked_until = case
                  when failed_attempts + 1 >= %s then %s
                  else locked_until
                end
            where user_id = %s
            returning failed_attempts
            """,
            (maximum_failures, locked_until, user_id),
        )
        row = await cursor.fetchone()
        return int(row["failed_attempts"]) if row is not None else 0

    async def complete_login(
        self,
        connection: AsyncConnection[DictRow],
        *,
        candidate: LoginCandidate,
        tenant_id: UUID,
        project_id: UUID,
        replacement_password_hash: str | None,
        session_id: UUID,
        token_hash: str,
        user_agent_hash: str | None,
        remembered: bool,
        created_at: datetime,
        idle_expires_at: datetime,
        absolute_expires_at: datetime,
    ) -> bool:
        """在授权和密码版本仍有效时创建 Session，并重置失败计数。"""

        credential_cursor = await connection.execute(
            """
            select c.password_hash, u.status
            from atlas.password_credential c
            join atlas.platform_user u on u.id = c.user_id
            where c.user_id = %s
            for update
            """,
            (candidate.user.id,),
        )
        credential = await credential_cursor.fetchone()
        if (
            credential is None
            or credential["password_hash"] != candidate.password_hash
            or credential["status"] != PlatformUserStatus.ACTIVE
        ):
            return False
        memberships = await self.list_active_memberships(
            connection,
            tenant_id=tenant_id,
            project_id=project_id,
            user_id=candidate.user.id,
        )
        if not memberships:
            return False

        if replacement_password_hash is None:
            await connection.execute(
                """
                update atlas.password_credential
                set failed_attempts = 0, locked_until = null
                where user_id = %s
                """,
                (candidate.user.id,),
            )
        else:
            await connection.execute(
                """
                update atlas.password_credential
                set password_hash = %s,
                    failed_attempts = 0,
                    locked_until = null,
                    password_changed_at = %s,
                    revision = revision + 1
                where user_id = %s
                """,
                (replacement_password_hash, created_at, candidate.user.id),
            )
        await connection.execute(
            """
            insert into atlas.platform_session (
              id, token_hash, user_id, tenant_id, project_id, auth_method,
              remembered, user_agent_hash, created_at, last_seen_at,
              idle_expires_at, absolute_expires_at
            ) values (%s, %s, %s, %s, %s, 'PASSWORD', %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                token_hash,
                candidate.user.id,
                tenant_id,
                project_id,
                remembered,
                user_agent_hash,
                created_at,
                created_at,
                idle_expires_at,
                absolute_expires_at,
            ),
        )
        return True

    async def get_session_identity(
        self,
        connection: AsyncConnection[DictRow],
        *,
        token_hash: str,
        now: datetime,
    ) -> SessionIdentity | None:
        """读取未撤销、未过期且主体仍启用的 Session。"""

        cursor = await connection.execute(
            """
            select
              s.id as session_id, s.tenant_id, s.project_id, s.auth_method, s.remembered,
              s.idle_expires_at, s.absolute_expires_at, s.last_seen_at,
              u.id, u.email, u.display_name, u.status, u.revision,
              u.created_at, u.updated_at
            from atlas.platform_session s
            join atlas.platform_user u on u.id = s.user_id
            where s.token_hash = %s and s.revoked_at is null
            """,
            (token_hash,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if (
            row["status"] != PlatformUserStatus.ACTIVE
            or row["idle_expires_at"] <= now
            or row["absolute_expires_at"] <= now
        ):
            await connection.execute(
                """
                update atlas.platform_session
                set revoked_at = coalesce(revoked_at, %s)
                where token_hash = %s
                """,
                (now, token_hash),
            )
            return None
        return SessionIdentity(
            session_id=row["session_id"],
            user=PlatformUser.model_validate(
                {
                    "id": row["id"],
                    "email": row["email"],
                    "display_name": row["display_name"],
                    "status": row["status"],
                    "revision": row["revision"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            ),
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            authentication_method=AuthenticationMethod(row["auth_method"]),
            remembered=row["remembered"],
            idle_expires_at=row["idle_expires_at"],
            absolute_expires_at=row["absolute_expires_at"],
            last_seen_at=row["last_seen_at"],
        )

    async def touch_session(
        self,
        connection: AsyncConnection[DictRow],
        *,
        token_hash: str,
        seen_at: datetime,
        idle_expires_at: datetime,
    ) -> None:
        """按配置节流后滑动 Idle Expiry，不延长 Absolute Expiry。"""

        await connection.execute(
            """
            update atlas.platform_session
            set last_seen_at = %s, idle_expires_at = %s
            where token_hash = %s and revoked_at is null
            """,
            (seen_at, idle_expires_at, token_hash),
        )

    async def revoke_session(
        self,
        connection: AsyncConnection[DictRow],
        *,
        token_hash: str,
        revoked_at: datetime,
    ) -> bool:
        """幂等撤销当前 Session。"""

        cursor = await connection.execute(
            """
            update atlas.platform_session
            set revoked_at = %s
            where token_hash = %s and revoked_at is null
            returning id
            """,
            (revoked_at, token_hash),
        )
        return await cursor.fetchone() is not None
