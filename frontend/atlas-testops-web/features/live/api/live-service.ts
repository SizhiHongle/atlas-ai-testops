import { apiClient } from "@/shared/api/client";
import {
  isApiProblem,
  toApiError
} from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";

import {
  mapLiveSnapshot,
  mapUnitAttempt
} from "../model/live-mapper";
import type {
  LiveControlCommandDto,
  LiveControlKind,
  LiveSnapshotViewModel,
  RequestLiveControlCommand,
  UnitAttemptPageDto,
  UnitAttemptViewModel
} from "../model/live";

function requireData<T>(data: T | undefined, message: string): T {
  if (!data) throw new Error(message);
  return data;
}

export async function readUnitAttempts(
  runId: string,
  unitId: string
): Promise<UnitAttemptViewModel[]> {
  const response = await apiClient.GET(
    "/v1/task-runs/{runId}/units/{unitId}/attempts",
    {
      params: {
        path: { runId, unitId },
        query: { afterAttemptNumber: 0, limit: 100 }
      }
    }
  );
  if (response.error) {
    throw toApiError(response.error, "无法读取 UnitAttempt。");
  }
  const page: UnitAttemptPageDto = requireData(
    response.data,
    "Atlas API 未返回 UnitAttempt。"
  );
  return page.items
    .map(mapUnitAttempt)
    .sort((left, right) => right.attemptNumber - left.attemptNumber);
}

export async function readLiveSnapshot(
  attemptId: string
): Promise<LiveSnapshotViewModel | null> {
  const response = await apiClient.GET(
    "/v1/unit-attempts/{attemptId}/snapshot",
    {
      params: { path: { attemptId } }
    }
  );
  if (response.error) {
    if (isApiProblem(response.error) && response.error.status === 404) {
      return null;
    }
    throw toApiError(response.error, "无法读取 LiveSession Snapshot。");
  }
  return mapLiveSnapshot(
    requireData(response.data, "Atlas API 未返回 LiveSession Snapshot。")
  );
}

export async function requestLiveControl(
  attemptId: string,
  controlEpoch: number,
  kind: LiveControlKind,
  command: RequestLiveControlCommand
): Promise<LiveControlCommandDto> {
  const clientMutationId = `${kind}-${createRequestId()}`;
  const params = {
    path: { attemptId },
    header: {
      "If-Match": `"control-epoch-${controlEpoch}"`,
      "Idempotency-Key": clientMutationId
    }
  };
  const response =
    kind === "takeover"
      ? await apiClient.POST("/v1/unit-attempts/{attemptId}/takeover", {
          params,
          body: command
        })
      : kind === "return"
        ? await apiClient.POST("/v1/unit-attempts/{attemptId}/return", {
            params,
            body: command
          })
        : kind === "pause"
          ? await apiClient.POST("/v1/unit-attempts/{attemptId}/pause", {
              params,
              body: command
            })
          : await apiClient.POST("/v1/unit-attempts/{attemptId}/resume", {
              params,
              body: command
            });

  if (response.error) {
    throw toApiError(response.error, `无法提交 Live ${kind} 命令。`);
  }
  return requireData(response.data, "Atlas API 未返回 Live Control Command。");
}
