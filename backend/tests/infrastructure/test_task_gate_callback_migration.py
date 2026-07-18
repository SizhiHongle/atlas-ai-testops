"""Static database guardrails for Task Gate callback delivery."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2] / "migrations" / "versions" / "20260718_0044_task_gate_callbacks.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_task_schedules_transactionally() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0044"
    assert namespace["down_revision"] == "20260718_0043"
    assert "autocommit_block" not in source


def test_callback_intent_is_exact_secret_free_and_rls_protected() -> None:
    sql = _sql()
    table = sql.split("create table atlas.task_gate_callback_intent", 1)[1].split(
        "create index task_gate_callback_ready_idx",
        1,
    )[0]

    assert "task_gate_callback_decision_scope_fk" in table
    assert "manifest_hash ~ '^sha256:[0-9a-f]{64}$'" in table
    assert "gate_decision in ('accepted', 'rejected', 'inconclusive')" in table
    assert all(
        forbidden not in table
        for forbidden in (
            "callback_url",
            "hmac",
            "signature",
            "password",
            "credential",
            "secret",
        )
    )
    assert "alter table atlas.task_gate_callback_intent force row level security" in sql
    assert "create policy task_gate_callback_tenant_isolation" in sql


def test_insert_guard_mirrors_gate_and_dispatcher_has_only_narrow_functions() -> None:
    sql = _sql()

    assert "new.manifest_hash <> stored_run.manifest_hash" in sql
    assert "new.gate_decision <> decision.decision" in sql
    assert "new.created_at <> decision.evaluated_at" in sql
    assert "session_user <> 'atlas_dispatcher'" in sql
    assert "for update skip locked" in sql
    assert "claim_token = p_claim_token" in sql
    assert "dispatch_revision = p_dispatch_revision" in sql
    assert "revoke all on atlas.task_gate_callback_intent from atlas_app, atlas_dispatcher" in sql
    assert "grant select, insert on atlas.task_gate_callback_intent to atlas_app" in sql
    assert "grant execute on function atlas.claim_task_gate_callback_intents" in sql


def test_callback_delivery_state_is_fenced_and_downgrade_refuses_fact_loss() -> None:
    sql = _sql()
    downgrade = _sql("DOWNGRADE_STATEMENTS")

    assert "status in ('pending', 'claimed', 'retry_wait', 'delivered', 'failed')" in sql
    assert "new.dispatch_attempts = old.dispatch_attempts + 1" in sql
    assert "new.dispatch_revision = old.dispatch_revision + 1" in sql
    assert "p_response_status_code not between 200 and 299" in sql
    assert "cannot downgrade while task gate callback facts exist" in downgrade
    assert "drop table atlas.task_gate_callback_intent" in downgrade
