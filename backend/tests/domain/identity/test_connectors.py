"""ConnectorInstallation 命令、投影和安全配置边界测试。"""

from datetime import UTC, datetime
from uuid import uuid7

import pytest
from pydantic import ValidationError

from atlas_testops.domain.identity import (
    ConnectorInstallationRecord,
    ConnectorMode,
    ConnectorStatus,
    CreateConnectorInstallation,
    ProviderCapability,
    UpdateConnectorInstallation,
)


def test_connector_command_normalizes_origins_capabilities_and_hides_config() -> None:
    command = CreateConnectorInstallation(
        environment_id=uuid7(),
        installation_key="primary-password",
        name=" Primary ",
        adapter_key="generic-password",
        mode=ConnectorMode.MANAGED_TEST_ACCOUNTS,
        configuration_ref="cfg_connector_private_01",
        allowed_origins=("HTTPS://EXAMPLE.test:443/", "https://example.test"),
        required_capabilities=(
            ProviderCapability.AUTH_PASSWORD,
            ProviderCapability.AUTH_PASSWORD,
        ),
    )

    assert command.name == "Primary"
    assert command.allowed_origins == ("https://example.test",)
    assert command.required_capabilities == (ProviderCapability.AUTH_PASSWORD,)
    assert "cfg_connector_private_01" not in repr(command)


def test_observe_only_connector_rejects_auth_or_mutating_capability() -> None:
    with pytest.raises(ValidationError, match="observe-only"):
        CreateConnectorInstallation(
            environment_id=uuid7(),
            installation_key="observer",
            name="Observer",
            adapter_key="generic-password",
            mode=ConnectorMode.OBSERVE_ONLY,
            configuration_ref="cfg_connector_observer_01",
            allowed_origins=("https://example.test",),
            required_capabilities=(ProviderCapability.AUTH_PASSWORD,),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": ConnectorStatus.ACTIVE},
        {"status": ConnectorStatus.DEGRADED},
        {
            "mode": ConnectorMode.OBSERVE_ONLY,
            "required_capabilities": (ProviderCapability.AUTH_PASSWORD,),
        },
    ],
)
def test_connector_update_rejects_empty_health_or_invalid_observer(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        UpdateConnectorInstallation.model_validate(payload)


def test_internal_connector_record_projects_without_configuration_ref() -> None:
    now = datetime.now(UTC)
    record = ConnectorInstallationRecord(
        id=uuid7(),
        tenant_id=uuid7(),
        project_id=uuid7(),
        environment_id=uuid7(),
        installation_key="password",
        name="Password",
        adapter_key="generic-password",
        mode=ConnectorMode.MANAGED_TEST_ACCOUNTS,
        configuration_ref="cfg_connector_private_02",
        allowed_origins=("https://example.test",),
        required_capabilities=(ProviderCapability.AUTH_PASSWORD,),
        status=ConnectorStatus.DRAFT,
        health_state=None,
        safe_message=None,
        protocol_version=None,
        implementation_version=None,
        last_validated_at=None,
        revision=1,
        created_at=now,
        updated_at=now,
    )

    public = record.to_public(())

    assert "configurationRef" not in public.model_dump(mode="json", by_alias=True)
    assert "cfg_connector_private_02" not in repr(record)
