"""平台登录、Session、RBAC 与数据库安全边界集成测试。"""

from datetime import UTC, datetime
from hashlib import sha256
from os import environ
from typing import cast
from uuid import UUID, uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr

from atlas_testops.core.config import Settings
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")
TRUSTED_ORIGIN = "https://atlas.example.test"
PASSWORD = "correct horse battery staple"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(DATABASE_URL is None, reason="ATLAS_TEST_DATABASE_URL is not configured"),
]


def create_test_app(*, maximum_failures: int = 5) -> TestClient:
    """创建连接真实 PostgreSQL、允许固定测试 Origin 的客户端。"""

    assert DATABASE_URL is not None
    application = create_app(
        Settings(
            environment="test",
            cors_origins=[TRUSTED_ORIGIN],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=4,
            password_max_failures=maximum_failures,
        )
    )
    return TestClient(application)


def bootstrap_workspace(client: TestClient) -> tuple[str, str, str]:
    """通过公开 API 创建一个 Tenant 和两个 Project。"""

    unique = uuid7().hex[-12:]
    tenant_response = client.post(
        "/v1/tenants",
        json={"slug": f"auth-{unique}", "name": f"Auth Tenant {unique}"},
    )
    assert tenant_response.status_code == 201, tenant_response.text
    tenant_id = cast(str, tenant_response.json()["id"])
    headers = {
        "X-Atlas-Tenant-ID": tenant_id,
        "X-Atlas-Actor-ID": str(uuid7()),
    }

    project_ids: list[str] = []
    for index in (1, 2):
        project_response = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": f"auth-project-{unique}-{index}"},
            json={
                "projectKey": f"AUTH_{unique.upper()}_{index}",
                "name": f"Auth Project {index}",
            },
        )
        assert project_response.status_code == 201, project_response.text
        project_ids.append(cast(str, project_response.json()["id"]))
    return tenant_id, project_ids[0], project_ids[1]


def bootstrap_principal(
    client: TestClient,
    *,
    tenant_id: str,
    project_id: str,
    email: str,
) -> dict[str, object]:
    """创建测试用平台组织管理员。"""

    response = client.post(
        "/v1/auth/bootstrap",
        json={
            "tenantId": tenant_id,
            "projectId": project_id,
            "email": email,
            "displayName": "Atlas Owner",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def login(
    client: TestClient,
    *,
    tenant_id: str,
    project_id: str,
    email: str,
    password: str = PASSWORD,
    remember: bool = True,
) -> Response:
    """使用可信 Origin 提交账号密码登录。"""

    return client.post(
        "/v1/auth/login",
        headers={"Origin": TRUSTED_ORIGIN},
        json={
            "tenantId": tenant_id,
            "projectId": project_id,
            "email": email,
            "password": password,
            "remember": remember,
        },
    )


def set_tenant(connection: psycopg.Connection[tuple[object, ...]], tenant_id: str) -> None:
    """为直接数据库断言设置事务级 Tenant RLS Context。"""

    connection.execute(
        "select set_config('atlas.tenant_id', %s, true)",
        (tenant_id,),
    )


def test_password_login_cookie_session_logout_and_storage_boundaries() -> None:
    """验证登录闭环、Opaque Cookie、RLS、审计和服务端撤销。"""

    assert DATABASE_URL is not None
    with create_test_app() as client:
        tenant_id, project_id, _ = bootstrap_workspace(client)
        email = f"owner-{uuid7().hex[-12:]}@example.com"
        principal = bootstrap_principal(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=email,
        )
        user = cast(dict[str, object], principal["user"])
        user_id = cast(str, user["id"])

        duplicate = client.post(
            "/v1/auth/bootstrap",
            json={
                "tenantId": tenant_id,
                "projectId": project_id,
                "email": email.upper(),
                "displayName": "Duplicate",
                "password": PASSWORD,
            },
        )
        assert duplicate.status_code == 409

        rejected = login(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=email,
            password="definitely incorrect",
        )
        assert rejected.status_code == 401
        assert rejected.json()["errorCode"] == "AUTHENTICATION_FAILED"
        assert "set-cookie" not in rejected.headers

        accepted = login(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=email.upper(),
        )
        assert accepted.status_code == 200, accepted.text
        session_view = accepted.json()
        assert session_view["user"]["id"] == user_id
        assert session_view["roles"] == ["ORG_ADMIN"]
        assert session_view["authenticationMethod"] == "PASSWORD"
        assert "token" not in session_view
        assert PASSWORD not in accepted.text

        set_cookie = accepted.headers["set-cookie"]
        assert "atlas_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Max-Age=" in set_cookie
        token = client.cookies.get("atlas_session")
        assert token is not None
        token_hash = sha256(token.encode()).hexdigest()

        current = client.get("/v1/session")
        assert current.status_code == 200
        assert current.json() == session_view

        visible_projects = client.get("/v1/projects")
        assert visible_projects.status_code == 200
        assert len(visible_projects.json()["items"]) == 2

        created = client.post(
            "/v1/projects",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "Idempotency-Key": f"session-project-{uuid7()}",
            },
            json={"projectKey": "SESSION_PROJECT", "name": "Session Project"},
        )
        assert created.status_code == 201, created.text

        untrusted = client.post(
            "/v1/projects",
            headers={
                "Origin": "https://evil.example",
                "Idempotency-Key": f"evil-project-{uuid7()}",
            },
            json={"projectKey": "EVIL_PROJECT", "name": "Must Not Exist"},
        )
        assert untrusted.status_code == 403
        assert untrusted.json()["errorCode"] == "FORBIDDEN"

        with psycopg.connect(DATABASE_URL) as connection:
            credential = connection.execute(
                "select password_hash from atlas.password_credential where user_id = %s",
                (UUID(user_id),),
            ).fetchone()
            assert credential is not None
            password_hash = cast(str, credential[0])
            assert password_hash.startswith("$argon2id$")
            assert PASSWORD not in password_hash

            hidden_sessions = connection.execute(
                "select count(*) from atlas.platform_session where user_id = %s",
                (UUID(user_id),),
            ).fetchone()
            assert hidden_sessions == (0,)

            connection.execute(
                "select set_config('atlas.session_hash', %s, true)",
                (token_hash,),
            )
            session = connection.execute(
                "select token_hash, user_agent_hash from atlas.platform_session"
            ).fetchone()
            assert session is not None
            assert session[0] == token_hash
            assert session[0] != token
            assert session[1] is not None

            with pytest.raises(
                psycopg.errors.InsufficientPrivilege
            ), connection.transaction():
                connection.execute(
                    "update atlas.platform_session set user_id = %s where token_hash = %s",
                    (UUID(user_id), token_hash),
                )

        logout = client.post("/v1/auth/logout", headers={"Origin": TRUSTED_ORIGIN})
        assert logout.status_code == 204
        assert client.cookies.get("atlas_session") is None

        client.cookies.set("atlas_session", token)
        revoked = client.get("/v1/session")
        assert revoked.status_code == 401
        assert revoked.json()["errorCode"] == "AUTHENTICATION_REQUIRED"
        client.cookies.clear()

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            persisted = connection.execute(
                "select revoked_at from atlas.platform_session where token_hash = %s",
                (token_hash,),
            ).fetchone()
            assert persisted is not None and persisted[0] is not None
            audit_events = {
                cast(str, row[0])
                for row in connection.execute(
                    "select event_type from atlas.audit_event where tenant_id = %s",
                    (UUID(tenant_id),),
                ).fetchall()
            }
            outbox_events = {
                cast(str, row[0])
                for row in connection.execute(
                    "select event_type from atlas.outbox_event where tenant_id = %s",
                    (UUID(tenant_id),),
                ).fetchall()
            }
        assert {
            "platform_user.bootstrapped",
            "platform_login.rejected",
            "platform_session.created",
            "platform_session.revoked",
        } <= audit_events
        assert {
            "platform_user.bootstrapped",
            "platform_session.created",
            "platform_session.revoked",
        } <= outbox_events


def test_observer_scope_and_membership_revocation_invalidate_session() -> None:
    """验证 Project 范围授权、隐藏越权资源以及实时撤销。"""

    assert DATABASE_URL is not None
    with create_test_app() as client:
        tenant_id, project_id, other_project_id = bootstrap_workspace(client)
        email = f"observer-{uuid7().hex[-12:]}@example.com"
        principal = bootstrap_principal(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=email,
        )
        user_id = cast(str, cast(dict[str, object], principal["user"])["id"])

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            connection.execute(
                """
                update atlas.platform_membership
                set role = 'OBSERVER', project_id = %s, revision = revision + 1
                where tenant_id = %s and user_id = %s and status = 'ACTIVE'
                """,
                (UUID(project_id), UUID(tenant_id), UUID(user_id)),
            )

        response = login(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=email,
            remember=False,
        )
        assert response.status_code == 200, response.text
        assert response.json()["roles"] == ["OBSERVER"]
        assert "Max-Age=" not in response.headers["set-cookie"]
        token = client.cookies.get("atlas_session")
        assert token is not None
        token_hash = sha256(token.encode()).hexdigest()

        visible = client.get("/v1/projects")
        assert visible.status_code == 200
        assert [item["id"] for item in visible.json()["items"]] == [project_id]
        assert client.get(f"/v1/projects/{other_project_id}").status_code == 404

        create_forbidden = client.post(
            "/v1/projects",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "Idempotency-Key": f"observer-project-{uuid7()}",
            },
            json={"projectKey": "OBSERVER_PROJECT", "name": "Forbidden"},
        )
        assert create_forbidden.status_code == 403
        update_forbidden = client.patch(
            f"/v1/projects/{project_id}",
            headers={"Origin": TRUSTED_ORIGIN, "If-Match": '"revision-1"'},
            json={"name": "Forbidden Change"},
        )
        assert update_forbidden.status_code == 403

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            connection.execute(
                """
                update atlas.platform_membership
                set status = 'REVOKED', revision = revision + 1
                where tenant_id = %s and user_id = %s and status = 'ACTIVE'
                """,
                (UUID(tenant_id), UUID(user_id)),
            )

        invalidated = client.get("/v1/session")
        assert invalidated.status_code == 401
        assert invalidated.json()["errorCode"] == "AUTHENTICATION_REQUIRED"

        with psycopg.connect(DATABASE_URL) as connection:
            set_tenant(connection, tenant_id)
            revoked_at = connection.execute(
                "select revoked_at from atlas.platform_session where token_hash = %s",
                (token_hash,),
            ).fetchone()
            assert revoked_at is not None and revoked_at[0] is not None


def test_repeated_password_failures_lock_the_credential_without_enumeration() -> None:
    """验证失败计数、临时锁定和统一登录错误。"""

    assert DATABASE_URL is not None
    with create_test_app(maximum_failures=3) as client:
        tenant_id, project_id, _ = bootstrap_workspace(client)
        email = f"locked-{uuid7().hex[-12:]}@example.com"
        principal = bootstrap_principal(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=email,
        )
        user_id = cast(str, cast(dict[str, object], principal["user"])["id"])

        errors: list[dict[str, object]] = []
        for attempt in range(3):
            rejected = login(
                client,
                tenant_id=tenant_id,
                project_id=project_id,
                email=email,
                password=f"wrong-password-{attempt}",
            )
            assert rejected.status_code == 401
            errors.append(cast(dict[str, object], rejected.json()))

        locked = login(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=email,
        )
        assert locked.status_code == 401
        errors.append(cast(dict[str, object], locked.json()))
        assert {error["errorCode"] for error in errors} == {"AUTHENTICATION_FAILED"}
        assert len({error["detail"] for error in errors}) == 1

        unknown = login(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=f"unknown-{uuid7()}@example.com",
            password="wrong-password",
        )
        assert unknown.status_code == 401
        assert unknown.json()["detail"] == errors[0]["detail"]

        with psycopg.connect(DATABASE_URL) as connection:
            credential = connection.execute(
                """
                select failed_attempts, locked_until
                from atlas.password_credential
                where user_id = %s
                """,
                (UUID(user_id),),
            ).fetchone()
            assert credential is not None
            assert credential[0] == 3
            assert cast(datetime, credential[1]) > datetime.now(UTC)
