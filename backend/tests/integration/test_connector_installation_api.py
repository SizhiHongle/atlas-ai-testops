"""ConnectorInstallation 管理、验证、隔离与失效链路集成测试。"""

from datetime import UTC, datetime, timedelta
from os import environ
from typing import cast
from uuid import UUID, uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from atlas_testops.application.ports.providers import AdapterContext
from atlas_testops.core.config import Settings
from atlas_testops.domain.identity import (
    AdapterManifest,
    AdapterMode,
    CapabilityDescriptor,
    CapabilityRequirement,
    ConnectorInstallationRecord,
    NegotiatedCapabilities,
    ProviderCapability,
    ProviderHealth,
    ProviderHealthState,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")
ORIGIN = "https://staging.example.test"
ALT_ORIGIN = "https://alt.example.test"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(DATABASE_URL is None, reason="ATLAS_TEST_DATABASE_URL is not configured"),
]


def actor_headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-Atlas-Tenant-ID": tenant_id,
        "X-Atlas-Actor-ID": str(uuid7()),
    }


def create_workspace(
    client: TestClient,
    prefix: str,
    *,
    kind: str = "TEST",
    origins: tuple[str, ...] = (ORIGIN, ALT_ORIGIN),
) -> tuple[str, str, str]:
    tenant = client.post(
        "/v1/tenants",
        json={"slug": f"connector-{prefix}", "name": f"Connector {prefix}"},
    )
    assert tenant.status_code == 201, tenant.text
    tenant_id = cast(str, tenant.json()["id"])
    headers = actor_headers(tenant_id)
    project = client.post(
        "/v1/projects",
        headers={**headers, "Idempotency-Key": f"connector-project-{prefix}"},
        json={
            "projectKey": f"CONNECTOR_{prefix.upper()}",
            "name": "Connector Project",
        },
    )
    assert project.status_code == 201, project.text
    project_id = cast(str, project.json()["id"])
    environment = client.post(
        f"/v1/projects/{project_id}/environments",
        headers={**headers, "Idempotency-Key": f"connector-environment-{prefix}"},
        json={
            "environmentKey": "pre-test" if kind == "TEST" else "production",
            "name": kind.title(),
            "kind": kind,
            "allowedOrigins": list(origins),
        },
    )
    assert environment.status_code == 201, environment.text
    return tenant_id, project_id, cast(str, environment.json()["id"])


def connector_body(
    *,
    environment_id: str,
    prefix: str,
    installation_key: str = "password",
    mode: str = "MANAGED_TEST_ACCOUNTS",
    required_capabilities: tuple[str, ...] = ("auth.password",),
    origins: tuple[str, ...] = (ORIGIN,),
    adapter_key: str = "generic-password",
) -> dict[str, object]:
    return {
        "environmentId": environment_id,
        "installationKey": installation_key,
        "name": f"Connector {installation_key}",
        "adapterKey": adapter_key,
        "mode": mode,
        "configurationRef": f"cfg_connector_{prefix}_{installation_key}",
        "allowedOrigins": list(origins),
        "requiredCapabilities": list(required_capabilities),
    }


def create_active_connector(
    client: TestClient,
    *,
    tenant_id: str,
    environment_id: str,
    prefix: str,
) -> tuple[str, str]:
    headers = actor_headers(tenant_id)
    created = client.post(
        "/v1/connector-installations",
        headers={**headers, "Idempotency-Key": f"connector-create-{prefix}"},
        json=connector_body(environment_id=environment_id, prefix=prefix),
    )
    assert created.status_code == 201, created.text
    connector_id = cast(str, created.json()["id"])
    validated = client.post(
        f"/v1/connector-installations/{connector_id}:validate",
        headers={**headers, "If-Match": created.headers["etag"]},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["status"] == "ACTIVE"
    return connector_id, validated.headers["etag"]


def set_tenant(connection: psycopg.Connection[tuple[object, ...]], tenant_id: str) -> None:
    connection.execute("select set_config('atlas.tenant_id', %s, true)", (tenant_id,))


class RevisionRacingAdapter:
    """在 Probe 中模拟并发管理更新，验证外部 I/O 后的 Revision CAS。"""

    _manifest = AdapterManifest(
        adapter_key="generic-password",
        protocol_version="1.0",
        implementation_version="9.9.9",
        capabilities=(
            CapabilityDescriptor(
                name=ProviderCapability.AUTH_PASSWORD,
                version="1.0",
                mode=AdapterMode.BROWSER,
            ),
        ),
    )

    def __init__(
        self,
        connector: ConnectorInstallationRecord,
        database: Database,
    ) -> None:
        self._connector = connector
        self._database = database

    def manifest(self) -> AdapterManifest:
        return self._manifest

    async def probe(self, _context: AdapterContext) -> ProviderHealth:
        async with self._database.transaction(
            DatabaseContext(tenant_id=self._connector.tenant_id)
        ) as connection:
            await connection.execute(
                """
                update atlas.connector_installation
                set name = 'Concurrent Update', revision = revision + 1
                where id = %s
                """,
                (self._connector.id,),
            )
        return ProviderHealth(
            state=ProviderHealthState.HEALTHY,
            safe_message="concurrent probe completed",
        )

    async def negotiate(
        self,
        _context: AdapterContext,
        _requirement: CapabilityRequirement,
    ) -> NegotiatedCapabilities:
        return NegotiatedCapabilities(capabilities=self._manifest.capabilities)

    async def health(self, _context: AdapterContext) -> ProviderHealth:
        return ProviderHealth(
            state=ProviderHealthState.HEALTHY,
            safe_message="connector is healthy",
        )


def test_connector_management_validation_pagination_and_secret_redaction() -> None:
    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    configuration_ref = f"cfg_connector_{prefix}_password"
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=4,
        )
    )

    with TestClient(app) as client:
        tenant_id, _, environment_id = create_workspace(client, prefix)
        headers = actor_headers(tenant_id)
        body = connector_body(environment_id=environment_id, prefix=prefix)
        command_headers = {
            **headers,
            "Idempotency-Key": f"connector-create-{prefix}",
        }
        created = client.post(
            "/v1/connector-installations",
            headers=command_headers,
            json=body,
        )
        assert created.status_code == 201, created.text
        assert created.headers["etag"] == '"revision-1"'
        assert created.headers["idempotency-replayed"] == "false"
        assert configuration_ref not in created.text
        assert "configurationRef" not in created.text
        connector_id = cast(str, created.json()["id"])
        assert created.json()["configurationState"] == "CONFIGURED"
        assert created.json()["negotiatedCapabilities"] == []

        replay = client.post(
            "/v1/connector-installations",
            headers=command_headers,
            json=body,
        )
        assert replay.status_code == 201
        assert replay.json() == created.json()
        assert replay.headers["idempotency-replayed"] == "true"

        fetched = client.get(
            f"/v1/connector-installations/{connector_id}",
            headers=headers,
        )
        assert fetched.status_code == 200
        assert fetched.headers["etag"] == '"revision-1"'

        renamed = client.patch(
            f"/v1/connector-installations/{connector_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"name": "Primary Password Connector"},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["revision"] == 2
        stale = client.patch(
            f"/v1/connector-installations/{connector_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"name": "Stale"},
        )
        assert stale.status_code == 412
        assert stale.headers["etag"] == '"revision-2"'

        validated = client.post(
            f"/v1/connector-installations/{connector_id}:validate",
            headers={**headers, "If-Match": renamed.headers["etag"]},
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["status"] == "ACTIVE"
        assert validated.json()["healthState"] == "HEALTHY"
        assert validated.json()["protocolVersion"] == "1.0"
        assert validated.json()["implementationVersion"] == "0.1.0"
        assert validated.json()["negotiatedCapabilities"] == [
            {"name": "account.read", "version": "1.0", "mode": "browser"},
            {"name": "auth.password", "version": "1.0", "mode": "browser"},
        ]

        second_body = connector_body(
            environment_id=environment_id,
            prefix=prefix,
            installation_key="password-backup",
        )
        second = client.post(
            "/v1/connector-installations",
            headers={
                **headers,
                "Idempotency-Key": f"connector-second-{prefix}",
            },
            json=second_body,
        )
        assert second.status_code == 201, second.text

        first_page = client.get(
            f"/v1/environments/{environment_id}/connector-installations",
            headers=headers,
            params={"limit": 1},
        )
        assert first_page.status_code == 200
        assert len(first_page.json()["items"]) == 1
        assert first_page.json()["nextCursor"] is not None
        second_page = client.get(
            f"/v1/environments/{environment_id}/connector-installations",
            headers=headers,
            params={"limit": 1, "cursor": first_page.json()["nextCursor"]},
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["items"]) == 1
        assert second_page.json()["items"][0]["id"] != first_page.json()["items"][0]["id"]

        reset = client.patch(
            f"/v1/connector-installations/{connector_id}",
            headers={**headers, "If-Match": validated.headers["etag"]},
            json={
                "configurationRef": f"cfg_connector_{prefix}_rotated",
                "allowedOrigins": [ORIGIN],
            },
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["status"] == "DRAFT"
        assert reset.json()["healthState"] is None
        assert reset.json()["negotiatedCapabilities"] == []

        revalidated = client.post(
            f"/v1/connector-installations/{connector_id}:validate",
            headers={**headers, "If-Match": reset.headers["etag"]},
        )
        assert revalidated.status_code == 200
        disabled = client.patch(
            f"/v1/connector-installations/{connector_id}",
            headers={**headers, "If-Match": revalidated.headers["etag"]},
            json={"status": "DISABLED"},
        )
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "DISABLED"
        cannot_validate = client.post(
            f"/v1/connector-installations/{connector_id}:validate",
            headers={**headers, "If-Match": disabled.headers["etag"]},
        )
        assert cannot_validate.status_code == 409

        draft = client.patch(
            f"/v1/connector-installations/{connector_id}",
            headers={**headers, "If-Match": disabled.headers["etag"]},
            json={"status": "DRAFT"},
        )
        assert draft.status_code == 200

        origin_dependency = client.patch(
            f"/v1/environments/{environment_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"allowedOrigins": [ALT_ORIGIN]},
        )
        assert origin_dependency.status_code == 409

        other_tenant, _, _ = create_workspace(client, uuid7().hex[-10:])
        hidden = client.get(
            f"/v1/connector-installations/{connector_id}",
            headers=actor_headers(other_tenant),
        )
        assert hidden.status_code == 404

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            serialized_payloads = [
                str(row[0])
                for row in connection.execute(
                    """
                    select payload from atlas.audit_event
                    where entity_type = 'connector_installation'
                    union all
                    select payload from atlas.outbox_event
                    where aggregate_type = 'connector_installation'
                    """
                ).fetchall()
            ]
            assert serialized_payloads
            assert all(configuration_ref not in payload for payload in serialized_payloads)
            assert all("configurationRef" not in payload for payload in serialized_payloads)


def test_connector_rejects_invalid_mode_origin_adapter_and_capability() -> None:
    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
        )
    )

    with TestClient(app) as client:
        tenant_id, _, environment_id = create_workspace(client, prefix)
        headers = actor_headers(tenant_id)
        outside = client.post(
            "/v1/connector-installations",
            headers={**headers, "Idempotency-Key": f"outside-origin-{prefix}"},
            json=connector_body(
                environment_id=environment_id,
                prefix=prefix,
                origins=("https://outside.example.test",),
            ),
        )
        assert outside.status_code == 400
        assert outside.json()["errorCode"] == "ORIGIN_NOT_ALLOWED"

        unknown = client.post(
            "/v1/connector-installations",
            headers={**headers, "Idempotency-Key": f"unknown-adapter-{prefix}"},
            json=connector_body(
                environment_id=environment_id,
                prefix=prefix,
                adapter_key="uninstalled-adapter",
            ),
        )
        assert unknown.status_code == 503

        invalid_observer = client.post(
            "/v1/connector-installations",
            headers={**headers, "Idempotency-Key": f"observer-auth-{prefix}"},
            json=connector_body(
                environment_id=environment_id,
                prefix=prefix,
                mode="OBSERVE_ONLY",
            ),
        )
        assert invalid_observer.status_code == 422

        managed = client.post(
            "/v1/connector-installations",
            headers={**headers, "Idempotency-Key": f"effective-mode-{prefix}"},
            json=connector_body(environment_id=environment_id, prefix=prefix),
        )
        assert managed.status_code == 201
        effective_conflict = client.patch(
            f"/v1/connector-installations/{managed.json()['id']}",
            headers={**headers, "If-Match": managed.headers["etag"]},
            json={"mode": "OBSERVE_ONLY"},
        )
        assert effective_conflict.status_code == 400
        forbidden_health = client.patch(
            f"/v1/connector-installations/{managed.json()['id']}",
            headers={**headers, "If-Match": managed.headers["etag"]},
            json={"status": "ACTIVE"},
        )
        assert forbidden_health.status_code == 422
        empty_patch = client.patch(
            f"/v1/connector-installations/{managed.json()['id']}",
            headers={**headers, "If-Match": managed.headers["etag"]},
            json={},
        )
        assert empty_patch.status_code == 422

        production_prefix = uuid7().hex[-10:]
        production_tenant, _, production_environment = create_workspace(
            client,
            production_prefix,
            kind="PRODUCTION",
            origins=("https://production.example.test",),
        )
        production_headers = actor_headers(production_tenant)
        forbidden_managed = client.post(
            "/v1/connector-installations",
            headers={
                **production_headers,
                "Idempotency-Key": f"production-managed-{production_prefix}",
            },
            json=connector_body(
                environment_id=production_environment,
                prefix=production_prefix,
                origins=("https://production.example.test",),
            ),
        )
        assert forbidden_managed.status_code == 403

        observer = client.post(
            "/v1/connector-installations",
            headers={
                **production_headers,
                "Idempotency-Key": f"production-observer-{production_prefix}",
            },
            json=connector_body(
                environment_id=production_environment,
                prefix=production_prefix,
                mode="OBSERVE_ONLY",
                required_capabilities=("account.read",),
                origins=("https://production.example.test",),
            ),
        )
        assert observer.status_code == 201, observer.text
        unavailable = client.post(
            f"/v1/connector-installations/{observer.json()['id']}:validate",
            headers={
                **production_headers,
                "If-Match": observer.headers["etag"],
            },
        )
        assert unavailable.status_code == 200
        assert unavailable.json()["status"] == "ACTIVE"
        assert unavailable.json()["healthState"] == "HEALTHY"
        assert unavailable.json()["negotiatedCapabilities"] == [
            {"name": "account.read", "version": "1.0", "mode": "browser"}
        ]


def test_disabling_connector_revokes_active_lease_and_issued_grant() -> None:
    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=6,
        )
    )
    now = datetime.now(UTC)

    with TestClient(app) as client:
        tenant_id, project_id, environment_id = create_workspace(client, prefix)
        headers = actor_headers(tenant_id)
        connector_id, connector_etag = create_active_connector(
            client,
            tenant_id=tenant_id,
            environment_id=environment_id,
            prefix=prefix,
        )
        role = client.post(
            f"/v1/projects/{project_id}/test-roles",
            headers={**headers, "Idempotency-Key": f"connector-role-{prefix}"},
            json={
                "roleKey": "sales",
                "name": "销售",
                "capabilities": ["customer.read"],
            },
        )
        assert role.status_code == 201
        pool = client.post(
            f"/v1/environments/{environment_id}/account-pools",
            headers={**headers, "Idempotency-Key": f"connector-pool-{prefix}"},
            json={
                "roleId": role.json()["id"],
                "poolKey": "sales-cn",
                "name": "销售账号池",
                "defaultTtlSeconds": 300,
                "cooldownSeconds": 0,
            },
        )
        assert pool.status_code == 201
        account = client.post(
            f"/v1/account-pools/{pool.json()['id']}/accounts",
            headers={**headers, "Idempotency-Key": f"connector-account-{prefix}"},
            json={
                "connectorInstallationId": connector_id,
                "accountKey": "sales-01",
                "source": "ATLAS_MANAGED",
                "loginHintMasked": "sa***@example.test",
                "credentials": [
                    {
                        "authMethod": "PASSWORD",
                        "purpose": "LOGIN",
                        "secretRef": f"sec_connector_{prefix}",
                        "secretVersion": "v1",
                    }
                ],
            },
        )
        assert account.status_code == 201, account.text
        account_id = cast(str, account.json()["id"])
        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            connection.execute(
                """
                update atlas.test_account
                set lifecycle_status = 'ACTIVE', health_status = 'HEALTHY',
                    operational_status = 'READY',
                    identity_fingerprint = 'sha256:' || repeat('a', 64),
                    last_health_checked_at = statement_timestamp(),
                    last_health_succeeded_at = statement_timestamp(),
                    revision = revision + 1
                where id = %s
                """,
                (UUID(account_id),),
            )

        acquired = client.post(
            "/internal/v1/account-leases",
            headers={**headers, "Idempotency-Key": f"connector-acquire-{prefix}"},
            json={
                "executionId": f"execution-connector-{prefix}",
                "workerId": "worker-connector-01",
                "environmentId": environment_id,
                "roleKey": "sales",
                "requirements": {"authMethods": ["PASSWORD"]},
                "ttlSeconds": 300,
                "executionDeadline": (now + timedelta(hours=1)).isoformat(),
            },
        )
        assert acquired.status_code == 201, acquired.text
        lease_id = cast(str, acquired.json()["leaseId"])
        issued = client.post(
            f"/internal/v1/account-leases/{lease_id}:issue-secret-grant",
            headers=headers,
            json={
                "fencingToken": acquired.json()["fencingToken"],
                "purpose": "LOGIN",
                "workerIdentity": "worker-connector-01",
                "allowedOrigins": [ORIGIN],
            },
        )
        assert issued.status_code == 201, issued.text

        disabled = client.patch(
            f"/v1/connector-installations/{connector_id}",
            headers={**headers, "If-Match": connector_etag},
            json={"status": "DISABLED"},
        )
        assert disabled.status_code == 200, disabled.text
        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            lease_state = connection.execute(
                "select status, release_reason from atlas.account_lease where id = %s",
                (UUID(lease_id),),
            ).fetchone()
            assert lease_state == ("REVOKED", "CONNECTOR_DISABLED")
            grant_state = connection.execute(
                """
                select status, termination_reason, connector_installation_id
                from atlas.secret_grant where lease_id = %s
                """,
                (UUID(lease_id),),
            ).fetchone()
            assert grant_state == (
                "REVOKED",
                "CONNECTOR_UNAVAILABLE",
                UUID(connector_id),
            )
            account_state = connection.execute(
                """
                select health_status, operational_status
                from atlas.test_account where id = %s
                """,
                (UUID(account_id),),
            ).fetchone()
            assert account_state == ("UNKNOWN", "VERIFYING")
            transition_reason = connection.execute(
                """
                select reason from atlas.account_state_transition
                where account_id = %s
                order by occurred_at desc, id desc limit 1
                """,
                (UUID(account_id),),
            ).fetchone()
            assert transition_reason == ("MANAGEMENT_REVOCATION",)


def test_connector_validation_rejects_concurrent_revision_after_external_probe() -> None:
    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=1,
        )
    )
    app.state.adapter_registry.register(
        "generic-password",
        lambda connector: RevisionRacingAdapter(
            connector,
            cast(Database, app.state.database),
        ),
    )

    with TestClient(app) as client:
        tenant_id, _, environment_id = create_workspace(client, prefix)
        headers = actor_headers(tenant_id)
        created = client.post(
            "/v1/connector-installations",
            headers={**headers, "Idempotency-Key": f"connector-race-{prefix}"},
            json=connector_body(environment_id=environment_id, prefix=prefix),
        )
        assert created.status_code == 201
        connector_id = cast(str, created.json()["id"])

        raced = client.post(
            f"/v1/connector-installations/{connector_id}:validate",
            headers={**headers, "If-Match": created.headers["etag"]},
        )
        assert raced.status_code == 412, raced.text
        assert raced.headers["etag"] == '"revision-2"'
        current = client.get(
            f"/v1/connector-installations/{connector_id}",
            headers=headers,
        )
        assert current.status_code == 200
        assert current.json()["name"] == "Concurrent Update"
        assert current.json()["status"] == "DRAFT"
        assert current.json()["revision"] == 2
