"""Static guardrails for recoverable large TaskRun materialization."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0042_partitioned_task_materialization.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_live_control_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0042"
    assert namespace["down_revision"] == "20260718_0041"
    assert "autocommit_block" not in source


def test_partition_checkpoints_are_bounded_rls_protected_and_dispatcher_only() -> None:
    sql = _sql()

    assert "create table atlas.task_run_materialization_partition" in sql
    assert "last_ordinal between first_ordinal and first_ordinal + 63" in sql
    assert "partition_index between 0 and 1562" in sql
    assert (
        "alter table atlas.task_run_materialization_partition "
        "force row level security"
    ) in sql
    assert "session_user = 'atlas_dispatcher'" in sql
    assert (
        "revoke all on atlas.task_run_materialization_partition "
        "from atlas_dispatcher"
    ) in sql
    assert (
        "grant execute on function "
        "atlas.claim_task_run_materialization_partitions"
    ) in sql
    assert "for update skip locked" in sql


def test_large_manifest_seals_only_after_complete_partition_coverage() -> None:
    sql = _sql()

    assert "jsonb_array_length(value) not between 1 and 100000" in sql
    assert "unit_count between 1 and 100000" in sql
    assert "stored_manifest.unit_count > 64" in sql
    assert "partition.status <> 'completed'" in sql
    assert "ceiling(stored_manifest.unit_count / 64.0)" in sql
    assert "materialized_at_value + interval '30 days'" in sql
    assert "atlas.seal_task_run_materialization" in sql
    assert sql.index("status = 'completed'") < sql.rindex(
        "atlas.seal_task_run_materialization"
    )


def test_partition_completion_uses_exact_claim_and_creates_no_partial_checkpoint() -> None:
    sql = _sql()

    assert "stored_partition.claim_token is distinct from p_claim_token" in sql
    assert "stored_partition.revision <> p_expected_revision" in sql
    assert "stored_partition.claim_expires_at <= materialized_at_value" in sql
    assert "insert into atlas.execution_unit" in sql
    assert "insert into atlas.unit_attempt" in sql
    assert "materialized_unit_count = expected_count" in sql
    assert "materialized_first_attempt_count = expected_count" in sql


def test_downgrade_refuses_to_erase_partitioned_facts() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while partitioned taskrun materialization facts exist" in sql
    assert "where unit_count > 64" in sql
    assert "jsonb_array_length(value) not between 1 and 64" in sql
