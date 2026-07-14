"""Secret-free Temporal session contracts and dispatcher unit tests."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid7

import pytest
from temporalio.client import Client

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.session_dispatcher import InlineAuthSessionDispatcher
from atlas_testops.application.session_janitor import SessionJanitorService
from atlas_testops.application.sessions import AuthSessionService
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.identity import (
    EnsureLoginSession,
    LoginSessionReady,
    SessionJanitorBatch,
)
from atlas_testops.orchestration.sessions import (
    AuthSessionActivities,
    AuthSessionActorPayload,
    EnsureAuthSessionWorkflowInput,
    SessionJanitorActivities,
    SessionJanitorWorkflowInput,
    TemporalAuthSessionDispatcher,
    WorkerOperationOutcome,
)

ORIGIN = "https://staging.example.test"


def actor_context() -> ActorContext:
    project_id = uuid7()
    return ActorContext(
        tenant_id=uuid7(),
        actor_id=uuid7(),
        request_id=f"orchestration-test-{uuid7()}",
        current_project_id=project_id,
        grants=(AccessGrant(role=PlatformRole.RUN_OPERATOR, project_id=project_id),),
    )


def ensure_command() -> EnsureLoginSession:
    return EnsureLoginSession(
        fencing_token=5,
        worker_identity="worker-orchestration-01",
        allowed_origins=(ORIGIN,),
    )


class FakeSessionService:
    def __init__(self, error: ApplicationError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[ActorContext, UUID, EnsureLoginSession]] = []

    async def ensure(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
    ) -> LoginSessionReady:
        self.calls.append((actor, lease_id, command))
        if self.error is not None:
            raise self.error
        return LoginSessionReady(
            browser_context_ref="bctx_" + "o" * 40,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


class FakeTemporalClient:
    def __init__(self, outcome: WorkerOperationOutcome) -> None:
        self.outcome = outcome
        self.calls: list[tuple[object, object, dict[str, object]]] = []

    async def execute_workflow(
        self,
        workflow: object,
        request: object,
        **kwargs: object,
    ) -> WorkerOperationOutcome:
        self.calls.append((workflow, request, kwargs))
        return self.outcome


class FakeJanitorService:
    async def run_once(
        self,
        actor: ActorContext,
        *,
        worker_identity: str,
        limit: int,
    ) -> SessionJanitorBatch:
        assert actor.tenant_id
        assert worker_identity == "worker-janitor-01"
        assert limit == 10
        return SessionJanitorBatch(
            expired=1,
            destroyed=2,
            failed=0,
            observed_at=datetime.now(UTC),
        )


def test_actor_and_command_payload_round_trip_without_secrets() -> None:
    actor = actor_context()
    lease_id = uuid7()
    payload = EnsureAuthSessionWorkflowInput.from_domain(
        actor,
        lease_id,
        ensure_command(),
        activity_timeout_seconds=30,
    )

    assert payload.actor.to_domain() == actor
    assert payload.command() == ensure_command()
    assert payload.lease_id == str(lease_id)
    assert "secret" not in repr(payload).casefold()
    assert "cookie" not in repr(payload).casefold()


@pytest.mark.anyio
async def test_activity_and_inline_dispatcher_return_safe_result() -> None:
    actor = actor_context()
    lease_id = uuid7()
    service = FakeSessionService()
    activities = AuthSessionActivities(cast(AuthSessionService, service))
    payload = EnsureAuthSessionWorkflowInput.from_domain(
        actor,
        lease_id,
        ensure_command(),
        activity_timeout_seconds=30,
    )

    outcome = await activities.ensure(payload)
    inline_result = await InlineAuthSessionDispatcher(
        cast(AuthSessionService, service)
    ).ensure(actor, lease_id, ensure_command())

    assert outcome.error_code is None
    assert outcome.result_json is not None
    assert isinstance(inline_result, LoginSessionReady)
    assert len(service.calls) == 2


@pytest.mark.anyio
async def test_activity_serializes_expected_business_error() -> None:
    error = ApplicationError(
        error_code=ErrorCode.LEASE_FENCED,
        title="Lease fenced",
        detail="safe detail",
        status_code=409,
        headers={"Retry-After": "1"},
    )
    service = FakeSessionService(error)
    activities = AuthSessionActivities(cast(AuthSessionService, service))
    payload = EnsureAuthSessionWorkflowInput.from_domain(
        actor_context(),
        uuid7(),
        ensure_command(),
        activity_timeout_seconds=30,
    )

    outcome = await activities.ensure(payload)

    with pytest.raises(ApplicationError) as raised:
        outcome.raise_for_error()
    assert raised.value.error_code is ErrorCode.LEASE_FENCED
    assert raised.value.headers == {"Retry-After": "1"}


def test_invalid_worker_outcome_and_dispatcher_configuration_fail_closed() -> None:
    invalid = WorkerOperationOutcome(
        result_json="{}",
        error_code=ErrorCode.INTERNAL_ERROR.value,
    )
    with pytest.raises(RuntimeError):
        invalid.raise_for_error()
    with pytest.raises(ValueError):
        TemporalAuthSessionDispatcher(
            cast(Client, object()),
            task_queue="atlas-auth-session",
            workflow_timeout=timedelta(seconds=5),
        )
    with pytest.raises(ValueError):
        TemporalAuthSessionDispatcher(
            cast(Client, object()),
            task_queue=" ",
            workflow_timeout=timedelta(seconds=30),
        )


@pytest.mark.anyio
async def test_temporal_dispatcher_parses_safe_success_and_business_error() -> None:
    success = WorkerOperationOutcome.success(
        LoginSessionReady(
            browser_context_ref="bctx_" + "d" * 40,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ).model_dump_json(by_alias=True)
    )
    client = FakeTemporalClient(success)
    dispatcher = TemporalAuthSessionDispatcher(
        cast(Client, client),
        task_queue="atlas-auth-session",
        workflow_timeout=timedelta(seconds=30),
    )
    actor = actor_context()
    lease_id = uuid7()

    result = await dispatcher.ensure(actor, lease_id, ensure_command())

    assert isinstance(result, LoginSessionReady)
    assert len(client.calls) == 1
    _, request, options = client.calls[0]
    assert isinstance(request, EnsureAuthSessionWorkflowInput)
    assert options["task_queue"] == "atlas-auth-session"
    assert str(actor.tenant_id.hex) in cast(str, options["id"])

    client.outcome = WorkerOperationOutcome.business_error(
        ApplicationError(
            error_code=ErrorCode.SESSION_UNAVAILABLE,
            title="Unavailable",
            detail="safe detail",
            status_code=503,
        )
    )
    with pytest.raises(ApplicationError) as raised:
        await dispatcher.ensure(actor, lease_id, ensure_command())
    assert raised.value.error_code is ErrorCode.SESSION_UNAVAILABLE


@pytest.mark.anyio
async def test_janitor_activity_returns_safe_batch() -> None:
    actor = actor_context()
    activities = SessionJanitorActivities(
        cast(SessionJanitorService, FakeJanitorService())
    )
    outcome = await activities.run_once(
        SessionJanitorWorkflowInput(
            actor=AuthSessionActorPayload.from_domain(actor),
            worker_identity="worker-janitor-01",
            limit=10,
        )
    )

    assert outcome.error_code is None
    assert outcome.result_json is not None
    assert '"destroyed":2' in outcome.result_json
