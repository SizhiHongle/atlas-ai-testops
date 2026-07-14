"""PostgreSQL repository for account health and immutable state transitions."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.pagination import TimeCursor
from atlas_testops.domain.identity import (
    AccountHealth,
    AccountHealthCheck,
    AccountHealthCheckStatus,
    AccountHealthCheckTrigger,
    AccountHealthFailureCode,
    AccountLifecycle,
    AccountOperationalStatus,
    AccountSource,
    AccountStateTransition,
    AccountStateTransitionReason,
    AccountSyncStatus,
    ConnectorStatus,
    ProviderCapability,
)
from atlas_testops.domain.platform import EnvironmentKind, EnvironmentStatus

HEALTH_CHECK_COLUMNS = (
    "id, tenant_id, project_id, environment_id, account_id, "
    "connector_installation_id, credential_binding_id, trigger, status, origin, "
    "role_key, account_revision, connector_revision, credential_revision, "
    "result_health_status, failure_code, retryable, safe_summary, actor_id, "
    "request_id, started_at, finished_at, expires_at, revision, created_at, updated_at"
)
TRANSITION_COLUMNS = (
    "id, tenant_id, project_id, environment_id, account_id, health_check_id, "
    "reason, from_lifecycle_status, to_lifecycle_status, from_health_status, "
    "to_health_status, from_operational_status, to_operational_status, "
    "from_sync_status, to_sync_status, from_cooldown_until, to_cooldown_until, "
    "safe_summary, actor_id, request_id, occurred_at"
)


@dataclass(frozen=True, slots=True)
class AccountStateRecord:
    """Account state required for before-and-after transition snapshots."""

    lifecycle_status: AccountLifecycle
    health_status: AccountHealth
    operational_status: AccountOperationalStatus
    sync_status: AccountSyncStatus
    cooldown_until: datetime | None
    consecutive_health_failures: int
    revision: int


@dataclass(frozen=True, slots=True)
class AccountStateChange:
    """Scoped before-and-after state produced by a dependency invalidation."""

    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    account_id: UUID
    before: AccountStateRecord
    after: AccountStateRecord


@dataclass(frozen=True, slots=True)
class AccountVerificationSnapshot:
    """Account, policy, Connector, and Credential snapshot locked briefly."""

    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    account_id: UUID
    pool_id: UUID
    connector_installation_id: UUID | None
    account_revision: int
    source: AccountSource
    external_subject_id: str | None
    identity_fingerprint: str | None
    state: AccountStateRecord
    role_key: str
    health_failure_threshold: int
    health_retry_cooldown_seconds: int
    environment_kind: EnvironmentKind
    environment_status: EnvironmentStatus
    environment_origins: tuple[str, ...]
    connector_adapter_key: str | None
    connector_status: ConnectorStatus | None
    connector_revision: int | None
    connector_origins: tuple[str, ...]
    connector_capabilities: frozenset[ProviderCapability]
    credential_binding_id: UUID | None
    credential_revision: int | None
    secret_ref: str | None = field(repr=False)
    secret_version: str | None = field(repr=False)
    active_lease: bool = False


class AccountHealthRepository:
    """Persist health attempts, state CAS updates, and safe history projections."""

    async def get_verification_snapshot_for_update(
        self,
        connection: AsyncConnection[DictRow],
        account_id: UUID,
        *,
        now: datetime,
    ) -> AccountVerificationSnapshot | None:
        """Lock the account and read verification dependencies without Lease races."""

        cursor = await connection.execute(
            """
            select
              a.tenant_id, a.project_id, a.environment_id, a.id as account_id,
              a.pool_id, a.connector_installation_id, a.revision as account_revision,
              a.source, a.external_subject_id, a.identity_fingerprint,
              a.lifecycle_status, a.health_status, a.operational_status,
              a.sync_status, a.cooldown_until, a.consecutive_health_failures,
              p.health_failure_threshold, p.health_retry_cooldown_seconds,
              r.role_key,
              e.kind as environment_kind, e.status as environment_status,
              e.allowed_origins as environment_origins,
              ci.adapter_key as connector_adapter_key,
              ci.status as connector_status,
              ci.revision as connector_revision,
              ci.allowed_origins as connector_origins,
              coalesce(
                array(
                  select cc.name
                  from atlas.connector_capability cc
                  where cc.connector_installation_id = ci.id
                  order by cc.name
                ),
                array[]::text[]
              ) as connector_capabilities,
              credential.id as credential_binding_id,
              credential.revision as credential_revision,
              credential.secret_ref,
              credential.secret_version,
              exists (
                select 1 from atlas.account_lease lease
                where lease.account_id = a.id and lease.status = 'ACTIVE'
              ) as active_lease
            from atlas.test_account a
            join atlas.account_pool p on p.id = a.pool_id
            join atlas.test_role r on r.id = p.role_id
            join atlas.environment e on e.id = a.environment_id
            left join atlas.connector_installation ci
              on ci.id = a.connector_installation_id
            left join lateral (
              select c.id, c.revision, c.secret_ref, c.secret_version
              from atlas.credential_binding c
              where c.account_id = a.id
                and c.auth_method = 'PASSWORD'
                and c.purpose = 'LOGIN'
                and c.status = 'ACTIVE'
                and (c.expires_at is null or c.expires_at > %s)
              order by c.created_at desc, c.id desc
              limit 1
            ) credential on true
            where a.id = %s
            for update of a
            """,
            (now, account_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        connector_status = (
            ConnectorStatus(row["connector_status"])
            if row["connector_status"] is not None
            else None
        )
        return AccountVerificationSnapshot(
            tenant_id=row["tenant_id"],
            project_id=row["project_id"],
            environment_id=row["environment_id"],
            account_id=row["account_id"],
            pool_id=row["pool_id"],
            connector_installation_id=row["connector_installation_id"],
            account_revision=row["account_revision"],
            source=AccountSource(row["source"]),
            external_subject_id=row["external_subject_id"],
            identity_fingerprint=row["identity_fingerprint"],
            state=self._state_from_row(row),
            role_key=row["role_key"],
            health_failure_threshold=row["health_failure_threshold"],
            health_retry_cooldown_seconds=row["health_retry_cooldown_seconds"],
            environment_kind=EnvironmentKind(row["environment_kind"]),
            environment_status=EnvironmentStatus(row["environment_status"]),
            environment_origins=tuple(row["environment_origins"]),
            connector_adapter_key=row["connector_adapter_key"],
            connector_status=connector_status,
            connector_revision=row["connector_revision"],
            connector_origins=tuple(row["connector_origins"] or ()),
            connector_capabilities=frozenset(
                ProviderCapability(value) for value in row["connector_capabilities"]
            ),
            credential_binding_id=row["credential_binding_id"],
            credential_revision=row["credential_revision"],
            secret_ref=row["secret_ref"],
            secret_version=row["secret_version"],
            active_lease=bool(row["active_lease"]),
        )

    async def expire_running_checks(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        now: datetime,
    ) -> tuple[AccountHealthCheck, ...]:
        """Expire timed-out RUNNING attempts so a new check can take over safely."""

        cursor = await connection.execute(
            f"""
            update atlas.account_health_check
            set status = 'STALE', failure_code = 'STALE_SNAPSHOT',
                retryable = true,
                safe_summary = '健康检查执行窗口已过期，结果未应用。',
                finished_at = %s, revision = revision + 1
            where account_id = %s and status = 'RUNNING' and expires_at <= %s
            returning {HEALTH_CHECK_COLUMNS}
            """,
            (now, account_id, now),
        )
        return tuple(AccountHealthCheck.model_validate(row) for row in await cursor.fetchall())

    async def has_running_check(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
    ) -> bool:
        cursor = await connection.execute(
            """
            select exists (
              select 1 from atlas.account_health_check
              where account_id = %s and status = 'RUNNING'
            ) as present
            """,
            (account_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("running health check existence query returned no row")
        return bool(row["present"])

    async def mark_account_verifying(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        expected_revision: int,
    ) -> AccountStateRecord | None:
        """Enter VERIFYING and advance revision to block Lease acquisition."""

        cursor = await connection.execute(
            """
            update atlas.test_account
            set operational_status = 'VERIFYING', cooldown_until = null,
                revision = revision + 1
            where id = %s and revision = %s
            returning lifecycle_status, health_status, operational_status,
                      sync_status, cooldown_until, consecutive_health_failures,
                      revision
            """,
            (account_id, expected_revision),
        )
        row = await cursor.fetchone()
        return self._state_from_row(row) if row is not None else None

    async def create_check(
        self,
        connection: AsyncConnection[DictRow],
        *,
        check_id: UUID,
        snapshot: AccountVerificationSnapshot,
        account_revision: int,
        origin: str,
        trigger: AccountHealthCheckTrigger,
        actor_id: UUID | None,
        request_id: str,
        started_at: datetime,
        expires_at: datetime,
    ) -> AccountHealthCheck:
        """Create a RUNNING fact without secret locators or raw Provider results."""

        assert snapshot.connector_installation_id is not None
        assert snapshot.connector_revision is not None
        assert snapshot.credential_binding_id is not None
        assert snapshot.credential_revision is not None
        cursor = await connection.execute(
            f"""
            insert into atlas.account_health_check (
              id, tenant_id, project_id, environment_id, account_id,
              connector_installation_id, credential_binding_id, trigger, status,
              origin, role_key, account_revision, connector_revision,
              credential_revision, safe_summary, actor_id, request_id,
              started_at, expires_at, created_at, updated_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, 'RUNNING', %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s
            )
            returning {HEALTH_CHECK_COLUMNS}
            """,
            (
                check_id,
                snapshot.tenant_id,
                snapshot.project_id,
                snapshot.environment_id,
                snapshot.account_id,
                snapshot.connector_installation_id,
                snapshot.credential_binding_id,
                trigger,
                origin,
                snapshot.role_key,
                account_revision,
                snapshot.connector_revision,
                snapshot.credential_revision,
                "账号登录与角色健康检查正在执行。",
                actor_id,
                request_id,
                started_at,
                expires_at,
                started_at,
                started_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("account health check insert returned no row")
        return AccountHealthCheck.model_validate(row)

    async def get_check_for_update(
        self,
        connection: AsyncConnection[DictRow],
        check_id: UUID,
    ) -> AccountHealthCheck | None:
        cursor = await connection.execute(
            f"""
            select {HEALTH_CHECK_COLUMNS}
            from atlas.account_health_check
            where id = %s
            for update
            """,
            (check_id,),
        )
        row = await cursor.fetchone()
        return AccountHealthCheck.model_validate(row) if row is not None else None

    async def get_check(
        self,
        connection: AsyncConnection[DictRow],
        check_id: UUID,
    ) -> AccountHealthCheck | None:
        cursor = await connection.execute(
            f"select {HEALTH_CHECK_COLUMNS} from atlas.account_health_check where id = %s",
            (check_id,),
        )
        row = await cursor.fetchone()
        return AccountHealthCheck.model_validate(row) if row is not None else None

    async def finish_check(
        self,
        connection: AsyncConnection[DictRow],
        *,
        check_id: UUID,
        status: AccountHealthCheckStatus,
        result_health_status: AccountHealth | None,
        failure_code: AccountHealthFailureCode | None,
        retryable: bool,
        safe_summary: str,
        finished_at: datetime,
    ) -> AccountHealthCheck | None:
        """Atomically advance a RUNNING attempt to an immutable terminal state."""

        cursor = await connection.execute(
            f"""
            update atlas.account_health_check
            set status = %s, result_health_status = %s, failure_code = %s,
                retryable = %s, safe_summary = %s, finished_at = %s,
                revision = revision + 1
            where id = %s and status = 'RUNNING'
            returning {HEALTH_CHECK_COLUMNS}
            """,
            (
                status,
                result_health_status,
                failure_code,
                retryable,
                safe_summary,
                finished_at,
                check_id,
            ),
        )
        row = await cursor.fetchone()
        return AccountHealthCheck.model_validate(row) if row is not None else None

    async def finalize_success(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        expected_revision: int,
        identity_fingerprint: str,
        now: datetime,
    ) -> AccountStateRecord | None:
        """Restore HEALTHY / READY and pin the first observed identity fingerprint."""

        cursor = await connection.execute(
            """
            update atlas.test_account
            set health_status = 'HEALTHY', operational_status = 'READY',
                cooldown_until = null, consecutive_health_failures = 0,
                last_health_checked_at = greatest(last_health_checked_at, %s),
                last_health_succeeded_at = greatest(last_health_succeeded_at, %s),
                identity_fingerprint = coalesce(identity_fingerprint, %s),
                revision = revision + 1
            where id = %s and revision = %s
            returning lifecycle_status, health_status, operational_status,
                      sync_status, cooldown_until, consecutive_health_failures,
                      revision
            """,
            (now, now, identity_fingerprint, account_id, expected_revision),
        )
        row = await cursor.fetchone()
        return self._state_from_row(row) if row is not None else None

    async def finalize_failure(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        expected_revision: int,
        health_status: AccountHealth,
        operational_status: AccountOperationalStatus,
        cooldown_until: datetime | None,
        consecutive_health_failures: int,
        now: datetime,
    ) -> AccountStateRecord | None:
        """Persist classified failure state without retaining raw Provider responses."""

        cursor = await connection.execute(
            """
            update atlas.test_account
            set health_status = %s, operational_status = %s, cooldown_until = %s,
                consecutive_health_failures = %s,
                last_health_checked_at = greatest(last_health_checked_at, %s),
                revision = revision + 1
            where id = %s and revision = %s
            returning lifecycle_status, health_status, operational_status,
                      sync_status, cooldown_until, consecutive_health_failures,
                      revision
            """,
            (
                health_status,
                operational_status,
                cooldown_until,
                consecutive_health_failures,
                now,
                account_id,
                expected_revision,
            ),
        )
        row = await cursor.fetchone()
        return self._state_from_row(row) if row is not None else None

    async def invalidate_connector_accounts(
        self,
        connection: AsyncConnection[DictRow],
        *,
        connector_id: UUID,
    ) -> tuple[AccountStateChange, ...]:
        """Require re-verification after a Connector loses a trusted snapshot."""

        cursor = await connection.execute(
            """
            select tenant_id, project_id, environment_id, id as account_id,
                   lifecycle_status, health_status, operational_status,
                   sync_status, cooldown_until, consecutive_health_failures,
                   revision
            from atlas.test_account
            where connector_installation_id = %s
              and (
                health_status not in ('UNKNOWN', 'QUARANTINED')
                or operational_status <> 'VERIFYING'
                or cooldown_until is not null
                or (
                  health_status <> 'QUARANTINED'
                  and consecutive_health_failures <> 0
                )
              )
            order by id
            for update
            """,
            (connector_id,),
        )
        changes: list[AccountStateChange] = []
        for row in await cursor.fetchall():
            before = self._state_from_row(row)
            updated = await connection.execute(
                """
                update atlas.test_account
                set health_status = case
                      when health_status = 'QUARANTINED' then health_status
                      else 'UNKNOWN'
                    end,
                    operational_status = 'VERIFYING', cooldown_until = null,
                    consecutive_health_failures = case
                      when health_status = 'QUARANTINED'
                        then consecutive_health_failures
                      else 0
                    end,
                    revision = revision + 1
                where id = %s and revision = %s
                returning lifecycle_status, health_status, operational_status,
                          sync_status, cooldown_until, consecutive_health_failures,
                          revision
                """,
                (row["account_id"], before.revision),
            )
            after_row = await updated.fetchone()
            if after_row is None:
                raise RuntimeError("connector account invalidation lost its revision")
            changes.append(
                AccountStateChange(
                    tenant_id=row["tenant_id"],
                    project_id=row["project_id"],
                    environment_id=row["environment_id"],
                    account_id=row["account_id"],
                    before=before,
                    after=self._state_from_row(after_row),
                )
            )
        return tuple(changes)

    async def append_transition(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        project_id: UUID,
        environment_id: UUID,
        account_id: UUID,
        health_check_id: UUID | None,
        reason: AccountStateTransitionReason,
        before: AccountStateRecord,
        after: AccountStateRecord,
        safe_summary: str,
        actor_id: UUID | None,
        request_id: str,
        occurred_at: datetime,
    ) -> AccountStateTransition | None:
        """Append an immutable fact only when orthogonal state actually changes."""

        if self._same_state(before, after):
            return None
        cursor = await connection.execute(
            f"""
            insert into atlas.account_state_transition (
              id, tenant_id, project_id, environment_id, account_id,
              health_check_id, reason, from_lifecycle_status,
              to_lifecycle_status, from_health_status, to_health_status,
              from_operational_status, to_operational_status, from_sync_status,
              to_sync_status, from_cooldown_until, to_cooldown_until,
              safe_summary, actor_id, request_id, occurred_at
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s
            )
            returning {TRANSITION_COLUMNS}
            """,
            (
                new_entity_id(),
                tenant_id,
                project_id,
                environment_id,
                account_id,
                health_check_id,
                reason,
                before.lifecycle_status,
                after.lifecycle_status,
                before.health_status,
                after.health_status,
                before.operational_status,
                after.operational_status,
                before.sync_status,
                after.sync_status,
                before.cooldown_until,
                after.cooldown_until,
                safe_summary,
                actor_id,
                request_id,
                occurred_at,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("account state transition insert returned no row")
        return AccountStateTransition.model_validate(row)

    async def list_checks(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[AccountHealthCheck, ...]:
        if cursor is None:
            result = await connection.execute(
                f"""
                select {HEALTH_CHECK_COLUMNS}
                from atlas.account_health_check
                where account_id = %s
                order by created_at desc, id desc
                limit %s
                """,
                (account_id, limit + 1),
            )
        else:
            result = await connection.execute(
                f"""
                select {HEALTH_CHECK_COLUMNS}
                from atlas.account_health_check
                where account_id = %s and (created_at, id) < (%s, %s)
                order by created_at desc, id desc
                limit %s
                """,
                (account_id, cursor.created_at, cursor.id, limit + 1),
            )
        return tuple(AccountHealthCheck.model_validate(row) for row in await result.fetchall())

    async def list_transitions(
        self,
        connection: AsyncConnection[DictRow],
        *,
        account_id: UUID,
        cursor: TimeCursor | None,
        limit: int,
    ) -> tuple[AccountStateTransition, ...]:
        if cursor is None:
            result = await connection.execute(
                f"""
                select {TRANSITION_COLUMNS}
                from atlas.account_state_transition
                where account_id = %s
                order by occurred_at desc, id desc
                limit %s
                """,
                (account_id, limit + 1),
            )
        else:
            result = await connection.execute(
                f"""
                select {TRANSITION_COLUMNS}
                from atlas.account_state_transition
                where account_id = %s and (occurred_at, id) < (%s, %s)
                order by occurred_at desc, id desc
                limit %s
                """,
                (account_id, cursor.created_at, cursor.id, limit + 1),
            )
        return tuple(AccountStateTransition.model_validate(row) for row in await result.fetchall())

    @staticmethod
    def _state_from_row(row: DictRow) -> AccountStateRecord:
        return AccountStateRecord(
            lifecycle_status=AccountLifecycle(row["lifecycle_status"]),
            health_status=AccountHealth(row["health_status"]),
            operational_status=AccountOperationalStatus(row["operational_status"]),
            sync_status=AccountSyncStatus(row["sync_status"]),
            cooldown_until=row["cooldown_until"],
            consecutive_health_failures=row["consecutive_health_failures"],
            revision=row["revision"] if "revision" in row else row["account_revision"],
        )

    @staticmethod
    def _same_state(before: AccountStateRecord, after: AccountStateRecord) -> bool:
        return (
            before.lifecycle_status,
            before.health_status,
            before.operational_status,
            before.sync_status,
            before.cooldown_until,
        ) == (
            after.lifecycle_status,
            after.health_status,
            after.operational_status,
            after.sync_status,
            after.cooldown_until,
        )
