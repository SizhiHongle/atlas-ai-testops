"""Contract tests for append-only TaskResultSnapshot persistence."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2] / "migrations" / "versions" / "20260718_0034_task_result_snapshot.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_unit_resolution_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0034"
    assert namespace["down_revision"] == "20260718_0033"
    assert "autocommit_block" not in source


def test_snapshot_is_append_only_and_binds_exact_resolution_set() -> None:
    sql = _sql()

    assert "create table atlas.task_result_snapshot" in sql
    assert "task_result_snapshot_revision_unique unique" in sql
    assert "task_result_snapshot_input_unique unique" in sql
    assert "task_result_snapshot_supersedes_fk" in sql
    assert "before update or delete on atlas.task_result_snapshot" in sql
    assert "stored_run.lifecycle <> 'closed'" in sql
    assert "stored_run.materialization_state <> 'sealed'" in sql
    assert "distinct on (resolution.execution_unit_id)" in sql
    assert "terminal.closed_attempt_count = terminal.terminal_fact_count" in sql
    assert "terminal.seal_ids = latest_resolution.input_seal_ids" in sql
    assert ("terminal.closure_notice_ids = latest_resolution.input_closure_notice_ids") in sql
    assert "'atlas.task-result-resolution-set/0.1'" in sql
    assert "expected_input_hash := atlas.task_sha256_json" in sql
    assert "expected_resolution_ids <> new.unit_resolution_revision_ids" in sql
    assert "taskresultsnapshot resolution coverage is invalid" in sql


def test_database_recomputes_counts_rates_policy_and_snapshot_hash() -> None:
    sql = _sql()

    assert "passed_count + failed_count + inconclusive_count" in sql
    assert "trusted_passed_count" in sql
    assert "autonomous_passed_count" in sql
    assert "'denominator', passed_count + failed_count" in sql
    assert "new.axis_distributions <> expected_axis_distributions" in sql
    assert "new.snapshot ?& array[" in sql
    assert "is distinct from new.snapshot_hash" in sql
    assert "new.snapshot - array[" in sql
    assert "'supersedessnapshotid'" in sql
    assert "sha256:f047f7c9925cce522ccf743a0dcaf69d89f9a5d60a6856ab7654de971be8951e" in sql


def test_snapshot_forces_rls_and_minimum_privileges() -> None:
    sql = _sql()

    assert "alter table atlas.task_result_snapshot force row level security" in sql
    assert "revoke all on atlas.task_result_snapshot from atlas_app" in sql
    assert "grant select, insert on atlas.task_result_snapshot to atlas_app" in sql
    assert (
        "revoke all on function atlas.guard_task_result_snapshot_insert() "
        "from public, atlas_app, atlas_dispatcher"
    ) in sql


def test_downgrade_refuses_to_discard_snapshot_truth() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while taskresultsnapshot facts exist" in sql
    assert sql.index("cannot downgrade") < sql.index(
        "drop table if exists atlas.task_result_snapshot"
    )
