"""Snapshot-explicit Result query service tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from tests.application.test_result_classification import (
    _ClassificationResultRepository,
    _Database,
    _service_fixture,
)

from atlas_testops.application.access import ActorContext
from atlas_testops.application.result_queries import ResultQueryService
from atlas_testops.core.errors import ApplicationError
from atlas_testops.domain.result import (
    FailureClassificationRevision,
    FailureClassificationRevisionContent,
    FailureClusterRevision,
    FailureClusterRevisionContent,
    ResultClusterCursor,
    ResultSnapshotSelection,
    TaskGateDecision,
    TaskResultSnapshot,
    UnitResolutionRevision,
    decode_result_cluster_cursor,
    encode_result_cluster_cursor,
    failure_classification_revision_hash,
    failure_cluster_fingerprint,
    failure_cluster_revision_hash,
)


class _QueryTaskRepository:
    def __init__(
        self,
        run: object,
        resolution: UnitResolutionRevision,
    ) -> None:
        self.run = run
        self.unit = SimpleNamespace(
            id=resolution.execution_unit_id,
            tenant_id=resolution.tenant_id,
            project_id=resolution.project_id,
            task_run_id=resolution.task_run_id,
        )

    async def get_run(self, _connection: object, task_run_id: UUID) -> object | None:
        return self.run if cast(Any, self.run).id == task_run_id else None

    async def get_unit(
        self,
        _connection: object,
        execution_unit_id: UUID,
    ) -> object | None:
        return self.unit if self.unit.id == execution_unit_id else None


class _QueryResultRepository(_ClassificationResultRepository):
    def __init__(
        self,
        *,
        snapshots: list[TaskResultSnapshot],
        resolutions: list[UnitResolutionRevision],
        clusters: list[FailureClusterRevision],
        classifications: list[FailureClassificationRevision],
    ) -> None:
        super().__init__(
            snapshots=snapshots,
            resolutions=resolutions,
            hygiene=[],
        )
        self.clusters = clusters
        self.classifications = classifications
        self.gates: list[TaskGateDecision] = []

    async def get_latest_snapshot(
        self,
        _connection: object,
        task_run_id: UUID,
    ) -> TaskResultSnapshot | None:
        matches = [item for item in self.snapshots if item.task_run_id == task_run_id]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def get_latest_resolution(
        self,
        _connection: object,
        execution_unit_id: UUID,
    ) -> UnitResolutionRevision | None:
        matches = [
            item
            for item in self.resolutions
            if item.execution_unit_id == execution_unit_id
        ]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def get_resolution_revision(
        self,
        _connection: object,
        *,
        execution_unit_id: UUID,
        revision: int,
    ) -> UnitResolutionRevision | None:
        return next(
            (
                item
                for item in self.resolutions
                if item.execution_unit_id == execution_unit_id
                and item.revision == revision
            ),
            None,
        )

    async def get_latest_task_gate_for_snapshot(
        self,
        _connection: object,
        result_snapshot_id: UUID,
    ) -> TaskGateDecision | None:
        matches = [
            item
            for item in self.gates
            if item.result_snapshot_id == result_snapshot_id
        ]
        return max(matches, key=lambda item: item.revision) if matches else None

    async def list_failure_clusters_page(
        self,
        _connection: object,
        *,
        result_snapshot_id: UUID,
        as_of: object,
        after_fingerprint: str | None,
        after_failure_cluster_id: UUID | None,
        after_cluster_revision_id: UUID | None,
        limit: int,
    ) -> tuple[
        tuple[FailureClusterRevision, FailureClassificationRevision | None],
        ...,
    ]:
        del as_of
        records: list[
            tuple[FailureClusterRevision, FailureClassificationRevision | None]
        ] = []
        for cluster in sorted(
            (
                item
                for item in self.clusters
                if item.result_snapshot_id == result_snapshot_id
            ),
            key=lambda item: (item.fingerprint, item.failure_cluster_id, item.id),
        ):
            key = (cluster.fingerprint, cluster.failure_cluster_id, cluster.id)
            if after_fingerprint is not None:
                assert after_failure_cluster_id is not None
                assert after_cluster_revision_id is not None
                if key <= (
                    after_fingerprint,
                    after_failure_cluster_id,
                    after_cluster_revision_id,
                ):
                    continue
            classification = await self.get_latest_failure_classification_for_cluster(
                _connection,
                cluster.id,
            )
            records.append((cluster, classification))
        return tuple(records[:limit])


@pytest.mark.anyio
async def test_queries_latest_exact_unit_and_stable_cluster_pages() -> None:
    run, actor, source, _, _, _, classification_service = await _service_fixture()
    batch = await classification_service.classify_snapshot(
        actor,
        source.snapshots[-1].id,
    )
    first_cluster = batch.clusters[0]
    first_classification = batch.classifications[0]
    second_signal = first_cluster.signal.model_copy(
        update={"signal_code": "SECOND_FAILURE_SIGNAL"}
    )
    second_cluster_content = FailureClusterRevisionContent(
        **{
            **first_cluster.model_dump(
                mode="python",
                by_alias=False,
                exclude={"cluster_hash"},
            ),
            "id": uuid4(),
            "failure_cluster_id": uuid4(),
            "signal": second_signal,
            "fingerprint": failure_cluster_fingerprint(second_signal),
        }
    )
    second_cluster = FailureClusterRevision(
        **second_cluster_content.model_dump(mode="python"),
        cluster_hash=failure_cluster_revision_hash(second_cluster_content),
    )
    second_classification_content = FailureClassificationRevisionContent(
        **{
            **first_classification.model_dump(
                mode="python",
                by_alias=False,
                exclude={"classification_hash"},
            ),
            "id": uuid4(),
            "failure_classification_id": uuid4(),
            "failure_cluster_revision_id": second_cluster.id,
            "client_mutation_id": "rule:classification:second:1",
        }
    )
    second_classification = FailureClassificationRevision(
        **second_classification_content.model_dump(mode="python"),
        classification_hash=failure_classification_revision_hash(
            second_classification_content
        ),
    )
    results = _QueryResultRepository(
        snapshots=source.snapshots,
        resolutions=source.resolutions,
        clusters=[first_cluster, second_cluster],
        classifications=[first_classification, second_classification],
    )
    tasks = _QueryTaskRepository(run, source.resolutions[0])
    service = ResultQueryService(
        cast(Any, _Database()),
        result_repository=cast(Any, results),
        task_repository=cast(Any, tasks),
    )

    latest = await service.get_task_result(actor, run.id, snapshot_id=None)
    exact = await service.get_task_result(
        actor,
        run.id,
        snapshot_id=source.snapshots[-1].id,
    )
    unit_latest = await service.get_unit_resolution(
        actor,
        source.resolutions[0].execution_unit_id,
        revision=None,
    )
    unit_exact = await service.get_unit_resolution(
        actor,
        source.resolutions[0].execution_unit_id,
        revision=source.resolutions[0].revision,
    )
    first_page = await service.list_snapshot_clusters(
        actor,
        source.snapshots[-1].id,
        cursor=None,
        limit=1,
    )
    second_page = await service.list_snapshot_clusters(
        actor,
        source.snapshots[-1].id,
        cursor=first_page.next_cursor,
        limit=1,
    )

    assert latest.selection is ResultSnapshotSelection.LATEST
    assert exact.selection is ResultSnapshotSelection.EXACT
    assert latest.result_snapshot == exact.result_snapshot == source.snapshots[-1]
    assert unit_latest == unit_exact == source.resolutions[0]
    assert first_page.next_cursor is not None
    assert len(first_page.items) == len(second_page.items) == 1
    assert first_page.as_of == second_page.as_of
    assert first_page.items[0].cluster.id != second_page.items[0].cluster.id


@pytest.mark.anyio
async def test_result_query_visibility_and_cursor_binding_fail_closed() -> None:
    run, actor, source, _, _, _, _ = await _service_fixture()
    results = _QueryResultRepository(
        snapshots=source.snapshots,
        resolutions=source.resolutions,
        clusters=[],
        classifications=[],
    )
    service = ResultQueryService(
        cast(Any, _Database()),
        result_repository=cast(Any, results),
        task_repository=cast(
            Any,
            _QueryTaskRepository(run, source.resolutions[0]),
        ),
    )
    hidden_actor = ActorContext(
        tenant_id=actor.tenant_id,
        actor_id=actor.actor_id,
        request_id="result-hidden",
    )
    cursor = encode_result_cluster_cursor(
        ResultClusterCursor(
            result_snapshot_id=source.snapshots[-1].id,
            as_of=source.snapshots[-1].created_at,
            fingerprint="sha256:" + "a" * 64,
            failure_cluster_id=uuid4(),
            cluster_revision_id=uuid4(),
        )
    )

    with pytest.raises(ApplicationError) as hidden:
        await service.get_task_result(hidden_actor, run.id, snapshot_id=None)
    assert hidden.value.status_code == 404

    with pytest.raises(ApplicationError) as mismatched:
        decode_result_cluster_cursor(
            cursor,
            expected_snapshot_id=uuid4(),
        )
    assert mismatched.value.status_code == 400

    decoded = decode_result_cluster_cursor(
        cursor,
        expected_snapshot_id=source.snapshots[-1].id,
    )
    assert decoded is not None
    assert decoded.result_snapshot_id == source.snapshots[-1].id
