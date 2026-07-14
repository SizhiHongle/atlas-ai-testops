"""Execution-plane ports for authenticated browser state and encrypted storage."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from atlas_testops.application.ports.providers import AdapterContext
from atlas_testops.domain.identity import CredentialAuthMethod, PasswordAuthenticationResult

type SessionStateOperation[T] = Callable[[memoryview], Awaitable[T]]


class AuthenticatedBrowserSession:
    """One-shot in-memory browser state with safe identity metadata."""

    __slots__ = (
        "_consumed",
        "_storage_state",
        "auth_strength",
        "provider_subject",
        "role_keys",
    )

    def __init__(
        self,
        *,
        provider_subject: str,
        role_keys: tuple[str, ...],
        auth_strength: tuple[CredentialAuthMethod, ...],
        storage_state: bytes,
    ) -> None:
        identity = PasswordAuthenticationResult(
            provider_subject=provider_subject,
            role_keys=role_keys,
        )
        normalized_strength = tuple(sorted(set(auth_strength), key=lambda item: item.value))
        if not normalized_strength:
            raise ValueError("auth_strength must not be empty")
        if not storage_state or len(storage_state) > 8 * 1024 * 1024:
            raise ValueError("storage_state must contain between 1 byte and 8 MiB")
        self.provider_subject = identity.provider_subject
        self.role_keys = identity.role_keys
        self.auth_strength = normalized_strength
        self._storage_state = bytearray(storage_state)
        self._consumed = False

    def __repr__(self) -> str:
        return (
            "AuthenticatedBrowserSession("
            f"provider_subject={self.provider_subject!r}, "
            f"role_keys={self.role_keys!r}, auth_strength={self.auth_strength!r}, "
            "storage_state=<redacted>)"
        )

    async def with_storage_state[T](self, operation: SessionStateOperation[T]) -> T:
        """Consume storage state once and overwrite the owned buffer afterwards."""

        if self._consumed:
            raise RuntimeError("authenticated browser state has already been consumed")
        self._consumed = True
        try:
            return await operation(memoryview(self._storage_state))
        finally:
            self._storage_state[:] = b"\x00" * len(self._storage_state)

    def discard(self) -> None:
        """Erase unconsumed state when policy validation rejects the session."""

        if self._consumed:
            return
        self._consumed = True
        self._storage_state[:] = b"\x00" * len(self._storage_state)


class PasswordSessionAdapter(Protocol):
    """Trusted adapter capable of producing a password-authenticated session."""

    async def establish_session(
        self,
        *,
        context: AdapterContext,
        account_handle: str,
    ) -> AuthenticatedBrowserSession: ...


@dataclass(frozen=True, slots=True)
class SessionArtifactScope:
    """Authenticated metadata bound to the ciphertext through AEAD AAD."""

    artifact_id: UUID
    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    lease_id: UUID
    lease_fence: int
    account_id: UUID
    connector_installation_id: UUID
    credential_binding_id: UUID
    allowed_origins: tuple[str, ...]
    format_version: str = "playwright-storage-state/v1"


@dataclass(frozen=True, slots=True)
class SealedSessionArtifact:
    """Safe metadata returned after plaintext has been encrypted and uploaded."""

    object_ref: str
    object_digest: str
    object_size_bytes: int
    key_version: str


type DecryptedSessionOperation[T] = Callable[[memoryview], Awaitable[T]]


class SessionArtifactVault(Protocol):
    """Store and consume encrypted session state without returning plaintext."""

    def object_ref_for(self, *, tenant_id: UUID, artifact_id: UUID) -> str: ...

    async def seal(
        self,
        *,
        object_ref: str,
        scope: SessionArtifactScope,
        plaintext: memoryview,
    ) -> SealedSessionArtifact: ...

    async def with_decrypted[T](
        self,
        *,
        object_ref: str,
        scope: SessionArtifactScope,
        expected_digest: str,
        expected_key_version: str,
        operation: DecryptedSessionOperation[T],
    ) -> T: ...

    async def delete(self, object_ref: str) -> None: ...
