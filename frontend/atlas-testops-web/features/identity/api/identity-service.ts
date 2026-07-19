import { apiClient } from "@/shared/api/client";
import { toApiError } from "@/shared/api/problem";

import { mapIdentityWallet } from "../model/identity-mapper";
import type {
  AccountPoolCapacityDto,
  AccountPoolDto,
  EnvironmentDto,
  IdentityWalletDto,
  IdentityWalletViewModel,
  TestAccountDto,
  TestRoleDto
} from "../model/identity";

function requireData<T>(data: T | undefined, message: string): T {
  if (!data) throw new Error(message);
  return data;
}

async function listEnvironments(projectId: string): Promise<EnvironmentDto[]> {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/environments",
    {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw toApiError(error, "无法读取 Environment 目录。");
  return requireData(data, "Atlas API 未返回 Environment 目录。").items;
}

async function listRoles(projectId: string): Promise<TestRoleDto[]> {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/test-roles",
    {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw toApiError(error, "无法读取 TestRole 目录。");
  return requireData(data, "Atlas API 未返回 TestRole 目录。").items;
}

async function listPools(environmentId: string): Promise<AccountPoolDto[]> {
  const { data, error } = await apiClient.GET(
    "/v1/environments/{environmentId}/account-pools",
    {
      params: {
        path: { environmentId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw toApiError(error, "无法读取 AccountPool 目录。");
  return requireData(data, "Atlas API 未返回 AccountPool 目录。").items;
}

async function readCapacity(
  poolId: string
): Promise<AccountPoolCapacityDto> {
  const { data, error } = await apiClient.GET(
    "/v1/account-pools/{poolId}/capacity",
    { params: { path: { poolId } } }
  );
  if (error) throw toApiError(error, "无法读取 AccountPool 容量。");
  return requireData(data, "Atlas API 未返回 AccountPool 容量。");
}

async function listAccounts(poolId: string): Promise<TestAccountDto[]> {
  const { data, error } = await apiClient.GET(
    "/v1/account-pools/{poolId}/accounts",
    {
      params: {
        path: { poolId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw toApiError(error, "无法读取 TestAccount 目录。");
  return requireData(data, "Atlas API 未返回 TestAccount 目录。").items;
}

export async function readIdentityWallet(
  projectId: string
): Promise<IdentityWalletViewModel> {
  const [environments, roles] = await Promise.all([
    listEnvironments(projectId),
    listRoles(projectId)
  ]);
  const environment =
    environments.find(
      (item) => item.status === "ACTIVE" && item.kind === "TEST"
    ) ??
    environments.find((item) => item.status === "ACTIVE") ??
    null;

  if (!environment) {
    return mapIdentityWallet({ environment: null, entries: [] });
  }

  const pools = await listPools(environment.id);
  const roleById = new Map(roles.map((role) => [role.id, role]));
  const entries = await Promise.all(
    pools
      .filter((pool) => roleById.has(pool.roleId))
      .map(async (pool) => {
        const [capacity, accounts] = await Promise.all([
          readCapacity(pool.id),
          listAccounts(pool.id)
        ]);
        return {
          role: roleById.get(pool.roleId)!,
          pool,
          capacity,
          accounts
        };
      })
  );

  const dto: IdentityWalletDto = { environment, entries };
  return mapIdentityWallet(dto);
}
