"""Static guardrails for Task Schedule catalog and Temporal synchronization."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2] / "migrations" / "versions" / "20260718_0043_task_schedule_catalog.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_partition_execution_transactionally() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0043"
    assert namespace["down_revision"] == "20260718_0042"
    assert "autocommit_block" not in source


def test_schedule_definition_is_bounded_exact_and_rls_protected() -> None:
    sql = _sql()

    assert "create table atlas.task_schedule (" in sql
    assert "atlas.task_schedule_calendar_valid(calendar)" in sql
    assert "overlap_policy in ('queue_one', 'skip')" in sql
    assert "catchup_policy in ('run_once', 'skip')" in sql
    assert "catchup_window_seconds between 60 and 604800" in sql
    assert "jitter_seconds < catchup_window_seconds" in sql
    assert "task_schedule_temporal_identity_unique" in sql
    assert "alter table atlas.task_schedule force row level security" in sql
    assert "create policy task_schedule_tenant_isolation" in sql
    assert "task schedule cannot target a production environment" in sql


def test_pause_resume_and_environment_reclassification_are_database_guarded() -> None:
    sql = _sql()

    assert "create or replace function atlas.guard_task_schedule_update()" in sql
    assert "security invoker" in sql
    assert "('active', 'paused'), ('paused', 'active')" in sql
    assert "new.revision <> old.revision + 1" in sql
    assert "new.status = 'active' and exists" in sql
    assert "environment.kind = 'production'" in sql
    assert "environment_auto_pause_task_schedules" in sql
    assert "environment_reclassified_as_production" in sql
    assert "task_schedule.auto_paused" in sql


def test_sync_intents_use_narrow_cross_tenant_fencing() -> None:
    sql = _sql()

    assert "create table atlas.task_schedule_sync_intent" in sql
    assert "for update skip locked" in sql
    assert "schedule.revision > intent.schedule_revision" in sql
    assert "status = 'superseded'" in sql
    assert "intent.claim_token is distinct from p_claim_token" in sql
    assert "claim_token = p_claim_token" in sql
    assert "dispatch_revision = p_dispatch_revision" in sql
    assert ("revoke all on atlas.task_schedule_sync_intent from atlas_app, atlas_dispatcher") in sql
    assert ("grant select, insert on atlas.task_schedule_sync_intent to atlas_app") in sql
    assert ("grant execute on function atlas.claim_task_schedule_sync_intents") in sql


def test_downgrade_removes_trigger_functions_and_refuses_fact_loss() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while task schedule facts exist" in sql
    assert "drop function if exists atlas.guard_task_schedule_sync_insert()" in sql
    assert "drop function if exists atlas.guard_task_schedule_update()" in sql
    assert "drop function if exists atlas.guard_task_schedule_insert()" in sql
