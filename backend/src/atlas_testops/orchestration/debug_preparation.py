"""Durable local-only preparation for trusted public-web DebugRun execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import cast
from uuid import UUID

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

with workflow.unsafe.imports_passed_through():
    from atlas_testops.application.access import ActorContext
    from atlas_testops.application.debug_run_dispatcher import DebugRunDispatcher
    from atlas_testops.application.debug_runtime import DebugRuntimeService
    from atlas_testops.application.fixture_dispatcher import FixtureRunDispatcher
    from atlas_testops.application.fixture_runs import FixtureRunService
    from atlas_testops.application.leases import LeaseService
    from atlas_testops.application.session_dispatcher import AuthSessionDispatcher
    from atlas_testops.core.config import Settings
    from atlas_testops.core.contracts import utc_now
    from atlas_testops.core.errors import ApplicationError, ErrorCode
    from atlas_testops.domain.case import (
        DebugRun,
        DebugRunLifecycle,
        DebugRunOutcome,
        ValueSourceKind,
    )
    from atlas_testops.domain.fixture import (
        FixtureActorLeaseBinding,
        FixtureRun,
        FixtureRunKind,
        FixtureRunStatus,
        StartFixtureRun,
    )
    from atlas_testops.domain.identity import (
        AcquireAccountLease,
        CredentialAuthMethod,
        EnsureLoginSession,
        LeaseRequirements,
        LoginSessionReady,
    )
    from atlas_testops.domain.runtime import (
        BindDebugExecution,
        BindExecutionActor,
        BrowserExecutionProfile,
        ModelExecutionProfile,
        ToolExecutionProfile,
        Viewport,
    )
    from atlas_testops.infrastructure.adapters.fixture_registry import (
        FixtureOperationRegistry,
    )
    from atlas_testops.infrastructure.adapters.local_public_web import (
        BAIDU_ORIGIN,
        BAIDU_SURFACE_DIGEST,
        BAIDU_SURFACE_VERSION_REF,
        LOCAL_PUBLIC_WEB_ALLOWED_ACTIONS,
        LOCAL_PUBLIC_WEB_MODEL_PROFILE_REF,
        LOCAL_PUBLIC_WEB_POLICY_BUNDLE_REF,
        LOCAL_PUBLIC_WEB_PROMPT_BUNDLE_REF,
        LOCAL_PUBLIC_WEB_REASONING_POLICY_REF,
        LOCAL_PUBLIC_WEB_TOOL_CATALOG_REF,
    )
    from atlas_testops.infrastructure.adapters.mock_provider import (
        LOCAL_PUBLIC_WEB_ROLE_KEY,
    )
    from atlas_testops.infrastructure.adapters.playwright_browser import BrowserToolCatalog
    from atlas_testops.infrastructure.database import Database, DatabaseContext
    from atlas_testops.infrastructure.repositories.debug_runs import DebugRunRepository
    from atlas_testops.orchestration.browser import TemporalBrowserExecutionDispatcher

DEBUG_PREPARATION_ACTIVITY = "atlas.prepare-local-debug-run/0.1"
DEBUG_PREPARATION_WORKFLOW = "atlas.local-debug-preparation-workflow/0.1"
LOCAL_FIXTURE_BLUEPRINT_REF = "demo.web.search-context@1.0.0"


@dataclass(frozen=True, slots=True)
class DebugPreparationWorkflowInput:
    """Secret-free identity of one already-frozen DebugRun."""

    tenant_id: str
    run_id: str
    activity_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class DebugPreparationPayload:
    """Safe preparation outcome retained in Temporal history."""

    run_id: str
    prepared: bool
    failure_code: str | None = None


@workflow.defn(name=DEBUG_PREPARATION_WORKFLOW)
class LocalDebugPreparationWorkflow:
    """Prepare and dispatch one local DebugRun through idempotent services."""

    @workflow.run
    async def run(
        self,
        request: DebugPreparationWorkflowInput,
    ) -> DebugPreparationPayload:
        result = await workflow.execute_activity(
            DEBUG_PREPARATION_ACTIVITY,
            request,
            result_type=DebugPreparationPayload,
            start_to_close_timeout=timedelta(
                seconds=request.activity_timeout_seconds
            ),
            heartbeat_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1),
                maximum_interval=timedelta(seconds=3),
                maximum_attempts=2,
            ),
        )
        return cast(DebugPreparationPayload, result)


class LocalDebugPreparationService:
    """Compose existing lease, session, Fixture, bind, and Browser dispatch ports."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        auth_session_dispatcher: AuthSessionDispatcher,
        fixture_run_dispatcher: FixtureRunDispatcher,
        browser_execution_dispatcher: TemporalBrowserExecutionDispatcher,
    ) -> None:
        if (
            settings.environment not in {"local", "development"}
            or not settings.debug_run_preparation_enabled
        ):
            raise ValueError("local DebugRun preparation is not enabled")
        self._database = database
        self._settings = settings
        self._auth_sessions = auth_session_dispatcher
        self._browser = browser_execution_dispatcher
        self._leases = LeaseService(database)
        self._fixtures = FixtureRunService(
            database,
            fixture_run_dispatcher,
            FixtureOperationRegistry.from_settings(settings),
            cleanup_grace=timedelta(
                seconds=settings.fixture_cleanup_grace_seconds
            ),
        )
        self._runtime = DebugRuntimeService(database)
        self._runs = DebugRunRepository()

    async def prepare(self, tenant_id: UUID, run_id: UUID) -> DebugPreparationPayload:
        """Prepare one run and record a truthful terminal failure after final retry."""

        try:
            await self._prepare(tenant_id, run_id)
        except Exception as error:
            if activity.info().attempt < 2:
                raise
            failure_code = (
                "LOCAL_RUNTIME_CASE_UNSUPPORTED"
                if isinstance(error, LocalDebugCaseUnsupportedError)
                else "LOCAL_RUNTIME_PREPARATION_FAILED"
            )
            outcome = (
                DebugRunOutcome.BLOCKED
                if isinstance(error, LocalDebugCaseUnsupportedError)
                else DebugRunOutcome.INFRA_ERROR
            )
            await self._runtime.fail_preparation(
                tenant_id,
                run_id,
                outcome=outcome,
                failure_code=failure_code,
                failure_detail=(
                    "The frozen case is outside the reviewed local Browser Runtime."
                    if outcome is DebugRunOutcome.BLOCKED
                    else "Trusted local Browser Runtime preparation did not complete."
                ),
            )
            return DebugPreparationPayload(
                run_id=str(run_id),
                prepared=False,
                failure_code=failure_code,
            )
        return DebugPreparationPayload(run_id=str(run_id), prepared=True)

    async def _prepare(self, tenant_id: UUID, run_id: UUID) -> None:
        run = await self._load_run(tenant_id, run_id)
        if run.lifecycle is DebugRunLifecycle.TERMINATED:
            return
        self._require_local_case(run)
        if run.requested_by is None:
            raise LocalDebugCaseUnsupportedError("DebugRun has no auditable requester")
        if run.execution_deadline - utc_now() > timedelta(seconds=3_500):
            raise LocalDebugCaseUnsupportedError("DebugRun exceeds local session TTL")
        actor = ActorContext(
            tenant_id=run.tenant_id,
            actor_id=run.requested_by,
            request_id=f"local-debug-preparation:{run.id}",
            current_project_id=run.project_id,
            development_override=True,
        )
        execution_id = f"debug-run:{run.id}"
        actor_contract = run.test_ir.actors[0]
        activity.heartbeat("acquiring local public-web account lease")
        lease = (
            await self._leases.acquire(
                actor,
                AcquireAccountLease(
                    execution_id=execution_id,
                    worker_id=self._settings.browser_runtime_worker_identity,
                    environment_id=run.environment_id,
                    role_key=actor_contract.role_key,
                    requirements=LeaseRequirements(
                        auth_methods=(CredentialAuthMethod.PASSWORD,),
                        capabilities=actor_contract.capabilities,
                    ),
                    ttl_seconds=7_200,
                    execution_deadline=run.execution_deadline
                    + timedelta(
                        seconds=self._settings.fixture_cleanup_grace_seconds
                    ),
                ),
                idempotency_key=f"local-debug-lease-{run.id}",
            )
        ).value

        activity.heartbeat("creating encrypted local Browser session artifact")
        session = await self._auth_sessions.ensure(
            actor,
            lease.lease_id,
            EnsureLoginSession(
                fencing_token=lease.fencing_token,
                worker_identity=self._settings.browser_runtime_worker_identity,
                allowed_origins=(BAIDU_ORIGIN,),
                ttl_seconds=3_600,
            ),
        )
        if not isinstance(session, LoginSessionReady):
            raise RuntimeError("local public-web session unexpectedly requires manual action")

        keyword = run.test_ir.variables["searchKeyword"].value
        activity.heartbeat("starting exact local FixtureRun")
        fixture = (
            await self._fixtures.start(
                actor,
                run.project_id,
                StartFixtureRun(
                    run_kind=FixtureRunKind.EXECUTION,
                    blueprint_version_id=run.test_ir.fixture.blueprint_version_id,
                    environment_id=run.environment_id,
                    execution_id=execution_id,
                    inputs={"keyword": keyword},
                    actor_bindings=(
                        FixtureActorLeaseBinding(
                            actor_slot=actor_contract.actor_slot,
                            account_lease_id=lease.lease_id,
                            fencing_token=lease.fencing_token,
                        ),
                    ),
                    execution_deadline=run.execution_deadline,
                ),
                idempotency_key=f"local-debug-fixture-{run.id}",
            )
        ).value
        fixture = await self._wait_for_fixture(actor, fixture.id, run)

        catalog = BrowserToolCatalog.reviewed(
            catalog_ref=LOCAL_PUBLIC_WEB_TOOL_CATALOG_REF,
            policy_bundle_ref=LOCAL_PUBLIC_WEB_POLICY_BUNDLE_REF,
            allowed_actions=LOCAL_PUBLIC_WEB_ALLOWED_ACTIONS,
        )
        revision = self._settings.browser_revision
        if revision is None:
            raise RuntimeError("local browser revision is unavailable")
        activity.heartbeat("binding exact local ExecutionContract")
        contract = await self._runtime.bind(
            run.tenant_id,
            run.id,
            BindDebugExecution(
                worker_identity=self._settings.browser_runtime_worker_identity,
                fixture_run_id=fixture.id,
                actors=(
                    BindExecutionActor(
                        actor_slot=actor_contract.actor_slot,
                        account_lease_id=lease.lease_id,
                        fencing_token=lease.fencing_token,
                        browser_context_ref=session.browser_context_ref,
                    ),
                ),
                browser=BrowserExecutionProfile(
                    revision=revision,
                    viewport=Viewport(width=1440, height=900),
                    locale="zh-CN",
                    timezone="Asia/Shanghai",
                ),
                model=ModelExecutionProfile(
                    model_profile_ref=LOCAL_PUBLIC_WEB_MODEL_PROFILE_REF,
                    prompt_bundle_ref=LOCAL_PUBLIC_WEB_PROMPT_BUNDLE_REF,
                    reasoning_policy_ref=LOCAL_PUBLIC_WEB_REASONING_POLICY_REF,
                ),
                tools=ToolExecutionProfile(
                    tool_catalog_ref=catalog.catalog_ref,
                    mcp_server_manifest_digest=catalog.mcp_server_manifest_digest,
                    tool_schema_digest=catalog.tool_schema_digest,
                    policy_bundle_ref=catalog.policy_bundle_ref,
                    policy_digest=catalog.policy_digest,
                ),
            ),
        )
        bound = await self._load_run(tenant_id, run_id)
        activity.heartbeat("dispatching trusted Browser execution")
        await self._browser.start_bound(bound, contract)
        try:
            await self._wait_for_browser(bound)
        finally:
            activity.heartbeat("releasing local FixtureRun")
            await self._fixtures.release(actor, fixture.id)
            await self._wait_for_fixture_release(actor, fixture.id, run)

    async def _load_run(self, tenant_id: UUID, run_id: UUID) -> DebugRun:
        async with self._database.transaction(
            DatabaseContext(
                tenant_id=tenant_id,
                request_id=f"local-debug-load:{run_id}",
            )
        ) as connection:
            run = await self._runs.get_run(connection, run_id)
        if run is None:
            raise RuntimeError("DebugRun is unavailable to local preparation")
        return run

    async def _wait_for_fixture(
        self,
        actor: ActorContext,
        fixture_run_id: UUID,
        debug_run: DebugRun,
    ) -> FixtureRun:
        while utc_now() < debug_run.execution_deadline:
            detail = await self._fixtures.get_detail(actor, fixture_run_id)
            if detail.run.status is FixtureRunStatus.READY:
                return detail.run
            if detail.run.status in {
                FixtureRunStatus.FAILED,
                FixtureRunStatus.CANCELED,
                FixtureRunStatus.CLEANUP_FAILED,
            }:
                raise RuntimeError("local FixtureRun did not reach READY")
            activity.heartbeat(f"waiting for FixtureRun {detail.run.status.value}")
            await asyncio.sleep(0.25)
        raise RuntimeError("local FixtureRun exceeded DebugRun deadline")

    async def _wait_for_browser(self, run: DebugRun) -> None:
        completion = asyncio.create_task(self._browser.wait_for_completion(run))
        while not completion.done():
            activity.heartbeat("waiting for trusted Browser execution")
            try:
                await asyncio.wait_for(asyncio.shield(completion), timeout=5)
            except TimeoutError:
                continue
        await completion

    async def _wait_for_fixture_release(
        self,
        actor: ActorContext,
        fixture_run_id: UUID,
        debug_run: DebugRun,
    ) -> None:
        while utc_now() < debug_run.execution_deadline:
            detail = await self._fixtures.get_detail(actor, fixture_run_id)
            if detail.run.status is FixtureRunStatus.RELEASED:
                return
            if detail.run.status in {
                FixtureRunStatus.CLEANUP_FAILED,
                FixtureRunStatus.FAILED,
                FixtureRunStatus.CANCELED,
            }:
                raise RuntimeError("local FixtureRun cleanup did not complete safely")
            activity.heartbeat(
                f"waiting for FixtureRun cleanup {detail.run.status.value}"
            )
            await asyncio.sleep(0.25)
        raise RuntimeError("local FixtureRun cleanup exceeded DebugRun deadline")

    @staticmethod
    def _require_local_case(run: DebugRun) -> None:
        surfaces = {
            (surface.version_ref, surface.content_digest)
            for surface in run.test_ir.surfaces
        }
        actors = run.test_ir.actors
        variable = run.test_ir.variables.get("searchKeyword")
        if (
            len(actors) != 1
            or actors[0].role_key != LOCAL_PUBLIC_WEB_ROLE_KEY
            or run.test_ir.fixture.blueprint_version_ref
            != LOCAL_FIXTURE_BLUEPRINT_REF
            or (BAIDU_SURFACE_VERSION_REF, BAIDU_SURFACE_DIGEST) not in surfaces
            or variable is None
            or variable.kind is not ValueSourceKind.LITERAL
            or not isinstance(variable.value, str)
            or not variable.value.strip()
        ):
            raise LocalDebugCaseUnsupportedError(
                "case does not match the reviewed local public-web profile"
            )


class DebugPreparationActivities:
    """Temporal activity adapter around the local preparation service."""

    def __init__(self, service: LocalDebugPreparationService) -> None:
        self._service = service

    @activity.defn(name=DEBUG_PREPARATION_ACTIVITY)
    async def prepare(
        self,
        request: DebugPreparationWorkflowInput,
    ) -> DebugPreparationPayload:
        return await self._service.prepare(
            UUID(request.tenant_id),
            UUID(request.run_id),
        )


class TemporalDebugRunDispatcher(DebugRunDispatcher):
    """Start or cancel the durable local preparation workflow."""

    def __init__(
        self,
        client: Client,
        *,
        task_queue: str,
        activity_timeout: timedelta,
    ) -> None:
        self._client = client
        self._task_queue = task_queue.strip()
        self._activity_timeout = activity_timeout
        if not self._task_queue:
            raise ValueError("debug preparation task queue must not be blank")

    async def start(self, run: DebugRun) -> None:
        try:
            await self._client.start_workflow(
                LocalDebugPreparationWorkflow.run,
                DebugPreparationWorkflowInput(
                    tenant_id=str(run.tenant_id),
                    run_id=str(run.id),
                    activity_timeout_seconds=int(
                        self._activity_timeout.total_seconds()
                    ),
                ),
                id=self.workflow_id(run),
                task_queue=self._task_queue,
                execution_timeout=max(
                    self._activity_timeout + timedelta(seconds=30),
                    run.execution_deadline - utc_now(),
                ),
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except WorkflowAlreadyStartedError:
            return
        except RPCError as error:
            raise ApplicationError(
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                title="Debug preparation Worker 不可用",
                detail="可信 DebugRun preparation workflow 未能提交。",
                status_code=503,
            ) from error

    async def cancel(self, run: DebugRun) -> None:
        for workflow_id in (self.workflow_id(run), run.temporal_workflow_id):
            try:
                await self._client.get_workflow_handle(workflow_id).cancel()
            except RPCError:
                continue

    @staticmethod
    def workflow_id(run: DebugRun) -> str:
        return f"atlas-debug-preparation/{run.tenant_id.hex}/{run.id.hex}"


class LocalDebugCaseUnsupportedError(RuntimeError):
    """The frozen run is outside the explicitly reviewed local demo."""
