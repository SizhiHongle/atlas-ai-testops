import { expect, test } from "@playwright/test";

import {
  ATTEMPT_ID,
  installAtlasApiFixture,
  PROJECT_ID,
  TASK_RUN_ID
} from "./atlas-api-fixture";

test.beforeEach(async ({ page }) => {
  await installAtlasApiFixture(page);
});

test("renders task facts and preserves URL-selected production context", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/tasks`);

  await expect(
    page.getByRole("heading", {
      name: "让每一次回归，都沿着自己的轨道运行。"
    })
  ).toBeVisible();
  await expect(page.getByText("夜间全量回归").first()).toBeVisible();
  await expect(page.getByText("nightly.crm@1.4.0").first()).toBeVisible();
  await expect(page.getByText("1 / 2 EXECUTIONS")).toBeVisible();
  await expect(page.getByText("RUN-66666666").first()).toBeVisible();

  await page.goto(
    `/projects/${PROJECT_ID}/tasks?planId=missing&runId=${TASK_RUN_ID}`
  );
  await expect(page.getByText("RUN-66666666").first()).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`runId=${TASK_RUN_ID}`));
});

test("loads an exact live snapshot and sends fenced takeover control", async ({
  page
}) => {
  await page.goto(
    `/projects/${PROJECT_ID}/live?runId=${TASK_RUN_ID}&unitId=77777777-7777-4777-8777-777777777777&attemptId=${ATTEMPT_ID}`
  );

  await expect(
    page.getByRole("heading", { name: "2 条执行，正在同一片现场发生。" })
  ).toBeVisible();
  await expect(page.getByText("AGENT_CONTROLLED").first()).toBeVisible();
  await expect(page.getByText("browser-e2e-01")).toBeVisible();

  const takeoverRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(`/v1/unit-attempts/${ATTEMPT_ID}/takeover`)
  );
  await page.getByRole("button", { name: "接管" }).click();
  const request = await takeoverRequest;

  expect(request.headers()["if-match"]).toBe('"control-epoch-7"');
  expect(request.headers()["idempotency-key"]).toMatch(/^takeover-/);
  expect(request.postDataJSON()).toEqual({
    reason: "Requested from Atlas live console: takeover.",
    requestedTtlSec: 300
  });
});
