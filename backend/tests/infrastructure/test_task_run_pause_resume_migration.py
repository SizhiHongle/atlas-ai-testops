"""Contract tests for durable TaskRun Pause/Resume storage."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260717_0029_task_run_pause_resume.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_is_transactional_extension_of_command_intents() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260717_0029"
    assert namespace["down_revision"] == "20260717_0028"
    assert "autocommit_block" not in source
    assert "create role" not in source


def test_v02_contract_adds_exact_pause_resume_and_superseded_shapes() -> None:
    sql = _sql()

    assert "schema_version = 'atlas.task-run-command/0.2'" in sql
    assert "command_type in ('cancel', 'pause', 'resume')" in sql
    assert "status = 'superseded'" in sql
    assert "superseded_at is not null" in sql
    assert "superseded_by_command_id is not null" in sql
    assert "task_run_command_open_pause_resume_idx" in sql
    assert "status in ('pending', 'claimed', 'retry_wait', 'delivered')" in sql


def test_insert_guard_locks_the_exact_command_specific_run_state() -> None:
    sql = _sql()
    guard = sql.split(
        "create or replace function atlas.guard_task_run_command_insert",
        maxsplit=1,
    )[1].split(
        "create or replace function atlas.guard_task_run_command_update",
        maxsplit=1,
    )[0]

    assert "from atlas.task_run run" in guard
    assert "for update" in guard
    assert "when 'cancel' then 'canceling'" in guard
    assert "when 'pause' then 'pause_requested'" in guard
    assert "when 'resume' then 'paused'" in guard
    assert "run_row.lifecycle <> expected_lifecycle" in guard
    assert "run_row.revision <> new.accepted_run_revision" in guard
    assert "'schemaversion', new.schema_version" in guard
    assert "new.command_digest <> expected_digest" in guard


def test_apply_functions_require_acknowledged_run_state_and_exact_identity() -> None:
    sql = _sql()
    pause = sql.split(
        "create function atlas.apply_task_run_pause_command",
        maxsplit=1,
    )[1].split(
        "create function atlas.apply_task_run_resume_command",
        maxsplit=1,
    )[0]
    resume = sql.split(
        "create function atlas.apply_task_run_resume_command",
        maxsplit=1,
    )[1].split(
        "create function atlas.supersede_task_run_commands",
        maxsplit=1,
    )[0]

    assert "command_row.command_type <> 'pause'" in pause
    assert "command_row.command_digest <> p_command_digest" in pause
    assert "run.lifecycle = 'paused'" in pause
    assert "status = 'applied'" in pause
    assert "command_row.command_type <> 'resume'" in resume
    assert "run.lifecycle = 'running'" in resume
    assert "status = 'applied'" in resume
    assert "to atlas_app" in sql
    assert "from public, atlas_dispatcher" in sql


def test_cancel_supersession_is_scoped_and_downgrade_refuses_v02_facts() -> None:
    sql = _sql()
    supersede = sql.split(
        "create function atlas.supersede_task_run_commands",
        maxsplit=1,
    )[1].split("revoke all on function", maxsplit=1)[0]
    downgrade = _sql("DOWNGRADE_STATEMENTS")

    assert "command.command_type = 'cancel'" in supersede
    assert "run.lifecycle = 'canceling'" in supersede
    assert "command.id <> p_superseding_command_id" in supersede
    assert "command.command_type in ('pause', 'resume')" in supersede
    assert "status = 'superseded'" in supersede
    assert "cannot downgrade task pause/resume after v0.2 commands were accepted" in downgrade
    assert downgrade.index("cannot downgrade") < downgrade.index(
        "drop function atlas.supersede_task_run_commands"
    )
