"""Dedicated database-free Temporal Worker for restricted Playwright execution."""

import asyncio
import logging
from datetime import timedelta
from typing import cast
from uuid import UUID

from temporalio.client import Client
from temporalio.worker import Worker

from atlas_testops.application.ports.browser_runtime import BrowserExecutionEngine
from atlas_testops.application.ports.sessions import SessionArtifactVault
from atlas_testops.core.config import BrowserWorkerSettings
from atlas_testops.domain.runtime import BrowserActionKind
from atlas_testops.infrastructure.adapters.playwright_browser import (
    BrowserArtifactWriter,
    BrowserOperationRegistry,
    BrowserRouteRegistry,
    BrowserToolCatalog,
    PlaywrightBrowserExecutionEngine,
    PlaywrightExecutionRuntime,
)
from atlas_testops.infrastructure.browser_auth import BrowserRuntimeRequestSigner
from atlas_testops.infrastructure.browser_envelope import AesGcmBrowserContextEnvelopeCodec
from atlas_testops.infrastructure.browser_gateway import HttpBrowserRuntimeGateway
from atlas_testops.infrastructure.session_runtime import build_optional_session_artifact_vault
from atlas_testops.orchestration.browser import (
    BrowserExecutionActivities,
    BrowserExecutionWorkflow,
    BrowserRuntimeGatewayFactory,
    CloseableBrowserRuntimeGateway,
)

LOGGER = logging.getLogger(__name__)


class HttpBrowserRuntimeGatewayFactory(BrowserRuntimeGatewayFactory):
    """Build one signed, run-scoped HTTP gateway per Activity."""

    def __init__(
        self,
        *,
        api_base_url: str,
        request_signer: BrowserRuntimeRequestSigner,
        timeout: timedelta,
        allow_insecure_http: bool = False,
    ) -> None:
        self._api_base_url = api_base_url
        self._request_signer = request_signer
        self._timeout = timeout
        self._allow_insecure_http = allow_insecure_http

    def create(
        self,
        *,
        tenant_id: UUID,
        worker_identity: str,
        execution_permit: str,
    ) -> CloseableBrowserRuntimeGateway:
        return HttpBrowserRuntimeGateway(
            api_base_url=self._api_base_url,
            tenant_id=tenant_id,
            worker_identity=worker_identity,
            execution_permit=execution_permit,
            request_signer=self._request_signer,
            timeout=self._timeout,
            allow_insecure_http=self._allow_insecure_http,
        )


async def run_worker(
    settings: BrowserWorkerSettings,
    *,
    session_vault: SessionArtifactVault | None = None,
    engine: BrowserExecutionEngine | None = None,
    route_registry: BrowserRouteRegistry | None = None,
    operation_registry: BrowserOperationRegistry | None = None,
    artifact_writer: BrowserArtifactWriter | None = None,
) -> None:
    """Run Chromium on its own queue without constructing or importing Database."""

    if not settings.browser_runtime_configured:
        raise ValueError("browser worker runtime is not configured")
    api_base_url = cast(str, settings.browser_runtime_api_base_url)
    request_key = settings.browser_runtime_request_hmac_key_base64
    envelope_key = settings.browser_context_envelope_key_base64
    envelope_key_version = settings.browser_context_envelope_key_version
    revision = settings.browser_revision
    catalog_ref = settings.browser_tool_catalog_ref
    policy_bundle_ref = settings.browser_policy_bundle_ref
    mcp_manifest_digest = settings.browser_mcp_server_manifest_digest
    tool_schema_digest = settings.browser_tool_schema_digest
    policy_digest = settings.browser_policy_digest
    if any(
        value is None
        for value in (
            request_key,
            envelope_key,
            envelope_key_version,
            revision,
            catalog_ref,
            policy_bundle_ref,
            mcp_manifest_digest,
            tool_schema_digest,
            policy_digest,
        )
    ):
        raise RuntimeError("validated browser worker configuration is incomplete")
    assert request_key is not None
    assert envelope_key is not None
    assert envelope_key_version is not None
    assert revision is not None
    assert catalog_ref is not None
    assert policy_bundle_ref is not None
    assert mcp_manifest_digest is not None
    assert tool_schema_digest is not None
    assert policy_digest is not None
    request_signer = BrowserRuntimeRequestSigner.from_base64_key(
        request_key.get_secret_value(),
    )
    envelope_codec = AesGcmBrowserContextEnvelopeCodec.from_base64_key(
        envelope_key.get_secret_value(),
        key_version=envelope_key_version,
    )
    catalog = BrowserToolCatalog(
        catalog_ref=catalog_ref,
        policy_bundle_ref=policy_bundle_ref,
        mcp_server_manifest_digest=mcp_manifest_digest,
        tool_schema_digest=tool_schema_digest,
        policy_digest=policy_digest,
        allowed_actions=frozenset(
            BrowserActionKind(item) for item in settings.browser_allowed_actions
        ),
    )
    vault = session_vault or await build_optional_session_artifact_vault(settings)
    if vault is None:
        raise ValueError("browser worker SessionArtifact Vault is not configured")
    runtime: PlaywrightExecutionRuntime | None = None
    selected_engine = engine
    if selected_engine is None:
        runtime = PlaywrightExecutionRuntime(
            revision=revision,
            headless=settings.browser_headless,
            maximum_concurrency=settings.browser_worker_max_concurrency,
        )
        selected_engine = PlaywrightBrowserExecutionEngine(
            runtime=runtime,
            session_vault=vault,
            envelope_codec=envelope_codec,
            tool_catalog=catalog,
            route_registry=route_registry or BrowserRouteRegistry(),
            operation_registry=operation_registry or BrowserOperationRegistry(),
            artifact_writer=artifact_writer,
            action_timeout=timedelta(seconds=settings.browser_action_timeout_seconds),
        )
    gateway_factory = HttpBrowserRuntimeGatewayFactory(
        api_base_url=api_base_url,
        request_signer=request_signer,
        timeout=timedelta(seconds=settings.browser_runtime_http_timeout_seconds),
        allow_insecure_http=settings.browser_runtime_allow_insecure_http,
    )
    activities = BrowserExecutionActivities(
        gateway_factory=gateway_factory,
        engine=selected_engine,
    )
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    worker = Worker(
        client,
        task_queue=settings.browser_runtime_task_queue,
        workflows=[BrowserExecutionWorkflow],
        activities=[activities.execute],
        max_concurrent_activities=settings.browser_worker_max_concurrency,
    )
    try:
        LOGGER.info(
            "Browser Worker started",
            extra={
                "task_queue": settings.browser_runtime_task_queue,
                "max_concurrent_activities": settings.browser_worker_max_concurrency,
                "browser_revision": revision,
                "artifact_writer_configured": artifact_writer is not None,
            },
        )
        await worker.run()
    finally:
        if runtime is not None:
            await runtime.close()


def main() -> None:
    """Start the isolated Browser Worker with no control-plane database configuration."""

    settings = BrowserWorkerSettings()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
