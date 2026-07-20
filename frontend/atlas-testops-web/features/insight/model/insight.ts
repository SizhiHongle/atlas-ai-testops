import type { components } from "@/shared/api/schema";

export type InsightBriefDto = components["schemas"]["InsightBrief"];
export type InsightSnapshotDto = components["schemas"]["InsightSnapshot"];
export type RequestInsightSnapshotCommand =
  components["schemas"]["RequestInsightSnapshot"];

export type InsightMetricViewModel = {
  key: string;
  numerator: number;
  denominator: number;
  basisPoints: number | null;
  percentage: number | null;
  sampleStatus: string;
};

export type InsightBriefViewModel = {
  mode: "LIVE" | "PINNED";
  schemaVersion:
    | "atlas.insight-brief/0.1"
    | "atlas.insight-snapshot/0.1";
  metricPolicyVersion: "0.1.0";
  metricDefinitions: Array<{
    metricKey: string;
    aggregation: "RATIO_OF_SUMS";
    minimumSample: 30;
    version: "1.0.0";
  }>;
  snapshot: {
    id: string;
    snapshotHash: string;
    requestHash: string;
    createdAt: Date;
    createdBy: string;
  } | null;
  windowDays: 7 | 30 | 90;
  current: {
    startAt: Date;
    endAt: Date;
    taskRunCount: number;
    executionUnitCount: number;
    trustedPassRate: InsightMetricViewModel;
    autonomousTrustedPassRate: InsightMetricViewModel;
    methodHealthRate: InsightMetricViewModel;
  };
  baseline: {
    startAt: Date;
    endAt: Date;
    taskRunCount: number;
    executionUnitCount: number;
    trustedPassRate: InsightMetricViewModel;
    autonomousTrustedPassRate: InsightMetricViewModel;
    methodHealthRate: InsightMetricViewModel;
  };
  deltas: {
    trustedPassRate: number | null;
    autonomousTrustedPassRate: number | null;
    methodHealthRate: number | null;
  };
  terrain: Array<{
    taskPlanId: string;
    label: string;
    taskRunCount: number;
    executionUnitCount: number;
    trustedPassRate: InsightMetricViewModel;
    latestTaskRunId: string;
    latestResultSnapshotId: string;
  }>;
  activeRisk: {
    taskPlanId: string;
    taskRunId: string;
    resultSnapshotId: string;
    taskPlanName: string;
    gateDecision: string;
    reasonCount: number;
    observedAt: Date;
  } | null;
  datasetCut: {
    asOf: Date;
    sourceSnapshotIds: string[];
    sourceSnapshotHashes: string[];
    gateDecisionIds: string[];
    gateDecisionHashes: string[];
    sourceSnapshotCount: number;
    gateDecisionCount: number;
    sourceSetDigest: string;
    queryHash: string;
    authScopeHash: string;
    projectionWatermark: Date | null;
  };
  generatedAt: Date;
};
