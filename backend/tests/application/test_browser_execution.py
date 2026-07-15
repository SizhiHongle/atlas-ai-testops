"""Database-free BrowserWorkerService report and fail-closed behavior."""

from collections.abc import Callable
from typing import cast
from uuid import UUID, uuid7

import pytest
from tests.domain.case.test_runtime_evidence import _run
from tests.domain.runtime.test_browser_protocol import _runtime

from atlas_testops.application.browser_execution import (
    BrowserWorkerService,
    DirectDebugRuntimeGateway,
)
from atlas_testops.application.debug_runtime import DebugRuntimeService
from atlas_testops.application.ports.browser_runtime import (
    BrowserExecutionOutput,
    BrowserExecutionReporter,
)
from atlas_testops.domain.case import DebugRun
from atlas_testops.domain.case import TestIntent as CaseIntent
from atlas_testops.domain.runtime import (
    CHAIN_START_DIGEST,
    AppendBrowserRuntimeReport,
    AssertionResultInput,
    AssertionStatus,
    BrowserExecutionBundle,
    BrowserRuntimeReport,
    BrowserRuntimeReportKind,
    EvidenceArtifactInput,
    EvidenceArtifactKind,
    EvidenceIntegrity,
    EvidenceManifest,
    FinalizeDebugEvidence,
    build_browser_runtime_report,
    expected_assertion_digest,
)
from atlas_testops.domain.workflow import WorkflowGraph


class FakeGateway:
    def __init__(self, bundle: BrowserExecutionBundle, run: DebugRun) -> None:
        self.bundle = bundle
        self.run = run
        self.reports: list[BrowserRuntimeReport] = []
        self.finalization: FinalizeDebugEvidence | None = None
        self.transitions: list[str] = []

    async def get_execution_bundle(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
    ) -> BrowserExecutionBundle:
        assert tenant_id == self.bundle.execution_contract.tenant_id
        assert run_id == self.bundle.execution_contract.debug_run_id
        assert worker_identity == self.bundle.execution_contract.worker_identity
        return self.bundle

    async def mark_ready(self, **kwargs: object) -> DebugRun:
        self.transitions.append("ready")
        return self.run

    async def start_execution(self, **kwargs: object) -> DebugRun:
        self.transitions.append("start")
        return self.run

    async def append_report(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        report: AppendBrowserRuntimeReport,
    ) -> BrowserRuntimeReport:
        assert report.sequence == len(self.reports) + 1
        persisted = BrowserRuntimeReport(
            tenant_id=tenant_id,
            project_id=self.bundle.execution_contract.project_id,
            environment_id=self.bundle.execution_contract.environment_id,
            debug_run_id=run_id,
            value=report,
            recorded_at=report.occurred_at,
        )
        self.reports.append(persisted)
        return persisted

    async def finalize_evidence(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        command: FinalizeDebugEvidence,
    ) -> tuple[DebugRun, EvidenceManifest]:
        self.finalization = command
        return self.run, cast(EvidenceManifest, object())


class PassingEngine:
    async def execute(
        self,
        bundle: BrowserExecutionBundle,
        reporter: BrowserExecutionReporter,
    ) -> BrowserExecutionOutput:
        specification = bundle.test_ir.assertions[0]
        return BrowserExecutionOutput(
            assertion_results=(
                AssertionResultInput(
                    assertion_id=specification.assertion_id,
                    status=AssertionStatus.PASSED,
                    expected_digest=expected_assertion_digest(
                        bundle.test_ir,
                        specification.assertion_id,
                    ),
                    actual_safe_summary="deterministic assertion passed",
                    evaluator_version_ref=specification.evaluator_version_ref,
                    observed_at=bundle.issued_at,
                    duration_ms=5,
                ),
            )
        )


class FailingEngine:
    async def execute(
        self,
        bundle: BrowserExecutionBundle,
        reporter: BrowserExecutionReporter,
    ) -> BrowserExecutionOutput:
        raise RuntimeError("provider response contained token=secret-value")


class ArtifactEngine(PassingEngine):
    async def execute(
        self,
        bundle: BrowserExecutionBundle,
        reporter: BrowserExecutionReporter,
    ) -> BrowserExecutionOutput:
        output = await super().execute(bundle, reporter)
        artifact = EvidenceArtifactInput(
            id=uuid7(),
            kind=EvidenceArtifactKind.SCREENSHOT,
            object_ref="evidence://tests/redacted-browser-view.png",
            content_digest="sha256:" + "c" * 64,
            size_bytes=512,
            mime_type="image/png",
            redaction_policy_digest="sha256:" + "d" * 64,
            integrity=EvidenceIntegrity.VERIFIED,
            required=True,
            captured_at=bundle.issued_at,
        )
        return BrowserExecutionOutput(
            assertion_results=tuple(
                result.model_copy(update={"evidence_refs": (artifact.id,)})
                for result in output.assertion_results
            ),
            artifacts=(artifact,),
        )


class RecordingRuntimeService:
    def __init__(
        self,
        *,
        bundle: BrowserExecutionBundle,
        run: DebugRun,
        report: BrowserRuntimeReport,
        manifest: EvidenceManifest,
    ) -> None:
        self.bundle = bundle
        self.run = run
        self.report = report
        self.manifest = manifest
        self.calls: list[str] = []

    async def get_browser_execution_bundle(
        self,
        *args: object,
        **kwargs: object,
    ) -> BrowserExecutionBundle:
        self.calls.append("bundle")
        return self.bundle

    async def mark_ready(self, *args: object, **kwargs: object) -> DebugRun:
        self.calls.append("ready")
        return self.run

    async def start_execution(self, *args: object, **kwargs: object) -> DebugRun:
        self.calls.append("start")
        return self.run

    async def append_browser_report(
        self,
        *args: object,
        **kwargs: object,
    ) -> BrowserRuntimeReport:
        self.calls.append("report")
        return self.report

    async def finalize_evidence(
        self,
        *args: object,
        **kwargs: object,
    ) -> tuple[DebugRun, EvidenceManifest]:
        self.calls.append("finalize")
        return self.run, self.manifest


@pytest.mark.anyio
async def test_worker_reports_outputs_and_finalizes_exact_chain(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    bundle, _codec = _runtime(valid_graph, intent_factory)
    gateway = FakeGateway(bundle, _run(valid_graph, intent_factory))
    service = BrowserWorkerService(gateway, PassingEngine())
    await service.execute(
        tenant_id=bundle.execution_contract.tenant_id,
        run_id=bundle.execution_contract.debug_run_id,
        worker_identity=bundle.execution_contract.worker_identity,
    )
    assert gateway.transitions == ["ready", "start"]
    assert [item.value.kind for item in gateway.reports] == [
        BrowserRuntimeReportKind.EXECUTION_STARTED,
        BrowserRuntimeReportKind.ASSERTION_EVALUATED,
        BrowserRuntimeReportKind.EXECUTION_COMPLETED,
    ]
    assert gateway.finalization is not None
    assert gateway.finalization.event_count == 3
    assert (
        gateway.finalization.event_chain_head_digest
        == gateway.reports[-1].value.chain_digest
    )


@pytest.mark.anyio
async def test_worker_reports_verified_artifact_in_chain_and_finalization(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    bundle, _codec = _runtime(valid_graph, intent_factory)
    gateway = FakeGateway(bundle, _run(valid_graph, intent_factory))

    await BrowserWorkerService(gateway, ArtifactEngine()).execute(
        tenant_id=bundle.execution_contract.tenant_id,
        run_id=bundle.execution_contract.debug_run_id,
        worker_identity=bundle.execution_contract.worker_identity,
    )

    assert BrowserRuntimeReportKind.ARTIFACT_CAPTURED in {
        report.value.kind for report in gateway.reports
    }
    assert gateway.finalization is not None
    assert len(gateway.finalization.artifacts) == 1
    artifact = gateway.finalization.artifacts[0]
    assert artifact.integrity is EvidenceIntegrity.VERIFIED
    assert artifact.id in gateway.finalization.assertion_results[0].evidence_refs


@pytest.mark.anyio
async def test_engine_failure_becomes_inconclusive_without_leaking_exception(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    bundle, _codec = _runtime(valid_graph, intent_factory)
    gateway = FakeGateway(bundle, _run(valid_graph, intent_factory))
    service = BrowserWorkerService(gateway, FailingEngine())
    await service.execute(
        tenant_id=bundle.execution_contract.tenant_id,
        run_id=bundle.execution_contract.debug_run_id,
        worker_identity=bundle.execution_contract.worker_identity,
    )
    assert gateway.finalization is not None
    assert all(
        item.status is AssertionStatus.INCONCLUSIVE
        for item in gateway.finalization.assertion_results
    )
    blocked = next(
        item.value
        for item in gateway.reports
        if item.value.kind is BrowserRuntimeReportKind.EXECUTION_BLOCKED
    )
    assert blocked.payload["failureType"] == "RuntimeError"
    assert "secret-value" not in blocked.model_dump_json(by_alias=True)


@pytest.mark.anyio
async def test_direct_runtime_gateway_forwards_every_trusted_port(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    bundle, _codec = _runtime(valid_graph, intent_factory)
    run = _run(valid_graph, intent_factory)
    contract = bundle.execution_contract
    raw_report = build_browser_runtime_report(
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
        report_id=uuid7(),
        sequence=1,
        kind=BrowserRuntimeReportKind.EXECUTION_STARTED,
        payload={
            "safeSummary": "browser execution started",
            "planDigest": contract.plan_digest,
        },
        occurred_at=bundle.issued_at,
        previous_chain_digest=CHAIN_START_DIGEST,
    )
    persisted_report = BrowserRuntimeReport(
        tenant_id=contract.tenant_id,
        project_id=contract.project_id,
        environment_id=contract.environment_id,
        debug_run_id=run.id,
        value=raw_report,
        recorded_at=bundle.issued_at,
    )
    manifest = cast(EvidenceManifest, object())
    command = cast(FinalizeDebugEvidence, object())
    service = RecordingRuntimeService(
        bundle=bundle,
        run=run,
        report=persisted_report,
        manifest=manifest,
    )
    gateway = DirectDebugRuntimeGateway(cast(DebugRuntimeService, service))
    assert await gateway.get_execution_bundle(
        tenant_id=contract.tenant_id,
        run_id=run.id,
        worker_identity=contract.worker_identity,
    ) == bundle
    assert await gateway.mark_ready(
        tenant_id=contract.tenant_id,
        run_id=run.id,
        worker_identity=contract.worker_identity,
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
    ) == run
    assert await gateway.start_execution(
        tenant_id=contract.tenant_id,
        run_id=run.id,
        worker_identity=contract.worker_identity,
        execution_contract_id=contract.id,
        execution_contract_digest=contract.content_digest,
    ) == run
    assert await gateway.append_report(
        tenant_id=contract.tenant_id,
        run_id=run.id,
        worker_identity=contract.worker_identity,
        report=raw_report,
    ) == persisted_report
    assert await gateway.finalize_evidence(
        tenant_id=contract.tenant_id,
        run_id=run.id,
        worker_identity=contract.worker_identity,
        command=command,
    ) == (run, manifest)
    assert service.calls == ["bundle", "ready", "start", "report", "finalize"]
