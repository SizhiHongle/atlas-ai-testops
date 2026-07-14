"""测试套件共享配置。"""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """项目运行时只使用原生 asyncio。"""

    return "asyncio"
