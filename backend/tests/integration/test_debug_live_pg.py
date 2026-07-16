"""Real PostgreSQL coverage for DebugRun live snapshot and replay guarantees."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import environ
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID, uuid7

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from tests.integration.test_cases_api import (
    RecordingDebugRunDispatcher,
    bootstrap_environment,
    bootstrap_project,
    case_payload,
)

from atlas_testops.application.access import AccessGrant, ActorContext
from atlas_testops.application.live import DebugLiveService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.auth import PlatformRole
from atlas_testops.domain.case import DebugRun, DebugRunLifecycle
from atlas_testops.domain.runtime import (
    DebugLiveCursor,
    DebugLiveEvent,
    decode_debug_live_cursor,
    encode_debug_live_cursor,
)
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.repositories.debug_runs import DebugRunRepository
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")
OWNER_DATABASE_URL = environ.get("ATLAS_TEST_OWNER_DATABASE_URL")
ALEMBIC_CONFIG_PATH = Path(__file__).parents[2] / "alembic.ini"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


@dataclass(frozen=True, slots=True)
class SeededDebugLiveRun:
    tenant_id: UUID
    project_id: UUID
    other_project_id: UUID
    other_tenant_id: UUID
    other_tenant_project_id: UUID
    run: DebugRun
    event_ids: tuple[UUID, UUID, UUID, UUID]


class CountingConnection:
    """Count repository statements while forwarding them to real PostgreSQL."""

    def __init__(self, connection: AsyncConnection[DictRow]) -> None:
        self._connection = connection
        self.statements: list[str] = []

    async def execute(self, query: object, params: object = None) -> Any:
        self.statements.append(str(query))
        return await cast(Any, self._connection).execute(query, params)


def test_debug_live_snapshot_replay_isolation_and_event_hardening() -> None:
    """Exercise exact head cursors, gapless replay, RLS, and staged guards."""

    assert DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=4,
    )
    seeded = _seed_terminal_run(settings)

    asyncio.run(_exercise_live_service(settings, seeded))
    _assert_payload_bound(seeded)
    _assert_event_immutability(seeded)


@pytest.mark.skipif(
    OWNER_DATABASE_URL is None,
    reason="ATLAS_TEST_OWNER_DATABASE_URL is not configured",
)
def test_debug_live_historical_payload_validation_is_retryable() -> None:
    """Leave 0019 repairable when 0020 rejects oversized historical data."""

    assert DATABASE_URL is not None
    assert OWNER_DATABASE_URL is not None
    config = Config(str(ALEMBIC_CONFIG_PATH))
    oversized_event_id = uuid7()
    with patch.dict(environ, {"ATLAS_DATABASE_URL": OWNER_DATABASE_URL}):
        command.upgrade(config, "head")
        settings = Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=4,
        )
        seeded = _seed_terminal_run(settings)
        try:
            command.downgrade(config, "20260715_0018")
            _insert_historical_oversized_event(seeded, oversized_event_id)

            command.upgrade(config, "20260716_0019")
            assert _migration_state() == ("20260716_0019", False, False, True)

            with pytest.raises(IntegrityError) as captured:
                command.upgrade(config, "20260716_0020")
            assert "debug_run_event_payload_size_valid" in str(captured.value)
            assert _migration_state() == ("20260716_0019", False, False, True)

            _repair_historical_event(oversized_event_id)
            command.upgrade(config, "20260716_0020")
            assert _migration_state() == ("20260716_0020", True, True, True)
            command.upgrade(config, "20260716_0021")
            assert _migration_state() == ("20260716_0021", True, True, False)
        finally:
            current_revision = _migration_state()[0]
            if current_revision in {"20260715_0018", "20260716_0019"}:
                _repair_historical_event(oversized_event_id)
            command.upgrade(config, "head")


def _seed_terminal_run(settings: Settings) -> SeededDebugLiveRun:
    assert DATABASE_URL is not None
    suffix = uuid7().hex[-10:]
    application = create_app(
        settings,
        debug_run_dispatcher=RecordingDebugRunDispatcher(),
    )
    with TestClient(application) as client:
        tenant_id, project_id, headers = bootstrap_project(client, suffix)
        other_project = client.post(
            "/v1/projects",
            headers={**headers, "Idempotency-Key": f"live-other-project-{suffix}"},
            json={
                "projectKey": f"LIVE_OTHER_{suffix.upper()}",
                "name": "Live Other Project",
            },
        )
        assert other_project.status_code == 201, other_project.text
        other_tenant_id, other_tenant_project_id, _ = bootstrap_project(
            client,
            f"x{suffix}",
        )
        environment_id = bootstrap_environment(
            client,
            project_id,
            headers,
            suffix,
        )
        created = client.post(
            f"/v1/projects/{project_id}/test-cases",
            headers={**headers, "Idempotency-Key": f"live-case-{suffix}"},
            json=case_payload(f"L{suffix}"),
        )
        assert created.status_code == 201, created.text
        draft = client.get(
            f"/v1/test-cases/{created.json()['id']}/workflow-draft",
            headers=headers,
        )
        assert draft.status_code == 200, draft.text
        started = client.post(
            f"/v1/test-cases/{created.json()['id']}/workflow-draft/debug-runs",
            headers={
                **headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": f"live-run-{suffix}",
            },
            json={
                "environmentId": environment_id,
                "baseSemanticRevision": 1,
                "executionDeadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            },
        )
        assert started.status_code == 202, started.text
        run = DebugRun.model_validate(started.json())

    event_ids = _terminate_with_gapless_events(run)
    return SeededDebugLiveRun(
        tenant_id=UUID(tenant_id),
        project_id=UUID(project_id),
        other_project_id=UUID(cast(str, other_project.json()["id"])),
        other_tenant_id=UUID(other_tenant_id),
        other_tenant_project_id=UUID(other_tenant_project_id),
        run=run,
        event_ids=event_ids,
    )


def _terminate_with_gapless_events(
    run: DebugRun,
) -> tuple[UUID, UUID, UUID, UUID]:
    assert DATABASE_URL is not None
    requested_event_id: UUID
    finalizing_event_id = uuid7()
    terminated_event_id = uuid7()
    outdated_event_id = uuid7()
    started_at = datetime.now(UTC)
    completed_at = started_at + timedelta(milliseconds=1)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, true)",
            (str(run.tenant_id),),
        )
        requested_row = connection.execute(
            """
            select id
            from atlas.debug_run_event
            where debug_run_id = %s and seq = 1
            """,
            (run.id,),
        ).fetchone()
        assert requested_row is not None
        requested_event_id = cast(UUID, requested_row[0])
        connection.execute(
            """
            update atlas.debug_run
            set lifecycle = 'FINALIZING', started_at = %s,
                failure_code = 'LIVE_TEST_FAILED',
                failure_detail = 'Live integration test terminal state.',
                revision = revision + 1
            where id = %s
            """,
            (started_at, run.id),
        )
        connection.execute(
            """
            insert into atlas.debug_run_event (
              id, tenant_id, project_id, test_case_id, debug_run_id,
              seq, event_type, lifecycle, outcome, snapshot_status,
              payload, occurred_at
            )
            select
              %s, tenant_id, project_id, test_case_id, id,
              2, 'debug_run.finalizing', lifecycle, outcome, snapshot_status,
              %s, %s
            from atlas.debug_run
            where id = %s
            """,
            (
                finalizing_event_id,
                Jsonb({"completeness": "PARTIAL", "integrity": "VALID"}),
                started_at,
                run.id,
            ),
        )
        connection.execute(
            """
            update atlas.debug_run
            set lifecycle = 'TERMINATED', outcome = 'FAILED',
                completed_at = %s, revision = revision + 1
            where id = %s
            """,
            (completed_at, run.id),
        )
        connection.execute(
            """
            insert into atlas.debug_run_event (
              id, tenant_id, project_id, test_case_id, debug_run_id,
              seq, event_type, lifecycle, outcome, snapshot_status,
              payload, occurred_at
            )
            select
              %s, tenant_id, project_id, test_case_id, id,
              3, 'debug_run.terminated', lifecycle, outcome, snapshot_status,
              %s, %s
            from atlas.debug_run
            where id = %s
            """,
            (
                terminated_event_id,
                Jsonb(
                    {
                        "outcome": "FAILED",
                        "failureDetail": "must not reach Live clients",
                    }
                ),
                completed_at,
                run.id,
            ),
        )
        outdated_at = completed_at + timedelta(milliseconds=1)
        connection.execute(
            """
            update atlas.debug_run
            set snapshot_status = 'OUTDATED', outdated_at = %s,
                revision = revision + 1
            where id = %s
            """,
            (outdated_at, run.id),
        )
        connection.execute(
            """
            insert into atlas.debug_run_event (
              id, tenant_id, project_id, test_case_id, debug_run_id,
              seq, event_type, lifecycle, outcome, snapshot_status,
              payload, occurred_at
            )
            select
              %s, tenant_id, project_id, test_case_id, id,
              4, 'debug_run.snapshot_outdated', lifecycle, outcome,
              snapshot_status, %s, %s
            from atlas.debug_run
            where id = %s
            """,
            (
                outdated_event_id,
                Jsonb(
                    {
                        "currentSemanticRevision": 2,
                        "currentSemanticDigest": "sha256:" + "f" * 64,
                    }
                ),
                outdated_at,
                run.id,
            ),
        )
    return (
        requested_event_id,
        finalizing_event_id,
        terminated_event_id,
        outdated_event_id,
    )


async def _exercise_live_service(
    settings: Settings,
    seeded: SeededDebugLiveRun,
) -> None:
    database = Database(settings)
    repository = DebugRunRepository()
    await database.open()
    try:
        actor = _observer(seeded.tenant_id, seeded.project_id)
        async with database.transaction(actor.database_context()) as connection:
            counted = CountingConnection(connection)
            seed = await repository.get_live_seed(
                cast(AsyncConnection[DictRow], counted),
                seeded.run.id,
            )

        assert seed is not None
        assert len(counted.statements) == 1
        normalized_statement = " ".join(counted.statements[0].split()).casefold()
        assert "left join lateral" in normalized_statement
        assert "jsonb_build_object" in normalized_statement
        assert "test_ir" not in normalized_statement
        assert "plan_template" not in normalized_statement
        assert "failure_detail" not in normalized_statement
        assert "for update" not in normalized_statement
        assert seed.head_seq == 4
        assert seed.latest_event is not None
        assert seed.latest_event.id == seeded.event_ids[3]

        service = DebugLiveService(
            database,
            poll_interval_seconds=0.01,
            heartbeat_interval_seconds=0.05,
            maximum_connection_seconds=0.2,
            batch_size=100,
        )
        snapshot = await service.get_snapshot(actor, seeded.run.id)
        snapshot_cursor = decode_debug_live_cursor(
            snapshot.cursor,
            expected_run_id=seeded.run.id,
        )
        assert snapshot_cursor.after_seq == 4
        assert snapshot.latest_event is not None
        assert snapshot.latest_event.seq == 4
        assert snapshot.latest_event.data == {"currentSemanticRevision": 2}
        assert "must not reach Live clients" not in snapshot.model_dump_json()

        last_event_id = encode_debug_live_cursor(
            DebugLiveCursor(debug_run_id=seeded.run.id, after_seq=1)
        )
        plan = await service.prepare_stream(
            actor,
            seeded.run.id,
            last_event_id=last_event_id,
        )
        assert plan.snapshot is None
        assert plan.after_seq == 1
        event_stream = cast(
            AsyncGenerator[DebugLiveEvent | None],
            service.iter_events(
                actor,
                seeded.run.id,
                plan,
                is_disconnected=_connected,
            ),
        )
        replayed = [await anext(event_stream) for _ in range(3)]
        await event_stream.aclose()
        assert all(event is not None for event in replayed)
        replayed_events = [event for event in replayed if event is not None]
        assert [event.seq for event in replayed_events] == [2, 3, 4]
        assert [event.event_type for event in replayed_events] == [
            "debug_run.finalizing",
            "debug_run.terminated",
            "debug_run.snapshot_outdated",
        ]
        assert replayed_events[-1].lifecycle is DebugRunLifecycle.TERMINATED
        assert replayed_events[-1].data == {"currentSemanticRevision": 2}

        at_head = await service.prepare_stream(
            actor,
            seeded.run.id,
            last_event_id=snapshot.cursor,
        )
        assert [
            item
            async for item in service.iter_events(
                actor,
                seeded.run.id,
                at_head,
                is_disconnected=_disconnected,
            )
        ] == []

        await _assert_not_found(
            service,
            _observer(seeded.tenant_id, seeded.other_project_id),
            seeded.run.id,
        )
        await _assert_not_found(
            service,
            _observer(
                seeded.other_tenant_id,
                seeded.other_tenant_project_id,
            ),
            seeded.run.id,
        )
    finally:
        await database.close()


async def _connected() -> bool:
    return False


async def _disconnected() -> bool:
    return True


async def _assert_not_found(
    service: DebugLiveService,
    actor: ActorContext,
    run_id: UUID,
) -> None:
    with pytest.raises(ApplicationError) as captured:
        await service.get_snapshot(actor, run_id)

    assert captured.value.error_code is ErrorCode.NOT_FOUND
    assert captured.value.status_code == 404


def _observer(tenant_id: UUID, project_id: UUID) -> ActorContext:
    return ActorContext(
        tenant_id=tenant_id,
        actor_id=uuid7(),
        request_id=f"debug-live-pg:{uuid7()}",
        current_project_id=project_id,
        grants=(AccessGrant(role=PlatformRole.OBSERVER, project_id=project_id),),
    )


def _insert_historical_oversized_event(
    seeded: SeededDebugLiveRun,
    event_id: UUID,
) -> None:
    assert OWNER_DATABASE_URL is not None
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, true)",
            (str(seeded.tenant_id),),
        )
        connection.execute(
            """
            insert into atlas.debug_run_event (
              id, tenant_id, project_id, test_case_id, debug_run_id,
              seq, event_type, lifecycle, outcome, snapshot_status,
              payload, occurred_at
            )
            select
              %s, tenant_id, project_id, test_case_id, id,
              5, 'debug_run.test.historical_oversized', lifecycle, outcome,
              snapshot_status, %s, clock_timestamp()
            from atlas.debug_run
            where id = %s
            """,
            (
                event_id,
                Jsonb({"blob": "x" * 32768}),
                seeded.run.id,
            ),
        )


def _repair_historical_event(event_id: UUID) -> None:
    assert OWNER_DATABASE_URL is not None
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute(
            """
            update atlas.debug_run_event
            set payload = '{"repaired": true}'::jsonb
            where id = %s
            """,
            (event_id,),
        )


def _migration_state() -> tuple[str, bool | None, bool, bool]:
    assert OWNER_DATABASE_URL is not None
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        row = connection.execute(
            """
            select
              (select version_num from alembic_version),
              (
                select convalidated
                from pg_constraint
                where conrelid = 'atlas.debug_run_event'::regclass
                  and conname = 'debug_run_event_payload_size_valid'
              ),
              exists (
                select 1
                from pg_trigger
                where tgrelid = 'atlas.debug_run_event'::regclass
                  and not tgisinternal
                  and tgname = 'debug_run_event_prevent_mutation'
              ),
              to_regclass('atlas.debug_run_event_replay_idx') is not null
            """
        ).fetchone()
    assert row is not None
    return (
        cast(str, row[0]),
        cast(bool | None, row[1]),
        cast(bool, row[2]),
        cast(bool, row[3]),
    )


def _assert_payload_bound(seeded: SeededDebugLiveRun) -> None:
    assert DATABASE_URL is not None
    oversized_event_id = uuid7()
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, true)",
            (str(seeded.tenant_id),),
        )
        with (
            pytest.raises(psycopg.errors.CheckViolation) as captured,
            connection.transaction(),
        ):
            connection.execute(
                """
                insert into atlas.debug_run_event (
                  id, tenant_id, project_id, test_case_id, debug_run_id,
                  seq, event_type, lifecycle, outcome, snapshot_status,
                  payload, occurred_at
                )
                select
                  %s, tenant_id, project_id, test_case_id, id,
                  5, 'debug_run.test.oversized', lifecycle, outcome,
                  snapshot_status, %s, clock_timestamp()
                from atlas.debug_run
                where id = %s
                """,
                (
                    oversized_event_id,
                    Jsonb({"blob": "x" * 32768}),
                    seeded.run.id,
                ),
            )
        assert captured.value.diag.constraint_name == ("debug_run_event_payload_size_valid")
        row = connection.execute(
            "select coalesce(max(seq), 0) from atlas.debug_run_event where debug_run_id = %s",
            (seeded.run.id,),
        ).fetchone()
        assert row == (4,)


def _assert_event_immutability(seeded: SeededDebugLiveRun) -> None:
    if OWNER_DATABASE_URL is None:
        assert DATABASE_URL is not None
        with psycopg.connect(DATABASE_URL) as connection:
            privileges = connection.execute(
                """
                select
                  has_table_privilege(current_user, 'atlas.debug_run_event', 'UPDATE'),
                  has_table_privilege(current_user, 'atlas.debug_run_event', 'DELETE')
                """
            ).fetchone()
        assert privileges == (False, False)
        return

    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, true)",
            (str(seeded.tenant_id),),
        )
        with (
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
            connection.transaction(),
        ):
            connection.execute(
                """
                update atlas.debug_run_event
                set payload = '{"tampered": true}'::jsonb
                where id = %s
                """,
                (seeded.event_ids[1],),
            )
        with (
            pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
            connection.transaction(),
        ):
            connection.execute(
                "delete from atlas.debug_run_event where id = %s",
                (seeded.event_ids[1],),
            )
