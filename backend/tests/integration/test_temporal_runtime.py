"""真实 Temporal Server 与 Worker 集成测试。"""

from datetime import UTC, datetime, timedelta
from os import environ
from typing import cast
from uuid import UUID, uuid7

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from atlas_testops.application.access import ActorContext
from atlas_testops.application.sessions import AuthSessionService
from atlas_testops.domain.identity import EnsureLoginSession, LoginSessionReady
from atlas_testops.orchestration.platform import (
    PlatformProbeRequest,
    PlatformProbeWorkflow,
)
from atlas_testops.orchestration.sessions import (
    AuthSessionActivities,
    EnsureAuthSessionWorkflow,
    TemporalAuthSessionDispatcher,
)

TEMPORAL_ADDRESS = environ.get("ATLAS_TEST_TEMPORAL_ADDRESS")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        TEMPORAL_ADDRESS is None,
        reason="ATLAS_TEST_TEMPORAL_ADDRESS is not configured",
    ),
]


@pytest.mark.anyio
async def test_real_worker_executes_platform_probe() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    task_queue = f"atlas-probe-{uuid7()}"
    request = PlatformProbeRequest(request_id="temporal-integration")

    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[PlatformProbeWorkflow],
    ):
        result = await client.execute_workflow(
            PlatformProbeWorkflow.run,
            request,
            id=f"atlas-platform-probe-{uuid7()}",
            task_queue=task_queue,
        )

    assert result.schema_version == "atlas.platform-probe/0.1"
    assert result.request_id == request.request_id


class FakeAuthSessionService:
    """Return a deterministic safe session without loading a browser or Vault."""

    async def ensure(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
    ) -> LoginSessionReady:
        assert actor.development_override
        assert str(lease_id)
        assert command.allowed_origins == ("https://staging.example.test",)
        return LoginSessionReady(
            browser_context_ref="bctx_" + "t" * 40,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


@pytest.mark.anyio
async def test_real_worker_executes_auth_session_dispatch_contract() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    task_queue = f"atlas-auth-session-{uuid7()}"
    activities = AuthSessionActivities(
        cast(AuthSessionService, FakeAuthSessionService())
    )
    dispatcher = TemporalAuthSessionDispatcher(
        client,
        task_queue=task_queue,
        workflow_timeout=timedelta(seconds=30),
    )
    actor = ActorContext(
        tenant_id=uuid7(),
        actor_id=uuid7(),
        request_id=f"temporal-auth-{uuid7()}",
        development_override=True,
    )
    lease_id = uuid7()

    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[EnsureAuthSessionWorkflow],
        activities=[activities.ensure],
    ):
        result = await dispatcher.ensure(
            actor,
            lease_id,
            EnsureLoginSession(
                fencing_token=3,
                worker_identity="worker-temporal-auth-01",
                allowed_origins=("https://staging.example.test",),
            ),
        )

    assert isinstance(result, LoginSessionReady)
    assert result.browser_context_ref == "bctx_" + "t" * 40
