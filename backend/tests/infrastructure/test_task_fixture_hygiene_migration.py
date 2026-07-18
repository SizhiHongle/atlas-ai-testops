"""Contract tests for Task Fixture binding and Unit Hygiene persistence."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0035_task_fixture_hygiene_truth.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_task_snapshot_without_autocommit() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0035"
    assert namespace["down_revision"] == "20260718_0034"
    assert "autocommit_block" not in source


def test_attempt_fixture_binding_is_exact_and_append_only() -> None:
    sql = _sql()

    assert "create table atlas.attempt_fixture_binding" in sql
    assert "attempt_fixture_binding_attempt_scope_fk" in sql
    assert "attempt_fixture_binding_fixture_scope_fk" in sql
    assert "attempt_fixture_binding_attempt_unique unique (unit_attempt_id)" in sql
    assert "attempt_fixture_binding_fixture_unique unique (fixture_run_id)" in sql
    assert "stored_fixture.run_kind <> 'execution'" in sql
    assert "'unit-attempt:' || new.unit_attempt_id::text" in sql
    assert "stored_fixture.blueprint_version_id" in sql
    assert "blueprint.plan_digest = stored_fixture.plan_digest" in sql
    assert "stored_fixture.environment_id <> stored_unit.environment_id" in sql
    assert "atlas.task_sha256_json(new.binding - 'bindinghash')" in sql
    assert "before update or delete on atlas.attempt_fixture_binding" in sql


def test_hygiene_resolution_rechecks_all_closed_attempt_cleanup_inputs() -> None:
    sql = _sql()

    assert "create table atlas.unit_hygiene_resolution_revision" in sql
    assert (
        "unit_hygiene_resolution_unit_scope_fk foreign key ( execution_unit_id, "
        "task_run_id, tenant_id, project_id ) references atlas.execution_unit "
        "( id, task_run_id, tenant_id, project_id )"
    ) in sql
    assert "closed_attempt_count <> jsonb_array_length(new.inputs)" in sql
    assert "stored_attempt.attempt_number <> input_index" in sql
    assert "input_value ->> 'source' = 'explicit_not_required'" in sql
    assert "input_value ->> 'source' = 'fixture_run'" in sql
    assert "from atlas.resource_record resource" in sql
    assert "from atlas.resource_cleanup_attempt attempt" in sql
    assert "node.status = 'outcome_uncertain'" in sql
    assert "'atlas.fixture-cleanup-observation/0.1'" in sql
    assert "'atlas.unit-hygiene-input-set/0.1'" in sql
    assert "expected_hygiene := case" in sql
    assert "new.input_set_hash <> expected_input_hash" in sql
    assert "before update or delete on atlas.unit_hygiene_resolution_revision" in sql
    assert "sha256:e8ad3538745d89b0a9516846a4d42a4cb9703ca5d964b894d29679a14f952e37" in sql


def test_cleanup_truth_forces_rls_and_minimum_privileges() -> None:
    sql = _sql()

    for table in (
        "attempt_fixture_binding",
        "unit_hygiene_resolution_revision",
    ):
        assert f"alter table atlas.{table} force row level security" in sql
        assert f"revoke all on atlas.{table} from atlas_app" in sql
        assert f"grant select, insert on atlas.{table} to atlas_app" in sql


def test_downgrade_refuses_to_discard_cleanup_truth() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while task fixture hygiene facts exist" in sql
    assert sql.index("cannot downgrade") < sql.index(
        "drop table if exists atlas.unit_hygiene_resolution_revision"
    )
