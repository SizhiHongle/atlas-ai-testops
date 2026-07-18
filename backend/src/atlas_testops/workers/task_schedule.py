"""Opt-in Worker for trusted Temporal Schedule fire Workflows."""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from atlas_testops.application.task_schedule_fires import (
    TaskScheduleFireService,
)
from atlas_testops.core.config import Settings, get_settings
from atlas_testops.infrastructure.database import Database
from atlas_testops.orchestration.task_schedules import (
    TASK_SCHEDULE_TASK_QUEUE,
    AtlasTaskScheduleTriggerWorkflow,
    TaskScheduleFireActivities,
)

LOGGER = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    """Run only with explicit enablement and the fixed trusted Task Queue."""

    if not settings.task_schedule_worker_enabled:
        LOGGER.info("Task Schedule Worker is disabled")
        return
    if settings.database_url_value is None:
        raise RuntimeError("enabled Task Schedule Worker has no API database DSN")
    if settings.task_schedule_task_queue != TASK_SCHEDULE_TASK_QUEUE:
        raise RuntimeError("Task Schedule queue does not match the trusted workflow contract")

    database = Database(settings)
    activities = TaskScheduleFireActivities(
        TaskScheduleFireService(
            database,
            temporal_namespace=settings.temporal_namespace,
        )
    )
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    worker = Worker(
        client,
        task_queue=settings.task_schedule_task_queue,
        workflows=[AtlasTaskScheduleTriggerWorkflow],
        activities=[activities.fire],
        max_concurrent_workflow_tasks=(settings.task_schedule_worker_max_concurrency),
        max_concurrent_activities=settings.task_schedule_worker_max_concurrency,
    )
    await database.open()
    try:
        LOGGER.info(
            "Task Schedule Worker started",
            extra={
                "task_queue": settings.task_schedule_task_queue,
                "max_concurrency": settings.task_schedule_worker_max_concurrency,
            },
        )
        await worker.run()
    finally:
        await database.close()


def main() -> None:
    """Start the opt-in Task Schedule Worker."""

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
