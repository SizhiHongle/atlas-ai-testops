"""Identity Provider Adapter 的能力协商与安全结果契约。"""

from enum import StrEnum
from re import fullmatch
from typing import Self

from pydantic import Field, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel


class ProviderCapability(StrEnum):
    """Provider Adapter 可以声明并协商的版本化能力。"""

    ACCOUNT_DISCOVER = "account.discover"
    ACCOUNT_READ = "account.read"
    ACCOUNT_PROVISION = "account.provision"
    AUTH_PASSWORD = "auth.password"
    AUTH_OAUTH2 = "auth.oauth2"
    AUTH_OIDC = "auth.oidc"
    AUTH_SAML_SSO = "auth.saml_sso"
    AUTH_MFA_TOTP = "auth.mfa.totp"
    AUTH_MANUAL_BOOTSTRAP = "auth.manual_bootstrap"


class AdapterMode(StrEnum):
    """Capability 的确定性执行模式。"""

    NATIVE_API = "native_api"
    BROWSER = "browser"
    WEBHOOK = "webhook"
    POLLING = "polling"
    MANUAL = "manual"


class AdapterErrorCode(StrEnum):
    """禁止携带 Provider 原始响应的稳定错误分类。"""

    CONFIGURATION_INVALID = "configuration_invalid"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    AUTHENTICATION_FAILED = "authentication_failed"
    CREDENTIAL_EXPIRED = "credential_expired"
    MANUAL_ACTION_REQUIRED = "manual_action_required"
    ACCOUNT_LOCKED = "account_locked"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NETWORK_TIMEOUT = "network_timeout"
    INTERNAL_ERROR = "internal_error"


class ProviderHealthState(StrEnum):
    """Probe 与运行健康的低基数状态。"""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class CapabilityDescriptor(FrozenWireModel):
    """Adapter 代码理论支持的单个版本化能力。"""

    name: ProviderCapability
    version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+$")
    mode: AdapterMode


class AdapterManifest(FrozenWireModel):
    """Adapter Protocol、实现版本与理论能力清单。"""

    adapter_key: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    protocol_version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+$")
    implementation_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    capabilities: tuple[CapabilityDescriptor, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_capabilities(self) -> Self:
        names = [capability.name for capability in self.capabilities]
        if len(names) != len(set(names)):
            raise ValueError("adapter capabilities must be unique")
        return self


class CapabilityRequirement(FrozenWireModel):
    """调用方必须在执行前明确满足的能力集合。"""

    required: tuple[ProviderCapability, ...] = Field(min_length=1, max_length=64)

    @field_validator("required")
    @classmethod
    def normalize_required(
        cls,
        required: tuple[ProviderCapability, ...],
    ) -> tuple[ProviderCapability, ...]:
        return tuple(sorted(set(required), key=lambda capability: capability.value))


class NegotiatedCapabilities(FrozenWireModel):
    """Connection Probe 后实际可用于当前操作的能力快照。"""

    capabilities: tuple[CapabilityDescriptor, ...]


class ProviderHealth(FrozenWireModel):
    """Provider Probe 的安全、低基数结果。"""

    state: ProviderHealthState
    safe_message: str = Field(min_length=1, max_length=500)


class PasswordAuthenticationResult(FrozenWireModel):
    """Generic Password Adapter 返回的无秘密认证摘要。"""

    provider_subject: str = Field(min_length=1, max_length=320)
    auth_strength: tuple[str, ...] = ("password",)
    role_keys: tuple[str, ...] = ()

    @field_validator("provider_subject")
    @classmethod
    def normalize_provider_subject(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider subject must not be blank")
        return normalized

    @field_validator("role_keys")
    @classmethod
    def normalize_role_keys(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted({value.strip().casefold() for value in values}))
        if any(fullmatch(r"^[a-z][a-z0-9._-]{1,63}$", value) is None for value in normalized):
            raise ValueError("role key is invalid")
        return normalized


class AdapterError(FrozenWireModel):
    """不包含 Provider 请求、响应或凭证的安全错误对象。"""

    code: AdapterErrorCode
    category: str = Field(min_length=1, max_length=80)
    operation: str = Field(min_length=1, max_length=80)
    safe_message: str = Field(min_length=1, max_length=500)
    retryable: bool
    retry_after_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    provider_code: str | None = Field(default=None, max_length=120)
    request_id: str = Field(min_length=1, max_length=200)
