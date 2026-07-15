"""Short-lived execution permits and request signatures for Browser Runtime HTTP."""

from __future__ import annotations

from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from hmac import new as new_hmac
from secrets import token_urlsafe
from uuid import UUID

from pydantic import AwareDatetime, Field, ValidationError

from atlas_testops.core.contracts import FrozenWireModel

AUTHORIZATION_HEADER = "Authorization"
PERMIT_HEADER = "X-Atlas-Execution-Permit"
TENANT_HEADER = "X-Atlas-Runtime-Tenant-ID"
WORKER_HEADER = "X-Atlas-Worker-ID"
TIMESTAMP_HEADER = "X-Atlas-Request-Timestamp"
NONCE_HEADER = "X-Atlas-Request-Nonce"
CONTENT_DIGEST_HEADER = "X-Atlas-Content-SHA256"
AUTHORIZATION_SCHEME = "Atlas-HMAC"


class BrowserRuntimeAuthenticationError(RuntimeError):
    """A runtime permit or signed request did not authenticate."""


class BrowserRuntimePermitClaims(FrozenWireModel):
    """API-minted authority for one Worker and one exact DebugRun."""

    version: int = Field(default=1, ge=1, le=1)
    tenant_id: UUID
    run_id: UUID
    worker_identity: str = Field(
        min_length=3,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$",
    )
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    nonce: str = Field(
        min_length=20,
        max_length=200,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class BrowserRuntimePermitSigner:
    """Mint and verify HMAC permits that never grant cross-run authority."""

    def __init__(self, key: bytes, *, maximum_lifetime: timedelta) -> None:
        if len(key) < 32:
            raise ValueError("browser runtime permit key must contain at least 32 bytes")
        if not timedelta(minutes=1) <= maximum_lifetime <= timedelta(hours=24):
            raise ValueError("browser runtime permit lifetime must be 1 minute to 24 hours")
        self._key = bytes(key)
        self._maximum_lifetime = maximum_lifetime

    @classmethod
    def from_base64_key(
        cls,
        encoded_key: str,
        *,
        maximum_lifetime: timedelta,
    ) -> BrowserRuntimePermitSigner:
        return cls(
            _decode_standard_key(encoded_key, "browser runtime permit"),
            maximum_lifetime=maximum_lifetime,
        )

    def mint(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        worker_identity: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> str:
        if (
            issued_at.tzinfo is None
            or expires_at.tzinfo is None
            or expires_at <= issued_at
            or expires_at - issued_at > self._maximum_lifetime
        ):
            raise ValueError("browser runtime permit time window is invalid")
        claims = BrowserRuntimePermitClaims(
            tenant_id=tenant_id,
            run_id=run_id,
            worker_identity=worker_identity,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=token_urlsafe(24),
        )
        payload = claims.model_dump_json(by_alias=True).encode()
        signature = new_hmac(self._key, payload, sha256).digest()
        return f"{_b64url(payload)}.{_b64url(signature)}"

    def verify(
        self,
        token: str,
        *,
        now: datetime,
    ) -> BrowserRuntimePermitClaims:
        try:
            payload_token, signature_token = token.split(".", 1)
            payload = _b64url_decode(payload_token)
            supplied_signature = _b64url_decode(signature_token)
        except (Base64Error, ValueError) as error:
            raise BrowserRuntimeAuthenticationError("runtime permit is invalid") from error
        expected_signature = new_hmac(self._key, payload, sha256).digest()
        if not compare_digest(supplied_signature, expected_signature):
            raise BrowserRuntimeAuthenticationError("runtime permit is invalid")
        try:
            claims = BrowserRuntimePermitClaims.model_validate_json(payload)
        except ValidationError as error:
            raise BrowserRuntimeAuthenticationError("runtime permit is invalid") from error
        if (
            now.tzinfo is None
            or claims.issued_at > now + timedelta(seconds=30)
            or claims.expires_at <= now
            or claims.expires_at - claims.issued_at > self._maximum_lifetime
        ):
            raise BrowserRuntimeAuthenticationError("runtime permit has expired")
        return claims


@dataclass(frozen=True, slots=True)
class BrowserRuntimeRequestIdentity:
    """Verified machine identity projected from one signed HTTP request."""

    tenant_id: UUID
    worker_identity: str
    permit: str


class BrowserRuntimeRequestSigner:
    """Sign every request so a leaked permit alone cannot call the Runtime API."""

    def __init__(self, key: bytes, *, maximum_clock_skew: timedelta) -> None:
        if len(key) < 32:
            raise ValueError("browser runtime request key must contain at least 32 bytes")
        if not timedelta(seconds=5) <= maximum_clock_skew <= timedelta(minutes=5):
            raise ValueError("browser runtime request clock skew is invalid")
        self._key = bytes(key)
        self._maximum_clock_skew = maximum_clock_skew

    @classmethod
    def from_base64_key(
        cls,
        encoded_key: str,
        *,
        maximum_clock_skew: timedelta = timedelta(seconds=30),
    ) -> BrowserRuntimeRequestSigner:
        return cls(
            _decode_standard_key(encoded_key, "browser runtime request"),
            maximum_clock_skew=maximum_clock_skew,
        )

    def sign_headers(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        tenant_id: UUID,
        worker_identity: str,
        permit: str,
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        timestamp = int((now or datetime.now(UTC)).timestamp())
        request_nonce = nonce or token_urlsafe(24)
        content_digest = f"sha256:{sha256(body).hexdigest()}"
        signature = self._signature(
            method=method,
            path=path,
            tenant_id=str(tenant_id),
            worker_identity=worker_identity,
            timestamp=str(timestamp),
            nonce=request_nonce,
            content_digest=content_digest,
            permit=permit,
        )
        return {
            AUTHORIZATION_HEADER: f"{AUTHORIZATION_SCHEME} {_b64url(signature)}",
            PERMIT_HEADER: permit,
            TENANT_HEADER: str(tenant_id),
            WORKER_HEADER: worker_identity,
            TIMESTAMP_HEADER: str(timestamp),
            NONCE_HEADER: request_nonce,
            CONTENT_DIGEST_HEADER: content_digest,
        }

    def verify_headers(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
        now: datetime,
    ) -> BrowserRuntimeRequestIdentity:
        try:
            authorization = headers[AUTHORIZATION_HEADER]
            permit = headers[PERMIT_HEADER]
            tenant = headers[TENANT_HEADER]
            worker = headers[WORKER_HEADER]
            timestamp = headers[TIMESTAMP_HEADER]
            nonce = headers[NONCE_HEADER]
            content_digest = headers[CONTENT_DIGEST_HEADER]
            scheme, supplied_token = authorization.split(" ", 1)
            supplied_signature = _b64url_decode(supplied_token)
            tenant_id = UUID(tenant)
            request_time = datetime.fromtimestamp(int(timestamp), tz=UTC)
        except (Base64Error, KeyError, OverflowError, ValueError) as error:
            raise BrowserRuntimeAuthenticationError(
                "browser runtime request authentication failed"
            ) from error
        if (
            scheme != AUTHORIZATION_SCHEME
            or abs(now - request_time) > self._maximum_clock_skew
            or len(worker) < 3
            or len(worker) > 160
            or len(nonce) < 20
            or len(nonce) > 200
            or content_digest != f"sha256:{sha256(body).hexdigest()}"
        ):
            raise BrowserRuntimeAuthenticationError(
                "browser runtime request authentication failed"
            )
        expected_signature = self._signature(
            method=method,
            path=path,
            tenant_id=tenant,
            worker_identity=worker,
            timestamp=timestamp,
            nonce=nonce,
            content_digest=content_digest,
            permit=permit,
        )
        if not compare_digest(supplied_signature, expected_signature):
            raise BrowserRuntimeAuthenticationError(
                "browser runtime request authentication failed"
            )
        return BrowserRuntimeRequestIdentity(
            tenant_id=tenant_id,
            worker_identity=worker,
            permit=permit,
        )

    def _signature(
        self,
        *,
        method: str,
        path: str,
        tenant_id: str,
        worker_identity: str,
        timestamp: str,
        nonce: str,
        content_digest: str,
        permit: str,
    ) -> bytes:
        canonical = "\n".join(
            (
                "ATLAS-RUNTIME-HMAC-V1",
                method.upper(),
                path,
                tenant_id,
                worker_identity,
                timestamp,
                nonce,
                content_digest,
                f"sha256:{sha256(permit.encode()).hexdigest()}",
            )
        ).encode()
        return new_hmac(self._key, canonical, sha256).digest()


def _decode_standard_key(encoded_key: str, label: str) -> bytes:
    try:
        key = b64decode(encoded_key, validate=True)
    except (Base64Error, ValueError) as error:
        raise ValueError(f"{label} key must be valid base64") from error
    if len(key) < 32:
        raise ValueError(f"{label} key must decode to at least 32 bytes")
    return key


def _b64url(value: bytes) -> str:
    return urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
