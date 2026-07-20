import { apiClient } from "@/shared/api/client";
import {
  isApiProblem,
  toApiError
} from "@/shared/api/problem";
import { createRequestId } from "@/shared/api/request-id";
import { ATLAS_API_BASE_URL } from "@/shared/config/client";

import {
  mapDebugEventWindow,
  mapDebugEvidence,
  mapDebugLiveSnapshot,
  mapDebugRunDetail,
  mapLiveSnapshot,
  mapUnitAttempt
} from "../model/live-mapper";
import type {
  DebugEventWindowViewModel,
  DebugEvidenceViewModel,
  DebugLiveFrameViewModel,
  DebugLiveSnapshotDto,
  DebugLiveSnapshotViewModel,
  DebugRunDetailViewModel,
  DebugRunDto,
  DebugRunEventDto,
  DebugRunEventPageDto,
  EvidenceManifestDto,
  EvidenceReadGrantDto,
  EvidenceReadPurpose,
  LiveControlCommandDto,
  LiveControlKind,
  LiveSnapshotViewModel,
  RequestLiveControlCommand,
  UnitAttemptPageDto,
  UnitAttemptViewModel
} from "../model/live";

const MAX_DEBUG_EVENT_WINDOW = 500;

function requireData<T>(data: T | undefined, message: string): T {
  if (!data) throw new Error(message);
  return data;
}

export async function readUnitAttempts(
  runId: string,
  unitId: string
): Promise<UnitAttemptViewModel[]> {
  const response = await apiClient.GET(
    "/v1/task-runs/{runId}/units/{unitId}/attempts",
    {
      params: {
        path: { runId, unitId },
        query: { afterAttemptNumber: 0, limit: 100 }
      }
    }
  );
  if (response.error) {
    throw toApiError(response.error, "无法读取 UnitAttempt。");
  }
  const page: UnitAttemptPageDto = requireData(
    response.data,
    "Atlas API 未返回 UnitAttempt。"
  );
  return page.items
    .map(mapUnitAttempt)
    .sort((left, right) => right.attemptNumber - left.attemptNumber);
}

export async function readLiveSnapshot(
  attemptId: string
): Promise<LiveSnapshotViewModel | null> {
  const response = await apiClient.GET(
    "/v1/unit-attempts/{attemptId}/snapshot",
    {
      params: { path: { attemptId } }
    }
  );
  if (response.error) {
    if (isApiProblem(response.error) && response.error.status === 404) {
      return null;
    }
    throw toApiError(response.error, "无法读取 LiveSession Snapshot。");
  }
  return mapLiveSnapshot(
    requireData(response.data, "Atlas API 未返回 LiveSession Snapshot。")
  );
}

export async function readDebugLiveSnapshot(
  debugRunId: string
): Promise<DebugLiveSnapshotViewModel> {
  const response = await apiClient.GET("/v1/debug-runs/{runId}/live", {
    params: { path: { runId: debugRunId } }
  });
  if (response.error) {
    throw toApiError(response.error, "无法读取 DebugRun Live Snapshot。");
  }
  return mapDebugLiveSnapshot(
    requireData<DebugLiveSnapshotDto>(
      response.data,
      "Atlas API 未返回 DebugRun Live Snapshot。"
    )
  );
}

export async function readDebugLiveFrame(
  debugRunId: string
): Promise<DebugLiveFrameViewModel | null> {
  const response = await fetch(
    `${ATLAS_API_BASE_URL}/v1/debug-runs/${encodeURIComponent(
      debugRunId
    )}/live-frame/content`,
    {
      credentials: "include",
      cache: "no-store",
      headers: {
        Accept: "image/jpeg,image/png,image/webp",
        "X-Request-ID": createRequestId()
      }
    }
  );
  if (response.status === 404) return null;
  if (!response.ok) {
    let error: unknown;
    try {
      error = await response.json();
    } catch {
      error = new Error(`Live frame HTTP ${response.status}.`);
    }
    throw toApiError(error, "无法读取实时浏览器画面。");
  }
  return {
    blob: await response.blob(),
    frameRevision: Number(response.headers.get("X-Atlas-Frame-Revision") ?? 0),
    pageRevision: Number(response.headers.get("X-Atlas-Page-Revision") ?? 0),
    capturedAt: new Date(
      response.headers.get("X-Atlas-Frame-Captured-At") ??
        new Date().toISOString()
    ),
    contentDigest: response.headers.get("Content-Digest") ?? ""
  };
}

export async function readDebugRun(
  debugRunId: string
): Promise<DebugRunDetailViewModel> {
  const response = await apiClient.GET("/v1/debug-runs/{runId}", {
    params: { path: { runId: debugRunId } }
  });
  if (response.error) {
    throw toApiError(response.error, "无法读取冻结的 DebugRun。");
  }
  return mapDebugRunDetail(
    requireData<DebugRunDto>(
      response.data,
      "Atlas API 未返回冻结的 DebugRun。"
    )
  );
}

export async function readDebugRunEvents(
  debugRunId: string
): Promise<DebugEventWindowViewModel> {
  const items: DebugRunEventDto[] = [];
  let afterSeq = 0;
  let truncated = false;

  while (items.length < MAX_DEBUG_EVENT_WINDOW) {
    const response = await apiClient.GET(
      "/v1/debug-runs/{runId}/events",
      {
        params: {
          path: { runId: debugRunId },
          query: {
            afterSeq,
            limit: Math.min(100, MAX_DEBUG_EVENT_WINDOW - items.length)
          }
        }
      }
    );
    if (response.error) {
      throw toApiError(response.error, "无法读取 DebugRun 单调事件。");
    }
    const page: DebugRunEventPageDto = requireData(
      response.data,
      "Atlas API 未返回 DebugRun 事件。"
    );
    items.push(...page.items);
    if (!page.nextAfterSeq) break;
    afterSeq = page.nextAfterSeq;
    if (items.length >= MAX_DEBUG_EVENT_WINDOW) truncated = true;
  }

  return mapDebugEventWindow(items, truncated);
}

export async function readDebugEvidence(
  debugRunId: string
): Promise<DebugEvidenceViewModel | null> {
  const response = await apiClient.GET(
    "/v1/debug-runs/{runId}/evidence",
    {
      params: { path: { runId: debugRunId } }
    }
  );
  if (response.error) {
    if (
      isApiProblem(response.error) &&
      [404, 409].includes(response.error.status)
    ) {
      return null;
    }
    throw toApiError(response.error, "无法读取 DebugRun EvidenceManifest。");
  }
  return mapDebugEvidence(
    requireData<EvidenceManifestDto>(
      response.data,
      "Atlas API 未返回 EvidenceManifest。"
    )
  );
}

export async function readEvidenceArtifact(
  debugRunId: string,
  artifactId: string,
  purpose: EvidenceReadPurpose
): Promise<Blob> {
  const grantResponse = await apiClient.POST(
    "/v1/debug-runs/{runId}/evidence/{artifactId}/read-tokens",
    {
      params: {
        path: { runId: debugRunId, artifactId }
      },
      body: { purpose }
    }
  );
  if (grantResponse.error) {
    throw toApiError(
      grantResponse.error,
      "无法签发 Evidence Read Grant。"
    );
  }
  const grant = requireData<EvidenceReadGrantDto>(
    grantResponse.data,
    "Atlas API 未返回 Evidence Read Grant。"
  );
  const response = await fetch(
    `${ATLAS_API_BASE_URL}/v1/evidence/artifacts/${encodeURIComponent(
      artifactId
    )}/content?purpose=${encodeURIComponent(purpose)}`,
    {
      credentials: "include",
      headers: {
        Accept: "application/octet-stream,image/png,image/jpeg,image/webp",
        Authorization: `Atlas-Evidence ${grant.readToken}`,
        "X-Request-ID": createRequestId()
      }
    }
  );
  if (!response.ok) {
    let error: unknown;
    try {
      error = await response.json();
    } catch {
      error = new Error(`Evidence content HTTP ${response.status}.`);
    }
    throw toApiError(error, "无法读取 Evidence Artifact。");
  }
  return response.blob();
}

export async function requestLiveControl(
  attemptId: string,
  controlEpoch: number,
  kind: LiveControlKind,
  command: RequestLiveControlCommand
): Promise<LiveControlCommandDto> {
  const clientMutationId = `${kind}-${createRequestId()}`;
  const params = {
    path: { attemptId },
    header: {
      "If-Match": `"control-epoch-${controlEpoch}"`,
      "Idempotency-Key": clientMutationId
    }
  };
  const response =
    kind === "takeover"
      ? await apiClient.POST("/v1/unit-attempts/{attemptId}/takeover", {
          params,
          body: command
        })
      : kind === "return"
        ? await apiClient.POST("/v1/unit-attempts/{attemptId}/return", {
            params,
            body: command
          })
        : kind === "pause"
          ? await apiClient.POST("/v1/unit-attempts/{attemptId}/pause", {
              params,
              body: command
            })
          : await apiClient.POST("/v1/unit-attempts/{attemptId}/resume", {
              params,
              body: command
            });

  if (response.error) {
    throw toApiError(response.error, `无法提交 Live ${kind} 命令。`);
  }
  return requireData(response.data, "Atlas API 未返回 Live Control Command。");
}
