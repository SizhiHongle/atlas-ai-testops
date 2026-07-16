"""Construction helpers for the isolated evidence writer and API reader."""

from minio import Minio
from urllib3 import PoolManager
from urllib3.util import Retry, Timeout

from atlas_testops.application.ports.evidence import (
    BrowserArtifactWriter,
    EvidenceObjectReader,
)
from atlas_testops.core.config import BrowserWorkerSettings, Settings
from atlas_testops.infrastructure.evidence_store import (
    MinioEvidenceObjectStore,
    PngEvidenceArtifactWriter,
    VerifiedEvidenceObjectReader,
)


async def build_evidence_artifact_writer(
    settings: BrowserWorkerSettings,
) -> BrowserArtifactWriter:
    """Build the Browser Worker's write/read-back verified screenshot writer."""

    store = await _build_store(
        endpoint=settings.evidence_object_store_endpoint,
        access_key=(
            settings.evidence_object_store_access_key.get_secret_value()
            if settings.evidence_object_store_access_key is not None
            else None
        ),
        secret_key=(
            settings.evidence_object_store_secret_key.get_secret_value()
            if settings.evidence_object_store_secret_key is not None
            else None
        ),
        bucket=settings.evidence_object_store_bucket,
        secure=settings.evidence_object_store_secure,
        create_bucket=settings.evidence_object_store_create_bucket,
        connect_timeout_seconds=settings.evidence_object_store_connect_timeout_seconds,
        read_timeout_seconds=settings.evidence_object_store_read_timeout_seconds,
        maximum_concurrency=settings.evidence_object_store_maximum_concurrency,
        maximum_retries=settings.evidence_object_store_maximum_retries,
    )
    return PngEvidenceArtifactWriter(
        store,
        bucket=settings.evidence_object_store_bucket,
        maximum_raw_bytes=settings.evidence_capture_maximum_raw_bytes,
        maximum_pixels=settings.evidence_capture_maximum_pixels,
    )


async def build_optional_evidence_artifact_writer(
    settings: BrowserWorkerSettings,
) -> BrowserArtifactWriter | None:
    """Keep capture fail-closed when the Browser Worker has no evidence store."""

    if not settings.evidence_store_configured:
        return None
    return await build_evidence_artifact_writer(settings)


async def build_evidence_object_reader(settings: Settings) -> EvidenceObjectReader:
    """Build the API's bounded independent object verifier."""

    store = await _build_store(
        endpoint=settings.evidence_object_store_endpoint,
        access_key=(
            settings.evidence_object_store_access_key.get_secret_value()
            if settings.evidence_object_store_access_key is not None
            else None
        ),
        secret_key=(
            settings.evidence_object_store_secret_key.get_secret_value()
            if settings.evidence_object_store_secret_key is not None
            else None
        ),
        bucket=settings.evidence_object_store_bucket,
        secure=settings.evidence_object_store_secure,
        create_bucket=settings.evidence_object_store_create_bucket,
        connect_timeout_seconds=settings.evidence_object_store_connect_timeout_seconds,
        read_timeout_seconds=settings.evidence_object_store_read_timeout_seconds,
        maximum_concurrency=settings.evidence_object_store_maximum_concurrency,
        maximum_retries=settings.evidence_object_store_maximum_retries,
    )
    return VerifiedEvidenceObjectReader(
        store,
        bucket=settings.evidence_object_store_bucket,
        maximum_bytes=settings.evidence_read_maximum_bytes,
    )


async def _build_store(
    *,
    endpoint: str | None,
    access_key: str | None,
    secret_key: str | None,
    bucket: str,
    secure: bool,
    create_bucket: bool,
    connect_timeout_seconds: float,
    read_timeout_seconds: float,
    maximum_concurrency: int,
    maximum_retries: int,
) -> MinioEvidenceObjectStore:
    if endpoint is None or access_key is None or secret_key is None:
        raise ValueError("evidence object store is not configured")
    normalized_endpoint = endpoint.strip()
    if not normalized_endpoint or "://" in normalized_endpoint or "/" in normalized_endpoint:
        raise ValueError("evidence object store endpoint must be host:port without a scheme")
    client = Minio(
        normalized_endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        http_client=PoolManager(
            num_pools=maximum_concurrency,
            maxsize=maximum_concurrency,
            block=True,
            timeout=Timeout(
                connect=connect_timeout_seconds,
                read=read_timeout_seconds,
            ),
            retries=Retry(
                total=maximum_retries,
                connect=maximum_retries,
                read=maximum_retries,
                status=maximum_retries,
                other=0,
                status_forcelist=(429, 500, 502, 503, 504),
                backoff_factor=0.2,
                backoff_max=2.0,
                respect_retry_after_header=False,
            ),
        ),
    )
    store = MinioEvidenceObjectStore(
        client,
        bucket=bucket,
        create_bucket=create_bucket,
        maximum_concurrency=maximum_concurrency,
    )
    await store.initialize()
    return store
