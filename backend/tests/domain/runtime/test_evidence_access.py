"""Evidence read grant wire boundaries and expiry invariants."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_testops.domain.runtime import (
    EvidenceReadGrant,
    EvidenceReadPurpose,
    IssueEvidenceReadGrant,
)

GRANT_ID = UUID("10000000-0000-4000-8000-000000000001")
ARTIFACT_ID = UUID("20000000-0000-4000-8000-000000000002")
TOKEN = "evr_" + "a" * 43
ISSUED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _grant(**overrides: object) -> EvidenceReadGrant:
    values: dict[str, object] = {
        "id": GRANT_ID,
        "artifact_id": ARTIFACT_ID,
        "purpose": EvidenceReadPurpose.INLINE,
        "read_token": TOKEN,
        "issued_at": ISSUED_AT,
        "expires_at": ISSUED_AT + timedelta(seconds=60),
        "max_reads": 8,
    }
    values.update(overrides)
    return EvidenceReadGrant.model_validate(values)


def test_issue_command_accepts_only_a_bounded_purpose() -> None:
    command = IssueEvidenceReadGrant(purpose=EvidenceReadPurpose.DOWNLOAD)

    assert command.model_dump(mode="json", by_alias=True) == {"purpose": "DOWNLOAD"}
    with pytest.raises(ValidationError):
        IssueEvidenceReadGrant.model_validate({"purpose": "PREVIEW"})
    with pytest.raises(ValidationError):
        IssueEvidenceReadGrant.model_validate({"purpose": "INLINE", "artifactId": str(ARTIFACT_ID)})


def test_grant_serializes_camel_case_but_hides_token_from_repr() -> None:
    grant = _grant()

    payload = grant.model_dump(mode="json", by_alias=True)
    assert payload == {
        "id": str(GRANT_ID),
        "artifactId": str(ARTIFACT_ID),
        "purpose": "INLINE",
        "readToken": TOKEN,
        "issuedAt": "2026-07-15T12:00:00Z",
        "expiresAt": "2026-07-15T12:01:00Z",
        "maxReads": 8,
    }
    assert TOKEN not in repr(grant)
    assert "read_token" not in repr(grant)


@pytest.mark.parametrize(
    "read_token",
    [
        "evr_short",
        "sgr_" + "a" * 43,
        "evr_" + "a" * 31,
        "evr_" + "a" * 201,
        "evr_" + "a" * 42 + ".",
    ],
)
def test_grant_rejects_malformed_tokens(read_token: str) -> None:
    with pytest.raises(ValidationError):
        _grant(read_token=read_token)


@pytest.mark.parametrize(
    "read_token",
    ["evr_" + "a" * 32, "evr_" + "a" * 200],
)
def test_grant_accepts_token_length_boundaries(read_token: str) -> None:
    assert _grant(read_token=read_token).read_token == read_token


@pytest.mark.parametrize("max_reads", [0, 33])
def test_grant_rejects_unbounded_read_counts(max_reads: int) -> None:
    with pytest.raises(ValidationError):
        _grant(max_reads=max_reads)


@pytest.mark.parametrize(
    "expires_at",
    [
        ISSUED_AT,
        ISSUED_AT - timedelta(microseconds=1),
        ISSUED_AT + timedelta(minutes=2, microseconds=1),
    ],
)
def test_grant_rejects_invalid_time_windows(expires_at: datetime) -> None:
    with pytest.raises(ValidationError):
        _grant(expires_at=expires_at)


def test_grant_accepts_the_maximum_time_and_read_boundaries() -> None:
    grant = _grant(
        expires_at=ISSUED_AT + timedelta(minutes=2),
        max_reads=32,
    )

    assert grant.expires_at - grant.issued_at == timedelta(minutes=2)
    assert grant.max_reads == 32


@pytest.mark.parametrize("field", ["issued_at", "expires_at"])
def test_grant_rejects_naive_timestamps(field: str) -> None:
    with pytest.raises(ValidationError):
        _grant(**{field: datetime(2026, 7, 15, 12, 0)})
