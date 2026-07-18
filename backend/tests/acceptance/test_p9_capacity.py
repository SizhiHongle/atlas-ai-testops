"""P9 opt-in capacity, evidence-volume, and Live latency acceptance."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from os import environ
from time import perf_counter_ns

import pytest
from PIL import Image

from atlas_testops.application.leases import LeaseCommandResult, LeaseService
from atlas_testops.application.ports.evidence import EvidenceObjectDescriptor
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.identity import LeaseReleaseReason, ReleaseAccountLease
from atlas_testops.domain.runtime import EvidenceArtifactKind, EvidenceIntegrity
from atlas_testops.infrastructure.evidence_store import (
    InMemoryEvidenceObjectStore,
    PngEvidenceArtifactWriter,
    VerifiedEvidenceObjectReader,
)
from tests.api.test_live_sse import (
    RecordingLiveService,
    _client,
    _event,
    _snapshot,
)
from tests.infrastructure.test_evidence_store import (
    BUCKET,
    _key,
    _scope,
)
from tests.integration.test_account_lease_concurrency import (
    MutableClock,
    acquire_command,
    create_database,
    make_actor,
    seed_lease_pool,
)

RUN_P9_ACCEPTANCE = environ.get("ATLAS_RUN_P9_ACCEPTANCE") == "1"
DATABASE_URL = environ.get("ATLAS_TEST_DATABASE_URL")
REFERENCE_PEAK_CONCURRENCY = 50
STRESS_CONCURRENCY = REFERENCE_PEAK_CONCURRENCY * 2
LEASE_STRESS_ROUNDS = 100
LIVE_EVENT_SAMPLES = 100
EVIDENCE_OBJECT_SAMPLES = REFERENCE_PEAK_CONCURRENCY * 2

pytestmark = [
    pytest.mark.p9,
    pytest.mark.skipif(
        not RUN_P9_ACCEPTANCE,
        reason="set ATLAS_RUN_P9_ACCEPTANCE=1 for the heavy P9 acceptance suite",
    ),
]


def _nearest_rank_p95_milliseconds(samples_ns: list[int]) -> int:
    """Return an integer nearest-rank P95 without hiding small sample sets."""

    if not samples_ns:
        raise ValueError("P95 requires at least one sample")
    ordered = sorted(samples_ns)
    rank = max(1, (95 * len(ordered) + 99) // 100)
    return (ordered[rank - 1] + 999_999) // 1_000_000


def _large_png() -> bytes:
    """Build one deterministic, poorly compressible screenshot-sized payload."""

    required_bytes = 512 * 512 * 3
    pixels = bytearray()
    counter = 0
    while len(pixels) < required_bytes:
        pixels.extend(sha256(counter.to_bytes(8, "big")).digest())
        counter += 1
    image = Image.frombytes("RGB", (512, 512), bytes(pixels[:required_bytes]))
    output = BytesIO()
    image.save(output, format="PNG", compress_level=6)
    return output.getvalue()


@pytest.mark.anyio
@pytest.mark.integration
@pytest.mark.skipif(
    DATABASE_URL is None,
    reason="ATLAS_TEST_DATABASE_URL is required for lease stress",
)
async def test_account_leases_survive_100_concurrent_by_100_rounds(
    record_property: Callable[[str, object], None],
) -> None:
    """Prove 10,000 full lease cycles without duplicate active slots."""

    database = create_database(maximum_connections=32)
    clock = MutableClock(datetime.now(UTC))
    await database.open()
    started_ns = perf_counter_ns()
    try:
        seed = await seed_lease_pool(
            database,
            account_count=STRESS_CONCURRENCY,
            cooldown_seconds=0,
        )
        actor = make_actor(seed)
        service = LeaseService(database, clock=clock)
        transient_shortages = 0

        async def acquire_with_retry(
            round_index: int,
            index: int,
        ) -> LeaseCommandResult:
            nonlocal transient_shortages
            ordinal = round_index * STRESS_CONCURRENCY + index
            for _ in range(100):
                try:
                    return await service.acquire(
                        actor,
                        acquire_command(seed, clock, ordinal),
                        idempotency_key=(f"p9-lease-{round_index:03d}-{index:03d}"),
                    )
                except ApplicationError as error:
                    if error.error_code is not ErrorCode.POOL_EXHAUSTED:
                        raise
                    transient_shortages += 1
                    await asyncio.sleep(0.01)
            raise AssertionError("lease admission did not converge after bounded retries")

        for round_index in range(LEASE_STRESS_ROUNDS):
            acquired = await asyncio.gather(
                *(acquire_with_retry(round_index, index) for index in range(STRESS_CONCURRENCY))
            )
            assert len({item.value.lease_id for item in acquired}) == (STRESS_CONCURRENCY)
            assert len({item.value.account_handle for item in acquired}) == (STRESS_CONCURRENCY)
            async with database.transaction(actor.database_context()) as connection:
                duplicates = await (
                    await connection.execute(
                        """
                        select count(*) as duplicate_slots
                        from (
                          select slot_id
                          from atlas.account_lease
                          where pool_id = %s and status = 'ACTIVE'
                          group by slot_id
                          having count(*) > 1
                        ) duplicate
                        """,
                        (seed.pool_id,),
                    )
                ).fetchone()
                assert duplicates == {"duplicate_slots": 0}

            await asyncio.gather(
                *(
                    service.release(
                        actor,
                        item.value.lease_id,
                        ReleaseAccountLease(
                            fencing_token=item.value.fencing_token,
                            reason=LeaseReleaseReason.COMPLETED,
                        ),
                    )
                    for item in acquired
                )
            )

        async with database.transaction(actor.database_context()) as connection:
            summary = await (
                await connection.execute(
                    """
                    select count(*) as lease_count,
                           count(*) filter (where status = 'ACTIVE') as active_count,
                           min(fencing_token) as minimum_fence,
                           max(fencing_token) as maximum_fence
                    from atlas.account_lease
                    where pool_id = %s
                    """,
                    (seed.pool_id,),
                )
            ).fetchone()
        assert summary == {
            "lease_count": STRESS_CONCURRENCY * LEASE_STRESS_ROUNDS,
            "active_count": 0,
            "minimum_fence": 1,
            "maximum_fence": LEASE_STRESS_ROUNDS,
        }
    finally:
        duration_ms = (perf_counter_ns() - started_ns + 999_999) // 1_000_000
        await database.close()

    record_property(
        "leaseOperations",
        STRESS_CONCURRENCY * LEASE_STRESS_ROUNDS,
    )
    record_property("leaseConflicts", 0)
    record_property("leaseTransientShortages", transient_shortages)
    record_property("leaseStressDurationMilliseconds", duration_ms)


@pytest.mark.anyio
async def test_large_evidence_reference_load_is_complete_and_verified(
    record_property: Callable[[str, object], None],
) -> None:
    """Write and independently read 2x the local reference object peak."""

    payload = _large_png()
    store = InMemoryEvidenceObjectStore()
    writer = PngEvidenceArtifactWriter(store, bucket=BUCKET)
    reader = VerifiedEvidenceObjectReader(store, bucket=BUCKET)
    scope = _scope()
    started_ns = perf_counter_ns()
    artifacts = await asyncio.gather(
        *(
            writer.write(
                scope=scope,
                kind=EvidenceArtifactKind.SCREENSHOT,
                payload=payload,
                mime_type="image/png",
                required=True,
                captured_at=scope.execution_created_at,
            )
            for _ in range(EVIDENCE_OBJECT_SAMPLES)
        )
    )
    assert len({artifact.id for artifact in artifacts}) == EVIDENCE_OBJECT_SAMPLES
    assert len({artifact.object_ref for artifact in artifacts}) == (EVIDENCE_OBJECT_SAMPLES)
    assert all(artifact.integrity is EvidenceIntegrity.VERIFIED for artifact in artifacts)

    descriptors = tuple(
        EvidenceObjectDescriptor(
            artifact_id=artifact.id,
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            environment_id=scope.environment_id,
            debug_run_id=scope.debug_run_id,
            execution_contract_id=scope.execution_contract_id,
            object_ref=artifact.object_ref,
            content_digest=artifact.content_digest,
            size_bytes=artifact.size_bytes,
            mime_type=artifact.mime_type,
        )
        for artifact in artifacts
    )
    restored = await asyncio.gather(
        *(reader.read_verified(descriptor) for descriptor in descriptors)
    )
    retained = await asyncio.gather(
        *(store.payload_for_test(_key(artifact.object_ref)) for artifact in artifacts)
    )
    assert restored == list(retained)
    duration_ms = (perf_counter_ns() - started_ns + 999_999) // 1_000_000
    total_bytes = sum(artifact.size_bytes for artifact in artifacts)
    record_property("evidenceObjects", len(artifacts))
    record_property("evidenceVerifiedObjects", len(restored))
    record_property("evidenceBytes", total_bytes)
    record_property("evidenceLoadDurationMilliseconds", duration_ms)


def test_live_event_local_reference_p95_is_below_two_seconds(
    record_property: Callable[[str, object], None],
) -> None:
    """Measure in-process event-to-client completion as a local upper bound."""

    service = RecordingLiveService(_snapshot(), events=(_event(),))
    client, _actor, limiter = _client(service)
    samples_ns: list[int] = []
    with client:
        for _ in range(LIVE_EVENT_SAMPLES):
            started_ns = perf_counter_ns()
            response = client.get(
                f"/v1/debug-runs/{service.snapshot.run.debug_run_id}/events/stream"
            )
            samples_ns.append(perf_counter_ns() - started_ns)
            assert response.status_code == 200
            assert "event: debug_run.browser.action.executed" in response.text
    p95_ms = _nearest_rank_p95_milliseconds(samples_ns)
    assert limiter.acquire_calls == LIVE_EVENT_SAMPLES
    assert limiter.release_calls == LIVE_EVENT_SAMPLES
    assert p95_ms < 2_000
    record_property("liveEventSamples", LIVE_EVENT_SAMPLES)
    record_property("liveEventP95Milliseconds", p95_ms)
