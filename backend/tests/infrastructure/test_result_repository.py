"""Repository tests for immutable AttemptSeal facts and stable ResultRef."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow
from tests.application.test_result_projection import _task_hygiene_resolution
from tests.domain.result.test_attempt_seal import _seal
from tests.domain.result.test_failure_classification_contracts import (
    _classification_content,
    _cluster_content,
)
from tests.domain.result.test_result_projection_contracts import (
    _notice,
    _resolution,
    _snapshot,
)
from tests.infrastructure.test_task_run_repository import NOW, _aggregate

from atlas_testops.domain.result import (
    TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
    DataHygiene,
    FailureClassificationRevision,
    FailureClusterRevision,
    ResultIntegrityIncident,
    ResultRef,
    TaskResultReevaluationCommand,
    TaskResultReevaluationCommandContent,
    TaskResultSnapshotFinality,
    failure_classification_revision_hash,
    failure_cluster_revision_hash,
    task_result_reevaluation_command_hash,
)
from atlas_testops.infrastructure.repositories.results import ResultFactRepository


class _Cursor:
    def __init__(self, result: DictRow | tuple[DictRow, ...] | None) -> None:
        self._result = result

    async def fetchone(self) -> DictRow | None:
        if isinstance(self._result, tuple):
            return self._result[0] if self._result else None
        return self._result

    async def fetchall(self) -> list[DictRow]:
        if isinstance(self._result, tuple):
            return list(self._result)
        return [self._result] if self._result is not None else []


class _Connection:
    def __init__(self, *rows: DictRow | tuple[DictRow, ...] | None) -> None:
        self._rows = list(rows)
        self.calls: list[tuple[str, Sequence[object] | None]] = []

    async def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> _Cursor:
        self.calls.append((query, params))
        row = self._rows.pop(0) if self._rows else None
        return _Cursor(row)


def _result_ref() -> ResultRef:
    seal = _seal()
    return ResultRef(
        id=uuid4(),
        tenant_id=seal.tenant_id,
        project_id=seal.project_id,
        task_run_id=seal.task_run_id,
        execution_unit_id=seal.execution_unit_id,
        unit_attempt_id=seal.unit_attempt_id,
        seal_id=seal.seal_id,
        seal_content_hash=seal.content_hash,
        created_at=NOW,
    )


@pytest.mark.anyio
async def test_loads_exact_seal_and_result_ref() -> None:
    seal = _seal()
    result_ref = _result_ref().model_copy(
        update={
            "seal_id": seal.seal_id,
            "seal_content_hash": seal.content_hash,
            "unit_attempt_id": seal.unit_attempt_id,
        }
    )
    connection = _Connection(
        cast(DictRow, {"seal": seal.model_dump(mode="json", by_alias=True)}),
        result_ref.model_dump(mode="python"),
    )
    repository = ResultFactRepository()

    loaded_seal = await repository.get_seal_by_attempt(
        cast(AsyncConnection[DictRow], connection),
        seal.unit_attempt_id,
    )
    loaded_ref = await repository.get_ref_by_attempt(
        cast(AsyncConnection[DictRow], connection),
        seal.unit_attempt_id,
    )

    assert loaded_seal == seal
    assert loaded_ref == result_ref
    assert all(call[1] == (seal.unit_attempt_id,) for call in connection.calls)


@pytest.mark.anyio
async def test_inserts_fact_ref_and_idempotent_integrity_incident() -> None:
    seal = _seal()
    result_ref = _result_ref().model_copy(
        update={
            "seal_id": seal.seal_id,
            "seal_content_hash": seal.content_hash,
            "unit_attempt_id": seal.unit_attempt_id,
        }
    )
    incident = ResultIntegrityIncident(
        id=uuid4(),
        tenant_id=seal.tenant_id,
        project_id=seal.project_id,
        task_run_id=seal.task_run_id,
        execution_unit_id=seal.execution_unit_id,
        unit_attempt_id=seal.unit_attempt_id,
        accepted_seal_id=seal.seal_id,
        accepted_content_hash=seal.content_hash,
        conflicting_seal_id=uuid4(),
        conflicting_content_hash="sha256:" + "f" * 64,
        signature_kid=seal.signature.kid,
        observed_at=NOW + timedelta(minutes=1),
    )
    connection = _Connection()
    repository = ResultFactRepository()

    await repository.insert_fact(
        cast(AsyncConnection[DictRow], connection),
        seal=seal,
        accepted_at=NOW,
    )
    await repository.insert_ref(
        cast(AsyncConnection[DictRow], connection),
        result_ref,
    )
    await repository.append_integrity_incident(
        cast(AsyncConnection[DictRow], connection),
        incident,
    )

    fact_query, fact_params = connection.calls[0]
    ref_query, ref_params = connection.calls[1]
    incident_query, incident_params = connection.calls[2]
    assert "insert into atlas.unit_attempt_result_fact" in fact_query
    assert fact_params is not None and len(fact_params) == 33
    assert "insert into atlas.result_ref" in ref_query
    assert ref_params is not None and len(ref_params) == 9
    assert "on conflict (unit_attempt_id, conflicting_content_hash) do nothing" in (incident_query)
    assert incident_params is not None and len(incident_params) == 12


@pytest.mark.anyio
async def test_loads_latest_task_resolutions_and_snapshot() -> None:
    resolution = _resolution()
    snapshot = _snapshot()
    connection = _Connection(
        (resolution.model_dump(mode="python"),),
        cast(
            DictRow,
            {"snapshot": snapshot.model_dump(mode="json", by_alias=True)},
        ),
        cast(
            DictRow,
            {"snapshot": snapshot.model_dump(mode="json", by_alias=True)},
        ),
        cast(
            DictRow,
            {"snapshot": snapshot.model_dump(mode="json", by_alias=True)},
        ),
        cast(
            DictRow,
            {"snapshot": snapshot.model_dump(mode="json", by_alias=True)},
        ),
    )
    repository = ResultFactRepository()

    resolutions = await repository.list_latest_resolutions_for_task(
        cast(AsyncConnection[DictRow], connection),
        resolution.task_run_id,
    )
    loaded_snapshot = await repository.get_latest_snapshot(
        cast(AsyncConnection[DictRow], connection),
        snapshot.task_run_id,
    )
    loaded_quality = await repository.get_latest_snapshot_for_finality(
        cast(AsyncConnection[DictRow], connection),
        snapshot.task_run_id,
        TaskResultSnapshotFinality.QUALITY_FINAL,
    )
    loaded_exact = await repository.get_snapshot_by_id(
        cast(AsyncConnection[DictRow], connection),
        snapshot.id,
    )
    loaded_reevaluated = await repository.get_reevaluated_snapshot(
        cast(AsyncConnection[DictRow], connection),
        task_run_id=snapshot.task_run_id,
        source_snapshot_id=snapshot.id,
        policy_digest=TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
    )

    assert resolutions == (resolution,)
    assert loaded_snapshot == snapshot
    assert loaded_quality == snapshot
    assert loaded_exact == snapshot
    assert loaded_reevaluated == snapshot
    assert "distinct on (resolution.execution_unit_id)" in connection.calls[0][0]
    assert "order by unit.ordinal" in connection.calls[0][0]
    assert "order by revision desc" in connection.calls[1][0]
    assert "and finality = %s" in connection.calls[2][0]
    assert "where id = %s" in connection.calls[3][0]
    assert "reevaluation_source_snapshot_id = %s" in connection.calls[4][0]


@pytest.mark.anyio
async def test_inserts_complete_task_snapshot_projection() -> None:
    snapshot = _snapshot()
    connection = _Connection()
    repository = ResultFactRepository()

    await repository.insert_snapshot(
        cast(AsyncConnection[DictRow], connection),
        snapshot,
    )

    query, params = connection.calls[0]
    assert "insert into atlas.task_result_snapshot" in query
    assert params is not None and len(params) == 27
    assert params[0] == snapshot.id
    assert params[9:13] == (None, None, None, None)
    assert params[-2] == snapshot.snapshot_hash


@pytest.mark.anyio
async def test_loads_and_inserts_explicit_reevaluation_command() -> None:
    snapshot = _snapshot()
    content = TaskResultReevaluationCommandContent(
        id=uuid4(),
        tenant_id=snapshot.tenant_id,
        project_id=snapshot.project_id,
        task_run_id=snapshot.task_run_id,
        source_snapshot_id=snapshot.id,
        target_policy_digest=TASK_RESULT_SNAPSHOT_REEVALUATED_POLICY_DIGEST,
        client_mutation_id="reevaluate-repository-001",
        requested_by=uuid4(),
        requested_at=NOW,
    )
    command = TaskResultReevaluationCommand(
        **content.model_dump(mode="python"),
        command_hash=task_result_reevaluation_command_hash(content),
    )
    connection = _Connection(
        cast(
            DictRow,
            {"command": command.model_dump(mode="json", by_alias=True)},
        ),
    )
    repository = ResultFactRepository()

    loaded = await repository.get_reevaluation_command(
        cast(AsyncConnection[DictRow], connection),
        task_run_id=command.task_run_id,
        client_mutation_id=command.client_mutation_id,
    )
    await repository.insert_reevaluation_command(
        cast(AsyncConnection[DictRow], connection),
        command,
    )

    assert loaded == command
    query, params = connection.calls[1]
    assert "insert into atlas.task_result_reevaluation_command" in query
    assert params is not None and len(params) == 12


@pytest.mark.anyio
async def test_lists_latest_task_hygiene_with_explicit_projection_columns() -> None:
    _, _, units, attempts = _aggregate(unit_count=1)
    resolution = _task_hygiene_resolution(
        units[0],
        attempts[0],
        hygiene=DataHygiene.CLEANED,
    )
    connection = _Connection((resolution.model_dump(mode="python"),))
    repository = ResultFactRepository()

    loaded = await repository.list_latest_hygiene_resolutions_for_task(
        cast(AsyncConnection[DictRow], connection),
        resolution.task_run_id,
    )

    assert loaded == (resolution,)
    query, params = connection.calls[0]
    assert "select latest.id, latest.unit_hygiene_resolution_id" in query
    assert "select latest.*" not in query
    assert "order by unit.ordinal" in query
    assert params == (resolution.task_run_id,)


@pytest.mark.anyio
async def test_loads_closure_and_ordered_unit_projection_inputs() -> None:
    seal = _seal()
    notice = _notice()
    resolution = _resolution()
    connection = _Connection(
        cast(DictRow, {"notice": notice.model_dump(mode="json", by_alias=True)}),
        (cast(DictRow, {"seal": seal.model_dump(mode="json", by_alias=True)}),),
        (cast(DictRow, {"notice": notice.model_dump(mode="json", by_alias=True)}),),
        resolution.model_dump(mode="python", by_alias=False),
    )
    repository = ResultFactRepository()

    loaded_notice = await repository.get_closure_by_attempt(
        cast(AsyncConnection[DictRow], connection),
        notice.unit_attempt_id,
    )
    seals = await repository.list_seals_for_unit(
        cast(AsyncConnection[DictRow], connection),
        resolution.execution_unit_id,
    )
    notices = await repository.list_closures_for_unit(
        cast(AsyncConnection[DictRow], connection),
        resolution.execution_unit_id,
    )
    latest = await repository.get_latest_resolution(
        cast(AsyncConnection[DictRow], connection),
        resolution.execution_unit_id,
    )

    assert loaded_notice == notice
    assert seals == (seal,)
    assert notices == (notice,)
    assert latest == resolution
    assert len(connection.calls) == 4


@pytest.mark.anyio
async def test_inserts_closure_and_resolution_projection() -> None:
    notice = _notice()
    resolution = _resolution()
    connection = _Connection()
    repository = ResultFactRepository()

    await repository.insert_closure(
        cast(AsyncConnection[DictRow], connection),
        notice,
    )
    await repository.insert_resolution(
        cast(AsyncConnection[DictRow], connection),
        resolution,
    )

    closure_query, closure_params = connection.calls[0]
    resolution_query, resolution_params = connection.calls[1]
    assert "insert into atlas.attempt_closure_notice" in closure_query
    assert closure_params is not None and len(closure_params) == 21
    assert "insert into atlas.unit_resolution_revision" in resolution_query
    assert resolution_params is not None and len(resolution_params) == 26


@pytest.mark.anyio
async def test_lists_fixture_bindings_with_join_qualified_columns() -> None:
    execution_unit_id = uuid4()
    connection = _Connection(())
    repository = ResultFactRepository()

    bindings = await repository.list_fixture_bindings_for_unit(
        cast(AsyncConnection[DictRow], connection),
        execution_unit_id,
    )

    assert bindings == ()
    query, params = connection.calls[0]
    assert "select binding.id, binding.tenant_id" in query
    assert "join atlas.unit_attempt attempt" in query
    assert params == (execution_unit_id,)


@pytest.mark.anyio
async def test_loads_and_inserts_failure_cluster_and_classification_facts() -> None:
    resolution = _resolution()
    _, _, units, attempts = _aggregate(unit_count=1)
    hygiene = _task_hygiene_resolution(
        units[0],
        attempts[0],
        hygiene=DataHygiene.CLEANED,
    )
    cluster_content = _cluster_content()
    cluster = FailureClusterRevision(
        **cluster_content.model_dump(mode="python"),
        cluster_hash=failure_cluster_revision_hash(cluster_content),
    )
    classification_content = _classification_content()
    classification = FailureClassificationRevision(
        **classification_content.model_dump(mode="python"),
        classification_hash=failure_classification_revision_hash(classification_content),
    )
    connection = _Connection(
        None,
        (resolution.model_dump(mode="python"),),
        (hygiene.model_dump(mode="python"),),
        cast(DictRow, {"cluster": cluster.model_dump(mode="json", by_alias=True)}),
        cast(DictRow, {"cluster": cluster.model_dump(mode="json", by_alias=True)}),
        cast(
            DictRow,
            {"classification": classification.model_dump(mode="json", by_alias=True)},
        ),
        None,
        cast(
            DictRow,
            {"classification": classification.model_dump(mode="json", by_alias=True)},
        ),
    )
    repository = ResultFactRepository()

    await repository.lock_failure_classification_snapshot(
        cast(AsyncConnection[DictRow], connection),
        cluster.result_snapshot_id,
    )
    exact_resolutions = await repository.list_resolutions_by_ids(
        cast(AsyncConnection[DictRow], connection),
        (resolution.id,),
    )
    exact_hygiene = await repository.list_hygiene_resolutions_by_ids(
        cast(AsyncConnection[DictRow], connection),
        (hygiene.id,),
    )
    loaded_cluster = await repository.get_failure_cluster(
        cast(AsyncConnection[DictRow], connection),
        result_snapshot_id=cluster.result_snapshot_id,
        fingerprint=cluster.fingerprint,
        policy_digest=cluster.fingerprint_policy_digest,
    )
    loaded_exact_cluster = await repository.get_failure_cluster_by_revision_id(
        cast(AsyncConnection[DictRow], connection),
        cluster.id,
    )
    loaded_classification = (
        await repository.get_latest_failure_classification_for_cluster(
            cast(AsyncConnection[DictRow], connection),
            cluster.id,
        )
    )
    loaded_for_update = (
        await repository.get_latest_failure_classification_for_update(
            cast(AsyncConnection[DictRow], connection),
            classification.failure_classification_id,
        )
    )
    await repository.insert_failure_cluster(
        cast(AsyncConnection[DictRow], connection),
        cluster,
    )
    await repository.insert_failure_classification(
        cast(AsyncConnection[DictRow], connection),
        classification,
    )

    assert exact_resolutions == (resolution,)
    assert exact_hygiene == (hygiene,)
    assert loaded_cluster == loaded_exact_cluster == cluster
    assert loaded_classification == loaded_for_update == classification
    assert "pg_advisory_xact_lock" in connection.calls[0][0]
    assert "hashtextextended(%s::text, 1)" in connection.calls[0][0]
    assert "select resolution.id, resolution.unit_resolution_id" in connection.calls[1][0]
    assert "select resolution.id, resolution.unit_hygiene_resolution_id" in (
        connection.calls[2][0]
    )
    assert "pg_advisory_xact_lock" in connection.calls[6][0]
    assert "for update" not in connection.calls[7][0]
    cluster_query, cluster_params = connection.calls[8]
    classification_query, classification_params = connection.calls[9]
    assert "insert into atlas.failure_cluster_revision" in cluster_query
    assert cluster_params is not None and len(cluster_params) == 19
    assert "insert into atlas.failure_classification_revision" in classification_query
    assert classification_params is not None and len(classification_params) == 26


@pytest.mark.anyio
async def test_reads_exact_resolution_gate_and_as_of_cluster_page() -> None:
    resolution = _resolution()
    cluster_content = _cluster_content()
    cluster = FailureClusterRevision(
        **cluster_content.model_dump(mode="python"),
        cluster_hash=failure_cluster_revision_hash(cluster_content),
    )
    classification_content = _classification_content()
    classification = FailureClassificationRevision(
        **classification_content.model_dump(mode="python"),
        classification_hash=failure_classification_revision_hash(
            classification_content
        ),
    )
    connection = _Connection(
        resolution.model_dump(mode="python"),
        None,
        (
            cast(
                DictRow,
                {
                    "cluster": cluster.model_dump(mode="json", by_alias=True),
                    "classification": classification.model_dump(
                        mode="json",
                        by_alias=True,
                    ),
                },
            ),
        ),
    )
    repository = ResultFactRepository()

    exact = await repository.get_resolution_revision(
        cast(AsyncConnection[DictRow], connection),
        execution_unit_id=resolution.execution_unit_id,
        revision=resolution.revision,
    )
    gate = await repository.get_latest_task_gate_for_snapshot(
        cast(AsyncConnection[DictRow], connection),
        cluster.result_snapshot_id,
    )
    page = await repository.list_failure_clusters_page(
        cast(AsyncConnection[DictRow], connection),
        result_snapshot_id=cluster.result_snapshot_id,
        as_of=NOW,
        after_fingerprint=None,
        after_failure_cluster_id=None,
        after_cluster_revision_id=None,
        limit=51,
    )

    assert exact == resolution
    assert gate is None
    assert page == ((cluster, classification),)
    resolution_query, resolution_params = connection.calls[0]
    gate_query, gate_params = connection.calls[1]
    page_query, page_params = connection.calls[2]
    assert "revision = %s" in resolution_query
    assert resolution_params == (resolution.execution_unit_id, resolution.revision)
    assert "where result_snapshot_id = %s" in gate_query
    assert gate_params == (cluster.result_snapshot_id,)
    assert "source.created_at <= %s" in page_query
    assert "left join lateral" in page_query
    assert page_params == (
        cluster.result_snapshot_id,
        NOW,
        NOW,
        None,
        None,
        None,
        None,
        51,
    )
