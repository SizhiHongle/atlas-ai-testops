"""Trusted AttemptSeal finalization for formal UnitAttempt execution."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.result_hygiene import (
    ResultHygieneProjectionError,
    ResultHygieneProjectionService,
)
from atlas_testops.application.result_projection import (
    ResultProjectionError,
    ResultProjectionService,
)
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.domain.case import canonical_digest
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.result import (
    AttemptSeal,
    DataHygiene,
    ResultIntegrityIncident,
    ResultRef,
    Verdict,
)
from atlas_testops.domain.task import (
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskRun,
    TaskRunManifest,
    TaskUnitExecutionTicket,
    UnitAttempt,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.results import ResultFactRepository
from atlas_testops.infrastructure.repositories.task_execution_tickets import (
    TaskExecutionTicketRepository,
)
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository


class AttemptSealVerificationPort(Protocol):
    """Signature verification boundary supplied by trusted deployment code."""

    def verify(self, seal: AttemptSeal) -> None: ...


class AttemptResultFinalizationError(RuntimeError):
    """Safe permanent finalization error suitable for an Activity boundary."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class AttemptResultIntegrityConflict(AttemptResultFinalizationError):
    """A different valid Seal was presented for an already sealed Attempt."""


class FinalizeAttemptResultService:
    """Atomically accept one signed Seal, close its Attempt, and emit ResultRef."""

    def __init__(
        self,
        database: Database,
        verifier: AttemptSealVerificationPort,
        *,
        task_repository: TaskRunRepository | None = None,
        state_repository: TaskExecutionStateRepository | None = None,
        ticket_repository: TaskExecutionTicketRepository | None = None,
        result_repository: ResultFactRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        projection_service: ResultProjectionService | None = None,
        hygiene_projection_service: ResultHygieneProjectionService | None = None,
    ) -> None:
        self._database = database
        self._verifier = verifier
        self._tasks = task_repository or TaskRunRepository()
        self._state = state_repository or TaskExecutionStateRepository()
        self._tickets = ticket_repository or TaskExecutionTicketRepository()
        self._results = result_repository or ResultFactRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._projection = projection_service or ResultProjectionService(
            result_repository=self._results,
            task_repository=self._tasks,
            outbox_repository=self._outbox,
        )
        self._hygiene_projection = hygiene_projection_service

    async def finalize(self, tenant_id: UUID, seal: AttemptSeal) -> ResultRef:
        """Accept exact replay or persist one new trusted terminal fact."""

        if seal.tenant_id != tenant_id:
            raise AttemptResultFinalizationError("ATTEMPT_SEAL_TENANT_MISMATCH")
        try:
            self._verifier.verify(seal)
        except ValueError as error:
            raise AttemptResultFinalizationError("ATTEMPT_SEAL_SIGNATURE_INVALID") from error

        conflict = False
        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"attempt-result-finalize:{seal.unit_attempt_id}",
        )
        async with self._database.transaction(context) as connection:
            await self._tasks.lock_execution_chain(
                connection,
                task_run_id=seal.task_run_id,
                execution_unit_id=seal.execution_unit_id,
                unit_attempt_id=seal.unit_attempt_id,
            )
            run = await self._tasks.get_run(connection, seal.task_run_id)
            manifest = await self._tasks.get_manifest(connection, seal.task_run_id)
            unit = await self._tasks.get_unit(connection, seal.execution_unit_id)
            attempt = await self._tasks.get_attempt(connection, seal.unit_attempt_id)
            ticket = await self._tickets.get_by_attempt(connection, seal.unit_attempt_id)
            self._require_exact_scope(
                seal,
                run=run,
                manifest=manifest,
                unit=unit,
                attempt=attempt,
                ticket=ticket,
            )
            assert run is not None
            assert unit is not None
            assert attempt is not None

            existing = await self._results.get_seal_by_attempt(
                connection,
                seal.unit_attempt_id,
            )
            if existing is not None:
                result_ref = await self._results.get_ref_by_attempt(
                    connection,
                    seal.unit_attempt_id,
                )
                if result_ref is None:
                    raise AttemptResultFinalizationError("ATTEMPT_RESULT_REF_MISSING")
                if existing.content_hash == seal.content_hash:
                    self._require_exact_ref(result_ref, existing)
                    projected_at = await _database_now(connection)
                    await self._resolve_unit(
                        connection,
                        unit=unit,
                        created_at=projected_at,
                    )
                    return result_ref
                observed_at = await _database_now(connection)
                self._require_seal_time(
                    seal,
                    attempt=attempt,
                    accepted_at=observed_at,
                )
                await self._record_integrity_incident(
                    connection,
                    accepted=existing,
                    conflicting=seal,
                    observed_at=observed_at,
                )
                conflict = True
            else:
                if attempt.lifecycle is not ExecutionLifecycle.RUNNING:
                    raise AttemptResultFinalizationError("ATTEMPT_SEAL_NOT_ACTIVE")
                accepted_at = await _database_now(connection)
                self._require_seal_time(
                    seal,
                    attempt=attempt,
                    accepted_at=accepted_at,
                )
                if seal.data_hygiene is not _result_hygiene(attempt.hygiene):
                    raise AttemptResultFinalizationError("ATTEMPT_SEAL_HYGIENE_INVALID")
                result_ref = await self._accept_new_seal(
                    connection,
                    run=run,
                    unit=unit,
                    attempt=attempt,
                    seal=seal,
                    accepted_at=accepted_at,
                )
                return result_ref

        if conflict:
            raise AttemptResultIntegrityConflict("ATTEMPT_SEAL_CONTENT_CONFLICT")
        raise AttemptResultFinalizationError("ATTEMPT_SEAL_FINALIZATION_FAILED")

    async def _accept_new_seal(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: TaskRun,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
        seal: AttemptSeal,
        accepted_at: datetime,
    ) -> ResultRef:
        await self._results.insert_fact(
            connection,
            seal=seal,
            accepted_at=accepted_at,
        )
        result_ref = ResultRef(
            id=new_entity_id(),
            tenant_id=seal.tenant_id,
            project_id=seal.project_id,
            task_run_id=seal.task_run_id,
            execution_unit_id=seal.execution_unit_id,
            unit_attempt_id=seal.unit_attempt_id,
            seal_id=seal.seal_id,
            seal_content_hash=seal.content_hash,
            created_at=accepted_at,
        )
        await self._results.insert_ref(connection, result_ref)
        quality = _quality_for_verdict(seal.oracle_verdict)
        finalizing = await self._state.transition_unit_attempt_state(
            connection,
            task_run_id=run.id,
            execution_unit_id=unit.id,
            unit_attempt_id=attempt.id,
            expected_revision=attempt.revision,
            lifecycle=ExecutionLifecycle.FINALIZING,
            quality=quality,
            hygiene=attempt.hygiene,
            started_at=attempt.started_at,
            finalized_at=seal.sealed_at,
            cleanup_resolved_at=attempt.cleanup_resolved_at,
            closed_at=None,
        )
        if finalizing is None:
            raise AttemptResultFinalizationError("ATTEMPT_SEAL_TRANSITION_LOST")
        await self._append_task_event(
            connection,
            attempt=finalizing,
            event_type="unit_attempt.seal_accepted",
            payload={
                "schemaVersion": seal.schema_version,
                "resultRefId": str(result_ref.id),
                "sealId": str(seal.seal_id),
                "contentHash": seal.content_hash,
                "oracleVerdict": seal.oracle_verdict.value,
            },
            occurred_at=accepted_at,
        )
        closed = await self._state.transition_unit_attempt_state(
            connection,
            task_run_id=run.id,
            execution_unit_id=unit.id,
            unit_attempt_id=attempt.id,
            expected_revision=finalizing.revision,
            lifecycle=ExecutionLifecycle.CLOSED,
            quality=quality,
            hygiene=attempt.hygiene,
            started_at=attempt.started_at,
            finalized_at=seal.sealed_at,
            cleanup_resolved_at=attempt.cleanup_resolved_at,
            closed_at=seal.sealed_at,
        )
        if closed is None:
            raise AttemptResultFinalizationError("ATTEMPT_SEAL_CLOSE_LOST")
        await self._append_task_event(
            connection,
            attempt=closed,
            event_type="unit_attempt.closed",
            payload={"resultRefId": str(result_ref.id)},
            occurred_at=accepted_at,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=seal.tenant_id,
                aggregate_type="unit_attempt",
                aggregate_id=seal.unit_attempt_id,
                event_type="unit_attempt.seal_accepted",
                occurred_at=accepted_at,
                payload={
                    "resultRefId": str(result_ref.id),
                    "sealId": str(seal.seal_id),
                    "contentHash": seal.content_hash,
                },
            ),
        )
        await self._resolve_unit(
            connection,
            unit=unit,
            created_at=accepted_at,
        )
        return result_ref

    async def _resolve_unit(
        self,
        connection: AsyncConnection[DictRow],
        *,
        unit: ExecutionUnit,
        created_at: datetime,
    ) -> None:
        try:
            await self._projection.resolve_unit(
                connection,
                unit=unit,
                created_at=created_at,
            )
            if self._hygiene_projection is not None:
                await self._hygiene_projection.project_unit(
                    connection,
                    unit=unit,
                    created_at=created_at,
                )
        except ResultProjectionError as error:
            raise AttemptResultFinalizationError(error.error_code) from error
        except ResultHygieneProjectionError as error:
            raise AttemptResultFinalizationError(error.error_code) from error

    @staticmethod
    def _require_exact_scope(
        seal: AttemptSeal,
        *,
        run: TaskRun | None,
        manifest: TaskRunManifest | None,
        unit: ExecutionUnit | None,
        attempt: UnitAttempt | None,
        ticket: TaskUnitExecutionTicket | None,
    ) -> None:
        """Validate every current Task aggregate and frozen ticket binding."""

        if run is None or manifest is None or unit is None or attempt is None or ticket is None:
            raise AttemptResultFinalizationError("ATTEMPT_SEAL_SCOPE_MISSING")
        if (
            run.tenant_id != seal.tenant_id
            or run.project_id != seal.project_id
            or run.id != seal.task_run_id
            or run.materialization_state is not TaskMaterializationState.SEALED
            or run.manifest_hash != seal.manifest_hash
            or manifest.task_run_id != seal.manifest_id
            or manifest.manifest_hash != seal.manifest_hash
            or unit.tenant_id != seal.tenant_id
            or unit.project_id != seal.project_id
            or unit.task_run_id != seal.task_run_id
            or unit.id != seal.execution_unit_id
            or unit.manifest_hash != seal.manifest_hash
            or unit.unit_key != seal.unit_key
            or attempt.tenant_id != seal.tenant_id
            or attempt.project_id != seal.project_id
            or attempt.task_run_id != seal.task_run_id
            or attempt.execution_unit_id != seal.execution_unit_id
            or attempt.id != seal.unit_attempt_id
            or attempt.manifest_hash != seal.manifest_hash
            or attempt.unit_key != seal.unit_key
            or ticket.id != seal.execution_ticket_id
            or ticket.ticket_digest != seal.execution_ticket_digest
            or ticket.task_run_id != seal.task_run_id
            or ticket.execution_unit_id != seal.execution_unit_id
            or ticket.unit_attempt_id != seal.unit_attempt_id
            or seal.runtime_digest != formal_attempt_runtime_digest(ticket)
        ):
            raise AttemptResultFinalizationError("ATTEMPT_SEAL_SCOPE_INVALID")
        if seal.evidence_policy_digest not in manifest.policy_digests.values():
            raise AttemptResultFinalizationError("ATTEMPT_SEAL_POLICY_INVALID")

    @staticmethod
    def _require_seal_time(
        seal: AttemptSeal,
        *,
        attempt: UnitAttempt,
        accepted_at: datetime,
    ) -> None:
        """Validate a newly observed Seal against the immutable Attempt window."""

        if (
            attempt.started_at is None
            or seal.sealed_at < attempt.started_at
            or seal.sealed_at > attempt.execution_deadline
            or seal.sealed_at > accepted_at
        ):
            raise AttemptResultFinalizationError("ATTEMPT_SEAL_TIME_INVALID")

    @staticmethod
    def _require_exact_ref(result_ref: ResultRef, seal: AttemptSeal) -> None:
        if (
            result_ref.tenant_id != seal.tenant_id
            or result_ref.project_id != seal.project_id
            or result_ref.task_run_id != seal.task_run_id
            or result_ref.execution_unit_id != seal.execution_unit_id
            or result_ref.unit_attempt_id != seal.unit_attempt_id
            or result_ref.seal_id != seal.seal_id
            or result_ref.seal_content_hash != seal.content_hash
        ):
            raise AttemptResultFinalizationError("ATTEMPT_RESULT_REF_INVALID")

    async def _record_integrity_incident(
        self,
        connection: AsyncConnection[DictRow],
        *,
        accepted: AttemptSeal,
        conflicting: AttemptSeal,
        observed_at: datetime,
    ) -> None:
        incident = ResultIntegrityIncident(
            id=new_entity_id(),
            tenant_id=accepted.tenant_id,
            project_id=accepted.project_id,
            task_run_id=accepted.task_run_id,
            execution_unit_id=accepted.execution_unit_id,
            unit_attempt_id=accepted.unit_attempt_id,
            accepted_seal_id=accepted.seal_id,
            accepted_content_hash=accepted.content_hash,
            conflicting_seal_id=conflicting.seal_id,
            conflicting_content_hash=conflicting.content_hash,
            signature_kid=conflicting.signature.kid,
            observed_at=observed_at,
        )
        await self._results.append_integrity_incident(connection, incident)
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=incident.tenant_id,
                aggregate_type="unit_attempt",
                aggregate_id=incident.unit_attempt_id,
                event_type="unit_attempt.seal_integrity_conflict",
                occurred_at=observed_at,
                payload={
                    "acceptedSealId": str(incident.accepted_seal_id),
                    "acceptedContentHash": incident.accepted_content_hash,
                    "conflictingSealId": str(incident.conflicting_seal_id),
                    "conflictingContentHash": incident.conflicting_content_hash,
                },
            ),
        )

    async def _append_task_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        attempt: UnitAttempt,
        event_type: str,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> None:
        sequence = await self._state.next_task_execution_event_seq(
            connection,
            task_run_id=attempt.task_run_id,
        )
        await self._tasks.append_event(
            connection,
            TaskExecutionEvent(
                id=new_entity_id(),
                tenant_id=attempt.tenant_id,
                project_id=attempt.project_id,
                task_run_id=attempt.task_run_id,
                execution_unit_id=attempt.execution_unit_id,
                unit_attempt_id=attempt.id,
                seq=sequence,
                event_type=event_type,
                lifecycle=attempt.lifecycle,
                quality=attempt.quality,
                hygiene=attempt.hygiene,
                payload=payload,
                occurred_at=occurred_at,
            ),
        )


def formal_attempt_runtime_digest(ticket: TaskUnitExecutionTicket) -> str:
    """Bind the Seal to every reviewed runtime dependency in its ticket."""

    return canonical_digest(
        {
            "schemaVersion": "atlas.formal-attempt-runtime/0.1",
            "executionTicketId": str(ticket.id),
            "executionTicketDigest": ticket.ticket_digest,
            "testIrDigest": ticket.test_ir_digest,
            "planDigest": ticket.plan_digest,
            "compiledDigest": ticket.compiled_digest,
            "executionProfileDigest": ticket.execution_profile_digest,
            "identityProfileDigest": ticket.identity_profile_digest,
            "browserProfileDigest": ticket.browser_profile_digest,
            "dataProfileDigest": ticket.data_profile_digest,
            "fixtureBlueprintDigest": ticket.fixture_blueprint_digest,
            "environmentId": str(ticket.environment_id),
            "environmentRevision": ticket.environment_revision,
        }
    )


def _quality_for_verdict(verdict: Verdict) -> ExecutionQuality:
    if verdict is Verdict.PASSED:
        return ExecutionQuality.PASSED
    if verdict is Verdict.FAILED:
        return ExecutionQuality.FAILED
    return ExecutionQuality.INCONCLUSIVE


def _result_hygiene(hygiene: ExecutionHygiene) -> DataHygiene:
    if hygiene is ExecutionHygiene.NOT_REQUIRED:
        return DataHygiene.NOT_APPLICABLE
    if hygiene in {ExecutionHygiene.PENDING, ExecutionHygiene.RUNNING}:
        return DataHygiene.PENDING
    return DataHygiene(hygiene.value)


async def _database_now(connection: AsyncConnection[DictRow]) -> datetime:
    cursor = await connection.execute("select transaction_timestamp() as observed_at")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("database clock query returned no row")
    return cast(datetime, row["observed_at"])


__all__ = [
    "AttemptResultFinalizationError",
    "AttemptResultIntegrityConflict",
    "AttemptSealVerificationPort",
    "FinalizeAttemptResultService",
    "formal_attempt_runtime_digest",
]
