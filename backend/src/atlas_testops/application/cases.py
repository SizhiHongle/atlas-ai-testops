"""Application service for TestCase and WorkflowDraft authoring."""

from datetime import datetime, timedelta
from typing import Protocol, cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.core.contracts import WireModel, new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.core.pagination import decode_cursor, next_time_cursor
from atlas_testops.domain.case import (
    CreateTestCase,
    LayoutPatch,
    PatchIssueCode,
    TestCase,
    TestCasePage,
    WorkflowDraftSnapshot,
    WorkflowPatch,
    WorkflowPatchPreview,
    canonical_digest,
    canonical_workflow_graph,
    preview_workflow_patch,
    semantic_digest,
)
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.platform import Project, ProjectStatus
from atlas_testops.domain.workflow import (
    DraftAuthor,
    GraphIssueCode,
    GraphValidationResult,
    NodeLayout,
    validate_workflow_graph,
)
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

CASE_IDEMPOTENCY_TTL = timedelta(hours=24)
NON_PERSISTABLE_GRAPH_ISSUES = frozenset(
    {
        GraphIssueCode.DUPLICATE_NODE_ID,
        GraphIssueCode.DUPLICATE_EDGE_ID,
        GraphIssueCode.DANGLING_EDGE,
    }
)


class CursorRecord(Protocol):
    """Minimum record projection needed for a keyset cursor."""

    @property
    def id(self) -> UUID: ...

    @property
    def created_at(self) -> datetime: ...


class CaseService:
    """Coordinate authoring authorization, CAS, audit, and durable events."""

    def __init__(
        self,
        database: Database,
        case_repository: CaseRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
        debug_run_repository: DebugRunRepository | None = None,
    ) -> None:
        self._database = database
        self._cases = case_repository or CaseRepository()
        self._platform = platform_repository or PlatformRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()
        self._debug_runs = debug_run_repository or DebugRunRepository()

    async def create_case(
        self,
        actor: ActorContext,
        project_id: UUID,
        command: CreateTestCase,
        *,
        idempotency_key: str,
    ) -> CommandResult[TestCase]:
        """Create a stable TestCase and its single current draft atomically."""

        now = utc_now()
        request_payload: dict[str, JsonValue] = {
            "projectId": str(project_id),
            **self._json_object(command),
        }
        request_hash = hash_request(request_payload)
        scope = f"projects.{project_id}.test-cases.create"
        async with self._database.transaction(actor.database_context()) as connection:
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=CASE_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=TestCase.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )

            project = await self._require_project(connection, actor, project_id)
            self._require_author(actor, project.id)
            if project.status is ProjectStatus.ARCHIVED:
                raise self._conflict(
                    "Project 已归档",
                    "归档 Project 不能创建 TestCase。",
                )

            graph = canonical_workflow_graph(command.graph)
            validation = validate_workflow_graph(graph)
            self._require_persistable_graph(validation)
            case_id = new_entity_id()
            intent_version_ref = f"test-intent/{case_id}@{command.intent_version}"
            intent_digest = canonical_digest(command.intent)
            case = await self._cases.create_case(
                connection,
                case_id=case_id,
                tenant_id=actor.tenant_id,
                project_id=project_id,
                intent_version_ref=intent_version_ref,
                intent_digest=intent_digest,
                command=command,
            )
            if case is None:
                raise self._conflict(
                    "TestCase Key 已存在",
                    "同一 Project 内的 TestCase Key 必须唯一。",
                )
            draft = await self._cases.create_draft(
                connection,
                draft_id=new_entity_id(),
                case=case,
                graph=graph,
                layout=command.layout,
                updated_by=DraftAuthor.HUMAN,
                semantic_digest=semantic_digest(graph, intent_version_ref),
                validation=validation,
            )
            await self._record_event(
                connection,
                actor=actor,
                case=case,
                aggregate_id=case.id,
                aggregate_type="test_case",
                event_type="test_case.created",
                occurred_at=now,
                payload={
                    "projectId": str(case.project_id),
                    "caseKey": case.case_key,
                    "intentDigest": case.intent_digest,
                    "draftId": str(draft.id),
                    "semanticRevision": draft.semantic_revision,
                    "semanticDigest": draft.semantic_digest,
                    "graphValid": draft.validation.valid,
                },
            )
            response = CachedHttpResponse(status_code=201, body=self._json_object(case))
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                response=response,
            )
            return CommandResult(value=case, status_code=201, replayed=False)

    async def list_cases(
        self,
        actor: ActorContext,
        project_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> TestCasePage:
        """List the visible TestCase catalog using keyset pagination."""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_project(connection, actor, project_id)
            records = await self._cases.list_cases(
                connection,
                project_id=project_id,
                cursor=decoded,
                limit=limit,
            )
        return TestCasePage(
            items=records[:limit],
            next_cursor=self._next_cursor(records, limit),
        )

    async def get_case(self, actor: ActorContext, case_id: UUID) -> TestCase:
        """Read one TestCase without leaking cross-tenant identities."""

        async with self._database.transaction(actor.database_context()) as connection:
            case = await self._require_case(connection, actor, case_id)
            return case

    async def get_draft(
        self,
        actor: ActorContext,
        case_id: UUID,
    ) -> WorkflowDraftSnapshot:
        """Read the authoritative graph, layout, and separate revisions."""

        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_case(connection, actor, case_id)
            draft = await self._cases.get_draft_by_case(connection, case_id)
            if draft is None:
                raise self._not_found("WorkflowDraft 不存在")
            return draft

    async def preview_patch(
        self,
        actor: ActorContext,
        case_id: UUID,
        patch: WorkflowPatch,
    ) -> WorkflowPatchPreview:
        """Validate one semantic patch without writing authoring state."""

        async with self._database.transaction(actor.database_context()) as connection:
            case = await self._require_case(connection, actor, case_id)
            self._require_author(actor, case.project_id)
            draft = await self._cases.get_draft_by_case(connection, case_id)
            if draft is None:
                raise self._not_found("WorkflowDraft 不存在")
            self._check_revision(
                draft.semantic_revision,
                patch.base_semantic_revision,
            )
            return preview_workflow_patch(
                draft.graph,
                patch,
                intent_version_ref=draft.intent_version_ref,
            )

    async def apply_patch(
        self,
        actor: ActorContext,
        case_id: UUID,
        patch: WorkflowPatch,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[WorkflowDraftSnapshot]:
        """Apply one atomic graph patch under a semantic revision CAS."""

        self._require_matching_mutation_key(idempotency_key, patch.client_mutation_id)
        now = utc_now()
        request_payload: dict[str, JsonValue] = {
            "testCaseId": str(case_id),
            **self._json_object(patch),
        }
        request_hash = hash_request(request_payload)
        scope = f"test-cases.{case_id}.workflow-draft.semantic"
        async with self._database.transaction(actor.database_context()) as connection:
            case = await self._require_case(connection, actor, case_id)
            self._require_author(actor, case.project_id)
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=CASE_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=WorkflowDraftSnapshot.model_validate(
                        reservation.cached_response.body
                    ),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            draft = await self._cases.get_draft_by_case(
                connection,
                case_id,
                for_update=True,
            )
            if draft is None:
                raise self._not_found("WorkflowDraft 不存在")
            self._check_revision(draft.semantic_revision, expected_revision)
            self._check_revision(
                draft.semantic_revision,
                patch.base_semantic_revision,
            )
            preview = preview_workflow_patch(
                draft.graph,
                patch,
                intent_version_ref=draft.intent_version_ref,
            )
            if not preview.applicable:
                raise self._patch_not_applicable(preview)
            updated = await self._cases.replace_graph(
                connection,
                draft=draft,
                expected_revision=expected_revision,
                graph=preview.graph,
                updated_by=patch.source,
                semantic_digest=preview.semantic_digest,
                validation=preview.validation,
            )
            if updated is None:
                raise self._revision_conflict(draft.semantic_revision)
            response_body = self._json_object(updated)
            await self._cases.append_operation(
                connection,
                draft=draft,
                operation_scope="SEMANTIC",
                patch_id=patch.patch_id,
                client_mutation_id=patch.client_mutation_id,
                source=patch.source,
                actor_id=actor.actor_id,
                base_revision=draft.semantic_revision,
                result_revision=updated.semantic_revision,
                request_digest=canonical_digest(patch),
                before_digest=draft.semantic_digest,
                after_digest=updated.semantic_digest,
                operations=[
                    operation.model_dump(mode="json", by_alias=True)
                    for operation in patch.operations
                ],
                response=response_body,
                rationale_summary=patch.rationale_summary,
            )
            outdated_runs = await self._debug_runs.mark_case_runs_outdated(
                connection,
                test_case_id=case.id,
                current_semantic_revision=updated.semantic_revision,
                current_semantic_digest=updated.semantic_digest,
                outdated_at=now,
            )
            for run in outdated_runs:
                outdated_payload: dict[str, JsonValue] = {
                    "currentSemanticRevision": updated.semantic_revision,
                    "currentSemanticDigest": updated.semantic_digest,
                }
                await self._debug_runs.append_event(
                    connection,
                    run=run,
                    event_type="debug_run.snapshot_outdated",
                    payload=outdated_payload,
                    occurred_at=now,
                )
                await self._record_event(
                    connection,
                    actor=actor,
                    case=case,
                    aggregate_id=run.id,
                    aggregate_type="debug_run",
                    event_type="debug_run.snapshot_outdated",
                    occurred_at=now,
                    payload=outdated_payload,
                )
            await self._record_event(
                connection,
                actor=actor,
                case=case,
                aggregate_id=draft.id,
                aggregate_type="workflow_draft",
                event_type="workflow_draft.semantic_updated",
                occurred_at=now,
                payload={
                    "testCaseId": str(case.id),
                    "semanticRevision": updated.semantic_revision,
                    "semanticDigest": updated.semantic_digest,
                    "graphValid": updated.validation.valid,
                    "source": patch.source.value,
                },
            )
            response = CachedHttpResponse(status_code=200, body=response_body)
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                response=response,
            )
            return CommandResult(value=updated, status_code=200, replayed=False)

    async def update_layout(
        self,
        actor: ActorContext,
        case_id: UUID,
        patch: LayoutPatch,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[WorkflowDraftSnapshot]:
        """Merge node positions without invalidating semantic debug evidence."""

        self._require_matching_mutation_key(idempotency_key, patch.client_mutation_id)
        now = utc_now()
        request_payload: dict[str, JsonValue] = {
            "testCaseId": str(case_id),
            **self._json_object(patch),
        }
        request_hash = hash_request(request_payload)
        scope = f"test-cases.{case_id}.workflow-draft.layout"
        async with self._database.transaction(actor.database_context()) as connection:
            case = await self._require_case(connection, actor, case_id)
            self._require_author(actor, case.project_id)
            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=CASE_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=WorkflowDraftSnapshot.model_validate(
                        reservation.cached_response.body
                    ),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )
            draft = await self._cases.get_draft_by_case(
                connection,
                case_id,
                for_update=True,
            )
            if draft is None:
                raise self._not_found("WorkflowDraft 不存在")
            self._check_revision(draft.layout_revision, expected_revision)
            self._check_revision(draft.layout_revision, patch.base_layout_revision)
            unknown = set(patch.positions).difference(node.id for node in draft.graph.nodes)
            if unknown:
                raise self._layout_not_applicable(unknown)
            layout = dict(draft.layout)
            layout.update(patch.positions)
            before_digest = self._layout_digest(draft.layout)
            after_digest = self._layout_digest(layout)
            updated = await self._cases.update_layout(
                connection,
                draft=draft,
                expected_revision=expected_revision,
                layout=layout,
                updated_by=patch.source,
            )
            if updated is None:
                raise self._revision_conflict(draft.layout_revision)
            response_body = self._json_object(updated)
            await self._cases.append_operation(
                connection,
                draft=draft,
                operation_scope="LAYOUT",
                patch_id=None,
                client_mutation_id=patch.client_mutation_id,
                source=patch.source,
                actor_id=actor.actor_id,
                base_revision=draft.layout_revision,
                result_revision=updated.layout_revision,
                request_digest=canonical_digest(patch),
                before_digest=before_digest,
                after_digest=after_digest,
                operations=self._json_object(patch)["positions"],
                response=response_body,
                rationale_summary=None,
            )
            await self._record_event(
                connection,
                actor=actor,
                case=case,
                aggregate_id=draft.id,
                aggregate_type="workflow_draft",
                event_type="workflow_draft.layout_updated",
                occurred_at=now,
                payload={
                    "testCaseId": str(case.id),
                    "layoutRevision": updated.layout_revision,
                    "layoutDigest": after_digest,
                    "source": patch.source.value,
                },
            )
            response = CachedHttpResponse(status_code=200, body=response_body)
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                response=response,
            )
            return CommandResult(value=updated, status_code=200, replayed=False)

    async def _require_project(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        project_id: UUID,
    ) -> Project:
        project = await self._platform.get_project(connection, project_id)
        if project is None or not actor.can_read_project(project_id):
            raise self._not_found("Project 不存在")
        return project

    async def _require_case(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        case_id: UUID,
    ) -> TestCase:
        case = await self._cases.get_case(connection, case_id)
        if case is None or not actor.can_read_project(case.project_id):
            raise self._not_found("TestCase 不存在")
        return case

    async def _record_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        case: TestCase,
        aggregate_id: UUID,
        aggregate_type: str,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, JsonValue],
    ) -> None:
        await self._audit.append(
            connection,
            tenant_id=case.tenant_id,
            project_id=case.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type=aggregate_type,
            entity_id=aggregate_id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=case.tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    @staticmethod
    def _require_author(actor: ActorContext, project_id: UUID) -> None:
        if not actor.can_author_cases(project_id):
            raise CaseService._forbidden("当前角色不能编排该 Project 的 TestCase。")

    @staticmethod
    def _require_persistable_graph(validation: GraphValidationResult) -> None:
        blocking = [
            issue.code
            for issue in validation.issues
            if issue.code in NON_PERSISTABLE_GRAPH_ISSUES
        ]
        if blocking:
            codes = ", ".join(sorted({item.value for item in blocking}))
            raise ApplicationError(
                error_code=ErrorCode.VALIDATION_FAILED,
                title="WorkflowGraph 不能持久化",
                detail=f"图包含结构性错误: {codes}。",
                status_code=422,
            )

    @staticmethod
    def _require_matching_mutation_key(header: str, command_key: str) -> None:
        if header != command_key:
            raise ApplicationError(
                error_code=ErrorCode.INVALID_REQUEST,
                title="幂等标识不一致",
                detail="Idempotency-Key 必须与 clientMutationId 完全一致。",
                status_code=400,
            )

    @staticmethod
    def _layout_digest(layout: dict[str, NodeLayout]) -> str:
        payload: dict[str, JsonValue] = {
            key: cast(
                dict[str, JsonValue],
                value.model_dump(mode="json", by_alias=True),
            )
            for key, value in sorted(layout.items())
        }
        return canonical_digest({"layout": payload})

    @staticmethod
    def _patch_not_applicable(preview: WorkflowPatchPreview) -> ApplicationError:
        codes = ", ".join(sorted({issue.code.value for issue in preview.issues}))
        return ApplicationError(
            error_code=ErrorCode.VALIDATION_FAILED,
            title="WorkflowPatch 无法应用",
            detail=f"Patch 包含结构性冲突: {codes}。",
            status_code=422,
        )

    @staticmethod
    def _layout_not_applicable(unknown: set[str]) -> ApplicationError:
        issue = PatchIssueCode.LAYOUT_NODE_NOT_FOUND.value
        return ApplicationError(
            error_code=ErrorCode.VALIDATION_FAILED,
            title="LayoutPatch 无法应用",
            detail=f"{issue}: {', '.join(sorted(unknown))}。",
            status_code=422,
        )

    @staticmethod
    def _check_revision(current_revision: int, expected_revision: int) -> None:
        if current_revision != expected_revision:
            raise CaseService._revision_conflict(current_revision)

    @staticmethod
    def _next_cursor[T: CursorRecord](records: tuple[T, ...], limit: int) -> str | None:
        if len(records) <= limit or not records:
            return None
        last = records[limit - 1]
        return next_time_cursor(last.created_at, last.id)

    @staticmethod
    def _json_object(model: WireModel) -> dict[str, JsonValue]:
        return cast(
            dict[str, JsonValue],
            model.model_dump(mode="json", by_alias=True, exclude_none=True),
        )

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
            title="权限不足",
            detail=detail,
            status_code=403,
        )

    @staticmethod
    def _conflict(title: str, detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title=title,
            detail=detail,
            status_code=409,
        )

    @staticmethod
    def _revision_conflict(current_revision: int) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.DRAFT_REVISION_CONFLICT,
            title="WorkflowDraft Revision 已变化",
            detail="请读取最新 WorkflowDraft 后重新提交变更。",
            status_code=412,
            headers={"ETag": format_revision_etag(current_revision)},
        )
