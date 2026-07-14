"""Test account health checks, state transitions, and command contracts."""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.identity.models import (
    AccountHealth,
    AccountLifecycle,
    AccountOperationalStatus,
    AccountSyncStatus,
    TestAccount,
)
from atlas_testops.domain.platform.models import normalize_origins


class AccountHealthCheckTrigger(StrEnum):
    """Stable source that triggered an account health check."""

    MANUAL = "MANUAL"
    RESTORE = "RESTORE"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_AUTH = "LEASE_AUTH"
    AUTH_FAILURE = "AUTH_FAILURE"
    CREDENTIAL_CHANGED = "CREDENTIAL_CHANGED"
    RECONCILE = "RECONCILE"


class AccountHealthCheckStatus(StrEnum):
    """Persisted state of an account health attempt."""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    STALE = "STALE"


class AccountHealthFailureCode(StrEnum):
    """Low-cardinality failure classes without raw Provider responses."""

    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    ROLE_DRIFT = "ROLE_DRIFT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    SECRET_UNAVAILABLE = "SECRET_UNAVAILABLE"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AccountStateTransitionReason(StrEnum):
    """Structured reason for a change to orthogonal account state."""

    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_SUCCEEDED = "VERIFICATION_SUCCEEDED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    FAILURE_THRESHOLD_REACHED = "FAILURE_THRESHOLD_REACHED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    ROLE_DRIFT = "ROLE_DRIFT"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    MANUAL_QUARANTINE = "MANUAL_QUARANTINE"
    MANUAL_RESTORE = "MANUAL_RESTORE"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    LEASE_RELEASED = "LEASE_RELEASED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    MANAGEMENT_REVOCATION = "MANAGEMENT_REVOCATION"
    RUNTIME_AUTH_SUCCEEDED = "RUNTIME_AUTH_SUCCEEDED"
    RUNTIME_AUTH_FAILED = "RUNTIME_AUTH_FAILED"


class VerifyTestAccount(FrozenWireModel):
    """Request a login and role health check on one exact Origin."""

    model_config = ConfigDict(str_strip_whitespace=True, frozen=True)

    origin: str = Field(min_length=8, max_length=2048)

    @field_validator("origin")
    @classmethod
    def normalize_origin(cls, value: str) -> str:
        return normalize_origins((value,))[0]


class AccountHealthCheck(FrozenWireModel):
    """Health fact without secrets, login names, subjects, or raw responses."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    account_id: UUID
    connector_installation_id: UUID
    credential_binding_id: UUID
    trigger: AccountHealthCheckTrigger
    status: AccountHealthCheckStatus
    origin: str
    role_key: str
    account_revision: int = Field(ge=1)
    connector_revision: int = Field(ge=1)
    credential_revision: int = Field(ge=1)
    result_health_status: AccountHealth | None
    failure_code: AccountHealthFailureCode | None
    retryable: bool | None
    safe_summary: str = Field(min_length=1, max_length=500)
    actor_id: UUID | None
    request_id: str = Field(min_length=1, max_length=200)
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    expires_at: AwareDatetime
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_terminal_metadata(self) -> Self:
        if self.status is AccountHealthCheckStatus.RUNNING:
            if any(
                value is not None
                for value in (
                    self.finished_at,
                    self.result_health_status,
                    self.failure_code,
                    self.retryable,
                )
            ):
                raise ValueError("running health check must not contain terminal metadata")
            return self
        if self.finished_at is None or self.retryable is None:
            raise ValueError("terminal health check requires completion metadata")
        if self.status is AccountHealthCheckStatus.SUCCEEDED:
            if (
                self.result_health_status is not AccountHealth.HEALTHY
                or self.failure_code is not None
                or self.retryable
            ):
                raise ValueError("successful health check metadata is invalid")
            return self
        if self.failure_code is None:
            raise ValueError("failed or stale health check requires a failure code")
        if self.status is AccountHealthCheckStatus.STALE:
            if (
                self.failure_code is not AccountHealthFailureCode.STALE_SNAPSHOT
                or self.result_health_status is not None
            ):
                raise ValueError("stale health check metadata is invalid")
            return self
        if self.result_health_status not in {
            AccountHealth.DEGRADED,
            AccountHealth.QUARANTINED,
        }:
            raise ValueError("failed health check requires a degraded result")
        return self


class AccountStateTransition(FrozenWireModel):
    """Immutable before-and-after snapshot of orthogonal account state."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    account_id: UUID
    health_check_id: UUID | None
    reason: AccountStateTransitionReason
    from_lifecycle_status: AccountLifecycle
    to_lifecycle_status: AccountLifecycle
    from_health_status: AccountHealth
    to_health_status: AccountHealth
    from_operational_status: AccountOperationalStatus
    to_operational_status: AccountOperationalStatus
    from_sync_status: AccountSyncStatus
    to_sync_status: AccountSyncStatus
    from_cooldown_until: AwareDatetime | None
    to_cooldown_until: AwareDatetime | None
    safe_summary: str = Field(min_length=1, max_length=500)
    actor_id: UUID | None
    request_id: str = Field(min_length=1, max_length=200)
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def require_state_change(self) -> Self:
        before = (
            self.from_lifecycle_status,
            self.from_health_status,
            self.from_operational_status,
            self.from_sync_status,
            self.from_cooldown_until,
        )
        after = (
            self.to_lifecycle_status,
            self.to_health_status,
            self.to_operational_status,
            self.to_sync_status,
            self.to_cooldown_until,
        )
        if before == after:
            raise ValueError("account state transition must change at least one field")
        return self


class AccountHealthVerification(FrozenWireModel):
    """A health check together with its final safe account projection."""

    check: AccountHealthCheck
    account: TestAccount


class AccountHealthCheckPage(FrozenWireModel):
    """Cursor page of AccountHealthCheck facts."""

    items: tuple[AccountHealthCheck, ...]
    next_cursor: str | None = None


class AccountStateTransitionPage(FrozenWireModel):
    """Cursor page of AccountStateTransition facts."""

    items: tuple[AccountStateTransition, ...]
    next_cursor: str | None = None
