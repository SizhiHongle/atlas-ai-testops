"""Real PostgreSQL coverage for TaskPlan authoring and publication APIs."""

from __future__ import annotations

import asyncio
from os import environ
from uuid import uuid7

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.integration.test_cases_api import (
    RecordingDebugRunDispatcher,
    actor_headers,
)
from tests.integration.test_task_execution_hosts_pg import (
    SeededCaseVersion,
    TaskAggregate,
    _build_aggregate,
    _seed_published_case_version,
)

from atlas_testops.core.config import Settings
from atlas_testops.domain.task import (
    TASK_RETRY_POLICY_DIGEST_KEY,
    TaskRetryPolicy,
    task_retry_policy_digest,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.task_profiles import TaskProfileRepository
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


def test_task_plan_api_publishes_exact_dependencies_and_rejects_drift() -> None:
    """Create, replay, publish, query, and fail closed on an invalid matrix."""

    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)
    aggregate = _build_aggregate(seeded)
    asyncio.run(_insert_profiles(settings, seeded, aggregate))
    headers = actor_headers(str(seeded.tenant_id))
    application = create_app(
        settings,
        debug_run_dispatcher=RecordingDebugRunDispatcher(),
    )
    create_mutation = f"create-task-plan-{uuid7().hex}"
    publish_mutation = f"publish-task-plan-{uuid7().hex}"
    retry_policy = TaskRetryPolicy(
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

    with TestClient(application) as client:
        created = client.post(
            f"/v1/projects/{seeded.project_id}/task-plans",
            headers={**headers, "Idempotency-Key": create_mutation},
            json={
                "taskKey": aggregate.plan.task_key,
                "name": aggregate.plan.name,
                "clientMutationId": create_mutation,
            },
        )
        assert created.status_code == 201, created.text
        plan_id = created.json()["id"]

        replayed = client.post(
            f"/v1/projects/{seeded.project_id}/task-plans",
            headers={**headers, "Idempotency-Key": create_mutation},
            json={
                "taskKey": aggregate.plan.task_key,
                "name": aggregate.plan.name,
                "clientMutationId": create_mutation,
            },
        )
        assert replayed.status_code == 201, replayed.text
        assert replayed.headers["idempotency-replayed"] == "true"
        assert replayed.json()["id"] == plan_id

        publish_payload = {
            "version": "1.0.0",
            "pinnedCaseVersionIds": [str(seeded.case_version_id)],
            "matrix": aggregate.version.matrix.model_dump(
                mode="json",
                by_alias=True,
            ),
            "profileRefs": aggregate.version.profile_refs.model_dump(
                mode="json",
                by_alias=True,
            ),
            "policyDigests": {
                **aggregate.version.policy_digests,
                TASK_RETRY_POLICY_DIGEST_KEY: retry_policy.content_digest,
            },
            "clientMutationId": publish_mutation,
        }
        published = client.post(
            f"/v1/task-plans/{plan_id}/versions",
            headers={**headers, "Idempotency-Key": publish_mutation},
            json=publish_payload,
        )
        assert published.status_code == 201, published.text
        version_id = published.json()["id"]
        assert published.json()["taskPlanId"] == plan_id

        catalog = client.get(
            f"/v1/projects/{seeded.project_id}/task-plans",
            headers=headers,
        )
        plan = client.get(f"/v1/task-plans/{plan_id}", headers=headers)
        versions = client.get(
            f"/v1/task-plans/{plan_id}/versions",
            headers=headers,
        )
        version = client.get(
            f"/v1/task-plan-versions/{version_id}",
            headers=headers,
        )
        assert catalog.status_code == 200, catalog.text
        assert any(item["id"] == plan_id for item in catalog.json()["items"])
        assert plan.status_code == 200, plan.text
        assert versions.status_code == 200, versions.text
        assert versions.json()["items"][0]["id"] == version_id
        assert version.status_code == 200, version.text

        launch_mutation = f"manual-launch-{uuid7().hex}"
        launch_payload = {
            "clientMutationId": launch_mutation,
            "iterationId": "iteration:integration",
            "retryPolicy": retry_policy.model_dump(mode="json", by_alias=True),
        }
        launched = client.post(
            f"/v1/task-plan-versions/{version_id}:run",
            headers={**headers, "Idempotency-Key": launch_mutation},
            json=launch_payload,
        )
        assert launched.status_code == 201, launched.text
        assert launched.json()["triggerSource"] == "MANUAL"
        assert launched.json()["materializationState"] == "SEALED"
        assert launched.json()["materializedUnitCount"] == 1
        run_id = launched.json()["id"]

        launch_replay = client.post(
            f"/v1/task-plan-versions/{version_id}:run",
            headers={**headers, "Idempotency-Key": launch_mutation},
            json=launch_payload,
        )
        assert launch_replay.status_code == 201, launch_replay.text
        assert launch_replay.headers["idempotency-replayed"] == "true"
        assert launch_replay.json()["id"] == run_id

        ci_mutation = f"ci-trigger-{uuid7().hex}"
        ci_trigger_payload: dict[str, object] = {
            "source": "CI",
            "provider": "github",
            "pipelineRunId": "build-8421",
            "jobId": "test",
            "rerunIndex": 0,
            "commitSha": "abcdef1",
            "branch": "main",
        }
        ci_payload = {
            "taskPlanVersionId": version_id,
            "clientMutationId": ci_mutation,
            "trigger": ci_trigger_payload,
            "iterationId": "iteration:integration",
            "retryPolicy": retry_policy.model_dump(mode="json", by_alias=True),
        }
        ci_created = client.post(
            "/v1/task-runs",
            headers={**headers, "Idempotency-Key": ci_mutation},
            json=ci_payload,
        )
        assert ci_created.status_code == 201, ci_created.text
        assert ci_created.json()["triggerSource"] == "CI"
        ci_run_id = ci_created.json()["id"]

        duplicate_mutation = f"ci-trigger-{uuid7().hex}"
        ci_duplicate = client.post(
            "/v1/task-runs",
            headers={**headers, "Idempotency-Key": duplicate_mutation},
            json={
                **ci_payload,
                "clientMutationId": duplicate_mutation,
                "trigger": {
                    **ci_trigger_payload,
                    "commitSha": "1234567",
                    "branch": "release",
                },
            },
        )
        assert ci_duplicate.status_code == 200, ci_duplicate.text
        assert ci_duplicate.json()["id"] == ci_run_id
        assert ci_duplicate.headers["idempotency-replayed"] == "true"

        manifest = client.get(
            f"/v1/task-runs/{run_id}/manifest",
            headers=headers,
        )
        units = client.get(
            f"/v1/task-runs/{run_id}/units",
            headers=headers,
        )
        events = client.get(
            f"/v1/task-runs/{run_id}/events",
            headers=headers,
        )
        assert manifest.status_code == 200, manifest.text
        assert manifest.json()["schemaVersion"] == "atlas.task-run-manifest/0.2"
        assert manifest.json()["retryPolicy"] == launch_payload["retryPolicy"]
        assert units.status_code == 200, units.text
        assert len(units.json()["items"]) == 1
        assert events.status_code == 200, events.text
        assert events.json()["items"][0]["eventType"] == "task_run.requested"
        assert asyncio.run(
            _has_pending_start_intent(settings, seeded, run_id)
        )

        invalid_mutation = f"publish-invalid-{uuid7().hex}"
        invalid_matrix = aggregate.version.matrix.model_dump(
            mode="json",
            by_alias=True,
        )
        invalid_matrix["environmentIds"] = [str(uuid7())]
        rejected = client.post(
            f"/v1/task-plans/{plan_id}/versions",
            headers={**headers, "Idempotency-Key": invalid_mutation},
            json={
                **publish_payload,
                "version": "2.0.0",
                "matrix": invalid_matrix,
                "clientMutationId": invalid_mutation,
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["errorCode"] == "CONFLICT"


async def _insert_profiles(
    settings: Settings,
    seeded: SeededCaseVersion,
    aggregate: TaskAggregate,
) -> None:
    """Seed the already-defined exact profiles through their trusted repository."""

    database = Database(settings)
    profiles = TaskProfileRepository()
    await database.open()
    try:
        async with database.transaction(
            DatabaseContext(
                tenant_id=seeded.tenant_id,
                actor_id=seeded.actor_id,
                request_id=f"task-plan-profile-seed:{uuid7()}",
            )
        ) as connection:
            await profiles.create_execution_profile_version(
                connection,
                aggregate.execution_profile,
            )
            await profiles.create_identity_profile_version(
                connection,
                aggregate.identity_profile,
            )
            await profiles.create_browser_profile_version(
                connection,
                aggregate.browser_profile,
            )
            await profiles.create_data_profile_version(
                connection,
                aggregate.data_profile,
            )
    finally:
        await database.close()


async def _has_pending_start_intent(
    settings: Settings,
    seeded: SeededCaseVersion,
    task_run_id: str,
) -> bool:
    """Verify that sealing atomically created the durable workflow start intent."""

    database = Database(settings)
    await database.open()
    try:
        async with database.transaction(
            DatabaseContext(
                tenant_id=seeded.tenant_id,
                actor_id=seeded.actor_id,
                request_id=f"task-plan-launch-check:{uuid7()}",
            )
        ) as connection:
            cursor = await connection.execute(
                """
                select status
                from atlas.task_workflow_start_intent
                where task_run_id = %s
                """,
                (task_run_id,),
            )
            row = await cursor.fetchone()
            return row is not None and row["status"] == "PENDING"
    finally:
        await database.close()
