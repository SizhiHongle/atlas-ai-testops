import type { components } from "@/shared/api/schema";

export type TaskResultViewDto = components["schemas"]["TaskResultView"];
export type FailureClusterPageDto =
  components["schemas"]["FailureClusterPage"];
export type FailureClusterItemDto =
  components["schemas"]["FailureClusterItem"];
export type RequestFailureClassificationRevisionCommand =
  components["schemas"]["RequestFailureClassificationRevision"];
export type RequestTaskGateEvaluationCommand =
  components["schemas"]["RequestTaskGateEvaluation"];
export type FailureClassificationRevisionDto =
  components["schemas"]["FailureClassificationRevision"];
export type TaskGateDecisionDto = components["schemas"]["TaskGateDecision"];
export type FailureEvidenceRefDto =
  components["schemas"]["FailureEvidenceRef"];

export type RateViewModel = {
  numerator: number;
  denominator: number;
  percentage: number | null;
};

export type TaskResultViewModel = {
  taskRunId: string;
  snapshot: {
    id: string;
    revision: number;
    finality: string;
    aggregationPolicyVersion: string;
    manifestCount: number;
    unitResolutionRevisionIds: string[];
    verdicts: {
      passed: number;
      failed: number;
      inconclusive: number;
      notEvaluated: number;
    };
    rawPassRate: RateViewModel;
    trustedPassRate: RateViewModel;
    autonomousPassRate: RateViewModel;
    decisivePassRate: RateViewModel;
    axes: {
      outcomeClass: Record<string, number>;
      executionInfluence: Record<string, number>;
      stability: Record<string, number>;
      evidenceCompleteness: Record<string, number>;
      evidenceIntegrity: Record<string, number>;
      dataHygiene: Record<string, number>;
    };
    hash: string;
    createdAt: Date;
  };
  gate: {
    id: string;
    decision: string;
    revision: number;
    policyVersion: string;
    reasons: Array<{ code: string; count: number }>;
    evaluatedAt: Date;
  } | null;
  projectionWatermark: Date;
};

export type FailureClusterViewModel = {
  id: string;
  revisionId: string;
  revision: number;
  fingerprint: string;
  affectedCount: number;
  affectedUnitResolutionRevisionIds: string[];
  representativeUnitResolutionRevisionId: string;
  signal: {
    code: string;
    domain: string;
    verdict: string;
    outcomeClass: string;
    stability: string;
    closureReason: string;
    evidenceCompleteness: string;
    evidenceIntegrity: string;
    dataHygiene: string;
  };
  classification: {
    id: string;
    revision: number;
    domain: string;
    hypothesisCode: string;
    hypothesis: string;
    confidence: number;
    judgmentState: string;
    authorKind: string;
    modelVersionRef: string | null;
    supportingEvidenceRefs: FailureEvidenceRefDto[];
    contradictingEvidenceRefs: FailureEvidenceRefDto[];
    evidenceGapCodes: string[];
  } | null;
  createdAt: Date;
  projectionWatermark: Date;
};

export type FailureClusterPageViewModel = {
  items: FailureClusterViewModel[];
  nextCursor: string | null;
  asOf: Date;
  projectionWatermark: Date;
  resultSnapshotId: string;
};
