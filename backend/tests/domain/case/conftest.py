"""Shared deterministic TestCase protocol fixtures."""

from collections.abc import Callable

import pytest

from atlas_testops.domain.case import TestIntent
from atlas_testops.domain.workflow import WorkflowGraph
from tests.domain.case.factories import build_intent_factory, build_valid_graph


@pytest.fixture
def valid_graph() -> WorkflowGraph:
    return build_valid_graph()


@pytest.fixture
def intent_factory() -> Callable[..., TestIntent]:
    return build_intent_factory()
