"""测试 Mock Provider、Secret 闭包与 Generic Password Adapter。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from atlas_testops.application.ports.providers import AdapterContext, AdapterOperationError
from atlas_testops.application.ports.secrets import (
    PasswordSecretScope,
    SecretProviderError,
)
from atlas_testops.domain.identity import (
    AccountSource,
    AdapterErrorCode,
    CapabilityRequirement,
    CredentialAuthMethod,
    CredentialPurpose,
    ProviderCapability,
    SecretGrantRecord,
    SecretGrantStatus,
)
from atlas_testops.infrastructure.adapters.generic_password import GenericPasswordAdapter
from atlas_testops.infrastructure.adapters.mock_provider import MockIdentityProvider
from atlas_testops.infrastructure.repositories.secret_grants import CredentialSecretAccess
from atlas_testops.infrastructure.secrets import InMemorySecretProvider

ORIGIN = "https://staging.example.test"
USERNAME = "sales@example.test"
PASSWORD = "adapter-secret-password"


def access_record() -> CredentialSecretAccess:
    now = datetime.now(UTC)
    grant = SecretGrantRecord(
        id=uuid7(),
        tenant_id=uuid7(),
        project_id=uuid7(),
        environment_id=uuid7(),
        connector_installation_id=uuid7(),
        lease_id=uuid7(),
        account_id=uuid7(),
        credential_binding_id=uuid7(),
        fencing_token=1,
        purpose=CredentialPurpose.LOGIN,
        worker_identity="worker-adapter-01",
        token_hash="b" * 64,
        allowed_origins=(ORIGIN,),
        status=SecretGrantStatus.REDEEMED,
        issued_at=now,
        expires_at=now + timedelta(minutes=1),
        redeemed_at=now,
        terminated_at=None,
        termination_reason=None,
        revision=2,
        updated_at=now,
    )
    return CredentialSecretAccess(
        grant=grant,
        auth_method=CredentialAuthMethod.PASSWORD,
        account_source=AccountSource.ATLAS_MANAGED,
        external_subject_id=None,
        identity_fingerprint="sha256:" + "a" * 64,
        role_key="sales",
        secret_ref="sec_adapter_password_01",
        secret_version="v1",
    )


def adapter_context(
    access: CredentialSecretAccess,
    *,
    secret_scope: PasswordSecretScope | None = None,
) -> AdapterContext:
    if secret_scope is not None:
        return AdapterContext.for_password_operation(
            tenant_id=access.grant.tenant_id,
            project_id=access.grant.project_id,
            environment_id=access.grant.environment_id,
            origin=ORIGIN,
            request_id="adapter-contract-test",
            secret_scope=secret_scope,
        )
    return AdapterContext(
        tenant_id=access.grant.tenant_id,
        project_id=access.grant.project_id,
        environment_id=access.grant.environment_id,
        origin=ORIGIN,
        request_id="adapter-contract-test",
    )


@pytest.mark.anyio
async def test_generic_password_adapter_contract_and_secret_closure() -> None:
    access = access_record()
    secrets = InMemorySecretProvider()
    secrets.put_password(
        secret_ref="sec_adapter_password_01",
        secret_version="v1",
        username=USERNAME,
        password=PASSWORD,
    )
    provider = MockIdentityProvider(allowed_origins=(ORIGIN,))
    provider.register_account(
        account_handle="ah_abcdefghijklmnopqrstuvwxyz123456",
        provider_subject="mock-sales-01",
        username=USERNAME,
        password=PASSWORD,
    )
    adapter = GenericPasswordAdapter(provider)
    secret_scope = PasswordSecretScope(
        provider=secrets,
        secret_ref="sec_adapter_password_01",
        secret_version="v1",
    )
    context = adapter_context(
        access,
        secret_scope=secret_scope,
    )

    manifest = adapter.manifest()
    assert manifest.adapter_key == "generic-password"
    negotiated = await adapter.negotiate(
        context,
        CapabilityRequirement(required=(ProviderCapability.AUTH_PASSWORD,)),
    )
    assert negotiated.capabilities == manifest.capabilities
    result = await adapter.authenticate(
        context=context,
        account_handle="ah_abcdefghijklmnopqrstuvwxyz123456",
    )

    assert result.provider_subject == "mock-sales-01"
    assert provider.authentication_attempts == 1
    assert USERNAME not in repr(access)
    assert PASSWORD not in repr(access)
    assert "sec_adapter_password_01" not in repr(context)
    assert "sec_adapter_password_01" not in repr(secret_scope)
    assert not hasattr(context, "get_secret")
    assert not hasattr(context, "secret_ref")
    assert not hasattr(context, "secret_version")


@pytest.mark.anyio
async def test_adapter_rejects_unsupported_capability_with_safe_error() -> None:
    access = access_record()
    adapter = GenericPasswordAdapter(MockIdentityProvider(allowed_origins=(ORIGIN,)))

    with pytest.raises(AdapterOperationError) as raised:
        await adapter.negotiate(
            adapter_context(access),
            CapabilityRequirement(required=(ProviderCapability.AUTH_OIDC,)),
        )

    assert raised.value.error.code is AdapterErrorCode.CAPABILITY_UNSUPPORTED
    assert "secret" not in raised.value.error.model_dump_json().lower()


@pytest.mark.anyio
async def test_missing_secret_raises_safe_provider_error() -> None:
    access = access_record()
    provider = MockIdentityProvider(allowed_origins=(ORIGIN,))
    adapter = GenericPasswordAdapter(provider)

    with pytest.raises(SecretProviderError) as raised:
        await adapter.authenticate(
            context=adapter_context(
                access,
                secret_scope=PasswordSecretScope(
                    provider=InMemorySecretProvider(),
                    secret_ref="sec_adapter_password_01",
                    secret_version="v1",
                ),
            ),
            account_handle="ah_abcdefghijklmnopqrstuvwxyz123456",
        )

    assert "sec_adapter_password_01" not in str(raised.value)


@pytest.mark.anyio
async def test_adapter_cannot_authenticate_without_a_bound_secret_scope() -> None:
    access = access_record()
    adapter = GenericPasswordAdapter(MockIdentityProvider(allowed_origins=(ORIGIN,)))

    with pytest.raises(SecretProviderError) as raised:
        await adapter.authenticate(
            context=adapter_context(access),
            account_handle="ah_abcdefghijklmnopqrstuvwxyz123456",
        )

    assert str(raised.value) == "password material is unavailable"


def test_adapter_context_rejects_blank_request_id() -> None:
    access = access_record()

    with pytest.raises(ValueError, match="request_id"):
        AdapterContext(
            tenant_id=access.grant.tenant_id,
            project_id=access.grant.project_id,
            environment_id=access.grant.environment_id,
            origin=ORIGIN,
            request_id=" ",
        )
