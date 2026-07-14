"""真实 PostgreSQL 上的 Migration、RLS 与事实不可变性测试。"""

from datetime import UTC, datetime, timedelta
from os import environ
from uuid import uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from atlas_testops.core.config import Settings
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(DATABASE_URL is None, reason="ATLAS_TEST_DATABASE_URL is not configured"),
]


def set_tenant(connection: psycopg.Connection[tuple[object, ...]], tenant_id: object) -> None:
    """在测试事务中切换可信 Tenant 上下文。"""

    connection.execute(
        "select set_config('atlas.tenant_id', %s, true)",
        (str(tenant_id),),
    )


def test_rls_isolation_and_append_only_audit() -> None:
    assert DATABASE_URL is not None
    connection = psycopg.connect(DATABASE_URL)
    tenant_a = uuid7()
    tenant_b = uuid7()
    project_a = uuid7()
    project_b = uuid7()
    audit_id = uuid7()
    now = datetime.now(UTC)

    try:
        set_tenant(connection, tenant_a)
        connection.execute(
            "insert into atlas.tenant (id, slug, name) values (%s, %s, %s)",
            (tenant_a, f"tenant-{tenant_a.hex[-12:]}", "Tenant A"),
        )
        connection.execute(
            """
            insert into atlas.project (id, tenant_id, project_key, name)
            values (%s, %s, %s, %s)
            """,
            (project_a, tenant_a, "PROJECT_A", "Project A"),
        )

        set_tenant(connection, tenant_b)
        connection.execute(
            "insert into atlas.tenant (id, slug, name) values (%s, %s, %s)",
            (tenant_b, f"tenant-{tenant_b.hex[-12:]}", "Tenant B"),
        )
        connection.execute(
            """
            insert into atlas.project (id, tenant_id, project_key, name)
            values (%s, %s, %s, %s)
            """,
            (project_b, tenant_b, "PROJECT_B", "Project B"),
        )

        set_tenant(connection, tenant_a)
        visible_projects = connection.execute(
            "select id from atlas.project order by id"
        ).fetchall()
        assert visible_projects == [(project_a,)]

        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute(
                """
                insert into atlas.project (id, tenant_id, project_key, name)
                values (%s, %s, %s, %s)
                """,
                (uuid7(), tenant_b, "CROSS_TENANT", "Cross Tenant"),
            )

        connection.execute(
            """
            insert into atlas.audit_event (
              id, tenant_id, project_id, event_type, entity_type,
              entity_id, occurred_at, payload, request_id
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                audit_id,
                tenant_a,
                project_a,
                "project.created",
                "project",
                project_a,
                now,
                Jsonb({"source": "integration-test"}),
                "integration-request",
            ),
        )
        with pytest.raises(
            psycopg.errors.ObjectNotInPrerequisiteState
        ), connection.transaction():
            connection.execute(
                "update atlas.audit_event set occurred_at = %s where id = %s",
                (now + timedelta(seconds=1), audit_id),
            )
    finally:
        connection.rollback()
        connection.close()


@pytest.mark.anyio
async def test_async_pool_applies_transaction_context() -> None:
    assert DATABASE_URL is not None
    database = Database(
        Settings(
            environment="test",
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=2,
        )
    )
    tenant_id = uuid7()
    tenant_slug = f"tenant-{tenant_id.hex[-12:]}"

    await database.open()
    try:
        async with database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            await connection.execute(
                "insert into atlas.tenant (id, slug, name) values (%s, %s, %s)",
                (tenant_id, tenant_slug, "Async Tenant"),
            )

        async with database.transaction(DatabaseContext(tenant_id=tenant_id)) as connection:
            row = await (
                await connection.execute("select id from atlas.tenant where id = %s", (tenant_id,))
            ).fetchone()
            assert row == {"id": tenant_id}
            await connection.execute("delete from atlas.tenant where id = %s", (tenant_id,))
    finally:
        await database.close()


def test_api_readiness_uses_real_pool() -> None:
    assert DATABASE_URL is not None
    app = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=2,
        )
    )

    with TestClient(app) as client:
        response = client.get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"] == [{"name": "database", "status": "ready"}]


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.test:99999",
        "http://example.test:0",
        "HTTPS://example.test",
        "https://example..test",
        "https://-example.test",
        "https://example.test:443",
    ],
)
def test_database_rejects_noncanonical_http_origins(origin: str) -> None:
    assert DATABASE_URL is not None

    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "select atlas.valid_http_origins(%s::text[])",
            ([origin],),
        ).fetchone()

    assert row == (False,)


def test_database_accepts_canonical_http_origins() -> None:
    assert DATABASE_URL is not None

    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "select atlas.valid_http_origins(%s::text[])",
            (["http://127.0.0.1:8080", "https://[2001:db8::1]"],),
        ).fetchone()

    assert row == (True,)
