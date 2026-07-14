"""只通过闭包消费秘密的 Generic Password Adapter。"""

from typing import cast

from atlas_testops.application.ports.providers import (
    AdapterContext,
    AdapterOperationError,
    PasswordAuthenticationTarget,
    PasswordSessionTarget,
)
from atlas_testops.application.ports.secrets import PasswordSecret
from atlas_testops.application.ports.sessions import AuthenticatedBrowserSession
from atlas_testops.domain.identity import (
    AdapterError,
    AdapterErrorCode,
    AdapterManifest,
    AdapterMode,
    CapabilityDescriptor,
    CapabilityRequirement,
    NegotiatedCapabilities,
    PasswordAuthenticationResult,
    ProviderCapability,
    ProviderHealth,
)


class GenericPasswordAdapter:
    """协商账号读取与密码认证，并把秘密直接交给固定认证目标。"""

    _manifest = AdapterManifest(
        adapter_key="generic-password",
        protocol_version="1.0",
        implementation_version="0.1.0",
        capabilities=(
            CapabilityDescriptor(
                name=ProviderCapability.ACCOUNT_READ,
                version="1.0",
                mode=AdapterMode.BROWSER,
            ),
            CapabilityDescriptor(
                name=ProviderCapability.AUTH_PASSWORD,
                version="1.0",
                mode=AdapterMode.BROWSER,
            ),
        ),
    )

    def __init__(self, target: PasswordAuthenticationTarget) -> None:
        self._target = target

    def manifest(self) -> AdapterManifest:
        return self._manifest

    async def probe(self, context: AdapterContext) -> ProviderHealth:
        return await self._target.probe(context)

    async def negotiate(
        self,
        context: AdapterContext,
        requirement: CapabilityRequirement,
    ) -> NegotiatedCapabilities:
        del context
        available = {capability.name for capability in self._manifest.capabilities}
        if not set(requirement.required).issubset(available):
            raise AdapterOperationError(
                AdapterError(
                    code=AdapterErrorCode.CAPABILITY_UNSUPPORTED,
                    category="capability",
                    operation="negotiate",
                    safe_message="required identity capability is unavailable",
                    retryable=False,
                    request_id="adapter-negotiation",
                )
            )
        return NegotiatedCapabilities(capabilities=self._manifest.capabilities)

    async def health(self, context: AdapterContext) -> ProviderHealth:
        return await self._target.probe(context)

    async def authenticate(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
    ) -> PasswordAuthenticationResult:
        """秘密只存在于 Secret Provider 调用的闭包参数中。"""

        async def submit(secret: PasswordSecret) -> PasswordAuthenticationResult:
            return await self._target.authenticate(
                context=context,
                account_handle=account_handle,
                secret=secret,
            )

        return await context.with_password_secret(submit)

    async def establish_session(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
    ) -> AuthenticatedBrowserSession:
        """Consume the password inside the target and return one-shot browser state."""

        if not isinstance(self._target, PasswordSessionTarget):
            raise AdapterOperationError(
                AdapterError(
                    code=AdapterErrorCode.CAPABILITY_UNSUPPORTED,
                    category="capability",
                    operation="establish_session",
                    safe_message="provider does not implement browser session creation",
                    retryable=False,
                    request_id=context.request_id,
                )
            )
        target = cast(PasswordSessionTarget, self._target)

        async def submit(secret: PasswordSecret) -> AuthenticatedBrowserSession:
            return await target.establish_session(
                context=context,
                account_handle=account_handle,
                secret=secret,
            )

        return await context.with_password_secret(submit)
