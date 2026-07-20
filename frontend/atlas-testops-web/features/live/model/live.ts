import type { components } from "@/shared/api/schema";

export type UnitAttemptDto = components["schemas"]["UnitAttempt"];
export type UnitAttemptPageDto = components["schemas"]["UnitAttemptPage"];
export type UnitAttemptLiveSnapshotDto =
  components["schemas"]["UnitAttemptLiveSnapshot"];
export type RequestLiveControlCommand =
  components["schemas"]["RequestLiveControl"];
export type LiveControlCommandDto =
  components["schemas"]["LiveControlCommand"];
export type DebugLiveSnapshotDto =
  components["schemas"]["DebugLiveSnapshot"];
export type DebugLiveEventDto = components["schemas"]["DebugLiveEvent"];
export type DebugRunDto = components["schemas"]["DebugRun"];
export type DebugRunEventDto = components["schemas"]["DebugRunEvent"];
export type DebugRunEventPageDto =
  components["schemas"]["DebugRunEventPage"];
export type EvidenceManifestDto = components["schemas"]["EvidenceManifest"];
export type EvidenceReadGrantDto =
  components["schemas"]["EvidenceReadGrant"];
export type EvidenceReadPurpose =
  components["schemas"]["EvidenceReadPurpose"];

export type UnitAttemptViewModel = {
  id: string;
  attemptNumber: number;
  lifecycle: string;
  quality: string;
  hygiene: string;
  revision: number;
  executionDeadline: Date;
  queuedAt: Date;
  startedAt: Date | null;
  closedAt: Date | null;
  finalizedAt: Date | null;
  cleanupResolvedAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
};

export type LiveSnapshotViewModel = {
  session: {
    id: string;
    unitAttemptId: string;
    browserSessionId: string;
    state: string;
    controlEpoch: number;
    fencingToken: number;
    browserRevision: number;
    revision: number;
    humanInfluenced: boolean;
    updatedAt: Date;
  };
  lease: {
    ownerId: string;
    ownerType: string;
    state: string;
    expiresAt: Date;
  } | null;
  pendingCommand: {
    id: string;
    type: string;
    status: string;
    reason: string;
  } | null;
  observedAt: Date;
};

export type LiveControlKind = "takeover" | "return" | "pause" | "resume";

export type DebugLiveEventViewModel = {
  id: string;
  seq: number;
  type: string;
  lifecycle: string;
  outcome: string;
  snapshotStatus: string;
  data: DebugLiveEventDto["data"];
  occurredAt: Date;
  cursor: string;
};

export type DebugLiveSnapshotViewModel = {
  run: {
    id: string;
    projectId: string;
    testCaseId: string;
    environmentId: string;
    lifecycle: string;
    outcome: string;
    snapshotStatus: string;
    revision: number;
    executionDeadline: Date;
    startedAt: Date | null;
    completedAt: Date | null;
    cancelRequestedAt: Date | null;
  };
  cursor: string;
  latestEvent: DebugLiveEventViewModel | null;
  observedAt: Date;
};

export type DebugRunDetailViewModel = {
  id: string;
  testCaseId: string;
  environmentId: string;
  semanticRevision: number;
  lifecycle: string;
  outcome: string;
  snapshotStatus: string;
  revision: number;
  requestedAt: Date;
  startedAt: Date | null;
  completedAt: Date | null;
  executionDeadline: Date;
  planDigest: string;
  planNodes: Array<{
    id: string;
    kind: string;
    versionRef: string;
    executionLevel: number;
    title: string;
    description: string;
  }>;
  searchKeyword: string | null;
};

export type DebugLiveFrameViewModel = {
  blob: Blob;
  frameRevision: number;
  pageRevision: number;
  capturedAt: Date;
  contentDigest: string;
};

export type DebugEventWindowViewModel = {
  items: DebugLiveEventViewModel[];
  truncated: boolean;
};

export type EvidenceArtifactViewModel = {
  id: string;
  kind: string;
  mimeType: string;
  sizeBytes: number;
  integrity: string;
  required: boolean;
  capturedAt: Date;
  contentDigest: string;
};

export type DebugEvidenceViewModel = {
  id: string;
  outcome: string;
  completeness: string;
  integrity: string;
  eventCount: number;
  passedAssertions: number;
  failedAssertions: number;
  inconclusiveAssertions: number;
  artifacts: EvidenceArtifactViewModel[];
  assertions: Array<{
    id: string;
    nodeId: string;
    status: string;
    strength: string;
    summary: string;
    durationMs: number;
    evidenceRefs: string[];
  }>;
  finalizedAt: Date;
  contentDigest: string;
};

export type DebugLiveStreamStatus =
  | "disabled"
  | "connecting"
  | "live"
  | "reconnecting";
