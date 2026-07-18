"""Manual TaskRun reruns that materialize new immutable child aggregates."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.task import (
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    RequestTaskRunInfraFailureRerun,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskRun,
    TaskRunManifest,
    TaskRunRerunSelectionMode,
    TaskTriggerSource,
    UnitAttempt,
    task_run_infra_rerun_trigger_fingerprint,
    task_run_manifest_hash,
    task_run_workflow_id,
    unit_attempt_workflow_id,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableFactConflictError,
    TaskRunRepository,
)


class TaskRunRerunService:
    """Create a new child Run containing every and only infra-failed source Unit."""

    def __init__(
        self,
        database: Database,
        *,
        task_repository: TaskRunRepository | None = None,
        state_repository: TaskExecutionStateRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self._database = database
        self._tasks = task_repository or TaskRunRepository()
        self._state = state_repository or TaskExecutionStateRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def rerun_infrastructure_failures(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        request: RequestTaskRunInfraFailureRerun,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[TaskRun]:
        """Materialize and seal one lineage-bound child Run in a short transaction."""

        if idempotency_key != request.client_mutation_id:
            raise _invalid_request(
                "Idempotency-Key 必须与 clientMutationId 完全一致。"
            )
        try:
            async with self._database.transaction(actor.database_context()) as connection:
                source_run = await self._tasks.get_run_for_update(connection, task_run_id)
                self._require_rerunnable_source(
                    actor,
                    source_run,
                    expected_revision=expected_revision,
                )
                assert source_run is not None
                source_manifest = await self._tasks.get_manifest(
                    connection,
                    source_run.id,
                )
                if source_manifest is None:
                    raise RuntimeError("sealed source TaskRun is missing its manifest")
                source_units = await self._tasks.list_units(connection, source_run.id)
                first_attempts = await self._tasks.list_first_attempts(
                    connection,
                    source_run.id,
                )
                now = await _database_now(connection)
                child = _build_infrastructure_rerun(
                    source_run=source_run,
                    source_manifest=source_manifest,
                    source_units=source_units,
                    source_first_attempts=first_attempts,
                    client_mutation_id=request.client_mutation_id,
                    requested_by=actor.actor_id,
                    now=now,
                )
                result = await self._tasks.create_run(
                    connection,
                    task_run=child.run,
                    manifest=child.manifest,
                    units=child.units,
                    first_attempts=child.first_attempts,
                )
                created = result.kind is ImmutableCreateKind.CREATED
                if created:
                    await self._record_created(
                        connection,
                        actor=actor,
                        source_run=source_run,
                        child_run=result.task_run,
                        selected_unit_count=len(child.units),
                        occurred_at=now,
                    )
                return CommandResult(
                    value=result.task_run,
                    status_code=201 if created else 200,
                    replayed=not created,
                )
        except ImmutableFactConflictError as error:
            raise _conflict(
                "clientMutationId 已绑定到不同的环境失败重跑输入。"
            ) from error

    @staticmethod
    def _require_rerunnable_source(
        actor: ActorContext,
        source_run: TaskRun | None,
        *,
        expected_revision: int,
    ) -> None:
        if source_run is None or not actor.can_read_project(source_run.project_id):
            raise _not_found()
        if not actor.can_operate_project(source_run.project_id):
            raise _forbidden()
        if source_run.revision != expected_revision:
            raise _revision_conflict(source_run.revision)
        if (
            source_run.materialization_state is not TaskMaterializationState.SEALED
            or source_run.lifecycle is not ExecutionLifecycle.CLOSED
        ):
            raise _conflict("只有已完成并封存物化的 TaskRun 可以重跑环境失败单元。")

    async def _record_created(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        source_run: TaskRun,
        child_run: TaskRun,
        selected_unit_count: int,
        occurred_at: datetime,
    ) -> None:
        assert child_run.request_digest is not None
        payload: dict[str, JsonValue] = {
            "sourceTaskRunId": str(source_run.id),
            "sourceRunRevision": source_run.revision,
            "rerunSelectionMode": TaskRunRerunSelectionMode.INFRA_FAILURES.value,
            "selectedUnitCount": selected_unit_count,
            "requestDigest": child_run.request_digest,
        }
        sequence = await self._state.next_task_execution_event_seq(
            connection,
            task_run_id=child_run.id,
        )
        await self._tasks.append_event(
            connection,
            TaskExecutionEvent(
                id=new_entity_id(),
                tenant_id=child_run.tenant_id,
                project_id=child_run.project_id,
                task_run_id=child_run.id,
                seq=sequence,
                event_type="task_run.rerun_requested",
                lifecycle=child_run.lifecycle,
                quality=child_run.quality,
                hygiene=child_run.hygiene,
                payload=payload,
                occurred_at=occurred_at,
            ),
        )
        await self._audit.append(
            connection,
            tenant_id=child_run.tenant_id,
            project_id=child_run.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type="task_run.rerun_requested",
            entity_type="task_run",
            entity_id=child_run.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=child_run.tenant_id,
                aggregate_type="task_run",
                aggregate_id=child_run.id,
                event_type="task_run.rerun_requested",
                occurred_at=occurred_at,
                payload=payload,
            ),
        )


class _TaskRunRerunAggregate:
    """Internal complete aggregate passed to the existing materialization repository."""

    def __init__(
        self,
        *,
        run: TaskRun,
        manifest: TaskRunManifest,
        units: tuple[ExecutionUnit, ...],
        first_attempts: tuple[UnitAttempt, ...],
    ) -> None:
        self.run = run
        self.manifest = manifest
        self.units = units
        self.first_attempts = first_attempts


def _build_infrastructure_rerun(
    *,
    source_run: TaskRun,
    source_manifest: TaskRunManifest,
    source_units: tuple[ExecutionUnit, ...],
    source_first_attempts: tuple[UnitAttempt, ...],
    client_mutation_id: str,
    requested_by: UUID | None,
    now: datetime,
) -> _TaskRunRerunAggregate:
    """Clone exact frozen inputs while replacing every physical child identity."""

    manifest_by_key = {unit.unit_key: unit for unit in source_manifest.units}
    if len(manifest_by_key) != len(source_manifest.units):
        raise RuntimeError("source TaskRun manifest unit identity is invalid")
    if {unit.unit_key for unit in source_units} != set(manifest_by_key):
        raise RuntimeError("source TaskRun units do not match its sealed manifest")
    selected_sources = tuple(
        sorted(
            (
                unit
                for unit in source_units
                if unit.lifecycle is ExecutionLifecycle.CLOSED
                and unit.quality is ExecutionQuality.INFRA_ERROR
            ),
            key=lambda unit: unit.unit_key,
        )
    )
    if not selected_sources:
        raise _conflict("该 TaskRun 没有可重跑的最终环境失败单元。")

    first_attempt_by_unit = {
        attempt.execution_unit_id: attempt for attempt in source_first_attempts
    }
    if len(first_attempt_by_unit) != len(source_units):
        raise RuntimeError("source TaskRun first Attempt set is incomplete")

    child_run_id = new_entity_id()
    trigger_fingerprint = task_run_infra_rerun_trigger_fingerprint(
        parent_task_run_id=source_run.id,
        client_mutation_id=client_mutation_id,
    )
    manifest_units = tuple(
        manifest_by_key[source.unit_key].model_copy(update={"ordinal": ordinal})
        for ordinal, source in enumerate(selected_sources, start=1)
    )
    manifest_hash = task_run_manifest_hash(
        task_run_id=child_run_id,
        task_plan_version_id=source_manifest.task_plan_version_id,
        trigger_source=TaskTriggerSource.API,
        trigger_fingerprint=trigger_fingerprint,
        tenant_id=source_manifest.tenant_id,
        project_id=source_manifest.project_id,
        iteration_id=source_manifest.iteration_id,
        units=manifest_units,
        policy_digests=source_manifest.policy_digests,
        compiler_version=source_manifest.compiler_version,
        schema_version=source_manifest.schema_version,
        retry_policy=source_manifest.retry_policy,
    )
    manifest = TaskRunManifest(
        schema_version=source_manifest.schema_version,
        task_run_id=child_run_id,
        task_plan_version_id=source_manifest.task_plan_version_id,
        trigger_source=TaskTriggerSource.API,
        trigger_fingerprint=trigger_fingerprint,
        tenant_id=source_manifest.tenant_id,
        project_id=source_manifest.project_id,
        iteration_id=source_manifest.iteration_id,
        units=manifest_units,
        policy_digests=source_manifest.policy_digests,
        retry_policy=source_manifest.retry_policy,
        compiler_version=source_manifest.compiler_version,
        manifest_hash=manifest_hash,
    )
    namespace = source_run.temporal_namespace
    if namespace is None:
        raise RuntimeError("sealed source TaskRun is missing its Temporal namespace")
    run = TaskRun(
        id=child_run_id,
        tenant_id=source_run.tenant_id,
        project_id=source_run.project_id,
        task_plan_version_id=source_run.task_plan_version_id,
        manifest_hash=manifest_hash,
        trigger_source=TaskTriggerSource.API,
        trigger_fingerprint=trigger_fingerprint,
        request_digest=manifest.recompute_request_digest(),
        rerun_of_task_run_id=source_run.id,
        rerun_selection_mode=TaskRunRerunSelectionMode.INFRA_FAILURES,
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        requested_by=requested_by,
        temporal_namespace=namespace,
        temporal_workflow_id=task_run_workflow_id(
            tenant_id=source_run.tenant_id,
            task_run_id=child_run_id,
        ),
        requested_at=now,
        queued_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )

    units: list[ExecutionUnit] = []
    attempts: list[UnitAttempt] = []
    for manifest_unit, source_unit in zip(
        manifest_units,
        selected_sources,
        strict=True,
    ):
        unit_id = new_entity_id()
        unit = ExecutionUnit(
            id=unit_id,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            manifest_hash=manifest_hash,
            lifecycle=ExecutionLifecycle.QUEUED,
            quality=ExecutionQuality.PENDING,
            hygiene=ExecutionHygiene.PENDING,
            revision=1,
            created_at=now,
            updated_at=now,
            **manifest_unit.model_dump(mode="python"),
        )
        source_attempt = first_attempt_by_unit[source_unit.id]
        execution_window = (
            source_attempt.execution_deadline - source_attempt.queued_at
        )
        if execution_window.total_seconds() <= 0:
            raise RuntimeError("source TaskRun contains an invalid execution window")
        attempt_id = new_entity_id()
        attempt = UnitAttempt(
            id=attempt_id,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            execution_unit_id=unit.id,
            manifest_hash=manifest_hash,
            unit_key=unit.unit_key,
            case_version_id=unit.case_version_id,
            attempt_number=1,
            lifecycle=ExecutionLifecycle.QUEUED,
            quality=ExecutionQuality.PENDING,
            hygiene=ExecutionHygiene.PENDING,
            temporal_namespace=namespace,
            temporal_workflow_id=unit_attempt_workflow_id(
                tenant_id=run.tenant_id,
                unit_attempt_id=attempt_id,
            ),
            queued_at=now,
            execution_deadline=now + execution_window,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        units.append(unit)
        attempts.append(attempt)

    return _TaskRunRerunAggregate(
        run=run,
        manifest=manifest,
        units=tuple(units),
        first_attempts=tuple(attempts),
    )


async def _database_now(connection: AsyncConnection[DictRow]) -> datetime:
    cursor = await connection.execute("select transaction_timestamp() as now")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("database clock query returned no row")
    return datetime.fromisoformat(str(row["now"]))


def _not_found() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="资源不存在",
        detail="TaskRun 不存在或不可见。",
        status_code=404,
    )


def _forbidden() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="无权重跑 TaskRun",
        detail="当前身份不能操作该 Project 的 TaskRun。",
        status_code=403,
    )


def _invalid_request(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="TaskRun 重跑请求无效",
        detail=detail,
        status_code=400,
    )


def _conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="TaskRun 重跑冲突",
        detail=detail,
        status_code=409,
    )


def _revision_conflict(current_revision: int) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.PRECONDITION_FAILED,
        title="TaskRun Revision 已变化",
        detail="请刷新 TaskRun 后使用最新 ETag 重试。",
        status_code=412,
        headers={"ETag": format_revision_etag(current_revision)},
    )
