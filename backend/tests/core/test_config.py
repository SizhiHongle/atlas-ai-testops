"""应用配置测试。"""

from base64 import b64encode

import pytest
from pydantic import SecretStr, ValidationError

from atlas_testops.core.config import (
    AuthSessionWorkerSettings,
    BrowserWorkerSettings,
    Settings,
)


def _browser_worker_runtime_values(api_base_url: str) -> dict[str, object]:
    encoded_key = b64encode(b"k" * 32).decode()
    digest = "sha256:" + "a" * 64
    return {
        "browser_runtime_api_base_url": api_base_url,
        "browser_runtime_request_hmac_key_base64": SecretStr(encoded_key),
        "browser_context_envelope_key_base64": SecretStr(encoded_key),
        "browser_context_envelope_key_version": "envelope-v1",
        "browser_revision": "playwright@1.55.0/chromium@140.0.0.0",
        "browser_tool_catalog_ref": "tools.browser-safe@1.0.0",
        "browser_policy_bundle_ref": "policy.browser-safe@1.0.0",
        "browser_mcp_server_manifest_digest": digest,
        "browser_tool_schema_digest": digest,
        "browser_policy_digest": digest,
    }


def test_normalizes_api_prefix_and_protects_database_secret() -> None:
    settings = Settings(
        environment="test",
        api_v1_prefix="v2/",
        database_url=SecretStr("postgresql://user:secret@localhost/atlas"),
    )

    assert settings.api_v1_prefix == "/v2"
    assert settings.database_url_value == "postgresql://user:secret@localhost/atlas"
    assert "postgresql://user:secret@localhost/atlas" not in repr(settings)


def test_rejects_empty_api_prefix() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", api_v1_prefix="/")


def test_rejects_invalid_pool_range() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            database_pool_min_size=5,
            database_pool_max_size=4,
        )


def test_production_disables_docs() -> None:
    settings = Settings(environment="production", docs_enabled=True)

    assert settings.docs_enabled is False


@pytest.mark.parametrize(
    ("environment", "cookie_name", "secure"),
    [
        ("test", "atlas_session", False),
        ("staging", "__Host-atlas_session", True),
        ("production", "__Host-atlas_session", True),
    ],
)
def test_session_cookie_policy(
    environment: str,
    cookie_name: str,
    secure: bool,
) -> None:
    settings = Settings(environment=environment)  # type: ignore[arg-type]

    assert settings.session_cookie_name == cookie_name
    assert settings.session_cookie_secure is secure


def test_rejects_idle_expiry_longer_than_absolute_expiry() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            session_idle_minutes=121,
            session_absolute_hours=2,
        )

    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            remembered_session_idle_hours=49,
            remembered_session_days=2,
        )


def test_auth_session_timeouts_preserve_attempt_and_workflow_deadlines() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            auth_session_creation_timeout_seconds=45,
            auth_session_attempt_ttl_seconds=45,
        )

    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            auth_session_creation_timeout_seconds=45,
            auth_session_workflow_timeout_seconds=45,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "account_health_verification_timeout_seconds": 30,
            "account_health_attempt_ttl_seconds": 30,
        },
        {
            "fixture_activity_timeout_seconds": 120,
            "fixture_cleanup_grace_seconds": 60,
        },
        {
            "fixture_activity_timeout_seconds": 330,
            "fixture_recovery_claim_ttl_seconds": 330,
        },
        {
            "fixture_retry_initial_seconds": 5,
            "fixture_retry_maximum_seconds": 4,
        },
    ],
)
def test_rejects_unsafe_health_and_fixture_timeout_relationships(
    overrides: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", **overrides)  # type: ignore[arg-type]


def test_local_auth_session_worker_accepts_complete_vault_configuration() -> None:
    encoded_key = b64encode(b"k" * 32).decode()
    settings = AuthSessionWorkerSettings(
        environment="test",
        session_object_store_endpoint="127.0.0.1:9000",
        session_object_store_access_key=SecretStr("access"),
        session_object_store_secret_key=SecretStr("secret"),
        session_object_store_create_bucket=True,
        session_artifact_aes_key_base64=SecretStr(encoded_key),
        session_artifact_key_version="test-v1",
    )

    assert settings.session_vault_configured
    assert encoded_key not in repr(settings)


def test_auth_session_worker_rejects_partial_or_unsafe_static_vault() -> None:
    encoded_key = b64encode(b"k" * 32).decode()
    with pytest.raises(ValidationError):
        AuthSessionWorkerSettings(
            environment="test",
            session_object_store_endpoint="127.0.0.1:9000",
        )

    with pytest.raises(ValidationError):
        AuthSessionWorkerSettings(
            environment="test",
            session_object_store_endpoint="127.0.0.1:9000",
            session_object_store_access_key=SecretStr("access"),
            session_object_store_secret_key=SecretStr("secret"),
            session_artifact_aes_key_base64=SecretStr("not-base64"),
            session_artifact_key_version="test-v1",
        )

    with pytest.raises(ValidationError):
        AuthSessionWorkerSettings(
            environment="staging",
            session_object_store_endpoint="minio.internal:9000",
            session_object_store_access_key=SecretStr("access"),
            session_object_store_secret_key=SecretStr("secret"),
            session_artifact_aes_key_base64=SecretStr(encoded_key),
            session_artifact_key_version="kms-placeholder",
        )


def test_auth_session_worker_rejects_unsafe_bucket_and_key_configuration() -> None:
    encoded_key = b64encode(b"k" * 32).decode()
    with pytest.raises(ValidationError, match="bucket creation requires"):
        AuthSessionWorkerSettings(
            environment="test",
            session_object_store_create_bucket=True,
        )

    with pytest.raises(ValidationError, match="bucket creation is local-only"):
        AuthSessionWorkerSettings(
            environment="staging",
            session_object_store_endpoint="minio.internal:9000",
            session_object_store_access_key=SecretStr("access"),
            session_object_store_secret_key=SecretStr("secret"),
            session_object_store_create_bucket=True,
            session_artifact_aes_key_base64=SecretStr(encoded_key),
            session_artifact_key_version="staging-v1",
        )

    with pytest.raises(ValidationError, match="exactly 32 bytes"):
        AuthSessionWorkerSettings(
            environment="test",
            session_object_store_endpoint="127.0.0.1:9000",
            session_object_store_access_key=SecretStr("access"),
            session_object_store_secret_key=SecretStr("secret"),
            session_artifact_aes_key_base64=SecretStr(b64encode(b"k" * 31).decode()),
            session_artifact_key_version="test-v1",
        )


def test_browser_runtime_requires_complete_api_security_and_safe_timeouts() -> None:
    key = SecretStr(b64encode(b"k" * 32).decode())
    settings = Settings(
        environment="test",
        browser_runtime_enabled=True,
        browser_runtime_permit_key_base64=key,
        browser_runtime_request_hmac_key_base64=key,
        browser_context_envelope_key_base64=key,
        browser_context_envelope_key_version="test-v1",
    )
    assert settings.browser_runtime_enabled
    with pytest.raises(ValidationError, match="complete security"):
        Settings(environment="test", browser_runtime_enabled=True)
    with pytest.raises(ValidationError, match="heartbeat"):
        Settings(
            environment="test",
            browser_runtime_activity_timeout_seconds=30,
            browser_runtime_heartbeat_timeout_seconds=30,
        )
    with pytest.raises(ValidationError, match="permit TTL"):
        Settings(
            environment="test",
            browser_runtime_activity_timeout_seconds=90,
            browser_runtime_permit_ttl_seconds=90,
        )


def test_database_free_browser_worker_settings_are_all_or_nothing() -> None:
    encoded_key = b64encode(b"k" * 32).decode()
    digest = "sha256:" + "a" * 64
    settings = BrowserWorkerSettings(
        environment="test",
        session_object_store_endpoint="127.0.0.1:9000",
        session_object_store_access_key=SecretStr("access"),
        session_object_store_secret_key=SecretStr("secret"),
        session_artifact_aes_key_base64=SecretStr(encoded_key),
        session_artifact_key_version="session-v1",
        browser_runtime_api_base_url="https://runtime.internal/",
        browser_runtime_request_hmac_key_base64=SecretStr(encoded_key),
        browser_context_envelope_key_base64=SecretStr(encoded_key),
        browser_context_envelope_key_version="envelope-v1",
        browser_revision="playwright@1.55.0/chromium@140.0.0.0",
        browser_tool_catalog_ref="tools.browser-safe@1.0.0",
        browser_policy_bundle_ref="policy.browser-safe@1.0.0",
        browser_mcp_server_manifest_digest=digest,
        browser_tool_schema_digest=digest,
        browser_policy_digest=digest,
    )
    assert settings.browser_runtime_configured
    assert settings.browser_runtime_api_base_url == "https://runtime.internal"
    assert not hasattr(settings, "database_url")

    with pytest.raises(ValidationError, match="runtime configuration"):
        BrowserWorkerSettings(
            environment="test",
            browser_runtime_api_base_url="https://runtime.internal",
        )
    with pytest.raises(ValidationError, match=r"HTTP\(S\) origin"):
        BrowserWorkerSettings(
            environment="test",
            browser_runtime_api_base_url="https://runtime.internal/path",
            browser_runtime_request_hmac_key_base64=SecretStr(encoded_key),
            browser_context_envelope_key_base64=SecretStr(encoded_key),
            browser_context_envelope_key_version="envelope-v1",
            browser_revision="revision-v1",
            browser_tool_catalog_ref="tools.browser-safe@1.0.0",
            browser_policy_bundle_ref="policy.browser-safe@1.0.0",
            browser_mcp_server_manifest_digest=digest,
            browser_tool_schema_digest=digest,
            browser_policy_digest=digest,
        )


@pytest.mark.parametrize("environment", ["local", "test", "development"])
def test_browser_worker_allows_http_only_in_development_environments(
    environment: str,
) -> None:
    settings = BrowserWorkerSettings.model_validate(
        {
            "environment": environment,
            **_browser_worker_runtime_values("http://runtime.internal/"),
        }
    )

    assert settings.browser_runtime_api_base_url == "http://runtime.internal"
    assert settings.browser_runtime_allow_insecure_http


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_browser_worker_requires_https_outside_development(environment: str) -> None:
    with pytest.raises(ValidationError, match="requires HTTPS"):
        BrowserWorkerSettings.model_validate(
            {
                "environment": environment,
                **_browser_worker_runtime_values("http://runtime.internal"),
            }
        )

    settings = BrowserWorkerSettings.model_validate(
        {
            "environment": environment,
            **_browser_worker_runtime_values("https://runtime.internal"),
        }
    )
    assert not settings.browser_runtime_allow_insecure_http
