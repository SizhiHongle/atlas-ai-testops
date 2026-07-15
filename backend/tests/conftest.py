"""测试套件共享配置。"""

from collections.abc import Callable

import pytest

from atlas_testops.domain.case import TestIntent
from atlas_testops.domain.workflow import WorkflowGraph
from tests.domain.case.factories import build_intent_factory, build_valid_graph


@pytest.fixture
def anyio_backend() -> str:
    """项目运行时只使用原生 asyncio。"""

    return "asyncio"


@pytest.fixture
def valid_graph() -> WorkflowGraph:
    """Return the shared deterministic workflow graph."""

    return build_valid_graph()


@pytest.fixture
def intent_factory() -> Callable[..., TestIntent]:
    """Return the shared deterministic TestIntent factory."""

    return build_intent_factory()
