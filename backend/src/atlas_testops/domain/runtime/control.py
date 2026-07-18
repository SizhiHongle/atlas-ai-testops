"""Formal UnitAttempt-scoped browser live-control contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import DIGEST_PATTERN

LIVE_SESSION_SCHEMA_VERSION: Literal["atlas.live-session/0.1"] = (
    "atlas.live-session/0.1"
)
CONTROL_LEASE_SCHEMA_VERSION: Literal["atlas.control-lease/0.1"] = (
    "atlas.control-lease/0.1"
)
LIVE_CONTROL_COMMAND_SCHEMA_VERSION: Literal["atlas.live-control-command/0.1"] = (
    "atlas.live-control-command/0.1"
)
LIVE_ACTION_GRANT_SCHEMA_VERSION: Literal["atlas.live-action-grant/0.1"] = (
    "atlas.live-action-grant/0.1"
)
UNIT_ATTEMPT_LIVE_SNAPSHOT_SCHEMA_VERSION: Literal[
    "atlas.unit-attempt-live-snapshot/0.1"
] = "atlas.unit-attempt-live-snapshot/0.1"

SAFE_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{1,199}$"
SAFE_OWNER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,199}$"
SAFE_TEXT_PATTERN = r"^[^\x00-\x1f\x7f]{1,500}$"


class LiveControllerType(StrEnum):
    """The exclusive controller kind for one BrowserSession."""

    AGENT = "AGENT"
    HUMAN = "HUMAN"


class LiveSessionState(StrEnum):
    """Control-plane state independent from UnitAttempt lifecycle."""

    AGENT_CONTROLLED = "AGENT_CONTROLLED"
    QUIESCING = "QUIESCING"
    PAUSED = "PAUSED"
    RESUME_REQUESTED = "RESUME_REQUESTED"
    HUMAN_CONTROLLED = "HUMAN_CONTROLLED"
    RECONCILING = "RECONCILING"
    NO_CONTROLLER = "NO_CONTROLLER"
    CLOSED = "CLOSED"


class ControlLeaseState(StrEnum):
    """Durable lifecycle for one exclusive controller lease."""

    ACTIVE = "ACTIVE"
    REVOKING = "REVOKING"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


class LiveControlCommandType(StrEnum):
    """Commands accepted by the asynchronous live-control REST lane."""

    PAUSE = "PAUSE"
    RESUME = "RESUME"
    TAKEOVER = "TAKEOVER"
    RETURN = "RETURN"


class LiveControlCommandStatus(StrEnum):
    """Durable command status visible to callers and Workers."""

    PENDING = "PENDING"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class LiveActionGrantState(StrEnum):
    """Single-use action capability lifecycle."""

    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    COMPLETED = "COMPLETED"
    REVOKED = "REVOKED"


class LiveActionExecutionStatus(StrEnum):
    """Adapter outcome attached after a consumed Grant."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class LiveSession(FrozenWireModel):
    """Rebuildable live projection rooted in one exact formal UnitAttempt."""

    schema_version: Literal["atlas.live-session/0.1"] = LIVE_SESSION_SCHEMA_VERSION
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    execution_ticket_id: UUID
    execution_ticket_digest: str = Field(pattern=DIGEST_PATTERN)
    browser_session_id: str = Field(pattern=SAFE_IDENTIFIER_PATTERN)
    state: LiveSessionState
    control_epoch: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    browser_revision: int = Field(ge=1)
    human_influenced: bool = False
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    closed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if (self.state is LiveSessionState.CLOSED) != (self.closed_at is not None):
            raise ValueError("closedAt must exist exactly for a CLOSED LiveSession")
        return self


class ControlLease(FrozenWireModel):
    """Exclusive, short-lived controller lease with a monotonic fence."""

    schema_version: Literal["atlas.control-lease/0.1"] = CONTROL_LEASE_SCHEMA_VERSION
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    live_session_id: UUID
    owner_type: LiveControllerType
    owner_id: str = Field(pattern=SAFE_OWNER_PATTERN)
    control_epoch: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    state: ControlLeaseState
    expires_at: AwareDatetime
    reason: str = Field(pattern=SAFE_TEXT_PATTERN)
    created_by: UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    released_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_lease(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("ControlLease expiresAt must follow createdAt")
        terminal = self.state in {
            ControlLeaseState.EXPIRED,
            ControlLeaseState.RELEASED,
        }
        if terminal != (self.released_at is not None):
            raise ValueError("terminal ControlLease requires releasedAt")
        return self


class LiveControlCommand(FrozenWireModel):
    """Idempotent requested transition over one exact control epoch."""

    schema_version: Literal[
        "atlas.live-control-command/0.1"
    ] = LIVE_CONTROL_COMMAND_SCHEMA_VERSION
    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    live_session_id: UUID
    command_type: LiveControlCommandType
    client_mutation_id: str = Field(
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    reason: str = Field(pattern=SAFE_TEXT_PATTERN)
    requested_ttl_sec: int | None = Field(default=None, ge=30, le=900)
    expected_control_epoch: int = Field(ge=1)
    accepted_session_revision: int = Field(ge=1)
    status: LiveControlCommandStatus
    requested_by: UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    applied_at: AwareDatetime | None = None
    resulting_control_epoch: int | None = Field(default=None, ge=1)
    resulting_fencing_token: int | None = Field(default=None, ge=1)
    checkpoint_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_completion(self) -> Self:
        applied = self.status is LiveControlCommandStatus.APPLIED
        completion = (
            self.applied_at,
            self.resulting_control_epoch,
            self.resulting_fencing_token,
            self.checkpoint_digest,
        )
        if applied and any(value is None for value in completion):
            raise ValueError("APPLIED command requires transition completion facts")
        if not applied and any(value is not None for value in completion):
            raise ValueError("non-APPLIED command cannot carry completion facts")
        return self


class UnitAttemptLiveSnapshot(FrozenWireModel):
    """Consistent current control projection for one UnitAttempt."""

    schema_version: Literal[
        "atlas.unit-attempt-live-snapshot/0.1"
    ] = UNIT_ATTEMPT_LIVE_SNAPSHOT_SCHEMA_VERSION
    session: LiveSession
    lease: ControlLease | None = None
    pending_command: LiveControlCommand | None = None
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if self.lease is not None and (
            self.lease.live_session_id != self.session.id
            or self.lease.control_epoch != self.session.control_epoch
            or self.lease.fencing_token != self.session.fencing_token
        ):
            raise ValueError("LiveSnapshot lease must match the current session fence")
        if (
            self.pending_command is not None
            and self.pending_command.live_session_id != self.session.id
        ):
            raise ValueError("LiveSnapshot command must belong to its session")
        return self


class RequestLiveControl(FrozenWireModel):
    """Safe public reason and bounded Human lease duration."""

    reason: str = Field(pattern=SAFE_TEXT_PATTERN)
    requested_ttl_sec: int | None = Field(default=None, ge=30, le=900)


class InitializeLiveSession(FrozenWireModel):
    """Worker request that establishes the initial Agent controller."""

    browser_session_id: str = Field(pattern=SAFE_IDENTIFIER_PATTERN)
    owner_id: str = Field(pattern=SAFE_OWNER_PATTERN)
    browser_revision: int = Field(default=1, ge=1)
    requested_ttl_sec: int = Field(default=120, ge=30, le=900)


class HeartbeatLiveControl(FrozenWireModel):
    """Renew the current Agent lease without changing its epoch or fence."""

    control_epoch: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    requested_ttl_sec: int = Field(default=120, ge=30, le=900)


class AcknowledgeLiveControl(FrozenWireModel):
    """Worker safe-point or reconcile acknowledgement."""

    command_id: UUID
    expected_control_epoch: int = Field(ge=1)
    expected_fencing_token: int = Field(ge=1)
    browser_revision: int = Field(ge=1)
    checkpoint_digest: str = Field(pattern=DIGEST_PATTERN)
    agent_owner_id: str = Field(pattern=SAFE_OWNER_PATTERN)


class RequestLiveActionGrant(FrozenWireModel):
    """One exact structured action approved by a trusted policy boundary."""

    action_id: UUID
    proposal_digest: str = Field(pattern=DIGEST_PATTERN)
    page_id: str = Field(pattern=SAFE_IDENTIFIER_PATTERN)
    page_revision: int = Field(ge=1)
    control_epoch: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    allowed_adapter: str = Field(pattern=SAFE_IDENTIFIER_PATTERN)
    policy_digest: str = Field(pattern=DIGEST_PATTERN)
    requested_ttl_sec: int = Field(default=15, ge=1, le=60)


class LiveActionGrant(FrozenWireModel):
    """Persistent, single-use, epoch/fence-bound action capability."""

    schema_version: Literal[
        "atlas.live-action-grant/0.1"
    ] = LIVE_ACTION_GRANT_SCHEMA_VERSION
    grant_id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    live_session_id: UUID
    control_lease_id: UUID
    action_id: UUID
    proposal_digest: str = Field(pattern=DIGEST_PATTERN)
    browser_session_id: str = Field(pattern=SAFE_IDENTIFIER_PATTERN)
    page_id: str = Field(pattern=SAFE_IDENTIFIER_PATTERN)
    page_revision: int = Field(ge=1)
    control_epoch: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    owner_type: LiveControllerType
    owner_id: str = Field(pattern=SAFE_OWNER_PATTERN)
    allowed_adapter: str = Field(pattern=SAFE_IDENTIFIER_PATTERN)
    expires_at: AwareDatetime
    max_executions: Literal[1] = 1
    policy_digest: str = Field(pattern=DIGEST_PATTERN)
    state: LiveActionGrantState
    created_at: AwareDatetime
    consumed_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None
    receipt_id: UUID | None = None
    execution_status: LiveActionExecutionStatus | None = None
    resulting_page_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("ActionGrant expiresAt must follow createdAt")
        if self.state is LiveActionGrantState.ISSUED and any(
            value is not None
            for value in (
                self.consumed_at,
                self.completed_at,
                self.revoked_at,
                self.receipt_id,
                self.execution_status,
                self.resulting_page_revision,
            )
        ):
            raise ValueError("ISSUED ActionGrant cannot carry terminal facts")
        if self.state is LiveActionGrantState.CONSUMED and (
            self.consumed_at is None
            or any(
                value is not None
                for value in (
                    self.completed_at,
                    self.revoked_at,
                    self.receipt_id,
                    self.execution_status,
                    self.resulting_page_revision,
                )
            )
        ):
            raise ValueError("CONSUMED ActionGrant must only carry consumedAt")
        if self.state is LiveActionGrantState.REVOKED and (
            self.revoked_at is None
            or self.consumed_at is not None
            or self.completed_at is not None
        ):
            raise ValueError("REVOKED ActionGrant must be unconsumed")
        if self.state is LiveActionGrantState.COMPLETED and any(
            value is None
            for value in (
                self.consumed_at,
                self.completed_at,
                self.receipt_id,
                self.execution_status,
                self.resulting_page_revision,
            )
        ):
            raise ValueError("COMPLETED ActionGrant requires exact receipt facts")
        return self


class ConsumeLiveActionGrant(FrozenWireModel):
    """Worker-side compare-and-consume request before Playwright side effects."""

    control_epoch: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    proposal_digest: str = Field(pattern=DIGEST_PATTERN)


class CompleteLiveActionGrant(FrozenWireModel):
    """Worker receipt attached after the adapter returns or becomes unknown."""

    control_epoch: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    receipt_id: UUID
    execution_status: LiveActionExecutionStatus
    resulting_page_revision: int = Field(ge=1)


class LiveControlEvent(FrozenWireModel):
    """Append-only audit projection ordered within one LiveSession."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    task_run_id: UUID
    execution_unit_id: UUID
    unit_attempt_id: UUID
    live_session_id: UUID
    seq: int = Field(ge=1)
    event_type: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[a-z][a-z0-9_.-]+$",
    )
    control_epoch: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: AwareDatetime


class ReapedLiveControlBatch(FrozenWireModel):
    """Bounded tenant-scoped expired controller reconciliation result."""

    reaped: int = Field(ge=0)
    observed_at: AwareDatetime


__all__ = [
    "CONTROL_LEASE_SCHEMA_VERSION",
    "LIVE_ACTION_GRANT_SCHEMA_VERSION",
    "LIVE_CONTROL_COMMAND_SCHEMA_VERSION",
    "LIVE_SESSION_SCHEMA_VERSION",
    "UNIT_ATTEMPT_LIVE_SNAPSHOT_SCHEMA_VERSION",
    "AcknowledgeLiveControl",
    "CompleteLiveActionGrant",
    "ConsumeLiveActionGrant",
    "ControlLease",
    "ControlLeaseState",
    "HeartbeatLiveControl",
    "InitializeLiveSession",
    "LiveActionExecutionStatus",
    "LiveActionGrant",
    "LiveActionGrantState",
    "LiveControlCommand",
    "LiveControlCommandStatus",
    "LiveControlCommandType",
    "LiveControlEvent",
    "LiveControllerType",
    "LiveSession",
    "LiveSessionState",
    "ReapedLiveControlBatch",
    "RequestLiveActionGrant",
    "RequestLiveControl",
    "UnitAttemptLiveSnapshot",
]
