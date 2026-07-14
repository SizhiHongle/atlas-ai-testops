"""Connector 安装、实际能力快照与安全配置引用协议。"""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.identity.providers import (
    CapabilityDescriptor,
    ProviderCapability,
    ProviderHealthState,
)
from atlas_testops.domain.platform.models import normalize_origins


class ConnectorMode(StrEnum):
    """Connector 对外部身份源的所有权模式。"""

    OBSERVE_ONLY = "OBSERVE_ONLY"
    MANAGED_TEST_ACCOUNTS = "MANAGED_TEST_ACCOUNTS"
    HYBRID = "HYBRID"
    FEDERATED_SESSION = "FEDERATED_SESSION"


class ConnectorStatus(StrEnum):
    """Connector 当前是否可供控制面和 Worker 使用。"""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"


class ConnectorCommand(FrozenWireModel):
    """清理 Connector 管理命令中的短文本。"""

    model_config = ConfigDict(str_strip_whitespace=True)


class CreateConnectorInstallation(ConnectorCommand):
    """在一个 Environment 中登记显式 Adapter 与安全配置引用。"""

    environment_id: UUID
    installation_key: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z][a-z0-9._-]{1,63}$",
    )
    name: str = Field(min_length=1, max_length=160)
    adapter_key: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    mode: ConnectorMode
    configuration_ref: str = Field(
        min_length=12,
        max_length=204,
        pattern=r"^cfg_[A-Za-z0-9_-]{8,200}$",
        repr=False,
    )
    allowed_origins: tuple[str, ...] = Field(min_length=1, max_length=16)
    required_capabilities: tuple[ProviderCapability, ...] = Field(
        min_length=1,
        max_length=64,
    )

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_origins(origins)

    @field_validator("required_capabilities")
    @classmethod
    def normalize_required_capabilities(
        cls,
        capabilities: tuple[ProviderCapability, ...],
    ) -> tuple[ProviderCapability, ...]:
        return tuple(sorted(set(capabilities), key=lambda item: item.value))

    @model_validator(mode="after")
    def validate_mode_capabilities(self) -> Self:
        if self.mode is ConnectorMode.OBSERVE_ONLY and any(
            capability not in {
                ProviderCapability.ACCOUNT_DISCOVER,
                ProviderCapability.ACCOUNT_READ,
            }
            for capability in self.required_capabilities
        ):
            raise ValueError("observe-only connector cannot require mutating or auth capability")
        return self


class UpdateConnectorInstallation(ConnectorCommand):
    """以 Revision CAS 修改 Connector 的管理配置。"""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    mode: ConnectorMode | None = None
    configuration_ref: str | None = Field(
        default=None,
        min_length=12,
        max_length=204,
        pattern=r"^cfg_[A-Za-z0-9_-]{8,200}$",
        repr=False,
    )
    allowed_origins: tuple[str, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=16,
    )
    required_capabilities: tuple[ProviderCapability, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=64,
    )
    status: ConnectorStatus | None = None

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(
        cls,
        origins: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        return normalize_origins(origins) if origins is not None else None

    @field_validator("required_capabilities")
    @classmethod
    def normalize_required_capabilities(
        cls,
        capabilities: tuple[ProviderCapability, ...] | None,
    ) -> tuple[ProviderCapability, ...] | None:
        if capabilities is None:
            return None
        return tuple(sorted(set(capabilities), key=lambda item: item.value))

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if all(
            value is None
            for value in (
                self.name,
                self.mode,
                self.configuration_ref,
                self.allowed_origins,
                self.required_capabilities,
                self.status,
            )
        ):
            raise ValueError("at least one connector field is required")
        if self.status in {ConnectorStatus.ACTIVE, ConnectorStatus.DEGRADED}:
            raise ValueError("connector health status can only be changed by validation")
        effective_mode = self.mode
        if (
            effective_mode is ConnectorMode.OBSERVE_ONLY
            and self.required_capabilities is not None
            and any(
                capability not in {
                    ProviderCapability.ACCOUNT_DISCOVER,
                    ProviderCapability.ACCOUNT_READ,
                }
                for capability in self.required_capabilities
            )
        ):
            raise ValueError(
                "observe-only connector cannot require mutating or auth capability"
            )
        return self


class ConnectorInstallation(FrozenWireModel):
    """不暴露配置引用的 Connector 管理投影。"""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    installation_key: str
    name: str
    adapter_key: str
    mode: ConnectorMode
    configuration_state: Literal["CONFIGURED"] = "CONFIGURED"
    allowed_origins: tuple[str, ...]
    required_capabilities: tuple[ProviderCapability, ...]
    negotiated_capabilities: tuple[CapabilityDescriptor, ...]
    status: ConnectorStatus
    health_state: ProviderHealthState | None
    safe_message: str | None
    protocol_version: str | None
    implementation_version: str | None
    last_validated_at: AwareDatetime | None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ConnectorInstallationRecord(FrozenWireModel):
    """包含不透明配置引用的内部 Connector 持久化记录。"""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    installation_key: str
    name: str
    adapter_key: str
    mode: ConnectorMode
    configuration_ref: str = Field(repr=False)
    allowed_origins: tuple[str, ...]
    required_capabilities: tuple[ProviderCapability, ...]
    status: ConnectorStatus
    health_state: ProviderHealthState | None
    safe_message: str | None
    protocol_version: str | None
    implementation_version: str | None
    last_validated_at: AwareDatetime | None
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    def to_public(
        self,
        capabilities: tuple[CapabilityDescriptor, ...],
    ) -> ConnectorInstallation:
        return ConnectorInstallation(
            id=self.id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            environment_id=self.environment_id,
            installation_key=self.installation_key,
            name=self.name,
            adapter_key=self.adapter_key,
            mode=self.mode,
            allowed_origins=self.allowed_origins,
            required_capabilities=self.required_capabilities,
            negotiated_capabilities=capabilities,
            status=self.status,
            health_state=self.health_state,
            safe_message=self.safe_message,
            protocol_version=self.protocol_version,
            implementation_version=self.implementation_version,
            last_validated_at=self.last_validated_at,
            revision=self.revision,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class ConnectorInstallationPage(FrozenWireModel):
    """ConnectorInstallation Cursor Page。"""

    items: tuple[ConnectorInstallation, ...]
    next_cursor: str | None = None
