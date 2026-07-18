"""AttemptSeal contract, digest, and signature tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from atlas_testops.domain.result import (
    AttemptEventChain,
    AttemptSeal,
    AttemptSealContent,
    AttemptSealSignature,
    DataHygiene,
    EvidenceCompleteness,
    EvidenceIntegrity,
    ExecutionInfluence,
    OutcomeClass,
    Stability,
    Verdict,
    attempt_seal_content_hash,
    attempt_seal_signing_bytes,
)
from atlas_testops.infrastructure.result_signatures import (
    AttemptSealSignatureError,
    AttemptSealVerifier,
    encode_attempt_seal_signature,
)

_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
_PUBLIC_KEY = _PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)
_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


def _content(**overrides: object) -> AttemptSealContent:
    task_run_id = UUID("00000000-0000-7000-8000-000000000003")
    values: dict[str, object] = {
        "seal_id": UUID("00000000-0000-7000-8000-000000000001"),
        "tenant_id": UUID("00000000-0000-7000-8000-000000000002"),
        "project_id": UUID("00000000-0000-7000-8000-000000000004"),
        "task_run_id": task_run_id,
        "execution_unit_id": UUID("00000000-0000-7000-8000-000000000005"),
        "unit_attempt_id": UUID("00000000-0000-7000-8000-000000000006"),
        "manifest_id": task_run_id,
        "manifest_hash": _DIGEST_A,
        "unit_key": _DIGEST_B,
        "execution_ticket_id": UUID("00000000-0000-7000-8000-000000000007"),
        "execution_ticket_digest": _DIGEST_C,
        "oracle_verdict": Verdict.PASSED,
        "outcome_class": OutcomeClass.BUSINESS,
        "closure_reason": "REQUIRED_ORACLES_PASSED",
        "data_hygiene": DataHygiene.CLEANED,
        "evidence_completeness": EvidenceCompleteness.COMPLETE,
        "evidence_integrity": EvidenceIntegrity.VERIFIED,
        "execution_influence": ExecutionInfluence.AUTONOMOUS,
        "stability": Stability.UNKNOWN,
        "oracle_results_hash": _DIGEST_A,
        "artifact_manifest_hash": _DIGEST_B,
        "event_chain": AttemptEventChain(head=_DIGEST_C, event_count=42),
        "evidence_policy_digest": _DIGEST_A,
        "runtime_digest": _DIGEST_B,
        "sealed_at": datetime(2026, 7, 18, 8, 30, tzinfo=UTC),
        "signature": AttemptSealSignature(kid="atlas-seal-k3"),
    }
    values.update(overrides)
    return AttemptSealContent.model_validate(values)


def _seal(**overrides: object) -> AttemptSeal:
    content = _content(**overrides)
    signature = _PRIVATE_KEY.sign(attempt_seal_signing_bytes(content))
    return AttemptSeal(
        **content.model_dump(),
        signature_value=encode_attempt_seal_signature(signature),
        content_hash=attempt_seal_content_hash(content),
    )


def test_attempt_seal_has_stable_canonical_hash_and_valid_signature() -> None:
    seal = _seal()

    assert attempt_seal_content_hash(seal) == (
        "sha256:e8d050ef39f07bad81e3edb8e33f892dfeb1094789f6182e509d74c3e5191cd3"
    )
    AttemptSealVerifier({"atlas-seal-k3": _PUBLIC_KEY}).verify(seal)


def test_attempt_seal_rejects_pending_and_untrusted_pass() -> None:
    with pytest.raises(ValidationError, match="provisional PENDING"):
        _content(oracle_verdict=Verdict.PENDING)

    with pytest.raises(ValidationError, match="complete and verified"):
        _content(evidence_integrity=EvidenceIntegrity.INVALID)


def test_attempt_seal_signature_fails_for_tampered_content_or_unknown_key() -> None:
    seal = _seal()
    tampered = seal.model_copy(update={"runtime_digest": _DIGEST_C})
    verifier = AttemptSealVerifier({"atlas-seal-k3": _PUBLIC_KEY})

    with pytest.raises(AttemptSealSignatureError, match="invalid"):
        verifier.verify(tampered)
    with pytest.raises(AttemptSealSignatureError, match="unknown"):
        AttemptSealVerifier({"another-key": _PUBLIC_KEY}).verify(seal)
