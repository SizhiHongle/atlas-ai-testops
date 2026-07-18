"""Real PostgreSQL proof for fenced UnitAttempt live control."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid7

import pytest
from pydantic import SecretStr
from tests.integration.test_task_execution_hosts_pg import (
    DATABASE_URL,
    SeededCaseVersion,
    _build_aggregate,
    _seed_published_case_version,
)
from tests.integration.test_task_orchestration_pg import _persist_sealed_aggregate

from atlas_testops.application.access import ActorContext
from atlas_testops.application.live_control import LiveControlService
from atlas_testops.application.task_orchestration import TaskWorkerService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.runtime import (
    AcknowledgeLiveControl,
    CompleteLiveActionGrant,
    ConsumeLiveActionGrant,
    ControlLease,
    ControlLeaseState,
    HeartbeatLiveControl,
    InitializeLiveSession,
    LiveActionExecutionStatus,
    LiveActionGrantState,
    LiveControllerType,
    LiveSession,
    LiveSessionState,
    RequestLiveActionGrant,
    RequestLiveControl,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.live_control import LiveControlRepository
from atlas_testops.infrastructure.repositories.task_execution_tickets import (
    TaskExecutionTicketRepository,
)
from atlas_testops.orchestration.task_intents import TaskRunWorkflowInput
from atlas_testops.orchestration.tasks import (
    TaskBatchPrepareInput,
    UnitAttemptWorkflowInput,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


def test_takeover_fences_old_agent_and_return_reconciles() -> None:
    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)
    asyncio.run(_exercise_takeover(settings, seeded))


def test_expired_agent_lease_is_reaped_and_fenced() -> None:
    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)
    asyncio.run(_exercise_expiry_reaper(settings, seeded))


async def _exercise_takeover(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    database = Database(settings)
    await database.open()
    try:
        aggregate = await _persist_sealed_aggregate(
            database,
            _build_aggregate(seeded),
        )
        run = aggregate.run
        assert run.request_digest is not None
        root = TaskRunWorkflowInput(
            tenant_id=str(run.tenant_id),
            project_id=str(run.project_id),
            task_run_id=str(run.id),
            request_digest=run.request_digest,
            manifest_hash=run.manifest_hash,
        )
        worker = TaskWorkerService(database)
        plan = await worker.load_dispatch_plan(root)
        dispatch = plan.units[0]
        attempt_input = UnitAttemptWorkflowInput(
            tenant_id=root.tenant_id,
            project_id=root.project_id,
            task_run_id=root.task_run_id,
            request_digest=root.request_digest,
            manifest_hash=root.manifest_hash,
            ordinal=dispatch.ordinal,
            execution_unit_id=dispatch.execution_unit_id,
            unit_attempt_id=dispatch.unit_attempt_id,
            execution_deadline=dispatch.execution_deadline,
            activity_timeout_seconds=dispatch.activity_timeout_seconds,
        )
        prepared = await worker.prepare_batch(
            TaskBatchPrepareInput(request=root, attempts=(attempt_input,))
        )
        assert prepared.status == "AUTHORIZED"
        assert (await worker.start_attempt(attempt_input)).status == "READY"

        service = LiveControlService(database)
        worker_identity = "browser-worker-1"
        with pytest.raises(ApplicationError) as wrong_worker:
            await service.initialize(
                run.tenant_id,
                aggregate.attempt.id,
                InitializeLiveSession(
                    browser_session_id="browser-session-1",
                    owner_id="another-browser-worker",
                ),
                worker_identity=worker_identity,
            )
        assert wrong_worker.value.error_code is ErrorCode.LEASE_FENCED
        initial = await service.initialize(
            run.tenant_id,
            aggregate.attempt.id,
            InitializeLiveSession(
                browser_session_id="browser-session-1",
                owner_id=worker_identity,
                browser_revision=1,
            ),
            worker_identity=worker_identity,
        )
        assert initial.session.state is LiveSessionState.AGENT_CONTROLLED
        assert initial.lease is not None
        assert initial.lease.owner_type is LiveControllerType.AGENT
        replayed_initial = await service.initialize(
            run.tenant_id,
            aggregate.attempt.id,
            InitializeLiveSession(
                browser_session_id="browser-session-1",
                owner_id=worker_identity,
                browser_revision=1,
            ),
            worker_identity=worker_identity,
        )
        assert replayed_initial.session.id == initial.session.id

        renewed = await service.heartbeat(
            run.tenant_id,
            aggregate.attempt.id,
            HeartbeatLiveControl(
                control_epoch=1,
                fencing_token=1,
                requested_ttl_sec=300,
            ),
            worker_identity=worker_identity,
        )
        assert renewed.session.control_epoch == 1
        assert renewed.session.fencing_token == 1
        assert renewed.lease is not None
        assert renewed.lease.expires_at >= initial.lease.expires_at
        shorter_heartbeat = await service.heartbeat(
            run.tenant_id,
            aggregate.attempt.id,
            HeartbeatLiveControl(
                control_epoch=1,
                fencing_token=1,
                requested_ttl_sec=30,
            ),
            worker_identity=worker_identity,
        )
        assert shorter_heartbeat.lease is not None
        assert shorter_heartbeat.lease.expires_at == renewed.lease.expires_at

        old_agent_grant = await service.issue_action_grant(
            run.tenant_id,
            aggregate.attempt.id,
            _grant_request(epoch=1, fence=1, page_revision=1),
            worker_identity=worker_identity,
        )
        actor = ActorContext(
            tenant_id=run.tenant_id,
            actor_id=run.requested_by,
            request_id=f"takeover:{aggregate.attempt.id}",
            development_override=True,
        )
        with pytest.raises(ApplicationError) as invalid_ttl:
            await service.pause(
                actor,
                aggregate.attempt.id,
                RequestLiveControl(
                    reason="invalid pause ttl",
                    requested_ttl_sec=30,
                ),
                expected_control_epoch=1,
                idempotency_key=f"invalid-pause-{aggregate.attempt.id}",
            )
        assert invalid_ttl.value.error_code is ErrorCode.INVALID_REQUEST
        pause = await service.pause(
            actor,
            aggregate.attempt.id,
            RequestLiveControl(reason="inspect current browser state"),
            expected_control_epoch=1,
            idempotency_key=f"pause-{aggregate.attempt.id}",
        )
        replayed_pause = await service.pause(
            actor,
            aggregate.attempt.id,
            RequestLiveControl(reason="inspect current browser state"),
            expected_control_epoch=1,
            idempotency_key=f"pause-{aggregate.attempt.id}",
        )
        assert replayed_pause.replayed
        with pytest.raises(ApplicationError) as mismatched_replay:
            await service.pause(
                actor,
                aggregate.attempt.id,
                RequestLiveControl(reason="different request"),
                expected_control_epoch=1,
                idempotency_key=f"pause-{aggregate.attempt.id}",
            )
        assert mismatched_replay.value.error_code is ErrorCode.CONFLICT
        assert (
            await service.get_command(
                actor,
                unit_attempt_id=aggregate.attempt.id,
                command_id=pause.value.id,
            )
        ).id == pause.value.id
        paused = await service.acknowledge(
            run.tenant_id,
            aggregate.attempt.id,
            AcknowledgeLiveControl(
                command_id=pause.value.id,
                expected_control_epoch=1,
                expected_fencing_token=1,
                browser_revision=1,
                checkpoint_digest=f"sha256:{'0' * 64}",
                agent_owner_id=worker_identity,
            ),
            worker_identity=worker_identity,
        )
        assert paused.session.state is LiveSessionState.PAUSED
        assert paused.session.control_epoch == 2
        assert paused.lease is None
        replayed_pause_ack = await service.acknowledge(
            run.tenant_id,
            aggregate.attempt.id,
            AcknowledgeLiveControl(
                command_id=pause.value.id,
                expected_control_epoch=1,
                expected_fencing_token=1,
                browser_revision=1,
                checkpoint_digest=f"sha256:{'0' * 64}",
                agent_owner_id=worker_identity,
            ),
            worker_identity=worker_identity,
        )
        assert replayed_pause_ack.session.state is LiveSessionState.PAUSED
        with pytest.raises(ApplicationError) as mismatched_ack:
            await service.acknowledge(
                run.tenant_id,
                aggregate.attempt.id,
                AcknowledgeLiveControl(
                    command_id=pause.value.id,
                    expected_control_epoch=1,
                    expected_fencing_token=1,
                    browser_revision=1,
                    checkpoint_digest=f"sha256:{'f' * 64}",
                    agent_owner_id=worker_identity,
                ),
                worker_identity=worker_identity,
            )
        assert mismatched_ack.value.error_code is ErrorCode.CONFLICT

        resume = await service.resume(
            actor,
            aggregate.attempt.id,
            RequestLiveControl(reason="resume automated execution"),
            expected_control_epoch=2,
            idempotency_key=f"resume-{aggregate.attempt.id}",
        )
        resumed = await service.acknowledge(
            run.tenant_id,
            aggregate.attempt.id,
            AcknowledgeLiveControl(
                command_id=resume.value.id,
                expected_control_epoch=2,
                expected_fencing_token=2,
                browser_revision=1,
                checkpoint_digest=f"sha256:{'1' * 64}",
                agent_owner_id=worker_identity,
            ),
            worker_identity=worker_identity,
        )
        assert resumed.session.state is LiveSessionState.AGENT_CONTROLLED
        assert resumed.session.control_epoch == 3
        assert resumed.lease is not None
        with pytest.raises(ApplicationError) as stale_epoch:
            await service.takeover(
                actor,
                aggregate.attempt.id,
                RequestLiveControl(reason="stale takeover"),
                expected_control_epoch=2,
                idempotency_key=f"stale-takeover-{aggregate.attempt.id}",
            )
        assert stale_epoch.value.error_code is ErrorCode.PRECONDITION_FAILED
        assert stale_epoch.value.headers == {"ETag": '"control-epoch-3"'}

        takeover = await service.takeover(
            actor,
            aggregate.attempt.id,
            RequestLiveControl(
                reason="inspect filter dialog",
                requested_ttl_sec=300,
            ),
            expected_control_epoch=3,
            idempotency_key=f"takeover-{aggregate.attempt.id}",
        )
        assert takeover.value.status.value == "PENDING"
        with pytest.raises(ApplicationError) as pending_command:
            await service.pause(
                actor,
                aggregate.attempt.id,
                RequestLiveControl(reason="second pending command"),
                expected_control_epoch=3,
                idempotency_key=f"pending-pause-{aggregate.attempt.id}",
            )
        assert pending_command.value.error_code is ErrorCode.CONFLICT
        with pytest.raises(ApplicationError) as stale:
            await service.consume_action_grant(
                run.tenant_id,
                aggregate.attempt.id,
                old_agent_grant.grant_id,
                ConsumeLiveActionGrant(
                    control_epoch=1,
                    fencing_token=1,
                    proposal_digest=old_agent_grant.proposal_digest,
                ),
            )
        assert stale.value.error_code in {ErrorCode.CONFLICT, ErrorCode.LEASE_FENCED}

        human = await service.acknowledge(
            run.tenant_id,
            aggregate.attempt.id,
            AcknowledgeLiveControl(
                command_id=takeover.value.id,
                expected_control_epoch=3,
                expected_fencing_token=3,
                browser_revision=1,
                checkpoint_digest=f"sha256:{'a' * 64}",
                agent_owner_id=worker_identity,
            ),
            worker_identity=worker_identity,
        )
        assert human.session.state is LiveSessionState.HUMAN_CONTROLLED
        assert human.session.control_epoch == 4
        assert human.session.fencing_token == 4
        assert human.session.human_influenced
        assert human.lease is not None
        assert human.lease.owner_type is LiveControllerType.HUMAN

        human_grant = await service.issue_action_grant(
            run.tenant_id,
            aggregate.attempt.id,
            _grant_request(epoch=4, fence=4, page_revision=1),
            worker_identity=worker_identity,
        )
        replayed_human_grant = await service.issue_action_grant(
            run.tenant_id,
            aggregate.attempt.id,
            RequestLiveActionGrant(
                action_id=human_grant.action_id,
                proposal_digest=human_grant.proposal_digest,
                page_id=human_grant.page_id,
                page_revision=human_grant.page_revision,
                control_epoch=human_grant.control_epoch,
                fencing_token=human_grant.fencing_token,
                allowed_adapter=human_grant.allowed_adapter,
                policy_digest=human_grant.policy_digest,
            ),
            worker_identity=worker_identity,
        )
        assert replayed_human_grant.grant_id == human_grant.grant_id
        with pytest.raises(ApplicationError) as changed_proposal:
            await service.issue_action_grant(
                run.tenant_id,
                aggregate.attempt.id,
                RequestLiveActionGrant(
                    action_id=human_grant.action_id,
                    proposal_digest=human_grant.proposal_digest,
                    page_id="different-page",
                    page_revision=human_grant.page_revision,
                    control_epoch=human_grant.control_epoch,
                    fencing_token=human_grant.fencing_token,
                    allowed_adapter=human_grant.allowed_adapter,
                    policy_digest=human_grant.policy_digest,
                ),
                worker_identity=worker_identity,
            )
        assert changed_proposal.value.error_code is ErrorCode.CONFLICT
        assert (
            await service.get_action_grant(
                run.tenant_id,
                aggregate.attempt.id,
                human_grant.grant_id,
            )
        ).grant_id == human_grant.grant_id
        consumed = await service.consume_action_grant(
            run.tenant_id,
            aggregate.attempt.id,
            human_grant.grant_id,
            ConsumeLiveActionGrant(
                control_epoch=4,
                fencing_token=4,
                proposal_digest=human_grant.proposal_digest,
            ),
        )
        assert consumed.state is LiveActionGrantState.CONSUMED
        with pytest.raises(ApplicationError) as duplicate_consume:
            await service.consume_action_grant(
                run.tenant_id,
                aggregate.attempt.id,
                human_grant.grant_id,
                ConsumeLiveActionGrant(
                    control_epoch=4,
                    fencing_token=4,
                    proposal_digest=human_grant.proposal_digest,
                ),
            )
        assert duplicate_consume.value.error_code is ErrorCode.CONFLICT
        completion = CompleteLiveActionGrant(
            control_epoch=4,
            fencing_token=4,
            receipt_id=uuid7(),
            execution_status=LiveActionExecutionStatus.SUCCEEDED,
            resulting_page_revision=2,
        )
        completed = await service.complete_action_grant(
            run.tenant_id,
            aggregate.attempt.id,
            human_grant.grant_id,
            completion,
        )
        assert completed.state is LiveActionGrantState.COMPLETED
        assert (
            await service.complete_action_grant(
                run.tenant_id,
                aggregate.attempt.id,
                human_grant.grant_id,
                completion,
            )
        ).grant_id == completed.grant_id
        with pytest.raises(ApplicationError) as changed_receipt:
            await service.complete_action_grant(
                run.tenant_id,
                aggregate.attempt.id,
                human_grant.grant_id,
                completion.model_copy(update={"receipt_id": uuid7()}),
            )
        assert changed_receipt.value.error_code is ErrorCode.CONFLICT

        stale_human_grant = await service.issue_action_grant(
            run.tenant_id,
            aggregate.attempt.id,
            _grant_request(epoch=4, fence=4, page_revision=2),
            worker_identity=worker_identity,
        )
        returned = await service.return_control(
            actor,
            aggregate.attempt.id,
            RequestLiveControl(reason="inspection complete"),
            expected_control_epoch=4,
            idempotency_key=f"return-{aggregate.attempt.id}",
        )
        with pytest.raises(ApplicationError):
            await service.consume_action_grant(
                run.tenant_id,
                aggregate.attempt.id,
                stale_human_grant.grant_id,
                ConsumeLiveActionGrant(
                    control_epoch=4,
                    fencing_token=4,
                    proposal_digest=stale_human_grant.proposal_digest,
                ),
            )
        agent = await service.acknowledge(
            run.tenant_id,
            aggregate.attempt.id,
            AcknowledgeLiveControl(
                command_id=returned.value.id,
                expected_control_epoch=4,
                expected_fencing_token=4,
                browser_revision=2,
                checkpoint_digest=f"sha256:{'b' * 64}",
                agent_owner_id=worker_identity,
            ),
            worker_identity=worker_identity,
        )
        assert agent.session.state is LiveSessionState.AGENT_CONTROLLED
        assert agent.session.control_epoch == 5
        assert agent.session.fencing_token == 5
        assert agent.lease is not None
        assert agent.lease.owner_id == worker_identity
    finally:
        await database.close()


async def _exercise_expiry_reaper(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    database = Database(settings)
    await database.open()
    try:
        aggregate = await _persist_sealed_aggregate(
            database,
            _build_aggregate(seeded),
        )
        run = aggregate.run
        assert run.request_digest is not None
        root = TaskRunWorkflowInput(
            tenant_id=str(run.tenant_id),
            project_id=str(run.project_id),
            task_run_id=str(run.id),
            request_digest=run.request_digest,
            manifest_hash=run.manifest_hash,
        )
        worker = TaskWorkerService(database)
        dispatch = (await worker.load_dispatch_plan(root)).units[0]
        attempt_input = UnitAttemptWorkflowInput(
            tenant_id=root.tenant_id,
            project_id=root.project_id,
            task_run_id=root.task_run_id,
            request_digest=root.request_digest,
            manifest_hash=root.manifest_hash,
            ordinal=dispatch.ordinal,
            execution_unit_id=dispatch.execution_unit_id,
            unit_attempt_id=dispatch.unit_attempt_id,
            execution_deadline=dispatch.execution_deadline,
            activity_timeout_seconds=dispatch.activity_timeout_seconds,
        )
        prepared = await worker.prepare_batch(
            TaskBatchPrepareInput(request=root, attempts=(attempt_input,))
        )
        assert prepared.status == "AUTHORIZED"
        assert (await worker.start_attempt(attempt_input)).status == "READY"

        live = LiveControlRepository()
        tickets = TaskExecutionTicketRepository()
        context = DatabaseContext(
            tenant_id=run.tenant_id,
            request_id=f"seed-expired-live-control:{aggregate.attempt.id}",
        )
        async with database.transaction(context) as connection:
            ticket = await tickets.get_by_attempt(connection, aggregate.attempt.id)
            assert ticket is not None
            cursor = await connection.execute(
                "select transaction_timestamp() as value"
            )
            row = await cursor.fetchone()
            assert row is not None
            observed_at = row["value"]
            session = LiveSession(
                id=uuid7(),
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                task_run_id=run.id,
                execution_unit_id=aggregate.attempt.execution_unit_id,
                unit_attempt_id=aggregate.attempt.id,
                execution_ticket_id=ticket.id,
                execution_ticket_digest=ticket.ticket_digest,
                browser_session_id="browser-session-expired",
                state=LiveSessionState.AGENT_CONTROLLED,
                control_epoch=1,
                fencing_token=1,
                browser_revision=1,
                revision=1,
                created_at=observed_at - timedelta(minutes=10),
                updated_at=observed_at - timedelta(minutes=10),
            )
            assert await live.create_session(connection, session) is not None
            await live.create_lease(
                connection,
                ControlLease(
                    id=uuid7(),
                    tenant_id=run.tenant_id,
                    project_id=run.project_id,
                    task_run_id=run.id,
                    execution_unit_id=aggregate.attempt.execution_unit_id,
                    unit_attempt_id=aggregate.attempt.id,
                    live_session_id=session.id,
                    owner_type=LiveControllerType.AGENT,
                    owner_id="browser-worker-expired",
                    control_epoch=1,
                    fencing_token=1,
                    state=ControlLeaseState.ACTIVE,
                    expires_at=observed_at - timedelta(minutes=1),
                    reason="expired agent control",
                    created_at=observed_at - timedelta(minutes=5),
                    updated_at=observed_at - timedelta(minutes=5),
                ),
            )

        actor = ActorContext(
            tenant_id=run.tenant_id,
            actor_id=run.requested_by,
            request_id=f"reap:{aggregate.attempt.id}",
            development_override=True,
        )
        service = LiveControlService(database)
        reaped = await service.reap_expired(actor, limit=10)
        assert reaped.reaped == 1
        snapshot = await service.get_snapshot(actor, aggregate.attempt.id)
        assert snapshot.session.state is LiveSessionState.NO_CONTROLLER
        assert snapshot.session.control_epoch == 2
        assert snapshot.session.fencing_token == 2
        assert snapshot.lease is None
        assert (await service.reap_expired(actor, limit=10)).reaped == 0
        with pytest.raises(ApplicationError) as fenced:
            await service.heartbeat(
                run.tenant_id,
                aggregate.attempt.id,
                HeartbeatLiveControl(control_epoch=1, fencing_token=1),
                worker_identity="browser-worker-expired",
            )
        assert fenced.value.error_code is ErrorCode.LEASE_FENCED
    finally:
        await database.close()


def _grant_request(
    *,
    epoch: int,
    fence: int,
    page_revision: int,
) -> RequestLiveActionGrant:
    return RequestLiveActionGrant(
        action_id=uuid7(),
        proposal_digest=f"sha256:{uuid7().hex * 2}",
        page_id="page-1",
        page_revision=page_revision,
        control_epoch=epoch,
        fencing_token=fence,
        allowed_adapter="click",
        policy_digest=f"sha256:{'c' * 64}",
    )
