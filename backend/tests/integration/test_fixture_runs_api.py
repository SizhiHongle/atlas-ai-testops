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
from atlas_testops.core.contracts import new_entity_id, utc_now
from atlas_testops.domain.fixture import (
    FixtureCleanupSweepBatch,
    FixtureFailureCategory,
    FixtureOperationResult,
    FixtureReconcileDisposition,
    FixtureReconcileResult,
    FixtureRun,
)
from atlas_testops.infrastructure.adapters.fixture_registry import FixtureOperationRegistry
from atlas_testops.infrastructure.adapters.mock_fixture import MockFixtureOperationProvider
from atlas_testops.infrastructure.database import Database, DatabaseContext
from atlas_testops.infrastructure.repositories.fixture_runs import FixtureRunRepository
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
        self.canceled: list[UUID] = []
        self.cleanup_retried: list[UUID] = []

    async def start(self, run: FixtureRun) -> None:
        self.started.append(run.id)

    async def release(self, run: FixtureRun) -> None:
        self.released.append(run.id)

    async def cancel(self, run: FixtureRun) -> None:
        self.canceled.append(run.id)

    async def retry_cleanup(self, run: FixtureRun) -> None:
        self.cleanup_retried.append(run.id)

    async def sweep(
        self,
        *,
        tenant_id: UUID,
        worker_identity: str,
        limit: int,
    ) -> FixtureCleanupSweepBatch:
        del tenant_id, worker_identity, limit
        return FixtureCleanupSweepBatch(
            reconciled_found=0,
            reconciled_absent=0,
            reconciled_inconclusive=0,
            cleanup_claimed=0,
            cleaned_resources=0,
            retry_scheduled=0,
            leaked_resources=0,
            finalized_runs=0,
            observed_at=datetime.now(UTC),
        )


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

    async def reconcile(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureReconcileResult:
        del context, invocation
        return FixtureReconcileResult(
            disposition=FixtureReconcileDisposition.INCONCLUSIVE,
            provider_request_id="provider-reconcile-inconclusive",
        )


class UncertainCreateProvider:
    """Expose an uncertain create that Reconcile can deterministically recover."""

    def operation_specs(self) -> tuple[FixtureOperationSpec, ...]:
        return MockFixtureOperationProvider().operation_specs()

    async def execute(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureOperationResult:
        if invocation.operation.operation_key == "customer.create":
            raise FixtureOperationError(
                category=FixtureFailureCategory.UNCERTAIN,
                code="CREATE_RESPONSE_LOST",
                safe_detail="The create response was lost after the provider accepted it.",
                retryable=False,
                outcome_uncertain=True,
                provider_request_id="provider-create-lost",
            )
        return await MockFixtureOperationProvider().execute(
            context=context,
            invocation=invocation,
        )

    async def reconcile(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureReconcileResult:
        return await MockFixtureOperationProvider().reconcile(
            context=context,
            invocation=invocation,
        )


class ReconciledPostconditionFailureProvider:
    """Recover an uncertain create, then reject its reviewed postcondition."""

    def operation_specs(self) -> tuple[FixtureOperationSpec, ...]:
        return MockFixtureOperationProvider().operation_specs()

    async def execute(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureOperationResult:
        if invocation.operation.operation_key == "customer.create":
            raise FixtureOperationError(
                category=FixtureFailureCategory.UNCERTAIN,
                code="CREATE_RESPONSE_LOST",
                safe_detail="The create response was lost after the provider accepted it.",
                retryable=False,
                outcome_uncertain=True,
            )
        if invocation.operation.operation_key == "customer.verify":
            raise FixtureOperationError(
                category=FixtureFailureCategory.VALIDATION,
                code="RECONCILED_RESOURCE_NOT_VISIBLE",
                safe_detail="The reconciled resource did not pass its reviewed postcondition.",
                retryable=False,
            )
        return await MockFixtureOperationProvider().execute(
            context=context,
            invocation=invocation,
        )

    async def reconcile(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureReconcileResult:
        return await MockFixtureOperationProvider().reconcile(
            context=context,
            invocation=invocation,
        )


class ReconcileDeniedProvider:
    """Return a reviewed provider error when an uncertain create is reconciled."""

    def operation_specs(self) -> tuple[FixtureOperationSpec, ...]:
        return MockFixtureOperationProvider().operation_specs()

    async def execute(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureOperationResult:
        if invocation.operation.operation_key == "customer.create":
            raise FixtureOperationError(
                category=FixtureFailureCategory.UNCERTAIN,
                code="CREATE_RESPONSE_LOST",
                safe_detail="The create response was lost after the provider accepted it.",
                retryable=False,
                outcome_uncertain=True,
            )
        return await MockFixtureOperationProvider().execute(
            context=context,
            invocation=invocation,
        )

    async def reconcile(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureReconcileResult:
        del context, invocation
        raise FixtureOperationError(
            category=FixtureFailureCategory.AUTH,
            code="RECONCILE_ACCESS_DENIED",
            safe_detail="The provider rejected the reviewed reconcile query.",
            retryable=False,
            provider_request_id="provider-reconcile-denied",
        )


class FaultyReconcileProvider:
    """Return an invalid reconcile payload or lose the reconcile response."""

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
        if invocation.operation.operation_key == "customer.create":
            raise FixtureOperationError(
                category=FixtureFailureCategory.UNCERTAIN,
                code="CREATE_RESPONSE_LOST",
                safe_detail="The create response was lost after the provider accepted it.",
                retryable=False,
                outcome_uncertain=True,
            )
        return await MockFixtureOperationProvider().execute(
            context=context,
            invocation=invocation,
        )

    async def reconcile(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureReconcileResult:
        del context, invocation
        if self.mode == "invalid-output":
            return FixtureReconcileResult(
                disposition=FixtureReconcileDisposition.FOUND,
                outputs={"customerRef": ""},
                provider_request_id="provider-reconcile-invalid",
            )
        raise RuntimeError("synthetic reconcile disconnect")


class AbsentThenCreatedProvider:
    """Prove the first uncertain create absent, then succeed on the safe retry."""

    def __init__(self) -> None:
        self.create_calls = 0

    def operation_specs(self) -> tuple[FixtureOperationSpec, ...]:
        return MockFixtureOperationProvider().operation_specs()

    async def execute(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureOperationResult:
        if invocation.operation.operation_key == "customer.create":
            self.create_calls += 1
            if self.create_calls == 1:
                raise FixtureOperationError(
                    category=FixtureFailureCategory.UNCERTAIN,
                    code="CREATE_RESPONSE_LOST",
                    safe_detail="The first create response was lost.",
                    retryable=False,
                    outcome_uncertain=True,
                )
        return await MockFixtureOperationProvider().execute(
            context=context,
            invocation=invocation,
        )

    async def reconcile(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureReconcileResult:
        del context, invocation
        return FixtureReconcileResult(
            disposition=FixtureReconcileDisposition.ABSENT,
            provider_request_id="provider-reconcile-absent",
        )


class TransientCleanupProvider:
    """Fail the first cleanup call and then complete its idempotent retry."""

    def __init__(self) -> None:
        self.cleanup_calls = 0

    def operation_specs(self) -> tuple[FixtureOperationSpec, ...]:
        return MockFixtureOperationProvider().operation_specs()

    async def execute(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureOperationResult:
        if invocation.operation.operation_key == "customer.delete":
            self.cleanup_calls += 1
            if self.cleanup_calls == 1:
                raise FixtureOperationError(
                    category=FixtureFailureCategory.TRANSIENT,
                    code="CLEANUP_PROVIDER_BUSY",
                    safe_detail="The cleanup provider is temporarily busy.",
                    retryable=True,
                    retry_after_seconds=0.001,
                    provider_request_id="provider-cleanup-busy",
                )
        return await MockFixtureOperationProvider().execute(
            context=context,
            invocation=invocation,
        )

    async def reconcile(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureReconcileResult:
        return await MockFixtureOperationProvider().reconcile(
            context=context,
            invocation=invocation,
        )


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


def _request_validation_run(
    client: TestClient,
    *,
    prefix: str,
) -> tuple[str, str, str, dict[str, str], str, str, int, str, str, str]:
    (
        tenant_id,
        project_id,
        environment_id,
        headers,
        execution_id,
        lease_id,
        fencing_token,
    ) = _seed_fixture_scope(client, prefix)
    atom_version_id, _, blueprint_version_id = _create_assets(
        client,
        project_id=project_id,
        headers=headers,
        prefix=prefix,
    )
    started = client.post(
        f"/v1/projects/{project_id}/fixture-runs",
        headers={**headers, "Idempotency-Key": f"fixture-recovery-{prefix}"},
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
    return (
        tenant_id,
        project_id,
        environment_id,
        headers,
        execution_id,
        lease_id,
        fencing_token,
        atom_version_id,
        blueprint_version_id,
        cast(str, started.json()["id"]),
    )


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


async def _prepare_run_with_registry(
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
        initial_status = ""
        for level in plan.execution_levels:
            for node_id in level:
                result = await worker.execute_node(UUID(tenant_id), UUID(run_id), node_id)
                initial_status = result.status.value
                for _ in range(4):
                    if result.status.value == "OUTCOME_UNCERTAIN":
                        result = await worker.reconcile_node(
                            UUID(tenant_id),
                            UUID(run_id),
                            node_id,
                        )
                    elif result.status.value == "READY":
                        result = await worker.execute_node(
                            UUID(tenant_id),
                            UUID(run_id),
                            node_id,
                        )
                    else:
                        break
                assert result.status.value == "SUCCEEDED"
        ready = await worker.finalize_ready(UUID(tenant_id), UUID(run_id))
        assert ready.status.value == "READY"
        return initial_status
    finally:
        await database.close()


async def _finish_canceled_run(
    settings: Settings,
    registry: FixtureOperationRegistry,
    tenant_id: str,
    run_id: str,
) -> None:
    database = Database(settings)
    await database.open()
    try:
        worker = FixtureWorkerService(
            database,
            registry,
            cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
        )
        plan = await worker.load_plan(UUID(tenant_id), UUID(run_id))
        canceled = await worker.begin_canceled_cleanup(UUID(tenant_id), UUID(run_id))
        assert canceled.status.value == "CLEANING"
        for node_id in plan.cleanup_order:
            await worker.cleanup_node(UUID(tenant_id), UUID(run_id), node_id)
        final = await worker.finalize_release(
            UUID(tenant_id),
            UUID(run_id),
            failed_run=False,
        )
        assert final.status.value == "CANCELED"
    finally:
        await database.close()


async def _fail_first_cleanup(
    settings: Settings,
    registry: FixtureOperationRegistry,
    tenant_id: str,
    run_id: str,
) -> None:
    database = Database(settings)
    await database.open()
    try:
        worker = FixtureWorkerService(
            database,
            registry,
            cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
            retry_initial=timedelta(milliseconds=1),
        )
        plan = await worker.load_plan(UUID(tenant_id), UUID(run_id))
        await worker.begin_release(UUID(tenant_id), UUID(run_id))
        for node_id in plan.cleanup_order:
            result = await worker.cleanup_node(UUID(tenant_id), UUID(run_id), node_id)
            assert result.cleaned_resources == 0
        pending = await worker.finalize_release(
            UUID(tenant_id),
            UUID(run_id),
            failed_run=False,
        )
        assert pending.status.value == "CLEANING"
        assert pending.cleanup_state.value == "PENDING"
    finally:
        await database.close()


async def _sweep_run(
    settings: Settings,
    registry: FixtureOperationRegistry,
    tenant_id: str,
) -> None:
    database = Database(settings)
    await database.open()
    try:
        worker = FixtureWorkerService(
            database,
            registry,
            cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
            retry_initial=timedelta(milliseconds=1),
        )
        result = await worker.sweep_cleanup(
            UUID(tenant_id),
            worker_identity="fixture-sweeper-test",
            limit=20,
        )
        assert result.cleaned_resources == 1
        assert result.finalized_runs >= 1
    finally:
        await database.close()


async def _recover_stale_cleanup_claim(
    settings: Settings,
    registry: FixtureOperationRegistry,
    tenant_id: str,
    run_id: str,
) -> None:
    database = Database(settings)
    await database.open()
    try:
        repository = FixtureRunRepository()
        worker = FixtureWorkerService(
            database,
            registry,
            cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
            recovery_claim_ttl=timedelta(seconds=30),
            retry_initial=timedelta(milliseconds=1),
        )
        plan = await worker.load_plan(UUID(tenant_id), UUID(run_id))
        await worker.begin_release(UUID(tenant_id), UUID(run_id))
        claimed_at = utc_now()
        async with database.transaction(DatabaseContext(tenant_id=UUID(tenant_id))) as connection:
            resources = await repository.list_cleanup_resources(
                connection,
                run_id=UUID(run_id),
                node_id=plan.cleanup_order[0],
            )
            claimed = await repository.claim_resource_cleanup(
                connection,
                resource_id=resources[0].id,
                expected_revision=resources[0].revision,
                attempt_id=new_entity_id(),
                worker_identity="fixture-crashed-worker",
                started_at=claimed_at,
                blocked_retry_at=claimed_at,
            )
            assert claimed is not None
        async with database.transaction(DatabaseContext(tenant_id=UUID(tenant_id))) as connection:
            retried, leaked = await repository.recover_stale_cleanup_claims(
                connection,
                stale_before=utc_now() + timedelta(seconds=1),
                retry_at=utc_now(),
                max_attempts=5,
                limit=20,
            )
            assert (retried, leaked) == (1, 0)
        swept = await worker.sweep_cleanup(
            UUID(tenant_id),
            worker_identity="fixture-recovery-worker",
            limit=20,
        )
        assert swept.cleaned_resources == 1
        assert swept.finalized_runs >= 1
    finally:
        await database.close()


async def _recover_stale_reconcile_claim(
    settings: Settings,
    registry: FixtureOperationRegistry,
    tenant_id: str,
    run_id: str,
) -> None:
    database = Database(settings)
    await database.open()
    try:
        repository = FixtureRunRepository()
        worker = FixtureWorkerService(
            database,
            registry,
            cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
        )
        plan = await worker.load_plan(UUID(tenant_id), UUID(run_id))
        node_id = plan.execution_levels[0][0]
        uncertain = await worker.execute_node(UUID(tenant_id), UUID(run_id), node_id)
        assert uncertain.status.value == "OUTCOME_UNCERTAIN"
        claimed_at = utc_now()
        async with database.transaction(DatabaseContext(tenant_id=UUID(tenant_id))) as connection:
            node = await repository.get_node_record(
                connection,
                run_id=UUID(run_id),
                node_id=node_id,
                for_update=True,
            )
            assert node is not None
            claimed = await repository.start_reconcile_attempt(
                connection,
                node=node,
                attempt_id=new_entity_id(),
                started_at=claimed_at,
            )
            assert claimed is not None
        async with database.transaction(DatabaseContext(tenant_id=UUID(tenant_id))) as connection:
            retried, exhausted = await repository.recover_stale_reconcile_claims(
                connection,
                stale_before=utc_now() + timedelta(seconds=1),
                retry_at=utc_now(),
                max_attempts=5,
                limit=20,
            )
            assert (retried, exhausted) == (1, 0)
        swept = await worker.sweep_cleanup(
            UUID(tenant_id),
            worker_identity="fixture-reconcile-worker",
            limit=20,
        )
        assert swept.reconciled_found == 1
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
            reconcile_max_attempts=1,
        )
        plan = await worker.load_plan(UUID(tenant_id), UUID(run_id))
        result = await worker.execute_node(
            UUID(tenant_id),
            UUID(run_id),
            plan.execution_levels[0][0],
        )
        assert result.failure_code is not None
        if result.status.value == "OUTCOME_UNCERTAIN":
            await worker.reconcile_node(
                UUID(tenant_id),
                UUID(run_id),
                plan.execution_levels[0][0],
            )
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
        assert resources.json()["cleanupAttempts"][0]["status"] == "SUCCEEDED"
        atom_after_cleanup = client.get(
            f"/v1/data-atom-versions/{atom_version_id}",
            headers=headers,
        )
        assert atom_after_cleanup.json()["cleanupValidationState"] == "PASSED"
        blueprint_after_cleanup = client.get(
            f"/v1/data-blueprint-versions/{blueprint_version_id}",
            headers=headers,
        )
        assert blueprint_after_cleanup.json()["cleanupValidationState"] == "PASSED"
        published_atom = client.post(
            f"/v1/data-atom-versions/{atom_version_id}:publish",
            headers={**headers, "If-Match": atom_after_cleanup.headers["etag"]},
        )
        assert published_atom.status_code == 200, published_atom.text
        published_blueprint = client.post(
            f"/v1/data-blueprint-versions/{blueprint_version_id}:publish",
            headers={**headers, "If-Match": blueprint_after_cleanup.headers["etag"]},
        )
        assert published_blueprint.status_code == 200, published_blueprint.text
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
              (select count(*) from atlas.fixture_validation_evidence
               where fixture_run_id = %s and kind = 'CLEANUP'),
              (select count(*) from atlas.resource_record where fixture_run_id = %s),
              (select count(*) from atlas.fixture_manifest where fixture_run_id = %s)
            """,
            (UUID(run_id), UUID(run_id), UUID(run_id), UUID(run_id)),
        ).fetchone()
        assert counts == (2, 2, 1, 1)
        with pytest.raises(psycopg.Error), connection.transaction():
            connection.execute(
                "update atlas.fixture_manifest set manifest = '{}'::jsonb "
                "where fixture_run_id = %s",
                (UUID(run_id),),
            )


def test_uncertain_create_reconciles_found_before_ready() -> None:
    settings = _settings()
    registry = FixtureOperationRegistry()
    registry.register("generic-password", UncertainCreateProvider())
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
            _,
            _,
            headers,
            _,
            _,
            _,
            _,
            _,
            run_id,
        ) = _request_validation_run(client, prefix=prefix)

    initial_status = asyncio.run(
        _prepare_run_with_registry(settings, registry, tenant_id, run_id)
    )
    assert initial_status == "OUTCOME_UNCERTAIN"

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["run"]["status"] == "READY"
        assert detail.json()["nodes"][0]["status"] == "SUCCEEDED"
        assert detail.json()["nodes"][0]["reconcileState"] == "FOUND"
        assert detail.json()["attempts"][0]["status"] == "OUTCOME_UNCERTAIN"
        assert detail.json()["reconcileAttempts"][0]["status"] == "FOUND"
        resources = client.get(f"/v1/fixture-runs/{run_id}/resources", headers=headers)
        assert resources.json()["items"][0]["status"] == "ACTIVE"


def test_reconciled_resource_failing_postcondition_is_cleaned_and_run_fails() -> None:
    settings = _settings()
    registry = FixtureOperationRegistry()
    registry.register("generic-password", ReconciledPostconditionFailureProvider())
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
            _,
            _,
            headers,
            _,
            _,
            _,
            atom_version_id,
            _,
            run_id,
        ) = _request_validation_run(client, prefix=prefix)

    initial_status = asyncio.run(_fail_run(settings, registry, tenant_id, run_id))
    assert initial_status == "OUTCOME_UNCERTAIN"

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["run"]["status"] == "FAILED"
        assert detail.json()["run"]["cleanupState"] == "CLEANED"
        assert detail.json()["nodes"][0]["status"] == "FAILED"
        assert detail.json()["nodes"][0]["failureCategory"] == "VALIDATION"
        assert detail.json()["nodes"][0]["failureCode"] == (
            "RECONCILED_RESOURCE_NOT_VISIBLE"
        )
        resources = client.get(f"/v1/fixture-runs/{run_id}/resources", headers=headers)
        assert resources.json()["items"][0]["status"] == "CLEANED"
        assert resources.json()["cleanupAttempts"][0]["status"] == "SUCCEEDED"
        atom = client.get(f"/v1/data-atom-versions/{atom_version_id}", headers=headers)
        assert atom.json()["cleanupValidationState"] == "PENDING"


def test_reconcile_provider_error_is_exhausted_without_create_retry() -> None:
    settings = _settings()
    registry = FixtureOperationRegistry()
    registry.register("generic-password", ReconcileDeniedProvider())
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
            _,
            _,
            headers,
            _,
            _,
            _,
            atom_version_id,
            _,
            run_id,
        ) = _request_validation_run(client, prefix=prefix)

    initial_status = asyncio.run(_fail_run(settings, registry, tenant_id, run_id))
    assert initial_status == "OUTCOME_UNCERTAIN"

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["run"]["status"] == "FAILED"
        assert detail.json()["run"]["cleanupState"] == "LEAKED"
        assert detail.json()["nodes"][0]["status"] == "OUTCOME_UNCERTAIN"
        assert detail.json()["nodes"][0]["reconcileState"] == "EXHAUSTED"
        assert detail.json()["nodes"][0]["reconcileAttemptCount"] == 1
        assert detail.json()["reconcileAttempts"][0]["status"] == "FAILED"
        assert detail.json()["reconcileAttempts"][0]["providerRequestId"] == (
            "provider-reconcile-denied"
        )
        resources = client.get(f"/v1/fixture-runs/{run_id}/resources", headers=headers)
        assert resources.json()["items"] == []
        atom = client.get(f"/v1/data-atom-versions/{atom_version_id}", headers=headers)
        assert atom.json()["cleanupValidationState"] == "PENDING"


@pytest.mark.parametrize(
    ("mode", "expected_attempt_status", "expected_provider_request_id"),
    [
        ("invalid-output", "FAILED", "provider-reconcile-invalid"),
        ("transport-error", "INCONCLUSIVE", None),
    ],
)
def test_faulty_reconcile_is_exhausted_without_create_retry(
    mode: str,
    expected_attempt_status: str,
    expected_provider_request_id: str | None,
) -> None:
    settings = _settings()
    registry = FixtureOperationRegistry()
    registry.register("generic-password", FaultyReconcileProvider(mode))
    dispatcher = RecordingFixtureDispatcher()
    app = create_app(
        settings,
        fixture_operation_registry=registry,
        fixture_run_dispatcher=dispatcher,
    )
    prefix = uuid7().hex[-10:]
    with TestClient(app) as client:
        tenant_id, _, _, headers, _, _, _, _, _, run_id = _request_validation_run(
            client,
            prefix=prefix,
        )

    initial_status = asyncio.run(_fail_run(settings, registry, tenant_id, run_id))
    assert initial_status == "OUTCOME_UNCERTAIN"

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["run"]["status"] == "FAILED"
        assert detail.json()["run"]["cleanupState"] == "LEAKED"
        assert detail.json()["nodes"][0]["status"] == "OUTCOME_UNCERTAIN"
        assert detail.json()["nodes"][0]["reconcileState"] == "EXHAUSTED"
        assert detail.json()["nodes"][0]["reconcileAttemptCount"] == 1
        attempt = detail.json()["reconcileAttempts"][0]
        assert attempt["status"] == expected_attempt_status
        assert attempt["providerRequestId"] == expected_provider_request_id
        assert attempt["failureCode"] == "RECONCILE_EXHAUSTED"


def test_reconcile_absent_allows_one_bounded_create_retry() -> None:
    settings = _settings()
    provider = AbsentThenCreatedProvider()
    registry = FixtureOperationRegistry()
    registry.register("generic-password", provider)
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
            _,
            _,
            headers,
            _,
            _,
            _,
            _,
            _,
            run_id,
        ) = _request_validation_run(client, prefix=prefix)

    initial_status = asyncio.run(
        _prepare_run_with_registry(settings, registry, tenant_id, run_id)
    )
    assert initial_status == "OUTCOME_UNCERTAIN"
    assert provider.create_calls == 2

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.json()["run"]["status"] == "READY"
        assert detail.json()["nodes"][0]["reconcileState"] == "ABSENT"
        assert [item["status"] for item in detail.json()["attempts"]] == [
            "OUTCOME_UNCERTAIN",
            "SUCCEEDED",
        ]
        assert detail.json()["reconcileAttempts"][0]["status"] == "ABSENT"


def test_sweeper_recovers_stale_reconcile_claim() -> None:
    settings = _settings()
    registry = FixtureOperationRegistry()
    registry.register("generic-password", UncertainCreateProvider())
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
            _,
            _,
            headers,
            _,
            _,
            _,
            _,
            _,
            run_id,
        ) = _request_validation_run(client, prefix=prefix)

    asyncio.run(_recover_stale_reconcile_claim(settings, registry, tenant_id, run_id))

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.json()["run"]["status"] == "RUNNING"
        assert detail.json()["nodes"][0]["status"] == "SUCCEEDED"
        assert [item["status"] for item in detail.json()["reconcileAttempts"]] == [
            "INCONCLUSIVE",
            "FOUND",
        ]


def test_cancel_ready_run_always_cleans_without_cleanup_evidence() -> None:
    settings = _settings()
    registry = FixtureOperationRegistry.from_settings(settings)
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
            _,
            _,
            headers,
            _,
            lease_id,
            _,
            atom_version_id,
            _,
            run_id,
        ) = _request_validation_run(client, prefix=prefix)

    asyncio.run(_prepare_run_with_registry(settings, registry, tenant_id, run_id))

    with TestClient(app) as client:
        canceled = client.post(f"/v1/fixture-runs/{run_id}:cancel", headers=headers)
        assert canceled.status_code == 202, canceled.text
        assert canceled.json()["cancelRequestedAt"] is not None
        assert dispatcher.canceled == [UUID(run_id)]

    asyncio.run(_finish_canceled_run(settings, registry, tenant_id, run_id))

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.json()["run"]["status"] == "CANCELED"
        assert detail.json()["run"]["terminalIntent"] == "CANCELED"
        assert detail.json()["run"]["cleanupState"] == "CLEANED"
        resources = client.get(f"/v1/fixture-runs/{run_id}/resources", headers=headers)
        assert resources.json()["items"][0]["status"] == "CLEANED"
        assert resources.json()["cleanupAttempts"][0]["status"] == "SUCCEEDED"
        atom = client.get(f"/v1/data-atom-versions/{atom_version_id}", headers=headers)
        assert atom.json()["cleanupValidationState"] == "PENDING"
        retry = client.post(
            f"/v1/fixture-runs/{run_id}:retry-cleanup",
            headers=headers,
        )
        assert retry.status_code == 409
        lease = client.get(f"/internal/v1/account-leases/{lease_id}", headers=headers)
        assert lease.json()["status"] == "RELEASED"


def test_cleanup_retry_and_sweeper_complete_transient_failure() -> None:
    settings = _settings()
    provider = TransientCleanupProvider()
    registry = FixtureOperationRegistry()
    registry.register("generic-password", provider)
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
            _,
            _,
            headers,
            _,
            lease_id,
            _,
            _,
            _,
            run_id,
        ) = _request_validation_run(client, prefix=prefix)

    asyncio.run(_prepare_run_with_registry(settings, registry, tenant_id, run_id))
    asyncio.run(_fail_first_cleanup(settings, registry, tenant_id, run_id))

    with TestClient(app) as client:
        pending = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert pending.json()["run"]["status"] == "CLEANING"
        assert pending.json()["run"]["cleanupState"] == "PENDING"
        retry = client.post(
            f"/v1/fixture-runs/{run_id}:retry-cleanup",
            headers=headers,
        )
        assert retry.status_code == 202, retry.text
        assert dispatcher.cleanup_retried == [UUID(run_id)]

    asyncio.run(_sweep_run(settings, registry, tenant_id))

    with TestClient(app) as client:
        released = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert released.json()["run"]["status"] == "RELEASED"
        resources = client.get(f"/v1/fixture-runs/{run_id}/resources", headers=headers)
        assert [item["status"] for item in resources.json()["cleanupAttempts"]] == [
            "FAILED",
            "SUCCEEDED",
        ]
        lease = client.get(f"/internal/v1/account-leases/{lease_id}", headers=headers)
        assert lease.json()["status"] == "RELEASED"
        sweep = client.post(
            "/internal/v1/fixture-cleanup:sweep",
            headers=headers,
            params={"workerIdentity": "fixture-api-sweeper", "limit": 10},
        )
        assert sweep.status_code == 200, sweep.text
        assert sweep.json()["cleanupClaimed"] == 0
    assert provider.cleanup_calls == 2


def test_sweeper_recovers_stale_cleanup_claim_before_retrying() -> None:
    settings = _settings()
    registry = FixtureOperationRegistry.from_settings(settings)
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
            _,
            _,
            headers,
            _,
            _,
            _,
            _,
            _,
            run_id,
        ) = _request_validation_run(client, prefix=prefix)

    asyncio.run(_prepare_run_with_registry(settings, registry, tenant_id, run_id))
    asyncio.run(_recover_stale_cleanup_claim(settings, registry, tenant_id, run_id))

    with TestClient(app) as client:
        detail = client.get(f"/v1/fixture-runs/{run_id}", headers=headers)
        assert detail.json()["run"]["status"] == "RELEASED"
        resources = client.get(f"/v1/fixture-runs/{run_id}/resources", headers=headers)
        assert [item["status"] for item in resources.json()["cleanupAttempts"]] == [
            "OUTCOME_UNCERTAIN",
            "SUCCEEDED",
        ]


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
        assert detail.json()["run"]["cleanupState"] == (
            "CLEANED" if mode == "provider-error" else "LEAKED"
        )
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
