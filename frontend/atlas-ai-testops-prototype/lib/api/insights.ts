"use client";

import useSWR from "swr";

import { apiClient } from "./client";
import { ApiProblemError, isProblemDetails } from "./problem";
import type { components } from "./schema";

export type InsightBrief = components["schemas"]["InsightBrief"];

function responseError(error: unknown): Error {
  if (isProblemDetails(error)) {
    return new ApiProblemError(error);
  }
  return new Error("Atlas Insight Center 返回了无法识别的错误响应。");
}

async function getInsightBrief(
  projectId: string,
  windowDays: 7 | 30 | 90
): Promise<InsightBrief> {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/insights/brief",
    {
      params: {
        path: { projectId },
        query: { windowDays, asOf: null }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 Insight Brief。");
  return data;
}

export function useInsightBrief(
  projectId: string | null,
  enabled: boolean,
  windowDays: 7 | 30 | 90 = 30
) {
  return useSWR(
    enabled && projectId
      ? (["insight-brief", projectId, windowDays] as const)
      : null,
    ([, currentProjectId, currentWindowDays]) =>
      getInsightBrief(currentProjectId, currentWindowDays),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}
