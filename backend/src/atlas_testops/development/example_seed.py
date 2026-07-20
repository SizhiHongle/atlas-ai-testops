"""Idempotently seed local example assets and graph-valid browser test cases."""

from __future__ import annotations

import argparse
import hashlib
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx2
from pydantic import JsonValue

from atlas_testops.domain.case import CreateTestCase
from atlas_testops.domain.fixture import (
    CreateDataAtom,
    CreateDataAtomVersion,
    CreateDataBlueprint,
    CreateDataBlueprintVersion,
)
from atlas_testops.domain.fixture import (
    canonical_digest as fixture_digest,
)
from atlas_testops.infrastructure.adapters.local_public_web import (
    BAIDU_ORIGIN,
    BAIDU_SURFACE_DIGEST,
    BAIDU_SURFACE_KEY,
    BAIDU_SURFACE_VERSION_REF,
)
from atlas_testops.infrastructure.secrets import (
    LOCAL_PUBLIC_WEB_SECRET_REF,
    LOCAL_PUBLIC_WEB_SECRET_VERSION,
)

JsonObject = dict[str, JsonValue]

ROLE_KEY = "public.web.visitor"
ATOM_VERSION = "1.0.0"
BLUEPRINT_KEY = "demo.web.search-context"
BLUEPRINT_VERSION = "1.0.0"
BLUEPRINT_VERSION_REF = f"{BLUEPRINT_KEY}@{BLUEPRINT_VERSION}"
SURFACE_KEY = BAIDU_SURFACE_KEY
SURFACE_VERSION_REF = BAIDU_SURFACE_VERSION_REF


class SeedError(RuntimeError):
    """Raised when local example data cannot be created safely."""


@dataclass(frozen=True, slots=True)
class AtomExample:
    """One stable atom identity and its exact version contract."""

    definition: CreateDataAtom
    version: CreateDataAtomVersion


@dataclass(slots=True)
class SeedReport:
    """Human-readable summary of created and reused local examples."""

    created: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def build_atom_examples() -> tuple[AtomExample, ...]:
    """Build the two reusable fixture atoms used by every search example."""

    keyword = AtomExample(
        definition=CreateDataAtom(
            atom_key="demo.web.search-keyword",
            business_domain="public-web",
            name="准备网页搜索词",
            description="将用例输入转换为浏览器搜索步骤可消费的结构化搜索词。",
        ),
        version=CreateDataAtomVersion.model_validate(
            {
                "version": ATOM_VERSION,
                "contract": {
                    "schemaVersion": "atlas.atom/0.1",
                    "effect": "READ",
                    "ports": [
                        {
                            "key": "keyword",
                            "direction": "INPUT",
                            "semanticType": "web.search-keyword",
                            "jsonSchema": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                            },
                            "required": True,
                            "classification": "PUBLIC",
                        },
                        {
                            "key": "searchKeyword",
                            "direction": "OUTPUT",
                            "semanticType": "web.search-keyword",
                            "jsonSchema": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                            },
                            "required": True,
                            "classification": "PUBLIC",
                        },
                    ],
                    "operation": {
                        "operationKey": "demo.web.resolve-keyword",
                        "operationVersion": "1.0.0",
                        "requiredCapabilities": ["web.search.read"],
                        "timeoutSeconds": 5,
                    },
                    "retryPolicy": {
                        "maxAttempts": 2,
                        "initialBackoffMs": 100,
                        "maximumBackoffMs": 500,
                        "retryableCategories": ["TRANSIENT"],
                    },
                    "idempotencyPolicy": {"mode": "PROVIDER_NATIVE"},
                    "postconditions": [
                        {
                            "kind": "OUTPUT_SCHEMA",
                            "outputPort": "searchKeyword",
                        }
                    ],
                    "allowedEnvironmentKinds": ["TEST", "STAGING"],
                },
            }
        ),
    )
    expectation = AtomExample(
        definition=CreateDataAtom(
            atom_key="demo.web.search-expectation",
            business_domain="public-web",
            name="准备搜索结果预期",
            description="根据搜索词生成结果页断言所需的结构化预期文本。",
        ),
        version=CreateDataAtomVersion.model_validate(
            {
                "version": ATOM_VERSION,
                "contract": {
                    "schemaVersion": "atlas.atom/0.1",
                    "effect": "READ",
                    "ports": [
                        {
                            "key": "searchKeyword",
                            "direction": "INPUT",
                            "semanticType": "web.search-keyword",
                            "jsonSchema": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                            },
                            "required": True,
                            "classification": "PUBLIC",
                        },
                        {
                            "key": "expectedText",
                            "direction": "OUTPUT",
                            "semanticType": "web.expected-text",
                            "jsonSchema": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                            },
                            "required": True,
                            "classification": "PUBLIC",
                        },
                    ],
                    "operation": {
                        "operationKey": "demo.web.build-expectation",
                        "operationVersion": "1.0.0",
                        "requiredCapabilities": ["web.search.read"],
                        "timeoutSeconds": 5,
                    },
                    "retryPolicy": {
                        "maxAttempts": 2,
                        "initialBackoffMs": 100,
                        "maximumBackoffMs": 500,
                        "retryableCategories": ["TRANSIENT"],
                    },
                    "idempotencyPolicy": {"mode": "PROVIDER_NATIVE"},
                    "postconditions": [
                        {
                            "kind": "OUTPUT_SCHEMA",
                            "outputPort": "expectedText",
                        }
                    ],
                    "allowedEnvironmentKinds": ["TEST", "STAGING"],
                },
            }
        ),
    )
    return keyword, expectation


def build_blueprint_commands(
    *,
    keyword_atom_version_id: UUID,
    expectation_atom_version_id: UUID,
) -> tuple[CreateDataBlueprint, CreateDataBlueprintVersion]:
    """Build the reusable two-node search context blueprint."""

    definition = CreateDataBlueprint(
        blueprint_key=BLUEPRINT_KEY,
        name="公共网页搜索上下文",
        description="准备搜索词和结果断言预期，可复用于百度等公开搜索页面用例。",
    )
    version = CreateDataBlueprintVersion.model_validate(
        {
            "version": BLUEPRINT_VERSION,
            "contract": {
                "schemaVersion": "atlas.fixture-blueprint/0.1",
                "runInputSchema": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 120,
                        }
                    },
                    "required": ["keyword"],
                    "additionalProperties": False,
                },
                "nodes": [
                    {
                        "id": "prepareKeyword",
                        "atomVersionId": str(keyword_atom_version_id),
                        "actorSlot": "primary",
                        "bindings": [
                            {
                                "kind": "RUN_INPUT",
                                "targetPort": "keyword",
                                "pointer": "/keyword",
                            }
                        ],
                    },
                    {
                        "id": "prepareExpectation",
                        "atomVersionId": str(expectation_atom_version_id),
                        "actorSlot": "primary",
                        "bindings": [
                            {
                                "kind": "NODE_OUTPUT",
                                "targetPort": "searchKeyword",
                                "sourceNodeId": "prepareKeyword",
                                "sourcePort": "searchKeyword",
                            }
                        ],
                    },
                ],
                "exports": [
                    {
                        "name": "searchKeyword",
                        "sourceNodeId": "prepareKeyword",
                        "sourcePort": "searchKeyword",
                        "classification": "PUBLIC",
                    },
                    {
                        "name": "expectedText",
                        "sourceNodeId": "prepareExpectation",
                        "sourcePort": "expectedText",
                        "classification": "PUBLIC",
                    },
                ],
                "cleanupPolicy": "ALWAYS",
            },
        }
    )
    return definition, version


def _port(key: str, semantic_type: str) -> JsonObject:
    return {
        "key": key,
        "semanticType": semantic_type,
        "kind": "data",
        "required": True,
        "sensitive": False,
    }


def build_search_case_command(
    *,
    case_key: str,
    name: str,
    keyword: str,
    role: JsonObject,
    blueprint_version: JsonObject,
) -> CreateTestCase:
    """Build one graph-valid Baidu search case without raw URLs or selectors."""

    requirement_source = "local-examples/public-web-search/v1"
    requirement_anchor = f"baidu/search/{case_key.casefold()}"
    surface_digest = BAIDU_SURFACE_DIGEST
    graph: JsonObject = {
        "schemaVersion": "atlas.workflow-graph/0.1",
        "nodes": [
            {
                "id": "prepare-search-context",
                "kind": "fixture",
                "versionRef": BLUEPRINT_VERSION_REF,
                "phase": "setup",
                "inputPorts": [],
                "outputPorts": [
                    _port("searchKeyword", "web.search-keyword"),
                    _port("expectedText", "web.expected-text"),
                ],
                "params": {"inputVariable": "searchKeyword"},
                "terminal": False,
                "oracleStrength": None,
            },
            {
                "id": "open-baidu-home",
                "kind": "browser",
                "versionRef": "browser.surface-open@1.0.0",
                "phase": "execute",
                "inputPorts": [],
                "outputPorts": [_port("pageReady", "web.page-ready")],
                "params": {
                    "surfaceKey": SURFACE_KEY,
                    "operationKey": "surface.open",
                },
                "terminal": False,
                "oracleStrength": None,
            },
            {
                "id": "submit-search",
                "kind": "browser",
                "versionRef": "browser.semantic-search@1.0.0",
                "phase": "execute",
                "inputPorts": [
                    _port("pageReady", "web.page-ready"),
                    _port("searchKeyword", "web.search-keyword"),
                ],
                "outputPorts": [_port("searchResults", "web.search-results")],
                "params": {
                    "surfaceKey": SURFACE_KEY,
                    "operationKey": "search.submit",
                    "queryVariable": "searchKeyword",
                },
                "terminal": False,
                "oracleStrength": None,
            },
            {
                "id": "assert-search-results",
                "kind": "assertion",
                "versionRef": "assert.search-results-visible@1.0.0",
                "phase": "assert",
                "inputPorts": [
                    _port("searchResults", "web.search-results"),
                    _port("expectedText", "web.expected-text"),
                ],
                "outputPorts": [_port("result", "AssertionResult")],
                "params": {
                    "oracleKey": "search.results-visible",
                    "expectedVariable": "expectedText",
                },
                "terminal": False,
                "oracleStrength": "hard",
            },
            {
                "id": "close-browser-context",
                "kind": "cleanup",
                "versionRef": "cleanup.browser-context@1.0.0",
                "phase": "cleanup",
                "inputPorts": [_port("result", "AssertionResult")],
                "outputPorts": [],
                "params": {},
                "terminal": True,
                "oracleStrength": None,
            },
        ],
        "edges": [
            {
                "id": "page-to-search",
                "sourceNodeId": "open-baidu-home",
                "sourcePort": "pageReady",
                "targetNodeId": "submit-search",
                "targetPort": "pageReady",
                "semanticType": "web.page-ready",
                "kind": "data",
                "mapping": "direct",
            },
            {
                "id": "keyword-to-search",
                "sourceNodeId": "prepare-search-context",
                "sourcePort": "searchKeyword",
                "targetNodeId": "submit-search",
                "targetPort": "searchKeyword",
                "semanticType": "web.search-keyword",
                "kind": "data",
                "mapping": "direct",
            },
            {
                "id": "results-to-assertion",
                "sourceNodeId": "submit-search",
                "sourcePort": "searchResults",
                "targetNodeId": "assert-search-results",
                "targetPort": "searchResults",
                "semanticType": "web.search-results",
                "kind": "data",
                "mapping": "direct",
            },
            {
                "id": "expectation-to-assertion",
                "sourceNodeId": "prepare-search-context",
                "sourcePort": "expectedText",
                "targetNodeId": "assert-search-results",
                "targetPort": "expectedText",
                "semanticType": "web.expected-text",
                "kind": "data",
                "mapping": "direct",
            },
            {
                "id": "assertion-to-cleanup",
                "sourceNodeId": "assert-search-results",
                "sourcePort": "result",
                "targetNodeId": "close-browser-context",
                "targetPort": "result",
                "semanticType": "AssertionResult",
                "kind": "data",
                "mapping": "direct",
            },
        ],
    }
    payload: JsonObject = {
        "caseKey": case_key,
        "name": name,
        "intentVersion": "0.1.0",
        "intent": {
            "schemaVersion": "atlas.test-intent/0.1",
            "summary": f"公共网页访客打开百度，搜索“{keyword}”，并确认结果页展示搜索结果。",
            "requirementRefs": [
                {
                    "documentId": requirement_source,
                    "documentVersion": "1.0.0",
                    "contentDigest": _sha256_text(requirement_source),
                    "anchor": requirement_anchor,
                    "excerptDigest": _sha256_text(f"{requirement_source}#{requirement_anchor}"),
                }
            ],
            "actors": [
                {
                    "actorSlot": "primary",
                    "roleId": role["id"],
                    "roleKey": role["roleKey"],
                    "roleRevision": role["revision"],
                    "capabilities": role["capabilities"],
                }
            ],
            "fixture": {
                "blueprintVersionId": blueprint_version["id"],
                "blueprintVersionRef": BLUEPRINT_VERSION_REF,
                "contentDigest": blueprint_version["contentDigest"],
                "requiredExports": {
                    "expectedText": "web.expected-text",
                    "searchKeyword": "web.search-keyword",
                },
            },
            "surfaces": [
                {
                    "surfaceKey": SURFACE_KEY,
                    "versionRef": SURFACE_VERSION_REF,
                    "contentDigest": surface_digest,
                }
            ],
            "variables": {
                "searchKeyword": {
                    "kind": "LITERAL",
                    "value": keyword,
                }
            },
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
            "requiredFeatures": ["browser", "semantic-surface", "trace"],
        },
        "graph": graph,
        "layout": {
            "prepare-search-context": {"x": 40, "y": 70},
            "open-baidu-home": {"x": 40, "y": 270},
            "submit-search": {"x": 300, "y": 170},
            "assert-search-results": {"x": 560, "y": 170},
            "close-browser-context": {"x": 820, "y": 170},
        },
    }
    return CreateTestCase.model_validate(payload)


class AtlasApi:
    """Small checked client for the public Atlas authoring APIs."""

    def __init__(
        self,
        client: httpx2.Client,
        *,
        tenant_id: UUID,
        actor_id: UUID,
    ) -> None:
        self._client = client
        self._headers = {
            "Accept": "application/json",
            "X-Atlas-Tenant-ID": str(tenant_id),
            "X-Atlas-Actor-ID": str(actor_id),
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: JsonObject | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> JsonObject:
        response = self._client.request(
            method,
            path,
            json=payload,
            headers={**self._headers, **(headers or {})},
            params=params,
        )
        if not response.is_success:
            detail = response.text
            try:
                problem = cast(JsonObject, response.json())
                detail = str(problem.get("detail") or problem.get("title") or response.text)
                violations = problem.get("violations")
                if isinstance(violations, list) and violations:
                    detail = f"{detail} violations={violations}"
            except ValueError:
                pass
            raise SeedError(f"{method} {path} failed ({response.status_code}): {detail}")
        if response.status_code == 204:
            return {}
        body = response.json()
        if not isinstance(body, dict):
            raise SeedError(f"{method} {path} returned a non-object response")
        return cast(JsonObject, body)

    def list_items(self, path: str) -> list[JsonObject]:
        items: list[JsonObject] = []
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": 100}
            if cursor is not None:
                params["cursor"] = cursor
            page = self.request("GET", path, params=params)
            page_items = page.get("items")
            if not isinstance(page_items, list):
                raise SeedError(f"GET {path} returned an invalid page")
            for item in page_items:
                if not isinstance(item, dict):
                    raise SeedError(f"GET {path} returned an invalid item")
                items.append(item)
            next_cursor = page.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                return items
            cursor = next_cursor


class AtlasExampleSeeder:
    """Create missing examples while preserving any pre-existing user data."""

    def __init__(self, api: AtlasApi, *, project_id: UUID) -> None:
        self._api = api
        self._project_id = project_id
        self._report = SeedReport()

    def run(self) -> SeedReport:
        environment = self._ensure_environment()
        role = self._ensure_role()
        connector = self._ensure_connector(environment)
        pool = self._ensure_account_pool(environment, role)
        self._ensure_test_account(pool, connector)
        atom_versions = {
            example.definition.atom_key: self._ensure_atom(example)
            for example in build_atom_examples()
        }
        blueprint_version = self._ensure_blueprint(
            keyword_atom_version_id=UUID(str(atom_versions["demo.web.search-keyword"]["id"])),
            expectation_atom_version_id=UUID(
                str(atom_versions["demo.web.search-expectation"]["id"])
            ),
        )
        atom_versions, blueprint_version = self._ensure_runtime_publication(
            environment=environment,
            role=role,
            atom_versions=atom_versions,
            blueprint_version=blueprint_version,
        )
        self._ensure_cases(role=role, blueprint_version=blueprint_version)
        return self._report

    def _ensure_environment(self) -> JsonObject:
        environments = self._api.list_items(
            f"/v1/projects/{self._project_id}/environments"
        )
        environment = _find_item(
            environments,
            "environmentKey",
            "local-public-web",
        )
        if environment is None:
            environment = self._api.request(
                "POST",
                f"/v1/projects/{self._project_id}/environments",
                payload={
                    "environmentKey": "local-public-web",
                    "name": "Local Public Web",
                    "kind": "TEST",
                    "allowedOrigins": [BAIDU_ORIGIN],
                },
                headers={"Idempotency-Key": "atlas-local-public-web-environment-v1"},
            )
            self._report.created.append("Environment local-public-web")
        else:
            self._report.reused.append("Environment local-public-web")
        if environment.get("kind") != "TEST" or environment.get("status") != "ACTIVE":
            raise SeedError("local-public-web Environment must be an ACTIVE TEST Environment")
        allowed_origins = environment.get("allowedOrigins")
        if not isinstance(allowed_origins, list) or BAIDU_ORIGIN not in allowed_origins:
            raise SeedError(
                "local-public-web Environment must allow the exact Baidu origin"
            )
        return environment

    def _ensure_connector(self, environment: JsonObject) -> JsonObject:
        environment_id = str(environment["id"])
        connectors = self._api.list_items(
            f"/v1/environments/{environment_id}/connector-installations"
        )
        connector = _find_item(
            connectors,
            "installationKey",
            "local-public-web",
        )
        if connector is None:
            connector = self._api.request(
                "POST",
                "/v1/connector-installations",
                payload={
                    "environmentId": environment_id,
                    "installationKey": "local-public-web",
                    "name": "Local Public Web Runtime",
                    "adapterKey": "generic-password",
                    "mode": "MANAGED_TEST_ACCOUNTS",
                    "configurationRef": "cfg_atlas_local_public_web",
                    "allowedOrigins": [BAIDU_ORIGIN],
                    "requiredCapabilities": ["auth.password"],
                },
                headers={"Idempotency-Key": "atlas-local-public-web-connector-v1"},
            )
            self._report.created.append("ConnectorInstallation local-public-web")
        else:
            self._report.reused.append("ConnectorInstallation local-public-web")
        if connector.get("healthState") != "HEALTHY":
            connector = self._api.request(
                "POST",
                f"/v1/connector-installations/{connector['id']}:validate",
                headers={"If-Match": _etag(connector)},
            )
            self._report.created.append("validated ConnectorInstallation local-public-web")
        return connector

    def _ensure_account_pool(
        self,
        environment: JsonObject,
        role: JsonObject,
    ) -> JsonObject:
        environment_id = str(environment["id"])
        pools = self._api.list_items(
            f"/v1/environments/{environment_id}/account-pools"
        )
        pool = _find_item(pools, "poolKey", "local-public-web")
        if pool is None:
            pool = self._api.request(
                "POST",
                f"/v1/environments/{environment_id}/account-pools",
                payload={
                    "roleId": role["id"],
                    "poolKey": "local-public-web",
                    "name": "Local Public Web",
                    "defaultTtlSeconds": 1800,
                    "cooldownSeconds": 0,
                },
                headers={"Idempotency-Key": "atlas-local-public-web-pool-v1"},
            )
            self._report.created.append("AccountPool local-public-web")
        else:
            self._report.reused.append("AccountPool local-public-web")
        return pool

    def _ensure_test_account(
        self,
        pool: JsonObject,
        connector: JsonObject,
    ) -> JsonObject:
        accounts = self._api.list_items(
            f"/v1/account-pools/{pool['id']}/accounts"
        )
        account = _find_item(accounts, "accountKey", "local-public-web-01")
        if account is None:
            account = self._api.request(
                "POST",
                f"/v1/account-pools/{pool['id']}/accounts",
                payload={
                    "connectorInstallationId": connector["id"],
                    "accountKey": "local-public-web-01",
                    "source": "ATLAS_MANAGED",
                    "loginHintMasked": "at***@example.test",
                    "labels": {"purpose": "local-public-web"},
                    "credentials": [
                        {
                            "authMethod": "PASSWORD",
                            "purpose": "LOGIN",
                            "secretRef": LOCAL_PUBLIC_WEB_SECRET_REF,
                            "secretVersion": LOCAL_PUBLIC_WEB_SECRET_VERSION,
                        }
                    ],
                },
                headers={"Idempotency-Key": "atlas-local-public-web-account-v1"},
            )
            self._report.created.append("TestAccount local-public-web-01")
        else:
            self._report.reused.append("TestAccount local-public-web-01")
        if account.get("lifecycleStatus") != "ACTIVE":
            account = self._api.request(
                "PATCH",
                f"/v1/test-accounts/{account['id']}",
                payload={"lifecycleStatus": "ACTIVE"},
                headers={"If-Match": _etag(account)},
            )
            self._report.created.append("activated TestAccount local-public-web-01")
        if account.get("available") is not True:
            verified = self._api.request(
                "POST",
                f"/v1/test-accounts/{account['id']}:verify",
                payload={"origin": BAIDU_ORIGIN},
                headers={
                    "Idempotency-Key": f"atlas-local-public-web-health-{uuid4()}",
                    "If-Match": _etag(account),
                },
            )
            verified_account = verified.get("account")
            if not isinstance(verified_account, dict):
                raise SeedError("account health response did not include an account")
            account = verified_account
            if account.get("available") is not True:
                raise SeedError("local public-web TestAccount did not become available")
            self._report.created.append("verified TestAccount local-public-web-01")
        return account

    def _ensure_role(self) -> JsonObject:
        roles = self._api.list_items(f"/v1/projects/{self._project_id}/test-roles")
        existing = _find_item(roles, "roleKey", ROLE_KEY)
        if existing is not None:
            self._report.reused.append(f"TestRole {ROLE_KEY}")
            return existing
        role = self._api.request(
            "POST",
            f"/v1/projects/{self._project_id}/test-roles",
            payload={
                "roleKey": ROLE_KEY,
                "name": "公共网页访客",
                "description": "无需登录即可访问公开网页并执行语义化搜索操作的测试角色。",
                "capabilities": [
                    "web.page.open",
                    "web.search.read",
                    "web.search.submit",
                ],
            },
            headers={"Idempotency-Key": "atlas-demo-public-web-role-v1"},
        )
        self._report.created.append(f"TestRole {ROLE_KEY}")
        return role

    def _ensure_atom(self, example: AtomExample) -> JsonObject:
        atom_key = example.definition.atom_key
        atoms = self._api.list_items(f"/v1/projects/{self._project_id}/data-atoms")
        definition = _find_item(atoms, "atomKey", atom_key)
        if definition is None:
            definition = self._api.request(
                "POST",
                f"/v1/projects/{self._project_id}/data-atoms",
                payload=_model_payload(example.definition),
                headers={"Idempotency-Key": f"atlas-{atom_key.replace('.', '-')}-definition-v1"},
            )
            self._report.created.append(f"DataAtom {atom_key}")
        else:
            self._report.reused.append(f"DataAtom {atom_key}")

        atom_id = str(definition["id"])
        versions = self._api.list_items(f"/v1/data-atoms/{atom_id}/versions")
        version = _find_item(versions, "version", ATOM_VERSION)
        expected_digest = fixture_digest(example.version.contract)
        if version is None:
            version = self._api.request(
                "POST",
                f"/v1/data-atoms/{atom_id}/versions",
                payload=_model_payload(example.version),
                headers={"Idempotency-Key": f"atlas-{atom_key.replace('.', '-')}-version-v1"},
            )
            self._report.created.append(f"DataAtomVersion {atom_key}@{ATOM_VERSION}")
        else:
            _require_digest(
                resource=f"DataAtomVersion {atom_key}@{ATOM_VERSION}",
                actual=version.get("contentDigest"),
                expected=expected_digest,
            )
            self._report.reused.append(f"DataAtomVersion {atom_key}@{ATOM_VERSION}")

        status = str(version["status"])
        if status == "DRAFT":
            version = self._api.request(
                "POST",
                f"/v1/data-atom-versions/{version['id']}:validate",
                headers={"If-Match": _etag(version)},
            )
            self._report.created.append(f"validated {atom_key}@{ATOM_VERSION}")
        elif status not in {"VALIDATED", "PUBLISHED"}:
            raise SeedError(
                f"DataAtomVersion {atom_key}@{ATOM_VERSION} has unsupported status {status}"
            )
        return version

    def _ensure_blueprint(
        self,
        *,
        keyword_atom_version_id: UUID,
        expectation_atom_version_id: UUID,
    ) -> JsonObject:
        definition_command, version_command = build_blueprint_commands(
            keyword_atom_version_id=keyword_atom_version_id,
            expectation_atom_version_id=expectation_atom_version_id,
        )
        blueprints = self._api.list_items(f"/v1/projects/{self._project_id}/data-blueprints")
        definition = _find_item(blueprints, "blueprintKey", BLUEPRINT_KEY)
        if definition is None:
            definition = self._api.request(
                "POST",
                f"/v1/projects/{self._project_id}/data-blueprints",
                payload=_model_payload(definition_command),
                headers={"Idempotency-Key": "atlas-demo-web-search-blueprint-v1"},
            )
            self._report.created.append(f"DataBlueprint {BLUEPRINT_KEY}")
        else:
            self._report.reused.append(f"DataBlueprint {BLUEPRINT_KEY}")

        blueprint_id = str(definition["id"])
        versions = self._api.list_items(f"/v1/data-blueprints/{blueprint_id}/versions")
        version = _find_item(versions, "version", BLUEPRINT_VERSION)
        expected_digest = fixture_digest(version_command.contract)
        if version is None:
            version = self._api.request(
                "POST",
                f"/v1/data-blueprints/{blueprint_id}/versions",
                payload=_model_payload(version_command),
                headers={"Idempotency-Key": "atlas-demo-web-search-blueprint-version-v1"},
            )
            self._report.created.append(f"DataBlueprintVersion {BLUEPRINT_VERSION_REF}")
        else:
            _require_digest(
                resource=f"DataBlueprintVersion {BLUEPRINT_VERSION_REF}",
                actual=version.get("contentDigest"),
                expected=expected_digest,
            )
            self._report.reused.append(f"DataBlueprintVersion {BLUEPRINT_VERSION_REF}")

        if version.get("compiledPlan") is None:
            response = self._api.request(
                "POST",
                f"/v1/data-blueprint-versions/{version['id']}:compile",
                headers={"If-Match": _etag(version)},
            )
            compilation = response.get("compilation")
            if not isinstance(compilation, dict) or compilation.get("valid") is not True:
                raise SeedError(f"DataBlueprintVersion {BLUEPRINT_VERSION_REF} did not compile")
            compiled_version = response.get("version")
            if not isinstance(compiled_version, dict):
                raise SeedError("Blueprint compile response did not include a version")
            version = compiled_version
            self._report.created.append(f"compiled {BLUEPRINT_VERSION_REF}")
        if str(version["status"]) not in {"VALIDATED", "PUBLISHED"}:
            raise SeedError(f"DataBlueprintVersion {BLUEPRINT_VERSION_REF} is not validated")
        return version

    def _ensure_runtime_publication(
        self,
        *,
        environment: JsonObject,
        role: JsonObject,
        atom_versions: dict[str, JsonObject],
        blueprint_version: JsonObject,
    ) -> tuple[dict[str, JsonObject], JsonObject]:
        resources = (*atom_versions.values(), blueprint_version)
        evidence_ready = all(
            item.get("runtimeValidationState") == "PASSED"
            and item.get("cleanupValidationState") == "PASSED"
            for item in resources
        )
        if not evidence_ready:
            self._run_fixture_validation(
                environment=environment,
                role=role,
                blueprint_version=blueprint_version,
            )
            atom_versions = {
                atom_key: self._api.request(
                    "GET",
                    f"/v1/data-atom-versions/{version['id']}",
                )
                for atom_key, version in atom_versions.items()
            }
            blueprint_version = self._api.request(
                "GET",
                f"/v1/data-blueprint-versions/{blueprint_version['id']}",
            )

        for atom_key, version in tuple(atom_versions.items()):
            if version.get("status") == "PUBLISHED":
                continue
            atom_versions[atom_key] = self._api.request(
                "POST",
                f"/v1/data-atom-versions/{version['id']}:publish",
                headers={"If-Match": _etag(version)},
            )
            self._report.created.append(
                f"published DataAtomVersion {atom_key}@{ATOM_VERSION}"
            )
        if blueprint_version.get("status") != "PUBLISHED":
            blueprint_version = self._api.request(
                "POST",
                f"/v1/data-blueprint-versions/{blueprint_version['id']}:publish",
                headers={"If-Match": _etag(blueprint_version)},
            )
            self._report.created.append(
                f"published DataBlueprintVersion {BLUEPRINT_VERSION_REF}"
            )
        return atom_versions, blueprint_version

    def _run_fixture_validation(
        self,
        *,
        environment: JsonObject,
        role: JsonObject,
        blueprint_version: JsonObject,
    ) -> None:
        now = datetime.now(UTC)
        execution_suffix = uuid4().hex
        execution_id = f"seed-validation:{execution_suffix}"
        lease = self._api.request(
            "POST",
            "/internal/v1/account-leases",
            payload={
                "executionId": execution_id,
                "workerId": "browser-worker-local",
                "environmentId": environment["id"],
                "roleKey": role["roleKey"],
                "requirements": {
                    "authMethods": ["PASSWORD"],
                    "capabilities": role["capabilities"],
                },
                "ttlSeconds": 1800,
                "executionDeadline": (now + timedelta(minutes=30)).isoformat(),
            },
            headers={"Idempotency-Key": f"seed-lease-{execution_suffix}"},
        )
        fixture = self._api.request(
            "POST",
            f"/v1/projects/{self._project_id}/fixture-runs",
            payload={
                "runKind": "VALIDATION",
                "blueprintVersionId": blueprint_version["id"],
                "environmentId": environment["id"],
                "executionId": execution_id,
                "inputs": {"keyword": "AAA"},
                "actorBindings": [
                    {
                        "actorSlot": "primary",
                        "accountLeaseId": lease["leaseId"],
                        "fencingToken": lease["fencingToken"],
                    }
                ],
                "executionDeadline": (now + timedelta(minutes=10)).isoformat(),
            },
            headers={"Idempotency-Key": f"seed-fixture-{execution_suffix}"},
        )
        ready = self._wait_for_fixture_status(
            str(fixture["id"]),
            {"READY"},
            timeout_seconds=60,
        )
        self._api.request(
            "POST",
            f"/v1/fixture-runs/{ready['id']}:release",
        )
        self._wait_for_fixture_status(
            str(ready["id"]),
            {"RELEASED"},
            timeout_seconds=60,
        )
        self._report.created.append(
            f"runtime and cleanup evidence for {BLUEPRINT_VERSION_REF}"
        )

    def _wait_for_fixture_status(
        self,
        fixture_run_id: str,
        expected: set[str],
        *,
        timeout_seconds: int,
    ) -> JsonObject:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            detail = self._api.request(
                "GET",
                f"/v1/fixture-runs/{fixture_run_id}",
            )
            run = detail.get("run")
            if not isinstance(run, dict):
                raise SeedError("FixtureRun detail did not include a run")
            status = str(run.get("status"))
            if status in expected:
                return run
            if status in {"FAILED", "CANCELED", "CLEANUP_FAILED"}:
                raise SeedError(
                    f"FixtureRun {fixture_run_id} ended in unexpected status {status}"
                )
            time.sleep(0.25)
        raise SeedError(
            f"FixtureRun {fixture_run_id} did not reach {sorted(expected)}"
        )

    def _ensure_cases(
        self,
        *,
        role: JsonObject,
        blueprint_version: JsonObject,
    ) -> None:
        existing_cases = self._api.list_items(f"/v1/projects/{self._project_id}/test-cases")
        examples = (
            ("WEB-BAIDU-SEARCH-AAA", "百度搜索 AAA", "AAA"),
            ("WEB-BAIDU-SEARCH-CODEX", "百度搜索 Codex", "Codex"),
            (
                "WEB-BAIDU-SEARCH-ATLAS",
                "百度搜索 Atlas AI TestOps",
                "Atlas AI TestOps",
            ),
        )
        for case_key, name, keyword in examples:
            if _find_item(existing_cases, "caseKey", case_key) is not None:
                self._report.reused.append(f"TestCase {case_key}")
                continue
            command = build_search_case_command(
                case_key=case_key,
                name=name,
                keyword=keyword,
                role=role,
                blueprint_version=blueprint_version,
            )
            self._api.request(
                "POST",
                f"/v1/projects/{self._project_id}/test-cases",
                payload=_model_payload(command),
                headers={"Idempotency-Key": f"atlas-demo-{case_key.casefold()}-v1"},
            )
            self._report.created.append(f"TestCase {case_key}")


def _find_item(
    items: list[JsonObject],
    field_name: str,
    expected: str,
) -> JsonObject | None:
    return next((item for item in items if item.get(field_name) == expected), None)


def _model_payload(model: object) -> JsonObject:
    if not hasattr(model, "model_dump"):
        raise TypeError("seed payload must be a Pydantic model")
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    return cast(JsonObject, payload)


def _etag(resource: JsonObject) -> str:
    revision = resource.get("revision")
    if not isinstance(revision, int) or revision < 1:
        raise SeedError("resource response does not contain a valid revision")
    return f'"revision-{revision}"'


def _require_digest(
    *,
    resource: str,
    actual: JsonValue | None,
    expected: str,
) -> None:
    if actual != expected:
        raise SeedError(
            f"{resource} already exists with different content; "
            "use a new version instead of overwriting local data"
        )


def _uuid_argument(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a UUID") from error


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI without touching process-global state."""

    parser = argparse.ArgumentParser(
        description="Seed reusable local Atlas fixture assets and browser examples."
    )
    parser.add_argument(
        "--api-origin",
        default=os.environ.get("ATLAS_API_ORIGIN", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--tenant-id",
        type=_uuid_argument,
        default=os.environ.get("NEXT_PUBLIC_ATLAS_TENANT_ID"),
        required=os.environ.get("NEXT_PUBLIC_ATLAS_TENANT_ID") is None,
    )
    parser.add_argument(
        "--project-id",
        type=_uuid_argument,
        default=os.environ.get("NEXT_PUBLIC_ATLAS_PROJECT_ID"),
        required=os.environ.get("NEXT_PUBLIC_ATLAS_PROJECT_ID") is None,
    )
    parser.add_argument("--actor-id", type=_uuid_argument)
    return parser


def main() -> None:
    """Run the local seed command and print a compact result summary."""

    args = build_parser().parse_args()
    tenant_id = cast(UUID, args.tenant_id)
    project_id = cast(UUID, args.project_id)
    actor_id = cast(
        UUID,
        args.actor_id or uuid5(NAMESPACE_URL, f"atlas-local-examples:{tenant_id}:{project_id}"),
    )
    api_origin = str(args.api_origin).rstrip("/")
    with httpx2.Client(base_url=api_origin, timeout=30.0) as client:
        report = AtlasExampleSeeder(
            AtlasApi(client, tenant_id=tenant_id, actor_id=actor_id),
            project_id=project_id,
        ).run()

    print("Atlas 本地示例初始化完成。")
    for item in report.created:
        print(f"  CREATED  {item}")
    for item in report.reused:
        print(f"  REUSED   {item}")


__all__ = [
    "ATOM_VERSION",
    "BLUEPRINT_KEY",
    "BLUEPRINT_VERSION",
    "BLUEPRINT_VERSION_REF",
    "ROLE_KEY",
    "SURFACE_KEY",
    "AtlasApi",
    "AtlasExampleSeeder",
    "AtomExample",
    "SeedError",
    "SeedReport",
    "build_atom_examples",
    "build_blueprint_commands",
    "build_parser",
    "build_search_case_command",
    "main",
]
