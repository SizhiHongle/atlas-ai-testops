"""Contract tests for Hygiene-bound FULLY_RESOLVED Snapshot persistence."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0036_task_result_fully_resolved.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_cleanup_truth_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0036"
    assert namespace["down_revision"] == "20260718_0035"
    assert "autocommit_block" not in source


def test_fully_resolved_binds_exact_terminal_hygiene_set() -> None:
    sql = _sql()

    assert "add column unit_hygiene_resolution_revision_ids uuid[]" in sql
    assert "add column input_hygiene_resolution_set_hash text" in sql
    assert "finality in ('quality_final', 'fully_resolved')" in sql
    assert "distinct on (hygiene.execution_unit_id)" in sql
    assert "jsonb_array_elements(hygiene.inputs)" in sql
    assert "fixture.revision" in sql
    assert "attempt.hygiene <> 'not_required'" in sql
    assert "data_hygiene in ('cleaned', 'leaked', 'not_applicable')" in sql
    assert "'atlas.task-result-hygiene-resolution-set/0.1'" in sql
    assert "expected_hygiene_ids <> new.unit_hygiene_resolution_revision_ids" in sql


def test_database_preserves_quality_axes_and_overlays_only_hygiene() -> None:
    sql = _sql()

    assert "expected_quality_axes" in sql
    assert "jsonb_set( expected_quality_axes, '{datahygiene}'" in sql
    assert "greatest(quality_watermark, hygiene_watermark)" in sql
    assert "fully_resolved requires its exact quality_final input" in sql
    assert "previous.finality = 'fully_resolved'" in sql
    assert "and new.finality = 'quality_final'" in sql
    assert "'atlas.task-result-snapshot/0.2'" in sql
    assert "expected_json_size := 25" in sql
    assert "sha256:e4a7985c6a76073cc78179e57330f373d7baa6eb25246c8932ee3fac71dcf759" in sql


def test_downgrade_preserves_quality_facts_but_refuses_full_facts() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while fully_resolved snapshot facts exist" in sql
    assert "where finality = 'fully_resolved'" in sql
    assert "drop column input_hygiene_resolution_set_hash" in sql
    assert "execute function atlas.guard_task_result_snapshot_insert()" in sql
    assert "cannot downgrade while taskresultsnapshot facts exist" not in sql
