"""Contract tests for the P5-00B1 Task dispatch prerequisite migration."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260716_0023_task_dispatch_prerequisites.py"
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


def test_revision_extends_task_hosts_transactionally() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert namespace["revision"] == "20260716_0023"
    assert namespace["down_revision"] == "20260716_0022"
    assert "autocommit_block" not in source


def test_upgrade_creates_four_scoped_immutable_profile_hosts() -> None:
    sql = _sql("UPGRADE_STATEMENTS")

    for table in (
        "execution_profile_version",
        "identity_profile_version",
        "identity_profile_actor_binding",
        "browser_profile_version",
        "data_profile_version",
    ):
        assert f"create table atlas.{table}" in sql

    assert "execution_profile_case_scope_fk" in sql
    assert "identity_profile_actor_role_scope_fk" in sql
    assert "browser_profile_project_scope_fk" in sql
    assert "data_profile_blueprint_scope_fk" in sql
    assert "guard_task_profile_update" in sql
    assert "identity profile actor bindings are already finalized" in sql
    assert sql.count("execute function atlas.prevent_fact_mutation()") >= 7
    assert "rename column execution_contract_version_id to execution_profile_version_id" in sql
    assert "'executionprofileversionid'" in sql
    assert "'executioncontractversionid'" not in sql


def test_profile_and_task_digests_use_one_recursive_canonical_serializer() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert "create function atlas.task_canonical_json" in sql
    assert "order by item.key collate \"c\"" in sql
    assert "with ordinality item(value, ordinality)" in sql
    assert "create function atlas.task_sha256_json" in sql
    assert "convert_to(atlas.task_canonical_json(value), 'utf8')" in sql
    assert "from jsonb_each(input_value) entry(key, value)" in sql
    assert "from jsonb_array_elements(input_value) element(value)" in sql
    assert "select key, value from jsonb_each(value)" not in source

    for guard in (
        "guard_execution_profile_insert",
        "guard_browser_profile_insert",
        "guard_data_profile_insert",
        "guard_task_plan_version_v2_insert",
        "guard_task_run_manifest_v2_insert",
    ):
        body = sql.split(f"create function atlas.{guard}", maxsplit=1)[1]
        assert "atlas.task_sha256_json" in body or "atlas.task_profile_content_digest" in body

    assert "task run manifest contains non-canonical unit digests" in sql
    assert "task run manifest or request digest is not canonical" in sql


def test_legacy_backfill_is_fail_closed_and_does_not_revise_business_facts() -> None:
    statements = _statements("UPGRADE_STATEMENTS")
    sql = _sql("UPGRADE_STATEMENTS")

    run_disable = statements.index(
        "alter table atlas.task_run disable trigger task_run_guard_update"
    )
    run_enable = statements.index(
        "alter table atlas.task_run enable trigger task_run_guard_update"
    )
    attempt_disable = statements.index(
        "alter table atlas.unit_attempt disable trigger unit_attempt_guard_update"
    )
    attempt_enable = statements.index(
        "alter table atlas.unit_attempt enable trigger unit_attempt_guard_update"
    )
    assert run_disable < run_enable < attempt_disable < attempt_enable

    run_backfill = " ".join(statements[run_disable + 2].casefold().split())
    attempt_backfill = " ".join(statements[attempt_disable + 2].casefold().split())
    assert "legacy_unsealed = true" in run_backfill
    assert "materialization_state = 'materializing'" in run_backfill
    assert "revision = revision + 1" not in run_backfill
    assert "revision = revision + 1" not in attempt_backfill
    assert "stored_run.legacy_unsealed" in sql
    assert "task run is not eligible for materialization seal" in sql
    assert "materialization_state in ('materializing', 'sealed')" in sql
    assert "materialization_state = 'legacy'" not in sql
    assert sql.count("not valid") >= 7


def test_workflow_identity_is_global_deterministic_and_append_only() -> None:
    sql = _sql("UPGRADE_STATEMENTS")

    assert "create table atlas.task_workflow_identity_registry" in sql
    assert "primary key ( namespace, workflow_id )" in sql
    assert "unique ( owner_kind, owner_id )" in sql
    assert "'atlas-task/run/' || replace(tenant_id::text, '-', '')" in sql
    assert "'atlas-task/attempt/' || replace(tenant_id::text, '-', '')" in sql
    assert "create function atlas.register_task_run_workflow_identity" in sql
    assert "create function atlas.register_unit_attempt_workflow_identity" in sql
    assert sql.count("security definer") >= 6
    assert "task_workflow_identity_prevent_mutation" in sql

    assert "create table atlas.task_workflow_start_intent" in sql
    assert "task_workflow_start_intent_owner_unique" in sql
    assert "task_workflow_start_intent_identity_unique" in sql
    assert "task_workflow_start_intent_registry_fk" in sql
    assert "status = 'pending'" in sql
    assert "task_workflow_start_intent_prevent_mutation" in sql


def test_retry_attempts_require_parent_namespace_and_closed_retryable_predecessor() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    guard = _function(
        sql,
        "guard_unit_attempt_v2_insert",
        "guard_unit_attempt_v2_update",
    )

    assert "security definer" in guard
    assert "unit attempt insertion requires exact tenant context" in guard
    assert "for update" in guard
    assert "new.temporal_namespace is distinct from parent_namespace" in guard
    assert "parent_materialization_state <> 'sealed'" in guard
    assert "parent_lifecycle not in ('queued', 'running')" in guard
    assert "parent_unit_lifecycle not in ('queued', 'running')" in guard
    assert "previous_lifecycle <> 'closed'" in guard
    for quality in ("failed", "blocked", "inconclusive", "infra_error", "canceled"):
        assert f"'{quality}'" in guard


def test_manifest_guard_can_lock_run_without_restoring_direct_state_updates() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    guard = _function(
        sql,
        "guard_task_run_manifest_v2_insert",
        "guard_execution_unit_v2_insert",
    )

    assert "security definer" in guard
    assert "task run manifest insertion requires exact tenant context" in guard
    assert "for update" in guard
    assert "revoke update (" in sql


def test_materialization_seal_recomputes_and_proves_the_complete_aggregate() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    seal = _function(sql, "seal_task_run_materialization", "transition_task_run_state")

    assert "for update" in seal
    assert "stored_run.revision <> p_expected_revision" in seal
    assert "using errcode = '40001'" in seal
    assert "expected_manifest_hash := atlas.task_sha256_json" in seal
    assert "expected_request_digest := atlas.task_sha256_json" in seal
    assert "stored_unit_count <> stored_manifest.unit_count" in seal
    assert "stored_attempt_count <> stored_manifest.unit_count" in seal
    assert "stored_first_attempt_count <> stored_manifest.unit_count" in seal
    for dependency in (
        "execution_profile_version",
        "identity_profile_version",
        "browser_profile_version",
        "data_profile_version",
        "data_blueprint_version",
        "environment",
        "test_role",
    ):
        assert f"atlas.{dependency}" in seal
    assert "identity_profile.content_digest = atlas.task_identity_profile_content_digest" in seal
    assert "materialization_state = 'sealed'" in seal
    assert "insert into atlas.task_workflow_start_intent" in seal
    assert seal.index("update atlas.task_run") < seal.index(
        "insert into atlas.task_workflow_start_intent"
    )


def test_trusted_cas_functions_share_run_unit_attempt_lock_order() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    run = _function(sql, "transition_task_run_state", "transition_execution_unit_state")
    unit = _function(sql, "transition_execution_unit_state", "transition_unit_attempt_state")
    attempt = _function(sql, "transition_unit_attempt_state", "guard_task_run_v2_update")

    for body in (run, unit, attempt):
        assert "security definer" in body
        assert "using errcode = '40001'" in body
        assert "revision =" in body
        assert "for update" in body
        assert "materialization_state" in body
        assert "legacy_unsealed" in body
    assert unit.index("from atlas.task_run run") < unit.index(
        "from atlas.execution_unit unit"
    )
    assert attempt.index("from atlas.task_run run") < attempt.index(
        "from atlas.execution_unit unit"
    ) < attempt.index("from atlas.unit_attempt attempt")


def test_rls_privileges_and_function_order_remain_fail_closed() -> None:
    sql = _sql("UPGRADE_STATEMENTS")

    for table in (
        "execution_profile_version",
        "identity_profile_version",
        "identity_profile_actor_binding",
        "browser_profile_version",
        "data_profile_version",
        "task_workflow_identity_registry",
        "task_workflow_start_intent",
    ):
        assert f"alter table atlas.{table} enable row level security" in sql
        assert f"alter table atlas.{table} force row level security" in sql
        assert f"revoke all on atlas.{table} from atlas_app" in sql

    assert "grant select on atlas.task_workflow_identity_registry to atlas_app" in sql
    assert "grant select on atlas.task_workflow_start_intent to atlas_app" in sql
    assert "grant insert on atlas.task_workflow_start_intent" not in sql
    assert sql.count("revoke update (") == 3
    assert (
        "alter function atlas.guard_task_run_event_insert() security definer"
        in sql
    )
    assert sql.index("create function atlas.seal_task_run_materialization") < sql.index(
        "revoke all on function atlas.seal_task_run_materialization"
    )
    assert sql.index("create function atlas.transition_unit_attempt_state") < sql.index(
        "grant execute on function atlas.transition_unit_attempt_state"
    )


def test_downgrade_restores_0022_contract_without_orphaning_triggers() -> None:
    statements = _statements("DOWNGRADE_STATEMENTS")
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "execute function atlas.guard_task_plan_version_insert()" in sql
    assert "execute function atlas.guard_task_run_insert()" in sql
    assert "execute function atlas.guard_unit_attempt_update()" in sql
    assert "drop table if exists atlas.task_workflow_start_intent" in sql
    assert "drop table if exists atlas.task_workflow_identity_registry" in sql
    assert "drop table if exists atlas.identity_profile_actor_binding" in sql
    assert "drop table if exists atlas.execution_profile_version" in sql
    assert "rename column execution_profile_version_id to execution_contract_version_id" in sql
    assert "atlas.task_profile_refs_valid(profile_refs, pinned_case_version_ids)" in sql
    assert "atlas.task_manifest_units_valid(units)" in sql
    assert "profile.value - 'executionprofileversionid'" in sql
    assert "'executioncontractversionid'" in sql
    assert "unit.value - 'executionprofileversionid'" in sql
    assert "order by profile.ordinality" in sql
    assert "order by unit.ordinality" in sql
    plan_disable = statements.index(
        "alter table atlas.task_plan_version disable trigger "
        "task_plan_version_prevent_mutation"
    )
    plan_enable = statements.index(
        "alter table atlas.task_plan_version enable trigger "
        "task_plan_version_prevent_mutation"
    )
    manifest_disable = statements.index(
        "alter table atlas.task_run_manifest disable trigger "
        "task_run_manifest_prevent_mutation"
    )
    manifest_enable = statements.index(
        "alter table atlas.task_run_manifest enable trigger "
        "task_run_manifest_prevent_mutation"
    )
    assert plan_disable < plan_enable < manifest_disable < manifest_enable
    assert (
        "alter function atlas.guard_task_run_event_insert() security invoker"
        in sql
    )
    assert sql.count("grant update (") == 3
