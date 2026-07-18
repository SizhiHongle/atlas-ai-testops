"""Explicit, snapshot-bound Task Gate evaluation service."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.application.result_classification import ResultClassificationService
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.result import (
    TASK_GATE_POLICY_DIGEST,
    TASK_GATE_POLICY_VERSION,
    FailureClassificationRevision,
    FailureClusterRevision,
    RequestTaskGateEvaluation,
    TaskGateDecision,
    TaskGateDecisionContent,
    TaskResultSnapshot,
    evaluate_task_gate,
    task_gate_classification_inputs,
    task_gate_classification_set_hash,
    task_gate_decision_hash,
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

TASK_GATE_IDEMPOTENCY_TTL = timedelta(hours=24)


class ResultGateService:
    """Append three-valued Gate facts after freezing exact current judgments."""

    def __init__(
        self,
        database: Database,
        *,
        classification_service: ResultClassificationService | None = None,
        result_repository: ResultFactRepository | None = None,
        task_repository: TaskRunRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
    ) -> None:
        self._database = database
        self._results = result_repository or ResultFactRepository()
        self._tasks = task_repository or TaskRunRepository()
        self._classification = classification_service or ResultClassificationService(database)
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()

    async def evaluate(
        self,
        actor: ActorContext,
        request: RequestTaskGateEvaluation,
        *,
        idempotency_key: str,
    ) -> CommandResult[TaskGateDecision]:
        """Evaluate one exact Snapshot without rewriting earlier Gate decisions."""

        if idempotency_key != request.client_mutation_id:
            raise _invalid_request("Idempotency-Key 必须与 clientMutationId 完全一致。")
        if actor.actor_id is None:
            raise _forbidden("Task Gate 评估需要可信 Actor 身份。")

        # Materialization is deterministic and idempotent. Gate still rechecks the
        # complete latest Cluster and Classification set in its own fenced transaction.
        await self._classification.classify_snapshot(actor, request.result_snapshot_id)

        request_payload = cast(
            dict[str, JsonValue],
            request.model_dump(mode="json", by_alias=True),
        )
        request_hash = hash_request(request_payload)
        async with self._database.transaction(actor.database_context()) as connection:
            now = await _database_now(connection)
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope="task-gates.evaluations",
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=TASK_GATE_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=TaskGateDecision.model_validate(
                        reservation.cached_response.body
                    ),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )

            snapshot = await self._results.get_snapshot_by_id(
                connection,
                request.result_snapshot_id,
            )
            if snapshot is None:
                raise _not_found()
            run = await self._tasks.get_run_for_update(connection, snapshot.task_run_id)
            self._require_evaluable_run(actor, run, snapshot)

            previous = await self._results.get_latest_task_gate_for_update(
                connection,
                snapshot.task_run_id,
            )
            await self._results.lock_failure_classification_snapshot(
                connection,
                snapshot.id,
            )
            pairs = await self._results.list_current_gate_classifications(
                connection,
                snapshot.id,
            )
            complete_pairs = self._require_complete_pairs(pairs)
            await self._results.lock_failure_classification_chains(
                connection,
                tuple(
                    classification.failure_classification_id
                    for _, classification in complete_pairs
                ),
            )
            locked_pairs = self._require_complete_pairs(
                await self._results.list_current_gate_classifications(
                    connection,
                    snapshot.id,
                )
            )
            inputs = task_gate_classification_inputs(locked_pairs)
            classifications = tuple(
                classification for _, classification in locked_pairs
            )
            gate_verdict, reasons = evaluate_task_gate(snapshot, classifications)

            content = TaskGateDecisionContent(
                id=new_entity_id(),
                task_gate_id=(
                    previous.task_gate_id if previous is not None else new_entity_id()
                ),
                tenant_id=snapshot.tenant_id,
                project_id=snapshot.project_id,
                task_run_id=snapshot.task_run_id,
                result_snapshot_id=snapshot.id,
                result_snapshot_hash=snapshot.snapshot_hash,
                revision=previous.revision + 1 if previous is not None else 1,
                failure_classification_revision_ids=tuple(
                    item.failure_classification_revision_id for item in inputs
                ),
                classification_set_hash=task_gate_classification_set_hash(
                    result_snapshot_id=snapshot.id,
                    inputs=inputs,
                ),
                gate_policy_version=TASK_GATE_POLICY_VERSION,
                gate_policy_digest=TASK_GATE_POLICY_DIGEST,
                decision=gate_verdict,
                reasons=reasons,
                evaluated_by=actor.actor_id,
                client_mutation_id=request.client_mutation_id,
                supersedes_gate_decision_id=(
                    previous.id if previous is not None else None
                ),
                evaluated_at=now,
            )
            decision = TaskGateDecision(
                **content.model_dump(mode="python"),
                decision_hash=task_gate_decision_hash(content),
            )
            await self._results.insert_task_gate_decision(connection, decision)
            await self._record_gate_evaluation(
                connection,
                actor=actor,
                decision=decision,
                occurred_at=now,
            )
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope="task-gates.evaluations",
                key=idempotency_key,
                request_hash=request_hash,
                response=CachedHttpResponse(
                    status_code=201,
                    body=decision.model_dump(mode="json", by_alias=True),
                ),
            )
            return CommandResult(value=decision, status_code=201, replayed=False)

    @staticmethod
    def _require_complete_pairs(
        pairs: tuple[
            tuple[FailureClusterRevision, FailureClassificationRevision | None],
            ...,
        ],
    ) -> tuple[
        tuple[FailureClusterRevision, FailureClassificationRevision],
        ...,
    ]:
        if any(classification is None for _, classification in pairs):
            raise _conflict(
                "Snapshot 的完整 FailureCluster 集合尚未全部生成 Classification。"
            )
        return tuple(
            (cluster, cast(FailureClassificationRevision, classification))
            for cluster, classification in pairs
        )

    @staticmethod
    def _require_evaluable_run(
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
        if not actor.can_review_results(snapshot.project_id):
            raise _forbidden("当前角色不能评估该 Project 的 Task Gate。")
        if (
            run.lifecycle is not ExecutionLifecycle.CLOSED
            or run.materialization_state is not TaskMaterializationState.SEALED
            or run.manifest_hash != snapshot.manifest_hash
        ):
            raise _conflict("只有已关闭并封存的 TaskRun Snapshot 可以评估 Gate。")

    async def _record_gate_evaluation(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        decision: TaskGateDecision,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "taskGateId": str(decision.task_gate_id),
            "taskGateDecisionId": str(decision.id),
            "taskRunId": str(decision.task_run_id),
            "resultSnapshotId": str(decision.result_snapshot_id),
            "revision": decision.revision,
            "decision": decision.decision.value,
            "classificationSetHash": decision.classification_set_hash,
            "gatePolicyDigest": decision.gate_policy_digest,
            "decisionHash": decision.decision_hash,
        }
        await self._audit.append(
            connection,
            tenant_id=decision.tenant_id,
            project_id=decision.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type="task_gate.evaluated",
            entity_type="task_gate",
            entity_id=decision.task_gate_id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=decision.tenant_id,
                aggregate_type="task_gate",
                aggregate_id=decision.task_gate_id,
                event_type="task_gate.evaluated",
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
        title="Task Gate 请求无效",
        detail=detail,
        status_code=400,
    )


def _not_found() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="Task Gate 资源不存在",
        detail="未找到可访问的 TaskRun 或 Result Snapshot。",
        status_code=404,
    )


def _forbidden(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="没有 Task Gate 权限",
        detail=detail,
        status_code=403,
    )


def _conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="Task Gate 评估冲突",
        detail=detail,
        status_code=409,
    )


__all__ = ["ResultGateService"]
