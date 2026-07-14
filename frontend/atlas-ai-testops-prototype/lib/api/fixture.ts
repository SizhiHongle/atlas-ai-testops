"use client";

import useSWR from "swr";

import { apiClient } from "./client";
import { ApiProblemError, isProblemDetails } from "./problem";
import type { components } from "./schema";

export type DataAtomCatalogItem = components["schemas"]["DataAtomCatalogItem"];
export type DataBlueprintCatalogItem = components["schemas"]["DataBlueprintCatalogItem"];

export type FixtureAssetCatalog = {
  atoms: DataAtomCatalogItem[];
  blueprints: DataBlueprintCatalogItem[];
};

function responseError(error: unknown): Error {
  if (isProblemDetails(error)) {
    return new ApiProblemError(error);
  }
  return new Error("Atlas fixture asset catalog 返回了无法识别的错误响应。");
}

async function getDataAtoms(projectId: string): Promise<DataAtomCatalogItem[]> {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/data-atoms",
    {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 DataAtom Catalog。");
  return data.items;
}

async function getDataBlueprints(
  projectId: string
): Promise<DataBlueprintCatalogItem[]> {
  const { data, error } = await apiClient.GET(
    "/v1/projects/{projectId}/data-blueprints",
    {
      params: {
        path: { projectId },
        query: { limit: 100 }
      }
    }
  );
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas API 未返回 DataBlueprint Catalog。");
  return data.items;
}

async function getFixtureAssetCatalog(
  projectId: string
): Promise<FixtureAssetCatalog> {
  const [atoms, blueprints] = await Promise.all([
    getDataAtoms(projectId),
    getDataBlueprints(projectId)
  ]);
  return { atoms, blueprints };
}

export function useFixtureAssetCatalog(projectId: string | null) {
  return useSWR(
    projectId ? (["fixture-asset-catalog", projectId] as const) : null,
    ([, currentProjectId]) => getFixtureAssetCatalog(currentProjectId),
    {
      revalidateOnFocus: false,
      shouldRetryOnError: false
    }
  );
}
