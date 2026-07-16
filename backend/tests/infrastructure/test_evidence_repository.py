"""Unit tests for private artifact lookup and bounded read grant SQL."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from atlas_testops.domain.runtime import EvidenceReadPurpose
from atlas_testops.infrastructure.repositories.evidence import (
    EvidenceArtifactScopeRecord,
    EvidenceReadGrantRecord,
    EvidenceRepository,
)


class StubCursor:
    """Return deterministic rows from repository SQL without emulating psycopg."""

    def __init__(
        self,
        *,
        row: DictRow | None = None,
        rows: tuple[DictRow, ...] = (),
        rowcount: int = -1,
    ) -> None:
        self._row = row
        self._rows = rows
        self.rowcount = rowcount

    async def fetchone(self) -> DictRow | None:
        return self._row

    async def fetchall(self) -> list[DictRow]:
        return list(self._rows)


class StubConnection:
    """Record SQL and return preloaded cursors in call order."""

    def __init__(self, *cursors: StubCursor) -> None:
        self._cursors = list(cursors)
        self.calls: list[tuple[str, Sequence[object] | None]] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> StubCursor:
        self.calls.append((query, params))
        return self._cursors.pop(0)


def _artifact_row() -> DictRow:
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    return cast(
        DictRow,
        {
            "artifact_id": UUID("10000000-0000-4000-8000-000000000001"),
            "tenant_id": UUID("20000000-0000-4000-8000-000000000001"),
            "project_id": UUID("30000000-0000-4000-8000-000000000001"),
            "environment_id": UUID("40000000-0000-4000-8000-000000000001"),
            "debug_run_id": UUID("50000000-0000-4000-8000-000000000001"),
            "execution_contract_id": UUID("60000000-0000-4000-8000-000000000001"),
            "kind": "SCREENSHOT",
            "object_ref": "evidence://tests/finalized/screenshot.png",
            "content_digest": f"sha256:{'a' * 64}",
            "size_bytes": 1024,
            "mime_type": "image/png",
            "redaction_policy_digest": f"sha256:{'b' * 64}",
            "integrity": "VERIFIED",
            "required": True,
            "captured_at": now,
            "manifest_id": UUID("70000000-0000-4000-8000-000000000001"),
            "manifest_digest": f"sha256:{'c' * 64}",
            "outcome": "PASSED",
            "finalized_at": now + timedelta(seconds=1),
        },
    )


def _grant_row(*, read_count: int = 0) -> DictRow:
    artifact = _artifact_row()
    now = datetime(2026, 7, 15, 8, 1, tzinfo=UTC)
    return cast(
        DictRow,
        {
            "id": UUID("80000000-0000-4000-8000-000000000001"),
            "tenant_id": artifact["tenant_id"],
            "project_id": artifact["project_id"],
            "environment_id": artifact["environment_id"],
            "debug_run_id": artifact["debug_run_id"],
            "execution_contract_id": artifact["execution_contract_id"],
            "artifact_id": artifact["artifact_id"],
            "issued_to_actor_id": UUID("90000000-0000-4000-8000-000000000001"),
            "platform_session_id": None,
            "purpose": "INLINE",
            "max_reads": 2,
            "read_count": read_count,
            "created_at": now,
            "expires_at": now + timedelta(seconds=60),
            "last_read_at": now + timedelta(seconds=1) if read_count else None,
            "revoked_at": None,
            "revision": read_count + 1,
        },
    )


@pytest.mark.anyio
async def test_repository_loads_manifest_rooted_artifact_and_lists_verified_only() -> None:
    artifact_row = _artifact_row()
    connection = StubConnection(
        StubCursor(row=artifact_row),
        StubCursor(rows=(artifact_row,)),
    )
    repository = EvidenceRepository()

    artifact = await repository.get_manifest_artifact(
        cast(AsyncConnection[DictRow], connection),
        debug_run_id=artifact_row["debug_run_id"],
        artifact_id=artifact_row["artifact_id"],
    )
    listed = await repository.list_manifest_artifacts(
        cast(AsyncConnection[DictRow], connection),
        manifest_id=artifact_row["manifest_id"],
    )

    assert artifact == EvidenceArtifactScopeRecord(**artifact_row)
    assert listed == (artifact,)
    assert "run.lifecycle = 'TERMINATED'" in connection.calls[0][0]
    assert "item ->> 'integrity' = artifact.integrity" in connection.calls[0][0]
    assert "artifact.integrity = 'VERIFIED'" not in connection.calls[0][0]
    assert "artifact.integrity = 'VERIFIED'" in connection.calls[1][0]
    assert "artifact.object_ref" in connection.calls[0][0]
    assert "evidence://" not in repr(artifact)


@pytest.mark.anyio
async def test_repository_issues_hash_only_exact_scope_grant() -> None:
    connection = StubConnection(StubCursor(row=_grant_row()))
    repository = EvidenceRepository()
    artifact = EvidenceArtifactScopeRecord(**_artifact_row())
    token_hash = "d" * 64
    created_at = datetime(2026, 7, 15, 8, 1, tzinfo=UTC)

    grant = await repository.issue_read_grant(
        cast(AsyncConnection[DictRow], connection),
        grant_id=UUID("80000000-0000-4000-8000-000000000001"),
        token_hash=token_hash,
        artifact=artifact,
        issued_to_actor_id=UUID("90000000-0000-4000-8000-000000000001"),
        platform_session_id=None,
        purpose=EvidenceReadPurpose.INLINE,
        max_reads=2,
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=60),
    )

    assert isinstance(grant, EvidenceReadGrantRecord)
    query, params = connection.calls[0]
    assert "insert into atlas.evidence_read_grant" in query
    assert params is not None
    assert params[1] == token_hash
    assert params[2:8] == (
        artifact.tenant_id,
        artifact.project_id,
        artifact.environment_id,
        artifact.debug_run_id,
        artifact.execution_contract_id,
        artifact.artifact_id,
    )


@pytest.mark.anyio
async def test_repository_serializes_exact_read_grant_scope() -> None:
    connection = StubConnection(StubCursor())
    repository = EvidenceRepository()
    artifact = _artifact_row()

    await repository.lock_read_grant_scope(
        cast(AsyncConnection[DictRow], connection),
        tenant_id=artifact["tenant_id"],
        artifact_id=artifact["artifact_id"],
        issued_to_actor_id=UUID("90000000-0000-4000-8000-000000000001"),
        platform_session_id=None,
        purpose=EvidenceReadPurpose.INLINE,
    )

    query, params = connection.calls[0]
    assert "pg_advisory_xact_lock(hashtextextended(%s, 0))" in query
    assert params is not None
    assert str(artifact["tenant_id"]) in str(params[0])
    assert str(artifact["artifact_id"]) in str(params[0])
    assert str(params[0]).endswith(":development:INLINE")


@pytest.mark.anyio
async def test_repository_redeems_one_read_atomically_and_binds_purpose() -> None:
    connection = StubConnection(
        StubCursor(row=_grant_row(read_count=1)),
        StubCursor(row=None),
    )
    repository = EvidenceRepository()
    grant_row = _grant_row()
    redeemed_at = grant_row["created_at"] + timedelta(seconds=1)

    redeemed = await repository.redeem_read_grant(
        cast(AsyncConnection[DictRow], connection),
        tenant_id=grant_row["tenant_id"],
        token_hash="d" * 64,
        artifact_id=grant_row["artifact_id"],
        issued_to_actor_id=grant_row["issued_to_actor_id"],
        platform_session_id=None,
        purpose=EvidenceReadPurpose.INLINE,
        redeemed_at=redeemed_at,
    )
    rejected = await repository.redeem_read_grant(
        cast(AsyncConnection[DictRow], connection),
        tenant_id=grant_row["tenant_id"],
        token_hash="d" * 64,
        artifact_id=grant_row["artifact_id"],
        issued_to_actor_id=grant_row["issued_to_actor_id"],
        platform_session_id=None,
        purpose=EvidenceReadPurpose.DOWNLOAD,
        redeemed_at=redeemed_at,
    )

    assert redeemed is not None and redeemed.read_count == 1
    assert rejected is None
    query, params = connection.calls[0]
    assert "set read_count = read_count + 1" in query
    assert "read_grant.read_count < read_grant.max_reads" in query
    assert "read_grant.created_at <= %s" in query
    assert "read_grant.purpose = %s" in query
    assert "session.revoked_at is null" in query
    assert params is not None and params[6] is EvidenceReadPurpose.INLINE


@pytest.mark.anyio
async def test_repository_revokes_without_deleting_grant_fact() -> None:
    row = _grant_row()
    revoked_row = cast(DictRow, {**row, "revoked_at": row["expires_at"]})
    connection = StubConnection(StubCursor(row=revoked_row))
    repository = EvidenceRepository()

    revoked = await repository.revoke_read_grant(
        cast(AsyncConnection[DictRow], connection),
        tenant_id=row["tenant_id"],
        grant_id=row["id"],
        artifact_id=row["artifact_id"],
        issued_to_actor_id=row["issued_to_actor_id"],
        revoked_at=row["expires_at"],
    )

    assert revoked is not None and revoked.revoked_at == row["expires_at"]
    assert "set revoked_at = %s" in connection.calls[0][0]
    assert "delete" not in connection.calls[0][0].casefold()


@pytest.mark.anyio
async def test_repository_revokes_prior_active_exact_scope_grants() -> None:
    row = _grant_row()
    connection = StubConnection(StubCursor(rowcount=2))
    repository = EvidenceRepository()

    revoked_count = await repository.revoke_active_read_grants(
        cast(AsyncConnection[DictRow], connection),
        tenant_id=row["tenant_id"],
        artifact_id=row["artifact_id"],
        issued_to_actor_id=row["issued_to_actor_id"],
        platform_session_id=None,
        purpose=EvidenceReadPurpose.INLINE,
        revoked_at=row["created_at"] + timedelta(seconds=5),
    )

    assert revoked_count == 2
    query, params = connection.calls[0]
    assert "platform_session_id is not distinct from %s" in query
    assert "expires_at > %s" in query
    assert params is not None and params[5] is EvidenceReadPurpose.INLINE
