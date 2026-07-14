"""Dispatch boundary between the API process and the Auth Session Worker."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from atlas_testops.application.access import ActorContext
from atlas_testops.domain.identity import EnsureLoginSession, EnsureLoginSessionResult

if TYPE_CHECKING:
    from atlas_testops.application.sessions import AuthSessionService


class AuthSessionDispatcher(Protocol):
    """Submit an origin-bound session request without loading browser or vault code."""

    async def ensure(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
    ) -> EnsureLoginSessionResult: ...


class InlineAuthSessionDispatcher:
    """Run the service inline only for isolated tests and explicit embedded runtimes."""

    def __init__(self, service: AuthSessionService) -> None:
        self._service = service

    async def ensure(
        self,
        actor: ActorContext,
        lease_id: UUID,
        command: EnsureLoginSession,
    ) -> EnsureLoginSessionResult:
        return await self._service.ensure(actor, lease_id, command)
