"use client";

import {
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";

import {
  applyWorkflowPatch,
  cancelDebugRun,
  createTestCase,
  previewWorkflowPatch,
  publishCaseVersion,
  readCaseWorkspace,
  readTestCases,
  startDebugRun,
  updateWorkflowLayout
} from "./case-service";
import type {
  CreateTestCaseCommand,
  LayoutPatchCommand,
  PublishCaseVersionCommand,
  RequestDebugRunCancelCommand,
  StartDebugRunCommand,
  WorkflowPatchCommand
} from "../model/case";

export const caseQueryKeys = {
  catalog: (projectId: string) => ["case", "catalog", projectId] as const,
  workspace: (caseId: string) => ["case", "workspace", caseId] as const
};

export function useCaseCatalogQuery(projectId: string) {
  return useQuery({
    queryKey: caseQueryKeys.catalog(projectId),
    queryFn: () => readTestCases(projectId)
  });
}

export function useCaseWorkspaceQuery(caseId: string | null) {
  return useQuery({
    queryKey: caseId
      ? caseQueryKeys.workspace(caseId)
      : (["case", "workspace", "none"] as const),
    queryFn: () => readCaseWorkspace(caseId!),
    enabled: Boolean(caseId)
  });
}

export function useCreateCaseMutation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (command: CreateTestCaseCommand) =>
      createTestCase(projectId, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: caseQueryKeys.catalog(projectId)
      });
    }
  });
}

export function usePreviewWorkflowPatchMutation(caseId: string) {
  return useMutation({
    mutationKey: ["case", "patch-preview", caseId],
    mutationFn: (command: WorkflowPatchCommand) =>
      previewWorkflowPatch(caseId, command)
  });
}

export function useApplyWorkflowPatchMutation(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["case", "patch-apply", caseId],
    mutationFn: (command: WorkflowPatchCommand) =>
      applyWorkflowPatch(caseId, command),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: caseQueryKeys.workspace(caseId)
        }),
        queryClient.invalidateQueries({
          queryKey: ["case", "catalog"]
        })
      ]);
    }
  });
}

export function useUpdateWorkflowLayoutMutation(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["case", "layout", caseId],
    mutationFn: (command: LayoutPatchCommand) =>
      updateWorkflowLayout(caseId, command),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: caseQueryKeys.workspace(caseId)
        }),
        queryClient.invalidateQueries({
          queryKey: ["case", "catalog"]
        })
      ]);
    }
  });
}

export function useStartDebugRunMutation(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (command: StartDebugRunCommand) =>
      startDebugRun(caseId, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: caseQueryKeys.workspace(caseId)
      });
    }
  });
}

export function useCancelDebugRunMutation(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      runId,
      revision,
      command
    }: {
      runId: string;
      revision: number;
      command: RequestDebugRunCancelCommand;
    }) => cancelDebugRun(runId, revision, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: caseQueryKeys.workspace(caseId)
      });
    }
  });
}

export function usePublishCaseMutation(caseId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (command: PublishCaseVersionCommand) =>
      publishCaseVersion(caseId, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: caseQueryKeys.workspace(caseId)
      });
    }
  });
}
