"""Real PostgreSQL coverage for immutable AttemptSeal Result truth."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from os import environ
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg.errors import InsufficientPrivilege, RaiseException
from pydantic import SecretStr
from tests.integration.test_task_execution_hosts_pg import (
    SeededCaseVersion,
    TaskAggregate,
    _build_aggregate,
    _seed_published_case_version,
)
from tests.integration.test_task_orchestration_pg import _persist_sealed_aggregate

from atlas_testops.application.result_truth import (
    AttemptResultIntegrityConflict,
    FinalizeAttemptResultService,
    formal_attempt_runtime_digest,
)
from atlas_testops.application.task_orchestration import TaskWorkerService
from atlas_testops.core.config import Settings
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
    Stability,
    Verdict,
    attempt_seal_content_hash,
    attempt_seal_signing_bytes,
)
from atlas_testops.domain.task import (
    ExecutionLifecycle,
    ExecutionQuality,
    TaskUnitExecutionTicket,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.results import ResultFactRepository
from atlas_testops.infrastructure.repositories.task_execution_tickets import (
    TaskExecutionTicketRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository
from atlas_testops.infrastructure.result_signatures import (
    AttemptSealVerifier,
    encode_attempt_seal_signature,
)
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

_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
_PUBLIC_KEY = _PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
_SIGNING_KEY_ID = "atlas-result-integration-k1"


def test_attempt_seal_fact_replays_conflicts_and_recovers_passed_run() -> None:
    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)

    asyncio.run(_exercise_result_truth(settings, seeded))


async def _exercise_result_truth(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> None:
    database = Database(settings)
    aggregate = _build_aggregate(seeded)
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
        prepared = await worker.prepare_attempt(attempt_request)
        assert (await worker.start_attempt(attempt_request)).status == "READY"

        context = DatabaseContext(
            tenant_id=aggregate.run.tenant_id,
            request_id=f"attempt-seal-read:{aggregate.attempt.id}",
        )
        async with database.transaction(context) as connection:
            ticket = await TaskExecutionTicketRepository().get(
                connection,
                UUID(prepared.ticket_id),
            )
            running_attempt = await TaskRunRepository().get_attempt(
                connection,
                aggregate.attempt.id,
            )
        assert ticket is not None
        assert running_attempt is not None
        invalid_runtime_seal = _signed_seal(
            aggregate=aggregate,
            ticket=ticket,
            sealed_at=datetime.now(UTC),
            runtime_digest="sha256:" + "f" * 64,
        )
        with pytest.raises(RaiseException, match="policy or runtime binding"):
            async with database.transaction(context) as connection:
                cursor = await connection.execute("select transaction_timestamp() as observed_at")
                row = await cursor.fetchone()
                assert row is not None
                await ResultFactRepository().insert_fact(
                    connection,
                    seal=invalid_runtime_seal,
                    accepted_at=row["observed_at"],
                )
        invalid_hygiene_seal = _signed_seal(
            aggregate=aggregate,
            ticket=ticket,
            sealed_at=datetime.now(UTC),
            data_hygiene=DataHygiene.CLEANED,
        )
        with pytest.raises(RaiseException, match="exact active UnitAttempt"):
            async with database.transaction(context) as connection:
                cursor = await connection.execute("select transaction_timestamp() as observed_at")
                row = await cursor.fetchone()
                assert row is not None
                await ResultFactRepository().insert_fact(
                    connection,
                    seal=invalid_hygiene_seal,
                    accepted_at=row["observed_at"],
                )
        async with database.transaction(context) as connection:
            cursor = await connection.execute("select transaction_timestamp() as observed_at")
            row = await cursor.fetchone()
            assert row is not None
            sealed_at = row["observed_at"]
        seal = _signed_seal(
            aggregate=aggregate,
            ticket=ticket,
            sealed_at=sealed_at,
        )
        finalizer = FinalizeAttemptResultService(
            database,
            AttemptSealVerifier({_SIGNING_KEY_ID: _PUBLIC_KEY}),
        )

        result_ref = await finalizer.finalize(aggregate.run.tenant_id, seal)
        assert await finalizer.finalize(aggregate.run.tenant_id, seal) == result_ref

        conflicting = _signed_seal(
            aggregate=aggregate,
            ticket=ticket,
            sealed_at=seal.sealed_at,
            seal_id=uuid4(),
            closure_reason="ALTERNATE_VERIFIED_RESULT",
        )
        with pytest.raises(
            AttemptResultIntegrityConflict,
            match="ATTEMPT_SEAL_CONTENT_CONFLICT",
        ):
            await finalizer.finalize(aggregate.run.tenant_id, conflicting)

        recovered = await worker.finish_attempt(
            TaskAttemptFinishInput(
                attempt=attempt_request,
                execution=TaskAttemptExecutionPayload(
                    status="INCONCLUSIVE",
                    error_code="TASK_ATTEMPT_ACTIVITY_FAILED",
                ),
            )
        )
        assert recovered.status == "PASSED"
        assert recovered.result_ref_id == str(result_ref.id)
        assert recovered.seal_content_hash == seal.content_hash

        settled = await worker.settle_attempt_batch(
            TaskAttemptBatchSettleInput(request=root, outcomes=(recovered,))
        )
        assert settled.final_outcomes == (recovered,)
        run_result = await worker.finish_run(
            TaskRunFinishInput(
                request=root,
                outcomes=(recovered,),
                cancel_requested=False,
                skipped_units=0,
            )
        )
        assert run_result.status == "PASSED"

        result_repository = ResultFactRepository()
        async with database.transaction(context) as connection:
            stored_seal = await result_repository.get_seal_by_attempt(
                connection,
                aggregate.attempt.id,
            )
            stored_closure = await result_repository.get_closure_by_attempt(
                connection,
                aggregate.attempt.id,
            )
            stored_resolution = await result_repository.get_latest_resolution(
                connection,
                aggregate.unit.id,
            )
            stored_snapshot = await result_repository.get_latest_snapshot(
                connection,
                aggregate.run.id,
            )
            stored_ref = await result_repository.get_ref_by_attempt(
                connection,
                aggregate.attempt.id,
            )
            stored_run = await TaskRunRepository().get_run(connection, aggregate.run.id)
            stored_unit = await TaskRunRepository().get_unit(
                connection,
                aggregate.unit.id,
            )
            stored_attempt = await TaskRunRepository().get_attempt(
                connection,
                aggregate.attempt.id,
            )
            incident_count = await (
                await connection.execute(
                    """
                        select count(*) as incident_count
                        from atlas.result_integrity_incident
                        where unit_attempt_id = %s
                        """,
                    (aggregate.attempt.id,),
                )
            ).fetchone()
        assert stored_seal == seal
        assert stored_closure is None
        assert stored_ref == result_ref
        assert stored_resolution is not None
        assert stored_resolution.revision == 1
        assert stored_resolution.input_seal_ids == (seal.seal_id,)
        assert stored_resolution.input_closure_notice_ids == ()
        assert stored_resolution.effective_verdict is Verdict.PASSED
        assert stored_resolution.stability is Stability.STABLE
        assert stored_resolution.decisive_unit_attempt_id == aggregate.attempt.id
        assert stored_snapshot is not None
        assert stored_snapshot.unit_resolution_revision_ids == (stored_resolution.id,)
        assert stored_snapshot.verdict_counts.passed == 1
        assert stored_snapshot.raw_pass_rate.numerator == 1
        assert stored_snapshot.autonomous_pass_rate.numerator == 1
        assert incident_count is not None and incident_count["incident_count"] == 1
        assert all(
            projection is not None
            and projection.lifecycle is ExecutionLifecycle.CLOSED
            and projection.quality is ExecutionQuality.PASSED
            for projection in (stored_run, stored_unit, stored_attempt)
        )

        with pytest.raises(InsufficientPrivilege):
            async with database.transaction(context) as connection:
                await connection.execute(
                    """
                    delete from atlas.unit_attempt_result_fact
                    where unit_attempt_id = %s
                    """,
                    (aggregate.attempt.id,),
                )
    finally:
        await database.close()


def _signed_seal(
    *,
    aggregate: TaskAggregate,
    ticket: TaskUnitExecutionTicket,
    sealed_at: datetime,
    seal_id: UUID | None = None,
    closure_reason: str = "REQUIRED_ORACLES_PASSED",
    runtime_digest: str | None = None,
    data_hygiene: DataHygiene = DataHygiene.PENDING,
) -> AttemptSeal:
    content = AttemptSealContent(
        seal_id=seal_id if seal_id is not None else uuid4(),
        tenant_id=aggregate.run.tenant_id,
        project_id=aggregate.run.project_id,
        task_run_id=aggregate.run.id,
        execution_unit_id=aggregate.unit.id,
        unit_attempt_id=aggregate.attempt.id,
        manifest_id=aggregate.manifest.task_run_id,
        manifest_hash=aggregate.manifest.manifest_hash,
        unit_key=aggregate.unit.unit_key,
        execution_ticket_id=ticket.id,
        execution_ticket_digest=ticket.ticket_digest,
        oracle_verdict=Verdict.PASSED,
        outcome_class=OutcomeClass.BUSINESS,
        closure_reason=closure_reason,
        data_hygiene=data_hygiene,
        evidence_completeness=EvidenceCompleteness.COMPLETE,
        evidence_integrity=EvidenceIntegrity.VERIFIED,
        execution_influence=ExecutionInfluence.AUTONOMOUS,
        stability=Stability.STABLE,
        oracle_results_hash="sha256:" + "a" * 64,
        artifact_manifest_hash="sha256:" + "b" * 64,
        event_chain=AttemptEventChain(
            head="sha256:" + "c" * 64,
            event_count=4,
        ),
        evidence_policy_digest=next(iter(aggregate.manifest.policy_digests.values())),
        runtime_digest=runtime_digest or formal_attempt_runtime_digest(ticket),
        sealed_at=sealed_at,
        signature=AttemptSealSignature(kid=_SIGNING_KEY_ID),
    )
    signature = _PRIVATE_KEY.sign(attempt_seal_signing_bytes(content))
    return AttemptSeal(
        **content.model_dump(mode="python"),
        signature_value=encode_attempt_seal_signature(signature),
        content_hash=attempt_seal_content_hash(content),
    )
