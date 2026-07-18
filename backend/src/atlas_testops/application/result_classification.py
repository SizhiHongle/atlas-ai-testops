"""Snapshot-bound deterministic failure clustering and human classification review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.result import (
    FAILURE_CLASSIFICATION_POLICY_DIGEST,
    FAILURE_CLASSIFICATION_POLICY_VERSION,
    FAILURE_CLUSTER_POLICY_DIGEST,
    FAILURE_FINGERPRINT_VERSION,
    ClassificationAuthorKind,
    ClassificationJudgmentState,
    FailureClassificationRevision,
    FailureClassificationRevisionContent,
    FailureClusterRevision,
    FailureClusterRevisionContent,
    FailureDomain,
    FailureEvidenceKind,
    FailureEvidenceRef,
    FailureSignal,
    RequestFailureClassificationRevision,
    TaskResultSnapshot,
    UnitHygieneResolutionRevision,
    UnitResolutionRevision,
    failure_classification_revision_hash,
    failure_cluster_fingerprint,
    failure_cluster_revision_hash,
    failure_signal_for,
    is_diagnostic_failure,
    rule_classification_for_signal,
)
from atlas_testops.domain.task import (
    ExecutionLifecycle,
    TaskMaterializationState,
    TaskRun,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.results import ResultFactRepository
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository

FAILURE_CLASSIFICATION_IDEMPOTENCY_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class FailureClassificationBatch:
    """Created or replayed cluster and latest classification projections."""

    result_snapshot_id: UUID
    clusters: tuple[FailureClusterRevision, ...]
    classifications: tuple[FailureClassificationRevision, ...]


@dataclass(frozen=True, slots=True)
class _ClusterInput:
    """Manifest-ordered immutable source used by one deterministic cluster."""

    resolution: UnitResolutionRevision
    hygiene: UnitHygieneResolutionRevision | None


class ResultClassificationService:
    """Build conservative clusters and append independently reviewed judgments."""

    def __init__(
        self,
        database: Database,
        *,
        result_repository: ResultFactRepository | None = None,
        task_repository: TaskRunRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
    ) -> None:
        self._database = database
        self._results = result_repository or ResultFactRepository()
        self._tasks = task_repository or TaskRunRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()

    async def classify_snapshot(
        self,
        actor: ActorContext,
        result_snapshot_id: UUID,
    ) -> FailureClassificationBatch:
        """Create or replay the deterministic baseline outside Snapshot finalization."""

        async with self._database.transaction(actor.database_context()) as connection:
            snapshot = await self._results.get_snapshot_by_id(connection, result_snapshot_id)
            if snapshot is None:
                raise _not_found()
            run = await self._tasks.get_run_for_update(connection, snapshot.task_run_id)
            self._require_operable_run(actor, run, snapshot)
            await self._results.lock_failure_classification_snapshot(
                connection,
                snapshot.id,
            )
            now = await _database_now(connection)
            inputs = await self._load_snapshot_inputs(connection, snapshot)

            grouped: dict[str, tuple[FailureSignal, list[_ClusterInput]]] = {}
            for item in inputs:
                if not is_diagnostic_failure(item.resolution, item.hygiene):
                    continue
                signal = failure_signal_for(item.resolution, item.hygiene)
                fingerprint = failure_cluster_fingerprint(signal)
                if fingerprint not in grouped:
                    grouped[fingerprint] = (signal, [])
                grouped[fingerprint][1].append(item)

            clusters: list[FailureClusterRevision] = []
            classifications: list[FailureClassificationRevision] = []
            for fingerprint, (signal, affected) in grouped.items():
                cluster = await self._get_or_create_cluster(
                    connection,
                    snapshot=snapshot,
                    signal=signal,
                    fingerprint=fingerprint,
                    affected=tuple(affected),
                    created_at=now,
                )
                classification = await self._get_or_create_rule_classification(
                    connection,
                    actor=actor,
                    snapshot=snapshot,
                    cluster=cluster,
                    affected=tuple(affected),
                    created_at=now,
                )
                clusters.append(cluster)
                classifications.append(classification)
            return FailureClassificationBatch(
                result_snapshot_id=snapshot.id,
                clusters=tuple(clusters),
                classifications=tuple(classifications),
            )

    async def revise_classification(
        self,
        actor: ActorContext,
        failure_classification_id: UUID,
        request: RequestFailureClassificationRevision,
        *,
        idempotency_key: str,
    ) -> CommandResult[FailureClassificationRevision]:
        """Append one authorized human judgment without changing Result truth."""

        if idempotency_key != request.client_mutation_id:
            raise _invalid_request("Idempotency-Key 必须与 clientMutationId 完全一致。")
        if actor.actor_id is None:
            raise _forbidden("失败归因复核需要可信 Actor 身份。")
        request_payload: dict[str, JsonValue] = {
            "failureClassificationId": str(failure_classification_id),
            **request.model_dump(mode="json", by_alias=True),
        }
        request_hash = hash_request(request_payload)
        scope = f"failure-classifications.{failure_classification_id}.revisions"
        async with self._database.transaction(actor.database_context()) as connection:
            now = await _database_now(connection)
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=FAILURE_CLASSIFICATION_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=FailureClassificationRevision.model_validate(
                        reservation.cached_response.body
                    ),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )

            latest = await self._results.get_latest_failure_classification_for_update(
                connection,
                failure_classification_id,
            )
            if latest is None:
                raise _not_found()
            cluster = await self._results.get_failure_cluster_by_revision_id(
                connection,
                latest.failure_cluster_revision_id,
            )
            if cluster is None:
                raise _conflict("FailureClassification 缺少绑定的 Cluster Revision。")
            run = await self._tasks.get_run_for_update(connection, latest.task_run_id)
            self._require_reviewable_run(actor, run, latest)
            if request.expected_revision != latest.revision:
                raise _conflict("FailureClassification Revision 已变化，请刷新后重试。")
            if request.judgment_state is ClassificationJudgmentState.HUMAN_CONFIRMED and (
                request.failure_domain is not latest.failure_domain
                or request.hypothesis_code != latest.hypothesis_code
                or request.hypothesis != latest.hypothesis
            ):
                raise _invalid_request("HUMAN_CONFIRMED 不能改变已确认的归因内容。")

            content = FailureClassificationRevisionContent(
                id=new_entity_id(),
                failure_classification_id=latest.failure_classification_id,
                tenant_id=latest.tenant_id,
                project_id=latest.project_id,
                task_run_id=latest.task_run_id,
                result_snapshot_id=latest.result_snapshot_id,
                failure_cluster_revision_id=latest.failure_cluster_revision_id,
                revision=latest.revision + 1,
                failure_domain=request.failure_domain,
                hypothesis_code=request.hypothesis_code,
                hypothesis=request.hypothesis,
                confidence=request.confidence,
                supporting_evidence_refs=request.supporting_evidence_refs,
                contradicting_evidence_refs=request.contradicting_evidence_refs,
                evidence_gap_codes=request.evidence_gap_codes,
                judgment_state=request.judgment_state,
                author_kind=ClassificationAuthorKind.HUMAN,
                authored_by=actor.actor_id,
                classification_policy_version=FAILURE_CLASSIFICATION_POLICY_VERSION,
                classification_policy_digest=FAILURE_CLASSIFICATION_POLICY_DIGEST,
                client_mutation_id=request.client_mutation_id,
                supersedes_revision_id=latest.id,
                created_at=now,
            )
            classification = FailureClassificationRevision(
                **content.model_dump(mode="python"),
                classification_hash=failure_classification_revision_hash(content),
            )
            await self._results.insert_failure_classification(connection, classification)
            await self._record_classification(
                connection,
                actor=actor,
                cluster=cluster,
                classification=classification,
                occurred_at=now,
            )
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                response=CachedHttpResponse(
                    status_code=201,
                    body=classification.model_dump(mode="json", by_alias=True),
                ),
            )
            return CommandResult(value=classification, status_code=201, replayed=False)

    async def _load_snapshot_inputs(
        self,
        connection: AsyncConnection[DictRow],
        snapshot: TaskResultSnapshot,
    ) -> tuple[_ClusterInput, ...]:
        resolutions = await self._results.list_resolutions_by_ids(
            connection,
            snapshot.unit_resolution_revision_ids,
        )
        if len(resolutions) != snapshot.manifest_count or tuple(
            item.id for item in resolutions
        ) != snapshot.unit_resolution_revision_ids:
            raise _conflict("Snapshot 绑定的 UnitResolution 集合不完整。")

        hygiene_by_unit: dict[UUID, UnitHygieneResolutionRevision] = {}
        if snapshot.unit_hygiene_resolution_revision_ids is not None:
            hygiene = await self._results.list_hygiene_resolutions_by_ids(
                connection,
                snapshot.unit_hygiene_resolution_revision_ids,
            )
            if len(hygiene) != snapshot.manifest_count or tuple(
                item.id for item in hygiene
            ) != snapshot.unit_hygiene_resolution_revision_ids:
                raise _conflict("Snapshot 绑定的 Hygiene Resolution 集合不完整。")
            hygiene_by_unit = {item.execution_unit_id: item for item in hygiene}

        inputs: list[_ClusterInput] = []
        for resolution in resolutions:
            if (
                resolution.tenant_id != snapshot.tenant_id
                or resolution.project_id != snapshot.project_id
                or resolution.task_run_id != snapshot.task_run_id
                or resolution.manifest_hash != snapshot.manifest_hash
            ):
                raise _conflict("Snapshot 与 UnitResolution scope 不一致。")
            hygiene_resolution = hygiene_by_unit.get(resolution.execution_unit_id)
            if hygiene_resolution is not None and (
                hygiene_resolution.tenant_id != snapshot.tenant_id
                or hygiene_resolution.project_id != snapshot.project_id
                or hygiene_resolution.task_run_id != snapshot.task_run_id
                or hygiene_resolution.manifest_hash != snapshot.manifest_hash
            ):
                raise _conflict("Snapshot 与 Hygiene Resolution scope 不一致。")
            inputs.append(
                _ClusterInput(
                    resolution=resolution,
                    hygiene=hygiene_resolution,
                )
            )
        return tuple(inputs)

    async def _get_or_create_cluster(
        self,
        connection: AsyncConnection[DictRow],
        *,
        snapshot: TaskResultSnapshot,
        signal: FailureSignal,
        fingerprint: str,
        affected: tuple[_ClusterInput, ...],
        created_at: datetime,
    ) -> FailureClusterRevision:
        existing = await self._results.get_failure_cluster(
            connection,
            result_snapshot_id=snapshot.id,
            fingerprint=fingerprint,
            policy_digest=FAILURE_CLUSTER_POLICY_DIGEST,
        )
        if existing is not None:
            return existing
        affected_ids = tuple(item.resolution.id for item in affected)
        content = FailureClusterRevisionContent(
            id=new_entity_id(),
            failure_cluster_id=new_entity_id(),
            tenant_id=snapshot.tenant_id,
            project_id=snapshot.project_id,
            task_run_id=snapshot.task_run_id,
            result_snapshot_id=snapshot.id,
            revision=1,
            fingerprint_version=FAILURE_FINGERPRINT_VERSION,
            fingerprint_policy_digest=FAILURE_CLUSTER_POLICY_DIGEST,
            fingerprint=fingerprint,
            signal=signal,
            affected_unit_resolution_revision_ids=affected_ids,
            affected_count=len(affected_ids),
            representative_unit_resolution_revision_id=affected_ids[0],
            projection_watermark=snapshot.projection_watermark,
            created_at=created_at,
        )
        cluster = FailureClusterRevision(
            **content.model_dump(mode="python"),
            cluster_hash=failure_cluster_revision_hash(content),
        )
        await self._results.insert_failure_cluster(connection, cluster)
        return cluster

    async def _get_or_create_rule_classification(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        snapshot: TaskResultSnapshot,
        cluster: FailureClusterRevision,
        affected: tuple[_ClusterInput, ...],
        created_at: datetime,
    ) -> FailureClassificationRevision:
        existing = await self._results.get_latest_failure_classification_for_cluster(
            connection,
            cluster.id,
        )
        if existing is not None:
            return existing
        hypothesis_code, hypothesis, confidence, gaps = rule_classification_for_signal(
            cluster.signal
        )
        representative = next(
            item
            for item in affected
            if item.resolution.id == cluster.representative_unit_resolution_revision_id
        )
        evidence = [
            FailureEvidenceRef(
                kind=FailureEvidenceKind.UNIT_RESOLUTION,
                ref_id=representative.resolution.id,
                content_digest=representative.resolution.input_set_hash,
            )
        ]
        if (
            representative.hygiene is not None
            and cluster.signal.failure_domain is FailureDomain.CLEANUP
        ):
            evidence.append(
                FailureEvidenceRef(
                    kind=FailureEvidenceKind.UNIT_HYGIENE_RESOLUTION,
                    ref_id=representative.hygiene.id,
                    content_digest=representative.hygiene.resolution_hash,
                )
            )
        supporting = tuple(sorted(evidence, key=FailureEvidenceRef.sort_key))
        classification_id = new_entity_id()
        content = FailureClassificationRevisionContent(
            id=new_entity_id(),
            failure_classification_id=classification_id,
            tenant_id=snapshot.tenant_id,
            project_id=snapshot.project_id,
            task_run_id=snapshot.task_run_id,
            result_snapshot_id=snapshot.id,
            failure_cluster_revision_id=cluster.id,
            revision=1,
            failure_domain=cluster.signal.failure_domain,
            hypothesis_code=hypothesis_code,
            hypothesis=hypothesis,
            confidence=confidence,
            supporting_evidence_refs=supporting,
            evidence_gap_codes=gaps,
            judgment_state=ClassificationJudgmentState.RULE_PROPOSED,
            author_kind=ClassificationAuthorKind.SYSTEM_RULE,
            classification_policy_version=FAILURE_CLASSIFICATION_POLICY_VERSION,
            classification_policy_digest=FAILURE_CLASSIFICATION_POLICY_DIGEST,
            client_mutation_id=f"rule:{cluster.id}",
            created_at=created_at,
        )
        classification = FailureClassificationRevision(
            **content.model_dump(mode="python"),
            classification_hash=failure_classification_revision_hash(content),
        )
        await self._results.insert_failure_classification(connection, classification)
        await self._record_classification(
            connection,
            actor=actor,
            cluster=cluster,
            classification=classification,
            occurred_at=created_at,
        )
        return classification

    @staticmethod
    def _require_operable_run(
        actor: ActorContext,
        run: TaskRun | None,
        snapshot: TaskResultSnapshot,
    ) -> None:
        if (
            run is None
            or run.id != snapshot.task_run_id
            or run.tenant_id != snapshot.tenant_id
            or run.project_id != snapshot.project_id
            or not actor.can_read_project(snapshot.project_id)
        ):
            raise _not_found()
        if not actor.can_operate_project(snapshot.project_id):
            raise _forbidden("当前角色不能为该 Project 生成失败归因。")
        if (
            run.lifecycle is not ExecutionLifecycle.CLOSED
            or run.materialization_state is not TaskMaterializationState.SEALED
            or run.manifest_hash != snapshot.manifest_hash
        ):
            raise _conflict("只有已关闭并封存的 TaskRun Snapshot 可以生成失败归因。")

    @staticmethod
    def _require_reviewable_run(
        actor: ActorContext,
        run: TaskRun | None,
        classification: FailureClassificationRevision,
    ) -> None:
        if (
            run is None
            or run.id != classification.task_run_id
            or run.tenant_id != classification.tenant_id
            or run.project_id != classification.project_id
            or not actor.can_read_project(classification.project_id)
        ):
            raise _not_found()
        if not actor.can_review_results(classification.project_id):
            raise _forbidden("当前角色不能复核该 Project 的失败归因。")
        if (
            run.lifecycle is not ExecutionLifecycle.CLOSED
            or run.materialization_state is not TaskMaterializationState.SEALED
        ):
            raise _conflict("只有已关闭并封存的 TaskRun 才能复核失败归因。")

    async def _record_classification(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        cluster: FailureClusterRevision,
        classification: FailureClassificationRevision,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "resultSnapshotId": str(classification.result_snapshot_id),
            "failureClusterRevisionId": str(cluster.id),
            "failureClusterId": str(cluster.failure_cluster_id),
            "failureClassificationId": str(classification.failure_classification_id),
            "failureClassificationRevisionId": str(classification.id),
            "revision": classification.revision,
            "failureDomain": classification.failure_domain.value,
            "judgmentState": classification.judgment_state.value,
            "classificationHash": classification.classification_hash,
        }
        await self._audit.append(
            connection,
            tenant_id=classification.tenant_id,
            project_id=classification.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type="failure_classification.revised",
            entity_type="failure_classification",
            entity_id=classification.failure_classification_id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=classification.tenant_id,
                aggregate_type="failure_classification",
                aggregate_id=classification.failure_classification_id,
                event_type="failure_classification.revised",
                occurred_at=occurred_at,
                payload=payload,
            ),
        )


async def _database_now(connection: AsyncConnection[DictRow]) -> datetime:
    cursor = await connection.execute("select transaction_timestamp() as observed_at")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("database transaction timestamp is unavailable")
    return datetime.fromisoformat(str(row["observed_at"]))


def _invalid_request(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="失败归因请求无效",
        detail=detail,
        status_code=400,
    )


def _not_found() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="失败归因不存在",
        detail="未找到可访问的 Snapshot、Cluster 或 Classification。",
        status_code=404,
    )


def _forbidden(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="没有失败归因权限",
        detail=detail,
        status_code=403,
    )


def _conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="失败归因冲突",
        detail=detail,
        status_code=409,
    )


__all__ = ["FailureClassificationBatch", "ResultClassificationService"]
