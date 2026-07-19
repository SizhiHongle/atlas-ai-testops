import { apiClient } from "@/shared/api/client";
import {
  ApiProblemError,
  isApiProblem,
  toApiError
} from "@/shared/api/problem";

import { mapSessionDto } from "../model/session-mapper";
import type {
  LoginCommand,
  PlatformSessionDto,
  SessionViewModel
} from "../model/session";

function requireSession(
  data: PlatformSessionDto | undefined,
  message: string
): SessionViewModel {
  if (!data) throw new Error(message);
  return mapSessionDto(data);
}

export async function login(command: LoginCommand): Promise<SessionViewModel> {
  const { data, error } = await apiClient.POST("/v1/auth/login", {
    body: command
  });
  if (error) throw toApiError(error, "Atlas 身份网关返回了无法识别的错误。");
  return requireSession(data, "Atlas 身份网关未返回 Session。");
}

export async function readSession(): Promise<SessionViewModel | null> {
  const { data, error } = await apiClient.GET("/v1/session");
  if (isApiProblem(error) && error.status === 401) return null;
  if (error) throw toApiError(error, "Atlas 身份网关返回了无法识别的错误。");
  return data ? mapSessionDto(data) : null;
}

export async function logout(): Promise<void> {
  const { error, response } = await apiClient.POST("/v1/auth/logout");
  if (response.status === 401) return;
  if (error) {
    if (isApiProblem(error)) throw new ApiProblemError(error);
    throw toApiError(error, "Atlas 身份网关未能完成退出。");
  }
}
