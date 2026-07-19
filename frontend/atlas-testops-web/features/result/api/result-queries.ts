"use client";

import {
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";

import {
  evaluateTaskGate,
  readFailureClusters,
  readTaskResult,
  reviseFailureClassification
} from "./result-service";
import type {
  RequestFailureClassificationRevisionCommand,
  RequestTaskGateEvaluationCommand
} from "../model/result";

export const resultQueryKeys = {
  task: (runId: string) => ["result", "task", runId] as const,
  clusters: (snapshotId: string, cursor?: string | null) =>
    cursor === undefined
      ? (["result", "clusters", snapshotId] as const)
      : (["result", "clusters", snapshotId, cursor ?? "first"] as const)
};

export function useTaskResultQuery(runId: string | null) {
  return useQuery({
    queryKey: runId
      ? resultQueryKeys.task(runId)
      : (["result", "task", "none"] as const),
    queryFn: () => readTaskResult(runId!),
    enabled: Boolean(runId)
  });
}

export function useFailureClustersQuery(
  snapshotId: string | null,
  cursor: string | null
) {
  return useQuery({
    queryKey: snapshotId
      ? resultQueryKeys.clusters(snapshotId, cursor)
      : (["result", "clusters", "none"] as const),
    queryFn: () => readFailureClusters(snapshotId!, cursor),
    enabled: Boolean(snapshotId)
  });
}

export function useReviseClassificationMutation(snapshotId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      classificationId,
      command
    }: {
      classificationId: string;
      command: RequestFailureClassificationRevisionCommand;
    }) => reviseFailureClassification(classificationId, command),
    onSuccess: async () => {
      if (!snapshotId) return;
      await queryClient.invalidateQueries({
        queryKey: resultQueryKeys.clusters(snapshotId)
      });
    }
  });
}

export function useEvaluateTaskGateMutation(runId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (command: RequestTaskGateEvaluationCommand) =>
      evaluateTaskGate(command),
    onSuccess: async () => {
      if (!runId) return;
      await queryClient.invalidateQueries({
        queryKey: resultQueryKeys.task(runId)
      });
    }
  });
}
