"""Real PostgreSQL coverage for the Task-to-Fixture Cleanup truth bridge."""

from __future__ import annotations

import asyncio
import json
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from os import environ
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID, uuid7

import httpx2
import pytest
from psycopg import AsyncConnection
from psycopg.errors import RaiseException
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from tests.integration.test_task_execution_hosts_pg import (
    SeededCaseVersion,
    TaskAggregate,
    _build_aggregate,
    _seed_published_case_version,
)
from tests.integration.test_task_orchestration_pg import _persist_sealed_aggregate

from atlas_testops.application.access import ActorContext
from atlas_testops.application.fixture_runs import FixtureWorkerService
from atlas_testops.application.insights import InsightService
from atlas_testops.application.result_callback_delivery import (
    TaskGateCallbackDeliveryConsumer,
)
from atlas_testops.application.result_classification import ResultClassificationService
from atlas_testops.application.result_gate import ResultGateService
from atlas_testops.application.result_hygiene import ResultHygieneProjectionService
from atlas_testops.application.result_projection import ResultProjectionService
from atlas_testops.application.result_queries import ResultQueryService
from atlas_testops.application.result_reevaluation import ResultReevaluationService
from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.application.task_orchestration import TaskWorkerService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.fixture import FixtureRun
from atlas_testops.domain.insight import RequestInsightSnapshot
from atlas_testops.domain.result import (
    ClassificationJudgmentState,
    DataHygiene,
    RequestFailureClassificationRevision,
    RequestTaskGateEvaluation,
    RequestTaskResultReevaluation,
    TaskGateVerdict,
    TaskResultSnapshotFinality,
    UnitHygieneInputSource,
    result_projection_digest,
    task_attempt_fixture_execution_id,
)
from atlas_testops.infrastructure.adapters.fixture_registry import (
    FixtureOperationRegistry,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.fixture_runs import FixtureRunRepository
from atlas_testops.infrastructure.repositories.results import ResultFactRepository
from atlas_testops.infrastructure.result_callbacks import (
    HttpTaskGateCallbackSender,
    TaskGateCallbackSigner,
)
from atlas_testops.infrastructure.task_intents import TaskIntentDispatcherDatabase
from atlas_testops.orchestration.task_intents import TaskRunWorkflowInput
from atlas_testops.orchestration.tasks import (
    TaskAttemptBatchSettleInput,
    TaskAttemptExecutionPayload,
    TaskAttemptFinishInput,
    TaskRunFinishInput,
    UnitAttemptWorkflowInput,
)

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


def test_task_fixture_cleanup_projects_exact_hygiene_revision() -> None:
    """Prove Python hashes and PostgreSQL guards agree on a real closed Attempt."""

    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)

    asyncio.run(_exercise_cleanup_truth(settings, seeded))


async def _exercise_cleanup_truth(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    database = Database(settings)
    aggregate = _build_aggregate(seeded)
    hygiene_projection = ResultHygieneProjectionService()
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
        attempt_request = UnitAttemptWorkflowInput(
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
        await worker.prepare_attempt(attempt_request)
        assert (await worker.start_attempt(attempt_request)).status == "READY"

        context = DatabaseContext(
            tenant_id=aggregate.run.tenant_id,
            request_id=f"task-fixture-hygiene:{aggregate.attempt.id}",
        )
        invalid_fixture_id = uuid7()
        with pytest.raises(RaiseException, match="scope or frozen Fixture"):
            async with database.transaction(context) as connection:
                invalid_fixture = await _insert_terminal_fixture(
                    connection,
                    aggregate=aggregate,
                    fixture_run_id=invalid_fixture_id,
                    plan_digest=f"sha256:{'f' * 64}",
                )
                await hygiene_projection.bind_fixture_run(
                    connection,
                    fixture_run=invalid_fixture,
                    created_at=invalid_fixture.requested_at,
                )

        fixture_run_id = uuid7()
        async with database.transaction(context) as connection:
            fixture_run = await _insert_terminal_fixture(
                connection,
                aggregate=aggregate,
                fixture_run_id=fixture_run_id,
            )
            binding = await hygiene_projection.bind_fixture_run(
                connection,
                fixture_run=fixture_run,
                created_at=fixture_run.requested_at,
            )
        assert binding is not None
        assert binding.unit_attempt_id == aggregate.attempt.id
        assert binding.fixture_run_id == fixture_run_id

        outcome = await worker.finish_attempt(
            TaskAttemptFinishInput(
                attempt=attempt_request,
                execution=TaskAttemptExecutionPayload(status="EXECUTED_UNSEALED"),
            )
        )
        assert outcome.status == "FINISHED_UNSEALED"

        settled = await worker.settle_attempt_batch(
            TaskAttemptBatchSettleInput(request=root, outcomes=(outcome,))
        )
        assert settled.final_outcomes == (outcome,)
        run_result = await worker.finish_run(
            TaskRunFinishInput(
                request=root,
                outcomes=(outcome,),
                cancel_requested=False,
                skipped_units=0,
            )
        )
        assert run_result.status == "FINISHED_UNSEALED"
        repository = ResultFactRepository()
        async with database.transaction(context) as connection:
            quality_snapshot = await repository.get_latest_snapshot_for_finality(
                connection,
                aggregate.run.id,
                TaskResultSnapshotFinality.QUALITY_FINAL,
            )
            fully_resolved_snapshot = await repository.get_latest_snapshot_for_finality(
                connection,
                aggregate.run.id,
                TaskResultSnapshotFinality.FULLY_RESOLVED,
            )
        assert quality_snapshot is not None
        assert fully_resolved_snapshot is None

        fixture_worker = FixtureWorkerService(
            database,
            FixtureOperationRegistry(),
            cleanup_grace=timedelta(minutes=1),
            result_hygiene_projection=hygiene_projection,
            result_projection=ResultProjectionService(),
        )
        await fixture_worker.finalize_release(
            aggregate.run.tenant_id,
            fixture_run_id,
            failed_run=False,
        )
        async with database.transaction(context) as connection:
            resolution = await repository.get_latest_hygiene_resolution(
                connection,
                aggregate.unit.id,
            )
            stored_binding = await repository.get_attempt_fixture_binding(
                connection,
                aggregate.attempt.id,
            )
            stored_resolution = await repository.get_latest_hygiene_resolution(
                connection,
                aggregate.unit.id,
            )
            replay = await hygiene_projection.project_unit(
                connection,
                unit=aggregate.unit,
                created_at=datetime.now(UTC),
            )
            fully_resolved_snapshot = await repository.get_latest_snapshot_for_finality(
                connection,
                aggregate.run.id,
                TaskResultSnapshotFinality.FULLY_RESOLVED,
            )
            privileges = await (
                await connection.execute(
                    """
                    select
                      has_table_privilege(
                        current_user,
                        'atlas.attempt_fixture_binding',
                        'UPDATE, DELETE'
                      ) as binding_mutation,
                      has_table_privilege(
                        current_user,
                        'atlas.unit_hygiene_resolution_revision',
                        'UPDATE, DELETE'
                      ) as resolution_mutation
                    """
                )
            ).fetchone()

        assert stored_binding == binding
        assert resolution is not None
        assert stored_resolution == resolution
        assert replay == resolution
        assert resolution.revision == 1
        assert resolution.data_hygiene is DataHygiene.NOT_APPLICABLE
        assert len(resolution.inputs) == 1
        assert resolution.inputs[0].source is UnitHygieneInputSource.FIXTURE_RUN
        assert resolution.inputs[0].fixture_run_id == fixture_run_id
        assert resolution.inputs[0].resource_count == 0
        assert privileges is not None
        assert privileges["binding_mutation"] is False
        assert privileges["resolution_mutation"] is False
        assert fully_resolved_snapshot is not None
        assert quality_snapshot.revision == 1
        assert fully_resolved_snapshot.revision == 2
        assert fully_resolved_snapshot.supersedes_snapshot_id == quality_snapshot.id
        assert fully_resolved_snapshot.unit_hygiene_resolution_revision_ids == (resolution.id,)
        assert fully_resolved_snapshot.input_hygiene_resolution_set_hash is not None
        assert fully_resolved_snapshot.axis_distributions.data_hygiene.not_applicable == 1

        reevaluation_request = RequestTaskResultReevaluation(
            source_snapshot_id=fully_resolved_snapshot.id,
            client_mutation_id="task-fixture-hygiene-reevaluate-001",
        )
        reevaluation_service = ResultReevaluationService(database)
        actor = ActorContext(
            tenant_id=aggregate.run.tenant_id,
            actor_id=uuid7(),
            request_id=f"task-result-reevaluate:{aggregate.run.id}",
            development_override=True,
        )
        reevaluated = await reevaluation_service.reevaluate(
            actor,
            aggregate.run.id,
            reevaluation_request,
            idempotency_key=reevaluation_request.client_mutation_id,
        )
        reevaluated_replay = await reevaluation_service.reevaluate(
            actor,
            aggregate.run.id,
            reevaluation_request,
            idempotency_key=reevaluation_request.client_mutation_id,
        )
        async with database.transaction(context) as connection:
            command = await repository.get_reevaluation_command(
                connection,
                task_run_id=aggregate.run.id,
                client_mutation_id=reevaluation_request.client_mutation_id,
            )
            privileges = await (
                await connection.execute(
                    """
                    select has_table_privilege(
                      current_user,
                      'atlas.task_result_reevaluation_command',
                      'UPDATE, DELETE'
                    ) as command_mutation
                    """
                )
            ).fetchone()

        assert reevaluated.status_code == 201
        assert reevaluated.replayed is False
        assert reevaluated.value.finality is TaskResultSnapshotFinality.REEVALUATED
        assert reevaluated.value.revision == 3
        assert reevaluated.value.reevaluation_source_snapshot_id == fully_resolved_snapshot.id
        assert reevaluated.value.verdict_counts == fully_resolved_snapshot.verdict_counts
        assert reevaluated_replay.value == reevaluated.value
        assert reevaluated_replay.replayed is True
        assert command is not None
        assert command.id == reevaluated.value.reevaluation_command_id
        assert privileges is not None
        assert privileges["command_mutation"] is False

        classification_service = ResultClassificationService(database)
        classification_batch = await classification_service.classify_snapshot(
            actor,
            reevaluated.value.id,
        )
        classification_replay = await classification_service.classify_snapshot(
            actor,
            reevaluated.value.id,
        )
        assert classification_replay == classification_batch
        assert len(classification_batch.clusters) == 1
        assert classification_batch.clusters[0].affected_count == 1
        assert len(classification_batch.classifications) == 1
        baseline_classification = classification_batch.classifications[0]
        review_request = RequestFailureClassificationRevision(
            expected_revision=baseline_classification.revision,
            failure_domain=baseline_classification.failure_domain,
            hypothesis_code=baseline_classification.hypothesis_code,
            hypothesis=baseline_classification.hypothesis,
            confidence=baseline_classification.confidence,
            supporting_evidence_refs=baseline_classification.supporting_evidence_refs,
            contradicting_evidence_refs=baseline_classification.contradicting_evidence_refs,
            evidence_gap_codes=baseline_classification.evidence_gap_codes,
            judgment_state=ClassificationJudgmentState.HUMAN_CONFIRMED,
            client_mutation_id="task-fixture-hygiene-classification-review-001",
        )
        reviewed = await classification_service.revise_classification(
            actor,
            baseline_classification.failure_classification_id,
            review_request,
            idempotency_key=review_request.client_mutation_id,
        )
        reviewed_replay = await classification_service.revise_classification(
            actor,
            baseline_classification.failure_classification_id,
            review_request,
            idempotency_key=review_request.client_mutation_id,
        )
        async with database.transaction(context) as connection:
            classification_rows = await (
                await connection.execute(
                    """
                    select count(*) as revision_count
                    from atlas.failure_classification_revision
                    where failure_classification_id = %s
                    """,
                    (baseline_classification.failure_classification_id,),
                )
            ).fetchone()
            classification_privileges = await (
                await connection.execute(
                    """
                    select
                      has_table_privilege(
                        current_user,
                        'atlas.failure_cluster_revision',
                        'UPDATE, DELETE'
                      ) as cluster_mutation,
                      has_table_privilege(
                        current_user,
                        'atlas.failure_classification_revision',
                        'UPDATE, DELETE'
                      ) as classification_mutation
                    """
                )
            ).fetchone()

        assert reviewed.status_code == 201
        assert reviewed.replayed is False
        assert reviewed.value.revision == 2
        assert reviewed.value.judgment_state is ClassificationJudgmentState.HUMAN_CONFIRMED
        assert reviewed_replay.replayed is True
        assert reviewed_replay.value == reviewed.value
        assert classification_rows is not None
        assert classification_rows["revision_count"] == 2
        assert classification_privileges is not None
        assert classification_privileges["cluster_mutation"] is False
        assert classification_privileges["classification_mutation"] is False

        gate_request = RequestTaskGateEvaluation(
            result_snapshot_id=reevaluated.value.id,
            client_mutation_id="task-fixture-hygiene-gate-evaluation-001",
        )
        gate_service = ResultGateService(database)
        gate = await gate_service.evaluate(
            actor,
            gate_request,
            idempotency_key=gate_request.client_mutation_id,
        )
        gate_replay = await gate_service.evaluate(
            actor,
            gate_request,
            idempotency_key=gate_request.client_mutation_id,
        )
        async with database.transaction(context) as connection:
            gate_rows = await (
                await connection.execute(
                    """
                    select count(*) as decision_count
                    from atlas.task_gate_decision
                    where task_run_id = %s
                    """,
                    (aggregate.run.id,),
                )
            ).fetchone()
            gate_privileges = await (
                await connection.execute(
                    """
                    select has_table_privilege(
                      current_user,
                      'atlas.task_gate_decision',
                      'UPDATE, DELETE'
                    ) as gate_mutation,
                    has_table_privilege(
                      current_user,
                      'atlas.task_gate_callback_intent',
                      'UPDATE, DELETE'
                    ) as callback_mutation
                    """
                )
            ).fetchone()
            callback_row = await (
                await connection.execute(
                    """
                    select event_id, task_gate_decision_id, task_run_id,
                           manifest_hash, gate_decision, status,
                           dispatch_attempts
                    from atlas.task_gate_callback_intent
                    where task_gate_decision_id = %s
                    """,
                    (gate.value.id,),
                )
            ).fetchone()

        assert gate.status_code == 201
        assert gate.replayed is False
        assert gate.value.decision is TaskGateVerdict.INCONCLUSIVE
        assert gate.value.result_snapshot_id == reevaluated.value.id
        assert gate.value.failure_classification_revision_ids == (reviewed.value.id,)
        assert gate_replay.replayed is True
        assert gate_replay.value == gate.value
        assert gate_rows is not None
        assert gate_rows["decision_count"] == 1
        assert gate_privileges is not None
        assert gate_privileges["gate_mutation"] is False
        assert gate_privileges["callback_mutation"] is False
        assert callback_row is not None
        assert callback_row["task_gate_decision_id"] == gate.value.id
        assert callback_row["task_run_id"] == aggregate.run.id
        assert callback_row["manifest_hash"] == aggregate.run.manifest_hash
        assert callback_row["gate_decision"] == gate.value.decision.value
        assert callback_row["status"] == "PENDING"
        assert callback_row["dispatch_attempts"] == 0

        callback_key = b64encode(b"callback-integration-key-value!" * 2).decode()
        signer = TaskGateCallbackSigner.from_base64_key(callback_key)
        delivered_documents: list[dict[str, object]] = []
        callback_statuses = [204]

        async def callback_handler(
            callback_request: httpx2.Request,
        ) -> httpx2.Response:
            document = json.loads(await callback_request.aread())
            delivered_documents.append(document)
            verified = signer.verify(document)
            assert verified.task_run_id == aggregate.run.id
            assert verified.manifest_hash == aggregate.run.manifest_hash
            assert verified.gate_decision is gate.value.decision
            assert callback_request.headers["Idempotency-Key"] == str(
                verified.event_id
            )
            return httpx2.Response(
                callback_statuses.pop(0),
                request=callback_request,
            )

        callback_client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(callback_handler)
        )
        callback_sender = HttpTaskGateCallbackSender(
            callback_url="https://callbacks.test/task-gates",
            signer=signer,
            timeout=timedelta(seconds=5),
            client=callback_client,
        )
        assert settings.database_url_value is not None
        dispatcher = TaskIntentDispatcherDatabase(
            _dispatcher_database_url(settings.database_url_value),
            pool_min_size=1,
            pool_max_size=2,
        )
        await dispatcher.open()
        try:
            async with dispatcher.transaction() as connection:
                dispatcher_privileges = await (
                    await connection.execute(
                        """
                        select has_table_privilege(
                          current_user,
                          'atlas.task_gate_callback_intent',
                          'SELECT, INSERT, UPDATE, DELETE'
                        ) as table_dml
                        """
                    )
                ).fetchone()
            assert dispatcher_privileges is not None
            assert dispatcher_privileges["table_dml"] is False

            callback_consumer = TaskGateCallbackDeliveryConsumer(
                dispatcher,
                callback_sender,
                dispatcher_id="callback-integration",
                batch_size=10,
                lease_duration=timedelta(seconds=30),
                poll_interval=timedelta(seconds=1),
                retry_policy=TaskIntentRetryPolicy(
                    max_attempts=3,
                    initial_backoff=timedelta(milliseconds=100),
                    maximum_backoff=timedelta(milliseconds=100),
                ),
            )
            callback_batch = await callback_consumer.run_once()

            retry_gate_request = gate_request.model_copy(
                update={
                    "client_mutation_id": (
                        "task-fixture-hygiene-gate-evaluation-002"
                    )
                }
            )
            retry_gate = await gate_service.evaluate(
                actor,
                retry_gate_request,
                idempotency_key=retry_gate_request.client_mutation_id,
            )
            callback_statuses.extend((503, 204))
            retry_batch = await callback_consumer.run_once()
            await asyncio.sleep(0.15)
            recovered_batch = await callback_consumer.run_once()

            failed_gate_request = gate_request.model_copy(
                update={
                    "client_mutation_id": (
                        "task-fixture-hygiene-gate-evaluation-003"
                    )
                }
            )
            failed_gate = await gate_service.evaluate(
                actor,
                failed_gate_request,
                idempotency_key=failed_gate_request.client_mutation_id,
            )
            callback_statuses.append(400)
            failed_batch = await callback_consumer.run_once()
        finally:
            await callback_sender.aclose()
            await callback_client.aclose()
            await dispatcher.close()

        assert callback_batch.claimed == callback_batch.delivered == 1
        assert callback_batch.retried == callback_batch.failed == 0
        assert retry_gate.value.revision == 2
        assert retry_batch.claimed == retry_batch.retried == 1
        assert recovered_batch.claimed == recovered_batch.delivered == 1
        assert failed_gate.value.revision == 3
        assert failed_batch.claimed == failed_batch.failed == 1
        assert len(delivered_documents) == 4
        event_ids = [document["eventId"] for document in delivered_documents]
        assert len(set(event_ids)) == 3
        assert event_ids[1] == event_ids[2]
        assert set(delivered_documents[0]) == {
            "eventId",
            "taskRunId",
            "manifestHash",
            "gateDecision",
            "timestamp",
            "signature",
        }
        async with database.transaction(context) as connection:
            callback_rows = await (
                await connection.execute(
                    """
                    select task_gate_decision_id, status, dispatch_attempts,
                           response_status_code, delivered_at, failed_at,
                           last_error_code
                    from atlas.task_gate_callback_intent
                    where task_gate_decision_id = any(%s)
                    order by created_at, event_id
                    """,
                    (
                        [
                            gate.value.id,
                            retry_gate.value.id,
                            failed_gate.value.id,
                        ],
                    ),
                )
            ).fetchall()
        assert len(callback_rows) == 3
        callback_by_decision = {
            row["task_gate_decision_id"]: row for row in callback_rows
        }
        delivered_row = callback_by_decision[gate.value.id]
        recovered_row = callback_by_decision[retry_gate.value.id]
        failed_row = callback_by_decision[failed_gate.value.id]
        assert delivered_row["status"] == recovered_row["status"] == "DELIVERED"
        assert delivered_row["dispatch_attempts"] == 1
        assert recovered_row["dispatch_attempts"] == 2
        assert delivered_row["response_status_code"] == 204
        assert recovered_row["response_status_code"] == 204
        assert delivered_row["delivered_at"] is not None
        assert recovered_row["delivered_at"] is not None
        assert failed_row["status"] == "FAILED"
        assert failed_row["dispatch_attempts"] == 1
        assert failed_row["response_status_code"] == 400
        assert failed_row["failed_at"] is not None
        assert failed_row["last_error_code"] == (
            "TASK_GATE_CALLBACK_HTTP_REJECTED"
        )
        latest_gate = failed_gate

        query_service = ResultQueryService(database)
        task_result = await query_service.get_task_result(
            actor,
            aggregate.run.id,
            snapshot_id=reevaluated.value.id,
        )
        unit_result = await query_service.get_unit_resolution(
            actor,
            aggregate.unit.id,
            revision=None,
        )
        cluster_page = await query_service.list_snapshot_clusters(
            actor,
            reevaluated.value.id,
            cursor=None,
            limit=50,
        )

        assert task_result.result_snapshot == reevaluated.value
        assert task_result.task_gate_decision == latest_gate.value
        assert unit_result.execution_unit_id == aggregate.unit.id
        assert len(cluster_page.items) == 1
        assert cluster_page.items[0].classification == reviewed.value
        assert cluster_page.next_cursor is None

        insight_service = InsightService(database)
        insight_preview = await insight_service.preview(
            actor,
            aggregate.run.project_id,
            window_days=30,
            as_of=None,
        )
        insight_request = RequestInsightSnapshot(
            window_days=30,
            client_mutation_id="task-fixture-hygiene-insight-pin-001",
        )
        insight = await insight_service.pin_snapshot(
            actor,
            aggregate.run.project_id,
            insight_request,
            idempotency_key=insight_request.client_mutation_id,
        )
        insight_replay = await insight_service.pin_snapshot(
            actor,
            aggregate.run.project_id,
            insight_request,
            idempotency_key=insight_request.client_mutation_id,
        )
        loaded_insight = await insight_service.get_snapshot(actor, insight.value.id)
        hidden_actor = ActorContext(
            tenant_id=uuid7(),
            actor_id=uuid7(),
            request_id=f"hidden-insight:{insight.value.id}",
            development_override=True,
        )
        with pytest.raises(ApplicationError) as hidden_error:
            await insight_service.get_snapshot(hidden_actor, insight.value.id)

        async with database.transaction(context) as connection:
            insight_rows = await (
                await connection.execute(
                    """
                    select count(*) as snapshot_count
                    from atlas.insight_snapshot
                    where id = %s
                    """,
                    (insight.value.id,),
                )
            ).fetchone()
            insight_privileges = await (
                await connection.execute(
                    """
                    select has_table_privilege(
                      current_user,
                      'atlas.insight_snapshot',
                      'UPDATE, DELETE'
                    ) as snapshot_mutation
                    """
                )
            ).fetchone()

        assert insight_preview.current.execution_unit_count == 1
        assert insight_preview.dataset_cut.source_snapshot_ids == (
            reevaluated.value.id,
        )
        assert insight_preview.dataset_cut.gate_decision_ids == (
            latest_gate.value.id,
        )
        assert insight_preview.active_risk is not None
        assert insight_preview.active_risk.gate_decision is TaskGateVerdict.INCONCLUSIVE
        assert insight.status_code == 201 and insight.replayed is False
        assert insight_replay.status_code == 200 and insight_replay.replayed is True
        assert insight_replay.value == loaded_insight == insight.value
        assert hidden_error.value.error_code is ErrorCode.NOT_FOUND
        assert insight_rows is not None and insight_rows["snapshot_count"] == 1
        assert insight_privileges is not None
        assert insight_privileges["snapshot_mutation"] is False
    finally:
        await database.close()


def _dispatcher_database_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.hostname is None:
        raise ValueError("test database URL has no hostname")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = f"{quote('atlas_dispatcher')}:{quote('atlas_dispatcher')}@{host}"
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


async def _insert_terminal_fixture(
    connection: AsyncConnection[DictRow],
    *,
    aggregate: TaskAggregate,
    fixture_run_id: UUID,
    plan_digest: str | None = None,
) -> FixtureRun:
    """Insert a terminal no-resource Fixture using the aggregate's published Blueprint."""

    blueprint_cursor = await connection.execute(
        """
        select compiled_plan, plan_digest, contract ->> 'cleanupPolicy' as cleanup_policy
        from atlas.data_blueprint_version
        where id = %s
        """,
        (aggregate.unit.fixture_blueprint_version_id,),
    )
    blueprint = await blueprint_cursor.fetchone()
    assert blueprint is not None
    effective_plan_digest = plan_digest or blueprint["plan_digest"]
    compiled_plan = dict(blueprint["compiled_plan"])
    compiled_plan["planDigest"] = effective_plan_digest
    clock_cursor = await connection.execute("select transaction_timestamp() as requested_at")
    clock = await clock_cursor.fetchone()
    assert clock is not None
    requested_at = clock["requested_at"]
    await connection.execute(
        """
        insert into atlas.fixture_run (
          id, tenant_id, project_id, environment_id, blueprint_version_id,
          run_kind, execution_id, plan_digest, input_digest,
          compiled_plan, run_inputs, cleanup_policy, status, cleanup_state,
          terminal_intent, temporal_workflow_id, execution_deadline,
          requested_at, started_at, ready_at, finished_at, released_at,
          revision, updated_at
        ) values (
          %s, %s, %s, %s, %s,
          'EXECUTION', %s, %s, %s,
          %s, %s, %s, 'RELEASED', 'NOT_REQUIRED',
          'RELEASED', %s, %s,
          %s, %s, %s, %s, %s,
          1, %s
        )
        """,
        (
            fixture_run_id,
            aggregate.run.tenant_id,
            aggregate.run.project_id,
            aggregate.unit.environment_id,
            aggregate.unit.fixture_blueprint_version_id,
            task_attempt_fixture_execution_id(aggregate.attempt.id),
            effective_plan_digest,
            result_projection_digest({}),
            Jsonb(compiled_plan),
            Jsonb({}),
            blueprint["cleanup_policy"],
            f"atlas-fixture/task-{fixture_run_id}",
            aggregate.attempt.execution_deadline,
            requested_at,
            requested_at,
            requested_at,
            requested_at,
            requested_at,
            requested_at,
        ),
    )
    fixture_run = await FixtureRunRepository().get_run(connection, fixture_run_id)
    assert fixture_run is not None
    return fixture_run
