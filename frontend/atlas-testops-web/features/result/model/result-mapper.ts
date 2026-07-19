import type {
  FailureClusterItemDto,
  FailureClusterPageDto,
  FailureClusterPageViewModel,
  FailureClusterViewModel,
  RateViewModel,
  TaskResultViewDto,
  TaskResultViewModel
} from "./result";

function mapRate(rate: {
  numerator: number;
  denominator: number;
}): RateViewModel {
  return {
    numerator: rate.numerator,
    denominator: rate.denominator,
    percentage:
      rate.denominator === 0
        ? null
        : Math.round((rate.numerator / rate.denominator) * 1000) / 10
  };
}

export function mapTaskResult(dto: TaskResultViewDto): TaskResultViewModel {
  const snapshot = dto.resultSnapshot;
  return {
    taskRunId: dto.taskRunId,
    snapshot: {
      id: snapshot.id,
      revision: snapshot.revision,
      finality: snapshot.finality,
      manifestCount: snapshot.manifestCount,
      verdicts: { ...snapshot.verdictCounts },
      rawPassRate: mapRate(snapshot.rawPassRate),
      trustedPassRate: mapRate(snapshot.trustedPassRate),
      autonomousPassRate: mapRate(snapshot.autonomousPassRate),
      decisivePassRate: mapRate(snapshot.decisivePassRate),
      axes: {
        outcomeClass: { ...snapshot.axisDistributions.outcomeClass },
        executionInfluence: {
          ...snapshot.axisDistributions.executionInfluence
        },
        stability: { ...snapshot.axisDistributions.stability },
        evidenceCompleteness: {
          ...snapshot.axisDistributions.evidenceCompleteness
        },
        evidenceIntegrity: {
          ...snapshot.axisDistributions.evidenceIntegrity
        },
        dataHygiene: { ...snapshot.axisDistributions.dataHygiene }
      },
      hash: snapshot.snapshotHash,
      createdAt: new Date(snapshot.createdAt)
    },
    gate: dto.taskGateDecision
      ? {
          id: dto.taskGateDecision.id,
          decision: dto.taskGateDecision.decision,
          revision: dto.taskGateDecision.revision,
          reasons: dto.taskGateDecision.reasons.map((reason) => ({
            code: reason.code,
            count: reason.count
          })),
          evaluatedAt: new Date(dto.taskGateDecision.evaluatedAt)
        }
      : null,
    projectionWatermark: new Date(dto.projectionWatermark)
  };
}

export function mapFailureCluster(
  dto: FailureClusterItemDto
): FailureClusterViewModel {
  const cluster = dto.cluster;
  const classification = dto.classification ?? null;
  return {
    id: cluster.failureClusterId,
    revisionId: cluster.id,
    revision: cluster.revision,
    fingerprint: cluster.fingerprint,
    affectedCount: cluster.affectedCount,
    signal: {
      code: cluster.signal.signalCode,
      domain: cluster.signal.failureDomain,
      verdict: cluster.signal.effectiveVerdict,
      outcomeClass: cluster.signal.outcomeClass,
      stability: cluster.signal.stability,
      closureReason: cluster.signal.closureReason
    },
    classification: classification
      ? {
          id: classification.failureClassificationId,
          revision: classification.revision,
          domain: classification.failureDomain,
          hypothesisCode: classification.hypothesisCode,
          hypothesis: classification.hypothesis,
          confidence:
            Math.round(
              (classification.confidence.numerator /
                classification.confidence.denominator) *
                1000
            ) / 10,
          judgmentState: classification.judgmentState,
          authorKind: classification.authorKind,
          supportingEvidenceRefs: [...classification.supportingEvidenceRefs],
          contradictingEvidenceRefs: [
            ...classification.contradictingEvidenceRefs
          ],
          evidenceGapCodes: [...classification.evidenceGapCodes]
        }
      : null,
    createdAt: new Date(cluster.createdAt)
  };
}

export function mapFailureClusterPage(
  dto: FailureClusterPageDto
): FailureClusterPageViewModel {
  return {
    items: dto.items.map(mapFailureCluster),
    nextCursor: dto.nextCursor ?? null,
    asOf: new Date(dto.asOf)
  };
}
