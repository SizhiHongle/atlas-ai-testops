"""Comparable quality brief compilation and immutable Insight pinning."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.insight import (
    INSIGHT_SNAPSHOT_SCHEMA_VERSION,
    InsightBrief,
    InsightDatasetCut,
    InsightMetricDeltas,
    InsightMetricKey,
    InsightRiskSignal,
    InsightSnapshot,
    InsightTerrainItem,
    InsightWindowSummary,
    RequestInsightSnapshot,
    insight_digest,
    insight_metric_catalog,
    insight_source_set_digest,
    metric_delta_basis_points,
    metric_point,
)
from atlas_testops.domain.result import TaskGateVerdict
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.insights import (
    InsightRepository,
    InsightSourceRecord,
)

INSIGHT_IDEMPOTENCY_TTL = timedelta(hours=24)


class InsightService:
    """Compile fixed metric semantics over exact stable Result revisions."""

    def __init__(
        self,
        database: Database,
        *,
        repository: InsightRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
    ) -> None:
        self._database = database
        self._insights = repository or InsightRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()

    async def preview(
        self,
        actor: ActorContext,
        project_id: UUID,
        *,
        window_days: int,
        as_of: datetime | None,
    ) -> InsightBrief:
        """Compile a latest or historical quality brief without persisting it."""

        _validate_window_days(window_days)
        self._require_project_access(actor, project_id)
        async with self._database.transaction(actor.database_context()) as connection:
            now = await _database_now(connection)
            selected_as_of = _validate_as_of(as_of or now, now)
            if not await self._insights.project_exists(connection, project_id):
                raise _not_found()
            sources = await self._load_sources(
                connection,
                project_id=project_id,
                as_of=selected_as_of,
                window_days=window_days,
            )
            return _compile_brief(
                actor=actor,
                project_id=project_id,
                window_days=window_days,
                as_of=selected_as_of,
                sources=sources,
            )

    async def pin_snapshot(
        self,
        actor: ActorContext,
        project_id: UUID,
        request: RequestInsightSnapshot,
        *,
        idempotency_key: str,
    ) -> CommandResult[InsightSnapshot]:
        """Pin one exact DatasetCut and deterministic quality brief."""

        if idempotency_key != request.client_mutation_id:
            raise _invalid_request("Idempotency-Key 必须与 clientMutationId 完全一致。")
        if actor.actor_id is None:
            raise _forbidden("固定 InsightSnapshot 需要可信 Actor 身份。")
        self._require_project_access(actor, project_id)
        request_payload = cast(
            JsonValue,
            {
                "projectId": str(project_id),
                "request": request.model_dump(mode="json", by_alias=True),
            },
        )
        request_hash = insight_digest(request_payload)
        idempotency_request_hash = hash_request(request_payload)
        scope = f"insight-snapshots:{project_id}"

        async with self._database.transaction(actor.database_context()) as connection:
            now = await _database_now(connection)
            if not await self._insights.project_exists(connection, project_id):
                raise _not_found()
            permanent = await self._insights.get_snapshot_by_mutation(
                connection,
                project_id=project_id,
                client_mutation_id=request.client_mutation_id,
            )
            if permanent is not None:
                if permanent.request_hash != request_hash:
                    raise _conflict("同一个 clientMutationId 已用于不同的 Insight 请求。")
                return CommandResult(
                    value=permanent,
                    status_code=200,
                    replayed=True,
                )

            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=idempotency_request_hash,
                now=now,
                ttl=INSIGHT_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=InsightSnapshot.model_validate(
                        reservation.cached_response.body
                    ),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )

            selected_as_of = _validate_as_of(request.as_of or now, now)
            sources = await self._load_sources(
                connection,
                project_id=project_id,
                as_of=selected_as_of,
                window_days=request.window_days,
            )
            brief = _compile_brief(
                actor=actor,
                project_id=project_id,
                window_days=request.window_days,
                as_of=selected_as_of,
                sources=sources,
            )
            snapshot = _pin_brief(
                brief,
                request_hash=request_hash,
                client_mutation_id=request.client_mutation_id,
                created_by=actor.actor_id,
                created_at=now,
            )
            stored = await self._insights.insert_snapshot(connection, snapshot)
            await self._record_snapshot(
                connection,
                actor=actor,
                snapshot=stored,
            )
            response_body = cast(
                dict[str, JsonValue],
                stored.model_dump(mode="json", by_alias=True),
            )
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=idempotency_request_hash,
                response=CachedHttpResponse(status_code=201, body=response_body),
            )
            return CommandResult(value=stored, status_code=201, replayed=False)

    async def get_snapshot(
        self,
        actor: ActorContext,
        snapshot_id: UUID,
    ) -> InsightSnapshot:
        """Read one exact pinned InsightSnapshot without crossing DatasetCuts."""

        async with self._database.transaction(actor.database_context()) as connection:
            snapshot = await self._insights.get_snapshot(connection, snapshot_id)
            if snapshot is None or not actor.can_read_project(snapshot.project_id):
                raise _not_found()
            return snapshot

    async def _load_sources(
        self,
        connection: AsyncConnection[DictRow],
        *,
        project_id: UUID,
        as_of: datetime,
        window_days: int,
    ) -> tuple[InsightSourceRecord, ...]:
        baseline_start = as_of - timedelta(days=window_days * 2)
        return await self._insights.list_comparable_sources(
            connection,
            project_id=project_id,
            as_of=as_of,
            start_at=baseline_start,
        )

    async def _record_snapshot(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        snapshot: InsightSnapshot,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "insightSnapshotId": str(snapshot.id),
            "windowDays": snapshot.window_days,
            "sourceSnapshotCount": len(snapshot.dataset_cut.source_snapshot_ids),
            "sourceSetDigest": snapshot.dataset_cut.source_set_digest,
            "queryHash": snapshot.dataset_cut.query_hash,
            "authScopeHash": snapshot.dataset_cut.auth_scope_hash,
            "projectionWatermark": (
                snapshot.dataset_cut.projection_watermark.isoformat()
                if snapshot.dataset_cut.projection_watermark is not None
                else None
            ),
            "snapshotHash": snapshot.snapshot_hash,
        }
        await self._audit.append(
            connection,
            tenant_id=snapshot.tenant_id,
            project_id=snapshot.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type="insight_snapshot.pinned",
            entity_type="insight_snapshot",
            entity_id=snapshot.id,
            occurred_at=snapshot.created_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=snapshot.tenant_id,
                aggregate_type="insight_snapshot",
                aggregate_id=snapshot.id,
                event_type="insight_snapshot.pinned",
                occurred_at=snapshot.created_at,
                payload=payload,
            ),
        )

    @staticmethod
    def _require_project_access(actor: ActorContext, project_id: UUID) -> None:
        if not actor.can_read_project(project_id):
            raise _not_found()


def _compile_brief(
    *,
    actor: ActorContext,
    project_id: UUID,
    window_days: int,
    as_of: datetime,
    sources: tuple[InsightSourceRecord, ...],
) -> InsightBrief:
    sources = tuple(
        sorted(
            sources,
            key=lambda source: (
                source.quality_finalized_at,
                str(source.snapshot.task_run_id),
                source.snapshot.revision,
                str(source.snapshot.id),
            ),
        )
    )
    window_delta = timedelta(days=window_days)
    current_start = as_of - window_delta
    baseline_start = current_start - window_delta
    baseline_sources = tuple(
        source
        for source in sources
        if baseline_start <= source.quality_finalized_at < current_start
    )
    current_sources = tuple(
        source
        for source in sources
        if current_start <= source.quality_finalized_at <= as_of
    )
    current = _summarize_window(
        current_sources,
        start_at=current_start,
        end_at=as_of,
    )
    baseline = _summarize_window(
        baseline_sources,
        start_at=baseline_start,
        end_at=current_start,
    )
    source_ids = tuple(source.snapshot.id for source in sources)
    source_hashes = tuple(source.snapshot.snapshot_hash for source in sources)
    gate_ids = tuple(
        source.gate_decision.id
        for source in sources
        if source.gate_decision is not None
    )
    gate_hashes = tuple(
        source.gate_decision.decision_hash
        for source in sources
        if source.gate_decision is not None
    )
    watermark = max(
        (
            max(
                source.snapshot.projection_watermark,
                (
                    source.gate_decision.evaluated_at
                    if source.gate_decision is not None
                    else source.snapshot.projection_watermark
                ),
            )
            for source in sources
        ),
        default=None,
    )
    query_hash = insight_digest(
        cast(
            JsonValue,
            {
                "schemaVersion": "atlas.insight-query/0.1",
                "projectId": str(project_id),
                "windowDays": window_days,
                "timezone": "UTC",
                "sourceFinality": "FULLY_RESOLVED_OR_REEVALUATED",
                "metricDefinitions": [
                    definition.model_dump(mode="json", by_alias=True)
                    for definition in insight_metric_catalog()
                ],
            },
        )
    )
    auth_scope_hash = _auth_scope_hash(actor, project_id)
    dataset_cut = InsightDatasetCut(
        as_of=as_of,
        source_snapshot_ids=source_ids,
        source_snapshot_hashes=source_hashes,
        gate_decision_ids=gate_ids,
        gate_decision_hashes=gate_hashes,
        source_set_digest=insight_source_set_digest(
            source_ids,
            source_hashes,
            gate_ids,
            gate_hashes,
        ),
        projection_watermark=watermark,
        query_hash=query_hash,
        auth_scope_hash=auth_scope_hash,
    )
    return InsightBrief(
        tenant_id=actor.tenant_id,
        project_id=project_id,
        window_days=window_days,  # type: ignore[arg-type]
        metric_definitions=insight_metric_catalog(),
        current=current,
        baseline=baseline,
        deltas=InsightMetricDeltas(
            trusted_pass_rate=metric_delta_basis_points(
                current.trusted_pass_rate,
                baseline.trusted_pass_rate,
            ),
            autonomous_trusted_pass_rate=metric_delta_basis_points(
                current.autonomous_trusted_pass_rate,
                baseline.autonomous_trusted_pass_rate,
            ),
            method_health_rate=metric_delta_basis_points(
                current.method_health_rate,
                baseline.method_health_rate,
            ),
        ),
        terrain=_terrain(current_sources),
        active_risk=_active_risk(current_sources),
        dataset_cut=dataset_cut,
        generated_at=as_of,
    )


def _summarize_window(
    sources: tuple[InsightSourceRecord, ...],
    *,
    start_at: datetime,
    end_at: datetime,
) -> InsightWindowSummary:
    manifest_count = sum(source.snapshot.manifest_count for source in sources)
    trusted_passed = sum(
        source.snapshot.trusted_pass_rate.numerator for source in sources
    )
    autonomous_passed = sum(
        source.snapshot.autonomous_pass_rate.numerator for source in sources
    )
    stable = sum(
        source.snapshot.axis_distributions.stability.stable for source in sources
    )
    return InsightWindowSummary(
        start_at=start_at,
        end_at=end_at,
        task_run_count=len(sources),
        execution_unit_count=manifest_count,
        trusted_pass_rate=metric_point(
            InsightMetricKey.TRUSTED_PASS_RATE,
            numerator=trusted_passed,
            denominator=manifest_count,
        ),
        autonomous_trusted_pass_rate=metric_point(
            InsightMetricKey.AUTONOMOUS_TRUSTED_PASS_RATE,
            numerator=autonomous_passed,
            denominator=manifest_count,
        ),
        method_health_rate=metric_point(
            InsightMetricKey.METHOD_HEALTH_RATE,
            numerator=stable,
            denominator=manifest_count,
        ),
    )


def _terrain(
    sources: tuple[InsightSourceRecord, ...],
) -> tuple[InsightTerrainItem, ...]:
    grouped: dict[UUID, list[InsightSourceRecord]] = defaultdict(list)
    for source in sources:
        grouped[source.task_plan_id].append(source)
    items: list[InsightTerrainItem] = []
    for task_plan_id, records in grouped.items():
        manifest_count = sum(record.snapshot.manifest_count for record in records)
        trusted_passed = sum(
            record.snapshot.trusted_pass_rate.numerator for record in records
        )
        latest = max(
            records,
            key=lambda record: (
                record.quality_finalized_at,
                record.snapshot.revision,
                str(record.snapshot.id),
            ),
        )
        items.append(
            InsightTerrainItem(
                task_plan_id=task_plan_id,
                label=latest.task_plan_name,
                task_run_count=len(records),
                execution_unit_count=manifest_count,
                trusted_pass_rate=metric_point(
                    InsightMetricKey.TRUSTED_PASS_RATE,
                    numerator=trusted_passed,
                    denominator=manifest_count,
                ),
                latest_task_run_id=latest.snapshot.task_run_id,
                latest_result_snapshot_id=latest.snapshot.id,
            )
        )
    return tuple(
        sorted(
            items,
            key=lambda item: (
                -item.execution_unit_count,
                item.label,
                str(item.task_plan_id),
            ),
        )[:4]
    )


def _active_risk(
    sources: tuple[InsightSourceRecord, ...],
) -> InsightRiskSignal | None:
    candidates: list[tuple[datetime, InsightSourceRecord, TaskGateVerdict, int]] = []
    for source in sources:
        gate = source.gate_decision
        if gate is not None and gate.decision is TaskGateVerdict.ACCEPTED:
            continue
        verdict = gate.decision if gate is not None else TaskGateVerdict.INCONCLUSIVE
        observed_at = (
            gate.evaluated_at
            if gate is not None
            else source.snapshot.projection_watermark
        )
        candidates.append((observed_at, source, verdict, len(gate.reasons) if gate else 1))
    if not candidates:
        return None
    observed_at, source, verdict, reason_count = max(
        candidates,
        key=lambda item: (
            item[0],
            item[1].snapshot.revision,
            str(item[1].snapshot.id),
        ),
    )
    return InsightRiskSignal(
        task_run_id=source.snapshot.task_run_id,
        result_snapshot_id=source.snapshot.id,
        task_plan_id=source.task_plan_id,
        task_plan_name=source.task_plan_name,
        gate_decision=verdict,
        reason_count=reason_count,
        observed_at=observed_at,
    )


def _pin_brief(
    brief: InsightBrief,
    *,
    request_hash: str,
    client_mutation_id: str,
    created_by: UUID,
    created_at: datetime,
) -> InsightSnapshot:
    semantic = brief.model_dump(mode="json", by_alias=True)
    semantic["schemaVersion"] = INSIGHT_SNAPSHOT_SCHEMA_VERSION
    snapshot_hash = insight_digest(cast(JsonValue, semantic))
    return InsightSnapshot.model_validate(
        {
            **semantic,
            "id": new_entity_id(),
            "requestHash": request_hash,
            "clientMutationId": client_mutation_id,
            "createdBy": str(created_by),
            "createdAt": created_at,
            "snapshotHash": snapshot_hash,
        }
    )


def _auth_scope_hash(actor: ActorContext, project_id: UUID) -> str:
    grants = sorted(
        (
            grant.role.value,
            str(grant.project_id) if grant.project_id is not None else "*",
        )
        for grant in actor.grants
        if grant.project_id in {None, project_id}
    )
    return insight_digest(
        cast(
            JsonValue,
            {
                "schemaVersion": "atlas.insight-auth-scope/0.1",
                "tenantId": str(actor.tenant_id),
                "projectId": str(project_id),
                "organizationAdmin": actor.is_organization_admin(),
                "developmentOverride": actor.development_override,
                "grants": [
                    {"role": role, "projectId": scoped_project}
                    for role, scoped_project in grants
                ],
            },
        )
    )


def _validate_window_days(window_days: int) -> None:
    if window_days not in {7, 30, 90}:
        raise _invalid_request("windowDays 只允许 7、30 或 90。")


def _validate_as_of(selected: datetime, now: datetime) -> datetime:
    if selected.tzinfo is None or selected.utcoffset() is None:
        raise _invalid_request("asOf 必须是带时区的 RFC 3339 时间。")
    if selected > now:
        raise _invalid_request("asOf 不能晚于数据库当前时间。")
    return selected


async def _database_now(connection: AsyncConnection[DictRow]) -> datetime:
    cursor = await connection.execute("select transaction_timestamp() as observed_at")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("database transaction timestamp is unavailable")
    return datetime.fromisoformat(str(row["observed_at"]))


def _invalid_request(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="Insight 请求无效",
        detail=detail,
        status_code=400,
    )


def _not_found() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="Insight 资源不存在",
        detail="Project 或 InsightSnapshot 不存在或不可见。",
        status_code=404,
    )


def _forbidden(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="没有 Insight 权限",
        detail=detail,
        status_code=403,
    )


def _conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="InsightSnapshot 冲突",
        detail=detail,
        status_code=409,
    )


__all__ = ["InsightService"]
