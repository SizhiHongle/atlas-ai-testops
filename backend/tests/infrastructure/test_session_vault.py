"""Security tests for one-shot session state and AES-GCM object storage."""

from json import loads
from uuid import uuid7

import pytest

from atlas_testops.application.ports.sessions import (
    AuthenticatedBrowserSession,
    SessionArtifactScope,
)
from atlas_testops.domain.identity import CredentialAuthMethod
from atlas_testops.infrastructure.session_vault import (
    AesGcmSessionArtifactVault,
    InMemorySessionObjectStore,
    SessionArtifactIntegrityError,
    SessionVaultUnavailableError,
)

ORIGIN = "https://staging.example.test"


def artifact_scope() -> SessionArtifactScope:
    return SessionArtifactScope(
        artifact_id=uuid7(),
        tenant_id=uuid7(),
        project_id=uuid7(),
        environment_id=uuid7(),
        lease_id=uuid7(),
        lease_fence=17,
        account_id=uuid7(),
        connector_installation_id=uuid7(),
        credential_binding_id=uuid7(),
        allowed_origins=(ORIGIN,),
    )


@pytest.mark.anyio
async def test_vault_ciphertext_contains_no_cookie_or_token_plaintext() -> None:
    store = InMemorySessionObjectStore()
    vault = AesGcmSessionArtifactVault(
        store,
        bucket="atlas-sessions",
        key=b"k" * 32,
        key_version="local-v1",
    )
    scope = artifact_scope()
    object_ref = vault.object_ref_for(
        tenant_id=scope.tenant_id,
        artifact_id=scope.artifact_id,
    )
    plaintext = (
        b'{"cookies":[{"name":"session","value":"cookie-secret-value"}],'
        b'"origins":[{"origin":"https://staging.example.test",'
        b'"localStorage":[{"name":"token","value":"access-token-value"}]}]}'
    )

    sealed = await vault.seal(
        object_ref=object_ref,
        scope=scope,
        plaintext=memoryview(plaintext),
    )
    key = f"tenants/{scope.tenant_id.hex}/sessions/{scope.artifact_id.hex}.json"
    ciphertext = await store.ciphertext_for_test(key)

    assert ciphertext is not None
    assert b"cookie-secret-value" not in ciphertext
    assert b"access-token-value" not in ciphertext
    assert sealed.object_digest.startswith("sha256:")
    assert sealed.object_size_bytes == len(ciphertext)

    async def inspect(value: memoryview) -> str:
        state = loads(bytes(value))
        cookie_value = state["cookies"][0]["value"]
        if not isinstance(cookie_value, str):
            raise TypeError("test Storage State cookie value must be a string")
        return cookie_value

    restored = await vault.with_decrypted(
        object_ref=object_ref,
        scope=scope,
        expected_digest=sealed.object_digest,
        expected_key_version=sealed.key_version,
        operation=inspect,
    )
    assert restored == "cookie-secret-value"


@pytest.mark.anyio
async def test_vault_rejects_scope_tampering_and_deletes_idempotently() -> None:
    store = InMemorySessionObjectStore()
    vault = AesGcmSessionArtifactVault(
        store,
        bucket="atlas-sessions",
        key=b"z" * 32,
        key_version="local-v1",
    )
    scope = artifact_scope()
    object_ref = vault.object_ref_for(
        tenant_id=scope.tenant_id,
        artifact_id=scope.artifact_id,
    )
    sealed = await vault.seal(
        object_ref=object_ref,
        scope=scope,
        plaintext=memoryview(b'{"cookies":[],"origins":[]}'),
    )
    tampered = SessionArtifactScope(
        artifact_id=scope.artifact_id,
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        environment_id=scope.environment_id,
        lease_id=scope.lease_id,
        lease_fence=scope.lease_fence + 1,
        account_id=scope.account_id,
        connector_installation_id=scope.connector_installation_id,
        credential_binding_id=scope.credential_binding_id,
        allowed_origins=scope.allowed_origins,
    )

    async def ignore(_value: memoryview) -> None:
        return None

    with pytest.raises(SessionArtifactIntegrityError):
        await vault.with_decrypted(
            object_ref=object_ref,
            scope=tampered,
            expected_digest=sealed.object_digest,
            expected_key_version=sealed.key_version,
            operation=ignore,
        )

    await vault.delete(object_ref)
    await vault.delete(object_ref)
    with pytest.raises(SessionVaultUnavailableError):
        await vault.with_decrypted(
            object_ref=object_ref,
            scope=scope,
            expected_digest=sealed.object_digest,
            expected_key_version=sealed.key_version,
            operation=ignore,
        )


@pytest.mark.anyio
async def test_authenticated_browser_state_is_one_shot_and_redacted() -> None:
    state = AuthenticatedBrowserSession(
        provider_subject="subject-01",
        role_keys=("sales",),
        auth_strength=(CredentialAuthMethod.PASSWORD,),
        storage_state=b'{"cookies":[{"value":"sensitive-cookie"}]}',
    )

    async def consume(value: memoryview) -> int:
        assert b"sensitive-cookie" in bytes(value)
        return len(value)

    consumed = await state.with_storage_state(consume)
    assert consumed > 0
    assert "sensitive-cookie" not in repr(state)

    with pytest.raises(RuntimeError, match="already been consumed"):
        await state.with_storage_state(consume)
