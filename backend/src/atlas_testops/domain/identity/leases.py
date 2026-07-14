"""账号租约、TTL、Heartbeat、Release 与 Fencing 协议。"""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.identity.models import (
    CredentialAuthMethod,
    normalize_capabilities,
    validate_labels,
)

LEASE_SUBJECT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$"
ROLE_KEY_PATTERN = r"^[a-z][a-z0-9._-]{1,63}$"


class AccountLeaseStatus(StrEnum):
    """账号租约的持久化状态。"""

    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class LeaseReleaseReason(StrEnum):
    """释放、回收和撤销只能使用结构化原因。"""

    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    WORKER_SHUTDOWN = "WORKER_SHUTDOWN"
    AUTH_FAILED = "AUTH_FAILED"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    MANUAL = "MANUAL"
    TTL_EXPIRED = "TTL_EXPIRED"
    ACCOUNT_QUARANTINED = "ACCOUNT_QUARANTINED"
    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
    ACCOUNT_RETIRED = "ACCOUNT_RETIRED"
    POOL_DISABLED = "POOL_DISABLED"
    ROLE_DISABLED = "ROLE_DISABLED"
    ENVIRONMENT_DISABLED = "ENVIRONMENT_DISABLED"
    CONNECTOR_DISABLED = "CONNECTOR_DISABLED"
    CONNECTOR_REBOUND = "CONNECTOR_REBOUND"


class LeaseCommand(FrozenWireModel):
    """清理 Worker 租约命令中的短文本。"""

    model_config = ConfigDict(str_strip_whitespace=True)


class LeaseRequirements(LeaseCommand):
    """按账号标签、认证方式和角色能力匹配可用槽。"""

    tags: tuple[str, ...] = Field(default=(), max_length=32)
    auth_methods: tuple[CredentialAuthMethod, ...] = Field(default=(), max_length=6)
    capabilities: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags: tuple[str, ...]) -> tuple[str, ...]:
        labels: dict[str, str] = {}
        for tag in tags:
            key, separator, value = tag.partition(":")
            if not separator:
                raise ValueError("lease tag must use key:value format")
            labels[key] = value
        normalized = validate_labels(labels)
        return tuple(f"{key}:{value}" for key, value in sorted(normalized.items()))

    @field_validator("auth_methods")
    @classmethod
    def normalize_auth_methods(
        cls,
        methods: tuple[CredentialAuthMethod, ...],
    ) -> tuple[CredentialAuthMethod, ...]:
        return tuple(sorted(set(methods), key=lambda item: item.value))

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_capabilities(values)

    def label_filter(self) -> dict[str, str]:
        """把规范 Tag 转成 PostgreSQL JSONB 包含过滤器。"""

        return dict(tag.split(":", 1) for tag in self.tags)


class AcquireAccountLease(LeaseCommand):
    """为一个 Execution 申请单个独占账号租约。"""

    execution_id: str = Field(min_length=3, max_length=160, pattern=LEASE_SUBJECT_PATTERN)
    worker_id: str = Field(min_length=3, max_length=160, pattern=LEASE_SUBJECT_PATTERN)
    environment_id: UUID
    role_key: str = Field(min_length=2, max_length=64, pattern=ROLE_KEY_PATTERN)
    requirements: LeaseRequirements = Field(default_factory=LeaseRequirements)
    ttl_seconds: int | None = Field(default=None, ge=300, le=7200)
    execution_deadline: AwareDatetime


class HeartbeatAccountLease(LeaseCommand):
    """使用最新 Fencing Token 续租。"""

    fencing_token: int = Field(ge=1)
    ttl_seconds: int | None = Field(default=None, ge=300, le=7200)


class ReleaseAccountLease(LeaseCommand):
    """幂等结束 Lease；原因不接受自由文本。"""

    fencing_token: int = Field(ge=1)
    reason: LeaseReleaseReason


class AccountLease(FrozenWireModel):
    """控制面内部租约事实；公共响应必须投影为 AccountLeaseHandle。"""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    pool_id: UUID
    account_id: UUID
    slot_id: UUID
    execution_id: str
    worker_id: str
    account_handle: str
    fencing_token: int = Field(ge=1)
    ttl_seconds: int = Field(ge=300, le=7200)
    status: AccountLeaseStatus
    acquired_at: AwareDatetime
    heartbeat_at: AwareDatetime
    expires_at: AwareDatetime
    max_expires_at: AwareDatetime
    released_at: AwareDatetime | None
    release_reason: LeaseReleaseReason | None
    revision: int = Field(ge=1)
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_terminal_state(self) -> Self:
        terminal = self.status is not AccountLeaseStatus.ACTIVE
        if terminal != (self.released_at is not None and self.release_reason is not None):
            raise ValueError("lease terminal metadata does not match status")
        return self

    def to_handle(self) -> AccountLeaseHandle:
        """只向 Worker/Agent 暴露不透明账号句柄和租约控制字段。"""

        return AccountLeaseHandle(
            lease_id=self.id,
            account_handle=self.account_handle,
            fencing_token=self.fencing_token,
            status=self.status,
            heartbeat_after_seconds=max(60, self.ttl_seconds // 3),
            expires_at=self.expires_at,
        )


class AccountLeaseHandle(FrozenWireModel):
    """不包含 Account ID、登录名或凭证引用的 Worker 安全投影。"""

    lease_id: UUID
    account_handle: str = Field(pattern=r"^ah_[A-Za-z0-9_-]{16,128}$")
    fencing_token: int = Field(ge=1)
    status: AccountLeaseStatus
    heartbeat_after_seconds: int = Field(ge=60)
    expires_at: AwareDatetime


class ReapedLeaseBatch(FrozenWireModel):
    """单次过期回收批次的安全摘要。"""

    reaped: int = Field(ge=0)
    observed_at: AwareDatetime


def lease_is_expired(lease: AccountLease, now: datetime) -> bool:
    """以服务端时间判断 Active Lease 是否已经越过 TTL 或执行上限。"""

    return lease.status is AccountLeaseStatus.ACTIVE and (
        lease.expires_at <= now or lease.max_expires_at <= now
    )


def lease_fence_matches(lease: AccountLease, token: int, account_epoch: int) -> bool:
    """敏感操作同时匹配 Lease Token 与账号最新 Epoch。"""

    return lease.fencing_token == token == account_epoch
