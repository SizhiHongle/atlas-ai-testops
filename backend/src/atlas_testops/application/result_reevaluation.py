"""Explicit, idempotent Task result re-evaluation over one immutable Snapshot."""

from __future__ import annotations

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
    TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
    TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION,
    TASK_RESULT_SNAPSHOT_REEVALUATED_SCHEMA_VERSION,
    RequestTaskResultReevaluation,
    TaskResultReevaluationCommand,
    TaskResultReevaluationCommandContent,
    TaskResultSnapshot,
    TaskResultSnapshotContent,
    TaskResultSnapshotFinality,
    task_result_reevaluation_command_hash,
    task_result_snapshot_hash,
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

RESULT_REEVALUATION_IDEMPOTENCY_TTL = timedelta(hours=24)


class ResultReevaluationService:
    """Append REEVALUATED only through an authorized explicit command."""

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

    async def reevaluate(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        request: RequestTaskResultReevaluation,
        *,
        idempotency_key: str,
    ) -> CommandResult[TaskResultSnapshot]:
        """Reinterpret one exact Full Snapshot without observing newer mutable state."""

        if idempotency_key != request.client_mutation_id:
            raise _invalid_request("Idempotency-Key 必须与 clientMutationId 完全一致。")
        request_payload: dict[str, JsonValue] = {
            "taskRunId": str(task_run_id),
            **request.model_dump(mode="json", by_alias=True),
        }
        request_hash = hash_request(request_payload)
        scope = f"task-runs.{task_run_id}.result.reevaluate"
        async with self._database.transaction(actor.database_context()) as connection:
            now = await _database_now(connection)
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=RESULT_REEVALUATION_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=TaskResultSnapshot.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )

            run = await self._tasks.get_run_for_update(connection, task_run_id)
            self._require_run(actor, run)
            assert run is not None
            source = await self._results.get_snapshot_by_id(
                connection,
                request.source_snapshot_id,
            )
            self._require_source(run, source)
            assert source is not None

            existing = await self._results.get_reevaluated_snapshot(
                connection,
                task_run_id=run.id,
                source_snapshot_id=source.id,
                policy_digest=TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
            )
            if existing is not None:
                await self._complete_idempotency(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    snapshot=existing,
                    status_code=200,
                )
                return CommandResult(value=existing, status_code=200, replayed=True)

            latest = await self._results.get_latest_snapshot(connection, run.id)
            if latest is None:
                raise _conflict("TaskRun 尚未形成可重评的结果 Revision。")
            command_content = TaskResultReevaluationCommandContent(
                id=new_entity_id(),
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                task_run_id=run.id,
                source_snapshot_id=source.id,
                target_policy_version=TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION,
                target_policy_digest=TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
                client_mutation_id=request.client_mutation_id,
                requested_by=actor.actor_id,
                requested_at=now,
            )
            command = TaskResultReevaluationCommand(
                **command_content.model_dump(mode="python"),
                command_hash=task_result_reevaluation_command_hash(command_content),
            )
            await self._results.insert_reevaluation_command(connection, command)

            assert source.unit_hygiene_resolution_revision_ids is not None
            assert source.input_hygiene_resolution_set_hash is not None
            content = TaskResultSnapshotContent(
                schema_version=TASK_RESULT_SNAPSHOT_REEVALUATED_SCHEMA_VERSION,
                id=new_entity_id(),
                tenant_id=source.tenant_id,
                project_id=source.project_id,
                task_run_id=source.task_run_id,
                manifest_hash=source.manifest_hash,
                revision=latest.revision + 1,
                finality=TaskResultSnapshotFinality.REEVALUATED,
                unit_resolution_revision_ids=source.unit_resolution_revision_ids,
                input_resolution_set_hash=source.input_resolution_set_hash,
                unit_hygiene_resolution_revision_ids=(source.unit_hygiene_resolution_revision_ids),
                input_hygiene_resolution_set_hash=(source.input_hygiene_resolution_set_hash),
                reevaluation_source_snapshot_id=source.id,
                reevaluation_command_id=command.id,
                aggregation_policy_version=(TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_VERSION),
                aggregation_policy_digest=(TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST),
                projection_watermark=source.projection_watermark,
                manifest_count=source.manifest_count,
                verdict_counts=source.verdict_counts,
                axis_distributions=source.axis_distributions,
                raw_pass_rate=source.raw_pass_rate,
                trusted_pass_rate=source.trusted_pass_rate,
                autonomous_pass_rate=source.autonomous_pass_rate,
                decisive_pass_rate=source.decisive_pass_rate,
                supersedes_snapshot_id=latest.id,
                created_at=now,
            )
            snapshot = TaskResultSnapshot(
                **content.model_dump(mode="python"),
                snapshot_hash=task_result_snapshot_hash(content),
            )
            await self._results.insert_snapshot(connection, snapshot)
            await self._record_created(
                connection,
                actor=actor,
                command=command,
                snapshot=snapshot,
                occurred_at=now,
            )
            await self._complete_idempotency(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                snapshot=snapshot,
                status_code=201,
            )
            return CommandResult(value=snapshot, status_code=201, replayed=False)

    @staticmethod
    def _require_run(actor: ActorContext, run: TaskRun | None) -> None:
        if run is None or not actor.can_read_project(run.project_id):
            raise _not_found()
        if not actor.can_operate_project(run.project_id):
            raise _forbidden()
        if (
            run.lifecycle is not ExecutionLifecycle.CLOSED
            or run.materialization_state is not TaskMaterializationState.SEALED
        ):
            raise _conflict("只有已关闭并封存物化的 TaskRun 可以重评结果。")

    @staticmethod
    def _require_source(
        run: TaskRun,
        source: TaskResultSnapshot | None,
    ) -> None:
        if (
            source is None
            or source.tenant_id != run.tenant_id
            or source.project_id != run.project_id
            or source.task_run_id != run.id
        ):
            raise _not_found()
        if (
            source.finality is not TaskResultSnapshotFinality.FULLY_RESOLVED
            or source.manifest_hash != run.manifest_hash
        ):
            raise _conflict("重评来源必须是该 TaskRun 的 FULLY_RESOLVED Snapshot。")

    async def _record_created(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        command: TaskResultReevaluationCommand,
        snapshot: TaskResultSnapshot,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "resultSnapshotId": str(snapshot.id),
            "revision": snapshot.revision,
            "finality": snapshot.finality.value,
            "reevaluationSourceSnapshotId": str(command.source_snapshot_id),
            "reevaluationCommandId": str(command.id),
            "aggregationPolicyVersion": snapshot.aggregation_policy_version,
            "aggregationPolicyDigest": snapshot.aggregation_policy_digest,
            "snapshotHash": snapshot.snapshot_hash,
        }
        await self._audit.append(
            connection,
            tenant_id=snapshot.tenant_id,
            project_id=snapshot.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type="task_result.reevaluated",
            entity_type="task_result_snapshot",
            entity_id=snapshot.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=snapshot.tenant_id,
                aggregate_type="task_run",
                aggregate_id=snapshot.task_run_id,
                event_type="task.snapshot_created",
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    async def _complete_idempotency(
        self,
        connection: AsyncConnection[DictRow],
        *,
        tenant_id: UUID,
        scope: str,
        key: str,
        request_hash: str,
        snapshot: TaskResultSnapshot,
        status_code: int,
    ) -> None:
        await self._idempotency.complete(
            connection,
            tenant_id=tenant_id,
            scope=scope,
            key=key,
            request_hash=request_hash,
            response=CachedHttpResponse(
                status_code=status_code,
                body=snapshot.model_dump(mode="json", by_alias=True),
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
        title="重评请求无效",
        detail=detail,
        status_code=400,
    )


def _not_found() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="结果快照不存在",
        detail="未找到可访问的 TaskRun 或结果快照。",
        status_code=404,
    )


def _forbidden() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="没有结果重评权限",
        detail="当前角色不能对该 Project 的结果执行重评。",
        status_code=403,
    )


def _conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="结果无法重评",
        detail=detail,
        status_code=409,
    )


__all__ = ["ResultReevaluationService"]
