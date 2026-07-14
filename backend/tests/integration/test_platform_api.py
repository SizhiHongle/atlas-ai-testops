"""Platform API 在真实 PostgreSQL 上的端到端测试。"""

from collections import Counter
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


def tenant_headers(tenant_id: str) -> dict[str, str]:
    """构造开发期 Actor Header。"""

    return {
        "X-Atlas-Tenant-ID": tenant_id,
        "X-Atlas-Actor-ID": str(uuid7()),
    }


def bootstrap_tenant(client: TestClient, *, slug: str, name: str) -> dict[str, object]:
    """通过公开 Bootstrap API 创建测试 Tenant。"""

    response = client.post("/v1/tenants", json={"slug": slug, "name": name})
    assert response.status_code == 201, response.text
    assert response.headers["etag"] == '"revision-1"'
    return cast(dict[str, object], response.json())


def set_tenant_status(tenant_id: str, status: str) -> None:
    """模拟后续管理端用例改变 Tenant 状态。"""

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, true)",
            (tenant_id,),
        )
        connection.execute(
            """
            update atlas.tenant
            set status = %s, revision = revision + 1
            where id = %s
            """,
            (status, UUID(tenant_id)),
        )


def read_platform_facts(tenant_id: str) -> tuple[list[str], list[str], int]:
    """读取当前 Tenant 的审计、Outbox 和幂等完成事实。"""

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, true)",
            (tenant_id,),
        )
        audit_events = [
            row[0]
            for row in connection.execute(
                "select event_type from atlas.audit_event where tenant_id = %s",
                (UUID(tenant_id),),
            ).fetchall()
        ]
        outbox_events = [
            row[0]
            for row in connection.execute(
                "select event_type from atlas.outbox_event where tenant_id = %s",
                (UUID(tenant_id),),
            ).fetchall()
        ]
        completed_count = connection.execute(
            """
            select count(*)
            from atlas.idempotency_record
            where tenant_id = %s and state = 'COMPLETED'
            """,
            (UUID(tenant_id),),
        ).fetchone()
        assert completed_count is not None
        return audit_events, outbox_events, int(completed_count[0])


def test_platform_api_tenant_isolation_idempotency_and_revision() -> None:
    """证明 P1-01 的创建、查询、隔离、幂等和 CAS 闭环。"""

    assert DATABASE_URL is not None
    unique = uuid7().hex[-12:]
    tenant_slug_a = f"api-a-{unique}"
    tenant_slug_b = f"api-b-{unique}"
    application = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=4,
        )
    )

    with TestClient(application) as client:
        tenant_a = bootstrap_tenant(client, slug=tenant_slug_a, name="Tenant A")
        tenant_b = bootstrap_tenant(client, slug=tenant_slug_b, name="Tenant B")
        tenant_id_a = str(tenant_a["id"])
        tenant_id_b = str(tenant_b["id"])
        headers_a = tenant_headers(tenant_id_a)
        headers_b = tenant_headers(tenant_id_b)

        duplicate_tenant = client.post(
            "/v1/tenants",
            json={"slug": tenant_slug_a, "name": "Duplicate"},
        )
        assert duplicate_tenant.status_code == 409

        current = client.get("/v1/tenants/current", headers=headers_a)
        assert current.status_code == 200
        assert current.json()["id"] == tenant_id_a
        assert current.headers["etag"] == '"revision-1"'

        project_request = {"projectKey": "ATLAS_CORE", "name": "Atlas Core"}
        project_headers_a = {**headers_a, "Idempotency-Key": "project-create-a-1"}
        created_project = client.post(
            "/v1/projects",
            headers=project_headers_a,
            json=project_request,
        )
        assert created_project.status_code == 201, created_project.text
        assert created_project.headers["idempotency-replayed"] == "false"
        assert created_project.headers["etag"] == '"revision-1"'
        project_a = created_project.json()
        project_id_a = project_a["id"]

        replayed_project = client.post(
            "/v1/projects",
            headers=project_headers_a,
            json=project_request,
        )
        assert replayed_project.status_code == 201
        assert replayed_project.json() == project_a
        assert replayed_project.headers["idempotency-replayed"] == "true"

        idempotency_conflict = client.post(
            "/v1/projects",
            headers=project_headers_a,
            json={"projectKey": "DIFFERENT", "name": "Different"},
        )
        assert idempotency_conflict.status_code == 409
        assert idempotency_conflict.json()["errorCode"] == "CONFLICT"

        duplicate_project_key = client.post(
            "/v1/projects",
            headers={**headers_a, "Idempotency-Key": "project-duplicate-a"},
            json=project_request,
        )
        assert duplicate_project_key.status_code == 409

        second_project = client.post(
            "/v1/projects",
            headers={**headers_a, "Idempotency-Key": "project-create-a-2"},
            json={"projectKey": "ATLAS_AUX", "name": "Atlas Auxiliary"},
        )
        assert second_project.status_code == 201

        first_page = client.get("/v1/projects?limit=1", headers=headers_a)
        assert first_page.status_code == 200
        assert len(first_page.json()["items"]) == 1
        assert first_page.json()["nextCursor"] is not None
        second_page = client.get(
            "/v1/projects",
            headers=headers_a,
            params={"limit": 1, "cursor": first_page.json()["nextCursor"]},
        )
        assert second_page.status_code == 200
        assert len(second_page.json()["items"]) == 1
        assert second_page.json()["items"][0]["id"] != first_page.json()["items"][0]["id"]
        invalid_cursor = client.get("/v1/projects?cursor=%25%25%25", headers=headers_a)
        assert invalid_cursor.status_code == 400

        project_b_response = client.post(
            "/v1/projects",
            headers={**headers_b, "Idempotency-Key": "project-create-b-1"},
            json=project_request,
        )
        assert project_b_response.status_code == 201
        project_b = project_b_response.json()

        tenant_b_projects = client.get("/v1/projects", headers=headers_b)
        assert [item["id"] for item in tenant_b_projects.json()["items"]] == [project_b["id"]]
        cross_tenant_project = client.get(f"/v1/projects/{project_id_a}", headers=headers_b)
        assert cross_tenant_project.status_code == 404

        updated_project = client.patch(
            f"/v1/projects/{project_id_a}",
            headers={**headers_a, "If-Match": '"revision-1"'},
            json={"name": "Atlas Control Plane"},
        )
        assert updated_project.status_code == 200
        assert updated_project.json()["revision"] == 2
        assert updated_project.headers["etag"] == '"revision-2"'
        stale_project = client.patch(
            f"/v1/projects/{project_id_a}",
            headers={**headers_a, "If-Match": '"revision-1"'},
            json={"name": "Stale Change"},
        )
        assert stale_project.status_code == 412
        assert stale_project.headers["etag"] == '"revision-2"'
        malformed_etag = client.patch(
            f"/v1/projects/{project_id_a}",
            headers={**headers_a, "If-Match": "*"},
            json={"name": "Invalid Change"},
        )
        assert malformed_etag.status_code == 400

        environment_request = {
            "environmentKey": "dev-east",
            "name": "Dev East",
            "kind": "TEST",
            "allowedOrigins": ["HTTPS://staging.example.test:443/"],
        }
        environment_headers_a = {
            **headers_a,
            "Idempotency-Key": "environment-create-a-1",
        }
        created_environment = client.post(
            f"/v1/projects/{project_id_a}/environments",
            headers=environment_headers_a,
            json=environment_request,
        )
        assert created_environment.status_code == 201, created_environment.text
        assert created_environment.headers["idempotency-replayed"] == "false"
        environment_a = created_environment.json()
        environment_id_a = environment_a["id"]
        assert environment_a["allowedOrigins"] == ["https://staging.example.test"]

        replayed_environment = client.post(
            f"/v1/projects/{project_id_a}/environments",
            headers=environment_headers_a,
            json=environment_request,
        )
        assert replayed_environment.status_code == 201
        assert replayed_environment.json() == environment_a
        assert replayed_environment.headers["idempotency-replayed"] == "true"

        environment_idempotency_conflict = client.post(
            f"/v1/projects/{project_id_a}/environments",
            headers=environment_headers_a,
            json={**environment_request, "environmentKey": "different"},
        )
        assert environment_idempotency_conflict.status_code == 409
        duplicate_environment = client.post(
            f"/v1/projects/{project_id_a}/environments",
            headers={**headers_a, "Idempotency-Key": "environment-duplicate-a"},
            json=environment_request,
        )
        assert duplicate_environment.status_code == 409

        second_environment = client.post(
            f"/v1/projects/{project_id_a}/environments",
            headers={**headers_a, "Idempotency-Key": "environment-create-a-2"},
            json={
                "environmentKey": "stage-east",
                "name": "Stage East",
                "kind": "STAGING",
            },
        )
        assert second_environment.status_code == 201
        environment_page = client.get(
            f"/v1/projects/{project_id_a}/environments?limit=1",
            headers=headers_a,
        )
        assert environment_page.status_code == 200
        assert len(environment_page.json()["items"]) == 1
        assert environment_page.json()["nextCursor"] is not None

        project_id_b = project_b["id"]
        environment_b_response = client.post(
            f"/v1/projects/{project_id_b}/environments",
            headers={**headers_b, "Idempotency-Key": "environment-create-b-1"},
            json=environment_request,
        )
        assert environment_b_response.status_code == 201
        cross_tenant_environment = client.get(
            f"/v1/environments/{environment_id_a}",
            headers=headers_b,
        )
        assert cross_tenant_environment.status_code == 404
        cross_tenant_environment_list = client.get(
            f"/v1/projects/{project_id_a}/environments",
            headers=headers_b,
        )
        assert cross_tenant_environment_list.status_code == 404

        environment_detail = client.get(
            f"/v1/environments/{environment_id_a}",
            headers=headers_a,
        )
        assert environment_detail.status_code == 200
        assert environment_detail.headers["etag"] == '"revision-1"'
        updated_environment = client.patch(
            f"/v1/environments/{environment_id_a}",
            headers={**headers_a, "If-Match": '"revision-1"'},
            json={
                "status": "DISABLED",
                "allowedOrigins": ["http://127.0.0.1:8080/"],
            },
        )
        assert updated_environment.status_code == 200
        assert updated_environment.json()["revision"] == 2
        assert updated_environment.json()["allowedOrigins"] == [
            "http://127.0.0.1:8080"
        ]
        stale_environment = client.patch(
            f"/v1/environments/{environment_id_a}",
            headers={**headers_a, "If-Match": '"revision-1"'},
            json={"name": "Stale Environment"},
        )
        assert stale_environment.status_code == 412

        archived_project = client.patch(
            f"/v1/projects/{project_id_a}",
            headers={**headers_a, "If-Match": '"revision-2"'},
            json={"status": "ARCHIVED"},
        )
        assert archived_project.status_code == 200
        create_on_archived_project = client.post(
            f"/v1/projects/{project_id_a}/environments",
            headers={**headers_a, "Idempotency-Key": "environment-archived-a"},
            json={
                "environmentKey": "blocked",
                "name": "Blocked",
                "kind": "TEST",
            },
        )
        assert create_on_archived_project.status_code == 409

        set_tenant_status(tenant_id_a, "SUSPENDED")
        create_on_suspended_tenant = client.post(
            "/v1/projects",
            headers={**headers_a, "Idempotency-Key": "project-suspended-a"},
            json={"projectKey": "BLOCKED", "name": "Blocked"},
        )
        assert create_on_suspended_tenant.status_code == 409

    audit_events, outbox_events, completed_count = read_platform_facts(tenant_id_a)
    expected_events = Counter(
        {
            "tenant.created": 1,
            "project.created": 2,
            "project.updated": 2,
            "environment.created": 2,
            "environment.updated": 1,
        }
    )
    assert Counter(audit_events) == expected_events
    assert Counter(outbox_events) == expected_events
    assert completed_count == 4


def test_platform_cursor_indexes_exist() -> None:
    """证明 P1 列表查询所需 Migration 已应用。"""

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        index_names = {
            row[0]
            for row in connection.execute(
                """
                select indexname
                from pg_indexes
                where schemaname = 'atlas'
                  and indexname in (
                    'project_tenant_created_idx',
                    'environment_project_created_idx'
                  )
                """
            ).fetchall()
        }

    assert index_names == {
        "project_tenant_created_idx",
        "environment_project_created_idx",
    }
