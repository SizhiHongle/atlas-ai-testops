"""Trusted execution port for deployment-registered fixture operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue

from atlas_testops.domain.fixture import (
    ConnectorOperationRef,
    FixtureFailureCategory,
    FixtureOperationResult,
    FixtureReconcileResult,
)


@dataclass(frozen=True, slots=True)
class FixtureOperationContext:
    """Worker-owned context that is never serialized into Temporal history."""

    tenant_id: UUID
    project_id: UUID
    environment_id: UUID
    fixture_run_id: UUID
    data_node_run_id: UUID
    connector_installation_id: UUID
    account_handle: str
    configuration_ref: str
    idempotency_key: str
    request_id: str
    deadline: datetime


@dataclass(frozen=True, slots=True)
class FixtureOperationInvocation:
    """Structured invocation accepted by a reviewed provider implementation."""

    operation: ConnectorOperationRef
    inputs: dict[str, JsonValue]
    expected_outputs: dict[str, dict[str, JsonValue]]


@dataclass(frozen=True, slots=True)
class FixtureOperationSpec:
    """One exact operation/version and its deployment-owned capabilities."""

    operation_key: str
    operation_version: str
    capabilities: frozenset[str]


class FixtureOperationProvider(Protocol):
    """Provider boundary implemented only by trusted deployment code."""

    def operation_specs(self) -> tuple[FixtureOperationSpec, ...]: ...

    async def execute(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureOperationResult: ...

    async def reconcile(
        self,
        *,
        context: FixtureOperationContext,
        invocation: FixtureOperationInvocation,
    ) -> FixtureReconcileResult: ...


class FixtureOperationError(RuntimeError):
    """Safe provider failure without raw credentials or response bodies."""

    def __init__(
        self,
        *,
        category: FixtureFailureCategory,
        code: str,
        safe_detail: str,
        retryable: bool,
        outcome_uncertain: bool = False,
        retry_after_seconds: float | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(safe_detail)
        self.category = category
        self.code = code
        self.safe_detail = safe_detail
        self.retryable = retryable
        self.outcome_uncertain = outcome_uncertain
        self.retry_after_seconds = retry_after_seconds
        self.provider_request_id = provider_request_id
