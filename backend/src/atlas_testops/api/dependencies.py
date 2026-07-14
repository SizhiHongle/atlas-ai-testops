"""共享 FastAPI 依赖。"""

from datetime import timedelta
from typing import Annotated, cast

from fastapi import Depends, Request

from atlas_testops.application.account_health import AccountHealthService
from atlas_testops.application.auth import AuthService
from atlas_testops.application.connectors import ConnectorService
from atlas_testops.application.credentials import CredentialBrokerService
from atlas_testops.application.fixture_assets import FixtureAssetService
from atlas_testops.application.identity import IdentityService
from atlas_testops.application.leases import LeaseService
from atlas_testops.application.platform import PlatformService
from atlas_testops.application.ports.secrets import SecretProvider
from atlas_testops.application.session_dispatcher import AuthSessionDispatcher
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.infrastructure.adapters.registry import AdapterRegistry
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.passwords import PasswordService


def get_app_settings(request: Request) -> Settings:
    """返回 Application Factory 绑定的配置。"""
    return cast(Settings, request.app.state.settings)


def get_database(request: Request) -> Database:
    """返回已经由 lifespan 打开的数据库组件。"""

    database = cast(Database | None, request.app.state.database)
    if database is None:
        raise RuntimeError("database is not configured")
    return database


def get_optional_database(request: Request) -> Database | None:
    """健康检查可以在数据库未配置的本地模式下运行。"""

    return cast(Database | None, request.app.state.database)


def get_password_service(request: Request) -> PasswordService:
    """返回 Application Factory 创建的受限并发 Password Service。"""

    return cast(PasswordService, request.app.state.password_service)


def get_adapter_registry(request: Request) -> AdapterRegistry:
    """返回进程启动时构造的显式 Adapter Registry。"""

    return cast(AdapterRegistry, request.app.state.adapter_registry)


def get_secret_provider(request: Request) -> SecretProvider | None:
    """返回部署时注入的 Secret Provider；未配置时保持 fail-closed。"""

    return cast(SecretProvider | None, request.app.state.secret_provider)


def get_auth_session_dispatcher(request: Request) -> AuthSessionDispatcher:
    """Return the isolated worker dispatcher or fail closed when it is disabled."""

    dispatcher = cast(
        AuthSessionDispatcher | None,
        request.app.state.auth_session_dispatcher,
    )
    if dispatcher is None:
        raise ApplicationError(
            error_code=ErrorCode.SESSION_UNAVAILABLE,
            title="Auth Session Worker 未配置",
            detail="当前 API 实例未连接独立 Auth Session Worker。",
            status_code=503,
        )
    return dispatcher


SettingsDependency = Annotated[Settings, Depends(get_app_settings)]
DatabaseDependency = Annotated[Database, Depends(get_database)]
OptionalDatabaseDependency = Annotated[Database | None, Depends(get_optional_database)]
PasswordServiceDependency = Annotated[PasswordService, Depends(get_password_service)]
AdapterRegistryDependency = Annotated[AdapterRegistry, Depends(get_adapter_registry)]
SecretProviderDependency = Annotated[
    SecretProvider | None,
    Depends(get_secret_provider),
]
AuthSessionDispatcherDependency = Annotated[
    AuthSessionDispatcher,
    Depends(get_auth_session_dispatcher),
]


def get_platform_service(database: DatabaseDependency) -> PlatformService:
    """为请求创建无状态 Platform 应用服务。"""

    return PlatformService(database)


PlatformServiceDependency = Annotated[PlatformService, Depends(get_platform_service)]


def get_fixture_asset_service(database: DatabaseDependency) -> FixtureAssetService:
    """Create a stateless fixture asset control-plane service."""

    return FixtureAssetService(database)


FixtureAssetServiceDependency = Annotated[
    FixtureAssetService,
    Depends(get_fixture_asset_service),
]


def get_identity_service(database: DatabaseDependency) -> IdentityService:
    """为请求创建无状态 Identity 应用服务。"""

    return IdentityService(database)


IdentityServiceDependency = Annotated[IdentityService, Depends(get_identity_service)]


def get_connector_service(
    database: DatabaseDependency,
    registry: AdapterRegistryDependency,
) -> ConnectorService:
    """为请求创建共享 Registry 上的 Connector 应用服务。"""

    return ConnectorService(database, registry)


ConnectorServiceDependency = Annotated[
    ConnectorService,
    Depends(get_connector_service),
]


def get_account_health_service(
    database: DatabaseDependency,
    settings: SettingsDependency,
    registry: AdapterRegistryDependency,
    secret_provider: SecretProviderDependency,
) -> AccountHealthService:
    """创建不跨请求保存秘密或数据库连接的账号健康服务。"""

    return AccountHealthService(
        database,
        adapter_registry=registry,
        secret_provider=secret_provider,
        verification_timeout=timedelta(
            seconds=settings.account_health_verification_timeout_seconds
        ),
        attempt_ttl=timedelta(seconds=settings.account_health_attempt_ttl_seconds),
    )


AccountHealthServiceDependency = Annotated[
    AccountHealthService,
    Depends(get_account_health_service),
]


def get_lease_service(database: DatabaseDependency) -> LeaseService:
    """为内部 Worker 请求创建无状态租约应用服务。"""

    return LeaseService(database)


LeaseServiceDependency = Annotated[LeaseService, Depends(get_lease_service)]


def get_credential_broker_service(
    database: DatabaseDependency,
    settings: SettingsDependency,
    registry: AdapterRegistryDependency,
    secret_provider: SecretProviderDependency,
) -> CredentialBrokerService:
    """为内部 Worker 请求创建无状态 Credential Broker。"""

    return CredentialBrokerService(
        database,
        secret_provider=secret_provider,
        adapter_registry=registry,
        grant_ttl=timedelta(seconds=settings.secret_grant_ttl_seconds),
    )


CredentialBrokerServiceDependency = Annotated[
    CredentialBrokerService,
    Depends(get_credential_broker_service),
]


def get_auth_service(
    database: DatabaseDependency,
    settings: SettingsDependency,
    password_service: PasswordServiceDependency,
) -> AuthService:
    """创建共享连接池与 Password Service 上的认证应用服务。"""

    return AuthService(database, settings, password_service)


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
