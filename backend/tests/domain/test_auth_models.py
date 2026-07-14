"""平台主体、成员关系与登录命令测试。"""

from uuid import uuid7

import pytest
from pydantic import SecretStr, ValidationError

from atlas_testops.domain.auth import (
    BootstrapPrincipalCommand,
    LoginCommand,
    normalize_email_address,
)


def test_normalizes_email_for_identity_matching() -> None:
    assert normalize_email_address("  Owner@Example.COM  ") == "owner@example.com"

    command = LoginCommand(
        tenant_id=uuid7(),
        project_id=uuid7(),
        email=" Owner@Example.COM ",
        password=SecretStr("correct horse battery staple"),
    )
    assert command.email == "owner@example.com"


@pytest.mark.parametrize(
    "email",
    ["missing-at.example.com", "missing.domain@example", "white space@example.com"],
)
def test_rejects_invalid_email_addresses(email: str) -> None:
    with pytest.raises(ValueError, match="email address is invalid"):
        normalize_email_address(email)


def test_bootstrap_requires_strong_minimum_password_and_hides_value() -> None:
    with pytest.raises(ValidationError):
        BootstrapPrincipalCommand(
            tenant_id=uuid7(),
            project_id=uuid7(),
            email="owner@example.com",
            display_name="Owner",
            password=SecretStr("too-short"),
        )

    command = BootstrapPrincipalCommand(
        tenant_id=uuid7(),
        project_id=uuid7(),
        email="owner@example.com",
        display_name="  Atlas Owner  ",
        password=SecretStr("correct horse battery staple"),
    )
    assert command.display_name == "Atlas Owner"
    assert "correct horse" not in repr(command)
