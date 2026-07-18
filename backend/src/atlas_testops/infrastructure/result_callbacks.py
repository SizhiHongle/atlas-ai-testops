"""Replay-bounded HMAC signing and fixed-endpoint Task Gate callbacks."""

from __future__ import annotations

from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from hmac import new as new_hmac
from urllib.parse import urlsplit

import httpx2

from atlas_testops.application.result_callback_delivery import (
    TaskGateCallbackSendResult,
    TaskGateCallbackSendStatus,
)
from atlas_testops.domain.result import (
    TASK_GATE_CALLBACK_SCHEMA_VERSION,
    TaskGateCallbackEvent,
    TaskGateCallbackEventContent,
    encode_task_gate_callback_document,
    task_gate_callback_signing_bytes,
)
from atlas_testops.infrastructure.result_callback_intents import (
    ClaimedTaskGateCallbackIntent,
)

CALLBACK_SCHEMA_HEADER = "X-Atlas-Callback-Schema"


class TaskGateCallbackAuthenticationError(ValueError):
    """Raised when a callback signature or replay window is invalid."""


class TaskGateCallbackSigner:
    """Sign and verify the exact six-field callback contract."""

    def __init__(self, key: bytes, *, replay_window: timedelta) -> None:
        if len(key) < 32:
            raise ValueError("Task Gate callback HMAC key must contain at least 32 bytes")
        if not timedelta(seconds=30) <= replay_window <= timedelta(hours=24):
            raise ValueError("Task Gate callback replay window is invalid")
        self._key = bytes(key)
        self._replay_window = replay_window

    @classmethod
    def from_base64_key(
        cls,
        encoded_key: str,
        *,
        replay_window: timedelta = timedelta(minutes=5),
    ) -> TaskGateCallbackSigner:
        """Decode one deployment secret without ever persisting it."""

        try:
            key = b64decode(encoded_key, validate=True)
        except (Base64Error, ValueError) as error:
            raise ValueError("Task Gate callback HMAC key must be valid base64") from error
        return cls(key, replay_window=replay_window)

    def sign(
        self,
        content: TaskGateCallbackEventContent,
    ) -> TaskGateCallbackEvent:
        """Attach a deterministic URL-safe HMAC to one canonical event."""

        signature = new_hmac(
            self._key,
            task_gate_callback_signing_bytes(content),
            "sha256",
        ).digest()
        return TaskGateCallbackEvent(
            **content.model_dump(mode="python"),
            signature=f"hmac-sha256:{_b64url(signature)}",
        )

    def verify(
        self,
        value: TaskGateCallbackEvent | Mapping[str, object],
        *,
        now: datetime | None = None,
    ) -> TaskGateCallbackEvent:
        """Authenticate exact content and reject stale or future replay."""

        try:
            event = (
                value
                if isinstance(value, TaskGateCallbackEvent)
                else TaskGateCallbackEvent.model_validate(value)
            )
            supplied = _b64url_decode(event.signature.removeprefix("hmac-sha256:"))
        except (Base64Error, ValueError) as error:
            raise TaskGateCallbackAuthenticationError(
                "Task Gate callback authentication failed"
            ) from error
        selected_now = now or datetime.now(UTC)
        if selected_now.tzinfo is None:
            raise ValueError("Task Gate callback verification clock must be aware")
        if abs(selected_now.astimezone(UTC) - event.timestamp) > self._replay_window:
            raise TaskGateCallbackAuthenticationError("Task Gate callback authentication failed")
        expected = new_hmac(
            self._key,
            task_gate_callback_signing_bytes(event),
            "sha256",
        ).digest()
        if not compare_digest(supplied, expected):
            raise TaskGateCallbackAuthenticationError("Task Gate callback authentication failed")
        return event


class HttpTaskGateCallbackSender:
    """Send one signed event to one operator-configured endpoint."""

    def __init__(
        self,
        *,
        callback_url: str,
        signer: TaskGateCallbackSigner,
        timeout: timedelta,
        allow_insecure_http: bool = False,
        client: httpx2.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        parsed = urlsplit(callback_url)
        try:
            parsed_port = parsed.port
        except ValueError as error:
            raise ValueError("Task Gate callback URL is invalid") from error
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (parsed_port is not None and not 1 <= parsed_port <= 65_535)
        ):
            raise ValueError("Task Gate callback URL must be one exact HTTP(S) endpoint")
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ValueError("Task Gate callback endpoint requires HTTPS")
        if not timedelta(seconds=1) <= timeout <= timedelta(minutes=1):
            raise ValueError("Task Gate callback HTTP timeout is invalid")
        self._callback_url = parsed.geturl()
        self._signer = signer
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._clock = clock or _utc_now

    async def deliver(
        self,
        intent: ClaimedTaskGateCallbackIntent,
    ) -> TaskGateCallbackSendResult:
        """Perform exactly one bounded, non-redirecting HTTP request."""

        try:
            timestamp = self._clock().astimezone(UTC).replace(microsecond=0)
            event = self._signer.sign(
                TaskGateCallbackEventContent(
                    event_id=intent.event_id,
                    task_run_id=intent.task_run_id,
                    manifest_hash=intent.manifest_hash,
                    gate_decision=intent.gate_decision,
                    timestamp=timestamp,
                )
            )
            body = encode_task_gate_callback_document(event)
        except TypeError, ValueError:
            return TaskGateCallbackSendResult(
                status=TaskGateCallbackSendStatus.PERMANENT_FAILURE,
                error_code="TASK_GATE_CALLBACK_EVENT_INVALID",
            )
        headers = {
            "Accept": "application/json",
            "Cache-Control": "no-store",
            "Content-Type": "application/json",
            "Idempotency-Key": str(intent.event_id),
            CALLBACK_SCHEMA_HEADER: TASK_GATE_CALLBACK_SCHEMA_VERSION,
        }
        try:
            client = self._require_client()
            async with client.stream(
                "POST",
                self._callback_url,
                content=body,
                headers=headers,
                follow_redirects=False,
                timeout=self._timeout.total_seconds(),
            ) as response:
                status_code = response.status_code
        except httpx2.TransportError:
            return TaskGateCallbackSendResult(
                status=TaskGateCallbackSendStatus.RETRYABLE,
                error_code="TASK_GATE_CALLBACK_TRANSPORT_ERROR",
            )
        if 200 <= status_code <= 299:
            return TaskGateCallbackSendResult(
                status=TaskGateCallbackSendStatus.DELIVERED,
                response_status_code=status_code,
            )
        if status_code in {408, 425, 429} or 500 <= status_code <= 599:
            return TaskGateCallbackSendResult(
                status=TaskGateCallbackSendStatus.RETRYABLE,
                error_code="TASK_GATE_CALLBACK_HTTP_RETRYABLE",
                response_status_code=status_code,
            )
        return TaskGateCallbackSendResult(
            status=TaskGateCallbackSendStatus.PERMANENT_FAILURE,
            error_code="TASK_GATE_CALLBACK_HTTP_REJECTED",
            response_status_code=status_code,
        )

    async def aclose(self) -> None:
        """Close an HTTP pool created by this adapter."""

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


def _b64url(value: bytes) -> str:
    return urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    if len(value) != 43:
        raise ValueError("Task Gate callback signature has an invalid length")
    return urlsafe_b64decode(value + "=")


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "CALLBACK_SCHEMA_HEADER",
    "HttpTaskGateCallbackSender",
    "TaskGateCallbackAuthenticationError",
    "TaskGateCallbackSigner",
]
