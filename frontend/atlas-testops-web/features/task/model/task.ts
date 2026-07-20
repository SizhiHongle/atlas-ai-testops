import type { components } from "@/shared/api/schema";

export type TaskPlanDto = components["schemas"]["TaskPlan"];
export type TaskPlanPageDto = components["schemas"]["TaskPlanPage"];
export type TaskPlanVersionDto = components["schemas"]["TaskPlanVersion"];
export type TaskPlanVersionPageDto =
  components["schemas"]["TaskPlanVersionPage"];
export type TaskScheduleDto = components["schemas"]["TaskSchedule"];
export type TaskSchedulePageDto = components["schemas"]["TaskSchedulePage"];
export type TaskRunDto = components["schemas"]["TaskRun"];
export type TaskRunPageDto = components["schemas"]["TaskRunPage"];
export type ExecutionUnitDto = components["schemas"]["ExecutionUnit"];
export type ExecutionUnitPageDto = components["schemas"]["ExecutionUnitPage"];
export type CreateTaskPlanCommand = components["schemas"]["CreateTaskPlan"];
export type CreateTaskScheduleCommand =
  components["schemas"]["CreateTaskSchedule"];
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
export type TaskRetryPolicy = components["schemas"]["TaskRetryPolicy"];
export type TestCaseCatalogItemDto =
  components["schemas"]["TestCaseCatalogItem"];
export type TestCasePageDto = components["schemas"]["TestCasePage"];
export type CaseVersionDto = components["schemas"]["CaseVersion"];
export type CaseVersionPageDto = components["schemas"]["CaseVersionPage"];
export type EnvironmentDto = components["schemas"]["Environment"];
export type EnvironmentPageDto = components["schemas"]["EnvironmentPage"];

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
  pinnedCaseVersionIds: string[];
  environmentIds: string[];
  browserProfileVersionIds: string[];
  identityProfileVersionIds: string[];
  dataProfileVersionIds: string[];
  caseCount: number;
  matrixSize: number;
  contentDigest: string;
  retryPolicyDigest: string | null;
  publishedAt: Date;
};

export type TaskScheduleViewModel = {
  id: string;
  taskPlanVersionId: string;
  name: string;
  key: string;
  status: string;
  syncStatus: string;
  timeZoneName: string;
  nextFireTimes: Date[];
  retryPolicy: TaskRetryPolicy;
  revision: number;
};

export type TaskControlCatalogViewModel = {
  plans: TaskPlanViewModel[];
  versions: TaskPlanVersionViewModel[];
  schedules: TaskScheduleViewModel[];
};

export type PublishedCaseVersionViewModel = {
  id: string;
  testCaseId: string;
  caseKey: string;
  caseName: string;
  roleKey: string | null;
  version: string;
  semanticRevision: number;
  publishedAt: Date;
};

export type TaskEnvironmentViewModel = {
  id: string;
  key: string;
  name: string;
  kind: string;
};

export type TaskAssemblyCatalogViewModel = {
  caseVersions: PublishedCaseVersionViewModel[];
  environments: TaskEnvironmentViewModel[];
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
