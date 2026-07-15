"""Application service for reviewed CaseVersion publication."""

from datetime import datetime, timedelta
from typing import cast
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
    CaseCompilationResult,
    CaseVersion,
    CaseVersionPage,
    DebugRun,
    DebugRunLifecycle,
    DebugRunOutcome,
    DebugRunSnapshotStatus,
    PublishCaseVersion,
    TestCase,
    TestCaseStatus,
    WorkflowDraftSnapshot,
    canonical_digest,
    case_version_content_digest,
    compile_case,
    semantic_digest,
)
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.fixture import (
    AssetDefinitionStatus,
    AssetVersionStatus,
    DataBlueprintDefinition,
    DataBlueprintVersion,
    ValidationState,
)
from atlas_testops.domain.fixture import (
    canonical_digest as fixture_digest,
)
from atlas_testops.domain.identity import TestRoleStatus
from atlas_testops.domain.platform import ProjectStatus
from atlas_testops.domain.runtime import (
    EvidenceCompleteness,
    EvidenceIntegrity,
    EvidenceManifest,
    OracleOutcome,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.case_versions import CaseVersionRepository
from atlas_testops.infrastructure.repositories.cases import CaseRepository
from atlas_testops.infrastructure.repositories.debug_runs import DebugRunRepository
from atlas_testops.infrastructure.repositories.fixture_assets import FixtureAssetRepository
from atlas_testops.infrastructure.repositories.identity import IdentityRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.runtime import RuntimeRepository

CASE_VERSION_IDEMPOTENCY_TTL = timedelta(hours=24)


class CaseVersionService:
    """Enforce publication gates and persist one immutable exact snapshot."""

    def __init__(
        self,
        database: Database,
        *,
        case_repository: CaseRepository | None = None,
        version_repository: CaseVersionRepository | None = None,
        debug_run_repository: DebugRunRepository | None = None,
        identity_repository: IdentityRepository | None = None,
        fixture_repository: FixtureAssetRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        runtime_repository: RuntimeRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
    ) -> None:
        self._database = database
        self._cases = case_repository or CaseRepository()
        self._versions = version_repository or CaseVersionRepository()
        self._runs = debug_run_repository or DebugRunRepository()
        self._identity = identity_repository or IdentityRepository()
        self._fixtures = fixture_repository or FixtureAssetRepository()
        self._platform = platform_repository or PlatformRepository()
        self._runtime = runtime_repository or RuntimeRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()

    async def publish(
        self,
        actor: ActorContext,
        case_id: UUID,
        command: PublishCaseVersion,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CommandResult[CaseVersion]:
        """Publish only the current compiled Draft with exact trusted run evidence."""

        self._require_matching_mutation_key(idempotency_key, command.client_mutation_id)
        now = utc_now()
        request_payload: dict[str, JsonValue] = {
            "testCaseId": str(case_id),
            **self._json_object(command),
        }
        request_hash = hash_request(request_payload)
        scope = f"test-cases.{case_id}.versions.publish"
        async with self._database.transaction(actor.database_context()) as connection:
            case = await self._require_case(connection, actor, case_id, for_share=True)
            publisher = self._require_reviewer(actor, case.project_id)
            if case.status is TestCaseStatus.ARCHIVED:
                raise self._conflict("已归档 TestCase 不能发布新版本。")
            project = await self._platform.get_project_for_share(connection, case.project_id)
            if project is None or project.status is not ProjectStatus.ACTIVE:
                raise self._conflict("只有活动 Project 可以发布 CaseVersion。")

            reservation = await self._idempotency.reserve(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                now=now,
                ttl=CASE_VERSION_IDEMPOTENCY_TTL,
            )
            if reservation.cached_response is not None:
                return CommandResult(
                    value=CaseVersion.model_validate(reservation.cached_response.body),
                    status_code=reservation.cached_response.status_code,
                    replayed=True,
                )

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

            authored_by = await self._cases.get_semantic_author(
                connection,
                draft_id=draft.id,
                test_case_id=case.id,
            )
            if authored_by is None:
                raise self._forbidden(
                    "当前 Draft 缺少可审计的语义 Author，不能进入 Reviewer 发布流程。"
                )
            if authored_by == publisher:
                raise self._forbidden("CaseVersion 必须由不同于当前 Author 的 Reviewer 发布。")

            await self._require_exact_bindings(connection, case)
            run = await self._runs.get_run(
                connection,
                command.debug_run_id,
            )
            evidence_manifest = (
                await self._runtime.get_evidence_manifest(
                    connection,
                    run.evidence_manifest_id,
                )
                if run is not None and run.evidence_manifest_id is not None
                else None
            )
            self._require_debug_evidence(
                run,
                evidence_manifest=evidence_manifest,
                case=case,
                draft=draft,
                compiled_digest=compilation.compiled_digest,
            )
            assert run is not None
            assert run.evidence_manifest_id is not None
            assert run.evidence_manifest_digest is not None
            content_digest = case_version_content_digest(
                test_case_id=case.id,
                version=command.version,
                source_draft_id=draft.id,
                semantic_revision=draft.semantic_revision,
                semantic_digest_value=draft.semantic_digest,
                intent_version_ref=case.intent_version_ref,
                intent_digest=case.intent_digest,
                intent=case.intent,
                graph=draft.graph,
                test_ir=compilation.test_ir,
                plan_template=compilation.plan_template,
                compiled_digest=compilation.compiled_digest,
                debug_run_id=run.id,
                evidence_manifest_id=run.evidence_manifest_id,
                evidence_manifest_digest=run.evidence_manifest_digest,
            )
            version = await self._versions.create_version(
                connection,
                version_id=new_entity_id(),
                case=case,
                draft=draft,
                run=run,
                command=command,
                test_ir=compilation.test_ir,
                plan_template=compilation.plan_template,
                compiled_digest=compilation.compiled_digest,
                content_digest=content_digest,
                authored_by=authored_by,
                published_by=publisher,
                published_at=now,
            )
            if version is None:
                raise self._conflict(
                    "该版本号已存在，或所选 DebugRun 已用于发布另一个 CaseVersion。"
                )
            event_payload: dict[str, JsonValue] = {
                "testCaseId": str(case.id),
                "caseVersionId": str(version.id),
                "version": version.version,
                "semanticRevision": version.semantic_revision,
                "semanticDigest": version.semantic_digest,
                "compiledDigest": version.compiled_digest,
                "contentDigest": version.content_digest,
                "debugRunId": str(version.debug_run_id),
                "authoredBy": str(version.authored_by),
                "publishedBy": str(version.published_by),
            }
            await self._record_event(
                connection,
                actor=actor,
                version=version,
                event_type="case.version.published",
                payload=event_payload,
                occurred_at=now,
            )
            response = CachedHttpResponse(
                status_code=201,
                body=self._json_object(version),
            )
            await self._idempotency.complete(
                connection,
                tenant_id=actor.tenant_id,
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                response=response,
            )
            return CommandResult(value=version, status_code=201, replayed=False)

    async def get(self, actor: ActorContext, version_id: UUID) -> CaseVersion:
        """Read one exact version without resolving a current or latest alias."""

        async with self._database.transaction(actor.database_context()) as connection:
            version = await self._versions.get_version(connection, version_id)
            if version is None or not actor.can_read_project(version.project_id):
                raise self._not_found("CaseVersion 不存在或不可见。")
            return version

    async def list_for_case(
        self,
        actor: ActorContext,
        case_id: UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> CaseVersionPage:
        """List immutable version history using a publishedAt keyset cursor."""

        decoded = decode_cursor(cursor)
        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_case(connection, actor, case_id)
            records = await self._versions.list_versions(
                connection,
                test_case_id=case_id,
                cursor=decoded,
                limit=limit,
            )
        items = records[:limit]
        next_cursor = (
            next_time_cursor(items[-1].published_at, items[-1].id)
            if len(records) > limit and items
            else None
        )
        return CaseVersionPage(items=items, next_cursor=next_cursor)

    async def _require_case(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        case_id: UUID,
        *,
        for_share: bool = False,
    ) -> TestCase:
        case = await self._cases.get_case(connection, case_id, for_share=for_share)
        if case is None or not actor.can_read_project(case.project_id):
            raise self._not_found("TestCase 不存在或不可见。")
        return case

    async def _require_exact_bindings(
        self,
        connection: AsyncConnection[DictRow],
        case: TestCase,
    ) -> None:
        """Resolve mutable catalogs once and require exact active published bindings."""

        for actor_contract in sorted(case.intent.actors, key=lambda item: str(item.role_id)):
            role = await self._identity.get_role(
                connection,
                actor_contract.role_id,
                for_share=True,
            )
            if (
                role is None
                or role.project_id != case.project_id
                or role.status is not TestRoleStatus.ACTIVE
                or role.role_key != actor_contract.role_key
                or role.revision != actor_contract.role_revision
                or set(role.capabilities) != set(actor_contract.capabilities)
            ):
                raise self._binding_stale(
                    "Test Intent 的角色绑定不存在、已停用或不再匹配精确 Revision。"
                )

        fixture_contract = case.intent.fixture
        if fixture_contract is None:
            raise self._binding_stale("Test Intent 缺少 Fixture 精确版本绑定。")
        fixture_version = await self._fixtures.get_blueprint_version(
            connection,
            fixture_contract.blueprint_version_id,
            for_share=True,
        )
        fixture_definition = (
            await self._fixtures.get_blueprint_definition(
                connection,
                fixture_version.blueprint_id,
                for_share=True,
            )
            if fixture_version is not None
            else None
        )
        self._require_published_fixture(
            case,
            fixture_version=fixture_version,
            fixture_definition=fixture_definition,
        )

    @staticmethod
    def _require_published_fixture(
        case: TestCase,
        *,
        fixture_version: DataBlueprintVersion | None,
        fixture_definition: DataBlueprintDefinition | None,
    ) -> None:
        fixture_contract = case.intent.fixture
        assert fixture_contract is not None
        if fixture_version is None or fixture_definition is None:
            raise CaseVersionService._binding_stale(
                "Fixture BlueprintVersion 不存在或不属于当前 Tenant。"
            )
        expected_ref = f"{fixture_definition.blueprint_key}@{fixture_version.version}"
        evidence_states = (
            fixture_version.static_validation_state,
            fixture_version.runtime_validation_state,
            fixture_version.cleanup_validation_state,
        )
        export_names = {item.name for item in fixture_version.contract.exports}
        if (
            fixture_version.project_id != case.project_id
            or fixture_definition.project_id != case.project_id
            or fixture_definition.status is not AssetDefinitionStatus.ACTIVE
            or fixture_version.status is not AssetVersionStatus.PUBLISHED
            or any(state is not ValidationState.PASSED for state in evidence_states)
            or fixture_version.compiled_plan is None
            or fixture_version.plan_digest is None
            or fixture_digest(fixture_version.contract) != fixture_version.content_digest
            or fixture_contract.blueprint_version_ref != expected_ref
            or fixture_contract.content_digest != fixture_version.content_digest
            or not set(fixture_contract.required_exports).issubset(export_names)
        ):
            raise CaseVersionService._binding_stale(
                "Fixture 必须是精确、已发布且具备 Static/Runtime/Cleanup 证据的版本。"
            )

    async def _record_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        version: CaseVersion,
        event_type: str,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> None:
        await self._audit.append(
            connection,
            tenant_id=version.tenant_id,
            project_id=version.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type=event_type,
            entity_type="case_version",
            entity_id=version.id,
            occurred_at=occurred_at,
            payload=payload,
            request_id=actor.request_id,
        )
        await self._outbox.append(
            connection,
            DomainEvent(
                tenant_id=version.tenant_id,
                aggregate_type="case_version",
                aggregate_id=version.id,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
            ),
        )

    @staticmethod
    def _require_draft_integrity(
        case: TestCase,
        draft: WorkflowDraftSnapshot,
    ) -> None:
        expected_digest = semantic_digest(draft.graph, draft.intent_version_ref)
        if (
            draft.intent_version_ref != case.intent_version_ref
            or draft.semantic_digest != expected_digest
            or canonical_digest(case.intent) != case.intent_digest
        ):
            raise ApplicationError(
                error_code=ErrorCode.VALIDATION_FAILED,
                title="WorkflowDraft 完整性校验失败",
                detail="Draft、Test Intent 或其权威 Digest 不一致。",
                status_code=422,
            )

    @staticmethod
    def _require_compiled(compilation: CaseCompilationResult) -> None:
        if compilation.valid:
            return
        codes = ", ".join(sorted({issue.code.value for issue in compilation.issues}))
        raise ApplicationError(
            error_code=ErrorCode.VALIDATION_FAILED,
            title="WorkflowDraft 无法发布",
            detail=f"发布编译门禁未通过: {codes}。",
            status_code=422,
        )

    @staticmethod
    def _require_debug_evidence(
        run: DebugRun | None,
        *,
        evidence_manifest: EvidenceManifest | None,
        case: TestCase,
        draft: WorkflowDraftSnapshot,
        compiled_digest: str,
    ) -> None:
        if run is None or run.test_case_id != case.id or run.draft_id != draft.id:
            raise ApplicationError(
                error_code=ErrorCode.TRIAL_RUN_REQUIRED,
                title="需要可信 DebugRun",
                detail="请选择当前 WorkflowDraft 的成功 DebugRun。",
                status_code=409,
            )
        if (
            run.snapshot_status is DebugRunSnapshotStatus.OUTDATED
            or run.semantic_revision != draft.semantic_revision
            or run.semantic_digest != draft.semantic_digest
            or run.compiled_digest != compiled_digest
        ):
            raise ApplicationError(
                error_code=ErrorCode.DEBUG_RUN_OUTDATED,
                title="DebugRun 已过期",
                detail="DebugRun 的 Revision 或 Digest 与待发布快照不一致。",
                status_code=409,
            )
        if (
            run.lifecycle is not DebugRunLifecycle.TERMINATED
            or run.outcome is not DebugRunOutcome.PASSED
            or run.evidence_manifest_id is None
            or run.evidence_manifest_digest is None
            or run.execution_contract_id is None
            or run.execution_contract_digest is None
        ):
            raise ApplicationError(
                error_code=ErrorCode.TRIAL_RUN_REQUIRED,
                title="需要成功 DebugRun",
                detail="发布要求 TERMINATED + PASSED 且 Evidence Manifest 已封存。",
                status_code=409,
            )
        if (
            evidence_manifest is None
            or evidence_manifest.id != run.evidence_manifest_id
            or evidence_manifest.content_digest != run.evidence_manifest_digest
            or evidence_manifest.tenant_id != run.tenant_id
            or evidence_manifest.project_id != run.project_id
            or evidence_manifest.environment_id != run.environment_id
            or evidence_manifest.debug_run_id != run.id
            or evidence_manifest.execution_contract_id
            != run.execution_contract_id
            or evidence_manifest.execution_contract_digest
            != run.execution_contract_digest
            or evidence_manifest.test_ir_digest != run.test_ir_digest
            or evidence_manifest.plan_digest != run.plan_digest
            or evidence_manifest.outcome is not OracleOutcome.PASSED
            or evidence_manifest.completeness is not EvidenceCompleteness.COMPLETE
            or evidence_manifest.integrity is not EvidenceIntegrity.VERIFIED
        ):
            raise ApplicationError(
                error_code=ErrorCode.TRIAL_RUN_REQUIRED,
                title="DebugRun 证据不可验证",
                detail="发布要求 Evidence Manifest 与 Runtime、Oracle 和全部 Digest 完全一致。",
                status_code=409,
            )

    @staticmethod
    def _require_reviewer(actor: ActorContext, project_id: UUID) -> UUID:
        if not actor.can_review_cases(project_id):
            raise CaseVersionService._forbidden(
                "当前角色没有该 Project 的 Case Reviewer 权限。"
            )
        if actor.actor_id is None:
            raise CaseVersionService._forbidden("发布动作需要可审计的 Reviewer Actor。")
        return actor.actor_id

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
    def _check_revision(current_revision: int, expected_revision: int) -> None:
        if current_revision != expected_revision:
            raise ApplicationError(
                error_code=ErrorCode.DRAFT_REVISION_CONFLICT,
                title="WorkflowDraft Revision 已变化",
                detail="请重新读取、调试并评审当前 WorkflowDraft。",
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
            title="发布被拒绝",
            detail=detail,
            status_code=403,
        )

    @staticmethod
    def _conflict(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.CONFLICT,
            title="CaseVersion 发布冲突",
            detail=detail,
            status_code=409,
        )

    @staticmethod
    def _binding_stale(detail: str) -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.PUBLICATION_EVIDENCE_REQUIRED,
            title="发布绑定或证据无效",
            detail=detail,
            status_code=409,
        )
