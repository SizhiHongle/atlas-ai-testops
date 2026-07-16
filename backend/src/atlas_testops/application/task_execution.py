"""Application gates and trusted state changes for formal Task execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import SerializationFailure
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.core.concurrency import format_revision_etag
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import CaseVersion, CaseVersionStatus
from atlas_testops.domain.fixture import (
    AssetVersionStatus,
    DataBlueprintVersion,
    validate_run_inputs,
)
from atlas_testops.domain.identity import TestRole, TestRoleStatus
from atlas_testops.domain.platform import (
    Environment,
    EnvironmentKind,
    EnvironmentStatus,
)
from atlas_testops.domain.task.models import (
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskRun,
    UnitAttempt,
)
from atlas_testops.domain.task.profiles import (
    BrowserProfileVersion,
    DataProfileVersion,
    ExecutionProfileVersion,
    IdentityProfileVersion,
    TaskProfileStatus,
)
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.repositories.case_versions import CaseVersionRepository
from atlas_testops.infrastructure.repositories.fixture_assets import FixtureAssetRepository
from atlas_testops.infrastructure.repositories.identity import IdentityRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
    TaskProfileRepository,
    TaskWorkflowStartIntent,
)
from atlas_testops.infrastructure.repositories.task_runs import TaskRunRepository


@dataclass(frozen=True, slots=True)
class TaskAdmissionSnapshot:
    """Exact, revalidated inputs that may be handed to a future dispatcher."""

    unit: ExecutionUnit
    case_version: CaseVersion
    execution_profile: ExecutionProfileVersion
    identity_profile: IdentityProfileVersion
    browser_profile: BrowserProfileVersion
    data_profile: DataProfileVersion
    fixture_blueprint_version: DataBlueprintVersion
    environment: Environment
    roles: tuple[TestRole, ...]


@dataclass(frozen=True, slots=True)
class TaskStateTransition:
    """Complete three-axis state supplied to one trusted PostgreSQL CAS function."""

    lifecycle: ExecutionLifecycle
    quality: ExecutionQuality
    hygiene: ExecutionHygiene
    started_at: datetime | None = None
    finalized_at: datetime | None = None
    cleanup_resolved_at: datetime | None = None
    closed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaskStateChangeResult[StateT]:
    """Expose whether an exact state command was applied or replayed."""

    value: StateT
    replayed: bool


class _ExecutionStateProjection(Protocol):
    lifecycle: ExecutionLifecycle
    quality: ExecutionQuality
    hygiene: ExecutionHygiene
    started_at: datetime | None
    finalized_at: datetime | None
    cleanup_resolved_at: datetime | None
    closed_at: datetime | None
    revision: int
    updated_at: datetime


class TaskAdmissionService:
    """Fail closed on mutable dependency drift before any dispatcher I/O."""

    def __init__(
        self,
        database: Database,
        *,
        task_repository: TaskRunRepository | None = None,
        profile_repository: TaskProfileRepository | None = None,
        case_repository: CaseVersionRepository | None = None,
        fixture_repository: FixtureAssetRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        identity_repository: IdentityRepository | None = None,
    ) -> None:
        self._database = database
        self._tasks = task_repository or TaskRunRepository()
        self._profiles = profile_repository or TaskProfileRepository()
        self._cases = case_repository or CaseVersionRepository()
        self._fixtures = fixture_repository or FixtureAssetRepository()
        self._platform = platform_repository or PlatformRepository()
        self._identity = identity_repository or IdentityRepository()

    async def admit_unit(
        self,
        actor: ActorContext,
        execution_unit_id: UUID,
    ) -> TaskAdmissionSnapshot:
        """Re-resolve one Unit's exact inputs without calling Temporal or a worker."""

        async with self._database.transaction(actor.database_context()) as connection:
            unit = await self._tasks.get_unit(connection, execution_unit_id)
            if unit is None or unit.tenant_id != actor.tenant_id:
                raise _not_found("ExecutionUnit 不存在或不可见。")
            if not actor.can_operate_project(unit.project_id):
                raise _forbidden("当前身份不能调度该 Project 的 ExecutionUnit。")

            run = await self._tasks.get_run(connection, unit.task_run_id)
            if (
                run is None
                or run.id != unit.task_run_id
                or run.tenant_id != unit.tenant_id
                or run.project_id != unit.project_id
            ):
                raise _admission_failed(
                    "ExecutionUnit 的父 TaskRun 不存在或作用域不一致。"
                )
            _require_sealed_run(run)
            if run.lifecycle not in {
                ExecutionLifecycle.QUEUED,
                ExecutionLifecycle.RUNNING,
            }:
                raise _admission_failed(
                    "TaskRun 当前状态不允许派发新的 ExecutionUnit。"
                )
            if unit.lifecycle is not ExecutionLifecycle.QUEUED:
                raise _admission_failed(
                    "只有 QUEUED ExecutionUnit 可以进入调度准入。"
                )

            case_version = await self._cases.get_version(connection, unit.case_version_id)
            execution_profile = await self._profiles.get_execution_profile_version(
                connection,
                unit.execution_profile_version_id,
            )
            identity_profile = await self._profiles.get_identity_profile_version(
                connection,
                unit.identity_profile_version_id,
            )
            browser_profile = await self._profiles.get_browser_profile_version(
                connection,
                unit.browser_profile_version_id,
            )
            data_profile = await self._profiles.get_data_profile_version(
                connection,
                unit.data_profile_version_id,
            )
            fixture = await self._fixtures.get_blueprint_version(
                connection,
                unit.fixture_blueprint_version_id,
                for_share=True,
            )
            environment = await self._platform.get_environment_for_share(
                connection,
                unit.environment_id,
            )

            required = {
                "CaseVersion": case_version,
                "ExecutionProfileVersion": execution_profile,
                "IdentityProfileVersion": identity_profile,
                "BrowserProfileVersion": browser_profile,
                "DataProfileVersion": data_profile,
                "DataBlueprintVersion": fixture,
                "Environment": environment,
            }
            missing = sorted(name for name, value in required.items() if value is None)
            if missing:
                raise _admission_failed(
                    "ExecutionUnit 的精确依赖不存在或不在当前 Tenant: "
                    + "、".join(missing)
                    + "。"
                )

            assert case_version is not None
            assert execution_profile is not None
            assert identity_profile is not None
            assert browser_profile is not None
            assert data_profile is not None
            assert fixture is not None
            assert environment is not None

            self._require_scope(
                unit,
                case_version,
                execution_profile,
                identity_profile,
                browser_profile,
                data_profile,
                fixture,
                environment,
            )
            self._require_published_profiles(
                execution_profile,
                identity_profile,
                browser_profile,
                data_profile,
            )
            self._require_execution_profile(unit, case_version, execution_profile)
            self._require_identity_profile(unit, case_version, identity_profile)
            self._require_browser_profile(unit, browser_profile)
            self._require_data_profile(unit, case_version, data_profile, fixture)
            self._require_environment(environment)
            roles = await self._require_current_roles(
                connection,
                unit,
                identity_profile,
            )
            return TaskAdmissionSnapshot(
                unit=unit,
                case_version=case_version,
                execution_profile=execution_profile,
                identity_profile=identity_profile,
                browser_profile=browser_profile,
                data_profile=data_profile,
                fixture_blueprint_version=fixture,
                environment=environment,
                roles=roles,
            )

    @staticmethod
    def _require_scope(unit: ExecutionUnit, *dependencies: object) -> None:
        expected = (unit.tenant_id, unit.project_id)
        for dependency in dependencies:
            actual = (
                getattr(dependency, "tenant_id", None),
                getattr(dependency, "project_id", None),
            )
            if actual != expected:
                raise _admission_failed(
                    f"{type(dependency).__name__} 与 ExecutionUnit 的 Tenant/Project 不一致。"
                )

    @staticmethod
    def _require_published_profiles(
        execution: ExecutionProfileVersion,
        identity: IdentityProfileVersion,
        browser: BrowserProfileVersion,
        data: DataProfileVersion,
    ) -> None:
        blocked = sorted(
            type(profile).__name__
            for profile in (execution, identity, browser, data)
            if profile.status is not TaskProfileStatus.PUBLISHED
        )
        if blocked:
            raise _admission_failed(
                "新调度只接受 PUBLISHED Profile；以下 Profile 已废弃或撤销: "
                + "、".join(blocked)
                + "。"
            )

    @staticmethod
    def _require_execution_profile(
        unit: ExecutionUnit,
        case: CaseVersion,
        profile: ExecutionProfileVersion,
    ) -> None:
        if case.status is not CaseVersionStatus.PUBLISHED:
            raise _admission_failed("CaseVersion 已退出发布态，不能创建新调度。")
        if (
            profile.id != unit.execution_profile_version_id
            or profile.case_version_id != case.id
            or profile.case_content_digest != case.content_digest
            or profile.test_ir_digest != case.test_ir_digest
            or profile.plan_digest != case.plan_digest
            or profile.compiled_digest != case.compiled_digest
        ):
            raise _admission_failed("ExecutionProfileVersion 与 exact CaseVersion 不一致。")
        unsupported = sorted(
            set(case.test_ir.required_features) - set(profile.supported_features)
        )
        if unsupported:
            raise _admission_failed(
                "ExecutionProfileVersion 不支持 CaseVersion 所需能力: "
                + "、".join(unsupported)
                + "。"
            )

    @staticmethod
    def _require_identity_profile(
        unit: ExecutionUnit,
        case: CaseVersion,
        profile: IdentityProfileVersion,
    ) -> None:
        expected = tuple(
            sorted(
                (
                    actor.actor_slot,
                    actor.role_id,
                    actor.role_key,
                    actor.role_revision,
                    actor.capabilities,
                )
                for actor in case.test_ir.actors
            )
        )
        actual = tuple(
            sorted(
                (
                    actor.actor_slot,
                    actor.role_id,
                    actor.role_key,
                    actor.role_revision,
                    actor.capabilities,
                )
                for actor in profile.actors
            )
        )
        if (
            profile.id != unit.identity_profile_version_id
            or profile.case_version_id != case.id
            or profile.case_content_digest != case.content_digest
            or actual != expected
        ):
            raise _admission_failed(
                "IdentityProfileVersion 必须逐项匹配 CaseVersion 内置 Actor 角色，不能覆盖。"
            )

    @staticmethod
    def _require_browser_profile(
        unit: ExecutionUnit,
        profile: BrowserProfileVersion,
    ) -> None:
        if profile.id != unit.browser_profile_version_id:
            raise _admission_failed(
                "BrowserProfileVersion 与 ExecutionUnit 的 exact browser binding 不一致。"
            )

    @staticmethod
    def _require_data_profile(
        unit: ExecutionUnit,
        case: CaseVersion,
        profile: DataProfileVersion,
        fixture: DataBlueprintVersion,
    ) -> None:
        fixture_contract = case.test_ir.fixture
        if fixture.status is not AssetVersionStatus.PUBLISHED:
            raise _admission_failed("DataBlueprintVersion 已退出发布态，不能创建新调度。")
        if fixture.plan_digest is None:
            raise _admission_failed("DataBlueprintVersion 缺少已编译 planDigest。")
        if (
            profile.id != unit.data_profile_version_id
            or profile.blueprint_version_id != unit.fixture_blueprint_version_id
            or profile.blueprint_version_id != fixture.id
            or profile.blueprint_version_id != fixture_contract.blueprint_version_id
            or profile.blueprint_version_ref != fixture_contract.blueprint_version_ref
            or profile.blueprint_content_digest != fixture.content_digest
            or profile.blueprint_content_digest != fixture_contract.content_digest
            or profile.plan_digest != fixture.plan_digest
        ):
            raise _admission_failed(
                "DataProfileVersion 与 exact Fixture Blueprint 或 Case Fixture 绑定不一致。"
            )
        try:
            validate_run_inputs(fixture.contract.run_input_schema, profile.run_inputs)
        except ValueError as error:
            raise _admission_failed(str(error)) from error

    @staticmethod
    def _require_environment(environment: Environment) -> None:
        if (
            environment.status is not EnvironmentStatus.ACTIVE
            or environment.kind not in {EnvironmentKind.TEST, EnvironmentKind.STAGING}
        ):
            raise _admission_failed("正式任务只允许调度到 ACTIVE TEST/STAGING Environment。")

    async def _require_current_roles(
        self,
        connection: AsyncConnection[DictRow],
        unit: ExecutionUnit,
        profile: IdentityProfileVersion,
    ) -> tuple[TestRole, ...]:
        roles: list[TestRole] = []
        for binding in profile.actors:
            role = await self._identity.get_role(connection, binding.role_id, for_share=True)
            if (
                role is None
                or role.tenant_id != unit.tenant_id
                or role.project_id != unit.project_id
                or role.status is not TestRoleStatus.ACTIVE
                or role.role_key != binding.role_key
                or role.revision != binding.role_revision
                or role.capabilities != binding.capabilities
            ):
                raise _admission_failed(
                    f"Identity Actor {binding.actor_slot} 的 TestRole 已失效或发生漂移。"
                )
            roles.append(role)
        return tuple(roles)


class TaskExecutionStateService:
    """Authorize and invoke the database-owned seal and state transition protocol."""

    def __init__(
        self,
        database: Database,
        *,
        task_repository: TaskRunRepository | None = None,
        state_repository: TaskExecutionStateRepository | None = None,
    ) -> None:
        self._database = database
        self._tasks = task_repository or TaskRunRepository()
        self._state = state_repository or TaskExecutionStateRepository()

    async def seal_run(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        *,
        expected_revision: int,
    ) -> TaskStateChangeResult[TaskRun]:
        """Seal one complete aggregate; PostgreSQL creates its root start intent."""

        try:
            async with self._database.transaction(actor.database_context()) as connection:
                current = await self._require_run(connection, actor, task_run_id)
                if current.materialization_state is TaskMaterializationState.SEALED:
                    return TaskStateChangeResult(current, replayed=True)
                _require_revision(current.revision, expected_revision)
                updated = await self._state.seal_task_run_materialization(
                    connection,
                    task_run_id=task_run_id,
                    expected_revision=expected_revision,
                )
                if updated is None:
                    raise RuntimeError("trusted seal function returned no TaskRun")
                await self._append_state_event(
                    connection,
                    run=updated,
                    projection=updated,
                    event_type="task_run.materialization_sealed",
                    payload={
                        "requestDigest": updated.request_digest,
                        "materializedUnitCount": updated.materialized_unit_count,
                        "materializedFirstAttemptCount": (
                            updated.materialized_first_attempt_count
                        ),
                    },
                )
                return TaskStateChangeResult(updated, replayed=False)
        except SerializationFailure:
            async with self._database.transaction(actor.database_context()) as connection:
                current = await self._require_run(connection, actor, task_run_id)
                if current.materialization_state is TaskMaterializationState.SEALED:
                    return TaskStateChangeResult(current, replayed=True)
                raise _revision_conflict(current.revision) from None

    async def transition_run(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        *,
        expected_revision: int,
        state: TaskStateTransition,
    ) -> TaskStateChangeResult[TaskRun]:
        """Apply or replay one exact TaskRun three-axis state command."""

        try:
            async with self._database.transaction(actor.database_context()) as connection:
                current = await self._require_run(connection, actor, task_run_id)
                _require_sealed_run(current)
                if _state_matches(current, state):
                    return TaskStateChangeResult(current, replayed=True)
                _require_revision(current.revision, expected_revision)
                updated = await self._state.transition_task_run_state(
                    connection,
                    task_run_id=task_run_id,
                    expected_revision=expected_revision,
                    lifecycle=state.lifecycle,
                    quality=state.quality,
                    hygiene=state.hygiene,
                    started_at=state.started_at,
                    finalized_at=state.finalized_at,
                    cleanup_resolved_at=state.cleanup_resolved_at,
                    closed_at=state.closed_at,
                )
                if updated is None:
                    raise RuntimeError("trusted TaskRun transition returned no row")
                await self._append_state_event(
                    connection,
                    run=updated,
                    projection=updated,
                    event_type="task_run.state_changed",
                    payload={"revision": updated.revision},
                )
                return TaskStateChangeResult(updated, replayed=False)
        except SerializationFailure:
            return await self._recover_run_replay(actor, task_run_id, state)

    async def transition_unit(
        self,
        actor: ActorContext,
        *,
        task_run_id: UUID,
        execution_unit_id: UUID,
        expected_revision: int,
        state: TaskStateTransition,
    ) -> TaskStateChangeResult[ExecutionUnit]:
        """Apply or replay one exact ExecutionUnit state command."""

        try:
            async with self._database.transaction(actor.database_context()) as connection:
                run = await self._require_run(connection, actor, task_run_id)
                _require_sealed_run(run)
                current = await self._tasks.get_unit(connection, execution_unit_id)
                if current is None or current.task_run_id != task_run_id:
                    raise _not_found("ExecutionUnit 不存在或不属于指定 TaskRun。")
                if _state_matches(current, state):
                    return TaskStateChangeResult(current, replayed=True)
                _require_revision(current.revision, expected_revision)
                updated = await self._state.transition_execution_unit_state(
                    connection,
                    task_run_id=task_run_id,
                    execution_unit_id=execution_unit_id,
                    expected_revision=expected_revision,
                    lifecycle=state.lifecycle,
                    quality=state.quality,
                    hygiene=state.hygiene,
                    started_at=state.started_at,
                    finalized_at=state.finalized_at,
                    cleanup_resolved_at=state.cleanup_resolved_at,
                    closed_at=state.closed_at,
                )
                if updated is None:
                    raise RuntimeError("trusted ExecutionUnit transition returned no row")
                await self._append_state_event(
                    connection,
                    run=run,
                    projection=updated,
                    event_type="execution_unit.state_changed",
                    execution_unit_id=execution_unit_id,
                    payload={"revision": updated.revision},
                )
                return TaskStateChangeResult(updated, replayed=False)
        except SerializationFailure:
            return await self._recover_unit_replay(
                actor,
                task_run_id=task_run_id,
                execution_unit_id=execution_unit_id,
                state=state,
            )

    async def transition_attempt(
        self,
        actor: ActorContext,
        *,
        task_run_id: UUID,
        execution_unit_id: UUID,
        unit_attempt_id: UUID,
        expected_revision: int,
        state: TaskStateTransition,
    ) -> TaskStateChangeResult[UnitAttempt]:
        """Apply or replay one exact UnitAttempt state command."""

        try:
            async with self._database.transaction(actor.database_context()) as connection:
                run = await self._require_run(connection, actor, task_run_id)
                _require_sealed_run(run)
                unit = await self._tasks.get_unit(connection, execution_unit_id)
                current = await self._tasks.get_attempt(connection, unit_attempt_id)
                if unit is None or unit.task_run_id != task_run_id:
                    raise _not_found("ExecutionUnit 不存在或不属于指定 TaskRun。")
                if (
                    current is None
                    or current.task_run_id != task_run_id
                    or current.execution_unit_id != execution_unit_id
                ):
                    raise _not_found("UnitAttempt 不存在或不属于指定 ExecutionUnit。")
                if _state_matches(current, state):
                    return TaskStateChangeResult(current, replayed=True)
                _require_revision(current.revision, expected_revision)
                updated = await self._state.transition_unit_attempt_state(
                    connection,
                    task_run_id=task_run_id,
                    execution_unit_id=execution_unit_id,
                    unit_attempt_id=unit_attempt_id,
                    expected_revision=expected_revision,
                    lifecycle=state.lifecycle,
                    quality=state.quality,
                    hygiene=state.hygiene,
                    started_at=state.started_at,
                    finalized_at=state.finalized_at,
                    cleanup_resolved_at=state.cleanup_resolved_at,
                    closed_at=state.closed_at,
                )
                if updated is None:
                    raise RuntimeError("trusted UnitAttempt transition returned no row")
                await self._append_state_event(
                    connection,
                    run=run,
                    projection=updated,
                    event_type="unit_attempt.state_changed",
                    execution_unit_id=execution_unit_id,
                    unit_attempt_id=unit_attempt_id,
                    payload={"revision": updated.revision},
                )
                return TaskStateChangeResult(updated, replayed=False)
        except SerializationFailure:
            return await self._recover_attempt_replay(
                actor,
                task_run_id=task_run_id,
                execution_unit_id=execution_unit_id,
                unit_attempt_id=unit_attempt_id,
                state=state,
            )

    async def get_run_start_intent(
        self,
        actor: ActorContext,
        task_run_id: UUID,
    ) -> TaskWorkflowStartIntent:
        """Read the append-only root intent without claiming or starting it."""

        async with self._database.transaction(actor.database_context()) as connection:
            await self._require_run(connection, actor, task_run_id)
            intent = await self._state.get_workflow_start_intent(
                connection,
                owner_kind="TASK_RUN",
                owner_id=task_run_id,
            )
            if intent is None:
                raise _not_found("TaskRun 尚未生成 Workflow Start Intent。")
            return intent

    async def list_pending_start_intents(
        self,
        actor: ActorContext,
        *,
        project_id: UUID,
        limit: int = 64,
    ) -> tuple[TaskWorkflowStartIntent, ...]:
        """Inspect pending intents without claiming, updating, or starting workflows."""

        if not actor.can_operate_project(project_id):
            raise _forbidden("当前身份不能查看该 Project 的 Workflow Start Intent。")
        if not 1 <= limit <= 1_000:
            raise _invalid_request("limit 必须在 1 到 1000 之间。")
        async with self._database.transaction(actor.database_context()) as connection:
            return await self._state.list_pending_workflow_start_intents(
                connection,
                project_id=project_id,
                limit=limit,
            )

    async def _require_run(
        self,
        connection: AsyncConnection[DictRow],
        actor: ActorContext,
        task_run_id: UUID,
    ) -> TaskRun:
        run = await self._tasks.get_run(connection, task_run_id)
        if run is None or run.tenant_id != actor.tenant_id:
            raise _not_found("TaskRun 不存在或不可见。")
        if not actor.can_operate_project(run.project_id):
            raise _forbidden("当前身份不能变更该 Project 的 TaskRun。")
        return run

    async def _append_state_event(
        self,
        connection: AsyncConnection[DictRow],
        *,
        run: TaskRun,
        projection: _ExecutionStateProjection,
        event_type: str,
        payload: dict[str, JsonValue],
        execution_unit_id: UUID | None = None,
        unit_attempt_id: UUID | None = None,
    ) -> None:
        """Append the matching projection event before the caller transaction commits."""

        seq = await self._state.next_task_execution_event_seq(
            connection,
            task_run_id=run.id,
        )
        event = TaskExecutionEvent(
            id=new_entity_id(),
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            execution_unit_id=execution_unit_id,
            unit_attempt_id=unit_attempt_id,
            seq=seq,
            event_type=event_type,
            lifecycle=projection.lifecycle,
            quality=projection.quality,
            hygiene=projection.hygiene,
            payload=payload,
            occurred_at=max(utc_now(), projection.updated_at),
        )
        await self._tasks.append_event(connection, event)

    async def _recover_run_replay(
        self,
        actor: ActorContext,
        task_run_id: UUID,
        state: TaskStateTransition,
    ) -> TaskStateChangeResult[TaskRun]:
        async with self._database.transaction(actor.database_context()) as connection:
            current = await self._require_run(connection, actor, task_run_id)
            _require_sealed_run(current)
            if _state_matches(current, state):
                return TaskStateChangeResult(current, replayed=True)
            raise _revision_conflict(current.revision)

    async def _recover_unit_replay(
        self,
        actor: ActorContext,
        *,
        task_run_id: UUID,
        execution_unit_id: UUID,
        state: TaskStateTransition,
    ) -> TaskStateChangeResult[ExecutionUnit]:
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._require_run(connection, actor, task_run_id)
            _require_sealed_run(run)
            current = await self._tasks.get_unit(connection, execution_unit_id)
            if current is None or current.task_run_id != task_run_id:
                raise _not_found("ExecutionUnit 不存在或不属于指定 TaskRun。")
            if _state_matches(current, state):
                return TaskStateChangeResult(current, replayed=True)
            raise _revision_conflict(current.revision)

    async def _recover_attempt_replay(
        self,
        actor: ActorContext,
        *,
        task_run_id: UUID,
        execution_unit_id: UUID,
        unit_attempt_id: UUID,
        state: TaskStateTransition,
    ) -> TaskStateChangeResult[UnitAttempt]:
        async with self._database.transaction(actor.database_context()) as connection:
            run = await self._require_run(connection, actor, task_run_id)
            _require_sealed_run(run)
            current = await self._tasks.get_attempt(connection, unit_attempt_id)
            if (
                current is None
                or current.task_run_id != task_run_id
                or current.execution_unit_id != execution_unit_id
            ):
                raise _not_found("UnitAttempt 不存在或不属于指定 ExecutionUnit。")
            if _state_matches(current, state):
                return TaskStateChangeResult(current, replayed=True)
            raise _revision_conflict(current.revision)


def _state_matches(
    projection: _ExecutionStateProjection,
    state: TaskStateTransition,
) -> bool:
    return (
        projection.lifecycle,
        projection.quality,
        projection.hygiene,
        projection.started_at,
        projection.finalized_at,
        projection.cleanup_resolved_at,
        projection.closed_at,
    ) == (
        state.lifecycle,
        state.quality,
        state.hygiene,
        state.started_at,
        state.finalized_at,
        state.cleanup_resolved_at,
        state.closed_at,
    )


def _require_revision(current_revision: int, expected_revision: int) -> None:
    if current_revision != expected_revision:
        raise _revision_conflict(current_revision)


def _require_sealed_run(run: TaskRun) -> None:
    if run.materialization_state is not TaskMaterializationState.SEALED:
        raise _admission_failed(
            "TaskRun 尚未完成 materialization seal，不能进入调度或推进执行状态。"
        )


def _invalid_request(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="请求参数无效",
        detail=detail,
        status_code=400,
    )


def _not_found(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="任务执行资源不存在",
        detail=detail,
        status_code=404,
    )


def _forbidden(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="任务执行权限不足",
        detail=detail,
        status_code=403,
    )


def _admission_failed(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONSTRAINT_UNSATISFIED,
        title="Task 调度准入失败",
        detail=detail,
        status_code=422,
    )


def _revision_conflict(current_revision: int) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.PRECONDITION_FAILED,
        title="任务执行 Revision 已变化",
        detail="请读取最新任务状态后重试。",
        status_code=412,
        headers={"ETag": format_revision_etag(current_revision)},
    )
