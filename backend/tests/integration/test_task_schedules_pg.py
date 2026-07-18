"""Real PostgreSQL coverage for Schedule catalog, desired state, and auto-pause."""

from __future__ import annotations

import asyncio
from os import environ
from uuid import uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.integration.test_cases_api import (
    RecordingDebugRunDispatcher,
    actor_headers,
)
from tests.integration.test_task_execution_hosts_pg import (
    SeededCaseVersion,
    _build_aggregate,
    _seed_published_case_version,
)
from tests.integration.test_task_plans_api_pg import _insert_profiles

from atlas_testops.core.config import Settings
from atlas_testops.domain.task import (
    TASK_RETRY_POLICY_DIGEST_KEY,
    TaskRetryPolicy,
    task_retry_policy_digest,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


def test_task_schedule_catalog_pause_resume_rls_and_environment_auto_pause() -> None:
    """Keep Schedule desired state durable and fail closed after production promotion."""

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
    policy = _retry_policy()
    app = create_app(
        settings,
        debug_run_dispatcher=RecordingDebugRunDispatcher(),
    )
    suffix = uuid7().hex[-10:]

    with TestClient(app) as client:
        plan_mutation = f"schedule-plan-{suffix}"
        created_plan = client.post(
            f"/v1/projects/{seeded.project_id}/task-plans",
            headers={**headers, "Idempotency-Key": plan_mutation},
            json={
                "taskKey": f"schedule.{suffix}",
                "name": "Schedule integration",
                "clientMutationId": plan_mutation,
            },
        )
        assert created_plan.status_code == 201, created_plan.text

        publish_mutation = f"schedule-publish-{suffix}"
        published = client.post(
            f"/v1/task-plans/{created_plan.json()['id']}/versions",
            headers={**headers, "Idempotency-Key": publish_mutation},
            json={
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
                    TASK_RETRY_POLICY_DIGEST_KEY: policy.content_digest,
                },
                "clientMutationId": publish_mutation,
            },
        )
        assert published.status_code == 201, published.text
        version_id = published.json()["id"]

        create_mutation = f"schedule-create-{suffix}"
        create_payload = {
            "scheduleKey": f"nightly.{suffix}",
            "name": "CRM nightly",
            "calendar": {
                "minutes": [0],
                "hours": [2],
                "daysOfMonth": [],
                "months": [],
                "isoDaysOfWeek": [],
            },
            "timeZoneName": "Asia/Shanghai",
            "overlapPolicy": "QUEUE_ONE",
            "catchupPolicy": "RUN_ONCE",
            "catchupWindowSeconds": 3600,
            "jitterSeconds": 30,
            "iterationId": "iteration:nightly",
            "retryPolicy": policy.model_dump(mode="json", by_alias=True),
            "clientMutationId": create_mutation,
        }
        created = client.post(
            f"/v1/task-plan-versions/{version_id}/schedules",
            headers={**headers, "Idempotency-Key": create_mutation},
            json=create_payload,
        )
        assert created.status_code == 201, created.text
        assert created.headers["etag"] == '"revision-1"'
        assert created.json()["status"] == "ACTIVE"
        assert created.json()["syncStatus"] == "PENDING"
        assert len(created.json()["nextFireTimesUtc"]) == 5
        schedule_id = created.json()["id"]

        replayed = client.post(
            f"/v1/task-plan-versions/{version_id}/schedules",
            headers={**headers, "Idempotency-Key": create_mutation},
            json=create_payload,
        )
        assert replayed.status_code == 201, replayed.text
        assert replayed.headers["idempotency-replayed"] == "true"
        assert replayed.json()["id"] == schedule_id

        listed = client.get(
            f"/v1/task-plan-versions/{version_id}/schedules",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["items"]] == [schedule_id]

        cross_tenant = client.get(
            f"/v1/schedules/{schedule_id}",
            headers=actor_headers(str(seeded.other_tenant_id)),
        )
        assert cross_tenant.status_code == 404

        pause_mutation = f"schedule-pause-{suffix}"
        paused = client.post(
            f"/v1/schedules/{schedule_id}:pause",
            headers={
                **headers,
                "If-Match": '"revision-1"',
                "Idempotency-Key": pause_mutation,
            },
            json={
                "clientMutationId": pause_mutation,
                "reason": "维护窗口",
            },
        )
        assert paused.status_code == 202, paused.text
        assert paused.json()["status"] == "PAUSED"
        assert paused.json()["revision"] == 2

        stale_pause = client.post(
            f"/v1/schedules/{schedule_id}:pause",
            headers={
                **headers,
                "If-Match": '"revision-1"',
                "Idempotency-Key": f"stale-{pause_mutation}",
            },
            json={
                "clientMutationId": f"stale-{pause_mutation}",
                "reason": "陈旧请求",
            },
        )
        assert stale_pause.status_code == 412, stale_pause.text

        resume_mutation = f"schedule-resume-{suffix}"
        resumed = client.post(
            f"/v1/schedules/{schedule_id}:resume",
            headers={
                **headers,
                "If-Match": '"revision-2"',
                "Idempotency-Key": resume_mutation,
            },
            json={
                "clientMutationId": resume_mutation,
                "reason": "维护完成",
            },
        )
        assert resumed.status_code == 202, resumed.text
        assert resumed.json()["status"] == "ACTIVE"
        assert resumed.json()["revision"] == 3

        environment = client.get(
            f"/v1/environments/{seeded.environment_id}",
            headers=headers,
        )
        assert environment.status_code == 200, environment.text
        promoted = client.patch(
            f"/v1/environments/{seeded.environment_id}",
            headers={**headers, "If-Match": environment.headers["etag"]},
            json={"kind": "PRODUCTION"},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["kind"] == "PRODUCTION"

        auto_paused = client.get(
            f"/v1/schedules/{schedule_id}",
            headers=headers,
        )
        assert auto_paused.status_code == 200, auto_paused.text
        assert auto_paused.json()["status"] == "PAUSED"
        assert auto_paused.json()["pauseReason"] == ("ENVIRONMENT_RECLASSIFIED_AS_PRODUCTION")
        assert auto_paused.json()["revision"] == 4

        blocked_resume_mutation = f"schedule-blocked-resume-{suffix}"
        blocked_resume = client.post(
            f"/v1/schedules/{schedule_id}:resume",
            headers={
                **headers,
                "If-Match": '"revision-4"',
                "Idempotency-Key": blocked_resume_mutation,
            },
            json={
                "clientMutationId": blocked_resume_mutation,
                "reason": "不应恢复生产环境",
            },
        )
        assert blocked_resume.status_code == 409, blocked_resume.text

    projection = asyncio.run(_schedule_projection(settings, seeded, schedule_id))
    assert projection["schedule_status"] == "PAUSED"
    assert projection["schedule_revision"] == 4
    assert projection["actions"] == (
        "CREATE",
        "PAUSE",
        "RESUME",
        "AUTO_PAUSE",
    )
    assert projection["auto_pause_audit"] == 1
    assert projection["auto_pause_outbox"] == 1

    with psycopg.connect(DATABASE_URL) as connection:
        privileges = connection.execute(
            """
            select
              has_table_privilege(
                'atlas_dispatcher', 'atlas.task_schedule', 'SELECT'
              ),
              has_table_privilege(
                'atlas_dispatcher', 'atlas.task_schedule_sync_intent', 'UPDATE'
              ),
              has_table_privilege(
                'atlas_app', 'atlas.task_schedule_sync_intent', 'UPDATE'
              ),
              has_function_privilege(
                'atlas_dispatcher',
                'atlas.claim_task_schedule_sync_intents(text,text,integer,integer)',
                'EXECUTE'
              )
            """
        ).fetchone()
    assert privileges == (False, False, False, True)


def _retry_policy() -> TaskRetryPolicy:
    digest = task_retry_policy_digest(
        infra_retry_attempts=2,
        max_total_infra_retries=8,
        initial_backoff_seconds=2,
        maximum_backoff_seconds=30,
        jitter_percent=10,
    )
    return TaskRetryPolicy(
        infra_retry_attempts=2,
        max_total_infra_retries=8,
        initial_backoff_seconds=2,
        maximum_backoff_seconds=30,
        jitter_percent=10,
        content_digest=digest,
    )


async def _schedule_projection(
    settings: Settings,
    seeded: SeededCaseVersion,
    schedule_id: str,
) -> dict[str, object]:
    database = Database(settings)
    await database.open()
    try:
        async with database.transaction(
            DatabaseContext(
                tenant_id=seeded.tenant_id,
                actor_id=seeded.actor_id,
                request_id=f"schedule-projection:{uuid7()}",
            )
        ) as connection:
            schedule = await (
                await connection.execute(
                    """
                    select status, revision
                    from atlas.task_schedule
                    where id = %s
                    """,
                    (schedule_id,),
                )
            ).fetchone()
            actions = await (
                await connection.execute(
                    """
                    select action
                    from atlas.task_schedule_sync_intent
                    where task_schedule_id = %s
                    order by schedule_revision
                    """,
                    (schedule_id,),
                )
            ).fetchall()
            audit = await (
                await connection.execute(
                    """
                    select count(*) as count
                    from atlas.audit_event
                    where entity_id = %s
                      and event_type = 'task_schedule.auto_paused'
                    """,
                    (schedule_id,),
                )
            ).fetchone()
            outbox = await (
                await connection.execute(
                    """
                    select count(*) as count
                    from atlas.outbox_event
                    where aggregate_id = %s
                      and event_type = 'task_schedule.auto_paused'
                    """,
                    (schedule_id,),
                )
            ).fetchone()
            assert schedule is not None and audit is not None and outbox is not None
            return {
                "schedule_status": schedule["status"],
                "schedule_revision": schedule["revision"],
                "actions": tuple(row["action"] for row in actions),
                "auto_pause_audit": audit["count"],
                "auto_pause_outbox": outbox["count"],
            }
    finally:
        await database.close()
