"""Dispatch boundary between the API process and the future Browser Worker."""

from typing import Protocol

from atlas_testops.domain.case import DebugRun


class DebugRunDispatcher(Protocol):
    """Start or signal a durable workflow using only its frozen snapshot."""

    async def start(self, run: DebugRun) -> None: ...

    async def cancel(self, run: DebugRun) -> None: ...
