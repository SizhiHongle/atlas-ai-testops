"""Application-level authorization and I/O boundaries for retained evidence."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.evidence import EvidenceService
from atlas_testops.application.ports.evidence import (
    EvidenceObjectDescriptor,
    EvidenceObjectIntegrityError,
    EvidenceObjectReader,
    EvidenceStoreUnavailableError,
)
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.runtime import (
    EvidenceManifest,
    EvidenceReadPurpose,
    IssueEvidenceReadGrant,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.evidence import (
    EvidenceArtifactScopeRecord,
    EvidenceReadGrantRecord,
)

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("20000000-0000-4000-8000-000000000002")
ENVIRONMENT_ID = UUID("30000000-0000-4000-8000-000000000003")
RUN_ID = UUID("40000000-0000-4000-8000-000000000004")
CONTRACT_ID = UUID("50000000-0000-4000-8000-000000000005")
ARTIFACT_ID = UUID("60000000-0000-4000-8000-000000000006")
MANIFEST_ID = UUID("70000000-0000-4000-8000-000000000007")
ACTOR_ID = UUID("80000000-0000-4000-8000-000000000008")
SESSION_ID = UUID("90000000-0000-4000-8000-000000000009")
GRANT_ID = UUID("a0000000-0000-4000-8000-00000000000a")
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
PAYLOAD = b"verified evidence bytes"


def _artifact() -> EvidenceArtifactScopeRecord:
    return EvidenceArtifactScopeRecord(
        artifact_id=ARTIFACT_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        environment_id=ENVIRONMENT_ID,
        debug_run_id=RUN_ID,
        execution_contract_id=CONTRACT_ID,
        kind="SCREENSHOT",
        object_ref=(
            "evidence://atlas-evidence-artifacts/"
            f"tenants/{TENANT_ID.hex}/projects/{PROJECT_ID.hex}/"
            f"environments/{ENVIRONMENT_ID.hex}/debug-runs/{RUN_ID.hex}/"
            f"contracts/{CONTRACT_ID.hex}/artifacts/{ARTIFACT_ID.hex}.png"
        ),
        content_digest=f"sha256:{sha256(PAYLOAD).hexdigest()}",
        size_bytes=len(PAYLOAD),
        mime_type="image/png",
        redaction_policy_digest="sha256:" + "b" * 64,
        integrity="VERIFIED",
        required=True,
        captured_at=NOW,
        manifest_id=MANIFEST_ID,
        manifest_digest="sha256:" + "c" * 64,
        outcome="PASSED",
        finalized_at=NOW + timedelta(seconds=1),
    )


def _grant(*, read_count: int = 0) -> EvidenceReadGrantRecord:
    return EvidenceReadGrantRecord(
        id=GRANT_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        environment_id=ENVIRONMENT_ID,
        debug_run_id=RUN_ID,
        execution_contract_id=CONTRACT_ID,
        artifact_id=ARTIFACT_ID,
        issued_to_actor_id=ACTOR_ID,
        platform_session_id=SESSION_ID,
        purpose=EvidenceReadPurpose.INLINE,
        max_reads=8,
        read_count=read_count,
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=60),
        last_read_at=NOW + timedelta(seconds=1) if read_count else None,
        revoked_at=None,
        revision=1 + read_count,
    )


def _actor() -> ActorContext:
    return ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        request_id="request-evidence-test",
        session_id=SESSION_ID,
        current_project_id=PROJECT_ID,
        grants=(AccessGrant(role=PlatformRole.RUN_OPERATOR, project_id=PROJECT_ID),),
    )


class RecordingDatabase:
    def __init__(self) -> None:
        self.active_transactions = 0
        self.contexts: list[DatabaseContext] = []

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        self.active_transactions += 1
        try:
            yield cast(AsyncConnection[DictRow], object())
        finally:
            self.active_transactions -= 1


class RecordingEvidenceRepository:
    def __init__(self) -> None:
        self.artifact = _artifact()
        self.issued: dict[str, Any] | None = None
        self.locked: dict[str, Any] | None = None
        self.redeemed = _grant(read_count=1)

    async def get_manifest_artifact(
        self,
        _connection: AsyncConnection[DictRow],
        *,
        debug_run_id: UUID,
        artifact_id: UUID,
    ) -> EvidenceArtifactScopeRecord | None:
        assert debug_run_id == RUN_ID
        assert artifact_id == ARTIFACT_ID
        return self.artifact

    async def lock_read_grant_scope(
        self,
        _connection: AsyncConnection[DictRow],
        **kwargs: Any,
    ) -> None:
        self.locked = kwargs

    async def revoke_active_read_grants(
        self,
        _connection: AsyncConnection[DictRow],
        **_kwargs: Any,
    ) -> int:
        return 1

    async def issue_read_grant(
        self,
        _connection: AsyncConnection[DictRow],
        **kwargs: Any,
    ) -> EvidenceReadGrantRecord:
        self.issued = kwargs
        return _grant()

    async def redeem_read_grant(
        self,
        _connection: AsyncConnection[DictRow],
        **_kwargs: Any,
    ) -> EvidenceReadGrantRecord | None:
        return self.redeemed


class RecordingAuditRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def append(
        self,
        _connection: AsyncConnection[DictRow],
        **kwargs: Any,
    ) -> None:
        self.events.append(kwargs)


class RecordingOutboxRepository:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def append(
        self,
        _connection: AsyncConnection[DictRow],
        event: DomainEvent,
    ) -> None:
        self.events.append(event)


class RecordingReader:
    def __init__(self, database: RecordingDatabase) -> None:
        self.database = database
        self.descriptors: list[EvidenceObjectDescriptor] = []

    async def read_verified(self, descriptor: EvidenceObjectDescriptor) -> bytes:
        assert self.database.active_transactions == 0
        self.descriptors.append(descriptor)
        return PAYLOAD


class FailingReader:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def read_verified(self, _descriptor: EvidenceObjectDescriptor) -> bytes:
        raise self.error


def _service(
    database: RecordingDatabase,
    repository: RecordingEvidenceRepository,
    reader: EvidenceObjectReader | None,
    audit: RecordingAuditRepository,
    outbox: RecordingOutboxRepository,
) -> EvidenceService:
    return EvidenceService(
        cast(Database, database),
        reader,
        grant_ttl=timedelta(seconds=60),
        maximum_reads=8,
        evidence_repository=cast(Any, repository),
        audit_repository=cast(Any, audit),
        outbox_repository=cast(Any, outbox),
    )


@pytest.mark.anyio
async def test_issue_grant_serializes_scope_and_never_persists_plaintext_token() -> None:
    database = RecordingDatabase()
    repository = RecordingEvidenceRepository()
    audit = RecordingAuditRepository()
    outbox = RecordingOutboxRepository()
    service = _service(
        database,
        repository,
        RecordingReader(database),
        audit,
        outbox,
    )

    result = await service.issue_read_grant(
        _actor(),
        RUN_ID,
        ARTIFACT_ID,
        IssueEvidenceReadGrant(purpose=EvidenceReadPurpose.INLINE),
    )

    assert result.read_token.startswith("evr_")
    assert repository.locked is not None
    assert repository.locked["platform_session_id"] == SESSION_ID
    assert repository.issued is not None
    assert repository.issued["token_hash"] == service.hash_read_token(result.read_token)
    serialized_internal_state = repr((repository.issued, audit.events, outbox.events))
    assert result.read_token not in serialized_internal_state
    assert audit.events[0]["event_type"] == "evidence_read_grant.issued"
    assert outbox.events[0].event_type == "evidence_read_grant.issued"
    assert database.active_transactions == 0


@pytest.mark.anyio
async def test_manifest_returns_only_matching_final_root_and_rejects_mismatch() -> None:
    database = RecordingDatabase()
    manifest_digest = "sha256:" + "c" * 64
    run = SimpleNamespace(
        id=RUN_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        environment_id=ENVIRONMENT_ID,
        evidence_manifest_id=MANIFEST_ID,
        evidence_manifest_digest=manifest_digest,
    )
    manifest = EvidenceManifest.model_construct(
        debug_run_id=RUN_ID,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        content_digest=manifest_digest,
    )

    class RunRepository:
        async def get_run(
            self,
            _connection: AsyncConnection[DictRow],
            _run_id: UUID,
        ) -> object:
            return run

    class RuntimeRepository:
        def __init__(self, value: EvidenceManifest) -> None:
            self.manifest = value

        async def get_evidence_manifest(
            self,
            _connection: AsyncConnection[DictRow],
            _manifest_id: UUID,
        ) -> EvidenceManifest:
            return self.manifest

    runtime_repository = RuntimeRepository(manifest)

    service = EvidenceService(
        cast(Database, database),
        None,
        grant_ttl=timedelta(seconds=60),
        maximum_reads=8,
        debug_run_repository=cast(Any, RunRepository()),
        runtime_repository=cast(Any, runtime_repository),
    )

    loaded = await service.get_manifest(_actor(), RUN_ID)
    assert loaded is manifest

    runtime_repository.manifest = manifest.model_copy(
        update={"content_digest": "sha256:" + "d" * 64}
    )
    with pytest.raises(ApplicationError) as mismatch:
        await service.get_manifest(_actor(), RUN_ID)
    assert mismatch.value.error_code is ErrorCode.EVIDENCE_INTEGRITY_FAILED


@pytest.mark.anyio
async def test_read_closes_transaction_before_object_io_and_passes_exact_scope() -> None:
    database = RecordingDatabase()
    repository = RecordingEvidenceRepository()
    audit = RecordingAuditRepository()
    outbox = RecordingOutboxRepository()
    reader = RecordingReader(database)
    service = _service(database, repository, reader, audit, outbox)
    token = "evr_" + "x" * 48

    content = await service.read_content(
        _actor(),
        ARTIFACT_ID,
        read_token=token,
        purpose=EvidenceReadPurpose.INLINE,
    )

    assert content.payload == PAYLOAD
    assert content.content_disposition == "inline"
    assert len(reader.descriptors) == 1
    descriptor = reader.descriptors[0]
    assert descriptor.artifact_id == ARTIFACT_ID
    assert descriptor.tenant_id == TENANT_ID
    assert descriptor.project_id == PROJECT_ID
    assert descriptor.debug_run_id == RUN_ID
    assert descriptor.execution_contract_id == CONTRACT_ID
    assert audit.events[0]["event_type"] == "evidence_read_grant.redeemed"
    assert token not in repr(audit.events)
    assert database.active_transactions == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("reader_error", "expected_code", "expected_status", "failure_event"),
    [
        (
            EvidenceObjectIntegrityError("tampered"),
            ErrorCode.EVIDENCE_INTEGRITY_FAILED,
            409,
            "evidence_artifact.integrity_failed",
        ),
        (
            EvidenceStoreUnavailableError("timeout"),
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            503,
            "evidence_artifact.read_unavailable",
        ),
    ],
)
async def test_read_maps_object_failures_without_leaking_unverified_bytes(
    reader_error: Exception,
    expected_code: ErrorCode,
    expected_status: int,
    failure_event: str,
) -> None:
    database = RecordingDatabase()
    repository = RecordingEvidenceRepository()
    audit = RecordingAuditRepository()
    service = _service(
        database,
        repository,
        cast(EvidenceObjectReader, FailingReader(reader_error)),
        audit,
        RecordingOutboxRepository(),
    )

    with pytest.raises(ApplicationError) as captured:
        await service.read_content(
            _actor(),
            ARTIFACT_ID,
            read_token="evr_" + "x" * 48,
            purpose=EvidenceReadPurpose.INLINE,
        )

    assert captured.value.error_code is expected_code
    assert captured.value.status_code == expected_status
    assert [event["event_type"] for event in audit.events] == [
        "evidence_read_grant.redeemed",
        failure_event,
    ]
    assert database.active_transactions == 0


@pytest.mark.anyio
async def test_issue_and_read_fail_closed_without_reader_or_workspace_scope() -> None:
    database = RecordingDatabase()
    repository = RecordingEvidenceRepository()
    audit = RecordingAuditRepository()
    service = _service(
        database,
        repository,
        None,
        audit,
        RecordingOutboxRepository(),
    )

    with pytest.raises(ApplicationError) as unavailable:
        await service.issue_read_grant(
            _actor(),
            RUN_ID,
            ARTIFACT_ID,
            IssueEvidenceReadGrant(purpose=EvidenceReadPurpose.INLINE),
        )
    assert unavailable.value.status_code == 503

    scoped_service = _service(
        database,
        repository,
        RecordingReader(database),
        audit,
        RecordingOutboxRepository(),
    )
    base_actor = _actor()
    wrong_workspace = ActorContext(
        tenant_id=base_actor.tenant_id,
        actor_id=base_actor.actor_id,
        request_id=base_actor.request_id,
        session_id=base_actor.session_id,
        current_project_id=UUID("b0000000-0000-4000-8000-00000000000b"),
        grants=base_actor.grants,
    )
    with pytest.raises(ApplicationError) as forbidden:
        await scoped_service.issue_read_grant(
            wrong_workspace,
            RUN_ID,
            ARTIFACT_ID,
            IssueEvidenceReadGrant(purpose=EvidenceReadPurpose.INLINE),
        )
    assert forbidden.value.status_code == 403
