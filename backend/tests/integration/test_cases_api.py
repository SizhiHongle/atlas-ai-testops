"""TestCase authoring API tests against real PostgreSQL guards and RLS."""

import asyncio
from datetime import UTC, datetime, timedelta
from os import environ
from typing import cast
from uuid import UUID, uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import JsonValue, SecretStr

from atlas_testops.application.debug_runtime import DebugRuntimeService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError
from atlas_testops.domain.case import DebugRun, canonical_digest
from atlas_testops.domain.fixture import (
    DataBlueprintContract,
)
from atlas_testops.domain.fixture import (
    canonical_digest as fixture_digest,
)
from atlas_testops.domain.runtime import (
    CHAIN_START_DIGEST,
    AssertionResultInput,
    AssertionStatus,
    BindDebugExecution,
    BindExecutionActor,
    BrowserExecutionProfile,
    BrowserRuntimeReportKind,
    EvidenceArtifactInput,
    EvidenceArtifactKind,
    EvidenceIntegrity,
    FinalizeDebugEvidence,
    ModelExecutionProfile,
    ToolExecutionProfile,
    Viewport,
    build_browser_runtime_report,
    expected_assertion_digest,
)
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.browser_runtime import (
    BrowserRuntimeReportRepository,
)
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")
OWNER_DATABASE_URL = environ.get("ATLAS_TEST_OWNER_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]

DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"


class RecordingDebugRunDispatcher:
    """Record only dispatch requests; never fabricate runtime completion."""

    def __init__(self, *, fail_starts: int = 0, fail_cancels: int = 0) -> None:
        self.started: list[DebugRun] = []
        self.canceled: list[DebugRun] = []
        self.fail_starts = fail_starts
        self.fail_cancels = fail_cancels

    async def start(self, run: DebugRun) -> None:
        self.started.append(run)
        if self.fail_starts > 0:
            self.fail_starts -= 1
            raise RuntimeError("simulated Debug Runtime dispatch failure")

    async def cancel(self, run: DebugRun) -> None:
        self.canceled.append(run)
        if self.fail_cancels > 0:
            self.fail_cancels -= 1
            raise RuntimeError("simulated Debug Runtime signal failure")


def actor_headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-Atlas-Tenant-ID": tenant_id,
        "X-Atlas-Actor-ID": str(uuid7()),
    }


def bootstrap_project(client: TestClient, suffix: str) -> tuple[str, str, dict[str, str]]:
    tenant_response = client.post(
        "/v1/tenants",
        json={"slug": f"cases-{suffix}", "name": f"Cases {suffix}"},
    )
    assert tenant_response.status_code == 201, tenant_response.text
    tenant_id = tenant_response.json()["id"]
    headers = actor_headers(tenant_id)
    project_response = client.post(
        "/v1/projects",
        headers={**headers, "Idempotency-Key": f"cases-project-{suffix}"},
        json={"projectKey": f"CASES_{suffix.upper()}", "name": "Cases Project"},
    )
    assert project_response.status_code == 201, project_response.text
    return tenant_id, project_response.json()["id"], headers


def bootstrap_environment(
    client: TestClient,
    project_id: str,
    headers: dict[str, str],
    suffix: str,
    *,
    kind: str = "TEST",
    allowed_origins: list[str] | None = None,
) -> str:
    response = client.post(
        f"/v1/projects/{project_id}/environments",
        headers={**headers, "Idempotency-Key": f"cases-env-{kind.lower()}-{suffix}"},
        json={
            "environmentKey": f"{kind.lower()}-{suffix[-8:]}",
            "name": f"{kind.title()} Environment",
            "kind": kind,
            "allowedOrigins": allowed_origins or [],
        },
    )
    assert response.status_code == 201, response.text
    return cast(str, response.json()["id"])


def graph_payload() -> dict[str, object]:
    def port(key: str, semantic_type: str) -> dict[str, object]:
        return {
            "key": key,
            "semanticType": semantic_type,
            "kind": "data",
            "required": True,
            "sensitive": False,
        }

    return {
        "schemaVersion": "atlas.workflow-graph/0.1",
        "nodes": [
            {
                "id": "prepare-data",
                "kind": "fixture",
                "versionRef": "fixture.customer@1.0.0",
                "phase": "setup",
                "inputPorts": [],
                "outputPorts": [port("customerId", "CustomerId")],
                "params": {},
                "terminal": False,
                "oracleStrength": None,
            },
            {
                "id": "filter-agent",
                "kind": "agent",
                "versionRef": "agent.semantic-filter@1.0.0",
                "phase": "execute",
                "inputPorts": [port("customerId", "CustomerId")],
                "outputPorts": [port("rows", "CustomerRows")],
                "params": {},
                "terminal": False,
                "oracleStrength": None,
            },
            {
                "id": "relationship-assert",
                "kind": "assertion",
                "versionRef": "assert.customer-visible@1.0.0",
                "phase": "assert",
                "inputPorts": [port("rows", "CustomerRows")],
                "outputPorts": [port("result", "AssertionResult")],
                "params": {},
                "terminal": False,
                "oracleStrength": "hard",
            },
            {
                "id": "cleanup",
                "kind": "cleanup",
                "versionRef": "cleanup.customer@1.0.0",
                "phase": "cleanup",
                "inputPorts": [port("result", "AssertionResult")],
                "outputPorts": [],
                "params": {},
                "terminal": True,
                "oracleStrength": None,
            },
        ],
        "edges": [
            {
                "id": "data-to-agent",
                "sourceNodeId": "prepare-data",
                "sourcePort": "customerId",
                "targetNodeId": "filter-agent",
                "targetPort": "customerId",
                "semanticType": "CustomerId",
                "kind": "data",
                "mapping": "direct",
            },
            {
                "id": "agent-to-assert",
                "sourceNodeId": "filter-agent",
                "sourcePort": "rows",
                "targetNodeId": "relationship-assert",
                "targetPort": "rows",
                "semanticType": "CustomerRows",
                "kind": "data",
                "mapping": "direct",
            },
            {
                "id": "assert-to-cleanup",
                "sourceNodeId": "relationship-assert",
                "sourcePort": "result",
                "targetNodeId": "cleanup",
                "targetPort": "result",
                "semanticType": "AssertionResult",
                "kind": "data",
                "mapping": "direct",
            },
        ],
    }


def case_payload(suffix: str) -> dict[str, object]:
    return {
        "caseKey": f"TC-{suffix.upper()}",
        "name": "Customer Relationship Visibility",
        "intentVersion": "0.1.0",
        "intent": {
            "schemaVersion": "atlas.test-intent/0.1",
            "summary": "A customer operator filters visible relationship rows.",
            "requirementRefs": [
                {
                    "documentId": "requirements/customer-search",
                    "documentVersion": "2026-07-15",
                    "contentDigest": DIGEST_A,
                    "anchor": "customer-search/filter-visible-rows",
                    "excerptDigest": DIGEST_B,
                }
            ],
            "actors": [
                {
                    "actorSlot": "operator",
                    "roleId": "11111111-1111-4111-8111-111111111111",
                    "roleKey": "customer.operator",
                    "roleRevision": 3,
                    "capabilities": ["customer.read"],
                }
            ],
            "fixture": {
                "blueprintVersionId": "22222222-2222-4222-8222-222222222222",
                "blueprintVersionRef": "fixture.customer@1.0.0",
                "contentDigest": DIGEST_A,
                "requiredExports": {"customerId": "CustomerId"},
            },
            "surfaces": [
                {
                    "surfaceKey": "customer.relationship-list",
                    "versionRef": "surface.customer-relationship@1.0.0",
                    "contentDigest": DIGEST_B,
                }
            ],
            "variables": {},
            "evidencePolicy": {
                "trace": True,
                "screenshots": "critical-actions",
                "retainSuccessDays": 7,
                "retainFailureDays": 30,
            },
            "recoveryPolicy": {
                "maxUnitAttempts": 1,
                "retryBrowserCrash": False,
                "retryUnknownSideEffect": False,
            },
            "outcomePolicy": {
                "requireHardOracle": True,
                "evidenceIncompleteBlocksPass": True,
                "agentMayDecidePass": False,
            },
            "requiredFeatures": [],
        },
        "graph": graph_payload(),
        "layout": {
            "prepare-data": {"x": 80, "y": 120},
            "filter-agent": {"x": 300, "y": 120},
        },
    }


def bootstrap_case_role(
    client: TestClient,
    project_id: str,
    headers: dict[str, str],
    suffix: str,
) -> dict[str, object]:
    response = client.post(
        f"/v1/projects/{project_id}/test-roles",
        headers={**headers, "Idempotency-Key": f"case-role-{suffix}"},
        json={
            "roleKey": "customer.operator",
            "name": "Customer Operator",
            "description": "Exact actor binding used by the published case.",
            "capabilities": ["customer.read"],
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def seed_published_case_blueprint(
    *,
    tenant_id: str,
    project_id: str,
    environment_id: str,
    published_by: str,
    suffix: str,
) -> tuple[str, str, str]:
    """Seed trusted Fixture evidence without exposing a public evidence API."""

    assert DATABASE_URL is not None
    blueprint_id = uuid7()
    version_id = uuid7()
    fixture_run_id = uuid7()
    runtime_evidence_id = uuid7()
    cleanup_evidence_id = uuid7()
    contract = DataBlueprintContract.model_validate(
        {
            "schemaVersion": "atlas.fixture-blueprint/0.1",
            "runInputSchema": {"type": "object", "additionalProperties": False},
            "nodes": [
                {
                    "id": "seedCustomer",
                    "atomVersionId": str(uuid7()),
                    "actorSlot": "operator",
                    "bindings": [],
                }
            ],
            "exports": [
                {
                    "name": "customerId",
                    "sourceNodeId": "seedCustomer",
                    "sourcePort": "customerId",
                    "classification": "INTERNAL",
                }
            ],
            "cleanupPolicy": "ALWAYS",
        }
    )
    content_digest = fixture_digest(contract)
    plan_digest = f"sha256:{'c' * 64}"
    compiled_plan = {
        "schemaVersion": "atlas.compiled-fixture-plan/0.1",
        "blueprintVersionId": str(version_id),
        "blueprintDigest": content_digest,
        "nodes": [],
        "executionLevels": [],
        "cleanupOrder": [],
        "exports": [
            {
                "name": "customerId",
                "sourceNodeId": "seedCustomer",
                "sourcePort": "customerId",
                "classification": "INTERNAL",
            }
        ],
        "planDigest": plan_digest,
    }
    now = datetime.now(UTC)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("select set_config('atlas.tenant_id', %s, true)", (tenant_id,))
        connection.execute(
            """
            insert into atlas.data_blueprint_definition (
              id, tenant_id, project_id, blueprint_key,
              name, description
            ) values (%s, %s, %s, 'fixture.customer', %s, %s)
            """,
            (
                blueprint_id,
                UUID(tenant_id),
                UUID(project_id),
                f"Case Fixture {suffix}",
                "Published fixture with runtime and cleanup evidence.",
            ),
        )
        connection.execute(
            """
            insert into atlas.data_blueprint_version (
              id, tenant_id, project_id, blueprint_id, version, status,
              contract, content_digest, static_validation_state,
              runtime_validation_state, cleanup_validation_state,
              validated_at, compiled_plan, plan_digest, compile_issues, compiled_at
            ) values (
              %s, %s, %s, %s, '1.0.0', 'VALIDATED',
              %s, %s, 'PASSED', 'PENDING', 'PENDING',
              %s, %s, %s, '[]'::jsonb, %s
            )
            """,
            (
                version_id,
                UUID(tenant_id),
                UUID(project_id),
                blueprint_id,
                Jsonb(contract.model_dump(mode="json", by_alias=True)),
                content_digest,
                now,
                Jsonb(compiled_plan),
                plan_digest,
                now,
            ),
        )
        connection.execute(
            """
            insert into atlas.fixture_run (
              id, tenant_id, project_id, environment_id,
              blueprint_version_id, run_kind, execution_id,
              plan_digest, input_digest, compiled_plan, run_inputs,
              cleanup_policy, status, cleanup_state, temporal_workflow_id,
              requested_by, execution_deadline, requested_at,
              started_at, ready_at, finished_at, released_at,
              terminal_intent, cleanup_generation
            ) values (
              %s, %s, %s, %s,
              %s, 'VALIDATION', %s,
              %s, %s, %s, '{}'::jsonb,
              'ALWAYS', 'RELEASED', 'CLEANED', %s,
              %s, %s, %s,
              %s, %s, %s, %s,
              'RELEASED', 1
            )
            """,
            (
                fixture_run_id,
                UUID(tenant_id),
                UUID(project_id),
                UUID(environment_id),
                version_id,
                f"case-fixture-{suffix}",
                plan_digest,
                DIGEST_A,
                Jsonb(compiled_plan),
                f"atlas-fixture/{tenant_id}/{fixture_run_id}",
                UUID(published_by),
                now + timedelta(minutes=10),
                now,
                now,
                now,
                now,
                now,
            ),
        )
        for evidence_id, kind in (
            (runtime_evidence_id, "RUNTIME"),
            (cleanup_evidence_id, "CLEANUP"),
        ):
            connection.execute(
                """
                insert into atlas.fixture_validation_evidence (
                  id, tenant_id, project_id, environment_id, fixture_run_id,
                  kind, subject, blueprint_version_id, subject_digest,
                  passed, safe_summary, observed_at
                ) values (
                  %s, %s, %s, %s, %s,
                  %s, 'BLUEPRINT_VERSION', %s, %s,
                  true, %s, %s
                )
                """,
                (
                    evidence_id,
                    UUID(tenant_id),
                    UUID(project_id),
                    UUID(environment_id),
                    fixture_run_id,
                    kind,
                    version_id,
                    plan_digest,
                    f"Trusted {kind.casefold()} validation completed.",
                    now,
                ),
            )
        connection.execute(
            """
            update atlas.data_blueprint_version
            set status = 'PUBLISHED',
                runtime_validation_state = 'PASSED',
                cleanup_validation_state = 'PASSED',
                runtime_validation_evidence_id = %s,
                runtime_validated_at = %s,
                cleanup_validation_evidence_id = %s,
                cleanup_validated_at = %s,
                published_at = %s,
                published_by = %s,
                revision = revision + 1
            where id = %s
            """,
            (
                runtime_evidence_id,
                now,
                cleanup_evidence_id,
                now,
                now,
                UUID(published_by),
                version_id,
            ),
        )
    return str(version_id), "fixture.customer@1.0.0", content_digest


def case_payload_with_exact_bindings(
    suffix: str,
    *,
    role: dict[str, object],
    blueprint_version_id: str,
    blueprint_version_ref: str,
    blueprint_digest: str,
) -> dict[str, object]:
    payload = case_payload(suffix)
    intent = cast(dict[str, object], payload["intent"])
    intent["actors"] = [
        {
            "actorSlot": "operator",
            "roleId": role["id"],
            "roleKey": role["roleKey"],
            "roleRevision": role["revision"],
            "capabilities": role["capabilities"],
        }
    ]
    intent["fixture"] = {
        "blueprintVersionId": blueprint_version_id,
        "blueprintVersionRef": blueprint_version_ref,
        "contentDigest": blueprint_digest,
        "requiredExports": {"customerId": "CustomerId"},
    }
    graph = cast(dict[str, object], payload["graph"])
    nodes = cast(list[dict[str, object]], graph["nodes"])
    fixture_node = next(node for node in nodes if node["id"] == "prepare-data")
    fixture_node["versionRef"] = blueprint_version_ref
    return payload


def mark_debug_run_passed(
    *,
    client: TestClient,
    tenant_id: str,
    project_id: str,
    environment_id: str,
    headers: dict[str, str],
    role: dict[str, object],
    blueprint_version_id: str,
    run: DebugRun,
    suffix: str,
) -> tuple[str, str]:
    """Drive a complete trusted Runtime pass without exposing an evidence API."""

    assert DATABASE_URL is not None
    origin = "https://staging.example.test"
    worker_identity = "browser-worker-integration-01"
    connector_response = client.post(
        "/v1/connector-installations",
        headers={**headers, "Idempotency-Key": f"runtime-connector-{suffix}"},
        json={
            "environmentId": environment_id,
            "installationKey": "runtime-password",
            "name": "Runtime Password Connector",
            "adapterKey": "generic-password",
            "mode": "MANAGED_TEST_ACCOUNTS",
            "configurationRef": f"cfg_runtime_{suffix}",
            "allowedOrigins": [origin],
            "requiredCapabilities": ["auth.password"],
        },
    )
    assert connector_response.status_code == 201, connector_response.text
    connector_id = cast(str, connector_response.json()["id"])
    validated_connector = client.post(
        f"/v1/connector-installations/{connector_id}:validate",
        headers={**headers, "If-Match": connector_response.headers["etag"]},
    )
    assert validated_connector.status_code == 200, validated_connector.text
    pool_response = client.post(
        f"/v1/environments/{environment_id}/account-pools",
        headers={**headers, "Idempotency-Key": f"runtime-pool-{suffix}"},
        json={
            "roleId": role["id"],
            "poolKey": "runtime-operators",
            "name": "Runtime Operators",
            "defaultTtlSeconds": 1200,
            "cooldownSeconds": 0,
        },
    )
    assert pool_response.status_code == 201, pool_response.text
    account_response = client.post(
        f"/v1/account-pools/{pool_response.json()['id']}/accounts",
        headers={**headers, "Idempotency-Key": f"runtime-account-{suffix}"},
        json={
            "connectorInstallationId": connector_id,
            "accountKey": "runtime-operator-01",
            "source": "ATLAS_MANAGED",
            "loginHintMasked": "ru***@example.test",
            "labels": {"purpose": "trusted-runtime"},
            "credentials": [
                {
                    "authMethod": "PASSWORD",
                    "purpose": "LOGIN",
                    "secretRef": f"sec_runtime_{suffix}",
                    "secretVersion": "v1",
                }
            ],
        },
    )
    assert account_response.status_code == 201, account_response.text
    account_id = cast(str, account_response.json()["id"])
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("select set_config('atlas.tenant_id', %s, true)", (tenant_id,))
        connection.execute(
            """
            update atlas.test_account
            set lifecycle_status = 'ACTIVE', health_status = 'HEALTHY',
                operational_status = 'READY',
                identity_fingerprint = 'sha256:' || repeat('d', 64),
                last_health_checked_at = statement_timestamp(),
                last_health_succeeded_at = statement_timestamp(),
                revision = revision + 1
            where id = %s
            """,
            (UUID(account_id),),
        )

    lease_response = client.post(
        "/internal/v1/account-leases",
        headers={**headers, "Idempotency-Key": f"runtime-lease-{suffix}"},
        json={
            "executionId": f"debug-run:{run.id}",
            "workerId": worker_identity,
            "environmentId": environment_id,
            "roleKey": role["roleKey"],
            "requirements": {"capabilities": role["capabilities"]},
            "ttlSeconds": 1200,
            "executionDeadline": run.execution_deadline.isoformat(),
        },
    )
    assert lease_response.status_code == 201, lease_response.text
    lease_id = UUID(cast(str, lease_response.json()["leaseId"]))
    fencing_token = cast(int, lease_response.json()["fencingToken"])
    fixture_run_id = uuid7()
    mismatched_fixture_run_id = uuid7()
    session_id = uuid7()
    browser_context_ref = f"bctx_{uuid7().hex}{uuid7().hex}"
    now = datetime.now(UTC)

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("select set_config('atlas.tenant_id', %s, true)", (tenant_id,))
        snapshot = connection.execute(
            """
            select account.revision as account_revision,
                   connector.revision as connector_revision,
                   credential.id as credential_binding_id,
                   credential.revision as credential_revision,
                   version.compiled_plan, version.plan_digest
            from atlas.test_account account
            join atlas.connector_installation connector
              on connector.id = account.connector_installation_id
            join atlas.credential_binding credential
              on credential.account_id = account.id
             and credential.status = 'ACTIVE'
            cross join atlas.data_blueprint_version version
            where account.id = %s and version.id = %s
            """,
            (UUID(account_id), UUID(blueprint_version_id)),
        ).fetchone()
        assert snapshot is not None
        (
            account_revision,
            connector_revision,
            credential_binding_id,
            credential_revision,
            compiled_plan,
            plan_digest,
        ) = snapshot
        connection.execute(
            """
            insert into atlas.browser_session_artifact (
              id, tenant_id, project_id, environment_id, lease_id,
              account_id, connector_installation_id, credential_binding_id,
              lease_fence, worker_identity, browser_context_ref,
              allowed_origins, auth_strength, status, object_ref,
              object_digest, object_size_bytes, key_version,
              account_revision, connector_revision, credential_revision,
              safe_summary, created_at, attempt_expires_at, ready_at, expires_at
            ) values (
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s,
              %s, array['PASSWORD'], 'READY', %s,
              %s, 2, 'test-v1',
              %s, %s, %s,
              %s, %s, %s, %s, %s
            )
            """,
            (
                session_id,
                UUID(tenant_id),
                UUID(project_id),
                UUID(environment_id),
                lease_id,
                UUID(account_id),
                UUID(connector_id),
                credential_binding_id,
                fencing_token,
                worker_identity,
                browser_context_ref,
                [origin],
                f"session-vault://tests/{session_id}",
                DIGEST_A,
                account_revision,
                connector_revision,
                credential_revision,
                "Encrypted browser session is ready for the trusted runtime test.",
                now,
                now + timedelta(seconds=30),
                now,
                run.execution_deadline,
            ),
        )
        connection.execute(
            """
            insert into atlas.fixture_run (
              id, tenant_id, project_id, environment_id,
              blueprint_version_id, run_kind, execution_id,
              plan_digest, input_digest, compiled_plan, run_inputs,
              cleanup_policy, status, cleanup_state, temporal_workflow_id,
              requested_by, execution_deadline, requested_at, started_at, ready_at
            ) values (
              %s, %s, %s, %s,
              %s, 'EXECUTION', %s,
              %s, %s, %s, '{}'::jsonb,
              'ALWAYS', 'READY', 'PENDING', %s,
              %s, %s, %s, %s, %s
            )
            """,
            (
                fixture_run_id,
                UUID(tenant_id),
                UUID(project_id),
                UUID(environment_id),
                UUID(blueprint_version_id),
                f"debug-run:{run.id}",
                plan_digest,
                DIGEST_B,
                Jsonb(compiled_plan),
                f"atlas-fixture/{tenant_id}/{fixture_run_id}",
                UUID(headers["X-Atlas-Actor-ID"]),
                run.execution_deadline,
                now,
                now,
                now,
            ),
        )
        connection.execute(
            """
            insert into atlas.fixture_actor_binding (
              fixture_run_id, tenant_id, project_id, environment_id,
              actor_slot, account_lease_id, fencing_token,
              connector_installation_id, bound_at
            ) values (%s, %s, %s, %s, 'operator', %s, %s, %s, %s)
            """,
            (
                fixture_run_id,
                UUID(tenant_id),
                UUID(project_id),
                UUID(environment_id),
                lease_id,
                fencing_token,
                UUID(connector_id),
                now,
            ),
        )
        fixture_manifest: dict[str, JsonValue] = {
            "schemaVersion": "atlas.fixture-manifest/0.1",
            "fixtureRunId": str(fixture_run_id),
            "blueprintVersionId": blueprint_version_id,
            "planDigest": cast(str, plan_digest),
            "exports": {
                "customerId": {
                    "semanticType": "CustomerId",
                    "classification": "INTERNAL",
                    "value": "customer-runtime-001",
                }
            },
        }
        mismatched_manifest = {
            **fixture_manifest,
            "fixtureRunId": str(mismatched_fixture_run_id),
        }
        mismatched_manifest_digest = fixture_digest(mismatched_manifest)
        connection.execute(
            """
            insert into atlas.fixture_run (
              id, tenant_id, project_id, environment_id,
              blueprint_version_id, run_kind, execution_id,
              plan_digest, input_digest, compiled_plan, run_inputs,
              cleanup_policy, status, cleanup_state, temporal_workflow_id,
              requested_by, execution_deadline, requested_at, started_at, ready_at
            )
            select
              %s, tenant_id, project_id, environment_id,
              blueprint_version_id, run_kind, %s,
              plan_digest, input_digest, compiled_plan, run_inputs,
              cleanup_policy, status, cleanup_state, %s,
              requested_by, execution_deadline, requested_at, started_at, ready_at
            from atlas.fixture_run
            where id = %s
            """,
            (
                mismatched_fixture_run_id,
                f"mismatched-debug-run:{run.id}",
                f"atlas-fixture/{tenant_id}/{mismatched_fixture_run_id}",
                fixture_run_id,
            ),
        )
        connection.execute(
            """
            insert into atlas.fixture_manifest (
              fixture_run_id, tenant_id, project_id, environment_id,
              blueprint_version_id, plan_digest, manifest,
              manifest_digest, created_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                mismatched_fixture_run_id,
                UUID(tenant_id),
                UUID(project_id),
                UUID(environment_id),
                UUID(blueprint_version_id),
                plan_digest,
                Jsonb(mismatched_manifest),
                mismatched_manifest_digest,
                now,
            ),
        )
        fixture_manifest_digest = fixture_digest(fixture_manifest)
        connection.execute(
            """
            insert into atlas.fixture_manifest (
              fixture_run_id, tenant_id, project_id, environment_id,
              blueprint_version_id, plan_digest, manifest,
              manifest_digest, created_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                fixture_run_id,
                UUID(tenant_id),
                UUID(project_id),
                UUID(environment_id),
                UUID(blueprint_version_id),
                plan_digest,
                Jsonb(fixture_manifest),
                fixture_manifest_digest,
                now,
            ),
        )

    async def execute_runtime() -> tuple[str, str]:
        database = Database(
            Settings(
                environment="test",
                cors_origins=[],
                database_url=SecretStr(DATABASE_URL),
                database_pool_min_size=1,
                database_pool_max_size=4,
            )
        )
        await database.open()
        try:
            runtime = DebugRuntimeService(database)
            bind_command = BindDebugExecution(
                worker_identity=worker_identity,
                fixture_run_id=fixture_run_id,
                actors=(
                    BindExecutionActor(
                        actor_slot="operator",
                        account_lease_id=lease_id,
                        fencing_token=fencing_token,
                        browser_context_ref=browser_context_ref,
                    ),
                ),
                browser=BrowserExecutionProfile(
                    revision="chromium-140.0.7339.16",
                    viewport=Viewport(width=1440, height=900),
                    locale="zh-CN",
                    timezone="Asia/Shanghai",
                ),
                model=ModelExecutionProfile(
                    model_profile_ref="model.browser-agent@1.0.0",
                    prompt_bundle_ref="prompt.browser-agent@1.0.0",
                    reasoning_policy_ref="reasoning.bounded@1.0.0",
                ),
                tools=ToolExecutionProfile(
                    tool_catalog_ref="tools.browser-safe@1.0.0",
                    mcp_server_manifest_digest=DIGEST_A,
                    tool_schema_digest=DIGEST_B,
                    policy_bundle_ref="policy.browser-test@1.0.0",
                    policy_digest=f"sha256:{'e' * 64}",
                ),
            )
            with pytest.raises(ApplicationError):
                await runtime.mark_ready(
                    UUID(tenant_id),
                    uuid7(),
                    execution_contract_id=uuid7(),
                    execution_contract_digest=DIGEST_A,
                )
            with pytest.raises(ApplicationError):
                await runtime.bind(
                    UUID(tenant_id),
                    run.id,
                    bind_command.model_copy(update={"fixture_run_id": uuid7()}),
                )
            with pytest.raises(ApplicationError):
                await runtime.bind(
                    UUID(tenant_id),
                    run.id,
                    bind_command.model_copy(
                        update={"fixture_run_id": mismatched_fixture_run_id}
                    ),
                )
            with pytest.raises(ApplicationError):
                await runtime.bind(
                    UUID(tenant_id),
                    run.id,
                    bind_command.model_copy(
                        update={
                            "actors": (
                                bind_command.actors[0].model_copy(
                                    update={"actor_slot": "unexpected"}
                                ),
                            )
                        }
                    ),
                )
            with pytest.raises(ApplicationError):
                await runtime.bind(
                    UUID(tenant_id),
                    run.id,
                    bind_command.model_copy(
                        update={
                            "actors": (
                                bind_command.actors[0].model_copy(
                                    update={
                                        "browser_context_ref": "bctx_" + "z" * 40
                                    }
                                ),
                            )
                        }
                    ),
                )
            contract = await runtime.bind(UUID(tenant_id), run.id, bind_command)
            replayed_contract = await runtime.bind(
                UUID(tenant_id),
                run.id,
                bind_command,
            )
            assert replayed_contract == contract
            with pytest.raises(ApplicationError):
                await runtime.bind(
                    UUID(tenant_id),
                    run.id,
                    bind_command.model_copy(
                        update={"worker_identity": "browser-worker-integration-02"}
                    ),
                )
            premature_finalize = FinalizeDebugEvidence(
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
                event_chain_head_digest=DIGEST_A,
                event_count=1,
                finalized_at=datetime.now(UTC),
            )
            with pytest.raises(ApplicationError):
                await runtime.finalize_evidence(
                    UUID(tenant_id),
                    run.id,
                    premature_finalize,
                )
            with pytest.raises(ApplicationError):
                await runtime.mark_ready(
                    UUID(tenant_id),
                    run.id,
                    execution_contract_id=contract.id,
                    execution_contract_digest=DIGEST_A,
                )
            ready = await runtime.mark_ready(
                UUID(tenant_id),
                run.id,
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
            )
            replayed_ready = await runtime.mark_ready(
                UUID(tenant_id),
                run.id,
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
            )
            assert replayed_ready == ready
            running = await runtime.start_execution(
                UUID(tenant_id),
                run.id,
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
            )
            replayed_running = await runtime.start_execution(
                UUID(tenant_id),
                run.id,
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
            )
            assert replayed_running == running
            with pytest.raises(ApplicationError):
                await runtime.mark_ready(
                    UUID(tenant_id),
                    run.id,
                    execution_contract_id=contract.id,
                    execution_contract_digest=contract.content_digest,
                )
            artifact_id = uuid7()
            action_id = uuid7()
            observed_at = datetime.now(UTC)
            assertion = run.test_ir.assertions[0]
            with pytest.raises(ApplicationError):
                await runtime.finalize_evidence(
                    UUID(tenant_id),
                    run.id,
                    premature_finalize.model_copy(
                        update={
                            "finalized_at": run.execution_deadline
                            + timedelta(seconds=1)
                        }
                    ),
                )
            with pytest.raises(ApplicationError):
                await runtime.finalize_evidence(
                    UUID(tenant_id),
                    run.id,
                    FinalizeDebugEvidence(
                        execution_contract_id=contract.id,
                        execution_contract_digest=contract.content_digest,
                        assertion_results=(
                            AssertionResultInput(
                                assertion_id=assertion.assertion_id,
                                status=AssertionStatus.PASSED,
                                expected_digest=expected_assertion_digest(
                                    run.test_ir,
                                    assertion.assertion_id,
                                ),
                                actual_safe_summary="Evidence reference is intentionally absent.",
                                evaluator_version_ref=assertion.evaluator_version_ref,
                                evidence_refs=(uuid7(),),
                                observed_at=observed_at,
                                duration_ms=1,
                            ),
                        ),
                        event_chain_head_digest=DIGEST_A,
                        event_count=1,
                        finalized_at=observed_at + timedelta(milliseconds=1),
                    ),
                )
            finalize_command = FinalizeDebugEvidence(
                execution_contract_id=contract.id,
                execution_contract_digest=contract.content_digest,
                assertion_results=(
                    AssertionResultInput(
                        assertion_id=assertion.assertion_id,
                        status=AssertionStatus.PASSED,
                        expected_digest=expected_assertion_digest(
                            run.test_ir,
                            assertion.assertion_id,
                        ),
                        actual_safe_summary=(
                            "The customer was visible to the frozen operator role."
                        ),
                        evaluator_version_ref=assertion.evaluator_version_ref,
                        evidence_refs=(artifact_id,),
                        observed_at=observed_at,
                        duration_ms=240,
                    ),
                ),
                artifacts=(
                    EvidenceArtifactInput(
                        id=artifact_id,
                        kind=EvidenceArtifactKind.SCREENSHOT,
                        object_ref=(
                            f"evidence://tests/{tenant_id}/{run.id}/{artifact_id}.png"
                        ),
                        content_digest=DIGEST_A,
                        size_bytes=1024,
                        mime_type="image/png",
                        redaction_policy_digest=DIGEST_B,
                        integrity=EvidenceIntegrity.VERIFIED,
                        required=True,
                        captured_at=observed_at,
                    ),
                ),
                event_chain_head_digest=DIGEST_A,
                event_count=7,
                finalized_at=observed_at + timedelta(milliseconds=5),
            )
            report_payloads: tuple[
                tuple[
                    BrowserRuntimeReportKind,
                    dict[str, JsonValue],
                    str | None,
                    UUID | None,
                ],
                ...,
            ] = (
                (
                    BrowserRuntimeReportKind.EXECUTION_STARTED,
                    {
                        "safeSummary": "browser execution started",
                        "planDigest": contract.plan_digest,
                    },
                    None,
                    None,
                ),
                (
                    BrowserRuntimeReportKind.ACTION_PROPOSED,
                    {
                        "safeSummary": "structured browser action proposed",
                        "action": "activate",
                        "risk": "mutation",
                        "nodeId": "filter-agent",
                        "targetRef": "target_" + "t" * 24,
                        "routeKey": None,
                        "proposalDigest": DIGEST_A,
                    },
                    "operator",
                    action_id,
                ),
                (
                    BrowserRuntimeReportKind.POLICY_DECIDED,
                    {
                        "safeSummary": "policy allowed the action",
                        "decision": "ALLOW",
                        "policyDigest": contract.tools.policy_digest,
                        "decisionDigest": DIGEST_B,
                        "matchedRules": ["tool.catalog"],
                    },
                    "operator",
                    action_id,
                ),
                (
                    BrowserRuntimeReportKind.ACTION_EXECUTED,
                    {
                        "safeSummary": "browser action completed",
                        "receiptId": str(uuid7()),
                        "receiptDigest": DIGEST_A,
                        "grantId": str(uuid7()),
                        "action": "activate",
                        "status": "SUCCEEDED",
                        "resultingPageRevision": 2,
                    },
                    "operator",
                    action_id,
                ),
                (
                    BrowserRuntimeReportKind.ASSERTION_EVALUATED,
                    {
                        "safeSummary": "browser assertion evaluated",
                        "assertionId": assertion.assertion_id,
                        "assertionInputDigest": canonical_digest(
                            finalize_command.assertion_results[0]
                        ),
                        "status": "PASSED",
                        "expectedDigest": expected_assertion_digest(
                            run.test_ir,
                            assertion.assertion_id,
                        ),
                    },
                    None,
                    None,
                ),
                (
                    BrowserRuntimeReportKind.ARTIFACT_CAPTURED,
                    {
                        "safeSummary": "verified browser evidence artifact captured",
                        "artifactId": str(artifact_id),
                        "artifactInputDigest": canonical_digest(
                            finalize_command.artifacts[0]
                        ),
                        "kind": "SCREENSHOT",
                        "contentDigest": DIGEST_A,
                        "sizeBytes": 1024,
                        "integrity": "VERIFIED",
                    },
                    None,
                    None,
                ),
                (
                    BrowserRuntimeReportKind.EXECUTION_COMPLETED,
                    {
                        "safeSummary": (
                            "browser execution reached evidence finalization"
                        ),
                        "assertionResultCount": 1,
                        "artifactCount": 1,
                    },
                    None,
                    None,
                ),
            )
            chain_head = CHAIN_START_DIGEST
            persisted_reports = []
            for sequence, (kind, payload, actor_slot, report_action_id) in enumerate(
                report_payloads,
                start=1,
            ):
                report = build_browser_runtime_report(
                    execution_contract_id=contract.id,
                    execution_contract_digest=contract.content_digest,
                    report_id=uuid7(),
                    sequence=sequence,
                    kind=kind,
                    payload=payload,
                    occurred_at=observed_at,
                    previous_chain_digest=chain_head,
                    actor_slot=actor_slot,
                    action_id=report_action_id,
                )
                persisted = await runtime.append_browser_report(
                    UUID(tenant_id),
                    run.id,
                    worker_identity=worker_identity,
                    report=report,
                )
                persisted_reports.append(persisted)
                chain_head = report.chain_digest
                if kind is BrowserRuntimeReportKind.ACTION_PROPOSED:
                    invalid_observation = build_browser_runtime_report(
                        execution_contract_id=contract.id,
                        execution_contract_digest=contract.content_digest,
                        report_id=uuid7(),
                        sequence=sequence + 1,
                        kind=BrowserRuntimeReportKind.OBSERVATION_CAPTURED,
                        payload={
                            "safeSummary": "browser observation captured",
                            "observationRef": "observation_" + "o" * 24,
                            "observationDigest": DIGEST_A,
                            "pageRef": "page_" + "p" * 24,
                            "pageRevision": 1,
                            "routeKey": None,
                            "targetCount": 1,
                        },
                        occurred_at=observed_at,
                        previous_chain_digest=chain_head,
                        actor_slot="operator",
                    )
                    with pytest.raises(ApplicationError):
                        await runtime.append_browser_report(
                            UUID(tenant_id),
                            run.id,
                            worker_identity=worker_identity,
                            report=invalid_observation,
                        )
                    with pytest.raises(psycopg.Error, match="action proposal"):
                        async with database.transaction(
                            DatabaseContext(
                                tenant_id=UUID(tenant_id),
                                request_id=f"runtime-trigger-test:{run.id}",
                            )
                        ) as connection:
                            await BrowserRuntimeReportRepository().append(
                                connection,
                                tenant_id=run.tenant_id,
                                project_id=run.project_id,
                                environment_id=run.environment_id,
                                debug_run_id=run.id,
                                report=invalid_observation,
                                recorded_at=observed_at,
                            )
                if kind is BrowserRuntimeReportKind.POLICY_DECIDED:
                    invalid_receipt = build_browser_runtime_report(
                        execution_contract_id=contract.id,
                        execution_contract_digest=contract.content_digest,
                        report_id=uuid7(),
                        sequence=sequence + 1,
                        kind=BrowserRuntimeReportKind.ACTION_EXECUTED,
                        payload=report_payloads[3][1],
                        occurred_at=observed_at,
                        previous_chain_digest=chain_head,
                        actor_slot="operator",
                        action_id=uuid7(),
                    )
                    with pytest.raises(ApplicationError):
                        await runtime.append_browser_report(
                            UUID(tenant_id),
                            run.id,
                            worker_identity=worker_identity,
                            report=invalid_receipt,
                        )
                if kind is BrowserRuntimeReportKind.ACTION_EXECUTED:
                    duplicate_proposal = build_browser_runtime_report(
                        execution_contract_id=contract.id,
                        execution_contract_digest=contract.content_digest,
                        report_id=uuid7(),
                        sequence=sequence + 1,
                        kind=BrowserRuntimeReportKind.ACTION_PROPOSED,
                        payload=report_payloads[1][1],
                        occurred_at=observed_at,
                        previous_chain_digest=chain_head,
                        actor_slot="operator",
                        action_id=action_id,
                    )
                    with pytest.raises(ApplicationError):
                        await runtime.append_browser_report(
                            UUID(tenant_id),
                            run.id,
                            worker_identity=worker_identity,
                            report=duplicate_proposal,
                        )
            replayed_report = await runtime.append_browser_report(
                UUID(tenant_id),
                run.id,
                worker_identity=worker_identity,
                report=persisted_reports[-1].value,
            )
            assert replayed_report == persisted_reports[-1]
            finalize_command = finalize_command.model_copy(
                update={"event_chain_head_digest": chain_head}
            )
            with pytest.raises(ApplicationError):
                await runtime.finalize_evidence(
                    UUID(tenant_id),
                    run.id,
                    finalize_command.model_copy(
                        update={
                            "assertion_results": (
                                finalize_command.assertion_results[0].model_copy(
                                    update={
                                        "actual_safe_summary": (
                                            "A caller-substituted assertion summary."
                                        )
                                    }
                                ),
                            )
                        }
                    ),
                )
            with pytest.raises(ApplicationError):
                await runtime.finalize_evidence(
                    UUID(tenant_id),
                    run.id,
                    finalize_command.model_copy(
                        update={
                            "artifacts": (
                                finalize_command.artifacts[0].model_copy(
                                    update={
                                        "object_ref": (
                                            "evidence://tests/substituted-object.png"
                                        )
                                    }
                                ),
                            )
                        }
                    ),
                )
            terminated, evidence = await runtime.finalize_evidence(
                UUID(tenant_id),
                run.id,
                finalize_command,
            )
            assert terminated.outcome.value == "PASSED"
            replayed_terminated, replayed_evidence = await runtime.finalize_evidence(
                UUID(tenant_id),
                run.id,
                finalize_command,
            )
            assert replayed_terminated == terminated
            assert replayed_evidence == evidence
            with pytest.raises(ApplicationError):
                await runtime.finalize_evidence(
                    UUID(tenant_id),
                    run.id,
                    finalize_command.model_copy(
                        update={"execution_contract_digest": DIGEST_A}
                    ),
                )
            return str(evidence.id), evidence.content_digest
        finally:
            await database.close()

    return asyncio.run(execute_runtime())


def test_case_authoring_revisions_idempotency_and_isolation() -> None:
    assert DATABASE_URL is not None
    suffix = uuid7().hex[-10:]
    application = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=4,
        )
    )

    with TestClient(application) as client:
        _, project_id, headers = bootstrap_project(client, suffix)
        _, _, other_headers = bootstrap_project(client, f"b{suffix}")
        create_headers = {
            **headers,
            "Idempotency-Key": f"case-create-{suffix}",
        }
        created = client.post(
            f"/v1/projects/{project_id}/test-cases",
            headers=create_headers,
            json=case_payload(suffix),
        )
        assert created.status_code == 201, created.text
        assert created.headers["idempotency-replayed"] == "false"
        case_id = created.json()["id"]

        replayed = client.post(
            f"/v1/projects/{project_id}/test-cases",
            headers=create_headers,
            json=case_payload(suffix),
        )
        assert replayed.status_code == 201
        assert replayed.headers["idempotency-replayed"] == "true"
        assert replayed.json() == created.json()

        catalog = client.get(
            f"/v1/projects/{project_id}/test-cases",
            headers=headers,
        )
        assert catalog.status_code == 200, catalog.text
        assert catalog.json()["items"][0]["id"] == case_id
        assert catalog.json()["items"][0]["graphValid"] is True

        hidden = client.get(f"/v1/test-cases/{case_id}", headers=other_headers)
        assert hidden.status_code == 404

        visible = client.get(f"/v1/test-cases/{case_id}", headers=headers)
        assert visible.status_code == 200, visible.text
        assert visible.json()["id"] == case_id

        initial = client.get(
            f"/v1/test-cases/{case_id}/workflow-draft",
            headers=headers,
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["semanticRevision"] == 1
        assert initial.json()["layoutRevision"] == 1
        assert initial.json()["validation"]["valid"] is True
        semantic_digest = initial.json()["semanticDigest"]

        mutation_id = f"case-semantic-{suffix}"
        semantic_patch = {
            "patchId": str(uuid7()),
            "clientMutationId": mutation_id,
            "baseSemanticRevision": 1,
            "source": "human",
            "operations": [{"op": "REMOVE_EDGE", "edgeId": "assert-to-cleanup"}],
            "rationaleSummary": "Keep the draft editable while the missing link is repaired.",
        }
        preview = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/patches:validate",
            headers=headers,
            json=semantic_patch,
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["applicable"] is True
        assert preview.json()["validation"]["valid"] is False

        unchanged = client.get(
            f"/v1/test-cases/{case_id}/workflow-draft",
            headers=headers,
        )
        assert unchanged.json()["semanticRevision"] == 1

        apply_headers = {
            **headers,
            "If-Match": initial.headers["etag"],
            "Idempotency-Key": mutation_id,
        }
        applied = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/patches:apply",
            headers=apply_headers,
            json=semantic_patch,
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["semanticRevision"] == 2
        assert applied.json()["layoutRevision"] == 1
        assert applied.json()["validation"]["valid"] is False
        assert applied.json()["semanticDigest"] != semantic_digest

        replayed_patch = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/patches:apply",
            headers=apply_headers,
            json=semantic_patch,
        )
        assert replayed_patch.status_code == 200
        assert replayed_patch.headers["idempotency-replayed"] == "true"
        assert replayed_patch.json() == applied.json()

        stale_mutation = f"case-stale-{suffix}"
        stale_patch = {
            **semantic_patch,
            "patchId": str(uuid7()),
            "clientMutationId": stale_mutation,
        }
        stale = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/patches:apply",
            headers={
                **headers,
                "If-Match": '"revision-1"',
                "Idempotency-Key": stale_mutation,
            },
            json=stale_patch,
        )
        assert stale.status_code == 412
        assert stale.json()["errorCode"] == "DRAFT_REVISION_CONFLICT"
        assert stale.headers["etag"] == '"revision-2"'

        layout_mutation = f"case-layout-{suffix}"
        layout = client.patch(
            f"/v1/test-cases/{case_id}/workflow-draft/layout",
            headers={
                **headers,
                "If-Match": initial.headers["x-layout-etag"],
                "Idempotency-Key": layout_mutation,
            },
            json={
                "clientMutationId": layout_mutation,
                "baseLayoutRevision": 1,
                "source": "human",
                "positions": {"prepare-data": {"x": 140, "y": 180}},
            },
        )
        assert layout.status_code == 200, layout.text
        assert layout.json()["semanticRevision"] == 2
        assert layout.json()["layoutRevision"] == 2
        assert layout.json()["semanticDigest"] == applied.json()["semanticDigest"]
        assert layout.json()["layout"]["prepare-data"] == {"x": 140.0, "y": 180.0}

        conflict_mutation = f"case-conflict-{suffix}"
        conflict_patch = {
            "patchId": str(uuid7()),
            "clientMutationId": conflict_mutation,
            "baseSemanticRevision": 2,
            "source": "human",
            "operations": [{"op": "REMOVE_EDGE", "edgeId": "assert-to-cleanup"}],
        }
        structural_conflict = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/patches:apply",
            headers={
                **headers,
                "If-Match": '"revision-2"',
                "Idempotency-Key": conflict_mutation,
            },
            json=conflict_patch,
        )
        assert structural_conflict.status_code == 422
        assert structural_conflict.json()["errorCode"] == "VALIDATION_FAILED"

        unknown_layout_mutation = f"case-unknown-layout-{suffix}"
        unknown_layout = client.patch(
            f"/v1/test-cases/{case_id}/workflow-draft/layout",
            headers={
                **headers,
                "If-Match": '"revision-2"',
                "Idempotency-Key": unknown_layout_mutation,
            },
            json={
                "clientMutationId": unknown_layout_mutation,
                "baseLayoutRevision": 2,
                "source": "human",
                "positions": {"unknown-node": {"x": 1, "y": 1}},
            },
        )
        assert unknown_layout.status_code == 422
        assert unknown_layout.json()["errorCode"] == "VALIDATION_FAILED"

    if OWNER_DATABASE_URL is not None:
        with (
            psycopg.connect(OWNER_DATABASE_URL) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "select operation_scope from atlas.draft_operation "
                "where test_case_id = %s order by created_at",
                (case_id,),
            )
            assert [row[0] for row in cursor.fetchall()] == ["SEMANTIC", "LAYOUT"]
            cursor.execute(
                """
                select
                  has_table_privilege('atlas_app', 'atlas.test_case', 'DELETE'),
                  has_table_privilege('atlas_app', 'atlas.workflow_draft', 'DELETE'),
                  has_table_privilege('atlas_app', 'atlas.draft_operation', 'UPDATE'),
                  has_table_privilege('atlas_app', 'atlas.workflow_node', 'DELETE'),
                  has_table_privilege('atlas_app', 'atlas.workflow_edge', 'DELETE')
                """
            )
            assert cursor.fetchone() == (False, False, False, True, True)


def test_debug_run_freezes_snapshot_replays_events_and_fails_closed() -> None:
    assert DATABASE_URL is not None
    suffix = uuid7().hex[-10:]
    settings = Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=4,
    )
    application = create_app(settings)
    with TestClient(application) as client:
        _, project_id, headers = bootstrap_project(client, suffix)
        _, _, other_headers = bootstrap_project(client, f"x{suffix}")
        environment_id = bootstrap_environment(
            client,
            project_id,
            headers,
            suffix,
        )
        production_environment_id = bootstrap_environment(
            client,
            project_id,
            headers,
            f"p{suffix}",
            kind="PRODUCTION",
        )
        created = client.post(
            f"/v1/projects/{project_id}/test-cases",
            headers={**headers, "Idempotency-Key": f"debug-case-{suffix}"},
            json=case_payload(f"D{suffix}"),
        )
        assert created.status_code == 201, created.text
        case_id = created.json()["id"]
        draft = client.get(
            f"/v1/test-cases/{case_id}/workflow-draft",
            headers=headers,
        )
        assert draft.status_code == 200, draft.text
        start_body = {
            "environmentId": environment_id,
            "baseSemanticRevision": 1,
            "executionDeadline": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        }
        hidden_before_runtime_check = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/debug-runs",
            headers={
                **other_headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": f"debug-hidden-{suffix}",
            },
            json=start_body,
        )
        assert hidden_before_runtime_check.status_code == 404
        unavailable = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/debug-runs",
            headers={
                **headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": f"debug-start-{suffix}",
            },
            json=start_body,
        )
        assert unavailable.status_code == 503, unavailable.text
        assert unavailable.json()["errorCode"] == "DEBUG_RUNTIME_UNAVAILABLE"

        empty_history = client.get(
            f"/v1/test-cases/{case_id}/debug-runs",
            headers=headers,
        )
        assert empty_history.status_code == 200
        assert empty_history.json()["items"] == []

    dispatcher = RecordingDebugRunDispatcher(fail_starts=1, fail_cancels=1)
    configured_application = create_app(settings, debug_run_dispatcher=dispatcher)
    with TestClient(configured_application) as client:
        draft = client.get(
            f"/v1/test-cases/{case_id}/workflow-draft",
            headers=headers,
        )
        production_rejected = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/debug-runs",
            headers={
                **headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": f"debug-production-{suffix}",
            },
            json={**start_body, "environmentId": production_environment_id},
        )
        assert production_rejected.status_code == 403, production_rejected.text
        assert dispatcher.started == []

        start_headers = {
            **headers,
            "If-Match": draft.headers["etag"],
            "Idempotency-Key": f"debug-start-{suffix}",
        }
        dispatch_failed = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/debug-runs",
            headers=start_headers,
            json=start_body,
        )
        assert dispatch_failed.status_code == 503, dispatch_failed.text
        assert dispatch_failed.json()["errorCode"] == "DEBUG_RUNTIME_UNAVAILABLE"
        frozen_history = client.get(
            f"/v1/test-cases/{case_id}/debug-runs",
            headers=headers,
        )
        assert frozen_history.status_code == 200
        assert len(frozen_history.json()["items"]) == 1
        assert frozen_history.json()["items"][0]["outcome"] == "NOT_SET"

        started = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/debug-runs",
            headers=start_headers,
            json=start_body,
        )
        assert started.status_code == 202, started.text
        assert started.headers["idempotency-replayed"] == "true"
        body = started.json()
        run_id = body["id"]
        assert body["semanticRevision"] == 1
        assert body["lifecycle"] == "CREATED"
        assert body["outcome"] == "NOT_SET"
        assert body["snapshotStatus"] == "CURRENT"
        assert body["evidenceManifestId"] is None
        assert body["testIr"]["contentDigest"] == body["testIrDigest"]
        assert body["planTemplate"]["planDigest"] == body["planDigest"]
        assert dispatcher.started[0].id == DebugRun.model_validate(body).id
        assert len(dispatcher.started) == 2

        replayed = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/debug-runs",
            headers=start_headers,
            json=start_body,
        )
        assert replayed.status_code == 202
        assert replayed.headers["idempotency-replayed"] == "true"
        assert replayed.json() == body
        assert len(dispatcher.started) == 3

        history = client.get(
            f"/v1/test-cases/{case_id}/debug-runs",
            headers=headers,
        )
        assert history.status_code == 200, history.text
        assert [item["id"] for item in history.json()["items"]] == [run_id]
        requested_events = client.get(
            f"/v1/debug-runs/{run_id}/events?afterSeq=0",
            headers=headers,
        )
        assert requested_events.status_code == 200, requested_events.text
        assert [item["eventType"] for item in requested_events.json()["items"]] == [
            "debug_run.requested"
        ]

        layout_mutation = f"debug-layout-{suffix}"
        layout = client.patch(
            f"/v1/test-cases/{case_id}/workflow-draft/layout",
            headers={
                **headers,
                "If-Match": draft.headers["x-layout-etag"],
                "Idempotency-Key": layout_mutation,
            },
            json={
                "clientMutationId": layout_mutation,
                "baseLayoutRevision": 1,
                "source": "human",
                "positions": {"prepare-data": {"x": 160, "y": 220}},
            },
        )
        assert layout.status_code == 200, layout.text
        after_layout = client.get(f"/v1/debug-runs/{run_id}", headers=headers)
        assert after_layout.status_code == 200
        assert after_layout.json()["snapshotStatus"] == "CURRENT"
        assert after_layout.headers["etag"] == '"revision-1"'

        graph_nodes = cast(list[dict[str, object]], graph_payload()["nodes"])
        assertion_node = next(
            node for node in graph_nodes if node["id"] == "relationship-assert"
        )
        replacement = {**assertion_node, "params": {"label": "updated-oracle"}}
        semantic_mutation = f"debug-semantic-{suffix}"
        semantic = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/patches:apply",
            headers={
                **headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": semantic_mutation,
            },
            json={
                "patchId": str(uuid7()),
                "clientMutationId": semantic_mutation,
                "baseSemanticRevision": 1,
                "source": "human",
                "operations": [
                    {
                        "op": "REPLACE_NODE",
                        "nodeId": "relationship-assert",
                        "node": replacement,
                    }
                ],
            },
        )
        assert semantic.status_code == 200, semantic.text
        assert semantic.json()["semanticRevision"] == 2

        outdated = client.get(f"/v1/debug-runs/{run_id}", headers=headers)
        assert outdated.status_code == 200, outdated.text
        assert outdated.json()["snapshotStatus"] == "OUTDATED"
        assert outdated.json()["outdatedAt"] is not None
        assert outdated.headers["etag"] == '"revision-2"'
        assert outdated.json()["semanticRevision"] == 1

        cancel_mutation = f"debug-cancel-{suffix}"
        cancel_headers = {
            **headers,
            "If-Match": outdated.headers["etag"],
            "Idempotency-Key": cancel_mutation,
        }
        cancel_failed = client.post(
            f"/v1/debug-runs/{run_id}:cancel",
            headers=cancel_headers,
            json={
                "clientMutationId": cancel_mutation,
                "reason": "Draft semantics changed.",
            },
        )
        assert cancel_failed.status_code == 503, cancel_failed.text
        persisted_cancel = client.get(f"/v1/debug-runs/{run_id}", headers=headers)
        assert persisted_cancel.status_code == 200
        assert persisted_cancel.json()["cancelRequestedAt"] is not None
        assert persisted_cancel.headers["etag"] == '"revision-3"'
        assert dispatcher.canceled[0].id == DebugRun.model_validate(
            persisted_cancel.json()
        ).id

        cancel_replay = client.post(
            f"/v1/debug-runs/{run_id}:cancel",
            headers=cancel_headers,
            json={
                "clientMutationId": cancel_mutation,
                "reason": "Draft semantics changed.",
            },
        )
        assert cancel_replay.status_code == 202
        assert cancel_replay.headers["idempotency-replayed"] == "true"
        assert len(dispatcher.canceled) == 2

        all_events = client.get(
            f"/v1/debug-runs/{run_id}/events?afterSeq=0",
            headers=headers,
        )
        assert all_events.status_code == 200, all_events.text
        assert [item["seq"] for item in all_events.json()["items"]] == [1, 2, 3]
        assert [item["eventType"] for item in all_events.json()["items"]] == [
            "debug_run.requested",
            "debug_run.snapshot_outdated",
            "debug_run.cancel_requested",
        ]

    read_only_application = create_app(settings)
    with TestClient(read_only_application) as client:
        readable = client.get(f"/v1/debug-runs/{run_id}", headers=headers)
        assert readable.status_code == 200, readable.text
        assert readable.json()["id"] == run_id
        hidden_cancel = client.post(
            f"/v1/debug-runs/{run_id}:cancel",
            headers={
                **other_headers,
                "If-Match": '"revision-3"',
                "Idempotency-Key": f"debug-hidden-cancel-{suffix}",
            },
            json={
                "clientMutationId": f"debug-hidden-cancel-{suffix}",
                "reason": "Cross-tenant probe.",
            },
        )
        assert hidden_cancel.status_code == 404

    if OWNER_DATABASE_URL is not None:
        with psycopg.connect(OWNER_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select count(*) from atlas.debug_run where id = %s",
                    (run_id,),
                )
                assert cursor.fetchone() == (1,)
                cursor.execute(
                    """
                    select
                      has_table_privilege('atlas_app', 'atlas.debug_run', 'DELETE'),
                      has_table_privilege(
                        'atlas_app', 'atlas.debug_run_event', 'UPDATE'
                      ),
                      has_table_privilege(
                        'atlas_app', 'atlas.debug_run_event', 'DELETE'
                      )
                    """
                )
                assert cursor.fetchone() == (False, False, False)

            with (
                pytest.raises(psycopg.errors.RaiseException),
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    insert into atlas.debug_run_event (
                      id, tenant_id, project_id, test_case_id, debug_run_id,
                      seq, event_type, lifecycle, outcome, snapshot_status,
                      payload, occurred_at
                    )
                    select
                      %s, tenant_id, project_id, test_case_id, id,
                      4, 'debug_run.illegal_state', 'BINDING', outcome,
                      snapshot_status, '{}'::jsonb, clock_timestamp()
                    from atlas.debug_run
                    where id = %s
                    """,
                    (uuid7(), run_id),
                )

            with (
                pytest.raises(psycopg.errors.RaiseException),
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    insert into atlas.debug_run_event (
                      id, tenant_id, project_id, test_case_id, debug_run_id,
                      seq, event_type, lifecycle, outcome, snapshot_status,
                      payload, occurred_at
                    )
                    select
                      %s, tenant_id, project_id, test_case_id, id,
                      5, 'debug_run.illegal_gap', lifecycle, outcome,
                      snapshot_status, '{}'::jsonb, clock_timestamp()
                    from atlas.debug_run
                    where id = %s
                    """,
                    (uuid7(), run_id),
                )

                with (
                    pytest.raises(psycopg.errors.RaiseException),
                    connection.transaction(),
                    connection.cursor() as cursor,
                ):
                    cursor.execute(
                        """
                        update atlas.debug_run
                        set lifecycle = 'BINDING', started_at = clock_timestamp(),
                            revision = revision + 1
                        where id = %s
                        """,
                        (run_id,),
                    )


def test_case_version_publication_gates_freeze_and_isolation() -> None:
    assert DATABASE_URL is not None
    suffix = uuid7().hex[-10:]
    dispatcher = RecordingDebugRunDispatcher()
    application = create_app(
        Settings(
            environment="test",
            cors_origins=[],
            database_url=SecretStr(DATABASE_URL),
            database_pool_min_size=1,
            database_pool_max_size=6,
        ),
        debug_run_dispatcher=dispatcher,
    )

    with TestClient(application) as client:
        tenant_id, project_id, author_headers = bootstrap_project(client, suffix)
        _, _, other_headers = bootstrap_project(client, f"v{suffix}")
        reviewer_headers = actor_headers(tenant_id)
        environment_id = bootstrap_environment(
            client,
            project_id,
            author_headers,
            suffix,
            allowed_origins=["https://staging.example.test"],
        )
        role = bootstrap_case_role(client, project_id, author_headers, suffix)
        blueprint_version_id, blueprint_version_ref, blueprint_digest = (
            seed_published_case_blueprint(
                tenant_id=tenant_id,
                project_id=project_id,
                environment_id=environment_id,
                published_by=author_headers["X-Atlas-Actor-ID"],
                suffix=suffix,
            )
        )
        created = client.post(
            f"/v1/projects/{project_id}/test-cases",
            headers={
                **author_headers,
                "Idempotency-Key": f"version-case-{suffix}",
            },
            json=case_payload_with_exact_bindings(
                f"V{suffix}",
                role=role,
                blueprint_version_id=blueprint_version_id,
                blueprint_version_ref=blueprint_version_ref,
                blueprint_digest=blueprint_digest,
            ),
        )
        assert created.status_code == 201, created.text
        case_id = cast(str, created.json()["id"])
        draft = client.get(
            f"/v1/test-cases/{case_id}/workflow-draft",
            headers=author_headers,
        )
        assert draft.status_code == 200, draft.text

        started = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/debug-runs",
            headers={
                **author_headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": f"version-debug-{suffix}",
            },
            json={
                "environmentId": environment_id,
                "baseSemanticRevision": 1,
                "executionDeadline": (
                    datetime.now(UTC) + timedelta(minutes=10)
                ).isoformat(),
            },
        )
        assert started.status_code == 202, started.text
        run_id = cast(str, started.json()["id"])

        trial_mutation = f"version-trial-{suffix}"
        trial_required = client.post(
            f"/v1/test-cases/{case_id}:publish",
            headers={
                **reviewer_headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": trial_mutation,
            },
            json={
                "clientMutationId": trial_mutation,
                "version": "1.0.0",
                "baseSemanticRevision": 1,
                "debugRunId": run_id,
                "reviewSummary": "Review completed; waiting for trusted trial evidence.",
            },
        )
        assert trial_required.status_code == 409, trial_required.text
        assert trial_required.json()["errorCode"] == "TRIAL_RUN_REQUIRED"

        evidence_id, evidence_digest = mark_debug_run_passed(
            client=client,
            tenant_id=tenant_id,
            project_id=project_id,
            environment_id=environment_id,
            headers=author_headers,
            role=role,
            blueprint_version_id=blueprint_version_id,
            run=DebugRun.model_validate(started.json()),
            suffix=suffix,
        )
        passed_run = client.get(f"/v1/debug-runs/{run_id}", headers=author_headers)
        assert passed_run.status_code == 200, passed_run.text
        assert passed_run.json()["outcome"] == "PASSED"

        self_review_mutation = f"version-self-review-{suffix}"
        self_review = client.post(
            f"/v1/test-cases/{case_id}:publish",
            headers={
                **author_headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": self_review_mutation,
            },
            json={
                "clientMutationId": self_review_mutation,
                "version": "1.0.0",
                "baseSemanticRevision": 1,
                "debugRunId": run_id,
                "reviewSummary": "Author attempted to approve their own semantics.",
            },
        )
        assert self_review.status_code == 403, self_review.text
        assert self_review.json()["errorCode"] == "FORBIDDEN"

        publish_mutation = f"version-publish-{suffix}"
        publish_body = {
            "clientMutationId": publish_mutation,
            "version": "1.0.0",
            "baseSemanticRevision": 1,
            "debugRunId": run_id,
            "reviewSummary": "Reviewer approved graph, bindings, Oracle, and evidence.",
        }
        publish_headers = {
            **reviewer_headers,
            "If-Match": draft.headers["etag"],
            "Idempotency-Key": publish_mutation,
        }
        published = client.post(
            f"/v1/test-cases/{case_id}:publish",
            headers=publish_headers,
            json=publish_body,
        )
        assert published.status_code == 201, published.text
        assert published.headers["idempotency-replayed"] == "false"
        version = published.json()
        version_id = cast(str, version["id"])
        assert version["schemaVersion"] == "atlas.case-version/0.1"
        assert version["versionRef"] == f"test-case/{case_id}@1.0.0"
        assert version["status"] == "PUBLISHED"
        assert version["semanticRevision"] == 1
        assert version["debugRunId"] == run_id
        assert version["evidenceManifestId"] == evidence_id
        assert version["evidenceManifestDigest"] == evidence_digest
        assert version["authoredBy"] == author_headers["X-Atlas-Actor-ID"]
        assert version["publishedBy"] == reviewer_headers["X-Atlas-Actor-ID"]
        assert version["testIr"]["workflow"] == version["graph"]
        assert version["testIrDigest"] == version["testIr"]["contentDigest"]
        assert version["planDigest"] == version["planTemplate"]["planDigest"]
        assert "layout" not in version

        replayed = client.post(
            f"/v1/test-cases/{case_id}:publish",
            headers=publish_headers,
            json=publish_body,
        )
        assert replayed.status_code == 201, replayed.text
        assert replayed.headers["idempotency-replayed"] == "true"
        assert replayed.json() == version

        detail = client.get(
            f"/v1/case-versions/{version_id}",
            headers=reviewer_headers,
        )
        assert detail.status_code == 200, detail.text
        assert detail.json() == version
        assert detail.headers["etag"] == '"revision-1"'
        history = client.get(
            f"/v1/test-cases/{case_id}/versions",
            headers=reviewer_headers,
        )
        assert history.status_code == 200, history.text
        assert [item["id"] for item in history.json()["items"]] == [version_id]
        hidden = client.get(
            f"/v1/case-versions/{version_id}",
            headers=other_headers,
        )
        assert hidden.status_code == 404

        duplicate_mutation = f"version-duplicate-{suffix}"
        duplicate = client.post(
            f"/v1/test-cases/{case_id}:publish",
            headers={
                **reviewer_headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": duplicate_mutation,
            },
            json={
                **publish_body,
                "clientMutationId": duplicate_mutation,
            },
        )
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["errorCode"] == "CONFLICT"

        graph_nodes = cast(list[dict[str, object]], graph_payload()["nodes"])
        assertion_node = next(
            node for node in graph_nodes if node["id"] == "relationship-assert"
        )
        semantic_mutation = f"version-semantic-{suffix}"
        changed = client.post(
            f"/v1/test-cases/{case_id}/workflow-draft/patches:apply",
            headers={
                **author_headers,
                "If-Match": draft.headers["etag"],
                "Idempotency-Key": semantic_mutation,
            },
            json={
                "patchId": str(uuid7()),
                "clientMutationId": semantic_mutation,
                "baseSemanticRevision": 1,
                "source": "human",
                "operations": [
                    {
                        "op": "REPLACE_NODE",
                        "nodeId": "relationship-assert",
                        "node": {
                            **assertion_node,
                            "params": {"reviewedBehavior": "updated"},
                        },
                    }
                ],
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["semanticRevision"] == 2
        outdated_mutation = f"version-outdated-{suffix}"
        outdated = client.post(
            f"/v1/test-cases/{case_id}:publish",
            headers={
                **reviewer_headers,
                "If-Match": changed.headers["etag"],
                "Idempotency-Key": outdated_mutation,
            },
            json={
                "clientMutationId": outdated_mutation,
                "version": "1.0.1",
                "baseSemanticRevision": 2,
                "debugRunId": run_id,
                "reviewSummary": "Old trial evidence must not authorize changed semantics.",
            },
        )
        assert outdated.status_code == 409, outdated.text
        assert outdated.json()["errorCode"] == "DEBUG_RUN_OUTDATED"

        frozen = client.get(
            f"/v1/case-versions/{version_id}",
            headers=reviewer_headers,
        )
        assert frozen.status_code == 200
        assert frozen.json() == version

    if OWNER_DATABASE_URL is not None:
        with psycopg.connect(OWNER_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select
                      has_table_privilege('atlas_app', 'atlas.case_version', 'DELETE'),
                      has_table_privilege(
                        'atlas_app', 'atlas.case_version_node', 'UPDATE'
                      ),
                      has_table_privilege(
                        'atlas_app', 'atlas.case_version_node', 'DELETE'
                      ),
                      has_table_privilege(
                        'atlas_app', 'atlas.case_version_edge', 'UPDATE'
                      ),
                      has_table_privilege(
                        'atlas_app', 'atlas.case_version_edge', 'DELETE'
                      )
                    """
                )
                assert cursor.fetchone() == (False, False, False, False, False)
                cursor.execute(
                    """
                    select
                      (select count(*) from atlas.case_version_node
                       where case_version_id = %s),
                      (select count(*) from atlas.case_version_edge
                       where case_version_id = %s)
                    """,
                    (UUID(version_id), UUID(version_id)),
                )
                assert cursor.fetchone() == (4, 3)

            with (
                pytest.raises(psycopg.errors.RaiseException),
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    update atlas.case_version
                    set review_summary = 'tampered', revision = revision + 1
                    where id = %s
                    """,
                    (UUID(version_id),),
                )

            with (
                pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    """
                    delete from atlas.case_version_node
                    where case_version_id = %s
                    """,
                    (UUID(version_id),),
                )
