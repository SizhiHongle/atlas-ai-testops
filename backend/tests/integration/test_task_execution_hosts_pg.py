"""Real PostgreSQL coverage for formal Task execution hosts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from os import environ
from typing import cast
from uuid import UUID, uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import JsonValue, SecretStr
from tests.integration.test_cases_api import (
    RecordingDebugRunDispatcher,
    actor_headers,
    bootstrap_case_role,
    bootstrap_environment,
    bootstrap_project,
    case_payload_with_exact_bindings,
    mark_debug_run_passed,
    seed_published_case_blueprint,
)

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.task_execution import TaskAdmissionService
from atlas_testops.core.config import Settings
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.case import CaseVersion, DebugRun, canonical_digest
from atlas_testops.domain.runtime import ModelExecutionProfile, ToolExecutionProfile, Viewport
from atlas_testops.domain.task import (
    BrowserProfileVersion,
    CaseExecutionProfileRef,
    DataProfileVersion,
    ExecutionHygiene,
    ExecutionLifecycle,
    ExecutionProfileVersion,
    ExecutionQuality,
    ExecutionUnit,
    ExecutionUnitManifest,
    IdentityActorBinding,
    IdentityProfileVersion,
    TaskExecutionEvent,
    TaskMaterializationState,
    TaskMatrixDefinition,
    TaskPlan,
    TaskPlanStatus,
    TaskPlanVersion,
    TaskProfileRefs,
    TaskProfileStatus,
    TaskRun,
    TaskRunManifest,
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
    task_plan_version_content_digest,
    task_plan_version_ref,
    task_run_manifest_hash,
    task_run_workflow_id,
    unit_attempt_workflow_id,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_profiles import (
    TaskExecutionStateRepository,
    TaskProfileRepository,
)
from atlas_testops.infrastructure.repositories.task_runs import (
    ImmutableCreateKind,
    ImmutableFactConflictError,
    TaskRunRepository,
)
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]

POLICY_DIGEST = f"sha256:{'a' * 64}"
PARAMETER_DIGEST = f"sha256:{'b' * 64}"
TASK_TABLES = (
    "browser_profile_version",
    "data_profile_version",
    "execution_profile_version",
    "identity_profile_actor_binding",
    "identity_profile_version",
    "task_plan",
    "task_plan_version",
    "task_run",
    "task_run_manifest",
    "task_workflow_identity_registry",
    "task_workflow_start_intent",
    "execution_unit",
    "unit_attempt",
    "task_run_event",
)


@dataclass(frozen=True, slots=True)
class SeededCaseVersion:
    """Exact published CaseVersion dependencies used by a Task manifest."""

    tenant_id: UUID
    project_id: UUID
    other_project_id: UUID
    other_tenant_id: UUID
    actor_id: UUID
    environment_id: UUID
    case_version_id: UUID
    case_version: CaseVersion
    execution_profile_version_id: UUID
    fixture_blueprint_version_id: UUID
    fixture_blueprint_version_ref: str
    fixture_blueprint_content_digest: str
    fixture_plan_digest: str


@dataclass(frozen=True, slots=True)
class TaskAggregate:
    """One complete initial Task execution aggregate."""

    plan: TaskPlan
    execution_profile: ExecutionProfileVersion
    identity_profile: IdentityProfileVersion
    browser_profile: BrowserProfileVersion
    data_profile: DataProfileVersion
    version: TaskPlanVersion
    run: TaskRun
    manifest: TaskRunManifest
    unit: ExecutionUnit
    attempt: UnitAttempt


def test_task_execution_hosts_preserve_chain_replays_and_isolation() -> None:
    """Exercise the complete host chain, retries, events, RLS, and immutability."""

    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)
    aggregate, second_attempt, first_event = asyncio.run(_exercise_repository(settings, seeded))

    _assert_immutable_bindings(seeded, aggregate, second_attempt, first_event)


def test_task_execution_host_tables_force_rls_and_deny_delete() -> None:
    """Keep every Task host fail-closed and outside the app role's delete surface."""

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        rows = connection.execute(
            """
            select class.relname, class.relrowsecurity, class.relforcerowsecurity,
                   has_table_privilege('atlas_app', class.oid, 'DELETE')
            from pg_class class
            join pg_namespace namespace on namespace.oid = class.relnamespace
            where namespace.nspname = 'atlas'
              and class.relname = any(%s)
            order by class.relname
            """,
            (list(TASK_TABLES),),
        ).fetchall()

    assert tuple(row[0] for row in rows) == tuple(sorted(TASK_TABLES))
    assert all(row[1] is True and row[2] is True for row in rows)
    assert all(row[3] is False for row in rows)


def _seed_published_case_version(settings: Settings) -> SeededCaseVersion:
    """Publish one real CaseVersion through the trusted API and Runtime gates."""

    assert DATABASE_URL is not None
    suffix = uuid7().hex[-10:]
    application = create_app(
        settings,
        debug_run_dispatcher=RecordingDebugRunDispatcher(),
    )
    with TestClient(application) as client:
        tenant_id, project_id, author_headers = bootstrap_project(client, suffix)
        reviewer_headers = actor_headers(tenant_id)
        other_project = client.post(
            "/v1/projects",
            headers={
                **author_headers,
                "Idempotency-Key": f"task-other-project-{suffix}",
            },
            json={
                "projectKey": f"TASK_OTHER_{suffix.upper()}",
                "name": "Task Other Project",
            },
        )
        assert other_project.status_code == 201, other_project.text
        other_tenant_id, _, _ = bootstrap_project(client, f"t{suffix}")
        environment_id = bootstrap_environment(
            client,
            project_id,
            author_headers,
            suffix,
            allowed_origins=["https://staging.example.test"],
        )
        role = bootstrap_case_role(client, project_id, author_headers, suffix)
        blueprint_version_id, blueprint_version_ref, blueprint_digest = (
            seed_published_case_blueprint(
                tenant_id=tenant_id,
                project_id=project_id,
                environment_id=environment_id,
                published_by=author_headers["X-Atlas-Actor-ID"],
                suffix=suffix,
            )
        )
        created = client.post(
            f"/v1/projects/{project_id}/test-cases",
            headers={
                **author_headers,
                "Idempotency-Key": f"task-case-{suffix}",
            },
            json=case_payload_with_exact_bindings(
                f"T{suffix}",
                role=role,
                blueprint_version_id=blueprint_version_id,
                blueprint_version_ref=blueprint_version_ref,
                blueprint_digest=blueprint_digest,
            ),
        )
        assert created.status_code == 201, created.text
        case_id = cast(str, created.json()["id"])
        draft = client.get(
            f"/v1/test-cases/{case_id}/workflow-draft",
            headers=author_headers,
        )
        assert draft.status_code == 200, draft.text
        started = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/debug-runs",
            headers={
                **author_headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": f"task-debug-{suffix}",
            },
            json={
                "environmentId": environment_id,
                "baseSemanticRevision": 1,
                "executionDeadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            },
        )
        assert started.status_code == 202, started.text
        debug_run = DebugRun.model_validate(started.json())
        mark_debug_run_passed(
            client=client,
            tenant_id=tenant_id,
            project_id=project_id,
            environment_id=environment_id,
            headers=author_headers,
            role=role,
            blueprint_version_id=blueprint_version_id,
            run=debug_run,
            suffix=suffix,
        )
        publish_mutation = f"task-publish-{suffix}"
        published = client.post(
            f"/v1/test-cases/{case_id}:publish",
            headers={
                **reviewer_headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": publish_mutation,
            },
            json={
                "clientMutationId": publish_mutation,
                "version": "1.0.0",
                "baseSemanticRevision": 1,
                "debugRunId": str(debug_run.id),
                "reviewSummary": "Reviewer approved the Task host integration fixture.",
            },
        )
        assert published.status_code == 201, published.text
        case_version = CaseVersion.model_validate(published.json())
        case_version_id = case_version.id

    return SeededCaseVersion(
        tenant_id=UUID(tenant_id),
        project_id=UUID(project_id),
        other_project_id=UUID(cast(str, other_project.json()["id"])),
        other_tenant_id=UUID(other_tenant_id),
        actor_id=UUID(author_headers["X-Atlas-Actor-ID"]),
        environment_id=UUID(environment_id),
        case_version_id=case_version_id,
        case_version=case_version,
        execution_profile_version_id=uuid7(),
        fixture_blueprint_version_id=UUID(blueprint_version_id),
        fixture_blueprint_version_ref=blueprint_version_ref,
        fixture_blueprint_content_digest=blueprint_digest,
        fixture_plan_digest=f"sha256:{'c' * 64}",
    )


def _build_aggregate(seeded: SeededCaseVersion) -> TaskAggregate:
    """Build one valid immutable manifest and its initial persistence projections."""

    now = datetime.now(UTC)
    plan = TaskPlan(
        id=uuid7(),
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        task_key=f"task.integration-{uuid7().hex[-8:]}",
        name="Task execution host integration",
        status=TaskPlanStatus.ACTIVE,
        created_by=seeded.actor_id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    identity_profile_version_id = uuid7()
    browser_profile_version_id = uuid7()
    data_profile_version_id = uuid7()
    model_profile = ModelExecutionProfile(
        model_profile_ref="model.integration@1.0.0",
        prompt_bundle_ref="prompt.integration@1.0.0",
        reasoning_policy_ref="reasoning.integration@1.0.0",
    )
    tool_profile = ToolExecutionProfile(
        tool_catalog_ref="tools.integration@1.0.0",
        mcp_server_manifest_digest=f"sha256:{'d' * 64}",
        tool_schema_digest=f"sha256:{'e' * 64}",
        policy_bundle_ref="policy.integration@1.0.0",
        policy_digest=f"sha256:{'f' * 64}",
    )
    execution_profile_key = "task.execution"
    execution_profile_digest = execution_profile_content_digest(
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        profile_key=execution_profile_key,
        version="1.0.0",
        case_version_id=seeded.case_version_id,
        case_content_digest=seeded.case_version.content_digest,
        test_ir_digest=seeded.case_version.test_ir_digest,
        plan_digest=seeded.case_version.plan_digest,
        compiled_digest=seeded.case_version.compiled_digest,
        model=model_profile,
        tools=tool_profile,
        supported_features=seeded.case_version.test_ir.required_features,
    )
    execution_profile = ExecutionProfileVersion(
        id=seeded.execution_profile_version_id,
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        profile_key=execution_profile_key,
        version="1.0.0",
        version_ref=execution_profile_version_ref(execution_profile_key, "1.0.0"),
        status=TaskProfileStatus.PUBLISHED,
        content_digest=execution_profile_digest,
        case_version_id=seeded.case_version_id,
        case_content_digest=seeded.case_version.content_digest,
        test_ir_digest=seeded.case_version.test_ir_digest,
        plan_digest=seeded.case_version.plan_digest,
        compiled_digest=seeded.case_version.compiled_digest,
        model=model_profile,
        tools=tool_profile,
        supported_features=seeded.case_version.test_ir.required_features,
        published_by=seeded.actor_id,
        published_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    identity_actors = tuple(
        IdentityActorBinding.model_validate(actor.model_dump(mode="python"))
        for actor in seeded.case_version.test_ir.actors
    )
    identity_profile_key = "task.identity"
    identity_profile_digest = identity_profile_content_digest(
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        profile_key=identity_profile_key,
        version="1.0.0",
        case_version_id=seeded.case_version_id,
        case_content_digest=seeded.case_version.content_digest,
        actors=identity_actors,
    )
    identity_profile = IdentityProfileVersion(
        id=identity_profile_version_id,
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        profile_key=identity_profile_key,
        version="1.0.0",
        version_ref=identity_profile_version_ref(identity_profile_key, "1.0.0"),
        status=TaskProfileStatus.PUBLISHED,
        content_digest=identity_profile_digest,
        case_version_id=seeded.case_version_id,
        case_content_digest=seeded.case_version.content_digest,
        actors=identity_actors,
        published_by=seeded.actor_id,
        published_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    viewport = Viewport(width=1440, height=900, device_scale_factor=1.0)
    browser_profile_key = "task.browser"
    runtime_image_digest = f"sha256:{'1' * 64}"
    capability_digest = f"sha256:{'2' * 64}"
    browser_profile_digest = browser_profile_content_digest(
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        profile_key=browser_profile_key,
        version="1.0.0",
        engine="chromium",
        revision="integration-chromium-1",
        viewport=viewport,
        locale="en-US",
        timezone="UTC",
        runtime_image_digest=runtime_image_digest,
        capability_digest=capability_digest,
    )
    browser_profile = BrowserProfileVersion(
        id=browser_profile_version_id,
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        profile_key=browser_profile_key,
        version="1.0.0",
        version_ref=browser_profile_version_ref(browser_profile_key, "1.0.0"),
        status=TaskProfileStatus.PUBLISHED,
        content_digest=browser_profile_digest,
        engine="chromium",
        browser_revision="integration-chromium-1",
        viewport=viewport,
        locale="en-US",
        timezone="UTC",
        runtime_image_digest=runtime_image_digest,
        capability_digest=capability_digest,
        published_by=seeded.actor_id,
        published_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    data_profile_key = "task.data"
    run_inputs: dict[str, JsonValue] = {}
    input_digest = canonical_digest(run_inputs)
    data_profile_digest = data_profile_content_digest(
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        profile_key=data_profile_key,
        version="1.0.0",
        blueprint_version_id=seeded.fixture_blueprint_version_id,
        blueprint_version_ref=seeded.fixture_blueprint_version_ref,
        blueprint_content_digest=seeded.fixture_blueprint_content_digest,
        plan_digest=seeded.fixture_plan_digest,
        run_inputs=run_inputs,
        input_digest=input_digest,
    )
    data_profile = DataProfileVersion(
        id=data_profile_version_id,
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        profile_key=data_profile_key,
        version="1.0.0",
        version_ref=data_profile_version_ref(data_profile_key, "1.0.0"),
        status=TaskProfileStatus.PUBLISHED,
        content_digest=data_profile_digest,
        blueprint_version_id=seeded.fixture_blueprint_version_id,
        blueprint_version_ref=seeded.fixture_blueprint_version_ref,
        blueprint_content_digest=seeded.fixture_blueprint_content_digest,
        plan_digest=seeded.fixture_plan_digest,
        run_inputs=run_inputs,
        input_digest=input_digest,
        published_by=seeded.actor_id,
        published_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    matrix = TaskMatrixDefinition(
        environment_ids=(seeded.environment_id,),
        browser_profile_version_ids=(browser_profile_version_id,),
        identity_profile_version_ids=(identity_profile_version_id,),
        data_profile_version_ids=(data_profile_version_id,),
    )
    profile_refs = TaskProfileRefs(
        case_profiles=(
            CaseExecutionProfileRef(
                case_version_id=seeded.case_version_id,
                execution_profile_version_id=seeded.execution_profile_version_id,
                fixture_blueprint_version_id=seeded.fixture_blueprint_version_id,
            ),
        )
    )
    version_id = uuid7()
    version_digest = task_plan_version_content_digest(
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        task_plan_id=plan.id,
        version="1.0.0",
        pinned_case_version_ids=(seeded.case_version_id,),
        matrix=matrix,
        profile_refs=profile_refs,
        policy_digests={"gate": POLICY_DIGEST},
    )
    version = TaskPlanVersion(
        id=version_id,
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        task_plan_id=plan.id,
        version="1.0.0",
        version_ref=task_plan_version_ref(plan.id, "1.0.0"),
        pinned_case_version_ids=(seeded.case_version_id,),
        matrix=matrix,
        profile_refs=profile_refs,
        policy_digests={"gate": POLICY_DIGEST},
        content_digest=version_digest,
        published_by=seeded.actor_id,
        published_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    unit_key = execution_unit_key(
        case_version_id=seeded.case_version_id,
        environment_id=seeded.environment_id,
        browser_profile_version_id=browser_profile_version_id,
        identity_profile_version_id=identity_profile_version_id,
        data_profile_version_id=data_profile_version_id,
        parameter_digest=PARAMETER_DIGEST,
    )
    dependency_digest = execution_unit_dependency_digest(
        case_version_id=seeded.case_version_id,
        execution_profile_version_id=seeded.execution_profile_version_id,
        fixture_blueprint_version_id=seeded.fixture_blueprint_version_id,
        identity_profile_version_id=identity_profile_version_id,
        environment_id=seeded.environment_id,
        browser_profile_version_id=browser_profile_version_id,
        data_profile_version_id=data_profile_version_id,
    )
    manifest_unit = ExecutionUnitManifest(
        ordinal=1,
        unit_key=unit_key,
        case_version_id=seeded.case_version_id,
        execution_profile_version_id=seeded.execution_profile_version_id,
        fixture_blueprint_version_id=seeded.fixture_blueprint_version_id,
        identity_profile_version_id=identity_profile_version_id,
        environment_id=seeded.environment_id,
        browser_profile_version_id=browser_profile_version_id,
        data_profile_version_id=data_profile_version_id,
        parameter_digest=PARAMETER_DIGEST,
        dependency_digest=dependency_digest,
    )
    run_id = uuid7()
    trigger_fingerprint = f"integration:task:{run_id}"
    manifest_hash = task_run_manifest_hash(
        task_run_id=run_id,
        task_plan_version_id=version.id,
        trigger_source=TaskTriggerSource.API,
        trigger_fingerprint=trigger_fingerprint,
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        iteration_id="integration:2026-07",
        units=(manifest_unit,),
        policy_digests={"gate": POLICY_DIGEST},
        compiler_version="0.1.0",
    )
    manifest = TaskRunManifest(
        task_run_id=run_id,
        task_plan_version_id=version.id,
        trigger_source=TaskTriggerSource.API,
        trigger_fingerprint=trigger_fingerprint,
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        iteration_id="integration:2026-07",
        units=(manifest_unit,),
        policy_digests={"gate": POLICY_DIGEST},
        compiler_version="0.1.0",
        manifest_hash=manifest_hash,
    )
    run = TaskRun(
        id=run_id,
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        task_plan_version_id=version.id,
        manifest_hash=manifest_hash,
        trigger_source=TaskTriggerSource.API,
        trigger_fingerprint=trigger_fingerprint,
        request_digest=manifest.recompute_request_digest(),
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        requested_by=seeded.actor_id,
        temporal_namespace="default",
        temporal_workflow_id=task_run_workflow_id(
            tenant_id=seeded.tenant_id,
            task_run_id=run_id,
        ),
        requested_at=now,
        queued_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    unit = ExecutionUnit(
        id=uuid7(),
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
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
    attempt_id = uuid7()
    attempt = UnitAttempt(
        id=attempt_id,
        tenant_id=seeded.tenant_id,
        project_id=seeded.project_id,
        task_run_id=run.id,
        execution_unit_id=unit.id,
        manifest_hash=manifest_hash,
        unit_key=unit.unit_key,
        case_version_id=unit.case_version_id,
        attempt_number=1,
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        temporal_namespace=run.temporal_namespace,
        temporal_workflow_id=unit_attempt_workflow_id(
            tenant_id=seeded.tenant_id,
            unit_attempt_id=attempt_id,
        ),
        queued_at=now,
        execution_deadline=now + timedelta(minutes=15),
        revision=1,
        created_at=now,
        updated_at=now,
    )
    return TaskAggregate(
        plan,
        execution_profile,
        identity_profile,
        browser_profile,
        data_profile,
        version,
        run,
        manifest,
        unit,
        attempt,
    )


def _build_incomplete_materialization(
    aggregate: TaskAggregate,
) -> tuple[TaskRun, TaskRunManifest]:
    """Build a canonical Run/Manifest pair whose child materialization is absent."""

    now = datetime.now(UTC)
    run_id = uuid7()
    trigger_fingerprint = f"integration:incomplete:{run_id}"
    manifest_hash = task_run_manifest_hash(
        task_run_id=run_id,
        task_plan_version_id=aggregate.version.id,
        trigger_source=TaskTriggerSource.API,
        trigger_fingerprint=trigger_fingerprint,
        tenant_id=aggregate.run.tenant_id,
        project_id=aggregate.run.project_id,
        iteration_id=aggregate.manifest.iteration_id,
        units=aggregate.manifest.units,
        policy_digests=aggregate.manifest.policy_digests,
        compiler_version=aggregate.manifest.compiler_version,
    )
    manifest = TaskRunManifest(
        task_run_id=run_id,
        task_plan_version_id=aggregate.version.id,
        trigger_source=TaskTriggerSource.API,
        trigger_fingerprint=trigger_fingerprint,
        tenant_id=aggregate.run.tenant_id,
        project_id=aggregate.run.project_id,
        iteration_id=aggregate.manifest.iteration_id,
        units=aggregate.manifest.units,
        policy_digests=aggregate.manifest.policy_digests,
        compiler_version=aggregate.manifest.compiler_version,
        manifest_hash=manifest_hash,
    )
    run = TaskRun(
        id=run_id,
        tenant_id=aggregate.run.tenant_id,
        project_id=aggregate.run.project_id,
        task_plan_version_id=aggregate.version.id,
        manifest_hash=manifest_hash,
        trigger_source=TaskTriggerSource.API,
        trigger_fingerprint=trigger_fingerprint,
        request_digest=manifest.recompute_request_digest(),
        materialization_state=TaskMaterializationState.MATERIALIZING,
        lifecycle=ExecutionLifecycle.QUEUED,
        quality=ExecutionQuality.PENDING,
        hygiene=ExecutionHygiene.PENDING,
        requested_by=aggregate.run.requested_by,
        temporal_namespace=aggregate.run.temporal_namespace,
        temporal_workflow_id=task_run_workflow_id(
            tenant_id=aggregate.run.tenant_id,
            task_run_id=run_id,
        ),
        requested_at=now,
        queued_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    return run, manifest


async def _exercise_repository(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> tuple[TaskAggregate, UnitAttempt, TaskExecutionEvent]:
    """Persist and replay a full Task execution history through the repository."""

    database = Database(settings)
    repository = TaskRunRepository()
    profile_repository = TaskProfileRepository()
    state_repository = TaskExecutionStateRepository()
    aggregate = _build_aggregate(seeded)
    requested_run = aggregate.run
    context = DatabaseContext(
        tenant_id=seeded.tenant_id,
        actor_id=seeded.actor_id,
        request_id=f"task-host:{aggregate.run.id}",
    )
    await database.open()
    try:
        async with database.transaction(context) as connection:
            plan_result = await repository.create_task_plan(connection, aggregate.plan)
            execution_profile_result = (
                await profile_repository.create_execution_profile_version(
                    connection,
                    aggregate.execution_profile,
                )
            )
            identity_profile_result = (
                await profile_repository.create_identity_profile_version(
                    connection,
                    aggregate.identity_profile,
                )
            )
            browser_profile_result = (
                await profile_repository.create_browser_profile_version(
                    connection,
                    aggregate.browser_profile,
                )
            )
            data_profile_result = await profile_repository.create_data_profile_version(
                connection,
                aggregate.data_profile,
            )
            version_result = await repository.create_task_plan_version(
                connection,
                aggregate.version,
            )
            run_result = await repository.create_run(
                connection,
                task_run=aggregate.run,
                manifest=aggregate.manifest,
                units=(aggregate.unit,),
                first_attempts=(aggregate.attempt,),
            )
            assert plan_result.kind is ImmutableCreateKind.CREATED
            assert execution_profile_result.kind is ImmutableCreateKind.CREATED
            assert identity_profile_result.kind is ImmutableCreateKind.CREATED
            assert browser_profile_result.kind is ImmutableCreateKind.CREATED
            assert data_profile_result.kind is ImmutableCreateKind.CREATED
            assert version_result.kind is ImmutableCreateKind.CREATED
            assert run_result.kind is ImmutableCreateKind.CREATED
            assert run_result.manifest == aggregate.manifest
            aggregate = replace(aggregate, run=run_result.task_run)

        admission = TaskAdmissionService(database)
        admitted = await admission.admit_unit(
            ActorContext(
                tenant_id=seeded.tenant_id,
                actor_id=seeded.actor_id,
                request_id=f"task-admission:{aggregate.unit.id}",
                current_project_id=seeded.project_id,
                grants=(
                    AccessGrant(
                        role=PlatformRole.RUN_OPERATOR,
                        project_id=seeded.project_id,
                    ),
                ),
            ),
            aggregate.unit.id,
        )
        assert admitted.execution_profile == aggregate.execution_profile
        assert admitted.identity_profile == aggregate.identity_profile
        assert admitted.browser_profile == aggregate.browser_profile
        assert admitted.data_profile == aggregate.data_profile

        async with database.transaction(context) as connection:
            stored_plan = await repository.get_task_plan(connection, aggregate.plan.id)
            stored_version = await repository.get_task_plan_version(
                connection,
                aggregate.version.id,
            )
            stored_run = await repository.get_run(connection, aggregate.run.id)
            stored_manifest = await repository.get_manifest(connection, aggregate.run.id)
            stored_units = await repository.list_units(connection, aggregate.run.id)
            stored_attempts = await repository.list_attempts(
                connection,
                aggregate.unit.id,
            )
            stored_execution_profile = (
                await profile_repository.get_execution_profile_version(
                    connection,
                    aggregate.execution_profile.id,
                )
            )
            stored_identity_profile = (
                await profile_repository.get_identity_profile_version(
                    connection,
                    aggregate.identity_profile.id,
                )
            )
            stored_browser_profile = await profile_repository.get_browser_profile_version(
                connection,
                aggregate.browser_profile.id,
            )
            stored_data_profile = await profile_repository.get_data_profile_version(
                connection,
                aggregate.data_profile.id,
            )
            start_intent = await state_repository.get_workflow_start_intent(
                connection,
                owner_kind="TASK_RUN",
                owner_id=aggregate.run.id,
            )

            assert stored_plan == aggregate.plan
            assert stored_version == aggregate.version
            assert stored_run == aggregate.run
            assert stored_manifest == aggregate.manifest
            assert stored_units == (aggregate.unit,)
            assert stored_attempts == (aggregate.attempt,)
            assert stored_attempts[0].execution_unit_id == stored_units[0].id
            assert stored_units[0].task_run_id == stored_run.id
            assert stored_run.task_plan_version_id == stored_version.id
            assert stored_manifest.task_plan_version_id == stored_version.id
            assert stored_manifest.manifest_hash == stored_run.manifest_hash
            assert stored_version.task_plan_id == stored_plan.id
            assert stored_execution_profile == aggregate.execution_profile
            assert stored_identity_profile == aggregate.identity_profile
            assert stored_browser_profile == aggregate.browser_profile
            assert stored_data_profile == aggregate.data_profile
            assert start_intent is not None
            assert start_intent.namespace == aggregate.run.temporal_namespace
            assert start_intent.workflow_id == aggregate.run.temporal_workflow_id
            assert start_intent.request_digest == aggregate.run.request_digest

            assert (
                await profile_repository.create_execution_profile_version(
                    connection,
                    aggregate.execution_profile,
                )
            ).kind is ImmutableCreateKind.EXISTING
            assert (
                await profile_repository.create_identity_profile_version(
                    connection,
                    aggregate.identity_profile,
                )
            ).kind is ImmutableCreateKind.EXISTING
            assert (
                await profile_repository.create_browser_profile_version(
                    connection,
                    aggregate.browser_profile,
                )
            ).kind is ImmutableCreateKind.EXISTING
            assert (
                await profile_repository.create_data_profile_version(
                    connection,
                    aggregate.data_profile,
                )
            ).kind is ImmutableCreateKind.EXISTING

            exact_replay = await repository.create_run(
                connection,
                task_run=requested_run,
                manifest=aggregate.manifest,
                units=(aggregate.unit,),
                first_attempts=(aggregate.attempt,),
            )
            assert exact_replay.kind is ImmutableCreateKind.EXISTING
            assert exact_replay.task_run == aggregate.run
            assert exact_replay.manifest == aggregate.manifest
            with pytest.raises(
                ImmutableFactConflictError,
                match="different immutable run input",
            ):
                await repository.create_run(
                    connection,
                    task_run=requested_run.model_copy(
                        update={"rerun_of_task_run_id": uuid7()}
                    ),
                    manifest=aggregate.manifest,
                    units=(aggregate.unit,),
                    first_attempts=(aggregate.attempt,),
                )

        finalized_actor = aggregate.identity_profile.actors[0]
        with pytest.raises(psycopg.Error, match="actor bindings are already finalized"):
            async with database.transaction(context) as connection:
                await connection.execute(
                    """
                    insert into atlas.identity_profile_actor_binding (
                      identity_profile_version_id, tenant_id, project_id,
                      actor_slot, ordinal, role_id, role_key,
                      role_revision, capabilities
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        aggregate.identity_profile.id,
                        aggregate.identity_profile.tenant_id,
                        aggregate.identity_profile.project_id,
                        "zz_extra_actor",
                        len(aggregate.identity_profile.actors) + 1,
                        finalized_actor.role_id,
                        finalized_actor.role_key,
                        finalized_actor.role_revision,
                        list(finalized_actor.capabilities),
                    ),
                )

        incomplete_run, incomplete_manifest = _build_incomplete_materialization(aggregate)
        with pytest.raises(psycopg.Error, match="every Unit and exactly its first Attempt"):
            async with database.transaction(context) as connection:
                await _insert_incomplete_materialization(
                    connection,
                    incomplete_run,
                    incomplete_manifest,
                )
                await state_repository.seal_task_run_materialization(
                    connection,
                    task_run_id=incomplete_run.id,
                    expected_revision=incomplete_run.revision,
                )

        manifest_units = [
            item.model_dump(mode="json", by_alias=True) for item in aggregate.manifest.units
        ]
        valid_matrix = aggregate.version.matrix.model_dump(mode="json", by_alias=True)
        valid_profiles = aggregate.version.profile_refs.model_dump(mode="json", by_alias=True)
        malformed_plan_versions: tuple[
            tuple[
                str,
                Mapping[str, object],
                Mapping[str, object],
                Mapping[str, object],
            ],
            ...,
        ] = (
            (
                "9.0.1",
                {f"wrongAxis{index}": [] for index in range(4)},
                valid_profiles,
                aggregate.version.policy_digests,
            ),
            (
                "9.0.2",
                valid_matrix,
                {"wrongProfiles": []},
                aggregate.version.policy_digests,
            ),
            (
                "9.0.3",
                valid_matrix,
                valid_profiles,
                {"gate": None},
            ),
        )
        for (
            malformed_version,
            malformed_matrix,
            malformed_profiles,
            malformed_policies,
        ) in malformed_plan_versions:
            with pytest.raises(psycopg.Error):
                async with database.transaction(context) as connection:
                    await _insert_untrusted_plan_version(
                        connection,
                        aggregate,
                        version=malformed_version,
                        matrix=malformed_matrix,
                        profile_refs=malformed_profiles,
                        policy_digests=malformed_policies,
                    )

        with pytest.raises(
            psycopg.Error,
            match="manifest policy digests must cover its task plan version",
        ):
            async with database.transaction(context) as connection:
                await _insert_untrusted_manifest(
                    connection,
                    aggregate,
                    units=manifest_units,
                    policy_digests={"gate": f"sha256:{'d' * 64}"},
                )

        out_of_matrix_unit = {**manifest_units[0], "environmentId": str(uuid7())}
        with pytest.raises(
            psycopg.Error,
            match="manifest unit must derive from its task plan version",
        ):
            async with database.transaction(context) as connection:
                await _insert_untrusted_manifest(
                    connection,
                    aggregate,
                    units=[out_of_matrix_unit],
                    policy_digests=aggregate.manifest.policy_digests,
                )

        malformed_unit = {f"wrongField{index}": "x" for index in range(11)}
        with pytest.raises(
            psycopg.Error,
            match="manifest units must use valid v2 provenance",
        ):
            async with database.transaction(context) as connection:
                await _insert_untrusted_manifest(
                    connection,
                    aggregate,
                    units=[malformed_unit],
                    policy_digests=aggregate.manifest.policy_digests,
                )

        conflicting_manifest = aggregate.manifest.model_copy(update={"compiler_version": "0.2.0"})
        with pytest.raises(
            ValueError,
            match="requestDigest must match",
        ):
            async with database.transaction(context) as connection:
                await repository.create_run(
                    connection,
                    task_run=aggregate.run,
                    manifest=conflicting_manifest,
                    units=(aggregate.unit,),
                    first_attempts=(aggregate.attempt,),
                )

        premature_attempt_id = uuid7()
        premature_attempt = aggregate.attempt.model_copy(
            update={
                "id": premature_attempt_id,
                "attempt_number": 2,
                "temporal_workflow_id": unit_attempt_workflow_id(
                    tenant_id=seeded.tenant_id,
                    unit_attempt_id=premature_attempt_id,
                ),
            }
        )
        with pytest.raises(ValueError, match="closed retryable previous Attempt"):
            async with database.transaction(context) as connection:
                await repository.create_attempt(connection, premature_attempt)
        with pytest.raises(psycopg.Error, match="closed retryable previous Attempt"):
            async with database.transaction(context) as connection:
                await _insert_untrusted_attempt(connection, premature_attempt)

        first_started_at = datetime.now(UTC)
        async with database.transaction(context) as connection:
            running_first_attempt = await state_repository.transition_unit_attempt_state(
                connection,
                task_run_id=aggregate.run.id,
                execution_unit_id=aggregate.unit.id,
                unit_attempt_id=aggregate.attempt.id,
                expected_revision=aggregate.attempt.revision,
                lifecycle=ExecutionLifecycle.RUNNING,
                quality=ExecutionQuality.PENDING,
                hygiene=ExecutionHygiene.PENDING,
                started_at=first_started_at,
                finalized_at=None,
                cleanup_resolved_at=None,
                closed_at=None,
            )
            assert running_first_attempt is not None
        first_finalized_at = datetime.now(UTC)
        async with database.transaction(context) as connection:
            finalizing_first_attempt = await state_repository.transition_unit_attempt_state(
                connection,
                task_run_id=aggregate.run.id,
                execution_unit_id=aggregate.unit.id,
                unit_attempt_id=aggregate.attempt.id,
                expected_revision=running_first_attempt.revision,
                lifecycle=ExecutionLifecycle.FINALIZING,
                quality=ExecutionQuality.INFRA_ERROR,
                hygiene=ExecutionHygiene.PENDING,
                started_at=first_started_at,
                finalized_at=first_finalized_at,
                cleanup_resolved_at=None,
                closed_at=None,
            )
            assert finalizing_first_attempt is not None
        first_closed_at = datetime.now(UTC)
        async with database.transaction(context) as connection:
            closed_first_attempt = await state_repository.transition_unit_attempt_state(
                connection,
                task_run_id=aggregate.run.id,
                execution_unit_id=aggregate.unit.id,
                unit_attempt_id=aggregate.attempt.id,
                expected_revision=finalizing_first_attempt.revision,
                lifecycle=ExecutionLifecycle.CLOSED,
                quality=ExecutionQuality.INFRA_ERROR,
                hygiene=ExecutionHygiene.PENDING,
                started_at=first_started_at,
                finalized_at=first_finalized_at,
                cleanup_resolved_at=None,
                closed_at=first_closed_at,
            )
            assert closed_first_attempt is not None

        wrong_namespace_attempt_id = uuid7()
        wrong_namespace_attempt = aggregate.attempt.model_copy(
            update={
                "id": wrong_namespace_attempt_id,
                "attempt_number": 2,
                "temporal_namespace": "atlas-other",
                "temporal_workflow_id": unit_attempt_workflow_id(
                    tenant_id=seeded.tenant_id,
                    unit_attempt_id=wrong_namespace_attempt_id,
                ),
            }
        )
        with pytest.raises(ValueError, match="TaskRun Temporal namespace"):
            async with database.transaction(context) as connection:
                await repository.create_attempt(connection, wrong_namespace_attempt)
        with pytest.raises(psycopg.Error, match="TaskRun namespace"):
            async with database.transaction(context) as connection:
                await _insert_untrusted_attempt(connection, wrong_namespace_attempt)

        next_time = datetime.now(UTC)
        second_attempt_id = uuid7()
        second_attempt = aggregate.attempt.model_copy(
            update={
                "id": second_attempt_id,
                "attempt_number": 2,
                "temporal_workflow_id": unit_attempt_workflow_id(
                    tenant_id=seeded.tenant_id,
                    unit_attempt_id=second_attempt_id,
                ),
                "queued_at": next_time,
                "execution_deadline": next_time + timedelta(minutes=15),
                "created_at": next_time,
                "updated_at": next_time,
            }
        )
        async with database.transaction(context) as connection:
            second_attempt_result = await repository.create_attempt(
                connection,
                second_attempt,
            )
            assert second_attempt_result.kind is ImmutableCreateKind.CREATED
            replayed_second = await repository.create_attempt(connection, second_attempt)
            assert replayed_second.kind is ImmutableCreateKind.EXISTING
            assert replayed_second.fact == second_attempt

        conflicting_second_id = uuid7()
        conflicting_second = second_attempt.model_copy(
            update={
                "id": conflicting_second_id,
                "temporal_workflow_id": unit_attempt_workflow_id(
                    tenant_id=seeded.tenant_id,
                    unit_attempt_id=conflicting_second_id,
                ),
                "execution_deadline": second_attempt.execution_deadline + timedelta(seconds=1),
            }
        )
        with pytest.raises(
            ImmutableFactConflictError,
            match="different immutable content",
        ):
            async with database.transaction(context) as connection:
                await repository.create_attempt(connection, conflicting_second)

        workflow_collision = second_attempt.model_copy(
            update={
                "id": uuid7(),
                "attempt_number": 3,
                "queued_at": next_time + timedelta(seconds=1),
                "execution_deadline": next_time + timedelta(minutes=16),
                "created_at": next_time + timedelta(seconds=1),
                "updated_at": next_time + timedelta(seconds=1),
            }
        )
        with pytest.raises(ValueError, match="deterministic Temporal identity"):
            async with database.transaction(context) as connection:
                await repository.create_attempt(connection, workflow_collision)

        gap_attempt_id = uuid7()
        gap_attempt = second_attempt.model_copy(
            update={
                "id": gap_attempt_id,
                "attempt_number": 4,
                "temporal_workflow_id": unit_attempt_workflow_id(
                    tenant_id=seeded.tenant_id,
                    unit_attempt_id=gap_attempt_id,
                ),
                "queued_at": next_time + timedelta(seconds=1),
                "execution_deadline": next_time + timedelta(minutes=16),
                "created_at": next_time + timedelta(seconds=1),
                "updated_at": next_time + timedelta(seconds=1),
            }
        )
        with pytest.raises(ValueError, match="closed retryable previous Attempt"):
            async with database.transaction(context) as connection:
                await repository.create_attempt(connection, gap_attempt)
        with pytest.raises(psycopg.Error, match="gapless"):
            async with database.transaction(context) as connection:
                await _insert_untrusted_attempt(connection, gap_attempt)

        async with database.transaction(context) as connection:
            attempts_after_rejections = await repository.list_attempts(
                connection,
                aggregate.unit.id,
            )
            assert attempts_after_rejections == (
                closed_first_attempt,
                second_attempt,
            )

        first_event = TaskExecutionEvent(
            id=uuid7(),
            tenant_id=seeded.tenant_id,
            project_id=seeded.project_id,
            task_run_id=aggregate.run.id,
            seq=1,
            event_type="task.queued",
            lifecycle=aggregate.run.lifecycle,
            quality=aggregate.run.quality,
            hygiene=aggregate.run.hygiene,
            payload={"manifestHash": aggregate.manifest.manifest_hash},
            occurred_at=aggregate.run.queued_at,
        )
        second_event = TaskExecutionEvent(
            id=uuid7(),
            tenant_id=seeded.tenant_id,
            project_id=seeded.project_id,
            task_run_id=aggregate.run.id,
            execution_unit_id=aggregate.unit.id,
            unit_attempt_id=second_attempt.id,
            seq=2,
            event_type="unit.attempt.queued",
            lifecycle=second_attempt.lifecycle,
            quality=second_attempt.quality,
            hygiene=second_attempt.hygiene,
            payload={"attemptNumber": 2},
            occurred_at=second_attempt.queued_at,
        )
        async with database.transaction(context) as connection:
            first_result = await repository.append_event(connection, first_event)
            replayed_first = await repository.append_event(connection, first_event)
            second_event_result = await repository.append_event(connection, second_event)
            assert first_result.kind is ImmutableCreateKind.CREATED
            assert replayed_first.kind is ImmutableCreateKind.EXISTING
            assert second_event_result.kind is ImmutableCreateKind.CREATED

        early_attempt_event = second_event.model_copy(
            update={
                "id": uuid7(),
                "seq": 3,
                "event_type": "unit.attempt.early",
                "occurred_at": second_attempt.queued_at - timedelta(milliseconds=1),
            }
        )
        with pytest.raises(psycopg.Error, match="narrowest scope"):
            async with database.transaction(context) as connection:
                await repository.append_event(connection, early_attempt_event)

        conflicting_event = first_event.model_copy(
            update={"id": uuid7(), "payload": {"manifestHash": POLICY_DIGEST}}
        )
        with pytest.raises(
            ImmutableFactConflictError,
            match="different immutable content",
        ):
            async with database.transaction(context) as connection:
                await repository.append_event(connection, conflicting_event)

        gap_event = first_event.model_copy(
            update={
                "id": uuid7(),
                "seq": 4,
                "event_type": "task.gap",
                "occurred_at": second_event.occurred_at + timedelta(seconds=1),
            }
        )
        with pytest.raises(psycopg.Error, match="gapless"):
            async with database.transaction(context) as connection:
                await repository.append_event(connection, gap_event)

        async with database.transaction(context) as connection:
            assert await repository.list_events(
                connection,
                task_run_id=aggregate.run.id,
                after_seq=0,
                limit=10,
            ) == (first_event, second_event)

        started_at = datetime.now(UTC)
        async with database.transaction(context) as connection:
            running_attempt = await state_repository.transition_unit_attempt_state(
                connection,
                task_run_id=aggregate.run.id,
                execution_unit_id=aggregate.unit.id,
                unit_attempt_id=second_attempt.id,
                expected_revision=second_attempt.revision,
                lifecycle=ExecutionLifecycle.RUNNING,
                quality=ExecutionQuality.PENDING,
                hygiene=ExecutionHygiene.PENDING,
                started_at=started_at,
                finalized_at=None,
                cleanup_resolved_at=None,
                closed_at=None,
            )
            assert running_attempt is not None
            advanced_replay = await repository.create_attempt(connection, second_attempt)
            assert advanced_replay.kind is ImmutableCreateKind.EXISTING
            assert advanced_replay.fact.lifecycle is ExecutionLifecycle.RUNNING
            assert advanced_replay.fact.started_at == started_at

        finalized_at = datetime.now(UTC)
        async with database.transaction(context) as connection:
            finalizing_attempt = await state_repository.transition_unit_attempt_state(
                connection,
                task_run_id=aggregate.run.id,
                execution_unit_id=aggregate.unit.id,
                unit_attempt_id=second_attempt.id,
                expected_revision=running_attempt.revision,
                lifecycle=ExecutionLifecycle.FINALIZING,
                quality=ExecutionQuality.FAILED,
                hygiene=ExecutionHygiene.PENDING,
                started_at=started_at,
                finalized_at=finalized_at,
                cleanup_resolved_at=None,
                closed_at=None,
            )
            assert finalizing_attempt is not None
        closed_at = datetime.now(UTC)
        async with database.transaction(context) as connection:
            closed_pending = await state_repository.transition_unit_attempt_state(
                connection,
                task_run_id=aggregate.run.id,
                execution_unit_id=aggregate.unit.id,
                unit_attempt_id=second_attempt.id,
                expected_revision=finalizing_attempt.revision,
                lifecycle=ExecutionLifecycle.CLOSED,
                quality=ExecutionQuality.FAILED,
                hygiene=ExecutionHygiene.PENDING,
                started_at=started_at,
                finalized_at=finalized_at,
                cleanup_resolved_at=None,
                closed_at=closed_at,
            )
            assert closed_pending is not None
            assert closed_pending.lifecycle is ExecutionLifecycle.CLOSED
            assert closed_pending.hygiene is ExecutionHygiene.PENDING

        async with database.transaction(context) as connection:
            cleanup_running = await state_repository.transition_unit_attempt_state(
                connection,
                task_run_id=aggregate.run.id,
                execution_unit_id=aggregate.unit.id,
                unit_attempt_id=second_attempt.id,
                expected_revision=closed_pending.revision,
                lifecycle=ExecutionLifecycle.CLOSED,
                quality=ExecutionQuality.FAILED,
                hygiene=ExecutionHygiene.RUNNING,
                started_at=started_at,
                finalized_at=finalized_at,
                cleanup_resolved_at=None,
                closed_at=closed_at,
            )
            assert cleanup_running is not None
        cleanup_resolved_at = datetime.now(UTC)
        assert cleanup_resolved_at >= closed_at
        async with database.transaction(context) as connection:
            closed_cleaned = await state_repository.transition_unit_attempt_state(
                connection,
                task_run_id=aggregate.run.id,
                execution_unit_id=aggregate.unit.id,
                unit_attempt_id=second_attempt.id,
                expected_revision=cleanup_running.revision,
                lifecycle=ExecutionLifecycle.CLOSED,
                quality=ExecutionQuality.FAILED,
                hygiene=ExecutionHygiene.CLEANED,
                started_at=started_at,
                finalized_at=finalized_at,
                cleanup_resolved_at=cleanup_resolved_at,
                closed_at=closed_at,
            )
            assert closed_cleaned is not None
            assert closed_cleaned.lifecycle is ExecutionLifecycle.CLOSED
            assert closed_cleaned.quality is ExecutionQuality.FAILED
            assert closed_cleaned.hygiene is ExecutionHygiene.CLEANED
            assert closed_cleaned.closed_at == closed_at
            assert closed_cleaned.cleanup_resolved_at == cleanup_resolved_at

            cleanup_event = TaskExecutionEvent(
                id=uuid7(),
                tenant_id=seeded.tenant_id,
                project_id=seeded.project_id,
                task_run_id=aggregate.run.id,
                execution_unit_id=aggregate.unit.id,
                unit_attempt_id=second_attempt.id,
                seq=3,
                event_type="unit.cleanup.resolved",
                lifecycle=closed_cleaned.lifecycle,
                quality=closed_cleaned.quality,
                hygiene=closed_cleaned.hygiene,
                payload={"attemptNumber": second_attempt.attempt_number},
                occurred_at=cleanup_resolved_at,
            )
            cleanup_result = await repository.append_event(connection, cleanup_event)
            assert cleanup_result.kind is ImmutableCreateKind.CREATED

        with pytest.raises(psycopg.Error, match="hygiene transition"):
            async with database.transaction(context) as connection:
                await state_repository.transition_unit_attempt_state(
                    connection,
                    task_run_id=aggregate.run.id,
                    execution_unit_id=aggregate.unit.id,
                    unit_attempt_id=second_attempt.id,
                    expected_revision=closed_cleaned.revision,
                    lifecycle=ExecutionLifecycle.CLOSED,
                    quality=ExecutionQuality.FAILED,
                    hygiene=ExecutionHygiene.RUNNING,
                    started_at=started_at,
                    finalized_at=finalized_at,
                    cleanup_resolved_at=cleanup_resolved_at,
                    closed_at=closed_at,
                )

        async with database.transaction(context) as connection:
            canceling_run = await state_repository.transition_task_run_state(
                connection,
                task_run_id=aggregate.run.id,
                expected_revision=aggregate.run.revision,
                lifecycle=ExecutionLifecycle.CANCELING,
                quality=ExecutionQuality.PENDING,
                hygiene=ExecutionHygiene.PENDING,
                started_at=None,
                finalized_at=None,
                cleanup_resolved_at=None,
                closed_at=None,
            )
            assert canceling_run is not None
        canceling_retry_id = uuid7()
        canceling_retry = second_attempt.model_copy(
            update={
                "id": canceling_retry_id,
                "attempt_number": 3,
                "temporal_workflow_id": unit_attempt_workflow_id(
                    tenant_id=seeded.tenant_id,
                    unit_attempt_id=canceling_retry_id,
                ),
            }
        )
        with pytest.raises(ValueError, match="dispatchable TaskRun"):
            async with database.transaction(context) as connection:
                await repository.create_attempt(connection, canceling_retry)
        with pytest.raises(psycopg.Error, match="sealed dispatchable TaskRun"):
            async with database.transaction(context) as connection:
                await _insert_untrusted_attempt(connection, canceling_retry)
        with pytest.raises(psycopg.Error, match="lifecycle transition"):
            async with database.transaction(context) as connection:
                await state_repository.transition_task_run_state(
                    connection,
                    task_run_id=aggregate.run.id,
                    expected_revision=canceling_run.revision,
                    lifecycle=ExecutionLifecycle.QUEUED,
                    quality=ExecutionQuality.PENDING,
                    hygiene=ExecutionHygiene.PENDING,
                    started_at=None,
                    finalized_at=None,
                    cleanup_resolved_at=None,
                    closed_at=None,
                )

        orphan_id = uuid7()
        with pytest.raises(psycopg.Error, match="task_run_manifest_reverse_scope_fk"):
            async with database.transaction(context) as connection:
                await connection.execute(
                    """
                    insert into atlas.task_run (
                      id, tenant_id, project_id, task_plan_version_id, manifest_hash,
                      trigger_source, trigger_fingerprint, request_digest,
                      materialization_state, lifecycle, quality, hygiene,
                      requested_by, temporal_namespace, temporal_workflow_id,
                      requested_at, queued_at, revision, created_at, updated_at
                    ) values (
                      %s, %s, %s, %s, %s,
                      'API', %s, %s,
                      'MATERIALIZING', 'QUEUED', 'PENDING', 'PENDING',
                      %s, 'default', %s,
                      %s, %s, 1, %s, %s
                    )
                    """,
                    (
                        orphan_id,
                        aggregate.run.tenant_id,
                        aggregate.run.project_id,
                        aggregate.run.task_plan_version_id,
                        POLICY_DIGEST,
                        f"integration:orphan:{orphan_id}",
                        POLICY_DIGEST,
                        aggregate.run.requested_by,
                        task_run_workflow_id(
                            tenant_id=aggregate.run.tenant_id,
                            task_run_id=orphan_id,
                        ),
                        aggregate.run.requested_at,
                        aggregate.run.queued_at,
                        aggregate.run.created_at,
                        aggregate.run.updated_at,
                    ),
                )

        other_tenant_context = DatabaseContext(
            tenant_id=seeded.other_tenant_id,
            actor_id=uuid7(),
            request_id=f"task-host-hidden:{aggregate.run.id}",
        )
        async with database.transaction(other_tenant_context) as connection:
            assert (
                await profile_repository.get_execution_profile_version(
                    connection,
                    aggregate.execution_profile.id,
                )
                is None
            )
            assert (
                await profile_repository.get_identity_profile_version(
                    connection,
                    aggregate.identity_profile.id,
                )
                is None
            )
            assert (
                await profile_repository.get_browser_profile_version(
                    connection,
                    aggregate.browser_profile.id,
                )
                is None
            )
            assert (
                await profile_repository.get_data_profile_version(
                    connection,
                    aggregate.data_profile.id,
                )
                is None
            )
            assert await repository.get_task_plan(connection, aggregate.plan.id) is None
            assert await repository.get_task_plan_version(connection, aggregate.version.id) is None
            assert await repository.get_run(connection, aggregate.run.id) is None
            assert await repository.get_manifest(connection, aggregate.run.id) is None
            assert await repository.get_unit(connection, aggregate.unit.id) is None
            assert await repository.get_attempt(connection, aggregate.attempt.id) is None
            assert (
                await repository.list_events(
                    connection,
                    task_run_id=aggregate.run.id,
                    after_seq=0,
                    limit=10,
                )
                == ()
            )

        cross_project_version = _cross_project_version(aggregate, seeded)
        with pytest.raises(psycopg.Error, match="same-scope case versions"):
            async with database.transaction(context) as connection:
                await repository.create_task_plan_version(
                    connection,
                    cross_project_version,
                )

        missing_environment_version = _dependency_variant(
            aggregate,
            version="2.1.0",
            matrix=aggregate.version.matrix.model_copy(update={"environment_ids": (uuid7(),)}),
        )
        with pytest.raises(
            psycopg.Error,
            match="active same-scope test or staging environments",
        ):
            async with database.transaction(context) as connection:
                await repository.create_task_plan_version(
                    connection,
                    missing_environment_version,
                )

        original_profile = aggregate.version.profile_refs.case_profiles[0]
        missing_fixture_version = _dependency_variant(
            aggregate,
            version="2.2.0",
            profile_refs=TaskProfileRefs(
                case_profiles=(
                    original_profile.model_copy(update={"fixture_blueprint_version_id": uuid7()}),
                )
            ),
        )
        with pytest.raises(
            psycopg.Error,
            match="published same-scope fixture blueprint versions",
        ):
            async with database.transaction(context) as connection:
                await repository.create_task_plan_version(
                    connection,
                    missing_fixture_version,
                )

        return aggregate, second_attempt, first_event
    finally:
        await database.close()


def _cross_project_version(
    aggregate: TaskAggregate,
    seeded: SeededCaseVersion,
) -> TaskPlanVersion:
    """Build a valid domain value whose parent scope is a different project."""

    digest = task_plan_version_content_digest(
        tenant_id=seeded.tenant_id,
        project_id=seeded.other_project_id,
        task_plan_id=aggregate.plan.id,
        version="2.0.0",
        pinned_case_version_ids=aggregate.version.pinned_case_version_ids,
        matrix=aggregate.version.matrix,
        profile_refs=aggregate.version.profile_refs,
        policy_digests=aggregate.version.policy_digests,
    )
    return aggregate.version.model_copy(
        update={
            "id": uuid7(),
            "project_id": seeded.other_project_id,
            "version": "2.0.0",
            "version_ref": task_plan_version_ref(aggregate.plan.id, "2.0.0"),
            "content_digest": digest,
        }
    )


def _dependency_variant(
    aggregate: TaskAggregate,
    *,
    version: str,
    matrix: TaskMatrixDefinition | None = None,
    profile_refs: TaskProfileRefs | None = None,
) -> TaskPlanVersion:
    """Build a validated PlanVersion variant for dependency admission tests."""

    selected_matrix = matrix or aggregate.version.matrix
    selected_profiles = profile_refs or aggregate.version.profile_refs
    digest = task_plan_version_content_digest(
        tenant_id=aggregate.version.tenant_id,
        project_id=aggregate.version.project_id,
        task_plan_id=aggregate.version.task_plan_id,
        version=version,
        pinned_case_version_ids=aggregate.version.pinned_case_version_ids,
        matrix=selected_matrix,
        profile_refs=selected_profiles,
        policy_digests=aggregate.version.policy_digests,
    )
    return TaskPlanVersion(
        id=uuid7(),
        tenant_id=aggregate.version.tenant_id,
        project_id=aggregate.version.project_id,
        task_plan_id=aggregate.version.task_plan_id,
        version=version,
        version_ref=task_plan_version_ref(aggregate.version.task_plan_id, version),
        pinned_case_version_ids=aggregate.version.pinned_case_version_ids,
        matrix=selected_matrix,
        profile_refs=selected_profiles,
        policy_digests=aggregate.version.policy_digests,
        content_digest=digest,
        published_by=aggregate.version.published_by,
        published_at=aggregate.version.published_at,
        revision=1,
        created_at=aggregate.version.created_at,
        updated_at=aggregate.version.updated_at,
    )


async def _insert_untrusted_manifest(
    connection: AsyncConnection[DictRow],
    aggregate: TaskAggregate,
    *,
    units: Sequence[Mapping[str, object]],
    policy_digests: Mapping[str, str],
) -> None:
    """Bypass the repository to prove PostgreSQL enforces Plan provenance."""

    run_id = uuid7()
    manifest_hash = f"sha256:{'e' * 64}"
    trigger_fingerprint = f"integration:untrusted-manifest:{run_id}"
    now = datetime.now(UTC)
    await connection.execute(
        """
        insert into atlas.task_run (
          id, tenant_id, project_id, task_plan_version_id, manifest_hash,
          trigger_source, trigger_fingerprint, request_digest,
          materialization_state, lifecycle, quality, hygiene,
          requested_by, temporal_namespace, temporal_workflow_id,
          requested_at, queued_at, revision, created_at, updated_at
        ) values (
          %s, %s, %s, %s, %s,
          'API', %s, %s,
          'MATERIALIZING', 'QUEUED', 'PENDING', 'PENDING',
          %s, 'default', %s,
          %s, %s, 1, %s, %s
        )
        """,
        (
            run_id,
            aggregate.run.tenant_id,
            aggregate.run.project_id,
            aggregate.version.id,
            manifest_hash,
            trigger_fingerprint,
            POLICY_DIGEST,
            aggregate.run.requested_by,
            task_run_workflow_id(
                tenant_id=aggregate.run.tenant_id,
                task_run_id=run_id,
            ),
            now,
            now,
            now,
            now,
        ),
    )
    await connection.execute(
        """
        insert into atlas.task_run_manifest (
          task_run_id, tenant_id, project_id, task_plan_version_id,
          schema_version, trigger_source, trigger_fingerprint, iteration_id,
          units, policy_digests, compiler_version, manifest_hash
        ) values (
          %s, %s, %s, %s,
          'atlas.task-run-manifest/0.1', 'API', %s, 'integration:2026-07',
          %s, %s, '0.1.0', %s
        )
        """,
        (
            run_id,
            aggregate.run.tenant_id,
            aggregate.run.project_id,
            aggregate.version.id,
            trigger_fingerprint,
            Jsonb(units),
            Jsonb(policy_digests),
            manifest_hash,
        ),
    )


async def _insert_untrusted_attempt(
    connection: AsyncConnection[DictRow],
    attempt: UnitAttempt,
) -> None:
    """Bypass the repository so PostgreSQL retry guards are exercised directly."""

    await connection.execute(
        """
        insert into atlas.unit_attempt (
          id, tenant_id, project_id, task_run_id, execution_unit_id,
          manifest_hash, unit_key, case_version_id, attempt_number,
          lifecycle, quality, hygiene, temporal_namespace,
          temporal_workflow_id, queued_at, execution_deadline,
          started_at, finalized_at, cleanup_resolved_at, closed_at,
          revision, created_at, updated_at
        ) values (
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s
        )
        """,
        (
            attempt.id,
            attempt.tenant_id,
            attempt.project_id,
            attempt.task_run_id,
            attempt.execution_unit_id,
            attempt.manifest_hash,
            attempt.unit_key,
            attempt.case_version_id,
            attempt.attempt_number,
            attempt.lifecycle,
            attempt.quality,
            attempt.hygiene,
            attempt.temporal_namespace,
            attempt.temporal_workflow_id,
            attempt.queued_at,
            attempt.execution_deadline,
            attempt.started_at,
            attempt.finalized_at,
            attempt.cleanup_resolved_at,
            attempt.closed_at,
            attempt.revision,
            attempt.created_at,
            attempt.updated_at,
        ),
    )


async def _insert_incomplete_materialization(
    connection: AsyncConnection[DictRow],
    run: TaskRun,
    manifest: TaskRunManifest,
) -> None:
    """Insert only the canonical root and Manifest to exercise the database seal."""

    await connection.execute(
        """
        insert into atlas.task_run (
          id, tenant_id, project_id, task_plan_version_id, manifest_hash,
          trigger_source, trigger_fingerprint, request_digest,
          materialization_state, materialized_unit_count,
          materialized_first_attempt_count, materialization_sealed_at,
          rerun_of_task_run_id, lifecycle, quality, hygiene, requested_by,
          temporal_namespace, temporal_workflow_id, requested_at, queued_at,
          started_at, finalized_at, cleanup_resolved_at, closed_at,
          revision, created_at, updated_at
        ) values (
          %s, %s, %s, %s, %s,
          %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s
        )
        """,
        (
            run.id,
            run.tenant_id,
            run.project_id,
            run.task_plan_version_id,
            run.manifest_hash,
            run.trigger_source,
            run.trigger_fingerprint,
            run.request_digest,
            run.materialization_state,
            run.materialized_unit_count,
            run.materialized_first_attempt_count,
            run.materialization_sealed_at,
            run.rerun_of_task_run_id,
            run.lifecycle,
            run.quality,
            run.hygiene,
            run.requested_by,
            run.temporal_namespace,
            run.temporal_workflow_id,
            run.requested_at,
            run.queued_at,
            run.started_at,
            run.finalized_at,
            run.cleanup_resolved_at,
            run.closed_at,
            run.revision,
            run.created_at,
            run.updated_at,
        ),
    )
    await connection.execute(
        """
        insert into atlas.task_run_manifest (
          task_run_id, tenant_id, project_id, task_plan_version_id,
          schema_version, trigger_source, trigger_fingerprint, iteration_id,
          units, policy_digests, compiler_version, manifest_hash, unit_count
        ) values (
          %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s, %s
        )
        """,
        (
            manifest.task_run_id,
            manifest.tenant_id,
            manifest.project_id,
            manifest.task_plan_version_id,
            manifest.schema_version,
            manifest.trigger_source,
            manifest.trigger_fingerprint,
            manifest.iteration_id,
            Jsonb(
                [
                    unit.model_dump(mode="json", by_alias=True)
                    for unit in manifest.units
                ]
            ),
            Jsonb(manifest.policy_digests),
            manifest.compiler_version,
            manifest.manifest_hash,
            len(manifest.units),
        ),
    )
async def _insert_untrusted_plan_version(
    connection: AsyncConnection[DictRow],
    aggregate: TaskAggregate,
    *,
    version: str,
    matrix: Mapping[str, object],
    profile_refs: Mapping[str, object],
    policy_digests: Mapping[str, object],
) -> None:
    """Bypass Pydantic to prove malformed JSON cannot enter Plan facts."""

    await connection.execute(
        """
        insert into atlas.task_plan_version (
          id, tenant_id, project_id, task_plan_id, schema_version,
          version, version_ref, pinned_case_version_ids, matrix,
          profile_refs, policy_digests, content_digest, published_by,
          published_at, revision, created_at, updated_at
        ) values (
          %s, %s, %s, %s, 'atlas.task-plan/0.1',
          %s, %s, %s, %s,
          %s, %s, %s, %s,
          %s, 1, %s, %s
        )
        """,
        (
            uuid7(),
            aggregate.version.tenant_id,
            aggregate.version.project_id,
            aggregate.version.task_plan_id,
            version,
            task_plan_version_ref(aggregate.version.task_plan_id, version),
            list(aggregate.version.pinned_case_version_ids),
            Jsonb(matrix),
            Jsonb(profile_refs),
            Jsonb(policy_digests),
            POLICY_DIGEST,
            aggregate.version.published_by,
            aggregate.version.published_at,
            aggregate.version.created_at,
            aggregate.version.updated_at,
        ),
    )


def _assert_immutable_bindings(
    seeded: SeededCaseVersion,
    aggregate: TaskAggregate,
    second_attempt: UnitAttempt,
    first_event: TaskExecutionEvent,
) -> None:
    """Reject mutation of every immutable published or append-only binding."""

    _assert_statement_rejected(
        seeded.tenant_id,
        "update atlas.task_plan_version set content_digest = %s where id = %s",
        (POLICY_DIGEST, aggregate.version.id),
    )
    _assert_statement_rejected(
        seeded.tenant_id,
        "update atlas.task_run_manifest set compiler_version = '9.9.9' where task_run_id = %s",
        (aggregate.run.id,),
    )
    _assert_statement_rejected(
        seeded.tenant_id,
        "update atlas.execution_unit set parameter_digest = %s where id = %s",
        (POLICY_DIGEST, aggregate.unit.id),
    )
    _assert_statement_rejected(
        seeded.tenant_id,
        "update atlas.unit_attempt set attempt_number = 9 where id = %s",
        (second_attempt.id,),
    )
    _assert_statement_rejected(
        seeded.tenant_id,
        "update atlas.task_run_event set payload = '{}'::jsonb where id = %s",
        (first_event.id,),
    )

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, true)",
            (str(seeded.tenant_id),),
        )
        rows = connection.execute(
            """
            select attempt_number, id
            from atlas.unit_attempt
            where execution_unit_id = %s
            order by attempt_number
            """,
            (aggregate.unit.id,),
        ).fetchall()
        event_rows = connection.execute(
            """
            select seq, id
            from atlas.task_run_event
            where task_run_id = %s
            order by seq
            """,
            (aggregate.run.id,),
        ).fetchall()

    assert rows == [(1, aggregate.attempt.id), (2, second_attempt.id)]
    assert event_rows[0] == (1, first_event.id)
    assert [row[0] for row in event_rows] == [1, 2, 3]


def _assert_statement_rejected(
    tenant_id: UUID,
    statement: str,
    params: tuple[object, ...],
) -> None:
    """Run one forbidden mutation in autocommit mode to isolate its failure."""

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, false)",
            (str(tenant_id),),
        )
        with pytest.raises(psycopg.Error):
            connection.execute(statement, params)
