"""Playwright password target tests with an isolated in-memory browser double."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from json import loads
from typing import Literal, cast
from uuid import uuid7

import pytest
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from atlas_testops.application.ports.providers import AdapterContext, AdapterOperationError
from atlas_testops.application.ports.secrets import PasswordSecret
from atlas_testops.domain.identity import (
    AdapterErrorCode,
    PasswordAuthenticationResult,
    ProviderHealthState,
)
from atlas_testops.infrastructure.adapters import playwright_password
from atlas_testops.infrastructure.adapters.playwright_password import (
    IsolatedBrowserRuntime,
    PasswordLoginFlow,
    PlaywrightBrowserRuntime,
    PlaywrightPasswordSessionTarget,
)

ORIGIN = "https://staging.example.test"


class FakeBrowserContext:
    def __init__(self) -> None:
        self.page = object()

    async def new_page(self) -> Page:
        return cast(Page, self.page)

    async def storage_state(self, *, indexed_db: bool = False) -> dict[str, object]:
        assert indexed_db
        return {
            "cookies": [{"name": "provider_session", "value": "opaque-cookie"}],
            "origins": [],
        }

    async def close(self) -> None:
        return None


class FakeBrowserRuntime:
    def __init__(self) -> None:
        self.contexts = 0

    @asynccontextmanager
    async def isolated_context(self) -> AsyncIterator[BrowserContext]:
        self.contexts += 1
        yield cast(BrowserContext, FakeBrowserContext())


class FailingBrowserRuntime:
    def __init__(self, error: Exception) -> None:
        self._error: Exception | None = error

    @asynccontextmanager
    async def isolated_context(self) -> AsyncIterator[BrowserContext]:
        if self._error is not None:
            raise self._error
        yield cast(BrowserContext, FakeBrowserContext())


class FakeLaunchedBrowser:
    def __init__(self) -> None:
        self.contexts = 0
        self.closed = False

    async def new_context(self, **options: object) -> BrowserContext:
        assert options["accept_downloads"] is False
        assert options["ignore_https_errors"] is False
        assert options["java_script_enabled"] is True
        self.contexts += 1
        return cast(BrowserContext, FakeBrowserContext())

    async def close(self) -> None:
        self.closed = True


class FakeBrowserType:
    def __init__(self, browser: FakeLaunchedBrowser) -> None:
        self.browser = browser
        self.launches = 0

    async def launch(self, *, headless: bool) -> Browser:
        assert headless
        self.launches += 1
        return cast(Browser, self.browser)


class FakePlaywrightDriver:
    def __init__(self, browser_type: FakeBrowserType) -> None:
        typed = cast(object, browser_type)
        self.chromium = typed
        self.firefox = typed
        self.webkit = typed
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakePlaywrightManager:
    def __init__(self, driver: FakePlaywrightDriver) -> None:
        self.driver = driver

    async def start(self) -> Playwright:
        return cast(Playwright, self.driver)


class FakePasswordLoginFlow:
    async def authenticate(
        self,
        *,
        page: Page,
        context: AdapterContext,
        account_handle: str,
        secret: PasswordSecret,
    ) -> PasswordAuthenticationResult:
        assert page is not None
        assert context.origin == ORIGIN
        assert account_handle == "ah_test_account_handle_1234"
        assert secret.reveal_username() == "browser@example.test"
        assert secret.reveal_password() == "browser-password"
        return PasswordAuthenticationResult(
            provider_subject="provider-browser-subject",
            role_keys=("sales",),
        )


def adapter_context(origin: str = ORIGIN) -> AdapterContext:
    return AdapterContext(
        tenant_id=uuid7(),
        project_id=uuid7(),
        environment_id=uuid7(),
        origin=origin,
        request_id=f"playwright-test-{uuid7()}",
    )


@pytest.mark.anyio
async def test_playwright_target_exports_indexed_db_storage_state_in_memory() -> None:
    runtime = FakeBrowserRuntime()
    target = PlaywrightPasswordSessionTarget(
        cast(IsolatedBrowserRuntime, runtime),
        cast(PasswordLoginFlow, FakePasswordLoginFlow()),
        allowed_origins=(ORIGIN,),
    )
    session = await target.establish_session(
        context=adapter_context(),
        account_handle="ah_test_account_handle_1234",
        secret=PasswordSecret(
            username="browser@example.test",
            password="browser-password",
        ),
    )

    async def copy_state(state: memoryview) -> bytes:
        return bytes(state)

    payload = loads(await session.with_storage_state(copy_state))
    assert payload["cookies"][0]["name"] == "provider_session"
    assert session.provider_subject == "provider-browser-subject"
    assert session.role_keys == ("sales",)
    assert runtime.contexts == 1


@pytest.mark.anyio
async def test_playwright_target_probe_reports_ready_and_origin_denied() -> None:
    runtime = FakeBrowserRuntime()
    target = PlaywrightPasswordSessionTarget(
        cast(IsolatedBrowserRuntime, runtime),
        cast(PasswordLoginFlow, FakePasswordLoginFlow()),
        allowed_origins=(ORIGIN,),
    )

    ready = await target.probe(adapter_context())
    denied = await target.probe(adapter_context("https://other.example.test"))

    assert ready.state is ProviderHealthState.HEALTHY
    assert denied.state is ProviderHealthState.UNAVAILABLE
    assert runtime.contexts == 1


@pytest.mark.anyio
async def test_playwright_target_rejects_an_origin_before_opening_a_context() -> None:
    runtime = FakeBrowserRuntime()
    target = PlaywrightPasswordSessionTarget(
        cast(IsolatedBrowserRuntime, runtime),
        cast(PasswordLoginFlow, FakePasswordLoginFlow()),
        allowed_origins=(ORIGIN,),
    )

    with pytest.raises(AdapterOperationError):
        await target.authenticate(
            context=adapter_context("https://other.example.test"),
            account_handle="ah_test_account_handle_1234",
            secret=PasswordSecret(username="hidden", password="hidden"),
        )
    assert runtime.contexts == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (PlaywrightTimeoutError("timeout"), AdapterErrorCode.NETWORK_TIMEOUT),
        (PlaywrightError("browser failed"), AdapterErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
async def test_playwright_target_maps_browser_failures_to_safe_adapter_errors(
    error: Exception,
    expected_code: AdapterErrorCode,
) -> None:
    target = PlaywrightPasswordSessionTarget(
        cast(IsolatedBrowserRuntime, FailingBrowserRuntime(error)),
        cast(PasswordLoginFlow, FakePasswordLoginFlow()),
        allowed_origins=(ORIGIN,),
    )

    with pytest.raises(AdapterOperationError) as raised:
        await target.authenticate(
            context=adapter_context(),
            account_handle="ah_test_account_handle_1234",
            secret=PasswordSecret(username="hidden", password="hidden"),
        )
    assert raised.value.error.code is expected_code


@pytest.mark.anyio
async def test_playwright_target_probe_hides_browser_start_failure() -> None:
    target = PlaywrightPasswordSessionTarget(
        cast(
            IsolatedBrowserRuntime,
            FailingBrowserRuntime(PlaywrightError("driver unavailable")),
        ),
        cast(PasswordLoginFlow, FakePasswordLoginFlow()),
        allowed_origins=(ORIGIN,),
    )

    health = await target.probe(adapter_context())

    assert health.state is ProviderHealthState.UNAVAILABLE


@pytest.mark.anyio
@pytest.mark.parametrize("browser_name", ["chromium", "firefox", "webkit"])
async def test_playwright_runtime_reuses_browser_and_isolates_contexts(
    monkeypatch: pytest.MonkeyPatch,
    browser_name: Literal["chromium", "firefox", "webkit"],
) -> None:
    browser = FakeLaunchedBrowser()
    browser_type = FakeBrowserType(browser)
    driver = FakePlaywrightDriver(browser_type)
    manager = FakePlaywrightManager(driver)
    monkeypatch.setattr(playwright_password, "async_playwright", lambda: manager)
    runtime = PlaywrightBrowserRuntime(
        browser_name=browser_name,
        maximum_concurrency=1,
    )

    await runtime.start()
    await runtime.start()
    async with runtime.isolated_context():
        pass
    await runtime.close()
    await runtime.close()

    assert browser_type.launches == 1
    assert browser.contexts == 1
    assert browser.closed
    assert driver.stopped


def test_playwright_runtime_rejects_unbounded_concurrency() -> None:
    with pytest.raises(ValueError):
        PlaywrightBrowserRuntime(maximum_concurrency=0)
