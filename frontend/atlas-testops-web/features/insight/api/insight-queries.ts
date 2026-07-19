"use client";

import {
  useMutation,
  useQuery
} from "@tanstack/react-query";

import {
  pinInsightSnapshot,
  readInsightBrief
} from "./insight-service";
import type { RequestInsightSnapshotCommand } from "../model/insight";

export const insightQueryKeys = {
  brief: (projectId: string, windowDays: 7 | 30 | 90) =>
    ["insight", "brief", projectId, windowDays] as const
};

export function useInsightBriefQuery(
  projectId: string,
  windowDays: 7 | 30 | 90
) {
  return useQuery({
    queryKey: insightQueryKeys.brief(projectId, windowDays),
    queryFn: () => readInsightBrief(projectId, windowDays)
  });
}

export function usePinInsightSnapshotMutation(projectId: string) {
  return useMutation({
    mutationFn: (command: RequestInsightSnapshotCommand) =>
      pinInsightSnapshot(projectId, command)
  });
}
