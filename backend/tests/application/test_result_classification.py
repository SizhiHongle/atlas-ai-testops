"""Snapshot-bound failure clustering and human review service tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.application.test_result_projection import (
    _closed_snapshot_run,
    _closed_snapshot_unit,
    _OutboxRepository,
    _ResultRepository,
    _task_hygiene_resolution,
    _task_resolution,
    _TaskRepository,
)
from tests.infrastructure.test_task_run_repository import NOW, _aggregate

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.result_classification import ResultClassificationService
from atlas_testops.application.result_projection import ResultProjectionService
from atlas_testops.core.errors import ApplicationError
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.result import (
    ClassificationAuthorKind,
    ClassificationConfidence,
    ClassificationJudgmentState,
    DataHygiene,
    ExecutionInfluence,
    FailureClassificationRevision,
    FailureClusterRevision,
    FailureDomain,
    RequestFailureClassificationRevision,
    TaskResultSnapshot,
    UnitHygieneResolutionRevision,
    UnitResolutionRevision,
    Verdict,
)
from atlas_testops.domain.task import ExecutionQuality, TaskRun
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyReservation,
)


class _Cursor:
    async def fetchone(self) -> dict[str, object]:
        return {"observed_at": NOW + timedelta(minutes=20)}


class _Connection:
    async def execute(
        self,
        _query: str,
        _parameters: object = None,
    ) -> _Cursor:
        return _Cursor()


class _Database:
    @asynccontextmanager
    async def transaction(self, _context: object) -> AsyncIterator[_Connection]:
        yield _Connection()


class _ClassificationTaskRepository(_TaskRepository):
    def __init__(self, run: TaskRun) -> None:
        super().__init__(())
        self.run = run

    async def get_run_for_update(
        self,
        _connection: object,
        task_run_id: UUID,
    ) -> TaskRun | None:
        return self.run if self.run.id == task_run_id else None


class _ClassificationResultRepository(_ResultRepository):
    def __init__(
        self,
        *,
        snapshots: list[TaskResultSnapshot],
        resolutions: list[UnitResolutionRevision],
        hygiene: list[UnitHygieneResolutionRevision],
    ) -> None:
        super().__init__()
        self.snapshots = snapshots
        self.resolutions = resolutions
        self.hygiene_resolutions = hygiene
        self.clusters: list[FailureClusterRevision] = []
        self.classifications: list[FailureClassificationRevision] = []

    async def get_snapshot_by_id(
        self,
        _connection: object,
        snapshot_id: UUID,
    ) -> TaskResultSnapshot | None:
        return next((item for item in self.snapshots if item.id == snapshot_id), None)

    async def list_resolutions_by_ids(
        self,
        _connection: object,
        resolution_ids: tuple[UUID, ...],
    ) -> tuple[UnitResolutionRevision, ...]:
        by_id = {item.id: item for item in self.resolutions}
        return tuple(by_id[item_id] for item_id in resolution_ids if item_id in by_id)

    async def lock_failure_classification_snapshot(
        self,
        _connection: object,
        _snapshot_id: UUID,
    ) -> None:
        return None

    async def list_hygiene_resolutions_by_ids(
        self,
        _connection: object,
        resolution_ids: tuple[UUID, ...],
    ) -> tuple[UnitHygieneResolutionRevision, ...]:
        by_id = {item.id: item for item in self.hygiene_resolutions}
        return tuple(by_id[item_id] for item_id in resolution_ids if item_id in by_id)

    async def get_failure_cluster(
        self,
        _connection: object,
        *,
        result_snapshot_id: UUID,
        fingerprint: str,
        policy_digest: str,
    ) -> FailureClusterRevision | None:
        return next(
            (
                item
                for item in self.clusters
                if item.result_snapshot_id == result_snapshot_id
                and item.fingerprint == fingerprint
                and item.fingerprint_policy_digest == policy_digest
            ),
            None,
        )

    async def get_failure_cluster_by_revision_id(
        self,
        _connection: object,
        cluster_revision_id: UUID,
    ) -> FailureClusterRevision | None:
        return next((item for item in self.clusters if item.id == cluster_revision_id), None)

    async def get_latest_failure_classification_for_cluster(
        self,
        _connection: object,
        cluster_revision_id: UUID,
    ) -> FailureClassificationRevision | None:
        matches = [
            item
            for item in self.classifications
            if item.failure_cluster_revision_id == cluster_revision_id
        ]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def get_latest_failure_classification_for_update(
        self,
        _connection: object,
        failure_classification_id: UUID,
    ) -> FailureClassificationRevision | None:
        matches = [
            item
            for item in self.classifications
            if item.failure_classification_id == failure_classification_id
        ]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def insert_failure_cluster(
        self,
        _connection: object,
        cluster: FailureClusterRevision,
    ) -> None:
        self.clusters.append(cluster)

    async def insert_failure_classification(
        self,
        _connection: object,
        classification: FailureClassificationRevision,
    ) -> None:
        self.classifications.append(classification)


class _IdempotencyRepository:
    def __init__(self) -> None:
        self.responses: dict[tuple[UUID, str, str], CachedHttpResponse] = {}

    async def reserve(
        self,
        _connection: object,
        *,
        tenant_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        now: object,
        ttl: object,
    ) -> IdempotencyReservation:
        del request_hash, now, ttl
        cached = self.responses.get((tenant_id, scope, key))
        if cached is None:
            return IdempotencyReservation(acquired=True)
        return IdempotencyReservation(acquired=False, cached_response=cached)

    async def complete(
        self,
        _connection: object,
        *,
        tenant_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        response: CachedHttpResponse,
    ) -> None:
        del request_hash
        self.responses[(tenant_id, scope, key)] = response


class _AuditRepository:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def append(self, _connection: object, **values: object) -> UUID:
        self.events.append(values)
        return uuid4()


def _actor(run: TaskRun) -> ActorContext:
    return ActorContext(
        tenant_id=run.tenant_id,
        actor_id=uuid4(),
        request_id="result-classification-test",
        development_override=True,
    )


async def _classification_chain() -> tuple[
    TaskRun,
    list[TaskResultSnapshot],
    list[UnitResolutionRevision],
    list[UnitHygieneResolutionRevision],
]:
    raw_run, manifest, raw_units, attempts = _aggregate(unit_count=3)
    run = _closed_snapshot_run(raw_run, unit_count=3)
    units = [
        _closed_snapshot_unit(raw_units[0], quality=ExecutionQuality.INCONCLUSIVE),
        _closed_snapshot_unit(raw_units[1], quality=ExecutionQuality.INCONCLUSIVE),
        _closed_snapshot_unit(raw_units[2], quality=ExecutionQuality.PASSED),
    ]
    tasks = _TaskRepository(attempts)
    tasks.units = units
    results = _ResultRepository()
    results.bind_attempts(tasks.attempts)
    results.bind_units(tasks.units)
    results.resolutions = [
        _task_resolution(
            units[0],
            attempts[0],
            verdict=Verdict.INCONCLUSIVE,
            influence=ExecutionInfluence.AUTONOMOUS,
        ),
        _task_resolution(
            units[1],
            attempts[1],
            verdict=Verdict.INCONCLUSIVE,
            influence=ExecutionInfluence.AUTONOMOUS,
        ),
        _task_resolution(
            units[2],
            attempts[2],
            verdict=Verdict.PASSED,
            influence=ExecutionInfluence.AUTONOMOUS,
        ),
    ]
    results.hygiene_resolutions = [
        _task_hygiene_resolution(unit, attempt, hygiene=DataHygiene.NOT_APPLICABLE)
        for unit, attempt in zip(units, attempts, strict=True)
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


async def _service_fixture() -> tuple[
    TaskRun,
    ActorContext,
    _ClassificationResultRepository,
    _AuditRepository,
    _OutboxRepository,
    _IdempotencyRepository,
    ResultClassificationService,
]:
    run, snapshots, resolutions, hygiene = await _classification_chain()
    actor = _actor(run)
    results = _ClassificationResultRepository(
        snapshots=snapshots,
        resolutions=resolutions,
        hygiene=hygiene,
    )
    audit = _AuditRepository()
    outbox = _OutboxRepository()
    idempotency = _IdempotencyRepository()
    service = ResultClassificationService(
        cast(Any, _Database()),
        result_repository=cast(Any, results),
        task_repository=cast(Any, _ClassificationTaskRepository(run)),
        audit_repository=cast(Any, audit),
        outbox_repository=cast(Any, outbox),
        idempotency_repository=cast(Any, idempotency),
    )
    return run, actor, results, audit, outbox, idempotency, service


@pytest.mark.anyio
async def test_classification_groups_exact_signal_and_replays_without_new_facts() -> None:
    _, actor, results, audit, outbox, _, service = await _service_fixture()
    snapshot = results.snapshots[-1]

    created = await service.classify_snapshot(actor, snapshot.id)
    replay = await service.classify_snapshot(actor, snapshot.id)

    assert created == replay
    assert len(created.clusters) == 1
    assert created.clusters[0].affected_count == 2
    assert created.clusters[0].affected_unit_resolution_revision_ids == (
        results.resolutions[0].id,
        results.resolutions[1].id,
    )
    assert created.classifications[0].failure_domain is FailureDomain.EVIDENCE
    assert created.classifications[0].judgment_state is (
        ClassificationJudgmentState.RULE_PROPOSED
    )
    assert len(results.clusters) == 1
    assert len(results.classifications) == 1
    assert len(audit.events) == 1
    assert [event.event_type for event in outbox.events] == [
        "failure_classification.revised"
    ]


@pytest.mark.anyio
async def test_human_review_appends_and_replays_one_authorized_revision() -> None:
    _, actor, results, audit, outbox, _, service = await _service_fixture()
    baseline = (await service.classify_snapshot(actor, results.snapshots[-1].id)).classifications[
        0
    ]
    request = RequestFailureClassificationRevision(
        expected_revision=baseline.revision,
        failure_domain=FailureDomain.PRODUCT,
        hypothesis_code="PRODUCT_DEFECT_CONFIRMED",
        hypothesis="The reviewed evidence attributes the failure to product behavior.",
        confidence=ClassificationConfidence(numerator=8_500),
        supporting_evidence_refs=baseline.supporting_evidence_refs,
        evidence_gap_codes=(),
        judgment_state=ClassificationJudgmentState.HUMAN_REVISED,
        client_mutation_id="review:classification:product:1",
    )

    created = await service.revise_classification(
        actor,
        baseline.failure_classification_id,
        request,
        idempotency_key=request.client_mutation_id,
    )
    replay = await service.revise_classification(
        actor,
        baseline.failure_classification_id,
        request,
        idempotency_key=request.client_mutation_id,
    )

    assert created.status_code == 201
    assert created.replayed is False
    assert created.value.revision == 2
    assert created.value.author_kind is ClassificationAuthorKind.HUMAN
    assert created.value.authored_by == actor.actor_id
    assert replay.replayed is True
    assert replay.value == created.value
    assert len(results.classifications) == 2
    assert len(audit.events) == 2
    assert len(outbox.events) == 2


@pytest.mark.anyio
async def test_review_requires_explicit_reviewer_role_and_unchanged_confirmation() -> None:
    run, actor, results, _, _, _, service = await _service_fixture()
    baseline = (await service.classify_snapshot(actor, results.snapshots[-1].id)).classifications[
        0
    ]
    changed_confirmation = RequestFailureClassificationRevision(
        expected_revision=baseline.revision,
        failure_domain=FailureDomain.PRODUCT,
        hypothesis_code="CHANGED_ATTRIBUTION",
        hypothesis="A confirmation cannot silently revise the attribution.",
        confidence=baseline.confidence,
        supporting_evidence_refs=baseline.supporting_evidence_refs,
        evidence_gap_codes=baseline.evidence_gap_codes,
        judgment_state=ClassificationJudgmentState.HUMAN_CONFIRMED,
        client_mutation_id="review:classification:confirm:1",
    )
    with pytest.raises(ApplicationError) as confirmation_error:
        await service.revise_classification(
            actor,
            baseline.failure_classification_id,
            changed_confirmation,
            idempotency_key=changed_confirmation.client_mutation_id,
        )
    assert confirmation_error.value.status_code == 400

    author_only = ActorContext(
        tenant_id=run.tenant_id,
        actor_id=uuid4(),
        request_id="result-classification-author-only",
        grants=(AccessGrant(role=PlatformRole.CASE_AUTHOR, project_id=run.project_id),),
    )
    unchanged_confirmation = changed_confirmation.model_copy(
        update={
            "failure_domain": baseline.failure_domain,
            "hypothesis_code": baseline.hypothesis_code,
            "hypothesis": baseline.hypothesis,
            "client_mutation_id": "review:classification:confirm:2",
        }
    )
    with pytest.raises(ApplicationError) as permission_error:
        await service.revise_classification(
            author_only,
            baseline.failure_classification_id,
            unchanged_confirmation,
            idempotency_key=unchanged_confirmation.client_mutation_id,
        )
    assert permission_error.value.status_code == 403
