"""Platform API 开发期身份边界测试。"""

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from atlas_testops.api.dependencies import get_database, get_fixture_run_dispatcher
from atlas_testops.api.security import require_trusted_origin
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.main import create_app


def request_with_origin(method: str, origin: str | None = None) -> Request:
    headers = [] if origin is None else [(b"origin", origin.encode())]
    return Request({"type": "http", "method": method, "headers": headers})


def test_platform_query_requires_development_actor() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))

    with TestClient(app) as client:
        response = client.get("/v1/projects")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["errorCode"] == "AUTHENTICATION_REQUIRED"


def test_production_rejects_bootstrap_headers_before_database_access() -> None:
    app = create_app(Settings(environment="production", cors_origins=[]))

    with TestClient(app) as client:
        response = client.get(
            "/v1/projects",
            headers={"X-Atlas-Tenant-ID": "019f0000-0000-7000-8000-000000000001"},
        )

    assert response.status_code == 403
    assert response.json()["errorCode"] == "FORBIDDEN"


def test_openapi_exposes_platform_contract() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))
    paths = app.openapi()["paths"]
    parameter_names = {
        parameter["name"] for parameter in paths["/v1/projects"]["post"]["parameters"]
    }

    assert "/v1/projects" in paths
    assert "/v1/projects/{projectId}/environments" in paths
    assert {"X-Atlas-Tenant-ID", "Idempotency-Key"} <= parameter_names


def test_cookie_writes_require_an_allowed_origin() -> None:
    settings = Settings(
        environment="production",
        cors_origins=["https://atlas.example.com"],
    )

    require_trusted_origin(
        request_with_origin("POST", "https://atlas.example.com/"),
        settings,
    )
    require_trusted_origin(request_with_origin("GET", "https://evil.example"), settings)

    with pytest.raises(ApplicationError) as missing_origin:
        require_trusted_origin(request_with_origin("POST"), settings)
    assert missing_origin.value.error_code is ErrorCode.FORBIDDEN

    with pytest.raises(ApplicationError) as untrusted_origin:
        require_trusted_origin(
            request_with_origin("POST", "https://evil.example"),
            settings,
        )
    assert untrusted_origin.value.error_code is ErrorCode.FORBIDDEN


def test_runtime_dependencies_fail_closed_when_not_configured() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))
    request = Request({"type": "http", "app": app})

    with pytest.raises(RuntimeError, match="database is not configured"):
        get_database(request)
    with pytest.raises(ApplicationError) as unavailable:
        get_fixture_run_dispatcher(request)
    assert unavailable.value.error_code is ErrorCode.DEPENDENCY_UNAVAILABLE
