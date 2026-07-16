"""Application tests for Task admission and database-owned execution state."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.errors import SerializationFailure
from psycopg.rows import DictRow
from pydantic import JsonValue
from tests.domain.case.test_case_versions import case_version_payload
from tests.domain.task.test_profiles import model_profile, tool_profile

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.task_execution import (
    TaskAdmissionService,
    TaskExecutionStateService,
    TaskStateTransition,
)
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.case import CaseVersion
from atlas_testops.domain.case.models import canonical_digest
from atlas_testops.domain.fixture import AssetVersionStatus, DataBlueprintVersion
from atlas_testops.domain.identity import TestRole as IdentityRole
from atlas_testops.domain.identity import TestRoleStatus as IdentityRoleStatus
from atlas_testops.domain.platform import (
    Environment,
    EnvironmentKind,
    EnvironmentStatus,
)
from atlas_testops.domain.runtime import Viewport
from atlas_testops.domain.task import (
    BrowserProfileVersion,
    DataProfileVersion,
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionProfileVersion,
    ExecutionQuality,
    ExecutionUnit,
    IdentityActorBinding,
    IdentityProfileVersion,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskProfileStatus,
    TaskRun,
    TaskTriggerSource,
    UnitAttempt,
    browser_profile_content_digest,
    browser_profile_version_ref,
    data_profile_content_digest,
    data_profile_version_ref,
    execution_profile_content_digest,
    execution_profile_version_ref,
    execution_unit_dependency_digest,
    execution_unit_key,
    identity_profile_content_digest,
    identity_profile_version_ref,
    task_run_workflow_id,
)
from atlas_testops.domain.workflow import WorkflowGraph
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_profiles import TaskWorkflowStartIntent
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableCreateResult,
)

NOW = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def uid(value: int) -> UUID:
    return UUID(int=value)


class RecordingDatabase:
    def __init__(self) -> None:
        self.contexts: list[DatabaseContext] = []
        self.active_transactions = 0

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        self.active_transactions += 1
        try:
            yield cast(AsyncConnection[DictRow], object())
        finally:
            self.active_transactions -= 1


@dataclass(frozen=True, slots=True)
class FixtureContractView:
    run_input_schema: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class FixtureVersionView:
    id: UUID
    tenant_id: UUID
    project_id: UUID
    status: AssetVersionStatus
    content_digest: str
    plan_digest: str | None
    contract: FixtureContractView


class FakeTaskRepository:
    def __init__(
        self,
        *,
        unit: ExecutionUnit | None = None,
        run: TaskRun | None = None,
        attempt: UnitAttempt | None = None,
        run_sequence: list[TaskRun] | None = None,
    ) -> None:
        self.unit = unit
        self.run = run
        self.attempt = attempt
        self.run_sequence = run_sequence or []
        self.events: list[TaskExecutionEvent] = []

    async def get_unit(self, _connection: object, unit_id: UUID) -> ExecutionUnit | None:
        return self.unit if self.unit is not None and self.unit.id == unit_id else None

    async def get_run(self, _connection: object, run_id: UUID) -> TaskRun | None:
        if self.run_sequence:
            return self.run_sequence.pop(0)
        return self.run if self.run is not None and self.run.id == run_id else None

    async def get_attempt(
        self,
        _connection: object,
        attempt_id: UUID,
    ) -> UnitAttempt | None:
        return self.attempt if self.attempt is not None and self.attempt.id == attempt_id else None

    async def append_event(
        self,
        _connection: object,
        event: TaskExecutionEvent,
    ) -> ImmutableCreateResult[TaskExecutionEvent]:
        self.events.append(event)
        return ImmutableCreateResult(ImmutableCreateKind.CREATED, event)


class FakeProfileRepository:
    def __init__(
        self,
        execution: ExecutionProfileVersion,
        identity: IdentityProfileVersion,
        browser: BrowserProfileVersion,
        data: DataProfileVersion,
    ) -> None:
        self.execution = execution
        self.identity = identity
        self.browser = browser
        self.data = data

    async def get_execution_profile_version(self, *_args: object) -> ExecutionProfileVersion:
        return self.execution

    async def get_identity_profile_version(self, *_args: object) -> IdentityProfileVersion:
        return self.identity

    async def get_browser_profile_version(self, *_args: object) -> BrowserProfileVersion:
        return self.browser

    async def get_data_profile_version(self, *_args: object) -> DataProfileVersion:
        return self.data


class FakeCaseRepository:
    def __init__(self, case: CaseVersion | None) -> None:
        self.case = case

    async def get_version(self, *_args: object) -> CaseVersion | None:
        return self.case


class FakeFixtureRepository:
    def __init__(self, fixture: FixtureVersionView | None) -> None:
        self.fixture = fixture

    async def get_blueprint_version(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> DataBlueprintVersion | None:
        return cast(DataBlueprintVersion | None, self.fixture)


class FakePlatformRepository:
    def __init__(self, environment: Environment | None) -> None:
        self.environment = environment

    async def get_environment_for_share(self, *_args: object) -> Environment | None:
        return self.environment


class FakeIdentityRepository:
    def __init__(self, role: IdentityRole | None) -> None:
        self.role = role
        self.calls = 0

    async def get_role(self, *_args: object, **_kwargs: object) -> IdentityRole | None:
        self.calls += 1
        return self.role


class FakeStateRepository:
    def __init__(self) -> None:
        self.sealed: TaskRun | None = None
        self.run_result: TaskRun | None = None
        self.unit_result: ExecutionUnit | None = None
        self.attempt_result: UnitAttempt | None = None
        self.raise_serialization = False
        self.calls: list[str] = []
        self.intent: TaskWorkflowStartIntent | None = None

    async def seal_task_run_materialization(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> TaskRun | None:
        self.calls.append("seal")
        return self.sealed

    async def transition_task_run_state(self, *_args: object, **_kwargs: object) -> TaskRun | None:
        self.calls.append("run")
        if self.raise_serialization:
            self.raise_serialization = False
            raise SerializationFailure("concurrent CAS")
        return self.run_result

    async def transition_execution_unit_state(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> ExecutionUnit | None:
        self.calls.append("unit")
        return self.unit_result

    async def transition_unit_attempt_state(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> UnitAttempt | None:
        self.calls.append("attempt")
        return self.attempt_result

    async def next_task_execution_event_seq(self, *_args: object, **_kwargs: object) -> int:
        return 1

    async def get_workflow_start_intent(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> TaskWorkflowStartIntent | None:
        return self.intent

    async def list_pending_workflow_start_intents(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> tuple[TaskWorkflowStartIntent, ...]:
        return (self.intent,) if self.intent is not None else ()


@dataclass(slots=True)
class AdmissionFixture:
    case: CaseVersion
    unit: ExecutionUnit
    profiles: FakeProfileRepository
    fixture: FixtureVersionView
    environment: Environment
    role: IdentityRole
    actor: ActorContext


def _profile_common(
    *,
    profile_id: UUID,
    case: CaseVersion,
    profile_key: str,
    version_ref: str,
    content_digest: str,
) -> dict[str, object]:
    return {
        "id": profile_id,
        "tenant_id": case.tenant_id,
        "project_id": case.project_id,
        "profile_key": profile_key,
        "version": "1.0.0",
        "version_ref": version_ref,
        "status": TaskProfileStatus.PUBLISHED,
        "content_digest": content_digest,
        "published_by": uid(900),
        "published_at": NOW,
        "revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _admission_fixture(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., object],
) -> AdmissionFixture:
    case = CaseVersion.model_validate(case_version_payload(valid_graph, cast(Any, intent_factory)))
    execution_id = uid(101)
    identity_id = uid(102)
    browser_id = uid(103)
    data_id = uid(104)
    environment_id = uid(105)
    fixture_id = case.test_ir.fixture.blueprint_version_id
    features = case.test_ir.required_features
    execution_digest = execution_profile_content_digest(
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        profile_key="case-runtime",
        version="1.0.0",
        case_version_id=case.id,
        case_content_digest=case.content_digest,
        test_ir_digest=case.test_ir_digest,
        plan_digest=case.plan_digest,
        compiled_digest=case.compiled_digest,
        model=model_profile(),
        tools=tool_profile(),
        supported_features=features,
    )
    execution = ExecutionProfileVersion.model_validate(
        {
            **_profile_common(
                profile_id=execution_id,
                case=case,
                profile_key="case-runtime",
                version_ref=execution_profile_version_ref("case-runtime", "1.0.0"),
                content_digest=execution_digest,
            ),
            "case_version_id": case.id,
            "case_content_digest": case.content_digest,
            "test_ir_digest": case.test_ir_digest,
            "plan_digest": case.plan_digest,
            "compiled_digest": case.compiled_digest,
            "model": model_profile(),
            "tools": tool_profile(),
            "supported_features": features,
        }
    )
    actor_bindings = tuple(
        IdentityActorBinding(
            actor_slot=item.actor_slot,
            role_id=item.role_id,
            role_key=item.role_key,
            role_revision=item.role_revision,
            capabilities=item.capabilities,
        )
        for item in case.test_ir.actors
    )
    identity_digest = identity_profile_content_digest(
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        profile_key="case-actors",
        version="1.0.0",
        case_version_id=case.id,
        case_content_digest=case.content_digest,
        actors=actor_bindings,
    )
    identity = IdentityProfileVersion.model_validate(
        {
            **_profile_common(
                profile_id=identity_id,
                case=case,
                profile_key="case-actors",
                version_ref=identity_profile_version_ref("case-actors", "1.0.0"),
                content_digest=identity_digest,
            ),
            "case_version_id": case.id,
            "case_content_digest": case.content_digest,
            "actors": actor_bindings,
        }
    )
    viewport = Viewport(width=1440, height=900)
    browser_digest = browser_profile_content_digest(
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        profile_key="browser-main",
        version="1.0.0",
        engine="chromium",
        revision="chromium-140",
        viewport=viewport,
        locale="zh-CN",
        timezone="Asia/Shanghai",
        runtime_image_digest=DIGEST_C,
        capability_digest=None,
    )
    browser = BrowserProfileVersion.model_validate(
        {
            **_profile_common(
                profile_id=browser_id,
                case=case,
                profile_key="browser-main",
                version_ref=browser_profile_version_ref("browser-main", "1.0.0"),
                content_digest=browser_digest,
            ),
            "engine": "chromium",
            "browser_revision": "chromium-140",
            "viewport": viewport,
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "runtime_image_digest": DIGEST_C,
        }
    )
    run_inputs: dict[str, JsonValue] = {"quantity": 2}
    input_digest = canonical_digest(run_inputs)
    fixture_plan_digest = DIGEST_B
    data_digest = data_profile_content_digest(
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        profile_key="data-main",
        version="1.0.0",
        blueprint_version_id=fixture_id,
        blueprint_version_ref=case.test_ir.fixture.blueprint_version_ref,
        blueprint_content_digest=case.test_ir.fixture.content_digest,
        plan_digest=fixture_plan_digest,
        run_inputs=run_inputs,
        input_digest=input_digest,
    )
    data = DataProfileVersion.model_validate(
        {
            **_profile_common(
                profile_id=data_id,
                case=case,
                profile_key="data-main",
                version_ref=data_profile_version_ref("data-main", "1.0.0"),
                content_digest=data_digest,
            ),
            "blueprint_version_id": fixture_id,
            "blueprint_version_ref": case.test_ir.fixture.blueprint_version_ref,
            "blueprint_content_digest": case.test_ir.fixture.content_digest,
            "plan_digest": fixture_plan_digest,
            "run_inputs": run_inputs,
            "input_digest": input_digest,
        }
    )
    parameter_digest = canonical_digest(run_inputs)
    unit_key = execution_unit_key(
        case_version_id=case.id,
        environment_id=environment_id,
        browser_profile_version_id=browser.id,
        identity_profile_version_id=identity.id,
        data_profile_version_id=data.id,
        parameter_digest=parameter_digest,
    )
    dependency_digest = execution_unit_dependency_digest(
        case_version_id=case.id,
        execution_profile_version_id=execution.id,
        fixture_blueprint_version_id=fixture_id,
        identity_profile_version_id=identity.id,
        environment_id=environment_id,
        browser_profile_version_id=browser.id,
        data_profile_version_id=data.id,
    )
    unit = ExecutionUnit(
        id=uid(110),
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        task_run_id=uid(111),
        manifest_hash=DIGEST_C,
        ordinal=1,
        unit_key=unit_key,
        case_version_id=case.id,
        execution_profile_version_id=execution.id,
        fixture_blueprint_version_id=fixture_id,
        identity_profile_version_id=identity.id,
        environment_id=environment_id,
        browser_profile_version_id=browser.id,
        data_profile_version_id=data.id,
        parameter_digest=parameter_digest,
        dependency_digest=dependency_digest,
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    fixture = FixtureVersionView(
        id=fixture_id,
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        status=AssetVersionStatus.PUBLISHED,
        content_digest=case.test_ir.fixture.content_digest,
        plan_digest=fixture_plan_digest,
        contract=FixtureContractView(
            run_input_schema={
                "type": "object",
                "properties": {"quantity": {"type": "integer"}},
                "required": ["quantity"],
                "additionalProperties": False,
            }
        ),
    )
    environment = Environment(
        id=environment_id,
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        environment_key="staging",
        name="Staging",
        kind=EnvironmentKind.STAGING,
        status=EnvironmentStatus.ACTIVE,
        allowed_origins=("https://example.test",),
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    binding = actor_bindings[0]
    role = IdentityRole(
        id=binding.role_id,
        tenant_id=case.tenant_id,
        project_id=case.project_id,
        role_key=binding.role_key,
        name="Operator",
        description="Task execution actor",
        capabilities=binding.capabilities,
        status=IdentityRoleStatus.ACTIVE,
        revision=binding.role_revision,
        created_at=NOW,
        updated_at=NOW,
    )
    actor = ActorContext(
        tenant_id=case.tenant_id,
        actor_id=uid(999),
        request_id="request-task-admission",
        grants=(AccessGrant(role=PlatformRole.RUN_OPERATOR, project_id=case.project_id),),
    )
    return AdmissionFixture(
        case=case,
        unit=unit,
        profiles=FakeProfileRepository(execution, identity, browser, data),
        fixture=fixture,
        environment=environment,
        role=role,
        actor=actor,
    )


def _admission_service(
    fixture: AdmissionFixture,
    *,
    run: TaskRun | None = None,
) -> tuple[TaskAdmissionService, RecordingDatabase, FakeIdentityRepository]:
    database = RecordingDatabase()
    identities = FakeIdentityRepository(fixture.role)
    service = TaskAdmissionService(
        cast(Database, database),
        task_repository=cast(
            Any,
            FakeTaskRepository(
                unit=fixture.unit,
                run=run
                or _run(
                    sealed=True,
                    task_run_id=fixture.unit.task_run_id,
                    tenant_id=fixture.unit.tenant_id,
                    project_id=fixture.unit.project_id,
                ),
            ),
        ),
        profile_repository=cast(Any, fixture.profiles),
        case_repository=cast(Any, FakeCaseRepository(fixture.case)),
        fixture_repository=cast(Any, FakeFixtureRepository(fixture.fixture)),
        platform_repository=cast(Any, FakePlatformRepository(fixture.environment)),
        identity_repository=cast(Any, identities),
    )
    return service, database, identities


@pytest.mark.anyio
async def test_admission_returns_exact_snapshot_without_dispatcher_io(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., object],
) -> None:
    fixture = _admission_fixture(valid_graph, intent_factory)
    service, database, identities = _admission_service(fixture)

    snapshot = await service.admit_unit(fixture.actor, fixture.unit.id)

    assert snapshot.unit == fixture.unit
    assert snapshot.case_version == fixture.case
    assert snapshot.execution_profile == fixture.profiles.execution
    assert snapshot.roles == (fixture.role,)
    assert identities.calls == 1
    assert database.active_transactions == 0


@pytest.mark.anyio
async def test_admission_blocks_profile_case_fixture_environment_and_role_drift(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., object],
) -> None:
    scenarios: list[tuple[str, Callable[[AdmissionFixture], None]]] = []

    def deprecated(item: AdmissionFixture) -> None:
        item.profiles.browser = item.profiles.browser.model_copy(
            update={"status": TaskProfileStatus.DEPRECATED}
        )

    def case_drift(item: AdmissionFixture) -> None:
        item.profiles.execution = item.profiles.execution.model_copy(
            update={"case_content_digest": DIGEST_C}
        )

    def actor_override(item: AdmissionFixture) -> None:
        binding = item.profiles.identity.actors[0].model_copy(
            update={"role_revision": item.profiles.identity.actors[0].role_revision + 1}
        )
        item.profiles.identity = item.profiles.identity.model_copy(update={"actors": (binding,)})

    def identity_alias(item: AdmissionFixture) -> None:
        item.profiles.identity = item.profiles.identity.model_copy(update={"id": uid(9_102)})

    def browser_alias(item: AdmissionFixture) -> None:
        item.profiles.browser = item.profiles.browser.model_copy(update={"id": uid(9_103)})

    def invalid_inputs(item: AdmissionFixture) -> None:
        item.profiles.data = item.profiles.data.model_copy(
            update={"run_inputs": {"quantity": "two"}}
        )

    def disabled_environment(item: AdmissionFixture) -> None:
        item.environment = item.environment.model_copy(
            update={"status": EnvironmentStatus.DISABLED}
        )

    def stale_role(item: AdmissionFixture) -> None:
        item.role = item.role.model_copy(update={"revision": item.role.revision + 1})

    scenarios.extend(
        (
            ("PUBLISHED", deprecated),
            ("ExecutionProfileVersion", case_drift),
            ("不能覆盖", actor_override),
            ("不能覆盖", identity_alias),
            ("exact browser binding", browser_alias),
            ("run inputs", invalid_inputs),
            ("ACTIVE TEST/STAGING", disabled_environment),
            ("发生漂移", stale_role),
        )
    )
    for message, mutate in scenarios:
        item = _admission_fixture(valid_graph, intent_factory)
        mutate(item)
        service, _database, _identities = _admission_service(item)
        with pytest.raises(ApplicationError, match=message) as caught:
            await service.admit_unit(item.actor, item.unit.id)
        assert caught.value.error_code is ErrorCode.CONSTRAINT_UNSATISFIED


@pytest.mark.anyio
async def test_admission_hides_missing_units_and_rejects_non_operator(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., object],
) -> None:
    fixture = _admission_fixture(valid_graph, intent_factory)
    database = RecordingDatabase()
    missing = TaskAdmissionService(
        cast(Database, database),
        task_repository=cast(Any, FakeTaskRepository()),
    )
    with pytest.raises(ApplicationError) as absent:
        await missing.admit_unit(fixture.actor, fixture.unit.id)
    assert absent.value.error_code is ErrorCode.NOT_FOUND

    forbidden_actor = fixture.actor.__class__(
        tenant_id=fixture.actor.tenant_id,
        actor_id=fixture.actor.actor_id,
        request_id=fixture.actor.request_id,
    )
    service, _database, _identities = _admission_service(fixture)
    with pytest.raises(ApplicationError) as forbidden:
        await service.admit_unit(forbidden_actor, fixture.unit.id)
    assert forbidden.value.error_code is ErrorCode.FORBIDDEN


@pytest.mark.anyio
async def test_admission_rejects_unit_until_parent_run_is_sealed(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., object],
) -> None:
    fixture = _admission_fixture(valid_graph, intent_factory)
    service, _database, identities = _admission_service(
        fixture,
        run=_run(
            task_run_id=fixture.unit.task_run_id,
            tenant_id=fixture.unit.tenant_id,
            project_id=fixture.unit.project_id,
        ),
    )

    with pytest.raises(ApplicationError, match="materialization seal") as blocked:
        await service.admit_unit(fixture.actor, fixture.unit.id)

    assert blocked.value.error_code is ErrorCode.CONSTRAINT_UNSATISFIED
    assert identities.calls == 0


@pytest.mark.anyio
async def test_admission_rejects_paused_run_and_non_queued_unit(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., object],
) -> None:
    paused_fixture = _admission_fixture(valid_graph, intent_factory)
    paused_run = _run(
        sealed=True,
        task_run_id=paused_fixture.unit.task_run_id,
        tenant_id=paused_fixture.unit.tenant_id,
        project_id=paused_fixture.unit.project_id,
    ).model_copy(
        update={"lifecycle": ExecutionLifecycle.PAUSED}
    )
    paused_service, _database, identities = _admission_service(
        paused_fixture,
        run=paused_run,
    )
    with pytest.raises(ApplicationError, match="不允许派发") as paused:
        await paused_service.admit_unit(paused_fixture.actor, paused_fixture.unit.id)
    assert paused.value.error_code is ErrorCode.CONSTRAINT_UNSATISFIED
    assert identities.calls == 0

    running_fixture = _admission_fixture(valid_graph, intent_factory)
    running_fixture.unit = running_fixture.unit.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": NOW,
        }
    )
    running_service, _database, identities = _admission_service(running_fixture)
    with pytest.raises(ApplicationError, match="QUEUED ExecutionUnit") as running:
        await running_service.admit_unit(running_fixture.actor, running_fixture.unit.id)
    assert running.value.error_code is ErrorCode.CONSTRAINT_UNSATISFIED
    assert identities.calls == 0


def _run(
    *,
    sealed: bool = False,
    task_run_id: UUID | None = None,
    tenant_id: UUID | None = None,
    project_id: UUID | None = None,
) -> TaskRun:
    task_run_id = task_run_id or uid(401)
    tenant_id = tenant_id or uid(1)
    project_id = project_id or uid(2)
    materialization = (
        {
            "request_digest": DIGEST_A,
            "materialization_state": TaskMaterializationState.SEALED,
            "materialized_unit_count": 1,
            "materialized_first_attempt_count": 1,
            "materialization_sealed_at": NOW,
            "temporal_namespace": "atlas-prod",
            "temporal_workflow_id": task_run_workflow_id(
                tenant_id=tenant_id,
                task_run_id=task_run_id,
            ),
            "revision": 2,
        }
        if sealed
        else {"revision": 1}
    )
    return TaskRun(
        id=task_run_id,
        tenant_id=tenant_id,
        project_id=project_id,
        task_plan_version_id=uid(3),
        manifest_hash=DIGEST_C,
        trigger_source=TaskTriggerSource.MANUAL,
        trigger_fingerprint="manual:request-123",
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        requested_by=uid(4),
        requested_at=NOW,
        queued_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        **materialization,
    )


def _state_actor() -> ActorContext:
    return ActorContext(
        tenant_id=uid(1),
        actor_id=uid(9),
        request_id="request-task-state",
        grants=(AccessGrant(role=PlatformRole.RUN_OPERATOR, project_id=uid(2)),),
    )


def _unit(run: TaskRun) -> ExecutionUnit:
    parameter_digest = DIGEST_A
    unit_key = execution_unit_key(
        case_version_id=uid(10),
        environment_id=uid(11),
        browser_profile_version_id=uid(12),
        identity_profile_version_id=uid(13),
        data_profile_version_id=uid(14),
        parameter_digest=parameter_digest,
    )
    dependency_digest = execution_unit_dependency_digest(
        case_version_id=uid(10),
        execution_profile_version_id=uid(15),
        fixture_blueprint_version_id=uid(16),
        identity_profile_version_id=uid(13),
        environment_id=uid(11),
        browser_profile_version_id=uid(12),
        data_profile_version_id=uid(14),
    )
    return ExecutionUnit(
        id=uid(501),
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        task_run_id=run.id,
        manifest_hash=run.manifest_hash,
        ordinal=1,
        unit_key=unit_key,
        case_version_id=uid(10),
        execution_profile_version_id=uid(15),
        fixture_blueprint_version_id=uid(16),
        identity_profile_version_id=uid(13),
        environment_id=uid(11),
        browser_profile_version_id=uid(12),
        data_profile_version_id=uid(14),
        parameter_digest=parameter_digest,
        dependency_digest=dependency_digest,
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _attempt(run: TaskRun, unit: ExecutionUnit) -> UnitAttempt:
    return UnitAttempt(
        id=uid(601),
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        task_run_id=run.id,
        execution_unit_id=unit.id,
        manifest_hash=run.manifest_hash,
        unit_key=unit.unit_key,
        case_version_id=unit.case_version_id,
        attempt_number=1,
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        queued_at=NOW,
        execution_deadline=NOW + timedelta(minutes=15),
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _state_service(
    tasks: FakeTaskRepository,
    state: FakeStateRepository,
) -> tuple[TaskExecutionStateService, RecordingDatabase]:
    database = RecordingDatabase()
    return (
        TaskExecutionStateService(
            cast(Database, database),
            task_repository=cast(Any, tasks),
            state_repository=cast(Any, state),
        ),
        database,
    )


@pytest.mark.anyio
async def test_state_service_seals_and_appends_event_in_same_transaction() -> None:
    current = _run()
    sealed = _run(sealed=True)
    tasks = FakeTaskRepository(run=current)
    state = FakeStateRepository()
    state.sealed = sealed
    service, database = _state_service(tasks, state)

    result = await service.seal_run(_state_actor(), current.id, expected_revision=1)

    assert result.value == sealed
    assert result.replayed is False
    assert state.calls == ["seal"]
    assert len(tasks.events) == 1
    assert tasks.events[0].event_type == "task_run.materialization_sealed"
    assert database.active_transactions == 0

    tasks.run = sealed
    replay = await service.seal_run(_state_actor(), current.id, expected_revision=1)
    assert replay.replayed is True
    assert len(tasks.events) == 1


@pytest.mark.anyio
async def test_state_service_transitions_each_scope_and_appends_matching_events() -> None:
    run = _run(sealed=True)
    unit = _unit(run)
    attempt = _attempt(run, unit)
    started = NOW + timedelta(minutes=1)
    target_state = TaskStateTransition(
        lifecycle=ExecutionLifecycle.RUNNING,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        started_at=started,
    )
    updated_run = run.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": started,
            "revision": run.revision + 1,
            "updated_at": started,
        }
    )
    updated_unit = unit.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": started,
            "revision": 2,
            "updated_at": started,
        }
    )
    updated_attempt = attempt.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": started,
            "revision": 2,
            "updated_at": started,
        }
    )
    tasks = FakeTaskRepository(run=run, unit=unit, attempt=attempt)
    state = FakeStateRepository()
    state.run_result = updated_run
    state.unit_result = updated_unit
    state.attempt_result = updated_attempt
    service, _database = _state_service(tasks, state)

    run_result = await service.transition_run(
        _state_actor(),
        run.id,
        expected_revision=run.revision,
        state=target_state,
    )
    unit_result = await service.transition_unit(
        _state_actor(),
        task_run_id=run.id,
        execution_unit_id=unit.id,
        expected_revision=unit.revision,
        state=target_state,
    )
    attempt_result = await service.transition_attempt(
        _state_actor(),
        task_run_id=run.id,
        execution_unit_id=unit.id,
        unit_attempt_id=attempt.id,
        expected_revision=attempt.revision,
        state=target_state,
    )

    assert run_result.value == updated_run
    assert unit_result.value == updated_unit
    assert attempt_result.value == updated_attempt
    assert state.calls == ["run", "unit", "attempt"]
    assert [event.event_type for event in tasks.events] == [
        "task_run.state_changed",
        "execution_unit.state_changed",
        "unit_attempt.state_changed",
    ]
    assert tasks.events[1].execution_unit_id == unit.id
    assert tasks.events[2].unit_attempt_id == attempt.id


@pytest.mark.anyio
async def test_state_service_replays_exact_state_and_recovers_serialization_race() -> None:
    current = _run(sealed=True)
    started = NOW + timedelta(minutes=1)
    target = current.model_copy(
        update={
            "lifecycle": ExecutionLifecycle.RUNNING,
            "started_at": started,
            "revision": 3,
            "updated_at": started,
        }
    )
    command = TaskStateTransition(
        lifecycle=ExecutionLifecycle.RUNNING,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        started_at=started,
    )
    direct_tasks = FakeTaskRepository(run=target)
    direct_state = FakeStateRepository()
    direct_service, _database = _state_service(direct_tasks, direct_state)
    direct = await direct_service.transition_run(
        _state_actor(),
        target.id,
        expected_revision=1,
        state=command,
    )
    assert direct.replayed is True
    assert direct_state.calls == []

    race_tasks = FakeTaskRepository(run_sequence=[current, target])
    race_state = FakeStateRepository()
    race_state.raise_serialization = True
    race_service, race_database = _state_service(race_tasks, race_state)
    recovered = await race_service.transition_run(
        _state_actor(),
        current.id,
        expected_revision=current.revision,
        state=command,
    )
    assert recovered.replayed is True
    assert len(race_database.contexts) == 2


@pytest.mark.anyio
async def test_state_service_revision_scope_and_pending_intent_guards() -> None:
    run = _run(sealed=True)
    tasks = FakeTaskRepository(run=run)
    state = FakeStateRepository()
    service, _database = _state_service(tasks, state)
    incompatible = TaskStateTransition(
        lifecycle=ExecutionLifecycle.CANCELING,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
    )
    with pytest.raises(ApplicationError) as stale:
        await service.transition_run(
            _state_actor(),
            run.id,
            expected_revision=1,
            state=incompatible,
        )
    assert stale.value.error_code is ErrorCode.PRECONDITION_FAILED
    assert stale.value.headers["ETag"] == '"revision-2"'

    unsealed = _run()
    unsealed_service, _database = _state_service(
        FakeTaskRepository(run=unsealed),
        FakeStateRepository(),
    )
    with pytest.raises(ApplicationError, match="materialization seal") as not_ready:
        await unsealed_service.transition_run(
            _state_actor(),
            unsealed.id,
            expected_revision=unsealed.revision,
            state=incompatible,
        )
    assert not_ready.value.error_code is ErrorCode.CONSTRAINT_UNSATISFIED

    with pytest.raises(ApplicationError) as invalid_limit:
        await service.list_pending_start_intents(
            _state_actor(),
            project_id=run.project_id,
            limit=0,
        )
    assert invalid_limit.value.error_code is ErrorCode.INVALID_REQUEST

    with pytest.raises(ApplicationError) as missing_intent:
        await service.get_run_start_intent(_state_actor(), run.id)
    assert missing_intent.value.error_code is ErrorCode.NOT_FOUND

    state.intent = TaskWorkflowStartIntent(
        id=run.id,
        tenant_id=run.tenant_id,
        project_id=run.project_id,
        task_run_id=run.id,
        owner_kind="TASK_RUN",
        owner_id=run.id,
        namespace="atlas-prod",
        workflow_id=cast(str, run.temporal_workflow_id),
        request_digest=cast(str, run.request_digest),
        workflow_type="AtlasTaskRunWorkflow",
        task_queue="atlas-task-run",
        status="PENDING",
        created_at=NOW,
    )
    assert await service.get_run_start_intent(_state_actor(), run.id) == state.intent
    assert await service.list_pending_start_intents(
        _state_actor(),
        project_id=run.project_id,
    ) == (state.intent,)
