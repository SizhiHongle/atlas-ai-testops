"use client";

import {
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";
import {
  useEffect,
  useState
} from "react";

import { ATLAS_API_BASE_URL } from "@/shared/config/client";

import {
  readDebugEvidence,
  readDebugLiveFrame,
  readDebugLiveSnapshot,
  readDebugRun,
  readDebugRunEvents,
  readEvidenceArtifact,
  readLiveSnapshot,
  readUnitAttempts,
  requestLiveControl
} from "./live-service";
import {
  mapDebugLiveEvent,
  mapDebugLiveSnapshot
} from "../model/live-mapper";
import type {
  DebugEventWindowViewModel,
  DebugLiveEventDto,
  DebugLiveSnapshotDto,
  DebugLiveSnapshotViewModel,
  DebugLiveStreamStatus,
  EvidenceReadPurpose,
  LiveControlKind,
  RequestLiveControlCommand
} from "../model/live";

const DEBUG_LIVE_EVENT_TYPES = [
  "debug_run.requested",
  "debug_run.snapshot_outdated",
  "debug_run.cancel_requested",
  "debug_run.execution_bound",
  "debug_run.ready",
  "debug_run.started",
  "debug_run.finalizing",
  "debug_run.terminated",
  "debug_run.browser.execution.started",
  "debug_run.browser.node.started",
  "debug_run.browser.observation.captured",
  "debug_run.browser.planner.completed",
  "debug_run.browser.action.proposed",
  "debug_run.browser.policy.decided",
  "debug_run.browser.action.executed",
  "debug_run.browser.artifact.captured",
  "debug_run.browser.assertion.evaluated",
  "debug_run.browser.node.completed",
  "debug_run.browser.execution.blocked",
  "debug_run.browser.execution.completed"
] as const;

export const liveQueryKeys = {
  attempts: (runId: string, unitId: string) =>
    ["live", "attempts", runId, unitId] as const,
  snapshot: (attemptId: string) =>
    ["live", "snapshot", attemptId] as const,
  debugSnapshot: (debugRunId: string) =>
    ["live", "debug-snapshot", debugRunId] as const,
  debugRun: (debugRunId: string) =>
    ["live", "debug-run", debugRunId] as const,
  debugEvents: (debugRunId: string) =>
    ["live", "debug-events", debugRunId] as const,
  debugEvidence: (debugRunId: string) =>
    ["live", "debug-evidence", debugRunId] as const,
  debugFrame: (debugRunId: string) =>
    ["live", "debug-frame", debugRunId] as const
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

export function useDebugLiveSnapshotQuery(debugRunId: string | null) {
  return useQuery({
    queryKey: debugRunId
      ? liveQueryKeys.debugSnapshot(debugRunId)
      : (["live", "debug-snapshot", "none"] as const),
    queryFn: () => readDebugLiveSnapshot(debugRunId!),
    enabled: Boolean(debugRunId),
    refetchInterval: (query) =>
      query.state.data?.run.lifecycle === "TERMINATED" ? false : 5_000
  });
}

export function useDebugRunQuery(debugRunId: string | null) {
  return useQuery({
    queryKey: debugRunId
      ? liveQueryKeys.debugRun(debugRunId)
      : (["live", "debug-run", "none"] as const),
    queryFn: () => readDebugRun(debugRunId!),
    enabled: Boolean(debugRunId),
    staleTime: Number.POSITIVE_INFINITY
  });
}

export function useDebugRunEventsQuery(debugRunId: string | null) {
  return useQuery({
    queryKey: debugRunId
      ? liveQueryKeys.debugEvents(debugRunId)
      : (["live", "debug-events", "none"] as const),
    queryFn: () => readDebugRunEvents(debugRunId!),
    enabled: Boolean(debugRunId),
    refetchInterval: (query) =>
      query.state.data?.items.at(-1)?.lifecycle === "TERMINATED"
        ? false
        : 10_000
  });
}

export function useDebugEvidenceQuery(
  debugRunId: string | null,
  enabled: boolean
) {
  return useQuery({
    queryKey: debugRunId
      ? liveQueryKeys.debugEvidence(debugRunId)
      : (["live", "debug-evidence", "none"] as const),
    queryFn: () => readDebugEvidence(debugRunId!),
    enabled: Boolean(debugRunId && enabled),
    retry: false
  });
}

export function useDebugLiveFrameQuery(
  debugRunId: string | null,
  running: boolean
) {
  return useQuery({
    queryKey: debugRunId
      ? liveQueryKeys.debugFrame(debugRunId)
      : (["live", "debug-frame", "none"] as const),
    queryFn: () => readDebugLiveFrame(debugRunId!),
    enabled: Boolean(debugRunId),
    refetchInterval: running ? 900 : false,
    retry: false
  });
}

export function useEvidenceArtifactMutation() {
  return useMutation({
    mutationFn: ({
      debugRunId,
      artifactId,
      purpose
    }: {
      debugRunId: string;
      artifactId: string;
      purpose: EvidenceReadPurpose;
    }) => readEvidenceArtifact(debugRunId, artifactId, purpose)
  });
}

function parseEventData<T>(event: Event): T | null {
  if (!(event instanceof MessageEvent) || typeof event.data !== "string") {
    return null;
  }
  try {
    const value: unknown = JSON.parse(event.data);
    return value && typeof value === "object" ? (value as T) : null;
  } catch {
    return null;
  }
}

export function useDebugLiveStream(
  debugRunId: string | null,
  enabled: boolean
): DebugLiveStreamStatus {
  const queryClient = useQueryClient();
  const [connection, setConnection] = useState<{
    debugRunId: string | null;
    status: DebugLiveStreamStatus;
  }>({
    debugRunId: null,
    status: "connecting"
  });
  const streamEnabled = Boolean(
    debugRunId && enabled && typeof EventSource !== "undefined"
  );

  useEffect(() => {
    if (!debugRunId || !streamEnabled) return;

    const source = new EventSource(
      `${ATLAS_API_BASE_URL}/v1/debug-runs/${encodeURIComponent(
        debugRunId
      )}/events/stream`,
      { withCredentials: true }
    );

    const applySnapshot = (event: Event) => {
      const payload = parseEventData<DebugLiveSnapshotDto>(event);
      if (!payload) return;
      queryClient.setQueryData(
        liveQueryKeys.debugSnapshot(debugRunId),
        mapDebugLiveSnapshot(payload)
      );
      setConnection({ debugRunId, status: "live" });
    };

    const applyEvent = (event: Event) => {
      const payload = parseEventData<DebugLiveEventDto>(event);
      if (!payload) return;
      const mapped = mapDebugLiveEvent(payload);
      queryClient.setQueryData<DebugEventWindowViewModel>(
        liveQueryKeys.debugEvents(debugRunId),
        (current) => {
          const byId = new Map(
            (current?.items ?? []).map((item) => [item.id, item])
          );
          byId.set(mapped.id, mapped);
          const allItems = [...byId.values()].sort(
            (left, right) => left.seq - right.seq
          );
          return {
            items: allItems.slice(-500),
            truncated:
              Boolean(current?.truncated) || allItems.length > 500
          };
        }
      );
      queryClient.setQueryData<DebugLiveSnapshotViewModel>(
        liveQueryKeys.debugSnapshot(debugRunId),
        (current) =>
          current
            ? {
                ...current,
                run: {
                  ...current.run,
                  lifecycle: mapped.lifecycle,
                  outcome: mapped.outcome,
                  snapshotStatus: mapped.snapshotStatus
                },
                cursor: mapped.cursor,
                latestEvent: mapped,
                observedAt: new Date()
              }
            : current
      );
      setConnection({ debugRunId, status: "live" });
    };

    source.addEventListener("debug_run.live.snapshot", applySnapshot);
    DEBUG_LIVE_EVENT_TYPES.forEach((eventType) => {
      source.addEventListener(eventType, applyEvent);
    });
    source.onopen = () => setConnection({ debugRunId, status: "live" });
    source.onerror = () =>
      setConnection({ debugRunId, status: "reconnecting" });

    return () => {
      source.close();
      source.removeEventListener("debug_run.live.snapshot", applySnapshot);
      DEBUG_LIVE_EVENT_TYPES.forEach((eventType) => {
        source.removeEventListener(eventType, applyEvent);
      });
    };
  }, [debugRunId, queryClient, streamEnabled]);

  if (!streamEnabled) return "disabled";
  return connection.debugRunId === debugRunId
    ? connection.status
    : "connecting";
}
