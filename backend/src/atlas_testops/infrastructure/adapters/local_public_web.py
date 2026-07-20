"""Reviewed local-only Browser operations for the public Baidu search demo."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

import httpx2

from atlas_testops.application.ports.browser_runtime import BrowserExecutionOutput
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.domain.runtime import (
    AssertionResultInput,
    AssertionStatus,
    BrowserActionKind,
    BrowserActionProposal,
    BrowserActionRisk,
    BrowserObservation,
    BrowserRuntimeReportKind,
    BrowserTargetCandidate,
    expected_assertion_digest,
)
from atlas_testops.infrastructure.adapters.playwright_browser import (
    BrowserOperationRegistry,
    BrowserPlanOperationContext,
    BrowserRouteDefinition,
    BrowserRouteRegistry,
)

LOCAL_PUBLIC_WEB_TOOL_CATALOG_REF = "tools.local-public-web@1.0.0"
LOCAL_PUBLIC_WEB_POLICY_BUNDLE_REF = "policy.local-public-web@1.0.0"
LOCAL_PUBLIC_WEB_MODEL_PROFILE_REF = "model.browser-semantic@1.0.0"
LOCAL_PUBLIC_WEB_PROMPT_BUNDLE_REF = "prompt.browser-semantic@1.0.0"
LOCAL_PUBLIC_WEB_REASONING_POLICY_REF = "reasoning.policy-gated@1.0.0"
BAIDU_SURFACE_KEY = "baidu.search-home"
BAIDU_SURFACE_VERSION_REF = "surface.baidu-search-home@1.0.0"
BAIDU_ROUTE_KEY = "baidu.search-home"
BAIDU_ORIGIN = "https://www.baidu.com"
BAIDU_HOME_URL = f"{BAIDU_ORIGIN}/"
BAIDU_SURFACE_DIGEST = "sha256:" + hashlib.sha256(
    b"baidu.search-home|surface.baidu-search-home@1.0.0|semantic-actions-only"
).hexdigest()

LOCAL_PUBLIC_WEB_ALLOWED_ACTIONS = frozenset(
    {
        BrowserActionKind.OPEN_ROUTE,
        BrowserActionKind.ACTIVATE,
        BrowserActionKind.ENTER_TEXT,
        BrowserActionKind.KEYPRESS,
        BrowserActionKind.CAPTURE_VIEW,
    }
)


def build_local_public_web_registries(
    planner: BrowserTargetPlanner | None = None,
) -> tuple[
    BrowserRouteRegistry,
    BrowserOperationRegistry,
]:
    """Build exact registries that cannot be extended by persisted case data."""

    routes = BrowserRouteRegistry(
        (
            BrowserRouteDefinition(
                route_key=BAIDU_ROUTE_KEY,
                surface_version_ref=BAIDU_SURFACE_VERSION_REF,
                surface_digest=BAIDU_SURFACE_DIGEST,
                absolute_url=BAIDU_HOME_URL,
            ),
        )
    )
    operations = BrowserOperationRegistry()
    operations.register("browser.surface-open@1.0.0", OpenBaiduSurfaceOperation())
    operations.register(
        "browser.semantic-search@1.0.0",
        BaiduSearchOperation(planner),
    )
    operations.register(
        "assert.search-results-visible@1.0.0",
        BaiduSearchResultsAssertionOperation(),
    )
    return routes, operations


@dataclass(frozen=True, slots=True)
class BrowserPlannerDecision:
    """Validated target choice plus truthful runtime accounting."""

    target: BrowserTargetCandidate
    planning_mode: str
    provider: str
    model: str
    external_call: bool
    status: str
    latency_ms: int
    input_units: int
    output_units: int


class BrowserTargetPlanner(Protocol):
    """Select a semantic target; policy remains the action authority."""

    async def select_textbox(
        self,
        observation: BrowserObservation,
    ) -> BrowserPlannerDecision: ...


class DeterministicBrowserTargetPlanner:
    """Reviewed fallback that never claims an external AI call."""

    async def select_textbox(
        self,
        observation: BrowserObservation,
    ) -> BrowserPlannerDecision:
        target = _first_textbox(observation)
        return BrowserPlannerDecision(
            target=target,
            planning_mode="DETERMINISTIC",
            provider="NONE",
            model="NONE",
            external_call=False,
            status="RESOLVED",
            latency_ms=0,
            input_units=0,
            output_units=0,
        )


class OpenAIResponsesBrowserTargetPlanner:
    """Optional structured-output selector with a deterministic fail-safe."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        api_base_url: str = "https://api.openai.com",
        timeout_seconds: float = 20,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._fallback = DeterministicBrowserTargetPlanner()

    async def select_textbox(
        self,
        observation: BrowserObservation,
    ) -> BrowserPlannerDecision:
        started = monotonic()
        candidates = [
            {
                "candidateIndex": index,
                "role": candidate.role,
                "accessibleName": candidate.accessible_name,
                "confidence": candidate.confidence,
            }
            for index, candidate in enumerate(observation.target_candidates)
        ]
        input_units = 0
        output_units = 0
        try:
            async with httpx2.AsyncClient(
                base_url=self._api_base_url,
                timeout=self._timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as client:
                response = await client.post(
                    "/v1/responses",
                    json={
                        "model": self._model,
                        "store": False,
                        "instructions": (
                            "Select the public search textbox from the supplied "
                            "accessibility candidates. Return only the structured "
                            "selection; do not propose or execute an action."
                        ),
                        "input": json.dumps(
                            {
                                "pageTitle": observation.title,
                                "goal": "Select the public search textbox.",
                                "candidates": candidates,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "text": {
                            "format": {
                                "type": "json_schema",
                                "name": "browser_target_selection",
                                "strict": True,
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "candidateIndex": {
                                            "type": "integer",
                                            "minimum": 0,
                                        }
                                    },
                                    "required": ["candidateIndex"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                    },
                )
            response.raise_for_status()
            body = response.json()
            usage = body.get("usage") if isinstance(body, dict) else None
            if isinstance(usage, dict):
                input_units = _non_negative_int(usage.get("input_tokens"))
                output_units = _non_negative_int(usage.get("output_tokens"))
            text = _responses_output_text(body)
            selection = json.loads(text)
            index = selection.get("candidateIndex")
            if not isinstance(index, int) or isinstance(index, bool):
                raise ValueError("model target index is invalid")
            target = observation.target_candidates[index]
            if target.role != "textbox":
                raise ValueError("model selected a non-textbox target")
            status = "RESOLVED"
        except Exception:
            fallback = await self._fallback.select_textbox(observation)
            target = fallback.target
            status = "FALLBACK"
        return BrowserPlannerDecision(
            target=target,
            planning_mode="OPENAI_RESPONSES",
            provider="OPENAI",
            model=self._model,
            external_call=True,
            status=status,
            latency_ms=max(0, int((monotonic() - started) * 1_000)),
            input_units=input_units,
            output_units=output_units,
        )


def build_browser_target_planner(
    *,
    mode: str,
    api_key: str | None,
    model: str,
    api_base_url: str,
    timeout_seconds: float,
) -> BrowserTargetPlanner:
    """Build only the explicitly configured planner implementation."""

    if mode == "openai":
        if not api_key:
            raise ValueError("OpenAI browser planner requires an API key")
        return OpenAIResponsesBrowserTargetPlanner(
            api_key=api_key,
            model=model,
            api_base_url=api_base_url,
            timeout_seconds=timeout_seconds,
        )
    return DeterministicBrowserTargetPlanner()


class OpenBaiduSurfaceOperation:
    """Open only the deployment-reviewed Baidu home route."""

    async def execute(
        self,
        context: BrowserPlanOperationContext,
    ) -> BrowserExecutionOutput:
        actor_slot = context.bundle.execution_contract.actors[0].actor_slot
        await context.tools.execute(
            BrowserActionProposal(
                action_id=new_entity_id(),
                node_id=context.node.id,
                actor_slot=actor_slot,
                action=BrowserActionKind.OPEN_ROUTE,
                risk=BrowserActionRisk.NAVIGATE,
                route_key=BAIDU_ROUTE_KEY,
                safe_summary="open the reviewed Baidu search home route",
            )
        )
        return BrowserExecutionOutput()


class BaiduSearchOperation:
    """Fill the observed search textbox and submit with the reviewed Enter key."""

    def __init__(self, planner: BrowserTargetPlanner | None = None) -> None:
        self._planner = planner or DeterministicBrowserTargetPlanner()

    async def execute(
        self,
        context: BrowserPlanOperationContext,
    ) -> BrowserExecutionOutput:
        actor_slot = context.bundle.execution_contract.actors[0].actor_slot
        observation = await context.tools.observe()
        planning = await self._planner.select_textbox(observation)
        textbox = planning.target
        contract = context.bundle.execution_contract
        await context.reporter.emit(
            BrowserRuntimeReportKind.PLANNER_COMPLETED,
            {
                "safeSummary": (
                    "external model selected a reviewed semantic target"
                    if planning.external_call and planning.status == "RESOLVED"
                    else (
                        "external model failed safely; deterministic target selection used"
                        if planning.external_call
                        else "deterministic semantic target selection completed"
                    )
                ),
                "planningMode": planning.planning_mode,
                "provider": planning.provider,
                "model": planning.model,
                "externalCall": planning.external_call,
                "status": planning.status,
                "latencyMs": planning.latency_ms,
                "inputUnits": planning.input_units,
                "outputUnits": planning.output_units,
                "modelProfileRef": contract.model.model_profile_ref,
                "promptBundleRef": contract.model.prompt_bundle_ref,
                "reasoningPolicyRef": contract.model.reasoning_policy_ref,
                "selectedTargetRole": textbox.role or "unknown",
            },
            actor_slot=actor_slot,
        )
        entered = await context.tools.execute(
            BrowserActionProposal(
                action_id=new_entity_id(),
                node_id=context.node.id,
                actor_slot=actor_slot,
                action=BrowserActionKind.ENTER_TEXT,
                risk=BrowserActionRisk.INPUT,
                expected_observation_ref=observation.observation_ref,
                expected_page_revision=observation.page_revision,
                next_step_nonce=observation.next_step_nonce,
                target_ref=textbox.target_ref,
                value_ref="searchKeyword",
                safe_summary="enter the frozen public search keyword",
            )
        )
        after_input = entered.observation or await context.tools.observe()
        await context.tools.execute(
            BrowserActionProposal(
                action_id=new_entity_id(),
                node_id=context.node.id,
                actor_slot=actor_slot,
                action=BrowserActionKind.KEYPRESS,
                risk=BrowserActionRisk.INPUT,
                expected_observation_ref=after_input.observation_ref,
                expected_page_revision=after_input.page_revision,
                next_step_nonce=after_input.next_step_nonce,
                key="Enter",
                safe_summary="submit the reviewed Baidu search form",
            )
        )
        return BrowserExecutionOutput()


class BaiduSearchResultsAssertionOperation:
    """Evaluate the frozen keyword against objective title/body observations."""

    async def execute(
        self,
        context: BrowserPlanOperationContext,
    ) -> BrowserExecutionOutput:
        started = monotonic()
        expected = context.tools.value_for("fixture.expectedText")
        if not isinstance(expected, str) or not expected.strip():
            raise RuntimeError("reviewed fixture expectation is unavailable")
        normalized_expected = expected.casefold()
        observation = await context.tools.observe()
        matched = await _search_keyword_is_observable(
            context,
            observation,
            normalized_expected,
        )
        for _ in range(20):
            if matched:
                break
            await asyncio.sleep(0.25)
            observation = await context.tools.observe()
            matched = await _search_keyword_is_observable(
                context,
                observation,
                normalized_expected,
            )

        actor_slot = context.bundle.execution_contract.actors[0].actor_slot
        capture = await context.tools.execute(
            BrowserActionProposal(
                action_id=new_entity_id(),
                node_id=context.node.id,
                actor_slot=actor_slot,
                action=BrowserActionKind.CAPTURE_VIEW,
                risk=BrowserActionRisk.READ,
                expected_observation_ref=observation.observation_ref,
                expected_page_revision=observation.page_revision,
                next_step_nonce=observation.next_step_nonce,
                safe_summary="capture the reviewed search result evidence view",
            )
        )
        if capture.artifact is None:
            raise RuntimeError("reviewed search evidence screenshot is unavailable")
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
                        AssertionStatus.PASSED if matched else AssertionStatus.FAILED
                    ),
                    expected_digest=expected_assertion_digest(
                        context.bundle.test_ir,
                        specification.assertion_id,
                    ),
                    actual_safe_summary=(
                        "The search observation preserved or displayed the frozen keyword."
                        if matched
                        else (
                            "The search observation did not preserve or display "
                            "the frozen keyword."
                        )
                    ),
                    evaluator_version_ref=specification.evaluator_version_ref,
                    evidence_refs=(capture.artifact.id,),
                    observed_at=utc_now(),
                    duration_ms=max(0, int((monotonic() - started) * 1_000)),
                ),
            )
        )


def _observation_contains(observation: BrowserObservation, expected: str) -> bool:
    return (
        expected
        in f"{observation.title}\n{observation.untrusted_page_summary}".casefold()
    )


async def _search_keyword_is_observable(
    context: BrowserPlanOperationContext,
    observation: BrowserObservation,
    normalized_expected: str,
) -> bool:
    if _observation_contains(observation, normalized_expected):
        return True
    textbox = next(
        (
            candidate
            for candidate in observation.target_candidates
            if candidate.role == "textbox"
        ),
        None,
    )
    return textbox is not None and await context.tools.target_value_matches(
        textbox.target_ref,
        "fixture.expectedText",
    )


def _first_textbox(observation: BrowserObservation) -> BrowserTargetCandidate:
    target = next(
        (
            candidate
            for candidate in observation.target_candidates
            if candidate.role == "textbox"
        ),
        None,
    )
    if target is None:
        raise RuntimeError("reviewed Baidu search textbox is unavailable")
    return target


def _responses_output_text(body: object) -> str:
    if not isinstance(body, dict):
        raise ValueError("OpenAI Responses payload is invalid")
    for output in body.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if content.get("type") == "output_text" and isinstance(text, str):
                return text
    raise ValueError("OpenAI Responses payload has no structured output text")


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
