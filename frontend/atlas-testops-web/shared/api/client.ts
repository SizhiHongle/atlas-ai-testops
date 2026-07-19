import createClient from "openapi-fetch";

import { ATLAS_API_BASE_URL } from "@/shared/config/client";

import { createRequestId } from "./request-id";
import type { paths } from "./schema";

export const apiClient = createClient<paths>({
  baseUrl: ATLAS_API_BASE_URL,
  credentials: "include"
});

apiClient.use({
  async onRequest({ request }) {
    request.headers.set("Accept", "application/json");
    if (!request.headers.has("X-Request-ID")) {
      request.headers.set("X-Request-ID", createRequestId());
    }
    return request;
  }
});
