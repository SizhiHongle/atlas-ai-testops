"""Canonical screenshot capture and independently verified evidence reads."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from minio import Minio
from PIL import Image, PngImagePlugin
from urllib3.exceptions import HTTPError

from atlas_testops.application.ports.evidence import (
    EvidenceArtifactCaptureError,
    EvidenceArtifactWriteScope,
    EvidenceObjectDescriptor,
    EvidenceObjectIntegrityError,
    EvidenceObjectMissingError,
    EvidenceStoreUnavailableError,
)
from atlas_testops.domain.runtime import EvidenceArtifactKind, EvidenceIntegrity
from atlas_testops.infrastructure.evidence_store import (
    InMemoryEvidenceObjectStore,
    MinioEvidenceObjectStore,
    PngEvidenceArtifactWriter,
    VerifiedEvidenceObjectReader,
)

BUCKET = "atlas-evidence-artifacts"
TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("20000000-0000-4000-8000-000000000002")
ENVIRONMENT_ID = UUID("30000000-0000-4000-8000-000000000003")
RUN_ID = UUID("40000000-0000-4000-8000-000000000004")
CONTRACT_ID = UUID("50000000-0000-4000-8000-000000000005")
ARTIFACT_ID = UUID("60000000-0000-4000-8000-000000000006")
DIGEST = "sha256:" + "a" * 64
CAPTURED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _scope() -> EvidenceArtifactWriteScope:
    return EvidenceArtifactWriteScope(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        environment_id=ENVIRONMENT_ID,
        debug_run_id=RUN_ID,
        execution_contract_id=CONTRACT_ID,
        execution_contract_digest=DIGEST,
        execution_created_at=CAPTURED_AT - timedelta(minutes=1),
        execution_deadline=CAPTURED_AT + timedelta(minutes=1),
    )


def _png(*, size: tuple[int, int] = (2, 1), include_metadata: bool = True) -> bytes:
    image = Image.new("RGBA", size, (255, 0, 0, 128))
    if size == (2, 1):
        image.putpixel((1, 0), (0, 0, 255, 0))
    metadata = PngImagePlugin.PngInfo()
    if include_metadata:
        metadata.add_text("sensitive-note", "must-not-survive-canonicalization")
    output = BytesIO()
    image.save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 1), "red").save(output, format="JPEG")
    return output.getvalue()


def _digest(payload: bytes) -> str:
    return f"sha256:{sha256(payload).hexdigest()}"


def _key(object_ref: str) -> str:
    return urlsplit(object_ref).path.lstrip("/")


def _scoped_key(artifact_id: UUID = ARTIFACT_ID) -> str:
    return (
        f"tenants/{TENANT_ID.hex}/projects/{PROJECT_ID.hex}/"
        f"environments/{ENVIRONMENT_ID.hex}/debug-runs/{RUN_ID.hex}/"
        f"contracts/{CONTRACT_ID.hex}/artifacts/{artifact_id.hex}.png"
    )


def _descriptor(
    *,
    payload: bytes = b"original",
    object_ref: str | None = None,
    size_bytes: int | None = None,
) -> EvidenceObjectDescriptor:
    return EvidenceObjectDescriptor(
        artifact_id=ARTIFACT_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        environment_id=ENVIRONMENT_ID,
        debug_run_id=RUN_ID,
        execution_contract_id=CONTRACT_ID,
        object_ref=object_ref or f"evidence://{BUCKET}/{_scoped_key()}",
        content_digest=_digest(payload),
        size_bytes=len(payload) if size_bytes is None else size_bytes,
        mime_type="image/png",
    )


@pytest.mark.anyio
async def test_writer_canonicalizes_png_and_returns_exact_verified_receipt() -> None:
    store = InMemoryEvidenceObjectStore()
    writer = PngEvidenceArtifactWriter(store, bucket=BUCKET)

    artifact = await writer.write(
        scope=_scope(),
        kind=EvidenceArtifactKind.SCREENSHOT,
        payload=_png(),
        mime_type="image/png",
        required=True,
        captured_at=CAPTURED_AT,
    )

    expected_scope = (
        f"evidence://{BUCKET}/tenants/{TENANT_ID.hex}/projects/{PROJECT_ID.hex}/"
        f"environments/{ENVIRONMENT_ID.hex}/debug-runs/{RUN_ID.hex}/contracts/"
        f"{CONTRACT_ID.hex}/artifacts/"
    )
    assert artifact.object_ref.startswith(expected_scope)
    assert artifact.object_ref.endswith(f"{artifact.id.hex}.png")
    retained = await store.payload_for_test(_key(artifact.object_ref))
    assert retained is not None
    assert artifact.kind is EvidenceArtifactKind.SCREENSHOT
    assert artifact.content_digest == _digest(retained)
    assert artifact.size_bytes == len(retained)
    assert artifact.mime_type == "image/png"
    assert artifact.redaction_policy_digest == writer.screenshot_redaction_policy.content_digest
    assert artifact.integrity is EvidenceIntegrity.VERIFIED
    assert artifact.required
    assert artifact.captured_at == CAPTURED_AT

    with Image.open(BytesIO(retained)) as normalized:
        normalized.load()
        assert normalized.format == "PNG"
        assert normalized.mode == "RGB"
        assert normalized.info.get("sensitive-note") is None
        assert normalized.getpixel((0, 0)) == (255, 127, 127)
        assert normalized.getpixel((1, 0)) == (255, 255, 255)

    reader = VerifiedEvidenceObjectReader(store, bucket=BUCKET)
    restored = await reader.read_verified(
        EvidenceObjectDescriptor(
            artifact_id=artifact.id,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            environment_id=ENVIRONMENT_ID,
            debug_run_id=RUN_ID,
            execution_contract_id=CONTRACT_ID,
            object_ref=artifact.object_ref,
            content_digest=artifact.content_digest,
            size_bytes=artifact.size_bytes,
            mime_type=artifact.mime_type,
        )
    )
    assert restored == retained


def test_writer_redaction_policy_digest_is_stable_over_normalized_configuration() -> None:
    store = InMemoryEvidenceObjectStore()
    first = PngEvidenceArtifactWriter(
        store,
        bucket=BUCKET,
        mask_selectors=(" input ", "[data-private]"),
        mask_color="#aabbcc",
    )
    second = PngEvidenceArtifactWriter(
        store,
        bucket=BUCKET,
        mask_selectors=("input", "[data-private]"),
        mask_color="#AABBCC",
    )
    changed = PngEvidenceArtifactWriter(
        store,
        bucket=BUCKET,
        mask_selectors=("input", "[data-private]"),
        mask_color="#000000",
    )

    assert first.screenshot_redaction_policy.selectors == ("input", "[data-private]")
    assert first.screenshot_redaction_policy.mask_color == "#AABBCC"
    assert (
        first.screenshot_redaction_policy.content_digest
        == second.screenshot_redaction_policy.content_digest
    )
    assert (
        first.screenshot_redaction_policy.content_digest
        != changed.screenshot_redaction_policy.content_digest
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kind", "mime_type"),
    [
        (EvidenceArtifactKind.SCREENSHOT, "image/jpeg"),
        (EvidenceArtifactKind.TRACE, "image/png"),
    ],
)
async def test_writer_rejects_untrusted_kind_or_mime(
    kind: EvidenceArtifactKind,
    mime_type: str,
) -> None:
    writer = PngEvidenceArtifactWriter(InMemoryEvidenceObjectStore(), bucket=BUCKET)

    with pytest.raises(EvidenceArtifactCaptureError, match="PNG screenshots only"):
        await writer.write(
            scope=_scope(),
            kind=kind,
            payload=_png(),
            mime_type=mime_type,
            required=False,
            captured_at=CAPTURED_AT,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [b"not-a-png", pytest.param(_jpeg(), id="jpeg")])
async def test_writer_rejects_invalid_png_bytes(payload: bytes) -> None:
    writer = PngEvidenceArtifactWriter(InMemoryEvidenceObjectStore(), bucket=BUCKET)

    with pytest.raises(EvidenceArtifactCaptureError, match=r"not a PNG|PNG is invalid"):
        await writer.write(
            scope=_scope(),
            kind=EvidenceArtifactKind.SCREENSHOT,
            payload=payload,
            mime_type="image/png",
            required=False,
            captured_at=CAPTURED_AT,
        )


@pytest.mark.anyio
async def test_writer_rejects_raw_byte_and_pixel_limits() -> None:
    writer = PngEvidenceArtifactWriter(
        InMemoryEvidenceObjectStore(),
        bucket=BUCKET,
        maximum_raw_bytes=1024 * 1024,
        maximum_pixels=1_000_000,
    )

    with pytest.raises(EvidenceArtifactCaptureError, match="capture size limit"):
        await writer.write(
            scope=_scope(),
            kind=EvidenceArtifactKind.SCREENSHOT,
            payload=b"x" * (1024 * 1024 + 1),
            mime_type="image/png",
            required=False,
            captured_at=CAPTURED_AT,
        )
    with pytest.raises(EvidenceArtifactCaptureError, match="pixel limit"):
        await writer.write(
            scope=_scope(),
            kind=EvidenceArtifactKind.SCREENSHOT,
            payload=_png(size=(1001, 1000), include_metadata=False),
            mime_type="image/png",
            required=False,
            captured_at=CAPTURED_AT,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "captured_at",
    [
        datetime(2026, 7, 15, 12, 0),
        CAPTURED_AT - timedelta(minutes=2),
        CAPTURED_AT + timedelta(minutes=1),
    ],
)
async def test_writer_rejects_capture_outside_contract_window(
    captured_at: datetime,
) -> None:
    writer = PngEvidenceArtifactWriter(InMemoryEvidenceObjectStore(), bucket=BUCKET)

    with pytest.raises(EvidenceArtifactCaptureError, match=r"time|contract window"):
        await writer.write(
            scope=_scope(),
            kind=EvidenceArtifactKind.SCREENSHOT,
            payload=_png(),
            mime_type="image/png",
            required=False,
            captured_at=captured_at,
        )


class TamperingReadBackStore:
    """Return altered same-size bytes after upload while retaining cleanup visibility."""

    def __init__(self) -> None:
        self.store = InMemoryEvidenceObjectStore()
        self.last_key: str | None = None

    async def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        self.last_key = key
        await self.store.put(key, payload, content_type=content_type)

    async def get(self, key: str, *, maximum_bytes: int) -> bytes:
        payload = await self.store.get(key, maximum_bytes=maximum_bytes)
        return bytes((payload[0] ^ 1,)) + payload[1:]

    async def delete(self, key: str) -> None:
        await self.store.delete(key)


@pytest.mark.anyio
async def test_writer_deletes_object_when_read_back_is_tampered() -> None:
    store = TamperingReadBackStore()
    writer = PngEvidenceArtifactWriter(store, bucket=BUCKET)

    with pytest.raises(EvidenceObjectIntegrityError, match="does not match"):
        await writer.write(
            scope=_scope(),
            kind=EvidenceArtifactKind.SCREENSHOT,
            payload=_png(),
            mime_type="image/png",
            required=True,
            captured_at=CAPTURED_AT,
        )

    assert store.last_key is not None
    assert await store.store.payload_for_test(store.last_key) is None


@pytest.mark.anyio
async def test_in_memory_store_is_write_once_bounded_and_deletes_idempotently() -> None:
    store = InMemoryEvidenceObjectStore()
    key = "tenants/a/artifacts/object.png"

    await store.put(key, b"original", content_type="image/png")
    assert await store.get(key, maximum_bytes=8) == b"original"
    with pytest.raises(EvidenceArtifactCaptureError, match="already exists"):
        await store.put(key, b"replacement", content_type="image/png")
    with pytest.raises(EvidenceObjectIntegrityError, match="size limit"):
        await store.get(key, maximum_bytes=7)

    await store.tamper_for_test(key, b"tampered")
    assert await store.get(key, maximum_bytes=8) == b"tampered"
    await store.delete(key)
    await store.delete(key)
    with pytest.raises(EvidenceObjectMissingError):
        await store.get(key, maximum_bytes=8)


@pytest.mark.anyio
async def test_minio_transport_errors_are_mapped_to_controlled_unavailability() -> None:
    class FailingMinioClient:
        def bucket_exists(self, _bucket: str) -> bool:
            raise HTTPError("connection failed")

    store = MinioEvidenceObjectStore(
        cast(Minio, FailingMinioClient()),
        bucket=BUCKET,
        create_bucket=False,
        maximum_concurrency=1,
    )

    with pytest.raises(EvidenceStoreUnavailableError, match="unavailable"):
        await store.initialize()


@pytest.mark.anyio
async def test_reader_rejects_missing_and_tampered_objects() -> None:
    store = InMemoryEvidenceObjectStore()
    reader = VerifiedEvidenceObjectReader(store, bucket=BUCKET)
    key = _scoped_key()
    descriptor = _descriptor()

    with pytest.raises(EvidenceObjectMissingError):
        await reader.read_verified(descriptor)

    await store.put(key, b"original", content_type="image/png")
    await store.tamper_for_test(key, b"tampered")
    with pytest.raises(EvidenceObjectIntegrityError, match="immutable receipt"):
        await reader.read_verified(descriptor)


@pytest.mark.anyio
@pytest.mark.parametrize("declared_size", [7, 9])
async def test_reader_rejects_size_mismatches(declared_size: int) -> None:
    store = InMemoryEvidenceObjectStore()
    key = _scoped_key()
    await store.put(key, b"original", content_type="image/png")
    reader = VerifiedEvidenceObjectReader(store, bucket=BUCKET)

    with pytest.raises(EvidenceObjectIntegrityError):
        await reader.read_verified(_descriptor(size_bytes=declared_size))


@pytest.mark.anyio
async def test_reader_rejects_objects_above_its_read_policy() -> None:
    reader = VerifiedEvidenceObjectReader(
        InMemoryEvidenceObjectStore(),
        bucket=BUCKET,
        maximum_bytes=1024 * 1024,
    )

    with pytest.raises(EvidenceObjectIntegrityError, match="outside policy"):
        await reader.read_verified(_descriptor(payload=b"payload", size_bytes=1024 * 1024 + 1))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "object_ref",
    [
        "evidence://another-bucket/tenants/a/artifacts/object.png",
        f"session-vault://{BUCKET}/tenants/a/artifacts/object.png",
        f"evidence://{BUCKET}/tenants/../object.png",
        f"evidence://{BUCKET}/tenants/a/object.png?version=1",
        f"evidence://{BUCKET}/tenants/a/object.png#fragment",
        f"evidence://{BUCKET}/",
    ],
)
async def test_reader_rejects_cross_bucket_and_malformed_object_refs(
    object_ref: str,
) -> None:
    reader = VerifiedEvidenceObjectReader(InMemoryEvidenceObjectStore(), bucket=BUCKET)

    with pytest.raises(
        EvidenceObjectIntegrityError,
        match=r"reference (?:is invalid|does not match)",
    ):
        await reader.read_verified(_descriptor(payload=b"payload", object_ref=object_ref))


@pytest.mark.parametrize(
    "factory",
    [
        lambda store: PngEvidenceArtifactWriter(store, bucket=""),
        lambda store: PngEvidenceArtifactWriter(
            store,
            bucket=BUCKET,
            mask_selectors=("input", "input"),
        ),
        lambda store: PngEvidenceArtifactWriter(
            store,
            bucket=BUCKET,
            mask_color="transparent",
        ),
        lambda store: VerifiedEvidenceObjectReader(store, bucket=""),
        lambda store: VerifiedEvidenceObjectReader(
            store,
            bucket=BUCKET,
            maximum_bytes=1024,
        ),
    ],
)
def test_store_components_reject_unsafe_configuration(
    factory: Callable[[InMemoryEvidenceObjectStore], object],
) -> None:
    with pytest.raises(ValueError):
        factory(InMemoryEvidenceObjectStore())
