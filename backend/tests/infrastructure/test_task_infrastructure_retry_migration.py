"""Contract tests for frozen Task infrastructure retry policy storage."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260717_0030_task_infrastructure_retry.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_pause_resume_transactionally() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260717_0030"
    assert namespace["down_revision"] == "20260717_0029"
    assert "autocommit_block" not in source


def test_v02_manifest_requires_exact_bounded_retry_policy_and_digest() -> None:
    sql = _sql()

    assert "add column retry_policy jsonb" in sql
    assert "schema_version = 'atlas.task-run-manifest/0.1'" in sql
    assert "and retry_policy is null" in sql
    assert "schema_version = 'atlas.task-run-manifest/0.2'" in sql
    assert "retry_policy - array[" in sql
    assert "] = '{}'::jsonb" in sql
    assert "'atlas.task-retry-policy/0.1'" in sql
    assert "'infra-retry'" in sql
    assert "between 0 and 4" in sql
    assert "between 0 and 256" in sql
    assert "between 1 and 3600" in sql
    assert "atlas.task_sha256_json(retry_policy - 'contentdigest')" in sql


def test_execution_ticket_guard_accepts_only_exact_infrastructure_retry_chain() -> None:
    sql = _sql()

    assert "pg_get_functiondef(" in sql
    assert "attempt.attempt_number = 1" in sql
    assert "attempt.attempt_number > 1" in sql
    assert "unit.lifecycle = 'running'" in sql
    assert "previous.attempt_number = attempt.attempt_number - 1" in sql
    assert "previous.lifecycle = 'closed'" in sql
    assert "previous.quality = 'infra_error'" in sql
    assert "transaction_timestamp() >= attempt.queued_at" in sql


def test_downgrade_refuses_to_discard_v02_retry_facts() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while task-run-manifest/0.2 retry policy facts exist" in sql
    assert sql.index("cannot downgrade") < sql.index("drop column retry_policy")
