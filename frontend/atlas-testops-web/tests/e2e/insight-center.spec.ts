import { expect, test } from "@playwright/test";

import {
  INSIGHT_SNAPSHOT_ID,
  installAtlasApiFixture,
  insightBriefForWindow,
  PROJECT_ID
} from "./atlas-api-fixture";

test.beforeEach(async ({ page }) => {
  await installAtlasApiFixture(page);
});

test("renders the real quality terrain in the approved prototype hierarchy", async ({
  page
}) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(message.text());
  });

  await page.goto(`/projects/${PROJECT_ID}/insights`);

  await expect(
    page.getByRole("heading", {
      name: "把失败放回它发生的旅程里。"
    })
  ).toBeVisible();
  await expect(page.getByText("1,284")).toBeVisible();
  await expect(
    page.getByRole("link", { name: /客户筛选 97\.78%/ })
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /权限边界 92\.41%/ })
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "客户权限发布门禁" })
  ).toBeVisible();
  await expect(page.getByText("DATASET TRACE · LIVE CUT")).toBeVisible();
  await expect(page.getByText("Source snapshots")).toBeVisible();
  await expect(page.getByText("CURRENT VS BASELINE")).toHaveCount(0);
  await expect(page.getByText("DATASET PROVENANCE")).toHaveCount(0);
  await expect(page.getByText("TRACE REPLAY · FROZEN")).toHaveCount(0);
  expect(runtimeErrors).toEqual([]);
});

test("switches the live comparison window through URL state", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/insights`);

  const briefRequest = page.waitForRequest((request) => {
    const url = new URL(request.url());
    return (
      request.method() === "GET" &&
      url.pathname.endsWith(`/v1/projects/${PROJECT_ID}/insights/brief`) &&
      url.searchParams.get("windowDays") === "7"
    );
  });
  await page.getByRole("link", { name: "7D" }).click();
  await briefRequest;

  await expect(page).toHaveURL(/window=7/);
  await expect(page.getByText("QUALITY TERRAIN · 7 DAYS")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "7D" })
  ).toHaveAttribute("aria-current", "page");
});

test("pins the exact DatasetCut and then reads the immutable snapshot", async ({
  page
}) => {
  await page.goto(`/projects/${PROJECT_ID}/insights?window=90`);

  const pinRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      request.url().endsWith(
        `/v1/projects/${PROJECT_ID}/insight-snapshots`
      )
  );
  const exactRead = page.waitForRequest(
    (request) =>
      request.method() === "GET" &&
      request.url().endsWith(
        `/v1/insight-snapshots/${INSIGHT_SNAPSHOT_ID}`
      )
  );
  await page.getByRole("button", { name: "固定当前 DatasetCut" }).click();

  const request = await pinRequest;
  const body = request.postDataJSON();
  expect(body).toMatchObject({
    windowDays: 90,
    asOf: "2026-07-19T08:00:00.000Z"
  });
  expect(body.clientMutationId).toMatch(/^pin-insight-/);
  expect(request.headers()["idempotency-key"]).toBe(body.clientMutationId);
  await exactRead;

  await expect(page).toHaveURL(
    new RegExp(`snapshot=${INSIGHT_SNAPSHOT_ID}`)
  );
  await expect(page.getByText("SNAPSHOT 8A8A8A8A")).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "当前 InsightSnapshot 已固定"
    })
  ).toBeDisabled();
  await expect(page.getByText("DATASET TRACE · FROZEN")).toBeVisible();
});

test("opens an immutable snapshot deep link without reading a live brief", async ({
  page
}) => {
  const insightRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/insight")) {
      insightRequests.push(request.url());
    }
  });

  await page.goto(
    `/projects/${PROJECT_ID}/insights?snapshot=${INSIGHT_SNAPSHOT_ID}`
  );

  await expect(page.getByText("SNAPSHOT 8A8A8A8A")).toBeVisible();
  expect(
    insightRequests.filter((url) => url.includes("/insights/brief"))
  ).toHaveLength(0);
  expect(
    insightRequests.filter((url) =>
      url.endsWith(`/v1/insight-snapshots/${INSIGHT_SNAPSHOT_ID}`)
    )
  ).toHaveLength(1);
});

test("keeps NO_DATA distinct from zero and does not infer a safe state", async ({
  page
}) => {
  const source = insightBriefForWindow(30);
  const noDataMetric = {
    ...source.current.trustedPassRate,
    numerator: 0,
    denominator: 0,
    basisPoints: null,
    sampleStatus: "NO_DATA"
  };
  await page.route(
    `**/api/atlas/v1/projects/${PROJECT_ID}/insights/brief**`,
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...source,
          current: {
            ...source.current,
            taskRunCount: 0,
            executionUnitCount: 0,
            trustedPassRate: noDataMetric,
            autonomousTrustedPassRate: {
              ...noDataMetric,
              metricKey: "quality.autonomous_trusted_pass_rate"
            },
            methodHealthRate: {
              ...noDataMetric,
              metricKey: "quality.method_health_rate"
            }
          },
          baseline: {
            ...source.baseline,
            taskRunCount: 0,
            executionUnitCount: 0,
            trustedPassRate: noDataMetric,
            autonomousTrustedPassRate: {
              ...noDataMetric,
              metricKey: "quality.autonomous_trusted_pass_rate"
            },
            methodHealthRate: {
              ...noDataMetric,
              metricKey: "quality.method_health_rate"
            }
          },
          deltas: {
            trustedPassRate: null,
            autonomousTrustedPassRate: null,
            methodHealthRate: null
          },
          terrain: [],
          activeRisk: null,
          datasetCut: {
            ...source.datasetCut,
            sourceSnapshotIds: [],
            sourceSnapshotHashes: [],
            gateDecisionIds: [],
            gateDecisionHashes: []
          }
        })
      })
  );

  await page.goto(`/projects/${PROJECT_ID}/insights`);

  await expect(page.getByText("NO COMPARABLE SLICES")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "当前 DatasetCut 无非通过 Gate" })
  ).toBeVisible();
  await expect(page.getByText("0%")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "当前无风险任务" })
  ).toBeDisabled();
});
