"""Contract tests for the Browser Runtime database guards."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260715_0017_browser_runtime_reports.py"
)


def _upgrade_sql() -> str:
    namespace = run_path(str(MIGRATION_PATH))
    statements = cast(tuple[str, ...], namespace["UPGRADE_STATEMENTS"])
    return "\n".join(statements)


def test_report_guard_closes_one_unique_action_sequence() -> None:
    sql = _upgrade_sql()

    assert "create unique index browser_runtime_report_action_kind_unique" in sql
    assert "previous_record.report_kind = 'action.proposed'" in sql
    assert "new.report_kind <> 'policy.decided'" in sql
    assert "previous_record.payload ->> 'decision' = 'ALLOW'" in sql
    assert "and new.report_kind <> 'execution.blocked'" in sql
    assert "new.report_kind <> 'action.executed'" in sql
    assert "non-allowed policy decision must block execution" in sql
    assert "proposal_record.payload ->> 'action'" in sql
    assert "browser action id is already present in this report chain" in sql


def test_manifest_guard_rejects_conclusive_unsafe_execution() -> None:
    sql = _upgrade_sql()

    assert "report.report_kind = 'execution.blocked'" in sql
    assert "report.payload ->> 'status' is distinct from 'SUCCEEDED'" in sql
    assert "new.outcome <> 'INCONCLUSIVE'" in sql
    assert "result.status <> 'INCONCLUSIVE'" in sql
    assert "item ->> 'status' is distinct from 'INCONCLUSIVE'" in sql
