"""Real PostgreSQL coverage for durable TaskRun Cancel commands."""

from __future__ import annotations

import asyncio
from os import environ
from uuid import UUID

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege
from pydantic import SecretStr
from tests.integration.test_task_execution_hosts_pg import (
    DATABASE_URL,
    SeededCaseVersion,
    _build_aggregate,
    _seed_published_case_version,
)
from tests.integration.test_task_orchestration_pg import _persist_sealed_aggregate

from atlas_testops.application.access import ActorContext
from atlas_testops.application.task_commands import TaskRunCommandService
from atlas_testops.application.task_orchestration import TaskWorkerService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.task import (
    ExecutionLifecycle,
    ExecutionQuality,
    RequestTaskRunCancel,
    RequestTaskRunPause,
    RequestTaskRunResume,
    TaskRunCommandStatus,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository
from atlas_testops.orchestration.task_intents import TaskRunWorkflowInput
from atlas_testops.orchestration.tasks import (
    TaskBatchPrepareInput,
    TaskRunFinishInput,
    UnitAttemptWorkflowInput,
)

OWNER_DATABASE_URL = environ.get("ATLAS_TEST_OWNER_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None or OWNER_DATABASE_URL is None,
        reason="Task command PostgreSQL URLs are not configured",
    ),
]


def test_cancel_acceptance_closure_and_dispatch_reconciliation_are_durable() -> None:
    assert DATABASE_URL is not None
    assert OWNER_DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)

    asyncio.run(_exercise_cancel_flow(settings, seeded))


def test_pause_resume_and_cancel_supersession_are_durable() -> None:
    assert DATABASE_URL is not None
    assert OWNER_DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)

    asyncio.run(_exercise_pause_resume_flow(settings, seeded))


async def _exercise_pause_resume_flow(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    database = Database(settings)
    repository = TaskRunRepository()
    command_ids: list[UUID] = []
    await database.open()
    try:
        aggregate = await _persist_sealed_aggregate(
            database,
            _build_aggregate(seeded),
        )
        run = aggregate.run
        assert run.request_digest is not None
        actor = ActorContext(
            tenant_id=run.tenant_id,
            actor_id=run.requested_by,
            request_id=f"task-pause-resume:{run.id}",
            development_override=True,
        )
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
        attempt = UnitAttemptWorkflowInput(
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
            TaskBatchPrepareInput(request=root, attempts=(attempt,))
        )
        assert prepared.status == "AUTHORIZED"
        assert (await worker.start_attempt(attempt)).status == "READY"

        context = DatabaseContext(
            tenant_id=run.tenant_id,
            actor_id=run.requested_by,
            request_id=f"task-pause-resume-state:{run.id}",
        )
        async with database.transaction(context) as connection:
            running = await repository.get_run(connection, run.id)
        assert running is not None
        assert running.lifecycle is ExecutionLifecycle.RUNNING

        commands = TaskRunCommandService(database)
        pause_request = RequestTaskRunPause(
            client_mutation_id=f"pause-{run.id}"
        )
        pause = await commands.pause(
            actor,
            run.id,
            pause_request,
            expected_revision=running.revision,
            idempotency_key=pause_request.client_mutation_id,
        )
        command_ids.append(pause.value.id)
        assert pause.value.status is TaskRunCommandStatus.PENDING
        assert (await worker.checkpoint_control(root)).state == "PAUSED"
        applied_pause = await commands.get(
            actor,
            task_run_id=run.id,
            command_id=pause.value.id,
        )
        assert applied_pause.status is TaskRunCommandStatus.APPLIED

        async with database.transaction(context) as connection:
            paused = await repository.get_run(connection, run.id)
        assert paused is not None
        assert paused.lifecycle is ExecutionLifecycle.PAUSED
        resume_request = RequestTaskRunResume(
            client_mutation_id=f"resume-{run.id}"
        )
        resume = await commands.resume(
            actor,
            run.id,
            resume_request,
            expected_revision=paused.revision,
            idempotency_key=resume_request.client_mutation_id,
        )
        command_ids.append(resume.value.id)
        assert resume.value.status is TaskRunCommandStatus.PENDING
        assert (await worker.checkpoint_control(root)).state == "DISPATCHABLE"
        applied_resume = await commands.get(
            actor,
            task_run_id=run.id,
            command_id=resume.value.id,
        )
        assert applied_resume.status is TaskRunCommandStatus.APPLIED

        async with database.transaction(context) as connection:
            resumed = await repository.get_run(connection, run.id)
        assert resumed is not None
        assert resumed.lifecycle is ExecutionLifecycle.RUNNING
        second_pause_request = RequestTaskRunPause(
            client_mutation_id=f"pause-again-{run.id}"
        )
        second_pause = await commands.pause(
            actor,
            run.id,
            second_pause_request,
            expected_revision=resumed.revision,
            idempotency_key=second_pause_request.client_mutation_id,
        )
        command_ids.append(second_pause.value.id)
        cancel_request = RequestTaskRunCancel(
            client_mutation_id=f"cancel-after-pause-{run.id}"
        )
        cancel = await commands.cancel(
            actor,
            run.id,
            cancel_request,
            expected_revision=second_pause.value.accepted_run_revision,
            idempotency_key=cancel_request.client_mutation_id,
        )
        command_ids.append(cancel.value.id)
        superseded = await commands.get(
            actor,
            task_run_id=run.id,
            command_id=second_pause.value.id,
        )
        assert superseded.status is TaskRunCommandStatus.SUPERSEDED
        assert superseded.superseded_by_command_id == cancel.value.id

        async with database.transaction(context) as connection:
            canceled = await repository.get_run(connection, run.id)
            events = await repository.list_events(
                connection,
                task_run_id=run.id,
                after_seq=0,
                limit=100,
            )
        assert canceled is not None
        assert canceled.lifecycle is ExecutionLifecycle.CANCELING
        assert {
            "task_run.pause_requested",
            "task_run.paused",
            "task_run.resume_requested",
            "task_run.resumed",
            "task_run.cancel_requested",
        } <= {event.event_type for event in events}
    finally:
        await database.close()
        for command_id in command_ids:
            await asyncio.to_thread(_delete_test_command, command_id)


async def _exercise_cancel_flow(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    database = Database(settings)
    repository = TaskRunRepository()
    command_id: UUID | None = None
    await database.open()
    try:
        aggregate = await _persist_sealed_aggregate(
            database,
            _build_aggregate(seeded),
        )
        run = aggregate.run
        assert run.request_digest is not None
        assert run.temporal_namespace is not None
        actor = ActorContext(
            tenant_id=run.tenant_id,
            actor_id=run.requested_by,
            request_id=f"task-command:{run.id}",
            development_override=True,
        )
        service = TaskRunCommandService(database)
        request = RequestTaskRunCancel(client_mutation_id=f"cancel-{run.id}")

        accepted = await service.cancel(
            actor,
            run.id,
            request,
            expected_revision=run.revision,
            idempotency_key=request.client_mutation_id,
        )
        command_id = accepted.value.id
        assert accepted.replayed is False
        assert accepted.value.status is TaskRunCommandStatus.PENDING
        replay = await service.cancel(
            actor,
            run.id,
            request,
            expected_revision=run.revision,
            idempotency_key=request.client_mutation_id,
        )
        assert replay.replayed is True
        assert replay.value.id == accepted.value.id

        root = TaskRunWorkflowInput(
            tenant_id=str(run.tenant_id),
            project_id=str(run.project_id),
            task_run_id=str(run.id),
            request_digest=run.request_digest,
            manifest_hash=run.manifest_hash,
        )
        worker = TaskWorkerService(database)
        plan = await worker.load_dispatch_plan(root)
        assert plan.cancel_requested is True
        assert len(plan.units) == 1
        result = await worker.finish_run(
            TaskRunFinishInput(
                request=root,
                outcomes=(),
                cancel_requested=True,
                skipped_units=1,
            )
        )
        assert result.status == "CANCELED"

        await asyncio.to_thread(
            _claim_and_reconcile_closed_command,
            run.temporal_namespace,
            accepted.value.id,
        )
        applied = await service.get(
            actor,
            task_run_id=run.id,
            command_id=accepted.value.id,
        )
        assert applied.status is TaskRunCommandStatus.APPLIED
        assert applied.applied_at is not None
        assert applied.last_error_code is None

        context = DatabaseContext(
            tenant_id=run.tenant_id,
            actor_id=run.requested_by,
            request_id=f"task-command-verify:{run.id}",
        )
        async with database.transaction(context) as connection:
            stored_run = await repository.get_run(connection, run.id)
            events = await repository.list_events(
                connection,
                task_run_id=run.id,
                after_seq=0,
                limit=100,
            )
        assert stored_run is not None
        assert stored_run.lifecycle is ExecutionLifecycle.CLOSED
        assert stored_run.quality is ExecutionQuality.CANCELED
        assert any(event.event_type == "task_run.cancel_requested" for event in events)

        hidden_actor = ActorContext(
            tenant_id=seeded.other_tenant_id,
            actor_id=None,
            request_id=f"task-command-hidden:{run.id}",
            development_override=True,
        )
        with pytest.raises(ApplicationError) as hidden:
            await service.get(
                hidden_actor,
                task_run_id=run.id,
                command_id=accepted.value.id,
            )
        assert hidden.value.error_code is ErrorCode.NOT_FOUND

        with pytest.raises(InsufficientPrivilege):
            async with database.transaction(context) as connection:
                await connection.execute(
                    """
                    update atlas.task_run_command_intent
                    set status = 'FAILED'
                    where id = %s
                    """,
                    (accepted.value.id,),
                )
    finally:
        await database.close()
        if command_id is not None:
            await asyncio.to_thread(_delete_test_command, command_id)


def _claim_and_reconcile_closed_command(namespace: str, command_id: UUID) -> None:
    assert OWNER_DATABASE_URL is not None
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute("set session authorization atlas_dispatcher")
        claimed = connection.execute(
            """
            select id, claim_token, dispatch_revision
            from atlas.claim_task_run_command_intents(
              'task-command-integration', %s, 100, 30
            )
            """,
            (namespace,),
        ).fetchall()
        command = next(row for row in claimed if row[0] == command_id)
        reconciled = connection.execute(
            """
            select atlas.fail_task_run_command_intent(%s, %s, %s, %s)
            """,
            (
                command[0],
                command[1],
                command[2],
                "TEMPORAL_WORKFLOW_NOT_RUNNING",
            ),
        ).fetchone()
        assert reconciled == (True,)


def _delete_test_command(command_id: UUID) -> None:
    """Remove only this test fact so migration tests retain database isolation."""

    assert OWNER_DATABASE_URL is not None
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute(
            "alter table atlas.task_run_command_intent "
            "disable trigger task_run_command_prevent_delete"
        )
        try:
            deleted = connection.execute(
                "delete from atlas.task_run_command_intent where id = %s",
                (command_id,),
            ).rowcount
            assert deleted == 1
        finally:
            connection.execute(
                "alter table atlas.task_run_command_intent "
                "enable trigger task_run_command_prevent_delete"
            )
