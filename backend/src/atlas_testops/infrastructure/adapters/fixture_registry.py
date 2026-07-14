"""Explicit registry for exact fixture operation implementations."""

from __future__ import annotations

from atlas_testops.application.ports.fixture_operations import FixtureOperationProvider
from atlas_testops.core.config import Settings
from atlas_testops.domain.fixture import ConnectorOperationRef
from atlas_testops.infrastructure.adapters.mock_fixture import MockFixtureOperationProvider


class FixtureOperationNotRegisteredError(LookupError):
    """The deployment does not own the requested exact operation."""


class FixtureOperationCapabilityError(LookupError):
    """The registered operation lacks a contract-required capability."""


class FixtureOperationRegistry:
    """Resolve only providers registered during process startup."""

    def __init__(self) -> None:
        self._providers: dict[tuple[str, str, str], FixtureOperationProvider] = {}
        self._capabilities: dict[tuple[str, str, str], frozenset[str]] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> FixtureOperationRegistry:
        """Install synthetic operations only in local and test deployments."""

        registry = cls()
        if settings.environment in {"local", "test", "development"}:
            registry.register("generic-password", MockFixtureOperationProvider())
        return registry

    def register(self, adapter_key: str, provider: FixtureOperationProvider) -> None:
        """Register every exact operation exposed by one trusted provider."""

        normalized = adapter_key.strip()
        if not normalized:
            raise ValueError("adapter_key must not be blank")
        specs = provider.operation_specs()
        if not specs:
            raise ValueError("fixture operation provider must expose at least one operation")
        for spec in specs:
            identity = (normalized, spec.operation_key, spec.operation_version)
            if identity in self._providers:
                raise ValueError(f"fixture operation is already registered: {identity!r}")
            self._providers[identity] = provider
            self._capabilities[identity] = spec.capabilities

    def supports(self, adapter_key: str, operation: ConnectorOperationRef) -> bool:
        """Return whether the exact operation is installed in this deployment."""

        identity = (adapter_key, operation.operation_key, operation.operation_version)
        return identity in self._providers

    def resolve(
        self,
        adapter_key: str,
        operation: ConnectorOperationRef,
    ) -> FixtureOperationProvider:
        """Resolve an exact operation and enforce its capability declaration."""

        identity = (adapter_key, operation.operation_key, operation.operation_version)
        provider = self._providers.get(identity)
        if provider is None:
            raise FixtureOperationNotRegisteredError(": ".join(identity))
        required = set(operation.required_capabilities)
        available = self._capabilities[identity]
        if not required.issubset(available):
            missing = ", ".join(sorted(required - available))
            raise FixtureOperationCapabilityError(
                f"fixture operation lacks required capabilities: {missing}"
            )
        return provider
