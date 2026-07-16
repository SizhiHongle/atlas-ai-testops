"""Verified, redacted browser evidence backed by an S3-compatible object store."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest
from io import BytesIO
from re import fullmatch
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from minio import Minio
from minio.error import MinioException, S3Error
from PIL import Image, UnidentifiedImageError
from pydantic import JsonValue
from urllib3.exceptions import HTTPError

from atlas_testops.application.ports.evidence import (
    BrowserArtifactWriter,
    EvidenceArtifactCaptureError,
    EvidenceArtifactWriteScope,
    EvidenceObjectDescriptor,
    EvidenceObjectIntegrityError,
    EvidenceObjectMissingError,
    EvidenceObjectReader,
    EvidenceStoreError,
    EvidenceStoreUnavailableError,
    ScreenshotRedactionPolicy,
)
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.domain.case import canonical_digest
from atlas_testops.domain.runtime import (
    EvidenceArtifactInput,
    EvidenceArtifactKind,
    EvidenceIntegrity,
)

DEFAULT_SCREENSHOT_MASK_SELECTORS = (
    "input",
    "textarea",
    "select",
    '[contenteditable]:not([contenteditable="false"])',
    "[data-atlas-sensitive]",
)
DEFAULT_SCREENSHOT_MASK_COLOR = "#000000"
DEFAULT_MAX_SCREENSHOT_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_SCREENSHOT_PIXELS = 33_177_600
DEFAULT_MAX_EVIDENCE_READ_BYTES = 64 * 1024 * 1024


class EvidenceObjectStore(Protocol):
    """Minimal write-once object operations used by evidence infrastructure."""

    async def put(self, key: str, payload: bytes, *, content_type: str) -> None: ...

    async def get(self, key: str, *, maximum_bytes: int) -> bytes: ...

    async def delete(self, key: str) -> None: ...


class InMemoryEvidenceObjectStore:
    """Deterministic write-once evidence store for unit and integration tests."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._content_types: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        async with self._lock:
            if key in self._objects:
                raise EvidenceArtifactCaptureError("evidence object key already exists")
            self._objects[key] = bytes(payload)
            self._content_types[key] = content_type

    async def get(self, key: str, *, maximum_bytes: int) -> bytes:
        async with self._lock:
            payload = self._objects.get(key)
            if payload is None:
                raise EvidenceObjectMissingError("evidence object is unavailable")
            if len(payload) > maximum_bytes:
                raise EvidenceObjectIntegrityError("evidence object exceeds its size limit")
            return bytes(payload)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._objects.pop(key, None)
            self._content_types.pop(key, None)

    async def tamper_for_test(self, key: str, payload: bytes) -> None:
        """Replace bytes without changing the key so integrity failures can be asserted."""

        async with self._lock:
            if key not in self._objects:
                raise EvidenceObjectMissingError("evidence object is unavailable")
            self._objects[key] = bytes(payload)

    async def payload_for_test(self, key: str) -> bytes | None:
        """Return a defensive copy for redaction and canonicalization assertions."""

        async with self._lock:
            payload = self._objects.get(key)
            return bytes(payload) if payload is not None else None


class MinioEvidenceObjectStore:
    """Async bounded facade over the synchronous MinIO Python SDK."""

    def __init__(
        self,
        client: Minio,
        *,
        bucket: str,
        create_bucket: bool,
        maximum_concurrency: int = 8,
    ) -> None:
        normalized_bucket = bucket.strip()
        if not normalized_bucket:
            raise ValueError("evidence bucket must not be blank")
        if not 1 <= maximum_concurrency <= 32:
            raise ValueError("evidence store concurrency must be 1-32")
        self._client = client
        self._bucket = normalized_bucket
        self._create_bucket = create_bucket
        self._operation_limiter = asyncio.Semaphore(maximum_concurrency)

    @property
    def bucket(self) -> str:
        return self._bucket

    async def initialize(self) -> None:
        """Validate the bucket and create it only when local policy allows."""

        try:
            async with self._operation_limiter:
                exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
                if exists:
                    return
                if not self._create_bucket:
                    raise EvidenceStoreUnavailableError("evidence bucket is unavailable")
                await asyncio.to_thread(self._client.make_bucket, self._bucket)
        except EvidenceStoreUnavailableError:
            raise
        except (OSError, MinioException, HTTPError) as error:
            raise EvidenceStoreUnavailableError("evidence object store is unavailable") from error

    async def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        def upload() -> None:
            try:
                self._client.stat_object(self._bucket, key)
            except S3Error as error:
                if error.code not in {"NoSuchKey", "NoSuchObject"}:
                    raise
            else:
                raise EvidenceArtifactCaptureError("evidence object key already exists")
            self._client.put_object(
                self._bucket,
                key,
                BytesIO(payload),
                len(payload),
                content_type=content_type,
            )

        try:
            async with self._operation_limiter:
                await asyncio.to_thread(upload)
        except EvidenceArtifactCaptureError:
            raise
        except (OSError, MinioException, HTTPError) as error:
            raise EvidenceStoreUnavailableError("evidence object store is unavailable") from error

    async def get(self, key: str, *, maximum_bytes: int) -> bytes:
        def download() -> bytes:
            try:
                response = self._client.get_object(self._bucket, key)
            except S3Error as error:
                if error.code in {"NoSuchKey", "NoSuchObject"}:
                    raise EvidenceObjectMissingError("evidence object is unavailable") from error
                raise
            try:
                payload = response.read(maximum_bytes + 1)
                if len(payload) > maximum_bytes:
                    raise EvidenceObjectIntegrityError("evidence object exceeds its size limit")
                return payload
            finally:
                response.close()
                response.release_conn()

        try:
            async with self._operation_limiter:
                return await asyncio.to_thread(download)
        except EvidenceObjectMissingError, EvidenceObjectIntegrityError:
            raise
        except (OSError, MinioException, HTTPError) as error:
            raise EvidenceStoreUnavailableError("evidence object store is unavailable") from error

    async def delete(self, key: str) -> None:
        try:
            async with self._operation_limiter:
                await asyncio.to_thread(self._client.remove_object, self._bucket, key)
        except (OSError, MinioException, HTTPError) as error:
            raise EvidenceStoreUnavailableError("evidence object store is unavailable") from error


class PngEvidenceArtifactWriter(BrowserArtifactWriter):
    """Canonicalize masked screenshots and return receipts only after read-back."""

    def __init__(
        self,
        object_store: EvidenceObjectStore,
        *,
        bucket: str,
        mask_selectors: Sequence[str] = DEFAULT_SCREENSHOT_MASK_SELECTORS,
        mask_color: str = DEFAULT_SCREENSHOT_MASK_COLOR,
        maximum_raw_bytes: int = DEFAULT_MAX_SCREENSHOT_BYTES,
        maximum_pixels: int = DEFAULT_MAX_SCREENSHOT_PIXELS,
    ) -> None:
        normalized_bucket = bucket.strip()
        selectors = tuple(item.strip() for item in mask_selectors)
        if not normalized_bucket:
            raise ValueError("evidence bucket must not be blank")
        if not selectors or any(not item or len(item) > 240 for item in selectors):
            raise ValueError("screenshot redaction selectors are invalid")
        if len(selectors) != len(set(selectors)) or len(selectors) > 32:
            raise ValueError("screenshot redaction selectors must be unique and bounded")
        if fullmatch(r"#[0-9A-Fa-f]{6}", mask_color) is None:
            raise ValueError("screenshot mask color must be an opaque RGB hex color")
        if not 1 * 1024 * 1024 <= maximum_raw_bytes <= 64 * 1024 * 1024:
            raise ValueError("maximum raw screenshot size must be 1-64 MiB")
        if not 1_000_000 <= maximum_pixels <= 100_000_000:
            raise ValueError("maximum screenshot pixels must be 1-100 million")
        self._object_store = object_store
        self._bucket = normalized_bucket
        self._maximum_raw_bytes = maximum_raw_bytes
        self._maximum_pixels = maximum_pixels
        policy_material: dict[str, JsonValue] = {
            "schemaVersion": "atlas.screenshot-redaction-policy/0.1",
            "maskSelectors": list(selectors),
            "maskColor": mask_color.upper(),
            "capture": "playwright-mask-before-encoding",
            "canonicalPng": {
                "mode": "RGB",
                "metadata": "stripped",
                "alpha": "flattened-on-white",
                "compressLevel": 9,
            },
            "maximumRawBytes": maximum_raw_bytes,
            "maximumPixels": maximum_pixels,
        }
        self._redaction_policy = ScreenshotRedactionPolicy(
            selectors=selectors,
            mask_color=mask_color.upper(),
            content_digest=canonical_digest(policy_material),
        )

    @property
    def screenshot_redaction_policy(self) -> ScreenshotRedactionPolicy:
        return self._redaction_policy

    async def write(
        self,
        *,
        scope: EvidenceArtifactWriteScope,
        kind: EvidenceArtifactKind,
        payload: bytes,
        mime_type: str,
        required: bool,
        captured_at: datetime,
    ) -> EvidenceArtifactInput:
        if captured_at.tzinfo is None:
            raise EvidenceArtifactCaptureError("evidence capture time must be timezone-aware")
        if not (scope.execution_created_at <= captured_at < scope.execution_deadline):
            raise EvidenceArtifactCaptureError("evidence capture is outside the contract window")
        if kind is not EvidenceArtifactKind.SCREENSHOT or mime_type != "image/png":
            raise EvidenceArtifactCaptureError(
                "initial evidence writer accepts PNG screenshots only"
            )
        if not payload or len(payload) > self._maximum_raw_bytes:
            raise EvidenceArtifactCaptureError("raw screenshot exceeds the capture size limit")
        canonical_payload = await asyncio.to_thread(self._canonicalize_png, payload)
        if len(canonical_payload) > self._maximum_raw_bytes:
            raise EvidenceArtifactCaptureError("canonical screenshot exceeds the size limit")
        artifact_id = new_entity_id()
        object_ref = self._object_ref(scope, artifact_id)
        key = _object_key(object_ref, expected_bucket=self._bucket)
        uploaded = False
        try:
            await self._object_store.put(key, canonical_payload, content_type="image/png")
            uploaded = True
            retained_payload = await self._object_store.get(
                key,
                maximum_bytes=len(canonical_payload),
            )
            expected_digest = _sha256_digest(canonical_payload)
            actual_digest = _sha256_digest(retained_payload)
            if len(retained_payload) != len(canonical_payload) or not compare_digest(
                actual_digest,
                expected_digest,
            ):
                raise EvidenceObjectIntegrityError(
                    "retained evidence does not match the capture receipt"
                )
        except BaseException:
            if uploaded:
                with suppress(EvidenceStoreError):
                    await self._object_store.delete(key)
            raise
        return EvidenceArtifactInput(
            id=artifact_id,
            kind=kind,
            object_ref=object_ref,
            content_digest=expected_digest,
            size_bytes=len(canonical_payload),
            mime_type="image/png",
            redaction_policy_digest=self._redaction_policy.content_digest,
            integrity=EvidenceIntegrity.VERIFIED,
            required=required,
            captured_at=captured_at,
        )

    def _canonicalize_png(self, payload: bytes) -> bytes:
        try:
            with Image.open(BytesIO(payload)) as source:
                if source.format != "PNG":
                    raise EvidenceArtifactCaptureError("screenshot bytes are not a PNG")
                width, height = source.size
                if width <= 0 or height <= 0 or width * height > self._maximum_pixels:
                    raise EvidenceArtifactCaptureError(
                        "screenshot dimensions exceed the pixel limit"
                    )
                source.load()
                rgba = source.convert("RGBA")
            flattened = Image.new("RGBA", rgba.size, "white")
            flattened.alpha_composite(rgba)
            normalized = flattened.convert("RGB")
            output = BytesIO()
            normalized.save(
                output,
                format="PNG",
                optimize=False,
                compress_level=9,
            )
            return output.getvalue()
        except EvidenceArtifactCaptureError:
            raise
        except (OSError, UnidentifiedImageError, ValueError) as error:
            raise EvidenceArtifactCaptureError("screenshot PNG is invalid") from error

    def _object_ref(self, scope: EvidenceArtifactWriteScope, artifact_id: UUID) -> str:
        return (
            f"evidence://{self._bucket}/tenants/{scope.tenant_id.hex}/"
            f"projects/{scope.project_id.hex}/environments/{scope.environment_id.hex}/"
            f"debug-runs/{scope.debug_run_id.hex}/contracts/"
            f"{scope.execution_contract_id.hex}/artifacts/{artifact_id.hex}.png"
        )


class VerifiedEvidenceObjectReader(EvidenceObjectReader):
    """Read complete bytes and verify size and SHA-256 before returning anything."""

    def __init__(
        self,
        object_store: EvidenceObjectStore,
        *,
        bucket: str,
        maximum_bytes: int = DEFAULT_MAX_EVIDENCE_READ_BYTES,
    ) -> None:
        normalized_bucket = bucket.strip()
        if not normalized_bucket:
            raise ValueError("evidence bucket must not be blank")
        if not 1 * 1024 * 1024 <= maximum_bytes <= 256 * 1024 * 1024:
            raise ValueError("maximum evidence read size must be 1-256 MiB")
        self._object_store = object_store
        self._bucket = normalized_bucket
        self._maximum_bytes = maximum_bytes

    async def read_verified(self, descriptor: EvidenceObjectDescriptor) -> bytes:
        if descriptor.size_bytes < 1 or descriptor.size_bytes > self._maximum_bytes:
            raise EvidenceObjectIntegrityError("evidence object size is outside policy")
        key = _object_key(descriptor.object_ref, expected_bucket=self._bucket)
        expected_prefix = (
            f"tenants/{descriptor.tenant_id.hex}/projects/{descriptor.project_id.hex}/"
            f"environments/{descriptor.environment_id.hex}/debug-runs/"
            f"{descriptor.debug_run_id.hex}/contracts/"
            f"{descriptor.execution_contract_id.hex}/artifacts/{descriptor.artifact_id.hex}."
        )
        if (
            not key.startswith(expected_prefix)
            or fullmatch(r"[a-z0-9]{1,10}", key.removeprefix(expected_prefix)) is None
        ):
            raise EvidenceObjectIntegrityError(
                "evidence object reference does not match its immutable scope"
            )
        payload = await self._object_store.get(
            key,
            maximum_bytes=descriptor.size_bytes,
        )
        actual_digest = _sha256_digest(payload)
        if len(payload) != descriptor.size_bytes or not compare_digest(
            actual_digest,
            descriptor.content_digest,
        ):
            raise EvidenceObjectIntegrityError(
                "evidence object does not match its immutable receipt"
            )
        return payload


def _object_key(object_ref: str, *, expected_bucket: str) -> str:
    parsed = urlsplit(object_ref)
    key = parsed.path.removeprefix("/")
    if (
        parsed.scheme != "evidence"
        or parsed.netloc != expected_bucket
        or parsed.query
        or parsed.fragment
        or not key
        or len(key) > 480
        or fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_.=-]+", key) is None
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        raise EvidenceObjectIntegrityError("evidence object reference is invalid")
    return key


def _sha256_digest(payload: bytes) -> str:
    return f"sha256:{sha256(payload).hexdigest()}"
