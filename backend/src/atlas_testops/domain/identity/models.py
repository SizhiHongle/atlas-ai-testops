"""被测系统角色、账号池、测试账号与凭证引用协议。"""

from datetime import datetime
from enum import StrEnum
from re import fullmatch
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from atlas_testops.core.contracts import FrozenWireModel

CAPABILITY_PATTERN = r"^[a-z][a-z0-9._:-]{1,127}$"
LABEL_KEY_PATTERN = r"^[a-z][a-z0-9._-]{0,63}$"
SENSITIVE_LABEL_FRAGMENTS = frozenset(
    {
        "authorization",
        "cookie",
        "otp",
        "password",
        "secret",
        "storage_state",
        "token",
        "totp_seed",
    }
)


class TestRoleStatus(StrEnum):
    """业务角色定义状态。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AccountPoolStatus(StrEnum):
    """账号池调度状态。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class AccountSource(StrEnum):
    """账号非敏感元数据的权威来源。"""

    ATLAS_MANAGED = "ATLAS_MANAGED"
    EXTERNAL_SYNCED = "EXTERNAL_SYNCED"
    EXTERNAL_DELEGATED = "EXTERNAL_DELEGATED"


class AccountLifecycle(StrEnum):
    """账号生命周期，与健康和运行状态正交。"""

    DRAFT = "DRAFT"
    PROVISIONING = "PROVISIONING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRING = "RETIRING"
    RETIRED = "RETIRED"


class AccountHealth(StrEnum):
    """账号健康状态。"""

    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    QUARANTINED = "QUARANTINED"


class AccountOperationalStatus(StrEnum):
    """账号当前运行准备状态。"""

    VERIFYING = "VERIFYING"
    READY = "READY"
    COOLDOWN = "COOLDOWN"
    CLEANUP_FAILED = "CLEANUP_FAILED"


class AccountSyncStatus(StrEnum):
    """Atlas 与外部身份源的一致性状态。"""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    IN_SYNC = "IN_SYNC"
    CONFLICT = "CONFLICT"
    TOMBSTONED = "TOMBSTONED"


class CredentialAuthMethod(StrEnum):
    """CredentialBinding 支持的确定性认证材料类型。"""

    PASSWORD = "PASSWORD"
    OAUTH2 = "OAUTH2"
    OIDC = "OIDC"
    SAML_SSO = "SAML_SSO"
    TOTP = "TOTP"
    MANUAL_BOOTSTRAP = "MANUAL_BOOTSTRAP"


class CredentialPurpose(StrEnum):
    """秘密引用允许被兑换的用途。"""

    LOGIN = "LOGIN"
    REFRESH_SESSION = "REFRESH_SESSION"
    ROTATE_CREDENTIAL = "ROTATE_CREDENTIAL"


class AccountAvailabilityReason(StrEnum):
    """账号当前不可调度的首要安全原因。"""

    AVAILABLE = "AVAILABLE"
    POOL_DISABLED = "POOL_DISABLED"
    CONNECTOR_UNAVAILABLE = "CONNECTOR_UNAVAILABLE"
    LIFECYCLE_NOT_ACTIVE = "LIFECYCLE_NOT_ACTIVE"
    HEALTH_NOT_HEALTHY = "HEALTH_NOT_HEALTHY"
    OPERATION_NOT_READY = "OPERATION_NOT_READY"
    SYNC_CONFLICT = "SYNC_CONFLICT"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
    SLOT_UNAVAILABLE = "SLOT_UNAVAILABLE"
    LEASED = "LEASED"


class IdentityCommand(FrozenWireModel):
    """统一清理身份管理命令中的用户文本。"""

    model_config = ConfigDict(str_strip_whitespace=True)


def normalize_capabilities(values: tuple[str, ...]) -> tuple[str, ...]:
    """校验并稳定排序角色能力，避免重复语义进入契约。"""

    normalized = tuple(sorted({value.strip().casefold() for value in values}))
    if any(fullmatch(CAPABILITY_PATTERN, value) is None for value in normalized):
        raise ValueError("capability is invalid")
    return normalized


def validate_labels(labels: dict[str, str]) -> dict[str, str]:
    """标签只允许短文本，并拒绝可能承载秘密的键名。"""

    if len(labels) > 32:
        raise ValueError("labels must contain at most 32 entries")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in labels.items():
        key = raw_key.strip().casefold()
        value = raw_value.strip()
        if fullmatch(LABEL_KEY_PATTERN, key) is None:
            raise ValueError("label key is invalid")
        if any(fragment in key for fragment in SENSITIVE_LABEL_FRAGMENTS):
            raise ValueError("sensitive label key is not allowed")
        if not value or len(value) > 160:
            raise ValueError("label value is invalid")
        normalized[key] = value
    return normalized


def require_masked_login_hint(value: str) -> str:
    """只接受脱敏登录提示，拒绝把完整登录名写入业务库。"""

    normalized = value.strip()
    if "*" not in normalized or not 3 <= len(normalized) <= 320:
        raise ValueError("login_hint_masked must be masked")
    return normalized


def account_availability_reason(
    *,
    pool_status: AccountPoolStatus,
    lifecycle_status: AccountLifecycle,
    health_status: AccountHealth,
    operational_status: AccountOperationalStatus,
    sync_status: AccountSyncStatus,
    cooldown_until: datetime | None,
    credential_valid: bool,
    slot_available: bool,
    active_lease: bool,
    now: datetime,
    connector_available: bool = True,
) -> AccountAvailabilityReason:
    """按文档规定的正交状态计算可用性，不保存可被人工篡改的 available。"""

    if pool_status is not AccountPoolStatus.ACTIVE:
        return AccountAvailabilityReason.POOL_DISABLED
    if not connector_available:
        return AccountAvailabilityReason.CONNECTOR_UNAVAILABLE
    if lifecycle_status is not AccountLifecycle.ACTIVE:
        return AccountAvailabilityReason.LIFECYCLE_NOT_ACTIVE
    if health_status is not AccountHealth.HEALTHY:
        return AccountAvailabilityReason.HEALTH_NOT_HEALTHY
    if operational_status is not AccountOperationalStatus.READY:
        return AccountAvailabilityReason.OPERATION_NOT_READY
    if sync_status in {AccountSyncStatus.CONFLICT, AccountSyncStatus.TOMBSTONED}:
        return AccountAvailabilityReason.SYNC_CONFLICT
    if cooldown_until is not None and cooldown_until > now:
        return AccountAvailabilityReason.COOLDOWN_ACTIVE
    if not credential_valid:
        return AccountAvailabilityReason.CREDENTIAL_UNAVAILABLE
    if not slot_available:
        return AccountAvailabilityReason.SLOT_UNAVAILABLE
    if active_lease:
        return AccountAvailabilityReason.LEASED
    return AccountAvailabilityReason.AVAILABLE


class TestRole(FrozenWireModel):
    """用例引用的稳定业务角色，不授予 Atlas 管理权限。"""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    role_key: str
    name: str
    description: str
    capabilities: tuple[str, ...]
    status: TestRoleStatus
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AccountPool(FrozenWireModel):
    """一个 Environment 中按角色组织的独占账号调度池。"""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    role_id: UUID
    pool_key: str
    name: str
    exclusive: bool
    default_ttl_seconds: int = Field(ge=300, le=7200)
    cooldown_seconds: int = Field(ge=0, le=86400)
    health_failure_threshold: int = Field(ge=1, le=20)
    health_retry_cooldown_seconds: int = Field(ge=0, le=86400)
    status: AccountPoolStatus
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TestAccount(FrozenWireModel):
    """不包含任何秘密的被测系统账号投影。"""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    pool_id: UUID
    connector_installation_id: UUID | None
    account_key: str
    source: AccountSource
    external_subject_id: str | None
    login_hint_masked: str
    lifecycle_status: AccountLifecycle
    health_status: AccountHealth
    operational_status: AccountOperationalStatus
    sync_status: AccountSyncStatus
    cooldown_until: AwareDatetime | None
    consecutive_health_failures: int = Field(ge=0)
    last_health_checked_at: AwareDatetime | None
    last_health_succeeded_at: AwareDatetime | None
    lease_epoch: int = Field(ge=0)
    labels: dict[str, str]
    last_leased_at: AwareDatetime | None
    auth_methods: tuple[CredentialAuthMethod, ...]
    credential_valid: bool
    available: bool
    availability_reason: AccountAvailabilityReason
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AccountPoolCapacity(FrozenWireModel):
    """账号池的实时容量投影。"""

    pool_id: UUID
    total_slots: int = Field(ge=0)
    available_slots: int = Field(ge=0)
    leased_slots: int = Field(ge=0)
    cooldown_accounts: int = Field(ge=0)
    quarantined_accounts: int = Field(ge=0)
    unverified_accounts: int = Field(ge=0)


class CreateTestRole(IdentityCommand):
    """创建业务角色。"""

    role_key: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z][a-z0-9._-]{1,63}$",
    )
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    capabilities: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_capabilities(values)


class UpdateTestRole(IdentityCommand):
    """更新业务角色的可变属性。"""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    capabilities: tuple[str, ...] | None = Field(default=None, max_length=64)
    status: TestRoleStatus | None = None

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(
        cls,
        values: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        return normalize_capabilities(values) if values is not None else None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if all(
            value is None for value in (self.name, self.description, self.capabilities, self.status)
        ):
            raise ValueError("at least one test role field is required")
        return self


class CreateAccountPool(IdentityCommand):
    """在一个 Environment 中创建独占账号池。"""

    role_id: UUID
    pool_key: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z][a-z0-9._-]{1,63}$",
    )
    name: str = Field(min_length=1, max_length=160)
    exclusive: Literal[True] = True
    default_ttl_seconds: int = Field(default=1800, ge=300, le=7200)
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)
    health_failure_threshold: int = Field(default=3, ge=1, le=20)
    health_retry_cooldown_seconds: int = Field(default=300, ge=0, le=86400)


class UpdateAccountPool(IdentityCommand):
    """更新账号池策略。"""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    default_ttl_seconds: int | None = Field(default=None, ge=300, le=7200)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)
    health_failure_threshold: int | None = Field(default=None, ge=1, le=20)
    health_retry_cooldown_seconds: int | None = Field(
        default=None,
        ge=0,
        le=86400,
    )
    status: AccountPoolStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if all(
            value is None
            for value in (
                self.name,
                self.default_ttl_seconds,
                self.cooldown_seconds,
                self.health_failure_threshold,
                self.health_retry_cooldown_seconds,
                self.status,
            )
        ):
            raise ValueError("at least one account pool field is required")
        return self


class CredentialBindingInput(IdentityCommand):
    """只写入 Secret Manager 的不透明引用，不接受秘密值。"""

    auth_method: CredentialAuthMethod
    purpose: CredentialPurpose = CredentialPurpose.LOGIN
    secret_ref: str = Field(
        min_length=12,
        max_length=204,
        pattern=r"^sec_[A-Za-z0-9_-]{8,200}$",
    )
    secret_version: str = Field(min_length=1, max_length=160)
    expires_at: AwareDatetime | None = None


class CreateTestAccount(IdentityCommand):
    """导入不含明文凭证的 TestAccount。"""

    connector_installation_id: UUID
    account_key: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$",
    )
    source: AccountSource = AccountSource.ATLAS_MANAGED
    external_subject_id: str | None = Field(default=None, min_length=1, max_length=320)
    login_hint_masked: str = Field(min_length=3, max_length=320)
    labels: dict[str, str] = Field(default_factory=dict)
    credentials: tuple[CredentialBindingInput, ...] = Field(min_length=1, max_length=4)

    @field_validator("login_hint_masked")
    @classmethod
    def validate_login_hint(cls, value: str) -> str:
        return require_masked_login_hint(value)

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, labels: dict[str, str]) -> dict[str, str]:
        return validate_labels(labels)

    @model_validator(mode="after")
    def validate_source_and_credentials(self) -> Self:
        if self.source is AccountSource.ATLAS_MANAGED and self.external_subject_id is not None:
            raise ValueError("ATLAS_MANAGED account must not have external_subject_id")
        if self.source is not AccountSource.ATLAS_MANAGED and self.external_subject_id is None:
            raise ValueError("external account requires external_subject_id")
        keys = {(item.auth_method, item.purpose) for item in self.credentials}
        if len(keys) != len(self.credentials):
            raise ValueError("credential auth method and purpose must be unique")
        return self


class UpdateTestAccount(IdentityCommand):
    """只修改 TestAccount 的非敏感管理字段。"""

    connector_installation_id: UUID | None = None
    login_hint_masked: str | None = Field(default=None, min_length=3, max_length=320)
    labels: dict[str, str] | None = None
    lifecycle_status: AccountLifecycle | None = None

    @field_validator("login_hint_masked")
    @classmethod
    def validate_login_hint(cls, value: str | None) -> str | None:
        return require_masked_login_hint(value) if value is not None else None

    @field_validator("labels")
    @classmethod
    def normalize_labels(cls, labels: dict[str, str] | None) -> dict[str, str] | None:
        return validate_labels(labels) if labels is not None else None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if all(
            value is None
            for value in (
                self.connector_installation_id,
                self.login_hint_masked,
                self.labels,
                self.lifecycle_status,
            )
        ):
            raise ValueError("at least one test account field is required")
        return self


class AccountStateReason(IdentityCommand):
    """隔离和恢复必须保留结构化安全原因。"""

    reason: str = Field(min_length=3, max_length=500)


class TestRolePage(FrozenWireModel):
    """TestRole Cursor Page。"""

    items: tuple[TestRole, ...]
    next_cursor: str | None = None


class AccountPoolPage(FrozenWireModel):
    """AccountPool Cursor Page。"""

    items: tuple[AccountPool, ...]
    next_cursor: str | None = None


class TestAccountPage(FrozenWireModel):
    """TestAccount Cursor Page。"""

    items: tuple[TestAccount, ...]
    next_cursor: str | None = None
