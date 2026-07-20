"""Temporal Worker 进程入口。"""

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from atlas_testops.core.config import Settings, get_settings
from atlas_testops.infrastructure.browser_auth import BrowserRuntimePermitSigner
from atlas_testops.infrastructure.database import Database
from atlas_testops.orchestration.browser import TemporalBrowserExecutionDispatcher
from atlas_testops.orchestration.debug_preparation import (
    DebugPreparationActivities,
    LocalDebugPreparationService,
    LocalDebugPreparationWorkflow,
)
from atlas_testops.orchestration.fixtures import TemporalFixtureRunDispatcher
from atlas_testops.orchestration.platform import PlatformProbeWorkflow
from atlas_testops.orchestration.sessions import TemporalAuthSessionDispatcher

LOGGER = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    """连接 Temporal，并在控制面 Task Queue 上运行已注册 Workflow。"""

    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    database: Database | None = None
    workflows: list[type[object]] = [PlatformProbeWorkflow]
    activities: list[Callable[..., Any]] = []
    if settings.debug_run_preparation_enabled:
        permit_key = settings.browser_runtime_permit_key_base64
        if permit_key is None:
            raise RuntimeError("local DebugRun preparation permit key is unavailable")
        database = Database(settings)
        await database.open()
        auth_dispatcher = TemporalAuthSessionDispatcher(
            client,
            task_queue=settings.auth_session_task_queue,
            workflow_timeout=timedelta(
                seconds=settings.auth_session_workflow_timeout_seconds
            ),
        )
        fixture_dispatcher = TemporalFixtureRunDispatcher(
            client,
            task_queue=settings.fixture_task_queue,
            activity_timeout=timedelta(
                seconds=settings.fixture_activity_timeout_seconds
            ),
            cleanup_grace=timedelta(
                seconds=settings.fixture_cleanup_grace_seconds
            ),
        )
        browser_dispatcher = TemporalBrowserExecutionDispatcher(
            client,
            task_queue=settings.browser_runtime_task_queue,
            worker_identity=settings.browser_runtime_worker_identity,
            permit_signer=BrowserRuntimePermitSigner.from_base64_key(
                permit_key.get_secret_value(),
                maximum_lifetime=timedelta(
                    seconds=settings.browser_runtime_permit_ttl_seconds
                ),
            ),
            activity_timeout=timedelta(
                seconds=settings.browser_runtime_activity_timeout_seconds
            ),
            heartbeat_timeout=timedelta(
                seconds=settings.browser_runtime_heartbeat_timeout_seconds
            ),
            permit_ttl=timedelta(
                seconds=settings.browser_runtime_permit_ttl_seconds
            ),
        )
        preparation = LocalDebugPreparationService(
            database,
            settings,
            auth_session_dispatcher=auth_dispatcher,
            fixture_run_dispatcher=fixture_dispatcher,
            browser_execution_dispatcher=browser_dispatcher,
        )
        preparation_activities = DebugPreparationActivities(preparation)
        workflows.append(LocalDebugPreparationWorkflow)
        activities.append(preparation_activities.prepare)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=workflows,
        activities=activities,
    )
    LOGGER.info(
        "Temporal Worker 已启动",
        extra={"task_queue": settings.temporal_task_queue},
    )
    try:
        await worker.run()
    finally:
        if database is not None:
            await database.close()


def main() -> None:
    """以原生 asyncio 事件循环启动 Worker。"""

    logging.basicConfig(level=get_settings().log_level)
    asyncio.run(run_worker(get_settings()))


if __name__ == "__main__":
    main()
