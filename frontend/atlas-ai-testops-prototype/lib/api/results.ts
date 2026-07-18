"use client";

import useSWR from "swr";

import { apiClient } from "./client";
import { ApiProblemError, isProblemDetails } from "./problem";
import type { components } from "./schema";

export type TaskRun = components["schemas"]["TaskRun"];
export type TaskRunPage = components["schemas"]["TaskRunPage"];
export type TaskResultView = components["schemas"]["TaskResultView"];
export type TaskResultSnapshot = components["schemas"]["TaskResultSnapshot"];
export type TaskGateDecision = components["schemas"]["TaskGateDecision"];
export type FailureClusterPage = components["schemas"]["FailureClusterPage"];
export type FailureClusterItem = components["schemas"]["FailureClusterItem"];
export type FailureClassificationRevision =
  components["schemas"]["FailureClassificationRevision"];
export type RequestFailureClassificationRevision =
  components["schemas"]["RequestFailureClassificationRevision"];
export type RequestTaskGateEvaluation =
  components["schemas"]["RequestTaskGateEvaluation"];

function responseError(error: unknown): Error {
  if (isProblemDetails(error)) {
    return new ApiProblemError(error);
  }
  return new Error("Atlas Result Center 返回了无法识别的错误响应。");
}

async function getTaskRuns(projectId: string): Promise<TaskRunPage> {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/task-runs",
    {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 TaskRun 列表。");
  return data;
}

async function getTaskResult(taskRunId: string): Promise<TaskResultView> {
  const { data, error } = await apiClient.GET(
    "/v1/task-runs/{runId}/result",
    {
      params: {
        path: { runId: taskRunId },
        query: { snapshotId: null }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 Task Result。");
  return data;
}

async function getFailureClusters(
  resultSnapshotId: string
): Promise<FailureClusterPage> {
  const { data, error } = await apiClient.GET(
    "/v1/result-snapshots/{snapshotId}/clusters",
    {
      params: {
        path: { snapshotId: resultSnapshotId },
        query: { cursor: null, limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 FailureCluster 列表。");
  return data;
}

export function useTaskRuns(projectId: string | null) {
  return useSWR(
    projectId ? (["task-runs", projectId] as const) : null,
    ([, currentProjectId]) => getTaskRuns(currentProjectId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useTaskResult(taskRunId: string | null) {
  return useSWR(
    taskRunId ? (["task-result", taskRunId] as const) : null,
    ([, currentTaskRunId]) => getTaskResult(currentTaskRunId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useFailureClusters(resultSnapshotId: string | null) {
  return useSWR(
    resultSnapshotId
      ? (["failure-clusters", resultSnapshotId] as const)
      : null,
    ([, currentSnapshotId]) => getFailureClusters(currentSnapshotId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export async function reviseFailureClassification(
  classificationId: string,
  command: RequestFailureClassificationRevision
): Promise<FailureClassificationRevision> {
  const { data, error } = await apiClient.POST(
    "/v1/failure-classifications/{classificationId}/revisions",
    {
      params: {
        path: { classificationId },
        header: { "Idempotency-Key": command.clientMutationId }
      },
      body: command
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 FailureClassification Revision。");
  return data;
}

export async function evaluateTaskGate(
  command: RequestTaskGateEvaluation
): Promise<TaskGateDecision> {
  const { data, error } = await apiClient.POST("/v1/task-gates/evaluations", {
    params: {
      header: { "Idempotency-Key": command.clientMutationId }
    },
    body: command
  });
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 TaskGateDecision。");
  return data;
}
