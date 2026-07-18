"""Real PostgreSQL proof for recoverable large TaskRun materialization."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from os import environ
from uuid import UUID, uuid7

import psycopg
import pytest
from pydantic import SecretStr
from tests.integration.test_task_execution_hosts_pg import (
    TaskAggregate,
    _build_aggregate,
    _seed_published_case_version,
)

from atlas_testops.application.task_orchestration import TaskWorkerService
from atlas_testops.core.config import Settings
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.domain.case import canonical_digest
from atlas_testops.domain.task import (
    ExecutionLifecycle,
    ExecutionUnitManifest,
    TaskMaterializationState,
    TaskRun,
    TaskRunManifest,
    execution_unit_key,
    task_run_manifest_hash,
    task_run_manual_trigger_fingerprint,
    task_run_workflow_id,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_profiles import TaskProfileRepository
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    TaskRunRepository,
)
from atlas_testops.orchestration.task_intents import TaskRunWorkflowInput
from atlas_testops.orchestration.tasks import (
    TaskAttemptBatchSettleInput,
    TaskAttemptWorkflowPayload,
    TaskRunProjectedFinishInput,
)

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")
OWNER_DATABASE_URL = environ.get("ATLAS_TEST_OWNER_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None or OWNER_DATABASE_URL is None,
        reason="Task partition PostgreSQL URLs are not configured",
    ),
]


def test_large_run_materializes_two_exact_partitions_before_one_seal() -> None:
    assert DATABASE_URL is not None
    assert OWNER_DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=4,
    )
    seeded = _seed_published_case_version(settings)
    aggregate = _build_aggregate(seeded)
    run, manifest = _large_run(aggregate.run, aggregate.manifest)

    created = asyncio.run(
        _create_large_root(settings, seeded.actor_id, aggregate, run, manifest)
    )
    assert created.materialization_state is TaskMaterializationState.MATERIALIZING

    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute("set session authorization atlas_dispatcher")
        claims = connection.execute(
            """
            select *
            from atlas.claim_task_run_materialization_partitions(%s, 100, 90)
            """,
            ("partition-integration",),
        ).fetchall()
        target = sorted(
            (row for row in claims if row[3] == run.id),
            key=lambda row: int(row[5]),
        )
        assert [(row[6], row[7]) for row in target] == [(1, 64), (65, 65)]
        sealed_flags: list[bool] = []
        for row in target:
            completed_row = connection.execute(
                """
                select atlas.complete_task_run_materialization_partition(
                  %s, %s, %s, %s
                )
                """,
                (row[0], row[9], row[10], "partition-integration"),
            ).fetchone()
            assert completed_row is not None
            sealed_flags.append(bool(completed_row[0]))
        assert sealed_flags == [False, True]

    asyncio.run(
        _assert_large_run_sealed(
            settings,
            tenant_id=seeded.tenant_id,
            actor_id=seeded.actor_id,
            task_run_id=run.id,
        )
    )
    asyncio.run(
        _cancel_large_run_by_pages(
            settings,
            task_run=run,
        )
    )


def _large_run(
    base_run: TaskRun,
    base_manifest: TaskRunManifest,
) -> tuple[TaskRun, TaskRunManifest]:
    template = base_manifest.units[0]
    candidates: list[ExecutionUnitManifest] = []
    for index in range(65):
        parameter_digest = canonical_digest({"partitionRow": index})
        candidates.append(
            template.model_copy(
                update={
                    "ordinal": 1,
                    "parameter_digest": parameter_digest,
                    "unit_key": execution_unit_key(
                        case_version_id=template.case_version_id,
                        environment_id=template.environment_id,
                        browser_profile_version_id=(
                            template.browser_profile_version_id
                        ),
                        identity_profile_version_id=(
                            template.identity_profile_version_id
                        ),
                        data_profile_version_id=template.data_profile_version_id,
                        parameter_digest=parameter_digest,
                    ),
                }
            )
        )
    units = tuple(
        unit.model_copy(update={"ordinal": ordinal})
        for ordinal, unit in enumerate(
            sorted(candidates, key=lambda item: item.unit_key),
            start=1,
        )
    )
    run_id = new_entity_id()
    trigger_fingerprint = task_run_manual_trigger_fingerprint(
        task_plan_version_id=base_manifest.task_plan_version_id,
        client_mutation_id=f"partitioned-{uuid7().hex}",
    )
    manifest_hash = task_run_manifest_hash(
        task_run_id=run_id,
        task_plan_version_id=base_manifest.task_plan_version_id,
        trigger_source=base_manifest.trigger_source,
        trigger_fingerprint=trigger_fingerprint,
        tenant_id=base_manifest.tenant_id,
        project_id=base_manifest.project_id,
        iteration_id="partition:integration",
        units=units,
        policy_digests=base_manifest.policy_digests,
        compiler_version=base_manifest.compiler_version,
        schema_version=base_manifest.schema_version,
        retry_policy=base_manifest.retry_policy,
    )
    manifest = base_manifest.model_copy(
        update={
            "task_run_id": run_id,
            "trigger_fingerprint": trigger_fingerprint,
            "iteration_id": "partition:integration",
            "units": units,
            "manifest_hash": manifest_hash,
        }
    )
    now = datetime.now(UTC)
    run = base_run.model_copy(
        update={
            "id": run_id,
            "manifest_hash": manifest_hash,
            "trigger_fingerprint": trigger_fingerprint,
            "request_digest": manifest.recompute_request_digest(),
            "materialization_state": TaskMaterializationState.MATERIALIZING,
            "materialized_unit_count": None,
            "materialized_first_attempt_count": None,
            "materialization_sealed_at": None,
            "temporal_workflow_id": task_run_workflow_id(
                tenant_id=base_run.tenant_id,
                task_run_id=run_id,
            ),
            "requested_at": now,
            "queued_at": now,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
    )
    return run, manifest


async def _create_large_root(
    settings: Settings,
    actor_id: UUID,
    aggregate: TaskAggregate,
    run: TaskRun,
    manifest: TaskRunManifest,
) -> TaskRun:
    database = Database(settings)
    tasks = TaskRunRepository()
    profiles = TaskProfileRepository()
    await database.open()
    try:
        async with database.transaction(
            DatabaseContext(
                tenant_id=run.tenant_id,
                actor_id=actor_id,
                request_id=f"partition-create:{run.id}",
            )
        ) as connection:
            await tasks.create_task_plan(connection, aggregate.plan)
            await profiles.create_execution_profile_version(
                connection,
                aggregate.execution_profile,
            )
            await profiles.create_identity_profile_version(
                connection,
                aggregate.identity_profile,
            )
            await profiles.create_browser_profile_version(
                connection,
                aggregate.browser_profile,
            )
            await profiles.create_data_profile_version(
                connection,
                aggregate.data_profile,
            )
            await tasks.create_task_plan_version(connection, aggregate.version)
            result = await tasks.create_partitioned_run(
                connection,
                task_run=run,
                manifest=manifest,
            )
            assert result.kind is ImmutableCreateKind.CREATED
            return result.task_run
    finally:
        await database.close()


async def _assert_large_run_sealed(
    settings: Settings,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    task_run_id: UUID,
) -> None:
    database = Database(settings)
    tasks = TaskRunRepository()
    await database.open()
    try:
        async with database.transaction(
            DatabaseContext(
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=f"partition-check:{task_run_id}",
            )
        ) as connection:
            run = await tasks.get_run(connection, task_run_id)
            units = await tasks.list_units(connection, task_run_id)
            attempts = await tasks.list_first_attempts(
                connection,
                task_run_id,
            )
            assert run is not None
            assert run.materialization_state is TaskMaterializationState.SEALED
            assert run.materialized_unit_count == 65
            assert len(units) == len(attempts) == 65
            assert all(
                attempt.execution_deadline - attempt.queued_at
                == timedelta(days=30)
                for attempt in attempts
            )
            start = await connection.execute(
                """
                select status
                from atlas.task_workflow_start_intent
                where task_run_id = %s
                """,
                (task_run_id,),
            )
            start_row = await start.fetchone()
            assert start_row is not None
            assert start_row["status"] == "PENDING"
    finally:
        await database.close()


async def _cancel_large_run_by_pages(
    settings: Settings,
    *,
    task_run: TaskRun,
) -> None:
    assert task_run.request_digest is not None
    database = Database(settings)
    tasks = TaskRunRepository()
    service = TaskWorkerService(database)
    root = TaskRunWorkflowInput(
        tenant_id=str(task_run.tenant_id),
        project_id=str(task_run.project_id),
        task_run_id=str(task_run.id),
        request_digest=task_run.request_digest,
        manifest_hash=task_run.manifest_hash,
    )
    await database.open()
    try:
        first = await service.load_dispatch_plan(root)
        assert len(first.units) == 64
        assert first.after_ordinal == 0
        assert first.total_units == 65
        assert first.has_more is True
        for offset in range(0, len(first.units), 8):
            outcomes = tuple(
                TaskAttemptWorkflowPayload(
                    execution_unit_id=unit.execution_unit_id,
                    unit_attempt_id=unit.unit_attempt_id,
                    ordinal=unit.ordinal,
                    status="CANCELED",
                    error_code="TASK_RUN_CANCELED_BEFORE_DISPATCH",
                )
                for unit in first.units[offset : offset + 8]
            )
            settled = await service.settle_attempt_batch(
                TaskAttemptBatchSettleInput(
                    request=root,
                    outcomes=outcomes,
                    cancel_requested=True,
                )
            )
            assert settled.state == "SETTLED"
            assert settled.final_outcomes == outcomes

        final_root = replace(root, dispatch_after_ordinal=64)
        final_page = await service.load_dispatch_plan(final_root)
        assert [unit.ordinal for unit in final_page.units] == [65]
        assert final_page.has_more is False
        final_unit = final_page.units[0]
        final_outcome = TaskAttemptWorkflowPayload(
            execution_unit_id=final_unit.execution_unit_id,
            unit_attempt_id=final_unit.unit_attempt_id,
            ordinal=final_unit.ordinal,
            status="CANCELED",
            error_code="TASK_RUN_CANCELED_BEFORE_DISPATCH",
        )
        settled = await service.settle_attempt_batch(
            TaskAttemptBatchSettleInput(
                request=final_root,
                outcomes=(final_outcome,),
                cancel_requested=True,
            )
        )
        assert settled.final_outcomes == (final_outcome,)

        result = await service.finish_partitioned_run(
            TaskRunProjectedFinishInput(
                request=final_root,
                cancel_requested=True,
            )
        )
        assert result.status == "CANCELED"
        assert result.skipped_units == 65
        assert (
            result.completed_units
            + result.failed_units
            + result.inconclusive_units
            + result.canceled_units
            + result.skipped_units
            == 65
        )

        async with database.transaction(
            DatabaseContext(
                tenant_id=task_run.tenant_id,
                request_id=f"partition-finish-check:{task_run.id}",
            )
        ) as connection:
            stored = await tasks.get_run(connection, task_run.id)
            projection = await tasks.get_completion_projection(
                connection,
                task_run.id,
            )
        assert stored is not None
        assert stored.lifecycle is ExecutionLifecycle.CLOSED
        assert projection.total_units == projection.closed_units == 65
        assert projection.total_attempts == projection.closed_attempts == 65
        assert projection.finalized_event_count == 65
        assert projection.finalized_unit_count == 65
        assert projection.skipped_units == 65
        assert projection.invalid_events == 0
    finally:
        await database.close()
