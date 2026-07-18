"""HTTP contracts for comparable briefs and pinned InsightSnapshots."""

from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient
from tests.domain.insight.test_insight_contracts import (
    ACTOR_ID,
    PLAN_A,
    PROJECT_ID,
    _actor,
    _source,
)
from tests.infrastructure.test_task_run_repository import NOW

from atlas_testops.api.dependencies import get_insight_service
from atlas_testops.api.security import get_actor
from atlas_testops.application.insights import (
    InsightService,
    _compile_brief,
    _pin_brief,
)
from atlas_testops.application.platform import CommandResult
from atlas_testops.core.config import Settings
from atlas_testops.domain.insight import InsightBrief, InsightSnapshot, RequestInsightSnapshot
from atlas_testops.main import create_app


class RecordingInsightService:
    def __init__(self, brief: InsightBrief, snapshot: InsightSnapshot) -> None:
        self.brief = brief
        self.snapshot = snapshot
        self.calls: list[tuple[object, ...]] = []

    async def preview(
        self,
        *args: object,
        **kwargs: object,
    ) -> InsightBrief:
        self.calls.append(("preview", *args, kwargs))
        return self.brief

    async def pin_snapshot(
        self,
        *args: object,
        **kwargs: object,
    ) -> CommandResult[InsightSnapshot]:
        self.calls.append(("pin", *args, kwargs))
        return CommandResult(
            value=self.snapshot,
            status_code=201,
            replayed=False,
        )

    async def get_snapshot(
        self,
        *args: object,
        **kwargs: object,
    ) -> InsightSnapshot:
        self.calls.append(("get", *args, kwargs))
        return self.snapshot


def _contracts() -> tuple[RecordingInsightService, RequestInsightSnapshot]:
    brief = _compile_brief(
        actor=_actor(),
        project_id=PROJECT_ID,
        window_days=30,
        as_of=NOW,
        sources=(
            _source(
                "api",
                days_ago=1,
                manifest_count=10,
                trusted_passed=9,
                stable=8,
                plan_id=PLAN_A,
            ),
        ),
    )
    command = RequestInsightSnapshot(
        window_days=30,
        as_of=NOW,
        client_mutation_id="insight:pin:api:1",
    )
    snapshot = _pin_brief(
        brief,
        request_hash="sha256:" + "a" * 64,
        client_mutation_id=command.client_mutation_id,
        created_by=ACTOR_ID,
        created_at=NOW,
    )
    return RecordingInsightService(brief, snapshot), command


def _client(service: RecordingInsightService) -> TestClient:
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = _actor
    app.dependency_overrides[get_insight_service] = lambda: cast(
        InsightService,
        service,
    )
    return TestClient(app)


def test_insight_reads_expose_dataset_headers_and_conditional_get() -> None:
    service, _ = _contracts()
    client = _client(service)

    with client:
        preview = client.get(
            f"/v1/projects/{PROJECT_ID}/insights/brief?windowDays=30"
        )
        preview_not_modified = client.get(
            f"/v1/projects/{PROJECT_ID}/insights/brief?windowDays=30",
            headers={"If-None-Match": preview.headers["etag"]},
        )
        exact = client.get(f"/v1/insight-snapshots/{service.snapshot.id}")
        exact_not_modified = client.get(
            f"/v1/insight-snapshots/{service.snapshot.id}",
            headers={"If-None-Match": exact.headers["etag"]},
        )

    assert preview.status_code == 200
    assert preview.json()["current"]["executionUnitCount"] == 10
    assert preview.headers["x-dataset-cut-digest"] == (
        service.brief.dataset_cut.source_set_digest
    )
    assert preview.headers["x-projection-watermark"]
    assert preview_not_modified.status_code == 304
    assert exact.status_code == 200
    assert exact.headers["cache-control"].endswith("immutable")
    assert exact_not_modified.status_code == 304
    assert [call[0] for call in service.calls] == [
        "preview",
        "preview",
        "get",
        "get",
    ]
    assert cast(dict[str, Any], service.calls[0][-1]) == {
        "window_days": 30,
        "as_of": None,
    }


def test_insight_pin_preserves_idempotency_and_location() -> None:
    service, command = _contracts()
    client = _client(service)

    with client:
        response = client.post(
            f"/v1/projects/{PROJECT_ID}/insight-snapshots",
            json=command.model_dump(mode="json", by_alias=True),
            headers={"Idempotency-Key": command.client_mutation_id},
        )

    assert response.status_code == 201
    assert response.json()["snapshotHash"] == service.snapshot.snapshot_hash
    assert response.headers["location"] == (
        f"/v1/insight-snapshots/{service.snapshot.id}"
    )
    assert response.headers["idempotency-replayed"] == "false"
    assert response.headers["x-insight-query-hash"] == (
        service.snapshot.dataset_cut.query_hash
    )
    assert cast(dict[str, Any], service.calls[0][-1]) == {
        "idempotency_key": command.client_mutation_id,
    }
