"""Contract tests for snapshot-bound failure attribution persistence."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0038_failure_classification.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_explicit_reevaluation_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0038"
    assert namespace["down_revision"] == "20260718_0037"
    assert "autocommit_block" not in source


def test_failure_cluster_is_snapshot_bound_append_only_and_database_revalidated() -> None:
    sql = _sql()

    assert "create table atlas.failure_cluster_revision" in sql
    assert "result_snapshot_id uuid not null" in sql
    assert "affected_unit_resolution_revision_ids uuid[] not null" in sql
    assert "failure_cluster_revision_initial_input_unique" in sql
    assert "source_snapshot.unit_resolution_revision_ids" in sql
    assert "source_is_diagnostic := (" in sql
    assert "expected_affected_ids := array_append" in sql
    assert "exact manifest-ordered signal group" in sql
    assert "new.fingerprint <> atlas.task_sha256_json(new.signal)" in sql
    assert "evidence_required_partial" in sql
    assert "evidence_integrity_unverified" in sql
    assert "failure_cluster_revision_prevent_mutation" in sql
    assert "force row level security" in sql
    assert "grant select, insert on atlas.failure_cluster_revision" in sql


def test_classification_is_evidence_backed_and_human_review_is_append_only() -> None:
    sql = _sql()

    assert "create table atlas.failure_classification_revision" in sql
    assert "supporting_evidence_refs jsonb not null" in sql
    assert "atlas.failure_classification_evidence_ref_valid" in sql
    assert "classification evidence must bind an exact cluster fact" in sql
    assert "classification evidence and gap codes must use canonical ordering" in sql
    assert "value ->> 'contentdigest'" in sql
    assert "first classification must be the exact rule judgment" in sql
    assert "human classification revision chain is invalid" in sql
    assert "human_confirmed cannot change attribution content" in sql
    assert "human_rejected requires unknown and contradiction evidence" in sql
    assert "failure_classification_revision_prevent_mutation" in sql
    assert "grant select, insert on atlas.failure_classification_revision" in sql


def test_canonical_projection_hashes_and_downgrade_refusal_are_enforced() -> None:
    upgrade_sql = _sql()
    downgrade_sql = _sql("DOWNGRADE_STATEMENTS")

    assert "atlas.task_json_object_size(new.cluster) <> 19" in upgrade_sql
    assert "atlas.task_json_object_size(new.classification) <> 26" in upgrade_sql
    assert "'createdat', 'clusterhash'" in upgrade_sql
    assert "'createdat', 'classificationhash'" in upgrade_sql
    assert "cannot downgrade while failurecluster or classification facts exist" in (
        downgrade_sql
    )
    assert "drop table atlas.failure_classification_revision" in downgrade_sql
    assert "drop table atlas.failure_cluster_revision" in downgrade_sql
