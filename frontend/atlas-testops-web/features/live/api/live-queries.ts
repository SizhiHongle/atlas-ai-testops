"use client";

import {
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";

import {
  readLiveSnapshot,
  readUnitAttempts,
  requestLiveControl
} from "./live-service";
import type {
  LiveControlKind,
  RequestLiveControlCommand
} from "../model/live";

export const liveQueryKeys = {
  attempts: (runId: string, unitId: string) =>
    ["live", "attempts", runId, unitId] as const,
  snapshot: (attemptId: string) =>
    ["live", "snapshot", attemptId] as const
};

export function useUnitAttemptsQuery(
  runId: string | null,
  unitId: string | null
) {
  return useQuery({
    queryKey:
      runId && unitId
        ? liveQueryKeys.attempts(runId, unitId)
        : (["live", "attempts", "none"] as const),
    queryFn: () => readUnitAttempts(runId!, unitId!),
    enabled: Boolean(runId && unitId)
  });
}

export function useLiveSnapshotQuery(attemptId: string | null) {
  return useQuery({
    queryKey: attemptId
      ? liveQueryKeys.snapshot(attemptId)
      : (["live", "snapshot", "none"] as const),
    queryFn: () => readLiveSnapshot(attemptId!),
    enabled: Boolean(attemptId),
    refetchInterval: 2_000
  });
}

export function useLiveControlMutation(attemptId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      controlEpoch,
      kind,
      command
    }: {
      controlEpoch: number;
      kind: LiveControlKind;
      command: RequestLiveControlCommand;
    }) => requestLiveControl(attemptId!, controlEpoch, kind, command),
    onSuccess: async () => {
      if (!attemptId) return;
      await queryClient.invalidateQueries({
        queryKey: liveQueryKeys.snapshot(attemptId)
      });
    }
  });
}
