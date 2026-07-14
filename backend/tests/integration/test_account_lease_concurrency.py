"""测试账号租约的真实 PostgreSQL 并发、TTL 与回收。"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from uuid import UUID, uuid7

import pytest
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from atlas_testops.application.access import ActorContext
from atlas_testops.application.account_health import AccountHealthService
from atlas_testops.application.credentials import CredentialBrokerService
from atlas_testops.application.identity import IdentityService
from atlas_testops.application.leases import LeaseCommandResult, LeaseService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.identity import (
    AccountPoolStatus,
    AcquireAccountLease,
    CredentialAuthMethod,
    CredentialPurpose,
    HeartbeatAccountLease,
    IssueSecretGrant,
    LeaseReleaseReason,
    LeaseRequirements,
    RedeemSecretGrant,
    ReleaseAccountLease,
    UpdateAccountPool,
)
from atlas_testops.infrastructure.adapters.generic_password import GenericPasswordAdapter
from atlas_testops.infrastructure.adapters.mock_provider import MockIdentityProvider
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.secrets import InMemorySecretProvider

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(DATABASE_URL is None, reason="ATLAS_TEST_DATABASE_URL is not configured"),
]


@dataclass(frozen=True, slots=True)
class SeededLeasePool:
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    connector_id: UUID
    pool_id: UUID
    account_ids: tuple[UUID, ...]


@dataclass(slots=True)
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


def create_database(*, maximum_connections: int = 24) -> Database:
    assert DATABASE_URL is not None
    return Database(
        Settings(
            environment="test",
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=maximum_connections,
        )
    )


async def seed_lease_pool(
    database: Database,
    *,
    account_count: int,
    cooldown_seconds: int = 0,
    allowed_origins: tuple[str, ...] = ("https://staging.example.test",),
) -> SeededLeasePool:
    tenant_id = uuid7()
    project_id = uuid7()
    environment_id = uuid7()
    connector_id = uuid7()
    role_id = uuid7()
    pool_id = uuid7()
    prefix = tenant_id.hex[-10:]
    account_ids: list[UUID] = []
    async with database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
        await connection.execute(
            "insert into atlas.tenant (id, slug, name) values (%s, %s, %s)",
            (tenant_id, f"lease-concurrency-{prefix}", "Lease Concurrency"),
        )
        await connection.execute(
            """
            insert into atlas.project (id, tenant_id, project_key, name)
            values (%s, %s, %s, %s)
            """,
            (project_id, tenant_id, f"LEASE_{prefix.upper()}", "Lease Project"),
        )
        await connection.execute(
            """
            insert into atlas.environment (
              id, tenant_id, project_id, environment_key, name, kind,
              allowed_origins
            ) values (%s, %s, %s, 'pre-test', 'Pre Test', 'TEST', %s)
            """,
            (environment_id, tenant_id, project_id, list(allowed_origins)),
        )
        await connection.execute(
            """
            insert into atlas.connector_installation (
              id, tenant_id, project_id, environment_id, installation_key,
              name, adapter_key, mode, configuration_ref, allowed_origins,
              required_capabilities, status, health_state, safe_message,
              protocol_version, implementation_version, last_validated_at
            ) values (
              %s, %s, %s, %s, 'password', 'Password Connector',
              'generic-password', 'MANAGED_TEST_ACCOUNTS', %s, %s,
              array['auth.password'], 'ACTIVE', 'HEALTHY',
              'mock provider is ready', '1.0', '0.1.0', clock_timestamp()
            )
            """,
            (
                connector_id,
                tenant_id,
                project_id,
                environment_id,
                f"cfg_mock_{prefix}",
                list(allowed_origins),
            ),
        )
        await connection.execute(
            """
            insert into atlas.connector_capability (
              connector_installation_id, tenant_id, project_id,
              environment_id, name, version, mode, observed_at
            ) values (%s, %s, %s, %s, 'auth.password', '1.0',
                      'browser', clock_timestamp())
            """,
            (connector_id, tenant_id, project_id, environment_id),
        )
        await connection.execute(
            """
            insert into atlas.test_role (
              id, tenant_id, project_id, role_key, name, capabilities
            ) values (%s, %s, %s, 'sales', '销售', %s)
            """,
            (role_id, tenant_id, project_id, ["customer.read", "visit:create"]),
        )
        await connection.execute(
            """
            insert into atlas.account_pool (
              id, tenant_id, project_id, environment_id, role_id, pool_key,
              name, default_ttl_seconds, cooldown_seconds
            ) values (%s, %s, %s, %s, %s, 'sales-cn', '销售账号池', 300, %s)
            """,
            (
                pool_id,
                tenant_id,
                project_id,
                environment_id,
                role_id,
                cooldown_seconds,
            ),
        )
        for index in range(account_count):
            account_id = uuid7()
            account_ids.append(account_id)
            await connection.execute(
                """
                insert into atlas.test_account (
                  id, tenant_id, project_id, environment_id, pool_id,
                  connector_installation_id, account_key, source,
                  login_hint_masked, lifecycle_status, health_status,
                  operational_status, identity_fingerprint,
                  last_health_checked_at, last_health_succeeded_at, labels
                ) values (
                  %s, %s, %s, %s, %s, %s, %s, 'ATLAS_MANAGED',
                  %s, 'ACTIVE', 'HEALTHY', 'READY', %s,
                  statement_timestamp(), statement_timestamp(), %s
                )
                """,
                (
                    account_id,
                    tenant_id,
                    project_id,
                    environment_id,
                    pool_id,
                    connector_id,
                    f"sales-{index:03d}",
                    f"sa***{index}@example.test",
                    AccountHealthService.identity_fingerprint(
                        connector_id,
                        "mock-sales-subject",
                    ),
                    Jsonb({"region": "cn", "persona": "new_customer"}),
                ),
            )
            await connection.execute(
                """
                insert into atlas.account_slot (
                  id, tenant_id, project_id, environment_id, account_id, slot_index
                ) values (%s, %s, %s, %s, %s, 1)
                """,
                (uuid7(), tenant_id, project_id, environment_id, account_id),
            )
            await connection.execute(
                """
                insert into atlas.credential_binding (
                  id, tenant_id, project_id, environment_id, account_id,
                  auth_method, purpose, secret_ref, secret_version
                ) values (%s, %s, %s, %s, %s, 'PASSWORD', 'LOGIN', %s, 'v1')
                """,
                (
                    uuid7(),
                    tenant_id,
                    project_id,
                    environment_id,
                    account_id,
                    f"sec_concurrency_{prefix}_{index:03d}",
                ),
            )
    return SeededLeasePool(
        tenant_id=tenant_id,
        project_id=project_id,
        environment_id=environment_id,
        connector_id=connector_id,
        pool_id=pool_id,
        account_ids=tuple(account_ids),
    )


def make_actor(seed: SeededLeasePool) -> ActorContext:
    return ActorContext(
        tenant_id=seed.tenant_id,
        actor_id=uuid7(),
        request_id=f"lease-test-{uuid7()}",
        current_project_id=seed.project_id,
        development_override=True,
    )


def acquire_command(
    seed: SeededLeasePool,
    clock: MutableClock,
    index: int,
) -> AcquireAccountLease:
    return AcquireAccountLease(
        execution_id=f"execution-concurrent-{index:03d}",
        worker_id=f"worker-concurrent-{index:03d}",
        environment_id=seed.environment_id,
        role_key="sales",
        requirements=LeaseRequirements(
            tags=("region:cn",),
            auth_methods=(CredentialAuthMethod.PASSWORD,),
            capabilities=("customer.read",),
        ),
        ttl_seconds=300,
        execution_deadline=clock.value + timedelta(hours=2),
    )


@pytest.mark.anyio
async def test_one_hundred_concurrent_acquires_never_duplicate_a_slot() -> None:
    database = create_database()
    clock = MutableClock(datetime.now(UTC))
    await database.open()
    try:
        seed = await seed_lease_pool(database, account_count=8)
        actor = make_actor(seed)
        service = LeaseService(database, clock=clock)

        async def attempt(index: int) -> LeaseCommandResult | None:
            try:
                return await service.acquire(
                    actor,
                    acquire_command(seed, clock, index),
                    idempotency_key=f"concurrent-acquire-{index:03d}",
                )
            except ApplicationError as error:
                assert error.error_code is ErrorCode.POOL_EXHAUSTED
                return None

        outcomes = await asyncio.gather(*(attempt(index) for index in range(100)))
        successful = [outcome for outcome in outcomes if outcome is not None]
        assert len(successful) == 8
        assert len({outcome.value.lease_id for outcome in successful}) == 8
        assert len({outcome.value.account_handle for outcome in successful}) == 8

        async with database.transaction(actor.database_context()) as connection:
            active_rows = await (
                await connection.execute(
                    """
                    select slot_id, count(*) as lease_count
                    from atlas.account_lease
                    where status = 'ACTIVE'
                    group by slot_id
                    order by slot_id
                    """
                )
            ).fetchall()
            assert len(active_rows) == 8
            assert all(row["lease_count"] == 1 for row in active_rows)

        await asyncio.gather(
            *(
                service.release(
                    actor,
                    outcome.value.lease_id,
                    ReleaseAccountLease(
                        fencing_token=outcome.value.fencing_token,
                        reason=LeaseReleaseReason.COMPLETED,
                    ),
                )
                for outcome in successful
            )
        )
        replacement = await service.acquire(
            actor,
            acquire_command(seed, clock, 101),
            idempotency_key="concurrent-acquire-replacement",
        )
        assert replacement.value.fencing_token == 2

        with pytest.raises(ApplicationError) as stale_heartbeat:
            await service.heartbeat(
                actor,
                successful[0].value.lease_id,
                HeartbeatAccountLease(fencing_token=successful[0].value.fencing_token),
            )
        assert stale_heartbeat.value.error_code is ErrorCode.LEASE_FENCED
    finally:
        await database.close()


@pytest.mark.anyio
async def test_pool_disable_racing_acquire_never_leaves_an_active_lease() -> None:
    database = create_database()
    clock = MutableClock(datetime.now(UTC))
    await database.open()
    try:
        lease_service = LeaseService(database, clock=clock)
        identity_service = IdentityService(database)

        async def acquire_during_disable(
            gate: asyncio.Event,
            actor: ActorContext,
            seed: SeededLeasePool,
            index: int,
        ) -> LeaseCommandResult | None:
            await gate.wait()
            try:
                return await lease_service.acquire(
                    actor,
                    acquire_command(seed, clock, index),
                    idempotency_key=f"disable-race-acquire-{index:03d}",
                )
            except ApplicationError as error:
                assert error.error_code is ErrorCode.POOL_EXHAUSTED
                return None

        async def disable_pool(
            gate: asyncio.Event,
            actor: ActorContext,
            seed: SeededLeasePool,
            expected_revision: int,
        ) -> None:
            await gate.wait()
            updated = await identity_service.update_pool(
                actor,
                seed.pool_id,
                UpdateAccountPool(status=AccountPoolStatus.DISABLED),
                expected_revision=expected_revision,
            )
            assert updated.status is AccountPoolStatus.DISABLED

        for index in range(20):
            seed = await seed_lease_pool(database, account_count=1)
            actor = make_actor(seed)
            pool = await identity_service.get_pool(actor, seed.pool_id)
            gate = asyncio.Event()

            acquire_task = asyncio.create_task(acquire_during_disable(gate, actor, seed, index))
            disable_task = asyncio.create_task(disable_pool(gate, actor, seed, pool.revision))
            gate.set()
            acquired, _ = await asyncio.gather(acquire_task, disable_task)

            async with database.transaction(actor.database_context()) as connection:
                active_count = await (
                    await connection.execute(
                        """
                        select count(*)
                        from atlas.account_lease
                        where pool_id = %s and status = 'ACTIVE'
                        """,
                        (seed.pool_id,),
                    )
                ).fetchone()
                assert active_count == {"count": 0}
                if acquired is not None:
                    terminal = await (
                        await connection.execute(
                            """
                            select status, release_reason
                            from atlas.account_lease
                            where id = %s
                            """,
                            (acquired.value.lease_id,),
                        )
                    ).fetchone()
                    assert terminal == {
                        "status": "REVOKED",
                        "release_reason": "POOL_DISABLED",
                    }
    finally:
        await database.close()


@pytest.mark.anyio
async def test_secret_grant_is_single_use_origin_bound_revocable_and_never_persisted() -> None:
    database = create_database(maximum_connections=24)
    clock = MutableClock(datetime.now(UTC))
    await database.open()
    try:
        alternate_origin = "https://alternate.example.test"
        seed = await seed_lease_pool(
            database,
            account_count=1,
            allowed_origins=(
                "https://staging.example.test",
                alternate_origin,
            ),
        )
        actor = make_actor(seed)
        lease_service = LeaseService(database, clock=clock)
        acquired = await lease_service.acquire(
            actor,
            acquire_command(seed, clock, 201),
            idempotency_key="secret-grant-lease-acquire",
        )
        origin = "https://staging.example.test"
        secret_ref = f"sec_concurrency_{seed.tenant_id.hex[-10:]}_000"
        username = "sales-secret@example.test"
        password = "never-persist-this-password"
        secret_provider = InMemorySecretProvider()
        secret_provider.put_password(
            secret_ref=secret_ref,
            secret_version="v1",
            username=username,
            password=password,
        )
        mock_provider = MockIdentityProvider(allowed_origins=(origin,))
        mock_provider.register_account(
            account_handle=acquired.value.account_handle,
            provider_subject="mock-sales-subject",
            username=username,
            password=password,
            role_keys=("sales",),
        )
        broker = CredentialBrokerService(
            database,
            secret_provider=secret_provider,
            password_adapter=GenericPasswordAdapter(mock_provider),
            clock=clock,
        )

        async def issue_grant() -> str:
            grant = await broker.issue(
                actor,
                acquired.value.lease_id,
                IssueSecretGrant(
                    fencing_token=acquired.value.fencing_token,
                    purpose=CredentialPurpose.LOGIN,
                    worker_identity="worker-concurrent-201",
                    allowed_origins=(origin,),
                ),
            )
            assert grant.grant_ref not in repr(grant)
            return grant.grant_ref

        grant_ref = await issue_grant()
        async with database.transaction(actor.database_context()) as connection:
            row = await (
                await connection.execute(
                    """
                    select token_hash, status
                    from atlas.secret_grant
                    where lease_id = %s
                    order by issued_at desc
                    limit 1
                    """,
                    (acquired.value.lease_id,),
                )
            ).fetchone()
            assert row == {
                "token_hash": broker.hash_grant_ref(grant_ref),
                "status": "ISSUED",
            }
            assert grant_ref not in str(row)
            assert password not in str(row)

        foreign_actor = ActorContext(
            tenant_id=uuid7(),
            actor_id=uuid7(),
            request_id="secret-grant-cross-tenant",
            development_override=True,
        )
        with pytest.raises(ApplicationError) as hidden:
            await broker.redeem_password(
                foreign_actor,
                grant_ref,
                RedeemSecretGrant(
                    worker_identity="worker-concurrent-201",
                    origin=origin,
                ),
            )
        assert hidden.value.error_code is ErrorCode.NOT_FOUND

        with pytest.raises(ApplicationError) as wrong_origin:
            await broker.redeem_password(
                actor,
                grant_ref,
                RedeemSecretGrant(
                    worker_identity="worker-concurrent-201",
                    origin=alternate_origin,
                ),
            )
        assert wrong_origin.value.error_code is ErrorCode.ORIGIN_NOT_ALLOWED

        with pytest.raises(ApplicationError) as wrong_worker:
            await broker.redeem_password(
                actor,
                grant_ref,
                RedeemSecretGrant(
                    worker_identity="worker-concurrent-999",
                    origin=origin,
                ),
            )
        assert wrong_worker.value.error_code is ErrorCode.SECRET_GRANT_REVOKED

        receipt = await broker.redeem_password(
            actor,
            grant_ref,
            RedeemSecretGrant(
                worker_identity="worker-concurrent-201",
                origin=origin,
            ),
        )
        assert receipt.status == "REDEEMED"
        assert receipt.capability == "auth.password"
        assert mock_provider.authentication_attempts == 1

        with pytest.raises(ApplicationError) as replayed:
            await broker.redeem_password(
                actor,
                grant_ref,
                RedeemSecretGrant(
                    worker_identity="worker-concurrent-201",
                    origin=origin,
                ),
            )
        assert replayed.value.error_code is ErrorCode.SECRET_GRANT_REPLAYED
        assert mock_provider.authentication_attempts == 1

        concurrent_ref = await issue_grant()

        async def redeem_once() -> str:
            try:
                await broker.redeem_password(
                    actor,
                    concurrent_ref,
                    RedeemSecretGrant(
                        worker_identity="worker-concurrent-201",
                        origin=origin,
                    ),
                )
                return "REDEEMED"
            except ApplicationError as error:
                assert error.error_code is ErrorCode.SECRET_GRANT_REPLAYED
                return "REPLAYED"

        outcomes = await asyncio.gather(*(redeem_once() for _ in range(20)))
        assert outcomes.count("REDEEMED") == 1
        assert outcomes.count("REPLAYED") == 19
        assert mock_provider.authentication_attempts == 2

        expiring_ref = await issue_grant()
        clock.value += timedelta(seconds=61)
        reaped = await lease_service.reap_expired(actor, limit=100)
        assert reaped.reaped == 0
        with pytest.raises(ApplicationError) as expired:
            await broker.redeem_password(
                actor,
                expiring_ref,
                RedeemSecretGrant(
                    worker_identity="worker-concurrent-201",
                    origin=origin,
                ),
            )
        assert expired.value.error_code is ErrorCode.SECRET_GRANT_EXPIRED

        revoked_ref = await issue_grant()
        await lease_service.release(
            actor,
            acquired.value.lease_id,
            ReleaseAccountLease(
                fencing_token=acquired.value.fencing_token,
                reason=LeaseReleaseReason.COMPLETED,
            ),
        )
        with pytest.raises(ApplicationError) as revoked:
            await broker.redeem_password(
                actor,
                revoked_ref,
                RedeemSecretGrant(
                    worker_identity="worker-concurrent-201",
                    origin=origin,
                ),
            )
        assert revoked.value.error_code is ErrorCode.SECRET_GRANT_REVOKED

        async with database.transaction(actor.database_context()) as connection:
            terminal_rows = await (
                await connection.execute(
                    """
                    select status, termination_reason
                    from atlas.secret_grant
                    where lease_id = %s
                    order by issued_at
                    """,
                    (acquired.value.lease_id,),
                )
            ).fetchall()
            assert [row["status"] for row in terminal_rows] == [
                "REDEEMED",
                "REDEEMED",
                "EXPIRED",
                "REVOKED",
            ]
            assert terminal_rows[-1]["termination_reason"] == "LEASE_TERMINATED"
            payloads = [
                str(row["payload"])
                for row in await (
                    await connection.execute(
                        """
                        select payload from atlas.audit_event
                        where entity_type = 'secret_grant'
                        union all
                        select payload from atlas.outbox_event
                        where aggregate_type = 'secret_grant'
                        """
                    )
                ).fetchall()
            ]
            assert payloads
            for payload in payloads:
                assert grant_ref not in payload
                assert concurrent_ref not in payload
                assert expiring_ref not in payload
                assert revoked_ref not in payload
                assert secret_ref not in payload
                assert username not in payload
                assert password not in payload
    finally:
        await database.close()


@pytest.mark.anyio
async def test_runtime_role_drift_revokes_lease_and_quarantines_account() -> None:
    """运行期角色漂移必须立即隔离账号并废弃当前 Lease。"""

    database = create_database(maximum_connections=4)
    clock = MutableClock(datetime.now(UTC))
    await database.open()
    try:
        seed = await seed_lease_pool(database, account_count=1)
        actor = make_actor(seed)
        acquired = await LeaseService(database, clock=clock).acquire(
            actor,
            acquire_command(seed, clock, 251),
            idempotency_key="runtime-role-drift-acquire",
        )
        origin = "https://staging.example.test"
        secret_ref = f"sec_concurrency_{seed.tenant_id.hex[-10:]}_000"
        secret_provider = InMemorySecretProvider()
        secret_provider.put_password(
            secret_ref=secret_ref,
            secret_version="v1",
            username="runtime-role@example.test",
            password="runtime-role-password",
        )
        provider = MockIdentityProvider(allowed_origins=(origin,))
        provider.register_account(
            account_handle=acquired.value.account_handle,
            provider_subject="mock-sales-subject",
            username="runtime-role@example.test",
            password="runtime-role-password",
            role_keys=("observer",),
        )
        broker = CredentialBrokerService(
            database,
            secret_provider=secret_provider,
            password_adapter=GenericPasswordAdapter(provider),
            clock=clock,
        )
        grant = await broker.issue(
            actor,
            acquired.value.lease_id,
            IssueSecretGrant(
                fencing_token=acquired.value.fencing_token,
                purpose=CredentialPurpose.LOGIN,
                worker_identity="worker-concurrent-251",
                allowed_origins=(origin,),
            ),
        )

        with pytest.raises(ApplicationError) as rejected:
            await broker.redeem_password(
                actor,
                grant.grant_ref,
                RedeemSecretGrant(
                    worker_identity="worker-concurrent-251",
                    origin=origin,
                ),
            )
        assert rejected.value.error_code is ErrorCode.AUTHENTICATION_FAILED

        async with database.transaction(actor.database_context()) as connection:
            lease = await (
                await connection.execute(
                    """
                    select status, release_reason from atlas.account_lease
                    where id = %s
                    """,
                    (acquired.value.lease_id,),
                )
            ).fetchone()
            account = await (
                await connection.execute(
                    """
                    select health_status, operational_status,
                           consecutive_health_failures
                    from atlas.test_account where id = %s
                    """,
                    (seed.account_ids[0],),
                )
            ).fetchone()
            check = await (
                await connection.execute(
                    """
                    select trigger, status, failure_code, safe_summary
                    from atlas.account_health_check where account_id = %s
                    order by created_at desc limit 1
                    """,
                    (seed.account_ids[0],),
                )
            ).fetchone()
            transition = await (
                await connection.execute(
                    """
                    select reason from atlas.account_state_transition
                    where account_id = %s order by occurred_at desc limit 1
                    """,
                    (seed.account_ids[0],),
                )
            ).fetchone()
        assert lease == {"status": "REVOKED", "release_reason": "AUTH_FAILED"}
        assert account == {
            "health_status": "QUARANTINED",
            "operational_status": "VERIFYING",
            "consecutive_health_failures": 1,
        }
        assert check is not None
        assert check["trigger"] == "AUTH_FAILURE"
        assert check["status"] == "FAILED"
        assert check["failure_code"] == "ROLE_DRIFT"
        assert "observer" not in check["safe_summary"]
        assert transition == {"reason": "ROLE_DRIFT"}
    finally:
        await database.close()


@pytest.mark.anyio
async def test_credential_revocation_terminates_every_issued_secret_grant() -> None:
    database = create_database(maximum_connections=4)
    clock = MutableClock(datetime.now(UTC))
    await database.open()
    try:
        seed = await seed_lease_pool(database, account_count=1)
        actor = make_actor(seed)
        acquired = await LeaseService(database, clock=clock).acquire(
            actor,
            acquire_command(seed, clock, 301),
            idempotency_key="credential-revocation-acquire",
        )
        broker = CredentialBrokerService(database, clock=clock)
        grant = await broker.issue(
            actor,
            acquired.value.lease_id,
            IssueSecretGrant(
                fencing_token=acquired.value.fencing_token,
                purpose=CredentialPurpose.LOGIN,
                worker_identity="worker-concurrent-301",
                allowed_origins=("https://staging.example.test",),
            ),
        )

        async with database.transaction(actor.database_context()) as connection:
            await connection.execute(
                """
                update atlas.credential_binding
                set status = 'REVOKED', revision = revision + 1
                where account_id = %s and purpose = 'LOGIN'
                """,
                (seed.account_ids[0],),
            )
            grant_row = await (
                await connection.execute(
                    """
                    select status, termination_reason
                    from atlas.secret_grant
                    where token_hash = %s
                    """,
                    (broker.hash_grant_ref(grant.grant_ref),),
                )
            ).fetchone()

        assert grant_row == {
            "status": "REVOKED",
            "termination_reason": "CREDENTIAL_UNAVAILABLE",
        }
    finally:
        await database.close()


@pytest.mark.anyio
async def test_heartbeat_expiry_commit_and_reaper_use_server_clock() -> None:
    database = create_database(maximum_connections=4)
    clock = MutableClock(datetime.now(UTC))
    await database.open()
    try:
        seed = await seed_lease_pool(database, account_count=1)
        actor = make_actor(seed)
        service = LeaseService(database, clock=clock)
        acquired = await service.acquire(
            actor,
            acquire_command(seed, clock, 1),
            idempotency_key="ttl-acquire-first",
        )
        initial_expiry = acquired.value.expires_at

        clock.value += timedelta(seconds=100)
        heartbeat = await service.heartbeat(
            actor,
            acquired.value.lease_id,
            HeartbeatAccountLease(fencing_token=acquired.value.fencing_token),
        )
        assert heartbeat.expires_at > initial_expiry

        clock.value = heartbeat.expires_at + timedelta(microseconds=1)
        with pytest.raises(ApplicationError) as expired_error:
            await service.heartbeat(
                actor,
                acquired.value.lease_id,
                HeartbeatAccountLease(fencing_token=acquired.value.fencing_token),
            )
        assert expired_error.value.error_code is ErrorCode.LEASE_EXPIRED

        async with database.transaction(actor.database_context()) as connection:
            lease_row = await (
                await connection.execute(
                    "select status, release_reason from atlas.account_lease where id = %s",
                    (acquired.value.lease_id,),
                )
            ).fetchone()
            account_row = await (
                await connection.execute(
                    """
                    select health_status, operational_status
                    from atlas.test_account where id = %s
                    """,
                    (seed.account_ids[0],),
                )
            ).fetchone()
            assert lease_row == {"status": "EXPIRED", "release_reason": "TTL_EXPIRED"}
            assert account_row == {
                "health_status": "DEGRADED",
                "operational_status": "VERIFYING",
            }
            await connection.execute(
                """
                update atlas.test_account
                set health_status = 'HEALTHY', operational_status = 'READY',
                    revision = revision + 1
                where id = %s
                """,
                (seed.account_ids[0],),
            )

        second = await service.acquire(
            actor,
            acquire_command(seed, clock, 2),
            idempotency_key="ttl-acquire-second",
        )
        assert second.value.fencing_token == acquired.value.fencing_token + 1
        clock.value = second.value.expires_at + timedelta(microseconds=1)

        reaped = await service.reap_expired(actor, limit=100)
        assert reaped.reaped == 1
        assert (await service.reap_expired(actor, limit=100)).reaped == 0
        with pytest.raises(ApplicationError) as fenced_after_reap:
            await service.heartbeat(
                actor,
                second.value.lease_id,
                HeartbeatAccountLease(fencing_token=second.value.fencing_token),
            )
        assert fenced_after_reap.value.error_code is ErrorCode.LEASE_FENCED
    finally:
        await database.close()
