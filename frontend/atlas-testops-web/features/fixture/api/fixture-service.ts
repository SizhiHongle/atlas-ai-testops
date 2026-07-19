import { apiClient } from "@/shared/api/client";
import { createRequestId } from "@/shared/api/request-id";
import { toApiError } from "@/shared/api/problem";

import { mapFixtureCatalog } from "../model/fixture-mapper";
import type {
  CreateDataAtomCommand,
  CreateDataBlueprintCommand,
  DataAtomDefinitionDto,
  DataBlueprintDefinitionDto,
  FixtureCatalogViewModel
} from "../model/fixture";

function requireData<T>(data: T | undefined, message: string): T {
  if (!data) throw new Error(message);
  return data;
}

export async function readFixtureCatalog(
  projectId: string
): Promise<FixtureCatalogViewModel> {
  const [atomsResponse, blueprintsResponse] = await Promise.all([
    apiClient.GET("/v1/projects/{projectId}/data-atoms", {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }),
    apiClient.GET("/v1/projects/{projectId}/data-blueprints", {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    })
  ]);

  if (atomsResponse.error) {
    throw toApiError(atomsResponse.error, "无法读取 DataAtom Catalog。");
  }
  if (blueprintsResponse.error) {
    throw toApiError(
      blueprintsResponse.error,
      "无法读取 DataBlueprint Catalog。"
    );
  }

  return mapFixtureCatalog({
    atoms: requireData(
      atomsResponse.data,
      "Atlas API 未返回 DataAtom Catalog。"
    ).items,
    blueprints: requireData(
      blueprintsResponse.data,
      "Atlas API 未返回 DataBlueprint Catalog。"
    ).items
  });
}

export async function createDataAtom(
  projectId: string,
  command: CreateDataAtomCommand
): Promise<DataAtomDefinitionDto> {
  const { data, error } = await apiClient.POST(
    "/v1/projects/{projectId}/data-atoms",
    {
      params: {
        header: { "Idempotency-Key": `atom-${createRequestId()}` },
        path: { projectId }
      },
      body: command
    }
  );
  if (error) throw toApiError(error, "无法创建 DataAtom。");
  return requireData(data, "Atlas API 未返回新建 DataAtom。");
}

export async function createDataBlueprint(
  projectId: string,
  command: CreateDataBlueprintCommand
): Promise<DataBlueprintDefinitionDto> {
  const { data, error } = await apiClient.POST(
    "/v1/projects/{projectId}/data-blueprints",
    {
      params: {
        header: { "Idempotency-Key": `blueprint-${createRequestId()}` },
        path: { projectId }
      },
      body: command
    }
  );
  if (error) throw toApiError(error, "无法创建 DataBlueprint。");
  return requireData(data, "Atlas API 未返回新建 DataBlueprint。");
}
