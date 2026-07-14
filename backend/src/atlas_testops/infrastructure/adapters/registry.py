"""显式 Adapter 工厂注册表，禁止按请求动态导入代码。"""

from collections.abc import Callable

from atlas_testops.application.ports.providers import IdentityProviderAdapter
from atlas_testops.core.config import Settings
from atlas_testops.domain.identity import ConnectorInstallationRecord
from atlas_testops.infrastructure.adapters.generic_password import GenericPasswordAdapter
from atlas_testops.infrastructure.adapters.mock_provider import MockIdentityProvider

type AdapterFactory = Callable[[ConnectorInstallationRecord], IdentityProviderAdapter]


class AdapterNotRegisteredError(LookupError):
    """请求的 Adapter Key 没有进程级可信工厂。"""


class AdapterRegistry:
    """仅解析部署时显式登记的 Adapter Factory。"""

    def __init__(self, factories: dict[str, AdapterFactory] | None = None) -> None:
        self._factories = dict(factories or {})

    @classmethod
    def from_settings(cls, settings: Settings) -> AdapterRegistry:
        """本地与测试启用 Mock；Staging/Production 不装载测试 Provider。"""

        registry = cls()
        if settings.environment in {"local", "test", "development"}:
            registry.register(
                "generic-password",
                lambda connector: GenericPasswordAdapter(
                    MockIdentityProvider(allowed_origins=connector.allowed_origins)
                ),
            )
        return registry

    def register(self, adapter_key: str, factory: AdapterFactory) -> None:
        """显式注册或替换一个可信工厂。"""

        normalized = adapter_key.strip()
        if not normalized:
            raise ValueError("adapter_key must not be blank")
        self._factories[normalized] = factory

    def supports(self, adapter_key: str) -> bool:
        """判断当前部署是否安装指定 Adapter。"""

        return adapter_key in self._factories

    def resolve(
        self,
        connector: ConnectorInstallationRecord,
    ) -> IdentityProviderAdapter:
        """由内部记录构造 Adapter，并校验 Manifest 身份。"""

        factory = self._factories.get(connector.adapter_key)
        if factory is None:
            raise AdapterNotRegisteredError(connector.adapter_key)
        adapter = factory(connector)
        if adapter.manifest().adapter_key != connector.adapter_key:
            raise RuntimeError("adapter manifest key does not match connector")
        return adapter
