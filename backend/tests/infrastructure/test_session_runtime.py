"""Auth Session Worker Vault construction tests without network access."""

from base64 import b64encode
from typing import cast
from uuid import uuid7

import pytest
from pydantic import SecretStr

from atlas_testops.core.config import AuthSessionWorkerSettings
from atlas_testops.infrastructure import session_runtime
from atlas_testops.infrastructure.session_vault import (
    InMemorySessionObjectStore,
    SessionObjectStore,
)


class InitializableMemoryStore(InMemorySessionObjectStore):
    def __init__(self) -> None:
        super().__init__()
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True


def configured_settings(*, endpoint: str = "127.0.0.1:9000") -> AuthSessionWorkerSettings:
    return AuthSessionWorkerSettings(
        environment="test",
        session_object_store_endpoint=endpoint,
        session_object_store_access_key=SecretStr("access-key"),
        session_object_store_secret_key=SecretStr("secret-key"),
        session_object_store_bucket="session-runtime-test",
        session_object_store_create_bucket=True,
        session_artifact_aes_key_base64=SecretStr(
            b64encode(b"v" * 32).decode()
        ),
        session_artifact_key_version="test-v1",
    )


@pytest.mark.anyio
async def test_build_session_vault_initializes_s3_store(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}
    store = InitializableMemoryStore()

    def fake_minio(
        endpoint: str,
        *,
        access_key: str,
        secret_key: str,
        secure: bool,
    ) -> object:
        observed.update(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        return object()

    def fake_store(
        client: object,
        *,
        bucket: str,
        create_bucket: bool,
    ) -> SessionObjectStore:
        observed.update(
            client=client,
            bucket=bucket,
            create_bucket=create_bucket,
        )
        return cast(SessionObjectStore, store)

    monkeypatch.setattr(session_runtime, "Minio", fake_minio)
    monkeypatch.setattr(session_runtime, "MinioSessionObjectStore", fake_store)

    vault = await session_runtime.build_optional_session_artifact_vault(
        configured_settings()
    )

    assert vault is not None
    assert store.initialized
    assert observed["endpoint"] == "127.0.0.1:9000"
    assert observed["access_key"] == "access-key"
    assert observed["secret_key"] == "secret-key"
    assert observed["secure"] is False
    assert observed["bucket"] == "session-runtime-test"
    assert observed["create_bucket"] is True
    assert vault.object_ref_for(tenant_id=uuid7(), artifact_id=uuid7()).startswith(
        "session-vault://session-runtime-test/"
    )


@pytest.mark.anyio
async def test_optional_vault_is_none_when_worker_has_no_vault_configuration() -> None:
    settings = AuthSessionWorkerSettings(environment="test")

    assert await session_runtime.build_optional_session_artifact_vault(settings) is None


@pytest.mark.anyio
async def test_vault_factory_rejects_missing_or_url_style_endpoint() -> None:
    with pytest.raises(ValueError):
        await session_runtime.build_session_artifact_vault(
            AuthSessionWorkerSettings(environment="test")
        )

    with pytest.raises(ValueError):
        await session_runtime.build_session_artifact_vault(
            configured_settings(endpoint="http://127.0.0.1:9000")
        )
