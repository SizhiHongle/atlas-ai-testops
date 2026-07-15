"""Short-lived Browser Runtime permits and request signature tests."""

from base64 import b64encode
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest

from atlas_testops.infrastructure.browser_auth import (
    BrowserRuntimeAuthenticationError,
    BrowserRuntimePermitSigner,
    BrowserRuntimeRequestSigner,
)


def test_permit_and_request_signature_bind_exact_run_worker_and_body() -> None:
    now = datetime.now(UTC)
    tenant_id = uuid7()
    run_id = uuid7()
    permit_signer = BrowserRuntimePermitSigner(b"p" * 32, maximum_lifetime=timedelta(hours=1))
    request_signer = BrowserRuntimeRequestSigner(
        b"r" * 32,
        maximum_clock_skew=timedelta(seconds=30),
    )
    permit = permit_signer.mint(
        tenant_id=tenant_id,
        run_id=run_id,
        worker_identity="browser-worker-01",
        issued_at=now,
        expires_at=now + timedelta(minutes=20),
    )
    body = b'{"executionContractId":"contract"}'
    headers = request_signer.sign_headers(
        method="POST",
        path=f"/internal/v1/debug-runs/{run_id}/browser-execution:start",
        body=body,
        tenant_id=tenant_id,
        worker_identity="browser-worker-01",
        permit=permit,
        now=now,
        nonce="n" * 24,
    )
    identity = request_signer.verify_headers(
        method="POST",
        path=f"/internal/v1/debug-runs/{run_id}/browser-execution:start",
        body=body,
        headers=headers,
        now=now,
    )
    claims = permit_signer.verify(identity.permit, now=now)
    assert claims.tenant_id == tenant_id
    assert claims.run_id == run_id
    assert identity.worker_identity == "browser-worker-01"

    with pytest.raises(BrowserRuntimeAuthenticationError):
        request_signer.verify_headers(
            method="POST",
            path=f"/internal/v1/debug-runs/{run_id}/browser-execution:start",
            body=b"{}",
            headers=headers,
            now=now,
        )
    with pytest.raises(BrowserRuntimeAuthenticationError):
        request_signer.verify_headers(
            method="POST",
            path=f"/internal/v1/debug-runs/{uuid7()}/browser-execution:start",
            body=body,
            headers=headers,
            now=now,
        )


def test_permit_and_signature_expire_fail_closed() -> None:
    now = datetime.now(UTC)
    signer = BrowserRuntimePermitSigner(b"p" * 32, maximum_lifetime=timedelta(minutes=10))
    permit = signer.mint(
        tenant_id=uuid7(),
        run_id=uuid7(),
        worker_identity="browser-worker",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    with pytest.raises(BrowserRuntimeAuthenticationError, match="expired"):
        signer.verify(permit, now=now + timedelta(minutes=5))
    tampered = f"{permit[:-1]}{'A' if permit[-1] != 'A' else 'B'}"
    with pytest.raises(BrowserRuntimeAuthenticationError, match="invalid"):
        signer.verify(tampered, now=now)

    request_signer = BrowserRuntimeRequestSigner(
        b"r" * 32,
        maximum_clock_skew=timedelta(seconds=5),
    )
    headers = request_signer.sign_headers(
        method="GET",
        path="/internal/v1/debug-runs/run/browser-execution",
        body=b"",
        tenant_id=uuid7(),
        worker_identity="browser-worker",
        permit=permit,
        now=now,
    )
    with pytest.raises(BrowserRuntimeAuthenticationError):
        request_signer.verify_headers(
            method="GET",
            path="/internal/v1/debug-runs/run/browser-execution",
            body=b"",
            headers=headers,
            now=now + timedelta(seconds=6),
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: BrowserRuntimePermitSigner(b"short", maximum_lifetime=timedelta(minutes=5)),
        lambda: BrowserRuntimePermitSigner(b"p" * 32, maximum_lifetime=timedelta(seconds=1)),
        lambda: BrowserRuntimeRequestSigner(
            b"short",
            maximum_clock_skew=timedelta(seconds=30),
        ),
        lambda: BrowserRuntimeRequestSigner(
            b"r" * 32,
            maximum_clock_skew=timedelta(seconds=1),
        ),
    ],
)
def test_invalid_signer_configuration_is_rejected(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


def test_base64_factories_validate_key_material_and_permit_window() -> None:
    encoded = b64encode(b"k" * 32).decode()
    BrowserRuntimePermitSigner.from_base64_key(
        encoded,
        maximum_lifetime=timedelta(hours=1),
    )
    BrowserRuntimeRequestSigner.from_base64_key(encoded)
    with pytest.raises(ValueError, match="base64"):
        BrowserRuntimeRequestSigner.from_base64_key("not-base64")

    signer = BrowserRuntimePermitSigner(b"p" * 32, maximum_lifetime=timedelta(minutes=5))
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="window"):
        signer.mint(
            tenant_id=uuid7(),
            run_id=uuid7(),
            worker_identity="browser-worker",
            issued_at=now,
            expires_at=now + timedelta(minutes=6),
        )
