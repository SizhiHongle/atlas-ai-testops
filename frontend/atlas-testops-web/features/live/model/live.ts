import type { components } from "@/shared/api/schema";

export type UnitAttemptDto = components["schemas"]["UnitAttempt"];
export type UnitAttemptPageDto = components["schemas"]["UnitAttemptPage"];
export type UnitAttemptLiveSnapshotDto =
  components["schemas"]["UnitAttemptLiveSnapshot"];
export type RequestLiveControlCommand =
  components["schemas"]["RequestLiveControl"];
export type LiveControlCommandDto =
  components["schemas"]["LiveControlCommand"];

export type UnitAttemptViewModel = {
  id: string;
  attemptNumber: number;
  lifecycle: string;
  quality: string;
  hygiene: string;
  revision: number;
  executionDeadline: Date;
  startedAt: Date | null;
  closedAt: Date | null;
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
