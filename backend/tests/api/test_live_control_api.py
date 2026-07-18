"""Public and machine-authenticated UnitAttempt live-control HTTP contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid7

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from atlas_testops.api.dependencies import (
    get_live_control_service as get_public_live_control_service,
)
from atlas_testops.api.internal.live_control import (
    get_live_control_service as get_runtime_live_control_service,
)
from atlas_testops.api.security import get_actor
from atlas_testops.application.access import ActorContext
from atlas_testops.application.live_control import LiveControlService
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.config import Settings
from atlas_testops.domain.runtime import (
    AcknowledgeLiveControl,
    CompleteLiveActionGrant,
    ConsumeLiveActionGrant,
    ControlLease,
    ControlLeaseState,
    HeartbeatLiveControl,
    InitializeLiveSession,
    LiveActionExecutionStatus,
    LiveActionGrant,
    LiveActionGrantState,
    LiveControlCommand,
    LiveControlCommandStatus,
    LiveControlCommandType,
    LiveControllerType,
    LiveSession,
    LiveSessionState,
    ReapedLiveControlBatch,
    RequestLiveActionGrant,
    UnitAttemptLiveSnapshot,
)
from atlas_testops.infrastructure.browser_auth import (
    BrowserRuntimePermitSigner,
    BrowserRuntimeRequestSigner,
)
from atlas_testops.main import create_app

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
TENANT_ID = uuid7()
PROJECT_ID = uuid7()
TASK_RUN_ID = uuid7()
UNIT_ID = uuid7()
ATTEMPT_ID = uuid7()
SESSION_ID = uuid7()
LEASE_ID = uuid7()
COMMAND_ID = uuid7()
GRANT_ID = uuid7()
ACTOR_ID = uuid7()
WORKER_ID = "browser-worker-api"
DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"


class RecordingLiveControlService:
    """Small typed double that records transport-to-application mapping."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.session = LiveSession(
            id=SESSION_ID,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            task_run_id=TASK_RUN_ID,
            execution_unit_id=UNIT_ID,
            unit_attempt_id=ATTEMPT_ID,
            execution_ticket_id=uuid7(),
            execution_ticket_digest=DIGEST_A,
            browser_session_id="browser-session-api",
            state=LiveSessionState.AGENT_CONTROLLED,
            control_epoch=3,
            fencing_token=3,
            browser_revision=5,
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        )
        self.lease = ControlLease(
            id=LEASE_ID,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            task_run_id=TASK_RUN_ID,
            execution_unit_id=UNIT_ID,
            unit_attempt_id=ATTEMPT_ID,
            live_session_id=SESSION_ID,
            owner_type=LiveControllerType.AGENT,
            owner_id=WORKER_ID,
            control_epoch=3,
            fencing_token=3,
            state=ControlLeaseState.ACTIVE,
            expires_at=NOW + timedelta(minutes=2),
            reason="agent control",
            created_at=NOW,
            updated_at=NOW,
        )
        self.command = LiveControlCommand(
            id=COMMAND_ID,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            task_run_id=TASK_RUN_ID,
            execution_unit_id=UNIT_ID,
            unit_attempt_id=ATTEMPT_ID,
            live_session_id=SESSION_ID,
            command_type=LiveControlCommandType.PAUSE,
            client_mutation_id="live-api-command",
            reason="operator request",
            expected_control_epoch=3,
            accepted_session_revision=2,
            status=LiveControlCommandStatus.PENDING,
            requested_by=ACTOR_ID,
            created_at=NOW,
            updated_at=NOW,
        )
        self.snapshot = UnitAttemptLiveSnapshot(
            session=self.session,
            lease=self.lease,
            observed_at=NOW,
        )
        self.grant = LiveActionGrant(
            grant_id=GRANT_ID,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            task_run_id=TASK_RUN_ID,
            execution_unit_id=UNIT_ID,
            unit_attempt_id=ATTEMPT_ID,
            live_session_id=SESSION_ID,
            control_lease_id=LEASE_ID,
            action_id=uuid7(),
            proposal_digest=DIGEST_A,
            browser_session_id="browser-session-api",
            page_id="page-main",
            page_revision=5,
            control_epoch=3,
            fencing_token=3,
            owner_type=LiveControllerType.AGENT,
            owner_id=WORKER_ID,
            allowed_adapter="click",
            expires_at=NOW + timedelta(seconds=15),
            policy_digest=DIGEST_B,
            state=LiveActionGrantState.ISSUED,
            created_at=NOW,
        )

    def _record(
        self,
        name: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> None:
        self.calls.append((name, args, kwargs))

    async def get_snapshot(self, *args: object, **kwargs: object) -> UnitAttemptLiveSnapshot:
        self._record("get_snapshot", args, kwargs)
        return self.snapshot

    async def get_command(self, *args: object, **kwargs: object) -> LiveControlCommand:
        self._record("get_command", args, kwargs)
        return self.command

    async def pause(
        self, *args: object, **kwargs: object
    ) -> CommandResult[LiveControlCommand]:
        return self._command_result("pause", LiveControlCommandType.PAUSE, args, kwargs)

    async def resume(
        self, *args: object, **kwargs: object
    ) -> CommandResult[LiveControlCommand]:
        return self._command_result("resume", LiveControlCommandType.RESUME, args, kwargs)

    async def takeover(
        self, *args: object, **kwargs: object
    ) -> CommandResult[LiveControlCommand]:
        return self._command_result(
            "takeover",
            LiveControlCommandType.TAKEOVER,
            args,
            kwargs,
        )

    async def return_control(
        self, *args: object, **kwargs: object
    ) -> CommandResult[LiveControlCommand]:
        return self._command_result(
            "return_control",
            LiveControlCommandType.RETURN,
            args,
            kwargs,
        )

    def _command_result(
        self,
        name: str,
        command_type: LiveControlCommandType,
        args: tuple[object, ...],
        kwargs: dict[str, object],
    ) -> CommandResult[LiveControlCommand]:
        self._record(name, args, kwargs)
        value = LiveControlCommand.model_validate(
            self.command.model_copy(
                update={"command_type": command_type},
            ).model_dump(mode="python")
        )
        return CommandResult(value=value, status_code=202, replayed=name == "resume")

    async def initialize(
        self, *args: object, **kwargs: object
    ) -> UnitAttemptLiveSnapshot:
        self._record("initialize", args, kwargs)
        return self.snapshot

    async def heartbeat(
        self, *args: object, **kwargs: object
    ) -> UnitAttemptLiveSnapshot:
        self._record("heartbeat", args, kwargs)
        return self.snapshot

    async def acknowledge(
        self, *args: object, **kwargs: object
    ) -> UnitAttemptLiveSnapshot:
        self._record("acknowledge", args, kwargs)
        return self.snapshot

    async def issue_action_grant(
        self, *args: object, **kwargs: object
    ) -> LiveActionGrant:
        self._record("issue_action_grant", args, kwargs)
        return self.grant

    async def get_action_grant(
        self, *args: object, **kwargs: object
    ) -> LiveActionGrant:
        self._record("get_action_grant", args, kwargs)
        return self.grant

    async def consume_action_grant(
        self, *args: object, **kwargs: object
    ) -> LiveActionGrant:
        self._record("consume_action_grant", args, kwargs)
        return self.grant

    async def complete_action_grant(
        self, *args: object, **kwargs: object
    ) -> LiveActionGrant:
        self._record("complete_action_grant", args, kwargs)
        return self.grant

    async def reap_expired(
        self, *args: object, **kwargs: object
    ) -> ReapedLiveControlBatch:
        self._record("reap_expired", args, kwargs)
        return ReapedLiveControlBatch(reaped=2, observed_at=NOW)


def _actor() -> ActorContext:
    return ActorContext(
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        request_id="live-control-api",
        development_override=True,
    )


def _app(
    service: RecordingLiveControlService,
    *,
    permit_signer: BrowserRuntimePermitSigner | None = None,
    request_signer: BrowserRuntimeRequestSigner | None = None,
) -> FastAPI:
    app = create_app(
        Settings(environment="test", cors_origins=[]),
        browser_runtime_permit_signer=permit_signer,
        browser_runtime_request_signer=request_signer,
    )
    app.dependency_overrides[get_actor] = _actor
    app.dependency_overrides[get_public_live_control_service] = lambda: cast(
        LiveControlService,
        service,
    )
    app.dependency_overrides[get_runtime_live_control_service] = lambda: cast(
        LiveControlService,
        service,
    )
    return app


def test_public_live_control_maps_etag_idempotency_and_existing_routes() -> None:
    service = RecordingLiveControlService()
    with TestClient(_app(service)) as client:
        snapshot = client.get(f"/v1/unit-attempts/{ATTEMPT_ID}/snapshot")
        responses = []
        for operation in ("pause", "resume", "takeover", "return"):
            responses.append(
                client.post(
                    f"/v1/unit-attempts/{ATTEMPT_ID}/{operation}",
                    json={"reason": "operator request"},
                    headers={
                        "If-Match": '"control-epoch-3"',
                        "Idempotency-Key": f"live-api-{operation}",
                    },
                )
            )
        command = client.get(
            f"/v1/unit-attempts/{ATTEMPT_ID}/commands/{COMMAND_ID}"
        )

    assert snapshot.status_code == 200
    assert snapshot.headers["etag"] == '"control-epoch-3"'
    assert snapshot.headers["cache-control"] == "private, no-store, max-age=0"
    assert all(response.status_code == 202 for response in responses)
    assert responses[0].headers["location"].endswith(f"/commands/{COMMAND_ID}")
    assert responses[1].headers["idempotency-replayed"] == "true"
    assert command.status_code == 200
    assert [call[0] for call in service.calls] == [
        "get_snapshot",
        "pause",
        "resume",
        "takeover",
        "return_control",
        "get_command",
    ]
    assert service.calls[1][2]["expected_control_epoch"] == 3


def test_internal_live_control_requires_exact_permit_and_maps_every_operation() -> None:
    service = RecordingLiveControlService()
    permit_signer = BrowserRuntimePermitSigner(
        b"p" * 32,
        maximum_lifetime=timedelta(hours=1),
    )
    request_signer = BrowserRuntimeRequestSigner(
        b"r" * 32,
        maximum_clock_skew=timedelta(seconds=30),
    )
    permit = permit_signer.mint(
        tenant_id=TENANT_ID,
        run_id=ATTEMPT_ID,
        worker_identity=WORKER_ID,
        issued_at=datetime.now(UTC) - timedelta(seconds=1),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    app = _app(
        service,
        permit_signer=permit_signer,
        request_signer=request_signer,
    )
    operations: tuple[tuple[str, str, BaseModel | None], ...] = (
        (
            "PUT",
            f"/internal/v1/unit-attempts/{ATTEMPT_ID}/live-session",
            InitializeLiveSession(
                browser_session_id="browser-session-api",
                owner_id=WORKER_ID,
            ),
        ),
        (
            "POST",
            f"/internal/v1/unit-attempts/{ATTEMPT_ID}/live-control:heartbeat",
            HeartbeatLiveControl(control_epoch=3, fencing_token=3),
        ),
        (
            "POST",
            f"/internal/v1/unit-attempts/{ATTEMPT_ID}/live-control:acknowledge",
            AcknowledgeLiveControl(
                command_id=COMMAND_ID,
                expected_control_epoch=3,
                expected_fencing_token=3,
                browser_revision=5,
                checkpoint_digest=DIGEST_A,
                agent_owner_id=WORKER_ID,
            ),
        ),
        (
            "POST",
            f"/internal/v1/unit-attempts/{ATTEMPT_ID}/action-grants",
            RequestLiveActionGrant(
                action_id=service.grant.action_id,
                proposal_digest=DIGEST_A,
                page_id="page-main",
                page_revision=5,
                control_epoch=3,
                fencing_token=3,
                allowed_adapter="click",
                policy_digest=DIGEST_B,
            ),
        ),
        (
            "GET",
            (
                f"/internal/v1/unit-attempts/{ATTEMPT_ID}"
                f"/action-grants/{GRANT_ID}"
            ),
            None,
        ),
        (
            "POST",
            (
                f"/internal/v1/unit-attempts/{ATTEMPT_ID}"
                f"/action-grants/{GRANT_ID}:consume"
            ),
            ConsumeLiveActionGrant(
                control_epoch=3,
                fencing_token=3,
                proposal_digest=DIGEST_A,
            ),
        ),
        (
            "POST",
            (
                f"/internal/v1/unit-attempts/{ATTEMPT_ID}"
                f"/action-grants/{GRANT_ID}:complete"
            ),
            CompleteLiveActionGrant(
                control_epoch=3,
                fencing_token=3,
                receipt_id=uuid7(),
                execution_status=LiveActionExecutionStatus.SUCCEEDED,
                resulting_page_revision=6,
            ),
        ),
    )
    with TestClient(app) as client:
        responses = []
        for method, path, model in operations:
            body = (
                model.model_dump_json(by_alias=True).encode()
                if model is not None
                else b""
            )
            headers = request_signer.sign_headers(
                method=method,
                path=path,
                body=body,
                tenant_id=TENANT_ID,
                worker_identity=WORKER_ID,
                permit=permit,
            )
            if model is not None:
                headers["Content-Type"] = "application/json"
            responses.append(
                client.request(method, path, content=body, headers=headers)
            )
        unauthorized = client.get(
            f"/internal/v1/unit-attempts/{ATTEMPT_ID}/action-grants/{GRANT_ID}"
        )
        reaped = client.post("/internal/v1/live-control:reap-expired?limit=25")

    assert all(response.status_code == 200 for response in responses)
    assert all(response.headers["cache-control"] == "no-store" for response in responses)
    assert unauthorized.status_code == 401
    assert unauthorized.headers["www-authenticate"] == "Atlas-HMAC"
    assert reaped.status_code == 200
    assert reaped.json()["reaped"] == 2
    assert [call[0] for call in service.calls] == [
        "initialize",
        "heartbeat",
        "acknowledge",
        "issue_action_grant",
        "get_action_grant",
        "consume_action_grant",
        "complete_action_grant",
        "reap_expired",
    ]
