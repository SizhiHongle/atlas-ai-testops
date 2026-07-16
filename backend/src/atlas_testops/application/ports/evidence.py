"""Trusted ports for browser evidence capture and verified object reads."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from atlas_testops.domain.runtime import EvidenceArtifactInput, EvidenceArtifactKind


class EvidenceStoreError(RuntimeError):
    """Safe base error for trusted evidence capture and reads."""


class EvidenceStoreUnavailableError(EvidenceStoreError):
    """The configured object store could not complete an operation."""


class EvidenceObjectMissingError(EvidenceStoreError):
    """An immutable object referenced by PostgreSQL no longer exists."""


class EvidenceObjectIntegrityError(EvidenceStoreError):
    """Object bytes do not match the immutable database receipt."""


class EvidenceArtifactCaptureError(EvidenceStoreError):
    """Raw browser bytes could not produce a verified evidence receipt."""


@dataclass(frozen=True, slots=True)
class ScreenshotRedactionPolicy:
    """Deployment-owned masking policy applied by Playwright before encoding."""

    selectors: tuple[str, ...]
    mask_color: str
    content_digest: str


@dataclass(frozen=True, slots=True)
class EvidenceArtifactWriteScope:
    """Exact execution scope encoded into an opaque evidence object reference."""

    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    debug_run_id: UUID
    execution_contract_id: UUID
    execution_contract_digest: str
    execution_created_at: datetime
    execution_deadline: datetime


class BrowserArtifactWriter(Protocol):
    """Redact, retain, independently hash, and verify browser evidence bytes."""

    @property
    def screenshot_redaction_policy(self) -> ScreenshotRedactionPolicy: ...

    async def write(
        self,
        *,
        scope: EvidenceArtifactWriteScope,
        kind: EvidenceArtifactKind,
        payload: bytes,
        mime_type: str,
        required: bool,
        captured_at: datetime,
    ) -> EvidenceArtifactInput: ...


@dataclass(frozen=True, slots=True)
class EvidenceObjectDescriptor:
    """Private immutable object metadata loaded under database authorization."""

    artifact_id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    debug_run_id: UUID
    execution_contract_id: UUID
    object_ref: str
    content_digest: str
    size_bytes: int
    mime_type: str


class EvidenceObjectReader(Protocol):
    """Read one object only after independently verifying its immutable metadata."""

    async def read_verified(self, descriptor: EvidenceObjectDescriptor) -> bytes: ...
