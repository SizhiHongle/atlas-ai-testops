"""共享 FastAPI 依赖。"""

from datetime import timedelta
from typing import Annotated, cast

from fastapi import Depends, Request

from atlas_testops.application.account_health import AccountHealthService
from atlas_testops.application.auth import AuthService
from atlas_testops.application.case_versions import CaseVersionService
from atlas_testops.application.cases import CaseService
from atlas_testops.application.connectors import ConnectorService
from atlas_testops.application.credentials import CredentialBrokerService
from atlas_testops.application.debug_run_dispatcher import DebugRunDispatcher
from atlas_testops.application.debug_runs import DebugRunService
from atlas_testops.application.evidence import EvidenceService
from atlas_testops.application.fixture_assets import FixtureAssetService
from atlas_testops.application.fixture_dispatcher import FixtureRunDispatcher
from atlas_testops.application.fixture_runs import FixtureRunService
from atlas_testops.application.identity import IdentityService
from atlas_testops.application.leases import LeaseService
from atlas_testops.application.live import DebugLiveService, DebugLiveStreamLimiter
from atlas_testops.application.platform import PlatformService
from atlas_testops.application.ports.evidence import EvidenceObjectReader
from atlas_testops.application.ports.secrets import SecretProvider
from atlas_testops.application.session_dispatcher import AuthSessionDispatcher
from atlas_testops.application.task_commands import TaskRunCommandService
from atlas_testops.application.task_launches import TaskPlanLaunchService
from atlas_testops.application.task_plans import TaskPlanService
from atlas_testops.application.task_reruns import TaskRunRerunService
from atlas_testops.application.task_runs import TaskRunQueryService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.infrastructure.adapters.fixture_registry import FixtureOperationRegistry
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


def get_fixture_operation_registry(request: Request) -> FixtureOperationRegistry:
    """Return the deployment-owned exact fixture operation registry."""

    return cast(FixtureOperationRegistry, request.app.state.fixture_operation_registry)


def get_fixture_run_dispatcher(request: Request) -> FixtureRunDispatcher:
    """Return the isolated Fixture Worker dispatcher or fail closed."""

    dispatcher = cast(
        FixtureRunDispatcher | None,
        request.app.state.fixture_run_dispatcher,
    )
    if dispatcher is None:
        raise ApplicationError(
            error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            title="Fixture Worker 未配置",
            detail="当前 API 实例未连接独立 Fixture Worker。",
            status_code=503,
        )
    return dispatcher


def get_optional_debug_run_dispatcher(request: Request) -> DebugRunDispatcher | None:
    """Return the Browser Runtime dispatcher without blocking read-only APIs."""

    return cast(
        DebugRunDispatcher | None,
        request.app.state.debug_run_dispatcher,
    )


def get_optional_evidence_object_reader(request: Request) -> EvidenceObjectReader | None:
    """Return the independently verifying reader without making metadata APIs fail."""

    return cast(
        EvidenceObjectReader | None,
        request.app.state.evidence_object_reader,
    )


def get_debug_live_stream_limiter(request: Request) -> DebugLiveStreamLimiter:
    """Return the process-local bound for concurrent live observers."""

    return cast(
        DebugLiveStreamLimiter,
        request.app.state.debug_live_stream_limiter,
    )


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
FixtureOperationRegistryDependency = Annotated[
    FixtureOperationRegistry,
    Depends(get_fixture_operation_registry),
]
FixtureRunDispatcherDependency = Annotated[
    FixtureRunDispatcher,
    Depends(get_fixture_run_dispatcher),
]
OptionalDebugRunDispatcherDependency = Annotated[
    DebugRunDispatcher | None,
    Depends(get_optional_debug_run_dispatcher),
]
OptionalEvidenceObjectReaderDependency = Annotated[
    EvidenceObjectReader | None,
    Depends(get_optional_evidence_object_reader),
]
DebugLiveStreamLimiterDependency = Annotated[
    DebugLiveStreamLimiter,
    Depends(get_debug_live_stream_limiter),
]


def get_platform_service(database: DatabaseDependency) -> PlatformService:
    """为请求创建无状态 Platform 应用服务。"""

    return PlatformService(database)


PlatformServiceDependency = Annotated[PlatformService, Depends(get_platform_service)]


def get_case_service(database: DatabaseDependency) -> CaseService:
    """Create a stateless TestCase authoring service."""

    return CaseService(database)


CaseServiceDependency = Annotated[CaseService, Depends(get_case_service)]


def get_case_version_service(database: DatabaseDependency) -> CaseVersionService:
    """Create a stateless reviewed CaseVersion publication service."""

    return CaseVersionService(database)


CaseVersionServiceDependency = Annotated[
    CaseVersionService,
    Depends(get_case_version_service),
]


def get_task_plan_service(database: DatabaseDependency) -> TaskPlanService:
    """Create the stateless TaskPlan authoring and publication service."""

    return TaskPlanService(database)


TaskPlanServiceDependency = Annotated[
    TaskPlanService,
    Depends(get_task_plan_service),
]


def get_debug_run_service(
    database: DatabaseDependency,
    dispatcher: OptionalDebugRunDispatcherDependency,
) -> DebugRunService:
    """Create a DebugRun service that keeps reads available and writes fail closed."""

    return DebugRunService(database, dispatcher)


DebugRunServiceDependency = Annotated[
    DebugRunService,
    Depends(get_debug_run_service),
]


def get_task_run_query_service(database: DatabaseDependency) -> TaskRunQueryService:
    """Create a stateless read-only TaskRun query service."""

    return TaskRunQueryService(database)


TaskRunQueryServiceDependency = Annotated[
    TaskRunQueryService,
    Depends(get_task_run_query_service),
]


def get_task_run_command_service(database: DatabaseDependency) -> TaskRunCommandService:
    """Create the stateless durable TaskRun command-acceptance service."""

    return TaskRunCommandService(database)


TaskRunCommandServiceDependency = Annotated[
    TaskRunCommandService,
    Depends(get_task_run_command_service),
]


def get_task_run_rerun_service(database: DatabaseDependency) -> TaskRunRerunService:
    """Create the stateless child TaskRun materialization service."""

    return TaskRunRerunService(database)


TaskRunRerunServiceDependency = Annotated[
    TaskRunRerunService,
    Depends(get_task_run_rerun_service),
]


def get_task_plan_launch_service(
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> TaskPlanLaunchService:
    """Create the bounded manual TaskPlanVersion launch service."""

    return TaskPlanLaunchService(
        database,
        temporal_namespace=settings.temporal_namespace,
    )


TaskPlanLaunchServiceDependency = Annotated[
    TaskPlanLaunchService,
    Depends(get_task_plan_launch_service),
]


def get_debug_live_service(
    database: DatabaseDependency,
    settings: SettingsDependency,
) -> DebugLiveService:
    """Create a short-transaction DebugRun live projection service."""

    return DebugLiveService(
        database,
        poll_interval_seconds=settings.debug_live_poll_interval_ms / 1_000,
        heartbeat_interval_seconds=settings.debug_live_heartbeat_seconds,
        maximum_connection_seconds=settings.debug_live_max_connection_seconds,
        batch_size=settings.debug_live_batch_size,
    )


DebugLiveServiceDependency = Annotated[
    DebugLiveService,
    Depends(get_debug_live_service),
]


def get_evidence_service(
    database: DatabaseDependency,
    reader: OptionalEvidenceObjectReaderDependency,
    settings: SettingsDependency,
) -> EvidenceService:
    """Create a request-scoped Evidence service over shared durable dependencies."""

    return EvidenceService(
        database,
        reader,
        grant_ttl=timedelta(seconds=settings.evidence_read_grant_ttl_seconds),
        maximum_reads=settings.evidence_read_grant_max_reads,
    )


EvidenceServiceDependency = Annotated[
    EvidenceService,
    Depends(get_evidence_service),
]


def get_fixture_asset_service(database: DatabaseDependency) -> FixtureAssetService:
    """Create a stateless fixture asset control-plane service."""

    return FixtureAssetService(database)


FixtureAssetServiceDependency = Annotated[
    FixtureAssetService,
    Depends(get_fixture_asset_service),
]


def get_fixture_run_service(
    database: DatabaseDependency,
    dispatcher: FixtureRunDispatcherDependency,
    registry: FixtureOperationRegistryDependency,
    settings: SettingsDependency,
) -> FixtureRunService:
    """Create a stateless durable FixtureRun control-plane service."""

    return FixtureRunService(
        database,
        dispatcher,
        registry,
        cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
    )


FixtureRunServiceDependency = Annotated[
    FixtureRunService,
    Depends(get_fixture_run_service),
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
