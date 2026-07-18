"""Deterministic Result projection tests over Seal and ClosureNotice facts."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.infrastructure.test_task_run_repository import NOW, _aggregate

from atlas_testops.application.result_projection import (
    ResultProjectionError,
    ResultProjectionService,
)
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.result import (
    UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST,
    UNIT_RESOLUTION_POLICY_DIGEST,
    AttemptClosureNotice,
    AttemptClosureSourceStatus,
    AttemptEventChain,
    AttemptSeal,
    AttemptSealContent,
    AttemptSealSignature,
    DataHygiene,
    EvidenceCompleteness,
    EvidenceIntegrity,
    ExecutionInfluence,
    OutcomeClass,
    Stability,
    TaskResultSnapshot,
    TaskResultSnapshotFinality,
    UnitHygieneInputSource,
    UnitHygieneResolutionInput,
    UnitHygieneResolutionRevision,
    UnitHygieneResolutionRevisionContent,
    UnitResolutionRevision,
    Verdict,
    attempt_seal_content_hash,
    unit_hygiene_input_set_hash,
    unit_hygiene_resolution_hash,
)
from atlas_testops.domain.task import (
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    TaskMaterializationState,
    TaskRun,
    UnitAttempt,
    unit_attempt_workflow_id,
    unit_retry_attempt_id,
)

_DIGEST = "sha256:" + "a" * 64


class _TaskRepository:
    def __init__(self, attempts: tuple[UnitAttempt, ...]) -> None:
        self.attempts = list(attempts)
        self.units: list[ExecutionUnit] = []

    async def list_attempts(
        self,
        _connection: object,
        execution_unit_id: UUID,
    ) -> tuple[UnitAttempt, ...]:
        return tuple(
            sorted(
                (
                    attempt
                    for attempt in self.attempts
                    if attempt.execution_unit_id == execution_unit_id
                ),
                key=lambda item: item.attempt_number,
            )
        )

    async def list_units(
        self,
        _connection: object,
        task_run_id: UUID,
    ) -> tuple[ExecutionUnit, ...]:
        return tuple(
            sorted(
                (unit for unit in self.units if unit.task_run_id == task_run_id),
                key=lambda item: item.ordinal,
            )
        )


class _ResultRepository:
    def __init__(self) -> None:
        self.seals: dict[UUID, AttemptSeal] = {}
        self.closures: dict[UUID, AttemptClosureNotice] = {}
        self.resolutions: list[UnitResolutionRevision] = []
        self.hygiene_resolutions: list[UnitHygieneResolutionRevision] = []
        self.snapshots: list[TaskResultSnapshot] = []

    async def get_seal_by_attempt(
        self,
        _connection: object,
        unit_attempt_id: UUID,
    ) -> AttemptSeal | None:
        return self.seals.get(unit_attempt_id)

    async def get_closure_by_attempt(
        self,
        _connection: object,
        unit_attempt_id: UUID,
    ) -> AttemptClosureNotice | None:
        return self.closures.get(unit_attempt_id)

    async def insert_closure(
        self,
        _connection: object,
        notice: AttemptClosureNotice,
    ) -> None:
        self.closures[notice.unit_attempt_id] = notice

    async def list_seals_for_unit(
        self,
        _connection: object,
        execution_unit_id: UUID,
    ) -> tuple[AttemptSeal, ...]:
        return tuple(
            seal
            for attempt_id, seal in self.seals.items()
            if any(
                attempt.id == attempt_id and attempt.execution_unit_id == execution_unit_id
                for attempt in self._attempts
            )
        )

    async def list_closures_for_unit(
        self,
        _connection: object,
        execution_unit_id: UUID,
    ) -> tuple[AttemptClosureNotice, ...]:
        return tuple(
            notice
            for notice in self.closures.values()
            if notice.execution_unit_id == execution_unit_id
        )

    async def get_latest_resolution(
        self,
        _connection: object,
        execution_unit_id: UUID,
    ) -> UnitResolutionRevision | None:
        matches = [item for item in self.resolutions if item.execution_unit_id == execution_unit_id]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def insert_resolution(
        self,
        _connection: object,
        resolution: UnitResolutionRevision,
    ) -> None:
        self.resolutions.append(resolution)

    async def list_latest_resolutions_for_task(
        self,
        _connection: object,
        task_run_id: UUID,
    ) -> tuple[UnitResolutionRevision, ...]:
        latest: dict[UUID, UnitResolutionRevision] = {}
        for resolution in self.resolutions:
            if resolution.task_run_id != task_run_id:
                continue
            previous = latest.get(resolution.execution_unit_id)
            if previous is None or resolution.revision > previous.revision:
                latest[resolution.execution_unit_id] = resolution
        return tuple(
            sorted(
                latest.values(),
                key=lambda item: next(
                    unit.ordinal for unit in self._units if unit.id == item.execution_unit_id
                ),
            )
        )

    async def get_latest_snapshot(
        self,
        _connection: object,
        task_run_id: UUID,
    ) -> TaskResultSnapshot | None:
        matches = [snapshot for snapshot in self.snapshots if snapshot.task_run_id == task_run_id]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def get_latest_snapshot_for_finality(
        self,
        _connection: object,
        task_run_id: UUID,
        finality: TaskResultSnapshotFinality,
    ) -> TaskResultSnapshot | None:
        matches = [
            snapshot
            for snapshot in self.snapshots
            if snapshot.task_run_id == task_run_id and snapshot.finality is finality
        ]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def list_latest_hygiene_resolutions_for_task(
        self,
        _connection: object,
        task_run_id: UUID,
    ) -> tuple[UnitHygieneResolutionRevision, ...]:
        latest: dict[UUID, UnitHygieneResolutionRevision] = {}
        for resolution in self.hygiene_resolutions:
            if resolution.task_run_id != task_run_id:
                continue
            previous = latest.get(resolution.execution_unit_id)
            if previous is None or resolution.revision > previous.revision:
                latest[resolution.execution_unit_id] = resolution
        return tuple(
            sorted(
                latest.values(),
                key=lambda item: next(
                    unit.ordinal for unit in self._units if unit.id == item.execution_unit_id
                ),
            )
        )

    async def insert_snapshot(
        self,
        _connection: object,
        snapshot: TaskResultSnapshot,
    ) -> None:
        self.snapshots.append(snapshot)

    def bind_attempts(self, attempts: list[UnitAttempt]) -> None:
        self._attempts = attempts

    def bind_units(self, units: list[ExecutionUnit]) -> None:
        self._units = units


class _OutboxRepository:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def append(self, _connection: object, event: DomainEvent) -> None:
        self.events.append(event)


def _closed_attempt(
    attempt: UnitAttempt,
    *,
    quality: ExecutionQuality,
    started: bool = True,
) -> UnitAttempt:
    return UnitAttempt.model_validate(
        {
            **attempt.model_dump(mode="python", by_alias=False),
            "lifecycle": ExecutionLifecycle.CLOSED,
            "quality": quality,
            "hygiene": ExecutionHygiene.NOT_REQUIRED,
            "started_at": NOW + timedelta(minutes=1) if started else None,
            "finalized_at": NOW + timedelta(minutes=2),
            "closed_at": NOW + timedelta(minutes=3),
            "revision": 3,
            "updated_at": NOW + timedelta(minutes=3),
        }
    )


def _retry_attempt(
    first: UnitAttempt,
    *,
    quality: ExecutionQuality,
) -> UnitAttempt:
    attempt_number = 2
    attempt_id = unit_retry_attempt_id(
        execution_unit_id=first.execution_unit_id,
        attempt_number=attempt_number,
    )
    return UnitAttempt.model_validate(
        {
            **first.model_dump(mode="python", by_alias=False),
            "id": attempt_id,
            "attempt_number": attempt_number,
            "temporal_workflow_id": unit_attempt_workflow_id(
                tenant_id=first.tenant_id,
                unit_attempt_id=attempt_id,
            ),
            "quality": quality,
            "queued_at": NOW + timedelta(minutes=3),
            "started_at": NOW + timedelta(minutes=4),
            "finalized_at": NOW + timedelta(minutes=5),
            "closed_at": NOW + timedelta(minutes=6),
            "revision": 3,
            "created_at": NOW + timedelta(minutes=3),
            "updated_at": NOW + timedelta(minutes=6),
        }
    )


def _seal(
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    *,
    verdict: Verdict,
    outcome_class: OutcomeClass = OutcomeClass.BUSINESS,
    closure_reason: str | None = None,
    oracle_hash: str = _DIGEST,
) -> AttemptSeal:
    content = AttemptSealContent(
        seal_id=uuid4(),
        tenant_id=attempt.tenant_id,
        project_id=attempt.project_id,
        task_run_id=attempt.task_run_id,
        execution_unit_id=attempt.execution_unit_id,
        unit_attempt_id=attempt.id,
        manifest_id=attempt.task_run_id,
        manifest_hash=attempt.manifest_hash,
        unit_key=attempt.unit_key,
        execution_ticket_id=uuid4(),
        execution_ticket_digest=_DIGEST,
        oracle_verdict=verdict,
        outcome_class=outcome_class,
        closure_reason=closure_reason
        or ("ORACLE_PASSED" if verdict is Verdict.PASSED else "ORACLE_FAILED"),
        data_hygiene=DataHygiene.NOT_APPLICABLE,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        evidence_integrity=EvidenceIntegrity.VERIFIED,
        execution_influence=ExecutionInfluence.AUTONOMOUS,
        stability=Stability.UNKNOWN,
        oracle_results_hash=oracle_hash,
        artifact_manifest_hash=_DIGEST,
        event_chain=AttemptEventChain(head=_DIGEST, event_count=3),
        evidence_policy_digest=_DIGEST,
        runtime_digest=_DIGEST,
        sealed_at=cast(Any, attempt.finalized_at),
        signature=AttemptSealSignature(kid="runtime-key-1"),
    )
    return AttemptSeal(
        **content.model_dump(mode="python"),
        signature_value="base64url:" + "A" * 86,
        content_hash=attempt_seal_content_hash(content),
    )


def _fixture(
    attempts: tuple[UnitAttempt, ...],
) -> tuple[
    ResultProjectionService,
    _TaskRepository,
    _ResultRepository,
    _OutboxRepository,
    ExecutionUnit,
]:
    _, _, units, _ = _aggregate(unit_count=1)
    unit = units[0].model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": NOW + timedelta(minutes=1),
            "updated_at": NOW + timedelta(minutes=1),
        }
    )
    tasks = _TaskRepository(attempts)
    tasks.units = [unit]
    results = _ResultRepository()
    results.bind_attempts(tasks.attempts)
    results.bind_units(tasks.units)
    outbox = _OutboxRepository()
    service = ResultProjectionService(
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        outbox_repository=cast(Any, outbox),
    )
    return service, tasks, results, outbox, unit


@pytest.mark.anyio
async def test_closure_notice_cannot_manufacture_failure_and_replays_exactly() -> None:
    _, _, _, attempts = _aggregate(unit_count=1)
    closed = _closed_attempt(
        attempts[0],
        quality=ExecutionQuality.FAILED,
    )
    service, _, results, outbox, unit = _fixture((closed,))

    resolution = await service.close_without_seal(
        cast(AsyncConnection[DictRow], object()),
        unit=unit,
        attempt=closed,
        source_status=AttemptClosureSourceStatus.FAILED,
        closure_reason="TASK_ATTEMPT_FAILED",
        created_at=cast(Any, closed.closed_at),
    )
    replay = await service.close_without_seal(
        cast(AsyncConnection[DictRow], object()),
        unit=unit,
        attempt=closed,
        source_status=AttemptClosureSourceStatus.FAILED,
        closure_reason="TASK_ATTEMPT_FAILED",
        created_at=cast(Any, closed.closed_at),
    )

    notice = results.closures[closed.id]
    assert notice.verdict is Verdict.INCONCLUSIVE
    assert resolution.effective_verdict is Verdict.INCONCLUSIVE
    assert resolution.stability is Stability.UNKNOWN
    assert replay == resolution
    assert len(results.resolutions) == 1
    assert [event.event_type for event in outbox.events] == ["unit.resolved"]


@pytest.mark.anyio
async def test_never_started_cancel_is_not_evaluated() -> None:
    _, _, _, attempts = _aggregate(unit_count=1)
    canceled = _closed_attempt(
        attempts[0],
        quality=ExecutionQuality.CANCELED,
        started=False,
    )
    service, _, results, _, unit = _fixture((canceled,))

    resolution = await service.close_without_seal(
        cast(AsyncConnection[DictRow], object()),
        unit=unit,
        attempt=canceled,
        source_status=AttemptClosureSourceStatus.CANCELED,
        closure_reason="TASK_RUN_CANCELED_BEFORE_DISPATCH",
        created_at=cast(Any, canceled.closed_at),
    )

    notice = results.closures[canceled.id]
    assert notice.verdict is Verdict.NOT_EVALUATED
    assert notice.evidence_completeness is EvidenceCompleteness.NOT_APPLICABLE
    assert resolution.effective_verdict is Verdict.NOT_EVALUATED


@pytest.mark.anyio
async def test_infrastructure_then_pass_appends_recovered_revision() -> None:
    _, _, _, attempts = _aggregate(unit_count=1)
    infra = _closed_attempt(
        attempts[0],
        quality=ExecutionQuality.INFRA_ERROR,
    )
    passed = _retry_attempt(infra, quality=ExecutionQuality.PASSED)
    service, tasks, results, outbox, unit = _fixture((infra,))

    first = await service.close_without_seal(
        cast(AsyncConnection[DictRow], object()),
        unit=unit,
        attempt=infra,
        source_status=AttemptClosureSourceStatus.INFRA_ERROR,
        closure_reason="TASK_INFRA_ERROR",
        created_at=cast(Any, infra.closed_at),
    )
    tasks.attempts.append(passed)
    results.seals[passed.id] = _seal(unit, passed, verdict=Verdict.PASSED)
    second = await service.resolve_unit(
        cast(AsyncConnection[DictRow], object()),
        unit=unit,
        created_at=cast(Any, passed.closed_at),
    )

    assert first.revision == 1
    assert first.effective_verdict is Verdict.INCONCLUSIVE
    assert second.revision == 2
    assert second.unit_resolution_id == first.unit_resolution_id
    assert second.supersedes_revision_id == first.id
    assert second.effective_verdict is Verdict.PASSED
    assert second.stability is Stability.INFRA_RECOVERED
    assert len(outbox.events) == 2


@pytest.mark.anyio
async def test_failed_then_pass_is_preserved_as_flaky_suspect() -> None:
    _, _, _, attempts = _aggregate(unit_count=1)
    failed = _closed_attempt(attempts[0], quality=ExecutionQuality.FAILED)
    passed = _retry_attempt(failed, quality=ExecutionQuality.PASSED)
    service, tasks, results, _, unit = _fixture((failed,))
    results.seals[failed.id] = _seal(unit, failed, verdict=Verdict.FAILED)

    first = await service.resolve_unit(
        cast(AsyncConnection[DictRow], object()),
        unit=unit,
        created_at=cast(Any, failed.closed_at),
    )
    tasks.attempts.append(passed)
    results.seals[passed.id] = _seal(unit, passed, verdict=Verdict.PASSED)
    second = await service.resolve_unit(
        cast(AsyncConnection[DictRow], object()),
        unit=unit,
        created_at=cast(Any, passed.closed_at),
    )

    assert first.stability is Stability.STABLE
    assert second.effective_verdict is Verdict.PASSED
    assert second.stability is Stability.FLAKY_SUSPECT


@pytest.mark.anyio
async def test_resolution_rejects_terminal_attempt_without_fact() -> None:
    _, _, _, attempts = _aggregate(unit_count=1)
    closed = _closed_attempt(
        attempts[0],
        quality=ExecutionQuality.INCONCLUSIVE,
    )
    service, _, _, _, unit = _fixture((closed,))

    with pytest.raises(ResultProjectionError, match="RESULT_TERMINAL_COVERAGE_INVALID"):
        await service.resolve_unit(
            cast(AsyncConnection[DictRow], object()),
            unit=unit,
            created_at=cast(Any, closed.closed_at),
        )


@pytest.mark.anyio
async def test_closure_rejects_source_status_that_conflicts_with_attempt_quality() -> None:
    _, _, _, attempts = _aggregate(unit_count=1)
    closed = _closed_attempt(
        attempts[0],
        quality=ExecutionQuality.FAILED,
    )
    service, _, _, _, unit = _fixture((closed,))

    with pytest.raises(ResultProjectionError, match="RESULT_CLOSURE_STATUS_CONFLICT"):
        await service.close_without_seal(
            cast(AsyncConnection[DictRow], object()),
            unit=unit,
            attempt=closed,
            source_status=AttemptClosureSourceStatus.INFRA_ERROR,
            closure_reason="TASK_BROWSER_HOST_UNAVAILABLE",
            created_at=cast(Any, closed.closed_at),
        )


def _closed_snapshot_run(run: TaskRun, *, unit_count: int) -> TaskRun:
    closed_at = NOW + timedelta(minutes=10)
    return TaskRun.model_validate(
        {
            **run.model_dump(mode="python", by_alias=False),
            "materialization_state": TaskMaterializationState.SEALED,
            "materialized_unit_count": unit_count,
            "materialized_first_attempt_count": unit_count,
            "materialization_sealed_at": NOW,
            "lifecycle": ExecutionLifecycle.CLOSED,
            "quality": ExecutionQuality.INCONCLUSIVE,
            "hygiene": ExecutionHygiene.NOT_REQUIRED,
            "started_at": NOW + timedelta(minutes=1),
            "finalized_at": NOW + timedelta(minutes=9),
            "closed_at": closed_at,
            "revision": 4,
            "updated_at": closed_at,
        }
    )


def _closed_snapshot_unit(
    unit: ExecutionUnit,
    *,
    quality: ExecutionQuality,
) -> ExecutionUnit:
    closed_at = NOW + timedelta(minutes=8)
    return ExecutionUnit.model_validate(
        {
            **unit.model_dump(mode="python", by_alias=False),
            "lifecycle": ExecutionLifecycle.CLOSED,
            "quality": quality,
            "hygiene": ExecutionHygiene.NOT_REQUIRED,
            "started_at": NOW + timedelta(minutes=1),
            "finalized_at": NOW + timedelta(minutes=7),
            "closed_at": closed_at,
            "revision": 4,
            "updated_at": closed_at,
        }
    )


def _task_resolution(
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    *,
    verdict: Verdict,
    influence: ExecutionInfluence,
) -> UnitResolutionRevision:
    passed = verdict is Verdict.PASSED
    return UnitResolutionRevision(
        id=uuid4(),
        unit_resolution_id=uuid4(),
        tenant_id=unit.tenant_id,
        project_id=unit.project_id,
        task_run_id=unit.task_run_id,
        execution_unit_id=unit.id,
        manifest_hash=unit.manifest_hash,
        unit_key=unit.unit_key,
        revision=1,
        input_seal_ids=(uuid4(),),
        input_closure_notice_ids=(),
        input_set_hash="sha256:" + uuid4().hex * 2,
        effective_verdict=verdict,
        outcome_class=OutcomeClass.BUSINESS,
        closure_reason="ORACLE_PASSED" if passed else "ORACLE_INCONCLUSIVE",
        data_hygiene=DataHygiene.NOT_APPLICABLE,
        evidence_completeness=(
            EvidenceCompleteness.COMPLETE if passed else EvidenceCompleteness.MISSING
        ),
        evidence_integrity=(EvidenceIntegrity.VERIFIED if passed else EvidenceIntegrity.UNVERIFIED),
        execution_influence=influence,
        stability=Stability.STABLE if passed else Stability.UNKNOWN,
        decisive_unit_attempt_id=attempt.id,
        decisive_attempt_number=attempt.attempt_number,
        resolution_policy_digest=UNIT_RESOLUTION_POLICY_DIGEST,
        created_at=NOW + timedelta(minutes=8),
    )


def _task_hygiene_resolution(
    unit: ExecutionUnit,
    attempt: UnitAttempt,
    *,
    hygiene: DataHygiene,
) -> UnitHygieneResolutionRevision:
    resource_count = int(hygiene in {DataHygiene.LEAKED, DataHygiene.PENDING})
    input_value = UnitHygieneResolutionInput(
        unit_attempt_id=attempt.id,
        attempt_number=attempt.attempt_number,
        source=UnitHygieneInputSource.FIXTURE_RUN,
        data_hygiene=hygiene,
        fixture_binding_id=uuid4(),
        fixture_run_id=uuid4(),
        fixture_run_revision=4,
        fixture_run_status=(
            "CLEANUP_FAILED"
            if hygiene is DataHygiene.LEAKED
            else "CLEANING"
            if hygiene is DataHygiene.PENDING
            else "RELEASED"
        ),
        cleanup_generation=1,
        fixture_plan_digest=_DIGEST,
        fixture_manifest_digest=_DIGEST,
        resource_state_hash="sha256:" + uuid4().hex * 2,
        resource_count=resource_count,
        cleaned_resource_count=0,
        leaked_resource_count=int(hygiene is DataHygiene.LEAKED),
        unresolved_resource_count=int(hygiene is DataHygiene.PENDING),
        exhausted_reconcile_count=0,
        unresolved_reconcile_count=0,
        observed_at=NOW + timedelta(minutes=8),
    )
    inputs = (input_value,)
    content = UnitHygieneResolutionRevisionContent(
        id=uuid4(),
        unit_hygiene_resolution_id=uuid4(),
        tenant_id=unit.tenant_id,
        project_id=unit.project_id,
        task_run_id=unit.task_run_id,
        execution_unit_id=unit.id,
        manifest_hash=unit.manifest_hash,
        unit_key=unit.unit_key,
        revision=1,
        inputs=inputs,
        input_set_hash=unit_hygiene_input_set_hash(
            execution_unit_id=unit.id,
            manifest_hash=unit.manifest_hash,
            inputs=inputs,
        ),
        data_hygiene=hygiene,
        resolution_policy_digest=UNIT_HYGIENE_RESOLUTION_POLICY_DIGEST,
        projection_watermark=input_value.observed_at,
        created_at=NOW + timedelta(minutes=8),
    )
    return UnitHygieneResolutionRevision(
        **content.model_dump(mode="python"),
        resolution_hash=unit_hygiene_resolution_hash(content),
    )


@pytest.mark.anyio
async def test_task_snapshot_aggregates_exact_manifest_and_replays() -> None:
    raw_run, manifest, raw_units, attempts = _aggregate(unit_count=2)
    run = _closed_snapshot_run(raw_run, unit_count=2)
    units = (
        _closed_snapshot_unit(raw_units[0], quality=ExecutionQuality.PASSED),
        _closed_snapshot_unit(
            raw_units[1],
            quality=ExecutionQuality.INCONCLUSIVE,
        ),
    )
    tasks = _TaskRepository(attempts)
    tasks.units = list(units)
    results = _ResultRepository()
    results.bind_attempts(tasks.attempts)
    results.bind_units(tasks.units)
    results.resolutions = [
        _task_resolution(
            units[0],
            attempts[0],
            verdict=Verdict.PASSED,
            influence=ExecutionInfluence.MANUAL_ASSISTED,
        ),
        _task_resolution(
            units[1],
            attempts[1],
            verdict=Verdict.INCONCLUSIVE,
            influence=ExecutionInfluence.AUTONOMOUS,
        ),
    ]
    outbox = _OutboxRepository()
    service = ResultProjectionService(
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        outbox_repository=cast(Any, outbox),
    )

    snapshot = await service.snapshot_task(
        cast(AsyncConnection[DictRow], object()),
        run=run,
        manifest=manifest,
        created_at=cast(Any, run.closed_at),
    )
    replay = await service.snapshot_task(
        cast(AsyncConnection[DictRow], object()),
        run=run,
        manifest=manifest,
        created_at=cast(Any, run.closed_at),
    )

    assert snapshot.finality is TaskResultSnapshotFinality.QUALITY_FINAL
    assert snapshot.verdict_counts.passed == 1
    assert snapshot.verdict_counts.inconclusive == 1
    assert snapshot.raw_pass_rate.model_dump() == {
        "numerator": 1,
        "denominator": 2,
    }
    assert snapshot.trusted_pass_rate.numerator == 1
    assert snapshot.autonomous_pass_rate.numerator == 0
    assert snapshot.decisive_pass_rate.model_dump() == {
        "numerator": 1,
        "denominator": 1,
    }
    assert replay == snapshot
    assert len(results.snapshots) == 1
    assert [event.event_type for event in outbox.events] == ["task.snapshot_created"]


@pytest.mark.anyio
async def test_fully_resolved_snapshot_overlays_terminal_hygiene_and_replays() -> None:
    raw_run, manifest, raw_units, attempts = _aggregate(unit_count=2)
    run = _closed_snapshot_run(raw_run, unit_count=2)
    units = (
        _closed_snapshot_unit(raw_units[0], quality=ExecutionQuality.PASSED),
        _closed_snapshot_unit(
            raw_units[1],
            quality=ExecutionQuality.INCONCLUSIVE,
        ),
    )
    tasks = _TaskRepository(attempts)
    tasks.units = list(units)
    results = _ResultRepository()
    results.bind_attempts(tasks.attempts)
    results.bind_units(tasks.units)
    results.resolutions = [
        _task_resolution(
            units[0],
            attempts[0],
            verdict=Verdict.PASSED,
            influence=ExecutionInfluence.AUTONOMOUS,
        ),
        _task_resolution(
            units[1],
            attempts[1],
            verdict=Verdict.INCONCLUSIVE,
            influence=ExecutionInfluence.AUTONOMOUS,
        ),
    ]
    results.hygiene_resolutions = [
        _task_hygiene_resolution(
            units[0],
            attempts[0],
            hygiene=DataHygiene.CLEANED,
        ),
        _task_hygiene_resolution(
            units[1],
            attempts[1],
            hygiene=DataHygiene.LEAKED,
        ),
    ]
    outbox = _OutboxRepository()
    service = ResultProjectionService(
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        outbox_repository=cast(Any, outbox),
    )
    connection = cast(AsyncConnection[DictRow], object())

    snapshot = await service.snapshot_task_fully_resolved(
        connection,
        run=run,
        manifest=manifest,
        created_at=cast(Any, run.closed_at),
    )
    replay = await service.snapshot_task_fully_resolved(
        connection,
        run=run,
        manifest=manifest,
        created_at=cast(Any, run.closed_at),
    )
    quality_replay = await service.snapshot_task(
        connection,
        run=run,
        manifest=manifest,
        created_at=cast(Any, run.closed_at),
    )

    assert snapshot is not None
    assert snapshot.finality is TaskResultSnapshotFinality.FULLY_RESOLVED
    assert snapshot.revision == 2
    assert snapshot.axis_distributions.data_hygiene.cleaned == 1
    assert snapshot.axis_distributions.data_hygiene.leaked == 1
    assert snapshot.verdict_counts.passed == 1
    assert replay == snapshot
    assert quality_replay.finality is TaskResultSnapshotFinality.QUALITY_FINAL
    assert quality_replay.revision == 1
    assert len(results.snapshots) == 2
    assert [event.event_type for event in outbox.events] == [
        "task.snapshot_created",
        "task.snapshot_created",
    ]


@pytest.mark.anyio
async def test_fully_resolved_snapshot_waits_for_terminal_hygiene() -> None:
    raw_run, manifest, raw_units, attempts = _aggregate(unit_count=1)
    run = _closed_snapshot_run(raw_run, unit_count=1)
    unit = _closed_snapshot_unit(
        raw_units[0],
        quality=ExecutionQuality.INCONCLUSIVE,
    )
    tasks = _TaskRepository(attempts)
    tasks.units = [unit]
    results = _ResultRepository()
    results.bind_attempts(tasks.attempts)
    results.bind_units(tasks.units)
    results.resolutions = [
        _task_resolution(
            unit,
            attempts[0],
            verdict=Verdict.INCONCLUSIVE,
            influence=ExecutionInfluence.AUTONOMOUS,
        )
    ]
    results.hygiene_resolutions = [
        _task_hygiene_resolution(
            unit,
            attempts[0],
            hygiene=DataHygiene.PENDING,
        )
    ]
    outbox = _OutboxRepository()
    service = ResultProjectionService(
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        outbox_repository=cast(Any, outbox),
    )

    snapshot = await service.snapshot_task_fully_resolved(
        cast(AsyncConnection[DictRow], object()),
        run=run,
        manifest=manifest,
        created_at=cast(Any, run.closed_at),
    )

    assert snapshot is None
    assert [item.finality for item in results.snapshots] == [
        TaskResultSnapshotFinality.QUALITY_FINAL
    ]
    assert len(outbox.events) == 1


@pytest.mark.anyio
async def test_task_snapshot_fails_closed_on_missing_unit_resolution() -> None:
    raw_run, manifest, raw_units, attempts = _aggregate(unit_count=1)
    run = _closed_snapshot_run(raw_run, unit_count=1)
    unit = _closed_snapshot_unit(
        raw_units[0],
        quality=ExecutionQuality.INCONCLUSIVE,
    )
    tasks = _TaskRepository(attempts)
    tasks.units = [unit]
    results = _ResultRepository()
    results.bind_attempts(tasks.attempts)
    results.bind_units(tasks.units)
    service = ResultProjectionService(
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        outbox_repository=cast(Any, _OutboxRepository()),
    )

    with pytest.raises(
        ResultProjectionError,
        match="RESULT_SNAPSHOT_UNIT_COVERAGE_INVALID",
    ):
        await service.snapshot_task(
            cast(AsyncConnection[DictRow], object()),
            run=run,
            manifest=manifest,
            created_at=cast(Any, run.closed_at),
        )


@pytest.mark.anyio
async def test_task_snapshot_requires_closed_and_materialization_sealed_run() -> None:
    raw_run, manifest, _, attempts = _aggregate(unit_count=1)
    tasks = _TaskRepository(attempts)
    results = _ResultRepository()
    results.bind_attempts(tasks.attempts)
    results.bind_units(tasks.units)
    service = ResultProjectionService(
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
        outbox_repository=cast(Any, _OutboxRepository()),
    )

    with pytest.raises(
        ResultProjectionError,
        match="RESULT_SNAPSHOT_RUN_NOT_CLOSED",
    ):
        await service.snapshot_task(
            cast(AsyncConnection[DictRow], object()),
            run=raw_run,
            manifest=manifest,
            created_at=NOW,
        )

    closed = _closed_snapshot_run(raw_run, unit_count=1).model_copy(
        update={
            "materialization_state": TaskMaterializationState.MATERIALIZING,
            "materialized_unit_count": None,
            "materialized_first_attempt_count": None,
            "materialization_sealed_at": None,
        }
    )
    with pytest.raises(
        ResultProjectionError,
        match="RESULT_SNAPSHOT_MANIFEST_INVALID",
    ):
        await service.snapshot_task(
            cast(AsyncConnection[DictRow], object()),
            run=closed,
            manifest=manifest,
            created_at=cast(Any, closed.closed_at),
        )
