"""Internal Auth Session API contract tests without browser or Vault dependencies."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

from fastapi.testclient import TestClient

from atlas_testops.application.access import ActorContext
from atlas_testops.core.config import Settings
from atlas_testops.domain.identity import (
    EnsureLoginSession,
    EnsureLoginSessionResult,
    LoginSessionReady,
)
from atlas_testops.main import create_app

ORIGIN = "https://staging.example.test"


class RecordingAuthSessionDispatcher:
    """Return one safe ref while recording the normalized worker request."""

    def __init__(self) -> None:
        self.calls: list[tuple[ActorContext, UUID, EnsureLoginSession]] = []

    async def ensure(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
    ) -> EnsureLoginSessionResult:
        self.calls.append((actor, lease_id, command))
        return LoginSessionReady(
            browser_context_ref="bctx_" + "a" * 40,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )


def test_ensure_session_api_returns_only_safe_no_store_projection() -> None:
    dispatcher = RecordingAuthSessionDispatcher()
    tenant_id = uuid7()
    actor_id = uuid7()
    lease_id = uuid7()
    app = create_app(
        Settings(environment="test", cors_origins=[]),
        auth_session_dispatcher=dispatcher,
    )

    with TestClient(app) as client:
        response = client.post(
            f"/internal/v1/account-leases/{lease_id}:ensure-session",
            headers={
                "X-Atlas-Tenant-ID": str(tenant_id),
                "X-Atlas-Actor-ID": str(actor_id),
            },
            json={
                "fencingToken": 7,
                "workerIdentity": "worker-auth-session-01",
                "allowedOrigins": ["HTTPS://STAGING.EXAMPLE.TEST:443"],
            },
        )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.json() == {
        "status": "ready",
        "browserContextRef": "bctx_" + "a" * 40,
        "expiresAt": response.json()["expiresAt"],
    }
    serialized = response.text
    assert "objectRef" not in serialized
    assert "storageState" not in serialized
    assert "credential" not in serialized.casefold()
    assert len(dispatcher.calls) == 1
    actor, observed_lease_id, command = dispatcher.calls[0]
    assert actor.tenant_id == tenant_id
    assert actor.actor_id == actor_id
    assert observed_lease_id == lease_id
    assert command.allowed_origins == (ORIGIN,)


def test_ensure_session_api_fails_closed_without_dispatcher() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))

    with TestClient(app) as client:
        response = client.post(
            f"/internal/v1/account-leases/{uuid7()}:ensure-session",
            headers={"X-Atlas-Tenant-ID": str(uuid7())},
            json={
                "fencingToken": 1,
                "workerIdentity": "worker-auth-session-01",
                "allowedOrigins": [ORIGIN],
            },
        )

    assert response.status_code == 503
    assert response.json()["errorCode"] == "SESSION_UNAVAILABLE"
