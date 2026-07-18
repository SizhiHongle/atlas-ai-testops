"""Real PostgreSQL coverage for the durable Task orchestration service."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import UUID

import pytest
from psycopg.errors import InsufficientPrivilege, RaiseException
from pydantic import SecretStr
from tests.integration.test_task_execution_hosts_pg import (
    DATABASE_URL,
    SeededCaseVersion,
    TaskAggregate,
    _build_aggregate,
    _seed_published_case_version,
)

from atlas_testops.application.access import ActorContext
from atlas_testops.application.task_orchestration import TaskWorkerService
from atlas_testops.application.task_reruns import TaskRunRerunService
from atlas_testops.core.config import Settings
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.domain.task import (
    TASK_RUN_MANIFEST_SCHEMA_VERSION,
    ExecutionLifecycle,
    ExecutionQuality,
    RequestTaskRunInfraFailureRerun,
    TaskRetryPolicy,
    TaskRunManifest,
    TaskRunRerunSelectionMode,
    task_plan_version_content_digest,
    task_retry_policy_digest,
    task_run_manifest_hash,
    task_unit_execution_ticket_digest,
    unit_retry_attempt_id,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_execution_tickets import (
    TaskExecutionTicketRepository,
)
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
    TaskProfileRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    TaskRunRepository,
)
from atlas_testops.orchestration.task_intents import TaskRunWorkflowInput
from atlas_testops.orchestration.tasks import (
    TaskAttemptBatchSettleInput,
    TaskAttemptExecutionPayload,
    TaskAttemptFinishInput,
    TaskRunFinishInput,
    UnitAttemptWorkflowInput,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]

_WRONG_REQUEST_DIGEST = f"sha256:{'0' * 64}"
_WRONG_MANIFEST_HASH = f"sha256:{'9' * 64}"


def _with_retry_policy(
    aggregate: TaskAggregate,
    *,
    infra_retry_attempts: int = 1,
    max_total_infra_retries: int = 1,
) -> TaskAggregate:
    retry_digest = task_retry_policy_digest(
        infra_retry_attempts=infra_retry_attempts,
        max_total_infra_retries=max_total_infra_retries,
        initial_backoff_seconds=1,
        maximum_backoff_seconds=5,
        jitter_percent=0,
    )
    retry_policy = TaskRetryPolicy(
        infra_retry_attempts=infra_retry_attempts,
        max_total_infra_retries=max_total_infra_retries,
        initial_backoff_seconds=1,
        maximum_backoff_seconds=5,
        jitter_percent=0,
        content_digest=retry_digest,
    )
    policy_digests = {
        **aggregate.version.policy_digests,
        "infra-retry": retry_digest,
    }
    version = aggregate.version.model_copy(
        update={
            "policy_digests": policy_digests,
            "content_digest": task_plan_version_content_digest(
                tenant_id=aggregate.version.tenant_id,
                project_id=aggregate.version.project_id,
                task_plan_id=aggregate.version.task_plan_id,
                version=aggregate.version.version,
                pinned_case_version_ids=aggregate.version.pinned_case_version_ids,
                matrix=aggregate.version.matrix,
                profile_refs=aggregate.version.profile_refs,
                policy_digests=policy_digests,
            ),
        }
    )
    manifest = aggregate.manifest
    manifest_hash = task_run_manifest_hash(
        task_run_id=manifest.task_run_id,
        task_plan_version_id=manifest.task_plan_version_id,
        trigger_source=manifest.trigger_source,
        trigger_fingerprint=manifest.trigger_fingerprint,
        tenant_id=manifest.tenant_id,
        project_id=manifest.project_id,
        iteration_id=manifest.iteration_id,
        units=manifest.units,
        policy_digests=policy_digests,
        compiler_version=manifest.compiler_version,
        schema_version=TASK_RUN_MANIFEST_SCHEMA_VERSION,
        retry_policy=retry_policy,
    )
    current_manifest = TaskRunManifest(
        **manifest.model_dump(
            mode="python",
            exclude={
                "schema_version",
                "policy_digests",
                "retry_policy",
                "manifest_hash",
            },
        ),
        schema_version=TASK_RUN_MANIFEST_SCHEMA_VERSION,
        policy_digests=policy_digests,
        retry_policy=retry_policy,
        manifest_hash=manifest_hash,
    )
    return replace(
        aggregate,
        version=version,
        manifest=current_manifest,
        run=aggregate.run.model_copy(
            update={
                "manifest_hash": manifest_hash,
                "request_digest": current_manifest.recompute_request_digest(),
            }
        ),
        unit=aggregate.unit.model_copy(update={"manifest_hash": manifest_hash}),
        attempt=aggregate.attempt.model_copy(update={"manifest_hash": manifest_hash}),
    )


def test_task_worker_service_persists_safe_exact_replay_chain() -> None:
    """Persist load/start/finish through atlas_app semantics without ever claiming PASSED."""

    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)

    asyncio.run(_exercise_task_worker_service(settings, seeded))


def test_task_worker_service_retries_exact_infrastructure_failure() -> None:
    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)

    asyncio.run(_exercise_infrastructure_retry(settings, seeded))


def test_task_run_reruns_only_closed_infrastructure_failures() -> None:
    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)

    asyncio.run(_exercise_manual_infrastructure_rerun(settings, seeded))


async def _exercise_manual_infrastructure_rerun(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    database = Database(settings)
    repository = TaskRunRepository()
    aggregate = _with_retry_policy(
        _build_aggregate(seeded),
        infra_retry_attempts=0,
        max_total_infra_retries=0,
    )
    await database.open()
    try:
        aggregate = await _persist_sealed_aggregate(database, aggregate)
        worker = TaskWorkerService(database)
        assert aggregate.run.request_digest is not None
        root = TaskRunWorkflowInput(
            tenant_id=str(aggregate.run.tenant_id),
            project_id=str(aggregate.run.project_id),
            task_run_id=str(aggregate.run.id),
            request_digest=aggregate.run.request_digest,
            manifest_hash=aggregate.run.manifest_hash,
        )
        dispatch = (await worker.load_dispatch_plan(root)).units[0]
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
        await worker.prepare_attempt(attempt)
        await worker.start_attempt(attempt)
        infra_outcome = await worker.finish_attempt(
            TaskAttemptFinishInput(
                attempt=attempt,
                execution=TaskAttemptExecutionPayload(
                    status="INFRA_ERROR",
                    error_code="TASK_BROWSER_HOST_UNAVAILABLE",
                ),
            )
        )
        settlement = await worker.settle_attempt_batch(
            TaskAttemptBatchSettleInput(request=root, outcomes=(infra_outcome,))
        )
        assert settlement.retry_attempts == ()
        assert settlement.final_outcomes == (infra_outcome,)
        await worker.finish_run(
            TaskRunFinishInput(
                request=root,
                outcomes=(infra_outcome,),
                cancel_requested=False,
                skipped_units=0,
            )
        )

        context = DatabaseContext(
            tenant_id=aggregate.run.tenant_id,
            actor_id=aggregate.run.requested_by,
            request_id=f"task-rerun-source:{aggregate.run.id}",
        )
        async with database.transaction(context) as connection:
            source = await repository.get_run(connection, aggregate.run.id)
        assert source is not None
        assert source.lifecycle is ExecutionLifecycle.CLOSED
        actor = ActorContext(
            tenant_id=aggregate.run.tenant_id,
            actor_id=aggregate.run.requested_by,
            request_id=f"task-rerun-create:{aggregate.run.id}",
            development_override=True,
        )
        request = RequestTaskRunInfraFailureRerun(
            client_mutation_id="integration-infra-rerun-001"
        )
        reruns = TaskRunRerunService(database)
        created = await reruns.rerun_infrastructure_failures(
            actor,
            source.id,
            request,
            expected_revision=source.revision,
            idempotency_key=request.client_mutation_id,
        )
        replay = await reruns.rerun_infrastructure_failures(
            actor,
            source.id,
            request,
            expected_revision=source.revision,
            idempotency_key=request.client_mutation_id,
        )
        assert created.status_code == 201
        assert replay.status_code == 200
        assert replay.value.id == created.value.id
        assert created.value.rerun_of_task_run_id == source.id
        assert (
            created.value.rerun_selection_mode
            is TaskRunRerunSelectionMode.INFRA_FAILURES
        )

        async with database.transaction(context) as connection:
            child_manifest = await repository.get_manifest(
                connection,
                created.value.id,
            )
            child_units = await repository.list_units(connection, created.value.id)
            child_attempts = await repository.list_first_attempts(
                connection,
                created.value.id,
            )
            start_intent = (
                await TaskExecutionStateRepository().get_workflow_start_intent(
                    connection,
                    owner_kind="TASK_RUN",
                    owner_id=created.value.id,
                )
            )
        assert child_manifest is not None
        assert len(child_manifest.units) == len(child_units) == len(child_attempts) == 1
        assert child_units[0].quality is ExecutionQuality.PENDING
        assert child_units[0].unit_key == aggregate.unit.unit_key
        assert child_attempts[0].attempt_number == 1
        assert start_intent is not None
        assert start_intent.task_run_id == created.value.id
    finally:
        await database.close()


async def _exercise_infrastructure_retry(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    database = Database(settings)
    repository = TaskRunRepository()
    aggregate = _with_retry_policy(_build_aggregate(seeded))
    await database.open()
    try:
        aggregate = await _persist_sealed_aggregate(database, aggregate)
        service = TaskWorkerService(database)
        request_digest = aggregate.run.request_digest
        assert request_digest is not None
        root = TaskRunWorkflowInput(
            tenant_id=str(aggregate.run.tenant_id),
            project_id=str(aggregate.run.project_id),
            task_run_id=str(aggregate.run.id),
            request_digest=request_digest,
            manifest_hash=aggregate.run.manifest_hash,
        )
        first_dispatch = (await service.load_dispatch_plan(root)).units[0]
        first_request = UnitAttemptWorkflowInput(
            tenant_id=root.tenant_id,
            project_id=root.project_id,
            task_run_id=root.task_run_id,
            request_digest=root.request_digest,
            manifest_hash=root.manifest_hash,
            ordinal=first_dispatch.ordinal,
            execution_unit_id=first_dispatch.execution_unit_id,
            unit_attempt_id=first_dispatch.unit_attempt_id,
            execution_deadline=first_dispatch.execution_deadline,
            activity_timeout_seconds=first_dispatch.activity_timeout_seconds,
        )
        await service.prepare_attempt(first_request)
        assert (await service.start_attempt(first_request)).status == "READY"
        infra_outcome = await service.finish_attempt(
            TaskAttemptFinishInput(
                attempt=first_request,
                execution=TaskAttemptExecutionPayload(
                    status="INFRA_ERROR",
                    error_code="TASK_BROWSER_HOST_UNAVAILABLE",
                ),
            )
        )
        first_settlement = await service.settle_attempt_batch(
            TaskAttemptBatchSettleInput(
                request=root,
                outcomes=(infra_outcome,),
            )
        )
        assert first_settlement.final_outcomes == ()
        assert len(first_settlement.retry_attempts) == 1
        retry_dispatch = first_settlement.retry_attempts[0]
        assert UUID(retry_dispatch.unit_attempt_id) == unit_retry_attempt_id(
            execution_unit_id=aggregate.unit.id,
            attempt_number=2,
        )
        retry_request = UnitAttemptWorkflowInput(
            tenant_id=root.tenant_id,
            project_id=root.project_id,
            task_run_id=root.task_run_id,
            request_digest=root.request_digest,
            manifest_hash=root.manifest_hash,
            ordinal=retry_dispatch.ordinal,
            execution_unit_id=retry_dispatch.execution_unit_id,
            unit_attempt_id=retry_dispatch.unit_attempt_id,
            execution_deadline=retry_dispatch.execution_deadline,
            activity_timeout_seconds=retry_dispatch.activity_timeout_seconds,
        )
        with pytest.raises(RuntimeError, match="TASK_ATTEMPT_NOT_READY"):
            await service.prepare_attempt(retry_request)
        await asyncio.sleep(1.1)
        await service.prepare_attempt(retry_request)
        assert (await service.start_attempt(retry_request)).status == "READY"
        retry_outcome = await service.finish_attempt(
            TaskAttemptFinishInput(
                attempt=retry_request,
                execution=TaskAttemptExecutionPayload(status="EXECUTED_UNSEALED"),
            )
        )
        final_settlement = await service.settle_attempt_batch(
            TaskAttemptBatchSettleInput(
                request=root,
                outcomes=(retry_outcome,),
            )
        )
        assert final_settlement.final_outcomes == (retry_outcome,)
        result = await service.finish_run(
            TaskRunFinishInput(
                request=root,
                outcomes=(retry_outcome,),
                cancel_requested=False,
                skipped_units=0,
            )
        )
        assert result.status == "FINISHED_UNSEALED"

        context = DatabaseContext(
            tenant_id=aggregate.run.tenant_id,
            request_id=f"task-retry-verify:{aggregate.run.id}",
        )
        async with database.transaction(context) as connection:
            attempts = await repository.list_attempts(connection, aggregate.unit.id)
            stored_unit = await repository.get_unit(connection, aggregate.unit.id)
        assert tuple(item.attempt_number for item in attempts) == (1, 2)
        assert attempts[0].quality is ExecutionQuality.INFRA_ERROR
        assert attempts[1].quality is ExecutionQuality.INCONCLUSIVE
        assert stored_unit is not None
        assert stored_unit.lifecycle is ExecutionLifecycle.CLOSED
        assert stored_unit.quality is ExecutionQuality.INCONCLUSIVE
    finally:
        await database.close()


async def _exercise_task_worker_service(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    database = Database(settings)
    repository = TaskRunRepository()
    aggregate = _build_aggregate(seeded)
    await database.open()
    try:
        role_context = DatabaseContext(
            tenant_id=seeded.tenant_id,
            request_id="task-worker-role-check",
        )
        async with database.transaction(role_context) as connection:
            cursor = await connection.execute("select current_user as role_name")
            role_row = await cursor.fetchone()
            assert role_row is not None and role_row["role_name"] == "atlas_app"

        aggregate = await _persist_sealed_aggregate(database, aggregate)
        service = TaskWorkerService(database)
        request_digest = aggregate.run.request_digest
        assert request_digest is not None
        root_request = TaskRunWorkflowInput(
            tenant_id=str(seeded.tenant_id),
            project_id=str(seeded.project_id),
            task_run_id=str(aggregate.run.id),
            request_digest=request_digest,
            manifest_hash=aggregate.run.manifest_hash,
        )

        plan = await service.load_dispatch_plan(root_request)
        assert await service.load_dispatch_plan(root_request) == plan
        assert plan.task_run_id == root_request.task_run_id
        assert len(plan.units) == 1
        dispatch = plan.units[0]
        assert dispatch.ordinal == 1
        assert dispatch.execution_unit_id == str(aggregate.unit.id)
        assert dispatch.unit_attempt_id == str(aggregate.attempt.id)
        assert dispatch.execution_deadline == aggregate.attempt.execution_deadline.isoformat()
        assert 1 <= dispatch.activity_timeout_seconds <= 3_600

        invalid_roots = (
            replace(root_request, tenant_id=str(seeded.other_tenant_id)),
            replace(root_request, request_digest=_WRONG_REQUEST_DIGEST),
            replace(root_request, manifest_hash=_WRONG_MANIFEST_HASH),
        )
        for invalid_root in invalid_roots:
            with pytest.raises(RuntimeError, match="TASK_ROOT_IDENTITY_MISMATCH"):
                await service.load_dispatch_plan(invalid_root)

        attempt_request = UnitAttemptWorkflowInput(
            tenant_id=root_request.tenant_id,
            project_id=root_request.project_id,
            task_run_id=root_request.task_run_id,
            request_digest=root_request.request_digest,
            manifest_hash=root_request.manifest_hash,
            ordinal=dispatch.ordinal,
            execution_unit_id=dispatch.execution_unit_id,
            unit_attempt_id=dispatch.unit_attempt_id,
            execution_deadline=dispatch.execution_deadline,
            activity_timeout_seconds=dispatch.activity_timeout_seconds,
        )
        prepared = await service.prepare_attempt(attempt_request)
        assert prepared.attempt == attempt_request
        assert await service.prepare_attempt(attempt_request) == prepared
        assert "password" not in str(prepared).casefold()
        assert "credential" not in str(prepared).casefold()
        ticket_repository = TaskExecutionTicketRepository()
        ticket_context = DatabaseContext(
            tenant_id=seeded.tenant_id,
            request_id=f"task-worker-ticket:{aggregate.attempt.id}",
        )
        async with database.transaction(ticket_context) as connection:
            stored_prepared_ticket = await ticket_repository.get(
                connection,
                UUID(prepared.ticket_id),
            )
        assert stored_prepared_ticket is not None

        tampered_values = stored_prepared_ticket.model_dump(
            mode="python",
            by_alias=False,
            exclude={"id", "schema_version", "ticket_digest", "created_at"},
        )
        tampered_values["environment_revision"] += 1
        tampered_digest = task_unit_execution_ticket_digest(**tampered_values)
        with pytest.raises(RaiseException, match="exact stored dependencies"):
            async with database.transaction(ticket_context) as connection:
                cursor = await connection.execute(
                    "select transaction_timestamp() as observed_at"
                )
                row = await cursor.fetchone()
                assert row is not None
                tampered = stored_prepared_ticket.model_copy(
                    update={
                        "id": new_entity_id(),
                        "environment_revision": (
                            stored_prepared_ticket.environment_revision + 1
                        ),
                        "ticket_digest": tampered_digest,
                        "created_at": row["observed_at"],
                    }
                )
                await ticket_repository.create(connection, tampered)
        started = await service.start_attempt(attempt_request)
        assert started.status == "READY"
        assert await service.start_attempt(attempt_request) == started

        finish_attempt = TaskAttemptFinishInput(
            attempt=attempt_request,
            execution=TaskAttemptExecutionPayload(status="EXECUTED_UNSEALED"),
        )
        attempt_result = await service.finish_attempt(finish_attempt)
        assert attempt_result.status == "FINISHED_UNSEALED"
        assert attempt_result.error_code == "TASK_ATTEMPT_RESULT_UNSEALED"
        assert await service.finish_attempt(finish_attempt) == attempt_result

        finish_run = TaskRunFinishInput(
            request=root_request,
            outcomes=(attempt_result,),
            cancel_requested=False,
            skipped_units=0,
        )
        run_result = await service.finish_run(finish_run)
        assert run_result.status == "FINISHED_UNSEALED"
        assert run_result.completed_units == 1
        assert run_result.failed_units == 0
        assert await service.finish_run(finish_run) == run_result

        context = DatabaseContext(
            tenant_id=seeded.tenant_id,
            request_id=f"task-worker-verify:{aggregate.run.id}",
        )
        async with database.transaction(context) as connection:
            stored_run = await repository.get_run(connection, aggregate.run.id)
            stored_unit = await repository.get_unit(connection, aggregate.unit.id)
            stored_attempt = await repository.get_attempt(connection, aggregate.attempt.id)
            stored_ticket = await TaskExecutionTicketRepository().get(
                connection,
                UUID(prepared.ticket_id),
            )
            events = await repository.list_events(
                connection,
                task_run_id=aggregate.run.id,
                after_seq=0,
                limit=100,
            )

        assert stored_run is not None
        assert stored_unit is not None
        assert stored_attempt is not None
        assert stored_ticket is not None
        assert stored_ticket.ticket_digest == prepared.ticket_digest
        assert stored_ticket.unit_attempt_id == aggregate.attempt.id
        cross_tenant_context = DatabaseContext(
            tenant_id=seeded.other_tenant_id,
            request_id=f"task-worker-ticket-cross-tenant:{aggregate.attempt.id}",
        )
        async with database.transaction(cross_tenant_context) as connection:
            assert await ticket_repository.get(
                connection,
                stored_ticket.id,
            ) is None

        with pytest.raises(InsufficientPrivilege):
            async with database.transaction(context) as connection:
                await connection.execute(
                    """
                    update atlas.task_unit_execution_ticket
                    set ticket_digest = %s
                    where id = %s
                    """,
                    (_WRONG_REQUEST_DIGEST, stored_ticket.id),
                )
        assert all(
            projection.lifecycle is ExecutionLifecycle.CLOSED
            for projection in (stored_run, stored_unit, stored_attempt)
        )
        assert all(
            projection.quality is ExecutionQuality.INCONCLUSIVE
            for projection in (stored_run, stored_unit, stored_attempt)
        )
        assert all(
            projection.quality is not ExecutionQuality.PASSED
            for projection in (stored_run, stored_unit, stored_attempt)
        )
        assert tuple(event.seq for event in events) == tuple(range(1, 10))
        assert tuple(event.event_type for event in events) == (
            "task_run.started",
            "execution_unit.started",
            "unit_attempt.started",
            "unit_attempt.finalized",
            "unit_attempt.closed",
            "execution_unit.finalized",
            "execution_unit.closed",
            "task_run.finalized",
            "task_run.closed",
        )
        assert all(event.quality is not ExecutionQuality.PASSED for event in events)
    finally:
        await database.close()


async def _persist_sealed_aggregate(
    database: Database,
    aggregate: TaskAggregate,
) -> TaskAggregate:
    repository = TaskRunRepository()
    profile_repository = TaskProfileRepository()
    context = DatabaseContext(
        tenant_id=aggregate.run.tenant_id,
        actor_id=aggregate.run.requested_by,
        request_id=f"task-worker-seed:{aggregate.run.id}",
    )
    async with database.transaction(context) as connection:
        assert (
            await repository.create_task_plan(connection, aggregate.plan)
        ).kind is ImmutableCreateKind.CREATED
        assert (
            await profile_repository.create_execution_profile_version(
                connection,
                aggregate.execution_profile,
            )
        ).kind is ImmutableCreateKind.CREATED
        assert (
            await profile_repository.create_identity_profile_version(
                connection,
                aggregate.identity_profile,
            )
        ).kind is ImmutableCreateKind.CREATED
        assert (
            await profile_repository.create_browser_profile_version(
                connection,
                aggregate.browser_profile,
            )
        ).kind is ImmutableCreateKind.CREATED
        assert (
            await profile_repository.create_data_profile_version(
                connection,
                aggregate.data_profile,
            )
        ).kind is ImmutableCreateKind.CREATED
        assert (
            await repository.create_task_plan_version(connection, aggregate.version)
        ).kind is ImmutableCreateKind.CREATED
        created = await repository.create_run(
            connection,
            task_run=aggregate.run,
            manifest=aggregate.manifest,
            units=(aggregate.unit,),
            first_attempts=(aggregate.attempt,),
        )
        assert created.kind is ImmutableCreateKind.CREATED
        assert created.task_run.materialization_state.value == "SEALED"
    return replace(aggregate, run=created.task_run)
