"""Contract tests for ClosureNotice and UnitResolution persistence."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0033_unit_result_resolution.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_attempt_seal_truth_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0033"
    assert namespace["down_revision"] == "20260718_0032"
    assert "autocommit_block" not in source


def test_closure_notice_is_exact_append_only_terminal_coverage() -> None:
    sql = _sql()

    assert "create table atlas.attempt_closure_notice" in sql
    assert "attempt_closure_notice_attempt_scope_fk" in sql
    assert "attempt_closure_notice_attempt_unique unique (unit_attempt_id)" in sql
    assert "before update or delete on atlas.attempt_closure_notice" in sql
    assert "stored_attempt.lifecycle <> 'closed'" in sql
    assert "new.created_at <> transaction_timestamp()" in sql
    assert "atlas.task_sha256_json(new.notice - 'noticehash')" in sql
    assert "attempt_closure_notice_terminal_exclusivity" in sql
    assert "already has a closurenotice" in sql
    assert "already has an attemptseal" in sql
    assert "stored_attempt.quality = 'inconclusive'" in sql
    assert "stored_attempt.quality = 'infra_error'" in sql
    assert "outcome_class = 'automation'" in sql


def test_resolution_revision_recomputes_full_input_set_and_chain() -> None:
    sql = _sql()

    assert "create table atlas.unit_resolution_revision" in sql
    assert "unit_resolution_revision_unique unique" in sql
    assert "unit_resolution_input_unique unique" in sql
    assert "unit_resolution_supersedes_fk" in sql
    assert "unitresolution revision chain is invalid" in sql
    assert "attempt.lifecycle = 'closed'" in sql
    assert "'atlas.unit-resolution-input-set/0.1'" in sql
    assert "expected_input_hash := atlas.task_sha256_json" in sql
    assert "unitresolution decisive projection is invalid" in sql
    assert "unitresolution stability is invalid" in sql
    assert "before update or delete on atlas.unit_resolution_revision" in sql


def test_projection_tables_force_rls_and_use_minimum_privileges() -> None:
    sql = _sql()

    for table in ("attempt_closure_notice", "unit_resolution_revision"):
        assert f"alter table atlas.{table} force row level security" in sql
        assert f"grant select, insert on atlas.{table} to atlas_app" in sql
        assert f"revoke all on atlas.{table} from atlas_app" in sql


def test_downgrade_refuses_to_discard_projection_truth() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while result projection facts exist" in sql
    assert sql.index("cannot downgrade") < sql.index(
        "drop table if exists atlas.unit_resolution_revision"
    )
