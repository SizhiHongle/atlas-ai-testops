"""HTTP contract tests for TaskPlan authoring and publication."""

from typing import cast

from fastapi.testclient import TestClient
from tests.infrastructure.test_task_run_repository import (
    _aggregate,
    _task_plan,
    _task_plan_version,
)

from atlas_testops.api.dependencies import (
    get_task_plan_launch_service,
    get_task_plan_service,
)
from atlas_testops.api.security import get_actor
from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.application.task_launches import TaskPlanLaunchService
from atlas_testops.application.task_plans import TaskPlanService
from atlas_testops.core.config import Settings
from atlas_testops.domain.task import (
    CreateTaskPlan,
    PublishTaskPlanVersion,
    StartTaskPlanVersionRun,
    TaskPlan,
    TaskPlanPage,
    TaskPlanVersion,
    TaskPlanVersionPage,
    TaskRetryPolicy,
    TaskRun,
    task_retry_policy_digest,
)
from atlas_testops.main import create_app


class RecordingTaskPlanService:
    def __init__(self) -> None:
        self.plan = _task_plan()
        self.version = _task_plan_version()
        self.calls: list[tuple[object, ...]] = []

    async def create(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskPlan]:
        self.calls.append(("create", *args, kwargs))
        return CommandResult(value=self.plan, status_code=201, replayed=False)

    async def list_for_project(
        self,
        *args: object,
        **kwargs: object,
    ) -> TaskPlanPage:
        self.calls.append(("plans", *args, kwargs))
        return TaskPlanPage(items=(self.plan,))

    async def get(self, *args: object) -> TaskPlan:
        self.calls.append(("plan", *args))
        return self.plan

    async def publish_version(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskPlanVersion]:
        self.calls.append(("publish", *args, kwargs))
        return CommandResult(value=self.version, status_code=201, replayed=False)

    async def list_versions(
        self,
        *args: object,
        **kwargs: object,
    ) -> TaskPlanVersionPage:
        self.calls.append(("versions", *args, kwargs))
        return TaskPlanVersionPage(items=(self.version,))

    async def get_version(self, *args: object) -> TaskPlanVersion:
        self.calls.append(("version", *args))
        return self.version


class RecordingTaskPlanLaunchService:
    def __init__(self) -> None:
        self.run = _aggregate(unit_count=1)[0]
        self.calls: list[tuple[object, ...]] = []

    async def launch(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskRun]:
        self.calls.append((*args, kwargs))
        return CommandResult(value=self.run, status_code=201, replayed=False)


def test_task_plan_routes_preserve_authoring_and_exact_publication_contracts() -> None:
    service = RecordingTaskPlanService()
    launches = RecordingTaskPlanLaunchService()
    actor = ActorContext(
        tenant_id=service.plan.tenant_id,
        actor_id=service.plan.created_by,
        request_id="task-plan-api-test",
        development_override=True,
    )
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = lambda: actor
    app.dependency_overrides[get_task_plan_service] = lambda: cast(
        TaskPlanService,
        service,
    )
    app.dependency_overrides[get_task_plan_launch_service] = lambda: cast(
        TaskPlanLaunchService,
        launches,
    )
    create = CreateTaskPlan(
        task_key=service.plan.task_key,
        name=service.plan.name,
        client_mutation_id="create-task-plan-001",
    )
    publish = PublishTaskPlanVersion(
        version=service.version.version,
        pinned_case_version_ids=service.version.pinned_case_version_ids,
        matrix=service.version.matrix,
        profile_refs=service.version.profile_refs,
        policy_digests=service.version.policy_digests,
        client_mutation_id="publish-task-plan-001",
    )
    launch = StartTaskPlanVersionRun(
        client_mutation_id="manual-launch-001",
        iteration_id="iteration:nightly",
        retry_policy=TaskRetryPolicy(
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
        ),
    )

    with TestClient(app) as client:
        missing_key = client.post(
            f"/v1/projects/{service.plan.project_id}/task-plans",
            json=create.model_dump(mode="json", by_alias=True),
        )
        created = client.post(
            f"/v1/projects/{service.plan.project_id}/task-plans",
            headers={"Idempotency-Key": create.client_mutation_id},
            json=create.model_dump(mode="json", by_alias=True),
        )
        plans = client.get(
            f"/v1/projects/{service.plan.project_id}/task-plans?limit=7"
        )
        plan = client.get(f"/v1/task-plans/{service.plan.id}")
        published = client.post(
            f"/v1/task-plans/{service.plan.id}/versions",
            headers={"Idempotency-Key": publish.client_mutation_id},
            json=publish.model_dump(mode="json", by_alias=True),
        )
        versions = client.get(
            f"/v1/task-plans/{service.plan.id}/versions?limit=8"
        )
        version = client.get(
            f"/v1/task-plan-versions/{service.version.id}"
        )
        launched = client.post(
            f"/v1/task-plan-versions/{service.version.id}:run",
            headers={"Idempotency-Key": launch.client_mutation_id},
            json=launch.model_dump(mode="json", by_alias=True),
        )

    assert missing_key.status_code == 422
    assert created.status_code == 201
    assert created.headers["etag"] == '"revision-1"'
    assert created.headers["idempotency-replayed"] == "false"
    assert created.headers["location"].endswith(str(service.plan.id))
    assert plans.json()["items"][0]["taskKey"] == service.plan.task_key
    assert plan.status_code == 200
    assert published.status_code == 201
    assert published.headers["location"].endswith(str(service.version.id))
    assert versions.json()["items"][0]["contentDigest"] == (
        service.version.content_digest
    )
    assert version.headers["etag"] == '"revision-1"'
    assert launched.status_code == 201
    assert launched.headers["location"].endswith(str(launches.run.id))
    assert launched.headers["idempotency-replayed"] == "false"
    assert launches.calls[0][-1] == {
        "idempotency_key": launch.client_mutation_id
    }
    assert [call[0] for call in service.calls] == [
        "create",
        "plans",
        "plan",
        "publish",
        "versions",
        "version",
    ]
    assert service.calls[0][-1] == {
        "idempotency_key": "create-task-plan-001"
    }
    assert service.calls[1][-1] == {"cursor": None, "limit": 7}
    assert service.calls[3][-1] == {
        "idempotency_key": "publish-task-plan-001"
    }
    assert service.calls[4][-1] == {"cursor": None, "limit": 8}
