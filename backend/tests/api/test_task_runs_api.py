"""HTTP contract tests for TaskRun reads and durable Cancel acceptance."""

from typing import cast

from fastapi.testclient import TestClient
from tests.infrastructure.test_task_run_repository import _aggregate, _event, uid

from atlas_testops.api.dependencies import (
    get_task_run_command_service,
    get_task_run_query_service,
    get_task_run_rerun_service,
)
from atlas_testops.api.security import get_actor
from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.application.task_commands import TaskRunCommandService
from atlas_testops.application.task_reruns import TaskRunRerunService
from atlas_testops.application.task_runs import TaskRunQueryService
from atlas_testops.core.config import Settings
from atlas_testops.domain.task import (
    ExecutionUnitPage,
    RequestTaskRunCancel,
    RequestTaskRunInfraFailureRerun,
    RequestTaskRunPause,
    RequestTaskRunResume,
    TaskExecutionEventPage,
    TaskRun,
    TaskRunCommandIntent,
    TaskRunCommandStatus,
    TaskRunCommandType,
    TaskRunPage,
    UnitAttemptPage,
    task_run_command_digest,
    task_run_workflow_id,
)
from atlas_testops.main import create_app


class RecordingTaskRunQueryService:
    def __init__(self) -> None:
        self.run, self.manifest, self.units, self.attempts = _aggregate()
        self.event = _event(self.run)
        self.calls: list[tuple[object, ...]] = []

    async def list_for_project(self, *args: object, **kwargs: object) -> TaskRunPage:
        self.calls.append(("runs", *args, kwargs))
        return TaskRunPage(items=(self.run,))

    async def get(self, *args: object) -> object:
        self.calls.append(("run", *args))
        return self.run

    async def get_manifest(self, *args: object) -> object:
        self.calls.append(("manifest", *args))
        return self.manifest

    async def list_units(self, *args: object, **kwargs: object) -> ExecutionUnitPage:
        self.calls.append(("units", *args, kwargs))
        return ExecutionUnitPage(items=self.units)

    async def list_attempts(self, *args: object, **kwargs: object) -> UnitAttemptPage:
        self.calls.append(("attempts", *args, kwargs))
        return UnitAttemptPage(items=self.attempts)

    async def list_events(
        self,
        *args: object,
        **kwargs: object,
    ) -> TaskExecutionEventPage:
        self.calls.append(("events", *args, kwargs))
        return TaskExecutionEventPage(items=(self.event,))


class RecordingTaskRunCommandService:
    def __init__(self, query: RecordingTaskRunQueryService) -> None:
        run = query.run
        assert run.request_digest is not None
        assert run.temporal_namespace is not None
        assert run.temporal_workflow_id is not None
        mutation_id = "cancel-command-001"
        self.command = TaskRunCommandIntent(
            id=run.id,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            task_run_id=run.id,
            command_type=TaskRunCommandType.CANCEL,
            client_mutation_id=mutation_id,
            command_digest=task_run_command_digest(
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                task_run_id=run.id,
                command_type=TaskRunCommandType.CANCEL,
                client_mutation_id=mutation_id,
                expected_run_revision=run.revision,
                request_digest=run.request_digest,
                manifest_hash=run.manifest_hash,
                temporal_namespace=run.temporal_namespace,
                temporal_workflow_id=run.temporal_workflow_id,
            ),
            expected_run_revision=run.revision,
            accepted_run_revision=run.revision + 1,
            request_digest=run.request_digest,
            manifest_hash=run.manifest_hash,
            temporal_namespace=run.temporal_namespace,
            temporal_workflow_id=run.temporal_workflow_id,
            status=TaskRunCommandStatus.PENDING,
            dispatch_attempts=0,
            created_by=run.requested_by,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )
        self.calls: list[tuple[object, ...]] = []

    async def cancel(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskRunCommandIntent]:
        self.calls.append(("cancel", *args, kwargs))
        return CommandResult(value=self.command, status_code=202, replayed=False)

    async def pause(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskRunCommandIntent]:
        self.calls.append(("pause", *args, kwargs))
        return CommandResult(value=self.command, status_code=202, replayed=False)

    async def resume(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskRunCommandIntent]:
        self.calls.append(("resume", *args, kwargs))
        return CommandResult(value=self.command, status_code=202, replayed=False)

    async def get(self, *args: object, **kwargs: object) -> TaskRunCommandIntent:
        self.calls.append(("command", *args, kwargs))
        return self.command


class RecordingTaskRunRerunService:
    def __init__(self, query: RecordingTaskRunQueryService) -> None:
        self.child = query.run.model_copy(
            update={
                "id": uid(7000),
                "rerun_of_task_run_id": query.run.id,
                "temporal_workflow_id": task_run_workflow_id(
                    tenant_id=query.run.tenant_id,
                    task_run_id=uid(7000),
                ),
            }
        )
        self.calls: list[tuple[object, ...]] = []

    async def rerun_infrastructure_failures(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskRun]:
        self.calls.append((*args, kwargs))
        return CommandResult(value=self.child, status_code=201, replayed=False)


def test_task_run_routes_expose_snapshots() -> None:
    service = RecordingTaskRunQueryService()
    actor = ActorContext(
        tenant_id=service.run.tenant_id,
        actor_id=service.run.requested_by,
        request_id="task-run-api-test",
        development_override=True,
    )
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = lambda: actor
    app.dependency_overrides[get_task_run_query_service] = lambda: cast(
        TaskRunQueryService,
        service,
    )

    with TestClient(app) as client:
        run_page = client.get(
            f"/v1/projects/{service.run.project_id}/task-runs?limit=7"
        )
        run = client.get(f"/v1/task-runs/{service.run.id}")
        manifest = client.get(f"/v1/task-runs/{service.run.id}/manifest")
        units = client.get(
            f"/v1/task-runs/{service.run.id}/units?afterOrdinal=1&limit=8"
        )
        attempts = client.get(
            f"/v1/task-runs/{service.run.id}/units/{service.units[0].id}/attempts"
            "?afterAttemptNumber=1&limit=9"
        )
        events = client.get(
            f"/v1/task-runs/{service.run.id}/events?afterSeq=2&limit=10"
        )

    assert run_page.status_code == 200
    assert run_page.json()["items"][0]["id"] == str(service.run.id)
    assert run.status_code == 200
    assert run.headers["etag"] == '"revision-1"'
    assert manifest.json()["manifestHash"] == service.manifest.manifest_hash
    assert len(units.json()["items"]) == 2
    assert len(attempts.json()["items"]) == 2
    assert events.json()["items"][0]["seq"] == 1
    assert [call[0] for call in service.calls] == [
        "runs",
        "run",
        "manifest",
        "units",
        "attempts",
        "events",
    ]
    assert service.calls[0][-1] == {"cursor": None, "limit": 7}
    assert service.calls[3][-1] == {"after_ordinal": 1, "limit": 8}
    assert service.calls[4][-1] == {"after_attempt_number": 1, "limit": 9}
    assert service.calls[5][-1] == {"after_seq": 2, "limit": 10}


def test_cancel_route_requires_revision_and_idempotency_then_exposes_status() -> None:
    query = RecordingTaskRunQueryService()
    service = RecordingTaskRunCommandService(query)
    actor = ActorContext(
        tenant_id=query.run.tenant_id,
        actor_id=query.run.requested_by,
        request_id="task-run-command-api-test",
        development_override=True,
    )
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = lambda: actor
    app.dependency_overrides[get_task_run_command_service] = lambda: cast(
        TaskRunCommandService,
        service,
    )

    with TestClient(app) as client:
        missing_headers = client.post(
            f"/v1/task-runs/{query.run.id}:cancel",
            json={"clientMutationId": "cancel-command-001"},
        )
        accepted = client.post(
            f"/v1/task-runs/{query.run.id}:cancel",
            headers={
                "If-Match": '"revision-1"',
                "Idempotency-Key": "cancel-command-001",
            },
            json=RequestTaskRunCancel(
                client_mutation_id="cancel-command-001"
            ).model_dump(mode="json", by_alias=True),
        )
        status_response = client.get(
            f"/v1/task-runs/{query.run.id}/commands/{service.command.id}"
        )

    assert missing_headers.status_code == 422
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "PENDING"
    assert accepted.headers["etag"] == '"revision-2"'
    assert accepted.headers["location"].endswith(
        f"/{query.run.id}/commands/{service.command.id}"
    )
    assert accepted.headers["idempotency-replayed"] == "false"
    assert status_response.status_code == 200
    assert status_response.json()["commandDigest"] == service.command.command_digest
    cancel_call = service.calls[0]
    assert cancel_call[0] == "cancel"
    assert cancel_call[-1] == {
        "expected_revision": 1,
        "idempotency_key": "cancel-command-001",
    }
    assert service.calls[1][0] == "command"


def test_pause_and_resume_routes_forward_exact_optimistic_control_contract() -> None:
    query = RecordingTaskRunQueryService()
    service = RecordingTaskRunCommandService(query)
    actor = ActorContext(
        tenant_id=query.run.tenant_id,
        actor_id=query.run.requested_by,
        request_id="task-run-control-api-test",
        development_override=True,
    )
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = lambda: actor
    app.dependency_overrides[get_task_run_command_service] = lambda: cast(
        TaskRunCommandService,
        service,
    )

    with TestClient(app) as client:
        pause = client.post(
            f"/v1/task-runs/{query.run.id}:pause",
            headers={
                "If-Match": '"revision-1"',
                "Idempotency-Key": "pause-command-001",
            },
            json=RequestTaskRunPause(
                client_mutation_id="pause-command-001"
            ).model_dump(mode="json", by_alias=True),
        )
        resume = client.post(
            f"/v1/task-runs/{query.run.id}:resume",
            headers={
                "If-Match": '"revision-2"',
                "Idempotency-Key": "resume-command-001",
            },
            json=RequestTaskRunResume(
                client_mutation_id="resume-command-001"
            ).model_dump(mode="json", by_alias=True),
        )

    assert pause.status_code == resume.status_code == 202
    assert pause.headers["location"].endswith(
        f"/{query.run.id}/commands/{service.command.id}"
    )
    assert resume.headers["idempotency-replayed"] == "false"
    pause_call, resume_call = service.calls
    assert pause_call[0] == "pause"
    assert pause_call[-1] == {
        "expected_revision": 1,
        "idempotency_key": "pause-command-001",
    }
    assert resume_call[0] == "resume"
    assert resume_call[-1] == {
        "expected_revision": 2,
        "idempotency_key": "resume-command-001",
    }


def test_infra_failure_rerun_route_creates_a_new_child_task_run() -> None:
    query = RecordingTaskRunQueryService()
    service = RecordingTaskRunRerunService(query)
    actor = ActorContext(
        tenant_id=query.run.tenant_id,
        actor_id=query.run.requested_by,
        request_id="task-run-rerun-api-test",
        development_override=True,
    )
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = lambda: actor
    app.dependency_overrides[get_task_run_rerun_service] = lambda: cast(
        TaskRunRerunService,
        service,
    )
    request = RequestTaskRunInfraFailureRerun(
        client_mutation_id="infra-rerun-api-001"
    )

    with TestClient(app) as client:
        missing_headers = client.post(
            f"/v1/task-runs/{query.run.id}:rerun-infra-failures",
            json=request.model_dump(mode="json", by_alias=True),
        )
        created = client.post(
            f"/v1/task-runs/{query.run.id}:rerun-infra-failures",
            headers={
                "If-Match": '"revision-1"',
                "Idempotency-Key": request.client_mutation_id,
            },
            json=request.model_dump(mode="json", by_alias=True),
        )

    assert missing_headers.status_code == 422
    assert created.status_code == 201
    assert created.json()["rerunOfTaskRunId"] == str(query.run.id)
    assert created.headers["etag"] == '"revision-1"'
    assert created.headers["location"] == f"/v1/task-runs/{service.child.id}"
    assert created.headers["idempotency-replayed"] == "false"
    assert service.calls[0][-1] == {
        "expected_revision": 1,
        "idempotency_key": request.client_mutation_id,
    }
