"""用于验证 Temporal Runtime 的内部 Workflow。"""

from dataclasses import dataclass

from temporalio import workflow

from atlas_testops import __version__

PLATFORM_PROBE_WORKFLOW_NAME = "atlas.platform-probe/0.1"


@dataclass(frozen=True, slots=True)
class PlatformProbeRequest:
    """平台探针输入，不包含业务数据或秘密。"""

    request_id: str


@dataclass(frozen=True, slots=True)
class PlatformProbeResult:
    """平台探针的确定性响应。"""

    schema_version: str
    request_id: str
    worker_version: str


@workflow.defn(name=PLATFORM_PROBE_WORKFLOW_NAME)
class PlatformProbeWorkflow:
    """证明 Worker 已注册且可以完成确定性 Workflow。"""

    @workflow.run
    async def run(self, request: PlatformProbeRequest) -> PlatformProbeResult:
        """只回显稳定协议，不执行外部 I/O。"""

        return PlatformProbeResult(
            schema_version="atlas.platform-probe/0.1",
            request_id=request.request_id,
            worker_version=__version__,
        )
