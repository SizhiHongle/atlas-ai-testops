"""一次性 Secret Grant 账本与原子消费 Repository。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.identity import (
    AccountLease,
    AccountSource,
    ConnectorInstallationRecord,
    CredentialAuthMethod,
    IssueSecretGrant,
    RedeemSecretGrant,
    SecretGrantRecord,
    SecretGrantStatus,
    SecretGrantTerminationReason,
)

GRANT_COLUMNS = (
    "id, tenant_id, project_id, environment_id, connector_installation_id, "
    "lease_id, account_id, "
    "credential_binding_id, fencing_token, purpose, worker_identity, token_hash, "
    "allowed_origins, status, issued_at, expires_at, redeemed_at, terminated_at, "
    "termination_reason, revision, updated_at"
)
QUALIFIED_GRANT_COLUMNS = ", ".join(
    f"current_grant.{column.strip()}" for column in GRANT_COLUMNS.split(",")
)


class SecretGrantClaimKind(StrEnum):
    """Grant 消费尝试的精确结果。"""

    REDEEMED = "REDEEMED"
    NOT_FOUND = "NOT_FOUND"
    REPLAYED = "REPLAYED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    FENCED = "FENCED"
    ORIGIN_DENIED = "ORIGIN_DENIED"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
    CONNECTOR_UNAVAILABLE = "CONNECTOR_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class CredentialSecretAccess:
    """只在 Broker 与 Adapter 之间传递的 Secret Provider 定位信息。"""

    grant: SecretGrantRecord
    auth_method: CredentialAuthMethod
    account_source: AccountSource
    external_subject_id: str | None
    identity_fingerprint: str | None
    role_key: str
    secret_ref: str = field(repr=False)
    secret_version: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SecretGrantClaimResult:
    """原子消费结果；失败分支不携带 SecretRef。"""

    kind: SecretGrantClaimKind
    grant: SecretGrantRecord | None
    access: CredentialSecretAccess | None = field(default=None, repr=False)


class SecretGrantRepository:
    """维护 Grant Token Hash、单次消费和过期终态。"""

    async def get_by_token_hash(
        self,
        connection: AsyncConnection[DictRow],
        token_hash: str,
    ) -> SecretGrantRecord | None:
        """使用不可逆 Token Hash 定位当前 Tenant 的 Grant。"""

        cursor = await connection.execute(
            f"select {GRANT_COLUMNS} from atlas.secret_grant where token_hash = %s",
            (token_hash,),
        )
        row = await cursor.fetchone()
        return SecretGrantRecord.model_validate(row) if row is not None else None

    async def issue(
        self,
        connection: AsyncConnection[DictRow],
        *,
        grant_id: UUID,
        token_hash: str,
        lease: AccountLease,
        connector: ConnectorInstallationRecord,
        command: IssueSecretGrant,
        issued_at: datetime,
        expires_at: datetime,
    ) -> SecretGrantRecord | None:
        """锁定 PASSWORD Credential，替换旧 Grant 并写入新账本记录。"""

        credential_cursor = await connection.execute(
            """
            select c.id
            from atlas.credential_binding c
            join atlas.test_account a on a.id = c.account_id
            where c.account_id = %s
              and a.connector_installation_id = %s
              and c.purpose = %s
              and c.auth_method = 'PASSWORD'
              and c.status = 'ACTIVE'
              and (c.expires_at is null or c.expires_at > %s)
            order by c.id
            for share of c, a
            limit 1
            """,
            (lease.account_id, connector.id, command.purpose, issued_at),
        )
        credential = await credential_cursor.fetchone()
        if credential is None:
            return None

        await connection.execute(
            """
            update atlas.secret_grant
            set status = 'REVOKED', terminated_at = %s,
                termination_reason = 'REPLACED', revision = revision + 1
            where lease_id = %s
              and credential_binding_id = %s
              and purpose = %s
              and worker_identity = %s
              and status = 'ISSUED'
            """,
            (
                issued_at,
                lease.id,
                credential["id"],
                command.purpose,
                command.worker_identity,
            ),
        )
        cursor = await connection.execute(
            f"""
            insert into atlas.secret_grant (
              id, tenant_id, project_id, environment_id, lease_id, account_id,
              connector_installation_id, credential_binding_id, fencing_token,
              purpose, worker_identity, token_hash, allowed_origins, issued_at,
              expires_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            returning {GRANT_COLUMNS}
            """,
            (
                grant_id,
                lease.tenant_id,
                lease.project_id,
                lease.environment_id,
                lease.id,
                lease.account_id,
                connector.id,
                credential["id"],
                lease.fencing_token,
                command.purpose,
                command.worker_identity,
                token_hash,
                list(command.allowed_origins),
                issued_at,
                expires_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("secret grant insert returned no row")
        return SecretGrantRecord.model_validate(row)

    async def claim(
        self,
        connection: AsyncConnection[DictRow],
        *,
        expected: SecretGrantRecord,
        lease: AccountLease,
        connector: ConnectorInstallationRecord,
        command: RedeemSecretGrant,
        now: datetime,
    ) -> SecretGrantClaimResult:
        """按 Credential → Grant 锁序原子消耗一次 Redemption。"""

        credential_cursor = await connection.execute(
            """
            select c.auth_method, c.secret_ref, c.secret_version,
                   c.status, c.expires_at, a.connector_installation_id,
                   a.source, a.external_subject_id, a.identity_fingerprint,
                   r.role_key
            from atlas.credential_binding c
            join atlas.test_account a on a.id = c.account_id
            join atlas.account_pool p on p.id = a.pool_id
            join atlas.test_role r on r.id = p.role_id
            where c.id = %s and c.account_id = %s and c.purpose = %s
            for share of c, a
            """,
            (
                expected.credential_binding_id,
                expected.account_id,
                expected.purpose,
            ),
        )
        credential = await credential_cursor.fetchone()
        cursor = await connection.execute(
            f"""
            select {GRANT_COLUMNS}
            from atlas.secret_grant
            where id = %s and token_hash = %s
            for update
            """,
            (expected.id, expected.token_hash),
        )
        row = await cursor.fetchone()
        if row is None:
            return SecretGrantClaimResult(SecretGrantClaimKind.NOT_FOUND, None)
        grant = SecretGrantRecord.model_validate(row)
        if grant.status is SecretGrantStatus.REDEEMED:
            return SecretGrantClaimResult(SecretGrantClaimKind.REPLAYED, grant)
        if grant.status is SecretGrantStatus.EXPIRED:
            return SecretGrantClaimResult(SecretGrantClaimKind.EXPIRED, grant)
        if grant.status is SecretGrantStatus.REVOKED:
            return SecretGrantClaimResult(SecretGrantClaimKind.REVOKED, grant)
        if now >= grant.expires_at:
            expired = await self._terminate(
                connection,
                grant=grant,
                status=SecretGrantStatus.EXPIRED,
                reason=SecretGrantTerminationReason.EXPIRED,
                now=now,
            )
            return SecretGrantClaimResult(SecretGrantClaimKind.EXPIRED, expired)
        if (
            grant.connector_installation_id != connector.id
            or expected.connector_installation_id != connector.id
            or credential is None
            or credential["connector_installation_id"] != connector.id
        ):
            unavailable = await self._terminate(
                connection,
                grant=grant,
                status=SecretGrantStatus.REVOKED,
                reason=SecretGrantTerminationReason.CONNECTOR_UNAVAILABLE,
                now=now,
            )
            return SecretGrantClaimResult(
                SecretGrantClaimKind.CONNECTOR_UNAVAILABLE,
                unavailable,
            )
        if (
            grant.lease_id != lease.id
            or grant.fencing_token != lease.fencing_token
            or grant.worker_identity != lease.worker_id
            or command.worker_identity != grant.worker_identity
        ):
            return SecretGrantClaimResult(SecretGrantClaimKind.FENCED, grant)
        if command.origin not in grant.allowed_origins:
            return SecretGrantClaimResult(SecretGrantClaimKind.ORIGIN_DENIED, grant)
        if credential["status"] != "ACTIVE" or (
            credential["expires_at"] is not None and credential["expires_at"] <= now
        ):
            unavailable = await self._terminate(
                connection,
                grant=grant,
                status=SecretGrantStatus.REVOKED,
                reason=SecretGrantTerminationReason.CREDENTIAL_UNAVAILABLE,
                now=now,
            )
            return SecretGrantClaimResult(
                SecretGrantClaimKind.CREDENTIAL_UNAVAILABLE,
                unavailable,
            )
        redeemed_cursor = await connection.execute(
            f"""
            update atlas.secret_grant
            set status = 'REDEEMED', redeemed_at = %s, revision = revision + 1
            where id = %s and status = 'ISSUED'
            returning {GRANT_COLUMNS}
            """,
            (now, grant.id),
        )
        redeemed_row = await redeemed_cursor.fetchone()
        if redeemed_row is None:
            return SecretGrantClaimResult(SecretGrantClaimKind.REPLAYED, grant)
        redeemed = SecretGrantRecord.model_validate(redeemed_row)
        access = CredentialSecretAccess(
            grant=redeemed,
            auth_method=CredentialAuthMethod(credential["auth_method"]),
            account_source=AccountSource(credential["source"]),
            external_subject_id=credential["external_subject_id"],
            identity_fingerprint=credential["identity_fingerprint"],
            role_key=credential["role_key"],
            secret_ref=credential["secret_ref"],
            secret_version=credential["secret_version"],
        )
        return SecretGrantClaimResult(
            SecretGrantClaimKind.REDEEMED,
            redeemed,
            access,
        )

    async def reap_expired(
        self,
        connection: AsyncConnection[DictRow],
        *,
        now: datetime,
        limit: int,
    ) -> tuple[SecretGrantRecord, ...]:
        """以 SKIP LOCKED 批量终结过期且未消费的 Grant。"""

        cursor = await connection.execute(
            f"""
            with candidates as (
              select id
              from atlas.secret_grant
              where status = 'ISSUED' and expires_at <= %s
              order by expires_at, id
              limit %s
              for update skip locked
            )
            update atlas.secret_grant as current_grant
            set status = 'EXPIRED', terminated_at = %s,
                termination_reason = 'EXPIRED', revision = current_grant.revision + 1
            from candidates
            where current_grant.id = candidates.id
            returning {QUALIFIED_GRANT_COLUMNS}
            """,
            (now, limit, now),
        )
        return tuple(SecretGrantRecord.model_validate(row) for row in await cursor.fetchall())

    @staticmethod
    async def _terminate(
        connection: AsyncConnection[DictRow],
        *,
        grant: SecretGrantRecord,
        status: SecretGrantStatus,
        reason: SecretGrantTerminationReason,
        now: datetime,
    ) -> SecretGrantRecord:
        cursor = await connection.execute(
            f"""
            update atlas.secret_grant
            set status = %s, terminated_at = %s, termination_reason = %s,
                revision = revision + 1
            where id = %s and status = 'ISSUED'
            returning {GRANT_COLUMNS}
            """,
            (status, now, reason, grant.id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("locked issued secret grant could not be terminated")
        return SecretGrantRecord.model_validate(row)
