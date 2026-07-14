"""统一 Problem Details 和 Request ID 测试。"""

from fastapi import Query
from fastapi.testclient import TestClient

from atlas_testops.api.problem_details import PROBLEM_CONTENT_TYPE, problem_openapi_response
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.main import create_app


def test_not_found_uses_problem_details_and_request_id() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))

    with TestClient(app) as client:
        response = client.get("/missing", headers={"X-Request-ID": "test/request-1"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert response.headers["X-Request-ID"] == "test/request-1"
    assert response.json()["errorCode"] == "NOT_FOUND"
    assert response.json()["requestId"] == "test/request-1"


def test_validation_errors_do_not_echo_input_values() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))

    @app.get("/contract-test")
    async def contract_test(value: int = Query(ge=1)) -> dict[str, int]:
        return {"value": value}

    with TestClient(app) as client:
        response = client.get("/contract-test", params={"value": "secret-invalid-value"})

    payload = response.json()
    assert response.status_code == 422
    assert payload["errorCode"] == "VALIDATION_FAILED"
    assert payload["violations"][0]["field"] == "query.value"
    assert "secret-invalid-value" not in response.text


def test_application_error_keeps_stable_code_and_headers() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))

    @app.get("/conflict-test")
    async def conflict_test() -> None:
        raise ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title="状态冲突",
            detail="资源已经被其他请求修改。",
            status_code=409,
            headers={"ETag": '"revision-2"'},
        )

    with TestClient(app) as client:
        response = client.get("/conflict-test")

    assert response.status_code == 409
    assert response.json()["errorCode"] == "CONFLICT"
    assert response.headers["etag"] == '"revision-2"'


def test_unexpected_error_is_redacted() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))

    @app.get("/unexpected-test")
    async def unexpected_test() -> None:
        raise RuntimeError("database-password-must-not-leak")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/unexpected-test")

    assert response.status_code == 500
    assert response.json()["errorCode"] == "INTERNAL_ERROR"
    assert "database-password" not in response.text


def test_problem_openapi_response_uses_shared_model() -> None:
    response = problem_openapi_response("统一错误")

    assert response["default"]["description"] == "统一错误"
