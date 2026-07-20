import type { components } from "@/shared/api/schema";

export type TestCaseCatalogItemDto =
  components["schemas"]["TestCaseCatalogItem"];
export type TestCasePageDto = components["schemas"]["TestCasePage"];
export type WorkflowDraftSnapshotDto =
  components["schemas"]["WorkflowDraftSnapshot"];
export type DebugRunDto = components["schemas"]["DebugRun"];
export type DebugRunPageDto = components["schemas"]["DebugRunPage"];
export type CaseVersionDto = components["schemas"]["CaseVersion"];
export type CaseVersionPageDto = components["schemas"]["CaseVersionPage"];
export type CreateTestCaseCommand = components["schemas"]["CreateTestCase"];
export type StartDebugRunCommand = components["schemas"]["StartDebugRun"];
export type RequestDebugRunCancelCommand =
  components["schemas"]["RequestDebugRunCancel"];
export type PublishCaseVersionCommand =
  components["schemas"]["PublishCaseVersion"];
export type WorkflowPatchCommand = components["schemas"]["WorkflowPatch"];
export type WorkflowPatchPreviewDto =
  components["schemas"]["WorkflowPatchPreview"];
export type LayoutPatchCommand = components["schemas"]["LayoutPatch"];

export type WorkflowPortViewModel = {
  key: string;
  semanticType: string;
  kind: "data" | "control";
  required: boolean;
  sensitive: boolean;
};

export type TestCaseCardViewModel = {
  id: string;
  draftId: string;
  key: string;
  name: string;
  summary: string;
  status: string;
  graphValid: boolean;
  semanticRevision: number;
  layoutRevision: number;
  actorCount: number;
  primaryRoleKey: string | null;
  updatedBy: string;
  updatedAt: Date;
};

export type WorkflowNodeViewModel = {
  id: string;
  kind: string;
  phase: string;
  versionRef: string;
  terminal: boolean;
  oracleStrength: string | null;
  inputPorts: WorkflowPortViewModel[];
  outputPorts: WorkflowPortViewModel[];
  x: number;
  y: number;
};

export type WorkflowEdgeViewModel = {
  id: string;
  sourceNodeId: string;
  targetNodeId: string;
  semanticType: string;
};

export type DebugRunViewModel = {
  id: string;
  semanticRevision: number;
  lifecycle: string;
  outcome: string;
  snapshotStatus: string;
  revision: number;
  requestedAt: Date;
  completedAt: Date | null;
  failureDetail: string | null;
};

export type CaseVersionViewModel = {
  id: string;
  version: string;
  revision: number;
  publishedAt: Date;
  reviewSummary: string;
  semanticRevision: number;
};

export type CaseWorkspaceViewModel = {
  draft: {
    id: string;
    semanticRevision: number;
    layoutRevision: number;
    semanticDigest: string;
    valid: boolean;
    matchedRequiredInputs: number;
    totalRequiredInputs: number;
    issues: Array<{
      code: string;
      message: string;
      nodeId: string | null;
    }>;
    executionLevels: string[][];
    nodes: WorkflowNodeViewModel[];
    edges: WorkflowEdgeViewModel[];
    canvasWidth: number;
    canvasHeight: number;
  };
  debugRuns: DebugRunViewModel[];
  versions: CaseVersionViewModel[];
};

export type WorkflowPatchPreviewViewModel = {
  applicable: boolean;
  semanticDigest: string;
  graphValid: boolean;
  nodeCount: number;
  edgeCount: number;
  issues: Array<{
    code: string;
    message: string;
  }>;
};
