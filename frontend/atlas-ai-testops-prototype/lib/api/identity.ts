"use client";

import useSWR from "swr";

import { apiClient } from "./client";
import { ApiProblemError, isProblemDetails } from "./problem";
import type { components } from "./schema";

export type AccountPool = components["schemas"]["AccountPool"];
export type AccountPoolCapacity = components["schemas"]["AccountPoolCapacity"];
export type Environment = components["schemas"]["Environment"];
export type TestAccount = components["schemas"]["TestAccount"];
export type TestRole = components["schemas"]["TestRole"];

export type IdentityWalletEntry = {
  environment: Environment;
  role: TestRole;
  pool: AccountPool;
  capacity: AccountPoolCapacity;
  account: TestAccount | null;
};

export type IdentityWallet = {
  environment: Environment | null;
  entries: IdentityWalletEntry[];
};

function responseError(error: unknown): Error {
  if (isProblemDetails(error)) {
    return new ApiProblemError(error);
  }
  return new Error("Atlas 身份目录返回了无法识别的错误响应。");
}

async function getProjectEnvironments(projectId: string) {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/environments",
    {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 Environment 列表。");
  return data.items;
}

async function getProjectRoles(projectId: string) {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/test-roles",
    {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 TestRole 列表。");
  return data.items;
}

async function getEnvironmentPools(environmentId: string) {
  const { data, error } = await apiClient.GET(
    "/v1/environments/{environmentId}/account-pools",
    {
      params: {
        path: { environmentId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 AccountPool 列表。");
  return data.items;
}

async function getPoolCapacity(poolId: string) {
  const { data, error } = await apiClient.GET(
    "/v1/account-pools/{poolId}/capacity",
    { params: { path: { poolId } } }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 AccountPool 容量。");
  return data;
}

async function getPoolAccounts(poolId: string) {
  const { data, error } = await apiClient.GET(
    "/v1/account-pools/{poolId}/accounts",
    {
      params: {
        path: { poolId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 TestAccount 列表。");
  return data.items;
}

async function getIdentityWallet(projectId: string): Promise<IdentityWallet> {
  const [environments, roles] = await Promise.all([
    getProjectEnvironments(projectId),
    getProjectRoles(projectId)
  ]);
  const environment = environments.find((item) => item.status === "ACTIVE") ?? null;
  if (!environment) return { environment: null, entries: [] };

  const pools = await getEnvironmentPools(environment.id);
  const roleById = new Map(roles.map((role) => [role.id, role]));
  const entries = await Promise.all(
    pools
      .filter((pool) => roleById.has(pool.roleId))
      .map(async (pool): Promise<IdentityWalletEntry> => {
        const [capacity, accounts] = await Promise.all([
          getPoolCapacity(pool.id),
          getPoolAccounts(pool.id)
        ]);
        const account = accounts.find((item) => item.available) ?? accounts[0] ?? null;
        return {
          environment,
          role: roleById.get(pool.roleId)!,
          pool,
          capacity,
          account
        };
      })
  );
  return { environment, entries };
}

export function useIdentityWallet(projectId: string | null) {
  return useSWR(
    projectId ? (["identity-wallet", projectId] as const) : null,
    ([, currentProjectId]) => getIdentityWallet(currentProjectId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}
