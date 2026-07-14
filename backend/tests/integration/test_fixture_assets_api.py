"""Fixture asset API tests against real PostgreSQL lifecycle guards."""

from os import environ
from typing import cast
from uuid import UUID, uuid7

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from atlas_testops.core.config import Settings
from atlas_testops.main import create_app

DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        DATABASE_URL is None,
        reason="ATLAS_TEST_DATABASE_URL is not configured",
    ),
]


def actor_headers(tenant_id: str) -> dict[str, str]:
    return {
        "X-Atlas-Tenant-ID": tenant_id,
        "X-Atlas-Actor-ID": str(uuid7()),
    }


def bootstrap_project(client: TestClient, suffix: str) -> tuple[str, str, dict[str, str]]:
    tenant_response = client.post(
        "/v1/tenants",
        json={"slug": f"fixture-{suffix}", "name": f"Fixture {suffix}"},
    )
    assert tenant_response.status_code == 201, tenant_response.text
    tenant_id = tenant_response.json()["id"]
    headers = actor_headers(tenant_id)
    project_response = client.post(
        "/v1/projects",
        headers={**headers, "Idempotency-Key": f"fixture-project-{suffix}"},
        json={"projectKey": f"FIXTURE_{suffix.upper()}", "name": "Fixture Project"},
    )
    assert project_response.status_code == 201, project_response.text
    return tenant_id, project_response.json()["id"], headers


def atom_contract() -> dict[str, object]:
    operation = {
        "operationKey": "customer.create",
        "operationVersion": "1.0.0",
        "requiredCapabilities": ["customer.create"],
    }
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
        "operation": operation,
        "idempotencyPolicy": {
            "mode": "RECONCILE",
            "markerInput": "executionId",
        },
        "postconditions": [
            {"kind": "OUTPUT_SCHEMA", "outputPort": "customerRef"},
        ],
        "resourcePolicy": {
            "resourceType": "resource.customer-ref",
            "resourceRefOutput": "customerRef",
        },
        "cleanupContract": {
            "operation": {
                **operation,
                "operationKey": "customer.delete",
                "requiredCapabilities": ["customer.delete"],
            },
            "resourceRefInput": "customerRef",
        },
        "reconcileContract": {
            "operation": {
                **operation,
                "operationKey": "customer.lookup",
                "requiredCapabilities": ["customer.lookup"],
            },
            "markerInput": "executionId",
            "resourceRefOutput": "customerRef",
        },
    }


def blueprint_contract(atom_version_id: str) -> dict[str, object]:
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


def mark_runtime_and_cleanup_evidence(
    table: str,
    *,
    tenant_id: str,
    version_id: str,
) -> None:
    assert DATABASE_URL is not None
    assert table in {"data_atom_version", "data_blueprint_version"}
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, true)",
            (tenant_id,),
        )
        connection.execute(
            f"""
            update atlas.{table}
            set runtime_validation_state = 'PASSED',
                cleanup_validation_state = 'PASSED',
                revision = revision + 1
            where id = %s
            """,
            (UUID(version_id),),
        )


def test_fixture_asset_control_plane_lifecycle_and_isolation() -> None:
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
        tenant_id, project_id, headers = bootstrap_project(client, suffix)
        other_tenant_id, _, other_headers = bootstrap_project(client, f"b{suffix}")
        assert other_tenant_id != tenant_id

        create_atom_headers = {
            **headers,
            "Idempotency-Key": f"fixture-atom-{suffix}",
        }
        atom_response = client.post(
            f"/v1/projects/{project_id}/data-atoms",
            headers=create_atom_headers,
            json={
                "atomKey": "customer.create",
                "businessDomain": "customer",
                "name": "Create Customer",
                "description": "Create one isolated customer fixture.",
            },
        )
        assert atom_response.status_code == 201, atom_response.text
        assert atom_response.headers["idempotency-replayed"] == "false"
        atom = cast(dict[str, object], atom_response.json())
        atom_id = str(atom["id"])

        replayed_atom = client.post(
            f"/v1/projects/{project_id}/data-atoms",
            headers=create_atom_headers,
            json={
                "atomKey": "customer.create",
                "businessDomain": "customer",
                "name": "Create Customer",
                "description": "Create one isolated customer fixture.",
            },
        )
        assert replayed_atom.status_code == 201
        assert replayed_atom.headers["idempotency-replayed"] == "true"
        assert replayed_atom.json() == atom

        cross_tenant = client.get(f"/v1/data-atoms/{atom_id}", headers=other_headers)
        assert cross_tenant.status_code == 404

        atom_version_response = client.post(
            f"/v1/data-atoms/{atom_id}/versions",
            headers={
                **headers,
                "Idempotency-Key": f"fixture-atom-version-{suffix}",
            },
            json={"version": "1.0.0", "contract": atom_contract()},
        )
        assert atom_version_response.status_code == 201, atom_version_response.text
        atom_version = atom_version_response.json()
        atom_version_id = atom_version["id"]
        assert atom_version["status"] == "DRAFT"

        validated_atom = client.post(
            f"/v1/data-atom-versions/{atom_version_id}:validate",
            headers={**headers, "If-Match": '"revision-1"'},
        )
        assert validated_atom.status_code == 200, validated_atom.text
        assert validated_atom.json()["status"] == "VALIDATED"
        assert validated_atom.json()["staticValidationState"] == "PASSED"
        assert validated_atom.json()["runtimeValidationState"] == "PENDING"
        assert validated_atom.json()["cleanupValidationState"] == "PENDING"

        blocked_atom_publish = client.post(
            f"/v1/data-atom-versions/{atom_version_id}:publish",
            headers={**headers, "If-Match": '"revision-2"'},
        )
        assert blocked_atom_publish.status_code == 409
        assert blocked_atom_publish.json()["errorCode"] == "PUBLICATION_EVIDENCE_REQUIRED"

        blueprint_response = client.post(
            f"/v1/projects/{project_id}/data-blueprints",
            headers={
                **headers,
                "Idempotency-Key": f"fixture-blueprint-{suffix}",
            },
            json={
                "blueprintKey": "customer.ready",
                "name": "Customer Ready",
                "description": "Prepare one customer for a test case.",
            },
        )
        assert blueprint_response.status_code == 201, blueprint_response.text
        blueprint_id = blueprint_response.json()["id"]

        blueprint_version_response = client.post(
            f"/v1/data-blueprints/{blueprint_id}/versions",
            headers={
                **headers,
                "Idempotency-Key": f"fixture-blueprint-version-{suffix}",
            },
            json={
                "version": "1.0.0",
                "contract": blueprint_contract(atom_version_id),
            },
        )
        assert blueprint_version_response.status_code == 201, blueprint_version_response.text
        blueprint_version_id = blueprint_version_response.json()["id"]

        first_compile = client.post(
            f"/v1/data-blueprint-versions/{blueprint_version_id}:compile",
            headers={**headers, "If-Match": '"revision-1"'},
        )
        assert first_compile.status_code == 200, first_compile.text
        assert first_compile.json()["compilation"]["valid"] is True
        first_plan = first_compile.json()["compilation"]["plan"]
        assert first_plan["cleanupOrder"] == ["createCustomer"]

        second_compile = client.post(
            f"/v1/data-blueprint-versions/{blueprint_version_id}:compile",
            headers={**headers, "If-Match": '"revision-2"'},
        )
        assert second_compile.status_code == 200, second_compile.text
        second_plan = second_compile.json()["compilation"]["plan"]
        assert second_plan["planDigest"] == first_plan["planDigest"]

        blocked_blueprint_publish = client.post(
            f"/v1/data-blueprint-versions/{blueprint_version_id}:publish",
            headers={**headers, "If-Match": '"revision-3"'},
        )
        assert blocked_blueprint_publish.status_code == 409
        assert blocked_blueprint_publish.json()["errorCode"] == "PUBLICATION_EVIDENCE_REQUIRED"

        mark_runtime_and_cleanup_evidence(
            "data_atom_version",
            tenant_id=tenant_id,
            version_id=atom_version_id,
        )
        published_atom = client.post(
            f"/v1/data-atom-versions/{atom_version_id}:publish",
            headers={**headers, "If-Match": '"revision-3"'},
        )
        assert published_atom.status_code == 200, published_atom.text
        assert published_atom.json()["status"] == "PUBLISHED"

        immutable_atom = client.patch(
            f"/v1/data-atom-versions/{atom_version_id}",
            headers={**headers, "If-Match": '"revision-4"'},
            json={"contract": atom_contract()},
        )
        assert immutable_atom.status_code == 409
        assert immutable_atom.json()["errorCode"] == "ASSET_IMMUTABLE"

        mark_runtime_and_cleanup_evidence(
            "data_blueprint_version",
            tenant_id=tenant_id,
            version_id=blueprint_version_id,
        )
        published_blueprint = client.post(
            f"/v1/data-blueprint-versions/{blueprint_version_id}:publish",
            headers={**headers, "If-Match": '"revision-4"'},
        )
        assert published_blueprint.status_code == 200, published_blueprint.text
        assert published_blueprint.json()["status"] == "PUBLISHED"

        atom_catalog = client.get(
            f"/v1/projects/{project_id}/data-atoms",
            headers=headers,
        )
        assert atom_catalog.status_code == 200, atom_catalog.text
        assert atom_catalog.json()["items"][0]["latestVersionStatus"] == "PUBLISHED"
        blueprint_catalog = client.get(
            f"/v1/projects/{project_id}/data-blueprints",
            headers=headers,
        )
        assert blueprint_catalog.status_code == 200, blueprint_catalog.text
        assert blueprint_catalog.json()["items"][0]["planDigest"] == first_plan["planDigest"]

    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "select set_config('atlas.tenant_id', %s, true)",
            (tenant_id,),
        )
        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                update atlas.data_atom_version
                set contract = contract || '{"effect":"READ"}'::jsonb,
                    revision = revision + 1
                where id = %s
                """,
                (UUID(atom_version_id),),
            )

    with psycopg.connect(DATABASE_URL) as connection:
        privileges = connection.execute(
            """
            select
              has_table_privilege('atlas_app', 'atlas.data_atom_version', 'DELETE'),
              has_table_privilege('atlas_app', 'atlas.data_blueprint_version', 'DELETE')
            """
        ).fetchone()
        assert privileges == (False, False)

        leaked_contracts = connection.execute(
            """
            select count(*)
            from atlas.audit_event
            where tenant_id = %s
              and entity_type in ('data_atom_version', 'data_blueprint_version')
              and payload::text like '%%operationKey%%'
            """,
            (UUID(tenant_id),),
        ).fetchone()
        assert leaked_contracts == (0,)


def test_fixture_asset_draft_crud_pagination_and_failed_compilation() -> None:
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
        _, project_id, headers = bootstrap_project(client, f"c{suffix}")
        atom_headers = {
            **headers,
            "Idempotency-Key": f"fixture-crud-atom-{suffix}",
        }
        atom_command = {
            "atomKey": "customer.create",
            "businessDomain": "customer",
            "name": "Create Customer",
            "description": "Create one customer.",
        }
        created_atom = client.post(
            f"/v1/projects/{project_id}/data-atoms",
            headers=atom_headers,
            json=atom_command,
        )
        assert created_atom.status_code == 201, created_atom.text
        atom_id = created_atom.json()["id"]

        idempotency_conflict = client.post(
            f"/v1/projects/{project_id}/data-atoms",
            headers=atom_headers,
            json={**atom_command, "name": "Different"},
        )
        assert idempotency_conflict.status_code == 409

        atom_detail = client.get(f"/v1/data-atoms/{atom_id}", headers=headers)
        assert atom_detail.status_code == 200
        updated_atom = client.patch(
            f"/v1/data-atoms/{atom_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"description": "Create one isolated customer."},
        )
        assert updated_atom.status_code == 200
        assert updated_atom.json()["revision"] == 2
        stale_atom = client.patch(
            f"/v1/data-atoms/{atom_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"name": "Stale"},
        )
        assert stale_atom.status_code == 412

        second_atom = client.post(
            f"/v1/projects/{project_id}/data-atoms",
            headers={
                **headers,
                "Idempotency-Key": f"fixture-crud-atom-second-{suffix}",
            },
            json={
                "atomKey": "order.create",
                "businessDomain": "order",
                "name": "Create Order",
                "description": "Create one order.",
            },
        )
        assert second_atom.status_code == 201
        atom_page = client.get(
            f"/v1/projects/{project_id}/data-atoms",
            headers=headers,
            params={"limit": 1},
        )
        assert atom_page.status_code == 200
        assert atom_page.json()["nextCursor"] is not None
        next_atom_page = client.get(
            f"/v1/projects/{project_id}/data-atoms",
            headers=headers,
            params={"limit": 1, "cursor": atom_page.json()["nextCursor"]},
        )
        assert next_atom_page.status_code == 200
        assert next_atom_page.json()["items"][0]["id"] != atom_page.json()["items"][0]["id"]

        atom_version_headers = {
            **headers,
            "Idempotency-Key": f"fixture-crud-atom-version-{suffix}",
        }
        atom_version_command = {"version": "1.0.0", "contract": atom_contract()}
        created_atom_version = client.post(
            f"/v1/data-atoms/{atom_id}/versions",
            headers=atom_version_headers,
            json=atom_version_command,
        )
        assert created_atom_version.status_code == 201, created_atom_version.text
        atom_version_id = created_atom_version.json()["id"]
        replayed_atom_version = client.post(
            f"/v1/data-atoms/{atom_id}/versions",
            headers=atom_version_headers,
            json=atom_version_command,
        )
        assert replayed_atom_version.status_code == 201
        assert replayed_atom_version.headers["idempotency-replayed"] == "true"
        assert (
            client.get(
                f"/v1/data-atom-versions/{atom_version_id}",
                headers=headers,
            ).status_code
            == 200
        )
        atom_versions = client.get(
            f"/v1/data-atoms/{atom_id}/versions",
            headers=headers,
        )
        assert atom_versions.status_code == 200
        assert len(atom_versions.json()["items"]) == 1

        updated_atom_version = client.patch(
            f"/v1/data-atom-versions/{atom_version_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"contract": atom_contract()},
        )
        assert updated_atom_version.status_code == 200
        assert updated_atom_version.json()["revision"] == 2
        validated_atom_version = client.post(
            f"/v1/data-atom-versions/{atom_version_id}:validate",
            headers={**headers, "If-Match": '"revision-2"'},
        )
        assert validated_atom_version.status_code == 200

        blueprint_headers = {
            **headers,
            "Idempotency-Key": f"fixture-crud-blueprint-{suffix}",
        }
        blueprint_command = {
            "blueprintKey": "customer.ready",
            "name": "Customer Ready",
            "description": "Prepare a customer.",
        }
        created_blueprint = client.post(
            f"/v1/projects/{project_id}/data-blueprints",
            headers=blueprint_headers,
            json=blueprint_command,
        )
        assert created_blueprint.status_code == 201, created_blueprint.text
        blueprint_id = created_blueprint.json()["id"]
        assert (
            client.get(
                f"/v1/data-blueprints/{blueprint_id}",
                headers=headers,
            ).status_code
            == 200
        )
        updated_blueprint = client.patch(
            f"/v1/data-blueprints/{blueprint_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"description": "Prepare one isolated customer."},
        )
        assert updated_blueprint.status_code == 200

        second_blueprint = client.post(
            f"/v1/projects/{project_id}/data-blueprints",
            headers={
                **headers,
                "Idempotency-Key": f"fixture-crud-blueprint-second-{suffix}",
            },
            json={
                "blueprintKey": "order.ready",
                "name": "Order Ready",
                "description": "Prepare an order.",
            },
        )
        assert second_blueprint.status_code == 201
        blueprint_page = client.get(
            f"/v1/projects/{project_id}/data-blueprints",
            headers=headers,
            params={"limit": 1},
        )
        assert blueprint_page.status_code == 200
        assert blueprint_page.json()["nextCursor"] is not None
        next_blueprint_page = client.get(
            f"/v1/projects/{project_id}/data-blueprints",
            headers=headers,
            params={"limit": 1, "cursor": blueprint_page.json()["nextCursor"]},
        )
        assert next_blueprint_page.status_code == 200

        blueprint_version = client.post(
            f"/v1/data-blueprints/{blueprint_id}/versions",
            headers={
                **headers,
                "Idempotency-Key": f"fixture-crud-blueprint-version-{suffix}",
            },
            json={
                "version": "1.0.0",
                "contract": blueprint_contract(atom_version_id),
            },
        )
        assert blueprint_version.status_code == 201, blueprint_version.text
        blueprint_version_id = blueprint_version.json()["id"]
        assert (
            client.get(
                f"/v1/data-blueprint-versions/{blueprint_version_id}",
                headers=headers,
            ).status_code
            == 200
        )
        blueprint_versions = client.get(
            f"/v1/data-blueprints/{blueprint_id}/versions",
            headers=headers,
        )
        assert blueprint_versions.status_code == 200
        assert len(blueprint_versions.json()["items"]) == 1

        missing_atom_id = str(uuid7())
        invalid_contract = blueprint_contract(missing_atom_id)
        updated_blueprint_version = client.patch(
            f"/v1/data-blueprint-versions/{blueprint_version_id}",
            headers={**headers, "If-Match": '"revision-1"'},
            json={"contract": invalid_contract},
        )
        assert updated_blueprint_version.status_code == 200
        failed_compile = client.post(
            f"/v1/data-blueprint-versions/{blueprint_version_id}:compile",
            headers={**headers, "If-Match": '"revision-2"'},
        )
        assert failed_compile.status_code == 200, failed_compile.text
        assert failed_compile.json()["compilation"]["valid"] is False
        assert failed_compile.json()["compilation"]["issues"][0]["code"] == (
            "ATOM_VERSION_NOT_FOUND"
        )

        restored_blueprint_version = client.patch(
            f"/v1/data-blueprint-versions/{blueprint_version_id}",
            headers={**headers, "If-Match": '"revision-3"'},
            json={"contract": blueprint_contract(atom_version_id)},
        )
        assert restored_blueprint_version.status_code == 200
        successful_compile = client.post(
            f"/v1/data-blueprint-versions/{blueprint_version_id}:compile",
            headers={**headers, "If-Match": '"revision-4"'},
        )
        assert successful_compile.status_code == 200
        assert successful_compile.json()["compilation"]["valid"] is True

        archived_atom = client.patch(
            f"/v1/data-atoms/{atom_id}",
            headers={**headers, "If-Match": '"revision-2"'},
            json={"status": "ARCHIVED"},
        )
        assert archived_atom.status_code == 200
        immutable_definition = client.patch(
            f"/v1/data-atoms/{atom_id}",
            headers={**headers, "If-Match": '"revision-3"'},
            json={"name": "Cannot Change"},
        )
        assert immutable_definition.status_code == 409
        create_on_archived_atom = client.post(
            f"/v1/data-atoms/{atom_id}/versions",
            headers={
                **headers,
                "Idempotency-Key": f"fixture-archived-version-{suffix}",
            },
            json={"version": "2.0.0", "contract": atom_contract()},
        )
        assert create_on_archived_atom.status_code == 409
