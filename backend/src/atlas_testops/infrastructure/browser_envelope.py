"""AES-GCM envelope for database-derived BrowserContext restore metadata."""

from base64 import b64decode, urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from json import dumps
from secrets import token_bytes

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from atlas_testops.application.ports.browser_runtime import BrowserContextEnvelopeCodec
from atlas_testops.core.contracts import utc_now
from atlas_testops.domain.runtime import (
    BrowserContextRestoreDescriptor,
    BrowserContextRestoreEnvelope,
    ExecutionContract,
)


class BrowserContextEnvelopeError(RuntimeError):
    """The encrypted restore descriptor failed authentication or scope checks."""


class AesGcmBrowserContextEnvelopeCodec(BrowserContextEnvelopeCodec):
    """Encrypt worker-only SessionArtifact metadata with contract-bound AAD."""

    def __init__(self, key: bytes, *, key_version: str) -> None:
        if len(key) != 32:
            raise ValueError("browser context envelope key must contain exactly 32 bytes")
        normalized_version = key_version.strip()
        if not normalized_version or len(normalized_version) > 100:
            raise ValueError("browser context envelope key version is invalid")
        self._cipher = AESGCM(bytes(key))
        self._key_version = normalized_version

    @classmethod
    def from_base64_key(
        cls,
        encoded_key: str,
        *,
        key_version: str,
    ) -> AesGcmBrowserContextEnvelopeCodec:
        try:
            key = b64decode(encoded_key, validate=True)
        except (Base64Error, ValueError) as error:
            raise ValueError("browser context envelope key must be valid base64") from error
        return cls(key, key_version=key_version)

    def seal(
        self,
        descriptor: BrowserContextRestoreDescriptor,
        *,
        contract: ExecutionContract,
    ) -> BrowserContextRestoreEnvelope:
        self._validate_descriptor_scope(descriptor, contract)
        nonce = token_bytes(12)
        plaintext = bytearray(descriptor.model_dump_json(by_alias=True).encode())
        try:
            ciphertext = self._cipher.encrypt(
                nonce,
                bytes(plaintext),
                self._associated_data(
                    contract=contract,
                    actor_slot=descriptor.actor_slot,
                    browser_context_ref=descriptor.browser_context_ref,
                    expires_at=descriptor.expires_at.isoformat(),
                ),
            )
        finally:
            plaintext[:] = b"\x00" * len(plaintext)
        return BrowserContextRestoreEnvelope(
            actor_slot=descriptor.actor_slot,
            browser_context_ref=descriptor.browser_context_ref,
            key_version=self._key_version,
            nonce=urlsafe_b64encode(nonce).decode("ascii"),
            ciphertext=urlsafe_b64encode(ciphertext).decode("ascii"),
            expires_at=descriptor.expires_at,
        )

    def open(
        self,
        envelope: BrowserContextRestoreEnvelope,
        *,
        contract: ExecutionContract,
    ) -> BrowserContextRestoreDescriptor:
        if envelope.key_version != self._key_version:
            raise BrowserContextEnvelopeError("browser context envelope key is stale")
        plaintext: bytearray | None = None
        try:
            nonce = urlsafe_b64decode(envelope.nonce.encode("ascii"))
            ciphertext = urlsafe_b64decode(envelope.ciphertext.encode("ascii"))
            plaintext = bytearray(
                self._cipher.decrypt(
                    nonce,
                    ciphertext,
                    self._associated_data(
                        contract=contract,
                        actor_slot=envelope.actor_slot,
                        browser_context_ref=envelope.browser_context_ref,
                        expires_at=envelope.expires_at.isoformat(),
                    ),
                )
            )
            descriptor = BrowserContextRestoreDescriptor.model_validate_json(bytes(plaintext))
        except (Base64Error, InvalidTag, ValidationError, ValueError) as error:
            raise BrowserContextEnvelopeError(
                "browser context restore envelope authentication failed"
            ) from error
        finally:
            if plaintext is not None:
                plaintext[:] = b"\x00" * len(plaintext)
        if (
            descriptor.actor_slot != envelope.actor_slot
            or descriptor.browser_context_ref != envelope.browser_context_ref
            or descriptor.expires_at != envelope.expires_at
        ):
            raise BrowserContextEnvelopeError(
                "browser context restore envelope metadata is inconsistent"
            )
        self._validate_descriptor_scope(descriptor, contract)
        return descriptor

    @staticmethod
    def _associated_data(
        *,
        contract: ExecutionContract,
        actor_slot: str,
        browser_context_ref: str,
        expires_at: str,
    ) -> bytes:
        return dumps(
            {
                "actorSlot": actor_slot,
                "browserContextRef": browser_context_ref,
                "executionContractDigest": contract.content_digest,
                "executionContractId": str(contract.id),
                "expiresAt": expires_at,
                "workerIdentity": contract.worker_identity,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    @staticmethod
    def _validate_descriptor_scope(
        descriptor: BrowserContextRestoreDescriptor,
        contract: ExecutionContract,
    ) -> None:
        actors = {item.actor_slot: item for item in contract.actors}
        actor = actors.get(descriptor.actor_slot)
        if actor is None or (
            descriptor.tenant_id != contract.tenant_id
            or descriptor.project_id != contract.project_id
            or descriptor.environment_id != contract.environment_id
            or descriptor.lease_id != actor.account_lease_id
            or descriptor.lease_fence != actor.fencing_token
            or descriptor.browser_context_ref != actor.browser_context_ref
            or descriptor.expires_at > contract.execution_deadline
            or utc_now() >= descriptor.expires_at
        ):
            raise BrowserContextEnvelopeError(
                "browser context restore descriptor does not match the contract"
            )
