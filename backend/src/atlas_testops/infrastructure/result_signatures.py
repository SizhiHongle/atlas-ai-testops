"""Ed25519 verification for bounded AttemptSeal signing content."""

from __future__ import annotations

import base64
from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from atlas_testops.domain.result import AttemptSeal, attempt_seal_signing_bytes


class AttemptSealSignatureError(ValueError):
    """Raised when an AttemptSeal signature cannot be trusted."""


class AttemptSealVerifier:
    """Verify AttemptSeal signatures against an injected public key ring."""

    def __init__(self, public_keys: Mapping[str, bytes]) -> None:
        if not public_keys:
            raise ValueError("AttemptSeal public key ring must not be empty")
        keys: dict[str, Ed25519PublicKey] = {}
        for kid, raw_key in public_keys.items():
            try:
                keys[kid] = Ed25519PublicKey.from_public_bytes(raw_key)
            except ValueError as error:
                raise ValueError("AttemptSeal public key must contain 32 bytes") from error
        self._keys = keys

    def verify(self, seal: AttemptSeal) -> None:
        """Fail closed unless the exact canonical Seal content verifies."""

        public_key = self._keys.get(seal.signature.kid)
        if public_key is None:
            raise AttemptSealSignatureError("AttemptSeal signing key is unknown")
        encoded = seal.signature_value.removeprefix("base64url:")
        try:
            signature = base64.urlsafe_b64decode(encoded + "==")
        except ValueError as error:
            raise AttemptSealSignatureError("AttemptSeal signature is malformed") from error
        try:
            public_key.verify(signature, attempt_seal_signing_bytes(seal))
        except InvalidSignature as error:
            raise AttemptSealSignatureError("AttemptSeal signature is invalid") from error


def encode_attempt_seal_signature(signature: bytes) -> str:
    """Encode one raw Ed25519 signature using the frozen wire format."""

    if len(signature) != 64:
        raise ValueError("Ed25519 signature must contain 64 bytes")
    encoded = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"base64url:{encoded}"
