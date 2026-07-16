"""Short-lived, user-scoped access grants for verified evidence bytes."""

from datetime import timedelta
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from atlas_testops.core.contracts import FrozenWireModel

EVIDENCE_READ_TOKEN_PATTERN = r"^evr_[A-Za-z0-9_-]{32,200}$"
MAX_EVIDENCE_READ_GRANT_LIFETIME = timedelta(minutes=2)


class EvidenceReadPurpose(StrEnum):
    """Exact presentation purpose authorized by an evidence read grant."""

    INLINE = "INLINE"
    DOWNLOAD = "DOWNLOAD"


class IssueEvidenceReadGrant(FrozenWireModel):
    """Request a bounded grant without accepting storage or scope identifiers."""

    purpose: EvidenceReadPurpose


class EvidenceReadGrant(FrozenWireModel):
    """Opaque, short-lived authority to read one exact verified artifact."""

    id: UUID
    artifact_id: UUID
    purpose: EvidenceReadPurpose
    read_token: str = Field(pattern=EVIDENCE_READ_TOKEN_PATTERN, repr=False)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    max_reads: int = Field(ge=1, le=32)

    @model_validator(mode="after")
    def validate_time_window(self) -> Self:
        """Keep grants positive and short even if an issuer is misconfigured."""

        lifetime = self.expires_at - self.issued_at
        if lifetime <= timedelta(0):
            raise ValueError("evidence read grant expiry must follow issuance")
        if lifetime > MAX_EVIDENCE_READ_GRANT_LIFETIME:
            raise ValueError("evidence read grant lifetime must not exceed two minutes")
        return self
