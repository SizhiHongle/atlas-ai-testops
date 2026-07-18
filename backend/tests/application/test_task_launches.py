"""Application tests for bounded Manual Launch compilation and materialization."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.infrastructure.test_task_run_repository import NOW, _task_plan, uid

from atlas_testops.application.access import ActorContext
from atlas_testops.application.task_launches import (
    TASK_LAUNCH_EXECUTION_WINDOW,
    TaskPlanLaunchService,
    compile_task_plan_version,
)
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.platform import Project, ProjectStatus
from atlas_testops.domain.task import (
    TASK_RETRY_POLICY_DIGEST_KEY,
    CaseExecutionProfileRef,
    ExecutionUnit,
    StartTaskPlanVersionRun,
    TaskMaterializationState,
    TaskMatrixDefinition,
    TaskPlanVersion,
    TaskProfileRefs,
    TaskRetryPolicy,
    TaskRun,
    TaskRunManifest,
    UnitAttempt,
    task_plan_version_content_digest,
    task_plan_version_ref,
    task_retry_policy_digest,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    IdempotencyReservation,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    TaskPlanLaunchBindings,
    TaskRunCreateResult,
    TaskRunRepository,
)


class ClockCursor:
    async def fetchone(self) -> dict[str, object]:
        return {"now": NOW}


class ClockConnection:
    async def execute(
        self,
        _query: str,
        _parameters: object = None,
    ) -> ClockCursor:
        return ClockCursor()


class RecordingDatabase:
    def __init__(self) -> None:
        self.contexts: list[DatabaseContext] = []

    @asynccontextmanager
    async def transaction(
        self,
        context: DatabaseContext,
    ) -> AsyncIterator[AsyncConnection[DictRow]]:
        self.contexts.append(context)
        yield cast(AsyncConnection[DictRow], ClockConnection())


class RecordingPlatformRepository:
    def __init__(self, project: Project) -> None:
        self.project = project

    async def get_project_for_share(
        self,
        _connection: object,
        project_id: UUID,
    ) -> Project | None:
        return self.project if project_id == self.project.id else None


class RecordingTaskRepository:
    def __init__(
        self,
        version: TaskPlanVersion,
        bindings: TaskPlanLaunchBindings,
    ) -> None:
        self.plan = _task_plan()
        self.version = version
        self.bindings = bindings
        self.create_calls: list[dict[str, object]] = []
        self.events: list[object] = []

    async def get_task_plan_version(
        self,
        _connection: object,
        version_id: UUID,
    ) -> TaskPlanVersion | None:
        return self.version if version_id == self.version.id else None

    async def get_task_plan(
        self,
        _connection: object,
        plan_id: UUID,
        *,
        for_share: bool = False,
    ) -> object | None:
        del for_share
        return self.plan if plan_id == self.plan.id else None

    async def get_task_plan_launch_bindings(
        self,
        _connection: object,
        **_values: object,
    ) -> TaskPlanLaunchBindings:
        return self.bindings

    async def create_run(
        self,
        _connection: object,
        **values: object,
    ) -> TaskRunCreateResult:
        self.create_calls.append(values)
        run = cast(TaskRun, values["task_run"])
        manifest = cast(TaskRunManifest, values["manifest"])
        units = cast(tuple[ExecutionUnit, ...], values["units"])
        attempts = cast(tuple[UnitAttempt, ...], values["first_attempts"])
        sealed = run.model_copy(
            update={
                "materialization_state": TaskMaterializationState.SEALED,
                "materialized_unit_count": len(units),
                "materialized_first_attempt_count": len(attempts),
                "materialization_sealed_at": NOW,
                "revision": 2,
            }
        )
        return TaskRunCreateResult(
            ImmutableCreateKind.CREATED,
            sealed,
            manifest,
        )

    async def append_event(
        self,
        _connection: object,
        event: object,
    ) -> None:
        self.events.append(event)


class RecordingStateRepository:
    async def next_task_execution_event_seq(
        self,
        _connection: object,
        *,
        task_run_id: UUID,
    ) -> int:
        del task_run_id
        return 1


class RecordingIdempotencyRepository:
    def __init__(self) -> None:
        self.responses: dict[tuple[str, str], CachedHttpResponse] = {}

    async def reserve(
        self,
        _connection: object,
        *,
        scope: str,
        key: str,
        **_values: object,
    ) -> IdempotencyReservation:
        cached = self.responses.get((scope, key))
        return IdempotencyReservation(
            acquired=cached is None,
            cached_response=cached,
        )

    async def complete(
        self,
        _connection: object,
        *,
        scope: str,
        key: str,
        response: CachedHttpResponse,
        **_values: object,
    ) -> None:
        self.responses[(scope, key)] = response


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def append(
        self,
        _connection: object,
        event: object = None,
        **values: Any,
    ) -> None:
        self.calls.append(values or {"event": event})


def _retry_policy() -> TaskRetryPolicy:
    return TaskRetryPolicy(
        infra_retry_attempts=2,
        max_total_infra_retries=8,
        initial_backoff_seconds=2,
        maximum_backoff_seconds=30,
        jitter_percent=10,
        content_digest=task_retry_policy_digest(
            infra_retry_attempts=2,
            max_total_infra_retries=8,
            initial_backoff_seconds=2,
            maximum_backoff_seconds=30,
            jitter_percent=10,
        ),
    )


def _version(
    *,
    environment_ids: tuple[UUID, ...] = (uid(11),),
    identity_ids: tuple[UUID, ...] = (uid(13),),
    data_ids: tuple[UUID, ...] = (uid(14),),
) -> TaskPlanVersion:
    plan = _task_plan()
    policy = _retry_policy()
    case_id = uid(7)
    matrix = TaskMatrixDefinition(
        environment_ids=environment_ids,
        browser_profile_version_ids=(uid(12),),
        identity_profile_version_ids=identity_ids,
        data_profile_version_ids=data_ids,
    )
    refs = TaskProfileRefs(
        case_profiles=(
            CaseExecutionProfileRef(
                case_version_id=case_id,
                execution_profile_version_id=uid(8),
                fixture_blueprint_version_id=uid(9),
            ),
        )
    )
    policies = {
        TASK_RETRY_POLICY_DIGEST_KEY: policy.content_digest,
        "gate": f"sha256:{'a' * 64}",
    }
    digest = task_plan_version_content_digest(
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        task_plan_id=plan.id,
        version="1.0.0",
        pinned_case_version_ids=(case_id,),
        matrix=matrix,
        profile_refs=refs,
        policy_digests=policies,
    )
    return TaskPlanVersion(
        id=uid(5),
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        task_plan_id=plan.id,
        version="1.0.0",
        version_ref=task_plan_version_ref(plan.id, "1.0.0"),
        pinned_case_version_ids=(case_id,),
        matrix=matrix,
        profile_refs=refs,
        policy_digests=policies,
        content_digest=digest,
        published_by=uid(6),
        published_at=NOW,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _project() -> Project:
    return Project(
        id=uid(2),
        tenant_id=uid(1),
        project_key="ATLAS",
        name="Atlas",
        status=ProjectStatus.ACTIVE,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _actor() -> ActorContext:
    return ActorContext(
        tenant_id=uid(1),
        actor_id=uid(6),
        request_id="manual-launch-test",
        development_override=True,
    )


def _service() -> tuple[
    TaskPlanLaunchService,
    RecordingTaskRepository,
    RecordingSink,
    RecordingSink,
]:
    version = _version()
    tasks = RecordingTaskRepository(
        version,
        TaskPlanLaunchBindings(
            identity_case_by_id={uid(13): uid(7)},
            data_blueprint_by_id={uid(14): uid(9)},
        ),
    )
    audit = RecordingSink()
    outbox = RecordingSink()
    service = TaskPlanLaunchService(
        cast(Database, RecordingDatabase()),
        temporal_namespace="default",
        task_repository=cast(TaskRunRepository, tasks),
        platform_repository=cast(
            PlatformRepository,
            RecordingPlatformRepository(_project()),
        ),
        state_repository=cast(
            TaskExecutionStateRepository,
            RecordingStateRepository(),
        ),
        audit_repository=cast(AuditRepository, audit),
        outbox_repository=cast(OutboxRepository, outbox),
        idempotency_repository=cast(
            IdempotencyRepository,
            RecordingIdempotencyRepository(),
        ),
    )
    return service, tasks, audit, outbox


@pytest.mark.anyio
async def test_manual_launch_compiles_seals_and_replays_one_exact_run() -> None:
    service, tasks, audit, outbox = _service()
    command = StartTaskPlanVersionRun(
        client_mutation_id="manual-launch-001",
        iteration_id="iteration:nightly",
        retry_policy=_retry_policy(),
    )

    created = await service.launch(
        _actor(),
        uid(5),
        command,
        idempotency_key=command.client_mutation_id,
    )
    replayed = await service.launch(
        _actor(),
        uid(5),
        command,
        idempotency_key=command.client_mutation_id,
    )

    assert created.status_code == 201
    assert created.value.materialization_state == "SEALED"
    assert created.value.materialized_unit_count == 1
    assert replayed.replayed
    assert replayed.value == created.value
    assert len(tasks.create_calls) == 1
    manifest = cast(TaskRunManifest, tasks.create_calls[0]["manifest"])
    attempts = cast(
        tuple[UnitAttempt, ...],
        tasks.create_calls[0]["first_attempts"],
    )
    assert manifest.schema_version == "atlas.task-run-manifest/0.2"
    assert manifest.trigger_source == "MANUAL"
    assert attempts[0].execution_deadline - attempts[0].queued_at == (
        TASK_LAUNCH_EXECUTION_WINDOW
    )
    assert len(tasks.events) == 1
    assert len(audit.calls) == 1
    assert len(outbox.calls) == 1


def test_compiler_only_expands_compatible_profiles_and_enforces_bound() -> None:
    version = _version(
        environment_ids=tuple(uid(100 + index) for index in range(33)),
        identity_ids=(uid(13), uid(15)),
        data_ids=(uid(14), uid(16)),
    )
    bindings = TaskPlanLaunchBindings(
        identity_case_by_id={uid(13): uid(7), uid(15): uid(99)},
        data_blueprint_by_id={uid(14): uid(9), uid(16): uid(98)},
    )

    units = compile_task_plan_version(version, bindings, maximum_units=64)
    assert len(units) == 33
    assert {unit.identity_profile_version_id for unit in units} == {uid(13)}
    assert {unit.data_profile_version_id for unit in units} == {uid(14)}

    with pytest.raises(ApplicationError) as overflow:
        compile_task_plan_version(
            _version(
                environment_ids=tuple(uid(200 + index) for index in range(65)),
            ),
            TaskPlanLaunchBindings(
                identity_case_by_id={uid(13): uid(7)},
                data_blueprint_by_id={uid(14): uid(9)},
            ),
            maximum_units=64,
        )
    assert overflow.value.error_code is ErrorCode.CONFLICT


@pytest.mark.anyio
async def test_manual_launch_rejects_mismatched_idempotency_and_retry_policy() -> None:
    service, _tasks, _audit, _outbox = _service()
    command = StartTaskPlanVersionRun(
        client_mutation_id="manual-launch-001",
        retry_policy=_retry_policy(),
    )
    with pytest.raises(ApplicationError) as mismatch:
        await service.launch(
            _actor(),
            uid(5),
            command,
            idempotency_key="manual-launch-other",
        )
    assert mismatch.value.error_code is ErrorCode.INVALID_REQUEST

    changed = command.model_copy(
        update={
            "retry_policy": TaskRetryPolicy(
                infra_retry_attempts=1,
                max_total_infra_retries=8,
                initial_backoff_seconds=2,
                maximum_backoff_seconds=30,
                jitter_percent=10,
                content_digest=task_retry_policy_digest(
                    infra_retry_attempts=1,
                    max_total_infra_retries=8,
                    initial_backoff_seconds=2,
                    maximum_backoff_seconds=30,
                    jitter_percent=10,
                ),
            )
        }
    )
    with pytest.raises(ApplicationError) as policy:
        await service.launch(
            _actor(),
            uid(5),
            changed,
            idempotency_key=changed.client_mutation_id,
        )
    assert policy.value.error_code is ErrorCode.CONFLICT
