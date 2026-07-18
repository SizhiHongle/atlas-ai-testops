"""Contract tests for database-proven manual infrastructure-failure reruns."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260717_0031_task_infra_failure_rerun.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_infrastructure_retry_transactionally() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260717_0031"
    assert namespace["down_revision"] == "20260717_0030"
    assert "autocommit_block" not in source


def test_rerun_mode_is_lineage_bound_and_immutable() -> None:
    sql = _sql()

    assert "add column rerun_selection_mode text" in sql
    assert "rerun_of_task_run_id is not null" in sql
    assert "rerun_selection_mode = 'infra_failures'" in sql
    assert "before update of rerun_selection_mode" in sql
    assert "rerun selection mode is immutable" in sql


def test_manifest_guard_proves_exact_closed_infra_failure_selection() -> None:
    sql = _sql()

    assert "returns trigger language plpgsql security definer" in sql
    assert (
        "set search_path = pg_catalog, atlas"
        in sql
    )
    assert (
        "revoke all on function atlas.guard_task_run_infra_rerun_manifest_insert() "
        "from public, atlas_app, atlas_dispatcher"
        in sql
    )
    assert "parent_run.materialization_state <> 'sealed'" in sql
    assert "parent_run.lifecycle <> 'closed'" in sql
    assert "unit.lifecycle = 'closed'" in sql
    assert "unit.quality = 'infra_error'" in sql
    assert "row_number() over (order by unit.unit_key)" in sql
    assert "new.units is distinct from expected_units" in sql
    assert "every and only failed infrastructure unit" in sql
    assert "new.retry_policy is distinct from parent_manifest.retry_policy" in sql


def test_downgrade_refuses_to_discard_rerun_facts() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while infrastructure rerun taskrun facts exist" in sql
    assert sql.index("cannot downgrade") < sql.index(
        "drop column rerun_selection_mode"
    )
