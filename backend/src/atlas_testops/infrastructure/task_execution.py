"""Signed HTTPS adapter for ticket-bound production Task Unit execution."""

from __future__ import annotations

import json
from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from hmac import new as new_hmac
from re import fullmatch
from secrets import token_urlsafe
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID

import httpx2

from atlas_testops.core.config import Settings
from atlas_testops.orchestration.tasks import (
    TASK_UNIT_ATTEMPT_INPUT_SCHEMA,
    TASK_UNIT_EXECUTION_REQUEST_SCHEMA,
    TaskAttemptExecutionPayload,
    TaskAttemptExecutionStatus,
    TaskUnitExecutionPort,
    TaskUnitExecutionRequest,
)

TASK_EXECUTION_PATH = "/internal/v1/task-unit-executions:execute"
TASK_EXECUTOR_REQUEST_SCHEMA = "atlas.task-unit-executor-request/0.1"
TASK_EXECUTOR_RESULT_SCHEMA = "atlas.task-unit-executor-result/0.1"

AUTHORIZATION_HEADER = "Authorization"
AUTHORIZATION_SCHEME = "Atlas-Task-Executor-HMAC"
WORKER_HEADER = "X-Atlas-Executor-Worker-ID"
TENANT_HEADER = "X-Atlas-Executor-Tenant-ID"
ATTEMPT_HEADER = "X-Atlas-Executor-Attempt-ID"
TICKET_HEADER = "X-Atlas-Executor-Ticket-ID"
TICKET_DIGEST_HEADER = "X-Atlas-Executor-Ticket-Digest"
TIMESTAMP_HEADER = "X-Atlas-Executor-Request-Timestamp"
NONCE_HEADER = "X-Atlas-Executor-Request-Nonce"
CONTENT_DIGEST_HEADER = "X-Atlas-Executor-Content-SHA256"
RESPONSE_AUTHORIZATION_HEADER = "X-Atlas-Executor-Response-Authorization"
RESPONSE_TIMESTAMP_HEADER = "X-Atlas-Executor-Response-Timestamp"
RESPONSE_CONTENT_DIGEST_HEADER = "X-Atlas-Executor-Response-Content-SHA256"

_DIGEST_PATTERN = r"sha256:[0-9a-f]{64}"
_ERROR_CODE_PATTERN = r"[A-Z][A-Z0-9_]{0,63}"
_SAFE_STATUSES = frozenset(
    {
        "RESULT_FINALIZED",
        "EXECUTED_UNSEALED",
        "FAILED",
        "INFRA_ERROR",
        "INCONCLUSIVE",
        "CANCELED",
    }
)


class TaskExecutionAuthenticationError(RuntimeError):
    """A Task executor response did not authenticate against the request."""


@dataclass(frozen=True, slots=True)
class TaskExecutionRequestIdentity:
    """Exact authenticated machine scope presented to the executor service."""

    worker_identity: str
    tenant_id: UUID
    attempt_id: UUID
    ticket_id: UUID
    ticket_digest: str


class TaskExecutionMessageSigner:
    """Sign a complete request and verify the exact response correlation."""

    def __init__(self, key: bytes, *, maximum_clock_skew: timedelta) -> None:
        if len(key) < 32:
            raise ValueError("Task execution HMAC key must contain at least 32 bytes")
        if not timedelta(seconds=5) <= maximum_clock_skew <= timedelta(minutes=5):
            raise ValueError("Task execution clock skew is invalid")
        self._key = bytes(key)
        self._maximum_clock_skew = maximum_clock_skew

    @classmethod
    def from_base64_key(
        cls,
        encoded_key: str,
        *,
        maximum_clock_skew: timedelta = timedelta(seconds=30),
    ) -> TaskExecutionMessageSigner:
        try:
            key = b64decode(encoded_key, validate=True)
        except (Base64Error, ValueError) as error:
            raise ValueError("Task execution HMAC key must be valid base64") from error
        return cls(key, maximum_clock_skew=maximum_clock_skew)

    def sign_request_headers(
        self,
        *,
        worker_identity: str,
        tenant_id: UUID,
        attempt_id: UUID,
        ticket_id: UUID,
        ticket_digest: str,
        body: bytes,
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        timestamp = str(int((now or datetime.now(UTC)).timestamp()))
        request_nonce = nonce or token_urlsafe(24)
        content_digest = _content_digest(body)
        signature = self._request_signature(
            worker_identity=worker_identity,
            tenant_id=str(tenant_id),
            attempt_id=str(attempt_id),
            ticket_id=str(ticket_id),
            ticket_digest=ticket_digest,
            timestamp=timestamp,
            nonce=request_nonce,
            content_digest=content_digest,
        )
        return {
            AUTHORIZATION_HEADER: f"{AUTHORIZATION_SCHEME} {_b64url(signature)}",
            WORKER_HEADER: worker_identity,
            TENANT_HEADER: str(tenant_id),
            ATTEMPT_HEADER: str(attempt_id),
            TICKET_HEADER: str(ticket_id),
            TICKET_DIGEST_HEADER: ticket_digest,
            TIMESTAMP_HEADER: timestamp,
            NONCE_HEADER: request_nonce,
            CONTENT_DIGEST_HEADER: content_digest,
            "Idempotency-Key": str(attempt_id),
        }

    def verify_request_headers(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        now: datetime | None = None,
    ) -> TaskExecutionRequestIdentity:
        """Verify the server half before any executor-side ticket resolution."""

        try:
            authorization = headers[AUTHORIZATION_HEADER]
            scheme, supplied_token = authorization.split(" ", 1)
            supplied_signature = _b64url_decode(supplied_token)
            worker_identity = headers[WORKER_HEADER]
            tenant = headers[TENANT_HEADER]
            attempt = headers[ATTEMPT_HEADER]
            ticket = headers[TICKET_HEADER]
            ticket_digest = headers[TICKET_DIGEST_HEADER]
            timestamp = headers[TIMESTAMP_HEADER]
            nonce = headers[NONCE_HEADER]
            content_digest = headers[CONTENT_DIGEST_HEADER]
            tenant_id = UUID(tenant)
            attempt_id = UUID(attempt)
            ticket_id = UUID(ticket)
            request_time = datetime.fromtimestamp(int(timestamp), tz=UTC)
        except (Base64Error, KeyError, OverflowError, ValueError) as error:
            raise TaskExecutionAuthenticationError(
                "Task executor request authentication failed"
            ) from error
        selected_now = now or datetime.now(UTC)
        if (
            scheme != AUTHORIZATION_SCHEME
            or abs(selected_now - request_time) > self._maximum_clock_skew
            or fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,159}", worker_identity)
            is None
            or fullmatch(_DIGEST_PATTERN, ticket_digest) is None
            or not 20 <= len(nonce) <= 200
            or content_digest != _content_digest(body)
            or headers.get("Idempotency-Key") != attempt
        ):
            raise TaskExecutionAuthenticationError(
                "Task executor request authentication failed"
            )
        expected_signature = self._request_signature(
            worker_identity=worker_identity,
            tenant_id=tenant,
            attempt_id=attempt,
            ticket_id=ticket,
            ticket_digest=ticket_digest,
            timestamp=timestamp,
            nonce=nonce,
            content_digest=content_digest,
        )
        if not compare_digest(supplied_signature, expected_signature):
            raise TaskExecutionAuthenticationError(
                "Task executor request authentication failed"
            )
        return TaskExecutionRequestIdentity(
            worker_identity=worker_identity,
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            ticket_id=ticket_id,
            ticket_digest=ticket_digest,
        )

    def sign_response_headers(
        self,
        *,
        worker_identity: str,
        tenant_id: UUID,
        attempt_id: UUID,
        ticket_id: UUID,
        ticket_digest: str,
        request_nonce: str,
        request_content_digest: str,
        status_code: int,
        body: bytes,
        now: datetime | None = None,
    ) -> dict[str, str]:
        """Produce the server half of the public transport contract."""

        timestamp = str(int((now or datetime.now(UTC)).timestamp()))
        response_content_digest = _content_digest(body)
        signature = self._response_signature(
            worker_identity=worker_identity,
            tenant_id=str(tenant_id),
            attempt_id=str(attempt_id),
            ticket_id=str(ticket_id),
            ticket_digest=ticket_digest,
            request_nonce=request_nonce,
            request_content_digest=request_content_digest,
            status_code=str(status_code),
            timestamp=timestamp,
            response_content_digest=response_content_digest,
        )
        return {
            RESPONSE_AUTHORIZATION_HEADER: (
                f"{AUTHORIZATION_SCHEME} {_b64url(signature)}"
            ),
            WORKER_HEADER: worker_identity,
            TENANT_HEADER: str(tenant_id),
            ATTEMPT_HEADER: str(attempt_id),
            TICKET_HEADER: str(ticket_id),
            TICKET_DIGEST_HEADER: ticket_digest,
            NONCE_HEADER: request_nonce,
            CONTENT_DIGEST_HEADER: request_content_digest,
            RESPONSE_TIMESTAMP_HEADER: timestamp,
            RESPONSE_CONTENT_DIGEST_HEADER: response_content_digest,
            "Cache-Control": "no-store",
            "Content-Type": "application/json",
        }

    def verify_response_headers(
        self,
        *,
        headers: Mapping[str, str],
        worker_identity: str,
        tenant_id: UUID,
        attempt_id: UUID,
        ticket_id: UUID,
        ticket_digest: str,
        request_nonce: str,
        request_content_digest: str,
        status_code: int,
        body: bytes,
        now: datetime | None = None,
    ) -> None:
        try:
            authorization = headers[RESPONSE_AUTHORIZATION_HEADER]
            scheme, supplied_token = authorization.split(" ", 1)
            supplied_signature = _b64url_decode(supplied_token)
            response_timestamp = headers[RESPONSE_TIMESTAMP_HEADER]
            response_time = datetime.fromtimestamp(int(response_timestamp), tz=UTC)
            response_content_digest = headers[RESPONSE_CONTENT_DIGEST_HEADER]
        except (Base64Error, KeyError, OverflowError, ValueError) as error:
            raise TaskExecutionAuthenticationError(
                "Task executor response authentication failed"
            ) from error
        selected_now = now or datetime.now(UTC)
        cache_directives = {
            item.strip().casefold()
            for item in headers.get("Cache-Control", "").split(",")
        }
        if (
            scheme != AUTHORIZATION_SCHEME
            or abs(selected_now - response_time) > self._maximum_clock_skew
            or headers.get(WORKER_HEADER) != worker_identity
            or headers.get(TENANT_HEADER) != str(tenant_id)
            or headers.get(ATTEMPT_HEADER) != str(attempt_id)
            or headers.get(TICKET_HEADER) != str(ticket_id)
            or headers.get(TICKET_DIGEST_HEADER) != ticket_digest
            or headers.get(NONCE_HEADER) != request_nonce
            or headers.get(CONTENT_DIGEST_HEADER) != request_content_digest
            or response_content_digest != _content_digest(body)
            or "no-store" not in cache_directives
        ):
            raise TaskExecutionAuthenticationError(
                "Task executor response authentication failed"
            )
        expected_signature = self._response_signature(
            worker_identity=worker_identity,
            tenant_id=str(tenant_id),
            attempt_id=str(attempt_id),
            ticket_id=str(ticket_id),
            ticket_digest=ticket_digest,
            request_nonce=request_nonce,
            request_content_digest=request_content_digest,
            status_code=str(status_code),
            timestamp=response_timestamp,
            response_content_digest=response_content_digest,
        )
        if not compare_digest(supplied_signature, expected_signature):
            raise TaskExecutionAuthenticationError(
                "Task executor response authentication failed"
            )

    def _request_signature(
        self,
        *,
        worker_identity: str,
        tenant_id: str,
        attempt_id: str,
        ticket_id: str,
        ticket_digest: str,
        timestamp: str,
        nonce: str,
        content_digest: str,
    ) -> bytes:
        canonical = "\n".join(
            (
                "ATLAS-TASK-EXECUTOR-REQUEST-V1",
                "POST",
                TASK_EXECUTION_PATH,
                worker_identity,
                tenant_id,
                attempt_id,
                ticket_id,
                ticket_digest,
                timestamp,
                nonce,
                content_digest,
                attempt_id,
            )
        ).encode()
        return new_hmac(self._key, canonical, sha256).digest()

    def _response_signature(
        self,
        *,
        worker_identity: str,
        tenant_id: str,
        attempt_id: str,
        ticket_id: str,
        ticket_digest: str,
        request_nonce: str,
        request_content_digest: str,
        status_code: str,
        timestamp: str,
        response_content_digest: str,
    ) -> bytes:
        canonical = "\n".join(
            (
                "ATLAS-TASK-EXECUTOR-RESPONSE-V1",
                status_code,
                TASK_EXECUTION_PATH,
                worker_identity,
                tenant_id,
                attempt_id,
                ticket_id,
                ticket_digest,
                request_nonce,
                request_content_digest,
                timestamp,
                response_content_digest,
            )
        ).encode()
        return new_hmac(self._key, canonical, sha256).digest()


class HttpTaskUnitExecutionPort(TaskUnitExecutionPort):
    """Execute one ticket exactly once through a correlated signed HTTPS call."""

    def __init__(
        self,
        *,
        api_base_url: str,
        worker_identity: str,
        signer: TaskExecutionMessageSigner,
        timeout: timedelta,
        response_maximum_bytes: int,
        allow_insecure_http: bool = False,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        parsed = urlsplit(api_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Task execution API base URL must be an HTTP(S) origin")
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ValueError("Task execution API requires HTTPS")
        normalized_worker = worker_identity.strip()
        if fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,159}", normalized_worker) is None:
            raise ValueError("Task execution worker identity is invalid")
        if not timedelta(seconds=1) <= timeout <= timedelta(minutes=5):
            raise ValueError("Task execution HTTP timeout is invalid")
        if not 1_024 <= response_maximum_bytes <= 64 * 1024:
            raise ValueError("Task execution response limit is invalid")
        self._api_base_url = f"{parsed.scheme}://{parsed.netloc}"
        self._worker_identity = normalized_worker
        self._signer = signer
        self._timeout = timeout
        self._response_maximum_bytes = response_maximum_bytes
        self._client = client
        self._owns_client = client is None

    async def execute(
        self,
        request: TaskUnitExecutionRequest,
    ) -> TaskAttemptExecutionPayload:
        try:
            tenant_id, attempt_id, ticket_id, remaining = _validate_request(request)
        except (TypeError, ValueError):
            return _inconclusive("TASK_EXECUTION_REQUEST_INVALID")
        if remaining <= timedelta(0):
            return TaskAttemptExecutionPayload(
                status="CANCELED",
                error_code="TASK_ATTEMPT_DEADLINE_EXPIRED",
            )
        body = _request_body(request, worker_identity=self._worker_identity)
        headers = self._signer.sign_request_headers(
            worker_identity=self._worker_identity,
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            ticket_id=ticket_id,
            ticket_digest=request.ticket_digest,
            body=body,
        )
        headers["Accept"] = "application/json"
        headers["Content-Type"] = "application/json"
        timeout_seconds = min(self._timeout, remaining).total_seconds()
        try:
            client = self._require_client()
            async with client.stream(
                "POST",
                f"{self._api_base_url}{TASK_EXECUTION_PATH}",
                content=body,
                headers=headers,
                follow_redirects=False,
                timeout=timeout_seconds,
            ) as response:
                response_body = await self._read_bounded(response)
        except (httpx2.TransportError, TaskExecutionAuthenticationError, ValueError):
            return _inconclusive("TASK_EXECUTOR_OUTCOME_UNKNOWN")
        if response.status_code != 200:
            return _inconclusive("TASK_EXECUTOR_OUTCOME_UNKNOWN")
        if (
            response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
            != "application/json"
        ):
            return _inconclusive("TASK_EXECUTOR_RESPONSE_INVALID")
        try:
            self._signer.verify_response_headers(
                headers=response.headers,
                worker_identity=self._worker_identity,
                tenant_id=tenant_id,
                attempt_id=attempt_id,
                ticket_id=ticket_id,
                ticket_digest=request.ticket_digest,
                request_nonce=headers[NONCE_HEADER],
                request_content_digest=headers[CONTENT_DIGEST_HEADER],
                status_code=response.status_code,
                body=response_body,
            )
            return _decode_result(response_body)
        except (TaskExecutionAuthenticationError, TypeError, ValueError):
            return _inconclusive("TASK_EXECUTOR_RESPONSE_INVALID")

    async def aclose(self) -> None:
        """Close the owned HTTP pool when the Task Worker shuts down."""

        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx2.AsyncClient:
        if self._client is None:
            self._client = httpx2.AsyncClient(
                timeout=self._timeout.total_seconds(),
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    async def _read_bounded(self, response: httpx2.Response) -> bytes:
        length_header = response.headers.get("Content-Length")
        if length_header is not None:
            try:
                if int(length_header) > self._response_maximum_bytes:
                    raise ValueError("Task executor response exceeds the configured limit")
            except ValueError as error:
                raise ValueError("Task executor Content-Length is invalid") from error
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self._response_maximum_bytes:
                raise ValueError("Task executor response exceeds the configured limit")
            chunks.append(chunk)
        return b"".join(chunks)


def build_optional_task_unit_execution_port(
    settings: Settings,
) -> HttpTaskUnitExecutionPort | None:
    """Build the production adapter only from one complete reviewed configuration."""

    if not settings.task_execution_adapter_configured:
        return None
    api_base_url = settings.task_execution_api_base_url
    encoded_key = settings.task_execution_hmac_key_base64
    if api_base_url is None or encoded_key is None:
        raise RuntimeError("validated Task execution adapter configuration is incomplete")
    signer = TaskExecutionMessageSigner.from_base64_key(
        encoded_key.get_secret_value(),
        maximum_clock_skew=timedelta(
            seconds=settings.task_execution_clock_skew_seconds
        ),
    )
    return HttpTaskUnitExecutionPort(
        api_base_url=api_base_url,
        worker_identity=settings.task_execution_worker_identity,
        signer=signer,
        timeout=timedelta(seconds=settings.task_execution_http_timeout_seconds),
        response_maximum_bytes=settings.task_execution_response_maximum_bytes,
        allow_insecure_http=settings.task_execution_allow_insecure_http,
    )


def _validate_request(
    request: TaskUnitExecutionRequest,
) -> tuple[UUID, UUID, UUID, timedelta]:
    if (
        not isinstance(request, TaskUnitExecutionRequest)
        or request.schema_version != TASK_UNIT_EXECUTION_REQUEST_SCHEMA
        or request.attempt.schema_version != TASK_UNIT_ATTEMPT_INPUT_SCHEMA
        or fullmatch(_DIGEST_PATTERN, request.ticket_digest) is None
        or fullmatch(_DIGEST_PATTERN, request.attempt.request_digest) is None
        or fullmatch(_DIGEST_PATTERN, request.attempt.manifest_hash) is None
        or not 1 <= request.attempt.ordinal <= 100_000
        or not 1 <= request.attempt.activity_timeout_seconds <= 3_600
    ):
        raise ValueError("Task execution request is invalid")
    tenant_id = _canonical_uuid(request.attempt.tenant_id)
    _canonical_uuid(request.attempt.project_id)
    _canonical_uuid(request.attempt.task_run_id)
    _canonical_uuid(request.attempt.execution_unit_id)
    attempt_id = _canonical_uuid(request.attempt.unit_attempt_id)
    ticket_id = _canonical_uuid(request.ticket_id)
    deadline = datetime.fromisoformat(request.attempt.execution_deadline)
    if deadline.tzinfo is None:
        raise ValueError("Task execution deadline must be timezone-aware")
    return tenant_id, attempt_id, ticket_id, deadline - datetime.now(UTC)


def _request_body(
    request: TaskUnitExecutionRequest,
    *,
    worker_identity: str,
) -> bytes:
    attempt = request.attempt
    payload = {
        "schemaVersion": TASK_EXECUTOR_REQUEST_SCHEMA,
        "workerIdentity": worker_identity,
        "executionRequest": {
            "schemaVersion": request.schema_version,
            "attempt": {
                "schemaVersion": attempt.schema_version,
                "tenantId": attempt.tenant_id,
                "projectId": attempt.project_id,
                "taskRunId": attempt.task_run_id,
                "requestDigest": attempt.request_digest,
                "manifestHash": attempt.manifest_hash,
                "ordinal": attempt.ordinal,
                "executionUnitId": attempt.execution_unit_id,
                "unitAttemptId": attempt.unit_attempt_id,
                "executionDeadline": attempt.execution_deadline,
                "activityTimeoutSeconds": attempt.activity_timeout_seconds,
            },
            "ticketId": request.ticket_id,
            "ticketDigest": request.ticket_digest,
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _decode_result(body: bytes) -> TaskAttemptExecutionPayload:
    raw = json.loads(body)
    if not isinstance(raw, dict):
        raise TypeError("Task executor result must be an object")
    required = {"schemaVersion", "status", "errorCode"}
    optional = {"retryAfterSeconds", "resultRefId", "sealContentHash"}
    if not required <= set(raw) or set(raw) - (required | optional):
        raise TypeError("Task executor result has an invalid shape")
    if raw["schemaVersion"] != TASK_EXECUTOR_RESULT_SCHEMA:
        raise ValueError("Task executor result schema is unsupported")
    status = raw["status"]
    error_code = raw["errorCode"]
    retry_after = raw.get("retryAfterSeconds")
    result_ref_id = raw.get("resultRefId")
    seal_content_hash = raw.get("sealContentHash")
    if (
        not isinstance(status, str)
        or status not in _SAFE_STATUSES
        or (
            error_code is not None
            and (
                not isinstance(error_code, str)
                or fullmatch(_ERROR_CODE_PATTERN, error_code) is None
            )
        )
        or (
            retry_after is not None
            and (
                type(retry_after) is not int
                or status != "INFRA_ERROR"
                or not 1 <= retry_after <= 3_600
            )
        )
    ):
        raise ValueError("Task executor result status is invalid")
    has_result_ref = result_ref_id is not None or seal_content_hash is not None
    if status == "RESULT_FINALIZED":
        if (
            error_code is not None
            or retry_after is not None
            or not isinstance(result_ref_id, str)
            or str(UUID(result_ref_id)) != result_ref_id
            or not isinstance(seal_content_hash, str)
            or fullmatch(_DIGEST_PATTERN, seal_content_hash) is None
        ):
            raise ValueError("Task executor finalized result is invalid")
    elif has_result_ref:
        raise ValueError("Only finalized Task execution can return a ResultRef")
    return TaskAttemptExecutionPayload(
        status=cast(TaskAttemptExecutionStatus, status),
        error_code=cast(str | None, error_code),
        retry_after_seconds=retry_after,
        result_ref_id=cast(str | None, result_ref_id),
        seal_content_hash=cast(str | None, seal_content_hash),
    )


def _inconclusive(error_code: str) -> TaskAttemptExecutionPayload:
    return TaskAttemptExecutionPayload(
        status="INCONCLUSIVE",
        error_code=error_code,
    )


def _content_digest(body: bytes) -> str:
    return f"sha256:{sha256(body).hexdigest()}"


def _canonical_uuid(value: str) -> UUID:
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError("Task execution UUID must use canonical form")
    return parsed


def _b64url(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


__all__ = [
    "TASK_EXECUTION_PATH",
    "TASK_EXECUTOR_REQUEST_SCHEMA",
    "TASK_EXECUTOR_RESULT_SCHEMA",
    "HttpTaskUnitExecutionPort",
    "TaskExecutionAuthenticationError",
    "TaskExecutionMessageSigner",
    "TaskExecutionRequestIdentity",
    "build_optional_task_unit_execution_port",
]
