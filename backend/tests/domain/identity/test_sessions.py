"""Domain tests for browser session state and safe response projections."""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.domain.identity import (
    CredentialAuthMethod,
    EnsureLoginSession,
    LoginSessionReady,
    SessionArtifactRecord,
    SessionArtifactStatus,
)

ORIGIN = "https://staging.example.test"


def test_ensure_session_normalizes_origins_and_defaults_to_password() -> None:
    command = EnsureLoginSession(
        fencing_token=9,
        worker_identity="worker-auth-01",
        allowed_origins=("HTTPS://STAGING.EXAMPLE.TEST:443",),
    )

    assert command.auth_method is CredentialAuthMethod.PASSWORD
    assert command.allowed_origins == (ORIGIN,)


def test_ready_response_never_contains_artifact_storage_metadata() -> None:
    response = LoginSessionReady(
        browser_context_ref="bctx_" + "a" * 40,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    payload = response.model_dump(mode="json", by_alias=True)
    assert set(payload) == {"status", "browserContextRef", "expiresAt"}
    assert "objectRef" not in payload
    assert "storageState" not in payload


def test_creating_artifact_requires_an_opaque_cleanup_reference() -> None:
    now = datetime.now(UTC)
    common = {
        "id": uuid7(),
        "tenant_id": uuid7(),
        "project_id": uuid7(),
        "environment_id": uuid7(),
        "lease_id": uuid7(),
        "account_id": uuid7(),
        "connector_installation_id": uuid7(),
        "credential_binding_id": uuid7(),
        "lease_fence": 3,
        "worker_identity": "worker-auth-01",
        "browser_context_ref": "bctx_" + "b" * 40,
        "allowed_origins": (ORIGIN,),
        "auth_strength": (),
        "status": SessionArtifactStatus.CREATING,
        "object_ref": None,
        "object_digest": None,
        "object_size_bytes": None,
        "key_version": None,
        "account_revision": 2,
        "connector_revision": 3,
        "credential_revision": 4,
        "safe_summary": "session creation reserved",
        "failure_code": None,
        "created_at": now,
        "attempt_expires_at": now + timedelta(minutes=2),
        "ready_at": None,
        "expires_at": now + timedelta(minutes=10),
        "terminated_at": None,
        "termination_reason": None,
        "cleanup_claimed_at": None,
        "cleanup_worker_identity": None,
        "destroyed_at": None,
        "revision": 1,
        "updated_at": now,
    }

    with pytest.raises(ValidationError, match="metadata does not match status"):
        SessionArtifactRecord.model_validate(common)


def test_artifact_rejects_partial_sealed_object_metadata() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="object metadata must be complete"):
        SessionArtifactRecord(
            id=uuid7(),
            tenant_id=uuid7(),
            project_id=uuid7(),
            environment_id=uuid7(),
            lease_id=uuid7(),
            account_id=uuid7(),
            connector_installation_id=uuid7(),
            credential_binding_id=uuid7(),
            lease_fence=3,
            worker_identity="worker-auth-01",
            browser_context_ref="bctx_" + "c" * 40,
            allowed_origins=(ORIGIN,),
            auth_strength=(CredentialAuthMethod.PASSWORD,),
            status=SessionArtifactStatus.READY,
            object_ref="session-vault://atlas-sessions/tenants/a/sessions/b.json",
            object_digest="sha256:" + "d" * 64,
            object_size_bytes=None,
            key_version="local-v1",
            account_revision=2,
            connector_revision=3,
            credential_revision=4,
            safe_summary="authenticated browser session is ready",
            failure_code=None,
            created_at=now,
            attempt_expires_at=now + timedelta(minutes=2),
            ready_at=now + timedelta(seconds=1),
            expires_at=now + timedelta(minutes=10),
            terminated_at=None,
            termination_reason=None,
            cleanup_claimed_at=None,
            cleanup_worker_identity=None,
            destroyed_at=None,
            revision=2,
            updated_at=now + timedelta(seconds=1),
        )
