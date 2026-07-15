"""Real Chromium checks for the restricted Browser Worker adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import cast
from uuid import UUID, uuid7

import pytest
from playwright.async_api import Page, async_playwright
from pydantic import JsonValue
from tests.domain.case.test_runtime_evidence import _bind_command, _contract, _run

from atlas_testops.application.ports.browser_runtime import (
    BrowserExecutionOutput,
    BrowserExecutionReporter,
)
from atlas_testops.application.ports.sessions import (
    DecryptedSessionOperation,
    SealedSessionArtifact,
    SessionArtifactScope,
)
from atlas_testops.core.contracts import utc_now
from atlas_testops.domain.case import TestIntent as CaseIntent
from atlas_testops.domain.runtime import (
    CHAIN_START_DIGEST,
    AssertionResultInput,
    AssertionStatus,
    BindDebugExecution,
    BrowserActionKind,
    BrowserActionProposal,
    BrowserActionRisk,
    BrowserContextRestoreDescriptor,
    BrowserExecutionBundle,
    BrowserExecutionProfile,
    BrowserPolicyDecisionKind,
    BrowserRuntimeReport,
    BrowserRuntimeReportKind,
    EvidenceArtifactInput,
    EvidenceArtifactKind,
    EvidenceIntegrity,
    ToolExecutionProfile,
    build_browser_runtime_report,
    build_execution_contract,
    expected_assertion_digest,
)
from atlas_testops.domain.workflow import WorkflowGraph
from atlas_testops.infrastructure.adapters.playwright_browser import (
    BrowserExecutionError,
    BrowserOperationRegistry,
    BrowserPlanOperationContext,
    BrowserRouteDefinition,
    BrowserRouteRegistry,
    BrowserToolCatalog,
    BrowserToolSession,
    PlaywrightBrowserExecutionEngine,
    PlaywrightExecutionRuntime,
    _actual_browser_revision,
    _normalized_web_socket_origin,
)
from atlas_testops.infrastructure.browser_envelope import AesGcmBrowserContextEnvelopeCodec


class MemoryVault:
    """Test Vault that still confines plaintext to a callback."""

    def __init__(self, state: bytes) -> None:
        self._state = bytearray(state)

    def object_ref_for(self, *, tenant_id: UUID, artifact_id: UUID) -> str:
        return f"session-vault://{tenant_id}/{artifact_id}"

    async def seal(
        self,
        *,
        object_ref: str,
        scope: SessionArtifactScope,
        plaintext: memoryview,
    ) -> SealedSessionArtifact:
        raise NotImplementedError

    async def with_decrypted[T](
        self,
        *,
        object_ref: str,
        scope: SessionArtifactScope,
        expected_digest: str,
        expected_key_version: str,
        operation: DecryptedSessionOperation[T],
    ) -> T:
        assert object_ref.startswith("session-vault://")
        assert scope.actor_id if hasattr(scope, "actor_id") else True
        assert expected_digest.startswith("sha256:")
        assert expected_key_version == "session-v1"
        return await operation(memoryview(self._state))

    async def delete(self, object_ref: str) -> None:
        self._state[:] = b"\x00" * len(self._state)


class RecordingReporter(BrowserExecutionReporter):
    def __init__(self, bundle: BrowserExecutionBundle) -> None:
        self._bundle = bundle
        self.reports: list[BrowserRuntimeReport] = []

    async def emit(
        self,
        kind: BrowserRuntimeReportKind,
        payload: dict[str, JsonValue],
        *,
        actor_slot: str | None = None,
        action_id: UUID | None = None,
    ) -> BrowserRuntimeReport:
        previous = (
            self.reports[-1].value.chain_digest if self.reports else CHAIN_START_DIGEST
        )
        report = build_browser_runtime_report(
            execution_contract_id=self._bundle.execution_contract.id,
            execution_contract_digest=self._bundle.execution_contract.content_digest,
            report_id=uuid7(),
            sequence=len(self.reports) + 1,
            kind=kind,
            payload=payload,
            occurred_at=utc_now(),
            previous_chain_digest=previous,
            actor_slot=actor_slot,
            action_id=action_id,
        )
        persisted = BrowserRuntimeReport(
            tenant_id=self._bundle.execution_contract.tenant_id,
            project_id=self._bundle.execution_contract.project_id,
            environment_id=self._bundle.execution_contract.environment_id,
            debug_run_id=self._bundle.execution_contract.debug_run_id,
            value=report,
            recorded_at=report.occurred_at,
        )
        self.reports.append(persisted)
        return persisted


class ReorderSafeClickOperation:
    def __init__(self) -> None:
        self.clicked_primary = False

    async def execute(self, context: BrowserPlanOperationContext) -> BrowserExecutionOutput:
        opened = await context.tools.execute(
            BrowserActionProposal(
                action_id=uuid7(),
                node_id=context.node.id,
                actor_slot="operator",
                action=BrowserActionKind.OPEN_ROUTE,
                risk=BrowserActionRisk.NAVIGATE,
                route_key="customer.list",
                safe_summary="open the reviewed customer list route",
            )
        )
        observation = opened.observation or await context.tools.observe()
        primary = next(
            item
            for item in observation.target_candidates
            if item.accessible_name == "Primary"
        )
        await asyncio.sleep(0.12)
        clicked = await context.tools.execute(
            BrowserActionProposal(
                action_id=uuid7(),
                node_id=context.node.id,
                actor_slot="operator",
                action=BrowserActionKind.ACTIVATE,
                risk=BrowserActionRisk.MUTATION,
                expected_observation_ref=observation.observation_ref,
                expected_page_revision=observation.page_revision,
                next_step_nonce=observation.next_step_nonce,
                target_ref=primary.target_ref,
                safe_summary="activate the observed primary action",
            )
        )
        assert clicked.receipt.status == "SUCCEEDED"
        after = clicked.observation or await context.tools.observe()
        self.clicked_primary = "result primary" in after.untrusted_page_summary.casefold()
        return BrowserExecutionOutput()


class AssertionOperation:
    def __init__(self, click: ReorderSafeClickOperation) -> None:
        self._click = click

    async def execute(self, context: BrowserPlanOperationContext) -> BrowserExecutionOutput:
        specification = next(
            item
            for item in context.bundle.test_ir.assertions
            if item.node_id == context.node.id
        )
        return BrowserExecutionOutput(
            assertion_results=(
                AssertionResultInput(
                    assertion_id=specification.assertion_id,
                    status=(
                        AssertionStatus.PASSED
                        if self._click.clicked_primary
                        else AssertionStatus.FAILED
                    ),
                    expected_digest=expected_assertion_digest(
                        context.bundle.test_ir,
                        specification.assertion_id,
                    ),
                    actual_safe_summary="The stable observed element received the action.",
                    evaluator_version_ref=specification.evaluator_version_ref,
                    observed_at=utc_now(),
                    duration_ms=1,
                ),
            )
        )


class UntrustedArtifactOperation:
    async def execute(self, context: BrowserPlanOperationContext) -> BrowserExecutionOutput:
        return BrowserExecutionOutput(
            artifacts=(
                EvidenceArtifactInput(
                    id=uuid7(),
                    kind=EvidenceArtifactKind.SCREENSHOT,
                    object_ref="evidence://untrusted/direct-return.png",
                    content_digest="sha256:" + "c" * 64,
                    size_bytes=128,
                    mime_type="image/png",
                    redaction_policy_digest="sha256:" + "d" * 64,
                    integrity=EvidenceIntegrity.VERIFIED,
                    required=True,
                    captured_at=context.bundle.issued_at,
                ),
            )
        )


class NoopToolSession:
    def require_deterministic_action_outcomes(self) -> None:
        return

    def drain_captured_artifacts(self) -> tuple[EvidenceArtifactInput, ...]:
        return ()


class NoopPage:
    def on(self, *args: object) -> None:
        return


async def _serve_page() -> tuple[asyncio.AbstractServer, str]:
    html = b"""<!doctype html><html><head><title>Runtime Test</title></head>
    <body><div id='actions'>
    <button data-testid='primary' aria-label='Primary'
      onclick="document.getElementById('result').textContent='result primary'">Primary</button>
    <button data-testid='secondary' aria-label='Secondary'
      onclick="document.getElementById('result').textContent='result secondary'">Secondary</button>
    </div><div id='result'>result pending</div>
    <script>setTimeout(() => {
      const actions = document.getElementById('actions');
      actions.insertBefore(actions.children[1], actions.children[0]);
    }, 50);</script></body></html>"""

    async def handle(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                + f"Content-Length: {len(html)}\r\nConnection: close\r\n\r\n".encode()
                + html
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    socket = server.sockets[0]
    port = socket.getsockname()[1]
    return server, f"http://127.0.0.1:{port}"


async def _installed_revision() -> str:
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    try:
        return _actual_browser_revision(browser)
    finally:
        await browser.close()
        await playwright.stop()


def _bundle(
    graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
    *,
    revision: str,
    origin: str,
    catalog: BrowserToolCatalog,
) -> tuple[BrowserExecutionBundle, AesGcmBrowserContextEnvelopeCodec]:
    run = _run(graph, intent_factory)
    base_contract = _contract(run, run.requested_at + timedelta(seconds=1))
    base_command = _bind_command()
    command = BindDebugExecution(
        worker_identity=base_command.worker_identity,
        fixture_run_id=base_command.fixture_run_id,
        actors=base_command.actors,
        browser=BrowserExecutionProfile(
            revision=revision,
            viewport=base_command.browser.viewport,
            locale=base_command.browser.locale,
            timezone=base_command.browser.timezone,
        ),
        model=base_command.model,
        tools=ToolExecutionProfile(
            tool_catalog_ref=catalog.catalog_ref,
            mcp_server_manifest_digest=catalog.mcp_server_manifest_digest,
            tool_schema_digest=catalog.tool_schema_digest,
            policy_bundle_ref=catalog.policy_bundle_ref,
            policy_digest=catalog.policy_digest,
        ),
    )
    contract = build_execution_contract(
        contract_id=uuid7(),
        run=run,
        command=command,
        actors=base_contract.actors,
        fixture=base_contract.fixture,
        created_at=base_contract.created_at,
    )
    descriptor = BrowserContextRestoreDescriptor(
        actor_slot="operator",
        browser_context_ref=contract.actors[0].browser_context_ref,
        artifact_id=uuid7(),
        tenant_id=contract.tenant_id,
        project_id=contract.project_id,
        environment_id=contract.environment_id,
        lease_id=contract.actors[0].account_lease_id,
        lease_fence=contract.actors[0].fencing_token,
        account_id=uuid7(),
        connector_installation_id=uuid7(),
        credential_binding_id=uuid7(),
        allowed_origins=(origin,),
        object_ref="session-vault://runtime/storage-state",
        object_digest="sha256:" + "a" * 64,
        key_version="session-v1",
        expires_at=contract.execution_deadline,
    )
    codec = AesGcmBrowserContextEnvelopeCodec(b"e" * 32, key_version="envelope-v1")
    return (
        BrowserExecutionBundle(
            execution_contract=contract,
            test_ir=run.test_ir,
            plan_template=run.plan_template,
            fixture_exports={"customerId": "customer-42"},
            restore_envelopes=(codec.seal(descriptor, contract=contract),),
            issued_at=contract.created_at + timedelta(milliseconds=1),
        ),
        codec,
    )


@pytest.mark.anyio
async def test_real_chromium_executes_stable_observed_element_after_dom_reorder(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    server, origin = await _serve_page()
    revision = await _installed_revision()
    catalog = BrowserToolCatalog.reviewed(
        catalog_ref="tools.browser-safe@1.0.0",
        policy_bundle_ref="policy.browser-safe@1.0.0",
        allowed_actions=frozenset(
            {BrowserActionKind.OPEN_ROUTE, BrowserActionKind.ACTIVATE}
        ),
    )
    bundle, codec = _bundle(
        valid_graph,
        intent_factory,
        revision=revision,
        origin=origin,
        catalog=catalog,
    )
    click = ReorderSafeClickOperation()
    operations = BrowserOperationRegistry()
    operations.register("agent.semantic-filter@1.0.0", click)
    operations.register("assert.customer-visible@1.0.0", AssertionOperation(click))
    routes = BrowserRouteRegistry(
        (
            BrowserRouteDefinition(
                route_key="customer.list",
                surface_version_ref="surface.customer-relationship@1.0.0",
                surface_digest=bundle.test_ir.surfaces[0].content_digest,
                absolute_url=origin,
            ),
        )
    )
    runtime = PlaywrightExecutionRuntime(revision=revision, maximum_concurrency=1)
    engine = PlaywrightBrowserExecutionEngine(
        runtime=runtime,
        session_vault=MemoryVault(b'{"cookies":[],"origins":[]}'),
        envelope_codec=codec,
        tool_catalog=catalog,
        route_registry=routes,
        operation_registry=operations,
    )
    reporter = RecordingReporter(bundle)
    await reporter.emit(
        BrowserRuntimeReportKind.EXECUTION_STARTED,
        {
            "safeSummary": "browser execution started",
            "planDigest": bundle.plan_template.plan_digest,
        },
    )
    try:
        output = await engine.execute(bundle, reporter)
    finally:
        await runtime.close()
        server.close()
        await server.wait_closed()
    assert click.clicked_primary
    assert output.assertion_results[0].status is AssertionStatus.PASSED
    assert any(
        report.value.kind is BrowserRuntimeReportKind.ACTION_EXECUTED
        for report in reporter.reports
    )


@pytest.mark.anyio
async def test_runtime_rejects_claimed_revision_and_catalog_digest_mismatch() -> None:
    runtime = PlaywrightExecutionRuntime(revision="playwright@0.0.0/chromium@0.0.0")
    with pytest.raises(BrowserExecutionError, match="does not match"):
        await runtime.start()
    await runtime.close()
    valid = BrowserToolCatalog.reviewed(
        catalog_ref="tools.browser-safe@1.0.0",
        policy_bundle_ref="policy.browser-safe@1.0.0",
        allowed_actions=frozenset({BrowserActionKind.OPEN_ROUTE}),
    )
    with pytest.raises(ValueError, match="policy digest"):
        BrowserToolCatalog(
            catalog_ref=valid.catalog_ref,
            policy_bundle_ref=valid.policy_bundle_ref,
            mcp_server_manifest_digest=valid.mcp_server_manifest_digest,
            tool_schema_digest=valid.tool_schema_digest,
            policy_digest="sha256:" + "f" * 64,
            allowed_actions=valid.allowed_actions,
        )
    assert _normalized_web_socket_origin("wss://example.test/socket") == "https://example.test"
    with pytest.raises(ValueError, match="WebSocket"):
        _normalized_web_socket_origin("ftp://example.test")


@pytest.mark.anyio
async def test_operation_cannot_return_artifact_without_trusted_writer(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    catalog = BrowserToolCatalog.reviewed(
        catalog_ref="tools.browser-safe@1.0.0",
        policy_bundle_ref="policy.browser-safe@1.0.0",
        allowed_actions=frozenset({BrowserActionKind.OPEN_ROUTE}),
    )
    bundle, codec = _bundle(
        valid_graph,
        intent_factory,
        revision="playwright@0.0.0/chromium@0.0.0",
        origin="https://example.test",
        catalog=catalog,
    )
    operations = BrowserOperationRegistry()
    operations.register("agent.semantic-filter@1.0.0", UntrustedArtifactOperation())
    engine = PlaywrightBrowserExecutionEngine(
        runtime=PlaywrightExecutionRuntime(revision=bundle.execution_contract.browser.revision),
        session_vault=MemoryVault(b'{"cookies":[],"origins":[]}'),
        envelope_codec=codec,
        tool_catalog=catalog,
        route_registry=BrowserRouteRegistry(),
        operation_registry=operations,
    )

    with pytest.raises(BrowserExecutionError, match="trusted artifact writer"):
        await engine._execute_plan(
            bundle,
            cast(BrowserToolSession, NoopToolSession()),
            RecordingReporter(bundle),
        )


def test_unregistered_route_is_reportable_policy_denial(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    catalog = BrowserToolCatalog.reviewed(
        catalog_ref="tools.browser-safe@1.0.0",
        policy_bundle_ref="policy.browser-safe@1.0.0",
        allowed_actions=frozenset({BrowserActionKind.OPEN_ROUTE}),
    )
    bundle, _codec = _bundle(
        valid_graph,
        intent_factory,
        revision="playwright@0.0.0/chromium@0.0.0",
        origin="https://example.test",
        catalog=catalog,
    )
    tools = BrowserToolSession(
        bundle=bundle,
        actor_slot="operator",
        page=cast(Page, NoopPage()),
        allowed_origins=("https://example.test",),
        catalog=catalog,
        routes=BrowserRouteRegistry(),
        reporter=RecordingReporter(bundle),
        artifact_writer=None,
        action_timeout=timedelta(seconds=1),
    )

    decision = tools._decide(
        BrowserActionProposal(
            action_id=uuid7(),
            node_id="filter-agent",
            actor_slot="operator",
            action=BrowserActionKind.OPEN_ROUTE,
            risk=BrowserActionRisk.NAVIGATE,
            route_key="missing.route",
            safe_summary="open a route that is absent from the reviewed registry",
        )
    )

    assert decision.decision is BrowserPolicyDecisionKind.DENY
    assert decision.matched_rules == ("route.not_allowed",)
