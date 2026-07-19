"use client";

import { useQuery } from "@tanstack/react-query";

import { readIdentityWallet } from "./identity-service";

export const identityQueryKeys = {
  wallet: (projectId: string) => ["identity", "wallet", projectId] as const
};

export function useIdentityWalletQuery(projectId: string) {
  return useQuery({
    queryKey: identityQueryKeys.wallet(projectId),
    queryFn: () => readIdentityWallet(projectId)
  });
}
