"""Immutable version hosts for formal Task execution profiles."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.case.models import (
    DIGEST_PATTERN,
    SemanticVersion,
    canonical_digest,
)
from atlas_testops.domain.runtime.models import (
    ModelExecutionProfile,
    ToolExecutionProfile,
    Viewport,
)
from atlas_testops.domain.workflow import ExactVersionRef

EXECUTION_PROFILE_SCHEMA_VERSION: Literal["atlas.execution-profile/0.1"] = (
    "atlas.execution-profile/0.1"
)
IDENTITY_PROFILE_SCHEMA_VERSION: Literal["atlas.identity-profile/0.1"] = (
    "atlas.identity-profile/0.1"
)
BROWSER_PROFILE_SCHEMA_VERSION: Literal["atlas.browser-profile/0.1"] = (
    "atlas.browser-profile/0.1"
)
DATA_PROFILE_SCHEMA_VERSION: Literal["atlas.data-profile/0.1"] = (
    "atlas.data-profile/0.1"
)

PROFILE_KEY_PATTERN = r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,7}$"
FEATURE_KEY_PATTERN = r"^[a-z][a-z0-9._:-]{1,127}$"
ACTOR_SLOT_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.-]{1,79}$"
ROLE_KEY_PATTERN = r"^[a-z][a-z0-9._-]{1,79}$"

ProfileKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=80,
        pattern=PROFILE_KEY_PATTERN,
    ),
]

_SENSITIVE_FIELD_TOKENS = frozenset(
    {
        "account",
        "authorization",
        "cookie",
        "credential",
        "lease",
        "login",
        "otp",
        "passwd",
        "password",
        "secret",
        "session",
        "token",
        "totp",
    }
)
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "apikey",
        "accesskey",
        "privatekey",
        "storagestate",
    }
)


class TaskProfileStatus(StrEnum):
    """Admission status shared by every immutable Task profile version."""

    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    REVOKED = "REVOKED"


def _profile_version_ref(kind: str, profile_key: str, version: str) -> str:
    """Build one exact profile reference inside a tenant and project scope."""

    return f"{kind}-profile/{profile_key}@{version}"


def execution_profile_version_ref(profile_key: str, version: str) -> str:
    """Build an exact ExecutionProfileVersion reference."""

    return _profile_version_ref("execution", profile_key, version)


def identity_profile_version_ref(profile_key: str, version: str) -> str:
    """Build an exact IdentityProfileVersion reference."""

    return _profile_version_ref("identity", profile_key, version)


def browser_profile_version_ref(profile_key: str, version: str) -> str:
    """Build an exact BrowserProfileVersion reference."""

    return _profile_version_ref("browser", profile_key, version)


def data_profile_version_ref(profile_key: str, version: str) -> str:
    """Build an exact DataProfileVersion reference."""

    return _profile_version_ref("data", profile_key, version)


def _profile_content_digest(
    *,
    schema_version: str,
    tenant_id: UUID,
    project_id: UUID,
    profile_key: str,
    version: str,
    version_ref: str,
    contract: dict[str, JsonValue],
) -> str:
    """Digest immutable profile content without mutable admission status."""

    body: dict[str, JsonValue] = {
        "schemaVersion": schema_version,
        "tenantId": str(tenant_id),
        "projectId": str(project_id),
        "profileKey": profile_key,
        "version": version,
        "versionRef": version_ref,
        "contract": contract,
    }
    return canonical_digest(body)


def execution_profile_content_digest(
    *,
    tenant_id: UUID,
    project_id: UUID,
    profile_key: str,
    version: str,
    case_version_id: UUID,
    case_content_digest: str,
    test_ir_digest: str,
    plan_digest: str,
    compiled_digest: str,
    model: ModelExecutionProfile,
    tools: ToolExecutionProfile,
    supported_features: tuple[str, ...],
) -> str:
    """Digest every frozen ExecutionProfileVersion input."""

    version_ref = execution_profile_version_ref(profile_key, version)
    normalized_features: list[JsonValue] = [
        feature
        for feature in sorted(
            {item.strip().casefold() for item in supported_features}
        )
    ]
    contract: dict[str, JsonValue] = {
        "caseVersionId": str(case_version_id),
        "caseContentDigest": case_content_digest,
        "testIrDigest": test_ir_digest,
        "planDigest": plan_digest,
        "compiledDigest": compiled_digest,
        "model": model.model_dump(mode="json", by_alias=True),
        "tools": tools.model_dump(mode="json", by_alias=True),
        "supportedFeatures": normalized_features,
    }
    return _profile_content_digest(
        schema_version=EXECUTION_PROFILE_SCHEMA_VERSION,
        tenant_id=tenant_id,
        project_id=project_id,
        profile_key=profile_key,
        version=version,
        version_ref=version_ref,
        contract=contract,
    )


class _SensitiveFieldGuard(FrozenWireModel):
    """Reject secret-bearing field shapes before profile parsing."""

    @model_validator(mode="before")
    @classmethod
    def reject_sensitive_fields(cls, value: object) -> object:
        """Keep credentials, accounts, leases, and sessions outside profiles."""

        _reject_sensitive_field_names(value)
        return value


class _TaskProfileVersion(_SensitiveFieldGuard):
    """Common identity and lifecycle facts for one profile version host."""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    profile_key: ProfileKey
    version: SemanticVersion
    version_ref: ExactVersionRef
    status: TaskProfileStatus
    content_digest: str = Field(pattern=DIGEST_PATTERN)
    published_by: UUID
    published_at: AwareDatetime
    deprecated_at: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        """Require status metadata and timestamps to describe one valid history."""

        if not self.created_at <= self.published_at <= self.updated_at:
            raise ValueError("publishedAt must be between createdAt and updatedAt")
        if self.deprecated_at is not None and not (
            self.published_at <= self.deprecated_at <= self.updated_at
        ):
            raise ValueError("deprecatedAt must be between publishedAt and updatedAt")
        if self.revoked_at is not None and not (
            self.published_at <= self.revoked_at <= self.updated_at
        ):
            raise ValueError("revokedAt must be between publishedAt and updatedAt")
        if (
            self.deprecated_at is not None
            and self.revoked_at is not None
            and self.deprecated_at > self.revoked_at
        ):
            raise ValueError("deprecatedAt cannot follow revokedAt")
        if self.status is TaskProfileStatus.PUBLISHED:
            if self.deprecated_at is not None or self.revoked_at is not None:
                raise ValueError("published profile cannot contain terminal metadata")
        elif self.status is TaskProfileStatus.DEPRECATED:
            if self.deprecated_at is None or self.revoked_at is not None:
                raise ValueError("deprecated profile requires deprecatedAt only")
        elif self.revoked_at is None:
            raise ValueError("revoked profile requires revokedAt")
        return self


class ExecutionProfileVersion(_TaskProfileVersion):
    """Published Case, model, Prompt, and tool execution snapshot."""

    schema_version: Literal["atlas.execution-profile/0.1"] = (
        EXECUTION_PROFILE_SCHEMA_VERSION
    )
    case_version_id: UUID
    case_content_digest: str = Field(pattern=DIGEST_PATTERN)
    test_ir_digest: str = Field(pattern=DIGEST_PATTERN)
    plan_digest: str = Field(pattern=DIGEST_PATTERN)
    compiled_digest: str = Field(pattern=DIGEST_PATTERN)
    model: ModelExecutionProfile
    tools: ToolExecutionProfile
    supported_features: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("supported_features")
    @classmethod
    def normalize_supported_features(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Canonicalize reviewed runtime feature keys."""

        normalized = tuple(sorted({value.strip().casefold() for value in values}))
        if any(re.fullmatch(FEATURE_KEY_PATTERN, value) is None for value in normalized):
            raise ValueError("supportedFeatures contains an invalid feature key")
        return normalized

    @model_validator(mode="after")
    def validate_reference_and_digest(self) -> Self:
        """Reject aliases or content that differ from the frozen Case binding."""

        expected_ref = execution_profile_version_ref(self.profile_key, self.version)
        if self.version_ref != expected_ref:
            raise ValueError("versionRef must be the exact ExecutionProfileVersion reference")
        expected_digest = execution_profile_content_digest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            profile_key=self.profile_key,
            version=self.version,
            case_version_id=self.case_version_id,
            case_content_digest=self.case_content_digest,
            test_ir_digest=self.test_ir_digest,
            plan_digest=self.plan_digest,
            compiled_digest=self.compiled_digest,
            model=self.model,
            tools=self.tools,
            supported_features=self.supported_features,
        )
        if self.content_digest != expected_digest:
            raise ValueError("contentDigest must match the ExecutionProfileVersion")
        return self


class IdentityActorBinding(_SensitiveFieldGuard):
    """Exact Case actor role binding without account or credential material."""

    actor_slot: str = Field(min_length=2, max_length=80, pattern=ACTOR_SLOT_PATTERN)
    role_id: UUID
    role_key: str = Field(min_length=2, max_length=80, pattern=ROLE_KEY_PATTERN)
    role_revision: int = Field(ge=1)
    capabilities: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("capabilities")
    @classmethod
    def normalize_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Canonicalize the exact capabilities expected by the Case actor."""

        normalized = tuple(sorted({item.strip().casefold() for item in values}))
        if any(
            not item or len(item) > 128 or not item[0].isalpha()
            for item in normalized
        ):
            raise ValueError("actor capabilities are invalid")
        return normalized


def identity_profile_content_digest(
    *,
    tenant_id: UUID,
    project_id: UUID,
    profile_key: str,
    version: str,
    case_version_id: UUID,
    case_content_digest: str,
    actors: tuple[IdentityActorBinding, ...],
) -> str:
    """Digest every frozen IdentityProfileVersion actor binding."""

    version_ref = identity_profile_version_ref(profile_key, version)
    contract: dict[str, JsonValue] = {
        "caseVersionId": str(case_version_id),
        "caseContentDigest": case_content_digest,
        "actors": [
            actor.model_dump(mode="json", by_alias=True)
            for actor in sorted(actors, key=lambda item: item.actor_slot)
        ],
    }
    return _profile_content_digest(
        schema_version=IDENTITY_PROFILE_SCHEMA_VERSION,
        tenant_id=tenant_id,
        project_id=project_id,
        profile_key=profile_key,
        version=version,
        version_ref=version_ref,
        contract=contract,
    )


class IdentityProfileVersion(_TaskProfileVersion):
    """Published exact Case actor-role snapshot without runtime identities."""

    schema_version: Literal["atlas.identity-profile/0.1"] = IDENTITY_PROFILE_SCHEMA_VERSION
    case_version_id: UUID
    case_content_digest: str = Field(pattern=DIGEST_PATTERN)
    actors: tuple[IdentityActorBinding, ...] = Field(min_length=1, max_length=8)

    @field_validator("actors")
    @classmethod
    def normalize_actors(
        cls,
        values: tuple[IdentityActorBinding, ...],
    ) -> tuple[IdentityActorBinding, ...]:
        """Require one deterministic binding for every actor slot."""

        slots = [actor.actor_slot for actor in values]
        if len(slots) != len(set(slots)):
            raise ValueError("identity actor slots must be unique")
        return tuple(sorted(values, key=lambda actor: actor.actor_slot))

    @model_validator(mode="after")
    def validate_reference_and_digest(self) -> Self:
        """Reject aliases or content that differ from exact actor bindings."""

        expected_ref = identity_profile_version_ref(self.profile_key, self.version)
        if self.version_ref != expected_ref:
            raise ValueError("versionRef must be the exact IdentityProfileVersion reference")
        expected_digest = identity_profile_content_digest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            profile_key=self.profile_key,
            version=self.version,
            case_version_id=self.case_version_id,
            case_content_digest=self.case_content_digest,
            actors=self.actors,
        )
        if self.content_digest != expected_digest:
            raise ValueError("contentDigest must match the IdentityProfileVersion")
        return self


def browser_profile_content_digest(
    *,
    tenant_id: UUID,
    project_id: UUID,
    profile_key: str,
    version: str,
    engine: str,
    revision: str,
    viewport: Viewport,
    locale: str,
    timezone: str,
    runtime_image_digest: str | None,
    capability_digest: str | None,
) -> str:
    """Digest every frozen BrowserProfileVersion runtime requirement."""

    version_ref = browser_profile_version_ref(profile_key, version)
    contract: dict[str, JsonValue] = {
        "engine": engine,
        "revision": revision,
        "viewport": viewport.model_dump(mode="json", by_alias=True),
        "locale": locale,
        "timezone": timezone,
        "runtimeImageDigest": runtime_image_digest,
        "capabilityDigest": capability_digest,
    }
    return _profile_content_digest(
        schema_version=BROWSER_PROFILE_SCHEMA_VERSION,
        tenant_id=tenant_id,
        project_id=project_id,
        profile_key=profile_key,
        version=version,
        version_ref=version_ref,
        contract=contract,
    )


class BrowserProfileVersion(_TaskProfileVersion):
    """Published browser binary, viewport, locale, and attestation snapshot."""

    model_config = ConfigDict(
        json_schema_extra={
            "anyOf": [
                {
                    "required": ["runtimeImageDigest"],
                    "properties": {"runtimeImageDigest": {"type": "string"}},
                },
                {
                    "required": ["capabilityDigest"],
                    "properties": {"capabilityDigest": {"type": "string"}},
                },
            ],
            "x-atlas-invariants": [
                "At least one runtimeImageDigest or capabilityDigest must be non-null."
            ],
        }
    )

    schema_version: Literal["atlas.browser-profile/0.1"] = BROWSER_PROFILE_SCHEMA_VERSION
    engine: Literal["chromium"] = "chromium"
    browser_revision: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/+=-]{0,159}$",
    )
    viewport: Viewport
    locale: str = Field(
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$",
    )
    timezone: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_+./-]+$",
    )
    runtime_image_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    capability_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_attestation_reference_and_digest(self) -> Self:
        """Require one runtime attestation and the exact frozen profile digest."""

        if self.runtime_image_digest is None and self.capability_digest is None:
            raise ValueError("browser profile requires a runtime image or capability digest")
        expected_ref = browser_profile_version_ref(self.profile_key, self.version)
        if self.version_ref != expected_ref:
            raise ValueError("versionRef must be the exact BrowserProfileVersion reference")
        expected_digest = browser_profile_content_digest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            profile_key=self.profile_key,
            version=self.version,
            engine=self.engine,
            revision=self.browser_revision,
            viewport=self.viewport,
            locale=self.locale,
            timezone=self.timezone,
            runtime_image_digest=self.runtime_image_digest,
            capability_digest=self.capability_digest,
        )
        if self.content_digest != expected_digest:
            raise ValueError("contentDigest must match the BrowserProfileVersion")
        return self


def data_profile_content_digest(
    *,
    tenant_id: UUID,
    project_id: UUID,
    profile_key: str,
    version: str,
    blueprint_version_id: UUID,
    blueprint_version_ref: str,
    blueprint_content_digest: str,
    plan_digest: str,
    run_inputs: dict[str, JsonValue],
    input_digest: str,
) -> str:
    """Digest every frozen DataProfileVersion fixture parameter."""

    version_ref = data_profile_version_ref(profile_key, version)
    contract: dict[str, JsonValue] = {
        "blueprintVersionId": str(blueprint_version_id),
        "blueprintVersionRef": blueprint_version_ref,
        "blueprintContentDigest": blueprint_content_digest,
        "planDigest": plan_digest,
        "runInputs": run_inputs,
        "inputDigest": input_digest,
    }
    return _profile_content_digest(
        schema_version=DATA_PROFILE_SCHEMA_VERSION,
        tenant_id=tenant_id,
        project_id=project_id,
        profile_key=profile_key,
        version=version,
        version_ref=version_ref,
        contract=contract,
    )


class DataProfileVersion(_TaskProfileVersion):
    """Published exact Fixture blueprint and secret-free run input snapshot."""

    schema_version: Literal["atlas.data-profile/0.1"] = DATA_PROFILE_SCHEMA_VERSION
    blueprint_version_id: UUID
    blueprint_version_ref: ExactVersionRef
    blueprint_content_digest: str = Field(pattern=DIGEST_PATTERN)
    plan_digest: str = Field(pattern=DIGEST_PATTERN)
    run_inputs: dict[str, JsonValue] = Field(default_factory=dict, max_length=128)
    input_digest: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_reference_and_digests(self) -> Self:
        """Reject altered Fixture inputs or profile metadata."""

        if self.input_digest != canonical_digest(self.run_inputs):
            raise ValueError("inputDigest must match the canonical runInputs")
        expected_ref = data_profile_version_ref(self.profile_key, self.version)
        if self.version_ref != expected_ref:
            raise ValueError("versionRef must be the exact DataProfileVersion reference")
        expected_digest = data_profile_content_digest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            profile_key=self.profile_key,
            version=self.version,
            blueprint_version_id=self.blueprint_version_id,
            blueprint_version_ref=self.blueprint_version_ref,
            blueprint_content_digest=self.blueprint_content_digest,
            plan_digest=self.plan_digest,
            run_inputs=self.run_inputs,
            input_digest=self.input_digest,
        )
        if self.content_digest != expected_digest:
            raise ValueError("contentDigest must match the DataProfileVersion")
        return self


def _reject_sensitive_field_names(value: object, path: tuple[str, ...] = ()) -> None:
    """Reject explicit secret, account, lease, and session field names recursively."""

    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key)
            separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
            tokens = tuple(
                token
                for token in re.split(r"[^A-Za-z0-9]+", separated.casefold())
                if token
            )
            compact = "".join(tokens)
            if (
                _SENSITIVE_FIELD_TOKENS.intersection(tokens)
                or any(marker in compact for marker in _SENSITIVE_FIELD_TOKENS)
                or compact in _SENSITIVE_FIELD_NAMES
            ):
                joined = ".".join((*path, key))
                raise ValueError(f"task profile contains sensitive field: {joined}")
            _reject_sensitive_field_names(nested, (*path, key))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _reject_sensitive_field_names(nested, (*path, str(index)))
