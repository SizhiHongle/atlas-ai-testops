"""Contract tests for the immutable AttemptSeal Result migration."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "20260718_0032_attempt_seal_result_fact.py"
)


def _namespace() -> dict[str, object]:
    return run_path(str(MIGRATION_PATH))


def _sql(name: str = "UPGRADE_STATEMENTS") -> str:
    statements = cast(tuple[str, ...], _namespace()[name])
    return " ".join("\n".join(statements).casefold().split())


def test_revision_extends_result_truth_atomically() -> None:
    namespace = _namespace()
    source = MIGRATION_PATH.read_text(encoding="utf-8").casefold()

    assert namespace["revision"] == "20260718_0032"
    assert namespace["down_revision"] == "20260717_0031"
    assert "autocommit_block" not in source


def test_result_fact_is_attempt_scoped_immutable_and_idempotent() -> None:
    sql = _sql()

    assert "create table atlas.unit_attempt_result_fact" in sql
    assert "unit_attempt_result_fact_attempt_scope_fk" in sql
    assert "unit_attempt_result_fact_attempt_unique unique (unit_attempt_id)" in sql
    assert "before update or delete on atlas.unit_attempt_result_fact" in sql
    assert "for each row execute function atlas.prevent_fact_mutation()" in sql
    assert "alter table atlas.unit_attempt_result_fact force row level security" in sql


def test_database_recomputes_canonical_hash_and_ticket_binding() -> None:
    sql = _sql()

    assert "returns trigger language plpgsql security definer" in sql
    assert "stored_attempt.lifecycle <> 'running'" in sql
    assert "new.accepted_at <> transaction_timestamp()" in sql
    assert "new.data_hygiene <> (case stored_attempt.hygiene" in sql
    assert "stored_ticket.ticket_digest <> new.execution_ticket_digest" in sql
    assert "jsonb_each_text(stored_manifest.policy_digests)" in sql
    assert "'schemaversion', 'atlas.formal-attempt-runtime/0.1'" in sql
    assert "new.runtime_digest <> atlas.task_sha256_json" in sql
    assert "atlas.task_json_has_sensitive_keys(new.seal)" in sql
    assert "new.seal - 'signaturevalue' - 'contenthash'" in sql
    assert ") <> new.content_hash" in sql
    assert "or new.seal ->> 'signaturevalue' <> new.signature_value" in sql


def test_result_ref_and_integrity_incident_are_append_only() -> None:
    sql = _sql()

    assert "create table atlas.result_ref" in sql
    assert "result_ref_attempt_unique unique (unit_attempt_id)" in sql
    assert (
        "tenant_id, project_id, seal_content_hash, created_at ) references "
        "atlas.unit_attempt_result_fact"
    ) in sql
    assert "create table atlas.result_integrity_incident" in sql
    assert (
        "tenant_id, project_id, accepted_content_hash ) references atlas.unit_attempt_result_fact"
    ) in sql
    assert "accepted_content_hash <> conflicting_content_hash" in sql
    assert "result_integrity_incident_conflict_unique" in sql
    assert "grant select, insert on atlas.result_integrity_incident to atlas_app" in sql


def test_downgrade_refuses_to_discard_result_truth() -> None:
    sql = _sql("DOWNGRADE_STATEMENTS")

    assert "cannot downgrade while attemptseal result facts exist" in sql
    assert sql.index("cannot downgrade") < sql.index(
        "drop table if exists atlas.unit_attempt_result_fact"
    )
