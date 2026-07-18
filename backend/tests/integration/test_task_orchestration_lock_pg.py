"""Real PostgreSQL permission coverage for Task orchestration locking."""

from __future__ import annotations

import asyncio
from os import environ
from uuid import uuid7

import psycopg
import pytest
from pydantic import SecretStr
from tests.integration.test_task_execution_hosts_pg import (
    SeededCaseVersion,
    TaskAggregate,
    _build_aggregate,
    _seed_published_case_version,
)
from tests.integration.test_task_orchestration_pg import _persist_sealed_aggregate

from atlas_testops.core.config import Settings
from atlas_testops.infrastructure.database import Database

OWNER_DATABASE_URL = environ.get("ATLAS_TEST_OWNER_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        OWNER_DATABASE_URL is None,
        reason="ATLAS_TEST_OWNER_DATABASE_URL is not configured",
    ),
]


def test_atlas_app_locks_only_exact_same_tenant_chain_through_function() -> None:
    """Keep atlas_app table updates denied while permitting one trusted lock function."""

    assert OWNER_DATABASE_URL is not None
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(OWNER_DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    seeded = _seed_published_case_version(settings)
    aggregate = asyncio.run(_seed_aggregate(settings, seeded))

    with psycopg.connect(OWNER_DATABASE_URL) as connection:
        privilege_row = connection.execute(
            """
            select function.prosecdef,
                   owner.rolsuper or owner.rolbypassrls,
                   has_function_privilege(
                     'atlas_app',
                     'atlas.lock_task_execution_chain(uuid,uuid,uuid)',
                     'EXECUTE'
                   ),
                   has_function_privilege(
                     'atlas_dispatcher',
                     'atlas.lock_task_execution_chain(uuid,uuid,uuid)',
                     'EXECUTE'
                   ),
                   not exists (
                     select 1
                     from aclexplode(
                       coalesce(
                         function.proacl,
                         acldefault('f', function.proowner)
                       )
                     ) acl
                     where acl.grantee = 0
                       and acl.privilege_type = 'EXECUTE'
                   ),
                   (
                     select coalesce(
                       array_agg(
                         coalesce(grantee.rolname, 'PUBLIC')
                         order by coalesce(grantee.rolname, 'PUBLIC')
                       ),
                       array[]::text[]
                     )
                     from aclexplode(
                       coalesce(
                         function.proacl,
                         acldefault('f', function.proowner)
                       )
                     ) acl
                     left join pg_catalog.pg_roles grantee
                       on grantee.oid = acl.grantee
                     where acl.grantee <> function.proowner
                       and acl.privilege_type = 'EXECUTE'
                   ),
                   has_any_column_privilege(
                     'atlas_app', 'atlas.task_run', 'UPDATE'
                   ),
                   has_any_column_privilege(
                     'atlas_app', 'atlas.execution_unit', 'UPDATE'
                   ),
                   has_any_column_privilege(
                     'atlas_app', 'atlas.unit_attempt', 'UPDATE'
                   )
            from pg_catalog.pg_proc function
            join pg_catalog.pg_namespace namespace
              on namespace.oid = function.pronamespace
             and namespace.nspname = 'atlas'
            join pg_catalog.pg_roles owner on owner.oid = function.proowner
            where function.proname = 'lock_task_execution_chain'
            """
        ).fetchone()
        assert privilege_row == (
            True,
            True,
            True,
            False,
            True,
            ["atlas_app"],
            False,
            False,
            False,
        )

        connection.execute("set session authorization atlas_app")
        connection.commit()

        _set_local_tenant(connection, seeded.tenant_id)
        locked = connection.execute(
            "select atlas.lock_task_execution_chain(%s, %s, %s)",
            (aggregate.run.id, aggregate.unit.id, aggregate.attempt.id),
        ).fetchone()
        assert locked is not None and len(locked) == 1
        with pytest.raises(psycopg.Error) as direct_lock:
            connection.execute(
                "select id from atlas.task_run where id = %s for update",
                (aggregate.run.id,),
            )
        assert direct_lock.value.sqlstate == "42501"
        connection.rollback()

        _assert_lock_rejected(
            connection,
            tenant_id=seeded.other_tenant_id,
            task_run_id=aggregate.run.id,
            execution_unit_id=aggregate.unit.id,
            unit_attempt_id=aggregate.attempt.id,
            sqlstate="P0002",
        )
        _assert_lock_rejected(
            connection,
            tenant_id=seeded.tenant_id,
            task_run_id=aggregate.run.id,
            execution_unit_id=uuid7(),
            unit_attempt_id=None,
            sqlstate="P0002",
        )
        _assert_lock_rejected(
            connection,
            tenant_id=seeded.tenant_id,
            task_run_id=aggregate.run.id,
            execution_unit_id=aggregate.unit.id,
            unit_attempt_id=uuid7(),
            sqlstate="P0002",
        )
        _assert_lock_rejected(
            connection,
            tenant_id=seeded.tenant_id,
            task_run_id=aggregate.run.id,
            execution_unit_id=None,
            unit_attempt_id=aggregate.attempt.id,
            sqlstate="22023",
        )


async def _seed_aggregate(
    settings: Settings,
    seeded: SeededCaseVersion,
) -> TaskAggregate:
    database = Database(settings)
    await database.open()
    try:
        return await _persist_sealed_aggregate(database, _build_aggregate(seeded))
    finally:
        await database.close()


def _set_local_tenant(
    connection: psycopg.Connection[tuple[object, ...]],
    tenant_id: object,
) -> None:
    connection.execute(
        "select set_config('atlas.tenant_id', %s, true)",
        (str(tenant_id),),
    )


def _assert_lock_rejected(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    tenant_id: object,
    task_run_id: object,
    execution_unit_id: object,
    unit_attempt_id: object,
    sqlstate: str,
) -> None:
    _set_local_tenant(connection, tenant_id)
    with pytest.raises(psycopg.Error) as rejected:
        connection.execute(
            "select atlas.lock_task_execution_chain(%s, %s, %s)",
            (task_run_id, execution_unit_id, unit_attempt_id),
        )
    assert rejected.value.sqlstate == sqlstate
    connection.rollback()
