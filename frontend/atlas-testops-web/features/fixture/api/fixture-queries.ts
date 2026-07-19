"use client";

import {
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";

import {
  createDataAtom,
  createDataBlueprint,
  readFixtureCatalog
} from "./fixture-service";
import type {
  CreateDataAtomCommand,
  CreateDataBlueprintCommand
} from "../model/fixture";

export const fixtureQueryKeys = {
  catalog: (projectId: string) => ["fixture", "catalog", projectId] as const
};

export function useFixtureCatalogQuery(projectId: string) {
  return useQuery({
    queryKey: fixtureQueryKeys.catalog(projectId),
    queryFn: () => readFixtureCatalog(projectId)
  });
}

export function useCreateAtomMutation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (command: CreateDataAtomCommand) =>
      createDataAtom(projectId, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: fixtureQueryKeys.catalog(projectId)
      });
    }
  });
}

export function useCreateBlueprintMutation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (command: CreateDataBlueprintCommand) =>
      createDataBlueprint(projectId, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: fixtureQueryKeys.catalog(projectId)
      });
    }
  });
}
