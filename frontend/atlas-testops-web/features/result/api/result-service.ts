import { apiClient } from "@/shared/api/client";
import {
  isApiProblem,
  toApiError
} from "@/shared/api/problem";

import {
  mapFailureClusterPage,
  mapTaskResult
} from "../model/result-mapper";
import type {
  FailureClassificationRevisionDto,
  FailureClusterPageDto,
  FailureClusterPageViewModel,
  RequestFailureClassificationRevisionCommand,
  RequestTaskGateEvaluationCommand,
  TaskGateDecisionDto,
  TaskResultViewModel
} from "../model/result";

function requireData<T>(data: T | undefined, message: string): T {
  if (!data) throw new Error(message);
  return data;
}

export async function readTaskResult(
  taskRunId: string
): Promise<TaskResultViewModel | null> {
  const response = await apiClient.GET("/v1/task-runs/{runId}/result", {
    params: {
      path: { runId: taskRunId },
      query: { snapshotId: null }
    }
  });
  if (response.error) {
    if (isApiProblem(response.error) && response.error.status === 404) {
      return null;
    }
    throw toApiError(response.error, "无法读取 Task Result。");
  }
  return mapTaskResult(
    requireData(response.data, "Atlas API 未返回 Task Result。")
  );
}

export async function readFailureClusters(
  resultSnapshotId: string,
  cursor: string | null
): Promise<FailureClusterPageViewModel> {
  const response = await apiClient.GET(
    "/v1/result-snapshots/{snapshotId}/clusters",
    {
      params: {
        path: { snapshotId: resultSnapshotId },
        query: { cursor, limit: 100 }
      }
    }
  );
  if (response.error) {
    throw toApiError(response.error, "无法读取 FailureCluster。");
  }
  const page: FailureClusterPageDto = requireData(
    response.data,
    "Atlas API 未返回 FailureCluster。"
  );
  return mapFailureClusterPage(page);
}

export async function reviseFailureClassification(
  classificationId: string,
  command: RequestFailureClassificationRevisionCommand
): Promise<FailureClassificationRevisionDto> {
  const response = await apiClient.POST(
    "/v1/failure-classifications/{classificationId}/revisions",
    {
      params: {
        path: { classificationId },
        header: { "Idempotency-Key": command.clientMutationId }
      },
      body: command
    }
  );
  if (response.error) {
    throw toApiError(response.error, "无法提交 FailureClassification 复核。");
  }
  return requireData(
    response.data,
    "Atlas API 未返回 FailureClassification Revision。"
  );
}

export async function evaluateTaskGate(
  command: RequestTaskGateEvaluationCommand
): Promise<TaskGateDecisionDto> {
  const response = await apiClient.POST("/v1/task-gates/evaluations", {
    params: {
      header: { "Idempotency-Key": command.clientMutationId }
    },
    body: command
  });
  if (response.error) {
    throw toApiError(response.error, "无法评估 TaskGate。");
  }
  return requireData(response.data, "Atlas API 未返回 TaskGateDecision。");
}
