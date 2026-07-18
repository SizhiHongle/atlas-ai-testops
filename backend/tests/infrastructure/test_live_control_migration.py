"""Static guardrails for the UnitAttempt live-control migration."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0041_unit_attempt_live_control.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_insights_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0041"
    assert namespace["down_revision"] == "20260718_0040"
    assert "autocommit_block" not in source


def test_live_control_is_attempt_scoped_rls_protected_and_fenced() -> None:
    sql = _sql()

    for table in (
        "live_session",
        "control_lease",
        "live_control_command",
        "live_action_grant",
        "live_control_event",
    ):
        assert f"create table atlas.{table}" in sql
        assert f"alter table atlas.{table} force row level security" in sql
    assert "live_session_attempt_scope_fk" in sql
    assert "live_session_ticket_scope_fk" in sql
    assert "control_lease_one_current_idx" in sql
    assert "live_control_command_one_pending_idx" in sql
    assert "live_action_grant_action_unique" in sql
    assert "new.control_epoch = old.control_epoch + 1" in sql
    assert "new.fencing_token = old.fencing_token + 1" in sql
    assert "controllease heartbeat must only extend expiry" in sql
    assert "state, expires_at, updated_at, released_at" in sql
    assert "new.state in ('quiescing', 'no_controller', 'closed')" in sql


def test_action_grants_are_single_use_and_human_influence_blocks_autonomous_seal() -> None:
    sql = _sql()

    assert "max_executions = 1" in sql
    assert "old.state = 'issued' and new.state in ('consumed', 'revoked')" in sql
    assert "old.state = 'consumed' and new.state = 'completed'" in sql
    assert "unit_attempt_result_fact_live_influence_guard" in sql
    assert "human-influenced execution cannot be sealed as autonomous" in sql
    assert "grant update (" in sql
    assert "grant delete" not in sql


def test_downgrade_refuses_to_erase_live_control_facts() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while live-control facts exist" in sql
