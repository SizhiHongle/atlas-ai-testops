"""Auth Session Worker-only construction for encrypted object storage."""

from minio import Minio

from atlas_testops.application.ports.sessions import SessionArtifactVault
from atlas_testops.core.config import AuthSessionWorkerSettings
from atlas_testops.infrastructure.session_vault import (
    AesGcmSessionArtifactVault,
    MinioSessionObjectStore,
)


async def build_session_artifact_vault(
    settings: AuthSessionWorkerSettings,
) -> SessionArtifactVault:
    """Build and validate the local S3/AES vault without exposing key material."""

    endpoint = settings.session_object_store_endpoint
    access_key = settings.session_object_store_access_key
    secret_key = settings.session_object_store_secret_key
    encoded_key = settings.session_artifact_aes_key_base64
    key_version = settings.session_artifact_key_version
    if (
        endpoint is None
        or access_key is None
        or secret_key is None
        or encoded_key is None
        or key_version is None
    ):
        raise ValueError("session artifact vault is not configured")
    normalized_endpoint = endpoint.strip()
    if not normalized_endpoint or "://" in normalized_endpoint or "/" in normalized_endpoint:
        raise ValueError("session object store endpoint must be host:port without a scheme")
    client = Minio(
        normalized_endpoint,
        access_key=access_key.get_secret_value(),
        secret_key=secret_key.get_secret_value(),
        secure=settings.session_object_store_secure,
    )
    object_store = MinioSessionObjectStore(
        client,
        bucket=settings.session_object_store_bucket,
        create_bucket=settings.session_object_store_create_bucket,
    )
    await object_store.initialize()
    return AesGcmSessionArtifactVault.from_base64_key(
        object_store,
        bucket=settings.session_object_store_bucket,
        encoded_key=encoded_key.get_secret_value(),
        key_version=key_version,
    )


async def build_optional_session_artifact_vault(
    settings: AuthSessionWorkerSettings,
) -> SessionArtifactVault | None:
    """Keep the worker fail-closed when no deployment vault has been injected."""

    if not settings.session_vault_configured:
        return None
    return await build_session_artifact_vault(settings)
