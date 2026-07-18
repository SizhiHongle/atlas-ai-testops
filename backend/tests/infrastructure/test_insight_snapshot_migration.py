"""Static contract tests for reproducible InsightSnapshot persistence."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0040_insight_snapshots.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_task_gate_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0040"
    assert namespace["down_revision"] == "20260718_0039"
    assert "autocommit_block" not in source


def test_snapshot_is_append_only_rls_scoped_and_idempotent() -> None:
    sql = _sql()

    assert "create table atlas.insight_snapshot" in sql
    assert "client_mutation_id text not null" in sql
    assert "insight_snapshot_mutation_unique" in sql
    assert "insight_snapshot_prevent_mutation" in sql
    assert "force row level security" in sql
    assert "grant select, insert on atlas.insight_snapshot to atlas_app" in sql
    assert "grant update" not in sql
    assert "grant delete" not in sql


def test_database_reselects_exact_result_and_gate_source_cut_as_of() -> None:
    sql = _sql()

    assert "distinct on (source.task_run_id)" in sql
    assert "source.finality in ('fully_resolved', 'reevaluated')" in sql
    assert "run.finalized_at >= new.baseline_start_at" in sql
    assert "source.created_at <= new.as_of" in sql
    assert "decision.evaluated_at <= new.as_of" in sql
    assert "new.source_snapshot_ids is distinct from expected_source_ids" in sql
    assert "new.gate_decision_ids is distinct from expected_gate_ids" in sql
    assert "datasetcut is stale or incomplete" in sql


def test_database_recomputes_source_and_snapshot_hashes_and_blocks_downgrade() -> None:
    sql = _sql()
    downgrade_sql = _sql("DOWNGRADE_STATEMENTS")

    assert "'result:' || id::text || ':' || snapshot_hash" in sql
    assert "'gate:' || gate_id::text || ':' || gate_hash" in sql
    assert "public.digest(" in sql
    assert "expected_source_set_digest" in sql
    assert "atlas.task_sha256_json" in sql
    assert "'atlas.insight-snapshot/0.1'" in sql
    assert "cannot downgrade while insightsnapshot facts exist" in downgrade_sql
