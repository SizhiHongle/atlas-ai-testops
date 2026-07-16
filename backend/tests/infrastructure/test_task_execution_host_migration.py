"""Contract tests for the Task execution host migration."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2] / "migrations" / "versions" / "20260716_0022_task_execution_hosts.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _statements(name: str) -> tuple[str, ...]:
    return cast(tuple[str, ...], _namespace()[name])


def _sql(name: str) -> str:
    return "\n".join(_statements(name)).casefold()


def test_revision_extends_the_single_migration_head_transactionally() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert namespace["revision"] == "20260716_0022"
    assert namespace["down_revision"] == "20260716_0021"
    assert "autocommit_block" not in source
    assert "advisory" not in source.casefold()


def test_upgrade_creates_the_complete_scoped_execution_hierarchy() -> None:
    sql = _sql("UPGRADE_STATEMENTS")

    for table in (
        "task_plan",
        "task_plan_version",
        "task_run",
        "task_run_manifest",
        "execution_unit",
        "unit_attempt",
        "task_run_event",
    ):
        assert f"create table atlas.{table}" in sql

    assert "case_version_task_scope_unique" in sql
    assert "task_run_id uuid primary key" in sql
    assert "task_run_manifest_run_scope_fk" in sql
    assert "execution_unit_manifest_scope_fk" in sql
    assert "execution_unit_case_version_scope_fk" in sql
    assert "execution_unit_environment_scope_fk" in sql
    assert "execution_unit_fixture_blueprint_scope_fk" in sql
    assert "unit_attempt_unit_scope_fk" in sql
    assert "task_run_event_attempt_scope_fk" in sql


def test_run_and_manifest_require_each_other_at_transaction_commit() -> None:
    sql = " ".join(_sql("UPGRADE_STATEMENTS").split())

    exact_binding = (
        "task_run_id, tenant_id, project_id, task_plan_version_id, "
        "manifest_hash, trigger_source, trigger_fingerprint"
    )
    exact_run_binding = (
        "id, tenant_id, project_id, task_plan_version_id, "
        "manifest_hash, trigger_source, trigger_fingerprint"
    )
    assert "constraint task_run_manifest_run_scope_fk foreign key" in sql
    assert "constraint task_run_manifest_reverse_scope_unique unique" in sql
    assert exact_binding in sql
    assert "add constraint task_run_manifest_reverse_scope_fk foreign key" in sql
    assert exact_run_binding in sql
    assert "references atlas.task_run_manifest" in sql
    assert "deferrable initially deferred" in sql


def test_upgrade_freezes_versions_manifests_and_exact_unit_bindings() -> None:
    sql = _sql("UPGRADE_STATEMENTS")

    assert "task_plan_version_number_unique" in sql
    assert "atlas.task_profile_refs_valid(profile_refs, pinned_case_version_ids)" in sql
    assert "atlas.task_policy_digests_valid(policy_digests)" in sql
    assert "atlas.task_manifest_units_valid(units)" in sql
    assert "unit_count = jsonb_array_length(units)" in sql
    assert "task_plan_version_prevent_mutation" in sql
    assert "task plan version requires published same-scope case versions" in sql
    assert "task_run_manifest_prevent_mutation" in sql
    for field in (
        "execution_contract_version_id",
        "fixture_blueprint_version_id",
        "identity_profile_version_id",
        "browser_profile_version_id",
        "data_profile_version_id",
        "parameter_digest",
        "dependency_digest",
    ):
        assert field in sql
    assert "execution unit bindings must match its run manifest" in sql
    assert "execution unit manifest identity is immutable" in sql


def test_json_validators_reject_missing_keys_and_json_null_values() -> None:
    sql = " ".join(_sql("UPGRADE_STATEMENTS").split())
    uuid_array = sql.split("create function atlas.task_uuid_json_array_valid", maxsplit=1)[1].split(
        "create function atlas.task_policy_digests_valid", maxsplit=1
    )[0]
    policy_digests = sql.split("create function atlas.task_policy_digests_valid", maxsplit=1)[
        1
    ].split("create function atlas.task_profile_refs_valid", maxsplit=1)[0]
    profile_refs = sql.split("create function atlas.task_profile_refs_valid", maxsplit=1)[1].split(
        "create function atlas.task_manifest_units_valid", maxsplit=1
    )[0]
    manifest_units = sql.split("create function atlas.task_manifest_units_valid", maxsplit=1)[
        1
    ].split("create function atlas.task_execution_state_valid", maxsplit=1)[0]
    matrix_constraint = sql.split("constraint task_plan_version_matrix_valid check", maxsplit=1)[
        1
    ].split("constraint task_plan_version_profiles_valid", maxsplit=1)[0]

    assert " strict " not in uuid_array
    assert "jsonb_typeof(value) is distinct from 'array'" in uuid_array
    assert "item.value is null" in uuid_array

    assert "jsonb_typeof(value) is distinct from 'object'" in policy_digests
    assert "item.digest is null" in policy_digests

    assert " strict " not in profile_refs
    assert "pinned is null" in profile_refs
    assert "jsonb_typeof(value) is distinct from 'object'" in profile_refs
    assert "not (value ?& array['caseprofiles'])" in profile_refs
    assert "jsonb_typeof(profile) is distinct from 'object'" in profile_refs
    assert "not (profile ?& array[" in profile_refs
    for key in (
        "caseversionid",
        "executioncontractversionid",
        "fixtureblueprintversionid",
    ):
        assert f"'{key}'" in profile_refs
        assert f"profile ->> '{key}' is null" in profile_refs
    assert "return coalesce(" in profile_refs

    assert " strict " not in manifest_units
    assert "jsonb_typeof(value) is distinct from 'array'" in manifest_units
    assert "jsonb_typeof(unit) is distinct from 'object'" in manifest_units
    assert "not (unit ?& array[" in manifest_units
    unit_keys = (
        "ordinal",
        "unitkey",
        "caseversionid",
        "executioncontractversionid",
        "fixtureblueprintversionid",
        "identityprofileversionid",
        "environmentid",
        "browserprofileversionid",
        "dataprofileversionid",
        "parameterdigest",
        "dependencydigest",
    )
    for key in unit_keys:
        assert f"'{key}'" in manifest_units
    assert "unit ->> 'ordinal' is distinct from position::text" in manifest_units
    for key in ("unitkey", "parameterdigest", "dependencydigest"):
        assert f"unit ->> '{key}' is null" in manifest_units
    assert "unit ->> uuid_field is null" in manifest_units

    assert "matrix ?& array[" in matrix_constraint
    for key in (
        "environmentids",
        "browserprofileversionids",
        "identityprofileversionids",
        "dataprofileversionids",
    ):
        assert f"'{key}'" in matrix_constraint
    assert matrix_constraint.count("coalesce( atlas.task_uuid_json_array_valid(matrix ->") == 4


def test_manifest_units_must_derive_from_the_exact_task_plan_version() -> None:
    sql = " ".join(_sql("UPGRADE_STATEMENTS").split())
    guard = sql.split("create function atlas.guard_task_run_manifest_insert", maxsplit=1)[1].split(
        "create function atlas.guard_task_run_insert", maxsplit=1
    )[0]

    assert "from atlas.task_plan_version version" in guard
    assert "version.id = new.task_plan_version_id" in guard
    assert "version.tenant_id = new.tenant_id" in guard
    assert "version.project_id = new.project_id" in guard
    assert "new.policy_digests @> plan_policy_digests" in guard
    assert "new.policy_digests is distinct from plan_policy_digests" not in guard
    assert "task run manifest policy digests must cover its task plan version" in guard
    assert "not coalesce(atlas.task_manifest_units_valid(new.units), false)" in guard
    assert "(unit ->> 'caseversionid')::uuid = any(plan_pinned_case_version_ids)" in guard
    matrix_memberships = {
        "environmentids": "environmentid",
        "browserprofileversionids": "browserprofileversionid",
        "identityprofileversionids": "identityprofileversionid",
        "dataprofileversionids": "dataprofileversionid",
    }
    for matrix_key, unit_key in matrix_memberships.items():
        assert f"(plan_matrix -> '{matrix_key}') ? (unit ->> '{unit_key}')" in guard
    assert "plan_profile_refs -> 'caseprofiles'" in guard
    assert "profile.value ->> 'caseversionid' = unit ->> 'caseversionid'" in guard
    assert (
        "row( unit ->> 'executioncontractversionid', "
        "unit ->> 'fixtureblueprintversionid' ) is distinct from row( "
        "case_profile ->> 'executioncontractversionid', "
        "case_profile ->> 'fixtureblueprintversionid' )"
    ) in guard
    assert guard.count("task run manifest unit must derive from its task plan version") == 4
    assert "parameterdigest" not in guard

    function_position = sql.index("create function atlas.guard_task_run_manifest_insert")
    guard_trigger_position = sql.index("create trigger task_run_manifest_guard_insert")
    immutable_trigger_position = sql.index("create trigger task_run_manifest_prevent_mutation")
    assert function_position < guard_trigger_position < immutable_trigger_position
    assert (
        "create trigger task_run_manifest_guard_insert before insert on "
        "atlas.task_run_manifest for each row execute function "
        "atlas.guard_task_run_manifest_insert()"
    ) in sql


def test_plan_version_references_only_publishable_same_scope_runtime_inputs() -> None:
    sql = " ".join(_sql("UPGRADE_STATEMENTS").split())

    assert "from atlas.case_version version" in sql
    assert sql.count("version.status = 'published'") == 2
    assert "from jsonb_array_elements_text(new.matrix -> 'environmentids')" in sql
    assert "left join atlas.environment environment" in sql
    assert "environment.tenant_id = new.tenant_id" in sql
    assert "environment.project_id = new.project_id" in sql
    assert "environment.status = 'active'" in sql
    assert "environment.kind in ('test', 'staging')" in sql
    assert "task plan version requires active same-scope test or staging environments" in sql
    assert "from jsonb_array_elements(new.profile_refs -> 'caseprofiles')" in sql
    assert "left join atlas.data_blueprint_version version" in sql
    assert "version.tenant_id = new.tenant_id" in sql
    assert "version.project_id = new.project_id" in sql
    assert "task plan version requires published same-scope fixture blueprint versions" in sql


def test_upgrade_enforces_trigger_attempt_and_event_idempotency() -> None:
    sql = _sql("UPGRADE_STATEMENTS")
    normalized = " ".join(sql.split())
    event_guard = normalized.split("create function atlas.guard_task_run_event_insert", maxsplit=1)[
        1
    ].split("create trigger task_plan_guard_update", maxsplit=1)[0]

    assert "unique (\n        tenant_id, trigger_source, trigger_fingerprint" in sql
    assert "execution_unit_run_key_unique" in sql
    assert "execution_unit_run_ordinal_unique" in sql
    assert "unit_attempt_number_unique" in sql
    assert "task_run_event_sequence_unique unique (task_run_id, seq)" in sql
    assert "from atlas.execution_unit unit" in sql
    assert "for update" in sql
    assert "unit attempt number must be gapless" in sql
    assert "task run event sequence must be gapless" in sql
    assert "task run event state must match its narrowest scope" in sql
    assert "attempt.queued_at" in sql
    assert "unit.created_at" in sql
    assert "new.occurred_at < target_occurred_floor" in sql
    assert "task run event cannot predate its narrowest scope" in sql
    assert "where attempt.id = new.id" in sql
    assert "where event.id = new.id" in sql
    assert "unit_attempt_id is null or execution_unit_id is not null" in sql

    run_lock = event_guard.index("from atlas.task_run run")
    replay_short_circuit = event_guard.index("from atlas.task_run_event event")
    replay_return = event_guard.index("return new")
    attempt_unit_lock = event_guard.index("from atlas.execution_unit unit")
    attempt_lock = event_guard.index("from atlas.unit_attempt attempt")
    state_revalidation = event_guard.index("task run event state must match its narrowest scope")
    assert (
        run_lock
        < replay_short_circuit
        < replay_return
        < attempt_unit_lock
        < attempt_lock
        < state_revalidation
    )
    assert event_guard.count("for update") == 4
    assert "greatest( run.queued_at, run.started_at, run.finalized_at," in event_guard
    assert "run.closed_at, run.cleanup_resolved_at )" in event_guard
    assert "greatest( unit.created_at, unit.started_at, unit.finalized_at," in event_guard
    assert "unit.closed_at, unit.cleanup_resolved_at )" in event_guard
    assert "greatest( attempt.queued_at, attempt.started_at, attempt.finalized_at," in event_guard
    assert "attempt.closed_at, attempt.cleanup_resolved_at )" in event_guard
    assert ".updated_at" not in event_guard


def test_upgrade_uses_only_the_frozen_three_axis_states() -> None:
    sql = _sql("UPGRADE_STATEMENTS")

    lifecycle = (
        "'queued', 'running', 'pause_requested', 'paused', 'canceling', 'finalizing', 'closed'"
    )
    quality = "'pending', 'passed', 'failed', 'blocked', 'inconclusive', 'infra_error', 'canceled'"
    hygiene = "'not_required', 'pending', 'running', 'cleaned', 'cleanup_failed', 'leaked'"
    assert lifecycle in " ".join(sql.split())
    assert quality in " ".join(sql.split())
    assert hygiene in " ".join(sql.split())
    assert "atlas.task_execution_state_valid(" in sql
    assert "hygiene in ('cleaned', 'leaked')" in sql
    assert "hygiene in ('cleaned', 'cleanup_failed', 'leaked')" not in sql
    assert "closed_at >= cleanup_resolved_at" not in sql
    assert "and hygiene not in ('pending', 'running')" not in sql
    assert "(quality <> 'pending' and finalized_at is not null)" in sql
    assert (
        "lifecycle = 'closed'\n            and closed_at is not null\n"
        "            and quality <> 'pending'"
    ) in sql
    assert sql.count("or new.hygiene not in ('pending', 'not_required')") == 3
    assert sql.count("if old.lifecycle = 'closed'") == 3
    assert (
        sql.count(
            "new.lifecycle, new.quality, new.started_at,\n          new.finalized_at, new.closed_at"
        )
        == 3
    )
    assert "closed task run is immutable" not in sql
    assert "closed execution unit is immutable" not in sql
    assert "closed unit attempt is immutable" not in sql
    assert "closed task run lifecycle, quality, and close milestones are immutable" in sql
    assert "closed execution unit lifecycle, quality, and close milestones are immutable" in sql
    assert "closed unit attempt lifecycle, quality, and close milestones are immutable" in sql


def test_upgrade_enforces_the_conservative_lifecycle_graph() -> None:
    sql = " ".join(_sql("UPGRADE_STATEMENTS").split())

    assert "create function atlas.task_lifecycle_transition_valid" in sql
    assert "old_state = new_state" in sql
    assert "old_state = new_state and old_state <> 'closed'" not in sql
    assert "old_state = 'queued' and new_state in ('running', 'canceling', 'finalizing')" in sql
    assert (
        "old_state = 'running' and new_state in ('pause_requested', 'canceling', 'finalizing')"
    ) in sql
    assert (
        "old_state = 'pause_requested' and new_state in "
        "('running', 'paused', 'canceling', 'finalizing')"
    ) in sql
    assert "old_state = 'paused' and new_state in ('running', 'canceling', 'finalizing')" in sql
    assert "old_state = 'canceling' and new_state = 'finalizing'" in sql
    assert "old_state = 'finalizing' and new_state = 'closed'" in sql
    assert "old_state = 'canceling' and new_state = 'queued'" not in sql
    assert sql.count("not atlas.task_lifecycle_transition_valid(") == 3
    assert "grant execute on function atlas.task_lifecycle_transition_valid" in sql


def test_upgrade_enforces_the_hygiene_retry_and_resolution_graph() -> None:
    sql = " ".join(_sql("UPGRADE_STATEMENTS").split())
    helper = sql.split("create function atlas.task_hygiene_transition_valid", maxsplit=1)[1].split(
        "alter table atlas.case_version", maxsplit=1
    )[0]

    assert "create function atlas.task_hygiene_transition_valid" in sql
    assert "old_state = new_state" in sql
    assert "old_state = 'pending' and new_state = 'running'" in sql
    assert "old_state = 'running' and new_state in ('cleaned', 'cleanup_failed', 'leaked')" in sql
    assert "old_state = 'cleanup_failed' and new_state in ('running', 'leaked')" in sql
    for terminal in ("not_required", "cleaned", "leaked"):
        assert f"old_state = '{terminal}' and" not in helper
    assert sql.count("not atlas.task_hygiene_transition_valid(") == 3
    assert "revoke all on function atlas.task_hygiene_transition_valid" in sql
    assert "grant execute on function atlas.task_hygiene_transition_valid" in sql


def test_upgrade_forces_rls_and_grants_no_delete_privilege() -> None:
    sql = _sql("UPGRADE_STATEMENTS")

    tables = (
        "task_plan",
        "task_plan_version",
        "task_run",
        "task_run_manifest",
        "execution_unit",
        "unit_attempt",
        "task_run_event",
    )
    for table in tables:
        assert f"alter table atlas.{table} force row level security" in sql
        assert f"revoke all on atlas.{table} from atlas_app" in sql
        assert f"grant select, insert on atlas.{table} to atlas_app" in sql
    assert "tenant_id = (select atlas.current_tenant_id())" in sql
    assert "grant update (" in sql
    assert "grant delete" not in sql


def test_downgrade_removes_children_helpers_and_added_case_scope() -> None:
    statements = tuple(
        " ".join(item.split()).casefold() for item in _statements("DOWNGRADE_STATEMENTS")
    )
    sql = "\n".join(statements)

    assert statements[:8] == (
        "alter table atlas.task_run drop constraint if exists task_run_manifest_reverse_scope_fk",
        "drop table if exists atlas.task_run_event",
        "drop table if exists atlas.unit_attempt",
        "drop table if exists atlas.execution_unit",
        "drop table if exists atlas.task_run_manifest",
        "drop table if exists atlas.task_run",
        "drop table if exists atlas.task_plan_version",
        "drop table if exists atlas.task_plan",
    )
    assert "drop function if exists atlas.guard_task_run_event_insert()" in sql
    assert "drop function if exists atlas.guard_task_run_manifest_insert()" in sql
    assert "drop function if exists atlas.task_execution_state_valid(" in sql
    assert "drop function if exists atlas.task_lifecycle_transition_valid(text, text)" in sql
    assert "drop function if exists atlas.task_hygiene_transition_valid(text, text)" in sql
    assert "drop function if exists atlas.task_manifest_units_valid(jsonb)" in sql
    assert "drop function if exists atlas.task_json_object_size(jsonb)" in sql
    assert "drop constraint if exists case_version_task_scope_unique" in sql
