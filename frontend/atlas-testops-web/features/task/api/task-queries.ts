"use client";

import {
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";

import {
  createTaskPlan,
  readExecutionUnits,
  readTaskPlans,
  readTaskPlanVersions,
  readTaskRuns,
  requestTaskRunCommand,
  startTaskPlanVersionRun
} from "./task-service";
import type { CommandKind, TaskCommand } from "./task-service";
import type {
  CreateTaskPlanCommand,
  StartTaskPlanVersionRunCommand
} from "../model/task";

export const taskQueryKeys = {
  plans: (projectId: string) => ["task", "plans", projectId] as const,
  versions: (taskPlanId: string) =>
    ["task", "versions", taskPlanId] as const,
  runs: (projectId: string) => ["task", "runs", projectId] as const,
  units: (runId: string, afterOrdinal?: number) =>
    afterOrdinal === undefined
      ? (["task", "units", runId] as const)
      : (["task", "units", runId, afterOrdinal] as const)
};

export function useTaskPlansQuery(projectId: string) {
  return useQuery({
    queryKey: taskQueryKeys.plans(projectId),
    queryFn: () => readTaskPlans(projectId)
  });
}

export function useTaskPlanVersionsQuery(taskPlanId: string | null) {
  return useQuery({
    queryKey: taskPlanId
      ? taskQueryKeys.versions(taskPlanId)
      : (["task", "versions", "none"] as const),
    queryFn: () => readTaskPlanVersions(taskPlanId!),
    enabled: Boolean(taskPlanId)
  });
}

export function useTaskRunsQuery(projectId: string) {
  return useQuery({
    queryKey: taskQueryKeys.runs(projectId),
    queryFn: () => readTaskRuns(projectId),
    refetchInterval: (query) => {
      const runs = query.state.data;
      return runs?.some((run) => run.lifecycle !== "CLOSED") ? 5_000 : false;
    }
  });
}

export function useExecutionUnitsQuery(
  runId: string | null,
  afterOrdinal = 0
) {
  return useQuery({
    queryKey: runId
      ? taskQueryKeys.units(runId, afterOrdinal)
      : (["task", "units", "none"] as const),
    queryFn: () => readExecutionUnits(runId!, afterOrdinal),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const units = query.state.data;
      return units?.items.some((unit) => unit.lifecycle !== "CLOSED")
        ? 5_000
        : false;
    }
  });
}

export function useCreateTaskPlanMutation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (command: CreateTaskPlanCommand) =>
      createTaskPlan(projectId, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: taskQueryKeys.plans(projectId)
      });
    }
  });
}

export function useStartTaskRunMutation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      taskPlanVersionId,
      command
    }: {
      taskPlanVersionId: string;
      command: StartTaskPlanVersionRunCommand;
    }) => startTaskPlanVersionRun(taskPlanVersionId, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: taskQueryKeys.runs(projectId)
      });
    }
  });
}

export function useTaskRunCommandMutation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      runId,
      revision,
      kind,
      command
    }: {
      runId: string;
      revision: number;
      kind: CommandKind;
      command: TaskCommand;
    }) => requestTaskRunCommand(runId, revision, kind, command),
    onSuccess: async (_command, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: taskQueryKeys.runs(projectId)
        }),
        queryClient.invalidateQueries({
          queryKey: taskQueryKeys.units(variables.runId)
        })
      ]);
    }
  });
}
