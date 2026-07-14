"""Isolated Playwright runtime for provider-specific password login flows."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from json import dumps
from typing import Literal, Protocol, cast

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

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


class PasswordLoginFlow(Protocol):
    """Provider-specific selectors and identity checks executed inside one page."""

    async def authenticate(
        self,
        *,
        page: Page,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> PasswordAuthenticationResult: ...


class IsolatedBrowserRuntime(Protocol):
    """Provide one non-persistent BrowserContext per authentication attempt."""

    def isolated_context(self) -> AbstractAsyncContextManager[BrowserContext]: ...


class PlaywrightBrowserRuntime:
    """Share one browser process while strictly isolating contexts and concurrency."""

    def __init__(
        self,
        *,
        browser_name: Literal["chromium", "firefox", "webkit"] = "chromium",
        headless: bool = True,
        maximum_concurrency: int = 4,
    ) -> None:
        if not 1 <= maximum_concurrency <= 32:
            raise ValueError("Playwright concurrency must be between 1 and 32")
        self._browser_name = browser_name
        self._headless = headless
        self._semaphore = asyncio.Semaphore(maximum_concurrency)
        self._lifecycle_lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        """Start the shared browser exactly once without creating persistent profiles."""

        async with self._lifecycle_lock:
            if self._browser is not None:
                return
            manager = async_playwright()
            playwright = await manager.start()
            try:
                if self._browser_name == "chromium":
                    browser_type = playwright.chromium
                elif self._browser_name == "firefox":
                    browser_type = playwright.firefox
                else:
                    browser_type = playwright.webkit
                browser = await browser_type.launch(headless=self._headless)
            except BaseException:
                await playwright.stop()
                raise
            self._playwright = playwright
            self._browser = browser

    async def close(self) -> None:
        """Close the browser and Playwright driver during worker shutdown."""

        async with self._lifecycle_lock:
            browser = self._browser
            playwright = self._playwright
            self._browser = None
            self._playwright = None
            if browser is not None:
                await browser.close()
            if playwright is not None:
                await playwright.stop()

    @asynccontextmanager
    async def isolated_context(self) -> AsyncIterator[BrowserContext]:
        """Create a fresh in-memory context with no video, trace, or download capture."""

        await self.start()
        async with self._semaphore:
            browser = self._browser
            if browser is None:
                raise RuntimeError("Playwright browser is not running")
            browser_context = await browser.new_context(
                accept_downloads=False,
                ignore_https_errors=False,
                java_script_enabled=True,
            )
            try:
                yield browser_context
            finally:
                await browser_context.close()


class PlaywrightPasswordSessionTarget:
    """Run a deterministic login flow and export Storage State only in memory."""

    def __init__(
        self,
        runtime: IsolatedBrowserRuntime,
        flow: PasswordLoginFlow,
        *,
        allowed_origins: tuple[str, ...],
    ) -> None:
        self._runtime = runtime
        self._flow = flow
        self._allowed_origins = frozenset(normalize_origins(allowed_origins))

    async def probe(self, context: AdapterContext) -> ProviderHealth:
        if context.origin not in self._allowed_origins:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                safe_message="origin is not allowed for the Playwright login flow",
            )
        try:
            async with self._runtime.isolated_context():
                pass
        except (PlaywrightError, OSError):
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                safe_message="Playwright browser runtime is unavailable",
            )
        return ProviderHealth(
            state=ProviderHealthState.HEALTHY,
            safe_message="Playwright browser runtime is ready",
        )

    async def authenticate(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> PasswordAuthenticationResult:
        return cast(
            PasswordAuthenticationResult,
            await self._run_login(
                context=context,
                account_handle=account_handle,
                secret=secret,
                capture_storage_state=False,
            ),
        )

    async def establish_session(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> AuthenticatedBrowserSession:
        result = await self._run_login(
            context=context,
            account_handle=account_handle,
            secret=secret,
            capture_storage_state=True,
        )
        if not isinstance(result, AuthenticatedBrowserSession):
            raise RuntimeError("Playwright login did not capture Storage State")
        return result

    async def _run_login(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
        capture_storage_state: bool,
    ) -> PasswordAuthenticationResult | AuthenticatedBrowserSession:
        if context.origin not in self._allowed_origins:
            raise self._adapter_error(
                context,
                code=AdapterErrorCode.CONFIGURATION_INVALID,
                safe_message="origin is not allowed for the Playwright login flow",
                retryable=False,
            )
        try:
            async with self._runtime.isolated_context() as browser_context:
                page = await browser_context.new_page()
                identity = await self._flow.authenticate(
                    page=page,
                    context=context,
                    account_handle=account_handle,
                    secret=secret,
                )
                if not capture_storage_state:
                    return identity
                storage_state = await browser_context.storage_state(indexed_db=True)
                serialized_state = dumps(
                    storage_state,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                return AuthenticatedBrowserSession(
                    provider_subject=identity.provider_subject,
                    role_keys=identity.role_keys,
                    auth_strength=(CredentialAuthMethod.PASSWORD,),
                    storage_state=serialized_state,
                )
        except AdapterOperationError:
            raise
        except PlaywrightTimeoutError as error:
            raise self._adapter_error(
                context,
                code=AdapterErrorCode.NETWORK_TIMEOUT,
                safe_message="provider login flow timed out",
                retryable=True,
            ) from error
        except (PlaywrightError, OSError) as error:
            raise self._adapter_error(
                context,
                code=AdapterErrorCode.PROVIDER_UNAVAILABLE,
                safe_message="provider browser login is unavailable",
                retryable=True,
            ) from error

    @staticmethod
    def _adapter_error(
        context: AdapterContext,
        *,
        code: AdapterErrorCode,
        safe_message: str,
        retryable: bool,
    ) -> AdapterOperationError:
        return AdapterOperationError(
            AdapterError(
                code=code,
                category="browser_authentication",
                operation="password_login",
                safe_message=safe_message,
                retryable=retryable,
                request_id=context.request_id,
            )
        )
