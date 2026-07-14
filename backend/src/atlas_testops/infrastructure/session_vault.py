"""AES-GCM encrypted session vault backed by an S3-compatible object store."""

import asyncio
from base64 import b64decode, b64encode
from collections.abc import Awaitable, Callable
from hashlib import sha256
from hmac import compare_digest
from io import BytesIO
from json import JSONDecodeError, dumps, loads
from secrets import token_bytes
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from minio import Minio
from minio.error import S3Error

from atlas_testops.application.ports.sessions import (
    DecryptedSessionOperation,
    SealedSessionArtifact,
    SessionArtifactScope,
    SessionArtifactVault,
)

MAX_ENCRYPTED_ARTIFACT_BYTES = 12 * 1024 * 1024


class SessionVaultError(RuntimeError):
    """Safe base error for object storage, encryption, and integrity failures."""


class SessionVaultUnavailableError(SessionVaultError):
    """The configured vault dependency cannot complete the operation."""


class SessionArtifactIntegrityError(SessionVaultError):
    """Ciphertext metadata, AAD, or authentication tag does not validate."""


class SessionObjectStore(Protocol):
    """Minimal object operations required by the encrypted vault."""

    async def put(self, key: str, payload: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...


class InMemorySessionObjectStore:
    """Deterministic ciphertext-only object store for unit and integration tests."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._lock = asyncio.Lock()

    async def put(self, key: str, payload: bytes) -> None:
        async with self._lock:
            self._objects[key] = bytes(payload)

    async def get(self, key: str) -> bytes:
        async with self._lock:
            payload = self._objects.get(key)
            if payload is None:
                raise SessionVaultUnavailableError("session artifact object is unavailable")
            return bytes(payload)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._objects.pop(key, None)

    async def ciphertext_for_test(self, key: str) -> bytes | None:
        """Return a defensive ciphertext copy for leakage assertions only."""

        async with self._lock:
            payload = self._objects.get(key)
            return bytes(payload) if payload is not None else None


class MinioSessionObjectStore:
    """Async facade over the synchronous MinIO Python SDK."""

    def __init__(
        self,
        client: Minio,
        *,
        bucket: str,
        create_bucket: bool,
    ) -> None:
        normalized_bucket = bucket.strip()
        if not normalized_bucket:
            raise ValueError("session artifact bucket must not be blank")
        self._client = client
        self._bucket = normalized_bucket
        self._create_bucket = create_bucket

    @property
    def bucket(self) -> str:
        return self._bucket

    async def initialize(self) -> None:
        """Validate the bucket and create it only when local policy allows."""

        try:
            exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
            if exists:
                return
            if not self._create_bucket:
                raise SessionVaultUnavailableError("session artifact bucket is unavailable")
            await asyncio.to_thread(self._client.make_bucket, self._bucket)
        except SessionVaultUnavailableError:
            raise
        except (OSError, S3Error) as error:
            raise SessionVaultUnavailableError(
                "session artifact object store is unavailable"
            ) from error

    async def put(self, key: str, payload: bytes) -> None:
        def upload() -> None:
            self._client.put_object(
                self._bucket,
                key,
                BytesIO(payload),
                len(payload),
                content_type="application/vnd.atlas.session-artifact+json",
            )

        try:
            await asyncio.to_thread(upload)
        except (OSError, S3Error) as error:
            raise SessionVaultUnavailableError(
                "session artifact object store is unavailable"
            ) from error

    async def get(self, key: str) -> bytes:
        def download() -> bytes:
            response = self._client.get_object(self._bucket, key)
            try:
                payload = response.read(MAX_ENCRYPTED_ARTIFACT_BYTES + 1)
                if len(payload) > MAX_ENCRYPTED_ARTIFACT_BYTES:
                    raise SessionArtifactIntegrityError(
                        "encrypted session artifact exceeds the size limit"
                    )
                return payload
            finally:
                response.close()
                response.release_conn()

        try:
            return await asyncio.to_thread(download)
        except SessionArtifactIntegrityError:
            raise
        except (OSError, S3Error) as error:
            raise SessionVaultUnavailableError(
                "session artifact object store is unavailable"
            ) from error

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self._client.remove_object, self._bucket, key)
        except (OSError, S3Error) as error:
            raise SessionVaultUnavailableError(
                "session artifact object store is unavailable"
            ) from error


class AesGcmSessionArtifactVault(SessionArtifactVault):
    """Seal session state with scope-bound AES-256-GCM before object upload."""

    def __init__(
        self,
        object_store: SessionObjectStore,
        *,
        bucket: str,
        key: bytes,
        key_version: str,
    ) -> None:
        if len(key) != 32:
            raise ValueError("session artifact AES key must contain exactly 32 bytes")
        normalized_bucket = bucket.strip()
        normalized_version = key_version.strip()
        if not normalized_bucket:
            raise ValueError("session artifact bucket must not be blank")
        if not normalized_version or len(normalized_version) > 100:
            raise ValueError("session artifact key version must contain 1-100 characters")
        self._object_store = object_store
        self._bucket = normalized_bucket
        self._aes = AESGCM(bytes(key))
        self._key_version = normalized_version

    @classmethod
    def from_base64_key(
        cls,
        object_store: SessionObjectStore,
        *,
        bucket: str,
        encoded_key: str,
        key_version: str,
    ) -> AesGcmSessionArtifactVault:
        """Decode deployment-provided key material without storing it in metadata."""

        try:
            key = b64decode(encoded_key, validate=True)
        except ValueError as error:
            raise ValueError("session artifact AES key must be valid base64") from error
        return cls(
            object_store,
            bucket=bucket,
            key=key,
            key_version=key_version,
        )

    def object_ref_for(self, *, tenant_id: UUID, artifact_id: UUID) -> str:
        return (
            f"session-vault://{self._bucket}/tenants/{tenant_id.hex}/"
            f"sessions/{artifact_id.hex}.json"
        )

    async def seal(
        self,
        *,
        object_ref: str,
        scope: SessionArtifactScope,
        plaintext: memoryview,
    ) -> SealedSessionArtifact:
        key = self._object_key(object_ref)
        aad = self._associated_data(scope)
        nonce = token_bytes(12)
        plaintext_buffer = bytearray(plaintext)
        try:
            ciphertext = self._aes.encrypt(nonce, bytes(plaintext_buffer), aad)
        finally:
            plaintext_buffer[:] = b"\x00" * len(plaintext_buffer)
        envelope = dumps(
            {
                "aadDigest": sha256(aad).hexdigest(),
                "algorithm": "AES-256-GCM",
                "ciphertext": b64encode(ciphertext).decode("ascii"),
                "keyVersion": self._key_version,
                "nonce": b64encode(nonce).decode("ascii"),
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(envelope) > MAX_ENCRYPTED_ARTIFACT_BYTES:
            raise SessionArtifactIntegrityError(
                "encrypted session artifact exceeds the size limit"
            )
        await self._object_store.put(key, envelope)
        return SealedSessionArtifact(
            object_ref=object_ref,
            object_digest=f"sha256:{sha256(envelope).hexdigest()}",
            object_size_bytes=len(envelope),
            key_version=self._key_version,
        )

    async def with_decrypted[T](
        self,
        *,
        object_ref: str,
        scope: SessionArtifactScope,
        expected_digest: str,
        expected_key_version: str,
        operation: DecryptedSessionOperation[T],
    ) -> T:
        key = self._object_key(object_ref)
        envelope_bytes = await self._object_store.get(key)
        actual_digest = f"sha256:{sha256(envelope_bytes).hexdigest()}"
        if not compare_digest(actual_digest, expected_digest):
            raise SessionArtifactIntegrityError("session artifact digest does not match")
        try:
            envelope = loads(envelope_bytes)
            if not isinstance(envelope, dict) or set(envelope) != {
                "aadDigest",
                "algorithm",
                "ciphertext",
                "keyVersion",
                "nonce",
                "version",
            }:
                raise SessionArtifactIntegrityError("session artifact envelope is invalid")
            aad = self._associated_data(scope)
            if (
                envelope["algorithm"] != "AES-256-GCM"
                or envelope["version"] != 1
                or envelope["keyVersion"] != expected_key_version
                or envelope["keyVersion"] != self._key_version
                or not compare_digest(envelope["aadDigest"], sha256(aad).hexdigest())
            ):
                raise SessionArtifactIntegrityError("session artifact envelope is invalid")
            nonce = b64decode(envelope["nonce"], validate=True)
            ciphertext = b64decode(envelope["ciphertext"], validate=True)
            if len(nonce) != 12 or not ciphertext:
                raise SessionArtifactIntegrityError("session artifact envelope is invalid")
            plaintext = bytearray(self._aes.decrypt(nonce, ciphertext, aad))
        except SessionArtifactIntegrityError:
            raise
        except (InvalidTag, JSONDecodeError, TypeError, ValueError) as error:
            raise SessionArtifactIntegrityError(
                "session artifact authentication failed"
            ) from error
        try:
            return await operation(memoryview(plaintext))
        finally:
            plaintext[:] = b"\x00" * len(plaintext)

    async def delete(self, object_ref: str) -> None:
        await self._object_store.delete(self._object_key(object_ref))

    def _object_key(self, object_ref: str) -> str:
        parsed = urlsplit(object_ref)
        key = parsed.path.lstrip("/")
        if (
            parsed.scheme != "session-vault"
            or parsed.netloc != self._bucket
            or not key.startswith("tenants/")
            or "/../" in f"/{key}/"
            or not key.endswith(".json")
        ):
            raise SessionArtifactIntegrityError("session artifact reference is invalid")
        return key

    @staticmethod
    def _associated_data(scope: SessionArtifactScope) -> bytes:
        return dumps(
            {
                "accountId": str(scope.account_id),
                "allowedOrigins": list(scope.allowed_origins),
                "artifactId": str(scope.artifact_id),
                "connectorInstallationId": str(scope.connector_installation_id),
                "credentialBindingId": str(scope.credential_binding_id),
                "environmentId": str(scope.environment_id),
                "formatVersion": scope.format_version,
                "leaseFence": scope.lease_fence,
                "leaseId": str(scope.lease_id),
                "projectId": str(scope.project_id),
                "tenantId": str(scope.tenant_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


type SessionObjectStoreFactory = Callable[[], Awaitable[SessionObjectStore]]
