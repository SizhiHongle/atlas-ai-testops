import { expect, test } from "@playwright/test";

import {
  CASE_ID,
  ENVIRONMENT_ID,
  installAtlasApiFixture,
  PROJECT_ID,
  STARTED_DEBUG_RUN_ID
} from "./atlas-api-fixture";

test.beforeEach(async ({ page }) => {
  await installAtlasApiFixture(page);
});

test("normalizes the prototype dotted Case Key before creating a TestCase", async ({
  page
}) => {
  const roleId = "20202020-2020-4020-8020-202020202020";
  const poolId = "21212121-2121-4121-8121-212121212121";
  await page.route(
    `**/api/atlas/v1/projects/${PROJECT_ID}/test-roles**`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: roleId,
              tenantId: "10101010-1010-4010-8010-101010101010",
              projectId: PROJECT_ID,
              roleKey: "sales",
              name: "真实销售",
              description: "CRM sales identity",
              capabilities: [
                "customer.read",
                "customer.self",
                "visit:create"
              ],
              status: "ACTIVE",
              revision: 2,
              createdAt: "2026-07-19T08:00:00.000Z",
              updatedAt: "2026-07-19T08:00:00.000Z"
            }
          ],
          nextCursor: null
        })
      })
  );
  await page.route(
    `**/api/atlas/v1/environments/${ENVIRONMENT_ID}/account-pools**`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: poolId,
              tenantId: "10101010-1010-4010-8010-101010101010",
              projectId: PROJECT_ID,
              environmentId: ENVIRONMENT_ID,
              roleId,
              poolKey: "sales-primary",
              name: "销售主账号池",
              status: "ACTIVE",
              exclusive: true,
              defaultTtlSeconds: 900,
              cooldownSeconds: 30,
              healthFailureThreshold: 3,
              healthRetryCooldownSeconds: 60,
              revision: 1,
              createdAt: "2026-07-19T08:00:00.000Z",
              updatedAt: "2026-07-19T08:00:00.000Z"
            }
          ],
          nextCursor: null
        })
      })
  );
  await page.route(
    `**/api/atlas/v1/account-pools/${poolId}/capacity`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          poolId,
          totalSlots: 1,
          availableSlots: 1,
          leasedSlots: 0,
          cooldownAccounts: 0,
          quarantinedAccounts: 0,
          unverifiedAccounts: 0
        })
      })
  );
  await page.route(
    `**/api/atlas/v1/account-pools/${poolId}/accounts**`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [], nextCursor: null })
      })
  );

  await page.goto(`/projects/${PROJECT_ID}/cases?caseId=${CASE_ID}`);
  await page.getByRole("button", { name: "新建用例" }).click();

  await page.getByRole("textbox", { name: "用例名称" }).fill("客户筛选");
  await page
    .getByRole("textbox", { name: "稳定 Case Key" })
    .fill("crm.customer.filter");
  await expect(
    page.getByRole("textbox", { name: "稳定 Case Key" })
  ).toHaveValue("CRM-CUSTOMER-FILTER");
  await page.getByRole("textbox", { name: "测试意图" }).fill("客户能勾筛选");
  await page
    .getByRole("combobox", { name: "主身份" })
    .selectOption({ label: "真实销售 · sales" });

  const createRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(`/v1/projects/${PROJECT_ID}/test-cases`)
  );
  await page.getByRole("button", { name: "创建用例" }).click();

  expect((await createRequest).postDataJSON()).toMatchObject({
    caseKey: "CRM-CUSTOMER-FILTER",
    name: "客户筛选",
    intentVersion: "0.1.0",
    intent: {
      schemaVersion: "atlas.test-intent/0.1",
      summary: "客户能勾筛选",
      actors: [
        {
          actorSlot: "primary",
          roleId,
          roleKey: "sales",
          roleRevision: 2,
          capabilities: [
            "customer.read",
            "customer.self",
            "visit:create"
          ]
        }
      ]
    },
    graph: {
      schemaVersion: "atlas.workflow-graph/0.1",
      nodes: [],
      edges: []
    },
    layout: {}
  });
});

test("keeps a handled create contract failure inside the dialog", async ({
  page
}) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.route(
    `**/api/atlas/v1/projects/${PROJECT_ID}/test-cases**`,
    async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 422,
        contentType: "application/problem+json",
        body: JSON.stringify({
          type: "https://atlas.test/problems/validation-failed",
          title: "请求校验失败",
          status: 422,
          detail: "一个或多个请求字段不符合接口契约。",
          instance: `/v1/projects/${PROJECT_ID}/test-cases`,
          errorCode: "VALIDATION_FAILED",
          requestId: "e2e-create-case-invalid",
          violations: [
            {
              field: "body.caseKey",
              message: "String should match pattern",
              code: "string_pattern_mismatch"
            }
          ]
        })
      });
    }
  );

  await page.goto(`/projects/${PROJECT_ID}/cases?caseId=${CASE_ID}`);
  await page.getByRole("button", { name: "新建用例" }).click();
  await page.getByRole("textbox", { name: "用例名称" }).fill("客户筛选");
  await page
    .getByRole("textbox", { name: "稳定 Case Key" })
    .fill("CRM-CUSTOMER-FILTER");
  await page.getByRole("textbox", { name: "测试意图" }).fill("客户能勾筛选");
  await page.getByRole("button", { name: "创建用例" }).click();

  await expect(page.getByRole("alert")).toHaveText(
    "稳定 Case Key 仅支持大写字母、数字和连字符，例如 CRM-CUSTOMER-FILTER。"
  );
  await expect(page.getByText("Unhandled Script Error")).toHaveCount(0);
  expect(runtimeErrors).toEqual([]);
});

test("uses the prototype workbench hierarchy with real workflow commands", async ({
  page
}) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(message.text());
  });

  await page.goto(`/projects/${PROJECT_ID}/cases?caseId=${CASE_ID}`);

  await expect(
    page.getByRole("heading", { name: "用例，才是编排真正的容器。" })
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "销售筛选客户", exact: true })
  ).toBeVisible();
  await expect(page.getByText("AI COPILOT · PATCH MODE")).toBeVisible();
  await expect(page.getByText("3 NODES · 2 EDGES")).toHaveText(
    "3 NODES · 2 EDGES"
  );
  await expect(
    page.getByRole("button", { name: "生成编排 Patch" })
  ).toBeDisabled();

  await page.getByRole("button", { name: "人工编排" }).click();
  await expect(page.getByText("MANUAL CANVAS · DIRECT MODE")).toBeVisible();
  await expect(page.getByText("端口可连接 · 连线可删除")).toBeVisible();
  await page.getByRole("button", { name: "AI 编排" }).click();
  await expect(page.getByText("AI COPILOT · PATCH MODE")).toBeVisible();
  await page.getByRole("button", { name: "人工编排" }).click();

  const validateRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(
        `/v1/test-cases/${CASE_ID}/workflow-draft/patches:validate`
      )
  );
  const applyRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(
        `/v1/test-cases/${CASE_ID}/workflow-draft/patches:apply`
      )
  );
  await page
    .getByRole("button", {
      name: "从 fixture.customer.create 创建连线"
    })
    .click();
  await page
    .getByRole("button", {
      name: "连接到 browser.customer.filter"
    })
    .click();
  const validated = await validateRequest;
  const applied = await applyRequest;

  expect(validated.postDataJSON().operations[0]).toMatchObject({
    op: "ADD_EDGE",
    edge: {
      sourceNodeId: "customer.create",
      sourcePort: "customerId",
      targetNodeId: "customer.filter",
      targetPort: "customerId",
      semanticType: "crm.customer.id",
      kind: "data",
      mapping: "direct"
    }
  });
  expect(applied.headers()["if-match"]).toBe('"revision-7"');
  expect(applied.headers()["idempotency-key"]).toMatch(/^workflow-patch-/);

  const layoutRequest = page.waitForRequest(
    (request) =>
      request.method() === "PATCH" &&
      request.url().endsWith(
        `/v1/test-cases/${CASE_ID}/workflow-draft/layout`
      )
  );
  await page.getByRole("button", { name: "自动布局" }).click();
  const layout = await layoutRequest;
  expect(layout.headers()["if-match"]).toBe('"revision-4"');
  expect(layout.postDataJSON().positions).toMatchObject({
    "customer.create": { x: 54, y: 54 },
    "customer.filter": { x: 268, y: 54 },
    "customer.assert": { x: 482, y: 54 }
  });

  await page.getByRole("button", { name: "发布新版本" }).click();
  await expect(
    page.getByRole("heading", { name: "发布 CaseVersion" })
  ).toBeVisible();
  expect(runtimeErrors).toEqual([]);
});

test("starts an exact DebugRun and enters its Test Theatre", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/cases?caseId=${CASE_ID}`);

  const debugRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(
        `/v1/test-cases/${CASE_ID}/workflow-draft/debug-runs`
      )
  );
  await page
    .getByRole("button", { name: "实时调试 Draft r7" })
    .click();

  expect((await debugRequest).postDataJSON()).toMatchObject({
    baseSemanticRevision: 7,
    environmentId: ENVIRONMENT_ID
  });
  await expect(page).toHaveURL(
    `/projects/${PROJECT_ID}/live?debugRunId=${STARTED_DEBUG_RUN_ID}&caseId=${CASE_ID}`
  );
  await expect(
    page.getByRole("heading", {
      name: "这是一场草稿调试，不是一条正式结果。"
    })
  ).toBeVisible();
});
