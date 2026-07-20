"""Static database guardrails for DebugRun live frames and planner receipts."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATIONS_PATH = Path(__file__).parents[2] / "migrations" / "versions"
FRAME_MIGRATION = MIGRATIONS_PATH / "20260720_0045_debug_live_frames.py"
PLANNER_MIGRATION = MIGRATIONS_PATH / "20260720_0046_browser_planner_reports.py"


def _frame_sql(name: str = "UPGRADE_STATEMENTS") -> str:
    namespace = run_path(str(FRAME_MIGRATION))
    statements = cast(tuple[str, ...], namespace[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revisions_extend_the_single_migration_head() -> None:
    frame = run_path(str(FRAME_MIGRATION))
    planner = run_path(str(PLANNER_MIGRATION))

    assert frame["revision"] == "20260720_0045"
    assert frame["down_revision"] == "20260718_0044"
    assert planner["revision"] == "20260720_0046"
    assert planner["down_revision"] == "20260720_0045"


def test_live_frame_is_bounded_scoped_and_tenant_isolated() -> None:
    sql = _frame_sql()

    assert "debug_run_id uuid primary key" in sql
    assert "debug_live_frame_contract_scope_fk" in sql
    assert "references atlas.execution_contract" in sql
    assert "size_bytes between 1 and 716800" in sql
    assert "octet_length(payload) = size_bytes" in sql
    assert "content_digest ~ '^sha256:[0-9a-f]{64}$'" in sql
    assert "force row level security" in sql
    assert "create policy debug_live_frame_tenant_isolation" in sql
    assert "grant select, insert, update on atlas.debug_live_frame to atlas_app" in sql


def test_live_frame_transition_is_monotonic_and_delete_protected() -> None:
    sql = _frame_sql()
    downgrade = _frame_sql("DOWNGRADE_STATEMENTS")

    assert "if tg_op = 'delete'" in sql
    assert "new.frame_revision <= old.frame_revision" in sql
    assert "new.captured_at < old.captured_at" in sql
    assert "before update or delete on atlas.debug_live_frame" in sql
    assert "drop table if exists atlas.debug_live_frame" in downgrade


def test_planner_receipt_is_added_without_invalidating_historical_downgrades() -> None:
    namespace = run_path(str(PLANNER_MIGRATION))
    upgrade_kinds = cast(str, namespace["_REPORT_KINDS_WITH_PLANNER"])
    legacy_kinds = cast(str, namespace["_LEGACY_REPORT_KINDS"])
    source = PLANNER_MIGRATION.read_text(encoding="utf-8").casefold()

    assert "'planner.completed'" in upgrade_kinds
    assert "'planner.completed'" not in legacy_kinds
    assert "drop constraint browser_report_kind_valid" in source
    assert "not valid" in source
