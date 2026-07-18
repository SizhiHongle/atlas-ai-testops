"""Real PostgreSQL permission coverage for Task Workflow intent delivery."""

from os import environ
from time import sleep

import psycopg
import pytest

OWNER_DATABASE_URL = environ.get("ATLAS_TEST_OWNER_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        OWNER_DATABASE_URL is None,
        reason="ATLAS_TEST_OWNER_DATABASE_URL is not configured",
    ),
]


def _pending_namespace() -> str | None:
    assert OWNER_DATABASE_URL is not None
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        row = connection.execute(
            """
            select namespace
            from atlas.task_workflow_start_intent
            where owner_kind = 'TASK_RUN'
              and workflow_type = 'AtlasTaskRunWorkflow'
              and task_queue = 'atlas-task-run'
              and status = 'PENDING'
            order by available_at, created_at, id
            limit 1
            """
        ).fetchone()
    return None if row is None else str(row[0])


def _claim(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    claimed_by: str,
    namespace: str,
    lease_seconds: int,
) -> list[tuple[object, ...]]:
    return connection.execute(
        """
        select *
        from atlas.claim_task_workflow_start_intents(%s, %s, 100, %s)
        """,
        (claimed_by, namespace, lease_seconds),
    ).fetchall()


def _claim_until(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    claimed_by: str,
    namespace: str,
    lease_seconds: int,
    intent_id: object,
) -> tuple[object, ...]:
    """Drain bounded older backlog until the target eligible intent is returned."""

    for _ in range(256):
        claims = _claim(
            connection,
            claimed_by=claimed_by,
            namespace=namespace,
            lease_seconds=lease_seconds,
        )
        target = next((row for row in claims if row[0] == intent_id), None)
        if target is not None:
            return target
        if not claims:
            break
    raise AssertionError("target Task Workflow start intent was not claimable")


def test_dispatcher_can_only_execute_fenced_functions_owned_by_rls_bypass_role() -> None:
    """Prevent a local superuser from hiding an unusable production permission model."""

    assert OWNER_DATABASE_URL is not None
    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        role_row = connection.execute(
            """
            select dispatcher.rolcanlogin,
                   dispatcher.rolsuper,
                   dispatcher.rolbypassrls,
                   owner.rolsuper or owner.rolbypassrls
            from pg_catalog.pg_roles dispatcher
            join pg_catalog.pg_proc function
              on function.proname = 'claim_task_workflow_start_intents'
            join pg_catalog.pg_namespace namespace
              on namespace.oid = function.pronamespace
             and namespace.nspname = 'atlas'
            join pg_catalog.pg_roles owner on owner.oid = function.proowner
            where dispatcher.rolname = 'atlas_dispatcher'
            """
        ).fetchone()
        assert role_row == (True, False, False, True)

        privilege_row = connection.execute(
            """
            select
              has_schema_privilege('atlas_dispatcher', 'atlas', 'USAGE'),
              has_table_privilege(
                'atlas_dispatcher', 'atlas.task_workflow_start_intent', 'SELECT'
              ) or has_table_privilege(
                'atlas_dispatcher', 'atlas.task_workflow_start_intent', 'INSERT'
              ) or has_table_privilege(
                'atlas_dispatcher', 'atlas.task_workflow_start_intent', 'UPDATE'
              ) or has_table_privilege(
                'atlas_dispatcher', 'atlas.task_workflow_start_intent', 'DELETE'
              ),
              has_function_privilege(
                'atlas_dispatcher',
                'atlas.claim_task_workflow_start_intents(text,text,integer,integer)',
                'EXECUTE'
              ),
              has_function_privilege(
                'atlas_app',
                'atlas.claim_task_workflow_start_intents(text,text,integer,integer)',
                'EXECUTE'
              )
            """
        ).fetchone()
        assert privilege_row == (True, False, True, False)

        connection.execute("set session authorization atlas_dispatcher")
        claimed_count = connection.execute(
            """
            select count(*)
            from atlas.claim_task_workflow_start_intents(
              'integration-permission-probe', 'default', 1, 30
            )
            """
        ).fetchone()
        assert claimed_count is not None
        assert claimed_count[0] in (0, 1)
        connection.rollback()


def test_concurrent_dispatchers_skip_each_others_locked_intents() -> None:
    """Keep overlapping Consumers non-blocking and prevent duplicate claims."""

    assert OWNER_DATABASE_URL is not None
    namespace = _pending_namespace()
    if namespace is None:
        pytest.skip("No PENDING Task Workflow start intent is available")
    with (
        psycopg.connect(OWNER_DATABASE_URL) as first_connection,
        psycopg.connect(OWNER_DATABASE_URL) as second_connection,
    ):
        first_connection.execute("set session authorization atlas_dispatcher")
        second_connection.execute("set session authorization atlas_dispatcher")

        first_claim = first_connection.execute(
            """
            select id
            from atlas.claim_task_workflow_start_intents(%s, %s, 1, 30)
            """,
            ("integration-consumer-a", namespace),
        ).fetchone()
        assert first_claim is not None

        second_claim = second_connection.execute(
            """
            select id
            from atlas.claim_task_workflow_start_intents(%s, %s, 1, 30)
            """,
            ("integration-consumer-b", namespace),
        ).fetchone()
        assert second_claim is None or second_claim[0] != first_claim[0]

        second_connection.rollback()
        first_connection.rollback()


def test_delivery_state_machine_fences_retries_and_terminal_states() -> None:
    """Exercise namespace isolation, lease takeover, retry delay, and terminals."""

    assert OWNER_DATABASE_URL is not None
    namespace = _pending_namespace()
    if namespace is None:
        pytest.skip("No PENDING Task Workflow start intent is available")

    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute("set session authorization atlas_dispatcher")
        assert (
            _claim(
                connection,
                claimed_by="integration-wrong-namespace",
                namespace="integration-missing-namespace",
                lease_seconds=1,
            )
            == []
        )

        initial_claims = _claim(
            connection,
            claimed_by="integration-state-a",
            namespace=namespace,
            lease_seconds=1,
        )
        assert initial_claims
        first = initial_claims[0]
        intent_id = first[0]
        first_token = first[13]
        assert isinstance(first[14], int)
        assert isinstance(first[15], int)
        first_revision = first[14]
        first_attempts = first[15]

        sleep(1.1)
        takeover = _claim_until(
            connection,
            claimed_by="integration-state-b",
            namespace=namespace,
            lease_seconds=30,
            intent_id=intent_id,
        )
        assert takeover[13] != first_token
        assert takeover[14] == first_revision + 1
        assert takeover[15] == first_attempts + 1

        stale_started = connection.execute(
            """
            select atlas.mark_task_workflow_start_intent_started(%s, %s, %s)
            """,
            (intent_id, first_token, first_revision),
        ).fetchone()
        assert stale_started == (False,)

        retried = connection.execute(
            """
            select atlas.retry_task_workflow_start_intent(%s, %s, %s, %s, %s)
            """,
            (intent_id, takeover[13], takeover[14], "TEMPORAL_UNAVAILABLE", 250),
        ).fetchone()
        assert retried == (True,)
        early_claims = _claim(
            connection,
            claimed_by="integration-state-c",
            namespace=namespace,
            lease_seconds=30,
        )
        assert intent_id not in {row[0] for row in early_claims}

        sleep(0.3)
        due = _claim_until(
            connection,
            claimed_by="integration-state-c",
            namespace=namespace,
            lease_seconds=30,
            intent_id=intent_id,
        )
        started = connection.execute(
            """
            select atlas.mark_task_workflow_start_intent_started(%s, %s, %s)
            """,
            (intent_id, due[13], due[14]),
        ).fetchone()
        assert started == (True,)
        assert intent_id not in {
            row[0]
            for row in _claim(
                connection,
                claimed_by="integration-state-d",
                namespace=namespace,
                lease_seconds=30,
            )
        }
        connection.rollback()

    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        connection.execute("set session authorization atlas_dispatcher")
        claimed = _claim(
            connection,
            claimed_by="integration-terminal-failure",
            namespace=namespace,
            lease_seconds=30,
        )[0]
        failed = connection.execute(
            """
            select atlas.fail_task_workflow_start_intent(%s, %s, %s, %s)
            """,
            (claimed[0], claimed[13], claimed[14], "TEMPORAL_REJECTED"),
        ).fetchone()
        assert failed == (True,)
        assert claimed[0] not in {
            row[0]
            for row in _claim(
                connection,
                claimed_by="integration-after-failure",
                namespace=namespace,
                lease_seconds=30,
            )
        }
        connection.rollback()
