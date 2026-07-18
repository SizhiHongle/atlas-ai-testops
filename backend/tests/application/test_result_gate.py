"""Explicit snapshot-bound Task Gate application tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.application.test_result_classification import (
    _AuditRepository,
    _classification_chain,
    _ClassificationResultRepository,
    _ClassificationTaskRepository,
    _Connection,
    _IdempotencyRepository,
)
from tests.application.test_result_projection import (
    _closed_snapshot_run,
    _closed_snapshot_unit,
    _OutboxRepository,
    _ResultRepository,
    _task_hygiene_resolution,
    _task_resolution,
    _TaskRepository,
)
from tests.infrastructure.test_task_run_repository import _aggregate

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.result_classification import ResultClassificationService
from atlas_testops.application.result_gate import ResultGateService
from atlas_testops.application.result_projection import ResultProjectionService
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.result import (
    DataHygiene,
    ExecutionInfluence,
    FailureClassificationRevision,
    FailureClusterRevision,
    RequestTaskGateEvaluation,
    TaskGateDecision,
    TaskGateVerdict,
    TaskResultSnapshot,
    UnitHygieneResolutionRevision,
    UnitResolutionRevision,
    Verdict,
)
from atlas_testops.domain.task import ExecutionQuality, TaskRun


class _Database:
    @asynccontextmanager
    async def transaction(self, _context: object) -> AsyncIterator[_Connection]:
        yield _Connection()


class _GateResultRepository(_ClassificationResultRepository):
    def __init__(
        self,
        *,
        snapshots: list[TaskResultSnapshot],
        resolutions: list[UnitResolutionRevision],
        hygiene: list[UnitHygieneResolutionRevision],
    ) -> None:
        super().__init__(
            snapshots=snapshots,
            resolutions=resolutions,
            hygiene=hygiene,
        )
        self.gates: list[TaskGateDecision] = []
        self.callback_intents: list[dict[str, object]] = []

    async def list_current_gate_classifications(
        self,
        _connection: object,
        result_snapshot_id: UUID,
    ) -> tuple[
        tuple[FailureClusterRevision, FailureClassificationRevision | None],
        ...,
    ]:
        clusters = sorted(
            (
                cluster
                for cluster in self.clusters
                if cluster.result_snapshot_id == result_snapshot_id
            ),
            key=lambda item: (item.fingerprint, item.failure_cluster_id, item.id),
        )
        pairs: list[
            tuple[FailureClusterRevision, FailureClassificationRevision | None]
        ] = []
        for cluster in clusters:
            pairs.append(
                (
                    cluster,
                    await self.get_latest_failure_classification_for_cluster(
                        _connection,
                        cluster.id,
                    ),
                )
            )
        return tuple(pairs)

    async def lock_failure_classification_chains(
        self,
        _connection: object,
        _failure_classification_ids: tuple[UUID, ...],
    ) -> None:
        return None

    async def get_latest_task_gate_for_update(
        self,
        _connection: object,
        task_run_id: UUID,
    ) -> TaskGateDecision | None:
        matches = [item for item in self.gates if item.task_run_id == task_run_id]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def insert_task_gate_decision(
        self,
        _connection: object,
        decision: TaskGateDecision,
    ) -> None:
        self.gates.append(decision)

    async def insert_task_gate_callback_intent(
        self,
        _connection: object,
        *,
        event_id: UUID,
        decision: TaskGateDecision,
        manifest_hash: str,
        created_at: datetime,
    ) -> None:
        self.callback_intents.append(
            {
                "event_id": event_id,
                "decision": decision,
                "manifest_hash": manifest_hash,
                "created_at": created_at,
            }
        )


async def _clean_chain() -> tuple[
    TaskRun,
    list[TaskResultSnapshot],
    list[UnitResolutionRevision],
    list[UnitHygieneResolutionRevision],
]:
    raw_run, manifest, raw_units, attempts = _aggregate(unit_count=1)
    run = _closed_snapshot_run(raw_run, unit_count=1)
    unit = _closed_snapshot_unit(raw_units[0], quality=ExecutionQuality.PASSED)
    tasks = _TaskRepository(attempts)
    tasks.units = [unit]
    results = _ResultRepository()
    results.bind_attempts(tasks.attempts)
    results.bind_units(tasks.units)
    results.resolutions = [
        _task_resolution(
            unit,
            attempts[0],
            verdict=Verdict.PASSED,
            influence=ExecutionInfluence.AUTONOMOUS,
        )
    ]
    results.hygiene_resolutions = [
        _task_hygiene_resolution(
            unit,
            attempts[0],
            hygiene=DataHygiene.CLEANED,
        )
    ]
    projection = ResultProjectionService(
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        outbox_repository=cast(Any, _OutboxRepository()),
    )
    await projection.snapshot_task_fully_resolved(
        cast(AsyncConnection[DictRow], object()),
        run=run,
        manifest=manifest,
        created_at=cast(Any, run.closed_at),
    )
    return run, results.snapshots, results.resolutions, results.hygiene_resolutions


async def _gate_service(
    *,
    clean: bool,
) -> tuple[
    TaskRun,
    ActorContext,
    _GateResultRepository,
    _AuditRepository,
    _OutboxRepository,
    ResultGateService,
]:
    run, snapshots, resolutions, hygiene = (
        await _clean_chain() if clean else await _classification_chain()
    )
    actor = ActorContext(
        tenant_id=run.tenant_id,
        actor_id=uuid4(),
        request_id="task-gate-test",
        grants=(
            AccessGrant(
                role=PlatformRole.CASE_REVIEWER,
                project_id=run.project_id,
            ),
        ),
    )
    database = cast(Any, _Database())
    results = _GateResultRepository(
        snapshots=snapshots,
        resolutions=resolutions,
        hygiene=hygiene,
    )
    tasks = cast(Any, _ClassificationTaskRepository(run))
    audit = _AuditRepository()
    outbox = _OutboxRepository()
    idempotency = _IdempotencyRepository()
    classification = ResultClassificationService(
        database,
        result_repository=cast(Any, results),
        task_repository=tasks,
        audit_repository=cast(Any, audit),
        outbox_repository=cast(Any, outbox),
        idempotency_repository=cast(Any, idempotency),
    )
    service = ResultGateService(
        database,
        classification_service=classification,
        result_repository=cast(Any, results),
        task_repository=tasks,
        audit_repository=cast(Any, audit),
        outbox_repository=cast(Any, outbox),
        idempotency_repository=cast(Any, idempotency),
    )
    return run, actor, results, audit, outbox, service


@pytest.mark.anyio
async def test_clean_snapshot_gate_is_accepted_and_idempotently_replayed() -> None:
    _, actor, results, audit, outbox, service = await _gate_service(clean=True)
    request = RequestTaskGateEvaluation(
        result_snapshot_id=results.snapshots[-1].id,
        client_mutation_id="gate:evaluate:clean:1",
    )

    created = await service.evaluate(
        actor,
        request,
        idempotency_key=request.client_mutation_id,
    )
    replay = await service.evaluate(
        actor,
        request,
        idempotency_key=request.client_mutation_id,
    )

    assert created.value.decision is TaskGateVerdict.ACCEPTED
    assert created.value.failure_classification_revision_ids == ()
    assert created.replayed is False
    assert replay.replayed
    assert replay.value == created.value
    assert len(results.gates) == 1
    assert len(results.callback_intents) == 1
    assert results.callback_intents[0]["decision"] == created.value
    assert [item["event_type"] for item in audit.events] == ["task_gate.evaluated"]
    assert [item.event_type for item in outbox.events] == ["task_gate.evaluated"]


@pytest.mark.anyio
async def test_uncertain_snapshot_gate_appends_auditable_revisions() -> None:
    _, actor, results, _, outbox, service = await _gate_service(clean=False)
    snapshot = results.snapshots[-1]
    first_request = RequestTaskGateEvaluation(
        result_snapshot_id=snapshot.id,
        client_mutation_id="gate:evaluate:uncertain:1",
    )
    second_request = first_request.model_copy(
        update={"client_mutation_id": "gate:evaluate:uncertain:2"}
    )

    first = await service.evaluate(
        actor,
        first_request,
        idempotency_key=first_request.client_mutation_id,
    )
    second = await service.evaluate(
        actor,
        second_request,
        idempotency_key=second_request.client_mutation_id,
    )

    assert first.value.decision is TaskGateVerdict.INCONCLUSIVE
    assert first.value.failure_classification_revision_ids
    assert second.value.revision == 2
    assert second.value.task_gate_id == first.value.task_gate_id
    assert second.value.supersedes_gate_decision_id == first.value.id
    assert len(results.gates) == 2
    assert len(results.callback_intents) == 2
    assert [item.event_type for item in outbox.events] == [
        "failure_classification.revised",
        "task_gate.evaluated",
        "task_gate.evaluated",
    ]
