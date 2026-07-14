"use client";

import useSWR from "swr";

import type { components } from "./schema";

export type HealthResponse = components["schemas"]["HealthResponse"];

export function useReadiness() {
  return useSWR<HealthResponse>("/v1/health/ready");
}
