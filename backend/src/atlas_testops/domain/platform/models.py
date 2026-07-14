"""Tenant、Project 与 Environment 领域模型。"""

from enum import StrEnum
from ipaddress import ip_address
from re import fullmatch
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, field_validator, model_validator

from atlas_testops.core.contracts import FrozenWireModel


class TenantStatus(StrEnum):
    """Tenant 生命周期。"""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class ProjectStatus(StrEnum):
    """Project 生命周期。"""

    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class EnvironmentKind(StrEnum):
    """Environment 安全等级。"""

    TEST = "TEST"
    STAGING = "STAGING"
    PRODUCTION = "PRODUCTION"


class EnvironmentStatus(StrEnum):
    """Environment 可用状态。"""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class PlatformCommand(FrozenWireModel):
    """在长度校验前清理用户输入两端的空白。"""

    model_config = ConfigDict(str_strip_whitespace=True)


class Tenant(FrozenWireModel):
    """多租户隔离根。"""

    id: UUID
    slug: str
    name: str
    status: TenantStatus
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Project(FrozenWireModel):
    """测试资产和运行的项目边界。"""

    id: UUID
    tenant_id: UUID
    project_key: str
    name: str
    status: ProjectStatus
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Environment(FrozenWireModel):
    """Connector、账号、策略和执行能力的环境边界。"""

    id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_key: str
    name: str
    kind: EnvironmentKind
    status: EnvironmentStatus
    allowed_origins: tuple[str, ...]
    revision: int = Field(ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CreateTenant(PlatformCommand):
    """Development Bootstrap 使用的 Tenant 创建命令。"""

    slug: str = Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=160)


class CreateProject(PlatformCommand):
    """Project 创建命令。"""

    project_key: str = Field(
        min_length=2,
        max_length=32,
        pattern=r"^[A-Z][A-Z0-9_]{1,31}$",
    )
    name: str = Field(min_length=1, max_length=160)


class UpdateProject(PlatformCommand):
    """Project 可变属性补丁。"""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: ProjectStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> UpdateProject:
        """拒绝没有任何变更意图的 PATCH。"""

        if self.name is None and self.status is None:
            raise ValueError("at least one project field is required")
        return self


class CreateEnvironment(PlatformCommand):
    """Environment 创建命令。"""

    environment_key: str = Field(
        min_length=2,
        max_length=32,
        pattern=r"^[a-z][a-z0-9-]{1,31}$",
    )
    name: str = Field(min_length=1, max_length=160)
    kind: EnvironmentKind
    allowed_origins: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_origins(origins)

    @model_validator(mode="after")
    def require_https_for_production(self) -> CreateEnvironment:
        if self.kind is EnvironmentKind.PRODUCTION and any(
            not origin.startswith("https://") for origin in self.allowed_origins
        ):
            raise ValueError("production environment origins must use https")
        return self


class UpdateEnvironment(PlatformCommand):
    """Environment 可变属性补丁。"""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: EnvironmentStatus | None = None
    allowed_origins: tuple[str, ...] | None = Field(default=None, max_length=32)

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(
        cls,
        origins: tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        return normalize_origins(origins) if origins is not None else None

    @model_validator(mode="after")
    def require_change(self) -> UpdateEnvironment:
        """拒绝没有任何变更意图的 PATCH。"""

        if self.name is None and self.status is None and self.allowed_origins is None:
            raise ValueError("at least one environment field is required")
        return self


def normalize_origins(origins: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize exact HTTP origins and reject paths, credentials, and fragments."""

    normalized: set[str] = set()
    for value in origins:
        raw_origin = value.strip()
        if not 8 <= len(raw_origin) <= 2048 or any(
            ord(character) < 32 or ord(character) == 127 for character in raw_origin
        ):
            raise ValueError("origin length or control characters are invalid")
        try:
            parsed = urlsplit(raw_origin)
        except ValueError as error:
            raise ValueError("origin is malformed") from error
        if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("origin must use http or https and include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("origin must not include user information")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("origin must not include a path, query, or fragment")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("origin port is invalid") from error
        if port == 0:
            raise ValueError("origin port must be between 1 and 65535")
        scheme = parsed.scheme.lower()
        hostname = normalize_origin_hostname(parsed.hostname)
        default_port = (scheme == "http" and port == 80) or (
            scheme == "https" and port == 443
        )
        port_suffix = f":{port}" if port is not None and not default_port else ""
        normalized.add(f"{scheme}://{hostname}{port_suffix}")
    return tuple(sorted(normalized))


def normalize_origin_hostname(hostname: str) -> str:
    """Return a canonical ASCII DNS or IP host without browser parsing ambiguity."""

    lowered = hostname.lower()
    if "%" in lowered:
        raise ValueError("origin host must not contain an IPv6 zone identifier")
    try:
        address = ip_address(lowered)
    except ValueError:
        if (
            not lowered.isascii()
            or len(lowered) > 253
            or lowered.endswith(".")
            or ":" in lowered
            or all(character in "0123456789." for character in lowered)
        ):
            raise ValueError("origin host is invalid") from None
        labels = lowered.split(".")
        if any(
            fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in labels
        ):
            raise ValueError("origin host is invalid") from None
        return lowered
    if address.version == 6:
        return f"[{address.compressed}]"
    return address.compressed


class ProjectPage(FrozenWireModel):
    """Project Cursor Page。"""

    items: tuple[Project, ...]
    next_cursor: str | None = None


class EnvironmentPage(FrozenWireModel):
    """Environment Cursor Page。"""

    items: tuple[Environment, ...]
    next_cursor: str | None = None
