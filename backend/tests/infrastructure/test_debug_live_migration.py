"""Contract tests for staged DebugRun live event database hardening."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATIONS_PATH = Path(__file__).parents[2] / "migrations" / "versions"
MIGRATION_0019_PATH = MIGRATIONS_PATH / "20260716_0019_debug_live_event_hardening.py"
MIGRATION_0020_PATH = MIGRATIONS_PATH / "20260716_0020_validate_debug_live_events.py"
MIGRATION_0021_PATH = MIGRATIONS_PATH / "20260716_0021_compact_debug_live_replay_index.py"


def _migration_namespace(path: Path) -> dict[str, object]:
    return run_path(str(path))


def _migration_statements(path: Path, name: str) -> tuple[str, ...]:
    return cast(tuple[str, ...], _migration_namespace(path)[name])


def _migration_sql(path: Path, name: str) -> str:
    return "\n".join(_migration_statements(path, name)).casefold()


def _migration_statement(path: Path, name: str) -> str:
    statement = cast(str, _migration_namespace(path)[name])
    return " ".join(statement.split()).casefold()


def test_revisions_form_a_retryable_validation_boundary() -> None:
    migration_0019 = _migration_namespace(MIGRATION_0019_PATH)
    migration_0020 = _migration_namespace(MIGRATION_0020_PATH)
    migration_0021 = _migration_namespace(MIGRATION_0021_PATH)

    assert migration_0019["revision"] == "20260716_0019"
    assert migration_0019["down_revision"] == "20260715_0018"
    assert migration_0020["revision"] == "20260716_0020"
    assert migration_0020["down_revision"] == "20260716_0019"
    assert migration_0021["revision"] == "20260716_0021"
    assert migration_0021["down_revision"] == "20260716_0020"
    assert "autocommit_block" not in MIGRATION_0019_PATH.read_text(encoding="utf-8")
    assert "autocommit_block" not in MIGRATION_0020_PATH.read_text(encoding="utf-8")
    assert "autocommit_block" in MIGRATION_0021_PATH.read_text(encoding="utf-8")


def test_0019_adds_only_the_unvalidated_payload_bound() -> None:
    upgrade_sql = _migration_sql(MIGRATION_0019_PATH, "UPGRADE_STATEMENTS")
    downgrade_sql = _migration_sql(MIGRATION_0019_PATH, "DOWNGRADE_STATEMENTS")

    assert "add constraint debug_run_event_payload_size_valid" in upgrade_sql
    assert "octet_length(payload::text) <= 32768" in upgrade_sql
    assert ") not valid" in upgrade_sql
    assert "validate constraint" not in upgrade_sql
    assert "create trigger" not in upgrade_sql
    assert "drop index" not in upgrade_sql
    assert "drop constraint if exists debug_run_event_payload_size_valid" in downgrade_sql
    assert "trigger" not in downgrade_sql
    assert "index" not in downgrade_sql


def test_0020_validates_then_enables_immutable_storage_without_touching_indexes() -> None:
    statements = tuple(
        " ".join(statement.split()).casefold()
        for statement in _migration_statements(MIGRATION_0020_PATH, "UPGRADE_STATEMENTS")
    )

    assert statements[0].startswith(
        "alter table atlas.debug_run_event validate constraint "
        "debug_run_event_payload_size_valid"
    )
    assert "create trigger debug_run_event_prevent_mutation" in statements[1]
    assert "before update or delete on atlas.debug_run_event" in statements[1]
    assert "execute function atlas.prevent_fact_mutation()" in statements[1]
    assert len(statements) == 2
    assert all("create table" not in statement for statement in statements)
    assert all("index" not in statement for statement in statements)
    assert all(
        "drop constraint debug_run_event_sequence_unique" not in statement
        for statement in statements
    )


def test_0020_downgrade_restores_exact_repairable_0019_shape() -> None:
    statements = tuple(
        " ".join(statement.split()).casefold()
        for statement in _migration_statements(MIGRATION_0020_PATH, "DOWNGRADE_STATEMENTS")
    )

    assert statements[0] == (
        "drop trigger if exists debug_run_event_prevent_mutation "
        "on atlas.debug_run_event"
    )
    assert statements[1] == (
        "alter table atlas.debug_run_event drop constraint if exists "
        "debug_run_event_payload_size_valid"
    )
    assert "add constraint debug_run_event_payload_size_valid" in statements[2]
    assert "octet_length(payload::text) <= 32768" in statements[2]
    assert statements[2].endswith(") not valid")
    assert all("index" not in statement for statement in statements)
    assert all("create unique index" not in statement for statement in statements)


def test_0021_compacts_and_restores_replay_index_concurrently() -> None:
    upgrade_statement = _migration_statement(MIGRATION_0021_PATH, "UPGRADE_STATEMENT")
    downgrade_statements = tuple(
        " ".join(statement.split()).casefold()
        for statement in _migration_statements(
            MIGRATION_0021_PATH,
            "DOWNGRADE_STATEMENTS",
        )
    )
    source = MIGRATION_0021_PATH.read_text(encoding="utf-8")

    assert upgrade_statement == (
        "drop index concurrently if exists atlas.debug_run_event_replay_idx"
    )
    assert downgrade_statements[0] == upgrade_statement
    assert downgrade_statements[1] == (
        "create index concurrently if not exists debug_run_event_replay_idx "
        "on atlas.debug_run_event (debug_run_id, seq)"
    )
    assert source.count("with op.get_context().autocommit_block():") == 2
    assert "for statement in DOWNGRADE_STATEMENTS:" in source
