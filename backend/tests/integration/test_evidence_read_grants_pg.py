"""Real PostgreSQL tests for finalized artifact scope and read grants."""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from os import environ
from typing import cast
from uuid import UUID, uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.integration.test_auth_api import (
    bootstrap_principal as bootstrap_auth_principal,
)
from tests.integration.test_auth_api import login as login_principal
from tests.integration.test_cases_api import (
    RecordingDebugRunDispatcher,
    bootstrap_case_role,
    bootstrap_environment,
    bootstrap_project,
    case_payload_with_exact_bindings,
    mark_debug_run_passed,
    seed_published_case_blueprint,
)

from atlas_testops.core.config import Settings
from atlas_testops.domain.case import DebugRun
from atlas_testops.domain.runtime import EvidenceReadPurpose
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.auth import AuthRepository
from atlas_testops.infrastructure.repositories.evidence import EvidenceRepository
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


def test_finalized_artifact_scope_and_bounded_grant_lifecycle() -> None:
    """Exercise RLS, exact artifact scope, atomic reads, and immutable revocation."""

    assert DATABASE_URL is not None
    suffix = uuid7().hex[-10:]
    settings = Settings(
        environment="test",
        cors_origins=["https://atlas.example.test"],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=6,
    )
    dispatcher = RecordingDebugRunDispatcher()
    application = create_app(settings, debug_run_dispatcher=dispatcher)

    with TestClient(application) as client:
        tenant_id, project_id, headers = bootstrap_project(client, suffix)
        environment_id = bootstrap_environment(
            client,
            project_id,
            headers,
            suffix,
            allowed_origins=["https://staging.example.test"],
        )
        role = bootstrap_case_role(client, project_id, headers, suffix)
        blueprint_version_id, blueprint_version_ref, blueprint_digest = (
            seed_published_case_blueprint(
                tenant_id=tenant_id,
                project_id=project_id,
                environment_id=environment_id,
                published_by=headers["X-Atlas-Actor-ID"],
                suffix=suffix,
            )
        )
        created = client.post(
            f"/v1/projects/{project_id}/test-cases",
            headers={**headers, "Idempotency-Key": f"evidence-case-{suffix}"},
            json=case_payload_with_exact_bindings(
                f"E{suffix}",
                role=role,
                blueprint_version_id=blueprint_version_id,
                blueprint_version_ref=blueprint_version_ref,
                blueprint_digest=blueprint_digest,
            ),
        )
        assert created.status_code == 201, created.text
        draft = client.get(
            f"/v1/test-cases/{created.json()['id']}/workflow-draft",
            headers=headers,
        )
        assert draft.status_code == 200, draft.text
        started = client.post(
            f"/v1/test-cases/{created.json()['id']}/workflow-draft/debug-runs",
            headers={
                **headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": f"evidence-debug-{suffix}",
            },
            json={
                "environmentId": environment_id,
                "baseSemanticRevision": 1,
                "executionDeadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            },
        )
        assert started.status_code == 202, started.text
        run = DebugRun.model_validate(started.json())
        manifest_id, _ = mark_debug_run_passed(
            client=client,
            tenant_id=tenant_id,
            project_id=project_id,
            environment_id=environment_id,
            headers=headers,
            role=role,
            blueprint_version_id=blueprint_version_id,
            run=run,
            suffix=suffix,
        )
        email = f"evidence-owner-{suffix}@example.com"
        principal = bootstrap_auth_principal(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=email,
        )
        principal_user = cast(dict[str, object], principal["user"])
        session_user_id = UUID(cast(str, principal_user["id"]))
        accepted_login = login_principal(
            client,
            tenant_id=tenant_id,
            project_id=project_id,
            email=email,
        )
        assert accepted_login.status_code == 200, accepted_login.text
        session_token = client.cookies.get("atlas_session")
        assert session_token is not None
        session_token_hash = sha256(session_token.encode()).hexdigest()
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "select set_config('atlas.session_hash', %s, true)",
                (session_token_hash,),
            )
            session_row = connection.execute(
                "select id from atlas.platform_session where token_hash = %s",
                (session_token_hash,),
            ).fetchone()
            assert session_row is not None
            platform_session_id = cast(UUID, session_row[0])

    async def exercise_repository() -> None:
        database = Database(settings)
        repository = EvidenceRepository()
        tenant_uuid = UUID(tenant_id)
        actor_id = UUID(headers["X-Atlas-Actor-ID"])
        await database.open()
        try:
            context = DatabaseContext(
                tenant_id=tenant_uuid,
                actor_id=actor_id,
                request_id=f"evidence-grant:{suffix}",
            )
            session_context = DatabaseContext(
                tenant_id=tenant_uuid,
                actor_id=session_user_id,
                request_id=f"evidence-session-grant:{suffix}",
            )
            async with database.transaction(context) as connection:
                artifacts = await repository.list_manifest_artifacts(
                    connection,
                    manifest_id=UUID(manifest_id),
                )
                assert len(artifacts) == 1
                artifact = artifacts[0]
                loaded = await repository.get_manifest_artifact(
                    connection,
                    debug_run_id=run.id,
                    artifact_id=artifact.artifact_id,
                )
                assert loaded == artifact

            session_created_at = datetime.now(UTC)
            session_grant_hash = sha256(f"{suffix}:session-bound".encode()).hexdigest()
            async with database.transaction(session_context) as connection:
                await repository.lock_read_grant_scope(
                    connection,
                    tenant_id=tenant_uuid,
                    artifact_id=artifact.artifact_id,
                    issued_to_actor_id=session_user_id,
                    platform_session_id=platform_session_id,
                    purpose=EvidenceReadPurpose.DOWNLOAD,
                )
                session_grant = await repository.issue_read_grant(
                    connection,
                    grant_id=uuid7(),
                    token_hash=session_grant_hash,
                    artifact=artifact,
                    issued_to_actor_id=session_user_id,
                    platform_session_id=platform_session_id,
                    purpose=EvidenceReadPurpose.DOWNLOAD,
                    max_reads=2,
                    created_at=session_created_at,
                    expires_at=session_created_at + timedelta(seconds=60),
                )
                assert session_grant.platform_session_id == platform_session_id

            async with database.transaction(session_context) as connection:
                session_redeemed = await repository.redeem_read_grant(
                    connection,
                    tenant_id=tenant_uuid,
                    token_hash=session_grant_hash,
                    artifact_id=artifact.artifact_id,
                    issued_to_actor_id=session_user_id,
                    platform_session_id=platform_session_id,
                    purpose=EvidenceReadPurpose.DOWNLOAD,
                    redeemed_at=datetime.now(UTC),
                )
                assert session_redeemed is not None and session_redeemed.read_count == 1

            async with database.session_transaction(
                token_hash=session_token_hash,
                request_id=f"revoke-evidence-session:{suffix}",
            ) as connection:
                assert await AuthRepository().revoke_session(
                    connection,
                    token_hash=session_token_hash,
                    revoked_at=datetime.now(UTC),
                )

            async with database.transaction(session_context) as connection:
                revoked_session_read = await repository.redeem_read_grant(
                    connection,
                    tenant_id=tenant_uuid,
                    token_hash=session_grant_hash,
                    artifact_id=artifact.artifact_id,
                    issued_to_actor_id=session_user_id,
                    platform_session_id=platform_session_id,
                    purpose=EvidenceReadPurpose.DOWNLOAD,
                    redeemed_at=datetime.now(UTC),
                )
                assert revoked_session_read is None

            created_at = datetime.now(UTC)
            old_token_hash = sha256(f"{suffix}:old".encode()).hexdigest()
            old_grant_id = uuid7()
            async with database.transaction(context) as connection:
                old_grant = await repository.issue_read_grant(
                    connection,
                    grant_id=old_grant_id,
                    token_hash=old_token_hash,
                    artifact=artifact,
                    issued_to_actor_id=actor_id,
                    platform_session_id=None,
                    purpose=EvidenceReadPurpose.INLINE,
                    max_reads=2,
                    created_at=created_at,
                    expires_at=created_at + timedelta(seconds=60),
                )
                assert old_grant.read_count == 0

            async with database.transaction(context) as connection:
                wrong_purpose = await repository.redeem_read_grant(
                    connection,
                    tenant_id=tenant_uuid,
                    token_hash=old_token_hash,
                    artifact_id=artifact.artifact_id,
                    issued_to_actor_id=actor_id,
                    platform_session_id=None,
                    purpose=EvidenceReadPurpose.DOWNLOAD,
                    redeemed_at=datetime.now(UTC),
                )
                assert wrong_purpose is None

            for expected_count in (1, 2):
                async with database.transaction(context) as connection:
                    consumed = await repository.redeem_read_grant(
                        connection,
                        tenant_id=tenant_uuid,
                        token_hash=old_token_hash,
                        artifact_id=artifact.artifact_id,
                        issued_to_actor_id=actor_id,
                        platform_session_id=None,
                        purpose=EvidenceReadPurpose.INLINE,
                        redeemed_at=datetime.now(UTC),
                    )
                    assert consumed is not None
                    assert consumed.read_count == expected_count

            async with database.transaction(context) as connection:
                exhausted = await repository.redeem_read_grant(
                    connection,
                    tenant_id=tenant_uuid,
                    token_hash=old_token_hash,
                    artifact_id=artifact.artifact_id,
                    issued_to_actor_id=actor_id,
                    platform_session_id=None,
                    purpose=EvidenceReadPurpose.INLINE,
                    redeemed_at=datetime.now(UTC),
                )
                assert exhausted is None
                replacement_created_at = datetime.now(UTC)
                revoked_count = await repository.revoke_active_read_grants(
                    connection,
                    tenant_id=tenant_uuid,
                    artifact_id=artifact.artifact_id,
                    issued_to_actor_id=actor_id,
                    platform_session_id=None,
                    purpose=EvidenceReadPurpose.INLINE,
                    revoked_at=replacement_created_at,
                )
                assert revoked_count == 1
                replacement = await repository.issue_read_grant(
                    connection,
                    grant_id=uuid7(),
                    token_hash=sha256(f"{suffix}:replacement".encode()).hexdigest(),
                    artifact=artifact,
                    issued_to_actor_id=actor_id,
                    platform_session_id=None,
                    purpose=EvidenceReadPurpose.INLINE,
                    max_reads=1,
                    created_at=replacement_created_at,
                    expires_at=replacement_created_at + timedelta(seconds=60),
                )

            async with database.transaction(context) as connection:
                revoked = await repository.revoke_read_grant(
                    connection,
                    tenant_id=tenant_uuid,
                    grant_id=replacement.id,
                    artifact_id=artifact.artifact_id,
                    issued_to_actor_id=actor_id,
                    revoked_at=datetime.now(UTC),
                )
                assert revoked is not None and revoked.revoked_at is not None

            concurrent_created_at = datetime.now(UTC)
            concurrent_token_hash = sha256(f"{suffix}:concurrent".encode()).hexdigest()
            async with database.transaction(context) as connection:
                await repository.issue_read_grant(
                    connection,
                    grant_id=uuid7(),
                    token_hash=concurrent_token_hash,
                    artifact=artifact,
                    issued_to_actor_id=actor_id,
                    platform_session_id=None,
                    purpose=EvidenceReadPurpose.DOWNLOAD,
                    max_reads=1,
                    created_at=concurrent_created_at,
                    expires_at=concurrent_created_at + timedelta(seconds=60),
                )

            async def redeem_concurrently() -> bool:
                async with database.transaction(context) as connection:
                    return (
                        await repository.redeem_read_grant(
                            connection,
                            tenant_id=tenant_uuid,
                            token_hash=concurrent_token_hash,
                            artifact_id=artifact.artifact_id,
                            issued_to_actor_id=actor_id,
                            platform_session_id=None,
                            purpose=EvidenceReadPurpose.DOWNLOAD,
                            redeemed_at=datetime.now(UTC),
                        )
                        is not None
                    )

            concurrent_results = await asyncio.gather(
                redeem_concurrently(),
                redeem_concurrently(),
            )
            assert sum(concurrent_results) == 1

            replacement_actor_id = uuid7()
            replacement_context = DatabaseContext(
                tenant_id=tenant_uuid,
                actor_id=replacement_actor_id,
                request_id=f"evidence-replacement:{suffix}",
            )

            async def issue_replacement(label: str) -> UUID:
                async with database.transaction(replacement_context) as connection:
                    await repository.lock_read_grant_scope(
                        connection,
                        tenant_id=tenant_uuid,
                        artifact_id=artifact.artifact_id,
                        issued_to_actor_id=replacement_actor_id,
                        platform_session_id=None,
                        purpose=EvidenceReadPurpose.INLINE,
                    )
                    issued_at = datetime.now(UTC)
                    await repository.revoke_active_read_grants(
                        connection,
                        tenant_id=tenant_uuid,
                        artifact_id=artifact.artifact_id,
                        issued_to_actor_id=replacement_actor_id,
                        platform_session_id=None,
                        purpose=EvidenceReadPurpose.INLINE,
                        revoked_at=issued_at,
                    )
                    grant = await repository.issue_read_grant(
                        connection,
                        grant_id=uuid7(),
                        token_hash=sha256(f"{suffix}:{label}".encode()).hexdigest(),
                        artifact=artifact,
                        issued_to_actor_id=replacement_actor_id,
                        platform_session_id=None,
                        purpose=EvidenceReadPurpose.INLINE,
                        max_reads=1,
                        created_at=issued_at,
                        expires_at=issued_at + timedelta(seconds=60),
                    )
                    return grant.id

            replacement_ids = await asyncio.gather(
                issue_replacement("replacement-a"),
                issue_replacement("replacement-b"),
            )
            assert len(set(replacement_ids)) == 2
            async with database.transaction(replacement_context) as connection:
                active = await connection.execute(
                    """
                    select count(*)
                    from atlas.evidence_read_grant
                    where tenant_id = %s
                      and artifact_id = %s
                      and issued_to_actor_id = %s
                      and platform_session_id is null
                      and purpose = 'INLINE'
                      and revoked_at is null
                      and expires_at > now()
                      and read_count < max_reads
                    """,
                    (tenant_uuid, artifact.artifact_id, replacement_actor_id),
                )
                active_row = await active.fetchone()
                assert active_row is not None and active_row["count"] == 1
            async with database.transaction(context) as connection:
                hidden_actor_grants = await connection.execute(
                    """
                    select count(*)
                    from atlas.evidence_read_grant
                    where issued_to_actor_id = %s
                    """,
                    (replacement_actor_id,),
                )
                hidden_actor_row = await hidden_actor_grants.fetchone()
                assert hidden_actor_row is not None and hidden_actor_row["count"] == 0

            with pytest.raises(
                (psycopg.errors.RaiseException, psycopg.errors.InsufficientPrivilege)
            ):
                async with database.transaction(context) as connection:
                    await connection.execute(
                        """
                        update atlas.evidence_read_grant
                        set max_reads = max_reads + 1, revision = revision + 1
                        where id = %s
                        """,
                        (replacement.id,),
                    )

            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with database.transaction(context) as connection:
                    await connection.execute(
                        "delete from atlas.evidence_read_grant where id = %s",
                        (replacement.id,),
                    )

            async with database.transaction(
                DatabaseContext(tenant_id=uuid7(), request_id="cross-tenant-evidence")
            ) as connection:
                hidden = await repository.get_manifest_artifact(
                    connection,
                    debug_run_id=run.id,
                    artifact_id=artifact.artifact_id,
                )
                assert hidden is None
        finally:
            await database.close()

    asyncio.run(exercise_repository())


def test_evidence_read_grant_schema_forces_rls_and_minimum_privileges() -> None:
    """Verify the deployed migration under the runtime database role."""

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        rls = connection.execute(
            """
            select class.relrowsecurity, class.relforcerowsecurity
            from pg_class class
            join pg_namespace namespace on namespace.oid = class.relnamespace
            where namespace.nspname = 'atlas'
              and class.relname = 'evidence_read_grant'
            """
        ).fetchone()
        privileges = connection.execute(
            """
            select
              has_table_privilege(current_user, 'atlas.evidence_read_grant', 'SELECT'),
              has_table_privilege(current_user, 'atlas.evidence_read_grant', 'INSERT'),
              has_table_privilege(current_user, 'atlas.evidence_read_grant', 'UPDATE'),
              has_table_privilege(current_user, 'atlas.evidence_read_grant', 'DELETE')
            """
        ).fetchone()
        update_columns = connection.execute(
            """
            select
              has_column_privilege(
                current_user, 'atlas.evidence_read_grant', 'read_count', 'UPDATE'
              ),
              has_column_privilege(
                current_user, 'atlas.evidence_read_grant', 'last_read_at', 'UPDATE'
              ),
              has_column_privilege(
                current_user, 'atlas.evidence_read_grant', 'revoked_at', 'UPDATE'
              ),
              has_column_privilege(
                current_user, 'atlas.evidence_read_grant', 'revision', 'UPDATE'
              ),
              has_column_privilege(
                current_user, 'atlas.evidence_read_grant', 'max_reads', 'UPDATE'
              )
            """
        ).fetchone()

    assert rls == (True, True)
    assert privileges == (True, True, False, False)
    assert update_columns == (True, True, True, True, False)
