"""Public TaskRun command acceptance and durable command delivery."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from re import fullmatch
from typing import Protocol
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import SerializationFailure
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.task import (
    ExecutionLifecycle,
    ExecutionQuality,
    RequestTaskRunCancel,
    RequestTaskRunPause,
    RequestTaskRunResume,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskRun,
    TaskRunCommandIntent,
    TaskRunCommandStatus,
    TaskRunCommandType,
    task_run_command_digest,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository
from atlas_testops.infrastructure.task_commands import (
    ClaimedTaskRunCommandIntent,
    TaskRunCommandRepository,
)


class TaskRunCommandService:
    """Accept control intent atomically without performing Temporal I/O in the API."""

    def __init__(
        self,
        database: Database,
        *,
        task_repository: TaskRunRepository | None = None,
        state_repository: TaskExecutionStateRepository | None = None,
        command_repository: TaskRunCommandRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self._database = database
        self._tasks = task_repository or TaskRunRepository()
        self._state = state_repository or TaskExecutionStateRepository()
        self._commands = command_repository or TaskRunCommandRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def cancel(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        request: RequestTaskRunCancel,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[TaskRunCommandIntent]:
        """Record one exact cancel request and move the Run to CANCELING atomically."""

        return await self._accept(
            actor,
            task_run_id,
            command_type=TaskRunCommandType.CANCEL,
            client_mutation_id=request.client_mutation_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    async def pause(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        request: RequestTaskRunPause,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[TaskRunCommandIntent]:
        """Request a batch-boundary pause without canceling active children."""

        return await self._accept(
            actor,
            task_run_id,
            command_type=TaskRunCommandType.PAUSE,
            client_mutation_id=request.client_mutation_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    async def resume(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        request: RequestTaskRunResume,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[TaskRunCommandIntent]:
        """Request continuation while Run remains PAUSED until Workflow acknowledgement."""

        return await self._accept(
            actor,
            task_run_id,
            command_type=TaskRunCommandType.RESUME,
            client_mutation_id=request.client_mutation_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    async def get(
        self,
        actor: ActorContext,
        *,
        task_run_id: UUID,
        command_id: UUID,
    ) -> TaskRunCommandIntent:
        """Read one visible command without exposing dispatcher claim internals."""

        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._tasks.get_run(connection, task_run_id)
            if run is None or not actor.can_read_project(run.project_id):
                raise _not_found()
            command = await self._commands.get(connection, command_id)
            if command is None or command.task_run_id != run.id:
                raise _not_found("TaskRun Command 不存在或不属于该 TaskRun。")
            return command

    async def _accept(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        *,
        command_type: TaskRunCommandType,
        client_mutation_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[TaskRunCommandIntent]:
        if idempotency_key != client_mutation_id:
            raise _invalid_request(
                "Idempotency-Key 必须与 clientMutationId 完全一致。"
            )
        try:
            return await self._accept_in_transaction(
                actor,
                task_run_id,
                command_type=command_type,
                client_mutation_id=client_mutation_id,
                expected_revision=expected_revision,
            )
        except SerializationFailure:
            async with self._database.transaction(actor.database_context()) as connection:
                existing = await self._commands.get_by_mutation(
                    connection,
                    task_run_id=task_run_id,
                    client_mutation_id=client_mutation_id,
                )
                if (
                    existing is not None
                    and existing.expected_run_revision == expected_revision
                    and existing.command_type is command_type
                ):
                    return CommandResult(value=existing, status_code=202, replayed=True)
                current = await self._tasks.get_run(connection, task_run_id)
                if current is None or not actor.can_read_project(current.project_id):
                    raise _not_found() from None
                raise _revision_conflict(current.revision) from None

    async def _accept_in_transaction(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        *,
        command_type: TaskRunCommandType,
        client_mutation_id: str,
        expected_revision: int,
    ) -> CommandResult[TaskRunCommandIntent]:
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._tasks.get_run_for_update(connection, task_run_id)
            self._require_operable_run(actor, run)
            assert run is not None
            existing = await self._commands.get_by_mutation(
                connection,
                task_run_id=run.id,
                client_mutation_id=client_mutation_id,
            )
            if existing is not None:
                if (
                    existing.expected_run_revision != expected_revision
                    or existing.command_type is not command_type
                ):
                    raise _idempotency_conflict()
                return CommandResult(value=existing, status_code=202, replayed=True)
            if run.revision != expected_revision:
                raise _revision_conflict(run.revision)
            target_lifecycle = _target_lifecycle(command_type, run.lifecycle)
            if run.quality is not ExecutionQuality.PENDING:
                raise _command_conflict("已经终结结果的 TaskRun 不能接受控制命令。")
            if command_type in {TaskRunCommandType.PAUSE, TaskRunCommandType.RESUME}:
                open_command = await self._commands.get_open_for_run(
                    connection,
                    task_run_id=run.id,
                )
                if open_command is not None:
                    raise _command_conflict("TaskRun 已有尚未收口的 Pause/Resume 命令。")
            assert run.request_digest is not None
            assert run.temporal_namespace is not None
            assert run.temporal_workflow_id is not None
            command_digest = task_run_command_digest(
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                task_run_id=run.id,
                command_type=command_type,
                client_mutation_id=client_mutation_id,
                expected_run_revision=expected_revision,
                request_digest=run.request_digest,
                manifest_hash=run.manifest_hash,
                temporal_namespace=run.temporal_namespace,
                temporal_workflow_id=run.temporal_workflow_id,
            )
            now = await _database_now(connection)
            updated = await self._state.transition_task_run_state(
                connection,
                task_run_id=run.id,
                expected_revision=run.revision,
                lifecycle=target_lifecycle,
                quality=run.quality,
                hygiene=run.hygiene,
                started_at=run.started_at,
                finalized_at=run.finalized_at,
                cleanup_resolved_at=run.cleanup_resolved_at,
                closed_at=run.closed_at,
            )
            if updated is None:
                raise RuntimeError("trusted TaskRun cancel transition returned no row")
            command = TaskRunCommandIntent(
                id=new_entity_id(),
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                task_run_id=run.id,
                command_type=command_type,
                client_mutation_id=client_mutation_id,
                command_digest=command_digest,
                expected_run_revision=expected_revision,
                accepted_run_revision=updated.revision,
                request_digest=run.request_digest,
                manifest_hash=run.manifest_hash,
                temporal_namespace=run.temporal_namespace,
                temporal_workflow_id=run.temporal_workflow_id,
                status=TaskRunCommandStatus.PENDING,
                dispatch_attempts=0,
                created_by=actor.actor_id,
                created_at=now,
                updated_at=now,
            )
            stored = (await self._commands.create(connection, command)).fact
            if command_type is TaskRunCommandType.CANCEL:
                await self._commands.supersede_for_cancel(
                    connection,
                    task_run_id=run.id,
                    cancel_command_id=stored.id,
                )
            await self._append_execution_event(
                connection,
                run=updated,
                command=stored,
                occurred_at=now,
            )
            await self._append_control_events(
                connection,
                actor=actor,
                run=updated,
                command=stored,
                occurred_at=now,
            )
            return CommandResult(value=stored, status_code=202, replayed=False)

    async def _append_execution_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: TaskRun,
        command: TaskRunCommandIntent,
        occurred_at: datetime,
    ) -> None:
        sequence = await self._state.next_task_execution_event_seq(
            connection,
            task_run_id=run.id,
        )
        await self._tasks.append_event(
            connection,
            TaskExecutionEvent(
                id=new_entity_id(),
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                task_run_id=run.id,
                seq=sequence,
                event_type=_command_event_type(command.command_type),
                lifecycle=run.lifecycle,
                quality=run.quality,
                hygiene=run.hygiene,
                payload={
                    "commandId": str(command.id),
                    "commandDigest": command.command_digest,
                    "acceptedRunRevision": command.accepted_run_revision,
                },
                occurred_at=occurred_at,
            ),
        )

    async def _append_control_events(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        run: TaskRun,
        command: TaskRunCommandIntent,
        occurred_at: datetime,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "commandId": str(command.id),
            "commandType": command.command_type.value,
            "commandDigest": command.command_digest,
            "acceptedRunRevision": command.accepted_run_revision,
        }
        await self._audit.append(
            connection,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type=_command_event_type(command.command_type),
            entity_type="task_run",
            entity_id=run.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=run.tenant_id,
                aggregate_type="task_run",
                aggregate_id=run.id,
                event_type=_command_event_type(command.command_type),
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    @staticmethod
    def _require_operable_run(actor: ActorContext, run: TaskRun | None) -> None:
        if run is None or not actor.can_read_project(run.project_id):
            raise _not_found()
        if not actor.can_operate_project(run.project_id):
            raise _forbidden()
        if run.materialization_state is not TaskMaterializationState.SEALED:
            raise _command_conflict("TaskRun 尚未完成 materialization seal。")


class TaskCommandDispatcherDatabase(Protocol):
    """Dispatcher authority limited to trusted cross-tenant command functions."""

    def transaction(
        self,
    ) -> AbstractAsyncContextManager[AsyncConnection[DictRow]]: ...


class TaskCommandSignaler(Protocol):
    """Deliver one exact claimed command to its durable Workflow."""

    async def signal(self, intent: ClaimedTaskRunCommandIntent) -> None: ...


class TaskCommandDeliveryError(Exception):
    """Safe classified command error whose code may be persisted."""

    def __init__(self, error_code: str) -> None:
        if fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code) is None:
            raise ValueError("Task command delivery error code is invalid")
        super().__init__(error_code)
        self.error_code = error_code


class TaskCommandInvariantError(TaskCommandDeliveryError):
    """Permanent Workflow identity or command mismatch."""


class TaskCommandTransientError(TaskCommandDeliveryError):
    """Ambiguous or unavailable Temporal result that may be retried."""


@dataclass(frozen=True, slots=True)
class TaskCommandDeliveryBatch:
    """Non-sensitive counters for one command claim-and-deliver pass."""

    claimed: int
    delivered: int
    retried: int
    failed: int
    lease_lost: int


@dataclass(frozen=True, slots=True)
class _DeliveryOutcome:
    delivered: int = 0
    retried: int = 0
    failed: int = 0
    lease_lost: int = 0


class TaskRunCommandIntentConsumer:
    """Claim briefly, Signal outside SQL, then CAS the delivery result."""

    def __init__(
        self,
        database: TaskCommandDispatcherDatabase,
        signaler: TaskCommandSignaler,
        *,
        dispatcher_id: str,
        temporal_namespace: str,
        batch_size: int,
        lease_duration: timedelta,
        poll_interval: timedelta,
        retry_policy: TaskIntentRetryPolicy,
        repository: TaskRunCommandRepository | None = None,
    ) -> None:
        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", dispatcher_id) is None:
            raise ValueError("Task command dispatcher identity is invalid")
        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", temporal_namespace) is None:
            raise ValueError("Task command Temporal namespace is invalid")
        if not 1 <= batch_size <= 100:
            raise ValueError("Task command batch size must be between 1 and 100")
        if not timedelta(seconds=1) <= lease_duration <= timedelta(minutes=15):
            raise ValueError("Task command lease duration is invalid")
        if not lease_duration.total_seconds().is_integer():
            raise ValueError("Task command lease duration must use whole seconds")
        if poll_interval <= timedelta(0):
            raise ValueError("Task command poll interval must be positive")
        self._database = database
        self._signaler = signaler
        self._dispatcher_id = dispatcher_id
        self._temporal_namespace = temporal_namespace
        self._batch_size = batch_size
        self._lease_duration = lease_duration
        self._poll_interval = poll_interval
        self._retry_policy = retry_policy
        self._repository = repository or TaskRunCommandRepository()

    async def run_once(self) -> TaskCommandDeliveryBatch:
        async with self._database.transaction() as connection:
            claimed = await self._repository.claim(
                connection,
                claimed_by=self._dispatcher_id,
                namespace=self._temporal_namespace,
                limit=self._batch_size,
                lease_duration=self._lease_duration,
            )
        outcomes = await asyncio.gather(*(self._deliver(intent) for intent in claimed))
        return TaskCommandDeliveryBatch(
            claimed=len(claimed),
            delivered=sum(item.delivered for item in outcomes),
            retried=sum(item.retried for item in outcomes),
            failed=sum(item.failed for item in outcomes),
            lease_lost=sum(item.lease_lost for item in outcomes),
        )

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval.total_seconds(),
                )
            except TimeoutError:
                continue

    async def _deliver(self, intent: ClaimedTaskRunCommandIntent) -> _DeliveryOutcome:
        try:
            await self._signaler.signal(intent)
        except TaskCommandInvariantError as error:
            return await self._fail(intent, error.error_code)
        except TaskCommandTransientError as error:
            if intent.dispatch_attempts >= self._retry_policy.max_attempts:
                return await self._fail(intent, "TEMPORAL_RETRY_EXHAUSTED")
            return await self._retry(intent, error.error_code)
        except asyncio.CancelledError:
            raise
        except Exception:
            if intent.dispatch_attempts >= self._retry_policy.max_attempts:
                return await self._fail(intent, "COMMAND_DISPATCH_RETRY_EXHAUSTED")
            return await self._retry(intent, "COMMAND_DISPATCH_UNEXPECTED")
        async with self._database.transaction() as connection:
            applied = await self._repository.mark_delivered(
                connection,
                intent_id=intent.id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
            )
        return _DeliveryOutcome(delivered=1) if applied else _DeliveryOutcome(lease_lost=1)

    async def _retry(
        self,
        intent: ClaimedTaskRunCommandIntent,
        error_code: str,
    ) -> _DeliveryOutcome:
        async with self._database.transaction() as connection:
            applied = await self._repository.retry(
                connection,
                intent_id=intent.id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
                error_code=error_code,
                retry_delay=self._retry_policy.delay_after(intent.dispatch_attempts),
            )
        return _DeliveryOutcome(retried=1) if applied else _DeliveryOutcome(lease_lost=1)

    async def _fail(
        self,
        intent: ClaimedTaskRunCommandIntent,
        error_code: str,
    ) -> _DeliveryOutcome:
        async with self._database.transaction() as connection:
            applied = await self._repository.fail(
                connection,
                intent_id=intent.id,
                claim_token=intent.claim_token,
                dispatch_revision=intent.dispatch_revision,
                error_code=error_code,
            )
        return _DeliveryOutcome(failed=1) if applied else _DeliveryOutcome(lease_lost=1)


async def _database_now(connection: AsyncConnection[DictRow]) -> datetime:
    cursor = await connection.execute("select transaction_timestamp() as now")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("database clock query returned no row")
    return datetime.fromisoformat(str(row["now"]))


def _target_lifecycle(
    command_type: TaskRunCommandType,
    current: ExecutionLifecycle,
) -> ExecutionLifecycle:
    if command_type is TaskRunCommandType.CANCEL:
        if current not in {
            ExecutionLifecycle.QUEUED,
            ExecutionLifecycle.RUNNING,
            ExecutionLifecycle.PAUSE_REQUESTED,
            ExecutionLifecycle.PAUSED,
        }:
            raise _command_conflict("TaskRun 当前状态不接受新的 Cancel 命令。")
        return ExecutionLifecycle.CANCELING
    if command_type is TaskRunCommandType.PAUSE:
        if current is not ExecutionLifecycle.RUNNING:
            raise _command_conflict("只有 RUNNING TaskRun 可以暂停派发。")
        return ExecutionLifecycle.PAUSE_REQUESTED
    if current is not ExecutionLifecycle.PAUSED:
        raise _command_conflict("只有 PAUSED TaskRun 可以继续派发。")
    return ExecutionLifecycle.PAUSED


def _command_event_type(command_type: TaskRunCommandType) -> str:
    return {
        TaskRunCommandType.CANCEL: "task_run.cancel_requested",
        TaskRunCommandType.PAUSE: "task_run.pause_requested",
        TaskRunCommandType.RESUME: "task_run.resume_requested",
    }[command_type]


def _not_found(detail: str = "TaskRun 不存在或不可见。") -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="资源不存在",
        detail=detail,
        status_code=404,
    )


def _forbidden() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="无权控制 TaskRun",
        detail="当前身份不能操作该 Project 的 TaskRun。",
        status_code=403,
    )


def _invalid_request(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="TaskRun Command 请求无效",
        detail=detail,
        status_code=400,
    )


def _command_conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="TaskRun Command 冲突",
        detail=detail,
        status_code=409,
    )


def _idempotency_conflict() -> ApplicationError:
    return _command_conflict("clientMutationId 已绑定到不同的 TaskRun Revision。")


def _revision_conflict(current_revision: int) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.PRECONDITION_FAILED,
        title="TaskRun Revision 已变化",
        detail="请刷新 TaskRun 后使用最新 ETag 重试。",
        status_code=412,
        headers={"ETag": format_revision_etag(current_revision)},
    )
