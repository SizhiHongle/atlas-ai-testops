"""DebugRun snapshot, dispatch, replay, and cancellation control plane."""

from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.debug_run_dispatcher import DebugRunDispatcher
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.core.contracts import WireModel, new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import decode_cursor, next_time_cursor
from atlas_testops.domain.case import (
    CaseCompilationResult,
    DebugRun,
    DebugRunEventPage,
    DebugRunLifecycle,
    DebugRunPage,
    RequestDebugRunCancel,
    StartDebugRun,
    TestCase,
    TestCaseStatus,
    WorkflowDraftSnapshot,
    compile_case,
    semantic_digest,
)
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.platform import EnvironmentKind, EnvironmentStatus, ProjectStatus
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.cases import CaseRepository
from atlas_testops.infrastructure.repositories.debug_runs import DebugRunRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository

DEBUG_RUN_IDEMPOTENCY_TTL = timedelta(hours=24)


class DebugRunService:
    """Freeze valid Draft semantics and dispatch only to a trusted runtime."""

    def __init__(
        self,
        database: Database,
        dispatcher: DebugRunDispatcher | None,
        *,
        case_repository: CaseRepository | None = None,
        debug_run_repository: DebugRunRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self._database = database
        self._dispatcher = dispatcher
        self._cases = case_repository or CaseRepository()
        self._runs = debug_run_repository or DebugRunRepository()
        self._platform = platform_repository or PlatformRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def start(
        self,
        actor: ActorContext,
        case_id: UUID,
        command: StartDebugRun,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[DebugRun]:
        """Freeze and enqueue one exact Draft snapshot after full compilation."""

        now = utc_now()
        if command.execution_deadline <= now:
            raise self._invalid_request("executionDeadline 必须晚于当前时间。")
        request_payload: dict[str, JsonValue] = {
            "testCaseId": str(case_id),
            **self._json_object(command),
        }
        request_hash = hash_request(request_payload)
        scope = f"test-cases.{case_id}.debug-runs.create"
        async with self._database.transaction(actor.database_context()) as connection:
            case = await self._require_case(
                connection,
                actor,
                case_id,
                for_share=True,
            )
            self._require_author(actor, case.project_id)
            if case.status is TestCaseStatus.ARCHIVED:
                raise self._conflict("已归档 TestCase 不能启动 DebugRun。")
            project = await self._platform.get_project_for_share(
                connection,
                case.project_id,
            )
            if project is None or project.status is not ProjectStatus.ACTIVE:
                raise self._conflict("只有活动 Project 可以启动 DebugRun。")
            environment = await self._platform.get_environment_for_share(
                connection,
                command.environment_id,
            )
            if environment is None or environment.project_id != case.project_id:
                raise self._not_found("Environment 不存在或不属于该 Project。")
            if environment.status is not EnvironmentStatus.ACTIVE:
                raise self._conflict("已停用 Environment 不能启动 DebugRun。")
            if environment.kind is EnvironmentKind.PRODUCTION:
                raise self._forbidden("DebugRun 禁止在 Production Environment 执行。")
            dispatcher = self._require_dispatcher()

            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=DEBUG_RUN_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                result = CommandResult(
                    value=DebugRun.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            else:
                draft = await self._cases.get_draft_by_case(
                    connection,
                    case_id,
                    for_update=True,
                )
                if draft is None:
                    raise self._not_found("WorkflowDraft 不存在。")
                self._check_revision(draft.semantic_revision, expected_revision)
                self._check_revision(
                    draft.semantic_revision,
                    command.base_semantic_revision,
                )
                self._require_draft_integrity(case, draft)
                compilation = compile_case(
                    test_case_id=case.id,
                    semantic_revision=draft.semantic_revision,
                    intent_version_ref=draft.intent_version_ref,
                    intent=case.intent,
                    graph=draft.graph,
                )
                self._require_compiled(compilation)
                assert compilation.test_ir is not None
                assert compilation.plan_template is not None
                assert compilation.compiled_digest is not None
                run_id = new_entity_id()
                run = await self._runs.create_run(
                    connection,
                    run_id=run_id,
                    draft=draft,
                    command=command,
                    test_ir=compilation.test_ir,
                    plan_template=compilation.plan_template,
                    compiled_digest=compilation.compiled_digest,
                    temporal_workflow_id=f"atlas-debug/{actor.tenant_id}/{run_id}",
                    requested_by=actor.actor_id,
                    requested_at=now,
                )
                event_payload: dict[str, JsonValue] = {
                    "semanticRevision": run.semantic_revision,
                    "semanticDigest": run.semantic_digest,
                    "compiledDigest": run.compiled_digest,
                    "environmentId": str(run.environment_id),
                }
                await self._runs.append_event(
                    connection,
                    run=run,
                    event_type="debug_run.requested",
                    payload=event_payload,
                    occurred_at=now,
                )
                await self._record_event(
                    connection,
                    actor=actor,
                    run=run,
                    event_type="debug_run.requested",
                    payload=event_payload,
                    occurred_at=now,
                )
                response = CachedHttpResponse(
                    status_code=202,
                    body=self._json_object(run),
                )
                await self._idempotency.complete(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    response=response,
                )
                result = CommandResult(value=run, status_code=202, replayed=False)

        try:
            await dispatcher.start(result.value)
        except ApplicationError:
            raise
        except Exception as error:
            raise self._runtime_unavailable(
                "DebugRun 已安全冻结，但暂时无法提交 Browser Runtime；请使用同一请求重试。"
            ) from error
        return result

    async def get(self, actor: ActorContext, run_id: UUID) -> DebugRun:
        """Read one visible DebugRun without requiring a runtime connection."""

        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._runs.get_run(connection, run_id)
            if run is None or not actor.can_read_project(run.project_id):
                raise self._not_found("DebugRun 不存在或不可见。")
            return run

    async def list_for_case(
        self,
        actor: ActorContext,
        case_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> DebugRunPage:
        """List immutable DebugRun snapshots using keyset pagination."""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_case(connection, actor, case_id)
            records = await self._runs.list_runs(
                connection,
                test_case_id=case_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        next_cursor = (
            next_time_cursor(items[-1].requested_at, items[-1].id)
            if len(records) > limit and items
            else None
        )
        return DebugRunPage(items=items, next_cursor=next_cursor)

    async def list_events(
        self,
        actor: ActorContext,
        run_id: UUID,
        *,
        after_seq: int,
        limit: int,
    ) -> DebugRunEventPage:
        """Replay monotonic DebugRun events after one acknowledged sequence."""

        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._runs.get_run(connection, run_id)
            if run is None or not actor.can_read_project(run.project_id):
                raise self._not_found("DebugRun 不存在或不可见。")
            records = await self._runs.list_events(
                connection,
                run_id=run.id,
                after_seq=after_seq,
                limit=limit,
            )
        items = records[:limit]
        next_after_seq = items[-1].seq if len(records) > limit and items else None
        return DebugRunEventPage(items=items, next_after_seq=next_after_seq)

    async def request_cancel(
        self,
        actor: ActorContext,
        run_id: UUID,
        command: RequestDebugRunCancel,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[DebugRun]:
        """Persist a cancellation intent before signaling the trusted runtime."""

        now = utc_now()
        request_hash = hash_request(
            {"debugRunId": str(run_id), **self._json_object(command)}
        )
        scope = f"debug-runs.{run_id}.cancel"
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._runs.get_run(connection, run_id, for_update=True)
            if run is None or not actor.can_read_project(run.project_id):
                raise self._not_found("DebugRun 不存在或不可见。")
            self._require_author(actor, run.project_id)
            if idempotency_key != command.client_mutation_id:
                raise self._invalid_request(
                    "Idempotency-Key 必须与 clientMutationId 完全一致。"
                )
            if actor.actor_id is None:
                raise self._forbidden("取消 DebugRun 需要可审计的 Actor 身份。")
            dispatcher = self._require_dispatcher()
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=DEBUG_RUN_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                result = CommandResult(
                    value=DebugRun.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            else:
                self._check_run_revision(run.revision, expected_revision)
                if run.lifecycle is DebugRunLifecycle.TERMINATED:
                    raise self._conflict("已结束 DebugRun 不能取消。")
                if run.cancel_requested_at is not None:
                    raise self._conflict("DebugRun 已请求取消。")
                canceled = await self._runs.request_cancel(
                    connection,
                    run_id=run.id,
                    expected_revision=expected_revision,
                    requested_by=actor.actor_id,
                    requested_at=now,
                )
                if canceled is None:
                    raise self._run_revision_conflict(run.revision)
                event_payload: dict[str, JsonValue] = {"reason": command.reason}
                await self._runs.append_event(
                    connection,
                    run=canceled,
                    event_type="debug_run.cancel_requested",
                    payload=event_payload,
                    occurred_at=now,
                )
                await self._record_event(
                    connection,
                    actor=actor,
                    run=canceled,
                    event_type="debug_run.cancel_requested",
                    payload=event_payload,
                    occurred_at=now,
                )
                response = CachedHttpResponse(
                    status_code=202,
                    body=self._json_object(canceled),
                )
                await self._idempotency.complete(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    response=response,
                )
                result = CommandResult(value=canceled, status_code=202, replayed=False)

        try:
            await dispatcher.cancel(result.value)
        except ApplicationError:
            raise
        except Exception as error:
            raise self._runtime_unavailable(
                "取消意图已持久化，但 Browser Runtime 暂时不可达；请使用同一请求重试。"
            ) from error
        return result

    async def _require_case(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        case_id: UUID,
        *,
        for_share: bool = False,
    ) -> TestCase:
        case = await self._cases.get_case(
            connection,
            case_id,
            for_share=for_share,
        )
        if case is None or not actor.can_read_project(case.project_id):
            raise self._not_found("TestCase 不存在或不可见。")
        return case

    async def _record_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        run: DebugRun,
        event_type: str,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> None:
        await self._audit.append(
            connection,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            environment_id=run.environment_id,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="debug_run",
            entity_id=run.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=run.tenant_id,
                aggregate_type="debug_run",
                aggregate_id=run.id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    def _require_dispatcher(self) -> DebugRunDispatcher:
        if self._dispatcher is None:
            raise self._runtime_unavailable(
                "当前 API 实例未连接受信任的 Browser Runtime；不会创建或伪造 DebugRun 结果。"
            )
        return self._dispatcher

    @staticmethod
    def _require_draft_integrity(
        case: TestCase,
        draft: WorkflowDraftSnapshot,
    ) -> None:
        expected_digest = semantic_digest(draft.graph, draft.intent_version_ref)
        if (
            draft.intent_version_ref != case.intent_version_ref
            or draft.semantic_digest != expected_digest
        ):
            raise ApplicationError(
                error_code=ErrorCode.VALIDATION_FAILED,
                title="WorkflowDraft 完整性校验失败",
                detail="Draft Intent 引用或 semanticDigest 与权威内容不一致。",
                status_code=422,
            )

    @staticmethod
    def _require_compiled(compilation: CaseCompilationResult) -> None:
        if compilation.valid:
            return
        codes = ", ".join(sorted({issue.code.value for issue in compilation.issues}))
        raise ApplicationError(
            error_code=ErrorCode.VALIDATION_FAILED,
            title="WorkflowDraft 无法启动 DebugRun",
            detail=f"编译门禁未通过: {codes}。",
            status_code=422,
        )

    @staticmethod
    def _require_author(actor: ActorContext, project_id: UUID) -> None:
        if not actor.can_author_cases(project_id):
            raise DebugRunService._forbidden(
                "当前角色不能运行该 Project 的 WorkflowDraft。"
            )

    @staticmethod
    def _check_revision(current_revision: int, expected_revision: int) -> None:
        if current_revision != expected_revision:
            raise ApplicationError(
                error_code=ErrorCode.DRAFT_REVISION_CONFLICT,
                title="WorkflowDraft Revision 已变化",
                detail="请读取最新 WorkflowDraft 后重新启动 DebugRun。",
                status_code=412,
                headers={"ETag": format_revision_etag(current_revision)},
            )

    @staticmethod
    def _check_run_revision(current_revision: int, expected_revision: int) -> None:
        if current_revision != expected_revision:
            raise DebugRunService._run_revision_conflict(current_revision)

    @staticmethod
    def _run_revision_conflict(current_revision: int) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.PRECONDITION_FAILED,
            title="DebugRun Revision 已变化",
            detail="请读取最新 DebugRun 后重新提交控制命令。",
            status_code=412,
            headers={"ETag": format_revision_etag(current_revision)},
        )

    @staticmethod
    def _json_object(model: WireModel) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], model.model_dump(mode="json", by_alias=True))

    @staticmethod
    def _not_found(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.NOT_FOUND,
            title="资源不存在",
            detail=detail,
            status_code=404,
        )

    @staticmethod
    def _forbidden(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.FORBIDDEN,
            title="操作被拒绝",
            detail=detail,
            status_code=403,
        )

    @staticmethod
    def _conflict(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title="DebugRun 状态冲突",
            detail=detail,
            status_code=409,
        )

    @staticmethod
    def _invalid_request(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.INVALID_REQUEST,
            title="DebugRun 请求无效",
            detail=detail,
            status_code=400,
        )

    @staticmethod
    def _runtime_unavailable(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.DEBUG_RUNTIME_UNAVAILABLE,
            title="Debug Runtime 不可用",
            detail=detail,
            status_code=503,
        )
