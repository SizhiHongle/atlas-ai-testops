"use client";

import useSWR from "swr";

import { apiClient } from "./client";
import { ApiProblemError, isProblemDetails } from "./problem";
import type { components } from "./schema";

export type Tenant = components["schemas"]["Tenant"];
export type Project = components["schemas"]["Project"];
export type ProjectPage = components["schemas"]["ProjectPage"];
export type Environment = components["schemas"]["Environment"];
export type EnvironmentPage = components["schemas"]["EnvironmentPage"];
export type EnvironmentKind = components["schemas"]["EnvironmentKind"];
export type CreateProject = components["schemas"]["CreateProject"];
export type UpdateProject = components["schemas"]["UpdateProject"];
export type CreateEnvironment = components["schemas"]["CreateEnvironment"];
export type UpdateEnvironment = components["schemas"]["UpdateEnvironment"];

const tenantHeader = (tenantId: string) => ({
  "X-Atlas-Tenant-ID": tenantId
});

function responseError(error: unknown): Error {
  if (isProblemDetails(error)) {
    return new ApiProblemError(error);
  }
  return new Error("Atlas API 返回了无法识别的错误响应。");
}

async function getCurrentTenant(tenantId: string): Promise<Tenant> {
  const { data, error } = await apiClient.GET("/v1/tenants/current", {
    params: { header: tenantHeader(tenantId) }
  });
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 Tenant。");
  return data;
}

async function getProjects(tenantId: string): Promise<ProjectPage> {
  const { data, error } = await apiClient.GET("/v1/projects", {
    params: {
      header: tenantHeader(tenantId),
      query: { limit: 100 }
    }
  });
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 Project 列表。");
  return data;
}

async function getEnvironments(
  tenantId: string,
  projectId: string
): Promise<EnvironmentPage> {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/environments",
    {
      params: {
        header: tenantHeader(tenantId),
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 Environment 列表。");
  return data;
}

export function useCurrentTenant(tenantId: string) {
  return useSWR(["platform-tenant", tenantId] as const, ([, currentTenantId]) =>
    getCurrentTenant(currentTenantId)
  );
}

export function useProjects(tenantId: string) {
  return useSWR(["platform-projects", tenantId] as const, ([, currentTenantId]) =>
    getProjects(currentTenantId)
  );
}

export function useEnvironments(tenantId: string, projectId: string | null) {
  return useSWR(
    projectId ? (["platform-environments", tenantId, projectId] as const) : null,
    ([, currentTenantId, currentProjectId]) =>
      getEnvironments(currentTenantId, currentProjectId)
  );
}

export async function createProject(
  tenantId: string,
  command: CreateProject
): Promise<Project> {
  const { data, error } = await apiClient.POST("/v1/projects", {
    params: {
      header: {
        ...tenantHeader(tenantId),
        "Idempotency-Key": `project-${globalThis.crypto.randomUUID()}`
      }
    },
    body: command
  });
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回新建 Project。");
  return data;
}

export async function updateProject(
  tenantId: string,
  project: Pick<Project, "id" | "revision">,
  command: UpdateProject
): Promise<Project> {
  const { data, error } = await apiClient.PATCH("/v1/projects/{projectId}", {
    params: {
      header: {
        ...tenantHeader(tenantId),
        "If-Match": `"revision-${project.revision}"`
      },
      path: { projectId: project.id }
    },
    body: command
  });
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回更新后的 Project。");
  return data;
}

export async function createEnvironment(
  tenantId: string,
  projectId: string,
  command: CreateEnvironment
): Promise<Environment> {
  const { data, error } = await apiClient.POST(
    "/v1/projects/{projectId}/environments",
    {
      params: {
        header: {
          ...tenantHeader(tenantId),
          "Idempotency-Key": `environment-${globalThis.crypto.randomUUID()}`
        },
        path: { projectId }
      },
      body: command
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回新建 Environment。");
  return data;
}

export async function updateEnvironment(
  tenantId: string,
  environment: Pick<Environment, "id" | "revision">,
  command: UpdateEnvironment
): Promise<Environment> {
  const { data, error } = await apiClient.PATCH(
    "/v1/environments/{environmentId}",
    {
      params: {
        header: {
          ...tenantHeader(tenantId),
          "If-Match": `"revision-${environment.revision}"`
        },
        path: { environmentId: environment.id }
      },
      body: command
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回更新后的 Environment。");
  return data;
}
