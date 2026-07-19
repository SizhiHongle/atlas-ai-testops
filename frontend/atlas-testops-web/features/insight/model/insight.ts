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
    taskRunId: string;
    resultSnapshotId: string;
    taskPlanName: string;
    gateDecision: string;
    reasonCount: number;
    observedAt: Date;
  } | null;
  datasetCut: {
    asOf: Date;
    sourceSnapshotCount: number;
    gateDecisionCount: number;
    sourceSetDigest: string;
    queryHash: string;
    projectionWatermark: Date | null;
  };
  generatedAt: Date;
};
