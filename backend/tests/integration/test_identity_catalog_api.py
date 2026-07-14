"""测试身份目录管理 API 的真实 PostgreSQL 集成测试。"""

from os import environ
from typing import cast
from uuid import UUID, uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from atlas_testops.core.config import Settings
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(DATABASE_URL is None, reason="ATLAS_TEST_DATABASE_URL is not configured"),
]


def actor_headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-Atlas-Tenant-ID": tenant_id,
        "X-Atlas-Actor-ID": str(uuid7()),
    }


def create_workspace(client: TestClient, prefix: str) -> tuple[str, str, str]:
    tenant_response = client.post(
        "/v1/tenants",
        json={"slug": f"identity-{prefix}", "name": f"Identity {prefix}"},
    )
    assert tenant_response.status_code == 201, tenant_response.text
    tenant_id = cast(str, tenant_response.json()["id"])
    headers = actor_headers(tenant_id)
    project_response = client.post(
        "/v1/projects",
        headers={**headers, "Idempotency-Key": f"identity-project-{prefix}"},
        json={"projectKey": f"IDENTITY_{prefix.upper()}", "name": "Identity Project"},
    )
    assert project_response.status_code == 201, project_response.text
    project_id = cast(str, project_response.json()["id"])
    environment_response = client.post(
        f"/v1/projects/{project_id}/environments",
        headers={**headers, "Idempotency-Key": f"identity-environment-{prefix}"},
        json={
            "environmentKey": "pre-test",
            "name": "Pre Test",
            "kind": "TEST",
            "allowedOrigins": ["https://staging.example.test"],
        },
    )
    assert environment_response.status_code == 201, environment_response.text
    environment_id = cast(str, environment_response.json()["id"])
    return tenant_id, project_id, environment_id


def set_tenant(connection: psycopg.Connection[tuple[object, ...]], tenant_id: str) -> None:
    connection.execute(
        "select set_config('atlas.tenant_id', %s, true)",
        (tenant_id,),
    )


def test_identity_catalog_scope_secrets_capacity_and_revision() -> None:
    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
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
        tenant_id, project_id, environment_id = create_workspace(client, prefix)
        headers = actor_headers(tenant_id)
        role_request = {
            "roleKey": "sales",
            "name": "销售",
            "description": "客户运营销售角色",
            "capabilities": ["customer.read", "visit:create"],
        }
        role_headers = {**headers, "Idempotency-Key": f"identity-role-{prefix}"}
        role_response = client.post(
            f"/v1/projects/{project_id}/test-roles",
            headers=role_headers,
            json=role_request,
        )
        assert role_response.status_code == 201, role_response.text
        assert role_response.headers["etag"] == '"revision-1"'
        assert role_response.headers["idempotency-replayed"] == "false"
        role = role_response.json()
        role_id = role["id"]

        replayed_role = client.post(
            f"/v1/projects/{project_id}/test-roles",
            headers=role_headers,
            json=role_request,
        )
        assert replayed_role.status_code == 201
        assert replayed_role.json() == role
        assert replayed_role.headers["idempotency-replayed"] == "true"

        listed_roles = client.get(
            f"/v1/projects/{project_id}/test-roles",
            headers=headers,
        )
        assert listed_roles.status_code == 200
        assert [item["id"] for item in listed_roles.json()["items"]] == [role_id]

        updated_role = client.patch(
            f"/v1/test-roles/{role_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"name": "销售顾问"},
        )
        assert updated_role.status_code == 200
        assert updated_role.json()["revision"] == 2
        stale_role = client.patch(
            f"/v1/test-roles/{role_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"name": "Stale"},
        )
        assert stale_role.status_code == 412
        assert stale_role.headers["etag"] == '"revision-2"'

        pool_response = client.post(
            f"/v1/environments/{environment_id}/account-pools",
            headers={**headers, "Idempotency-Key": f"identity-pool-{prefix}"},
            json={
                "roleId": role_id,
                "poolKey": "sales-cn",
                "name": "销售账号池",
                "defaultTtlSeconds": 1800,
                "cooldownSeconds": 60,
            },
        )
        assert pool_response.status_code == 201, pool_response.text
        pool = pool_response.json()
        pool_id = pool["id"]

        secret_ref = f"sec_catalog_{prefix}"
        connector_response = client.post(
            "/v1/connector-installations",
            headers={
                **headers,
                "Idempotency-Key": f"identity-connector-{prefix}",
            },
            json={
                "environmentId": environment_id,
                "installationKey": "password",
                "name": "Password Connector",
                "adapterKey": "generic-password",
                "mode": "MANAGED_TEST_ACCOUNTS",
                "configurationRef": f"cfg_mock_{prefix}",
                "allowedOrigins": ["https://staging.example.test"],
                "requiredCapabilities": ["auth.password"],
            },
        )
        assert connector_response.status_code == 201, connector_response.text
        connector_id = connector_response.json()["id"]
        validated_connector = client.post(
            f"/v1/connector-installations/{connector_id}:validate",
            headers={**headers, "If-Match": connector_response.headers["etag"]},
        )
        assert validated_connector.status_code == 200, validated_connector.text
        account_request = {
            "connectorInstallationId": connector_id,
            "accountKey": "sales-01",
            "source": "ATLAS_MANAGED",
            "loginHintMasked": "sa***@example.test",
            "labels": {"region": "cn", "persona": "new_customer"},
            "credentials": [
                {
                    "authMethod": "PASSWORD",
                    "purpose": "LOGIN",
                    "secretRef": secret_ref,
                    "secretVersion": "v1",
                }
            ],
        }
        account_headers = {
            **headers,
            "Idempotency-Key": f"identity-account-{prefix}",
        }
        account_response = client.post(
            f"/v1/account-pools/{pool_id}/accounts",
            headers=account_headers,
            json=account_request,
        )
        assert account_response.status_code == 201, account_response.text
        assert secret_ref not in account_response.text
        assert "secretRef" not in account_response.text
        account = account_response.json()
        account_id = account["id"]
        assert account["authMethods"] == ["PASSWORD"]
        assert account["available"] is False
        assert account["availabilityReason"] == "LIFECYCLE_NOT_ACTIVE"

        replayed_account = client.post(
            f"/v1/account-pools/{pool_id}/accounts",
            headers=account_headers,
            json=account_request,
        )
        assert replayed_account.status_code == 201
        assert replayed_account.json() == account

        capacity = client.get(
            f"/v1/account-pools/{pool_id}/capacity",
            headers=headers,
        )
        assert capacity.status_code == 200
        assert capacity.json() == {
            "poolId": pool_id,
            "totalSlots": 1,
            "availableSlots": 0,
            "leasedSlots": 0,
            "cooldownAccounts": 0,
            "quarantinedAccounts": 0,
            "unverifiedAccounts": 1,
        }

        activated = client.patch(
            f"/v1/test-accounts/{account_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"lifecycleStatus": "ACTIVE"},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["availabilityReason"] == "HEALTH_NOT_HEALTHY"

        quarantined = client.post(
            f"/v1/test-accounts/{account_id}:quarantine",
            headers={**headers, "If-Match": '"revision-2"'},
            json={"reason": "连续登录失败"},
        )
        assert quarantined.status_code == 200
        assert quarantined.json()["healthStatus"] == "QUARANTINED"
        restored = client.post(
            f"/v1/test-accounts/{account_id}:restore",
            headers={**headers, "If-Match": '"revision-3"'},
            json={"reason": "人工复核完成"},
        )
        assert restored.status_code == 200
        assert restored.json()["healthStatus"] == "UNKNOWN"
        assert restored.json()["operationalStatus"] == "VERIFYING"

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            stored_credential = connection.execute(
                """
                select secret_ref from atlas.credential_binding
                where account_id = %s
                """,
                (UUID(account_id),),
            ).fetchone()
            assert stored_credential == (secret_ref,)
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
            event_payloads = [
                str(row[0])
                for row in connection.execute(
                    """
                    select payload from atlas.audit_event
                    where tenant_id = %s and entity_id = %s
                    union all
                    select payload from atlas.outbox_event
                    where tenant_id = %s and aggregate_id = %s
                    """,
                    (
                        UUID(tenant_id),
                        UUID(account_id),
                        UUID(tenant_id),
                        UUID(account_id),
                    ),
                ).fetchall()
            ]
            assert all(secret_ref not in payload for payload in event_payloads)

        verified = client.get(f"/v1/test-accounts/{account_id}", headers=headers)
        assert verified.status_code == 200
        assert verified.json()["available"] is True
        assert verified.json()["availabilityReason"] == "AVAILABLE"
        verified_capacity = client.get(
            f"/v1/account-pools/{pool_id}/capacity",
            headers=headers,
        )
        assert verified_capacity.json()["availableSlots"] == 1

        disabled_role = client.patch(
            f"/v1/test-roles/{role_id}",
            headers={**headers, "If-Match": '"revision-2"'},
            json={"status": "DISABLED"},
        )
        assert disabled_role.status_code == 200
        disabled_account = client.get(
            f"/v1/test-accounts/{account_id}",
            headers=headers,
        )
        assert disabled_account.json()["available"] is False
        assert disabled_account.json()["availabilityReason"] == "POOL_DISABLED"
        disabled_capacity = client.get(
            f"/v1/account-pools/{pool_id}/capacity",
            headers=headers,
        )
        assert disabled_capacity.json()["availableSlots"] == 0

        other_tenant_id, _, _ = create_workspace(client, uuid7().hex[-10:])
        cross_tenant = client.get(
            f"/v1/test-accounts/{account_id}",
            headers=actor_headers(other_tenant_id),
        )
        assert cross_tenant.status_code == 404

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, other_tenant_id)
            hidden = connection.execute(
                "select count(*) from atlas.test_account where id = %s",
                (UUID(account_id),),
            ).fetchone()
            assert hidden == (0,)
