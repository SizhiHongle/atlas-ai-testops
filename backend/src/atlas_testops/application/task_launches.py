"""Unified TaskPlanVersion triggers and bounded initial TaskRun materialization."""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import product
from typing import cast
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import (
    CheckViolation,
    ForeignKeyViolation,
    ObjectNotInPrerequisiteState,
    RaiseException,
)
from psycopg.rows import DictRow
from pydantic import JsonValue

from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.contracts import WireModel, new_entity_id
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import canonical_digest
from atlas_testops.domain.events import DomainEvent
from atlas_testops.domain.platform import ProjectStatus
from atlas_testops.domain.task import (
    TASK_RETRY_POLICY_DIGEST_KEY,
    TASK_RUN_MANIFEST_SCHEMA_VERSION,
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionQuality,
    ExecutionUnit,
    ExecutionUnitManifest,
    StartTaskPlanVersionRun,
    TaskExecutionEvent,
    TaskPlanStatus,
    TaskPlanVersion,
    TaskRetryPolicy,
    TaskRun,
    TaskRunManifest,
    TaskTriggerSource,
    TriggerTaskPlanVersionRun,
    UnitAttempt,
    execution_unit_dependency_digest,
    execution_unit_key,
    task_run_manifest_hash,
    task_run_manual_trigger_fingerprint,
    task_run_trigger_fingerprint,
    task_run_workflow_id,
    unit_attempt_workflow_id,
)
from atlas_testops.infrastructure.audit import AuditRepository
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.idempotency import (
    CachedHttpResponse,
    IdempotencyRepository,
    hash_request,
)
from atlas_testops.infrastructure.outbox import OutboxRepository
from atlas_testops.infrastructure.repositories.platform import PlatformRepository
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    MAX_INITIAL_EXECUTION_UNITS,
    ImmutableCreateKind,
    ImmutableFactConflictError,
    TaskPlanLaunchBindings,
    TaskRunRepository,
)

TASK_LAUNCH_IDEMPOTENCY_TTL = timedelta(hours=24)
TASK_LAUNCH_EXECUTION_WINDOW = timedelta(minutes=15)
TASK_LAUNCH_COMPILER_VERSION = "0.1.0"
EMPTY_PARAMETER_DIGEST = canonical_digest({})


class TaskPlanLaunchService:
    """Compile one published TaskPlanVersion and atomically seal its first Run."""

    def __init__(
        self,
        database: Database,
        *,
        temporal_namespace: str,
        task_repository: TaskRunRepository | None = None,
        platform_repository: PlatformRepository | None = None,
        state_repository: TaskExecutionStateRepository | None = None,
        audit_repository: AuditRepository | None = None,
        outbox_repository: OutboxRepository | None = None,
        idempotency_repository: IdempotencyRepository | None = None,
    ) -> None:
        self._database = database
        self._temporal_namespace = temporal_namespace
        self._tasks = task_repository or TaskRunRepository()
        self._platform = platform_repository or PlatformRepository()
        self._state = state_repository or TaskExecutionStateRepository()
        self._audit = audit_repository or AuditRepository()
        self._outbox = outbox_repository or OutboxRepository()
        self._idempotency = idempotency_repository or IdempotencyRepository()

    async def launch(
        self,
        actor: ActorContext,
        task_plan_version_id: UUID,
        command: StartTaskPlanVersionRun,
        *,
        idempotency_key: str,
    ) -> CommandResult[TaskRun]:
        """Create an exact Manual Run or replay its stored immutable result."""

        if idempotency_key != command.client_mutation_id:
            raise _invalid_request(
                "Idempotency-Key 必须与 clientMutationId 完全一致。"
            )
        return await self._launch(
            actor,
            task_plan_version_id=task_plan_version_id,
            iteration_id=command.iteration_id,
            retry_policy=command.retry_policy,
            trigger_source=TaskTriggerSource.MANUAL,
            trigger_fingerprint=task_run_manual_trigger_fingerprint(
                task_plan_version_id=task_plan_version_id,
                client_mutation_id=command.client_mutation_id,
            ),
            request_payload={
                "taskPlanVersionId": str(task_plan_version_id),
                **_json_object(command),
            },
            trigger_metadata={},
            idempotency_key=idempotency_key,
        )

    async def trigger(
        self,
        actor: ActorContext,
        command: TriggerTaskPlanVersionRun,
        *,
        idempotency_key: str,
    ) -> CommandResult[TaskRun]:
        """Create one Schedule, CI, or Webhook Run through the same frozen chain."""

        if idempotency_key != command.client_mutation_id:
            raise _invalid_request(
                "Idempotency-Key 必须与 clientMutationId 完全一致。"
            )
        trigger_source = TaskTriggerSource(command.trigger.source)
        return await self._launch(
            actor,
            task_plan_version_id=command.task_plan_version_id,
            iteration_id=command.iteration_id,
            retry_policy=command.retry_policy,
            trigger_source=trigger_source,
            trigger_fingerprint=task_run_trigger_fingerprint(command.trigger),
            request_payload=_json_object(command),
            trigger_metadata=_json_object(command.trigger),
            idempotency_key=idempotency_key,
        )

    async def _launch(
        self,
        actor: ActorContext,
        *,
        task_plan_version_id: UUID,
        iteration_id: str | None,
        retry_policy: TaskRetryPolicy,
        trigger_source: TaskTriggerSource,
        trigger_fingerprint: str,
        request_payload: dict[str, JsonValue],
        trigger_metadata: dict[str, JsonValue],
        idempotency_key: str,
    ) -> CommandResult[TaskRun]:
        """Compile, persist, seal, and emit one source-independent TaskRun."""

        request_hash = hash_request(request_payload)
        scope = (
            f"task-plan-versions.{task_plan_version_id}.runs."
            f"{trigger_source.value.lower()}"
        )
        try:
            async with self._database.transaction(actor.database_context()) as connection:
                version = await self._tasks.get_task_plan_version(
                    connection,
                    task_plan_version_id,
                )
                if version is None or not actor.can_read_project(version.project_id):
                    raise _not_found()
                plan = await self._tasks.get_task_plan(
                    connection,
                    version.task_plan_id,
                    for_share=True,
                )
                project = await self._platform.get_project_for_share(
                    connection,
                    version.project_id,
                )
                if (
                    plan is None
                    or project is None
                    or plan.tenant_id != version.tenant_id
                    or plan.project_id != version.project_id
                    or project.tenant_id != version.tenant_id
                ):
                    raise _not_found()
                operator_id = _require_operator(actor, version.project_id)
                now = await _database_now(connection)
                reservation = await self._idempotency.reserve(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    now=now,
                    ttl=TASK_LAUNCH_IDEMPOTENCY_TTL,
                )
                if reservation.cached_response is not None:
                    return CommandResult(
                        value=TaskRun.model_validate(
                            reservation.cached_response.body
                        ),
                        status_code=reservation.cached_response.status_code,
                        replayed=True,
                    )
                if project.status is not ProjectStatus.ACTIVE:
                    raise _conflict("只有活动 Project 可以启动 TaskRun。")
                if plan.status is not TaskPlanStatus.ACTIVE:
                    raise _conflict("已归档 TaskPlan 不能启动新的 TaskRun。")
                if (
                    version.policy_digests.get(TASK_RETRY_POLICY_DIGEST_KEY)
                    != retry_policy.content_digest
                ):
                    raise _conflict(
                        "retryPolicy 必须与已发布 TaskPlanVersion 的 "
                        "infra-retry 策略摘要完全一致。"
                    )
                bindings = await self._tasks.get_task_plan_launch_bindings(
                    connection,
                    tenant_id=version.tenant_id,
                    project_id=version.project_id,
                    identity_profile_version_ids=(
                        version.matrix.identity_profile_version_ids
                    ),
                    data_profile_version_ids=version.matrix.data_profile_version_ids,
                )
                manifest_units = compile_task_plan_version(
                    version,
                    bindings,
                    maximum_units=MAX_INITIAL_EXECUTION_UNITS,
                )
                aggregate = _build_initial_run(
                    version=version,
                    iteration_id=iteration_id,
                    retry_policy=retry_policy,
                    trigger_source=trigger_source,
                    trigger_fingerprint=trigger_fingerprint,
                    requested_by=operator_id,
                    temporal_namespace=self._temporal_namespace,
                    manifest_units=manifest_units,
                    now=now,
                )
                result = await self._tasks.create_run(
                    connection,
                    task_run=aggregate.run,
                    manifest=aggregate.manifest,
                    units=aggregate.units,
                    first_attempts=aggregate.first_attempts,
                )
                created = result.kind is ImmutableCreateKind.CREATED
                if created:
                    await self._record_created(
                        connection,
                        actor=actor,
                        run=result.task_run,
                        version=version,
                        occurred_at=now,
                        unit_count=len(manifest_units),
                        trigger_metadata=trigger_metadata,
                    )
                response = CachedHttpResponse(
                    status_code=201 if created else 200,
                    body=_json_object(result.task_run),
                )
                await self._idempotency.complete(
                    connection,
                    tenant_id=actor.tenant_id,
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    response=response,
                )
                return CommandResult(
                    value=result.task_run,
                    status_code=response.status_code,
                    replayed=not created,
                )
        except ImmutableFactConflictError as error:
            raise _conflict(
                f"{trigger_source.value} Trigger 已绑定到不同的不可变 Run 输入。"
            ) from error
        except (
            CheckViolation,
            ForeignKeyViolation,
            ObjectNotInPrerequisiteState,
            RaiseException,
        ) as error:
            raise _conflict(
                "TaskPlanVersion 的 Case、Profile、Fixture 或 Environment "
                "已失效，无法通过 TaskRun 物化门禁。"
            ) from error

    async def _record_created(
        self,
        connection: AsyncConnection[DictRow],
        *,
        actor: ActorContext,
        run: TaskRun,
        version: TaskPlanVersion,
        occurred_at: datetime,
        unit_count: int,
        trigger_metadata: dict[str, JsonValue],
    ) -> None:
        """Append the initial execution event, audit fact, and outbox event."""

        assert run.request_digest is not None
        payload: dict[str, JsonValue] = {
            "taskPlanId": str(version.task_plan_id),
            "taskPlanVersionId": str(version.id),
            "taskPlanVersionDigest": version.content_digest,
            "triggerSource": run.trigger_source.value,
            "trigger": trigger_metadata,
            "unitCount": unit_count,
            "manifestHash": run.manifest_hash,
            "requestDigest": run.request_digest,
        }
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
                event_type="task_run.requested",
                lifecycle=run.lifecycle,
                quality=run.quality,
                hygiene=run.hygiene,
                payload=payload,
                occurred_at=occurred_at,
            ),
        )
        await self._audit.append(
            connection,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            environment_id=None,
            actor_id=actor.actor_id,
            event_type="task_run.requested",
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
                event_type="task_run.requested",
                occurred_at=occurred_at,
                payload=payload,
            ),
        )


class _InitialTaskRunAggregate:
    """Complete aggregate passed to the bounded synchronous repository path."""

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


def compile_task_plan_version(
    version: TaskPlanVersion,
    bindings: TaskPlanLaunchBindings,
    *,
    maximum_units: int,
) -> tuple[ExecutionUnitManifest, ...]:
    """Compile compatible matrix cells without creating invalid cross-case pairs."""

    expected_identity_ids = set(version.matrix.identity_profile_version_ids)
    expected_data_ids = set(version.matrix.data_profile_version_ids)
    if set(bindings.identity_case_by_id) != expected_identity_ids:
        raise _conflict(
            "TaskPlanVersion 包含已失效或不属于当前范围的 IdentityProfileVersion。"
        )
    if set(bindings.data_blueprint_by_id) != expected_data_ids:
        raise _conflict(
            "TaskPlanVersion 包含已失效或不属于当前范围的 DataProfileVersion。"
        )

    identities_by_case: dict[UUID, list[UUID]] = {}
    for identity_id, case_version_id in bindings.identity_case_by_id.items():
        identities_by_case.setdefault(case_version_id, []).append(identity_id)
    data_by_blueprint: dict[UUID, list[UUID]] = {}
    for data_id, blueprint_version_id in bindings.data_blueprint_by_id.items():
        data_by_blueprint.setdefault(blueprint_version_id, []).append(data_id)

    candidates: list[ExecutionUnitManifest] = []
    for case_profile in version.profile_refs.case_profiles:
        identity_ids = sorted(
            identities_by_case.get(case_profile.case_version_id, ()),
            key=str,
        )
        data_ids = sorted(
            data_by_blueprint.get(case_profile.fixture_blueprint_version_id, ()),
            key=str,
        )
        if not identity_ids:
            raise _conflict(
                "TaskPlanVersion 中至少一个 CaseVersion 没有兼容的 IdentityProfileVersion。"
            )
        if not data_ids:
            raise _conflict(
                "TaskPlanVersion 中至少一个 Fixture 没有兼容的 DataProfileVersion。"
            )
        for environment_id, browser_id, identity_id, data_id in product(
            version.matrix.environment_ids,
            version.matrix.browser_profile_version_ids,
            identity_ids,
            data_ids,
        ):
            unit_key = execution_unit_key(
                case_version_id=case_profile.case_version_id,
                environment_id=environment_id,
                browser_profile_version_id=browser_id,
                identity_profile_version_id=identity_id,
                data_profile_version_id=data_id,
                parameter_digest=EMPTY_PARAMETER_DIGEST,
            )
            candidates.append(
                ExecutionUnitManifest(
                    ordinal=1,
                    unit_key=unit_key,
                    case_version_id=case_profile.case_version_id,
                    execution_profile_version_id=(
                        case_profile.execution_profile_version_id
                    ),
                    fixture_blueprint_version_id=(
                        case_profile.fixture_blueprint_version_id
                    ),
                    identity_profile_version_id=identity_id,
                    environment_id=environment_id,
                    browser_profile_version_id=browser_id,
                    data_profile_version_id=data_id,
                    parameter_digest=EMPTY_PARAMETER_DIGEST,
                    dependency_digest=execution_unit_dependency_digest(
                        case_version_id=case_profile.case_version_id,
                        execution_profile_version_id=(
                            case_profile.execution_profile_version_id
                        ),
                        fixture_blueprint_version_id=(
                            case_profile.fixture_blueprint_version_id
                        ),
                        identity_profile_version_id=identity_id,
                        environment_id=environment_id,
                        browser_profile_version_id=browser_id,
                        data_profile_version_id=data_id,
                    ),
                )
            )
            if len(candidates) > maximum_units:
                raise _conflict(
                    "Manual Launch 编译后的 ExecutionUnit 超过同步物化上限 "
                    f"{maximum_units}。"
                )
    ordered = sorted(candidates, key=lambda item: item.unit_key)
    if len({item.unit_key for item in ordered}) != len(ordered):
        raise _conflict("TaskPlanVersion 编译产生了重复 ExecutionUnit。")
    return tuple(
        item.model_copy(update={"ordinal": ordinal})
        for ordinal, item in enumerate(ordered, start=1)
    )


def _build_initial_run(
    *,
    version: TaskPlanVersion,
    iteration_id: str | None,
    retry_policy: TaskRetryPolicy,
    trigger_source: TaskTriggerSource,
    trigger_fingerprint: str,
    requested_by: UUID,
    temporal_namespace: str,
    manifest_units: tuple[ExecutionUnitManifest, ...],
    now: datetime,
) -> _InitialTaskRunAggregate:
    run_id = new_entity_id()
    manifest_hash = task_run_manifest_hash(
        task_run_id=run_id,
        task_plan_version_id=version.id,
        trigger_source=trigger_source,
        trigger_fingerprint=trigger_fingerprint,
        tenant_id=version.tenant_id,
        project_id=version.project_id,
        iteration_id=iteration_id,
        units=manifest_units,
        policy_digests=version.policy_digests,
        compiler_version=TASK_LAUNCH_COMPILER_VERSION,
        schema_version=TASK_RUN_MANIFEST_SCHEMA_VERSION,
        retry_policy=retry_policy,
    )
    manifest = TaskRunManifest(
        schema_version=TASK_RUN_MANIFEST_SCHEMA_VERSION,
        task_run_id=run_id,
        task_plan_version_id=version.id,
        trigger_source=trigger_source,
        trigger_fingerprint=trigger_fingerprint,
        tenant_id=version.tenant_id,
        project_id=version.project_id,
        iteration_id=iteration_id,
        units=manifest_units,
        policy_digests=version.policy_digests,
        retry_policy=retry_policy,
        compiler_version=TASK_LAUNCH_COMPILER_VERSION,
        manifest_hash=manifest_hash,
    )
    run = TaskRun(
        id=run_id,
        tenant_id=version.tenant_id,
        project_id=version.project_id,
        task_plan_version_id=version.id,
        manifest_hash=manifest_hash,
        trigger_source=trigger_source,
        trigger_fingerprint=trigger_fingerprint,
        request_digest=manifest.recompute_request_digest(),
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        requested_by=requested_by,
        temporal_namespace=temporal_namespace,
        temporal_workflow_id=task_run_workflow_id(
            tenant_id=version.tenant_id,
            task_run_id=run_id,
        ),
        requested_at=now,
        queued_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    units: list[ExecutionUnit] = []
    attempts: list[UnitAttempt] = []
    for manifest_unit in manifest_units:
        unit_id = new_entity_id()
        unit = ExecutionUnit(
            id=unit_id,
            tenant_id=version.tenant_id,
            project_id=version.project_id,
            task_run_id=run_id,
            manifest_hash=manifest_hash,
            lifecycle=ExecutionLifecycle.QUEUED,
            quality=ExecutionQuality.PENDING,
            hygiene=ExecutionHygiene.PENDING,
            revision=1,
            created_at=now,
            updated_at=now,
            **manifest_unit.model_dump(mode="python"),
        )
        attempt_id = new_entity_id()
        attempt = UnitAttempt(
            id=attempt_id,
            tenant_id=version.tenant_id,
            project_id=version.project_id,
            task_run_id=run_id,
            execution_unit_id=unit_id,
            manifest_hash=manifest_hash,
            unit_key=unit.unit_key,
            case_version_id=unit.case_version_id,
            attempt_number=1,
            lifecycle=ExecutionLifecycle.QUEUED,
            quality=ExecutionQuality.PENDING,
            hygiene=ExecutionHygiene.PENDING,
            temporal_namespace=temporal_namespace,
            temporal_workflow_id=unit_attempt_workflow_id(
                tenant_id=version.tenant_id,
                unit_attempt_id=attempt_id,
            ),
            queued_at=now,
            execution_deadline=now + TASK_LAUNCH_EXECUTION_WINDOW,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        units.append(unit)
        attempts.append(attempt)
    return _InitialTaskRunAggregate(
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


def _require_operator(actor: ActorContext, project_id: UUID) -> UUID:
    if not actor.can_operate_project(project_id):
        raise _forbidden()
    if actor.actor_id is None:
        raise _forbidden()
    return actor.actor_id


def _json_object(model: WireModel) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], model.model_dump(mode="json", by_alias=True))


def _not_found() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.NOT_FOUND,
        title="资源不存在",
        detail="TaskPlanVersion 不存在或不可见。",
        status_code=404,
    )


def _forbidden() -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.FORBIDDEN,
        title="TaskRun Trigger 被拒绝",
        detail="当前身份不能运行该 Project，或缺少可审计 Actor。",
        status_code=403,
    )


def _invalid_request(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.INVALID_REQUEST,
        title="TaskRun Trigger 请求无效",
        detail=detail,
        status_code=400,
    )


def _conflict(detail: str) -> ApplicationError:
    return ApplicationError(
        error_code=ErrorCode.CONFLICT,
        title="TaskRun Trigger 冲突",
        detail=detail,
        status_code=409,
    )
