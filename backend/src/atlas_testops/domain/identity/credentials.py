"""一次性 Secret Grant 与受控凭证消费协议。"""

from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.identity.models import CredentialPurpose
from atlas_testops.domain.platform.models import normalize_origins

WORKER_IDENTITY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$"
SECRET_GRANT_REF_PATTERN = r"^sgr_[A-Za-z0-9_-]{32,200}$"


class SecretGrantStatus(StrEnum):
    """一次性 Grant 的持久化状态。"""

    ISSUED = "ISSUED"
    REDEEMED = "REDEEMED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class SecretGrantTerminationReason(StrEnum):
    """未消费 Grant 的结构化终结原因。"""

    REPLACED = "REPLACED"
    LEASE_TERMINATED = "LEASE_TERMINATED"
    EXPIRED = "EXPIRED"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"
    CONNECTOR_UNAVAILABLE = "CONNECTOR_UNAVAILABLE"


class SecretGrantCommand(FrozenWireModel):
    """清理 Worker 输入中的短文本。"""

    model_config = ConfigDict(str_strip_whitespace=True)


class IssueSecretGrant(SecretGrantCommand):
    """为有效 Lease 签发单次、短期、Origin 受限的凭证授权。"""

    fencing_token: int = Field(ge=1)
    purpose: CredentialPurpose
    worker_identity: str = Field(
        min_length=3,
        max_length=160,
        pattern=WORKER_IDENTITY_PATTERN,
    )
    allowed_origins: tuple[str, ...] = Field(min_length=1, max_length=16)

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_origins(origins)


class RedeemSecretGrant(SecretGrantCommand):
    """Auth Worker 在进程内消费 Grant 时提供的绑定上下文。"""

    worker_identity: str = Field(
        min_length=3,
        max_length=160,
        pattern=WORKER_IDENTITY_PATTERN,
    )
    origin: str = Field(min_length=8, max_length=2048)

    @field_validator("origin")
    @classmethod
    def validate_origin(cls, origin: str) -> str:
        normalized = normalize_origins((origin,))
        if len(normalized) != 1:
            raise ValueError("exactly one origin is required")
        return normalized[0]


class SecretGrant(FrozenWireModel):
    """只向受信 Worker 返回不可兑换为账号信息的短期 Grant Ref。"""

    grant_ref: str = Field(pattern=SECRET_GRANT_REF_PATTERN, repr=False)
    expires_at: AwareDatetime
    max_redemptions: Literal[1] = 1


class SecretGrantRecord(FrozenWireModel):
    """不包含原始 Token 与 SecretRef 的 Grant 持久化事实。"""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    connector_installation_id: UUID | None
    lease_id: UUID
    account_id: UUID
    credential_binding_id: UUID
    fencing_token: int = Field(ge=1)
    purpose: CredentialPurpose
    worker_identity: str
    token_hash: str = Field(pattern=r"^[0-9a-f]{64}$", repr=False)
    allowed_origins: tuple[str, ...]
    status: SecretGrantStatus
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    redeemed_at: AwareDatetime | None
    terminated_at: AwareDatetime | None
    termination_reason: SecretGrantTerminationReason | None
    revision: int = Field(ge=1)
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_terminal_metadata(self) -> Self:
        if (
            self.status is SecretGrantStatus.ISSUED
            and self.connector_installation_id is None
        ):
            raise ValueError("issued secret grant must reference a connector")
        if self.status is SecretGrantStatus.ISSUED:
            valid = (
                self.redeemed_at is None
                and self.terminated_at is None
                and self.termination_reason is None
            )
        elif self.status is SecretGrantStatus.REDEEMED:
            valid = (
                self.redeemed_at is not None
                and self.terminated_at is None
                and self.termination_reason is None
            )
        else:
            valid = (
                self.redeemed_at is None
                and self.terminated_at is not None
                and self.termination_reason is not None
            )
        if not valid:
            raise ValueError("secret grant terminal metadata does not match status")
        return self

    def to_grant(self, grant_ref: str) -> SecretGrant:
        """组合仅在签发响应期间存在的原始 Grant Ref。"""

        return SecretGrant(grant_ref=grant_ref, expires_at=self.expires_at)


class SecretGrantReceipt(FrozenWireModel):
    """Adapter 完成受控消费后返回的无秘密摘要。"""

    status: Literal["REDEEMED"] = "REDEEMED"
    grant_id: UUID
    adapter_key: str = Field(min_length=2, max_length=80)
    capability: str = Field(min_length=2, max_length=80)
    origin: str
    completed_at: AwareDatetime
