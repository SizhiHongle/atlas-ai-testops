"""平台探针 Workflow 单元测试。"""

import pytest

from atlas_testops import __version__
from atlas_testops.orchestration.platform import (
    PlatformProbeRequest,
    PlatformProbeWorkflow,
)


@pytest.mark.anyio
async def test_platform_probe_returns_stable_contract() -> None:
    workflow = PlatformProbeWorkflow()

    result = await workflow.run(PlatformProbeRequest(request_id="probe-1"))

    assert result.schema_version == "atlas.platform-probe/0.1"
    assert result.request_id == "probe-1"
    assert result.worker_version == __version__
