"""Contract tests for private Evidence read grant database guards."""

from pathlib import Path
from runpy import run_path
from typing import cast

MIGRATION_PATH = (
    Path(__file__).parents[2] / "migrations" / "versions" / "20260715_0018_evidence_read_grants.py"
)


def _migration_sql(name: str) -> str:
    namespace = run_path(str(MIGRATION_PATH))
    statements = cast(tuple[str, ...], namespace[name])
    return "\n".join(statements)


def test_upgrade_adds_exact_artifact_scope_and_bounded_hash_only_grants() -> None:
    sql = _migration_sql("UPGRADE_STATEMENTS")

    assert "evidence_artifact_read_scope_unique" in sql
    assert "evidence_artifact_object_ref_scope_valid" in sql
    assert "replace(execution_contract_id::text, '-', '')" in sql
    assert "create unique index evidence_artifact_object_ref_unique" in sql
    assert "create index evidence_artifact_run_idx" in sql
    assert "create table atlas.evidence_read_grant" in sql
    assert "evidence_read_grant_token_hash_unique" in sql
    assert "token_hash ~ '^[0-9a-f]{64}$'" in sql
    assert "max_reads between 1 and 32" in sql
    assert "created_at + interval '120 seconds'" in sql
    assert "purpose in ('INLINE', 'DOWNLOAD')" in sql


def test_upgrade_requires_finalized_verified_scope_and_strict_updates() -> None:
    sql = _migration_sql("UPGRADE_STATEMENTS")

    assert "artifact.integrity = 'VERIFIED'" in sql
    assert "run.lifecycle = 'TERMINATED'" in sql
    assert "run.evidence_manifest_id = manifest.id" in sql
    assert "evidence read grant session scope is invalid" in sql
    assert "evidence read grant must start unused and active" in sql
    assert "evidence read grant scope is immutable" in sql
    assert "new.read_count <> old.read_count + 1" in sql
    assert "revoked evidence read grant is immutable" in sql


def test_upgrade_forces_rls_and_grants_no_delete_privilege() -> None:
    sql = _migration_sql("UPGRADE_STATEMENTS")

    assert "alter table atlas.evidence_read_grant force row level security" in sql
    assert "tenant_id = (select atlas.current_tenant_id())" in sql
    assert "issued_to_actor_id = (select atlas.current_actor_id())" in sql
    assert "revoke all on atlas.evidence_read_grant from atlas_app" in sql
    assert "grant select, insert on atlas.evidence_read_grant to atlas_app" in sql
    assert "grant update (read_count, last_read_at, revoked_at, revision)" in sql
    assert "grant delete" not in sql.casefold()


def test_downgrade_removes_every_added_object() -> None:
    sql = _migration_sql("DOWNGRADE_STATEMENTS")

    assert "drop table if exists atlas.evidence_read_grant" in sql
    assert "drop function if exists atlas.guard_evidence_read_grant_update()" in sql
    assert "drop function if exists atlas.guard_evidence_read_grant_insert()" in sql
    assert "drop index if exists atlas.evidence_artifact_run_idx" in sql
    assert "drop index if exists atlas.evidence_artifact_object_ref_unique" in sql
    assert "drop constraint if exists evidence_artifact_object_ref_scope_valid" in sql
    assert "drop constraint if exists evidence_artifact_read_scope_unique" in sql
