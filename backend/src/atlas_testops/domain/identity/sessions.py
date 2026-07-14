"""Auth session, encrypted artifact, and manual-action contracts."""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.identity.credentials import WORKER_IDENTITY_PATTERN
from atlas_testops.domain.identity.models import CredentialAuthMethod
from atlas_testops.domain.platform.models import normalize_origins

BROWSER_CONTEXT_REF_PATTERN = r"^bctx_[A-Za-z0-9_-]{32,200}$"
SESSION_OBJECT_REF_PATTERN = r"^session-vault://[A-Za-z0-9][A-Za-z0-9/_.=-]{7,511}$"


class SessionArtifactStatus(StrEnum):
    """Persistent lifecycle of an encrypted browser session artifact."""

    CREATING = "CREATING"
    READY = "READY"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    DESTROYING = "DESTROYING"
    DESTROYED = "DESTROYED"


class SessionArtifactTerminationReason(StrEnum):
    """Structured reason for invalidating or destroying a session artifact."""

    LEASE_TERMINATED = "LEASE_TERMINATED"
    ACCOUNT_UNAVAILABLE = "ACCOUNT_UNAVAILABLE"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
    CONNECTOR_UNAVAILABLE = "CONNECTOR_UNAVAILABLE"
    SUPERSEDED = "SUPERSEDED"
    TTL_EXPIRED = "TTL_EXPIRED"
    CREATION_FAILED = "CREATION_FAILED"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    MANUAL = "MANUAL"


class SessionArtifactFailureCode(StrEnum):
    """Safe failure taxonomy for automatic session creation."""

    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    ROLE_DRIFT = "ROLE_DRIFT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    SECRET_UNAVAILABLE = "SECRET_UNAVAILABLE"
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ManualActionTicketStatus(StrEnum):
    """Lifecycle of a bounded manual authentication request."""

    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ManualActionReason(StrEnum):
    """Safe reason why deterministic login cannot continue automatically."""

    AUTH_METHOD_REQUIRES_MANUAL = "AUTH_METHOD_REQUIRES_MANUAL"
    MFA_REQUIRED = "MFA_REQUIRED"
    DEVICE_TRUST_REQUIRED = "DEVICE_TRUST_REQUIRED"
    PROVIDER_CHALLENGE = "PROVIDER_CHALLENGE"


class SessionCommand(FrozenWireModel):
    """Normalize short worker inputs at the wire boundary."""

    model_config = ConfigDict(str_strip_whitespace=True)


class EnsureLoginSession(SessionCommand):
    """Request an origin-bound session for the latest account lease fence."""

    fencing_token: int = Field(ge=1)
    worker_identity: str = Field(
        min_length=3,
        max_length=160,
        pattern=WORKER_IDENTITY_PATTERN,
    )
    allowed_origins: tuple[str, ...] = Field(min_length=1, max_length=16)
    auth_method: CredentialAuthMethod = CredentialAuthMethod.PASSWORD
    ttl_seconds: int | None = Field(default=None, ge=60, le=3600)

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_origins(origins)


class LoginSessionReady(FrozenWireModel):
    """Safe handle for a ready encrypted session; never contains browser state."""

    status: Literal["ready"] = "ready"
    browser_context_ref: str = Field(pattern=BROWSER_CONTEXT_REF_PATTERN)
    expires_at: AwareDatetime


class LoginSessionManualAction(FrozenWireModel):
    """Safe response for an authentication flow that needs human interaction."""

    status: Literal["manual_action_required"] = "manual_action_required"
    action_ticket_id: UUID
    expires_at: AwareDatetime
    safe_reason: str = Field(min_length=1, max_length=500)


EnsureLoginSessionResult = Annotated[
    LoginSessionReady | LoginSessionManualAction,
    Field(discriminator="status"),
]
ensure_login_session_result_adapter: TypeAdapter[EnsureLoginSessionResult] = TypeAdapter(
    EnsureLoginSessionResult
)


class SessionArtifactRecord(FrozenWireModel):
    """Persistent metadata for ciphertext stored outside PostgreSQL."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    lease_id: UUID
    account_id: UUID
    connector_installation_id: UUID
    credential_binding_id: UUID
    lease_fence: int = Field(ge=1)
    worker_identity: str
    browser_context_ref: str = Field(pattern=BROWSER_CONTEXT_REF_PATTERN)
    allowed_origins: tuple[str, ...]
    auth_strength: tuple[CredentialAuthMethod, ...]
    status: SessionArtifactStatus
    object_ref: str | None = Field(default=None, pattern=SESSION_OBJECT_REF_PATTERN, repr=False)
    object_digest: str | None = Field(default=None, pattern=r"^sha256:[a-f0-9]{64}$")
    object_size_bytes: int | None = Field(default=None, ge=1)
    key_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$",
    )
    format_version: Literal["playwright-storage-state/v1"] = "playwright-storage-state/v1"
    refreshable: bool = False
    account_revision: int = Field(ge=1)
    connector_revision: int = Field(ge=1)
    credential_revision: int = Field(ge=1)
    safe_summary: str = Field(min_length=1, max_length=500)
    failure_code: SessionArtifactFailureCode | None
    created_at: AwareDatetime
    attempt_expires_at: AwareDatetime
    ready_at: AwareDatetime | None
    expires_at: AwareDatetime
    terminated_at: AwareDatetime | None
    termination_reason: SessionArtifactTerminationReason | None
    cleanup_claimed_at: AwareDatetime | None
    cleanup_worker_identity: str | None = Field(
        default=None,
        min_length=3,
        max_length=160,
        pattern=WORKER_IDENTITY_PATTERN,
    )
    destroyed_at: AwareDatetime | None
    revision: int = Field(ge=1)
    updated_at: AwareDatetime

    @field_validator("allowed_origins")
    @classmethod
    def normalize_allowed_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_origins(origins)

    @field_validator("auth_strength")
    @classmethod
    def normalize_auth_strength(
        cls,
        methods: tuple[CredentialAuthMethod, ...],
    ) -> tuple[CredentialAuthMethod, ...]:
        return tuple(sorted(set(methods), key=lambda item: item.value))

    @model_validator(mode="after")
    def validate_state_metadata(self) -> Self:
        sealed_object_fields = (
            self.object_digest,
            self.object_size_bytes,
            self.key_version,
        )
        has_sealed_object = self.object_ref is not None and all(
            value is not None for value in sealed_object_fields
        )
        if any(value is not None for value in sealed_object_fields) and not has_sealed_object:
            raise ValueError("session artifact object metadata must be complete")
        if not self.created_at < self.attempt_expires_at <= self.expires_at:
            raise ValueError("session artifact attempt and session expiry are invalid")
        cleanup_unclaimed = (
            self.cleanup_claimed_at is None and self.cleanup_worker_identity is None
        )
        if self.status is SessionArtifactStatus.CREATING:
            valid = (
                self.object_ref is not None
                and not has_sealed_object
                and not self.auth_strength
                and self.ready_at is None
                and self.failure_code is None
                and self.terminated_at is None
                and self.termination_reason is None
                and cleanup_unclaimed
                and self.destroyed_at is None
            )
        elif self.status is SessionArtifactStatus.READY:
            valid = (
                has_sealed_object
                and bool(self.auth_strength)
                and self.ready_at is not None
                and self.failure_code is None
                and self.terminated_at is None
                and self.termination_reason is None
                and cleanup_unclaimed
                and self.destroyed_at is None
            )
        elif self.status is SessionArtifactStatus.FAILED:
            valid = (
                self.failure_code is not None
                and self.terminated_at is not None
                and self.termination_reason
                in {
                    SessionArtifactTerminationReason.CREATION_FAILED,
                    SessionArtifactTerminationReason.STALE_SNAPSHOT,
                }
                and cleanup_unclaimed
                and self.destroyed_at is None
            )
        elif self.status in {
            SessionArtifactStatus.REVOKED,
            SessionArtifactStatus.EXPIRED,
        }:
            valid = (
                self.failure_code is None
                and self.terminated_at is not None
                and self.termination_reason is not None
                and cleanup_unclaimed
                and self.destroyed_at is None
            )
        elif self.status is SessionArtifactStatus.DESTROYING:
            valid = (
                self.terminated_at is not None
                and self.termination_reason is not None
                and self.cleanup_claimed_at is not None
                and self.cleanup_worker_identity is not None
                and self.destroyed_at is None
            )
        else:
            valid = (
                self.terminated_at is not None
                and self.termination_reason is not None
                and self.cleanup_claimed_at is not None
                and self.cleanup_worker_identity is not None
                and self.destroyed_at is not None
                and self.destroyed_at >= self.terminated_at
            )
        if not valid:
            raise ValueError("session artifact metadata does not match status")
        return self

    def to_ready_result(self) -> LoginSessionReady:
        """Project a ready artifact into the only safe automatic-login response."""

        if self.status is not SessionArtifactStatus.READY:
            raise ValueError("only a ready session artifact can be projected")
        return LoginSessionReady(
            browser_context_ref=self.browser_context_ref,
            expires_at=self.expires_at,
        )


class ManualActionTicketRecord(FrozenWireModel):
    """Persistent metadata for a scoped manual authentication request."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    lease_id: UUID
    account_id: UUID
    connector_installation_id: UUID
    lease_fence: int = Field(ge=1)
    worker_identity: str
    allowed_origins: tuple[str, ...]
    auth_method: CredentialAuthMethod
    reason: ManualActionReason
    status: ManualActionTicketStatus
    safe_reason: str = Field(min_length=1, max_length=500)
    created_at: AwareDatetime
    expires_at: AwareDatetime
    completed_at: AwareDatetime | None
    terminated_at: AwareDatetime | None
    revision: int = Field(ge=1)
    updated_at: AwareDatetime

    @field_validator("allowed_origins")
    @classmethod
    def normalize_ticket_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_origins(origins)

    @model_validator(mode="after")
    def validate_terminal_metadata(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("manual action ticket expiry must follow creation")
        if self.status is ManualActionTicketStatus.OPEN:
            valid = self.completed_at is None and self.terminated_at is None
        elif self.status is ManualActionTicketStatus.COMPLETED:
            valid = self.completed_at is not None and self.terminated_at is None
        else:
            valid = self.completed_at is None and self.terminated_at is not None
        if not valid:
            raise ValueError("manual action ticket metadata does not match status")
        return self

    def to_manual_result(self) -> LoginSessionManualAction:
        """Project an open ticket without exposing account or connector metadata."""

        if self.status is not ManualActionTicketStatus.OPEN:
            raise ValueError("only an open manual action ticket can be projected")
        return LoginSessionManualAction(
            action_ticket_id=self.id,
            expires_at=self.expires_at,
            safe_reason=self.safe_reason,
        )


class SessionJanitorBatch(FrozenWireModel):
    """Safe summary returned by one bounded artifact cleanup pass."""

    expired: int = Field(ge=0)
    destroyed: int = Field(ge=0)
    failed: int = Field(ge=0)
    observed_at: AwareDatetime
