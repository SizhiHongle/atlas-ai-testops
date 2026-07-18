"""Contract tests for durable TaskRun control-command storage."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260717_0028_task_run_command_intents.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def _function(sql: str, name: str, next_name: str) -> str:
    return sql.split(f"create function atlas.{name}", maxsplit=1)[1].split(
        f"create function atlas.{next_name}", maxsplit=1
    )[0]


def test_revision_is_transactional_single_head_extension() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260717_0028"
    assert namespace["down_revision"] == "20260717_0027"
    assert "autocommit_block" not in source
    assert "create role" not in source


def test_table_persists_exact_command_identity_and_bounded_state_machine() -> None:
    sql = _sql()

    assert "create table atlas.task_run_command_intent" in sql
    for field in (
        "client_mutation_id",
        "command_digest",
        "expected_run_revision",
        "accepted_run_revision",
        "request_digest",
        "manifest_hash",
        "namespace",
        "workflow_id",
        "available_at",
        "claim_token",
        "dispatch_revision",
        "dispatch_attempts",
        "signal_delivered_at",
        "applied_at",
        "dispatch_failed_at",
    ):
        assert field in sql
    for status in ("pending", "claimed", "retry_wait", "delivered", "applied", "failed"):
        assert f"'{status}'" in sql
    assert (
        "task_run_command_mutation_unique unique "
        "( tenant_id, task_run_id, client_mutation_id )"
    ) in sql
    assert "task_run_command_digest_unique unique ( tenant_id, task_run_id, command_digest )" in sql
    assert "accepted_run_revision = expected_run_revision + 1" in sql
    assert "task_run_command_dispatch_ready_idx" in sql
    assert "task_run_command_expired_claim_idx" in sql


def test_insert_guard_locks_exact_canceling_run_and_recomputes_digest() -> None:
    sql = _sql()
    guard = _function(
        sql,
        "guard_task_run_command_insert",
        "guard_task_run_command_update",
    )

    assert "security definer" in guard
    assert "new.tenant_id <> atlas.current_tenant_id()" in guard
    assert "new.created_by is distinct from atlas.current_actor_id()" in guard
    assert "from atlas.task_run run" in guard
    assert "for update" in guard
    assert "run_row.lifecycle <> 'canceling'" in guard
    assert "run_row.materialization_state <> 'sealed'" in guard
    assert "run_row.legacy_unsealed" in guard
    assert "run_row.revision <> new.accepted_run_revision" in guard
    assert "run_row.request_digest <> new.request_digest" in guard
    assert "atlas.task_sha256_json(" in guard
    assert "new.command_digest <> expected_digest" in guard


def test_update_guard_freezes_identity_and_only_allows_dispatch_transitions() -> None:
    sql = _sql()
    guard = _function(
        sql,
        "guard_task_run_command_update",
        "claim_task_run_command_intents",
    )

    for identity in (
        "new.id",
        "new.tenant_id",
        "new.project_id",
        "new.task_run_id",
        "new.command_type",
        "new.client_mutation_id",
        "new.command_digest",
        "new.expected_run_revision",
        "new.accepted_run_revision",
        "new.request_digest",
        "new.manifest_hash",
        "new.namespace",
        "new.workflow_id",
        "new.created_by",
        "new.created_at",
    ):
        assert identity in guard
    assert "immutable identity cannot change" in guard
    assert "new.dispatch_revision <> old.dispatch_revision + 1" in guard
    assert "old.status in ('pending', 'retry_wait') and new.status = 'claimed'" in guard
    assert "old.status = 'claimed' and new.status in" in guard
    for status in ("'retry_wait'", "'delivered'", "'applied'", "'failed'"):
        assert status in guard
    assert "old.status = 'delivered' and new.status = 'applied'" in guard


def test_dispatcher_claim_and_cas_are_cross_tenant_function_only() -> None:
    sql = _sql()
    claim = _function(
        sql,
        "claim_task_run_command_intents",
        "mark_task_run_command_intent_delivered",
    )

    assert "security definer" in claim
    assert "session_user <> 'atlas_dispatcher'" in claim
    assert "p_namespace text" in claim
    assert "intent.namespace = p_namespace" in claim
    assert "for update skip locked" in claim
    assert "gen_random_uuid()" in claim
    assert "dispatch_attempts = intent.dispatch_attempts + 1" in claim
    assert "dispatch_revision = intent.dispatch_revision + 1" in claim

    for function in (
        "mark_task_run_command_intent_delivered",
        "retry_task_run_command_intent",
        "fail_task_run_command_intent",
    ):
        body = sql.split(f"create function atlas.{function}", maxsplit=1)[1]
        assert "session_user <> 'atlas_dispatcher'" in body
        assert "intent.claim_token = p_claim_token" in body
        assert "intent.dispatch_revision = p_expected_dispatch_revision" in body
        assert "intent.claim_expires_at >" in body
    assert (
        "revoke all on atlas.task_run_command_intent "
        "from public, atlas_app, atlas_dispatcher"
    ) in sql
    assert "grant select, insert on atlas.task_run_command_intent to atlas_app" in sql
    assert "to atlas_dispatcher" in sql


def test_workflow_application_is_app_only_and_requires_canceled_closed_run() -> None:
    sql = _sql()
    apply = sql.split(
        "create function atlas.apply_task_run_cancel_command", maxsplit=1
    )[1].split("alter table atlas.task_run_command_intent", maxsplit=1)[0]

    assert "atlas.current_tenant_id() is null" in apply
    assert "run.lifecycle = 'closed'" in apply
    assert "run.quality = 'canceled'" in apply
    assert "command_row.status not in ('claimed', 'delivered')" in apply
    assert "command_row.status = 'applied'" in apply
    assert "status = 'applied'" in apply
    assert (
        "grant execute on function "
        "atlas.apply_task_run_cancel_command(uuid, text) to atlas_app"
    ) in sql
    assert "from public, atlas_dispatcher" in sql


def test_force_rls_delete_protection_and_fail_closed_downgrade() -> None:
    sql = _sql()
    downgrade = _sql("DOWNGRADE_STATEMENTS")

    assert "enable row level security" in sql
    assert "force row level security" in sql
    assert "tenant_id = (select atlas.current_tenant_id())" in sql
    assert "execute function atlas.prevent_fact_mutation()" in sql
    assert "cannot downgrade task command intents after commands were accepted" in downgrade
    assert downgrade.index("cannot downgrade") < downgrade.index(
        "drop function atlas.apply_task_run_cancel_command"
    )
