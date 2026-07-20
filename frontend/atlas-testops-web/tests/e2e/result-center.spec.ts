import { expect, test } from "@playwright/test";

import {
  AUTOMATION_CLUSTER_ID,
  CLOSED_RUN_ID,
  FAILED_RESULT_SNAPSHOT_ID,
  installAtlasApiFixture,
  PASSED_RUN_ID,
  PRODUCT_CLASSIFICATION_ID,
  PRODUCT_CLUSTER_ID,
  PROJECT_ID
} from "./atlas-api-fixture";

test.beforeEach(async ({ page }) => {
  await installAtlasApiFixture(page);
});

test("renders the exact failed snapshot and keeps cluster selection in the URL", async ({
  page
}) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(message.text());
  });

  await page.goto(`/projects/${PROJECT_ID}/results`);

  await expect(
    page.getByRole("heading", {
      name: "结果不是一张报表，而是一次发布决定。"
    })
  ).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`runId=${CLOSED_RUN_ID}`));
  await expect(page).toHaveURL(
    new RegExp(`clusterId=${PRODUCT_CLUSTER_ID}`)
  );
  await expect(page.getByText("50%").first()).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "阻止本次发布" })
  ).toBeVisible();
  await expect(page.getByText("PRODUCT_ASSERTION_FAILED").first()).toBeVisible();
  await expect(page.getByText("UNIT 000001")).toBeVisible();

  await page.getByText("SELECTOR_STABILITY_UNCERTAIN").first().click();
  await expect(page).toHaveURL(
    new RegExp(`clusterId=${AUTOMATION_CLUSTER_ID}`)
  );
  await expect(page.getByText("UNIT 000002")).toBeVisible();
  await expect(
    page
      .getByRole("paragraph")
      .filter({
        hasText: "客户列表选择器在一次执行中失去稳定锚点，需要人工复核证据。"
      })
  ).toBeVisible();

  await expect(page.getByRole("button", { name: "导出证据" })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "重跑失败单元" }).first()
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "标记已知问题" })
  ).toBeDisabled();
  expect(runtimeErrors).toEqual([]);
});

test("evaluates Gate against the exact snapshot and policy", async ({
  page
}) => {
  await page.goto(
    `/projects/${PROJECT_ID}/results?runId=${CLOSED_RUN_ID}`
  );

  const gateRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith("/v1/task-gates/evaluations")
  );
  await page.getByRole("button", { name: "重新评估门禁" }).click();
  const request = await gateRequest;
  const body = request.postDataJSON();

  expect(body).toMatchObject({
    resultSnapshotId: FAILED_RESULT_SNAPSHOT_ID,
    gatePolicyVersion: "0.1.0"
  });
  expect(body.clientMutationId).toMatch(/^evaluate-gate-/);
  expect(request.headers()["idempotency-key"]).toBe(body.clientMutationId);
  await expect(
    page.getByRole("heading", { name: "阻止本次发布" })
  ).toBeVisible();
});

test("appends a human classification revision without replacing evidence", async ({
  page
}) => {
  await page.goto(
    `/projects/${PROJECT_ID}/results?runId=${CLOSED_RUN_ID}&clusterId=${PRODUCT_CLUSTER_ID}`
  );

  await page.getByRole("button", { name: "复核失败归因" }).click();
  await expect(
    page.getByRole("heading", { name: "复核失败归因" })
  ).toBeVisible();
  await page
    .getByLabel("判断说明")
    .fill("人工复核确认客户筛选结果与业务 Oracle 不一致。");
  await page.getByLabel("置信度（0—100）").fill("91.5");
  await page.getByLabel("人工判断").selectOption("HUMAN_CONFIRMED");

  const reviewRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(
        `/v1/failure-classifications/${PRODUCT_CLASSIFICATION_ID}/revisions`
      )
  );
  await page.getByRole("button", { name: "追加人工 Revision" }).click();
  const request = await reviewRequest;
  const body = request.postDataJSON();

  expect(body).toMatchObject({
    expectedRevision: 1,
    failureDomain: "PRODUCT",
    hypothesisCode: "PRODUCT_BEHAVIOR_MISMATCH",
    hypothesis: "人工复核确认客户筛选结果与业务 Oracle 不一致。",
    confidence: { numerator: 9150, denominator: 10000 },
    judgmentState: "HUMAN_CONFIRMED"
  });
  expect(body.supportingEvidenceRefs).toHaveLength(2);
  expect(body.contradictingEvidenceRefs).toHaveLength(0);
  expect(request.headers()["idempotency-key"]).toBe(body.clientMutationId);
  await expect(
    page.getByRole("heading", { name: "复核失败归因" })
  ).toHaveCount(0);
  await expect(page.getByText("HUMAN_CONFIRMED · r2")).toBeVisible();
});

test("switches to an accepted snapshot and shows the truthful clear state", async ({
  page
}) => {
  await page.goto(
    `/projects/${PROJECT_ID}/results?runId=${PASSED_RUN_ID}`
  );

  await expect(
    page.getByRole("heading", {
      name: "这次回归，可以进入下一道发布门。"
    })
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "建议进入下一道发布门" })
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "当前 ResultSnapshot 没有失败聚类。"
    })
  ).toBeVisible();
  await expect(page.getByLabel("切换结果任务")).toHaveValue(PASSED_RUN_ID);
  await expect(
    page.getByRole("button", { name: "相同配置再跑" })
  ).toBeDisabled();
});
