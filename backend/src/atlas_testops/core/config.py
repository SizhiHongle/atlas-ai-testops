"""Application settings."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed service configuration."""

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

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        """Normalize the versioned API prefix."""
        normalized = f"/{value.strip('/')}"
        if normalized == "/":
            raise ValueError("api_v1_prefix must not be empty")
        return normalized

    @model_validator(mode="after")
    def protect_production_docs(self) -> Self:
        """Disable interactive API documentation in production."""
        if self.environment == "production" and self.docs_enabled:
            object.__setattr__(self, "docs_enabled", False)
        return self


@lru_cache
def get_settings() -> Settings:
    """Load and cache process-wide settings."""
    return Settings()
