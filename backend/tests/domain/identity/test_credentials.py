"""测试 Secret Grant、Origin 与秘密对象的领域边界。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.application.ports.secrets import PasswordSecret
from atlas_testops.domain.identity import (
    CredentialPurpose,
    IssueSecretGrant,
    SecretGrant,
    SecretGrantRecord,
    SecretGrantStatus,
)
from atlas_testops.domain.platform import CreateEnvironment, EnvironmentKind


def test_environment_origins_are_exact_normalized_and_unique() -> None:
    environment = CreateEnvironment(
        environment_key="pre-test",
        name="Pre Test",
        kind=EnvironmentKind.TEST,
        allowed_origins=(
            "HTTPS://Example.TEST:443/",
            "https://example.test",
            "http://127.0.0.1:8080",
            "HTTPS://[2001:0DB8::1]:443/",
        ),
    )

    assert environment.allowed_origins == (
        "http://127.0.0.1:8080",
        "https://[2001:db8::1]",
        "https://example.test",
    )


@pytest.mark.parametrize(
    "origin",
    [
        "ftp://example.test",
        "https://user@example.test",
        "https://example.test/login",
        "https://example.test?token=value",
        "https://example.test/#fragment",
        "https://example.test:99999",
        "http://example.test:0",
        "https://bad_host.example.test",
        "https://example..test",
        "https://-example.test",
        "https://127.1",
        "https://例子.test",
    ],
)
def test_environment_rejects_non_origin_urls(origin: str) -> None:
    with pytest.raises(ValidationError):
        CreateEnvironment(
            environment_key="pre-test",
            name="Pre Test",
            kind=EnvironmentKind.TEST,
            allowed_origins=(origin,),
        )


def test_production_environment_rejects_http_origin() -> None:
    with pytest.raises(ValidationError):
        CreateEnvironment(
            environment_key="production",
            name="Production",
            kind=EnvironmentKind.PRODUCTION,
            allowed_origins=("http://production.example.test",),
        )


def test_issue_secret_grant_normalizes_origins_and_hides_ref_from_repr() -> None:
    command = IssueSecretGrant(
        fencing_token=3,
        purpose=CredentialPurpose.LOGIN,
        worker_identity="worker-secret-01",
        allowed_origins=("https://EXAMPLE.test:443/",),
    )
    grant_ref = "sgr_abcdefghijklmnopqrstuvwxyzABCDEFGH12345678"
    grant = SecretGrant(
        grant_ref=grant_ref,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    assert command.allowed_origins == ("https://example.test",)
    assert grant.model_dump(mode="json", by_alias=True)["grantRef"] == grant_ref
    assert grant_ref not in repr(grant)


def test_secret_grant_record_rejects_mismatched_terminal_metadata() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        SecretGrantRecord(
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
            worker_identity="worker-secret-01",
            token_hash="a" * 64,
            allowed_origins=("https://example.test",),
            status=SecretGrantStatus.REDEEMED,
            issued_at=now,
            expires_at=now + timedelta(minutes=1),
            redeemed_at=None,
            terminated_at=None,
            termination_reason=None,
            revision=1,
            updated_at=now,
        )


def test_password_secret_repr_never_contains_material() -> None:
    secret = PasswordSecret(username="user@example.test", password="super-secret-value")

    rendered = repr(secret)
    assert "user@example.test" not in rendered
    assert "super-secret-value" not in rendered
    assert "**********" in rendered
