"""Deterministic local fixture provider with no network or secret access."""

from __future__ import annotations

import hashlib

from pydantic import JsonValue

from atlas_testops.application.ports.fixture_operations import (
    FixtureOperationContext,
    FixtureOperationInvocation,
    FixtureOperationSpec,
)
from atlas_testops.domain.fixture import FixtureOperationResult

_OPERATION_KEYS = (
    "customer.create",
    "customer.delete",
    "customer.lookup",
    "customer.verify",
    "visit.create",
    "visit.delete",
    "visit.lookup",
    "visit.verify",
)


class MockFixtureOperationProvider:
    """Return schema-shaped values derived from the logical idempotency key."""

    def operation_specs(self) -> tuple[FixtureOperationSpec, ...]:
        capabilities = frozenset(_OPERATION_KEYS)
        return tuple(
            FixtureOperationSpec(
                operation_key=operation_key,
                operation_version="1.0.0",
                capabilities=capabilities,
            )
            for operation_key in _OPERATION_KEYS
        )

    async def execute(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureOperationResult:
        seed = hashlib.sha256(
            f"{context.idempotency_key}:{invocation.operation.operation_key}".encode()
        ).hexdigest()
        outputs = {
            key: _value_for_schema(schema, seed=seed, key=key)
            for key, schema in sorted(invocation.expected_outputs.items())
        }
        return FixtureOperationResult(
            outputs=outputs,
            provider_request_id=f"mock-{seed[:24]}",
        )


def _value_for_schema(
    schema: dict[str, JsonValue],
    *,
    seed: str,
    key: str,
) -> JsonValue:
    """Build a small deterministic value for common JSON Schema primitives."""

    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    value_type = schema.get("type")
    if isinstance(value_type, list):
        value_type = next((item for item in value_type if item != "null"), "null")
    if value_type == "boolean":
        return True
    if value_type == "integer":
        return int(seed[:8], 16) % 1_000_000
    if value_type == "number":
        return (int(seed[:8], 16) % 1_000_000) / 100
    if value_type == "array":
        return []
    if value_type == "object":
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        required = schema.get("required")
        required_keys = required if isinstance(required, list) else list(properties)
        return {
            str(child_key): _value_for_schema(
                child_schema,
                seed=seed,
                key=f"{key}.{child_key}",
            )
            for child_key, child_schema in properties.items()
            if child_key in required_keys and isinstance(child_schema, dict)
        }
    if value_type == "null":
        return None
    return f"mock-{key}-{seed[:16]}"
