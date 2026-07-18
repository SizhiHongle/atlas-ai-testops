"""Contract tests for least-privilege Task orchestration locking."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260716_0025_task_orchestration_locks.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _statements(name: str) -> tuple[str, ...]:
    return cast(tuple[str, ...], _namespace()[name])


def _sql(name: str) -> str:
    return " ".join("\n".join(_statements(name)).casefold().split())


def test_revision_extends_task_intent_delivery_transactionally() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260716_0025"
    assert namespace["down_revision"] == "20260716_0024"
    assert "autocommit_block" not in source
    assert "create role" not in source


def test_upgrade_requires_a_function_owner_that_can_cross_forced_rls() -> None:
    statements = _statements("UPGRADE_STATEMENTS")
    preflight = " ".join(statements[0].casefold().split())

    assert "from pg_catalog.pg_roles role" in preflight
    assert "role.rolname = current_user" in preflight
    assert "role.rolsuper or role.rolbypassrls" in preflight
    assert "function owner must bypass row-level security" in preflight
    assert "using errcode = '42501'" in preflight


def test_lock_function_is_tenant_scoped_sealed_and_strictly_ordered() -> None:
    sql = _sql("UPGRADE_STATEMENTS")

    assert "create function atlas.lock_task_execution_chain" in sql
    assert "p_execution_unit_id uuid default null" in sql
    assert "p_unit_attempt_id uuid default null" in sql
    assert "returns void" in sql
    assert "security definer" in sql
    assert "set search_path = pg_catalog, atlas" in sql
    assert "atlas.current_tenant_id() is null" in sql
    assert "using errcode = '42501'" in sql
    assert "p_unit_attempt_id is not null and p_execution_unit_id is null" in sql
    assert "using errcode = '22023'" in sql

    run_lock = sql.index("from atlas.task_run run")
    unit_lock = sql.index("from atlas.execution_unit unit")
    attempt_lock = sql.index("from atlas.unit_attempt attempt")
    assert run_lock < unit_lock < attempt_lock
    assert sql.count("for update") == 3
    assert "run.tenant_id = atlas.current_tenant_id()" in sql
    assert "run.materialization_state = 'sealed'" in sql
    assert "and not run.legacy_unsealed" in sql
    assert "unit.task_run_id = p_task_run_id" in sql
    assert "unit.manifest_hash = stored_manifest_hash" in sql
    assert "attempt.execution_unit_id = p_execution_unit_id" in sql
    assert "attempt.task_run_id = p_task_run_id" in sql
    assert "attempt.manifest_hash = stored_manifest_hash" in sql
    assert sql.count("using errcode = 'p0002'") == 3


def test_atlas_app_receives_only_function_execute_authority() -> None:
    statements = _statements("UPGRADE_STATEMENTS")
    sql = _sql("UPGRADE_STATEMENTS")

    signature = "atlas.lock_task_execution_chain(uuid, uuid, uuid)"
    assert f"revoke all on function {signature} from public, atlas_dispatcher" in sql
    assert f"grant execute on function {signature} to atlas_app" in sql
    assert "create function" in statements[1].casefold()
    assert "revoke all on function" in statements[2].casefold()
    assert "grant execute on function" in statements[3].casefold()
    assert "grant update" not in sql
    assert "grant select" not in sql
    assert "grant execute on function" in sql
    assert "to atlas_dispatcher" not in sql


def test_downgrade_revokes_and_drops_only_the_lock_boundary() -> None:
    statements = _statements("DOWNGRADE_STATEMENTS")
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert statements[0].casefold().startswith("revoke all on function")
    assert "from atlas_app" in sql
    assert "drop function if exists atlas.lock_task_execution_chain" in sql
    assert "alter table" not in sql
