"""Contract tests for the immutable Task Unit execution ticket migration."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260717_0027_task_unit_execution_tickets.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql() -> str:
    statements = cast(tuple[str, ...], _namespace()["UPGRADE_STATEMENTS"])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_the_current_single_head_transactionally() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert namespace["revision"] == "20260717_0027"
    assert namespace["down_revision"] == "20260716_0026"
    assert "autocommit_block" not in source


def test_ticket_is_one_per_attempt_and_uses_exact_scope_foreign_keys() -> None:
    sql = _sql()

    assert "create table atlas.task_unit_execution_ticket" in sql
    assert "task_unit_execution_ticket_attempt_unique unique (unit_attempt_id)" in sql
    for constraint in (
        "task_unit_execution_ticket_attempt_scope_fk",
        "task_unit_execution_ticket_unit_scope_fk",
        "task_unit_execution_ticket_case_scope_fk",
        "task_unit_execution_ticket_execution_profile_scope_fk",
        "task_unit_execution_ticket_identity_profile_scope_fk",
        "task_unit_execution_ticket_browser_profile_scope_fk",
        "task_unit_execution_ticket_data_profile_scope_fk",
        "task_unit_execution_ticket_fixture_scope_fk",
        "task_unit_execution_ticket_environment_scope_fk",
    ):
        assert constraint in sql


def test_insert_guard_revalidates_admission_and_canonical_ticket_digest() -> None:
    sql = _sql()
    guard = sql.split(
        "create function atlas.guard_task_unit_execution_ticket_insert", maxsplit=1
    )[1].split("create trigger task_unit_execution_ticket_guard_insert", maxsplit=1)[0]

    for table in (
        "unit_attempt",
        "execution_unit",
        "task_run",
        "case_version",
        "execution_profile_version",
        "identity_profile_version",
        "browser_profile_version",
        "data_profile_version",
        "data_blueprint_version",
        "environment",
    ):
        assert f"atlas.{table}" in guard
    assert "run.materialization_state = 'sealed'" in guard
    assert "and not run.legacy_unsealed" in guard
    assert "unit.lifecycle = 'queued'" in guard
    assert "attempt.lifecycle = 'queued'" in guard
    assert guard.count("status = 'published'") == 6
    assert "environment.status = 'active'" in guard
    assert "environment.kind in ('test', 'staging')" in guard
    assert "transaction_timestamp()" in guard
    assert "security definer" in guard
    assert "stored_allowed_origins is null" in guard
    assert "atlas.task_sha256_json(" in guard
    assert "new.ticket_digest is distinct from expected_digest" in guard
    assert "for share of attempt, unit, run" in guard


def test_ticket_is_force_rls_immutable_and_least_privilege() -> None:
    sql = _sql()

    assert "task_unit_execution_ticket_prevent_mutation" in sql
    assert "execute function atlas.prevent_fact_mutation()" in sql
    assert "enable row level security" in sql
    assert "force row level security" in sql
    assert "task_unit_execution_ticket_tenant_isolation" in sql
    assert "tenant_id = (select atlas.current_tenant_id())" in sql
    assert (
        "revoke all on atlas.task_unit_execution_ticket from atlas_app, atlas_dispatcher"
        in sql
    )
    assert "grant select, insert on atlas.task_unit_execution_ticket to atlas_app" in sql
    assert "guard owner must bypass row-level security" in sql
    assert (
        "revoke all on function atlas.guard_task_unit_execution_ticket_insert() "
        "from public, atlas_dispatcher"
    ) in sql
    assert "to atlas_dispatcher" not in sql
