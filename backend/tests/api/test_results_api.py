"""HTTP contracts for snapshot-explicit Result reads and review commands."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from fastapi.testclient import TestClient
from tests.domain.result.test_failure_classification_contracts import (
    _classification_content,
    _cluster_content,
)
from tests.domain.result.test_result_projection_contracts import _resolution
from tests.domain.result.test_task_gate_contracts import _final_snapshot
from tests.infrastructure.test_task_run_repository import NOW

from atlas_testops.api.dependencies import (
    get_result_classification_service,
    get_result_gate_service,
    get_result_query_service,
)
from atlas_testops.api.security import get_actor
from atlas_testops.application.access import ActorContext
from atlas_testops.application.platform import CommandResult
from atlas_testops.application.result_classification import ResultClassificationService
from atlas_testops.application.result_gate import ResultGateService
from atlas_testops.application.result_queries import ResultQueryService
from atlas_testops.core.config import Settings
from atlas_testops.domain.result import (
    TASK_GATE_POLICY_DIGEST,
    ClassificationConfidence,
    ClassificationJudgmentState,
    FailureClassificationRevision,
    FailureClusterItem,
    FailureClusterPage,
    FailureClusterRevision,
    FailureDomain,
    RequestFailureClassificationRevision,
    RequestTaskGateEvaluation,
    ResultSnapshotSelection,
    TaskGateDecision,
    TaskGateDecisionContent,
    TaskGateVerdict,
    TaskResultView,
    UnitResolutionRevision,
    failure_classification_revision_hash,
    failure_cluster_revision_hash,
    task_gate_decision_hash,
)
from atlas_testops.main import create_app


class RecordingResultQueryService:
    def __init__(
        self,
        *,
        task_result: TaskResultView,
        resolution: UnitResolutionRevision,
        clusters: FailureClusterPage,
    ) -> None:
        self.task_result = task_result
        self.resolution = resolution
        self.clusters = clusters
        self.calls: list[tuple[object, ...]] = []

    async def get_task_result(
        self,
        *args: object,
        **kwargs: object,
    ) -> TaskResultView:
        self.calls.append(("task-result", *args, kwargs))
        return self.task_result

    async def get_unit_resolution(
        self,
        *args: object,
        **kwargs: object,
    ) -> UnitResolutionRevision:
        self.calls.append(("unit-resolution", *args, kwargs))
        return self.resolution

    async def list_snapshot_clusters(
        self,
        *args: object,
        **kwargs: object,
    ) -> FailureClusterPage:
        self.calls.append(("clusters", *args, kwargs))
        return self.clusters


class RecordingClassificationService:
    def __init__(self, value: FailureClassificationRevision) -> None:
        self.value = value
        self.calls: list[tuple[object, ...]] = []

    async def revise_classification(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[FailureClassificationRevision]:
        self.calls.append((*args, kwargs))
        return CommandResult(value=self.value, status_code=201, replayed=False)


class RecordingGateService:
    def __init__(self, value: TaskGateDecision) -> None:
        self.value = value
        self.calls: list[tuple[object, ...]] = []

    async def evaluate(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[TaskGateDecision]:
        self.calls.append((*args, kwargs))
        return CommandResult(value=self.value, status_code=201, replayed=False)


def _contracts() -> tuple[
    ActorContext,
    RecordingResultQueryService,
    RecordingClassificationService,
    RecordingGateService,
]:
    snapshot = _final_snapshot()
    gate_content = TaskGateDecisionContent(
        id=UUID("00000000-0000-7000-8000-000000000051"),
        task_gate_id=UUID("00000000-0000-7000-8000-000000000052"),
        tenant_id=snapshot.tenant_id,
        project_id=snapshot.project_id,
        task_run_id=snapshot.task_run_id,
        result_snapshot_id=snapshot.id,
        result_snapshot_hash=snapshot.snapshot_hash,
        revision=1,
        failure_classification_revision_ids=(),
        classification_set_hash="sha256:" + "f" * 64,
        gate_policy_digest=TASK_GATE_POLICY_DIGEST,
        decision=TaskGateVerdict.ACCEPTED,
        reasons=(),
        evaluated_by=UUID("00000000-0000-7000-8000-000000000053"),
        client_mutation_id="gate:api:evaluate:1",
        evaluated_at=NOW,
    )
    gate = TaskGateDecision(
        **gate_content.model_dump(mode="python"),
        decision_hash=task_gate_decision_hash(gate_content),
    )
    task_result = TaskResultView(
        task_run_id=snapshot.task_run_id,
        selection=ResultSnapshotSelection.LATEST,
        result_snapshot=snapshot,
        task_gate_decision=gate,
        projection_watermark=snapshot.projection_watermark,
    )
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
    page = FailureClusterPage(
        result_snapshot_id=cluster.result_snapshot_id,
        as_of=NOW,
        projection_watermark=cluster.projection_watermark,
        items=(
            FailureClusterItem(
                cluster=cluster,
                classification=classification,
            ),
        ),
    )
    actor = ActorContext(
        tenant_id=snapshot.tenant_id,
        actor_id=gate.evaluated_by,
        request_id="result-api-test",
        development_override=True,
    )
    return (
        actor,
        RecordingResultQueryService(
            task_result=task_result,
            resolution=resolution,
            clusters=page,
        ),
        RecordingClassificationService(classification),
        RecordingGateService(gate),
    )


def _client(
    actor: ActorContext,
    query: RecordingResultQueryService,
    classification: RecordingClassificationService,
    gate: RecordingGateService,
) -> TestClient:
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = lambda: actor
    app.dependency_overrides[get_result_query_service] = lambda: cast(
        ResultQueryService,
        query,
    )
    app.dependency_overrides[get_result_classification_service] = lambda: cast(
        ResultClassificationService,
        classification,
    )
    app.dependency_overrides[get_result_gate_service] = lambda: cast(
        ResultGateService,
        gate,
    )
    return TestClient(app)


def test_result_reads_expose_snapshot_headers_and_conditional_get() -> None:
    actor, query, classification, gate = _contracts()
    client = _client(actor, query, classification, gate)

    with client:
        task_result = client.get(
            f"/v1/task-runs/{query.task_result.task_run_id}/result"
        )
        task_not_modified = client.get(
            f"/v1/task-runs/{query.task_result.task_run_id}/result",
            headers={"If-None-Match": task_result.headers["etag"]},
        )
        resolution = client.get(
            f"/v1/execution-units/{query.resolution.execution_unit_id}/resolution"
            f"?revision={query.resolution.revision}"
        )
        resolution_not_modified = client.get(
            f"/v1/execution-units/{query.resolution.execution_unit_id}/resolution"
            f"?revision={query.resolution.revision}",
            headers={"If-None-Match": resolution.headers["etag"]},
        )
        clusters = client.get(
            f"/v1/result-snapshots/{query.clusters.result_snapshot_id}/clusters"
            "?limit=7"
        )
        clusters_not_modified = client.get(
            f"/v1/result-snapshots/{query.clusters.result_snapshot_id}/clusters"
            "?limit=7",
            headers={"If-None-Match": clusters.headers["etag"]},
        )

    assert task_result.status_code == 200
    assert task_result.json()["resultSnapshot"]["id"] == str(
        query.task_result.result_snapshot.id
    )
    assert task_result.headers["x-result-snapshot-id"] == str(
        query.task_result.result_snapshot.id
    )
    assert task_not_modified.status_code == 304
    assert resolution.status_code == 200
    assert resolution.headers["cache-control"].endswith("immutable")
    assert resolution_not_modified.status_code == 304
    assert clusters.status_code == 200
    assert clusters.json()["items"][0]["cluster"]["id"] == str(
        query.clusters.items[0].cluster.id
    )
    assert clusters.headers["x-result-as-of"]
    assert clusters_not_modified.status_code == 304
    assert [call[0] for call in query.calls] == [
        "task-result",
        "task-result",
        "unit-resolution",
        "unit-resolution",
        "clusters",
        "clusters",
    ]
    assert query.calls[4][-1] == {"cursor": None, "limit": 7}


def test_result_review_and_gate_routes_preserve_idempotency_contract() -> None:
    actor, query, classification, gate = _contracts()
    client = _client(actor, query, classification, gate)
    baseline = classification.value
    review = RequestFailureClassificationRevision(
        expected_revision=baseline.revision,
        failure_domain=FailureDomain.PRODUCT,
        hypothesis_code="PRODUCT_DEFECT_CONFIRMED",
        hypothesis="Reviewed evidence attributes the failure to product behavior.",
        confidence=ClassificationConfidence(numerator=8_500),
        supporting_evidence_refs=baseline.supporting_evidence_refs,
        judgment_state=ClassificationJudgmentState.HUMAN_REVISED,
        client_mutation_id="review:result-api:1",
    )
    gate_request = RequestTaskGateEvaluation(
        result_snapshot_id=gate.value.result_snapshot_id,
        client_mutation_id="gate:result-api:1",
    )

    with client:
        reviewed = client.post(
            f"/v1/failure-classifications/"
            f"{baseline.failure_classification_id}/revisions",
            json=review.model_dump(mode="json", by_alias=True),
            headers={"Idempotency-Key": review.client_mutation_id},
        )
        evaluated = client.post(
            "/v1/task-gates/evaluations",
            json=gate_request.model_dump(mode="json", by_alias=True),
            headers={"Idempotency-Key": gate_request.client_mutation_id},
        )

    assert reviewed.status_code == 201
    assert reviewed.headers["etag"] == f'"revision-{baseline.revision}"'
    assert reviewed.headers["idempotency-replayed"] == "false"
    assert reviewed.headers["cache-control"] == "no-store"
    assert evaluated.status_code == 201
    assert evaluated.json()["decision"] == "ACCEPTED"
    assert evaluated.headers["location"].endswith(
        f"?snapshotId={gate.value.result_snapshot_id}"
    )
    assert cast(dict[str, Any], classification.calls[0][-1]) == {
        "idempotency_key": review.client_mutation_id
    }
    assert cast(dict[str, Any], gate.calls[0][-1]) == {
        "idempotency_key": gate_request.client_mutation_id
    }


def test_openapi_exposes_public_result_contracts() -> None:
    document = create_app(Settings(environment="test", cors_origins=[])).openapi()
    paths = document["paths"]

    assert "/v1/task-runs/{runId}/result" in paths
    assert "/v1/execution-units/{unitId}/resolution" in paths
    assert "/v1/result-snapshots/{snapshotId}/clusters" in paths
    assert "/v1/failure-classifications/{classificationId}/revisions" in paths
    assert "/v1/task-gates/evaluations" in paths
    assert (
        paths["/v1/task-gates/evaluations"]["post"]["requestBody"]["required"]
        is True
    )
