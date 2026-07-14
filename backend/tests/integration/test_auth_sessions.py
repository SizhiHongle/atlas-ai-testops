"""PostgreSQL integration tests for encrypted browser session lifecycle."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from urllib.parse import urlsplit
from uuid import UUID, uuid7

import pytest
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from atlas_testops.application.access import ActorContext
from atlas_testops.application.account_health import AccountHealthService
from atlas_testops.application.leases import LeaseService
from atlas_testops.application.ports.providers import AdapterContext
from atlas_testops.application.ports.secrets import PasswordSecret
from atlas_testops.application.ports.sessions import AuthenticatedBrowserSession
from atlas_testops.application.session_janitor import SessionJanitorService
from atlas_testops.application.sessions import AuthSessionService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.identity import (
    AccountLeaseHandle,
    AcquireAccountLease,
    CredentialAuthMethod,
    EnsureLoginSession,
    LeaseReleaseReason,
    LeaseRequirements,
    LoginSessionManualAction,
    LoginSessionReady,
    PasswordAuthenticationResult,
    ProviderHealth,
    ProviderHealthState,
    ReleaseAccountLease,
)
from atlas_testops.infrastructure.adapters.generic_password import GenericPasswordAdapter
from atlas_testops.infrastructure.adapters.mock_provider import MockIdentityProvider
from atlas_testops.infrastructure.adapters.registry import AdapterRegistry
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.secrets import InMemorySecretProvider
from atlas_testops.infrastructure.session_vault import (
    AesGcmSessionArtifactVault,
    InMemorySessionObjectStore,
)

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")
ORIGIN = "https://staging.example.test"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


@dataclass(frozen=True, slots=True)
class SessionSeed:
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    connector_id: UUID
    account_id: UUID
    secret_ref: str


class RevisionRacingSessionProvider:
    """Mutate an account during external login to exercise final revision CAS."""

    def __init__(
        self,
        database: Database,
        *,
        tenant_id: UUID,
        account_id: UUID,
    ) -> None:
        self._database = database
        self._tenant_id = tenant_id
        self._account_id = account_id
        self.attempts = 0

    async def probe(self, context: AdapterContext) -> ProviderHealth:
        assert context.origin == ORIGIN
        return ProviderHealth(
            state=ProviderHealthState.HEALTHY,
            safe_message="revision racing provider is ready",
        )

    async def authenticate(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> PasswordAuthenticationResult:
        del context, account_handle, secret
        raise AssertionError("session creation must call establish_session")

    async def establish_session(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> AuthenticatedBrowserSession:
        assert context.origin == ORIGIN
        assert account_handle.startswith("ah_")
        assert secret.reveal_username() == "race@example.test"
        assert secret.reveal_password() == "race-password"
        self.attempts += 1
        async with self._database.transaction(
            DatabaseContext(tenant_id=self._tenant_id)
        ) as connection:
            await connection.execute(
                """
                update atlas.test_account
                set labels = labels || '{"revision_race":"won"}'::jsonb,
                    revision = revision + 1
                where id = %s
                """,
                (self._account_id,),
            )
        return AuthenticatedBrowserSession(
            provider_subject="provider-session-subject",
            role_keys=("sales",),
            auth_strength=(CredentialAuthMethod.PASSWORD,),
            storage_state=b'{"cookies":[],"origins":[]}',
        )


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


async def seed_session_account(database: Database) -> SessionSeed:
    tenant_id = uuid7()
    project_id = uuid7()
    environment_id = uuid7()
    connector_id = uuid7()
    role_id = uuid7()
    pool_id = uuid7()
    account_id = uuid7()
    secret_ref = f"sec_session_{tenant_id.hex[-16:]}"
    prefix = tenant_id.hex[-10:]
    fingerprint = AccountHealthService.identity_fingerprint(
        connector_id,
        "provider-session-subject",
    )
    async with database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
        await connection.execute(
            "insert into atlas.tenant (id, slug, name) values (%s, %s, %s)",
            (tenant_id, f"session-{prefix}", "Session Integration"),
        )
        await connection.execute(
            """
            insert into atlas.project (id, tenant_id, project_key, name)
            values (%s, %s, %s, %s)
            """,
            (project_id, tenant_id, f"SESSION_{prefix.upper()}", "Session Project"),
        )
        await connection.execute(
            """
            insert into atlas.environment (
              id, tenant_id, project_id, environment_key, name, kind,
              allowed_origins
            ) values (%s, %s, %s, 'pre-test', 'Pre Test', 'TEST', %s)
            """,
            (environment_id, tenant_id, project_id, [ORIGIN]),
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
                f"cfg_session_{prefix}",
                [ORIGIN],
            ),
        )
        await connection.execute(
            """
            insert into atlas.connector_capability (
              connector_installation_id, tenant_id, project_id,
              environment_id, name, version, mode, observed_at
            ) values
              (%s, %s, %s, %s, 'auth.password', '1.0', 'browser', clock_timestamp()),
              (%s, %s, %s, %s, 'auth.manual_bootstrap', '1.0', 'manual',
               clock_timestamp())
            """,
            (
                connector_id,
                tenant_id,
                project_id,
                environment_id,
                connector_id,
                tenant_id,
                project_id,
                environment_id,
            ),
        )
        await connection.execute(
            """
            insert into atlas.test_role (
              id, tenant_id, project_id, role_key, name, capabilities
            ) values (%s, %s, %s, 'sales', 'Sales', %s)
            """,
            (role_id, tenant_id, project_id, ["customer.read"]),
        )
        await connection.execute(
            """
            insert into atlas.account_pool (
              id, tenant_id, project_id, environment_id, role_id, pool_key,
              name, default_ttl_seconds, cooldown_seconds
            ) values (%s, %s, %s, %s, %s, 'sales-cn', 'Sales Pool', 600, 0)
            """,
            (pool_id, tenant_id, project_id, environment_id, role_id),
        )
        await connection.execute(
            """
            insert into atlas.test_account (
              id, tenant_id, project_id, environment_id, pool_id,
              connector_installation_id, account_key, source,
              login_hint_masked, lifecycle_status, health_status,
              operational_status, identity_fingerprint,
              last_health_checked_at, last_health_succeeded_at, labels
            ) values (
              %s, %s, %s, %s, %s, %s, 'sales-001', 'ATLAS_MANAGED',
              'sa***@example.test', 'ACTIVE', 'HEALTHY', 'READY', %s,
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
                fingerprint,
                Jsonb({"region": "cn"}),
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
                secret_ref,
            ),
        )
    return SessionSeed(
        tenant_id=tenant_id,
        project_id=project_id,
        environment_id=environment_id,
        connector_id=connector_id,
        account_id=account_id,
        secret_ref=secret_ref,
    )


def make_actor(seed: SessionSeed) -> ActorContext:
    return ActorContext(
        tenant_id=seed.tenant_id,
        actor_id=uuid7(),
        request_id=f"session-test-{uuid7()}",
        current_project_id=seed.project_id,
        development_override=True,
    )


async def acquire_lease(
    database: Database,
    seed: SessionSeed,
    actor: ActorContext,
) -> tuple[LeaseService, AccountLeaseHandle]:
    service = LeaseService(database)
    result = await service.acquire(
        actor,
        AcquireAccountLease(
            execution_id=f"execution-{uuid7()}",
            worker_id="worker-auth-session-01",
            environment_id=seed.environment_id,
            role_key="sales",
            requirements=LeaseRequirements(
                tags=("region:cn",),
                auth_methods=(CredentialAuthMethod.PASSWORD,),
                capabilities=("customer.read",),
            ),
            ttl_seconds=600,
            execution_deadline=datetime.now(UTC) + timedelta(minutes=20),
        ),
        idempotency_key=f"session-lease-{uuid7()}",
    )
    return service, result.value


@pytest.mark.anyio
async def test_session_creation_is_single_flight_encrypted_and_destroyed() -> None:
    database = create_database()
    await database.open()
    try:
        seed = await seed_session_account(database)
        actor = make_actor(seed)
        lease_service, handle = await acquire_lease(database, seed, actor)
        secrets = InMemorySecretProvider()
        secrets.put_password(
            secret_ref=seed.secret_ref,
            secret_version="v1",
            username="session@example.test",
            password="session-password",
        )
        provider = MockIdentityProvider(allowed_origins=(ORIGIN,))
        provider.register_account(
            account_handle=handle.account_handle,
            provider_subject="provider-session-subject",
            username="session@example.test",
            password="session-password",
            role_keys=("sales",),
        )
        adapter = GenericPasswordAdapter(provider)
        registry = AdapterRegistry({"generic-password": lambda _connector: adapter})
        object_store = InMemorySessionObjectStore()
        vault = AesGcmSessionArtifactVault(
            object_store,
            bucket="session-test-artifacts",
            key=b"s" * 32,
            key_version="test-v1",
        )
        service = AuthSessionService(
            database,
            adapter_registry=registry,
            secret_provider=secrets,
            session_vault=vault,
        )
        command = EnsureLoginSession(
            fencing_token=handle.fencing_token,
            worker_identity="worker-auth-session-01",
            allowed_origins=(ORIGIN,),
        )

        results = await asyncio.gather(
            *(service.ensure(actor, handle.lease_id, command) for _ in range(20))
        )

        assert all(isinstance(result, LoginSessionReady) for result in results)
        refs = {
            result.browser_context_ref
            for result in results
            if isinstance(result, LoginSessionReady)
        }
        assert len(refs) == 1
        assert provider.authentication_attempts == 1

        async with database.transaction(actor.database_context()) as connection:
            row_cursor = await connection.execute(
                """
                select id, status, object_ref, object_digest, key_version,
                       browser_context_ref
                from atlas.browser_session_artifact
                where lease_id = %s
                """,
                (handle.lease_id,),
            )
            artifact = await row_cursor.fetchone()
            event_cursor = await connection.execute(
                """
                select payload::text as payload
                from atlas.audit_event
                where entity_type in ('browser_session_artifact', 'auth_action_ticket')
                   or event_type like 'secret_grant.%'
                union all
                select payload::text as payload
                from atlas.outbox_event
                where aggregate_type in ('browser_session_artifact', 'auth_action_ticket',
                                         'secret_grant')
                """
            )
            event_payloads = [row["payload"] for row in await event_cursor.fetchall()]
        assert artifact is not None
        assert artifact["status"] == "READY"
        assert artifact["object_digest"].startswith("sha256:")
        assert artifact["key_version"] == "test-v1"
        assert artifact["browser_context_ref"] in refs
        object_ref = artifact["object_ref"]
        assert isinstance(object_ref, str)
        async with database.transaction(
            DatabaseContext(tenant_id=uuid7())
        ) as connection:
            hidden_cursor = await connection.execute(
                "select count(*) as count from atlas.browser_session_artifact "
                "where browser_context_ref = %s",
                (artifact["browser_context_ref"],),
            )
            hidden = await hidden_cursor.fetchone()
        assert hidden is not None
        assert hidden["count"] == 0
        object_key = urlsplit(object_ref).path.lstrip("/")
        ciphertext = await object_store.ciphertext_for_test(object_key)
        assert ciphertext is not None
        assert b"session-password" not in ciphertext
        assert b"session@example.test" not in ciphertext
        assert b"atlas_mock_session" not in ciphertext
        joined_payloads = "\n".join(event_payloads)
        assert "objectRef" not in joined_payloads
        assert seed.secret_ref not in joined_payloads
        assert "session-password" not in joined_payloads

        with pytest.raises(ApplicationError) as stale_fence:
            await service.ensure(
                actor,
                handle.lease_id,
                command.model_copy(update={"fencing_token": handle.fencing_token + 1}),
            )
        assert stale_fence.value.error_code is ErrorCode.LEASE_FENCED
        assert provider.authentication_attempts == 1

        await lease_service.release(
            actor,
            handle.lease_id,
            ReleaseAccountLease(
                fencing_token=handle.fencing_token,
                reason=LeaseReleaseReason.COMPLETED,
            ),
        )
        janitor = SessionJanitorService(database, session_vault=vault)
        batch = await janitor.run_once(
            actor,
            worker_identity="worker-session-janitor-01",
            limit=100,
        )
        assert batch.destroyed == 1
        assert batch.failed == 0
        assert await object_store.ciphertext_for_test(object_key) is None
        async with database.transaction(actor.database_context()) as connection:
            destroyed_cursor = await connection.execute(
                "select status, termination_reason from atlas.browser_session_artifact "
                "where lease_id = %s",
                (handle.lease_id,),
            )
            destroyed = await destroyed_cursor.fetchone()
        assert destroyed is not None
        assert destroyed["status"] == "DESTROYED"
        assert destroyed["termination_reason"] == "LEASE_TERMINATED"
    finally:
        await database.close()


@pytest.mark.anyio
async def test_manual_ticket_does_not_require_secret_or_vault_and_password_fails_closed() -> None:
    database = create_database()
    await database.open()
    try:
        seed = await seed_session_account(database)
        actor = make_actor(seed)
        _, handle = await acquire_lease(database, seed, actor)
        service = AuthSessionService(
            database,
            adapter_registry=AdapterRegistry(),
            secret_provider=None,
            session_vault=None,
        )
        manual_command = EnsureLoginSession(
            fencing_token=handle.fencing_token,
            worker_identity="worker-auth-session-01",
            allowed_origins=(ORIGIN,),
            auth_method=CredentialAuthMethod.MANUAL_BOOTSTRAP,
        )
        first = await service.ensure(actor, handle.lease_id, manual_command)
        second = await service.ensure(actor, handle.lease_id, manual_command)
        assert isinstance(first, LoginSessionManualAction)
        assert isinstance(second, LoginSessionManualAction)
        assert second.action_ticket_id == first.action_ticket_id

        with pytest.raises(ApplicationError) as unavailable:
            await service.ensure(
                actor,
                handle.lease_id,
                EnsureLoginSession(
                    fencing_token=handle.fencing_token,
                    worker_identity="worker-auth-session-01",
                    allowed_origins=(ORIGIN,),
                ),
            )
        assert unavailable.value.error_code is ErrorCode.SESSION_UNAVAILABLE
        async with database.transaction(actor.database_context()) as connection:
            artifact_cursor = await connection.execute(
                "select count(*) as count from atlas.browser_session_artifact "
                "where lease_id = %s",
                (handle.lease_id,),
            )
            artifact_count = await artifact_cursor.fetchone()
        assert artifact_count is not None
        assert artifact_count["count"] == 0
    finally:
        await database.close()


@pytest.mark.anyio
async def test_final_revision_cas_rejects_state_changed_during_external_login() -> None:
    database = create_database()
    await database.open()
    try:
        seed = await seed_session_account(database)
        actor = make_actor(seed)
        _, handle = await acquire_lease(database, seed, actor)
        secrets = InMemorySecretProvider()
        secrets.put_password(
            secret_ref=seed.secret_ref,
            secret_version="v1",
            username="race@example.test",
            password="race-password",
        )
        provider = RevisionRacingSessionProvider(
            database,
            tenant_id=seed.tenant_id,
            account_id=seed.account_id,
        )
        adapter = GenericPasswordAdapter(provider)
        registry = AdapterRegistry({"generic-password": lambda _connector: adapter})
        object_store = InMemorySessionObjectStore()
        vault = AesGcmSessionArtifactVault(
            object_store,
            bucket="session-race-artifacts",
            key=b"r" * 32,
            key_version="test-race-v1",
        )
        service = AuthSessionService(
            database,
            adapter_registry=registry,
            secret_provider=secrets,
            session_vault=vault,
        )

        with pytest.raises(ApplicationError) as stale_snapshot:
            await service.ensure(
                actor,
                handle.lease_id,
                EnsureLoginSession(
                    fencing_token=handle.fencing_token,
                    worker_identity="worker-auth-session-01",
                    allowed_origins=(ORIGIN,),
                ),
            )
        assert stale_snapshot.value.error_code is ErrorCode.PRECONDITION_FAILED
        assert provider.attempts == 1
        async with database.transaction(actor.database_context()) as connection:
            cursor = await connection.execute(
                """
                select status, failure_code, object_ref, object_digest
                from atlas.browser_session_artifact
                where lease_id = %s
                """,
                (handle.lease_id,),
            )
            artifact = await cursor.fetchone()
        assert artifact is not None
        assert artifact["status"] == "FAILED"
        assert artifact["failure_code"] == "STALE_SNAPSHOT"
        assert artifact["object_digest"].startswith("sha256:")
        object_ref = artifact["object_ref"]
        assert isinstance(object_ref, str)
        object_key = urlsplit(object_ref).path.lstrip("/")
        assert await object_store.ciphertext_for_test(object_key) is not None

        janitor = SessionJanitorService(database, session_vault=vault)
        batch = await janitor.run_once(
            actor,
            worker_identity="worker-session-janitor-race",
            limit=10,
        )
        assert batch.destroyed == 1
        assert await object_store.ciphertext_for_test(object_key) is None
    finally:
        await database.close()
