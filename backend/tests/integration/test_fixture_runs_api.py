"""FixtureRun API and worker happy path against real PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from os import environ
from typing import cast
from uuid import UUID, uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from atlas_testops.application.fixture_runs import FixtureWorkerService
from atlas_testops.application.ports.fixture_operations import (
    FixtureOperationContext,
    FixtureOperationError,
    FixtureOperationInvocation,
    FixtureOperationSpec,
)
from atlas_testops.core.config import Settings
from atlas_testops.domain.fixture import (
    FixtureFailureCategory,
    FixtureOperationResult,
    FixtureRun,
)
from atlas_testops.infrastructure.adapters.fixture_registry import FixtureOperationRegistry
from atlas_testops.infrastructure.adapters.mock_fixture import MockFixtureOperationProvider
from atlas_testops.infrastructure.database import Database
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


class RecordingFixtureDispatcher:
    """Accept control-plane commands while the test drives worker phases explicitly."""

    def __init__(self) -> None:
        self.started: list[UUID] = []
        self.released: list[UUID] = []

    async def start(self, run: FixtureRun) -> None:
        self.started.append(run.id)

    async def release(self, run: FixtureRun) -> None:
        self.released.append(run.id)


class FailingFixtureProvider:
    """Exercise explicit, invalid-output, and uncertain provider failures."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def operation_specs(self) -> tuple[FixtureOperationSpec, ...]:
        return MockFixtureOperationProvider().operation_specs()

    async def execute(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureOperationResult:
        if invocation.operation.operation_key != "customer.create":
            return FixtureOperationResult(outputs={})
        if self.mode == "provider-error":
            raise FixtureOperationError(
                category=FixtureFailureCategory.TRANSIENT,
                code="PROVIDER_BUSY",
                safe_detail="The fixture provider is temporarily busy.",
                retryable=False,
            )
        if self.mode == "invalid-output":
            return FixtureOperationResult(
                outputs={},
                provider_request_id="provider-invalid-output",
            )
        raise RuntimeError("synthetic transport disconnect")


def _headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-Atlas-Tenant-ID": tenant_id,
        "X-Atlas-Actor-ID": str(uuid7()),
    }


def _atom_contract() -> dict[str, object]:
    return {
        "schemaVersion": "atlas.atom/0.1",
        "effect": "CREATE",
        "ports": [
            {
                "key": "executionId",
                "direction": "INPUT",
                "semanticType": "atlas.execution-id",
                "jsonSchema": {"type": "string", "minLength": 1},
            },
            {
                "key": "customerRef",
                "direction": "OUTPUT",
                "semanticType": "resource.customer-ref",
                "jsonSchema": {"type": "string", "minLength": 1},
            },
        ],
        "operation": {
            "operationKey": "customer.create",
            "operationVersion": "1.0.0",
            "requiredCapabilities": ["customer.create"],
        },
        "idempotencyPolicy": {"mode": "RECONCILE", "markerInput": "executionId"},
        "postconditions": [
            {"kind": "OUTPUT_SCHEMA", "outputPort": "customerRef"},
            {
                "kind": "RESOURCE_VISIBLE",
                "operation": {
                    "operationKey": "customer.verify",
                    "operationVersion": "1.0.0",
                    "requiredCapabilities": ["customer.verify"],
                },
            },
        ],
        "resourcePolicy": {
            "resourceType": "resource.customer-ref",
            "resourceRefOutput": "customerRef",
            "ttlSeconds": 600,
        },
        "cleanupContract": {
            "operation": {
                "operationKey": "customer.delete",
                "operationVersion": "1.0.0",
                "requiredCapabilities": ["customer.delete"],
            },
            "resourceRefInput": "customerRef",
        },
        "reconcileContract": {
            "operation": {
                "operationKey": "customer.lookup",
                "operationVersion": "1.0.0",
                "requiredCapabilities": ["customer.lookup"],
            },
            "markerInput": "executionId",
            "resourceRefOutput": "customerRef",
        },
    }


def _blueprint_contract(atom_version_id: str) -> dict[str, object]:
    return {
        "schemaVersion": "atlas.fixture-blueprint/0.1",
        "runInputSchema": {"type": "object", "additionalProperties": False},
        "nodes": [
            {
                "id": "createCustomer",
                "atomVersionId": atom_version_id,
                "actorSlot": "primaryUser",
                "bindings": [
                    {
                        "kind": "EXECUTION_CONTEXT",
                        "targetPort": "executionId",
                        "field": "executionId",
                    }
                ],
            }
        ],
        "exports": [
            {
                "name": "customerRef",
                "sourceNodeId": "createCustomer",
                "sourcePort": "customerRef",
                "classification": "INTERNAL",
            }
        ],
        "cleanupPolicy": "ALWAYS",
    }


def _settings() -> Settings:
    assert DATABASE_URL is not None
    return Settings(
        environment="test",
        cors_origins=[],
        database_url=SecretStr(DATABASE_URL),
        database_pool_min_size=1,
        database_pool_max_size=8,
        fixture_activity_timeout_seconds=30,
        fixture_cleanup_grace_seconds=60,
    )


def _seed_fixture_scope(
    client: TestClient,
    prefix: str,
) -> tuple[str, str, str, dict[str, str], str, str, int]:
    tenant_response = client.post(
        "/v1/tenants",
        json={"slug": f"run-{prefix}", "name": f"Run {prefix}"},
    )
    assert tenant_response.status_code == 201, tenant_response.text
    tenant_id = cast(str, tenant_response.json()["id"])
    headers = _headers(tenant_id)
    project_response = client.post(
        "/v1/projects",
        headers={**headers, "Idempotency-Key": f"run-project-{prefix}"},
        json={"projectKey": f"RUN_{prefix.upper()}", "name": "Run Project"},
    )
    assert project_response.status_code == 201, project_response.text
    project_id = cast(str, project_response.json()["id"])
    environment_response = client.post(
        f"/v1/projects/{project_id}/environments",
        headers={**headers, "Idempotency-Key": f"run-environment-{prefix}"},
        json={
            "environmentKey": "fixture-test",
            "name": "Fixture Test",
            "kind": "TEST",
            "allowedOrigins": ["https://staging.example.test"],
        },
    )
    assert environment_response.status_code == 201, environment_response.text
    environment_id = cast(str, environment_response.json()["id"])
    connector_response = client.post(
        "/v1/connector-installations",
        headers={**headers, "Idempotency-Key": f"run-connector-{prefix}"},
        json={
            "environmentId": environment_id,
            "installationKey": "password",
            "name": "Password Connector",
            "adapterKey": "generic-password",
            "mode": "MANAGED_TEST_ACCOUNTS",
            "configurationRef": f"cfg_fixture_{prefix}",
            "allowedOrigins": ["https://staging.example.test"],
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
    role_response = client.post(
        f"/v1/projects/{project_id}/test-roles",
        headers={**headers, "Idempotency-Key": f"run-role-{prefix}"},
        json={
            "roleKey": "fixture-user",
            "name": "Fixture User",
            "description": "Fixture execution actor",
            "capabilities": ["customer.read"],
        },
    )
    assert role_response.status_code == 201, role_response.text
    pool_response = client.post(
        f"/v1/environments/{environment_id}/account-pools",
        headers={**headers, "Idempotency-Key": f"run-pool-{prefix}"},
        json={
            "roleId": role_response.json()["id"],
            "poolKey": "fixture-users",
            "name": "Fixture Users",
            "defaultTtlSeconds": 300,
            "cooldownSeconds": 0,
        },
    )
    assert pool_response.status_code == 201, pool_response.text
    account_response = client.post(
        f"/v1/account-pools/{pool_response.json()['id']}/accounts",
        headers={**headers, "Idempotency-Key": f"run-account-{prefix}"},
        json={
            "connectorInstallationId": connector_id,
            "accountKey": "fixture-user-01",
            "source": "ATLAS_MANAGED",
            "loginHintMasked": "fi***@example.test",
            "labels": {"purpose": "fixture"},
            "credentials": [
                {
                    "authMethod": "PASSWORD",
                    "purpose": "LOGIN",
                    "secretRef": f"sec_fixture_{prefix}",
                    "secretVersion": "v1",
                }
            ],
        },
    )
    assert account_response.status_code == 201, account_response.text
    account_id = cast(str, account_response.json()["id"])
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("select set_config('atlas.tenant_id', %s, true)", (tenant_id,))
        connection.execute(
            """
            update atlas.test_account
            set lifecycle_status = 'ACTIVE', health_status = 'HEALTHY',
                operational_status = 'READY',
                identity_fingerprint = 'sha256:' || repeat('f', 64),
                last_health_checked_at = statement_timestamp(),
                last_health_succeeded_at = statement_timestamp(),
                revision = revision + 1
            where id = %s
            """,
            (UUID(account_id),),
        )
    execution_id = f"fixture-execution-{prefix}"
    lease_deadline = datetime.now(UTC) + timedelta(minutes=4)
    lease_response = client.post(
        "/internal/v1/account-leases",
        headers={**headers, "Idempotency-Key": f"run-lease-{prefix}"},
        json={
            "executionId": execution_id,
            "workerId": "worker-fixture-01",
            "environmentId": environment_id,
            "roleKey": "fixture-user",
            "requirements": {"capabilities": ["customer.read"]},
            "ttlSeconds": 300,
            "executionDeadline": lease_deadline.isoformat(),
        },
    )
    assert lease_response.status_code == 201, lease_response.text
    return (
        tenant_id,
        project_id,
        environment_id,
        headers,
        execution_id,
        cast(str, lease_response.json()["leaseId"]),
        cast(int, lease_response.json()["fencingToken"]),
    )


def _create_assets(
    client: TestClient,
    *,
    project_id: str,
    headers: dict[str, str],
    prefix: str,
) -> tuple[str, str, str]:
    atom = client.post(
        f"/v1/projects/{project_id}/data-atoms",
        headers={**headers, "Idempotency-Key": f"run-atom-{prefix}"},
        json={
            "atomKey": "customer.create",
            "businessDomain": "customer",
            "name": "Create Customer",
            "description": "Create a deterministic customer fixture.",
        },
    )
    assert atom.status_code == 201, atom.text
    atom_version = client.post(
        f"/v1/data-atoms/{atom.json()['id']}/versions",
        headers={**headers, "Idempotency-Key": f"run-atom-version-{prefix}"},
        json={"version": "1.0.0", "contract": _atom_contract()},
    )
    assert atom_version.status_code == 201, atom_version.text
    atom_version_id = cast(str, atom_version.json()["id"])
    validated = client.post(
        f"/v1/data-atom-versions/{atom_version_id}:validate",
        headers={**headers, "If-Match": atom_version.headers["etag"]},
    )
    assert validated.status_code == 200, validated.text
    blueprint = client.post(
        f"/v1/projects/{project_id}/data-blueprints",
        headers={**headers, "Idempotency-Key": f"run-blueprint-{prefix}"},
        json={
            "blueprintKey": "customer.ready",
            "name": "Customer Ready",
            "description": "Prepare one customer.",
        },
    )
    assert blueprint.status_code == 201, blueprint.text
    blueprint_version = client.post(
        f"/v1/data-blueprints/{blueprint.json()['id']}/versions",
        headers={**headers, "Idempotency-Key": f"run-blueprint-version-{prefix}"},
        json={
            "version": "1.0.0",
            "contract": _blueprint_contract(atom_version_id),
        },
    )
    assert blueprint_version.status_code == 201, blueprint_version.text
    blueprint_version_id = cast(str, blueprint_version.json()["id"])
    compiled = client.post(
        f"/v1/data-blueprint-versions/{blueprint_version_id}:compile",
        headers={**headers, "If-Match": blueprint_version.headers["etag"]},
    )
    assert compiled.status_code == 200, compiled.text
    return atom_version_id, cast(str, blueprint.json()["id"]), blueprint_version_id


async def _prepare_run(settings: Settings, tenant_id: str, run_id: str) -> None:
    database = Database(settings)
    await database.open()
    try:
        worker = FixtureWorkerService(
            database,
            FixtureOperationRegistry.from_settings(settings),
            cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
        )
        plan = await worker.load_plan(UUID(tenant_id), UUID(run_id))
        for level in plan.execution_levels:
            results = await asyncio.gather(
                *(worker.execute_node(UUID(tenant_id), UUID(run_id), node_id) for node_id in level)
            )
            assert all(item.status.value == "SUCCEEDED" for item in results)
        ready = await worker.finalize_ready(UUID(tenant_id), UUID(run_id))
        assert ready.status.value == "READY"
    finally:
        await database.close()


async def _release_run(settings: Settings, tenant_id: str, run_id: str) -> None:
    database = Database(settings)
    await database.open()
    try:
        worker = FixtureWorkerService(
            database,
            FixtureOperationRegistry.from_settings(settings),
            cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
        )
        plan = await worker.load_plan(UUID(tenant_id), UUID(run_id))
        cleaning = await worker.begin_release(UUID(tenant_id), UUID(run_id))
        assert cleaning.status.value == "CLEANING"
        for node_id in plan.cleanup_order:
            await worker.cleanup_node(UUID(tenant_id), UUID(run_id), node_id)
        released = await worker.finalize_release(
            UUID(tenant_id),
            UUID(run_id),
            failed_run=False,
        )
        assert released.status.value == "RELEASED"
        assert released.cleaned_resources == 1
        assert released.leaked_resources == 0
    finally:
        await database.close()


async def _fail_run(
    settings: Settings,
    registry: FixtureOperationRegistry,
    tenant_id: str,
    run_id: str,
) -> str:
    database = Database(settings)
    await database.open()
    try:
        worker = FixtureWorkerService(
            database,
            registry,
            cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
        )
        plan = await worker.load_plan(UUID(tenant_id), UUID(run_id))
        result = await worker.execute_node(
            UUID(tenant_id),
            UUID(run_id),
            plan.execution_levels[0][0],
        )
        assert result.failure_code is not None
        await worker.begin_failed_cleanup(
            UUID(tenant_id),
            UUID(run_id),
            category=result.failure_category or FixtureFailureCategory.INFRASTRUCTURE,
            code=result.failure_code,
        )
        for node_id in plan.cleanup_order:
            await worker.cleanup_node(UUID(tenant_id), UUID(run_id), node_id)
        failed = await worker.finalize_release(
            UUID(tenant_id),
            UUID(run_id),
            failed_run=True,
        )
        assert failed.status.value == "FAILED"
        return result.status.value
    finally:
        await database.close()


def test_fixture_run_prepares_manifest_records_evidence_and_releases() -> None:
    settings = _settings()
    dispatcher = RecordingFixtureDispatcher()
    registry = FixtureOperationRegistry.from_settings(settings)
    app = create_app(
        settings,
        fixture_operation_registry=registry,
        fixture_run_dispatcher=dispatcher,
    )
    prefix = uuid7().hex[-10:]
    with TestClient(app) as client:
        (
            tenant_id,
            project_id,
            environment_id,
            headers,
            execution_id,
            lease_id,
            fencing_token,
        ) = _seed_fixture_scope(client, prefix)
        atom_version_id, blueprint_id, blueprint_version_id = _create_assets(
            client,
            project_id=project_id,
            headers=headers,
            prefix=prefix,
        )
        deadline = datetime.now(UTC) + timedelta(minutes=2)
        command = {
            "runKind": "VALIDATION",
            "blueprintVersionId": blueprint_version_id,
            "environmentId": environment_id,
            "executionId": execution_id,
            "inputs": {},
            "actorBindings": [
                {
                    "actorSlot": "primaryUser",
                    "accountLeaseId": lease_id,
                    "fencingToken": fencing_token,
                }
            ],
            "executionDeadline": deadline.isoformat(),
        }
        start_headers = {**headers, "Idempotency-Key": f"fixture-run-{prefix}"}
        unpublished_execution = client.post(
            f"/v1/projects/{project_id}/fixture-runs",
            headers={**headers, "Idempotency-Key": f"fixture-execution-{prefix}"},
            json={**command, "runKind": "EXECUTION"},
        )
        assert unpublished_execution.status_code == 409
        assert unpublished_execution.json()["errorCode"] == "CONFLICT"

        started = client.post(
            f"/v1/projects/{project_id}/fixture-runs",
            headers=start_headers,
            json=command,
        )
        assert started.status_code == 202, started.text
        assert started.headers["idempotency-replayed"] == "false"
        run_id = cast(str, started.json()["id"])
        assert started.json()["status"] == "REQUESTED"
        assert dispatcher.started == [UUID(run_id)]
        replayed = client.post(
            f"/v1/projects/{project_id}/fixture-runs",
            headers=start_headers,
            json=command,
        )
        assert replayed.status_code == 202, replayed.text
        assert replayed.headers["idempotency-replayed"] == "true"
        assert replayed.json() == started.json()
        assert dispatcher.started == [UUID(run_id), UUID(run_id)]

        second_blueprint_version = client.post(
            f"/v1/data-blueprints/{blueprint_id}/versions",
            headers={**headers, "Idempotency-Key": f"run-blueprint-version-2-{prefix}"},
            json={
                "version": "1.0.1",
                "contract": _blueprint_contract(atom_version_id),
            },
        )
        assert second_blueprint_version.status_code == 201
        second_blueprint_version_id = cast(str, second_blueprint_version.json()["id"])
        second_compiled = client.post(
            f"/v1/data-blueprint-versions/{second_blueprint_version_id}:compile",
            headers={**headers, "If-Match": second_blueprint_version.headers["etag"]},
        )
        assert second_compiled.status_code == 200
        lease_reuse = client.post(
            f"/v1/projects/{project_id}/fixture-runs",
            headers={**headers, "Idempotency-Key": f"fixture-run-lease-reuse-{prefix}"},
            json={**command, "blueprintVersionId": second_blueprint_version_id},
        )
        assert lease_reuse.status_code == 409
        assert lease_reuse.json()["errorCode"] == "CONFLICT"

        requested = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert requested.status_code == 200
        assert requested.json()["nodes"][0]["status"] == "PENDING"

    asyncio.run(_prepare_run(settings, tenant_id, run_id))

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["run"]["status"] == "READY"
        assert detail.json()["nodes"][0]["status"] == "SUCCEEDED"
        assert detail.json()["attempts"][0]["status"] == "SUCCEEDED"
        manifest = client.get(f"/v1/fixture-runs/{run_id}/manifest", headers=headers)
        assert manifest.status_code == 200, manifest.text
        assert manifest.json()["manifest"]["exports"]["customerRef"].startswith("mock-")
        resources = client.get(f"/v1/fixture-runs/{run_id}/resources", headers=headers)
        assert resources.status_code == 200, resources.text
        assert resources.json()["items"][0]["status"] == "ACTIVE"
        assert "opaqueRef" not in resources.text
        atom_version = client.get(
            f"/v1/data-atom-versions/{atom_version_id}",
            headers=headers,
        )
        assert atom_version.json()["runtimeValidationState"] == "PASSED"
        assert atom_version.json()["cleanupValidationState"] == "PENDING"
        blocked_publish = client.post(
            f"/v1/data-atom-versions/{atom_version_id}:publish",
            headers={**headers, "If-Match": atom_version.headers["etag"]},
        )
        assert blocked_publish.status_code == 409
        release = client.post(f"/v1/fixture-runs/{run_id}:release", headers=headers)
        assert release.status_code == 202, release.text
        assert dispatcher.released == [UUID(run_id)]

    asyncio.run(_release_run(settings, tenant_id, run_id))

    with TestClient(app) as client:
        released = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert released.status_code == 200, released.text
        assert released.json()["run"]["status"] == "RELEASED"
        assert released.json()["run"]["cleanupState"] == "CLEANED"
        resources = client.get(f"/v1/fixture-runs/{run_id}/resources", headers=headers)
        assert resources.json()["items"][0]["status"] == "CLEANED"
        lease = client.get(f"/internal/v1/account-leases/{lease_id}", headers=headers)
        assert lease.status_code == 200
        assert lease.json()["status"] == "RELEASED"

    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute("select set_config('atlas.tenant_id', %s, true)", (tenant_id,))
        counts = connection.execute(
            """
            select
              (select count(*) from atlas.fixture_validation_evidence
               where fixture_run_id = %s and kind = 'RUNTIME'),
              (select count(*) from atlas.resource_record where fixture_run_id = %s),
              (select count(*) from atlas.fixture_manifest where fixture_run_id = %s)
            """,
            (UUID(run_id), UUID(run_id), UUID(run_id)),
        ).fetchone()
        assert counts == (2, 1, 1)
        with pytest.raises(psycopg.Error), connection.transaction():
            connection.execute(
                "update atlas.fixture_manifest set manifest = '{}'::jsonb "
                "where fixture_run_id = %s",
                (UUID(run_id),),
            )


@pytest.mark.parametrize(
    ("mode", "expected_node_status", "expected_failure_category"),
    [
        ("provider-error", "FAILED", "TRANSIENT"),
        ("invalid-output", "OUTCOME_UNCERTAIN", "VALIDATION"),
        ("transport-error", "OUTCOME_UNCERTAIN", "UNCERTAIN"),
    ],
)
def test_fixture_run_provider_failures_stop_and_release(
    mode: str,
    expected_node_status: str,
    expected_failure_category: str,
) -> None:
    settings = _settings()
    registry = FixtureOperationRegistry()
    registry.register("generic-password", FailingFixtureProvider(mode))
    dispatcher = RecordingFixtureDispatcher()
    app = create_app(
        settings,
        fixture_operation_registry=registry,
        fixture_run_dispatcher=dispatcher,
    )
    prefix = uuid7().hex[-10:]
    with TestClient(app) as client:
        (
            tenant_id,
            project_id,
            environment_id,
            headers,
            execution_id,
            lease_id,
            fencing_token,
        ) = _seed_fixture_scope(client, prefix)
        _, _, blueprint_version_id = _create_assets(
            client,
            project_id=project_id,
            headers=headers,
            prefix=prefix,
        )
        started = client.post(
            f"/v1/projects/{project_id}/fixture-runs",
            headers={**headers, "Idempotency-Key": f"fixture-failure-{prefix}"},
            json={
                "runKind": "VALIDATION",
                "blueprintVersionId": blueprint_version_id,
                "environmentId": environment_id,
                "executionId": execution_id,
                "inputs": {},
                "actorBindings": [
                    {
                        "actorSlot": "primaryUser",
                        "accountLeaseId": lease_id,
                        "fencingToken": fencing_token,
                    }
                ],
                "executionDeadline": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
            },
        )
        assert started.status_code == 202, started.text
        run_id = cast(str, started.json()["id"])

    node_status = asyncio.run(_fail_run(settings, registry, tenant_id, run_id))
    assert node_status == expected_node_status

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["run"]["status"] == "FAILED"
        assert detail.json()["run"]["cleanupState"] == "CLEANED"
        assert detail.json()["run"]["failureCategory"] == expected_failure_category
        assert detail.json()["nodes"][0]["status"] == expected_node_status
        assert detail.json()["attempts"][0]["status"] in {
            "FAILED",
            "OUTCOME_UNCERTAIN",
        }
        if mode == "invalid-output":
            assert detail.json()["attempts"][0]["providerRequestId"] == "provider-invalid-output"
        resources = client.get(f"/v1/fixture-runs/{run_id}/resources", headers=headers)
        assert resources.json()["items"] == []
        lease = client.get(f"/internal/v1/account-leases/{lease_id}", headers=headers)
        assert lease.json()["status"] == "RELEASED"
