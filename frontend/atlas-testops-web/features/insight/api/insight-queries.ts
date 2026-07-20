"use client";

import {
  useMutation,
  useQuery
} from "@tanstack/react-query";

import {
  pinInsightSnapshot,
  readInsightBrief,
  readInsightSnapshot
} from "./insight-service";
import type { RequestInsightSnapshotCommand } from "../model/insight";

export const insightQueryKeys = {
  brief: (projectId: string, windowDays: 7 | 30 | 90) =>
    ["insight", "brief", projectId, windowDays] as const,
  snapshot: (snapshotId: string) =>
    ["insight", "snapshot", snapshotId] as const
};

export function useInsightBriefQuery(
  projectId: string,
  windowDays: 7 | 30 | 90,
  enabled = true
) {
  return useQuery({
    queryKey: insightQueryKeys.brief(projectId, windowDays),
    queryFn: () => readInsightBrief(projectId, windowDays),
    enabled
  });
}

export function useInsightSnapshotQuery(snapshotId: string | null) {
  return useQuery({
    queryKey: insightQueryKeys.snapshot(snapshotId ?? "none"),
    queryFn: () => readInsightSnapshot(snapshotId!),
    enabled: Boolean(snapshotId)
  });
}

export function usePinInsightSnapshotMutation(projectId: string) {
  return useMutation({
    mutationFn: (command: RequestInsightSnapshotCommand) =>
      pinInsightSnapshot(projectId, command)
  });
}
