"""真实 Temporal Server 与 Worker 集成测试。"""

import asyncio
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
    FixtureCleanupSweepBatch,
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
    FixtureCleanupSweepWorkflow,
    FixtureCleanupWorkflow,
    FixtureNodeInput,
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

    def __init__(self, *, fail_node: bool, reconcile_absent: bool = False) -> None:
        self.fail_node = fail_node
        self.reconcile_absent = reconcile_absent
        self.execute_calls = 0
        self.canceled = False
        self.calls: list[str] = []
        self.plan_loaded = asyncio.Event()

    async def load_plan(self, tenant_id: UUID, run_id: UUID) -> FixtureWorkerPlan:
        self.calls.append("load")
        self.plan_loaded.set()
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
        self.execute_calls += 1
        self.calls.append(f"execute:{node_id}")
        if self.reconcile_absent and self.execute_calls == 1:
            return FixtureNodeActivityResult(
                node_id=node_id,
                status=DataNodeRunStatus.OUTCOME_UNCERTAIN,
                failure_category=FixtureFailureCategory.UNCERTAIN,
                failure_code="MOCK_CREATE_UNCERTAIN",
            )
        return FixtureNodeActivityResult(
            node_id=node_id,
            status=(DataNodeRunStatus.FAILED if self.fail_node else DataNodeRunStatus.SUCCEEDED),
            failure_category=(FixtureFailureCategory.TRANSIENT if self.fail_node else None),
            failure_code="MOCK_NODE_FAILED" if self.fail_node else None,
        )

    async def reconcile_node(
        self,
        tenant_id: UUID,
        run_id: UUID,
        node_id: str,
    ) -> FixtureNodeActivityResult:
        self.calls.append(f"reconcile:{node_id}")
        return FixtureNodeActivityResult(
            node_id=node_id,
            status=(
                DataNodeRunStatus.READY
                if self.reconcile_absent
                else DataNodeRunStatus.SUCCEEDED
            ),
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

    async def begin_canceled_cleanup(self, tenant_id: UUID, run_id: UUID) -> FixtureRun:
        self.canceled = True
        self.calls.append("begin-canceled")
        return _fixture_run(tenant_id, run_id, status=FixtureRunStatus.CLEANING)

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
            status=(
                FixtureRunStatus.CANCELED
                if self.canceled
                else (FixtureRunStatus.FAILED if failed_run else FixtureRunStatus.RELEASED)
            ),
            cleanup_state=FixtureCleanupState.CLEANED,
            cleaned_resources=1,
            leaked_resources=0,
        )

    async def sweep_cleanup(
        self,
        tenant_id: UUID,
        *,
        worker_identity: str,
        limit: int,
    ) -> FixtureCleanupSweepBatch:
        self.calls.append(f"sweep:{tenant_id}:{worker_identity}:{limit}")
        return FixtureCleanupSweepBatch(
            reconciled_found=1,
            reconciled_absent=2,
            reconciled_inconclusive=3,
            cleanup_claimed=4,
            cleaned_resources=5,
            retry_scheduled=6,
            leaked_resources=7,
            finalized_runs=8,
            observed_at=datetime.now(UTC),
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
            activities.begin_canceled_cleanup,
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


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_mode", ["signal", "temporal-cancel"])
async def test_real_fixture_workflow_cancel_signal_runs_cleanup(cancel_mode: str) -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    task_queue = f"atlas-fixture-cancel-{uuid7()}"
    fake = FakeFixtureWorkerService(fail_node=False)
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
            activities.begin_canceled_cleanup,
            activities.cleanup_node,
            activities.finalize_release,
        ],
    ):
        await dispatcher.start(run)
        handle = client.get_workflow_handle(run.temporal_workflow_id)
        await asyncio.wait_for(fake.plan_loaded.wait(), timeout=5)
        if cancel_mode == "signal":
            await dispatcher.cancel(run)
        else:
            await handle.cancel()
        result = await handle.result()

    assert result["status"] == "CANCELED"
    assert "begin-canceled" in fake.calls
    assert "cleanup:createCustomer" in fake.calls


@pytest.mark.anyio
async def test_real_fixture_workflow_reconciles_absent_then_retries_create() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    task_queue = f"atlas-fixture-reconcile-{uuid7()}"
    fake = FakeFixtureWorkerService(fail_node=False, reconcile_absent=True)
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
            activities.reconcile_node,
            activities.finalize_ready,
            activities.begin_release,
            activities.begin_failed_cleanup,
            activities.begin_canceled_cleanup,
            activities.cleanup_node,
            activities.finalize_release,
        ],
    ):
        await dispatcher.start(run)
        await dispatcher.release(run)
        result = await client.get_workflow_handle(run.temporal_workflow_id).result()

    assert result["status"] == "RELEASED"
    assert fake.calls.count("execute:createCustomer") == 2
    assert "reconcile:createCustomer" in fake.calls


@pytest.mark.anyio
async def test_real_fixture_cleanup_retry_workflow_only_replays_cleanup() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    task_queue = f"atlas-fixture-cleanup-{uuid7()}"
    fake = FakeFixtureWorkerService(fail_node=False)
    activities = FixtureActivities(cast(FixtureWorkerService, fake))
    dispatcher = TemporalFixtureRunDispatcher(
        client,
        task_queue=task_queue,
        activity_timeout=timedelta(seconds=10),
        cleanup_grace=timedelta(seconds=10),
    )
    tenant_id = uuid7()
    run_id = uuid7()
    run = _fixture_run(tenant_id, run_id, status=FixtureRunStatus.CLEANING)

    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[FixtureCleanupWorkflow],
        activities=[
            activities.load_plan,
            activities.cleanup_node,
            activities.finalize_release,
        ],
    ):
        await dispatcher.retry_cleanup(run)
        result = await client.get_workflow_handle(
            f"{run.temporal_workflow_id}/cleanup/{run.cleanup_generation}"
        ).result()

    assert result["status"] == "RELEASED"
    assert fake.calls == ["load", "cleanup:createCustomer", "finalize:False"]


@pytest.mark.anyio
async def test_real_fixture_cleanup_sweep_workflow_returns_bounded_summary() -> None:
    assert TEMPORAL_ADDRESS is not None
    client = await Client.connect(TEMPORAL_ADDRESS, namespace="default")
    task_queue = f"atlas-fixture-sweep-{uuid7()}"
    fake = FakeFixtureWorkerService(fail_node=False)
    activities = FixtureActivities(cast(FixtureWorkerService, fake))
    dispatcher = TemporalFixtureRunDispatcher(
        client,
        task_queue=task_queue,
        activity_timeout=timedelta(seconds=10),
        cleanup_grace=timedelta(seconds=10),
    )
    tenant_id = uuid7()

    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[FixtureCleanupSweepWorkflow],
        activities=[activities.sweep_cleanup],
    ):
        result = await dispatcher.sweep(
            tenant_id=tenant_id,
            worker_identity="fixture-temporal-sweeper",
            limit=12,
        )

    assert result.cleanup_claimed == 4
    assert result.finalized_runs == 8
    assert fake.calls == [f"sweep:{tenant_id}:fixture-temporal-sweeper:12"]


@pytest.mark.anyio
async def test_fixture_reconcile_activity_maps_safe_result() -> None:
    tenant_id = uuid7()
    run_id = uuid7()
    fake = FakeFixtureWorkerService(fail_node=False)
    activities = FixtureActivities(cast(FixtureWorkerService, fake))

    result = await activities.reconcile_node(
        FixtureNodeInput(
            tenant_id=str(tenant_id),
            run_id=str(run_id),
            node_id="createCustomer",
        )
    )

    assert result.status == "SUCCEEDED"
    assert fake.calls == ["reconcile:createCustomer"]
