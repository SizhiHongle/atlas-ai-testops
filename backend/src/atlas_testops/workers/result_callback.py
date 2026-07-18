"""Opt-in process for durable signed Task Gate callback delivery."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from atlas_testops.application.result_callback_delivery import (
    TaskGateCallbackDeliveryConsumer,
)
from atlas_testops.application.task_intents import TaskIntentRetryPolicy
from atlas_testops.core.config import TaskGateCallbackWorkerSettings
from atlas_testops.infrastructure.result_callbacks import (
    HttpTaskGateCallbackSender,
    TaskGateCallbackSigner,
)
from atlas_testops.infrastructure.task_intents import TaskIntentDispatcherDatabase

LOGGER = logging.getLogger(__name__)


async def run_consumer(
    settings: TaskGateCallbackWorkerSettings,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run only with complete process-local authority and endpoint config."""

    if not settings.task_gate_callback_delivery_enabled:
        LOGGER.info("Task Gate callback delivery is disabled")
        return
    database_url = settings.task_dispatcher_database_url_value
    callback_url = settings.task_gate_callback_url
    encoded_key = settings.task_gate_callback_hmac_key_value
    if database_url is None:
        raise RuntimeError("enabled Task Gate callback delivery has no dispatcher DSN")
    if callback_url is None or encoded_key is None:
        raise RuntimeError("enabled Task Gate callback delivery has incomplete signing config")

    database = TaskIntentDispatcherDatabase(
        database_url,
        pool_min_size=settings.task_dispatcher_database_pool_min_size,
        pool_max_size=settings.task_dispatcher_database_pool_max_size,
        connect_timeout_seconds=(settings.task_dispatcher_database_connect_timeout_seconds),
        statement_timeout_ms=settings.task_dispatcher_database_statement_timeout_ms,
    )
    sender = HttpTaskGateCallbackSender(
        callback_url=callback_url,
        signer=TaskGateCallbackSigner.from_base64_key(
            encoded_key,
            replay_window=timedelta(seconds=settings.task_gate_callback_replay_window_seconds),
        ),
        timeout=timedelta(seconds=settings.task_gate_callback_http_timeout_seconds),
        allow_insecure_http=settings.task_gate_callback_allow_insecure_http,
    )
    consumer = TaskGateCallbackDeliveryConsumer(
        database,
        sender,
        dispatcher_id=settings.task_gate_callback_worker_identity,
        batch_size=settings.task_gate_callback_batch_size,
        lease_duration=timedelta(seconds=settings.task_gate_callback_lease_seconds),
        poll_interval=timedelta(seconds=settings.task_gate_callback_poll_interval_seconds),
        retry_policy=TaskIntentRetryPolicy(
            max_attempts=settings.task_gate_callback_max_attempts,
            initial_backoff=timedelta(seconds=settings.task_gate_callback_retry_initial_seconds),
            maximum_backoff=timedelta(seconds=settings.task_gate_callback_retry_maximum_seconds),
        ),
    )
    selected_stop_event = stop_event or asyncio.Event()
    await database.open()
    try:
        LOGGER.info(
            "Task Gate callback Consumer started",
            extra={
                "dispatcher_id": settings.task_gate_callback_worker_identity,
                "batch_size": settings.task_gate_callback_batch_size,
            },
        )
        await consumer.run_forever(selected_stop_event)
    finally:
        selected_stop_event.set()
        await sender.aclose()
        await database.close()


def main() -> None:
    """Start the opt-in callback Consumer."""

    settings = TaskGateCallbackWorkerSettings()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run_consumer(settings))


if __name__ == "__main__":
    main()
