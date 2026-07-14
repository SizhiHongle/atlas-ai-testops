"""Dispatch boundary between the API process and the Fixture Worker."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from atlas_testops.domain.fixture import FixtureCleanupSweepBatch, FixtureRun


class FixtureRunDispatcher(Protocol):
    """Start and signal one durable fixture workflow by its frozen identity."""

    async def start(self, run: FixtureRun) -> None: ...

    async def release(self, run: FixtureRun) -> None: ...

    async def cancel(self, run: FixtureRun) -> None: ...

    async def retry_cleanup(self, run: FixtureRun) -> None: ...

    async def sweep(
        self,
        *,
        tenant_id: UUID,
        worker_identity: str,
        limit: int,
    ) -> FixtureCleanupSweepBatch: ...
