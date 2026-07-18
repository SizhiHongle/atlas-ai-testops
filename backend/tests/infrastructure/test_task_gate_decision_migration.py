"""Contract tests for exact Snapshot-bound Task Gate persistence."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0039_task_gate_decision.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_classification_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0039"
    assert namespace["down_revision"] == "20260718_0038"
    assert "autocommit_block" not in source


def test_gate_is_append_only_and_binds_exact_snapshot_policy_and_actor() -> None:
    sql = _sql()

    assert "create table atlas.task_gate_decision" in sql
    assert "result_snapshot_id uuid not null" in sql
    assert "failure_classification_revision_ids uuid[] not null" in sql
    assert "classification_set_hash text not null" in sql
    assert "gate_policy_version = '0.1.0'" in sql
    assert "new.evaluated_by <> atlas.current_actor_id()" in sql
    assert "task_gate_decision_prevent_mutation" in sql
    assert "force row level security" in sql
    assert "grant select, insert on atlas.task_gate_decision" in sql


def test_database_recomputes_cluster_coverage_latest_classifications_and_hash() -> None:
    sql = _sql()

    assert "expected_diagnostic_ids" in sql
    assert "complete non-overlapping cluster coverage" in sql
    assert "distinct on (cluster.failure_cluster_id)" in sql
    assert "order by source.revision desc" in sql
    assert "latest_cluster_count <> latest_classification_count" in sql
    assert "complete latest classification set" in sql
    assert "'atlas.task-gate-classification-set/0.1'" in sql
    assert "expected_classification_set_hash := atlas.task_sha256_json" in sql


def test_database_recomputes_three_value_verdict_and_canonical_projection() -> None:
    sql = _sql()
    downgrade_sql = _sql("DOWNGRADE_STATEMENTS")

    assert "classification_not_gate_ready" in sql
    assert "snapshot_not_fully_resolved" in sql
    assert "evidence_invalid_or_unverified" in sql
    assert "expected_decision := 'inconclusive'" in sql
    assert "expected_decision := 'rejected'" in sql
    assert "expected_decision := 'accepted'" in sql
    assert "atlas.task_json_object_size(new.decision_document) <> 20" in sql
    assert "'evaluatedat', 'decisionhash'" in sql
    assert "cannot downgrade while taskgatedecision facts exist" in downgrade_sql
