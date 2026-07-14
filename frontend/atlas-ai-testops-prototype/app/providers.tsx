"use client";

import type { ReactNode } from "react";
import { SWRConfig, type SWRConfiguration } from "swr";

import { fetchApi } from "../lib/api/fetcher";

const SWR_OPTIONS: SWRConfiguration = {
  fetcher: fetchApi,
  dedupingInterval: 2_000,
  revalidateOnFocus: true,
  shouldRetryOnError: true,
  errorRetryCount: 2
};

export function AppProviders({ children }: Readonly<{ children: ReactNode }>) {
  return <SWRConfig value={SWR_OPTIONS}>{children}</SWRConfig>;
}
