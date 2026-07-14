"""应用配置测试。"""

from base64 import b64encode

import pytest
from pydantic import SecretStr, ValidationError

from atlas_testops.core.config import AuthSessionWorkerSettings, Settings


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
