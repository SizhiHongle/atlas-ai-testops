"""Account health policy, RLS, and concurrent snapshot integration tests."""

from dataclasses import dataclass
from os import environ
from typing import cast
from uuid import UUID, uuid7

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from atlas_testops.application.account_health import AccountHealthService
from atlas_testops.application.ports.providers import AdapterContext
from atlas_testops.application.ports.secrets import PasswordSecret
from atlas_testops.core.config import Settings
from atlas_testops.domain.identity import (
    PasswordAuthenticationResult,
    ProviderHealth,
    ProviderHealthState,
)
from atlas_testops.infrastructure.adapters.generic_password import GenericPasswordAdapter
from atlas_testops.infrastructure.adapters.mock_provider import MockIdentityProvider
from atlas_testops.infrastructure.adapters.registry import AdapterRegistry
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.secrets import InMemorySecretProvider
from atlas_testops.main import create_app

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
class HealthSeed:
    tenant_id: str
    project_id: str
    environment_id: str
    connector_id: str
    role_id: str
    pool_id: str
    account_id: str
    secret_ref: str
    headers: dict[str, str]


class RevisionRacingIdentityProvider:
    """Advance account revision during auth to prove external I/O holds no DB lock."""

    def __init__(self) -> None:
        self.database: Database | None = None
        self.tenant_id: UUID | None = None
        self.account_id: UUID | None = None

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
        assert context.origin == ORIGIN
        assert account_handle.startswith("health_")
        assert secret.reveal_username() == "race@example.test"
        assert secret.reveal_password() == "race-password"
        assert self.database is not None
        assert self.tenant_id is not None
        assert self.account_id is not None
        async with self.database.transaction(
            DatabaseContext(tenant_id=self.tenant_id)
        ) as connection:
            await connection.execute(
                """
                update atlas.test_account
                set labels = labels || '{"revision_race":"won"}'::jsonb,
                    revision = revision + 1
                where id = %s
                """,
                (self.account_id,),
            )
        return PasswordAuthenticationResult(
            provider_subject="provider-race",
            role_keys=("sales",),
        )


def actor_headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-Atlas-Tenant-ID": tenant_id,
        "X-Atlas-Actor-ID": str(uuid7()),
    }


def build_health_app(
    prefix: str,
    *,
    username: str = "sales-health@example.test",
    password: str = "health-password",
    store_secret: bool = True,
) -> tuple[FastAPI, MockIdentityProvider, InMemorySecretProvider, str]:
    assert DATABASE_URL is not None
    secret_ref = f"sec_health_{prefix}"
    secrets = InMemorySecretProvider()
    if store_secret:
        secrets.put_password(
            secret_ref=secret_ref,
            secret_version="v1",
            username=username,
            password=password,
        )
    provider = MockIdentityProvider(allowed_origins=(ORIGIN,))
    adapter = GenericPasswordAdapter(provider)
    registry = AdapterRegistry({"generic-password": lambda _connector: adapter})
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=4,
        ),
        adapter_registry=registry,
        secret_provider=secrets,
    )
    return app, provider, secrets, secret_ref


def seed_health_account(
    client: TestClient,
    prefix: str,
    *,
    secret_ref: str,
    account_key: str = "sales-01",
    source: str = "ATLAS_MANAGED",
    external_subject_id: str | None = None,
) -> HealthSeed:
    tenant = client.post(
        "/v1/tenants",
        json={"slug": f"health-{prefix}", "name": f"Health {prefix}"},
    )
    assert tenant.status_code == 201, tenant.text
    tenant_id = cast(str, tenant.json()["id"])
    headers = actor_headers(tenant_id)
    project = client.post(
        "/v1/projects",
        headers={**headers, "Idempotency-Key": f"health-project-{prefix}"},
        json={"projectKey": f"HEALTH_{prefix.upper()}", "name": "Health Project"},
    )
    assert project.status_code == 201, project.text
    project_id = cast(str, project.json()["id"])
    environment = client.post(
        f"/v1/projects/{project_id}/environments",
        headers={**headers, "Idempotency-Key": f"health-environment-{prefix}"},
        json={
            "environmentKey": "pre-test",
            "name": "Pre Test",
            "kind": "TEST",
            "allowedOrigins": [ORIGIN],
        },
    )
    assert environment.status_code == 201, environment.text
    environment_id = cast(str, environment.json()["id"])
    connector = client.post(
        "/v1/connector-installations",
        headers={**headers, "Idempotency-Key": f"health-connector-{prefix}"},
        json={
            "environmentId": environment_id,
            "installationKey": "password",
            "name": "Password Connector",
            "adapterKey": "generic-password",
            "mode": "MANAGED_TEST_ACCOUNTS",
            "configurationRef": f"cfg_health_{prefix}",
            "allowedOrigins": [ORIGIN],
            "requiredCapabilities": ["account.read", "auth.password"],
        },
    )
    assert connector.status_code == 201, connector.text
    connector_id = cast(str, connector.json()["id"])
    validated = client.post(
        f"/v1/connector-installations/{connector_id}:validate",
        headers={**headers, "If-Match": connector.headers["etag"]},
    )
    assert validated.status_code == 200, validated.text
    assert {item["name"] for item in validated.json()["negotiatedCapabilities"]} == {
        "account.read",
        "auth.password",
    }
    role = client.post(
        f"/v1/projects/{project_id}/test-roles",
        headers={**headers, "Idempotency-Key": f"health-role-{prefix}"},
        json={"roleKey": "sales", "name": "销售", "capabilities": []},
    )
    assert role.status_code == 201, role.text
    role_id = cast(str, role.json()["id"])
    pool = client.post(
        f"/v1/environments/{environment_id}/account-pools",
        headers={**headers, "Idempotency-Key": f"health-pool-{prefix}"},
        json={
            "roleId": role_id,
            "poolKey": "sales-cn",
            "name": "销售账号池",
            "healthFailureThreshold": 3,
            "healthRetryCooldownSeconds": 300,
        },
    )
    assert pool.status_code == 201, pool.text
    pool_id = cast(str, pool.json()["id"])
    account_body: dict[str, object] = {
        "connectorInstallationId": connector_id,
        "accountKey": account_key,
        "source": source,
        "loginHintMasked": "sa***@example.test",
        "labels": {"region": "cn"},
        "credentials": [
            {
                "authMethod": "PASSWORD",
                "purpose": "LOGIN",
                "secretRef": secret_ref,
                "secretVersion": "v1",
            }
        ],
    }
    if external_subject_id is not None:
        account_body["externalSubjectId"] = external_subject_id
    account = client.post(
        f"/v1/account-pools/{pool_id}/accounts",
        headers={**headers, "Idempotency-Key": f"health-account-{prefix}"},
        json=account_body,
    )
    assert account.status_code == 201, account.text
    account_id = cast(str, account.json()["id"])
    activated = client.patch(
        f"/v1/test-accounts/{account_id}",
        headers={**headers, "If-Match": account.headers["etag"]},
        json={"lifecycleStatus": "ACTIVE"},
    )
    assert activated.status_code == 200, activated.text
    return HealthSeed(
        tenant_id=tenant_id,
        project_id=project_id,
        environment_id=environment_id,
        connector_id=connector_id,
        role_id=role_id,
        pool_id=pool_id,
        account_id=account_id,
        secret_ref=secret_ref,
        headers=headers,
    )


def test_account_health_success_idempotency_history_and_secret_redaction() -> None:
    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    username = "sales-health@example.test"
    password = "health-password"
    app, provider, _, secret_ref = build_health_app(
        prefix,
        username=username,
        password=password,
    )
    with TestClient(app) as client:
        seed = seed_health_account(client, prefix, secret_ref=secret_ref)
        provider.register_account(
            account_handle=AccountHealthService.verification_account_handle(UUID(seed.account_id)),
            provider_subject="provider-sales-01",
            username=username,
            password=password,
            role_keys=("sales",),
        )
        headers = {
            **seed.headers,
            "Idempotency-Key": f"health-verify-{prefix}",
            "If-Match": '"revision-2"',
        }
        verified = client.post(
            f"/v1/test-accounts/{seed.account_id}:verify",
            headers=headers,
            json={"origin": ORIGIN},
        )
        assert verified.status_code == 201, verified.text
        assert verified.headers["etag"] == '"revision-4"'
        assert verified.headers["idempotency-replayed"] == "false"
        payload = verified.json()
        assert payload["check"]["status"] == "SUCCEEDED"
        assert payload["check"]["resultHealthStatus"] == "HEALTHY"
        assert payload["account"]["healthStatus"] == "HEALTHY"
        assert payload["account"]["operationalStatus"] == "READY"
        assert payload["account"]["consecutiveHealthFailures"] == 0
        assert payload["account"]["available"] is True
        for forbidden in (secret_ref, username, password, "provider-sales-01"):
            assert forbidden not in verified.text

        replay = client.post(
            f"/v1/test-accounts/{seed.account_id}:verify",
            headers=headers,
            json={"origin": ORIGIN},
        )
        assert replay.status_code == 201
        assert replay.json() == payload
        assert replay.headers["idempotency-replayed"] == "true"
        assert provider.authentication_attempts == 1

        checks = client.get(
            f"/v1/test-accounts/{seed.account_id}/health-checks",
            headers=seed.headers,
        )
        assert checks.status_code == 200, checks.text
        assert [item["status"] for item in checks.json()["items"]] == ["SUCCEEDED"]
        transitions = client.get(
            f"/v1/test-accounts/{seed.account_id}/state-transitions",
            headers=seed.headers,
        )
        assert transitions.status_code == 200, transitions.text
        reasons = {item["reason"] for item in transitions.json()["items"]}
        assert {
            "MANAGEMENT_REVOCATION",
            "VERIFICATION_SUCCEEDED",
        }.issubset(reasons)
        foreign_tenant = client.post(
            "/v1/tenants",
            json={"slug": f"foreign-{prefix}", "name": "Foreign"},
        )
        assert foreign_tenant.status_code == 201
        hidden = client.get(
            f"/v1/test-accounts/{seed.account_id}/health-checks",
            headers=actor_headers(cast(str, foreign_tenant.json()["id"])),
        )
        assert hidden.status_code == 404

        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "select set_config('atlas.tenant_id', %s, true)",
                (seed.tenant_id,),
            )
            row = connection.execute(
                """
                select identity_fingerprint, last_health_checked_at,
                       last_health_succeeded_at
                from atlas.test_account where id = %s
                """,
                (UUID(seed.account_id),),
            ).fetchone()
            assert row is not None
            assert cast(str, row[0]).startswith("sha256:")
            assert row[0] != "provider-sales-01"
            assert row[1] is not None and row[2] is not None
            persisted = " ".join(
                str(item[0])
                for item in connection.execute(
                    """
                    select payload from atlas.audit_event where tenant_id = %s
                    union all
                    select payload from atlas.outbox_event where tenant_id = %s
                    """,
                    (UUID(seed.tenant_id), UUID(seed.tenant_id)),
                ).fetchall()
            )
            for forbidden in (secret_ref, username, password, "provider-sales-01"):
                assert forbidden not in persisted

        rebound = client.post(
            "/v1/connector-installations",
            headers={
                **seed.headers,
                "Idempotency-Key": f"health-rebound-connector-{prefix}",
            },
            json={
                "environmentId": seed.environment_id,
                "installationKey": "password-rebound",
                "name": "Rebound Password Connector",
                "adapterKey": "generic-password",
                "mode": "MANAGED_TEST_ACCOUNTS",
                "configurationRef": f"cfg_health_rebound_{prefix}",
                "allowedOrigins": [ORIGIN],
                "requiredCapabilities": ["account.read", "auth.password"],
            },
        )
        assert rebound.status_code == 201, rebound.text
        rebound_validated = client.post(
            f"/v1/connector-installations/{rebound.json()['id']}:validate",
            headers={**seed.headers, "If-Match": rebound.headers["etag"]},
        )
        assert rebound_validated.status_code == 200, rebound_validated.text
        rebound_account = client.patch(
            f"/v1/test-accounts/{seed.account_id}",
            headers={**seed.headers, "If-Match": '"revision-4"'},
            json={"connectorInstallationId": rebound.json()["id"]},
        )
        assert rebound_account.status_code == 200, rebound_account.text
        assert rebound_account.json()["healthStatus"] == "UNKNOWN"
        assert rebound_account.json()["operationalStatus"] == "VERIFYING"
        assert rebound_account.json()["lastHealthCheckedAt"] is None
        assert rebound_account.json()["lastHealthSucceededAt"] is None
        assert rebound_account.json()["available"] is False
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "select set_config('atlas.tenant_id', %s, true)",
                (seed.tenant_id,),
            )
            rebound_identity = connection.execute(
                """
                select identity_fingerprint from atlas.test_account where id = %s
                """,
                (UUID(seed.account_id),),
            ).fetchone()
            assert rebound_identity == (None,)


def test_account_health_failure_threshold_and_role_drift_quarantine() -> None:
    prefix = uuid7().hex[-10:]
    username = "threshold@example.test"
    password = "stored-password"
    app, provider, _, secret_ref = build_health_app(
        prefix,
        username=username,
        password=password,
    )
    with TestClient(app) as client:
        seed = seed_health_account(client, prefix, secret_ref=secret_ref)
        provider.register_account(
            account_handle=AccountHealthService.verification_account_handle(UUID(seed.account_id)),
            provider_subject="provider-threshold",
            username=username,
            password="different-provider-password",
            role_keys=("sales",),
        )
        revision = 2
        for attempt in range(1, 4):
            response = client.post(
                f"/v1/test-accounts/{seed.account_id}:verify",
                headers={
                    **seed.headers,
                    "Idempotency-Key": f"health-threshold-{prefix}-{attempt}",
                    "If-Match": f'"revision-{revision}"',
                },
                json={"origin": ORIGIN},
            )
            assert response.status_code == 201, response.text
            payload = response.json()
            assert payload["check"]["failureCode"] == "AUTHENTICATION_FAILED"
            assert payload["account"]["consecutiveHealthFailures"] == attempt
            revision = cast(int, payload["account"]["revision"])
            if attempt < 3:
                assert payload["account"]["healthStatus"] == "DEGRADED"
                assert payload["account"]["operationalStatus"] == "COOLDOWN"
                assert payload["account"]["cooldownUntil"] is not None
            else:
                assert payload["account"]["healthStatus"] == "QUARANTINED"
                assert payload["account"]["operationalStatus"] == "VERIFYING"
                assert payload["account"]["cooldownUntil"] is None

        first_check_page = client.get(
            f"/v1/test-accounts/{seed.account_id}/health-checks",
            headers=seed.headers,
            params={"limit": 1},
        )
        assert first_check_page.status_code == 200
        assert first_check_page.json()["nextCursor"] is not None
        second_check_page = client.get(
            f"/v1/test-accounts/{seed.account_id}/health-checks",
            headers=seed.headers,
            params={
                "limit": 1,
                "cursor": first_check_page.json()["nextCursor"],
            },
        )
        assert second_check_page.status_code == 200
        assert (
            second_check_page.json()["items"][0]["id"] != first_check_page.json()["items"][0]["id"]
        )
        first_transition_page = client.get(
            f"/v1/test-accounts/{seed.account_id}/state-transitions",
            headers=seed.headers,
            params={"limit": 1},
        )
        assert first_transition_page.status_code == 200
        assert first_transition_page.json()["nextCursor"] is not None
        second_transition_page = client.get(
            f"/v1/test-accounts/{seed.account_id}/state-transitions",
            headers=seed.headers,
            params={
                "limit": 1,
                "cursor": first_transition_page.json()["nextCursor"],
            },
        )
        assert second_transition_page.status_code == 200
        assert (
            second_transition_page.json()["items"][0]["id"]
            != first_transition_page.json()["items"][0]["id"]
        )

        restored = client.post(
            f"/v1/test-accounts/{seed.account_id}:restore",
            headers={**seed.headers, "If-Match": f'"revision-{revision}"'},
            json={"reason": "人工调整登录 Credential"},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["healthStatus"] == "UNKNOWN"
        assert restored.json()["available"] is False

    role_prefix = uuid7().hex[-10:]
    role_app, role_provider, _, role_secret_ref = build_health_app(role_prefix)
    with TestClient(role_app) as client:
        role_seed = seed_health_account(
            client,
            role_prefix,
            secret_ref=role_secret_ref,
        )
        role_provider.register_account(
            account_handle=AccountHealthService.verification_account_handle(
                UUID(role_seed.account_id)
            ),
            provider_subject="provider-role-drift",
            username="sales-health@example.test",
            password="health-password",
            role_keys=("observer",),
        )
        drift = client.post(
            f"/v1/test-accounts/{role_seed.account_id}:verify",
            headers={
                **role_seed.headers,
                "Idempotency-Key": f"health-role-drift-{role_prefix}",
                "If-Match": '"revision-2"',
            },
            json={"origin": ORIGIN},
        )
        assert drift.status_code == 201, drift.text
        assert drift.json()["check"]["failureCode"] == "ROLE_DRIFT"
        assert drift.json()["account"]["healthStatus"] == "QUARANTINED"


def test_account_health_stale_snapshot_uses_no_database_connection_during_probe() -> None:
    """A concurrent write in a one-connection pool must make the result STALE."""

    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    secret_ref = f"sec_health_{prefix}"
    secrets = InMemorySecretProvider()
    secrets.put_password(
        secret_ref=secret_ref,
        secret_version="v1",
        username="race@example.test",
        password="race-password",
    )
    provider = RevisionRacingIdentityProvider()
    adapter = GenericPasswordAdapter(provider)
    registry = AdapterRegistry({"generic-password": lambda _connector: adapter})
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=1,
            database_connect_timeout_seconds=2,
            account_health_verification_timeout_seconds=5,
            account_health_attempt_ttl_seconds=30,
        ),
        adapter_registry=registry,
        secret_provider=secrets,
    )
    with TestClient(app) as client:
        seed = seed_health_account(client, prefix, secret_ref=secret_ref)
        provider.database = cast(Database, app.state.database)
        provider.tenant_id = UUID(seed.tenant_id)
        provider.account_id = UUID(seed.account_id)
        response = client.post(
            f"/v1/test-accounts/{seed.account_id}:verify",
            headers={
                **seed.headers,
                "Idempotency-Key": f"health-race-{prefix}",
                "If-Match": '"revision-2"',
            },
            json={"origin": ORIGIN},
        )
        assert response.status_code == 201, response.text
        assert response.json()["check"]["status"] == "STALE"
        assert response.json()["check"]["failureCode"] == "STALE_SNAPSHOT"
        assert response.json()["check"]["resultHealthStatus"] is None
        assert response.json()["account"]["revision"] == 4
        assert response.json()["account"]["operationalStatus"] == "VERIFYING"
        assert response.json()["account"]["labels"]["revision_race"] == "won"


def test_account_health_without_secret_provider_fails_closed_without_state_change() -> None:
    """A missing Secret Provider must fail closed without creating fake state."""

    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    provider = MockIdentityProvider(allowed_origins=(ORIGIN,))
    registry = AdapterRegistry(
        {"generic-password": lambda _connector: GenericPasswordAdapter(provider)}
    )
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
        ),
        adapter_registry=registry,
    )
    with TestClient(app) as client:
        seed = seed_health_account(
            client,
            prefix,
            secret_ref=f"sec_health_{prefix}",
        )
        response = client.post(
            f"/v1/test-accounts/{seed.account_id}:verify",
            headers={
                **seed.headers,
                "Idempotency-Key": f"health-no-secret-{prefix}",
                "If-Match": '"revision-2"',
            },
            json={"origin": ORIGIN},
        )
        assert response.status_code == 503
        assert response.json()["errorCode"] == "PROVIDER_UNAVAILABLE"
        account = client.get(
            f"/v1/test-accounts/{seed.account_id}",
            headers=seed.headers,
        )
        assert account.status_code == 200
        assert account.json()["revision"] == 2
        checks = client.get(
            f"/v1/test-accounts/{seed.account_id}/health-checks",
            headers=seed.headers,
        )
        assert checks.status_code == 200
        assert checks.json()["items"] == []
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "select set_config('atlas.tenant_id', %s, true)",
                (seed.tenant_id,),
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    update atlas.test_account
                    set health_status = 'HEALTHY', operational_status = 'READY'
                    where id = %s
                    """,
                    (UUID(seed.account_id),),
                )
            connection.rollback()


def test_account_health_missing_secret_records_retryable_infrastructure_failure() -> None:
    prefix = uuid7().hex[-10:]
    app, provider, _, secret_ref = build_health_app(prefix, store_secret=False)
    with TestClient(app) as client:
        seed = seed_health_account(client, prefix, secret_ref=secret_ref)
        response = client.post(
            f"/v1/test-accounts/{seed.account_id}:verify",
            headers={
                **seed.headers,
                "Idempotency-Key": f"health-missing-material-{prefix}",
                "If-Match": '"revision-2"',
            },
            json={"origin": ORIGIN},
        )

        assert response.status_code == 201, response.text
        assert response.json()["check"]["status"] == "FAILED"
        assert response.json()["check"]["failureCode"] == "SECRET_UNAVAILABLE"
        assert response.json()["check"]["retryable"] is True
        assert response.json()["account"]["healthStatus"] == "DEGRADED"
        assert response.json()["account"]["consecutiveHealthFailures"] == 0
        assert provider.authentication_attempts == 0


def test_account_health_preflight_rejects_origin_and_stale_revision_without_mutation() -> None:
    prefix = uuid7().hex[-10:]
    app, _, _, secret_ref = build_health_app(prefix)
    with TestClient(app) as client:
        seed = seed_health_account(client, prefix, secret_ref=secret_ref)
        wrong_origin = client.post(
            f"/v1/test-accounts/{seed.account_id}:verify",
            headers={
                **seed.headers,
                "Idempotency-Key": f"health-origin-{prefix}",
                "If-Match": '"revision-2"',
            },
            json={"origin": "https://other.example.test"},
        )
        assert wrong_origin.status_code == 403
        assert wrong_origin.json()["errorCode"] == "ORIGIN_NOT_ALLOWED"

        stale = client.post(
            f"/v1/test-accounts/{seed.account_id}:verify",
            headers={
                **seed.headers,
                "Idempotency-Key": f"health-stale-revision-{prefix}",
                "If-Match": '"revision-1"',
            },
            json={"origin": ORIGIN},
        )
        assert stale.status_code == 412
        assert stale.headers["etag"] == '"revision-2"'
        checks = client.get(
            f"/v1/test-accounts/{seed.account_id}/health-checks",
            headers=seed.headers,
        )
        assert checks.status_code == 200
        assert checks.json()["items"] == []


def test_external_account_subject_mismatch_is_immediately_quarantined() -> None:
    prefix = uuid7().hex[-10:]
    app, provider, _, secret_ref = build_health_app(prefix)
    with TestClient(app) as client:
        seed = seed_health_account(
            client,
            prefix,
            secret_ref=secret_ref,
            source="EXTERNAL_SYNCED",
            external_subject_id="expected-provider-subject",
        )
        provider.register_account(
            account_handle=AccountHealthService.verification_account_handle(UUID(seed.account_id)),
            provider_subject="unexpected-provider-subject",
            username="sales-health@example.test",
            password="health-password",
            role_keys=("sales",),
        )
        response = client.post(
            f"/v1/test-accounts/{seed.account_id}:verify",
            headers={
                **seed.headers,
                "Idempotency-Key": f"health-subject-mismatch-{prefix}",
                "If-Match": '"revision-2"',
            },
            json={"origin": ORIGIN},
        )

        assert response.status_code == 201, response.text
        assert response.json()["check"]["failureCode"] == "IDENTITY_MISMATCH"
        assert response.json()["account"]["healthStatus"] == "QUARANTINED"
