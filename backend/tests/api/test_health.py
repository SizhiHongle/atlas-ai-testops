"""健康检查接口测试。"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from atlas_testops.api.dependencies import get_optional_database
from atlas_testops.core.config import Settings
from atlas_testops.infrastructure.database import Database
from atlas_testops.main import create_app


def test_liveness_reports_service_metadata() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))

    with TestClient(app) as client:
        response = client.get("/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "Atlas TestOps Backend",
        "version": "0.1.0",
        "environment": "test",
        "checks": [],
    }
    assert len(response.headers["X-Request-ID"]) == 36


def test_readiness_reports_ready() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))

    with TestClient(app) as client:
        response = client.get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "Atlas TestOps Backend",
        "version": "0.1.0",
        "environment": "test",
        "checks": [{"name": "database", "status": "disabled"}],
    }


def test_readiness_reports_database_failure() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))
    database = MagicMock()
    database.check = AsyncMock(side_effect=RuntimeError("database unavailable"))
    app.dependency_overrides[get_optional_database] = lambda: cast(Database, database)

    with TestClient(app) as client:
        response = client.get("/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"] == [{"name": "database", "status": "not_ready"}]


def test_production_disables_openapi() -> None:
    app = create_app(Settings(environment="production", docs_enabled=True, cors_origins=[]))

    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 404
