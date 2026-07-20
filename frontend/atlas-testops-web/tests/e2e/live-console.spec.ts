import { expect, test } from "@playwright/test";

import {
  ATTEMPT_ID,
  CASE_ID,
  DEBUG_RUN_ID,
  installAtlasApiFixture,
  PROJECT_ID,
  SCREENSHOT_ARTIFACT_ID,
  TASK_RUN_ID
} from "./atlas-api-fixture";

test.beforeEach(async ({ page }) => {
  await installAtlasApiFixture(page);
});

test("renders the database-authoritative batch cockpit and switches matrix grouping", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/live`);

  await expect(
    page.getByRole("heading", {
      name: "2 条执行，正在同一片现场发生。"
    })
  ).toBeVisible();
  await expect(page.getByText("EXECUTION MATRIX").first()).toBeVisible();
  await expect(page.getByText("EXE-001")).toBeVisible();
  await expect(
    page.getByText("AGENT_CONTROLLED", { exact: true })
  ).toBeVisible();
  await expect(page.getByText("GATED")).toHaveCount(3);

  await page.getByRole("link", { name: "按浏览器" }).click();
  await expect(page).toHaveURL(/group=browser/);
  await expect(page.getByText("Browser FFFFFFFF")).toBeVisible();
});

test("submits epoch-fenced Attempt control and revision-fenced TaskRun control", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/live`);
  await expect(
    page.getByRole("button", { name: "接管当前执行" }).first()
  ).toBeEnabled();

  const takeoverRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(`/v1/unit-attempts/${ATTEMPT_ID}/takeover`)
  );
  await page
    .getByRole("button", { name: "接管当前执行" })
    .first()
    .click();
  const takeover = await takeoverRequest;

  expect(takeover.headers()["if-match"]).toBe('"control-epoch-7"');
  expect(takeover.headers()["idempotency-key"]).toMatch(/^takeover-/);
  expect(takeover.postDataJSON()).toMatchObject({
    reason: "Requested from Atlas live console: takeover.",
    requestedTtlSec: 300
  });

  const pauseRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(`/v1/task-runs/${TASK_RUN_ID}:pause`)
  );
  await page.getByRole("button", { name: "暂停派发" }).click();
  const pause = await pauseRequest;
  const body = pause.postDataJSON();

  expect(pause.headers()["if-match"]).toBe('"revision-4"');
  expect(pause.headers()["idempotency-key"]).toBe(body.clientMutationId);
  expect(body.clientMutationId).toMatch(/^pause-run-/);
});

test("renders a frozen DebugRun from monotonic events and reads verified evidence through a grant", async ({
  page
}) => {
  await page.goto(
    `/projects/${PROJECT_ID}/live?debugRunId=${DEBUG_RUN_ID}&caseId=${CASE_ID}`
  );

  await expect(page.getByText("REAL-TIME DEBUG · DRAFT R7")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "销售筛选客户" })
  ).toBeVisible();
  await expect(page.getByText("执行步骤")).toBeVisible();
  await expect(page.getByText("3/3")).toBeVisible();
  await expect(page.getByText("AI Runtime")).toBeVisible();
  await expect(page.getByText("NO EXTERNAL CALL")).toBeVisible();
  await expect(page.getByText("运行结果")).toBeVisible();
  await expect(page.getByText("技术事件与完整字段")).toBeVisible();
  await expect(
    page.getByText(
      "DebugRun 已完成，EvidenceManifest 完整且校验通过。"
    ).first()
  ).toBeVisible();
  await expect(page.getByText("hard · 218 ms")).toBeVisible();

  const grantRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(
        `/v1/debug-runs/${DEBUG_RUN_ID}/evidence/${SCREENSHOT_ARTIFACT_ID}/read-tokens`
      )
  );
  const contentRequest = page.waitForRequest(
    (request) =>
      request.method() === "GET" &&
      request.url().includes(
        `/v1/evidence/artifacts/${SCREENSHOT_ARTIFACT_ID}/content`
      )
  );
  await page.getByRole("button", { name: /截图/ }).click();
  const grant = await grantRequest;
  const content = await contentRequest;

  expect(grant.postDataJSON()).toEqual({ purpose: "INLINE" });
  expect(content.headers().authorization).toBe(
    `Atlas-Evidence e2e-evidence-token-${SCREENSHOT_ARTIFACT_ID}`
  );
  await expect(
    page.getByRole("button", { name: "关闭 Evidence 预览" })
  ).toBeVisible();
});
