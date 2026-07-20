import type {
  InsightBriefDto,
  InsightBriefViewModel,
  InsightMetricViewModel,
  InsightSnapshotDto
} from "./insight";

function mapMetric(metric: {
  metricKey: string;
  numerator: number;
  denominator: number;
  basisPoints?: number | null;
  sampleStatus: string;
}): InsightMetricViewModel {
  return {
    key: metric.metricKey,
    numerator: metric.numerator,
    denominator: metric.denominator,
    basisPoints: metric.basisPoints ?? null,
    percentage:
      metric.basisPoints === null || metric.basisPoints === undefined
        ? null
        : metric.basisPoints / 100,
    sampleStatus: metric.sampleStatus
  };
}

function mapWindow(
  window: InsightBriefDto["current"] | InsightSnapshotDto["current"]
) {
  return {
    startAt: new Date(window.startAt),
    endAt: new Date(window.endAt),
    taskRunCount: window.taskRunCount,
    executionUnitCount: window.executionUnitCount,
    trustedPassRate: mapMetric(window.trustedPassRate),
    autonomousTrustedPassRate: mapMetric(
      window.autonomousTrustedPassRate
    ),
    methodHealthRate: mapMetric(window.methodHealthRate)
  };
}

function mapInsightDocument(
  dto: InsightBriefDto | InsightSnapshotDto,
  snapshot: InsightBriefViewModel["snapshot"]
): InsightBriefViewModel {
  return {
    mode: snapshot ? "PINNED" : "LIVE",
    schemaVersion: dto.schemaVersion,
    metricPolicyVersion: dto.metricPolicyVersion,
    metricDefinitions: dto.metricDefinitions.map((definition) => ({
      metricKey: definition.metricKey,
      aggregation: definition.aggregation,
      minimumSample: definition.minimumSample,
      version: definition.version
    })),
    snapshot,
    windowDays: dto.windowDays,
    current: mapWindow(dto.current),
    baseline: mapWindow(dto.baseline),
    deltas: {
      trustedPassRate: dto.deltas.trustedPassRate ?? null,
      autonomousTrustedPassRate:
        dto.deltas.autonomousTrustedPassRate ?? null,
      methodHealthRate: dto.deltas.methodHealthRate ?? null
    },
    terrain: dto.terrain.map((item) => ({
      taskPlanId: item.taskPlanId,
      label: item.label,
      taskRunCount: item.taskRunCount,
      executionUnitCount: item.executionUnitCount,
      trustedPassRate: mapMetric(item.trustedPassRate),
      latestTaskRunId: item.latestTaskRunId,
      latestResultSnapshotId: item.latestResultSnapshotId
    })),
    activeRisk: dto.activeRisk
      ? {
          taskPlanId: dto.activeRisk.taskPlanId,
          taskRunId: dto.activeRisk.taskRunId,
          resultSnapshotId: dto.activeRisk.resultSnapshotId,
          taskPlanName: dto.activeRisk.taskPlanName,
          gateDecision: dto.activeRisk.gateDecision,
          reasonCount: dto.activeRisk.reasonCount,
          observedAt: new Date(dto.activeRisk.observedAt)
        }
      : null,
    datasetCut: {
      asOf: new Date(dto.datasetCut.asOf),
      sourceSnapshotIds: dto.datasetCut.sourceSnapshotIds,
      sourceSnapshotHashes: dto.datasetCut.sourceSnapshotHashes,
      gateDecisionIds: dto.datasetCut.gateDecisionIds,
      gateDecisionHashes: dto.datasetCut.gateDecisionHashes,
      sourceSnapshotCount: dto.datasetCut.sourceSnapshotIds.length,
      gateDecisionCount: dto.datasetCut.gateDecisionIds.length,
      sourceSetDigest: dto.datasetCut.sourceSetDigest,
      queryHash: dto.datasetCut.queryHash,
      authScopeHash: dto.datasetCut.authScopeHash,
      projectionWatermark: dto.datasetCut.projectionWatermark
        ? new Date(dto.datasetCut.projectionWatermark)
        : null
    },
    generatedAt: new Date(dto.generatedAt)
  };
}

export function mapInsightBrief(
  dto: InsightBriefDto
): InsightBriefViewModel {
  return mapInsightDocument(dto, null);
}

export function mapInsightSnapshot(
  dto: InsightSnapshotDto
): InsightBriefViewModel {
  return mapInsightDocument(dto, {
    id: dto.id,
    snapshotHash: dto.snapshotHash,
    requestHash: dto.requestHash,
    createdAt: new Date(dto.createdAt),
    createdBy: dto.createdBy
  });
}
