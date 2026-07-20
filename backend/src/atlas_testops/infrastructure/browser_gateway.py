"""Signed HTTP gateway used by the database-free Browser Worker."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TypeVar
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from pydantic import ValidationError

from atlas_testops.api.problem_details import ProblemDetails
from atlas_testops.application.ports.browser_runtime import BrowserRuntimeGateway
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case import DebugRun
from atlas_testops.domain.runtime import (
    AppendBrowserRuntimeReport,
    BrowserEvidenceFinalization,
    BrowserExecutionBundle,
    BrowserFinalizeCommand,
    BrowserRuntimeReport,
    BrowserRuntimeTransition,
    DebugLiveFrame,
    DebugLiveFrameUpdate,
    EvidenceManifest,
    FinalizeDebugEvidence,
)
from atlas_testops.infrastructure.browser_auth import BrowserRuntimeRequestSigner

ResponseModel = TypeVar(
    "ResponseModel",
    DebugRun,
    BrowserExecutionBundle,
    BrowserRuntimeReport,
    BrowserEvidenceFinalization,
    DebugLiveFrame,
)


class HttpBrowserRuntimeGateway(BrowserRuntimeGateway):
    """Call only the bounded internal Runtime API with an expiring execution permit."""

    def __init__(
        self,
        *,
        api_base_url: str,
        tenant_id: UUID,
        worker_identity: str,
        execution_permit: str,
        request_signer: BrowserRuntimeRequestSigner,
        timeout: timedelta = timedelta(seconds=20),
        allow_insecure_http: bool = False,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(api_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("browser runtime API base URL must be an HTTP(S) origin")
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ValueError(
                "browser runtime API base URL requires HTTPS unless insecure HTTP "
                "is explicitly allowed"
            )
        if timeout <= timedelta(0) or timeout > timedelta(minutes=2):
            raise ValueError("browser runtime HTTP timeout must be between 0 and 2 minutes")
        normalized_worker = worker_identity.strip()
        if not 3 <= len(normalized_worker) <= 160:
            raise ValueError("browser runtime worker identity is invalid")
        if len(execution_permit) < 32:
            raise ValueError("browser runtime execution permit is invalid")
        self._tenant_id = tenant_id
        self._worker_identity = normalized_worker
        self._execution_permit = execution_permit
        self._request_signer = request_signer
        self._client = client or httpx2.AsyncClient(
            base_url=f"{parsed.scheme}://{parsed.netloc}",
            timeout=timeout.total_seconds(),
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close an internally-owned connection pool after one Temporal Activity."""

        if self._owns_client:
            await self._client.aclose()

    async def get_execution_bundle(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
    ) -> BrowserExecutionBundle:
        self._require_scope(tenant_id, worker_identity)
        path = f"/internal/v1/debug-runs/{run_id}/browser-execution"
        response = await self._request("GET", path, b"")
        return self._parse(response, BrowserExecutionBundle)

    async def mark_ready(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        execution_contract_id: UUID,
        execution_contract_digest: str,
    ) -> DebugRun:
        self._require_scope(tenant_id, worker_identity)
        body = BrowserRuntimeTransition(
            execution_contract_id=execution_contract_id,
            execution_contract_digest=execution_contract_digest,
        ).model_dump_json(by_alias=True).encode()
        response = await self._request(
            "POST",
            f"/internal/v1/debug-runs/{run_id}/browser-execution:ready",
            body,
        )
        return self._parse(response, DebugRun)

    async def start_execution(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        execution_contract_id: UUID,
        execution_contract_digest: str,
    ) -> DebugRun:
        self._require_scope(tenant_id, worker_identity)
        body = BrowserRuntimeTransition(
            execution_contract_id=execution_contract_id,
            execution_contract_digest=execution_contract_digest,
        ).model_dump_json(by_alias=True).encode()
        response = await self._request(
            "POST",
            f"/internal/v1/debug-runs/{run_id}/browser-execution:start",
            body,
        )
        return self._parse(response, DebugRun)

    async def append_report(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        report: AppendBrowserRuntimeReport,
    ) -> BrowserRuntimeReport:
        self._require_scope(tenant_id, worker_identity)
        response = await self._request(
            "POST",
            f"/internal/v1/debug-runs/{run_id}/browser-reports",
            report.model_dump_json(by_alias=True).encode(),
        )
        persisted = self._parse(response, BrowserRuntimeReport)
        if persisted.value != report:
            raise self._invalid_response()
        return persisted

    async def publish_live_frame(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        command: DebugLiveFrameUpdate,
    ) -> DebugLiveFrame:
        self._require_scope(tenant_id, worker_identity)
        response = await self._request(
            "POST",
            f"/internal/v1/debug-runs/{run_id}/live-frame",
            command.model_dump_json(by_alias=True).encode(),
        )
        frame = self._parse(response, DebugLiveFrame)
        if (
            frame.debug_run_id != run_id
            or frame.execution_contract_id != command.execution_contract_id
            or frame.frame_revision != command.frame_revision
            or frame.content_digest != command.content_digest
        ):
            raise self._invalid_response()
        return frame

    async def finalize_evidence(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        command: FinalizeDebugEvidence,
    ) -> tuple[DebugRun, EvidenceManifest]:
        self._require_scope(tenant_id, worker_identity)
        body = BrowserFinalizeCommand(command=command).model_dump_json(by_alias=True).encode()
        response = await self._request(
            "POST",
            f"/internal/v1/debug-runs/{run_id}/browser-execution:finalize-evidence",
            body,
        )
        result = self._parse(response, BrowserEvidenceFinalization)
        if (
            result.run.id != run_id
            or result.evidence_manifest.execution_contract_id
            != command.execution_contract_id
        ):
            raise self._invalid_response()
        return result.run, result.evidence_manifest

    async def _request(self, method: str, path: str, body: bytes) -> httpx2.Response:
        headers = self._request_signer.sign_headers(
            method=method,
            path=path,
            body=body,
            tenant_id=self._tenant_id,
            worker_identity=self._worker_identity,
            permit=self._execution_permit,
        )
        headers["Accept"] = "application/json, application/problem+json"
        if body:
            headers["Content-Type"] = "application/json"
        response: httpx2.Response | None = None
        last_transport_error: httpx2.TransportError | None = None
        for attempt in range(3):
            try:
                response = await self._client.request(
                    method,
                    path,
                    content=body if body else None,
                    headers=headers,
                )
            except httpx2.TransportError as error:
                last_transport_error = error
            else:
                if response.status_code not in {500, 502, 503, 504} or attempt == 2:
                    break
            await asyncio.sleep(0.05 * (attempt + 1))
        if response is None:
            raise ApplicationError(
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                title="Browser Runtime API 不可用",
                detail="Browser Worker 未能连接受信控制面。",
                status_code=503,
            ) from last_transport_error
        if response.status_code >= 400:
            self._raise_problem(response)
        cache_directives = {
            item.strip().casefold()
            for item in response.headers.get("Cache-Control", "").split(",")
        }
        if "no-store" not in cache_directives:
            raise self._invalid_response()
        return response

    @staticmethod
    def _parse(
        response: httpx2.Response,
        model: type[ResponseModel],
    ) -> ResponseModel:
        try:
            return model.model_validate_json(response.content)
        except ValidationError as error:
            raise HttpBrowserRuntimeGateway._invalid_response() from error

    @staticmethod
    def _raise_problem(response: httpx2.Response) -> None:
        try:
            problem = ProblemDetails.model_validate_json(response.content)
        except ValidationError as error:
            raise HttpBrowserRuntimeGateway._invalid_response() from error
        raise ApplicationError(
            error_code=problem.error_code,
            title=problem.title,
            detail=problem.detail,
            status_code=problem.status,
        )

    def _require_scope(self, tenant_id: UUID, worker_identity: str) -> None:
        if tenant_id != self._tenant_id or worker_identity != self._worker_identity:
            raise ValueError("browser runtime gateway scope changed")

    @staticmethod
    def _invalid_response() -> ApplicationError:
        return ApplicationError(
            error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            title="Browser Runtime 响应无效",
            detail="控制面返回了不可信或不完整的内部协议响应。",
            status_code=503,
        )
