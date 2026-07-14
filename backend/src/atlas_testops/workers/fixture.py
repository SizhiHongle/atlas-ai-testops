"""Dedicated Temporal Worker for fixture provider operations and cleanup."""

import asyncio
import logging
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from atlas_testops.application.fixture_runs import FixtureWorkerService
from atlas_testops.core.config import Settings, get_settings
from atlas_testops.infrastructure.adapters.fixture_registry import FixtureOperationRegistry
from atlas_testops.infrastructure.database import Database
from atlas_testops.orchestration.fixtures import FixtureActivities, FixtureRunWorkflow

LOGGER = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    """Run reviewed fixture operations on an isolated task queue."""

    database = Database(settings)
    registry = FixtureOperationRegistry.from_settings(settings)
    service = FixtureWorkerService(
        database,
        registry,
        cleanup_grace=timedelta(seconds=settings.fixture_cleanup_grace_seconds),
    )
    activities = FixtureActivities(service)
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    worker = Worker(
        client,
        task_queue=settings.fixture_task_queue,
        workflows=[FixtureRunWorkflow],
        activities=[
            activities.load_plan,
            activities.execute_node,
            activities.finalize_ready,
            activities.begin_release,
            activities.begin_failed_cleanup,
            activities.cleanup_node,
            activities.finalize_release,
        ],
        max_concurrent_activities=settings.fixture_worker_max_concurrency,
    )
    await database.open()
    try:
        LOGGER.info(
            "Fixture Worker started",
            extra={
                "task_queue": settings.fixture_task_queue,
                "max_concurrent_activities": settings.fixture_worker_max_concurrency,
            },
        )
        await worker.run()
    finally:
        await database.close()


def main() -> None:
    """Start the isolated Fixture Worker."""

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
