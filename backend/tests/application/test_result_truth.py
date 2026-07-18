"""Application tests for trusted AttemptSeal finalization."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from tests.application.test_task_orchestration import _Database
from tests.infrastructure.test_task_run_repository import NOW, _aggregate, _sealed_run

from atlas_testops.application.result_truth import (
    AttemptResultFinalizationError,
    AttemptResultIntegrityConflict,
    FinalizeAttemptResultService,
    formal_attempt_runtime_digest,
)
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.result import (
    AttemptEventChain,
    AttemptSeal,
    AttemptSealContent,
    AttemptSealSignature,
    DataHygiene,
    EvidenceCompleteness,
    EvidenceIntegrity,
    ExecutionInfluence,
    OutcomeClass,
    ResultIntegrityIncident,
    ResultRef,
    Stability,
    Verdict,
    attempt_seal_content_hash,
)
from atlas_testops.domain.task import (
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    TaskExecutionEvent,
    TaskRun,
    TaskRunManifest,
    TaskUnitExecutionTicket,
    UnitAttempt,
    task_unit_execution_ticket_digest,
)
from atlas_testops.infrastructure.database import Database

_DIGEST = "sha256:" + "a" * 64


class _Verifier:
    def __init__(self, error: ValueError | None = None) -> None:
        self.error = error
        self.calls = 0

    def verify(self, _seal: AttemptSeal) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class _TaskRepository:
    def __init__(
        self,
        run: TaskRun,
        manifest: TaskRunManifest,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
    ) -> None:
        self.run = run
        self.manifest = manifest
        self.unit = unit
        self.attempt = attempt
        self.events: list[TaskExecutionEvent] = []
        self.lock_calls = 0

    async def lock_execution_chain(self, *_args: object, **_kwargs: object) -> None:
        self.lock_calls += 1

    async def get_run(self, _connection: object, task_run_id: UUID) -> TaskRun | None:
        return self.run if self.run.id == task_run_id else None

    async def get_manifest(
        self,
        _connection: object,
        task_run_id: UUID,
    ) -> TaskRunManifest | None:
        return self.manifest if self.manifest.task_run_id == task_run_id else None

    async def get_unit(
        self,
        _connection: object,
        execution_unit_id: UUID,
    ) -> ExecutionUnit | None:
        return self.unit if self.unit.id == execution_unit_id else None

    async def get_attempt(
        self,
        _connection: object,
        unit_attempt_id: UUID,
    ) -> UnitAttempt | None:
        return self.attempt if self.attempt.id == unit_attempt_id else None

    async def append_event(
        self,
        _connection: object,
        event: TaskExecutionEvent,
    ) -> None:
        self.events.append(event)


class _StateRepository:
    def __init__(self, tasks: _TaskRepository) -> None:
        self.tasks = tasks
        self.transitions: list[ExecutionLifecycle] = []

    async def next_task_execution_event_seq(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> int:
        return len(self.tasks.events) + 1

    async def transition_unit_attempt_state(
        self,
        _connection: object,
        **values: Any,
    ) -> UnitAttempt | None:
        current = self.tasks.attempt
        if current.revision != values["expected_revision"]:
            return None
        self.transitions.append(values["lifecycle"])
        updated = current.model_copy(
            update={
                "lifecycle": values["lifecycle"],
                "quality": values["quality"],
                "hygiene": values["hygiene"],
                "started_at": values["started_at"],
                "finalized_at": values["finalized_at"],
                "cleanup_resolved_at": values["cleanup_resolved_at"],
                "closed_at": values["closed_at"],
                "revision": current.revision + 1,
                "updated_at": NOW + timedelta(minutes=current.revision + 3),
            }
        )
        self.tasks.attempt = updated
        return updated


class _TicketRepository:
    def __init__(self, ticket: TaskUnitExecutionTicket) -> None:
        self.ticket = ticket

    async def get_by_attempt(
        self,
        _connection: object,
        unit_attempt_id: UUID,
    ) -> TaskUnitExecutionTicket | None:
        return self.ticket if self.ticket.unit_attempt_id == unit_attempt_id else None


class _ResultRepository:
    def __init__(self) -> None:
        self.seal: AttemptSeal | None = None
        self.result_ref: ResultRef | None = None
        self.incidents: list[ResultIntegrityIncident] = []
        self.fact_inserts = 0
        self.ref_inserts = 0

    async def get_seal_by_attempt(
        self,
        _connection: object,
        unit_attempt_id: UUID,
    ) -> AttemptSeal | None:
        if self.seal is None or self.seal.unit_attempt_id != unit_attempt_id:
            return None
        return self.seal

    async def get_ref_by_attempt(
        self,
        _connection: object,
        unit_attempt_id: UUID,
    ) -> ResultRef | None:
        if self.result_ref is None or self.result_ref.unit_attempt_id != unit_attempt_id:
            return None
        return self.result_ref

    async def insert_fact(
        self,
        _connection: object,
        *,
        seal: AttemptSeal,
        accepted_at: object,
    ) -> None:
        assert accepted_at == NOW + timedelta(minutes=3)
        self.fact_inserts += 1
        self.seal = seal

    async def insert_ref(self, _connection: object, result_ref: ResultRef) -> None:
        self.ref_inserts += 1
        self.result_ref = result_ref

    async def append_integrity_incident(
        self,
        _connection: object,
        incident: ResultIntegrityIncident,
    ) -> None:
        self.incidents.append(incident)


class _OutboxRepository:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def append(self, _connection: object, event: DomainEvent) -> None:
        self.events.append(event)


class _ResultProjection:
    async def resolve_unit(self, *_args: object, **_kwargs: object) -> object:
        return object()


def _ticket(
    run: TaskRun,
    unit: ExecutionUnit,
    attempt: UnitAttempt,
) -> TaskUnitExecutionTicket:
    assert run.request_digest is not None
    values: dict[str, object] = {
        "tenant_id": run.tenant_id,
        "project_id": run.project_id,
        "task_run_id": run.id,
        "execution_unit_id": unit.id,
        "unit_attempt_id": attempt.id,
        "request_digest": run.request_digest,
        "manifest_hash": run.manifest_hash,
        "ordinal": unit.ordinal,
        "unit_key": unit.unit_key,
        "case_version_id": unit.case_version_id,
        "case_content_digest": _DIGEST,
        "test_ir_digest": _DIGEST,
        "plan_digest": _DIGEST,
        "compiled_digest": _DIGEST,
        "attempt_number": attempt.attempt_number,
        "execution_profile_version_id": unit.execution_profile_version_id,
        "execution_profile_digest": _DIGEST,
        "identity_profile_version_id": unit.identity_profile_version_id,
        "identity_profile_digest": _DIGEST,
        "browser_profile_version_id": unit.browser_profile_version_id,
        "browser_profile_digest": _DIGEST,
        "data_profile_version_id": unit.data_profile_version_id,
        "data_profile_digest": _DIGEST,
        "fixture_blueprint_version_id": unit.fixture_blueprint_version_id,
        "fixture_blueprint_digest": _DIGEST,
        "environment_id": unit.environment_id,
        "environment_revision": 1,
        "allowed_origins": ("https://example.test",),
        "execution_deadline": attempt.execution_deadline,
    }
    return TaskUnitExecutionTicket.model_validate(
        {
            "id": uuid4(),
            "created_at": NOW + timedelta(seconds=30),
            "ticket_digest": task_unit_execution_ticket_digest(**cast(Any, values)),
            **values,
        }
    )


def _seal(
    tasks: _TaskRepository,
    ticket: TaskUnitExecutionTicket,
    **overrides: object,
) -> AttemptSeal:
    policy_digest = next(iter(tasks.manifest.policy_digests.values()))
    content_values: dict[str, object] = {
        "seal_id": uuid4(),
        "tenant_id": tasks.run.tenant_id,
        "project_id": tasks.run.project_id,
        "task_run_id": tasks.run.id,
        "execution_unit_id": tasks.unit.id,
        "unit_attempt_id": tasks.attempt.id,
        "manifest_id": tasks.manifest.task_run_id,
        "manifest_hash": tasks.manifest.manifest_hash,
        "unit_key": tasks.unit.unit_key,
        "execution_ticket_id": ticket.id,
        "execution_ticket_digest": ticket.ticket_digest,
        "oracle_verdict": Verdict.PASSED,
        "outcome_class": OutcomeClass.BUSINESS,
        "closure_reason": "REQUIRED_ORACLES_PASSED",
        "data_hygiene": DataHygiene.PENDING,
        "evidence_completeness": EvidenceCompleteness.COMPLETE,
        "evidence_integrity": EvidenceIntegrity.VERIFIED,
        "execution_influence": ExecutionInfluence.AUTONOMOUS,
        "stability": Stability.STABLE,
        "oracle_results_hash": _DIGEST,
        "artifact_manifest_hash": _DIGEST,
        "event_chain": AttemptEventChain(head=_DIGEST, event_count=4),
        "evidence_policy_digest": policy_digest,
        "runtime_digest": formal_attempt_runtime_digest(ticket),
        "sealed_at": NOW + timedelta(minutes=2),
        "signature": AttemptSealSignature(kid="runtime-key-1"),
    }
    content_values.update(overrides)
    content = AttemptSealContent.model_validate(content_values)
    return AttemptSeal(
        **content.model_dump(mode="python"),
        signature_value="base64url:" + "A" * 86,
        content_hash=attempt_seal_content_hash(content),
    )


def _fixture(
    *,
    verifier: _Verifier | None = None,
) -> tuple[
    FinalizeAttemptResultService,
    _Verifier,
    _TaskRepository,
    _StateRepository,
    _ResultRepository,
    _OutboxRepository,
    TaskUnitExecutionTicket,
]:
    raw_run, manifest, units, attempts = _aggregate(unit_count=1)
    run = _sealed_run(raw_run, unit_count=1).model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": NOW + timedelta(minutes=1),
            "updated_at": NOW + timedelta(minutes=1),
        }
    )
    unit = units[0].model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": NOW + timedelta(minutes=1),
            "updated_at": NOW + timedelta(minutes=1),
        }
    )
    attempt = attempts[0].model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": NOW + timedelta(minutes=1),
            "updated_at": NOW + timedelta(minutes=1),
        }
    )
    tasks = _TaskRepository(run, manifest, unit, attempt)
    state = _StateRepository(tasks)
    ticket = _ticket(run, unit, attempt)
    tickets = _TicketRepository(ticket)
    results = _ResultRepository()
    outbox = _OutboxRepository()
    seal_verifier = verifier or _Verifier()
    service = FinalizeAttemptResultService(
        cast(Database, _Database(NOW + timedelta(minutes=3))),
        seal_verifier,
        task_repository=cast(Any, tasks),
        state_repository=cast(Any, state),
        ticket_repository=cast(Any, tickets),
        result_repository=cast(Any, results),
        outbox_repository=cast(Any, outbox),
        projection_service=cast(Any, _ResultProjection()),
    )
    return service, seal_verifier, tasks, state, results, outbox, ticket


@pytest.mark.anyio
async def test_finalize_persists_one_fact_ref_and_exact_replay() -> None:
    service, verifier, tasks, state, results, outbox, ticket = _fixture()
    seal = _seal(tasks, ticket)

    result_ref = await service.finalize(tasks.run.tenant_id, seal)
    tasks.attempt = tasks.attempt.model_copy(
        update={
            "hygiene": ExecutionHygiene.CLEANED,
            "cleanup_resolved_at": NOW + timedelta(minutes=4),
            "updated_at": NOW + timedelta(minutes=4),
        }
    )
    replay = await service.finalize(tasks.run.tenant_id, seal)

    assert replay == result_ref
    assert results.seal == seal
    assert results.result_ref == result_ref
    assert results.fact_inserts == 1
    assert results.ref_inserts == 1
    assert verifier.calls == 2
    assert state.transitions == [
        ExecutionLifecycle.FINALIZING,
        ExecutionLifecycle.CLOSED,
    ]
    assert tasks.attempt.lifecycle is ExecutionLifecycle.CLOSED
    assert tasks.attempt.quality is ExecutionQuality.PASSED
    assert [event.event_type for event in tasks.events] == [
        "unit_attempt.seal_accepted",
        "unit_attempt.closed",
    ]
    assert [event.event_type for event in outbox.events] == ["unit_attempt.seal_accepted"]


@pytest.mark.anyio
async def test_finalize_records_valid_content_conflict_without_overwrite() -> None:
    service, _, tasks, _, results, outbox, ticket = _fixture()
    accepted = _seal(tasks, ticket)
    accepted_ref = await service.finalize(tasks.run.tenant_id, accepted)
    conflicting = _seal(
        tasks,
        ticket,
        seal_id=uuid4(),
        closure_reason="ALTERNATE_VERIFIED_RESULT",
    )

    with pytest.raises(
        AttemptResultIntegrityConflict,
        match="ATTEMPT_SEAL_CONTENT_CONFLICT",
    ):
        await service.finalize(tasks.run.tenant_id, conflicting)

    assert results.seal == accepted
    assert results.result_ref == accepted_ref
    assert results.fact_inserts == 1
    assert results.ref_inserts == 1
    assert len(results.incidents) == 1
    assert results.incidents[0].conflicting_content_hash == conflicting.content_hash
    assert outbox.events[-1].event_type == "unit_attempt.seal_integrity_conflict"


@pytest.mark.anyio
async def test_finalize_rejects_out_of_window_conflict_without_incident() -> None:
    service, _, tasks, _, results, outbox, ticket = _fixture()
    accepted = _seal(tasks, ticket)
    await service.finalize(tasks.run.tenant_id, accepted)
    conflicting = _seal(
        tasks,
        ticket,
        seal_id=uuid4(),
        sealed_at=tasks.attempt.execution_deadline + timedelta(seconds=1),
    )

    with pytest.raises(
        AttemptResultFinalizationError,
        match="ATTEMPT_SEAL_TIME_INVALID",
    ):
        await service.finalize(tasks.run.tenant_id, conflicting)

    assert results.incidents == []
    assert [event.event_type for event in outbox.events] == ["unit_attempt.seal_accepted"]


@pytest.mark.anyio
async def test_finalize_rejects_invalid_signature_before_database_access() -> None:
    verifier = _Verifier(ValueError("untrusted"))
    service, _, tasks, _, results, outbox, ticket = _fixture(verifier=verifier)
    seal = _seal(tasks, ticket)

    with pytest.raises(
        AttemptResultFinalizationError,
        match="ATTEMPT_SEAL_SIGNATURE_INVALID",
    ):
        await service.finalize(tasks.run.tenant_id, seal)

    assert results.seal is None
    assert outbox.events == []
