import { apiClient } from "@/shared/api/client";
import { createRequestId } from "@/shared/api/request-id";
import { toApiError } from "@/shared/api/problem";

import {
  mapCaseWorkspace,
  mapTestCase,
  mapWorkflowPatchPreview
} from "../model/case-mapper";
import type {
  CaseVersionDto,
  CaseWorkspaceViewModel,
  CreateTestCaseCommand,
  DebugRunDto,
  LayoutPatchCommand,
  PublishCaseVersionCommand,
  RequestDebugRunCancelCommand,
  StartDebugRunCommand,
  TestCaseCardViewModel,
  WorkflowDraftSnapshotDto,
  WorkflowPatchCommand,
  WorkflowPatchPreviewDto,
  WorkflowPatchPreviewViewModel
} from "../model/case";

function requireData<T>(data: T | undefined, message: string): T {
  if (!data) throw new Error(message);
  return data;
}

export async function readTestCases(
  projectId: string
): Promise<TestCaseCardViewModel[]> {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/test-cases",
    {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw toApiError(error, "无法读取 TestCase Catalog。");
  return requireData(data, "Atlas API 未返回 TestCase Catalog。").items.map(
    mapTestCase
  );
}

export async function readCaseWorkspace(
  caseId: string
): Promise<CaseWorkspaceViewModel> {
  const [draftResponse, runsResponse, versionsResponse] = await Promise.all([
    apiClient.GET("/v1/test-cases/{caseId}/workflow-draft", {
      params: { path: { caseId } }
    }),
    apiClient.GET("/v1/test-cases/{caseId}/debug-runs", {
      params: {
        path: { caseId },
        query: { limit: 100 }
      }
    }),
    apiClient.GET("/v1/test-cases/{caseId}/versions", {
      params: {
        path: { caseId },
        query: { limit: 100 }
      }
    })
  ]);

  if (draftResponse.error) {
    throw toApiError(draftResponse.error, "无法读取 WorkflowDraft。");
  }
  if (runsResponse.error) {
    throw toApiError(runsResponse.error, "无法读取 DebugRun 历史。");
  }
  if (versionsResponse.error) {
    throw toApiError(versionsResponse.error, "无法读取 CaseVersion 历史。");
  }

  const draft = requireData<WorkflowDraftSnapshotDto>(
    draftResponse.data,
    "Atlas API 未返回 WorkflowDraft。"
  );
  const runs = requireData(
    runsResponse.data,
    "Atlas API 未返回 DebugRun 历史。"
  );
  const versions = requireData(
    versionsResponse.data,
    "Atlas API 未返回 CaseVersion 历史。"
  );
  return mapCaseWorkspace(draft, runs.items, versions.items);
}

export async function createTestCase(
  projectId: string,
  command: CreateTestCaseCommand
): Promise<string> {
  const { data, error } = await apiClient.POST(
    "/v1/projects/{projectId}/test-cases",
    {
      params: {
        header: { "Idempotency-Key": `case-${createRequestId()}` },
        path: { projectId }
      },
      body: command
    }
  );
  if (error) throw toApiError(error, "无法创建 TestCase。");
  return requireData(data, "Atlas API 未返回新建 TestCase。").id;
}

export async function previewWorkflowPatch(
  caseId: string,
  command: WorkflowPatchCommand
): Promise<WorkflowPatchPreviewViewModel> {
  const { data, error } = await apiClient.POST(
    "/v1/test-cases/{caseId}/workflow-draft/patches:validate",
    {
      params: { path: { caseId } },
      body: command
    }
  );
  if (error) throw toApiError(error, "无法预检 WorkflowPatch。");
  return mapWorkflowPatchPreview(
    requireData<WorkflowPatchPreviewDto>(
      data,
      "Atlas API 未返回 WorkflowPatch 预检结果。"
    )
  );
}

export async function applyWorkflowPatch(
  caseId: string,
  command: WorkflowPatchCommand
): Promise<void> {
  const { data, error } = await apiClient.POST(
    "/v1/test-cases/{caseId}/workflow-draft/patches:apply",
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
  if (error) throw toApiError(error, "无法应用 WorkflowPatch。");
  requireData(data, "Atlas API 未返回更新后的 WorkflowDraft。");
}

export async function updateWorkflowLayout(
  caseId: string,
  command: LayoutPatchCommand
): Promise<void> {
  const { data, error } = await apiClient.PATCH(
    "/v1/test-cases/{caseId}/workflow-draft/layout",
    {
      params: {
        header: {
          "Idempotency-Key": command.clientMutationId,
          "If-Match": `"revision-${command.baseLayoutRevision}"`
        },
        path: { caseId }
      },
      body: command
    }
  );
  if (error) throw toApiError(error, "无法保存 WorkflowDraft 布局。");
  requireData(data, "Atlas API 未返回更新布局后的 WorkflowDraft。");
}

export async function startDebugRun(
  caseId: string,
  command: StartDebugRunCommand
): Promise<DebugRunDto> {
  const { data, error } = await apiClient.POST(
    "/v1/test-cases/{caseId}/workflow-draft/debug-runs",
    {
      params: {
        header: {
          "Idempotency-Key": `debug-run-${createRequestId()}`,
          "If-Match": `"revision-${command.baseSemanticRevision}"`
        },
        path: { caseId }
      },
      body: command
    }
  );
  if (error) throw toApiError(error, "无法启动 DebugRun。");
  return requireData(data, "Atlas API 未返回新建 DebugRun。");
}

export async function cancelDebugRun(
  runId: string,
  revision: number,
  command: RequestDebugRunCancelCommand
): Promise<DebugRunDto> {
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
  if (error) throw toApiError(error, "无法取消 DebugRun。");
  return requireData(data, "Atlas API 未返回取消后的 DebugRun。");
}

export async function publishCaseVersion(
  caseId: string,
  command: PublishCaseVersionCommand
): Promise<CaseVersionDto> {
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
  if (error) throw toApiError(error, "无法发布 CaseVersion。");
  return requireData(data, "Atlas API 未返回 CaseVersion。");
}
