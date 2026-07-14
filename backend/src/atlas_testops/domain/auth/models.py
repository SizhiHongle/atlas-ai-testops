"""Atlas 平台用户、授权与登录协议。"""

from enum import StrEnum
from re import fullmatch
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, SecretStr, field_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.domain.platform import Project, Tenant


class PlatformUserStatus(StrEnum):
    """平台主体生命周期。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class MembershipStatus(StrEnum):
    """成员关系生命周期。"""

    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class PlatformRole(StrEnum):
    """文档定义的平台组织级与项目级角色。"""

    ORG_ADMIN = "ORG_ADMIN"
    PROJECT_ADMIN = "PROJECT_ADMIN"
    COMPONENT_MAINTAINER = "COMPONENT_MAINTAINER"
    CASE_AUTHOR = "CASE_AUTHOR"
    CASE_REVIEWER = "CASE_REVIEWER"
    RUN_OPERATOR = "RUN_OPERATOR"
    OBSERVER = "OBSERVER"


class AuthenticationMethod(StrEnum):
    """平台 Session 的认证来源，不包含被测系统认证。"""

    PASSWORD = "PASSWORD"
    FEISHU = "FEISHU"


def normalize_email_address(value: str) -> str:
    """生成用于唯一约束和登录匹配的规范邮箱。"""

    normalized = value.strip().casefold()
    if len(normalized) > 320 or fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized) is None:
        raise ValueError("email address is invalid")
    return normalized


class PlatformUser(FrozenWireModel):
    """登录 Atlas 的人员主体，不携带任何测试账号秘密。"""

    id: UUID
    email: str
    display_name: str
    status: PlatformUserStatus
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class PlatformMembership(FrozenWireModel):
    """平台主体在 Tenant 或 Project 范围内的一项角色授权。"""

    id: UUID
    tenant_id: UUID
    project_id: UUID | None
    user_id: UUID
    role: PlatformRole
    status: MembershipStatus
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class BootstrapPrincipalCommand(FrozenWireModel):
    """仅供 Development Bootstrap 创建首个组织管理员。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    tenant_id: UUID
    project_id: UUID
    email: str
    display_name: str = Field(min_length=1, max_length=160)
    password: SecretStr = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        """在进入应用层前规范邮箱。"""

        return normalize_email_address(value)


class LoginCommand(FrozenWireModel):
    """账号密码登录命令；Workspace Context 必须显式选择。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    tenant_id: UUID
    project_id: UUID
    email: str
    password: SecretStr = Field(min_length=1, max_length=256)
    remember: bool = True

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        """登录匹配不区分邮箱大小写。"""

        return normalize_email_address(value)


class BootstrapPrincipal(FrozenWireModel):
    """Development Bootstrap 创建的主体与初始授权。"""

    user: PlatformUser
    membership: PlatformMembership


class PlatformSessionView(FrozenWireModel):
    """返回前端的安全 Session 投影，不包含 Session ID 或令牌。"""

    user: PlatformUser
    tenant: Tenant
    project: Project
    roles: tuple[PlatformRole, ...]
    authentication_method: AuthenticationMethod
    expires_at: AwareDatetime
