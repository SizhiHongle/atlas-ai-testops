"""Dedicated process entrypoint for durable Task Workflow intent delivery."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from temporalio.client import Client

from atlas_testops.application.task_commands import TaskRunCommandIntentConsumer
from atlas_testops.application.task_intents import (
    TaskIntentRetryPolicy,
    TaskWorkflowIntentConsumer,
)
from atlas_testops.core.config import TaskIntentConsumerSettings
from atlas_testops.infrastructure.task_intents import TaskIntentDispatcherDatabase
from atlas_testops.orchestration.task_commands import TemporalTaskCommandSignaler
from atlas_testops.orchestration.task_intents import (
    TASK_RUN_TASK_QUEUE,
    TemporalTaskIntentStarter,
)

LOGGER = logging.getLogger(__name__)


async def run_consumer(
    settings: TaskIntentConsumerSettings,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Poll only when explicitly enabled and always release dispatcher resources."""

    if not settings.task_intent_consumption_enabled:
        LOGGER.info("Task Workflow Intent Consumer is disabled")
        return
    database_url = settings.task_dispatcher_database_url_value
    if database_url is None:
        raise RuntimeError("enabled Task Intent consumption has no dispatcher DSN")
    if settings.task_intent_task_queue != TASK_RUN_TASK_QUEUE:
        raise RuntimeError("Task Intent queue does not match the trusted workflow contract")

    database = TaskIntentDispatcherDatabase(
        database_url,
        pool_min_size=settings.task_dispatcher_database_pool_min_size,
        pool_max_size=settings.task_dispatcher_database_pool_max_size,
        connect_timeout_seconds=(
            settings.task_dispatcher_database_connect_timeout_seconds
        ),
        statement_timeout_ms=settings.task_dispatcher_database_statement_timeout_ms,
    )
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.task_intent_temporal_namespace,
    )
    starter = TemporalTaskIntentStarter(
        client,
        rpc_attempts=settings.task_intent_rpc_attempts,
        rpc_timeout=timedelta(seconds=settings.task_intent_rpc_timeout_seconds),
        retry_delay=timedelta(
            seconds=settings.task_intent_rpc_retry_delay_seconds,
        ),
    )
    retry_policy = TaskIntentRetryPolicy(
        max_attempts=settings.task_intent_max_attempts,
        initial_backoff=timedelta(
            seconds=settings.task_intent_retry_initial_seconds,
        ),
        maximum_backoff=timedelta(
            seconds=settings.task_intent_retry_maximum_seconds,
        ),
    )
    consumer = TaskWorkflowIntentConsumer(
        database,
        starter,
        dispatcher_id=settings.task_intent_worker_identity,
        temporal_namespace=settings.task_intent_temporal_namespace,
        batch_size=settings.task_intent_batch_size,
        lease_duration=timedelta(seconds=settings.task_intent_lease_seconds),
        poll_interval=timedelta(
            seconds=settings.task_intent_poll_interval_seconds,
        ),
        retry_policy=retry_policy,
    )
    command_consumer = TaskRunCommandIntentConsumer(
        database,
        TemporalTaskCommandSignaler(
            client,
            rpc_attempts=settings.task_intent_rpc_attempts,
            rpc_timeout=timedelta(seconds=settings.task_intent_rpc_timeout_seconds),
            retry_delay=timedelta(
                seconds=settings.task_intent_rpc_retry_delay_seconds,
            ),
        ),
        dispatcher_id=settings.task_intent_worker_identity,
        temporal_namespace=settings.task_intent_temporal_namespace,
        batch_size=settings.task_intent_batch_size,
        lease_duration=timedelta(seconds=settings.task_intent_lease_seconds),
        poll_interval=timedelta(
            seconds=settings.task_intent_poll_interval_seconds,
        ),
        retry_policy=retry_policy,
    )
    selected_stop_event = stop_event or asyncio.Event()
    await database.open()
    try:
        LOGGER.info(
            "Task Workflow Intent Consumer started",
            extra={
                "dispatcher_id": settings.task_intent_worker_identity,
                "namespace": settings.task_intent_temporal_namespace,
                "task_queue": settings.task_intent_task_queue,
                "batch_size": settings.task_intent_batch_size,
            },
        )
        async with asyncio.TaskGroup() as group:
            group.create_task(consumer.run_forever(selected_stop_event))
            group.create_task(command_consumer.run_forever(selected_stop_event))
    finally:
        selected_stop_event.set()
        await database.close()


def main() -> None:
    """Start the opt-in Consumer with its isolated dispatcher credentials."""

    settings = TaskIntentConsumerSettings()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run_consumer(settings))


if __name__ == "__main__":
    main()
