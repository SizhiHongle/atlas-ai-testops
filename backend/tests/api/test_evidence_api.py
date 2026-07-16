"""HTTP contract tests for private Evidence Manifest and verified content reads."""

from collections.abc import Callable
from datetime import timedelta
from typing import cast
from uuid import UUID, uuid7

import pytest
from fastapi.testclient import TestClient
from tests.domain.case.test_runtime_evidence import DIGEST_A, _artifact, _contract, _run

from atlas_testops.api.dependencies import get_evidence_service
from atlas_testops.api.security import get_actor
from atlas_testops.application.access import ActorContext
from atlas_testops.application.evidence import EvidenceArtifactContent, EvidenceService
from atlas_testops.core.config import Settings
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import TestIntent as CaseIntent
from atlas_testops.domain.runtime import (
    EvidenceManifest,
    EvidenceReadGrant,
    EvidenceReadPurpose,
    FinalizeDebugEvidence,
    IssueEvidenceReadGrant,
    build_evidence_manifest,
)
from atlas_testops.domain.workflow import WorkflowGraph
from atlas_testops.main import create_app

TOKEN = "evr_" + "x" * 48
PAYLOAD = b"verified-png-bytes"


def _manifest(
    graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> EvidenceManifest:
    run = _run(graph, intent_factory)
    contract = _contract(run, run.requested_at + timedelta(seconds=1))
    captured_at = contract.created_at + timedelta(seconds=1)
    manifest, _private = build_evidence_manifest(
        manifest_id=uuid7(),
        run=run,
        contract=contract,
        command=FinalizeDebugEvidence(
            execution_contract_id=contract.id,
            execution_contract_digest=contract.content_digest,
            artifacts=(_artifact(captured_at),),
            event_chain_head_digest=DIGEST_A,
            event_count=1,
            finalized_at=captured_at + timedelta(milliseconds=1),
        ),
    )
    return manifest


class RecordingEvidenceService:
    def __init__(self, manifest: EvidenceManifest) -> None:
        self.manifest = manifest
        self.read_calls: list[tuple[UUID, str, EvidenceReadPurpose]] = []
        self.issue_calls: list[tuple[UUID, UUID, EvidenceReadPurpose]] = []
        self.content_error: ApplicationError | None = None

    async def get_manifest(
        self,
        _actor: ActorContext,
        run_id: UUID,
    ) -> EvidenceManifest:
        assert run_id == self.manifest.debug_run_id
        return self.manifest

    async def issue_read_grant(
        self,
        _actor: ActorContext,
        run_id: UUID,
        artifact_id: UUID,
        command: IssueEvidenceReadGrant,
    ) -> EvidenceReadGrant:
        self.issue_calls.append((run_id, artifact_id, command.purpose))
        return EvidenceReadGrant(
            id=uuid7(),
            artifact_id=artifact_id,
            purpose=command.purpose,
            read_token=TOKEN,
            issued_at=self.manifest.finalized_at,
            expires_at=self.manifest.finalized_at + timedelta(seconds=60),
            max_reads=8,
        )

    async def read_content(
        self,
        _actor: ActorContext,
        artifact_id: UUID,
        *,
        read_token: str,
        purpose: EvidenceReadPurpose,
    ) -> EvidenceArtifactContent:
        self.read_calls.append((artifact_id, read_token, purpose))
        if self.content_error is not None:
            raise self.content_error
        return EvidenceArtifactContent(
            artifact_id=artifact_id,
            payload=PAYLOAD,
            mime_type="image/png",
            purpose=purpose,
            content_disposition=(
                "inline" if purpose is EvidenceReadPurpose.INLINE else "attachment"
            ),
            filename=f"atlas-evidence-{artifact_id.hex}.png",
        )


def _client(service: RecordingEvidenceService) -> TestClient:
    app = create_app(Settings(environment="test", cors_origins=[]))
    app.dependency_overrides[get_actor] = lambda: ActorContext(
        tenant_id=service.manifest.tenant_id,
        actor_id=uuid7(),
        request_id="request-evidence-api",
        development_override=True,
    )
    app.dependency_overrides[get_evidence_service] = lambda: cast(
        EvidenceService,
        service,
    )
    return TestClient(app)


def test_manifest_grant_and_content_use_private_no_store_contract(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
) -> None:
    manifest = _manifest(valid_graph, intent_factory)
    service = RecordingEvidenceService(manifest)
    artifact_id = manifest.artifacts[0].id

    with _client(service) as client:
        manifest_response = client.get(f"/v1/debug-runs/{manifest.debug_run_id}/evidence")
        grant_response = client.post(
            f"/v1/debug-runs/{manifest.debug_run_id}/evidence/{artifact_id}/read-tokens",
            json={"purpose": "INLINE"},
        )
        content_response = client.get(
            f"/v1/evidence/artifacts/{artifact_id}/content",
            headers={"Authorization": f"Atlas-Evidence {TOKEN}"},
        )

    assert manifest_response.status_code == 200
    assert "objectRef" not in manifest_response.text
    assert manifest_response.headers["cache-control"] == "private, no-store, max-age=0"
    assert manifest_response.headers["x-content-type-options"] == "nosniff"
    assert grant_response.status_code == 201
    assert grant_response.json()["readToken"] == TOKEN
    assert grant_response.headers["cache-control"] == "private, no-store, max-age=0"
    assert service.issue_calls == [(manifest.debug_run_id, artifact_id, EvidenceReadPurpose.INLINE)]
    assert content_response.status_code == 200
    assert content_response.content == PAYLOAD
    assert content_response.headers["content-type"] == "image/png"
    assert content_response.headers["content-length"] == str(len(PAYLOAD))
    assert content_response.headers["cache-control"] == "private, no-store, max-age=0"
    assert content_response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert content_response.headers["cross-origin-resource-policy"] == "same-origin"
    assert content_response.headers["content-disposition"].startswith("inline;")
    assert service.read_calls == [(artifact_id, TOKEN, EvidenceReadPurpose.INLINE)]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": TOKEN},
        {"Authorization": f"Bearer {TOKEN}"},
        {"Authorization": "Atlas-Evidence token with spaces"},
        {"Authorization": "Atlas-Evidence evr_" + "x" * 512},
    ],
)
def test_content_rejects_missing_or_wrong_authorization_with_generic_401(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
    headers: dict[str, str],
) -> None:
    manifest = _manifest(valid_graph, intent_factory)
    service = RecordingEvidenceService(manifest)
    artifact_id = manifest.artifacts[0].id

    with _client(service) as client:
        response = client.get(
            f"/v1/evidence/artifacts/{artifact_id}/content?readToken={TOKEN}",
            headers=headers,
        )

    assert response.status_code == 401
    assert response.json()["errorCode"] == "AUTHENTICATION_FAILED"
    assert response.headers["www-authenticate"] == "Atlas-Evidence"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert TOKEN not in response.text
    assert service.read_calls == []


@pytest.mark.parametrize(
    ("error_code", "status_code"),
    [
        (ErrorCode.EVIDENCE_INTEGRITY_FAILED, 409),
        (ErrorCode.DEPENDENCY_UNAVAILABLE, 503),
    ],
)
def test_content_errors_keep_problem_details_private_and_empty(
    valid_graph: WorkflowGraph,
    intent_factory: Callable[..., CaseIntent],
    error_code: ErrorCode,
    status_code: int,
) -> None:
    manifest = _manifest(valid_graph, intent_factory)
    service = RecordingEvidenceService(manifest)
    service.content_error = ApplicationError(
        error_code=error_code,
        title="Evidence unavailable",
        detail="No unverified bytes are returned.",
        status_code=status_code,
    )
    artifact_id = manifest.artifacts[0].id

    with _client(service) as client:
        response = client.get(
            f"/v1/evidence/artifacts/{artifact_id}/content",
            headers={"Authorization": f"Atlas-Evidence {TOKEN}"},
        )

    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert PAYLOAD not in response.content


def test_openapi_requires_platform_session_and_evidence_grant_together() -> None:
    app = create_app(Settings(environment="test", cors_origins=[]))
    document = app.openapi()
    schemes = document["components"]["securitySchemes"]
    content_operation = document["paths"]["/v1/evidence/artifacts/{artifactId}/content"]["get"]

    assert schemes["PlatformSession"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "atlas_session",
        "description": "Validated Atlas Platform Session cookie.",
    }
    assert schemes["AtlasEvidenceReadGrant"]["name"] == "Authorization"
    assert content_operation["security"] == [{"PlatformSession": [], "AtlasEvidenceReadGrant": []}]
    assert {parameter["name"] for parameter in content_operation.get("parameters", [])}.isdisjoint(
        {"Authorization", "readToken"}
    )
    binary = content_operation["responses"]["200"]["content"]["image/png"]["schema"]
    assert binary == {"type": "string", "format": "binary"}
    manifest_security = document["paths"]["/v1/debug-runs/{runId}/evidence"]["get"]
    assert manifest_security["security"] == [{"PlatformSession": []}]
