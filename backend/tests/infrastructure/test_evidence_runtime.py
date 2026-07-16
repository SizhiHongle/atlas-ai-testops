"""Construction tests for bounded API and Browser Worker evidence stores."""

from typing import Any, cast

import pytest
from minio import Minio
from pydantic import SecretStr
from urllib3 import PoolManager
from urllib3.util import Retry, Timeout

from atlas_testops.core.config import BrowserWorkerSettings, Settings
from atlas_testops.infrastructure import evidence_runtime
from atlas_testops.infrastructure.evidence_store import (
    MinioEvidenceObjectStore,
    PngEvidenceArtifactWriter,
    VerifiedEvidenceObjectReader,
)


@pytest.mark.anyio
async def test_runtime_builders_use_bounded_minio_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[tuple[str, dict[str, Any]]] = []
    initialized: list[MinioEvidenceObjectStore] = []

    def fake_minio(endpoint: str, **kwargs: Any) -> Minio:
        clients.append((endpoint, kwargs))
        return cast(Minio, object())

    async def fake_initialize(store: MinioEvidenceObjectStore) -> None:
        initialized.append(store)

    monkeypatch.setattr(evidence_runtime, "Minio", fake_minio)
    monkeypatch.setattr(MinioEvidenceObjectStore, "initialize", fake_initialize)
    worker_settings = BrowserWorkerSettings(
        environment="test",
        evidence_object_store_endpoint="127.0.0.1:9000",
        evidence_object_store_access_key=SecretStr("writer"),
        evidence_object_store_secret_key=SecretStr("writer-secret"),
        evidence_object_store_create_bucket=True,
        evidence_object_store_connect_timeout_seconds=2,
        evidence_object_store_read_timeout_seconds=9,
        evidence_object_store_maximum_concurrency=3,
        evidence_object_store_maximum_retries=1,
    )
    api_settings = Settings(
        environment="test",
        evidence_object_store_endpoint="127.0.0.1:9000",
        evidence_object_store_access_key=SecretStr("reader"),
        evidence_object_store_secret_key=SecretStr("reader-secret"),
        evidence_object_store_connect_timeout_seconds=2,
        evidence_object_store_read_timeout_seconds=9,
        evidence_object_store_maximum_concurrency=3,
        evidence_object_store_maximum_retries=1,
    )

    writer = await evidence_runtime.build_evidence_artifact_writer(worker_settings)
    reader = await evidence_runtime.build_evidence_object_reader(api_settings)

    assert isinstance(writer, PngEvidenceArtifactWriter)
    assert isinstance(reader, VerifiedEvidenceObjectReader)
    assert len(initialized) == 2
    assert [endpoint for endpoint, _kwargs in clients] == [
        "127.0.0.1:9000",
        "127.0.0.1:9000",
    ]
    for _endpoint, kwargs in clients:
        assert kwargs["secure"] is False
        http_client = cast(PoolManager, kwargs["http_client"])
        assert http_client.connection_pool_kw["maxsize"] == 3
        assert http_client.connection_pool_kw["block"] is True
        timeout = cast(Timeout, http_client.connection_pool_kw["timeout"])
        assert timeout.connect_timeout == 2
        assert timeout.read_timeout == 9
        retries = cast(Retry, http_client.connection_pool_kw["retries"])
        assert retries.total == 1
        assert retries.other == 0


@pytest.mark.anyio
async def test_optional_writer_is_absent_without_store_configuration() -> None:
    assert (
        await evidence_runtime.build_optional_evidence_artifact_writer(
            BrowserWorkerSettings(environment="test")
        )
        is None
    )
