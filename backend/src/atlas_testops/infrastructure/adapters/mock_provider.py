"""Adapter 合约测试使用的固定 Origin Mock Identity Provider。"""

from dataclasses import dataclass, field
from hashlib import sha256
from hmac import compare_digest
from json import dumps
from urllib.parse import urlsplit

from atlas_testops.application.ports.providers import AdapterContext, AdapterOperationError
from atlas_testops.application.ports.secrets import PasswordSecret
from atlas_testops.application.ports.sessions import AuthenticatedBrowserSession
from atlas_testops.domain.identity import (
    AdapterError,
    AdapterErrorCode,
    CredentialAuthMethod,
    PasswordAuthenticationResult,
    ProviderHealth,
    ProviderHealthState,
)
from atlas_testops.domain.platform.models import normalize_origins


@dataclass(frozen=True, slots=True)
class _MockAccount:
    provider_subject: str
    role_keys: tuple[str, ...]
    username: str = field(repr=False)
    password: str = field(repr=False)


class MockIdentityProvider:
    """不执行网络调用的确定性 Provider，用于验证 Adapter 安全边界。"""

    def __init__(self, *, allowed_origins: tuple[str, ...] = ()) -> None:
        self._allowed_origins = frozenset(normalize_origins(allowed_origins))
        self._accounts: dict[str, _MockAccount] = {}
        self.authentication_attempts = 0

    def register_account(
        self,
        *,
        account_handle: str,
        provider_subject: str,
        username: str,
        password: str,
        role_keys: tuple[str, ...] = (),
    ) -> None:
        self._accounts[account_handle] = _MockAccount(
            provider_subject=provider_subject,
            role_keys=tuple(sorted(set(role_keys))),
            username=username,
            password=password,
        )

    async def probe(self, context: AdapterContext) -> ProviderHealth:
        if context.origin not in self._allowed_origins:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                safe_message="origin is not configured for the mock provider",
            )
        return ProviderHealth(
            state=ProviderHealthState.HEALTHY,
            safe_message="mock provider is ready",
        )

    async def authenticate(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> PasswordAuthenticationResult:
        account = self._require_account(
            context=context,
            account_handle=account_handle,
            secret=secret,
        )
        return PasswordAuthenticationResult(
            provider_subject=account.provider_subject,
            role_keys=account.role_keys,
        )

    async def establish_session(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> AuthenticatedBrowserSession:
        """Produce deterministic synthetic storage state for contract tests."""

        account = self._require_account(
            context=context,
            account_handle=account_handle,
            secret=secret,
        )
        hostname = urlsplit(context.origin).hostname
        if hostname is None:
            raise RuntimeError("normalized mock origin does not contain a hostname")
        session_value = sha256(
            f"{account.provider_subject}:{context.request_id}".encode()
        ).hexdigest()
        storage_state = dumps(
            {
                "cookies": [
                    {
                        "name": "atlas_mock_session",
                        "value": session_value,
                        "domain": hostname,
                        "path": "/",
                        "expires": -1,
                        "httpOnly": True,
                        "secure": context.origin.startswith("https://"),
                        "sameSite": "Lax",
                    }
                ],
                "origins": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return AuthenticatedBrowserSession(
            provider_subject=account.provider_subject,
            role_keys=account.role_keys,
            auth_strength=(CredentialAuthMethod.PASSWORD,),
            storage_state=storage_state,
        )

    def _require_account(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> _MockAccount:
        """Validate a mock credential without including it in an error object."""

        self.authentication_attempts += 1
        account = self._accounts.get(account_handle)
        authorized_origin = context.origin in self._allowed_origins
        valid = (
            account is not None
            and authorized_origin
            and compare_digest(account.username, secret.reveal_username())
            and compare_digest(account.password, secret.reveal_password())
        )
        if not valid or account is None:
            raise AdapterOperationError(
                AdapterError(
                    code=AdapterErrorCode.AUTHENTICATION_FAILED,
                    category="authentication",
                    operation="password_login",
                    safe_message="provider rejected the authentication attempt",
                    retryable=False,
                    request_id=context.request_id,
                )
            )
        return account
