"""Fenced UnitAttempt live-control application services."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.concurrency import format_control_epoch_etag
from atlas_testops.core.contracts import new_entity_id
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.platform import EnvironmentKind
from atlas_testops.domain.runtime import (
    AcknowledgeLiveControl,
    CompleteLiveActionGrant,
    ConsumeLiveActionGrant,
    ControlLease,
    ControlLeaseState,
    HeartbeatLiveControl,
    InitializeLiveSession,
    LiveActionGrant,
    LiveActionGrantState,
    LiveControlCommand,
    LiveControlCommandStatus,
    LiveControlCommandType,
    LiveControlEvent,
    LiveControllerType,
    LiveSession,
    LiveSessionState,
    ReapedLiveControlBatch,
    RequestLiveActionGrant,
    RequestLiveControl,
    UnitAttemptLiveSnapshot,
)
from atlas_testops.domain.task import ExecutionLifecycle, TaskUnitExecutionTicket, UnitAttempt
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.live_control import LiveControlRepository
from atlas_testops.infrastructure.repositories.task_execution_tickets import (
    TaskExecutionTicketRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository

AGENT_LEASE_TTL = timedelta(seconds=120)
DEFAULT_HUMAN_LEASE_TTL = timedelta(seconds=300)


class LiveControlService:
    """Coordinate public commands and database-free Worker control operations."""

    def __init__(
        self,
        database: Database,
        *,
        repository: LiveControlRepository | None = None,
        task_repository: TaskRunRepository | None = None,
        ticket_repository: TaskExecutionTicketRepository | None = None,
    ) -> None:
        self._database = database
        self._live = repository or LiveControlRepository()
        self._tasks = task_repository or TaskRunRepository()
        self._tickets = ticket_repository or TaskExecutionTicketRepository()

    async def get_snapshot(
        self,
        actor: ActorContext,
        unit_attempt_id: UUID,
    ) -> UnitAttemptLiveSnapshot:
        """Return one exact, project-authorized control projection."""

        async with self._database.transaction(actor.database_context()) as connection:
            session = await self._live.get_session_by_attempt(
                connection,
                unit_attempt_id,
            )
            self._require_visible(actor, session)
            assert session is not None
            return await self._snapshot(connection, session)

    async def get_command(
        self,
        actor: ActorContext,
        *,
        unit_attempt_id: UUID,
        command_id: UUID,
    ) -> LiveControlCommand:
        """Read one command without exposing any Worker permit or secret."""

        async with self._database.transaction(actor.database_context()) as connection:
            session = await self._live.get_session_by_attempt(
                connection,
                unit_attempt_id,
            )
            self._require_visible(actor, session)
            assert session is not None
            command = await self._live.get_command(connection, command_id)
            if command is None or command.live_session_id != session.id:
                raise _not_found("LiveControlCommand 不存在或不属于当前 UnitAttempt。")
            return command

    async def pause(
        self,
        actor: ActorContext,
        unit_attempt_id: UUID,
        request: RequestLiveControl,
        *,
        expected_control_epoch: int,
        idempotency_key: str,
    ) -> CommandResult[LiveControlCommand]:
        return await self._request_transition(
            actor,
            unit_attempt_id,
            request,
            command_type=LiveControlCommandType.PAUSE,
            expected_control_epoch=expected_control_epoch,
            idempotency_key=idempotency_key,
        )

    async def resume(
        self,
        actor: ActorContext,
        unit_attempt_id: UUID,
        request: RequestLiveControl,
        *,
        expected_control_epoch: int,
        idempotency_key: str,
    ) -> CommandResult[LiveControlCommand]:
        return await self._request_transition(
            actor,
            unit_attempt_id,
            request,
            command_type=LiveControlCommandType.RESUME,
            expected_control_epoch=expected_control_epoch,
            idempotency_key=idempotency_key,
        )

    async def takeover(
        self,
        actor: ActorContext,
        unit_attempt_id: UUID,
        request: RequestLiveControl,
        *,
        expected_control_epoch: int,
        idempotency_key: str,
    ) -> CommandResult[LiveControlCommand]:
        return await self._request_transition(
            actor,
            unit_attempt_id,
            request,
            command_type=LiveControlCommandType.TAKEOVER,
            expected_control_epoch=expected_control_epoch,
            idempotency_key=idempotency_key,
        )

    async def return_control(
        self,
        actor: ActorContext,
        unit_attempt_id: UUID,
        request: RequestLiveControl,
        *,
        expected_control_epoch: int,
        idempotency_key: str,
    ) -> CommandResult[LiveControlCommand]:
        return await self._request_transition(
            actor,
            unit_attempt_id,
            request,
            command_type=LiveControlCommandType.RETURN,
            expected_control_epoch=expected_control_epoch,
            idempotency_key=idempotency_key,
        )

    async def initialize(
        self,
        tenant_id: UUID,
        unit_attempt_id: UUID,
        request: InitializeLiveSession,
        *,
        worker_identity: str,
    ) -> UnitAttemptLiveSnapshot:
        """Establish the initial Agent lease through a machine-authenticated lane."""

        if request.owner_id != worker_identity:
            raise _fenced("Agent ownerId 必须与已认证 Worker 身份一致。")
        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"live-initialize:{unit_attempt_id}",
        )
        async with self._database.transaction(context) as connection:
            existing = await self._live.get_session_by_attempt(
                connection,
                unit_attempt_id,
                for_update=True,
            )
            if existing is not None:
                lease = await self._live.get_current_lease(connection, existing.id)
                if (
                    existing.browser_session_id != request.browser_session_id
                    or lease is None
                    or lease.owner_type is not LiveControllerType.AGENT
                    or lease.owner_id != worker_identity
                ):
                    raise _conflict(
                        "UnitAttempt 已绑定不同 BrowserSession 或 Agent controller。"
                    )
                return await self._snapshot(connection, existing)

            attempt, ticket = await self._load_attempt_and_ticket(
                connection,
                unit_attempt_id,
            )
            if attempt.lifecycle is not ExecutionLifecycle.RUNNING:
                raise _conflict("只有 RUNNING UnitAttempt 可以建立正式 LiveSession。")
            now = await _database_now(connection)
            if attempt.execution_deadline <= now:
                raise _conflict("UnitAttempt executionDeadline 已过期。")
            session = LiveSession(
                id=new_entity_id(),
                tenant_id=attempt.tenant_id,
                project_id=attempt.project_id,
                task_run_id=attempt.task_run_id,
                execution_unit_id=attempt.execution_unit_id,
                unit_attempt_id=attempt.id,
                execution_ticket_id=ticket.id,
                execution_ticket_digest=ticket.ticket_digest,
                browser_session_id=request.browser_session_id,
                state=LiveSessionState.AGENT_CONTROLLED,
                control_epoch=1,
                fencing_token=1,
                browser_revision=request.browser_revision,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            stored = await self._live.create_session(connection, session)
            if stored is None:
                raise _conflict("LiveSession 正在由另一个 Worker 建立，请重新读取。")
            lease = _new_lease(
                stored,
                owner_type=LiveControllerType.AGENT,
                owner_id=worker_identity,
                reason="initial agent control",
                created_by=None,
                expires_at=min(
                    now + timedelta(seconds=request.requested_ttl_sec),
                    attempt.execution_deadline,
                ),
                now=now,
            )
            await self._live.create_lease(connection, lease)
            await self._append_event(
                connection,
                session=stored,
                event_type="live_session.initialized",
                payload={
                    "ownerType": LiveControllerType.AGENT.value,
                    "browserRevision": stored.browser_revision,
                },
                occurred_at=now,
            )
            return await self._snapshot(connection, stored)

    async def heartbeat(
        self,
        tenant_id: UUID,
        unit_attempt_id: UUID,
        request: HeartbeatLiveControl,
        *,
        worker_identity: str,
    ) -> UnitAttemptLiveSnapshot:
        """Renew the current Agent lease without changing Epoch/Fence."""

        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"live-heartbeat:{unit_attempt_id}",
        )
        async with self._database.transaction(context) as connection:
            session = await self._require_runtime_session(
                connection,
                unit_attempt_id,
            )
            lease = await self._live.get_current_lease(
                connection,
                session.id,
                for_update=True,
            )
            if (
                session.state is not LiveSessionState.AGENT_CONTROLLED
                or lease is None
                or lease.state is not ControlLeaseState.ACTIVE
                or lease.owner_type is not LiveControllerType.AGENT
                or lease.owner_id != worker_identity
                or request.control_epoch != session.control_epoch
                or request.fencing_token != session.fencing_token
                or request.control_epoch != lease.control_epoch
                or request.fencing_token != lease.fencing_token
            ):
                raise _fenced("Heartbeat 使用了陈旧或不属于当前 Worker 的 Epoch/Fence。")
            now = await _database_now(connection)
            if lease.expires_at <= now:
                expired = await self._expire_current_control(
                    connection,
                    session=session,
                    lease=lease,
                    now=now,
                )
                if expired is None:
                    raise _fenced("ControlLease 已过期或由其他 Reconciler 回收。")
                return await self._snapshot(connection, expired)
            attempt, _ = await self._load_attempt_and_ticket(
                connection,
                unit_attempt_id,
            )
            expires_at = min(
                now + timedelta(seconds=request.requested_ttl_sec),
                attempt.execution_deadline,
            )
            if expires_at <= lease.expires_at:
                return await self._snapshot(connection, session)
            renewed = await self._live.heartbeat_lease(
                connection,
                session=session,
                lease=lease,
                expires_at=expires_at,
                now=now,
            )
            if renewed is None:
                raise _fenced("ControlLease Heartbeat 丢失当前 Epoch/Fence。")
            return UnitAttemptLiveSnapshot(
                session=session,
                lease=renewed,
                pending_command=None,
                observed_at=now,
            )

    async def reap_expired(
        self,
        actor: ActorContext,
        *,
        limit: int,
    ) -> ReapedLiveControlBatch:
        """Reconcile a bounded tenant-scoped batch of expired controllers."""

        if not actor.is_organization_admin():
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="没有 Live Control 回收权限",
                detail="只有组织管理员或内部 Reconciler 可以回收过期 controller。",
                status_code=403,
            )
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        async with self._database.transaction(actor.database_context()) as connection:
            now = await _database_now(connection)
            session_ids = await self._live.claim_expired_session_ids(
                connection,
                now=now,
                limit=limit,
            )
            reaped = 0
            for session_id in session_ids:
                session = await self._live.get_session_by_id(
                    connection,
                    session_id,
                    for_update=True,
                )
                if session is None:
                    continue
                lease = await self._live.get_current_lease(
                    connection,
                    session.id,
                    for_update=True,
                )
                if lease is None:
                    continue
                if (
                    await self._expire_current_control(
                        connection,
                        session=session,
                        lease=lease,
                        now=now,
                    )
                    is not None
                ):
                    reaped += 1
        return ReapedLiveControlBatch(reaped=reaped, observed_at=now)

    async def acknowledge(
        self,
        tenant_id: UUID,
        unit_attempt_id: UUID,
        request: AcknowledgeLiveControl,
        *,
        worker_identity: str,
    ) -> UnitAttemptLiveSnapshot:
        """Apply a transition only after the Worker proves a safe checkpoint."""

        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"live-ack:{request.command_id}",
        )
        async with self._database.transaction(context) as connection:
            session = await self._require_runtime_session(
                connection,
                unit_attempt_id,
            )
            command = await self._live.get_command(connection, request.command_id)
            if command is None or command.live_session_id != session.id:
                raise _not_found("待确认的 LiveControlCommand 不存在。")
            if command.status is LiveControlCommandStatus.APPLIED:
                if (
                    command.expected_control_epoch != request.expected_control_epoch
                    or command.checkpoint_digest != request.checkpoint_digest
                ):
                    raise _conflict("LiveControlCommand 已由不同 Safe Point 完成。")
                return await self._snapshot(connection, session)
            if command.status is not LiveControlCommandStatus.PENDING:
                raise _conflict("LiveControlCommand 已终结且不可确认。")
            if (
                command.expected_control_epoch != session.control_epoch
                or request.expected_control_epoch != session.control_epoch
                or request.expected_fencing_token != session.fencing_token
            ):
                raise _fenced("Safe Point acknowledgement 使用了陈旧 Epoch/Fence。")
            if request.browser_revision < session.browser_revision:
                raise _conflict("Safe Point browserRevision 不能回退。")
            if await self._live.has_inflight_action(connection, session.id):
                raise _conflict("仍有已消费但未回执的 ActionGrant，尚未到达 Safe Point。")
            attempt, _ = await self._load_attempt_and_ticket(
                connection,
                unit_attempt_id,
            )
            now = await _database_now(connection)
            target_state, owner_type, owner_id, ttl = _ack_target(
                command,
                request,
            )
            if (
                owner_type is LiveControllerType.AGENT
                and owner_id != worker_identity
            ):
                raise _fenced("新 Agent ownerId 必须与已认证 Worker 身份一致。")
            updated_session, _ = await self._live.apply_transition(
                connection,
                session=session,
                command=command,
                state=target_state,
                browser_revision=request.browser_revision,
                human_influenced=(
                    session.human_influenced
                    or command.command_type is LiveControlCommandType.TAKEOVER
                ),
                checkpoint_digest=request.checkpoint_digest,
                now=now,
            )
            if owner_type is not None and owner_id is not None:
                expires_at = min(now + ttl, attempt.execution_deadline)
                if expires_at <= now:
                    raise _conflict("UnitAttempt 已无可签发 ControlLease 的剩余时间。")
                await self._live.create_lease(
                    connection,
                    _new_lease(
                        updated_session,
                        owner_type=owner_type,
                        owner_id=owner_id,
                        reason=f"{command.command_type.value.lower()} applied",
                        created_by=command.requested_by,
                        expires_at=expires_at,
                        now=now,
                    ),
                )
            await self._append_event(
                connection,
                session=updated_session,
                event_type=f"live_control.{command.command_type.value.lower()}.applied",
                payload={
                    "commandId": str(command.id),
                    "checkpointDigest": request.checkpoint_digest,
                    "browserRevision": request.browser_revision,
                    "controllerType": owner_type.value if owner_type else None,
                },
                occurred_at=now,
            )
            return await self._snapshot(connection, updated_session)

    async def issue_action_grant(
        self,
        tenant_id: UUID,
        unit_attempt_id: UUID,
        request: RequestLiveActionGrant,
        *,
        worker_identity: str,
    ) -> LiveActionGrant:
        """Issue or exactly replay one Policy-approved persistent ActionGrant."""

        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"live-grant:{request.action_id}",
        )
        async with self._database.transaction(context) as connection:
            session = await self._require_runtime_session(
                connection,
                unit_attempt_id,
            )
            existing = await self._live.get_action_grant_by_action(
                connection,
                unit_attempt_id=unit_attempt_id,
                action_id=request.action_id,
            )
            if existing is not None:
                if not _grant_request_matches(existing, request):
                    raise _conflict("actionId 已绑定不同 Proposal 或控制权。")
                return existing
            lease = await self._live.get_current_lease(
                connection,
                session.id,
                for_update=True,
            )
            now = await _database_now(connection)
            _require_grant_authority(
                session,
                lease,
                request,
                worker_identity=worker_identity,
                now=now,
            )
            assert lease is not None
            expires_at = min(
                now + timedelta(seconds=request.requested_ttl_sec),
                lease.expires_at,
            )
            if expires_at <= now:
                raise _fenced("ControlLease 已过期，不能签发 ActionGrant。")
            grant = LiveActionGrant(
                grant_id=new_entity_id(),
                tenant_id=session.tenant_id,
                project_id=session.project_id,
                task_run_id=session.task_run_id,
                execution_unit_id=session.execution_unit_id,
                unit_attempt_id=session.unit_attempt_id,
                live_session_id=session.id,
                control_lease_id=lease.id,
                action_id=request.action_id,
                proposal_digest=request.proposal_digest,
                browser_session_id=session.browser_session_id,
                page_id=request.page_id,
                page_revision=request.page_revision,
                control_epoch=request.control_epoch,
                fencing_token=request.fencing_token,
                owner_type=lease.owner_type,
                owner_id=lease.owner_id,
                allowed_adapter=request.allowed_adapter,
                expires_at=expires_at,
                policy_digest=request.policy_digest,
                state=LiveActionGrantState.ISSUED,
                created_at=now,
            )
            stored = await self._live.create_action_grant(connection, grant)
            if stored is None:
                raise _conflict("ActionGrant 正在由另一个请求创建，请重新读取。")
            await self._append_event(
                connection,
                session=session,
                event_type="live_action.grant_issued",
                payload={
                    "grantId": str(stored.grant_id),
                    "actionId": str(stored.action_id),
                    "ownerType": stored.owner_type.value,
                    "pageRevision": stored.page_revision,
                },
                occurred_at=now,
            )
            return stored

    async def get_action_grant(
        self,
        tenant_id: UUID,
        unit_attempt_id: UUID,
        grant_id: UUID,
    ) -> LiveActionGrant:
        """Let the Worker recover a lost consume/receipt response without replay."""

        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"live-grant-read:{grant_id}",
        )
        async with self._database.transaction(context) as connection:
            await self._require_runtime_session(connection, unit_attempt_id)
            grant = await self._live.get_action_grant(connection, grant_id)
            if grant is None or grant.unit_attempt_id != unit_attempt_id:
                raise _not_found("ActionGrant 不存在或不属于当前 UnitAttempt。")
            return grant

    async def consume_action_grant(
        self,
        tenant_id: UUID,
        unit_attempt_id: UUID,
        grant_id: UUID,
        request: ConsumeLiveActionGrant,
    ) -> LiveActionGrant:
        """Atomically consume a Grant before any Playwright side effect."""

        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"live-grant-consume:{grant_id}",
        )
        async with self._database.transaction(context) as connection:
            session = await self._require_runtime_session(
                connection,
                unit_attempt_id,
            )
            grant = await self._live.get_action_grant(
                connection,
                grant_id,
                for_update=True,
            )
            if grant is None or grant.live_session_id != session.id:
                raise _not_found("ActionGrant 不存在或不属于当前 UnitAttempt。")
            if (
                grant.control_epoch != request.control_epoch
                or grant.fencing_token != request.fencing_token
                or grant.proposal_digest != request.proposal_digest
            ):
                raise _fenced("ActionGrant Epoch/Fence/Proposal 绑定不匹配。")
            if grant.state is not LiveActionGrantState.ISSUED:
                raise _conflict(
                    "ActionGrant 已消费或撤销；请查询状态恢复，禁止重复执行副作用。"
                )
            now = await _database_now(connection)
            consumed = await self._live.consume_action_grant(
                connection,
                grant=grant,
                now=now,
            )
            if consumed is None:
                raise _fenced("ActionGrant 已过期、被撤销或 controller 已被替换。")
            await self._append_event(
                connection,
                session=session,
                event_type="live_action.grant_consumed",
                payload={
                    "grantId": str(consumed.grant_id),
                    "actionId": str(consumed.action_id),
                },
                occurred_at=now,
            )
            return consumed

    async def complete_action_grant(
        self,
        tenant_id: UUID,
        unit_attempt_id: UUID,
        grant_id: UUID,
        request: CompleteLiveActionGrant,
    ) -> LiveActionGrant:
        """Attach one exact adapter receipt and advance the browser revision."""

        context = DatabaseContext(
            tenant_id=tenant_id,
            request_id=f"live-grant-complete:{grant_id}",
        )
        async with self._database.transaction(context) as connection:
            session = await self._require_runtime_session(
                connection,
                unit_attempt_id,
            )
            grant = await self._live.get_action_grant(
                connection,
                grant_id,
                for_update=True,
            )
            if grant is None or grant.live_session_id != session.id:
                raise _not_found("ActionGrant 不存在或不属于当前 UnitAttempt。")
            if (
                grant.control_epoch != request.control_epoch
                or grant.fencing_token != request.fencing_token
            ):
                raise _fenced("Action receipt 使用了错误的 Epoch/Fence。")
            if grant.state is LiveActionGrantState.COMPLETED:
                if (
                    grant.receipt_id != request.receipt_id
                    or grant.execution_status is not request.execution_status
                    or grant.resulting_page_revision
                    != request.resulting_page_revision
                ):
                    raise _conflict("ActionGrant 已绑定不同 ExecutionReceipt。")
                return grant
            if grant.state is not LiveActionGrantState.CONSUMED:
                raise _conflict("只有已消费的 ActionGrant 可以写入 ExecutionReceipt。")
            if request.resulting_page_revision < session.browser_revision:
                raise _conflict("ExecutionReceipt browserRevision 不能回退。")
            now = await _database_now(connection)
            completed = await self._live.complete_action_grant(
                connection,
                grant=grant,
                receipt_id=request.receipt_id,
                execution_status=request.execution_status.value,
                resulting_page_revision=request.resulting_page_revision,
                now=now,
            )
            if completed is None:
                raise _conflict("ActionGrant completion lost its single-use state。")
            updated_session = await self._live.advance_browser_revision(
                connection,
                session=session,
                browser_revision=request.resulting_page_revision,
                now=now,
            )
            await self._append_event(
                connection,
                session=updated_session,
                event_type="live_action.execution_completed",
                payload={
                    "grantId": str(completed.grant_id),
                    "actionId": str(completed.action_id),
                    "receiptId": str(request.receipt_id),
                    "status": request.execution_status.value,
                    "resultingPageRevision": request.resulting_page_revision,
                },
                occurred_at=now,
            )
            return completed

    async def _request_transition(
        self,
        actor: ActorContext,
        unit_attempt_id: UUID,
        request: RequestLiveControl,
        *,
        command_type: LiveControlCommandType,
        expected_control_epoch: int,
        idempotency_key: str,
    ) -> CommandResult[LiveControlCommand]:
        if request.requested_ttl_sec is not None and (
            command_type is not LiveControlCommandType.TAKEOVER
        ):
            raise _invalid("requestedTtlSec 只适用于 Human Takeover。")
        async with self._database.transaction(actor.database_context()) as connection:
            session = await self._live.get_session_by_attempt(
                connection,
                unit_attempt_id,
                for_update=True,
            )
            self._require_operable(actor, session)
            assert session is not None
            existing = await self._live.get_command_by_mutation(
                connection,
                live_session_id=session.id,
                client_mutation_id=idempotency_key,
            )
            if existing is not None:
                if not _command_request_matches(
                    existing,
                    command_type=command_type,
                    request=request,
                    expected_control_epoch=expected_control_epoch,
                ):
                    raise _conflict("Idempotency-Key 已用于不同 LiveControlCommand。")
                return CommandResult(value=existing, status_code=202, replayed=True)
            if session.control_epoch != expected_control_epoch:
                raise _epoch_changed(session.control_epoch)
            if await self._live.get_pending_command(connection, session.id) is not None:
                raise _conflict("LiveSession 已有尚未到达 Safe Point 的控制命令。")
            lease = await self._live.get_current_lease(
                connection,
                session.id,
                for_update=True,
            )
            now = await _database_now(connection)
            await self._validate_transition(
                connection,
                session=session,
                lease=lease,
                command_type=command_type,
                now=now,
            )
            requested_state, revoke = _requested_state(command_type)
            updated_session = await self._live.request_transition(
                connection,
                session=session,
                state=requested_state,
                now=now,
                revoke_current_lease=revoke,
            )
            command = LiveControlCommand(
                id=new_entity_id(),
                tenant_id=session.tenant_id,
                project_id=session.project_id,
                task_run_id=session.task_run_id,
                execution_unit_id=session.execution_unit_id,
                unit_attempt_id=session.unit_attempt_id,
                live_session_id=session.id,
                command_type=command_type,
                client_mutation_id=idempotency_key,
                reason=request.reason,
                requested_ttl_sec=request.requested_ttl_sec,
                expected_control_epoch=expected_control_epoch,
                accepted_session_revision=updated_session.revision,
                status=LiveControlCommandStatus.PENDING,
                requested_by=actor.actor_id,
                created_at=now,
                updated_at=now,
            )
            stored = await self._live.create_command(connection, command)
            await self._append_event(
                connection,
                session=updated_session,
                event_type=f"live_control.{command_type.value.lower()}.requested",
                payload={
                    "commandId": str(stored.id),
                    "requestedBy": str(actor.actor_id) if actor.actor_id else None,
                    "reason": request.reason,
                },
                occurred_at=now,
            )
            return CommandResult(value=stored, status_code=202, replayed=False)

    async def _validate_transition(
        self,
        connection: AsyncConnection[DictRow],
        *,
        session: LiveSession,
        lease: ControlLease | None,
        command_type: LiveControlCommandType,
        now: datetime,
    ) -> None:
        expected: dict[LiveControlCommandType, LiveSessionState] = {
            LiveControlCommandType.PAUSE: LiveSessionState.AGENT_CONTROLLED,
            LiveControlCommandType.RESUME: LiveSessionState.PAUSED,
            LiveControlCommandType.TAKEOVER: LiveSessionState.AGENT_CONTROLLED,
            LiveControlCommandType.RETURN: LiveSessionState.HUMAN_CONTROLLED,
        }
        if session.state is not expected[command_type]:
            raise _conflict(
                f"{command_type.value} 不适用于当前 {session.state.value} 状态。"
            )
        requires_lease = command_type is not LiveControlCommandType.RESUME
        if requires_lease and (
            lease is None
            or lease.state is not ControlLeaseState.ACTIVE
            or lease.expires_at <= now
            or lease.control_epoch != session.control_epoch
            or lease.fencing_token != session.fencing_token
        ):
            raise _fenced("当前 controller 没有匹配 LiveSession 的活动 ControlLease。")
        if command_type in {
            LiveControlCommandType.PAUSE,
            LiveControlCommandType.TAKEOVER,
        } and (lease is None or lease.owner_type is not LiveControllerType.AGENT):
            raise _conflict(f"{command_type.value} 要求当前 controller 为 AGENT。")
        if command_type is LiveControlCommandType.RETURN and (
            lease is None or lease.owner_type is not LiveControllerType.HUMAN
        ):
            raise _conflict("RETURN 要求当前 controller 为 HUMAN。")
        if command_type is LiveControlCommandType.TAKEOVER:
            ticket = await self._tickets.get_by_attempt(
                connection,
                session.unit_attempt_id,
            )
            if ticket is None:
                raise _conflict("LiveSession 缺少 immutable ExecutionTicket。")
            kind = await self._live.get_environment_kind(
                connection,
                ticket.environment_id,
            )
            if kind == EnvironmentKind.PRODUCTION.value:
                raise ApplicationError(
                    error_code=ErrorCode.FORBIDDEN,
                    title="Production Takeover 被禁止",
                    detail="V1 不允许人工接管 Production Environment 浏览器。",
                    status_code=403,
                )

    async def _load_attempt_and_ticket(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
    ) -> tuple[UnitAttempt, TaskUnitExecutionTicket]:
        attempt = await self._tasks.get_attempt(connection, unit_attempt_id)
        ticket = await self._tickets.get_by_attempt(connection, unit_attempt_id)
        if attempt is None or ticket is None:
            raise _not_found("UnitAttempt 或 immutable ExecutionTicket 不存在。")
        if (
            ticket.tenant_id != attempt.tenant_id
            or ticket.project_id != attempt.project_id
            or ticket.task_run_id != attempt.task_run_id
            or ticket.execution_unit_id != attempt.execution_unit_id
            or ticket.manifest_hash != attempt.manifest_hash
            or ticket.unit_key != attempt.unit_key
        ):
            raise _conflict("UnitAttempt 与 ExecutionTicket scope 不一致。")
        return attempt, ticket

    async def _require_runtime_session(
        self,
        connection: AsyncConnection[DictRow],
        unit_attempt_id: UUID,
    ) -> LiveSession:
        session = await self._live.get_session_by_attempt(
            connection,
            unit_attempt_id,
            for_update=True,
        )
        if session is None:
            raise _not_found("正式 LiveSession 尚未建立。")
        return session

    async def _snapshot(
        self,
        connection: AsyncConnection[DictRow],
        session: LiveSession,
    ) -> UnitAttemptLiveSnapshot:
        return UnitAttemptLiveSnapshot(
            session=session,
            lease=await self._live.get_current_lease(connection, session.id),
            pending_command=await self._live.get_pending_command(
                connection,
                session.id,
            ),
            observed_at=await _database_now(connection),
        )

    async def _expire_current_control(
        self,
        connection: AsyncConnection[DictRow],
        *,
        session: LiveSession,
        lease: ControlLease,
        now: datetime,
    ) -> LiveSession | None:
        expired = await self._live.expire_current_control(
            connection,
            session=session,
            lease=lease,
            now=now,
        )
        if expired is None:
            return None
        updated_session, expired_lease = expired
        await self._append_event(
            connection,
            session=updated_session,
            event_type="live_control.lease_expired",
            payload={
                "leaseId": str(expired_lease.id),
                "ownerType": expired_lease.owner_type.value,
                "ownerId": expired_lease.owner_id,
                "expiredControlEpoch": expired_lease.control_epoch,
                "expiredFencingToken": expired_lease.fencing_token,
            },
            occurred_at=now,
        )
        return updated_session

    async def _append_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        session: LiveSession,
        event_type: str,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> None:
        await self._live.append_event(
            connection,
            LiveControlEvent(
                id=new_entity_id(),
                tenant_id=session.tenant_id,
                project_id=session.project_id,
                task_run_id=session.task_run_id,
                execution_unit_id=session.execution_unit_id,
                unit_attempt_id=session.unit_attempt_id,
                live_session_id=session.id,
                seq=await self._live.next_event_seq(connection, session.id),
                event_type=event_type,
                control_epoch=session.control_epoch,
                fencing_token=session.fencing_token,
                payload=payload,
                occurred_at=occurred_at,
            ),
        )

    @staticmethod
    def _require_visible(
        actor: ActorContext,
        session: LiveSession | None,
    ) -> None:
        if session is None or not actor.can_read_project(session.project_id):
            raise _not_found()

    @staticmethod
    def _require_operable(
        actor: ActorContext,
        session: LiveSession | None,
    ) -> None:
        LiveControlService._require_visible(actor, session)
        assert session is not None
        if not actor.can_operate_project(session.project_id):
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="没有 Live Control 权限",
                detail="当前角色不能控制该 UnitAttempt 的浏览器。",
                status_code=403,
            )
        if actor.actor_id is None:
            raise ApplicationError(
                error_code=ErrorCode.FORBIDDEN,
                title="缺少可审计操作者",
                detail="Live Control 命令要求可审计的 Actor 身份。",
                status_code=403,
            )


def _requested_state(
    command_type: LiveControlCommandType,
) -> tuple[LiveSessionState, bool]:
    if command_type in {
        LiveControlCommandType.PAUSE,
        LiveControlCommandType.TAKEOVER,
    }:
        return LiveSessionState.QUIESCING, True
    if command_type is LiveControlCommandType.RESUME:
        return LiveSessionState.RESUME_REQUESTED, False
    return LiveSessionState.RECONCILING, True


def _ack_target(
    command: LiveControlCommand,
    request: AcknowledgeLiveControl,
) -> tuple[
    LiveSessionState,
    LiveControllerType | None,
    str | None,
    timedelta,
]:
    if command.command_type is LiveControlCommandType.PAUSE:
        return LiveSessionState.PAUSED, None, None, timedelta(0)
    if command.command_type is LiveControlCommandType.TAKEOVER:
        if command.requested_by is None:
            raise _conflict("Takeover 缺少可审计的 Human controller。")
        return (
            LiveSessionState.HUMAN_CONTROLLED,
            LiveControllerType.HUMAN,
            f"user:{command.requested_by}",
            timedelta(
                seconds=command.requested_ttl_sec
                or int(DEFAULT_HUMAN_LEASE_TTL.total_seconds())
            ),
        )
    return (
        LiveSessionState.AGENT_CONTROLLED,
        LiveControllerType.AGENT,
        request.agent_owner_id,
        AGENT_LEASE_TTL,
    )


def _new_lease(
    session: LiveSession,
    *,
    owner_type: LiveControllerType,
    owner_id: str,
    reason: str,
    created_by: UUID | None,
    expires_at: datetime,
    now: datetime,
) -> ControlLease:
    return ControlLease(
        id=new_entity_id(),
        tenant_id=session.tenant_id,
        project_id=session.project_id,
        task_run_id=session.task_run_id,
        execution_unit_id=session.execution_unit_id,
        unit_attempt_id=session.unit_attempt_id,
        live_session_id=session.id,
        owner_type=owner_type,
        owner_id=owner_id,
        control_epoch=session.control_epoch,
        fencing_token=session.fencing_token,
        state=ControlLeaseState.ACTIVE,
        expires_at=expires_at,
        reason=reason,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )


def _require_grant_authority(
    session: LiveSession,
    lease: ControlLease | None,
    request: RequestLiveActionGrant,
    *,
    worker_identity: str,
    now: datetime,
) -> None:
    if (
        session.state
        not in {
            LiveSessionState.AGENT_CONTROLLED,
            LiveSessionState.HUMAN_CONTROLLED,
        }
        or lease is None
        or lease.state is not ControlLeaseState.ACTIVE
        or lease.expires_at <= now
        or request.control_epoch != session.control_epoch
        or request.fencing_token != session.fencing_token
        or request.control_epoch != lease.control_epoch
        or request.fencing_token != lease.fencing_token
    ):
        raise _fenced("当前 Epoch/Fence 没有可签发 ActionGrant 的活动 controller。")
    if request.page_revision != session.browser_revision:
        raise _conflict("ActionProposal pageRevision 已陈旧。")
    if (
        lease.owner_type is LiveControllerType.AGENT
        and lease.owner_id != worker_identity
    ):
        raise _fenced("Agent ActionGrant 请求来自不同 Worker。")


def _grant_request_matches(
    grant: LiveActionGrant,
    request: RequestLiveActionGrant,
) -> bool:
    return (
        grant.action_id == request.action_id
        and grant.proposal_digest == request.proposal_digest
        and grant.page_id == request.page_id
        and grant.page_revision == request.page_revision
        and grant.control_epoch == request.control_epoch
        and grant.fencing_token == request.fencing_token
        and grant.allowed_adapter == request.allowed_adapter
        and grant.policy_digest == request.policy_digest
    )


def _command_request_matches(
    command: LiveControlCommand,
    *,
    command_type: LiveControlCommandType,
    request: RequestLiveControl,
    expected_control_epoch: int,
) -> bool:
    return (
        command.command_type is command_type
        and command.reason == request.reason
        and command.requested_ttl_sec == request.requested_ttl_sec
        and command.expected_control_epoch == expected_control_epoch
    )


async def _database_now(connection: AsyncConnection[DictRow]) -> datetime:
    cursor = await connection.execute("select transaction_timestamp() as value")
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("database did not return transaction time")
    return datetime.fromisoformat(str(row["value"]))


def _not_found(detail: str = "UnitAttempt LiveSession 不存在或不可见。") -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="LiveSession 不存在",
        detail=detail,
        status_code=404,
    )


def _invalid(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="Live Control 请求无效",
        detail=detail,
        status_code=400,
    )


def _conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="Live Control 状态冲突",
        detail=detail,
        status_code=409,
    )


def _fenced(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.LEASE_FENCED,
        title="Live Controller 已被 Fencing",
        detail=detail,
        status_code=409,
    )


def _epoch_changed(current_epoch: int) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.PRECONDITION_FAILED,
        title="Control Epoch 已变化",
        detail="If-Match 对应的 Control Epoch 已陈旧，请刷新 LiveSnapshot。",
        status_code=409,
        headers={"ETag": format_control_epoch_etag(current_epoch)},
    )


__all__ = ["LiveControlService"]
