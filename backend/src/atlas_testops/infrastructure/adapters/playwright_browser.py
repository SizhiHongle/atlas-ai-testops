"""Restricted Playwright execution engine for frozen Browser Worker plans."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import timedelta
from importlib.metadata import version as distribution_version
from json import JSONDecodeError, loads
from re import sub
from secrets import token_urlsafe
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit
from uuid import UUID

from playwright.async_api import (
    Browser,
    BrowserContext,
    ElementHandle,
    Locator,
    Page,
    Playwright,
    Route,
    StorageState,
    WebSocketRoute,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import JsonValue

from atlas_testops.application.ports.browser_runtime import (
    BrowserContextEnvelopeCodec,
    BrowserExecutionEngine,
    BrowserExecutionOutput,
    BrowserExecutionReporter,
)
from atlas_testops.application.ports.evidence import (
    BrowserArtifactWriter as BrowserArtifactWriter,
)
from atlas_testops.application.ports.evidence import (
    EvidenceArtifactWriteScope,
)
from atlas_testops.application.ports.sessions import (
    SessionArtifactScope,
    SessionArtifactVault,
)
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.domain.case import ValueSourceKind, canonical_digest
from atlas_testops.domain.runtime import (
    AssertionResultInput,
    BrowserActionGrant,
    BrowserActionKind,
    BrowserActionProposal,
    BrowserActionRisk,
    BrowserExecutionBundle,
    BrowserExecutionReceipt,
    BrowserObservation,
    BrowserPolicyDecision,
    BrowserPolicyDecisionKind,
    BrowserRuntimeReportKind,
    BrowserTargetCandidate,
    EvidenceArtifactInput,
    EvidenceArtifactKind,
    browser_action_proposal_digest,
)
from atlas_testops.domain.workflow import WorkflowNode
from atlas_testops.infrastructure.session_vault import SessionArtifactIntegrityError

_OBSERVED_ROLES = (
    "button",
    "textbox",
    "link",
    "combobox",
    "checkbox",
    "radio",
)
_ALLOWED_KEYS = frozenset(
    {
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "Enter",
        "Escape",
        "Space",
        "Tab",
    }
)
_EXECUTABLE_ACTIONS = frozenset(
    {
        BrowserActionKind.OPEN_ROUTE,
        BrowserActionKind.ACTIVATE,
        BrowserActionKind.ENTER_TEXT,
        BrowserActionKind.CHOOSE_OPTION,
        BrowserActionKind.KEYPRESS,
        BrowserActionKind.SCROLL,
        BrowserActionKind.CAPTURE_VIEW,
    }
)
_ALLOWED_RISKS = {
    BrowserActionKind.OPEN_ROUTE: frozenset({BrowserActionRisk.NAVIGATE}),
    BrowserActionKind.ACTIVATE: frozenset(
        {BrowserActionRisk.NAVIGATE, BrowserActionRisk.MUTATION}
    ),
    BrowserActionKind.ENTER_TEXT: frozenset({BrowserActionRisk.INPUT}),
    BrowserActionKind.CHOOSE_OPTION: frozenset({BrowserActionRisk.INPUT}),
    BrowserActionKind.KEYPRESS: frozenset({BrowserActionRisk.INPUT}),
    BrowserActionKind.SCROLL: frozenset({BrowserActionRisk.READ}),
    BrowserActionKind.CAPTURE_VIEW: frozenset({BrowserActionRisk.READ}),
}
_ALLOWED_TARGET_ROLES = {
    BrowserActionKind.ACTIVATE: frozenset({"button", "link", "checkbox", "radio"}),
    BrowserActionKind.ENTER_TEXT: frozenset({"textbox"}),
    BrowserActionKind.CHOOSE_OPTION: frozenset({"combobox"}),
}
_MAX_SCREENSHOT_FRAMES = 32


def _tool_schema_digest(
    catalog_ref: str,
    allowed_actions: frozenset[BrowserActionKind],
) -> str:
    material: dict[str, JsonValue] = {
        "schemaVersion": "atlas.browser-tool-catalog/0.1",
        "catalogRef": catalog_ref,
        "allowedActions": [item.value for item in sorted(allowed_actions, key=str)],
    }
    return canonical_digest(material)


def _policy_digest(
    policy_bundle_ref: str,
    catalog_ref: str,
    allowed_actions: frozenset[BrowserActionKind],
) -> str:
    risk_rules: dict[str, JsonValue] = {
        action.value: [risk.value for risk in sorted(risks, key=str)]
        for action, risks in sorted(_ALLOWED_RISKS.items(), key=lambda item: item[0].value)
        if action in allowed_actions
    }
    target_role_rules: dict[str, JsonValue] = {
        action.value: list(sorted(roles))
        for action, roles in sorted(
            _ALLOWED_TARGET_ROLES.items(),
            key=lambda item: item[0].value,
        )
        if action in allowed_actions
    }
    material: dict[str, JsonValue] = {
        "schemaVersion": "atlas.browser-policy/0.1",
        "policyBundleRef": policy_bundle_ref,
        "catalogRef": catalog_ref,
        "allowedActions": [item.value for item in sorted(allowed_actions, key=str)],
        "riskRules": risk_rules,
        "targetRoleRules": target_role_rules,
        "allowedKeys": list(sorted(_ALLOWED_KEYS)),
        "routePolicy": "exact-surface-and-session-origin",
        "grantExecutions": 1,
    }
    return canonical_digest(material)


def _empty_mcp_manifest_digest() -> str:
    return canonical_digest(
        {
            "schemaVersion": "atlas.mcp-server-manifest/0.1",
            "servers": [],
        }
    )


class BrowserExecutionError(RuntimeError):
    """Safe base failure for a fail-closed Browser Worker execution."""


class BrowserOperationUnavailableError(BrowserExecutionError):
    """No deployment-reviewed operation exists for one exact node version."""


class BrowserPolicyDeniedError(BrowserExecutionError):
    """The deterministic policy rejected a structured browser proposal."""


class BrowserTargetStaleError(BrowserExecutionError):
    """A proposal referenced an observation invalidated by a page change."""


class BrowserPlanOperation(Protocol):
    """Deployment-owned exact implementation for one Plan node version."""

    async def execute(
        self,
        context: BrowserPlanOperationContext,
    ) -> BrowserExecutionOutput: ...


@dataclass(frozen=True, slots=True)
class BrowserRouteDefinition:
    """Deployment-owned route mapping; an Agent never supplies an absolute URL."""

    route_key: str
    surface_version_ref: str
    surface_digest: str
    absolute_url: str


@dataclass(frozen=True, slots=True)
class BrowserToolCatalog:
    """Exact deployment tool catalog matched against ExecutionContract digests."""

    catalog_ref: str
    policy_bundle_ref: str
    mcp_server_manifest_digest: str
    tool_schema_digest: str
    policy_digest: str
    allowed_actions: frozenset[BrowserActionKind]

    def __post_init__(self) -> None:
        unsupported = self.allowed_actions.difference(_EXECUTABLE_ACTIONS)
        if unsupported:
            raise ValueError("tool catalog enables actions without a deterministic adapter")
        expected_schema = _tool_schema_digest(self.catalog_ref, self.allowed_actions)
        expected_policy = _policy_digest(
            self.policy_bundle_ref,
            self.catalog_ref,
            self.allowed_actions,
        )
        expected_mcp_manifest = _empty_mcp_manifest_digest()
        if self.tool_schema_digest != expected_schema:
            raise ValueError("tool schema digest does not match executable catalog")
        if self.policy_digest != expected_policy:
            raise ValueError("policy digest does not match executable rules")
        if self.mcp_server_manifest_digest != expected_mcp_manifest:
            raise ValueError("initial Browser Worker requires an empty MCP server manifest")

    @classmethod
    def reviewed(
        cls,
        *,
        catalog_ref: str,
        policy_bundle_ref: str,
        allowed_actions: frozenset[BrowserActionKind],
    ) -> BrowserToolCatalog:
        """Build digests from the rules that will actually execute."""

        tool_schema_digest = _tool_schema_digest(catalog_ref, allowed_actions)
        policy_digest = _policy_digest(
            policy_bundle_ref,
            catalog_ref,
            allowed_actions,
        )
        return cls(
            catalog_ref=catalog_ref,
            policy_bundle_ref=policy_bundle_ref,
            mcp_server_manifest_digest=_empty_mcp_manifest_digest(),
            tool_schema_digest=tool_schema_digest,
            policy_digest=policy_digest,
            allowed_actions=allowed_actions,
        )


@dataclass(frozen=True, slots=True)
class BrowserActionOutcome:
    """One execution receipt plus the objective browser facts captured afterwards."""

    receipt: BrowserExecutionReceipt
    observation: BrowserObservation | None = None
    artifact: EvidenceArtifactInput | None = None


@dataclass(frozen=True, slots=True)
class BrowserPlanOperationContext:
    """Minimal execution view available to a registered node operation."""

    bundle: BrowserExecutionBundle
    node: WorkflowNode
    tools: BrowserToolSession
    reporter: BrowserExecutionReporter


class BrowserOperationRegistry:
    """Exact version registry; requests and persisted assets cannot inject code."""

    def __init__(self) -> None:
        self._operations: dict[str, BrowserPlanOperation] = {}

    def register(self, version_ref: str, operation: BrowserPlanOperation) -> None:
        normalized = version_ref.strip()
        if not normalized or "@" not in normalized:
            raise ValueError("browser operation requires an exact version reference")
        if normalized in self._operations:
            raise ValueError("browser operation version is already registered")
        self._operations[normalized] = operation

    def require(self, version_ref: str) -> BrowserPlanOperation:
        operation = self._operations.get(version_ref)
        if operation is None:
            raise BrowserOperationUnavailableError(
                "no reviewed browser operation is registered for the frozen node"
            )
        return operation


class BrowserRouteRegistry:
    """Resolve published route keys and verify their exact Surface bindings."""

    def __init__(self, definitions: tuple[BrowserRouteDefinition, ...] = ()) -> None:
        self._definitions: dict[str, BrowserRouteDefinition] = {}
        for definition in definitions:
            if definition.route_key in self._definitions:
                raise ValueError("browser route key is already registered")
            parsed = urlsplit(definition.absolute_url)
            if (
                parsed.scheme not in {"http", "https"}
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
            ):
                raise ValueError("browser route must use a safe absolute HTTP(S) URL")
            self._definitions[definition.route_key] = definition

    def require(
        self,
        route_key: str,
        *,
        bundle: BrowserExecutionBundle,
        allowed_origins: frozenset[str],
    ) -> BrowserRouteDefinition:
        definition = self._definitions.get(route_key)
        if definition is None:
            raise BrowserPolicyDeniedError("routeKey is not registered")
        surfaces = {item.version_ref: item for item in bundle.test_ir.surfaces}
        surface = surfaces.get(definition.surface_version_ref)
        if surface is None or surface.content_digest != definition.surface_digest:
            raise BrowserPolicyDeniedError("routeKey Surface binding is stale")
        if _normalized_origin(definition.absolute_url) not in allowed_origins:
            raise BrowserPolicyDeniedError("routeKey origin is outside the session scope")
        return definition

    def route_key_for(self, url: str) -> str | None:
        for key, definition in self._definitions.items():
            if url == definition.absolute_url:
                return key
        return None


class PlaywrightExecutionRuntime:
    """Share one Chromium process while isolating every execution context."""

    def __init__(
        self,
        *,
        revision: str,
        headless: bool = True,
        maximum_concurrency: int = 2,
    ) -> None:
        normalized_revision = revision.strip()
        if not normalized_revision:
            raise ValueError("browser runtime revision must not be blank")
        if not 1 <= maximum_concurrency <= 16:
            raise ValueError("browser execution concurrency must be between 1 and 16")
        self.revision = normalized_revision
        self._headless = headless
        self._semaphore = asyncio.Semaphore(maximum_concurrency)
        self._lifecycle_lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._actual_revision: str | None = None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._browser is not None and self._browser.is_connected():
                return
            if self._browser is not None or self._playwright is not None:
                stale_browser = self._browser
                stale_playwright = self._playwright
                self._browser = None
                self._playwright = None
                self._actual_revision = None
                if stale_browser is not None:
                    with suppress(PlaywrightError):
                        await stale_browser.close()
                if stale_playwright is not None:
                    await stale_playwright.stop()
            manager = async_playwright()
            playwright = await manager.start()
            try:
                browser = await playwright.chromium.launch(headless=self._headless)
                actual_revision = _actual_browser_revision(browser)
                if actual_revision != self.revision:
                    await browser.close()
                    raise BrowserExecutionError(
                        "installed Playwright/Chromium revision does not match configuration"
                    )
            except BaseException:
                await playwright.stop()
                raise
            self._playwright = playwright
            self._browser = browser
            self._actual_revision = actual_revision

    async def close(self) -> None:
        async with self._lifecycle_lock:
            browser = self._browser
            playwright = self._playwright
            self._browser = None
            self._playwright = None
            self._actual_revision = None
            if browser is not None:
                await browser.close()
            if playwright is not None:
                await playwright.stop()

    @asynccontextmanager
    async def restored_context(
        self,
        *,
        bundle: BrowserExecutionBundle,
        storage_state: dict[str, JsonValue],
        allowed_origins: tuple[str, ...],
    ) -> AsyncIterator[BrowserContext]:
        if bundle.execution_contract.browser.revision != self.revision:
            raise BrowserExecutionError("browser revision does not match the contract")
        normalized_origins = frozenset(_normalized_origin(item) for item in allowed_origins)
        _validate_storage_state_origins(storage_state, normalized_origins)
        await self.start()
        async with self._semaphore:
            browser = self._browser
            if browser is None or not browser.is_connected():
                raise BrowserExecutionError("Playwright browser is not running")
            if self._actual_revision != bundle.execution_contract.browser.revision:
                raise BrowserExecutionError("actual browser revision does not match the contract")
            profile = bundle.execution_contract.browser
            context = await browser.new_context(
                accept_downloads=False,
                ignore_https_errors=False,
                java_script_enabled=True,
                storage_state=cast(StorageState, storage_state),
                viewport={
                    "width": profile.viewport.width,
                    "height": profile.viewport.height,
                },
                device_scale_factor=profile.viewport.device_scale_factor,
                locale=profile.locale,
                timezone_id=profile.timezone,
                service_workers="block",
            )

            async def enforce_origin(route: Route) -> None:
                try:
                    origin = _normalized_origin(route.request.url)
                except ValueError:
                    await route.abort("blockedbyclient")
                    return
                if origin not in normalized_origins:
                    await route.abort("blockedbyclient")
                    return
                await route.continue_()

            await context.route("**/*", enforce_origin)

            async def enforce_web_socket(web_socket: WebSocketRoute) -> None:
                try:
                    origin = _normalized_web_socket_origin(web_socket.url)
                except ValueError:
                    await web_socket.close(code=1008, reason="origin policy denied")
                    return
                if origin not in normalized_origins:
                    await web_socket.close(code=1008, reason="origin policy denied")
                    return
                web_socket.connect_to_server()

            await context.route_web_socket("**/*", enforce_web_socket)
            try:
                yield context
            finally:
                await context.close()


@dataclass(slots=True)
class _TargetHandle:
    observation_ref: str
    page_revision: int
    role: str
    element_key: str | None
    accessible_name: str
    semantic_fingerprint: str
    element: ElementHandle


class BrowserToolSession:
    """Deterministic policy shell around a single isolated Playwright Page."""

    def __init__(
        self,
        *,
        bundle: BrowserExecutionBundle,
        actor_slot: str,
        page: Page,
        allowed_origins: tuple[str, ...],
        catalog: BrowserToolCatalog,
        routes: BrowserRouteRegistry,
        reporter: BrowserExecutionReporter,
        artifact_writer: BrowserArtifactWriter | None,
        action_timeout: timedelta,
    ) -> None:
        contract = bundle.execution_contract
        if (
            contract.tools.tool_catalog_ref != catalog.catalog_ref
            or contract.tools.policy_bundle_ref != catalog.policy_bundle_ref
            or contract.tools.mcp_server_manifest_digest
            != catalog.mcp_server_manifest_digest
            or contract.tools.tool_schema_digest != catalog.tool_schema_digest
            or contract.tools.policy_digest != catalog.policy_digest
        ):
            raise BrowserExecutionError("tool catalog does not match ExecutionContract")
        if action_timeout <= timedelta(0) or action_timeout > timedelta(minutes=5):
            raise ValueError("browser action timeout must be between 0 and 5 minutes")
        self._bundle = bundle
        self._actor_slot = actor_slot
        self._page = page
        self._allowed_origins = frozenset(_normalized_origin(item) for item in allowed_origins)
        self._catalog = catalog
        self._routes = routes
        self._reporter = reporter
        self._artifact_writer = artifact_writer
        self._action_timeout_ms = int(action_timeout.total_seconds() * 1_000)
        self._page_ref = f"page_{token_urlsafe(24)}"
        self._page_revision = 1
        self._observations: dict[str, BrowserObservation] = {}
        self._targets: dict[str, _TargetHandle] = {}
        self._consumed_grants: set[UUID] = set()
        self._consumed_action_ids: set[UUID] = set()
        self._captured_artifacts: list[EvidenceArtifactInput] = []
        self._execution_uncertain = False
        self._external_revision_dirty = False
        self._page.on("framenavigated", lambda _frame: self._mark_external_revision_dirty())

    async def observe(self) -> BrowserObservation:
        self._synchronize_external_revision()
        candidates: list[BrowserTargetCandidate] = []
        targets: dict[str, _TargetHandle] = {}
        observation_ref = f"observation_{token_urlsafe(24)}"
        for role in _OBSERVED_ROLES:
            locator = self._page.get_by_role(cast(Literal["button"], role))
            count = min(await locator.count(), 40)
            for index in range(count):
                item = locator.nth(index)
                try:
                    element = await item.element_handle()
                    if element is None or not await element.is_visible():
                        continue
                    name = await self._accessible_name(element)
                    element_key = await element.get_attribute("data-testid")
                except PlaywrightError:
                    continue
                target_ref = f"target_{token_urlsafe(24)}"
                fingerprint = canonical_digest(
                    {
                        "role": role,
                        "accessibleName": name,
                        "elementKey": element_key,
                        "pageRevision": self._page_revision,
                    }
                )
                candidates.append(
                    BrowserTargetCandidate(
                        target_ref=target_ref,
                        element_key=element_key[:160] if element_key else None,
                        role=role,
                        accessible_name=name,
                        confidence=1.0 if element_key else 0.9,
                        semantic_fingerprint=fingerprint,
                    )
                )
                targets[target_ref] = _TargetHandle(
                    observation_ref=observation_ref,
                    page_revision=self._page_revision,
                    role=role,
                    element_key=element_key,
                    accessible_name=name,
                    semantic_fingerprint=fingerprint,
                    element=element,
                )
        title = await self._page.title()
        try:
            body = await self._page.locator("body").inner_text(timeout=1_000)
        except PlaywrightError:
            body = ""
        observation = BrowserObservation(
            observation_ref=observation_ref,
            page_ref=self._page_ref,
            page_revision=self._page_revision,
            route_key=self._routes.route_key_for(self._page.url),
            title=_safe_text(title, 240),
            target_candidates=tuple(candidates),
            untrusted_page_summary=_safe_text(body, 2_000),
            next_step_nonce=token_urlsafe(24),
            observed_at=utc_now(),
        )
        self._observations = {observation_ref: observation}
        self._targets = targets
        await self._reporter.emit(
            BrowserRuntimeReportKind.OBSERVATION_CAPTURED,
            {
                "safeSummary": "browser observation captured",
                "observationRef": observation.observation_ref,
                "observationDigest": canonical_digest(observation),
                "pageRef": observation.page_ref,
                "pageRevision": observation.page_revision,
                "routeKey": observation.route_key,
                "targetCount": len(observation.target_candidates),
            },
            actor_slot=self._actor_slot,
        )
        return observation

    async def execute(self, proposal: BrowserActionProposal) -> BrowserActionOutcome:
        self._synchronize_external_revision()
        if proposal.node_id not in {item.node_id for item in self._bundle.plan_template.nodes}:
            raise BrowserPolicyDeniedError("proposal node is outside the frozen plan")
        if proposal.actor_slot != self._actor_slot:
            raise BrowserPolicyDeniedError("proposal actor slot does not own this context")
        await self._reporter.emit(
            BrowserRuntimeReportKind.ACTION_PROPOSED,
            {
                "safeSummary": "structured browser action proposed",
                "action": proposal.action.value,
                "risk": proposal.risk.value,
                "nodeId": proposal.node_id,
                "targetRef": proposal.target_ref,
                "routeKey": proposal.route_key,
                "proposalDigest": browser_action_proposal_digest(proposal),
            },
            actor_slot=self._actor_slot,
            action_id=proposal.action_id,
        )
        decision = self._decide(proposal)
        await self._reporter.emit(
            BrowserRuntimeReportKind.POLICY_DECIDED,
            {
                "safeSummary": decision.safe_summary,
                "decision": decision.decision.value,
                "policyDigest": decision.policy_digest,
                "decisionDigest": canonical_digest(decision),
                "matchedRules": list(decision.matched_rules),
            },
            actor_slot=self._actor_slot,
            action_id=proposal.action_id,
        )
        if decision.decision is not BrowserPolicyDecisionKind.ALLOW:
            raise BrowserPolicyDeniedError(decision.safe_summary)
        if proposal.expected_observation_ref:
            self._require_observation(proposal)
        grant = BrowserActionGrant(
            grant_id=new_entity_id(),
            action_id=proposal.action_id,
            proposal_digest=browser_action_proposal_digest(proposal),
            execution_contract_id=self._bundle.execution_contract.id,
            execution_contract_digest=self._bundle.execution_contract.content_digest,
            actor_slot=self._actor_slot,
            page_ref=self._page_ref,
            page_revision=self._page_revision,
            allowed_action=proposal.action,
            expires_at=min(
                utc_now() + timedelta(seconds=15),
                self._bundle.execution_contract.execution_deadline,
            ),
            policy_digest=decision.policy_digest,
        )
        receipt, artifact = await self._execute_grant(grant, proposal)
        next_observation = None
        if receipt.status == "SUCCEEDED" and proposal.action not in {
            BrowserActionKind.CAPTURE_VIEW,
        }:
            next_observation = await self.observe()
        return BrowserActionOutcome(
            receipt=receipt,
            observation=next_observation,
            artifact=artifact,
        )

    async def _capture_screenshot(self, *, required: bool) -> EvidenceArtifactInput:
        if self._artifact_writer is None:
            raise BrowserExecutionError("trusted Evidence Artifact writer is unavailable")
        frames = self._page.frames
        if len(frames) > _MAX_SCREENSHOT_FRAMES:
            raise BrowserExecutionError("page exceeds the trusted screenshot frame limit")
        redaction_policy = self._artifact_writer.screenshot_redaction_policy
        masks = [
            frame.locator(selector) for frame in frames for selector in redaction_policy.selectors
        ]
        payload = await self._page.screenshot(
            animations="disabled",
            caret="hide",
            full_page=False,
            mask=masks,
            mask_color=redaction_policy.mask_color,
            type="png",
        )
        captured_at = utc_now()
        contract = self._bundle.execution_contract
        artifact = await self._artifact_writer.write(
            scope=EvidenceArtifactWriteScope(
                tenant_id=contract.tenant_id,
                project_id=contract.project_id,
                environment_id=contract.environment_id,
                debug_run_id=contract.debug_run_id,
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
                execution_created_at=contract.created_at,
                execution_deadline=contract.execution_deadline,
            ),
            kind=EvidenceArtifactKind.SCREENSHOT,
            payload=payload,
            mime_type="image/png",
            required=required,
            captured_at=captured_at,
        )
        if artifact.captured_at < captured_at:
            raise BrowserExecutionError("artifact writer returned an invalid capture time")
        self._captured_artifacts.append(artifact)
        return artifact

    def drain_captured_artifacts(self) -> tuple[EvidenceArtifactInput, ...]:
        """Return policy-mediated artifacts exactly once to the execution engine."""

        artifacts = tuple(self._captured_artifacts)
        self._captured_artifacts.clear()
        return artifacts

    def require_deterministic_action_outcomes(self) -> None:
        """Prevent an operation from turning a failed/unknown receipt into PASS."""

        if self._execution_uncertain:
            raise BrowserExecutionError(
                "a browser action failed or has an unknown outcome"
            )

    def value_for(self, value_ref: str) -> JsonValue:
        if value_ref.startswith("fixture."):
            key = value_ref.removeprefix("fixture.")
            if key in self._bundle.fixture_exports:
                return self._bundle.fixture_exports[key]
        value = self._bundle.test_ir.variables.get(value_ref)
        if value is not None and value.kind is ValueSourceKind.LITERAL:
            return value.value
        raise BrowserPolicyDeniedError("valueRef is not available in the execution view")

    def _decide(self, proposal: BrowserActionProposal) -> BrowserPolicyDecision:
        now = utc_now()
        allowed = proposal.action in self._catalog.allowed_actions
        matched_rules: tuple[str, ...]
        safe_summary: str
        if not allowed:
            matched_rules = ("tool.not_allowed",)
            safe_summary = "the frozen tool catalog does not allow this action"
        elif (
            proposal.expected_observation_ref is not None
            and not self._observation_is_current(proposal)
        ):
            allowed = False
            matched_rules = ("observation.stale",)
            safe_summary = "the proposal observation, revision, or nonce is stale"
        elif proposal.risk not in _ALLOWED_RISKS[proposal.action]:
            allowed = False
            matched_rules = ("risk.action_mismatch",)
            safe_summary = "the proposed risk does not match the executable action"
        elif proposal.action is BrowserActionKind.OPEN_ROUTE:
            assert proposal.route_key is not None
            try:
                self._routes.require(
                    proposal.route_key,
                    bundle=self._bundle,
                    allowed_origins=self._allowed_origins,
                )
            except BrowserPolicyDeniedError:
                allowed = False
                matched_rules = ("route.not_allowed",)
                safe_summary = "route or origin policy denied the action"
            else:
                matched_rules = ("route.exact_surface", "origin.session_scope")
                safe_summary = "route and origin policy allowed the action"
        elif proposal.action is BrowserActionKind.KEYPRESS and proposal.key not in _ALLOWED_KEYS:
            allowed = False
            matched_rules = ("keyboard.key_not_allowed",)
            safe_summary = "the requested key is not in the reviewed allowlist"
        else:
            matched_rules = ("tool.catalog", "contract.scope")
            safe_summary = "frozen tool and execution scope allowed the action"
        if allowed and proposal.action in _ALLOWED_TARGET_ROLES:
            candidate = self._target_candidate(proposal)
            if candidate is None or candidate.role not in _ALLOWED_TARGET_ROLES[proposal.action]:
                allowed = False
                matched_rules = ("target.semantic_role_mismatch",)
                safe_summary = "the observed target role is not allowed for this action"
        return BrowserPolicyDecision(
            action_id=proposal.action_id,
            decision=(
                BrowserPolicyDecisionKind.ALLOW if allowed else BrowserPolicyDecisionKind.DENY
            ),
            policy_digest=self._catalog.policy_digest,
            matched_rules=matched_rules,
            safe_summary=safe_summary,
            expires_at=min(
                now + timedelta(seconds=15),
                self._bundle.execution_contract.execution_deadline,
            ),
        )

    def _require_observation(self, proposal: BrowserActionProposal) -> BrowserObservation:
        assert proposal.expected_observation_ref is not None
        observation = self._observations.get(proposal.expected_observation_ref)
        if observation is None or (
            proposal.expected_page_revision != self._page_revision
            or observation.page_revision != self._page_revision
            or proposal.next_step_nonce != observation.next_step_nonce
        ):
            raise BrowserTargetStaleError("TARGET_STALE")
        return observation

    def _observation_is_current(self, proposal: BrowserActionProposal) -> bool:
        if proposal.expected_observation_ref is None:
            return False
        observation = self._observations.get(proposal.expected_observation_ref)
        return observation is not None and (
            proposal.expected_page_revision == self._page_revision
            and observation.page_revision == self._page_revision
            and proposal.next_step_nonce == observation.next_step_nonce
        )

    async def _execute_grant(
        self,
        grant: BrowserActionGrant,
        proposal: BrowserActionProposal,
    ) -> tuple[BrowserExecutionReceipt, EvidenceArtifactInput | None]:
        now = utc_now()
        if (
            grant.grant_id in self._consumed_grants
            or grant.action_id in self._consumed_action_ids
            or now >= grant.expires_at
            or grant.proposal_digest != browser_action_proposal_digest(proposal)
            or grant.action_id != proposal.action_id
            or grant.page_revision != self._page_revision
            or grant.allowed_action is not proposal.action
        ):
            raise BrowserPolicyDeniedError("action grant is stale or already consumed")
        self._consumed_grants.add(grant.grant_id)
        self._consumed_action_ids.add(grant.action_id)
        started_at = utc_now()
        status: Literal["SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"] = "SUCCEEDED"
        summary = "browser action completed"
        artifact: EvidenceArtifactInput | None = None
        try:
            if proposal.action is BrowserActionKind.OPEN_ROUTE:
                assert proposal.route_key is not None
                route = self._routes.require(
                    proposal.route_key,
                    bundle=self._bundle,
                    allowed_origins=self._allowed_origins,
                )
                await self._page.goto(
                    route.absolute_url,
                    wait_until="domcontentloaded",
                    timeout=self._action_timeout_ms,
                )
            elif proposal.action is BrowserActionKind.ACTIVATE:
                await (await self._require_target(proposal)).click(
                    force=False,
                    timeout=self._action_timeout_ms,
                )
            elif proposal.action is BrowserActionKind.ENTER_TEXT:
                assert proposal.value_ref is not None
                value = self.value_for(proposal.value_ref)
                if not isinstance(value, str | int | float | bool):
                    raise BrowserPolicyDeniedError("valueRef is not scalar text")
                await (await self._require_target(proposal)).fill(
                    str(value),
                    force=False,
                    timeout=self._action_timeout_ms,
                )
            elif proposal.action is BrowserActionKind.CHOOSE_OPTION:
                assert proposal.option_value is not None
                await (await self._require_target(proposal)).select_option(
                    label=proposal.option_value,
                    timeout=self._action_timeout_ms,
                )
            elif proposal.action is BrowserActionKind.KEYPRESS:
                assert proposal.key is not None
                await self._page.keyboard.press(proposal.key)
            elif proposal.action is BrowserActionKind.SCROLL:
                assert proposal.scroll_delta_y is not None
                await self._page.mouse.wheel(0, proposal.scroll_delta_y)
            elif proposal.action is BrowserActionKind.CAPTURE_VIEW:
                artifact = await self._capture_screenshot(required=False)
            else:
                raise BrowserPolicyDeniedError(
                    "the action requires a deterministic assertion or condition adapter"
                )
        except BrowserPolicyDeniedError:
            status = "FAILED"
            summary = "browser action was rejected before adapter execution"
            raise
        except PlaywrightTimeoutError:
            status = (
                "OUTCOME_UNKNOWN"
                if proposal.action
                in {
                    BrowserActionKind.OPEN_ROUTE,
                    BrowserActionKind.ACTIVATE,
                    BrowserActionKind.ENTER_TEXT,
                    BrowserActionKind.CHOOSE_OPTION,
                    BrowserActionKind.KEYPRESS,
                }
                else "FAILED"
            )
            summary = "browser action timed out"
        except OSError, PlaywrightError:
            status = "OUTCOME_UNKNOWN"
            summary = "browser adapter lost a definitive action outcome"
        self._page_revision += 1
        self._external_revision_dirty = False
        self._observations.clear()
        self._targets.clear()
        receipt = BrowserExecutionReceipt(
            receipt_id=new_entity_id(),
            grant_id=grant.grant_id,
            action_id=proposal.action_id,
            adapter=proposal.action,
            started_at=started_at,
            completed_at=utc_now(),
            status=status,
            safe_summary=summary,
            resulting_page_revision=self._page_revision,
        )
        await self._reporter.emit(
            BrowserRuntimeReportKind.ACTION_EXECUTED,
            {
                "safeSummary": receipt.safe_summary,
                "receiptId": str(receipt.receipt_id),
                "receiptDigest": canonical_digest(receipt),
                "grantId": str(receipt.grant_id),
                "action": receipt.adapter.value,
                "status": receipt.status,
                "resultingPageRevision": receipt.resulting_page_revision,
            },
            actor_slot=self._actor_slot,
            action_id=proposal.action_id,
        )
        if receipt.status != "SUCCEEDED":
            self._execution_uncertain = True
        return receipt, artifact

    async def _require_target(self, proposal: BrowserActionProposal) -> ElementHandle:
        assert proposal.target_ref is not None
        handle = self._targets.get(proposal.target_ref)
        if handle is None or (
            handle.observation_ref != proposal.expected_observation_ref
            or handle.page_revision != self._page_revision
        ):
            raise BrowserTargetStaleError("TARGET_STALE")
        try:
            if not await handle.element.is_visible():
                raise BrowserTargetStaleError("TARGET_STALE")
            current_key = await handle.element.get_attribute("data-testid")
            current_name = await self._accessible_name(handle.element)
        except PlaywrightError as error:
            raise BrowserTargetStaleError("TARGET_STALE") from error
        current_fingerprint = canonical_digest(
            {
                "role": handle.role,
                "accessibleName": current_name,
                "elementKey": current_key,
                "pageRevision": self._page_revision,
            }
        )
        if (
            current_key != handle.element_key
            or current_name != handle.accessible_name
            or current_fingerprint != handle.semantic_fingerprint
        ):
            raise BrowserTargetStaleError("TARGET_STALE")
        return handle.element

    def _target_candidate(
        self,
        proposal: BrowserActionProposal,
    ) -> BrowserTargetCandidate | None:
        if proposal.target_ref is None or proposal.expected_observation_ref is None:
            return None
        observation = self._observations.get(proposal.expected_observation_ref)
        if observation is None:
            return None
        return next(
            (
                item
                for item in observation.target_candidates
                if item.target_ref == proposal.target_ref
            ),
            None,
        )

    def _mark_external_revision_dirty(self) -> None:
        self._external_revision_dirty = True

    def _synchronize_external_revision(self) -> None:
        if not self._external_revision_dirty:
            return
        self._page_revision += 1
        self._observations.clear()
        self._targets.clear()
        self._external_revision_dirty = False

    @staticmethod
    async def _accessible_name(locator: Locator | ElementHandle) -> str:
        candidates = (
            await locator.get_attribute("aria-label"),
            await locator.get_attribute("placeholder"),
            await locator.get_attribute("title"),
        )
        for candidate in candidates:
            if candidate:
                return _safe_text(candidate, 240)
        try:
            inner_text = (
                await locator.inner_text(timeout=500)
                if isinstance(locator, Locator)
                else await locator.inner_text()
            )
            return _safe_text(inner_text, 240)
        except PlaywrightError:
            return ""


class PlaywrightBrowserExecutionEngine(BrowserExecutionEngine):
    """Restore one context and execute exact registered nodes in frozen order."""

    def __init__(
        self,
        *,
        runtime: PlaywrightExecutionRuntime,
        session_vault: SessionArtifactVault,
        envelope_codec: BrowserContextEnvelopeCodec,
        tool_catalog: BrowserToolCatalog,
        route_registry: BrowserRouteRegistry,
        operation_registry: BrowserOperationRegistry,
        artifact_writer: BrowserArtifactWriter | None = None,
        action_timeout: timedelta = timedelta(seconds=15),
    ) -> None:
        self._runtime = runtime
        self._session_vault = session_vault
        self._envelope_codec = envelope_codec
        self._tool_catalog = tool_catalog
        self._routes = route_registry
        self._operations = operation_registry
        self._artifact_writer = artifact_writer
        self._action_timeout = action_timeout

    async def execute(
        self,
        bundle: BrowserExecutionBundle,
        reporter: BrowserExecutionReporter,
    ) -> BrowserExecutionOutput:
        if len(bundle.execution_contract.actors) != 1:
            raise BrowserExecutionError("initial Browser Worker supports one active actor")
        actor = bundle.execution_contract.actors[0]
        envelope = bundle.restore_envelopes[0]
        descriptor = self._envelope_codec.open(
            envelope,
            contract=bundle.execution_contract,
        )
        scope = SessionArtifactScope(
            artifact_id=descriptor.artifact_id,
            tenant_id=descriptor.tenant_id,
            project_id=descriptor.project_id,
            environment_id=descriptor.environment_id,
            lease_id=descriptor.lease_id,
            lease_fence=descriptor.lease_fence,
            account_id=descriptor.account_id,
            connector_installation_id=descriptor.connector_installation_id,
            credential_binding_id=descriptor.credential_binding_id,
            allowed_origins=descriptor.allowed_origins,
            format_version=descriptor.format_version,
        )

        async def run_with_state(plaintext: memoryview) -> BrowserExecutionOutput:
            try:
                raw_state = loads(bytes(plaintext))
            except (JSONDecodeError, TypeError, ValueError) as error:
                raise SessionArtifactIntegrityError(
                    "browser session storage state is invalid"
                ) from error
            if not isinstance(raw_state, dict):
                raise SessionArtifactIntegrityError(
                    "browser session storage state must be an object"
                )
            storage_state = cast(dict[str, JsonValue], raw_state)
            async with self._runtime.restored_context(
                bundle=bundle,
                storage_state=storage_state,
                allowed_origins=descriptor.allowed_origins,
            ) as browser_context:
                page = await browser_context.new_page()
                tools = BrowserToolSession(
                    bundle=bundle,
                    actor_slot=actor.actor_slot,
                    page=page,
                    allowed_origins=descriptor.allowed_origins,
                    catalog=self._tool_catalog,
                    routes=self._routes,
                    reporter=reporter,
                    artifact_writer=self._artifact_writer,
                    action_timeout=self._action_timeout,
                )
                return await self._execute_plan(bundle, tools, reporter)

        return await self._session_vault.with_decrypted(
            object_ref=descriptor.object_ref,
            scope=scope,
            expected_digest=descriptor.object_digest,
            expected_key_version=descriptor.key_version,
            operation=run_with_state,
        )

    async def _execute_plan(
        self,
        bundle: BrowserExecutionBundle,
        tools: BrowserToolSession,
        reporter: BrowserExecutionReporter,
    ) -> BrowserExecutionOutput:
        nodes = {item.id: item for item in bundle.test_ir.workflow.nodes}
        assertions: list[AssertionResultInput] = []
        artifacts: list[EvidenceArtifactInput] = []
        for level in bundle.plan_template.execution_levels:
            for node_id in level:
                node = nodes[node_id]
                await reporter.emit(
                    BrowserRuntimeReportKind.NODE_STARTED,
                    {
                        "safeSummary": "frozen plan node started",
                        "nodeId": node.id,
                        "nodeKind": node.kind,
                        "versionRef": node.version_ref,
                    },
                )
                if node.kind.casefold() in {"fixture", "cleanup"}:
                    output = BrowserExecutionOutput()
                else:
                    operation = self._operations.require(node.version_ref)
                    output = await operation.execute(
                        BrowserPlanOperationContext(
                            bundle=bundle,
                            node=node,
                            tools=tools,
                            reporter=reporter,
                        )
                    )
                    tools.require_deterministic_action_outcomes()
                    if output.artifacts:
                        raise BrowserExecutionError(
                            "browser operations must capture evidence through the "
                            "trusted artifact writer"
                        )
                    output = BrowserExecutionOutput(
                        assertion_results=output.assertion_results,
                        artifacts=tools.drain_captured_artifacts(),
                    )
                assertions.extend(output.assertion_results)
                artifacts.extend(output.artifacts)
                await reporter.emit(
                    BrowserRuntimeReportKind.NODE_COMPLETED,
                    {
                        "safeSummary": "frozen plan node completed",
                        "nodeId": node.id,
                        "assertionResultCount": len(output.assertion_results),
                        "artifactCount": len(output.artifacts),
                    },
                )
        assertion_ids = [item.assertion_id for item in assertions]
        artifact_ids = [item.id for item in artifacts]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise BrowserExecutionError("browser operations returned duplicate assertions")
        if len(artifact_ids) != len(set(artifact_ids)):
            raise BrowserExecutionError("browser operations returned duplicate artifacts")
        return BrowserExecutionOutput(
            assertion_results=tuple(assertions),
            artifacts=tuple(artifacts),
        )


def _normalized_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("browser origin must use HTTP(S)")
    host = parsed.hostname.casefold()
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    suffix = "" if port == default_port else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def _normalized_web_socket_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"ws", "wss"} or parsed.hostname is None:
        raise ValueError("browser WebSocket origin must use WS(S)")
    mapped_scheme = "https" if parsed.scheme == "wss" else "http"
    host = parsed.hostname.casefold()
    default_port = 443 if parsed.scheme == "wss" else 80
    port = parsed.port or default_port
    suffix = "" if port == default_port else f":{port}"
    return f"{mapped_scheme}://{host}{suffix}"


def _actual_browser_revision(browser: Browser) -> str:
    return f"playwright@{distribution_version('playwright')}/chromium@{browser.version}"


def _validate_storage_state_origins(
    storage_state: dict[str, JsonValue],
    allowed_origins: frozenset[str],
) -> None:
    if set(storage_state).difference({"cookies", "origins"}):
        raise SessionArtifactIntegrityError("storage state contains unknown fields")
    origins = storage_state.get("origins", [])
    cookies = storage_state.get("cookies", [])
    if not isinstance(origins, list) or not isinstance(cookies, list):
        raise SessionArtifactIntegrityError("storage state collections are invalid")
    allowed_hosts = {urlsplit(item).hostname for item in allowed_origins}
    for item in origins:
        if not isinstance(item, dict):
            raise SessionArtifactIntegrityError("storage state origin is invalid")
        origin = item.get("origin")
        if not isinstance(origin, str):
            raise SessionArtifactIntegrityError("storage state origin is invalid")
        if _normalized_origin(origin) not in allowed_origins:
            raise SessionArtifactIntegrityError("storage state origin is outside scope")
    for item in cookies:
        if not isinstance(item, dict):
            raise SessionArtifactIntegrityError("storage state cookie is invalid")
        cookie_domain = item.get("domain")
        if not isinstance(cookie_domain, str):
            raise SessionArtifactIntegrityError("storage state cookie is invalid")
        domain = cookie_domain.lstrip(".").casefold()
        if domain not in allowed_hosts and not any(
            host is not None and host.endswith(f".{domain}") for host in allowed_hosts
        ):
            raise SessionArtifactIntegrityError("storage state cookie is outside scope")


def _safe_text(value: str, maximum: int) -> str:
    normalized = " ".join(value.split())
    normalized = sub(r"(?i)bearer\s+[A-Za-z0-9._~-]+", "Bearer <redacted>", normalized)
    normalized = sub(
        r"(?i)(password|secret|token)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        normalized,
    )
    return normalized[:maximum]
