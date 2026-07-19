import { getServerEnvironment } from "@/shared/config/server";
import { createRequestId } from "@/shared/api/request-id";

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

const HOP_BY_HOP_HEADERS = [
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade"
] as const;

const PRIVATE_RESPONSE_HEADERS = [
  "server",
  "via",
  "x-aspnet-version",
  "x-powered-by"
] as const;

const MAX_REQUEST_BODY_BYTES = 10 * 1024 * 1024;
const REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

class RequestBodyTooLargeError extends Error {}

function resolveRequestId(request: Request): string {
  const candidate = request.headers.get("X-Request-ID");
  return candidate && REQUEST_ID_PATTERN.test(candidate)
    ? candidate
    : createRequestId();
}

function problem(
  status: number,
  title: string,
  detail: string,
  requestId: string
): Response {
  return Response.json(
    {
      type: "about:blank",
      title,
      status,
      detail,
      requestId
    },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": "application/problem+json",
        "X-Atlas-Proxy": "same-origin-bff",
        "X-Content-Type-Options": "nosniff",
        "X-Request-ID": requestId
      }
    }
  );
}

function upstreamHeaders(request: Request, requestId: string): Headers {
  const headers = new Headers(request.headers);
  HOP_BY_HOP_HEADERS.forEach((name) => headers.delete(name));
  headers.delete("forwarded");
  headers.delete("x-forwarded-for");
  headers.delete("x-forwarded-host");
  headers.delete("x-forwarded-proto");

  const publicUrl = new URL(request.url);
  headers.set("Origin", publicUrl.origin);
  headers.set("X-Request-ID", requestId);
  headers.set("X-Forwarded-Host", publicUrl.host);
  headers.set("X-Forwarded-Proto", publicUrl.protocol.slice(0, -1));
  return headers;
}

function appendVary(headers: Headers, name: string): void {
  const values = new Set(
    (headers.get("Vary") ?? "")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean)
  );
  values.add(name);
  headers.set("Vary", [...values].join(", "));
}

function responseHeaders(
  headers: Headers,
  requestId: string,
  publicOrigin: string,
  apiOrigin: string
): Headers {
  const result = new Headers(headers);
  HOP_BY_HOP_HEADERS.forEach((name) => result.delete(name));
  PRIVATE_RESPONSE_HEADERS.forEach((name) => result.delete(name));
  result.set("Cache-Control", "no-store");
  result.set("Pragma", "no-cache");
  result.set("X-Content-Type-Options", "nosniff");
  result.set("X-Request-ID", requestId);
  result.set("X-Atlas-Proxy", "same-origin-bff");
  appendVary(result, "Cookie");

  const location = result.get("Location");
  if (location?.startsWith(apiOrigin)) {
    const upstreamLocation = new URL(location);
    result.set(
      "Location",
      `${publicOrigin}/api/atlas${upstreamLocation.pathname}${upstreamLocation.search}${upstreamLocation.hash}`
    );
  }
  return result;
}

async function proxiedResponse(
  upstream: Response,
  requestId: string,
  publicOrigin: string,
  apiOrigin: string
): Promise<Response> {
  const headers = responseHeaders(
    upstream.headers,
    requestId,
    publicOrigin,
    apiOrigin
  );

  if (
    upstream.headers
      .get("Content-Type")
      ?.toLowerCase()
      .includes("application/problem+json")
  ) {
    try {
      const payload: unknown = await upstream.clone().json();
      if (payload && typeof payload === "object" && !Array.isArray(payload)) {
        return new Response(
          JSON.stringify({
            ...payload,
            requestId
          }),
          {
            status: upstream.status,
            statusText: upstream.statusText,
            headers
          }
        );
      }
    } catch {
      // Preserve malformed upstream responses for diagnostics.
    }
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers
  });
}

async function readRequestBody(
  request: Request
): Promise<ArrayBuffer | undefined> {
  if (request.method === "GET" || request.method === "HEAD") return undefined;

  const declaredLength = Number(request.headers.get("Content-Length"));
  if (
    Number.isFinite(declaredLength) &&
    declaredLength > MAX_REQUEST_BODY_BYTES
  ) {
    throw new RequestBodyTooLargeError();
  }

  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_REQUEST_BODY_BYTES) {
    throw new RequestBodyTooLargeError();
  }
  return body;
}

async function proxy(request: Request, context: RouteContext): Promise<Response> {
  const requestId = resolveRequestId(request);
  const { path } = await context.params;
  if (!path.length || path[0] !== "v1") {
    return problem(
      404,
      "Atlas API 路径不存在",
      "BFF 只代理 /v1 API。",
      requestId
    );
  }

  let apiOrigin: string;
  try {
    apiOrigin = getServerEnvironment().ATLAS_API_ORIGIN;
  } catch {
    return problem(
      503,
      "Atlas API 尚未配置",
      "服务端缺少有效的 ATLAS_API_ORIGIN。",
      requestId
    );
  }

  const requestUrl = new URL(request.url);
  const upstreamUrl = new URL(
    path.map((segment) => encodeURIComponent(segment)).join("/"),
    `${apiOrigin}/`
  );
  upstreamUrl.search = requestUrl.search;

  try {
    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers: upstreamHeaders(request, requestId),
      body: await readRequestBody(request),
      redirect: "manual"
    });

    return proxiedResponse(
      upstream,
      requestId,
      requestUrl.origin,
      apiOrigin
    );
  } catch (error) {
    if (error instanceof RequestBodyTooLargeError) {
      return problem(
        413,
        "请求体超过限制",
        "Atlas BFF 单次请求体不得超过 10 MiB。",
        requestId
      );
    }
    return problem(
      502,
      "Atlas API 连接失败",
      "生产前端无法连接到已配置的后端服务。",
      requestId
    );
  }
}

export const dynamic = "force-dynamic";

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
