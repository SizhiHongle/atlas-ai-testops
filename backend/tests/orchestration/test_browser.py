"""Bound Browser workflow dispatch tests."""

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid7

import pytest
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from tests.application.test_browser_execution import FakeGateway, PassingEngine
from tests.domain.case.test_runtime_evidence import _contract, _run
from tests.domain.runtime.test_browser_protocol import _runtime

from atlas_testops.application.ports.browser_runtime import BrowserExecutionEngine
from atlas_testops.core.contracts import utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import DebugRun, DebugRunLifecycle
from atlas_testops.domain.case import TestIntent as CaseIntent
from atlas_testops.domain.runtime import (
    BrowserExecutionBundle,
    EvidenceManifest,
    ExecutionContract,
    FinalizeDebugEvidence,
)
from atlas_testops.domain.workflow import WorkflowGraph
from atlas_testops.infrastructure.browser_auth import BrowserRuntimePermitSigner
from atlas_testops.orchestration import browser as browser_orchestration
from atlas_testops.orchestration.browser import (
    BrowserExecutionActivities,
    BrowserExecutionPayload,
    BrowserExecutionWorkflow,
    BrowserExecutionWorkflowInput,
    TemporalBrowserExecutionDispatcher,
)


class RecordingClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.input: BrowserExecutionWorkflowInput | None = None
        self.options: dict[str, Any] = {}
        self.error = error

    async def start_workflow(
        self,
        workflow: object,
        request: BrowserExecutionWorkflowInput,
        **options: Any,
    ) -> object:
        self.input = request
        self.options = options
        if self.error is not None:
            raise self.error
        return object()


class _ManifestProjection:
    def __init__(self) -> None:
        self.id = uuid7()
        self.content_digest = "sha256:" + "f" * 64


class CloseableFakeGateway(FakeGateway):
    def __init__(self, bundle: BrowserExecutionBundle, run: DebugRun) -> None:
        super().__init__(bundle, run)
        self.closed = False

    async def finalize_evidence(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        command: FinalizeDebugEvidence,
    ) -> tuple[DebugRun, EvidenceManifest]:
        await super().finalize_evidence(
            tenant_id=tenant_id,
            run_id=run_id,
            worker_identity=worker_identity,
            command=command,
        )
        return self.run, cast(EvidenceManifest, _ManifestProjection())

    async def aclose(self) -> None:
        self.closed = True


class SingleGatewayFactory:
    def __init__(self, gateway: CloseableFakeGateway) -> None:
        self.gateway = gateway
        self.created = False

    def create(
        self,
        *,
        tenant_id: UUID,
        worker_identity: str,
        execution_permit: str,
    ) -> CloseableFakeGateway:
        assert tenant_id == self.gateway.bundle.execution_contract.tenant_id
        assert worker_identity == self.gateway.bundle.execution_contract.worker_identity
        assert execution_permit == "permit-value"
        self.created = True
        return self.gateway


def _bound_runtime(
    graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> tuple[DebugRun, ExecutionContract]:
    run = _run(graph, intent_factory)
    contract = _contract(run, run.requested_at + timedelta(seconds=1))
    bound = DebugRun.model_validate(
        run.model_copy(
            update={
                "lifecycle": DebugRunLifecycle.BINDING,
                "started_at": contract.created_at,
                "execution_contract_id": contract.id,
                "execution_contract_digest": contract.content_digest,
                "revision": 2,
                "updated_at": contract.created_at,
            }
        ).model_dump(mode="python")
    )
    return bound, contract


@pytest.mark.anyio
async def test_dispatcher_mints_run_scoped_permit_and_uses_single_workflow(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    run, raw_contract = _bound_runtime(valid_graph, intent_factory)
    contract = raw_contract
    client = RecordingClient()
    signer = BrowserRuntimePermitSigner(
        b"p" * 32,
        maximum_lifetime=timedelta(minutes=12),
    )
    dispatcher = TemporalBrowserExecutionDispatcher(
        cast(Client, client),
        task_queue="atlas-browser",
        worker_identity=contract.worker_identity,
        permit_signer=signer,
        activity_timeout=timedelta(minutes=10),
        heartbeat_timeout=timedelta(seconds=20),
        permit_ttl=timedelta(minutes=12),
    )
    await dispatcher.start_bound(run, contract)
    assert client.input is not None
    claims = signer.verify(client.input.execution_permit, now=utc_now())
    assert claims.run_id == run.id
    assert claims.tenant_id == run.tenant_id
    assert client.options["id"] == run.temporal_workflow_id
    assert client.options["task_queue"] == "atlas-browser"


@pytest.mark.anyio
async def test_dispatcher_rejects_unbound_run_and_invalid_time_relationships(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    run = _run(valid_graph, intent_factory)
    contract = _contract(run, run.requested_at + timedelta(seconds=1))
    signer = BrowserRuntimePermitSigner(
        b"p" * 32,
        maximum_lifetime=timedelta(minutes=12),
    )
    client = cast(Client, RecordingClient())
    dispatcher = TemporalBrowserExecutionDispatcher(
        client,
        task_queue="atlas-browser",
        worker_identity=contract.worker_identity,
        permit_signer=signer,
        activity_timeout=timedelta(minutes=10),
        heartbeat_timeout=timedelta(seconds=20),
        permit_ttl=timedelta(minutes=12),
    )
    with pytest.raises(ValueError, match="bound contract"):
        await dispatcher.start_bound(run, contract)
    with pytest.raises(ValueError, match="heartbeat"):
        TemporalBrowserExecutionDispatcher(
            client,
            task_queue="atlas-browser",
            worker_identity=contract.worker_identity,
            permit_signer=signer,
            activity_timeout=timedelta(seconds=20),
            heartbeat_timeout=timedelta(seconds=20),
            permit_ttl=timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="task queue"):
        TemporalBrowserExecutionDispatcher(
            client,
            task_queue=" ",
            worker_identity=contract.worker_identity,
            permit_signer=signer,
            activity_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=10),
            permit_ttl=timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="worker identity"):
        TemporalBrowserExecutionDispatcher(
            client,
            task_queue="atlas-browser",
            worker_identity="x",
            permit_signer=signer,
            activity_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=10),
            permit_ttl=timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="permit TTL"):
        TemporalBrowserExecutionDispatcher(
            client,
            task_queue="atlas-browser",
            worker_identity=contract.worker_identity,
            permit_signer=signer,
            activity_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=10),
            permit_ttl=timedelta(seconds=30),
        )


@pytest.mark.anyio
async def test_activity_executes_service_heartbeats_and_closes_gateway(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _codec = _runtime(valid_graph, intent_factory)
    run = _run(valid_graph, intent_factory)
    gateway = CloseableFakeGateway(bundle, run)
    factory = SingleGatewayFactory(gateway)
    heartbeats: list[str] = []
    monkeypatch.setattr(
        activity,
        "heartbeat",
        lambda detail: heartbeats.append(cast(str, detail)),
    )
    activities = BrowserExecutionActivities(
        gateway_factory=factory,
        engine=cast(BrowserExecutionEngine, PassingEngine()),
    )

    result = await activities.execute(
        BrowserExecutionWorkflowInput(
            tenant_id=str(run.tenant_id),
            run_id=str(run.id),
            worker_identity=bundle.execution_contract.worker_identity,
            execution_permit="permit-value",
            activity_timeout_seconds=30,
            heartbeat_timeout_seconds=2,
        )
    )

    assert result.run_id == str(run.id)
    assert result.evidence_manifest_digest == "sha256:" + "f" * 64
    assert factory.created is True
    assert gateway.closed is True
    assert gateway.finalization is not None
    assert heartbeats in ([], ["browser execution active"])


@pytest.mark.anyio
async def test_heartbeat_loop_and_workflow_activity_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopHeartbeat(RuntimeError):
        pass

    heartbeats: list[str] = []

    async def stop_after_first_sleep(interval_seconds: int) -> None:
        assert interval_seconds == 3
        raise StopHeartbeat

    monkeypatch.setattr(
        activity,
        "heartbeat",
        lambda detail: heartbeats.append(cast(str, detail)),
    )
    monkeypatch.setattr(asyncio, "sleep", stop_after_first_sleep)
    with pytest.raises(StopHeartbeat):
        await BrowserExecutionActivities._heartbeat(3)
    assert heartbeats == ["browser execution active"]

    request = BrowserExecutionWorkflowInput(
        tenant_id=str(uuid7()),
        run_id=str(uuid7()),
        worker_identity="browser-worker-v1",
        execution_permit="permit-value",
        activity_timeout_seconds=45,
        heartbeat_timeout_seconds=9,
    )
    expected = BrowserExecutionPayload(
        run_id=request.run_id,
        lifecycle="TERMINATED",
        outcome="PASSED",
        evidence_manifest_id=str(uuid7()),
        evidence_manifest_digest="sha256:" + "e" * 64,
    )
    captured: dict[str, object] = {}

    async def execute_activity(
        activity_name: object,
        activity_input: object,
        **options: object,
    ) -> BrowserExecutionPayload:
        captured["activity_name"] = activity_name
        captured["activity_input"] = activity_input
        captured.update(options)
        return expected

    monkeypatch.setattr(
        workflow,
        "execute_activity",
        execute_activity,
    )
    result = await BrowserExecutionWorkflow().run(request)
    assert result == expected
    assert captured["activity_input"] == request
    assert captured["start_to_close_timeout"] == timedelta(seconds=45)
    assert captured["heartbeat_timeout"] == timedelta(seconds=9)


@pytest.mark.anyio
async def test_dispatcher_handles_idempotent_start_dependency_and_short_window(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, contract = _bound_runtime(valid_graph, intent_factory)
    signer = BrowserRuntimePermitSigner(
        b"p" * 32,
        maximum_lifetime=timedelta(minutes=12),
    )

    def dispatcher(client: RecordingClient) -> TemporalBrowserExecutionDispatcher:
        return TemporalBrowserExecutionDispatcher(
            cast(Client, client),
            task_queue="atlas-browser",
            worker_identity=contract.worker_identity,
            permit_signer=signer,
            activity_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(seconds=20),
            permit_ttl=timedelta(minutes=12),
        )

    already_started = RecordingClient(
        error=WorkflowAlreadyStartedError(
            run.temporal_workflow_id,
            "atlas.browser-execution-workflow/0.1",
        )
    )
    await dispatcher(already_started).start_bound(run, contract)

    unavailable = RecordingClient(
        error=RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
    )
    with pytest.raises(ApplicationError) as captured:
        await dispatcher(unavailable).start_bound(run, contract)
    assert captured.value.error_code is ErrorCode.DEPENDENCY_UNAVAILABLE

    monkeypatch.setattr(
        browser_orchestration,
        "utc_now",
        lambda: contract.execution_deadline - timedelta(minutes=5),
    )
    with pytest.raises(ApplicationError) as short_window:
        await dispatcher(RecordingClient()).start_bound(run, contract)
    assert short_window.value.error_code is ErrorCode.CONFLICT
