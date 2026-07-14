"use client";

import useSWR from "swr";

import { apiClient } from "./client";
import { ApiProblemError, isProblemDetails } from "./problem";
import type { components } from "./schema";

export type LoginCommand = components["schemas"]["LoginCommand"];
export type PlatformSessionView = components["schemas"]["PlatformSessionView"];

function responseError(error: unknown): Error {
  if (isProblemDetails(error)) {
    return new ApiProblemError(error);
  }
  return new Error("Atlas 身份网关返回了无法识别的错误响应。");
}

export async function loginToPlatform(
  command: LoginCommand
): Promise<PlatformSessionView> {
  const { data, error } = await apiClient.POST("/v1/auth/login", {
    body: command
  });
  if (error) throw responseError(error);
  if (!data) throw new Error("Atlas 身份网关未返回 Session。");
  return data;
}

async function getPlatformSession(): Promise<PlatformSessionView | null> {
  const { data, error } = await apiClient.GET("/v1/session");
  if (error) {
    if (isProblemDetails(error) && error.status === 401) return null;
    throw responseError(error);
  }
  return data ?? null;
}

export function usePlatformSession() {
  return useSWR("platform-session", getPlatformSession, {
    revalidateOnFocus: false,
    shouldRetryOnError: false
  });
}
