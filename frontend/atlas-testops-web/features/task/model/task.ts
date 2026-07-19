import type { components } from "@/shared/api/schema";

export type TaskPlanDto = components["schemas"]["TaskPlan"];
export type TaskPlanPageDto = components["schemas"]["TaskPlanPage"];
export type TaskPlanVersionDto = components["schemas"]["TaskPlanVersion"];
export type TaskPlanVersionPageDto =
  components["schemas"]["TaskPlanVersionPage"];
export type TaskRunDto = components["schemas"]["TaskRun"];
export type TaskRunPageDto = components["schemas"]["TaskRunPage"];
export type ExecutionUnitDto = components["schemas"]["ExecutionUnit"];
export type ExecutionUnitPageDto = components["schemas"]["ExecutionUnitPage"];
export type CreateTaskPlanCommand = components["schemas"]["CreateTaskPlan"];
export type StartTaskPlanVersionRunCommand =
  components["schemas"]["StartTaskPlanVersionRun"];
export type RequestTaskRunCancelCommand =
  components["schemas"]["RequestTaskRunCancel"];
export type RequestTaskRunPauseCommand =
  components["schemas"]["RequestTaskRunPause"];
export type RequestTaskRunResumeCommand =
  components["schemas"]["RequestTaskRunResume"];
export type TaskRunCommandIntentDto =
  components["schemas"]["TaskRunCommandIntent"];

export type TaskPlanViewModel = {
  id: string;
  key: string;
  name: string;
  status: string;
  revision: number;
  updatedAt: Date;
};

export type TaskPlanVersionViewModel = {
  id: string;
  taskPlanId: string;
  version: string;
  versionRef: string;
  caseCount: number;
  matrixSize: number;
  contentDigest: string;
  retryPolicyDigest: string | null;
  publishedAt: Date;
};

export type TaskRunViewModel = {
  id: string;
  taskPlanVersionId: string;
  lifecycle: string;
  quality: string;
  hygiene: string;
  triggerSource: string;
  materializationState: string;
  unitCount: number | null;
  revision: number;
  requestedAt: Date;
  startedAt: Date | null;
  closedAt: Date | null;
};

export type ExecutionUnitViewModel = {
  id: string;
  ordinal: number;
  caseVersionId: string;
  environmentId: string;
  browserProfileVersionId: string;
  lifecycle: string;
  quality: string;
  hygiene: string;
};

export type ExecutionUnitPageViewModel = {
  items: ExecutionUnitViewModel[];
  nextAfterOrdinal: number | null;
};

export type TaskUnitSummary = {
  total: number;
  closed: number;
  running: number;
  queued: number;
  passed: number;
  failed: number;
  blocked: number;
  infraError: number;
  canceled: number;
  progress: number;
};
