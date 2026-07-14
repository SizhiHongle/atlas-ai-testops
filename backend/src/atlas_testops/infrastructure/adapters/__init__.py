"""受 Capability 契约约束的 Provider Adapter 实现。"""

from atlas_testops.infrastructure.adapters.registry import (
    AdapterNotRegisteredError,
    AdapterRegistry,
)

__all__ = ["AdapterNotRegisteredError", "AdapterRegistry"]
