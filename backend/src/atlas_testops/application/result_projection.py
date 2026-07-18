"""Deterministic append-only Result projection over terminal Attempt facts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.core.contracts import new_entity_id
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.result import (
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST,
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION,
    TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
    TASK_RESULT_SNAPSHOT_POLICY_DIGEST,
    TASK_RESULT_SNAPSHOT_POLICY_VERSION,
    UNIT_RESOLUTION_POLICY_DIGEST,
    UNIT_RESOLUTION_POLICY_VERSION,
    AttemptClosureNotice,
    AttemptClosureNoticeContent,
    AttemptClosureSourceStatus,
    AttemptSeal,
    DataHygiene,
    EvidenceCompleteness,
    EvidenceIntegrity,
    ExecutionInfluence,
    OutcomeClass,
    ResultPassRate,
    Stability,
    TaskDataHygieneCounts,
    TaskEvidenceCompletenessCounts,
    TaskEvidenceIntegrityCounts,
    TaskExecutionInfluenceCounts,
    TaskOutcomeClassCounts,
    TaskResultAxisDistributions,
    TaskResultSnapshot,
    TaskResultSnapshotContent,
    TaskResultSnapshotFinality,
    TaskStabilityCounts,
    TaskVerdictCounts,
    UnitHygieneResolutionRevision,
    UnitResolutionRevision,
    Verdict,
    attempt_closure_notice_hash,
    result_projection_digest,
    task_result_hygiene_resolution_set_hash,
    task_result_resolution_set_hash,
    task_result_snapshot_hash,
)
from atlas_testops.domain.task import (
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    TaskMaterializationState,
    TaskRun,
    TaskRunManifest,
    UnitAttempt,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.results import ResultFactRepository
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository

_MANIFEST_UNIT_FIELDS = (
    "ordinal",
    "unit_key",
    "case_version_id",
    "execution_profile_version_id",
    "fixture_blueprint_version_id",
    "identity_profile_version_id",
    "environment_id",
    "browser_profile_version_id",
    "data_profile_version_id",
    "parameter_digest",
    "dependency_digest",
)


class ResultProjectionError(RuntimeError):
    """Safe deterministic projection failure suitable for worker boundaries."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


@dataclass(frozen=True, slots=True)
class _ResolutionSource:
    attempt: UnitAttempt
    fact_id: UUID
    fact_hash: str
    fact_kind: str
    effective_verdict: Verdict
    outcome_class: OutcomeClass
    closure_reason: str
    data_hygiene: DataHygiene
    evidence_completeness: EvidenceCompleteness
    evidence_integrity: EvidenceIntegrity
    execution_influence: ExecutionInfluence
    failure_fingerprint: str | None


class ResultProjectionService:
    """Append ClosureNotice and UnitResolution facts inside caller transactions."""

    def __init__(
        self,
        *,
        result_repository: ResultFactRepository | None = None,
        task_repository: TaskRunRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self._results = result_repository or ResultFactRepository()
        self._tasks = task_repository or TaskRunRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def close_without_seal(
        self,
        connection: AsyncConnection[DictRow],
        *,
        unit: ExecutionUnit,
        attempt: UnitAttempt,
        source_status: AttemptClosureSourceStatus,
        closure_reason: str,
        created_at: datetime,
    ) -> UnitResolutionRevision:
        """Record an exact no-Seal terminal fact and resolve its logical Unit."""

        self._require_attempt_scope(unit, attempt)
        if attempt.lifecycle is not ExecutionLifecycle.CLOSED or attempt.closed_at is None:
            raise ResultProjectionError("RESULT_CLOSURE_ATTEMPT_NOT_CLOSED")
        _require_closure_source(attempt, source_status)
        if await self._results.get_seal_by_attempt(connection, attempt.id) is not None:
            raise ResultProjectionError("RESULT_CLOSURE_SEAL_ALREADY_EXISTS")

        existing = await self._results.get_closure_by_attempt(connection, attempt.id)
        if existing is None:
            notice = _build_closure_notice(
                attempt,
                source_status=source_status,
                closure_reason=closure_reason,
                created_at=created_at,
            )
            await self._results.insert_closure(connection, notice)
        else:
            _require_exact_closure(
                existing,
                attempt=attempt,
                source_status=source_status,
                closure_reason=closure_reason,
            )
        return await self.resolve_unit(
            connection,
            unit=unit,
            created_at=created_at,
        )

    async def resolve_unit(
        self,
        connection: AsyncConnection[DictRow],
        *,
        unit: ExecutionUnit,
        created_at: datetime,
    ) -> UnitResolutionRevision:
        """Append a new revision only when the exact terminal input set changed."""

        attempts = await self._tasks.list_attempts(connection, unit.id)
        seals = {
            seal.unit_attempt_id: seal
            for seal in await self._results.list_seals_for_unit(connection, unit.id)
        }
        closures = {
            notice.unit_attempt_id: notice
            for notice in await self._results.list_closures_for_unit(
                connection,
                unit.id,
            )
        }
        sources: list[_ResolutionSource] = []
        for attempt in attempts:
            self._require_attempt_scope(unit, attempt)
            if attempt.lifecycle is not ExecutionLifecycle.CLOSED:
                continue
            seal = seals.get(attempt.id)
            closure = closures.get(attempt.id)
            if (seal is None) == (closure is None):
                raise ResultProjectionError("RESULT_TERMINAL_COVERAGE_INVALID")
            sources.append(
                _source_from_seal(attempt, seal)
                if seal is not None
                else _source_from_closure(attempt, closure)
            )
        if not sources:
            raise ResultProjectionError("RESULT_RESOLUTION_INPUT_MISSING")

        input_set_hash = _input_set_hash(unit, sources)
        latest = await self._results.get_latest_resolution(connection, unit.id)
        if latest is not None and (
            latest.input_set_hash == input_set_hash
            and latest.resolution_policy_digest == UNIT_RESOLUTION_POLICY_DIGEST
        ):
            return latest

        decisive = sources[-1]
        resolution = UnitResolutionRevision(
            id=new_entity_id(),
            unit_resolution_id=(
                latest.unit_resolution_id if latest is not None else new_entity_id()
            ),
            tenant_id=unit.tenant_id,
            project_id=unit.project_id,
            task_run_id=unit.task_run_id,
            execution_unit_id=unit.id,
            manifest_hash=unit.manifest_hash,
            unit_key=unit.unit_key,
            revision=(latest.revision + 1 if latest is not None else 1),
            input_seal_ids=tuple(
                source.fact_id for source in sources if source.fact_kind == "SEAL"
            ),
            input_closure_notice_ids=tuple(
                source.fact_id for source in sources if source.fact_kind == "CLOSURE_NOTICE"
            ),
            input_set_hash=input_set_hash,
            effective_verdict=decisive.effective_verdict,
            outcome_class=decisive.outcome_class,
            closure_reason=decisive.closure_reason,
            data_hygiene=decisive.data_hygiene,
            evidence_completeness=decisive.evidence_completeness,
            evidence_integrity=decisive.evidence_integrity,
            execution_influence=decisive.execution_influence,
            stability=_resolve_stability(sources),
            decisive_unit_attempt_id=decisive.attempt.id,
            decisive_attempt_number=decisive.attempt.attempt_number,
            resolution_policy_version=UNIT_RESOLUTION_POLICY_VERSION,
            resolution_policy_digest=UNIT_RESOLUTION_POLICY_DIGEST,
            supersedes_revision_id=(latest.id if latest is not None else None),
            created_at=created_at,
        )
        await self._results.insert_resolution(connection, resolution)
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=resolution.tenant_id,
                aggregate_type="execution_unit",
                aggregate_id=resolution.execution_unit_id,
                event_type="unit.resolved",
                occurred_at=created_at,
                payload={
                    "unitResolutionRevisionId": str(resolution.id),
                    "revision": resolution.revision,
                    "inputSetHash": resolution.input_set_hash,
                    "effectiveVerdict": resolution.effective_verdict.value,
                    "stability": resolution.stability.value,
                },
            ),
        )
        return resolution

    async def snapshot_task(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: TaskRun,
        manifest: TaskRunManifest,
        created_at: datetime,
    ) -> TaskResultSnapshot:
        """Append one final Task projection only after exact Manifest coverage."""

        _, resolutions = await self._load_snapshot_quality_inputs(
            connection,
            run=run,
            manifest=manifest,
        )

        input_set_hash = task_result_resolution_set_hash(
            task_run_id=run.id,
            manifest_hash=run.manifest_hash,
            resolutions=resolutions,
        )
        latest_quality = await self._results.get_latest_snapshot_for_finality(
            connection,
            run.id,
            TaskResultSnapshotFinality.QUALITY_FINAL,
        )
        if latest_quality is not None and (
            latest_quality.input_resolution_set_hash == input_set_hash
            and latest_quality.aggregation_policy_digest == TASK_RESULT_SNAPSHOT_POLICY_DIGEST
        ):
            return latest_quality
        latest = await self._results.get_latest_snapshot(connection, run.id)
        if latest is not None and latest.finality in {
            TaskResultSnapshotFinality.FULLY_RESOLVED,
            TaskResultSnapshotFinality.REEVALUATED,
        }:
            raise ResultProjectionError("RESULT_SNAPSHOT_FINALITY_ORDER_INVALID")

        projection_watermark = max(resolution.created_at for resolution in resolutions)
        if created_at < projection_watermark:
            raise ResultProjectionError("RESULT_SNAPSHOT_WATERMARK_INVALID")
        verdict_counts = _verdict_counts(resolutions)
        trusted_passed = sum(
            resolution.effective_verdict is Verdict.PASSED
            and resolution.evidence_completeness is EvidenceCompleteness.COMPLETE
            and resolution.evidence_integrity is EvidenceIntegrity.VERIFIED
            for resolution in resolutions
        )
        autonomous_passed = sum(
            resolution.effective_verdict is Verdict.PASSED
            and resolution.execution_influence is ExecutionInfluence.AUTONOMOUS
            for resolution in resolutions
        )
        manifest_count = len(manifest.units)
        content = TaskResultSnapshotContent(
            id=new_entity_id(),
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            manifest_hash=run.manifest_hash,
            revision=(latest.revision + 1 if latest is not None else 1),
            finality=TaskResultSnapshotFinality.QUALITY_FINAL,
            unit_resolution_revision_ids=tuple(resolution.id for resolution in resolutions),
            input_resolution_set_hash=input_set_hash,
            aggregation_policy_version=TASK_RESULT_SNAPSHOT_POLICY_VERSION,
            aggregation_policy_digest=TASK_RESULT_SNAPSHOT_POLICY_DIGEST,
            projection_watermark=projection_watermark,
            manifest_count=manifest_count,
            verdict_counts=verdict_counts,
            axis_distributions=_axis_distributions(resolutions),
            raw_pass_rate=ResultPassRate(
                numerator=verdict_counts.passed,
                denominator=manifest_count,
            ),
            trusted_pass_rate=ResultPassRate(
                numerator=trusted_passed,
                denominator=manifest_count,
            ),
            autonomous_pass_rate=ResultPassRate(
                numerator=autonomous_passed,
                denominator=manifest_count,
            ),
            decisive_pass_rate=ResultPassRate(
                numerator=verdict_counts.passed,
                denominator=verdict_counts.passed + verdict_counts.failed,
            ),
            supersedes_snapshot_id=(latest.id if latest is not None else None),
            created_at=created_at,
        )
        snapshot = TaskResultSnapshot(
            **content.model_dump(mode="python"),
            snapshot_hash=task_result_snapshot_hash(content),
        )
        await self._append_snapshot(connection, snapshot)
        return snapshot

    async def snapshot_task_fully_resolved(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: TaskRun,
        manifest: TaskRunManifest,
        created_at: datetime,
    ) -> TaskResultSnapshot | None:
        """Append a Hygiene-bound revision after every Unit cleanup is terminal."""

        if run.lifecycle is not ExecutionLifecycle.CLOSED or run.closed_at is None:
            return None
        quality_snapshot = await self.snapshot_task(
            connection,
            run=run,
            manifest=manifest,
            created_at=created_at,
        )
        units, resolutions = await self._load_snapshot_quality_inputs(
            connection,
            run=run,
            manifest=manifest,
        )
        hygiene_resolutions = await self._results.list_latest_hygiene_resolutions_for_task(
            connection,
            run.id,
        )
        if len(hygiene_resolutions) != len(manifest.units):
            return None
        for unit, hygiene in zip(units, hygiene_resolutions, strict=True):
            if (
                hygiene.tenant_id != run.tenant_id
                or hygiene.project_id != run.project_id
                or hygiene.task_run_id != run.id
                or hygiene.execution_unit_id != unit.id
                or hygiene.manifest_hash != run.manifest_hash
                or hygiene.unit_key != unit.unit_key
            ):
                raise ResultProjectionError("RESULT_SNAPSHOT_HYGIENE_COVERAGE_INVALID")
        terminal_hygiene = {
            DataHygiene.CLEANED,
            DataHygiene.LEAKED,
            DataHygiene.NOT_APPLICABLE,
        }
        if any(
            resolution.data_hygiene not in terminal_hygiene for resolution in hygiene_resolutions
        ):
            return None

        input_hygiene_set_hash = task_result_hygiene_resolution_set_hash(
            task_run_id=run.id,
            manifest_hash=run.manifest_hash,
            resolutions=hygiene_resolutions,
        )
        latest_fully_resolved = await self._results.get_latest_snapshot_for_finality(
            connection,
            run.id,
            TaskResultSnapshotFinality.FULLY_RESOLVED,
        )
        if latest_fully_resolved is not None and (
            latest_fully_resolved.input_resolution_set_hash
            == quality_snapshot.input_resolution_set_hash
            and latest_fully_resolved.input_hygiene_resolution_set_hash == input_hygiene_set_hash
            and latest_fully_resolved.aggregation_policy_digest
            == TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST
        ):
            return latest_fully_resolved

        latest = await self._results.get_latest_snapshot(connection, run.id)
        if latest is None:
            raise ResultProjectionError("RESULT_SNAPSHOT_QUALITY_FINAL_MISSING")
        if latest.finality is TaskResultSnapshotFinality.REEVALUATED:
            raise ResultProjectionError("RESULT_SNAPSHOT_FINALITY_ORDER_INVALID")
        projection_watermark = max(
            *(resolution.created_at for resolution in resolutions),
            *(resolution.created_at for resolution in hygiene_resolutions),
        )
        if created_at < projection_watermark:
            raise ResultProjectionError("RESULT_SNAPSHOT_WATERMARK_INVALID")
        verdict_counts = _verdict_counts(resolutions)
        trusted_passed = sum(
            resolution.effective_verdict is Verdict.PASSED
            and resolution.evidence_completeness is EvidenceCompleteness.COMPLETE
            and resolution.evidence_integrity is EvidenceIntegrity.VERIFIED
            for resolution in resolutions
        )
        autonomous_passed = sum(
            resolution.effective_verdict is Verdict.PASSED
            and resolution.execution_influence is ExecutionInfluence.AUTONOMOUS
            for resolution in resolutions
        )
        manifest_count = len(manifest.units)
        content = TaskResultSnapshotContent(
            schema_version=TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_SCHEMA_VERSION,
            id=new_entity_id(),
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            manifest_hash=run.manifest_hash,
            revision=latest.revision + 1,
            finality=TaskResultSnapshotFinality.FULLY_RESOLVED,
            unit_resolution_revision_ids=tuple(resolution.id for resolution in resolutions),
            input_resolution_set_hash=quality_snapshot.input_resolution_set_hash,
            unit_hygiene_resolution_revision_ids=tuple(
                resolution.id for resolution in hygiene_resolutions
            ),
            input_hygiene_resolution_set_hash=input_hygiene_set_hash,
            aggregation_policy_version=(TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_VERSION),
            aggregation_policy_digest=(TASK_RESULT_SNAPSHOT_FULLY_RESOLVED_POLICY_DIGEST),
            projection_watermark=projection_watermark,
            manifest_count=manifest_count,
            verdict_counts=verdict_counts,
            axis_distributions=_axis_distributions(
                resolutions,
                hygiene_resolutions=hygiene_resolutions,
            ),
            raw_pass_rate=ResultPassRate(
                numerator=verdict_counts.passed,
                denominator=manifest_count,
            ),
            trusted_pass_rate=ResultPassRate(
                numerator=trusted_passed,
                denominator=manifest_count,
            ),
            autonomous_pass_rate=ResultPassRate(
                numerator=autonomous_passed,
                denominator=manifest_count,
            ),
            decisive_pass_rate=ResultPassRate(
                numerator=verdict_counts.passed,
                denominator=verdict_counts.passed + verdict_counts.failed,
            ),
            supersedes_snapshot_id=latest.id,
            created_at=created_at,
        )
        snapshot = TaskResultSnapshot(
            **content.model_dump(mode="python"),
            snapshot_hash=task_result_snapshot_hash(content),
        )
        await self._append_snapshot(connection, snapshot)
        return snapshot

    async def snapshot_task_fully_resolved_by_id(
        self,
        connection: AsyncConnection[DictRow],
        *,
        task_run_id: UUID,
        created_at: datetime,
    ) -> TaskResultSnapshot | None:
        """Lock and project a Task reached through a late Fixture cleanup event."""

        run = await self._tasks.get_run_for_update(connection, task_run_id)
        if run is None:
            raise ResultProjectionError("RESULT_SNAPSHOT_RUN_MISSING")
        if run.lifecycle is not ExecutionLifecycle.CLOSED or run.closed_at is None:
            return None
        manifest = await self._tasks.get_manifest(connection, task_run_id)
        if manifest is None:
            raise ResultProjectionError("RESULT_SNAPSHOT_MANIFEST_INVALID")
        return await self.snapshot_task_fully_resolved(
            connection,
            run=run,
            manifest=manifest,
            created_at=created_at,
        )

    async def _load_snapshot_quality_inputs(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: TaskRun,
        manifest: TaskRunManifest,
    ) -> tuple[tuple[ExecutionUnit, ...], tuple[UnitResolutionRevision, ...]]:
        if run.lifecycle is not ExecutionLifecycle.CLOSED or run.closed_at is None:
            raise ResultProjectionError("RESULT_SNAPSHOT_RUN_NOT_CLOSED")
        if (
            run.materialization_state is not TaskMaterializationState.SEALED
            or run.materialized_unit_count != len(manifest.units)
            or run.materialized_first_attempt_count != len(manifest.units)
            or manifest.task_run_id != run.id
            or manifest.tenant_id != run.tenant_id
            or manifest.project_id != run.project_id
            or manifest.manifest_hash != run.manifest_hash
        ):
            raise ResultProjectionError("RESULT_SNAPSHOT_MANIFEST_INVALID")

        units = await self._tasks.list_units(connection, run.id)
        resolutions = await self._results.list_latest_resolutions_for_task(
            connection,
            run.id,
        )
        if len(units) != len(manifest.units) or len(resolutions) != len(manifest.units):
            raise ResultProjectionError("RESULT_SNAPSHOT_UNIT_COVERAGE_INVALID")
        for ordinal, (manifest_unit, unit, resolution) in enumerate(
            zip(manifest.units, units, resolutions, strict=True),
            start=1,
        ):
            if (
                manifest_unit.ordinal != ordinal
                or any(
                    getattr(manifest_unit, field) != getattr(unit, field)
                    for field in _MANIFEST_UNIT_FIELDS
                )
                or unit.lifecycle is not ExecutionLifecycle.CLOSED
                or unit.tenant_id != run.tenant_id
                or unit.project_id != run.project_id
                or unit.task_run_id != run.id
                or unit.manifest_hash != run.manifest_hash
                or resolution.tenant_id != run.tenant_id
                or resolution.project_id != run.project_id
                or resolution.task_run_id != run.id
                or resolution.execution_unit_id != unit.id
                or resolution.manifest_hash != run.manifest_hash
                or resolution.unit_key != unit.unit_key
            ):
                raise ResultProjectionError("RESULT_SNAPSHOT_UNIT_COVERAGE_INVALID")
        return units, resolutions

    async def _append_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        snapshot: TaskResultSnapshot,
    ) -> None:
        await self._results.insert_snapshot(connection, snapshot)
        payload: dict[str, JsonValue] = {
            "resultSnapshotId": str(snapshot.id),
            "revision": snapshot.revision,
            "finality": snapshot.finality.value,
            "inputResolutionSetHash": snapshot.input_resolution_set_hash,
            "snapshotHash": snapshot.snapshot_hash,
            "projectionWatermark": snapshot.projection_watermark.isoformat(),
        }
        if snapshot.input_hygiene_resolution_set_hash is not None:
            payload["inputHygieneResolutionSetHash"] = snapshot.input_hygiene_resolution_set_hash
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=snapshot.tenant_id,
                aggregate_type="task_run",
                aggregate_id=snapshot.task_run_id,
                event_type="task.snapshot_created",
                occurred_at=snapshot.created_at,
                payload=payload,
            ),
        )

    @staticmethod
    def _require_attempt_scope(unit: ExecutionUnit, attempt: UnitAttempt) -> None:
        if (
            attempt.tenant_id != unit.tenant_id
            or attempt.project_id != unit.project_id
            or attempt.task_run_id != unit.task_run_id
            or attempt.execution_unit_id != unit.id
            or attempt.manifest_hash != unit.manifest_hash
            or attempt.unit_key != unit.unit_key
        ):
            raise ResultProjectionError("RESULT_ATTEMPT_SCOPE_INVALID")


def _verdict_counts(
    resolutions: tuple[UnitResolutionRevision, ...],
) -> TaskVerdictCounts:
    """Count final Verdict values without allowing PENDING into a final Snapshot."""

    counts = Counter(resolution.effective_verdict for resolution in resolutions)
    if counts[Verdict.PENDING]:
        raise ResultProjectionError("RESULT_SNAPSHOT_VERDICT_PENDING")
    return TaskVerdictCounts(
        passed=counts[Verdict.PASSED],
        failed=counts[Verdict.FAILED],
        inconclusive=counts[Verdict.INCONCLUSIVE],
        not_evaluated=counts[Verdict.NOT_EVALUATED],
    )


def _axis_distributions(
    resolutions: tuple[UnitResolutionRevision, ...],
    *,
    hygiene_resolutions: tuple[UnitHygieneResolutionRevision, ...] | None = None,
) -> TaskResultAxisDistributions:
    """Count every frozen Result axis over the same Manifest-conserving input set."""

    hygiene = Counter(
        resolution.data_hygiene
        for resolution in (hygiene_resolutions if hygiene_resolutions is not None else resolutions)
    )
    completeness = Counter(resolution.evidence_completeness for resolution in resolutions)
    integrity = Counter(resolution.evidence_integrity for resolution in resolutions)
    influence = Counter(resolution.execution_influence for resolution in resolutions)
    stability = Counter(resolution.stability for resolution in resolutions)
    outcome = Counter(resolution.outcome_class for resolution in resolutions)
    return TaskResultAxisDistributions(
        data_hygiene=TaskDataHygieneCounts(
            pending=hygiene[DataHygiene.PENDING],
            cleaned=hygiene[DataHygiene.CLEANED],
            cleanup_failed=hygiene[DataHygiene.CLEANUP_FAILED],
            leaked=hygiene[DataHygiene.LEAKED],
            not_applicable=hygiene[DataHygiene.NOT_APPLICABLE],
        ),
        evidence_completeness=TaskEvidenceCompletenessCounts(
            pending=completeness[EvidenceCompleteness.PENDING],
            complete=completeness[EvidenceCompleteness.COMPLETE],
            partial=completeness[EvidenceCompleteness.PARTIAL],
            missing=completeness[EvidenceCompleteness.MISSING],
            not_applicable=completeness[EvidenceCompleteness.NOT_APPLICABLE],
        ),
        evidence_integrity=TaskEvidenceIntegrityCounts(
            unverified=integrity[EvidenceIntegrity.UNVERIFIED],
            verified=integrity[EvidenceIntegrity.VERIFIED],
            invalid=integrity[EvidenceIntegrity.INVALID],
        ),
        execution_influence=TaskExecutionInfluenceCounts(
            autonomous=influence[ExecutionInfluence.AUTONOMOUS],
            manual_assisted=influence[ExecutionInfluence.MANUAL_ASSISTED],
            manual_only=influence[ExecutionInfluence.MANUAL_ONLY],
        ),
        stability=TaskStabilityCounts(
            unknown=stability[Stability.UNKNOWN],
            stable=stability[Stability.STABLE],
            infra_recovered=stability[Stability.INFRA_RECOVERED],
            flaky_suspect=stability[Stability.FLAKY_SUSPECT],
            flaky_confirmed=stability[Stability.FLAKY_CONFIRMED],
        ),
        outcome_class=TaskOutcomeClassCounts(
            business=outcome[OutcomeClass.BUSINESS],
            dependency=outcome[OutcomeClass.DEPENDENCY],
            platform=outcome[OutcomeClass.PLATFORM],
            user=outcome[OutcomeClass.USER],
            automation=outcome[OutcomeClass.AUTOMATION],
            policy=outcome[OutcomeClass.POLICY],
            unknown=outcome[OutcomeClass.UNKNOWN],
        ),
    )


def _build_closure_notice(
    attempt: UnitAttempt,
    *,
    source_status: AttemptClosureSourceStatus,
    closure_reason: str,
    created_at: datetime,
) -> AttemptClosureNotice:
    if attempt.closed_at is None:
        raise ResultProjectionError("RESULT_CLOSURE_TIME_MISSING")
    verdict = (
        Verdict.NOT_EVALUATED
        if source_status is AttemptClosureSourceStatus.CANCELED and attempt.started_at is None
        else Verdict.INCONCLUSIVE
    )
    outcome_class = {
        AttemptClosureSourceStatus.CANCELED: OutcomeClass.USER,
        AttemptClosureSourceStatus.INFRA_ERROR: OutcomeClass.PLATFORM,
        AttemptClosureSourceStatus.FINISHED_UNSEALED: OutcomeClass.AUTOMATION,
        AttemptClosureSourceStatus.FAILED: OutcomeClass.AUTOMATION,
        AttemptClosureSourceStatus.INCONCLUSIVE: OutcomeClass.UNKNOWN,
    }[source_status]
    content = AttemptClosureNoticeContent(
        id=new_entity_id(),
        tenant_id=attempt.tenant_id,
        project_id=attempt.project_id,
        task_run_id=attempt.task_run_id,
        execution_unit_id=attempt.execution_unit_id,
        unit_attempt_id=attempt.id,
        manifest_hash=attempt.manifest_hash,
        unit_key=attempt.unit_key,
        attempt_number=attempt.attempt_number,
        source_status=source_status,
        verdict=verdict,
        outcome_class=outcome_class,
        closure_reason=closure_reason,
        data_hygiene=_result_hygiene(attempt.hygiene),
        evidence_completeness=(
            EvidenceCompleteness.NOT_APPLICABLE
            if verdict is Verdict.NOT_EVALUATED
            else EvidenceCompleteness.MISSING
        ),
        evidence_integrity=EvidenceIntegrity.UNVERIFIED,
        execution_influence=ExecutionInfluence.AUTONOMOUS,
        closed_at=attempt.closed_at,
        created_at=created_at,
    )
    return AttemptClosureNotice(
        **content.model_dump(),
        notice_hash=attempt_closure_notice_hash(content),
    )


def _require_closure_source(
    attempt: UnitAttempt,
    source_status: AttemptClosureSourceStatus,
) -> None:
    allowed = {
        ExecutionQuality.INCONCLUSIVE: {
            AttemptClosureSourceStatus.FINISHED_UNSEALED,
            AttemptClosureSourceStatus.INCONCLUSIVE,
        },
        ExecutionQuality.FAILED: {AttemptClosureSourceStatus.FAILED},
        ExecutionQuality.INFRA_ERROR: {AttemptClosureSourceStatus.INFRA_ERROR},
        ExecutionQuality.CANCELED: {AttemptClosureSourceStatus.CANCELED},
    }.get(attempt.quality, set())
    if source_status not in allowed:
        raise ResultProjectionError("RESULT_CLOSURE_STATUS_CONFLICT")


def _require_exact_closure(
    notice: AttemptClosureNotice,
    *,
    attempt: UnitAttempt,
    source_status: AttemptClosureSourceStatus,
    closure_reason: str,
) -> None:
    _require_closure_source(attempt, source_status)
    if (
        notice.tenant_id != attempt.tenant_id
        or notice.project_id != attempt.project_id
        or notice.task_run_id != attempt.task_run_id
        or notice.execution_unit_id != attempt.execution_unit_id
        or notice.unit_attempt_id != attempt.id
        or notice.manifest_hash != attempt.manifest_hash
        or notice.unit_key != attempt.unit_key
        or notice.attempt_number != attempt.attempt_number
        or notice.source_status is not source_status
        or notice.closure_reason != closure_reason
        or notice.closed_at != attempt.closed_at
        or notice.data_hygiene is not _result_hygiene(attempt.hygiene)
    ):
        raise ResultProjectionError("RESULT_CLOSURE_REPLAY_CONFLICT")


def _source_from_seal(
    attempt: UnitAttempt,
    seal: AttemptSeal,
) -> _ResolutionSource:
    if (
        seal.tenant_id != attempt.tenant_id
        or seal.project_id != attempt.project_id
        or seal.task_run_id != attempt.task_run_id
        or seal.execution_unit_id != attempt.execution_unit_id
        or seal.unit_attempt_id != attempt.id
        or seal.manifest_hash != attempt.manifest_hash
        or seal.unit_key != attempt.unit_key
    ):
        raise ResultProjectionError("RESULT_SEAL_SCOPE_INVALID")
    effective_verdict = seal.oracle_verdict
    if seal.evidence_integrity is not EvidenceIntegrity.VERIFIED or seal.evidence_completeness in {
        EvidenceCompleteness.PENDING,
        EvidenceCompleteness.MISSING,
    }:
        effective_verdict = Verdict.INCONCLUSIVE
    return _ResolutionSource(
        attempt=attempt,
        fact_id=seal.seal_id,
        fact_hash=seal.content_hash,
        fact_kind="SEAL",
        effective_verdict=effective_verdict,
        outcome_class=seal.outcome_class,
        closure_reason=seal.closure_reason,
        data_hygiene=seal.data_hygiene,
        evidence_completeness=seal.evidence_completeness,
        evidence_integrity=seal.evidence_integrity,
        execution_influence=seal.execution_influence,
        failure_fingerprint=(
            f"{seal.closure_reason}:{seal.oracle_results_hash}"
            if effective_verdict is Verdict.FAILED
            else None
        ),
    )


def _source_from_closure(
    attempt: UnitAttempt,
    notice: AttemptClosureNotice | None,
) -> _ResolutionSource:
    if notice is None:
        raise ResultProjectionError("RESULT_CLOSURE_INPUT_MISSING")
    _require_exact_closure(
        notice,
        attempt=attempt,
        source_status=notice.source_status,
        closure_reason=notice.closure_reason,
    )
    return _ResolutionSource(
        attempt=attempt,
        fact_id=notice.id,
        fact_hash=notice.notice_hash,
        fact_kind="CLOSURE_NOTICE",
        effective_verdict=notice.verdict,
        outcome_class=notice.outcome_class,
        closure_reason=notice.closure_reason,
        data_hygiene=notice.data_hygiene,
        evidence_completeness=notice.evidence_completeness,
        evidence_integrity=notice.evidence_integrity,
        execution_influence=notice.execution_influence,
        failure_fingerprint=None,
    )


def _input_set_hash(
    unit: ExecutionUnit,
    sources: list[_ResolutionSource],
) -> str:
    inputs: list[JsonValue] = [
        {
            "attemptNumber": source.attempt.attempt_number,
            "unitAttemptId": str(source.attempt.id),
            "kind": source.fact_kind,
            "factId": str(source.fact_id),
            "factHash": source.fact_hash,
        }
        for source in sources
    ]
    return result_projection_digest(
        {
            "schemaVersion": "atlas.unit-resolution-input-set/0.1",
            "executionUnitId": str(unit.id),
            "manifestHash": unit.manifest_hash,
            "unitKey": unit.unit_key,
            "inputs": inputs,
        }
    )


def _resolve_stability(sources: list[_ResolutionSource]) -> Stability:
    verdicts = tuple(source.effective_verdict for source in sources)
    if len(sources) == 1:
        return (
            Stability.STABLE
            if verdicts[0] in {Verdict.PASSED, Verdict.FAILED}
            else Stability.UNKNOWN
        )
    if verdicts[-1] is Verdict.PASSED:
        if Verdict.FAILED in verdicts[:-1]:
            return Stability.FLAKY_SUSPECT
        if any(source.outcome_class is OutcomeClass.PLATFORM for source in sources[:-1]):
            return Stability.INFRA_RECOVERED
        if all(verdict is Verdict.PASSED for verdict in verdicts):
            return Stability.STABLE
    if all(verdict is Verdict.FAILED for verdict in verdicts):
        fingerprints = {
            source.failure_fingerprint
            for source in sources
            if source.failure_fingerprint is not None
        }
        return Stability.STABLE if len(fingerprints) == 1 else Stability.UNKNOWN
    if Verdict.PASSED in verdicts and Verdict.FAILED in verdicts:
        return Stability.FLAKY_SUSPECT
    return Stability.UNKNOWN


def _result_hygiene(value: ExecutionHygiene) -> DataHygiene:
    return {
        ExecutionHygiene.NOT_REQUIRED: DataHygiene.NOT_APPLICABLE,
        ExecutionHygiene.PENDING: DataHygiene.PENDING,
        ExecutionHygiene.RUNNING: DataHygiene.PENDING,
        ExecutionHygiene.CLEANED: DataHygiene.CLEANED,
        ExecutionHygiene.CLEANUP_FAILED: DataHygiene.CLEANUP_FAILED,
        ExecutionHygiene.LEAKED: DataHygiene.LEAKED,
    }[value]


__all__ = ["ResultProjectionError", "ResultProjectionService"]
