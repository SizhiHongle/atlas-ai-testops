"""Identity Provider Adapter 与密码认证目标端口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from atlas_testops.application.ports.secrets import (
    PasswordSecret,
    PasswordSecretOperation,
    PasswordSecretScope,
    SecretProviderError,
)
from atlas_testops.domain.identity import (
    AdapterError,
    AdapterManifest,
    CapabilityRequirement,
    NegotiatedCapabilities,
    PasswordAuthenticationResult,
    ProviderHealth,
)
from atlas_testops.domain.platform.models import normalize_origins

if TYPE_CHECKING:
    from atlas_testops.application.ports.sessions import AuthenticatedBrowserSession


@dataclass(frozen=True, slots=True)
class AdapterContext:
    """Expose request metadata and scoped secret use without exposing a locator."""

    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    origin: str
    request_id: str
    _password_secret_scope: PasswordSecretScope | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        normalized_origins = normalize_origins((self.origin,))
        object.__setattr__(self, "origin", normalized_origins[0])
        normalized_request_id = self.request_id.strip()
        if not 1 <= len(normalized_request_id) <= 200:
            raise ValueError("request_id must contain between 1 and 200 characters")
        object.__setattr__(self, "request_id", normalized_request_id)

    @classmethod
    def for_password_operation(
        cls,
        *,
        tenant_id: UUID,
        project_id: UUID,
        environment_id: UUID,
        origin: str,
        request_id: str,
        secret_scope: PasswordSecretScope,
    ) -> AdapterContext:
        return cls(
            tenant_id=tenant_id,
            project_id=project_id,
            environment_id=environment_id,
            origin=origin,
            request_id=request_id,
            _password_secret_scope=secret_scope,
        )

    async def with_password_secret[T](
        self,
        operation: PasswordSecretOperation[T],
    ) -> T:
        """Run an operation with a password without returning the secret itself."""

        if self._password_secret_scope is None:
            raise SecretProviderError("password material is unavailable")
        return await self._password_secret_scope.with_password_secret(operation)


class IdentityProviderAdapter(Protocol):
    """Capability-driven Provider Adapter 顶层契约。"""

    def manifest(self) -> AdapterManifest: ...

    async def probe(self, context: AdapterContext) -> ProviderHealth: ...

    async def negotiate(
        self,
        context: AdapterContext,
        requirement: CapabilityRequirement,
    ) -> NegotiatedCapabilities: ...

    async def health(self, context: AdapterContext) -> ProviderHealth: ...


class PasswordAuthenticationTarget(Protocol):
    """Generic Password Adapter 可以驱动的固定认证目标。"""

    async def probe(self, context: AdapterContext) -> ProviderHealth: ...

    async def authenticate(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> PasswordAuthenticationResult: ...


@runtime_checkable
class PasswordSessionTarget(Protocol):
    """Trusted target that can establish an authenticated browser context state."""

    async def establish_session(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> AuthenticatedBrowserSession: ...


class AdapterOperationError(Exception):
    """用安全 AdapterError 替代 Provider 原始异常。"""

    def __init__(self, error: AdapterError) -> None:
        super().__init__(error.safe_message)
        self.error = error
