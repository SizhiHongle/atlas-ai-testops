"""Temporal contracts for isolated Auth Session and Janitor activities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, cast
from uuid import UUID

from temporalio import activity, workflow
from temporalio.client import Client, WorkflowFailureError
from temporalio.common import (
    RetryPolicy,
    WorkflowIDConflictPolicy,
    WorkflowIDReusePolicy,
)
from temporalio.service import RPCError

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.session_dispatcher import AuthSessionDispatcher
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.identity import (
    CredentialAuthMethod,
    EnsureLoginSession,
    EnsureLoginSessionResult,
    ensure_login_session_result_adapter,
)

if TYPE_CHECKING:
    from atlas_testops.application.session_janitor import SessionJanitorService
    from atlas_testops.application.sessions import AuthSessionService

ENSURE_AUTH_SESSION_ACTIVITY_NAME = "atlas.ensure-auth-session/0.1"
ENSURE_AUTH_SESSION_WORKFLOW_NAME = "atlas.ensure-auth-session-workflow/0.1"
RUN_SESSION_JANITOR_ACTIVITY_NAME = "atlas.run-session-janitor/0.1"
RUN_SESSION_JANITOR_WORKFLOW_NAME = "atlas.run-session-janitor-workflow/0.1"


@dataclass(frozen=True, slots=True)
class AuthSessionGrantPayload:
    """Serializable projection of one already-authenticated access grant."""

    role: str
    project_id: str | None


@dataclass(frozen=True, slots=True)
class AuthSessionActorPayload:
    """Serializable actor context that never contains cookies or credentials."""

    tenant_id: str
    actor_id: str | None
    request_id: str
    current_project_id: str | None
    grants: tuple[AuthSessionGrantPayload, ...]
    development_override: bool

    @classmethod
    def from_domain(cls, actor: ActorContext) -> AuthSessionActorPayload:
        return cls(
            tenant_id=str(actor.tenant_id),
            actor_id=str(actor.actor_id) if actor.actor_id is not None else None,
            request_id=actor.request_id,
            current_project_id=(
                str(actor.current_project_id)
                if actor.current_project_id is not None
                else None
            ),
            grants=tuple(
                AuthSessionGrantPayload(
                    role=grant.role.value,
                    project_id=(
                        str(grant.project_id) if grant.project_id is not None else None
                    ),
                )
                for grant in actor.grants
            ),
            development_override=actor.development_override,
        )

    def to_domain(self) -> ActorContext:
        return ActorContext(
            tenant_id=UUID(self.tenant_id),
            actor_id=UUID(self.actor_id) if self.actor_id is not None else None,
            request_id=self.request_id,
            current_project_id=(
                UUID(self.current_project_id)
                if self.current_project_id is not None
                else None
            ),
            grants=tuple(
                AccessGrant(
                    role=PlatformRole(grant.role),
                    project_id=(
                        UUID(grant.project_id) if grant.project_id is not None else None
                    ),
                )
                for grant in self.grants
            ),
            development_override=self.development_override,
        )


@dataclass(frozen=True, slots=True)
class EnsureAuthSessionWorkflowInput:
    """Versioned, secret-free input for one idempotent session request."""

    actor: AuthSessionActorPayload
    lease_id: str
    fencing_token: int
    worker_identity: str
    allowed_origins: tuple[str, ...]
    auth_method: str
    ttl_seconds: int | None
    activity_timeout_seconds: int

    @classmethod
    def from_domain(
        cls,
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
        *,
        activity_timeout_seconds: int,
    ) -> EnsureAuthSessionWorkflowInput:
        return cls(
            actor=AuthSessionActorPayload.from_domain(actor),
            lease_id=str(lease_id),
            fencing_token=command.fencing_token,
            worker_identity=command.worker_identity,
            allowed_origins=command.allowed_origins,
            auth_method=command.auth_method.value,
            ttl_seconds=command.ttl_seconds,
            activity_timeout_seconds=activity_timeout_seconds,
        )

    def command(self) -> EnsureLoginSession:
        return EnsureLoginSession(
            fencing_token=self.fencing_token,
            worker_identity=self.worker_identity,
            allowed_origins=self.allowed_origins,
            auth_method=CredentialAuthMethod(self.auth_method),
            ttl_seconds=self.ttl_seconds,
        )


@dataclass(frozen=True, slots=True)
class WorkerOperationOutcome:
    """Safe success-or-business-error envelope returned by an activity."""

    result_json: str | None = None
    error_code: str | None = None
    error_title: str | None = None
    error_detail: str | None = None
    error_status_code: int | None = None
    error_headers: tuple[tuple[str, str], ...] = ()

    @classmethod
    def success(cls, result_json: str) -> WorkerOperationOutcome:
        return cls(result_json=result_json)

    @classmethod
    def business_error(cls, error: ApplicationError) -> WorkerOperationOutcome:
        return cls(
            error_code=error.error_code.value,
            error_title=error.title,
            error_detail=error.detail,
            error_status_code=error.status_code,
            error_headers=tuple(sorted(error.headers.items())),
        )

    def raise_for_error(self) -> None:
        if self.error_code is None:
            return
        if (
            self.result_json is not None
            or self.error_title is None
            or self.error_detail is None
            or self.error_status_code is None
        ):
            raise RuntimeError("Auth Session Worker returned an invalid outcome")
        raise ApplicationError(
            error_code=ErrorCode(self.error_code),
            title=self.error_title,
            detail=self.error_detail,
            status_code=self.error_status_code,
            headers=dict(self.error_headers),
        )


class AuthSessionActivities:
    """Activity wrapper that converts only expected business failures to safe data."""

    def __init__(self, service: AuthSessionService) -> None:
        self._service = service

    @activity.defn(name=ENSURE_AUTH_SESSION_ACTIVITY_NAME)
    async def ensure(
        self,
        request: EnsureAuthSessionWorkflowInput,
    ) -> WorkerOperationOutcome:
        try:
            result = await self._service.ensure(
                request.actor.to_domain(),
                UUID(request.lease_id),
                request.command(),
            )
        except ApplicationError as error:
            return WorkerOperationOutcome.business_error(error)
        return WorkerOperationOutcome.success(result.model_dump_json(by_alias=True))


@workflow.defn(name=ENSURE_AUTH_SESSION_WORKFLOW_NAME)
class EnsureAuthSessionWorkflow:
    """Execute one retry-safe Auth Session activity on the isolated task queue."""

    @workflow.run
    async def run(
        self,
        request: EnsureAuthSessionWorkflowInput,
    ) -> WorkerOperationOutcome:
        outcome = await workflow.execute_activity(
            ENSURE_AUTH_SESSION_ACTIVITY_NAME,
            request,
            result_type=WorkerOperationOutcome,
            start_to_close_timeout=timedelta(seconds=request.activity_timeout_seconds),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1),
                maximum_interval=timedelta(seconds=5),
                maximum_attempts=2,
            ),
        )
        return cast(WorkerOperationOutcome, outcome)


@dataclass(frozen=True, slots=True)
class SessionJanitorWorkflowInput:
    """Tenant-scoped command for one bounded cleanup pass."""

    actor: AuthSessionActorPayload
    worker_identity: str
    limit: int
    activity_timeout_seconds: int = 60


class SessionJanitorActivities:
    """Run ciphertext deletion only in the worker that owns the Vault."""

    def __init__(self, service: SessionJanitorService) -> None:
        self._service = service

    @activity.defn(name=RUN_SESSION_JANITOR_ACTIVITY_NAME)
    async def run_once(
        self,
        request: SessionJanitorWorkflowInput,
    ) -> WorkerOperationOutcome:
        try:
            result = await self._service.run_once(
                request.actor.to_domain(),
                worker_identity=request.worker_identity,
                limit=request.limit,
            )
        except ApplicationError as error:
            return WorkerOperationOutcome.business_error(error)
        return WorkerOperationOutcome.success(result.model_dump_json(by_alias=True))


@workflow.defn(name=RUN_SESSION_JANITOR_WORKFLOW_NAME)
class RunSessionJanitorWorkflow:
    """Execute one bounded cleanup activity without putting Vault code in the API."""

    @workflow.run
    async def run(
        self,
        request: SessionJanitorWorkflowInput,
    ) -> WorkerOperationOutcome:
        outcome = await workflow.execute_activity(
            RUN_SESSION_JANITOR_ACTIVITY_NAME,
            request,
            result_type=WorkerOperationOutcome,
            start_to_close_timeout=timedelta(seconds=request.activity_timeout_seconds),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        return cast(WorkerOperationOutcome, outcome)


class TemporalAuthSessionDispatcher(AuthSessionDispatcher):
    """Submit secret-free requests to the dedicated Temporal task queue."""

    def __init__(
        self,
        client: Client,
        *,
        task_queue: str,
        workflow_timeout: timedelta,
    ) -> None:
        if workflow_timeout <= timedelta(seconds=5):
            raise ValueError("auth session workflow timeout must exceed five seconds")
        normalized_queue = task_queue.strip()
        if not normalized_queue:
            raise ValueError("auth session task queue must not be blank")
        self._client = client
        self._task_queue = normalized_queue
        self._workflow_timeout = workflow_timeout

    async def ensure(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
    ) -> EnsureLoginSessionResult:
        activity_timeout_seconds = max(
            5,
            int(self._workflow_timeout.total_seconds()) - 5,
        )
        request = EnsureAuthSessionWorkflowInput.from_domain(
            actor,
            lease_id,
            command,
            activity_timeout_seconds=activity_timeout_seconds,
        )
        try:
            raw_outcome = await self._client.execute_workflow(
                EnsureAuthSessionWorkflow.run,
                request,
                id=self._workflow_id(actor, lease_id, command),
                task_queue=self._task_queue,
                execution_timeout=self._workflow_timeout,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except (RPCError, WorkflowFailureError) as error:
            raise ApplicationError(
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                title="Auth Session Worker 不可用",
                detail="Session 请求未能由独立 Worker 完成，请稍后重试。",
                status_code=503,
            ) from error
        outcome = raw_outcome
        outcome.raise_for_error()
        if outcome.result_json is None:
            raise RuntimeError("Auth Session Worker returned an empty outcome")
        return ensure_login_session_result_adapter.validate_json(outcome.result_json)

    @staticmethod
    def _workflow_id(
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
    ) -> str:
        actor_scope = str(actor.actor_id) if actor.actor_id is not None else actor.request_id
        material = "\n".join(
            (
                actor_scope,
                command.worker_identity,
                command.auth_method.value,
                *(command.allowed_origins),
            )
        )
        scope_digest = sha256(material.encode()).hexdigest()[:24]
        return (
            f"atlas-auth-session/{actor.tenant_id.hex}/{lease_id.hex}/"
            f"{command.fencing_token}/{scope_digest}"
        )
