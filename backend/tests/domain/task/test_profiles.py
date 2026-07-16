"""Task profile version digest, lifecycle, and secret-boundary contracts."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator
from pydantic import JsonValue, ValidationError

from atlas_testops.domain.case.models import canonical_digest
from atlas_testops.domain.runtime.models import (
    ModelExecutionProfile,
    ToolExecutionProfile,
    Viewport,
)
from atlas_testops.domain.task.profiles import (
    BROWSER_PROFILE_SCHEMA_VERSION,
    DATA_PROFILE_SCHEMA_VERSION,
    EXECUTION_PROFILE_SCHEMA_VERSION,
    IDENTITY_PROFILE_SCHEMA_VERSION,
    BrowserProfileVersion,
    DataProfileVersion,
    ExecutionProfileVersion,
    IdentityActorBinding,
    IdentityProfileVersion,
    TaskProfileStatus,
    browser_profile_content_digest,
    browser_profile_version_ref,
    data_profile_content_digest,
    data_profile_version_ref,
    execution_profile_content_digest,
    execution_profile_version_ref,
    identity_profile_content_digest,
    identity_profile_version_ref,
)

NOW = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
DIGEST_D = "sha256:" + "d" * 64


def uid(value: int) -> UUID:
    """Return a deterministic UUID for contract fixtures."""

    return UUID(int=value)


def model_profile() -> ModelExecutionProfile:
    """Build a frozen model and Prompt profile."""

    return ModelExecutionProfile(
        model_profile_ref="model/default@1.0.0",
        prompt_bundle_ref="prompt/regression@1.2.0",
        reasoning_policy_ref="reasoning/bounded@1.0.0",
    )


def tool_profile() -> ToolExecutionProfile:
    """Build a frozen tool and policy profile."""

    return ToolExecutionProfile(
        tool_catalog_ref="tools/browser@2.0.0",
        mcp_server_manifest_digest=DIGEST_A,
        tool_schema_digest=DIGEST_B,
        policy_bundle_ref="policy/browser-safe@1.0.0",
        policy_digest=DIGEST_C,
    )


def common_payload(
    *,
    profile_key: str,
    version_ref: str,
    content_digest: str,
) -> dict[str, Any]:
    """Build shared camelCase publication metadata."""

    return {
        "id": str(uid(1)),
        "tenantId": str(uid(2)),
        "projectId": str(uid(3)),
        "profileKey": profile_key,
        "version": "1.2.0",
        "versionRef": version_ref,
        "status": TaskProfileStatus.PUBLISHED,
        "contentDigest": content_digest,
        "publishedBy": str(uid(4)),
        "publishedAt": NOW + timedelta(minutes=1),
        "revision": 1,
        "createdAt": NOW,
        "updatedAt": NOW + timedelta(minutes=1),
    }


def execution_payload() -> dict[str, Any]:
    """Build one valid ExecutionProfileVersion wire payload."""

    profile_key = "case-runtime"
    version = "1.2.0"
    features = ("trace", "aria", "trace")
    digest = execution_profile_content_digest(
        tenant_id=uid(2),
        project_id=uid(3),
        profile_key=profile_key,
        version=version,
        case_version_id=uid(10),
        case_content_digest=DIGEST_A,
        test_ir_digest=DIGEST_B,
        plan_digest=DIGEST_C,
        compiled_digest=DIGEST_D,
        model=model_profile(),
        tools=tool_profile(),
        supported_features=features,
    )
    return {
        "schemaVersion": EXECUTION_PROFILE_SCHEMA_VERSION,
        **common_payload(
            profile_key=profile_key,
            version_ref=execution_profile_version_ref(profile_key, version),
            content_digest=digest,
        ),
        "caseVersionId": str(uid(10)),
        "caseContentDigest": DIGEST_A,
        "testIrDigest": DIGEST_B,
        "planDigest": DIGEST_C,
        "compiledDigest": DIGEST_D,
        "model": model_profile().model_dump(mode="json", by_alias=True),
        "tools": tool_profile().model_dump(mode="json", by_alias=True),
        "supportedFeatures": list(features),
    }


def actors() -> tuple[IdentityActorBinding, ...]:
    """Build actor bindings in deliberately non-canonical order."""

    return (
        IdentityActorBinding(
            actor_slot="reviewer",
            role_id=uid(22),
            role_key="reviewer",
            role_revision=2,
            capabilities=("Review.Approve", "review.approve"),
        ),
        IdentityActorBinding(
            actor_slot="author",
            role_id=uid(21),
            role_key="author",
            role_revision=3,
            capabilities=("document.write",),
        ),
    )


def identity_payload() -> dict[str, Any]:
    """Build one valid IdentityProfileVersion wire payload."""

    profile_key = "case-actors"
    version = "1.2.0"
    bindings = actors()
    digest = identity_profile_content_digest(
        tenant_id=uid(2),
        project_id=uid(3),
        profile_key=profile_key,
        version=version,
        case_version_id=uid(10),
        case_content_digest=DIGEST_A,
        actors=bindings,
    )
    return {
        "schemaVersion": IDENTITY_PROFILE_SCHEMA_VERSION,
        **common_payload(
            profile_key=profile_key,
            version_ref=identity_profile_version_ref(profile_key, version),
            content_digest=digest,
        ),
        "caseVersionId": str(uid(10)),
        "caseContentDigest": DIGEST_A,
        "actors": [actor.model_dump(mode="json", by_alias=True) for actor in bindings],
    }


def browser_payload() -> dict[str, Any]:
    """Build one valid BrowserProfileVersion wire payload."""

    profile_key = "desktop-chromium"
    version = "1.2.0"
    viewport = Viewport(width=1440, height=900, device_scale_factor=2)
    digest = browser_profile_content_digest(
        tenant_id=uid(2),
        project_id=uid(3),
        profile_key=profile_key,
        version=version,
        engine="chromium",
        revision="chromium-140.0.7339.41",
        viewport=viewport,
        locale="zh-CN",
        timezone="Asia/Shanghai",
        runtime_image_digest=DIGEST_A,
        capability_digest=None,
    )
    return {
        "schemaVersion": BROWSER_PROFILE_SCHEMA_VERSION,
        **common_payload(
            profile_key=profile_key,
            version_ref=browser_profile_version_ref(profile_key, version),
            content_digest=digest,
        ),
        "engine": "chromium",
        "browserRevision": "chromium-140.0.7339.41",
        "viewport": viewport.model_dump(mode="json", by_alias=True),
        "locale": "zh-CN",
        "timezone": "Asia/Shanghai",
        "runtimeImageDigest": DIGEST_A,
        "capabilityDigest": None,
    }


def data_payload() -> dict[str, Any]:
    """Build one valid DataProfileVersion wire payload."""

    profile_key = "checkout-basic"
    version = "1.2.0"
    run_inputs: dict[str, JsonValue] = {"quantity": 2, "region": "cn-east"}
    input_digest = canonical_digest(run_inputs)
    digest = data_profile_content_digest(
        tenant_id=uid(2),
        project_id=uid(3),
        profile_key=profile_key,
        version=version,
        blueprint_version_id=uid(30),
        blueprint_version_ref="fixture/checkout@1.0.0",
        blueprint_content_digest=DIGEST_A,
        plan_digest=DIGEST_B,
        run_inputs=run_inputs,
        input_digest=input_digest,
    )
    return {
        "schemaVersion": DATA_PROFILE_SCHEMA_VERSION,
        **common_payload(
            profile_key=profile_key,
            version_ref=data_profile_version_ref(profile_key, version),
            content_digest=digest,
        ),
        "blueprintVersionId": str(uid(30)),
        "blueprintVersionRef": "fixture/checkout@1.0.0",
        "blueprintContentDigest": DIGEST_A,
        "planDigest": DIGEST_B,
        "runInputs": run_inputs,
        "inputDigest": input_digest,
    }


def test_execution_profile_normalizes_features_and_uses_camel_case() -> None:
    profile = ExecutionProfileVersion.model_validate(execution_payload())

    assert profile.supported_features == ("aria", "trace")
    assert profile.model_dump(mode="json")["schemaVersion"] == (
        EXECUTION_PROFILE_SCHEMA_VERSION
    )
    assert profile.model_dump(mode="json")["supportedFeatures"] == ["aria", "trace"]


def test_identity_profile_normalizes_actor_bindings_without_runtime_identity() -> None:
    profile = IdentityProfileVersion.model_validate(identity_payload())

    assert tuple(actor.actor_slot for actor in profile.actors) == ("author", "reviewer")
    assert profile.actors[1].capabilities == ("review.approve",)
    actor_wire = profile.actors[0].model_dump(mode="json")
    assert set(actor_wire) == {
        "actorSlot",
        "roleId",
        "roleKey",
        "roleRevision",
        "capabilities",
    }


def test_browser_profile_accepts_either_attestation_digest() -> None:
    image_profile = BrowserProfileVersion.model_validate(browser_payload())
    capability_payload = browser_payload()
    capability_payload["runtimeImageDigest"] = None
    capability_payload["capabilityDigest"] = DIGEST_B
    capability_payload["contentDigest"] = browser_profile_content_digest(
        tenant_id=uid(2),
        project_id=uid(3),
        profile_key="desktop-chromium",
        version="1.2.0",
        engine="chromium",
        revision="chromium-140.0.7339.41",
        viewport=Viewport(width=1440, height=900, device_scale_factor=2),
        locale="zh-CN",
        timezone="Asia/Shanghai",
        runtime_image_digest=None,
        capability_digest=DIGEST_B,
    )

    capability_profile = BrowserProfileVersion.model_validate(capability_payload)

    assert image_profile.runtime_image_digest == DIGEST_A
    assert capability_profile.capability_digest == DIGEST_B


def test_data_profile_requires_canonical_input_and_profile_digests() -> None:
    profile = DataProfileVersion.model_validate(data_payload())

    assert profile.input_digest == canonical_digest({"region": "cn-east", "quantity": 2})
    assert profile.model_dump(mode="json")["runInputs"] == {
        "quantity": 2,
        "region": "cn-east",
    }

    wrong_input = data_payload()
    wrong_input["inputDigest"] = DIGEST_D
    with pytest.raises(ValidationError, match="inputDigest must match"):
        DataProfileVersion.model_validate(wrong_input)


@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [
        (ExecutionProfileVersion, execution_payload),
        (IdentityProfileVersion, identity_payload),
        (BrowserProfileVersion, browser_payload),
        (DataProfileVersion, data_payload),
    ],
)
def test_every_profile_rejects_content_digest_drift(
    model_type: type[
        ExecutionProfileVersion
        | IdentityProfileVersion
        | BrowserProfileVersion
        | DataProfileVersion
    ],
    payload_factory: Any,
) -> None:
    payload = payload_factory()
    payload["contentDigest"] = DIGEST_D if payload["contentDigest"] != DIGEST_D else DIGEST_C

    with pytest.raises(ValidationError, match="contentDigest must match"):
        model_type.model_validate(payload)


@pytest.mark.parametrize(
    ("model_type", "payload_factory"),
    [
        (ExecutionProfileVersion, execution_payload),
        (IdentityProfileVersion, identity_payload),
        (BrowserProfileVersion, browser_payload),
        (DataProfileVersion, data_payload),
    ],
)
def test_every_profile_rejects_non_exact_version_reference(
    model_type: type[
        ExecutionProfileVersion
        | IdentityProfileVersion
        | BrowserProfileVersion
        | DataProfileVersion
    ],
    payload_factory: Any,
) -> None:
    payload = payload_factory()
    payload["versionRef"] = "other/profile@1.2.0"

    with pytest.raises(ValidationError, match="versionRef must be the exact"):
        model_type.model_validate(payload)


@pytest.mark.parametrize(
    ("payload_path", "sensitive_key"),
    [
        (("runInputs", "nested"), "password"),
        (("runInputs", "nested"), "apiKey"),
        (("runInputs",), "accountId"),
        (("runInputs",), "lease_id"),
        (("runInputs",), "browserSessionRef"),
        (("runInputs",), "storageState"),
        (("runInputs",), "tokensUsed"),
    ],
)
def test_data_profile_rejects_nested_sensitive_fields(
    payload_path: tuple[str, ...],
    sensitive_key: str,
) -> None:
    payload = data_payload()
    target: dict[str, Any] = payload
    for part in payload_path:
        if part not in target:
            target[part] = {}
        target = target[part]
    target[sensitive_key] = "must-not-enter-profile"

    with pytest.raises(ValidationError, match="task profile contains sensitive field"):
        DataProfileVersion.model_validate(payload)


def test_identity_actor_rejects_account_or_lease_fields_before_extra_fields() -> None:
    payload = actors()[0].model_dump(mode="json")
    payload["accountLeaseId"] = str(uid(99))

    with pytest.raises(ValidationError, match="task profile contains sensitive field"):
        IdentityActorBinding.model_validate(payload)


def test_identity_profile_rejects_duplicate_actor_slots() -> None:
    payload = identity_payload()
    payload["actors"][1]["actorSlot"] = payload["actors"][0]["actorSlot"]

    with pytest.raises(ValidationError, match="actor slots must be unique"):
        IdentityProfileVersion.model_validate(payload)


def test_execution_profile_rejects_invalid_supported_feature() -> None:
    payload = execution_payload()
    payload["supportedFeatures"] = ["invalid feature"]

    with pytest.raises(ValidationError, match="invalid feature key"):
        ExecutionProfileVersion.model_validate(payload)


def test_browser_profile_requires_runtime_attestation() -> None:
    payload = browser_payload()
    payload["runtimeImageDigest"] = None

    with pytest.raises(ValidationError, match="requires a runtime image"):
        BrowserProfileVersion.model_validate(payload)


def test_browser_profile_schema_requires_non_null_runtime_attestation() -> None:
    schema = BrowserProfileVersion.model_json_schema(by_alias=True)
    payload = browser_payload()
    payload["runtimeImageDigest"] = None
    payload["capabilityDigest"] = None

    errors = tuple(Draft202012Validator(schema).iter_errors(payload))

    assert errors
    assert schema["x-atlas-invariants"] == [
        "At least one runtimeImageDigest or capabilityDigest must be non-null."
    ]


def test_profile_lifecycle_metadata_is_orthogonal_to_content_digest() -> None:
    published = ExecutionProfileVersion.model_validate(execution_payload())
    deprecated_payload = execution_payload()
    deprecated_payload.update(
        {
            "status": TaskProfileStatus.DEPRECATED,
            "deprecatedAt": NOW + timedelta(minutes=2),
            "updatedAt": NOW + timedelta(minutes=2),
            "revision": 2,
        }
    )
    revoked_payload = execution_payload()
    revoked_payload.update(
        {
            "status": TaskProfileStatus.REVOKED,
            "deprecatedAt": NOW + timedelta(minutes=2),
            "revokedAt": NOW + timedelta(minutes=3),
            "updatedAt": NOW + timedelta(minutes=3),
            "revision": 3,
        }
    )

    deprecated = ExecutionProfileVersion.model_validate(deprecated_payload)
    revoked = ExecutionProfileVersion.model_validate(revoked_payload)

    assert published.content_digest == deprecated.content_digest == revoked.content_digest


@pytest.mark.parametrize(
    "updates",
    [
        {"status": TaskProfileStatus.PUBLISHED, "deprecatedAt": NOW},
        {"status": TaskProfileStatus.DEPRECATED},
        {"status": TaskProfileStatus.DEPRECATED, "revokedAt": NOW},
        {"status": TaskProfileStatus.REVOKED},
        {
            "status": TaskProfileStatus.REVOKED,
            "deprecatedAt": NOW + timedelta(minutes=3),
            "revokedAt": NOW + timedelta(minutes=2),
            "updatedAt": NOW + timedelta(minutes=3),
        },
        {"publishedAt": NOW - timedelta(seconds=1)},
    ],
)
def test_profile_lifecycle_rejects_inconsistent_metadata(
    updates: dict[str, object],
) -> None:
    payload = execution_payload()
    payload.update(updates)

    with pytest.raises(ValidationError):
        ExecutionProfileVersion.model_validate(payload)
