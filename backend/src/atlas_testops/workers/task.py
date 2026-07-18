"""Opt-in Temporal Worker assembly for durable Task run orchestration."""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from atlas_testops.application.result_hygiene import ResultHygieneProjectionService
from atlas_testops.application.task_orchestration import (
    TaskUnitExecutionPort,
    TaskWorkerService,
)
from atlas_testops.core.config import Settings, get_settings
from atlas_testops.infrastructure.database import Database
from atlas_testops.orchestration.task_intents import TASK_RUN_TASK_QUEUE
from atlas_testops.orchestration.tasks import (
    TASK_UNIT_ATTEMPT_TASK_QUEUE,
    AtlasTaskRunWorkflow,
    AtlasUnitAttemptWorkflow,
    TaskOrchestrationActivities,
)

LOGGER = logging.getLogger(__name__)


async def run_worker(
    settings: Settings,
    *,
    executor: TaskUnitExecutionPort | None = None,
) -> None:
    """Run isolated Root and Attempt workers only with an approved executor."""

    if not settings.task_worker_enabled:
        LOGGER.info("Task Worker is disabled")
        return
    if executor is None:
        raise RuntimeError("enabled Task Worker requires a formal TaskUnitExecutionPort")
    if settings.task_run_task_queue != TASK_RUN_TASK_QUEUE:
        raise RuntimeError("Task Root queue does not match the trusted workflow contract")
    if settings.task_attempt_task_queue != TASK_UNIT_ATTEMPT_TASK_QUEUE:
        raise RuntimeError("Task Attempt queue does not match the trusted workflow contract")

    database = Database(settings)
    service = TaskWorkerService(
        database,
        result_hygiene_projection_service=ResultHygieneProjectionService(),
    )
    activities = TaskOrchestrationActivities(service, executor)
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    root_worker = Worker(
        client,
        task_queue=settings.task_run_task_queue,
        workflows=[AtlasTaskRunWorkflow],
        activities=[
            activities.load_dispatch_plan,
            activities.prepare_batch,
            activities.checkpoint_control,
            activities.settle_attempt_batch,
            activities.finish_run,
            activities.finish_partitioned_run,
        ],
        max_concurrent_workflow_tasks=settings.task_run_worker_max_concurrency,
        max_concurrent_activities=settings.task_run_worker_max_concurrency,
    )
    attempt_worker = Worker(
        client,
        task_queue=settings.task_attempt_task_queue,
        workflows=[AtlasUnitAttemptWorkflow],
        activities=[
            activities.prepare_attempt,
            activities.begin_attempt,
            activities.execute_attempt,
            activities.finish_attempt,
        ],
        max_concurrent_workflow_tasks=settings.task_attempt_worker_max_concurrency,
        max_concurrent_activities=settings.task_attempt_worker_max_concurrency,
    )
    try:
        await database.open()
        LOGGER.info(
            "Task Worker started",
            extra={
                "root_task_queue": settings.task_run_task_queue,
                "attempt_task_queue": settings.task_attempt_task_queue,
                "root_max_concurrency": settings.task_run_worker_max_concurrency,
                "attempt_max_concurrency": settings.task_attempt_worker_max_concurrency,
            },
        )
        await _run_workers(root_worker, attempt_worker)
    finally:
        await database.close()


async def _run_workers(root_worker: Worker, attempt_worker: Worker) -> None:
    """Run both pollers together and cancel the peer if either one exits."""

    tasks = (
        asyncio.create_task(root_worker.run()),
        asyncio.create_task(attempt_worker.run()),
    )
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    """Start the opt-in Task Worker without manufacturing an execution adapter."""

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
