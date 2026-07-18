"use client";

import useSWR from "swr";

import { apiClient } from "./client";
import { ApiProblemError, isProblemDetails } from "./problem";
import type { components } from "./schema";

export type ExecutionUnit = components["schemas"]["ExecutionUnit"];
export type UnitAttempt = components["schemas"]["UnitAttempt"];
export type UnitAttemptLiveSnapshot =
  components["schemas"]["UnitAttemptLiveSnapshot"];
export type LiveControlCommand =
  components["schemas"]["LiveControlCommand"];
export type LiveSessionState = components["schemas"]["LiveSessionState"];

function responseError(error: unknown): Error {
  if (isProblemDetails(error)) {
    return new ApiProblemError(error);
  }
  return new Error("Atlas Live Control 返回了无法识别的错误响应。");
}

async function getExecutionUnits(taskRunId: string): Promise<ExecutionUnit[]> {
  const { data, error } = await apiClient.GET(
    "/v1/task-runs/{runId}/units",
    {
      params: {
        path: { runId: taskRunId },
        query: { afterOrdinal: 0, limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 ExecutionUnit 列表。");
  return data.items;
}

async function getUnitAttempts(
  taskRunId: string,
  executionUnitId: string
): Promise<UnitAttempt[]> {
  const { data, error } = await apiClient.GET(
    "/v1/task-runs/{runId}/units/{unitId}/attempts",
    {
      params: {
        path: { runId: taskRunId, unitId: executionUnitId },
        query: { afterAttemptNumber: 0, limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 UnitAttempt 列表。");
  return data.items;
}

async function getLiveSnapshot(
  unitAttemptId: string
): Promise<UnitAttemptLiveSnapshot | null> {
  const { data, error } = await apiClient.GET(
    "/v1/unit-attempts/{attemptId}/snapshot",
    {
      params: { path: { attemptId: unitAttemptId } }
    }
  );
  if (error) {
    if (isProblemDetails(error) && error.status === 404) return null;
    throw responseError(error);
  }
  return data ?? null;
}

export function useExecutionUnits(
  taskRunId: string | null,
  enabled: boolean
) {
  return useSWR(
    enabled && taskRunId
      ? (["task-live-units", taskRunId] as const)
      : null,
    ([, currentTaskRunId]) => getExecutionUnits(currentTaskRunId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useUnitAttempts(
  taskRunId: string | null,
  executionUnitId: string | null,
  enabled: boolean
) {
  return useSWR(
    enabled && taskRunId && executionUnitId
      ? (["task-live-attempts", taskRunId, executionUnitId] as const)
      : null,
    ([, currentTaskRunId, currentExecutionUnitId]) =>
      getUnitAttempts(currentTaskRunId, currentExecutionUnitId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useUnitAttemptLiveSnapshot(
  unitAttemptId: string | null,
  enabled: boolean
) {
  return useSWR(
    enabled && unitAttemptId
      ? (["unit-attempt-live-snapshot", unitAttemptId] as const)
      : null,
    ([, currentUnitAttemptId]) => getLiveSnapshot(currentUnitAttemptId),
    {
      refreshInterval: 2000,
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export async function requestLiveTakeover(
  snapshot: UnitAttemptLiveSnapshot,
  reason: string
): Promise<LiveControlCommand> {
  const mutationId = `takeover-${globalThis.crypto.randomUUID()}`;
  const { data, error } = await apiClient.POST(
    "/v1/unit-attempts/{attemptId}/takeover",
    {
      params: {
        path: { attemptId: snapshot.session.unitAttemptId },
        header: {
          "If-Match": `"control-epoch-${snapshot.session.controlEpoch}"`,
          "Idempotency-Key": mutationId
        }
      },
      body: { reason, requestedTtlSec: 300 }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 Takeover Command。");
  return data;
}

export async function requestLiveReturn(
  snapshot: UnitAttemptLiveSnapshot,
  reason: string
): Promise<LiveControlCommand> {
  const mutationId = `return-${globalThis.crypto.randomUUID()}`;
  const { data, error } = await apiClient.POST(
    "/v1/unit-attempts/{attemptId}/return",
    {
      params: {
        path: { attemptId: snapshot.session.unitAttemptId },
        header: {
          "If-Match": `"control-epoch-${snapshot.session.controlEpoch}"`,
          "Idempotency-Key": mutationId
        }
      },
      body: { reason, requestedTtlSec: null }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 Return Command。");
  return data;
}
