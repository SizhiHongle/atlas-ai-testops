"use client";

import useSWR from "swr";

import { apiClient } from "./client";
import { ApiProblemError, isProblemDetails } from "./problem";
import type { components } from "./schema";

export type TestCase = components["schemas"]["TestCase"];
export type TestCaseCatalogItem = components["schemas"]["TestCaseCatalogItem"];
export type TestCasePage = components["schemas"]["TestCasePage"];
export type CreateTestCase = components["schemas"]["CreateTestCase"];
export type WorkflowDraftSnapshot =
  components["schemas"]["WorkflowDraftSnapshot"];
export type WorkflowPatch = components["schemas"]["WorkflowPatch"];
export type WorkflowPatchPreview =
  components["schemas"]["WorkflowPatchPreview"];
export type LayoutPatch = components["schemas"]["LayoutPatch"];
export type DebugRun = components["schemas"]["DebugRun"];
export type DebugRunEvent = components["schemas"]["DebugRunEvent"];
export type DebugRunEventPage = components["schemas"]["DebugRunEventPage"];
export type DebugRunPage = components["schemas"]["DebugRunPage"];
export type StartDebugRun = components["schemas"]["StartDebugRun"];
export type RequestDebugRunCancel =
  components["schemas"]["RequestDebugRunCancel"];
export type CaseVersion = components["schemas"]["CaseVersion"];
export type CaseVersionPage = components["schemas"]["CaseVersionPage"];
export type PublishCaseVersion = components["schemas"]["PublishCaseVersion"];

function responseError(error: unknown): Error {
  if (isProblemDetails(error)) {
    return new ApiProblemError(error);
  }
  return new Error("Atlas TestCase API 返回了无法识别的错误响应。");
}

async function getTestCases(projectId: string): Promise<TestCasePage> {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/test-cases",
    {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 TestCase Catalog。");
  return data;
}

async function getWorkflowDraft(
  caseId: string
): Promise<WorkflowDraftSnapshot> {
  const { data, error } = await apiClient.GET(
    "/v1/test-cases/{caseId}/workflow-draft",
    {
      params: { path: { caseId } }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 WorkflowDraft。");
  return data;
}

async function getDebugRuns(caseId: string): Promise<DebugRunPage> {
  const { data, error } = await apiClient.GET(
    "/v1/test-cases/{caseId}/debug-runs",
    {
      params: {
        path: { caseId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 DebugRun 历史。");
  return data;
}

async function getDebugRun(runId: string): Promise<DebugRun> {
  const { data, error } = await apiClient.GET("/v1/debug-runs/{runId}", {
    params: { path: { runId } }
  });
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 DebugRun 快照。");
  return data;
}

async function getDebugRunEvents(
  runId: string,
  afterSeq: number
): Promise<DebugRunEventPage> {
  const { data, error } = await apiClient.GET(
    "/v1/debug-runs/{runId}/events",
    {
      params: {
        path: { runId },
        query: { afterSeq, limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 DebugRun 事件。");
  return data;
}

async function getCaseVersions(caseId: string): Promise<CaseVersionPage> {
  const { data, error } = await apiClient.GET(
    "/v1/test-cases/{caseId}/versions",
    {
      params: {
        path: { caseId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 CaseVersion 历史。");
  return data;
}

async function getCaseVersion(versionId: string): Promise<CaseVersion> {
  const { data, error } = await apiClient.GET(
    "/v1/case-versions/{versionId}",
    {
      params: { path: { versionId } }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 CaseVersion 快照。");
  return data;
}

export function useTestCases(projectId: string | null) {
  return useSWR(
    projectId ? (["test-case-catalog", projectId] as const) : null,
    ([, currentProjectId]) => getTestCases(currentProjectId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useWorkflowDraft(caseId: string | null) {
  return useSWR(
    caseId ? (["workflow-draft", caseId] as const) : null,
    ([, currentCaseId]) => getWorkflowDraft(currentCaseId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useDebugRuns(caseId: string | null) {
  return useSWR(
    caseId ? (["debug-runs", caseId] as const) : null,
    ([, currentCaseId]) => getDebugRuns(currentCaseId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useDebugRun(runId: string | null) {
  return useSWR(
    runId ? (["debug-run", runId] as const) : null,
    ([, currentRunId]) => getDebugRun(currentRunId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useDebugRunEvents(
  runId: string | null,
  afterSeq: number = 0
) {
  return useSWR(
    runId ? (["debug-run-events", runId, afterSeq] as const) : null,
    ([, currentRunId, currentAfterSeq]) =>
      getDebugRunEvents(currentRunId, currentAfterSeq),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useCaseVersions(caseId: string | null) {
  return useSWR(
    caseId ? (["case-versions", caseId] as const) : null,
    ([, currentCaseId]) => getCaseVersions(currentCaseId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export function useCaseVersion(versionId: string | null) {
  return useSWR(
    versionId ? (["case-version", versionId] as const) : null,
    ([, currentVersionId]) => getCaseVersion(currentVersionId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}

export async function createTestCase(
  projectId: string,
  command: CreateTestCase
): Promise<TestCase> {
  const { data, error } = await apiClient.POST(
    "/v1/projects/{projectId}/test-cases",
    {
      params: {
        header: {
          "Idempotency-Key": `test-case-${globalThis.crypto.randomUUID()}`
        },
        path: { projectId }
      },
      body: command
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回新建 TestCase。");
  return data;
}

export async function previewWorkflowPatch(
  caseId: string,
  patch: WorkflowPatch
): Promise<WorkflowPatchPreview> {
  const { data, error } = await apiClient.POST(
    "/v1/test-cases/{caseId}/workflow-draft/patches:validate",
    {
      params: { path: { caseId } },
      body: patch
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 WorkflowPatch 预检结果。");
  return data;
}

export async function applyWorkflowPatch(
  caseId: string,
  patch: WorkflowPatch
): Promise<WorkflowDraftSnapshot> {
  const { data, error } = await apiClient.POST(
    "/v1/test-cases/{caseId}/workflow-draft/patches:apply",
    {
      params: {
        header: {
          "Idempotency-Key": patch.clientMutationId,
          "If-Match": `"revision-${patch.baseSemanticRevision}"`
        },
        path: { caseId }
      },
      body: patch
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回更新后的 WorkflowDraft。");
  return data;
}

export async function updateWorkflowLayout(
  caseId: string,
  patch: LayoutPatch
): Promise<WorkflowDraftSnapshot> {
  const { data, error } = await apiClient.PATCH(
    "/v1/test-cases/{caseId}/workflow-draft/layout",
    {
      params: {
        header: {
          "Idempotency-Key": patch.clientMutationId,
          "If-Match": `"revision-${patch.baseLayoutRevision}"`
        },
        path: { caseId }
      },
      body: patch
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回更新布局后的 WorkflowDraft。");
  return data;
}

export async function startDebugRun(
  caseId: string,
  command: StartDebugRun,
  idempotencyKey: string = `debug-run-${globalThis.crypto.randomUUID()}`
): Promise<DebugRun> {
  const { data, error } = await apiClient.POST(
    "/v1/test-cases/{caseId}/workflow-draft/debug-runs",
    {
      params: {
        header: {
          "Idempotency-Key": idempotencyKey,
          "If-Match": `"revision-${command.baseSemanticRevision}"`
        },
        path: { caseId }
      },
      body: command
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回新建 DebugRun。");
  return data;
}

export async function cancelDebugRun(
  runId: string,
  command: RequestDebugRunCancel,
  revision: number
): Promise<DebugRun> {
  const { data, error } = await apiClient.POST(
    "/v1/debug-runs/{runId}:cancel",
    {
      params: {
        header: {
          "Idempotency-Key": command.clientMutationId,
          "If-Match": `"revision-${revision}"`
        },
        path: { runId }
      },
      body: command
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回取消后的 DebugRun。");
  return data;
}

export async function publishCaseVersion(
  caseId: string,
  command: PublishCaseVersion
): Promise<CaseVersion> {
  const { data, error } = await apiClient.POST(
    "/v1/test-cases/{caseId}:publish",
    {
      params: {
        header: {
          "Idempotency-Key": command.clientMutationId,
          "If-Match": `"revision-${command.baseSemanticRevision}"`
        },
        path: { caseId }
      },
      body: command
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回已发布 CaseVersion。");
  return data;
}
