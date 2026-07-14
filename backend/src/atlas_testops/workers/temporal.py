"""Temporal Worker 进程入口。"""

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from atlas_testops.core.config import Settings, get_settings
from atlas_testops.orchestration.platform import PlatformProbeWorkflow

LOGGER = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    """连接 Temporal，并在控制面 Task Queue 上运行已注册 Workflow。"""

    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[PlatformProbeWorkflow],
    )
    LOGGER.info(
        "Temporal Worker 已启动",
        extra={"task_queue": settings.temporal_task_queue},
    )
    await worker.run()


def main() -> None:
    """以原生 asyncio 事件循环启动 Worker。"""

    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_worker(get_settings()))


if __name__ == "__main__":
    main()
