import { apiClient } from "@/shared/api/client";
import { toApiError } from "@/shared/api/problem";

import {
  mapExecutionUnit,
  mapPublishedCaseVersion,
  mapTaskEnvironment,
  mapTaskPlan,
  mapTaskPlanVersion,
  mapTaskRun,
  mapTaskSchedule
} from "../model/task-mapper";
import type {
  CaseVersionPageDto,
  CreateTaskScheduleCommand,
  CreateTaskPlanCommand,
  EnvironmentPageDto,
  ExecutionUnitPageDto,
  ExecutionUnitPageViewModel,
  RequestTaskRunCancelCommand,
  RequestTaskRunPauseCommand,
  RequestTaskRunResumeCommand,
  StartTaskPlanVersionRunCommand,
  TaskAssemblyCatalogViewModel,
  TaskControlCatalogViewModel,
  TaskPlanVersionViewModel,
  TaskPlanVersionPageDto,
  TaskPlanPageDto,
  TaskPlanViewModel,
  TaskRunPageDto,
  TaskRunCommandIntentDto,
  TaskRunViewModel,
  TaskSchedulePageDto,
  TestCasePageDto
} from "../model/task";

function requireData<T>(data: T | undefined, message: string): T {
  if (!data) throw new Error(message);
  return data;
}

async function mapInBatches<T, R>(
  items: T[],
  mapper: (item: T) => Promise<R>,
  batchSize = 8
): Promise<R[]> {
  const results: R[] = [];
  for (let index = 0; index < items.length; index += batchSize) {
    const batch = items.slice(index, index + batchSize);
    results.push(...(await Promise.all(batch.map(mapper))));
  }
  return results;
}

export async function readTaskPlans(
  projectId: string
): Promise<TaskPlanViewModel[]> {
  const items: TaskPlanViewModel[] = [];
  let cursor: string | null = null;

  do {
    const response = await apiClient.GET(
      "/v1/projects/{projectId}/task-plans",
      {
        params: {
          path: { projectId },
          query: { cursor, limit: 100 }
        }
      }
    );
    if (response.error) {
      throw toApiError(response.error, "无法读取 TaskPlan Catalog。");
    }
    const page: TaskPlanPageDto = requireData(
      response.data,
      "Atlas API 未返回 TaskPlan Catalog。"
    );
    items.push(...page.items.map(mapTaskPlan));
    cursor = page.nextCursor ?? null;
  } while (cursor);

  return items.sort(
    (left, right) => right.updatedAt.getTime() - left.updatedAt.getTime()
  );
}

export async function readTaskPlanVersions(
  taskPlanId: string
): Promise<TaskPlanVersionViewModel[]> {
  const items: TaskPlanVersionViewModel[] = [];
  let cursor: string | null = null;

  do {
    const response = await apiClient.GET(
      "/v1/task-plans/{taskPlanId}/versions",
      {
        params: {
          path: { taskPlanId },
          query: { cursor, limit: 100 }
        }
      }
    );
    if (response.error) {
      throw toApiError(response.error, "无法读取 TaskPlanVersion。");
    }
    const page: TaskPlanVersionPageDto = requireData(
      response.data,
      "Atlas API 未返回 TaskPlanVersion。"
    );
    items.push(...page.items.map(mapTaskPlanVersion));
    cursor = page.nextCursor ?? null;
  } while (cursor);

  return items.sort(
    (left, right) => right.publishedAt.getTime() - left.publishedAt.getTime()
  );
}

async function readTaskSchedules(taskPlanVersionId: string) {
  const items = [];
  let cursor: string | null = null;

  do {
    const response = await apiClient.GET(
      "/v1/task-plan-versions/{taskPlanVersionId}/schedules",
      {
        params: {
          path: { taskPlanVersionId },
          query: { cursor, limit: 100 }
        }
      }
    );
    if (response.error) {
      throw toApiError(response.error, "无法读取 Task Schedule。");
    }
    const page: TaskSchedulePageDto = requireData(
      response.data,
      "Atlas API 未返回 Task Schedule。"
    );
    items.push(...page.items.map(mapTaskSchedule));
    cursor = page.nextCursor ?? null;
  } while (cursor);

  return items;
}

export async function readTaskControlCatalog(
  projectId: string
): Promise<TaskControlCatalogViewModel> {
  const plans = await readTaskPlans(projectId);
  const versionGroups = await mapInBatches(plans, (plan) =>
    readTaskPlanVersions(plan.id)
  );
  const versions = versionGroups.flat();
  const scheduleGroups = await mapInBatches(versions, (version) =>
    readTaskSchedules(version.id)
  );

  return {
    plans,
    versions,
    schedules: scheduleGroups
      .flat()
      .sort((left, right) => {
        const leftFire = left.nextFireTimes[0]?.getTime() ?? Number.MAX_VALUE;
        const rightFire = right.nextFireTimes[0]?.getTime() ?? Number.MAX_VALUE;
        return leftFire - rightFire;
      })
  };
}

async function readProjectTestCases(projectId: string) {
  const items = [];
  let cursor: string | null = null;

  do {
    const response = await apiClient.GET(
      "/v1/projects/{projectId}/test-cases",
      {
        params: {
          path: { projectId },
          query: { cursor, limit: 100 }
        }
      }
    );
    if (response.error) {
      throw toApiError(response.error, "无法读取 TestCase Catalog。");
    }
    const page: TestCasePageDto = requireData(
      response.data,
      "Atlas API 未返回 TestCase Catalog。"
    );
    items.push(...page.items);
    cursor = page.nextCursor ?? null;
  } while (cursor);

  return items;
}

async function readProjectEnvironments(projectId: string) {
  const items = [];
  let cursor: string | null = null;

  do {
    const response = await apiClient.GET(
      "/v1/projects/{projectId}/environments",
      {
        params: {
          path: { projectId },
          query: { cursor, limit: 100 }
        }
      }
    );
    if (response.error) {
      throw toApiError(response.error, "无法读取 Environment Catalog。");
    }
    const page: EnvironmentPageDto = requireData(
      response.data,
      "Atlas API 未返回 Environment Catalog。"
    );
    items.push(...page.items);
    cursor = page.nextCursor ?? null;
  } while (cursor);

  return items;
}

export async function readTaskAssemblyCatalog(
  projectId: string
): Promise<TaskAssemblyCatalogViewModel> {
  const [testCases, environments] = await Promise.all([
    readProjectTestCases(projectId),
    readProjectEnvironments(projectId)
  ]);
  const versionGroups = await mapInBatches(testCases, async (testCase) => {
    const items = [];
    let cursor: string | null = null;

    do {
      const response = await apiClient.GET(
        "/v1/test-cases/{caseId}/versions",
        {
          params: {
            path: { caseId: testCase.id },
            query: { cursor, limit: 100 }
          }
        }
      );
      if (response.error) {
        throw toApiError(response.error, "无法读取 CaseVersion Catalog。");
      }
      const page: CaseVersionPageDto = requireData(
        response.data,
        "Atlas API 未返回 CaseVersion Catalog。"
      );
      items.push(
        ...page.items.map((version) =>
          mapPublishedCaseVersion(testCase, version)
        )
      );
      cursor = page.nextCursor ?? null;
    } while (cursor);

    return items;
  });

  return {
    caseVersions: versionGroups
      .flat()
      .sort(
        (left, right) =>
          right.publishedAt.getTime() - left.publishedAt.getTime()
      ),
    environments: environments
      .filter(
        (environment) =>
          environment.status === "ACTIVE" &&
          ["TEST", "STAGING"].includes(environment.kind)
      )
      .map(mapTaskEnvironment)
      .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"))
  };
}

export async function readTaskRuns(
  projectId: string
): Promise<TaskRunViewModel[]> {
  const items: TaskRunViewModel[] = [];
  let cursor: string | null = null;

  do {
    const response = await apiClient.GET(
      "/v1/projects/{projectId}/task-runs",
      {
        params: {
          path: { projectId },
          query: { cursor, limit: 100 }
        }
      }
    );
    if (response.error) throw toApiError(response.error, "无法读取 TaskRun。");
    const page: TaskRunPageDto = requireData(
      response.data,
      "Atlas API 未返回 TaskRun。"
    );
    items.push(...page.items.map(mapTaskRun));
    cursor = page.nextCursor ?? null;
  } while (cursor);

  return items.sort(
    (left, right) => right.requestedAt.getTime() - left.requestedAt.getTime()
  );
}

export async function readExecutionUnits(
  runId: string,
  afterOrdinal = 0
): Promise<ExecutionUnitPageViewModel> {
  const response = await apiClient.GET("/v1/task-runs/{runId}/units", {
    params: {
      path: { runId },
      query: { afterOrdinal, limit: 100 }
    }
  });
  if (response.error) {
    throw toApiError(response.error, "无法读取 ExecutionUnit。");
  }
  const page: ExecutionUnitPageDto = requireData(
    response.data,
    "Atlas API 未返回 ExecutionUnit。"
  );
  return {
    items: page.items.map(mapExecutionUnit),
    nextAfterOrdinal: page.nextAfterOrdinal ?? null
  };
}

export async function createTaskPlan(
  projectId: string,
  command: CreateTaskPlanCommand
): Promise<string> {
  const { data, error } = await apiClient.POST(
    "/v1/projects/{projectId}/task-plans",
    {
      params: {
        header: { "Idempotency-Key": command.clientMutationId },
        path: { projectId }
      },
      body: command
    }
  );
  if (error) throw toApiError(error, "无法创建 TaskPlan。");
  return requireData(data, "Atlas API 未返回新建 TaskPlan。").id;
}

export async function createTaskSchedule(
  taskPlanVersionId: string,
  command: CreateTaskScheduleCommand
): Promise<string> {
  const { data, error } = await apiClient.POST(
    "/v1/task-plan-versions/{taskPlanVersionId}/schedules",
    {
      params: {
        header: { "Idempotency-Key": command.clientMutationId },
        path: { taskPlanVersionId }
      },
      body: command
    }
  );
  if (error) throw toApiError(error, "无法创建 Task Schedule。");
  return requireData(data, "Atlas API 未返回新建 Task Schedule。").id;
}

export async function startTaskPlanVersionRun(
  taskPlanVersionId: string,
  command: StartTaskPlanVersionRunCommand
): Promise<string> {
  const { data, error } = await apiClient.POST(
    "/v1/task-plan-versions/{taskPlanVersionId}:run",
    {
      params: {
        header: { "Idempotency-Key": command.clientMutationId },
        path: { taskPlanVersionId }
      },
      body: command
    }
  );
  if (error) throw toApiError(error, "无法启动 TaskRun。");
  return requireData(data, "Atlas API 未返回新建 TaskRun。").id;
}

export type CommandKind = "cancel" | "pause" | "resume";

export type TaskCommand =
  | RequestTaskRunCancelCommand
  | RequestTaskRunPauseCommand
  | RequestTaskRunResumeCommand;

export async function requestTaskRunCommand(
  runId: string,
  revision: number,
  kind: CommandKind,
  command: TaskCommand
): Promise<TaskRunCommandIntentDto> {
  const params = {
    header: {
      "Idempotency-Key": command.clientMutationId,
      "If-Match": `"revision-${revision}"`
    },
    path: { runId }
  };

  const response =
    kind === "cancel"
      ? await apiClient.POST("/v1/task-runs/{runId}:cancel", {
          params,
          body: command
        })
      : kind === "pause"
        ? await apiClient.POST("/v1/task-runs/{runId}:pause", {
            params,
            body: command
          })
        : await apiClient.POST("/v1/task-runs/{runId}:resume", {
            params,
            body: command
          });

  if (response.error) {
    throw toApiError(response.error, `无法提交 TaskRun ${kind} 命令。`);
  }
  return requireData(response.data, "Atlas API 未返回 TaskRun 控制命令。");
}
