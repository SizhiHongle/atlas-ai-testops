import type { Page, Route } from "@playwright/test";

export const TENANT_ID = "11111111-1111-4111-8111-111111111111";
export const PROJECT_ID = "22222222-2222-4222-8222-222222222222";
export const USER_ID = "33333333-3333-4333-8333-333333333333";
export const TASK_PLAN_ID = "44444444-4444-4444-8444-444444444444";
export const TASK_PLAN_VERSION_ID =
  "55555555-5555-4555-8555-555555555555";
export const TASK_RUN_ID = "66666666-6666-4666-8666-666666666666";
export const CLOSED_RUN_ID = "67676767-6767-4767-8767-676767676767";
export const PASSED_RUN_ID = "69696969-6969-4969-8969-696969696969";
export const RUNNING_UNIT_ID = "77777777-7777-4777-8777-777777777777";
export const CLOSED_UNIT_ID = "88888888-8888-4888-8888-888888888888";
export const ATTEMPT_ID = "99999999-9999-4999-8999-999999999999";
export const CASE_ID = "15151515-1515-4515-8515-151515151515";
export const DRAFT_ID = "16161616-1616-4616-8616-161616161616";
export const ENVIRONMENT_ID = "17171717-1717-4717-8717-171717171717";
export const DEBUG_RUN_ID = "18181818-1818-4818-8818-181818181818";
export const CASE_VERSION_ID = "19191919-1919-4919-8919-191919191919";
export const STARTED_DEBUG_RUN_ID =
  "21212121-2121-4121-8121-212121212121";
export const SCREENSHOT_ARTIFACT_ID =
  "23232323-2323-4323-8323-232323232323";
const NETWORK_ARTIFACT_ID = "24242424-2424-4424-8424-242424242424";
const CONSOLE_ARTIFACT_ID = "25252525-2525-4525-8525-252525252525";
const EVIDENCE_MANIFEST_ID = "26262626-2626-4626-8626-262626262626";
const EXECUTION_CONTRACT_ID = "27272727-2727-4727-8727-272727272727";
const FIXTURE_RUN_ID = "28282828-2828-4828-8828-282828282828";

const CREATED_AT = "2026-07-19T08:00:00.000Z";
const UPDATED_AT = "2026-07-19T08:05:00.000Z";
const SHA_A = `sha256:${"a".repeat(64)}`;
const SHA_B = `sha256:${"b".repeat(64)}`;
const RETRY_SHA =
  "sha256:ba357ee61f53d177233ffc331445fbd46e551997dd6aa3b22b09883c8ff49e27";

const session = {
  authenticationMethod: "PASSWORD",
  expiresAt: "2026-07-20T08:00:00.000Z",
  project: {
    id: PROJECT_ID,
    tenantId: TENANT_ID,
    projectKey: "CRM",
    name: "客户运营",
    status: "ACTIVE",
    revision: 1,
    createdAt: CREATED_AT,
    updatedAt: UPDATED_AT
  },
  roles: ["PROJECT_ADMIN", "RUN_OPERATOR", "CASE_REVIEWER"],
  tenant: {
    id: TENANT_ID,
    slug: "atlas",
    name: "Atlas",
    status: "ACTIVE",
    revision: 1,
    createdAt: CREATED_AT,
    updatedAt: UPDATED_AT
  },
  user: {
    id: USER_ID,
    displayName: "陈航",
    email: "chen.hang@example.com",
    status: "ACTIVE",
    revision: 1,
    createdAt: CREATED_AT,
    updatedAt: UPDATED_AT
  }
};

const taskPlan = {
  id: TASK_PLAN_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  taskKey: "nightly.crm",
  name: "夜间全量回归",
  status: "ACTIVE",
  createdBy: USER_ID,
  revision: 3,
  createdAt: CREATED_AT,
  updatedAt: UPDATED_AT
};

const taskPlanVersion = {
  id: TASK_PLAN_VERSION_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  taskPlanId: TASK_PLAN_ID,
  version: "1.4.0",
  versionRef: "nightly.crm@1.4.0",
  pinnedCaseVersionIds: [CASE_VERSION_ID],
  matrix: {
    environmentIds: ["cccccccc-cccc-4ccc-8ccc-cccccccccccc"],
    browserProfileVersionIds: [
      "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
      "abababab-abab-4bab-8bab-abababababab"
    ],
    identityProfileVersionIds: ["eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"],
    dataProfileVersionIds: ["ffffffff-ffff-4fff-8fff-ffffffffffff"]
  },
  profileRefs: {},
  policyDigests: {
    "infra-retry": RETRY_SHA
  },
  contentDigest: SHA_A,
  publishedBy: USER_ID,
  publishedAt: "2026-07-19T07:50:00.000Z",
  revision: 1,
  createdAt: CREATED_AT,
  updatedAt: UPDATED_AT
};

const taskRun = {
  id: TASK_RUN_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  taskPlanVersionId: TASK_PLAN_VERSION_ID,
  manifestHash: SHA_A,
  triggerSource: "MANUAL",
  triggerFingerprint: "manual:e2e-production",
  lifecycle: "RUNNING",
  quality: "PENDING",
  hygiene: "PENDING",
  materializationState: "SEALED",
  materializedUnitCount: 2,
  materializedFirstAttemptCount: 2,
  materializationSealedAt: "2026-07-19T08:01:00.000Z",
  requestDigest: SHA_B,
  temporalNamespace: "atlas-production",
  temporalWorkflowId: `atlas-task/${TENANT_ID}/${TASK_RUN_ID}`,
  requestedBy: USER_ID,
  requestedAt: CREATED_AT,
  queuedAt: CREATED_AT,
  startedAt: "2026-07-19T08:02:00.000Z",
  closedAt: null,
  finalizedAt: null,
  cleanupResolvedAt: null,
  rerunOfTaskRunId: null,
  rerunSelectionMode: null,
  revision: 4,
  createdAt: CREATED_AT,
  updatedAt: UPDATED_AT
};

const taskRuns = [
  taskRun,
  {
    ...taskRun,
    id: CLOSED_RUN_ID,
    triggerSource: "CI",
    triggerFingerprint: "ci:e2e-production:816",
    lifecycle: "CLOSED",
    quality: "FAILED",
    hygiene: "CLEANED",
    materializedUnitCount: 6,
    requestedAt: "2026-07-19T07:30:00.000Z",
    startedAt: "2026-07-19T07:31:00.000Z",
    closedAt: "2026-07-19T07:44:00.000Z",
    revision: 7
  },
  {
    ...taskRun,
    id: "68686868-6868-4868-8868-686868686868",
    triggerSource: "SCHEDULE",
    triggerFingerprint: "schedule:e2e-production:nightly",
    lifecycle: "QUEUED",
    quality: "PENDING",
    hygiene: "PENDING",
    materializedUnitCount: 4,
    requestedAt: "2026-07-19T07:10:00.000Z",
    startedAt: null,
    closedAt: null,
    revision: 2
  },
  {
    ...taskRun,
    id: PASSED_RUN_ID,
    triggerSource: "MANUAL",
    triggerFingerprint: "manual:e2e-production:passed",
    lifecycle: "CLOSED",
    quality: "PASSED",
    hygiene: "CLEANED",
    materializedUnitCount: 3,
    requestedAt: "2026-07-19T06:20:00.000Z",
    startedAt: "2026-07-19T06:21:00.000Z",
    closedAt: "2026-07-19T06:29:00.000Z",
    revision: 5
  }
];

export const FAILED_RESULT_SNAPSHOT_ID =
  "80808080-8080-4080-8080-808080808080";
export const PASSED_RESULT_SNAPSHOT_ID =
  "81818181-8181-4181-8181-818181818181";
export const INSIGHT_SNAPSHOT_ID =
  "8a8a8a8a-8a8a-4a8a-8a8a-8a8a8a8a8a8a";
export const PRODUCT_CLUSTER_ID =
  "82828282-8282-4282-8282-828282828282";
export const AUTOMATION_CLUSTER_ID =
  "83838383-8383-4383-8383-838383838383";
export const INFRA_CLUSTER_ID =
  "84848484-8484-4484-8484-848484848484";
export const PRODUCT_CLASSIFICATION_ID =
  "85858585-8585-4585-8585-858585858585";

const RESULT_POLICY_SHA = `sha256:${"c".repeat(64)}`;
const RESULT_SNAPSHOT_SHA = `sha256:${"d".repeat(64)}`;
const CLASSIFICATION_SET_SHA = `sha256:${"e".repeat(64)}`;
const RESULT_GATE_SHA = `sha256:${"f".repeat(64)}`;

const resultResolutionIds = Array.from(
  { length: 6 },
  (_, index) =>
    `90909090-9090-4090-8090-${String(index + 1).padStart(12, "0")}`
);
const passedResolutionIds = Array.from(
  { length: 3 },
  (_, index) =>
    `91919191-9191-4191-8191-${String(index + 1).padStart(12, "0")}`
);

const failedResultSnapshot = {
  id: FAILED_RESULT_SNAPSHOT_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  taskRunId: CLOSED_RUN_ID,
  manifestHash: SHA_A,
  revision: 3,
  unitResolutionRevisionIds: resultResolutionIds,
  inputResolutionSetHash: SHA_B,
  inputHygieneResolutionSetHash: SHA_A,
  aggregationPolicyDigest: RESULT_POLICY_SHA,
  aggregationPolicyVersion: "0.1.0",
  finality: "FULLY_RESOLVED",
  schemaVersion: "atlas.task-result-snapshot/0.1",
  projectionWatermark: "2026-07-19T07:46:00.000Z",
  manifestCount: 6,
  verdictCounts: {
    passed: 3,
    failed: 1,
    inconclusive: 1,
    notEvaluated: 1
  },
  axisDistributions: {
    outcomeClass: {
      business: 3,
      dependency: 0,
      platform: 1,
      user: 0,
      automation: 1,
      policy: 0,
      unknown: 1
    },
    executionInfluence: {
      autonomous: 5,
      manualAssisted: 1,
      manualOnly: 0
    },
    stability: {
      unknown: 1,
      stable: 3,
      infraRecovered: 0,
      flakySuspect: 2,
      flakyConfirmed: 0
    },
    evidenceCompleteness: {
      pending: 0,
      complete: 4,
      partial: 1,
      missing: 1,
      notApplicable: 0
    },
    evidenceIntegrity: {
      unverified: 1,
      verified: 5,
      invalid: 0
    },
    dataHygiene: {
      pending: 0,
      cleaned: 5,
      cleanupFailed: 1,
      leaked: 0,
      notApplicable: 0
    }
  },
  rawPassRate: { numerator: 3, denominator: 6 },
  trustedPassRate: { numerator: 3, denominator: 6 },
  autonomousPassRate: { numerator: 3, denominator: 5 },
  decisivePassRate: { numerator: 3, denominator: 4 },
  unitHygieneResolutionRevisionIds: resultResolutionIds,
  reevaluationCommandId: null,
  reevaluationSourceSnapshotId: null,
  supersedesSnapshotId: null,
  createdAt: "2026-07-19T07:46:00.000Z",
  snapshotHash: RESULT_SNAPSHOT_SHA
};

const passedResultSnapshot = {
  ...failedResultSnapshot,
  id: PASSED_RESULT_SNAPSHOT_ID,
  taskRunId: PASSED_RUN_ID,
  revision: 1,
  unitResolutionRevisionIds: passedResolutionIds,
  manifestCount: 3,
  verdictCounts: {
    passed: 3,
    failed: 0,
    inconclusive: 0,
    notEvaluated: 0
  },
  axisDistributions: {
    outcomeClass: {
      business: 3,
      dependency: 0,
      platform: 0,
      user: 0,
      automation: 0,
      policy: 0,
      unknown: 0
    },
    executionInfluence: {
      autonomous: 3,
      manualAssisted: 0,
      manualOnly: 0
    },
    stability: {
      unknown: 0,
      stable: 3,
      infraRecovered: 0,
      flakySuspect: 0,
      flakyConfirmed: 0
    },
    evidenceCompleteness: {
      pending: 0,
      complete: 3,
      partial: 0,
      missing: 0,
      notApplicable: 0
    },
    evidenceIntegrity: {
      unverified: 0,
      verified: 3,
      invalid: 0
    },
    dataHygiene: {
      pending: 0,
      cleaned: 3,
      cleanupFailed: 0,
      leaked: 0,
      notApplicable: 0
    }
  },
  rawPassRate: { numerator: 3, denominator: 3 },
  trustedPassRate: { numerator: 3, denominator: 3 },
  autonomousPassRate: { numerator: 3, denominator: 3 },
  decisivePassRate: { numerator: 3, denominator: 3 },
  unitHygieneResolutionRevisionIds: passedResolutionIds,
  projectionWatermark: "2026-07-19T06:31:00.000Z",
  createdAt: "2026-07-19T06:31:00.000Z",
  snapshotHash: SHA_B
};

function failureClassification(
  classificationId: string,
  clusterRevisionId: string,
  domain: string,
  hypothesisCode: string,
  hypothesis: string,
  confidence: number,
  evidenceRefId: string
) {
  return {
    id: `92929292-9292-4292-8292-${classificationId.slice(-12)}`,
    tenantId: TENANT_ID,
    projectId: PROJECT_ID,
    taskRunId: CLOSED_RUN_ID,
    resultSnapshotId: FAILED_RESULT_SNAPSHOT_ID,
    failureClusterRevisionId: clusterRevisionId,
    failureClassificationId: classificationId,
    revision: 1,
    supersedesRevisionId: null as string | null,
    failureDomain: domain,
    hypothesisCode,
    hypothesis,
    confidence: { numerator: confidence * 100, denominator: 10000 },
    supportingEvidenceRefs: [
      {
        kind: "UNIT_RESOLUTION",
        refId: evidenceRefId,
        contentDigest: SHA_A
      },
      {
        kind: "ATTEMPT_SEAL",
        refId: `93939393-9393-4393-8393-${classificationId.slice(-12)}`,
        contentDigest: SHA_B
      }
    ],
    contradictingEvidenceRefs: [],
    evidenceGapCodes: [],
    judgmentState: "RULE_PROPOSED",
    authorKind: "SYSTEM_RULE",
    authoredBy: null as string | null,
    modelVersionRef: null as string | null,
    classificationPolicyVersion: "0.1.0",
    classificationPolicyDigest: RESULT_POLICY_SHA,
    clientMutationId: `fixture-classification-${classificationId}`,
    schemaVersion: "atlas.failure-classification-revision/0.1",
    createdAt: "2026-07-19T07:46:30.000Z",
    classificationHash: SHA_A
  };
}

function failureCluster(
  clusterId: string,
  revisionId: string,
  affectedResolutionIds: string[],
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
  },
  fingerprint: string,
  classificationId: string,
  hypothesisCode: string,
  hypothesis: string,
  confidence: number
) {
  return {
    cluster: {
      id: revisionId,
      tenantId: TENANT_ID,
      projectId: PROJECT_ID,
      taskRunId: CLOSED_RUN_ID,
      resultSnapshotId: FAILED_RESULT_SNAPSHOT_ID,
      failureClusterId: clusterId,
      revision: 1,
      affectedUnitResolutionRevisionIds: affectedResolutionIds,
      representativeUnitResolutionRevisionId: affectedResolutionIds[0],
      affectedCount: affectedResolutionIds.length,
      signal: {
        schemaVersion: "atlas.failure-signal/0.1",
        signalCode: signal.code,
        failureDomain: signal.domain,
        effectiveVerdict: signal.verdict,
        outcomeClass: signal.outcomeClass,
        stability: signal.stability,
        closureReason: signal.closureReason,
        evidenceCompleteness: signal.evidenceCompleteness,
        evidenceIntegrity: signal.evidenceIntegrity,
        dataHygiene: signal.dataHygiene
      },
      fingerprint,
      fingerprintVersion: "0.1.0",
      fingerprintPolicyDigest: RESULT_POLICY_SHA,
      projectionWatermark: "2026-07-19T07:46:20.000Z",
      supersedesClusterRevisionId: null,
      schemaVersion: "atlas.failure-cluster-revision/0.1",
      createdAt: "2026-07-19T07:46:20.000Z",
      clusterHash: SHA_B
    },
    classification: failureClassification(
      classificationId,
      revisionId,
      signal.domain,
      hypothesisCode,
      hypothesis,
      confidence,
      affectedResolutionIds[0]
    )
  };
}

const productCluster = failureCluster(
  PRODUCT_CLUSTER_ID,
  "94949494-9494-4494-8494-949494949494",
  [resultResolutionIds[0]],
  {
    code: "PRODUCT_ASSERTION_FAILED",
    domain: "PRODUCT",
    verdict: "FAILED",
    outcomeClass: "BUSINESS",
    stability: "STABLE",
    closureReason: "HARD_ORACLE_FAILED",
    evidenceCompleteness: "COMPLETE",
    evidenceIntegrity: "VERIFIED",
    dataHygiene: "CLEANED"
  },
  "product:customer-filter:assertion",
  PRODUCT_CLASSIFICATION_ID,
  "PRODUCT_BEHAVIOR_MISMATCH",
  "客户筛选结果与已封存的业务 Oracle 不一致。",
  86
);

const automationCluster = failureCluster(
  AUTOMATION_CLUSTER_ID,
  "95959595-9595-4595-8595-959595959595",
  [resultResolutionIds[1]],
  {
    code: "SELECTOR_STABILITY_UNCERTAIN",
    domain: "AGENT_AUTOMATION",
    verdict: "INCONCLUSIVE",
    outcomeClass: "AUTOMATION",
    stability: "FLAKY_SUSPECT",
    closureReason: "EVIDENCE_PARTIAL",
    evidenceCompleteness: "PARTIAL",
    evidenceIntegrity: "VERIFIED",
    dataHygiene: "CLEANED"
  },
  "automation:customer-grid:selector",
  "86868686-8686-4686-8686-868686868686",
  "SELECTOR_STABILITY_GAP",
  "客户列表选择器在一次执行中失去稳定锚点，需要人工复核证据。",
  62
);

const infraCluster = failureCluster(
  INFRA_CLUSTER_ID,
  "96969696-9696-4696-8696-969696969696",
  [resultResolutionIds[2]],
  {
    code: "BROWSER_SESSION_INTERRUPTED",
    domain: "INFRASTRUCTURE",
    verdict: "NOT_EVALUATED",
    outcomeClass: "PLATFORM",
    stability: "FLAKY_SUSPECT",
    closureReason: "BROWSER_DISCONNECTED",
    evidenceCompleteness: "MISSING",
    evidenceIntegrity: "UNVERIFIED",
    dataHygiene: "CLEANUP_FAILED"
  },
  "infra:browser-session:disconnect",
  "87878787-8787-4787-8787-878787878787",
  "INFRA_SESSION_INTERRUPTION",
  "浏览器会话中断，现有证据不足以形成业务结论。",
  94
);

const failedGateDecision = {
  id: "97979797-9797-4797-8797-979797979797",
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  taskGateId: "98989898-9898-4898-8898-989898989898",
  taskRunId: CLOSED_RUN_ID,
  resultSnapshotId: FAILED_RESULT_SNAPSHOT_ID,
  revision: 1,
  supersedesGateDecisionId: null as string | null,
  decision: "REJECTED",
  reasons: [
    { code: "FAILED_UNITS", count: 1 },
    { code: "EVIDENCE_INCOMPLETE", count: 2 }
  ],
  resultSnapshotHash: RESULT_SNAPSHOT_SHA,
  failureClassificationRevisionIds: [
    productCluster.classification.id,
    automationCluster.classification.id,
    infraCluster.classification.id
  ],
  classificationSetHash: CLASSIFICATION_SET_SHA,
  gatePolicyVersion: "0.1.0",
  gatePolicyDigest: RESULT_POLICY_SHA,
  evaluatedBy: USER_ID,
  clientMutationId: "fixture-gate-evaluation",
  evaluatedAt: "2026-07-19T07:47:00.000Z",
  schemaVersion: "atlas.task-gate-decision/0.1",
  decisionHash: RESULT_GATE_SHA
};

const passedGateDecision = {
  ...failedGateDecision,
  id: "99999999-9999-4999-8999-999999999998",
  taskGateId: "99999999-9999-4999-8999-999999999997",
  taskRunId: PASSED_RUN_ID,
  resultSnapshotId: PASSED_RESULT_SNAPSHOT_ID,
  decision: "ACCEPTED",
  reasons: [],
  resultSnapshotHash: SHA_B,
  failureClassificationRevisionIds: [],
  evaluatedAt: "2026-07-19T06:32:00.000Z"
};

const INSIGHT_RISK_TASK_PLAN_ID =
  "8b8b8b8b-8b8b-4b8b-8b8b-8b8b8b8b8b8b";
const INSIGHT_TERRAIN_RUN_IDS = [
  PASSED_RUN_ID,
  CLOSED_RUN_ID,
  "8c8c8c8c-8c8c-4c8c-8c8c-8c8c8c8c8c8c",
  "8d8d8d8d-8d8d-4d8d-8d8d-8d8d8d8d8d8d"
];
const INSIGHT_TERRAIN_PLAN_IDS = [
  TASK_PLAN_ID,
  INSIGHT_RISK_TASK_PLAN_ID,
  "8e8e8e8e-8e8e-4e8e-8e8e-8e8e8e8e8e8e",
  "8f8f8f8f-8f8f-4f8f-8f8f-8f8f8f8f8f8f"
];
const INSIGHT_SOURCE_SNAPSHOT_IDS = [
  FAILED_RESULT_SNAPSHOT_ID,
  PASSED_RESULT_SNAPSHOT_ID,
  "aaaa0001-aaaa-4aaa-8aaa-aaaaaaaa0001",
  "aaaa0002-aaaa-4aaa-8aaa-aaaaaaaa0002",
  "aaaa0003-aaaa-4aaa-8aaa-aaaaaaaa0003",
  "aaaa0004-aaaa-4aaa-8aaa-aaaaaaaa0004"
];

function insightMetric(
  metricKey:
    | "quality.trusted_pass_rate"
    | "quality.autonomous_trusted_pass_rate"
    | "quality.method_health_rate",
  numerator: number,
  denominator: number,
  basisPoints: number | null,
  sampleStatus: "NO_DATA" | "LOW_SAMPLE" | "ENOUGH" = "ENOUGH"
) {
  return {
    metricKey,
    numerator,
    denominator,
    basisPoints,
    sampleStatus,
    metricVersion: "1.0.0"
  };
}

export function insightBriefForWindow(windowDays: 7 | 30 | 90) {
  const currentStarts = {
    7: "2026-07-12T08:00:00.000Z",
    30: "2026-06-19T08:00:00.000Z",
    90: "2026-04-20T08:00:00.000Z"
  } as const;
  const baselineStarts = {
    7: "2026-07-05T08:00:00.000Z",
    30: "2026-05-20T08:00:00.000Z",
    90: "2026-01-20T08:00:00.000Z"
  } as const;
  const trusted = insightMetric(
    "quality.trusted_pass_rate",
    1243,
    1284,
    9681
  );
  const autonomous = insightMetric(
    "quality.autonomous_trusted_pass_rate",
    1037,
    1100,
    9427
  );
  const method = insightMetric(
    "quality.method_health_rate",
    1207,
    1284,
    9400
  );

  return {
    tenantId: TENANT_ID,
    projectId: PROJECT_ID,
    windowDays,
    metricPolicyVersion: "0.1.0",
    schemaVersion: "atlas.insight-brief/0.1",
    metricDefinitions: [
      {
        metricKey: "quality.trusted_pass_rate",
        aggregation: "RATIO_OF_SUMS",
        eventTime: "QUALITY_FINALIZED_AT",
        grain: "UNIT",
        minimumSample: 30,
        population: "MANIFEST_UNITS",
        sourceFinality: "FULLY_RESOLVED_OR_REEVALUATED",
        version: "1.0.0"
      },
      {
        metricKey: "quality.autonomous_trusted_pass_rate",
        aggregation: "RATIO_OF_SUMS",
        eventTime: "QUALITY_FINALIZED_AT",
        grain: "UNIT",
        minimumSample: 30,
        population: "MANIFEST_UNITS",
        sourceFinality: "FULLY_RESOLVED_OR_REEVALUATED",
        version: "1.0.0"
      },
      {
        metricKey: "quality.method_health_rate",
        aggregation: "RATIO_OF_SUMS",
        eventTime: "QUALITY_FINALIZED_AT",
        grain: "UNIT",
        minimumSample: 30,
        population: "MANIFEST_UNITS",
        sourceFinality: "FULLY_RESOLVED_OR_REEVALUATED",
        version: "1.0.0"
      }
    ],
    current: {
      startAt: currentStarts[windowDays],
      endAt: "2026-07-19T08:00:00.000Z",
      taskRunCount: 36,
      executionUnitCount: 1284,
      trustedPassRate: trusted,
      autonomousTrustedPassRate: autonomous,
      methodHealthRate: method
    },
    baseline: {
      startAt: baselineStarts[windowDays],
      endAt: currentStarts[windowDays],
      taskRunCount: 33,
      executionUnitCount: 1180,
      trustedPassRate: insightMetric(
        "quality.trusted_pass_rate",
        1126,
        1180,
        9541
      ),
      autonomousTrustedPassRate: insightMetric(
        "quality.autonomous_trusted_pass_rate",
        981,
        1050,
        9347
      ),
      methodHealthRate: insightMetric(
        "quality.method_health_rate",
        1122,
        1180,
        9508
      )
    },
    deltas: {
      trustedPassRate: 140,
      autonomousTrustedPassRate: 80,
      methodHealthRate: -108
    },
    terrain: [
      {
        taskPlanId: INSIGHT_TERRAIN_PLAN_IDS[0],
        label: "客户筛选",
        taskRunCount: 10,
        executionUnitCount: 360,
        trustedPassRate: insightMetric(
          "quality.trusted_pass_rate",
          352,
          360,
          9778
        ),
        latestTaskRunId: INSIGHT_TERRAIN_RUN_IDS[0],
        latestResultSnapshotId: PASSED_RESULT_SNAPSHOT_ID
      },
      {
        taskPlanId: INSIGHT_TERRAIN_PLAN_IDS[1],
        label: "权限边界",
        taskRunCount: 8,
        executionUnitCount: 290,
        trustedPassRate: insightMetric(
          "quality.trusted_pass_rate",
          268,
          290,
          9241
        ),
        latestTaskRunId: INSIGHT_TERRAIN_RUN_IDS[1],
        latestResultSnapshotId: FAILED_RESULT_SNAPSHOT_ID
      },
      {
        taskPlanId: INSIGHT_TERRAIN_PLAN_IDS[2],
        label: "来访关系",
        taskRunCount: 9,
        executionUnitCount: 324,
        trustedPassRate: insightMetric(
          "quality.trusted_pass_rate",
          321,
          324,
          9907
        ),
        latestTaskRunId: INSIGHT_TERRAIN_RUN_IDS[2],
        latestResultSnapshotId: INSIGHT_SOURCE_SNAPSHOT_IDS[2]
      },
      {
        taskPlanId: INSIGHT_TERRAIN_PLAN_IDS[3],
        label: "身份租约",
        taskRunCount: 9,
        executionUnitCount: 310,
        trustedPassRate: insightMetric(
          "quality.trusted_pass_rate",
          299,
          310,
          9645
        ),
        latestTaskRunId: INSIGHT_TERRAIN_RUN_IDS[3],
        latestResultSnapshotId: INSIGHT_SOURCE_SNAPSHOT_IDS[3]
      }
    ],
    activeRisk: {
      taskPlanId: INSIGHT_RISK_TASK_PLAN_ID,
      taskRunId: CLOSED_RUN_ID,
      resultSnapshotId: FAILED_RESULT_SNAPSHOT_ID,
      taskPlanName: "客户权限发布门禁",
      gateDecision: "REJECTED",
      reasonCount: 2,
      observedAt: "2026-07-19T07:47:00.000Z"
    },
    datasetCut: {
      schemaVersion: "atlas.insight-dataset-cut/0.1",
      asOf: "2026-07-19T08:00:00.000Z",
      sourceSnapshotIds: INSIGHT_SOURCE_SNAPSHOT_IDS,
      sourceSnapshotHashes: INSIGHT_SOURCE_SNAPSHOT_IDS.map(
        (_, index) => `sha256:${String(index + 1).repeat(64)}`
      ),
      gateDecisionIds: [failedGateDecision.id, passedGateDecision.id],
      gateDecisionHashes: [
        failedGateDecision.decisionHash,
        passedGateDecision.decisionHash
      ],
      sourceSetDigest: `sha256:${"6".repeat(64)}`,
      queryHash: `sha256:${"7".repeat(64)}`,
      authScopeHash: `sha256:${"8".repeat(64)}`,
      projectionWatermark: "2026-07-19T07:59:00.000Z"
    },
    generatedAt: "2026-07-19T08:00:10.000Z"
  };
}

function insightSnapshotForWindow(
  windowDays: 7 | 30 | 90,
  clientMutationId = "fixture-insight-pin"
) {
  return {
    ...insightBriefForWindow(windowDays),
    id: INSIGHT_SNAPSHOT_ID,
    schemaVersion: "atlas.insight-snapshot/0.1",
    clientMutationId,
    createdBy: USER_ID,
    createdAt: "2026-07-19T08:00:20.000Z",
    requestHash: `sha256:${"9".repeat(64)}`,
    snapshotHash: `sha256:${"a".repeat(64)}`
  };
}

const taskSchedule = {
  id: "70707070-7070-4070-8070-707070707070",
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  taskPlanVersionId: TASK_PLAN_VERSION_ID,
  scheduleKey: "nightly.crm.daily",
  name: "夜间全量回归每日调度",
  calendar: {
    schemaVersion: "atlas.task-schedule-calendar/0.1",
    minutes: [30],
    hours: [21],
    daysOfMonth: [],
    months: [],
    isoDaysOfWeek: []
  },
  timeZoneName: "Asia/Shanghai",
  overlapPolicy: "QUEUE_ONE",
  catchupPolicy: "RUN_ONCE",
  catchupWindowSeconds: 3600,
  jitterSeconds: 0,
  iterationId: null,
  retryPolicy: {
    schemaVersion: "atlas.task-retry-policy/0.1",
    infraRetryAttempts: 1,
    maxTotalInfraRetries: 8,
    initialBackoffSeconds: 2,
    maximumBackoffSeconds: 30,
    jitterPercent: 10,
    contentDigest: RETRY_SHA
  },
  status: "ACTIVE",
  syncStatus: "SYNCED",
  nextFireTimesUtc: [
    "2026-07-20T13:30:00.000Z",
    "2026-07-21T13:30:00.000Z"
  ],
  temporalNamespace: "atlas-production",
  temporalScheduleId: "atlas-task/schedule/e2e",
  contentDigest: SHA_A,
  createdBy: USER_ID,
  updatedBy: USER_ID,
  pauseReason: null,
  lastSyncErrorCode: null,
  syncedRevision: 1,
  revision: 1,
  createdAt: CREATED_AT,
  updatedAt: UPDATED_AT
};

function executionUnit(
  id: string,
  ordinal: number,
  lifecycle: string,
  quality: string
) {
  return {
    id,
    tenantId: TENANT_ID,
    projectId: PROJECT_ID,
    taskRunId: TASK_RUN_ID,
    manifestHash: SHA_A,
    ordinal,
    unitKey: SHA_B,
    caseVersionId: CASE_VERSION_ID,
    executionProfileVersionId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    fixtureBlueprintVersionId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    identityProfileVersionId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    environmentId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    browserProfileVersionId: "ffffffff-ffff-4fff-8fff-ffffffffffff",
    dataProfileVersionId: "12121212-1212-4212-8212-121212121212",
    parameterDigest: SHA_A,
    dependencyDigest: SHA_B,
    lifecycle,
    quality,
    hygiene: lifecycle === "CLOSED" ? "CLEANED" : "PENDING",
    revision: 2,
    startedAt: "2026-07-19T08:02:30.000Z",
    closedAt: lifecycle === "CLOSED" ? UPDATED_AT : null,
    finalizedAt: lifecycle === "CLOSED" ? UPDATED_AT : null,
    cleanupResolvedAt: lifecycle === "CLOSED" ? UPDATED_AT : null,
    createdAt: CREATED_AT,
    updatedAt: UPDATED_AT
  };
}

const units = [
  executionUnit(RUNNING_UNIT_ID, 1, "RUNNING", "PENDING"),
  executionUnit(CLOSED_UNIT_ID, 2, "CLOSED", "PASSED")
];

const environment = {
  id: ENVIRONMENT_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  environmentKey: "crm-test",
  name: "CRM 测试环境",
  kind: "TEST",
  status: "ACTIVE",
  allowedOrigins: ["https://crm.example.test"],
  revision: 1,
  createdAt: CREATED_AT,
  updatedAt: UPDATED_AT
};

const testIntent = {
  schemaVersion: "atlas.test-intent/0.1",
  summary: "创建客户并通过销售身份筛选，验证客户出现在结果列表。",
  actors: [
    {
      actorSlot: "primary",
      roleId: "20202020-2020-4020-8020-202020202020",
      roleKey: "sales",
      roleRevision: 2,
      capabilities: ["crm.customer.read"]
    }
  ],
  surfaces: [],
  requiredFeatures: [],
  requirementRefs: [],
  evidencePolicy: {
    retainFailureDays: 30,
    retainSuccessDays: 7,
    screenshots: "critical-actions",
    trace: true
  },
  outcomePolicy: {
    agentMayDecidePass: false,
    evidenceIncompleteBlocksPass: true,
    requireHardOracle: true
  },
  recoveryPolicy: {
    maxUnitAttempts: 1,
    retryBrowserCrash: false,
    retryUnknownSideEffect: false
  },
  variables: {}
};

const workflowNodes = [
  {
    id: "customer.create",
    kind: "fixture.customer.create",
    versionRef: "customer.create@1.2.0",
    phase: "setup",
    terminal: false,
    inputPorts: [],
    outputPorts: [
      {
        key: "customerId",
        semanticType: "crm.customer.id",
        kind: "data",
        required: true,
        sensitive: false
      }
    ],
    params: {}
  },
  {
    id: "customer.filter",
    kind: "browser.customer.filter",
    versionRef: "customer.filter@2.1.0",
    phase: "execute",
    terminal: false,
    inputPorts: [
      {
        key: "customerId",
        semanticType: "crm.customer.id",
        kind: "data",
        required: true,
        sensitive: false
      }
    ],
    outputPorts: [
      {
        key: "matchedRows",
        semanticType: "crm.customer.rows",
        kind: "data",
        required: true,
        sensitive: false
      }
    ],
    params: {}
  },
  {
    id: "customer.assert",
    kind: "assert.customer.visible",
    versionRef: "customer.visible@1.0.0",
    phase: "assert",
    terminal: true,
    oracleStrength: "HARD",
    inputPorts: [
      {
        key: "matchedRows",
        semanticType: "crm.customer.rows",
        kind: "data",
        required: true,
        sensitive: false
      }
    ],
    outputPorts: [],
    params: {}
  }
];

const workflowEdges = [
  {
    id: "customer-created",
    sourceNodeId: "customer.create",
    sourcePort: "customerId",
    targetNodeId: "customer.filter",
    targetPort: "customerId",
    semanticType: "crm.customer.id",
    kind: "data",
    mapping: "direct"
  },
  {
    id: "rows-matched",
    sourceNodeId: "customer.filter",
    sourcePort: "matchedRows",
    targetNodeId: "customer.assert",
    targetPort: "matchedRows",
    semanticType: "crm.customer.rows",
    kind: "data",
    mapping: "direct"
  }
];

const workflowDraft = {
  id: DRAFT_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  testCaseId: CASE_ID,
  intentVersionRef: "CRM-CUSTOMER-FILTER:intent@1",
  semanticRevision: 7,
  layoutRevision: 4,
  semanticDigest: SHA_A,
  graph: {
    schemaVersion: "atlas.workflow-graph/0.1",
    nodes: workflowNodes,
    edges: workflowEdges
  },
  layout: {
    "customer.create": { x: 76, y: 196 },
    "customer.filter": { x: 346, y: 104 },
    "customer.assert": { x: 620, y: 196 }
  },
  validation: {
    valid: true,
    issues: [],
    executionLevels: [
      ["customer.create"],
      ["customer.filter"],
      ["customer.assert"]
    ],
    matchedRequiredInputs: 2,
    totalRequiredInputs: 2
  },
  updatedBy: "human",
  createdAt: CREATED_AT,
  updatedAt: UPDATED_AT
};

const testCase = {
  id: CASE_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  draftId: DRAFT_ID,
  caseKey: "CRM-CUSTOMER-FILTER",
  name: "销售筛选客户",
  status: "ACTIVE",
  revision: 3,
  intent: testIntent,
  intentDigest: SHA_B,
  intentVersion: "1",
  intentVersionRef: "CRM-CUSTOMER-FILTER:intent@1",
  graphValid: true,
  semanticRevision: 7,
  layoutRevision: 4,
  updatedBy: "human",
  createdAt: CREATED_AT,
  updatedAt: UPDATED_AT
};

const planTemplate = {
  schemaVersion: "atlas.plan-template/0.1",
  testCaseId: CASE_ID,
  semanticRevision: 7,
  testIrDigest: SHA_A,
  graphDigest: SHA_B,
  planDigest: SHA_A,
  executionLevels: [
    ["customer.create"],
    ["customer.filter"],
    ["customer.assert"]
  ],
  nodes: workflowNodes.map((node, index) => ({
    nodeId: node.id,
    kind: node.kind,
    versionRef: node.versionRef,
    executionLevel: index
  })),
  requiredFeatures: []
};

const testIr = {
  schemaVersion: "atlas.test-ir/0.2",
  testCaseId: CASE_ID,
  semanticRevision: 7,
  intentVersionRef: "CRM-CUSTOMER-FILTER:intent@1",
  actors: testIntent.actors,
  surfaces: [],
  requiredFeatures: [],
  requirementRefs: [],
  evidencePolicy: testIntent.evidencePolicy,
  outcomePolicy: testIntent.outcomePolicy,
  recoveryPolicy: testIntent.recoveryPolicy,
  variables: {},
  fixture: {
    blueprintVersionId: "29292929-2929-4929-8929-292929292929",
    blueprintVersionRef: "crm.customer.seed@1.0.0",
    contentDigest: SHA_B,
    requiredExports: {}
  },
  workflow: {
    schemaVersion: "atlas.workflow-graph/0.1",
    nodes: workflowNodes,
    edges: workflowEdges
  },
  executionLevels: planTemplate.executionLevels,
  assertions: [
    {
      assertionId: "customer-visible",
      evaluatorVersionRef: "customer.visible@1.0.0",
      nodeId: "customer.assert",
      strength: "hard"
    }
  ],
  contentDigest: SHA_A
};

const debugRun = {
  id: DEBUG_RUN_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  testCaseId: CASE_ID,
  draftId: DRAFT_ID,
  environmentId: ENVIRONMENT_ID,
  semanticRevision: 7,
  semanticDigest: SHA_A,
  testIr,
  testIrDigest: SHA_A,
  planTemplate,
  planDigest: SHA_A,
  compiledDigest: SHA_B,
  lifecycle: "TERMINATED",
  outcome: "PASSED",
  snapshotStatus: "CURRENT",
  temporalWorkflowId: `atlas-debug/${TENANT_ID}/${DEBUG_RUN_ID}`,
  executionContractId: EXECUTION_CONTRACT_ID,
  executionContractDigest: SHA_B,
  evidenceManifestId: EVIDENCE_MANIFEST_ID,
  evidenceManifestDigest: SHA_A,
  requestedBy: USER_ID,
  revision: 3,
  requestedAt: CREATED_AT,
  startedAt: "2026-07-19T08:01:00.000Z",
  executionDeadline: "2026-07-19T08:20:00.000Z",
  completedAt: UPDATED_AT,
  cancelRequestedAt: null,
  cancelRequestedBy: null,
  outdatedAt: null,
  failureCode: null,
  failureDetail: null,
  createdAt: CREATED_AT,
  updatedAt: UPDATED_AT
};

function debugEvent(
  seq: number,
  eventType: string,
  payload: Record<string, string | number | boolean>,
  lifecycle = "RUNNING",
  outcome = "NOT_SET"
) {
  return {
    id: `30303030-3030-4030-8030-${String(seq).padStart(12, "0")}`,
    tenantId: TENANT_ID,
    projectId: PROJECT_ID,
    testCaseId: CASE_ID,
    debugRunId: DEBUG_RUN_ID,
    seq,
    eventType,
    lifecycle,
    outcome,
    snapshotStatus: "CURRENT",
    payload,
    occurredAt: new Date(
      Date.parse(CREATED_AT) + seq * 18_000
    ).toISOString()
  };
}

const debugRunEvents = [
  debugEvent(1, "debug_run.requested", {
    safeSummary: "草稿语义快照已冻结，等待受信 Browser Runtime。"
  }),
  debugEvent(2, "debug_run.browser.execution.started", {
    actorSlot: "primary",
    safeSummary: "销售身份的浏览器执行已开始。"
  }),
  debugEvent(3, "debug_run.browser.node.started", {
    nodeId: "customer.create",
    actorSlot: "primary",
    safeSummary: "开始准备客户测试数据。"
  }),
  debugEvent(4, "debug_run.browser.node.completed", {
    nodeId: "customer.create",
    status: "PASSED",
    safeSummary: "客户 Fixture 已准备完成。"
  }),
  debugEvent(5, "debug_run.browser.node.started", {
    nodeId: "customer.filter",
    actorSlot: "primary",
    safeSummary: "开始执行客户筛选动作。"
  }),
  debugEvent(6, "debug_run.browser.observation.captured", {
    nodeId: "customer.filter",
    actorSlot: "primary",
    safeSummary: "已捕获可交互筛选控件与客户列表。"
  }),
  debugEvent(7, "debug_run.browser.policy.decided", {
    nodeId: "customer.filter",
    actorSlot: "primary",
    action: "select customer status filter",
    decision: "ALLOW",
    safeSummary: "策略允许执行已冻结的客户筛选动作。"
  }),
  debugEvent(8, "debug_run.browser.action.executed", {
    nodeId: "customer.filter",
    actorSlot: "primary",
    action: "select customer status filter",
    status: "SUCCEEDED",
    safeSummary: "客户筛选动作已执行并产生新的页面观察。"
  }),
  debugEvent(9, "debug_run.browser.node.completed", {
    nodeId: "customer.filter",
    status: "PASSED",
    safeSummary: "客户筛选节点已完成。"
  }),
  debugEvent(10, "debug_run.browser.assertion.evaluated", {
    nodeId: "customer.assert",
    status: "PASSED",
    safeSummary: "目标客户出现在筛选结果中，Hard Oracle 通过。"
  }),
  debugEvent(11, "debug_run.browser.node.completed", {
    nodeId: "customer.assert",
    status: "PASSED",
    safeSummary: "确定性断言节点已完成。"
  }),
  debugEvent(
    12,
    "debug_run.terminated",
    {
      status: "PASSED",
      safeSummary: "DebugRun 已完成，EvidenceManifest 完整且校验通过。"
    },
    "TERMINATED",
    "PASSED"
  )
];

const latestDebugEvent = debugRunEvents.at(-1)!;
const debugLiveSnapshot = {
  schemaVersion: "atlas.debug-live-snapshot/0.1",
  run: {
    schemaVersion: "atlas.debug-live-run-projection/0.1",
    debugRunId: DEBUG_RUN_ID,
    projectId: PROJECT_ID,
    testCaseId: CASE_ID,
    environmentId: ENVIRONMENT_ID,
    lifecycle: "TERMINATED",
    outcome: "PASSED",
    snapshotStatus: "CURRENT",
    revision: 3,
    executionDeadline: "2026-07-19T08:20:00.000Z",
    startedAt: "2026-07-19T08:01:00.000Z",
    completedAt: UPDATED_AT,
    cancelRequestedAt: null
  },
  cursor: "12",
  latestEvent: {
    schemaVersion: "atlas.debug-live-event/0.1",
    debugRunId: DEBUG_RUN_ID,
    eventId: latestDebugEvent.id,
    seq: latestDebugEvent.seq,
    eventType: latestDebugEvent.eventType,
    lifecycle: latestDebugEvent.lifecycle,
    outcome: latestDebugEvent.outcome,
    snapshotStatus: latestDebugEvent.snapshotStatus,
    data: latestDebugEvent.payload,
    occurredAt: latestDebugEvent.occurredAt,
    cursor: "12"
  },
  observedAt: UPDATED_AT
};

const evidenceManifest = {
  schemaVersion: "atlas.evidence-manifest/0.1",
  id: EVIDENCE_MANIFEST_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  debugRunId: DEBUG_RUN_ID,
  environmentId: ENVIRONMENT_ID,
  executionContractId: EXECUTION_CONTRACT_ID,
  fixtureRunId: FIXTURE_RUN_ID,
  outcome: "PASSED",
  completeness: "COMPLETE",
  integrity: "VERIFIED",
  eventCount: debugRunEvents.length,
  passedAssertions: 1,
  failedAssertions: 0,
  inconclusiveAssertions: 0,
  missingAssertionIds: [],
  artifacts: [
    {
      id: SCREENSHOT_ARTIFACT_ID,
      kind: "SCREENSHOT",
      mimeType: "image/png",
      sizeBytes: 68,
      integrity: "VERIFIED",
      required: true,
      capturedAt: "2026-07-19T08:04:20.000Z",
      contentDigest: SHA_A,
      redactionPolicyDigest: SHA_B
    },
    {
      id: NETWORK_ARTIFACT_ID,
      kind: "NETWORK_SUMMARY",
      mimeType: "application/json",
      sizeBytes: 218,
      integrity: "VERIFIED",
      required: false,
      capturedAt: "2026-07-19T08:04:10.000Z",
      contentDigest: SHA_B,
      redactionPolicyDigest: SHA_A
    },
    {
      id: CONSOLE_ARTIFACT_ID,
      kind: "CONSOLE_SUMMARY",
      mimeType: "application/json",
      sizeBytes: 96,
      integrity: "VERIFIED",
      required: false,
      capturedAt: "2026-07-19T08:04:00.000Z",
      contentDigest: SHA_A,
      redactionPolicyDigest: SHA_B
    }
  ],
  assertionResults: [
    {
      schemaVersion: "atlas.assertion-result/0.1",
      id: "31313131-3131-4131-8131-313131313131",
      assertionId: "customer-visible",
      nodeId: "customer.assert",
      status: "PASSED",
      strength: "hard",
      expectedDigest: SHA_A,
      actualSafeSummary: "目标客户出现在筛选结果中。",
      evaluatorVersionRef: "customer.visible@1.0.0",
      durationMs: 218,
      evidenceRefs: [SCREENSHOT_ARTIFACT_ID],
      observedAt: "2026-07-19T08:04:20.000Z",
      resultDigest: SHA_B
    }
  ],
  artifactManifestDigest: SHA_A,
  eventChainHeadDigest: SHA_B,
  executionContractDigest: SHA_B,
  fixtureManifestDigest: SHA_A,
  oracleResultsDigest: SHA_B,
  planDigest: SHA_A,
  testIrDigest: SHA_A,
  finalizedAt: UPDATED_AT,
  contentDigest: SHA_A
};

const caseVersion = {
  id: CASE_VERSION_ID,
  tenantId: TENANT_ID,
  projectId: PROJECT_ID,
  testCaseId: CASE_ID,
  version: "1.3.0",
  semanticRevision: 6,
  reviewSummary: "上一版客户筛选旅程已通过审核。",
  publishedAt: "2026-07-18T08:00:00.000Z",
  revision: 1
};

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers: {
      "Cache-Control": "no-store",
      "X-Request-ID": "e2e-request"
    },
    body: JSON.stringify(body)
  });
}

export async function installAtlasApiFixture(page: Page): Promise<void> {
  let currentFailedGateDecision = failedGateDecision;
  let currentClusterItems = [
    productCluster,
    automationCluster,
    infraCluster
  ];
  let currentInsightSnapshot = insightSnapshotForWindow(30);

  await page.route("**/api/atlas/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/atlas", "");

    if (request.method() === "GET" && path === "/v1/session") {
      return fulfillJson(route, session);
    }
    if (
      request.method() === "GET" &&
      path === `/v1/projects/${PROJECT_ID}/environments`
    ) {
      return fulfillJson(route, { items: [environment], nextCursor: null });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/projects/${PROJECT_ID}/test-roles`
    ) {
      return fulfillJson(route, { items: [], nextCursor: null });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/environments/${ENVIRONMENT_ID}/account-pools`
    ) {
      return fulfillJson(route, { items: [], nextCursor: null });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/projects/${PROJECT_ID}/data-atoms`
    ) {
      return fulfillJson(route, { items: [], nextCursor: null });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/projects/${PROJECT_ID}/data-blueprints`
    ) {
      return fulfillJson(route, { items: [], nextCursor: null });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/projects/${PROJECT_ID}/test-cases`
    ) {
      return fulfillJson(route, { items: [testCase], nextCursor: null });
    }
    if (
      request.method() === "POST" &&
      path === `/v1/projects/${PROJECT_ID}/test-cases`
    ) {
      const command = request.postDataJSON();
      return fulfillJson(
        route,
        {
          ...testCase,
          id: "20202020-2020-4020-8020-202020202020",
          caseKey: command.caseKey,
          name: command.name,
          intent: command.intent,
          intentVersion: command.intentVersion,
          revision: 1
        },
        201
      );
    }
    if (
      request.method() === "GET" &&
      path === `/v1/test-cases/${CASE_ID}/workflow-draft`
    ) {
      return fulfillJson(route, workflowDraft);
    }
    if (
      request.method() === "GET" &&
      path === `/v1/test-cases/${CASE_ID}/debug-runs`
    ) {
      return fulfillJson(route, { items: [debugRun], nextCursor: null });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/test-cases/${CASE_ID}/versions`
    ) {
      return fulfillJson(route, { items: [caseVersion], nextCursor: null });
    }
    if (
      request.method() === "POST" &&
      path === `/v1/test-cases/${CASE_ID}/workflow-draft/patches:validate`
    ) {
      return fulfillJson(route, {
        patchId: request.postDataJSON().patchId,
        applicable: true,
        semanticDigest: SHA_A,
        graph: workflowDraft.graph,
        validation: workflowDraft.validation,
        issues: []
      });
    }
    if (
      request.method() === "POST" &&
      path === `/v1/test-cases/${CASE_ID}/workflow-draft/patches:apply`
    ) {
      return fulfillJson(route, workflowDraft);
    }
    if (
      request.method() === "PATCH" &&
      path === `/v1/test-cases/${CASE_ID}/workflow-draft/layout`
    ) {
      return fulfillJson(route, workflowDraft);
    }
    if (
      request.method() === "POST" &&
      path === `/v1/test-cases/${CASE_ID}/workflow-draft/debug-runs`
    ) {
      return fulfillJson(route, {
        ...debugRun,
        id: STARTED_DEBUG_RUN_ID,
        lifecycle: "CREATED",
        outcome: "NOT_SET",
        snapshotStatus: "CURRENT",
        temporalWorkflowId: `atlas-debug/${TENANT_ID}/${STARTED_DEBUG_RUN_ID}`,
        evidenceManifestId: null,
        evidenceManifestDigest: null,
        executionContractId: null,
        executionContractDigest: null,
        startedAt: null,
        completedAt: null
      }, 202);
    }
    if (
      request.method() === "GET" &&
      [
        `/v1/debug-runs/${DEBUG_RUN_ID}`,
        `/v1/debug-runs/${STARTED_DEBUG_RUN_ID}`
      ].includes(path)
    ) {
      const runId = path.split("/").at(-1)!;
      return fulfillJson(route, {
        ...debugRun,
        id: runId,
        temporalWorkflowId: `atlas-debug/${TENANT_ID}/${runId}`
      });
    }
    if (
      request.method() === "GET" &&
      [
        `/v1/debug-runs/${DEBUG_RUN_ID}/live`,
        `/v1/debug-runs/${STARTED_DEBUG_RUN_ID}/live`
      ].includes(path)
    ) {
      const runId = path.split("/").at(-2)!;
      return fulfillJson(route, {
        ...debugLiveSnapshot,
        run: {
          ...debugLiveSnapshot.run,
          debugRunId: runId
        },
        latestEvent: {
          ...debugLiveSnapshot.latestEvent,
          debugRunId: runId
        }
      });
    }
    if (
      request.method() === "GET" &&
      [
        `/v1/debug-runs/${DEBUG_RUN_ID}/events`,
        `/v1/debug-runs/${STARTED_DEBUG_RUN_ID}/events`
      ].includes(path)
    ) {
      const runId = path.split("/").at(-2)!;
      const afterSeq = Number(
        new URL(request.url()).searchParams.get("afterSeq") ?? "0"
      );
      return fulfillJson(route, {
        items: debugRunEvents
          .filter((event) => event.seq > afterSeq)
          .map((event) => ({ ...event, debugRunId: runId })),
        nextAfterSeq: null
      });
    }
    if (
      request.method() === "GET" &&
      [
        `/v1/debug-runs/${DEBUG_RUN_ID}/evidence`,
        `/v1/debug-runs/${STARTED_DEBUG_RUN_ID}/evidence`
      ].includes(path)
    ) {
      const runId = path.split("/").at(-2)!;
      return fulfillJson(route, {
        ...evidenceManifest,
        debugRunId: runId
      });
    }
    if (
      request.method() === "POST" &&
      new RegExp(
        `^/v1/debug-runs/(${DEBUG_RUN_ID}|${STARTED_DEBUG_RUN_ID})/evidence/[^/]+/read-tokens$`
      ).test(path)
    ) {
      const artifactId = path.split("/").at(-2)!;
      return fulfillJson(
        route,
        {
          id: "32323232-3232-4232-8232-323232323232",
          artifactId,
          purpose: request.postDataJSON().purpose,
          readToken: `e2e-evidence-token-${artifactId}`,
          maxReads: 1,
          issuedAt: UPDATED_AT,
          expiresAt: "2026-07-19T08:06:00.000Z"
        },
        201
      );
    }
    if (
      request.method() === "GET" &&
      /^\/v1\/evidence\/artifacts\/[^/]+\/content$/.test(path)
    ) {
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        headers: {
          "Cache-Control": "no-store",
          "X-Request-ID": "e2e-evidence-read"
        },
        body: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
          "base64"
        )
      });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/projects/${PROJECT_ID}/task-plans`
    ) {
      return fulfillJson(route, { items: [taskPlan], nextCursor: null });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/task-plans/${TASK_PLAN_ID}/versions`
    ) {
      return fulfillJson(route, {
        items: [taskPlanVersion],
        nextCursor: null
      });
    }
    if (
      request.method() === "GET" &&
      path ===
        `/v1/task-plan-versions/${TASK_PLAN_VERSION_ID}/schedules`
    ) {
      return fulfillJson(route, {
        items: [taskSchedule],
        nextCursor: null
      });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/projects/${PROJECT_ID}/task-runs`
    ) {
      return fulfillJson(route, { items: taskRuns, nextCursor: null });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/projects/${PROJECT_ID}/insights/brief`
    ) {
      const requested = Number(
        new URL(request.url()).searchParams.get("windowDays") ?? "30"
      );
      const windowDays: 7 | 30 | 90 =
        requested === 7 || requested === 90 ? requested : 30;
      return fulfillJson(route, insightBriefForWindow(windowDays));
    }
    if (
      request.method() === "POST" &&
      path === `/v1/projects/${PROJECT_ID}/insight-snapshots`
    ) {
      const command = request.postDataJSON();
      currentInsightSnapshot = insightSnapshotForWindow(
        command.windowDays,
        command.clientMutationId
      );
      return fulfillJson(route, currentInsightSnapshot, 201);
    }
    if (
      request.method() === "GET" &&
      path === `/v1/insight-snapshots/${INSIGHT_SNAPSHOT_ID}`
    ) {
      return fulfillJson(route, currentInsightSnapshot);
    }
    if (
      request.method() === "GET" &&
      path === `/v1/task-runs/${CLOSED_RUN_ID}/result`
    ) {
      return fulfillJson(route, {
        taskRunId: CLOSED_RUN_ID,
        selection: "LATEST",
        resultSnapshot: failedResultSnapshot,
        taskGateDecision: currentFailedGateDecision,
        projectionWatermark: failedResultSnapshot.projectionWatermark
      });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/task-runs/${PASSED_RUN_ID}/result`
    ) {
      return fulfillJson(route, {
        taskRunId: PASSED_RUN_ID,
        selection: "LATEST",
        resultSnapshot: passedResultSnapshot,
        taskGateDecision: passedGateDecision,
        projectionWatermark: passedResultSnapshot.projectionWatermark
      });
    }
    if (
      request.method() === "GET" &&
      /^\/v1\/task-runs\/[^/]+\/result$/.test(path)
    ) {
      return fulfillJson(
        route,
        {
          type: "about:blank",
          title: "Result snapshot not found",
          status: 404,
          detail: "TaskRun 尚未形成 ResultSnapshot。"
        },
        404
      );
    }
    if (
      request.method() === "GET" &&
      path ===
        `/v1/result-snapshots/${FAILED_RESULT_SNAPSHOT_ID}/clusters`
    ) {
      return fulfillJson(route, {
        resultSnapshotId: FAILED_RESULT_SNAPSHOT_ID,
        items: currentClusterItems,
        nextCursor: null,
        asOf: "2026-07-19T07:47:00.000Z",
        projectionWatermark: "2026-07-19T07:46:30.000Z"
      });
    }
    if (
      request.method() === "GET" &&
      path ===
        `/v1/result-snapshots/${PASSED_RESULT_SNAPSHOT_ID}/clusters`
    ) {
      return fulfillJson(route, {
        resultSnapshotId: PASSED_RESULT_SNAPSHOT_ID,
        items: [],
        nextCursor: null,
        asOf: "2026-07-19T06:32:00.000Z",
        projectionWatermark: "2026-07-19T06:31:00.000Z"
      });
    }
    if (
      request.method() === "POST" &&
      path === "/v1/task-gates/evaluations"
    ) {
      const command = request.postDataJSON();
      const source =
        command.resultSnapshotId === PASSED_RESULT_SNAPSHOT_ID
          ? passedGateDecision
          : currentFailedGateDecision;
      const nextDecision = {
        ...source,
        id:
          command.resultSnapshotId === PASSED_RESULT_SNAPSHOT_ID
            ? "a1a1a1a1-a1a1-41a1-81a1-a1a1a1a1a1a1"
            : "a2a2a2a2-a2a2-42a2-82a2-a2a2a2a2a2a2",
        revision: source.revision + 1,
        supersedesGateDecisionId: source.id,
        gatePolicyVersion: command.gatePolicyVersion,
        clientMutationId: command.clientMutationId,
        evaluatedAt: "2026-07-19T08:10:00.000Z"
      };
      if (command.resultSnapshotId === FAILED_RESULT_SNAPSHOT_ID) {
        currentFailedGateDecision = nextDecision;
      }
      return fulfillJson(route, nextDecision, 201);
    }
    if (
      request.method() === "POST" &&
      /^\/v1\/failure-classifications\/[^/]+\/revisions$/.test(path)
    ) {
      const classificationId = path.split("/").at(-2)!;
      const command = request.postDataJSON();
      const current = currentClusterItems.find(
        (item) =>
          item.classification.failureClassificationId === classificationId
      )?.classification;
      if (!current) {
        return fulfillJson(
          route,
          {
            type: "about:blank",
            title: "Classification not found",
            status: 404,
            detail: "FailureClassification 不存在。"
          },
          404
        );
      }
      const nextClassification = {
        ...current,
        id: "a3a3a3a3-a3a3-43a3-83a3-a3a3a3a3a3a3",
        revision: current.revision + 1,
        supersedesRevisionId: current.id,
        failureDomain: command.failureDomain,
        hypothesisCode: command.hypothesisCode,
        hypothesis: command.hypothesis,
        confidence: command.confidence,
        supportingEvidenceRefs: command.supportingEvidenceRefs,
        contradictingEvidenceRefs: command.contradictingEvidenceRefs,
        evidenceGapCodes: command.evidenceGapCodes,
        judgmentState: command.judgmentState,
        authorKind: "HUMAN",
        authoredBy: USER_ID,
        modelVersionRef: null,
        clientMutationId: command.clientMutationId,
        createdAt: "2026-07-19T08:11:00.000Z",
        classificationHash: SHA_B
      };
      currentClusterItems = currentClusterItems.map((item) =>
        item.classification.failureClassificationId === classificationId
          ? { ...item, classification: nextClassification }
          : item
      );
      return fulfillJson(route, nextClassification, 201);
    }
    if (
      request.method() === "POST" &&
      path === `/v1/projects/${PROJECT_ID}/task-plans`
    ) {
      return fulfillJson(
        route,
        {
          ...taskPlan,
          id: "71717171-7171-4171-8171-717171717171",
          taskKey: request.postDataJSON().taskKey,
          name: request.postDataJSON().name,
          revision: 1
        },
        201
      );
    }
    if (
      request.method() === "POST" &&
      path === `/v1/task-plan-versions/${TASK_PLAN_VERSION_ID}:run`
    ) {
      return fulfillJson(
        route,
        {
          ...taskRun,
          id: "72727272-7272-4272-8272-727272727272",
          lifecycle: "QUEUED",
          startedAt: null,
          revision: 1
        },
        201
      );
    }
    if (
      request.method() === "POST" &&
      path ===
        `/v1/task-plan-versions/${TASK_PLAN_VERSION_ID}/schedules`
    ) {
      return fulfillJson(
        route,
        {
          ...taskSchedule,
          id: "73737373-7373-4373-8373-737373737373",
          ...request.postDataJSON(),
          syncStatus: "PENDING",
          nextFireTimesUtc: []
        },
        201
      );
    }
    if (
      request.method() === "POST" &&
      new RegExp(
        `^/v1/task-runs/${TASK_RUN_ID}:(pause|resume|cancel)$`
      ).test(path)
    ) {
      const commandType = path.split(":").at(-1)!.toUpperCase();
      return fulfillJson(
        route,
        {
          id: "74747474-7474-4474-8474-747474747474",
          taskRunId: TASK_RUN_ID,
          commandType,
          status: "PENDING",
          clientMutationId: request.postDataJSON().clientMutationId
        },
        202
      );
    }
    if (
      request.method() === "GET" &&
      path === `/v1/task-runs/${TASK_RUN_ID}/units`
    ) {
      return fulfillJson(route, {
        items: units,
        nextAfterOrdinal: null
      });
    }
    if (
      request.method() === "GET" &&
      /^\/v1\/task-runs\/[^/]+\/units$/.test(path)
    ) {
      return fulfillJson(route, {
        items: [],
        nextAfterOrdinal: null
      });
    }
    if (
      request.method() === "GET" &&
      path ===
        `/v1/task-runs/${TASK_RUN_ID}/units/${RUNNING_UNIT_ID}/attempts`
    ) {
      return fulfillJson(route, {
        items: [
          {
            id: ATTEMPT_ID,
            tenantId: TENANT_ID,
            projectId: PROJECT_ID,
            taskRunId: TASK_RUN_ID,
            executionUnitId: RUNNING_UNIT_ID,
            unitKey: SHA_B,
            manifestHash: SHA_A,
            caseVersionId: CASE_VERSION_ID,
            attemptNumber: 1,
            lifecycle: "RUNNING",
            quality: "PENDING",
            hygiene: "PENDING",
            revision: 2,
            temporalNamespace: "atlas-production",
            temporalWorkflowId: `atlas-unit/${TENANT_ID}/${ATTEMPT_ID}`,
            executionDeadline: "2026-07-19T08:20:00.000Z",
            queuedAt: "2026-07-19T08:02:00.000Z",
            startedAt: "2026-07-19T08:02:30.000Z",
            closedAt: null,
            finalizedAt: null,
            cleanupResolvedAt: null,
            createdAt: "2026-07-19T08:02:00.000Z",
            updatedAt: UPDATED_AT
          }
        ],
        nextAfterAttemptNumber: null
      });
    }
    if (
      request.method() === "GET" &&
      /^\/v1\/task-runs\/[^/]+\/units\/[^/]+\/attempts$/.test(path)
    ) {
      return fulfillJson(route, {
        items: [],
        nextAfterAttemptNumber: null
      });
    }
    if (
      request.method() === "GET" &&
      path === `/v1/unit-attempts/${ATTEMPT_ID}/snapshot`
    ) {
      return fulfillJson(route, {
        session: {
          id: "13131313-1313-4313-8313-131313131313",
          unitAttemptId: ATTEMPT_ID,
          browserSessionId: "browser-e2e-01",
          state: "AGENT_CONTROLLED",
          controlEpoch: 7,
          fencingToken: 11,
          browserRevision: 5,
          revision: 3,
          humanInfluenced: false,
          updatedAt: UPDATED_AT
        },
        lease: {
          ownerId: "agent-worker-07",
          ownerType: "AGENT",
          state: "ACTIVE",
          expiresAt: "2026-07-19T08:10:00.000Z"
        },
        pendingCommand: null,
        observedAt: UPDATED_AT
      });
    }
    if (
      request.method() === "POST" &&
      new RegExp(
        `^/v1/unit-attempts/${ATTEMPT_ID}/(takeover|return|pause|resume)$`
      ).test(path)
    ) {
      const commandType = path.split("/").at(-1)!.toUpperCase();
      return fulfillJson(route, {
        id: "14141414-1414-4414-8414-141414141414",
        unitAttemptId: ATTEMPT_ID,
        commandType,
        status: "PENDING",
        reason: request.postDataJSON().reason,
        requestedBy: USER_ID,
        requestedTtlSec: request.postDataJSON().requestedTtlSec ?? null,
        createdAt: UPDATED_AT,
        updatedAt: UPDATED_AT
      }, 202);
    }

    return fulfillJson(
      route,
      {
        type: "about:blank",
        title: "Unmocked Atlas API request",
        status: 404,
        detail: `${request.method()} ${path}`
      },
      404
    );
  });
}

export async function installDenseLiveApiFixture(
  page: Page
): Promise<void> {
  const additionalCases = [
    {
      caseId: "41414141-4141-4141-8141-414141414141",
      draftId: "42424242-4242-4242-8242-424242424242",
      versionId: "43434343-4343-4343-8343-434343434343",
      caseKey: "CRM-CUSTOMER-OWNER",
      name: "主管查看团队客户",
      roleKey: "manager"
    },
    {
      caseId: "44444444-4444-4444-8444-444444444445",
      draftId: "45454545-4545-4545-8545-454545454545",
      versionId: "46464646-4646-4646-8646-464646464646",
      caseKey: "CRM-CUSTOMER-SERVICE",
      name: "客服核对客户状态",
      roleKey: "support"
    }
  ];
  const caseCatalog = [
    testCase,
    ...additionalCases.map((item) => ({
      ...testCase,
      id: item.caseId,
      draftId: item.draftId,
      caseKey: item.caseKey,
      name: item.name,
      intent: {
        ...testIntent,
        actors: testIntent.actors.map((actor) => ({
          ...actor,
          roleKey: item.roleKey
        }))
      }
    }))
  ];
  const versionCatalog = new Map([
    [CASE_ID, [caseVersion]],
    ...additionalCases.map(
      (item) =>
        [
          item.caseId,
          [
            {
              ...caseVersion,
              id: item.versionId,
              testCaseId: item.caseId
            }
          ]
        ] as const
    )
  ]);
  const caseVersionIds = [
    CASE_VERSION_ID,
    ...additionalCases.map((item) => item.versionId)
  ];
  const browserIds = [
    taskPlanVersion.matrix.browserProfileVersionIds[0]!,
    taskPlanVersion.matrix.browserProfileVersionIds[1]!
  ];
  const denseUnits = Array.from({ length: 36 }, (_, index) => {
    const ordinal = index + 1;
    const lifecycle =
      ordinal === 1 ? "RUNNING" : ordinal === 2 ? "QUEUED" : "CLOSED";
    const quality =
      ordinal === 1 || ordinal === 2
        ? "PENDING"
        : ordinal % 13 === 0
          ? "BLOCKED"
          : ordinal % 11 === 0
            ? "INFRA_ERROR"
            : ordinal % 7 === 0
              ? "FAILED"
              : "PASSED";
    return {
      ...executionUnit(
        ordinal === 1
          ? RUNNING_UNIT_ID
          : `47474747-4747-4747-8747-${String(ordinal).padStart(
              12,
              "0"
            )}`,
        ordinal,
        lifecycle,
        quality
      ),
      caseVersionId: caseVersionIds[index % caseVersionIds.length],
      browserProfileVersionId: browserIds[index % browserIds.length]
    };
  });

  await page.route(
    new RegExp(
      `/api/atlas/v1/projects/${PROJECT_ID}/task-runs(?:\\?.*)?$`
    ),
    (route) =>
      fulfillJson(route, {
        items: [
          {
            ...taskRun,
            materializedUnitCount: denseUnits.length,
            materializedFirstAttemptCount: denseUnits.length
          },
          ...taskRuns.slice(1)
        ],
        nextCursor: null
      })
  );
  await page.route(
    new RegExp(
      `/api/atlas/v1/task-runs/${TASK_RUN_ID}/units(?:\\?.*)?$`
    ),
    (route) =>
      fulfillJson(route, {
        items: denseUnits,
        nextAfterOrdinal: null
      })
  );
  await page.route(
    `**/api/atlas/v1/projects/${PROJECT_ID}/test-cases**`,
    (route) =>
      fulfillJson(route, {
        items: caseCatalog,
        nextCursor: null
      })
  );
  await page.route(
    "**/api/atlas/v1/test-cases/*/versions**",
    (route) => {
      const segments = new URL(route.request().url()).pathname.split("/");
      const caseId = segments.at(-2) ?? "";
      return fulfillJson(route, {
        items: versionCatalog.get(caseId) ?? [],
        nextCursor: null
      });
    }
  );
  await page.route(
    `**/api/atlas/v1/task-plans/${TASK_PLAN_ID}/versions**`,
    (route) =>
      fulfillJson(route, {
        items: [
          {
            ...taskPlanVersion,
            pinnedCaseVersionIds: caseVersionIds,
            matrix: {
              ...taskPlanVersion.matrix,
              identityProfileVersionIds: [
                "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
                "48484848-4848-4848-8848-484848484848",
                "49494949-4949-4949-8949-494949494949"
              ]
            }
          }
        ],
        nextCursor: null
      })
  );
}
