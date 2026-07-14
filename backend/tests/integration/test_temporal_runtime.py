"""真实 Temporal Server 与 Worker 集成测试。"""

from datetime import UTC, datetime, timedelta
from os import environ
from typing import cast
from uuid import UUID, uuid7

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from atlas_testops.application.access import ActorContext
from atlas_testops.application.fixture_runs import FixtureWorkerService
from atlas_testops.application.sessions import AuthSessionService
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.domain.fixture import (
    DataNodeRunStatus,
    FixtureCleanupState,
    FixtureFailureCategory,
    FixtureNodeActivityResult,
    FixtureReleaseResult,
    FixtureRun,
    FixtureRunKind,
    FixtureRunStatus,
    FixtureWorkerPlan,
)
from atlas_testops.domain.identity import EnsureLoginSession, LoginSessionReady
from atlas_testops.orchestration.fixtures import (
    FixtureActivities,
    FixtureRunWorkflow,
    TemporalFixtureRunDispatcher,
)
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
    activities = AuthSessionActivities(cast(AuthSessionService, FakeAuthSessionService()))
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


class FakeFixtureWorkerService:
    """Record workflow sequencing without using PostgreSQL or a provider."""

    def __init__(self, *, fail_node: bool) -> None:
        self.fail_node = fail_node
        self.calls: list[str] = []

    async def load_plan(self, tenant_id: UUID, run_id: UUID) -> FixtureWorkerPlan:
        self.calls.append("load")
        return FixtureWorkerPlan(
            fixture_run_id=run_id,
            execution_levels=(("createCustomer",),),
            cleanup_order=("createCustomer",),
            execution_deadline=datetime.now(UTC) + timedelta(seconds=20),
        )

    async def execute_node(
        self,
        tenant_id: UUID,
        run_id: UUID,
        node_id: str,
    ) -> FixtureNodeActivityResult:
        self.calls.append(f"execute:{node_id}")
        return FixtureNodeActivityResult(
            node_id=node_id,
            status=(DataNodeRunStatus.FAILED if self.fail_node else DataNodeRunStatus.SUCCEEDED),
            failure_category=(FixtureFailureCategory.TRANSIENT if self.fail_node else None),
            failure_code="MOCK_NODE_FAILED" if self.fail_node else None,
        )

    async def finalize_ready(self, tenant_id: UUID, run_id: UUID) -> FixtureRun:
        self.calls.append("ready")
        return _fixture_run(tenant_id, run_id, status=FixtureRunStatus.READY)

    async def begin_release(self, tenant_id: UUID, run_id: UUID) -> FixtureRun:
        self.calls.append("begin-release")
        return _fixture_run(tenant_id, run_id, status=FixtureRunStatus.CLEANING)

    async def begin_failed_cleanup(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        category: FixtureFailureCategory,
        code: str,
    ) -> FixtureRun:
        assert category is FixtureFailureCategory.TRANSIENT
        self.calls.append(f"begin-failed:{code}")
        return _fixture_run(tenant_id, run_id, status=FixtureRunStatus.RUNNING)

    async def cleanup_node(
        self,
        tenant_id: UUID,
        run_id: UUID,
        node_id: str,
    ) -> FixtureReleaseResult:
        self.calls.append(f"cleanup:{node_id}")
        return FixtureReleaseResult(
            fixture_run_id=run_id,
            status=FixtureRunStatus.CLEANING,
            cleanup_state=FixtureCleanupState.RUNNING,
            cleaned_resources=1,
            leaked_resources=0,
        )

    async def finalize_release(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        failed_run: bool,
    ) -> FixtureReleaseResult:
        self.calls.append(f"finalize:{failed_run}")
        return FixtureReleaseResult(
            fixture_run_id=run_id,
            status=(FixtureRunStatus.FAILED if failed_run else FixtureRunStatus.RELEASED),
            cleanup_state=FixtureCleanupState.CLEANED,
            cleaned_resources=1,
            leaked_resources=0,
        )


def _fixture_run(
    tenant_id: UUID,
    run_id: UUID,
    *,
    status: FixtureRunStatus,
) -> FixtureRun:
    now = datetime.now(UTC)
    return FixtureRun(
        id=run_id,
        tenant_id=tenant_id,
        project_id=new_entity_id(),
        environment_id=new_entity_id(),
        blueprint_version_id=new_entity_id(),
        run_kind=FixtureRunKind.VALIDATION,
        execution_id=f"temporal-fixture-{run_id}",
        plan_digest="sha256:" + "a" * 64,
        input_digest="sha256:" + "b" * 64,
        status=status,
        cleanup_state=(
            FixtureCleanupState.RUNNING
            if status is FixtureRunStatus.CLEANING
            else FixtureCleanupState.PENDING
        ),
        temporal_workflow_id=f"atlas-fixture/{tenant_id}/{run_id}",
        requested_by=None,
        execution_deadline=now + timedelta(seconds=20),
        requested_at=now,
        started_at=now if status is not FixtureRunStatus.REQUESTED else None,
        ready_at=now if status is FixtureRunStatus.READY else None,
        revision=1,
        updated_at=now,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("fail_node", [False, True])
async def test_real_fixture_workflow_sequences_prepare_and_cleanup(fail_node: bool) -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    task_queue = f"atlas-fixture-{uuid7()}"
    fake = FakeFixtureWorkerService(fail_node=fail_node)
    activities = FixtureActivities(cast(FixtureWorkerService, fake))
    dispatcher = TemporalFixtureRunDispatcher(
        client,
        task_queue=task_queue,
        activity_timeout=timedelta(seconds=10),
        cleanup_grace=timedelta(seconds=10),
    )
    tenant_id = uuid7()
    run_id = uuid7()
    run = _fixture_run(tenant_id, run_id, status=FixtureRunStatus.REQUESTED)

    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[FixtureRunWorkflow],
        activities=[
            activities.load_plan,
            activities.execute_node,
            activities.finalize_ready,
            activities.begin_release,
            activities.begin_failed_cleanup,
            activities.cleanup_node,
            activities.finalize_release,
        ],
    ):
        await dispatcher.start(run)
        if not fail_node:
            await dispatcher.release(run)
        result = await client.get_workflow_handle(run.temporal_workflow_id).result()
        await dispatcher.start(run)

    assert result["status"] == ("FAILED" if fail_node else "RELEASED")
    assert "cleanup:createCustomer" in fake.calls
    assert f"finalize:{fail_node}" in fake.calls
    if fail_node:
        assert "begin-failed:MOCK_NODE_FAILED" in fake.calls
        assert "ready" not in fake.calls
    else:
        assert "ready" in fake.calls
        assert "begin-release" in fake.calls
