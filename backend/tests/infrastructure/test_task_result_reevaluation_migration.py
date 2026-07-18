"""Contract tests for explicit command-bound REEVALUATED persistence."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0037_task_result_reevaluation.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_full_snapshot_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0037"
    assert namespace["down_revision"] == "20260718_0036"
    assert "autocommit_block" not in source


def test_explicit_command_is_immutable_tenant_scoped_and_policy_bound() -> None:
    sql = _sql()

    assert "create table atlas.task_result_reevaluation_command" in sql
    assert "source_snapshot_id uuid not null" in sql
    assert "target_policy_version = '0.3.0'" in sql
    assert "task_result_reevaluation_command_mutation_unique" in sql
    assert "task_result_reevaluation_command_prevent_mutation" in sql
    assert "force row level security" in sql
    assert "grant select, insert on atlas.task_result_reevaluation_command" in sql
    assert "atlas.task_sha256_json(new.command - 'commandhash')" in sql


def test_reevaluated_snapshot_preserves_exact_full_source_and_command() -> None:
    sql = _sql()

    assert "finality in ('quality_final', 'fully_resolved', 'reevaluated')" in sql
    assert "add column reevaluation_source_snapshot_id uuid" in sql
    assert "add column reevaluation_command_id uuid" in sql
    assert "source.finality <> 'fully_resolved'" in sql
    assert "command.source_snapshot_id <> source.id" in sql
    assert "new.unit_resolution_revision_ids is distinct from source." in sql
    assert "new.axis_distributions is distinct from source.axis_distributions" in sql
    assert "new.raw_pass_rate is distinct from source.raw_pass_rate" in sql
    assert "atlas.task_json_object_size(new.snapshot) <> 27" in sql
    assert "'reevaluationcommandid', 'createdat', 'snapshothash'" in sql


def test_phase_guard_blocks_automatic_backward_projection() -> None:
    sql = _sql()

    assert "previous.finality in ('fully_resolved', 'reevaluated')" in sql
    assert "previous.finality = 'reevaluated'" in sql
    assert "and new.finality = 'fully_resolved'" in sql
    assert "when (new.finality <> 'reevaluated')" in sql
    assert "when (new.finality = 'reevaluated')" in sql


def test_downgrade_refuses_command_or_reevaluated_facts() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while reevaluated snapshot or command facts exist" in sql
    assert "from atlas.task_result_reevaluation_command" in sql
    assert "drop column reevaluation_command_id" in sql
    assert "execute function atlas.guard_task_result_snapshot_v2_insert()" in sql
