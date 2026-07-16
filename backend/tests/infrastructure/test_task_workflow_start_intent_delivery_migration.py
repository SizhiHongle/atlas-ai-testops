"""Contract tests for durable Task Workflow start-intent delivery."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260716_0024_task_workflow_start_intent_delivery.py"
)
RUNTIME_ROLE_PATH = (
    Path(__file__).parents[3]
    / "infrastructure"
    / "postgres"
    / "init"
    / "001-runtime-role.sql"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _statements(name: str) -> tuple[str, ...]:
    return cast(tuple[str, ...], _namespace()[name])


def _sql(name: str) -> str:
    return " ".join("\n".join(_statements(name)).casefold().split())


def _function(sql: str, name: str, next_name: str) -> str:
    return sql.split(f"create function atlas.{name}", maxsplit=1)[1].split(
        f"create function atlas.{next_name}", maxsplit=1
    )[0]


def test_revision_extends_dispatch_prerequisites_transactionally() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260716_0024"
    assert namespace["down_revision"] == "20260716_0023"
    assert "autocommit_block" not in source
    assert "create role" not in source


def test_upgrade_requires_a_function_owner_that_can_cross_forced_rls() -> None:
    statements = _statements("UPGRADE_STATEMENTS")
    preflight = " ".join(statements[0].casefold().split())

    assert "from pg_catalog.pg_roles role" in preflight
    assert "role.rolname = current_user" in preflight
    assert "role.rolsuper or role.rolbypassrls" in preflight
    assert "function owner must bypass row-level security" in preflight
    assert "role.rolname = 'atlas_dispatcher'" in preflight
    assert "not role.rolcanlogin" in preflight
    assert "or role.rolsuper" in preflight
    assert "or role.rolbypassrls" in preflight
    assert "must be login, nosuperuser, and nobypassrls" in preflight
    assert "using errcode = '42501'" in preflight


def test_upgrade_adds_complete_strict_delivery_state() -> None:
    sql = _sql("UPGRADE_STATEMENTS")

    for column in (
        "manifest_hash",
        "available_at",
        "claim_token",
        "claimed_by",
        "claimed_at",
        "claim_expires_at",
        "dispatch_attempts",
        "last_error_code",
        "last_error_at",
        "workflow_started_at",
        "dispatch_failed_at",
        "dispatch_revision",
    ):
        assert f"add column {column}" in sql

    for status in ("pending", "claimed", "retry_wait", "started", "failed"):
        assert f"'{status}'" in sql
    assert "task_workflow_start_intent_manifest_scope_fk" in sql
    assert "references atlas.task_run_manifest" in sql
    assert "task_workflow_start_intent_dispatch_ready_idx" in sql
    assert "task_workflow_start_intent_expired_claim_idx" in sql
    assert "dispatch_attempts >= 0" in sql
    assert "dispatch_revision >= 0" in sql
    assert "dispatch_failed_at = last_error_at" in sql
    assert "last_error_code ~ '^[a-z][a-z0-9_]{0,63}$'" in sql


def test_manifest_is_backfilled_and_future_inserts_use_authoritative_task_run() -> None:
    statements = _statements("UPGRADE_STATEMENTS")
    sql = _sql("UPGRADE_STATEMENTS")
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    drop_immutable = statements.index(
        "drop trigger task_workflow_start_intent_prevent_mutation "
        "on atlas.task_workflow_start_intent"
    )
    backfill = next(
        index
        for index, statement in enumerate(statements)
        if statement.strip().casefold().startswith(
            "update atlas.task_workflow_start_intent intent"
        )
    )
    assert drop_immutable < backfill
    assert "manifest_hash = run.manifest_hash" in sql
    assert "available_at = intent.created_at" in sql
    assert "delivery backfill is incomplete" in sql

    guard = _function(
        sql,
        "guard_task_workflow_start_intent_insert",
        "guard_task_workflow_start_intent_update",
    )
    assert "security definer" in guard
    assert "from atlas.task_run run" in guard
    assert "new.manifest_hash := stored_manifest_hash" in guard
    assert "new.available_at := new.created_at" in guard
    assert "default 'sha256:" not in source


def test_update_guard_freezes_identity_and_allows_only_explicit_transitions() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    guard = _function(
        sql,
        "guard_task_workflow_start_intent_update",
        "claim_task_workflow_start_intents",
    )

    for identity_field in (
        "new.id",
        "new.tenant_id",
        "new.project_id",
        "new.task_run_id",
        "new.owner_kind",
        "new.owner_id",
        "new.namespace",
        "new.workflow_id",
        "new.request_digest",
        "new.manifest_hash",
        "new.workflow_type",
        "new.task_queue",
        "new.created_at",
    ):
        assert identity_field in guard
    assert "identity is immutable" in guard
    assert "new.dispatch_revision is distinct from old.dispatch_revision + 1" in guard
    assert "old.status in ('pending', 'retry_wait', 'claimed')" in guard
    assert "old.status = 'claimed' and new.status = 'retry_wait'" in guard
    assert "old.status = 'claimed' and new.status = 'started'" in guard
    assert "old.status = 'claimed' and new.status = 'failed'" in guard
    assert "claim is still active" in guard
    assert "cannot be deleted" in guard


def test_claim_is_cross_tenant_fenced_bounded_and_skip_locked() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    claim = _function(
        sql,
        "claim_task_workflow_start_intents",
        "mark_task_workflow_start_intent_started",
    )

    assert "returns table" in claim
    for field in (
        "tenant_id uuid",
        "project_id uuid",
        "task_run_id uuid",
        "namespace text",
        "workflow_id text",
        "request_digest text",
        "manifest_hash text",
        "workflow_type text",
        "task_queue text",
        "claim_token uuid",
        "dispatch_revision bigint",
        "dispatch_attempts integer",
    ):
        assert field in claim
    assert "security definer" in claim
    assert "session_user <> 'atlas_dispatcher'" in claim
    assert "p_namespace text" in claim
    assert "intent.owner_kind = 'task_run'" in claim
    assert "intent.workflow_type = 'atlastaskrunworkflow'" in claim
    assert "intent.task_queue = 'atlas-task-run'" in claim
    assert "intent.namespace = p_namespace" in claim
    assert "p_limit not between 1 and 100" in claim
    assert "p_lease_seconds not between 1 and 900" in claim
    assert claim.index("limit p_limit") < claim.index("for update skip locked")
    assert "gen_random_uuid()" in claim
    assert "intent.claim_expires_at <= claimed_at_value" in claim
    assert "dispatch_attempts = intent.dispatch_attempts + 1" in claim
    assert "dispatch_revision = intent.dispatch_revision + 1" in claim


def test_terminal_and_retry_mutations_require_exact_live_fence() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    started = _function(
        sql,
        "mark_task_workflow_start_intent_started",
        "retry_task_workflow_start_intent",
    )
    retry = _function(
        sql,
        "retry_task_workflow_start_intent",
        "fail_task_workflow_start_intent",
    )
    failed = sql.split(
        "create function atlas.fail_task_workflow_start_intent", maxsplit=1
    )[1].split("grant usage on schema atlas", maxsplit=1)[0]

    for body in (started, retry, failed):
        assert "returns boolean" in body
        assert "security definer" in body
        assert "session_user <> 'atlas_dispatcher'" in body
        assert "intent.status = 'claimed'" in body
        assert "intent.claim_token = p_claim_token" in body
        assert "intent.dispatch_revision = p_expected_dispatch_revision" in body
        assert "intent.claim_expires_at >" in body
        assert "dispatch_revision = intent.dispatch_revision + 1" in body
        assert "return affected_count = 1" in body
    assert "status = 'started'" in started
    assert "p_workflow_started_at" not in started
    assert "acknowledged_at_value timestamptz := clock_timestamp()" in started
    assert "workflow_started_at = acknowledged_at_value" in started
    assert "status = 'retry_wait'" in retry
    assert "p_retry_delay_ms integer" in retry
    assert "p_retry_delay_ms not between 100 and 3600000" in retry
    assert "available_at = failed_at_value" in retry
    assert "make_interval(secs => p_retry_delay_ms / 1000.0)" in retry
    assert "status = 'failed'" in failed
    assert "dispatch_failed_at = failed_at_value" in failed
    assert "error_detail" not in sql


def test_dispatcher_is_function_only_and_atlas_app_cannot_dispatch() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert "using (session_user = 'atlas_dispatcher')" in sql
    assert "with check (session_user = 'atlas_dispatcher')" in sql
    assert "grant usage on schema atlas to atlas_dispatcher" in sql
    assert "revoke all on atlas.task_workflow_start_intent from atlas_dispatcher" in sql
    assert (
        "revoke insert, update, delete, truncate, references, trigger on "
        "atlas.task_workflow_start_intent from atlas_app"
    ) in sql
    for function in (
        "claim_task_workflow_start_intents(text, text, integer, integer)",
        "mark_task_workflow_start_intent_started(uuid, uuid, bigint)",
        "retry_task_workflow_start_intent(uuid, uuid, bigint, text, integer)",
        "fail_task_workflow_start_intent(uuid, uuid, bigint, text)",
    ):
        assert f"revoke all on function atlas.{function} from public, atlas_app" in sql
        assert f"grant execute on function atlas.{function} to atlas_dispatcher" in sql
        assert f"grant execute on function atlas.{function} to atlas_app" not in sql
    assert "create role" not in source


def test_downgrade_is_fail_closed_after_any_delivery_transition() -> None:
    statements = _statements("DOWNGRADE_STATEMENTS")
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert statements[0].strip().casefold().startswith("do $$")
    assert "perform set_config('row_security', 'off', true)" in sql
    assert "intent.status <> 'pending'" in sql
    assert "intent.dispatch_attempts <> 0" in sql
    assert "intent.dispatch_revision <> 0" in sql
    assert "cannot downgrade task workflow start-intent delivery after dispatch began" in sql
    assert "using errcode = '55000'" in sql
    assert sql.index("cannot downgrade") < sql.index(
        "drop function if exists atlas.claim_task_workflow_start_intents"
    )
    assert "add constraint task_workflow_start_intent_shape check" in sql
    assert "and status = 'pending'" in sql
    assert "create index task_workflow_start_intent_pending_idx" in sql
    assert "create trigger task_workflow_start_intent_prevent_mutation" in sql
    assert "revoke usage on schema atlas from atlas_dispatcher" in sql


def test_local_bootstrap_has_a_non_privileged_dedicated_dispatcher_login() -> None:
    sql = " ".join(RUNTIME_ROLE_PATH.read_text(encoding="utf-8").casefold().split())

    assert "create role atlas_dispatcher" in sql
    assert "login" in sql
    assert "password 'atlas_dispatcher'" in sql
    assert "nosuperuser" in sql
    assert "nocreatedb" in sql
    assert "nocreaterole" in sql
    assert "nobypassrls" in sql
    assert "noinherit" in sql
    assert "grant connect on database atlas to atlas_dispatcher" in sql
