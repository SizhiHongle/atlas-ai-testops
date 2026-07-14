"""Dispatch boundary between the API process and the Fixture Worker."""

from __future__ import annotations

from typing import Protocol

from atlas_testops.domain.fixture import FixtureRun


class FixtureRunDispatcher(Protocol):
    """Start and signal one durable fixture workflow by its frozen identity."""

    async def start(self, run: FixtureRun) -> None: ...

    async def release(self, run: FixtureRun) -> None: ...
