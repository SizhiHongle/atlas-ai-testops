import createClient from "openapi-fetch";

import type { paths } from "./schema";
import { API_BASE_URL, createRequestId } from "./config";

export const apiClient = createClient<paths>({
  baseUrl: API_BASE_URL,
  credentials: "include"
});

apiClient.use({
  async onRequest({ request }) {
    if (!request.headers.has("X-Request-ID")) {
      request.headers.set("X-Request-ID", createRequestId());
    }
    return request;
  }
});
