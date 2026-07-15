"""Process boundaries for Browser Worker execution and protected session restore."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from atlas_testops.domain.case import DebugRun
from atlas_testops.domain.runtime import (
    AppendBrowserRuntimeReport,
    AssertionResultInput,
    BrowserContextRestoreDescriptor,
    BrowserContextRestoreEnvelope,
    BrowserExecutionBundle,
    BrowserRuntimeReport,
    BrowserRuntimeReportKind,
    EvidenceArtifactInput,
    EvidenceManifest,
    ExecutionContract,
    FinalizeDebugEvidence,
)


class BrowserContextEnvelopeCodec(Protocol):
    """Protect SessionArtifact metadata before it enters Temporal history."""

    def seal(
        self,
        descriptor: BrowserContextRestoreDescriptor,
        *,
        contract: ExecutionContract,
    ) -> BrowserContextRestoreEnvelope: ...

    def open(
        self,
        envelope: BrowserContextRestoreEnvelope,
        *,
        contract: ExecutionContract,
    ) -> BrowserContextRestoreDescriptor: ...


class BrowserRuntimeGateway(Protocol):
    """Only control-plane operations available to the database-free Worker."""

    async def get_execution_bundle(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
    ) -> BrowserExecutionBundle: ...

    async def mark_ready(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        execution_contract_id: UUID,
        execution_contract_digest: str,
    ) -> DebugRun: ...

    async def start_execution(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        execution_contract_id: UUID,
        execution_contract_digest: str,
    ) -> DebugRun: ...

    async def append_report(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        report: AppendBrowserRuntimeReport,
    ) -> BrowserRuntimeReport: ...

    async def finalize_evidence(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        command: FinalizeDebugEvidence,
    ) -> tuple[DebugRun, EvidenceManifest]: ...


class BrowserExecutionReporter(Protocol):
    """Append one safe execution fact through the trusted control-plane gateway."""

    async def emit(
        self,
        kind: BrowserRuntimeReportKind,
        payload: dict[str, JsonValue],
        *,
        actor_slot: str | None = None,
        action_id: UUID | None = None,
    ) -> BrowserRuntimeReport: ...


@dataclass(frozen=True, slots=True)
class BrowserExecutionOutput:
    """Oracle inputs and verified Artifact receipts returned by one engine."""

    assertion_results: tuple[AssertionResultInput, ...] = ()
    artifacts: tuple[EvidenceArtifactInput, ...] = ()


class BrowserExecutionEngine(Protocol):
    """Execute a frozen plan without owning database or final outcome authority."""

    async def execute(
        self,
        bundle: BrowserExecutionBundle,
        reporter: BrowserExecutionReporter,
    ) -> BrowserExecutionOutput: ...
