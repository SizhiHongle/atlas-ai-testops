"""Dedicated Temporal Worker for browser authentication and artifact cleanup."""

import asyncio
import logging
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from atlas_testops.application.ports.secrets import SecretProvider
from atlas_testops.application.ports.sessions import SessionArtifactVault
from atlas_testops.application.session_janitor import SessionJanitorService
from atlas_testops.application.sessions import AuthSessionService
from atlas_testops.core.config import AuthSessionWorkerSettings, Settings, get_settings
from atlas_testops.infrastructure.adapters.registry import AdapterRegistry
from atlas_testops.infrastructure.database import Database
from atlas_testops.infrastructure.secrets import LocalDevelopmentSecretProvider
from atlas_testops.infrastructure.session_runtime import build_optional_session_artifact_vault
from atlas_testops.orchestration.sessions import (
    AuthSessionActivities,
    EnsureAuthSessionWorkflow,
    RunSessionJanitorWorkflow,
    SessionJanitorActivities,
)

LOGGER = logging.getLogger(__name__)


async def run_worker(
    settings: Settings,
    worker_settings: AuthSessionWorkerSettings,
    *,
    secret_provider: SecretProvider | None = None,
    session_vault: SessionArtifactVault | None = None,
) -> None:
    """Run browser work on an isolated queue with bounded activity concurrency."""

    database = Database(settings)
    vault = session_vault
    if vault is None:
        vault = await build_optional_session_artifact_vault(worker_settings)
    registry = AdapterRegistry.from_settings(settings)
    selected_secret_provider = secret_provider
    if (
        selected_secret_provider is None
        and settings.environment in {"local", "development"}
    ):
        selected_secret_provider = LocalDevelopmentSecretProvider()
    session_service = AuthSessionService(
        database,
        adapter_registry=registry,
        secret_provider=selected_secret_provider,
        session_vault=vault,
        session_ttl=timedelta(seconds=settings.auth_session_ttl_seconds),
        creation_timeout=timedelta(
            seconds=settings.auth_session_creation_timeout_seconds
        ),
        attempt_ttl=timedelta(seconds=settings.auth_session_attempt_ttl_seconds),
        manual_ticket_ttl=timedelta(
            seconds=settings.auth_session_manual_ticket_ttl_seconds
        ),
        grant_ttl=timedelta(seconds=settings.secret_grant_ttl_seconds),
    )
    janitor_service = SessionJanitorService(
        database,
        session_vault=vault,
        cleanup_claim_ttl=timedelta(
            seconds=settings.session_janitor_claim_ttl_seconds
        ),
    )
    auth_activities = AuthSessionActivities(session_service)
    janitor_activities = SessionJanitorActivities(janitor_service)
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    worker = Worker(
        client,
        task_queue=settings.auth_session_task_queue,
        workflows=[EnsureAuthSessionWorkflow, RunSessionJanitorWorkflow],
        activities=[auth_activities.ensure, janitor_activities.run_once],
        max_concurrent_activities=settings.auth_session_worker_max_concurrency,
    )
    await database.open()
    try:
        LOGGER.info(
            "Auth Session Worker started",
            extra={
                "task_queue": settings.auth_session_task_queue,
                "max_concurrent_activities": settings.auth_session_worker_max_concurrency,
                "secret_provider_configured": selected_secret_provider is not None,
                "session_vault_configured": vault is not None,
            },
        )
        await worker.run()
    finally:
        await database.close()


def main() -> None:
    """Start the isolated worker with deployment-injected dependencies when available."""

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run_worker(settings, AuthSessionWorkerSettings()))


if __name__ == "__main__":
    main()
