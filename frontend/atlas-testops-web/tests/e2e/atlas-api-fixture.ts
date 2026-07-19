import type { Page, Route } from "@playwright/test";

export const TENANT_ID = "11111111-1111-4111-8111-111111111111";
export const PROJECT_ID = "22222222-2222-4222-8222-222222222222";
export const USER_ID = "33333333-3333-4333-8333-333333333333";
export const TASK_PLAN_ID = "44444444-4444-4444-8444-444444444444";
export const TASK_PLAN_VERSION_ID =
  "55555555-5555-4555-8555-555555555555";
export const TASK_RUN_ID = "66666666-6666-4666-8666-666666666666";
export const RUNNING_UNIT_ID = "77777777-7777-4777-8777-777777777777";
export const CLOSED_UNIT_ID = "88888888-8888-4888-8888-888888888888";
export const ATTEMPT_ID = "99999999-9999-4999-8999-999999999999";

const CREATED_AT = "2026-07-19T08:00:00.000Z";
const UPDATED_AT = "2026-07-19T08:05:00.000Z";
const SHA_A = `sha256:${"a".repeat(64)}`;
const SHA_B = `sha256:${"b".repeat(64)}`;

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
  roles: ["PROJECT_ADMIN", "RUN_OPERATOR", "RESULT_REVIEWER"],
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
  pinnedCaseVersionIds: [
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
  ],
  matrix: {
    environmentIds: ["cccccccc-cccc-4ccc-8ccc-cccccccccccc"],
    browserProfileVersionIds: ["dddddddd-dddd-4ddd-8ddd-dddddddddddd"],
    identityProfileVersionIds: ["eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"],
    dataProfileVersionIds: ["ffffffff-ffff-4fff-8fff-ffffffffffff"]
  },
  profileRefs: {},
  policyDigests: {
    "infra-retry": SHA_B
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
    caseVersionId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
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
  await page.route("**/api/atlas/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/atlas", "");

    if (request.method() === "GET" && path === "/v1/session") {
      return fulfillJson(route, session);
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
      path === `/v1/projects/${PROJECT_ID}/task-runs`
    ) {
      return fulfillJson(route, { items: [taskRun], nextCursor: null });
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
      path ===
        `/v1/task-runs/${TASK_RUN_ID}/units/${RUNNING_UNIT_ID}/attempts`
    ) {
      return fulfillJson(route, {
        items: [
          {
            id: ATTEMPT_ID,
            attemptNumber: 1,
            lifecycle: "RUNNING",
            quality: "PENDING",
            hygiene: "PENDING",
            revision: 2,
            executionDeadline: "2026-07-19T08:20:00.000Z",
            startedAt: "2026-07-19T08:02:30.000Z",
            closedAt: null
          }
        ],
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
      path === `/v1/unit-attempts/${ATTEMPT_ID}/takeover`
    ) {
      return fulfillJson(route, {
        id: "14141414-1414-4414-8414-141414141414",
        commandType: "TAKEOVER",
        status: "PENDING",
        reason: "Requested from Atlas live console: takeover."
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
