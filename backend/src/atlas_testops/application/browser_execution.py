"""Database-free Browser Worker execution loop and report-chain coordinator."""

from __future__ import annotations

from uuid import UUID

from pydantic import JsonValue

from atlas_testops.application.debug_runtime import DebugRuntimeService
from atlas_testops.application.ports.browser_runtime import (
    BrowserExecutionEngine,
    BrowserExecutionOutput,
    BrowserExecutionReporter,
    BrowserRuntimeGateway,
)
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.domain.case import DebugRun, canonical_digest
from atlas_testops.domain.runtime import (
    CHAIN_START_DIGEST,
    AppendBrowserRuntimeReport,
    AssertionResultInput,
    AssertionStatus,
    BrowserExecutionBundle,
    BrowserRuntimeReport,
    BrowserRuntimeReportKind,
    EvidenceManifest,
    FinalizeDebugEvidence,
    build_browser_runtime_report,
    expected_assertion_digest,
)


class BrowserExecutionReporterService(BrowserExecutionReporter):
    """Build and submit a monotonic report chain from one Worker activity."""

    def __init__(
        self,
        gateway: BrowserRuntimeGateway,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        bundle: BrowserExecutionBundle,
    ) -> None:
        self._gateway = gateway
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._worker_identity = worker_identity
        self._bundle = bundle
        self._sequence = 0
        self._chain_head = CHAIN_START_DIGEST

    @property
    def event_count(self) -> int:
        return self._sequence

    @property
    def chain_head(self) -> str:
        return self._chain_head

    async def emit(
        self,
        kind: BrowserRuntimeReportKind,
        payload: dict[str, JsonValue],
        *,
        actor_slot: str | None = None,
        action_id: UUID | None = None,
    ) -> BrowserRuntimeReport:
        report = build_browser_runtime_report(
            execution_contract_id=self._bundle.execution_contract.id,
            execution_contract_digest=self._bundle.execution_contract.content_digest,
            report_id=new_entity_id(),
            sequence=self._sequence + 1,
            kind=kind,
            payload=payload,
            occurred_at=utc_now(),
            previous_chain_digest=self._chain_head,
            actor_slot=actor_slot,
            action_id=action_id,
        )
        persisted = await self._gateway.append_report(
            tenant_id=self._tenant_id,
            run_id=self._run_id,
            worker_identity=self._worker_identity,
            report=report,
        )
        self._sequence = report.sequence
        self._chain_head = report.chain_digest
        return persisted


class BrowserWorkerService:
    """Restore, execute, report, and finalize one already-bound DebugRun."""

    def __init__(
        self,
        gateway: BrowserRuntimeGateway,
        engine: BrowserExecutionEngine,
    ) -> None:
        self._gateway = gateway
        self._engine = engine

    async def execute(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
    ) -> tuple[DebugRun, EvidenceManifest]:
        bundle = await self._gateway.get_execution_bundle(
            tenant_id=tenant_id,
            run_id=run_id,
            worker_identity=worker_identity,
        )
        contract = bundle.execution_contract
        await self._gateway.mark_ready(
            tenant_id=tenant_id,
            run_id=run_id,
            worker_identity=worker_identity,
            execution_contract_id=contract.id,
            execution_contract_digest=contract.content_digest,
        )
        await self._gateway.start_execution(
            tenant_id=tenant_id,
            run_id=run_id,
            worker_identity=worker_identity,
            execution_contract_id=contract.id,
            execution_contract_digest=contract.content_digest,
        )
        reporter = BrowserExecutionReporterService(
            self._gateway,
            tenant_id=tenant_id,
            run_id=run_id,
            worker_identity=worker_identity,
            bundle=bundle,
        )
        await reporter.emit(
            BrowserRuntimeReportKind.EXECUTION_STARTED,
            {
                "safeSummary": "browser execution started",
                "planDigest": contract.plan_digest,
            },
        )
        try:
            output = await self._engine.execute(bundle, reporter)
        except Exception as error:
            output = await self._record_fail_closed_output(
                bundle,
                reporter,
                error,
            )
        await self._emit_evidence_reports(output, reporter)
        await reporter.emit(
            BrowserRuntimeReportKind.EXECUTION_COMPLETED,
            {
                "safeSummary": "browser execution reached evidence finalization",
                "assertionResultCount": len(output.assertion_results),
                "artifactCount": len(output.artifacts),
            },
        )
        finalized_at = utc_now()
        command = FinalizeDebugEvidence(
            execution_contract_id=contract.id,
            execution_contract_digest=contract.content_digest,
            assertion_results=output.assertion_results,
            artifacts=output.artifacts,
            event_chain_head_digest=reporter.chain_head,
            event_count=reporter.event_count,
            finalized_at=finalized_at,
        )
        return await self._gateway.finalize_evidence(
            tenant_id=tenant_id,
            run_id=run_id,
            worker_identity=worker_identity,
            command=command,
        )

    @staticmethod
    async def _emit_evidence_reports(
        output: BrowserExecutionOutput,
        reporter: BrowserExecutionReporter,
    ) -> None:
        for result in output.assertion_results:
            await reporter.emit(
                BrowserRuntimeReportKind.ASSERTION_EVALUATED,
                {
                    "safeSummary": "browser assertion evaluated",
                    "assertionId": result.assertion_id,
                    "assertionInputDigest": canonical_digest(result),
                    "status": result.status.value,
                    "expectedDigest": result.expected_digest,
                },
            )
        for artifact in output.artifacts:
            await reporter.emit(
                BrowserRuntimeReportKind.ARTIFACT_CAPTURED,
                {
                    "safeSummary": "verified browser evidence artifact captured",
                    "artifactId": str(artifact.id),
                    "artifactInputDigest": canonical_digest(artifact),
                    "kind": artifact.kind.value,
                    "contentDigest": artifact.content_digest,
                    "sizeBytes": artifact.size_bytes,
                    "integrity": artifact.integrity.value,
                },
            )

    @staticmethod
    async def _record_fail_closed_output(
        bundle: BrowserExecutionBundle,
        reporter: BrowserExecutionReporter,
        error: Exception,
    ) -> BrowserExecutionOutput:
        observed_at = utc_now()
        results = tuple(
            AssertionResultInput(
                assertion_id=spec.assertion_id,
                status=AssertionStatus.INCONCLUSIVE,
                expected_digest=expected_assertion_digest(
                    bundle.test_ir,
                    spec.assertion_id,
                ),
                actual_safe_summary="browser execution did not reach a verified assertion",
                evaluator_version_ref=spec.evaluator_version_ref,
                evidence_refs=(),
                observed_at=observed_at,
                duration_ms=0,
            )
            for spec in bundle.test_ir.assertions
        )
        await reporter.emit(
            BrowserRuntimeReportKind.EXECUTION_BLOCKED,
            {
                "safeSummary": "browser execution stopped before verified completion",
                "failureType": type(error).__name__[:120],
            },
        )
        return BrowserExecutionOutput(assertion_results=results)


class DirectDebugRuntimeGateway(BrowserRuntimeGateway):
    """In-process control-plane adapter used by integration tests and probes."""

    def __init__(self, service: DebugRuntimeService) -> None:
        self._service = service

    async def get_execution_bundle(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
    ) -> BrowserExecutionBundle:
        return await self._service.get_browser_execution_bundle(
            tenant_id,
            run_id,
            worker_identity=worker_identity,
        )

    async def mark_ready(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        execution_contract_id: UUID,
        execution_contract_digest: str,
    ) -> DebugRun:
        return await self._service.mark_ready(
            tenant_id,
            run_id,
            execution_contract_id=execution_contract_id,
            execution_contract_digest=execution_contract_digest,
            worker_identity=worker_identity,
        )

    async def start_execution(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        execution_contract_id: UUID,
        execution_contract_digest: str,
    ) -> DebugRun:
        return await self._service.start_execution(
            tenant_id,
            run_id,
            execution_contract_id=execution_contract_id,
            execution_contract_digest=execution_contract_digest,
            worker_identity=worker_identity,
        )

    async def append_report(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        report: AppendBrowserRuntimeReport,
    ) -> BrowserRuntimeReport:
        return await self._service.append_browser_report(
            tenant_id,
            run_id,
            worker_identity=worker_identity,
            report=report,
        )

    async def finalize_evidence(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        command: FinalizeDebugEvidence,
    ) -> tuple[DebugRun, EvidenceManifest]:
        return await self._service.finalize_evidence(
            tenant_id,
            run_id,
            command,
            worker_identity=worker_identity,
        )
