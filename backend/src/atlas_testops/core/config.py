"""应用配置。"""

from base64 import b64decode
from binascii import Error as BinasciiError
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """由环境变量提供的进程配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ATLAS_",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "Atlas TestOps Backend"
    environment: Literal["local", "test", "development", "staging", "production"] = "local"
    api_v1_prefix: str = "/v1"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    docs_enabled: bool = True
    database_url: SecretStr | None = None
    database_pool_min_size: int = Field(default=1, ge=1, le=50)
    database_pool_max_size: int = Field(default=10, ge=1, le=100)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    database_statement_timeout_ms: int = Field(default=10_000, ge=100, le=300_000)
    session_idle_minutes: int = Field(default=120, ge=5, le=1_440)
    session_absolute_hours: int = Field(default=12, ge=1, le=168)
    remembered_session_days: int = Field(default=30, ge=1, le=90)
    remembered_session_idle_hours: int = Field(default=168, ge=1, le=720)
    session_touch_interval_seconds: int = Field(default=300, ge=30, le=3_600)
    password_hash_concurrency: int = Field(default=4, ge=1, le=16)
    password_max_failures: int = Field(default=5, ge=3, le=20)
    password_lock_minutes: int = Field(default=15, ge=1, le=1_440)
    secret_grant_ttl_seconds: int = Field(default=60, ge=30, le=300)
    account_health_verification_timeout_seconds: int = Field(default=30, ge=1, le=120)
    account_health_attempt_ttl_seconds: int = Field(default=120, ge=10, le=600)
    feishu_client_id: str | None = None
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "atlas-control"
    auth_session_dispatch_enabled: bool = False
    auth_session_task_queue: str = "atlas-auth-session"
    auth_session_workflow_timeout_seconds: int = Field(default=90, ge=10, le=600)
    auth_session_ttl_seconds: int = Field(default=900, ge=60, le=3_600)
    auth_session_creation_timeout_seconds: int = Field(default=45, ge=1, le=300)
    auth_session_attempt_ttl_seconds: int = Field(default=120, ge=30, le=600)
    auth_session_manual_ticket_ttl_seconds: int = Field(default=600, ge=60, le=3_600)
    auth_session_worker_max_concurrency: int = Field(default=4, ge=1, le=32)
    session_janitor_claim_ttl_seconds: int = Field(default=120, ge=30, le=600)

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        """规范化版本化 API 前缀。"""
        normalized = f"/{value.strip('/')}"
        if normalized == "/":
            raise ValueError("api_v1_prefix must not be empty")
        return normalized

    @model_validator(mode="after")
    def protect_production_docs(self) -> Self:
        """生产环境禁止暴露交互式 API 文档。"""
        if self.environment == "production" and self.docs_enabled:
            object.__setattr__(self, "docs_enabled", False)
        if self.database_pool_max_size < self.database_pool_min_size:
            raise ValueError("database_pool_max_size must be >= database_pool_min_size")
        if self.session_idle_minutes > self.session_absolute_hours * 60:
            raise ValueError("session_idle_minutes must not exceed session_absolute_hours")
        if self.remembered_session_idle_hours > self.remembered_session_days * 24:
            raise ValueError(
                "remembered_session_idle_hours must not exceed remembered_session_days"
            )
        if (
            self.account_health_attempt_ttl_seconds
            <= self.account_health_verification_timeout_seconds
        ):
            raise ValueError("account_health_attempt_ttl_seconds must exceed verification timeout")
        if self.auth_session_attempt_ttl_seconds <= self.auth_session_creation_timeout_seconds:
            raise ValueError(
                "auth_session_attempt_ttl_seconds must exceed auth session creation timeout"
            )
        if self.auth_session_workflow_timeout_seconds <= self.auth_session_creation_timeout_seconds:
            raise ValueError(
                "auth_session_workflow_timeout_seconds must exceed auth session creation timeout"
            )
        return self

    @property
    def database_url_value(self) -> str | None:
        """只在创建数据库组件时解开受保护的 DSN。"""

        return self.database_url.get_secret_value() if self.database_url is not None else None

    @property
    def session_cookie_name(self) -> str:
        """正式环境使用 Host Prefix 绑定 Secure、Path 与无 Domain 约束。"""

        return (
            "__Host-atlas_session"
            if self.environment in {"staging", "production"}
            else "atlas_session"
        )

    @property
    def session_cookie_secure(self) -> bool:
        """本地 HTTP 可调试，Staging 与 Production 只通过 HTTPS 发送。"""

        return self.environment in {"staging", "production"}


@lru_cache
def get_settings() -> Settings:
    """加载并缓存进程级配置。"""
    return Settings()


class AuthSessionWorkerSettings(BaseSettings):
    """Secrets and object-store settings loaded only by the Auth Session Worker."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ATLAS_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["local", "test", "development", "staging", "production"] = "local"
    session_object_store_endpoint: str | None = None
    session_object_store_access_key: SecretStr | None = None
    session_object_store_secret_key: SecretStr | None = None
    session_object_store_bucket: str = Field(
        default="atlas-session-artifacts",
        pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$",
    )
    session_object_store_secure: bool = False
    session_object_store_create_bucket: bool = False
    session_artifact_aes_key_base64: SecretStr | None = None
    session_artifact_key_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$",
    )

    @model_validator(mode="after")
    def validate_session_vault_configuration(self) -> Self:
        """Reject partial or unsafe static-key deployments before worker startup."""

        configured_values = (
            self.session_object_store_endpoint,
            self.session_object_store_access_key,
            self.session_object_store_secret_key,
            self.session_artifact_aes_key_base64,
            self.session_artifact_key_version,
        )
        configured = [value is not None for value in configured_values]
        if any(configured) and not all(configured):
            raise ValueError("session artifact vault configuration must be complete")
        if self.session_object_store_create_bucket and not all(configured):
            raise ValueError("session artifact bucket creation requires a configured vault")
        if self.environment in {"staging", "production"}:
            if self.session_object_store_create_bucket:
                raise ValueError("automatic session artifact bucket creation is local-only")
            if self.session_artifact_aes_key_base64 is not None:
                raise ValueError(
                    "static session artifact keys are forbidden outside local development"
                )
        if self.session_artifact_aes_key_base64 is not None:
            try:
                key = b64decode(
                    self.session_artifact_aes_key_base64.get_secret_value(),
                    validate=True,
                )
            except (BinasciiError, ValueError) as error:
                raise ValueError("session artifact AES key must be valid base64") from error
            if len(key) != 32:
                raise ValueError("session artifact AES key must decode to exactly 32 bytes")
        return self

    @property
    def session_vault_configured(self) -> bool:
        """Return whether the worker has a complete local S3/AES vault configuration."""

        return self.session_object_store_endpoint is not None
