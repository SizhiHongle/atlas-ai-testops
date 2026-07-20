import { expect, test } from "@playwright/test";

import {
  installAtlasApiFixture,
  PROJECT_ID,
  TASK_PLAN_VERSION_ID
} from "./atlas-api-fixture";

test.beforeEach(async ({ page }) => {
  await installAtlasApiFixture(page);
});

test("filters project runs and switches between orbit and dense list", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/tasks`);

  await expect(page.getByText("夜间全量回归每日调度")).toBeVisible();
  await expect(page.getByText("RUN-66666666").first()).toBeVisible();

  await page.getByRole("link", { name: "需关注 1" }).click();
  await expect(page).toHaveURL(/filter=attention/);
  await expect(page.getByText("RUN-67676767").first()).toBeVisible();

  await page.getByRole("link", { name: "列表" }).click();
  await expect(page).toHaveURL(/view=list/);
  await expect(page.getByText("RUN-67676767").first()).toBeVisible();
});

test("submits a revision-fenced pause command from Task Focus", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/tasks`);

  const pauseRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith("/v1/task-runs/66666666-6666-4666-8666-666666666666:pause")
  );
  await page.getByRole("button", { name: "暂停派发" }).click();
  const request = await pauseRequest;
  const body = request.postDataJSON();

  expect(request.headers()["if-match"]).toBe('"revision-4"');
  expect(request.headers()["idempotency-key"]).toBe(body.clientMutationId);
  expect(body.clientMutationId).toMatch(/^pause-run-/);
});

test("creates a TaskPlan with contract-matched idempotency", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/tasks?panel=create`);
  await page.getByRole("button", { name: "新建 TaskPlan" }).click();

  await page.getByLabel("任务名称").fill("客户发布门禁");
  await page.getByLabel("稳定 Task Key").fill("crm.customer.release");

  const createRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(`/v1/projects/${PROJECT_ID}/task-plans`)
  );
  await page.getByRole("button", { name: "创建 TaskPlan" }).click();
  const request = await createRequest;
  const body = request.postDataJSON();

  expect(body).toMatchObject({
    taskKey: "crm.customer.release",
    name: "客户发布门禁"
  });
  expect(body.clientMutationId).toMatch(/^create-plan-/);
  expect(request.headers()["idempotency-key"]).toBe(body.clientMutationId);
});

test("launches an exact immutable version with its frozen retry policy", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/tasks?panel=create`);
  await expect(
    page.getByRole("heading", { name: "把测试范围，展开成一张真实执行矩阵。" })
  ).toBeVisible();

  const runRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(
        `/v1/task-plan-versions/${TASK_PLAN_VERSION_ID}:run`
      )
  );
  await page.getByRole("button", { name: "创建并进入现场" }).click();
  const request = await runRequest;
  const body = request.postDataJSON();

  expect(request.headers()["idempotency-key"]).toBe(body.clientMutationId);
  expect(body.retryPolicy).toMatchObject({
    schemaVersion: "atlas.task-retry-policy/0.1",
    infraRetryAttempts: 1,
    maxTotalInfraRetries: 8,
    initialBackoffSeconds: 2,
    maximumBackoffSeconds: 30,
    jitterPercent: 10
  });
  expect(body.retryPolicy.contentDigest).toMatch(/^sha256:[a-f0-9]{64}$/);
});

test("creates a database-authoritative daily schedule", async ({ page }) => {
  await page.goto(`/projects/${PROJECT_ID}/tasks?panel=create`);
  await page
    .getByRole("button", { name: "每日调度 数据库权威 Schedule" })
    .click();

  const scheduleRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(
        `/v1/task-plan-versions/${TASK_PLAN_VERSION_ID}/schedules`
      )
  );
  await page.getByRole("button", { name: "创建调度任务" }).click();
  const request = await scheduleRequest;
  const body = request.postDataJSON();

  expect(request.headers()["idempotency-key"]).toBe(body.clientMutationId);
  expect(body).toMatchObject({
    scheduleKey: "nightly.crm.daily",
    timeZoneName: "Asia/Shanghai",
    overlapPolicy: "QUEUE_ONE",
    catchupPolicy: "RUN_ONCE",
    calendar: {
      schemaVersion: "atlas.task-schedule-calendar/0.1",
      hours: [21],
      minutes: [30]
    }
  });
});
