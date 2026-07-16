"""Real Temporal delivery checks for Task Workflow start intents."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from os import environ
from typing import Any, cast
from uuid import uuid7

import pytest
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowHandle
from temporalio.service import RPCError, RPCStatusCode

from atlas_testops.application.task_intents import TaskIntentInvariantError
from atlas_testops.domain.task import task_run_workflow_id
from atlas_testops.infrastructure.task_intents import ClaimedTaskWorkflowIntent
from atlas_testops.orchestration.task_intents import (
    TASK_INTENT_MEMO_KEY,
    TASK_RUN_TASK_QUEUE,
    TASK_RUN_WORKFLOW_TYPE,
    TemporalTaskIntentStarter,
)

TEMPORAL_ADDRESS = environ.get("ATLAS_TEST_TEMPORAL_ADDRESS")
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        TEMPORAL_ADDRESS is None,
        reason="ATLAS_TEST_TEMPORAL_ADDRESS is not configured",
    ),
]


def _claimed_intent(*, namespace: str) -> ClaimedTaskWorkflowIntent:
    now = datetime.now(UTC)
    tenant_id = uuid7()
    task_run_id = uuid7()
    return ClaimedTaskWorkflowIntent(
        id=uuid7(),
        tenant_id=tenant_id,
        project_id=uuid7(),
        task_run_id=task_run_id,
        owner_kind="TASK_RUN",
        owner_id=task_run_id,
        namespace=namespace,
        workflow_id=task_run_workflow_id(
            tenant_id=tenant_id,
            task_run_id=task_run_id,
        ),
        request_digest=DIGEST_A,
        manifest_hash=DIGEST_B,
        workflow_type=TASK_RUN_WORKFLOW_TYPE,
        task_queue=TASK_RUN_TASK_QUEUE,
        status="CLAIMED",
        claim_token=uuid7(),
        dispatch_revision=2,
        dispatch_attempts=1,
        claim_expires_at=now + timedelta(minutes=2),
        created_at=now,
    )


async def _terminate_unconsumed(handle: WorkflowHandle[Any, Any]) -> None:
    try:
        await handle.terminate(
            reason="Task Intent Temporal integration test cleanup",
            rpc_timeout=timedelta(seconds=5),
        )
    except RPCError as error:
        if error.status not in {
            RPCStatusCode.NOT_FOUND,
            RPCStatusCode.FAILED_PRECONDITION,
        }:
            raise


@pytest.mark.anyio
async def test_real_temporal_accepts_unconsumed_root_and_verifies_exact_replay() -> None:
    assert TEMPORAL_ADDRESS is not None
    namespace = "default"
    client = await Client.connect(TEMPORAL_ADDRESS, namespace=namespace)
    intent = _claimed_intent(namespace=namespace)
    handle = client.get_workflow_handle(intent.workflow_id)
    starter = TemporalTaskIntentStarter(
        client,
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=5),
        retry_delay=timedelta(milliseconds=100),
    )

    try:
        await starter.start(intent)
        description = await handle.describe(rpc_timeout=timedelta(seconds=5))
        assert description.status is WorkflowExecutionStatus.RUNNING
        assert description.workflow_type == TASK_RUN_WORKFLOW_TYPE
        assert description.task_queue == TASK_RUN_TASK_QUEUE

        replay = replace(
            intent,
            claim_token=uuid7(),
            dispatch_revision=3,
            dispatch_attempts=2,
        )
        await starter.start(replay)

        replay_description = await handle.describe(
            rpc_timeout=timedelta(seconds=5),
        )
        memo = await replay_description.memo()
        identity = cast(dict[str, str], memo[TASK_INTENT_MEMO_KEY])
        assert identity["requestDigest"] == intent.request_digest
        assert identity["manifestHash"] == intent.manifest_hash
    finally:
        await _terminate_unconsumed(handle)


@pytest.mark.anyio
async def test_real_temporal_existing_workflow_rejects_different_digest_memo() -> None:
    assert TEMPORAL_ADDRESS is not None
    namespace = "default"
    client = await Client.connect(TEMPORAL_ADDRESS, namespace=namespace)
    intent = _claimed_intent(namespace=namespace)
    handle = client.get_workflow_handle(intent.workflow_id)
    starter = TemporalTaskIntentStarter(
        client,
        rpc_attempts=2,
        rpc_timeout=timedelta(seconds=5),
        retry_delay=timedelta(milliseconds=100),
    )

    try:
        await starter.start(intent)
        conflicting_replay = replace(
            intent,
            id=uuid7(),
            claim_token=uuid7(),
            request_digest=DIGEST_C,
            dispatch_revision=3,
            dispatch_attempts=2,
        )

        with pytest.raises(
            TaskIntentInvariantError,
            match="TEMPORAL_WORKFLOW_MEMO_MISMATCH",
        ) as failure:
            await starter.start(conflicting_replay)

        assert failure.value.error_code == "TEMPORAL_WORKFLOW_MEMO_MISMATCH"
    finally:
        await _terminate_unconsumed(handle)
