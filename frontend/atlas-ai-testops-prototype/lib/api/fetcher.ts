import { API_BASE_URL, createRequestId } from "./config";
import { ApiProblemError, isProblemDetails } from "./problem";

export async function fetchApi<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    headers: {
      Accept: "application/json",
      "X-Request-ID": createRequestId()
    }
  });

  if (!response.ok) {
    const body: unknown = await response.json().catch(() => undefined);
    if (isProblemDetails(body)) {
      throw new ApiProblemError(body);
    }
    throw new Error(`Atlas API 请求失败：HTTP ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
