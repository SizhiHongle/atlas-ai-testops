"""Health endpoint tests."""

from fastapi.testclient import TestClient

from atlas_testops.core.config import Settings
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
    }


def test_readiness_reports_ready() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))

    with TestClient(app) as client:
        response = client.get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_production_disables_openapi() -> None:
    app = create_app(Settings(environment="production", docs_enabled=True, cors_origins=[]))

    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 404
