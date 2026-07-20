"""Truthful external-planner receipts and deterministic fallback behavior."""

import json

import httpx2
import pytest

from atlas_testops.core.contracts import utc_now
from atlas_testops.domain.runtime import BrowserObservation, BrowserTargetCandidate
from atlas_testops.infrastructure.adapters.local_public_web import (
    DeterministicBrowserTargetPlanner,
    OpenAIResponsesBrowserTargetPlanner,
)

DIGEST = "sha256:" + "a" * 64


def _observation() -> BrowserObservation:
    return BrowserObservation(
        observation_ref="observation_" + "o" * 24,
        page_ref="page_" + "p" * 24,
        page_revision=2,
        route_key="baidu.search-home",
        title="百度一下",
        target_candidates=(
            BrowserTargetCandidate(
                target_ref="target_" + "b" * 24,
                role="button",
                accessible_name="百度一下",
                confidence=0.99,
                semantic_fingerprint=DIGEST,
            ),
            BrowserTargetCandidate(
                target_ref="target_" + "t" * 24,
                role="textbox",
                accessible_name="搜索",
                confidence=0.98,
                semantic_fingerprint=DIGEST,
            ),
        ),
        untrusted_page_summary="Public search page",
        next_step_nonce="n" * 24,
        observed_at=utc_now(),
    )


@pytest.mark.anyio
async def test_openai_planner_accepts_only_structured_candidate_selection() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        return httpx2.Response(
            200,
            json={
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"candidateIndex":1}',
                            }
                        ]
                    }
                ],
                "usage": {"input_tokens": 41, "output_tokens": 7},
            },
        )

    planner = OpenAIResponsesBrowserTargetPlanner(
        api_key="test-api-key",
        model="gpt-test",
        api_base_url="https://api.openai.test",
        transport=httpx2.MockTransport(handler),
    )

    decision = await planner.select_textbox(_observation())

    assert decision.target.role == "textbox"
    assert decision.planning_mode == "OPENAI_RESPONSES"
    assert decision.provider == "OPENAI"
    assert decision.model == "gpt-test"
    assert decision.external_call is True
    assert decision.status == "RESOLVED"
    assert decision.input_units == 41
    assert decision.output_units == 7


@pytest.mark.anyio
async def test_openai_planner_failure_is_reported_and_falls_back_deterministically() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503, json={"error": {"message": "unavailable"}})

    planner = OpenAIResponsesBrowserTargetPlanner(
        api_key="test-api-key",
        model="gpt-test",
        api_base_url="https://api.openai.test",
        transport=httpx2.MockTransport(handler),
    )

    decision = await planner.select_textbox(_observation())

    assert decision.target.role == "textbox"
    assert decision.external_call is True
    assert decision.provider == "OPENAI"
    assert decision.status == "FALLBACK"
    assert decision.input_units == 0
    assert decision.output_units == 0


@pytest.mark.anyio
async def test_deterministic_planner_never_claims_an_external_call() -> None:
    decision = await DeterministicBrowserTargetPlanner().select_textbox(
        _observation()
    )

    assert decision.target.role == "textbox"
    assert decision.planning_mode == "DETERMINISTIC"
    assert decision.provider == "NONE"
    assert decision.model == "NONE"
    assert decision.external_call is False
    assert decision.status == "RESOLVED"
