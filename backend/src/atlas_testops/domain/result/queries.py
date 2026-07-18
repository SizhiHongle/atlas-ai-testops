"""Public, snapshot-explicit Result query contracts."""

from __future__ import annotations

import base64
import binascii
import json
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, ValidationError, model_validator

from atlas_testops.core.contracts import FrozenWireModel
from atlas_testops.core.errors import ApplicationError, ErrorCode
from atlas_testops.domain.case.models import DIGEST_PATTERN
from atlas_testops.domain.result.classification import (
    FailureClassificationRevision,
    FailureClusterRevision,
)
from atlas_testops.domain.result.gate import TaskGateDecision
from atlas_testops.domain.result.projections import TaskResultSnapshot

RESULT_CLUSTER_CURSOR_SCHEMA_VERSION: Literal[
    "atlas.result-cluster-cursor/0.1"
] = "atlas.result-cluster-cursor/0.1"
RESULT_CLUSTER_CURSOR_MAX_LENGTH = 1_024


class ResultSnapshotSelection(StrEnum):
    """Whether the caller selected the latest or one exact Snapshot."""

    LATEST = "LATEST"
    EXACT = "EXACT"


class TaskResultView(FrozenWireModel):
    """One explicit Task Result Snapshot and its latest bound Gate decision."""

    task_run_id: UUID
    selection: ResultSnapshotSelection
    result_snapshot: TaskResultSnapshot
    task_gate_decision: TaskGateDecision | None = None
    projection_watermark: AwareDatetime

    @model_validator(mode="after")
    def validate_scope(self) -> TaskResultView:
        """Prevent a view from combining facts from different Task scopes."""

        if self.result_snapshot.task_run_id != self.task_run_id:
            raise ValueError("TaskResultView Snapshot must belong to taskRunId")
        if self.projection_watermark != self.result_snapshot.projection_watermark:
            raise ValueError("TaskResultView watermark must come from its Snapshot")
        if self.task_gate_decision is not None and (
            self.task_gate_decision.task_run_id != self.task_run_id
            or self.task_gate_decision.result_snapshot_id != self.result_snapshot.id
            or self.task_gate_decision.result_snapshot_hash
            != self.result_snapshot.snapshot_hash
        ):
            raise ValueError("TaskResultView Gate must bind the exact Snapshot")
        return self


class FailureClusterItem(FrozenWireModel):
    """One current Cluster revision and its latest judgment as of the page fence."""

    cluster: FailureClusterRevision
    classification: FailureClassificationRevision | None = None

    @model_validator(mode="after")
    def validate_binding(self) -> FailureClusterItem:
        """Keep a Classification bound to the exact Cluster revision."""

        if self.classification is not None and (
            self.classification.failure_cluster_revision_id != self.cluster.id
            or self.classification.result_snapshot_id != self.cluster.result_snapshot_id
        ):
            raise ValueError("Classification must bind the exact Cluster revision")
        return self


class FailureClusterPage(FrozenWireModel):
    """Stable as-of page of current failure clusters for one immutable Snapshot."""

    result_snapshot_id: UUID
    as_of: AwareDatetime
    projection_watermark: AwareDatetime
    items: tuple[FailureClusterItem, ...]
    next_cursor: str | None = Field(default=None, max_length=RESULT_CLUSTER_CURSOR_MAX_LENGTH)

    @model_validator(mode="after")
    def validate_scope(self) -> FailureClusterPage:
        """Require every page item to belong to the requested Snapshot."""

        if any(
            item.cluster.result_snapshot_id != self.result_snapshot_id
            for item in self.items
        ):
            raise ValueError("FailureClusterPage items must belong to resultSnapshotId")
        return self


class ResultClusterCursor(FrozenWireModel):
    """Opaque keyset cursor bound to one Snapshot and one database as-of fence."""

    schema_version: Literal["atlas.result-cluster-cursor/0.1"] = (
        RESULT_CLUSTER_CURSOR_SCHEMA_VERSION
    )
    result_snapshot_id: UUID
    as_of: AwareDatetime
    fingerprint: str = Field(pattern=DIGEST_PATTERN)
    failure_cluster_id: UUID
    cluster_revision_id: UUID


def encode_result_cluster_cursor(cursor: ResultClusterCursor) -> str:
    """Encode a canonical, unpadded Base64URL Result Cluster cursor."""

    payload = json.dumps(
        cursor.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_result_cluster_cursor(
    value: str | None,
    *,
    expected_snapshot_id: UUID,
) -> ResultClusterCursor | None:
    """Decode an untrusted cursor and require its exact Snapshot binding."""

    if value is None:
        return None
    try:
        if not 1 <= len(value) <= RESULT_CLUSTER_CURSOR_MAX_LENGTH:
            raise ValueError("cursor length is invalid")
        if "=" in value:
            raise ValueError("cursor must be unpadded")
        padded = value + "=" * (-len(value) % 4)
        payload = base64.b64decode(padded, altchars=b"-_", validate=True)
        cursor = ResultClusterCursor.model_validate(json.loads(payload))
        if cursor.result_snapshot_id != expected_snapshot_id:
            raise ValueError("cursor belongs to another Result Snapshot")
        return cursor
    except (
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
        ValidationError,
    ):
        raise ApplicationError(
            error_code=ErrorCode.INVALID_REQUEST,
            title="Result Cursor 无效",
            detail="Cluster 分页 Cursor 已损坏或不属于当前 Result Snapshot。",
            status_code=400,
        ) from None


__all__ = [
    "RESULT_CLUSTER_CURSOR_MAX_LENGTH",
    "RESULT_CLUSTER_CURSOR_SCHEMA_VERSION",
    "FailureClusterItem",
    "FailureClusterPage",
    "ResultClusterCursor",
    "ResultSnapshotSelection",
    "TaskResultView",
    "decode_result_cluster_cursor",
    "encode_result_cluster_cursor",
]
