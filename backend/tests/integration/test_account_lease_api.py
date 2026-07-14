"""测试账号租约内部 API 的真实 PostgreSQL 闭环。"""

from datetime import UTC, datetime, timedelta
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
        json={"slug": f"lease-{prefix}", "name": f"Lease {prefix}"},
    )
    assert tenant_response.status_code == 201, tenant_response.text
    tenant_id = cast(str, tenant_response.json()["id"])
    headers = actor_headers(tenant_id)
    project_response = client.post(
        "/v1/projects",
        headers={**headers, "Idempotency-Key": f"lease-project-{prefix}"},
        json={"projectKey": f"LEASE_{prefix.upper()}", "name": "Lease Project"},
    )
    assert project_response.status_code == 201, project_response.text
    project_id = cast(str, project_response.json()["id"])
    environment_response = client.post(
        f"/v1/projects/{project_id}/environments",
        headers={**headers, "Idempotency-Key": f"lease-environment-{prefix}"},
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


def seed_account_pool(
    client: TestClient,
    *,
    tenant_id: str,
    project_id: str,
    environment_id: str,
    prefix: str,
) -> tuple[str, str, str]:
    headers = actor_headers(tenant_id)
    connector_response = client.post(
        "/v1/connector-installations",
        headers={**headers, "Idempotency-Key": f"lease-connector-{prefix}"},
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
    assert "configurationRef" not in connector_response.text
    connector_id = cast(str, connector_response.json()["id"])
    validated_connector = client.post(
        f"/v1/connector-installations/{connector_id}:validate",
        headers={**headers, "If-Match": connector_response.headers["etag"]},
    )
    assert validated_connector.status_code == 200, validated_connector.text
    assert validated_connector.json()["status"] == "ACTIVE"
    role_response = client.post(
        f"/v1/projects/{project_id}/test-roles",
        headers={**headers, "Idempotency-Key": f"lease-role-{prefix}"},
        json={
            "roleKey": "sales",
            "name": "销售",
            "description": "租约集成角色",
            "capabilities": ["customer.read", "visit:create"],
        },
    )
    assert role_response.status_code == 201, role_response.text
    role_id = cast(str, role_response.json()["id"])
    pool_response = client.post(
        f"/v1/environments/{environment_id}/account-pools",
        headers={**headers, "Idempotency-Key": f"lease-pool-{prefix}"},
        json={
            "roleId": role_id,
            "poolKey": "sales-cn",
            "name": "销售账号池",
            "defaultTtlSeconds": 300,
            "cooldownSeconds": 0,
        },
    )
    assert pool_response.status_code == 201, pool_response.text
    pool_id = cast(str, pool_response.json()["id"])
    secret_ref = f"sec_lease_{prefix}"
    account_response = client.post(
        f"/v1/account-pools/{pool_id}/accounts",
        headers={**headers, "Idempotency-Key": f"lease-account-{prefix}"},
        json={
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
        },
    )
    assert account_response.status_code == 201, account_response.text
    account_id = cast(str, account_response.json()["id"])
    assert secret_ref not in account_response.text
    return pool_id, account_id, secret_ref


def make_acquire_body(
    *,
    environment_id: str,
    execution_id: str,
    deadline: datetime,
) -> dict[str, object]:
    return {
        "executionId": execution_id,
        "workerId": "worker-integration-01",
        "environmentId": environment_id,
        "roleKey": "sales",
        "requirements": {
            "tags": ["region:cn"],
            "authMethods": ["PASSWORD"],
            "capabilities": ["customer.read"],
        },
        "ttlSeconds": 300,
        "executionDeadline": deadline.isoformat(),
    }


def set_tenant(connection: psycopg.Connection[tuple[object, ...]], tenant_id: str) -> None:
    connection.execute("select set_config('atlas.tenant_id', %s, true)", (tenant_id,))


def test_account_lease_api_fencing_idempotency_and_safe_projection() -> None:
    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=8,
        )
    )
    now = datetime.now(UTC)

    with TestClient(app) as client:
        tenant_id, project_id, environment_id = create_workspace(client, prefix)
        pool_id, account_id, secret_ref = seed_account_pool(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            environment_id=environment_id,
            prefix=prefix,
        )
        headers = actor_headers(tenant_id)
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

        acquire_body = make_acquire_body(
            environment_id=environment_id,
            execution_id=f"execution-{prefix}-01",
            deadline=now + timedelta(hours=2),
        )
        acquire_headers = {
            **headers,
            "Idempotency-Key": f"lease-acquire-{prefix}-01",
        }
        acquired = client.post(
            "/internal/v1/account-leases",
            headers=acquire_headers,
            json=acquire_body,
        )
        assert acquired.status_code == 201, acquired.text
        assert acquired.headers["idempotency-replayed"] == "false"
        lease = acquired.json()
        assert set(lease) == {
            "leaseId",
            "accountHandle",
            "fencingToken",
            "status",
            "heartbeatAfterSeconds",
            "expiresAt",
        }
        assert lease["status"] == "ACTIVE"
        assert lease["accountHandle"].startswith("ah_")
        assert account_id not in acquired.text
        assert secret_ref not in acquired.text
        assert "accountId" not in acquired.text
        assert "slotId" not in acquired.text
        lease_id = cast(str, lease["leaseId"])
        first_fence = cast(int, lease["fencingToken"])

        replayed = client.post(
            "/internal/v1/account-leases",
            headers=acquire_headers,
            json=acquire_body,
        )
        assert replayed.status_code == 201
        assert replayed.json() == lease
        assert replayed.headers["idempotency-replayed"] == "true"

        capacity = client.get(
            f"/v1/account-pools/{pool_id}/capacity",
            headers=headers,
        )
        assert capacity.status_code == 200
        assert capacity.json()["availableSlots"] == 0
        assert capacity.json()["leasedSlots"] == 1
        leased_account = client.get(f"/v1/test-accounts/{account_id}", headers=headers)
        assert leased_account.json()["availabilityReason"] == "LEASED"

        wrong_fence = client.post(
            f"/internal/v1/account-leases/{lease_id}:heartbeat",
            headers=headers,
            json={"fencingToken": first_fence + 1},
        )
        assert wrong_fence.status_code == 409
        assert wrong_fence.json()["errorCode"] == "LEASE_FENCED"
        heartbeat = client.post(
            f"/internal/v1/account-leases/{lease_id}:heartbeat",
            headers=headers,
            json={"fencingToken": first_fence},
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["fencingToken"] == first_fence

        released = client.post(
            f"/internal/v1/account-leases/{lease_id}:release",
            headers=headers,
            json={"fencingToken": first_fence, "reason": "COMPLETED"},
        )
        assert released.status_code == 200, released.text
        assert released.json()["status"] == "RELEASED"
        assert released.headers["idempotency-replayed"] == "false"
        released_again = client.post(
            f"/internal/v1/account-leases/{lease_id}:release",
            headers=headers,
            json={"fencingToken": first_fence, "reason": "CANCELLED"},
        )
        assert released_again.status_code == 200
        assert released_again.json() == released.json()
        assert released_again.headers["idempotency-replayed"] == "true"

        second_body = make_acquire_body(
            environment_id=environment_id,
            execution_id=f"execution-{prefix}-02",
            deadline=now + timedelta(hours=2),
        )
        second = client.post(
            "/internal/v1/account-leases",
            headers={
                **headers,
                "Idempotency-Key": f"lease-acquire-{prefix}-02",
            },
            json=second_body,
        )
        assert second.status_code == 201, second.text
        second_lease = second.json()
        assert second_lease["fencingToken"] == first_fence + 1

        old_release_retry = client.post(
            f"/internal/v1/account-leases/{lease_id}:release",
            headers=headers,
            json={"fencingToken": first_fence, "reason": "COMPLETED"},
        )
        assert old_release_retry.status_code == 200
        active_capacity = client.get(
            f"/v1/account-pools/{pool_id}/capacity",
            headers=headers,
        )
        assert active_capacity.json()["leasedSlots"] == 1

        cleanup_failed = client.post(
            f"/internal/v1/account-leases/{second_lease['leaseId']}:release",
            headers=headers,
            json={
                "fencingToken": second_lease["fencingToken"],
                "reason": "CLEANUP_FAILED",
            },
        )
        assert cleanup_failed.status_code == 200
        quarantined = client.get(f"/v1/test-accounts/{account_id}", headers=headers)
        assert quarantined.json()["healthStatus"] == "QUARANTINED"
        assert quarantined.json()["operationalStatus"] == "CLEANUP_FAILED"

        incompatible = client.post(
            "/internal/v1/account-leases",
            headers={
                **headers,
                "Idempotency-Key": f"lease-acquire-{prefix}-capability",
            },
            json={
                **make_acquire_body(
                    environment_id=environment_id,
                    execution_id=f"execution-{prefix}-03",
                    deadline=now + timedelta(hours=2),
                ),
                "requirements": {"capabilities": ["admin:all"]},
            },
        )
        assert incompatible.status_code == 422
        assert incompatible.json()["errorCode"] == "CONSTRAINT_UNSATISFIED"

        other_tenant_id, _, _ = create_workspace(client, uuid7().hex[-10:])
        hidden = client.get(
            f"/internal/v1/account-leases/{lease_id}",
            headers=actor_headers(other_tenant_id),
        )
        assert hidden.status_code == 404

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            payloads = [
                str(row[0])
                for row in connection.execute(
                    """
                    select payload from atlas.audit_event
                    where tenant_id = %s and entity_type = 'account_lease'
                    union all
                    select payload from atlas.outbox_event
                    where tenant_id = %s and aggregate_type = 'account_lease'
                    """,
                    (UUID(tenant_id), UUID(tenant_id)),
                ).fetchall()
            ]
            assert payloads
            assert all(secret_ref not in payload for payload in payloads)
            with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
                connection.execute(
                    """
                    update atlas.account_lease
                    set heartbeat_at = heartbeat_at + interval '1 second',
                        revision = revision + 1
                    where id = %s
                    """,
                    (UUID(lease_id),),
                )


def test_secret_grant_api_returns_only_one_time_ref_and_never_persists_it() -> None:
    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=8,
        )
    )
    now = datetime.now(UTC)

    with TestClient(app) as client:
        tenant_id, project_id, environment_id = create_workspace(client, prefix)
        _, account_id, secret_ref = seed_account_pool(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            environment_id=environment_id,
            prefix=prefix,
        )
        headers = actor_headers(tenant_id)
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
            headers={**headers, "Idempotency-Key": f"grant-acquire-{prefix}"},
            json=make_acquire_body(
                environment_id=environment_id,
                execution_id=f"execution-{prefix}-grant",
                deadline=now + timedelta(hours=2),
            ),
        )
        assert acquired.status_code == 201, acquired.text
        lease = acquired.json()
        issued = client.post(
            f"/internal/v1/account-leases/{lease['leaseId']}:issue-secret-grant",
            headers=headers,
            json={
                "fencingToken": lease["fencingToken"],
                "purpose": "LOGIN",
                "workerIdentity": "worker-integration-01",
                "allowedOrigins": ["https://staging.example.test"],
            },
        )
        assert issued.status_code == 201, issued.text
        assert issued.headers["cache-control"] == "no-store"
        assert issued.headers["pragma"] == "no-cache"
        assert set(issued.json()) == {"grantRef", "expiresAt", "maxRedemptions"}
        grant_ref = cast(str, issued.json()["grantRef"])
        assert grant_ref.startswith("sgr_")
        assert issued.json()["maxRedemptions"] == 1
        assert secret_ref not in issued.text
        assert account_id not in issued.text

        other_tenant_id, _, _ = create_workspace(client, uuid7().hex[-10:])
        hidden = client.post(
            f"/internal/v1/account-leases/{lease['leaseId']}:issue-secret-grant",
            headers=actor_headers(other_tenant_id),
            json={
                "fencingToken": lease["fencingToken"],
                "purpose": "LOGIN",
                "workerIdentity": "worker-integration-01",
                "allowedOrigins": ["https://staging.example.test"],
            },
        )
        assert hidden.status_code == 404

        released = client.post(
            f"/internal/v1/account-leases/{lease['leaseId']}:release",
            headers=headers,
            json={"fencingToken": lease["fencingToken"], "reason": "COMPLETED"},
        )
        assert released.status_code == 200

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            grant_row = connection.execute(
                """
                select id, token_hash, status, termination_reason
                from atlas.secret_grant
                where lease_id = %s
                """,
                (UUID(cast(str, lease["leaseId"])),),
            ).fetchone()
            assert grant_row is not None
            assert grant_row[1] != grant_ref
            assert len(cast(str, grant_row[1])) == 64
            assert grant_row[2:] == ("REVOKED", "LEASE_TERMINATED")
            payloads = [
                str(row[0])
                for row in connection.execute(
                    """
                    select payload from atlas.audit_event
                    where entity_type = 'secret_grant'
                    union all
                    select payload from atlas.outbox_event
                    where aggregate_type = 'secret_grant'
                    """
                ).fetchall()
            ]
            assert payloads
            assert all(grant_ref not in payload for payload in payloads)
            assert all(secret_ref not in payload for payload in payloads)
            with pytest.raises(psycopg.errors.RaiseException), connection.transaction():
                connection.execute(
                    """
                    update atlas.secret_grant
                    set status = 'EXPIRED', terminated_at = clock_timestamp(),
                        termination_reason = 'EXPIRED', revision = revision + 1
                    where id = %s
                    """,
                    (grant_row[0],),
                )


@pytest.mark.parametrize(
    ("management_action", "release_reason"),
    [
        ("quarantine", "ACCOUNT_QUARANTINED"),
        ("suspend", "ACCOUNT_SUSPENDED"),
        ("retire", "ACCOUNT_RETIRED"),
        ("disable_pool", "POOL_DISABLED"),
        ("disable_role", "ROLE_DISABLED"),
        ("disable_environment", "ENVIRONMENT_DISABLED"),
    ],
)
def test_management_state_change_revokes_active_lease_and_advances_fence(
    management_action: str,
    release_reason: str,
) -> None:
    assert DATABASE_URL is not None
    prefix = uuid7().hex[-10:]
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=8,
        )
    )
    now = datetime.now(UTC)

    with TestClient(app) as client:
        tenant_id, project_id, environment_id = create_workspace(client, prefix)
        pool_id, account_id, _ = seed_account_pool(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            environment_id=environment_id,
            prefix=prefix,
        )
        headers = actor_headers(tenant_id)
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
            headers={
                **headers,
                "Idempotency-Key": f"lease-management-{prefix}-{management_action}",
            },
            json=make_acquire_body(
                environment_id=environment_id,
                execution_id=f"execution-{prefix}-{management_action}",
                deadline=now + timedelta(hours=2),
            ),
        )
        assert acquired.status_code == 201, acquired.text
        lease = acquired.json()
        lease_id = cast(str, lease["leaseId"])
        fence = cast(int, lease["fencingToken"])

        if management_action in {"quarantine", "suspend", "retire"}:
            account = client.get(f"/v1/test-accounts/{account_id}", headers=headers)
            assert account.status_code == 200
            action_headers = {**headers, "If-Match": account.headers["etag"]}
            if management_action == "quarantine":
                changed = client.post(
                    f"/v1/test-accounts/{account_id}:quarantine",
                    headers=action_headers,
                    json={"reason": "lease management safety test"},
                )
            else:
                lifecycle_status = "SUSPENDED" if management_action == "suspend" else "RETIRING"
                changed = client.patch(
                    f"/v1/test-accounts/{account_id}",
                    headers=action_headers,
                    json={"lifecycleStatus": lifecycle_status},
                )
        elif management_action == "disable_pool":
            pool = client.get(f"/v1/account-pools/{pool_id}", headers=headers)
            assert pool.status_code == 200
            changed = client.patch(
                f"/v1/account-pools/{pool_id}",
                headers={**headers, "If-Match": pool.headers["etag"]},
                json={"status": "DISABLED"},
            )
        elif management_action == "disable_role":
            roles = client.get(
                f"/v1/projects/{project_id}/test-roles",
                headers=headers,
            )
            assert roles.status_code == 200
            role_id = cast(str, roles.json()["items"][0]["id"])
            role = client.get(f"/v1/test-roles/{role_id}", headers=headers)
            assert role.status_code == 200
            changed = client.patch(
                f"/v1/test-roles/{role_id}",
                headers={**headers, "If-Match": role.headers["etag"]},
                json={"status": "DISABLED"},
            )
        else:
            environment = client.get(
                f"/v1/environments/{environment_id}",
                headers=headers,
            )
            assert environment.status_code == 200
            changed = client.patch(
                f"/v1/environments/{environment_id}",
                headers={**headers, "If-Match": environment.headers["etag"]},
                json={"status": "DISABLED"},
            )
        assert changed.status_code == 200, changed.text

        revoked = client.get(
            f"/internal/v1/account-leases/{lease_id}",
            headers=headers,
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "REVOKED"
        fenced = client.post(
            f"/internal/v1/account-leases/{lease_id}:heartbeat",
            headers=headers,
            json={"fencingToken": fence},
        )
        assert fenced.status_code == 409
        assert fenced.json()["errorCode"] == "LEASE_FENCED"

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            lease_row = connection.execute(
                """
                select status, release_reason
                from atlas.account_lease
                where id = %s
                """,
                (UUID(lease_id),),
            ).fetchone()
            assert lease_row == ("REVOKED", release_reason)
            account_row = connection.execute(
                """
                select lease_epoch, operational_status
                from atlas.test_account
                where id = %s
                """,
                (UUID(account_id),),
            ).fetchone()
            assert account_row == (fence + 1, "VERIFYING")
            event_count = connection.execute(
                """
                select count(*)
                from atlas.audit_event
                where tenant_id = %s
                  and event_type = 'account_lease.revoked'
                  and payload ->> 'releaseReason' = %s
                """,
                (UUID(tenant_id), release_reason),
            ).fetchone()
            assert event_count == (1,)
