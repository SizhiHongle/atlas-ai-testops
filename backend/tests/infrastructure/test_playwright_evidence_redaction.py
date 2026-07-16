"""Real Chromium coverage for trusted screenshot redaction."""

from collections.abc import Callable
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
from urllib.parse import urlsplit
from uuid import uuid7

import pytest
from PIL import Image
from playwright.async_api import async_playwright
from tests.infrastructure.test_playwright_browser_runtime import RecordingReporter, _bundle

from atlas_testops.domain.case import TestIntent as CaseIntent
from atlas_testops.domain.runtime import (
    BrowserActionKind,
    BrowserActionProposal,
    BrowserActionRisk,
    EvidenceIntegrity,
)
from atlas_testops.domain.workflow import WorkflowGraph
from atlas_testops.infrastructure.adapters import playwright_browser
from atlas_testops.infrastructure.adapters.playwright_browser import (
    BrowserRouteRegistry,
    BrowserToolCatalog,
    BrowserToolSession,
)
from atlas_testops.infrastructure.evidence_store import (
    InMemoryEvidenceObjectStore,
    PngEvidenceArtifactWriter,
)

BUCKET = "atlas-browser-evidence"
PAGE_HTML = """
<!doctype html>
<html>
  <head>
    <style>
      html, body { margin: 0; width: 420px; height: 220px; background: #ffffff; }
      .sensitive {
        position: absolute;
        box-sizing: border-box;
        width: 80px;
        height: 40px;
        border: 0;
        padding: 0;
        background: #ef4444;
      }
      #sensitive-input { left: 20px; top: 20px; }
      #sensitive-textarea { left: 120px; top: 20px; resize: none; }
      #sensitive-editable { left: 220px; top: 20px; }
      #sensitive-marked { left: 320px; top: 20px; }
      #sensitive-frame {
        position: absolute;
        left: 20px;
        top: 100px;
        width: 80px;
        height: 40px;
        border: 0;
      }
      #normal-region {
        position: absolute;
        left: 140px;
        top: 100px;
        width: 140px;
        height: 80px;
        background: rgb(0, 200, 80);
      }
    </style>
  </head>
  <body>
    <input id="sensitive-input" class="sensitive" aria-label="Sensitive input">
    <textarea id="sensitive-textarea" class="sensitive" aria-label="Sensitive textarea"></textarea>
    <div id="sensitive-editable" class="sensitive" contenteditable="true"></div>
    <div id="sensitive-marked" class="sensitive" data-atlas-sensitive></div>
    <iframe
      id="sensitive-frame"
      srcdoc="<style>
        html,body{margin:0}
        input{box-sizing:border-box;width:80px;height:40px;border:0;background:#ef4444}
      </style><input aria-label='Sensitive framed input'>"
    ></iframe>
    <div id="normal-region"></div>
  </body>
</html>
"""


@pytest.mark.anyio
async def test_real_chromium_redacts_sensitive_regions_before_verified_write(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = BrowserToolCatalog.reviewed(
        catalog_ref="tools.browser-evidence@1.0.0",
        policy_bundle_ref="policy.browser-evidence@1.0.0",
        allowed_actions=frozenset({BrowserActionKind.CAPTURE_VIEW}),
    )
    bundle, _codec = _bundle(
        valid_graph,
        intent_factory,
        revision="playwright@0.0.0/chromium@0.0.0",
        origin="https://example.test",
        catalog=catalog,
    )
    trusted_now = bundle.execution_contract.created_at + timedelta(seconds=1)
    monkeypatch.setattr(playwright_browser, "utc_now", lambda: trusted_now)
    store = InMemoryEvidenceObjectStore()
    writer = PngEvidenceArtifactWriter(store, bucket=BUCKET)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": 420, "height": 220},
                device_scale_factor=1,
            )
            try:
                page = await context.new_page()
                await page.set_content(PAGE_HTML, wait_until="load")
                tools = BrowserToolSession(
                    bundle=bundle,
                    actor_slot="operator",
                    page=page,
                    allowed_origins=("https://example.test",),
                    catalog=catalog,
                    routes=BrowserRouteRegistry(),
                    reporter=RecordingReporter(bundle),
                    artifact_writer=writer,
                    action_timeout=timedelta(seconds=5),
                )
                observation = await tools.observe()

                outcome = await tools.execute(
                    BrowserActionProposal(
                        action_id=uuid7(),
                        node_id="filter-agent",
                        actor_slot="operator",
                        action=BrowserActionKind.CAPTURE_VIEW,
                        risk=BrowserActionRisk.READ,
                        expected_observation_ref=observation.observation_ref,
                        expected_page_revision=observation.page_revision,
                        next_step_nonce=observation.next_step_nonce,
                        safe_summary="capture a redacted browser evidence view",
                    )
                )
            finally:
                await context.close()
        finally:
            await browser.close()

    artifact = outcome.artifact
    assert outcome.receipt.status == "SUCCEEDED"
    assert outcome.receipt.adapter is BrowserActionKind.CAPTURE_VIEW
    assert artifact is not None
    assert artifact.integrity is EvidenceIntegrity.VERIFIED
    assert artifact.redaction_policy_digest == writer.screenshot_redaction_policy.content_digest
    assert tools.drain_captured_artifacts() == (artifact,)

    retained = await store.payload_for_test(urlsplit(artifact.object_ref).path.lstrip("/"))
    assert retained is not None
    assert artifact.size_bytes == len(retained)
    assert artifact.content_digest == f"sha256:{sha256(retained).hexdigest()}"

    with Image.open(BytesIO(retained)) as screenshot:
        screenshot.load()
        assert screenshot.mode == "RGB"
        for point in ((60, 40), (160, 40), (260, 40), (360, 40), (60, 120)):
            assert screenshot.getpixel(point) == (0, 0, 0)
        assert screenshot.getpixel((210, 140)) == (0, 200, 80)
