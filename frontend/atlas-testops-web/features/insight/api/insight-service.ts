import { apiClient } from "@/shared/api/client";
import { toApiError } from "@/shared/api/problem";

import { mapInsightBrief } from "../model/insight-mapper";
import type {
  InsightBriefViewModel,
  InsightSnapshotDto,
  RequestInsightSnapshotCommand
} from "../model/insight";

function requireData<T>(data: T | undefined, message: string): T {
  if (!data) throw new Error(message);
  return data;
}

export async function readInsightBrief(
  projectId: string,
  windowDays: 7 | 30 | 90
): Promise<InsightBriefViewModel> {
  const response = await apiClient.GET(
    "/v1/projects/{projectId}/insights/brief",
    {
      params: {
        path: { projectId },
        query: { windowDays, asOf: null }
      }
    }
  );
  if (response.error) {
    throw toApiError(response.error, "无法读取 Insight Brief。");
  }
  return mapInsightBrief(
    requireData(response.data, "Atlas API 未返回 Insight Brief。")
  );
}

export async function pinInsightSnapshot(
  projectId: string,
  command: RequestInsightSnapshotCommand
): Promise<InsightSnapshotDto> {
  const response = await apiClient.POST(
    "/v1/projects/{projectId}/insight-snapshots",
    {
      params: {
        path: { projectId },
        header: { "Idempotency-Key": command.clientMutationId }
      },
      body: command
    }
  );
  if (response.error) {
    throw toApiError(response.error, "无法固定 InsightSnapshot。");
  }
  return requireData(response.data, "Atlas API 未返回 InsightSnapshot。");
}
