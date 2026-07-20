import { expect, test } from "@playwright/test";

import {
  CASE_ID,
  CLOSED_RUN_ID,
  DEBUG_RUN_ID,
  installAtlasApiFixture,
  installDenseLiveApiFixture,
  PROJECT_ID
} from "./atlas-api-fixture";

test("keeps the production task center visually stable", async ({ page }) => {
  await installAtlasApiFixture(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/projects/${PROJECT_ID}/tasks`);

  await expect(
    page.getByRole("heading", {
      name: "让每一次回归，都沿着自己的轨道运行。"
    })
  ).toBeVisible();
  await expect(page.getByText("1 / 2 EXECUTIONS")).toBeVisible();

  await expect(page).toHaveScreenshot("task-center.png", {
    animations: "disabled",
    fullPage: true
  });
});

test("keeps the batch task assembly aligned with the approved prototype", async ({
  page
}) => {
  await installAtlasApiFixture(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/projects/${PROJECT_ID}/tasks?panel=create`);

  await expect(
    page.getByRole("heading", {
      name: "把测试范围，展开成一张真实执行矩阵。"
    })
  ).toBeVisible();
  await expect(page.getByText("MATRIX REACTOR")).toBeVisible();
  await expect(page.getByText("Profile Catalog 尚未开放")).toBeVisible();

  await expect(page).toHaveScreenshot("task-builder.png", {
    animations: "disabled",
    fullPage: true
  });
});

test("keeps the case workbench aligned with the approved prototype", async ({
  page
}) => {
  await installAtlasApiFixture(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/projects/${PROJECT_ID}/cases?caseId=${CASE_ID}`);

  await expect(
    page.getByRole("heading", { name: "用例，才是编排真正的容器。" })
  ).toBeVisible();
  await expect(page.getByText("WORKFLOW GRAPH")).toBeVisible();
  await expect(page.getByText("草稿已具备发布条件")).toBeVisible();

  await expect(page).toHaveScreenshot("case-workbench.png", {
    animations: "disabled",
    fullPage: true
  });
});

test("keeps the batch Live cockpit aligned with the approved prototype", async ({
  page
}) => {
  await installAtlasApiFixture(page);
  await installDenseLiveApiFixture(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/projects/${PROJECT_ID}/live`);

  await expect(
    page.getByRole("heading", {
      name: "36 条执行，正在同一片现场发生。"
    })
  ).toBeVisible();
  await expect(page.getByText("EXECUTION MATRIX").first()).toBeVisible();
  await expect(
    page.getByText("AGENT_CONTROLLED", { exact: true })
  ).toBeVisible();

  await expect(page).toHaveScreenshot("live-batch.png", {
    animations: "disabled",
    fullPage: true
  });
});

test("keeps the Debug Test Theatre aligned with the approved prototype", async ({
  page
}) => {
  await installAtlasApiFixture(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(
    `/projects/${PROJECT_ID}/live?debugRunId=${DEBUG_RUN_ID}&caseId=${CASE_ID}`
  );

  await expect(
    page.getByRole("heading", {
      name: "这是一场草稿调试，不是一条正式结果。"
    })
  ).toBeVisible();
  await expect(page.getByText("VERIFIED RUNTIME PROJECTION")).toBeVisible();
  await expect(page.getByText("3 / 3")).toBeVisible();

  await expect(page).toHaveScreenshot("live-debug.png", {
    animations: "disabled",
    fullPage: true
  });
});

test("keeps the Result Center aligned with the approved prototype", async ({
  page
}) => {
  await installAtlasApiFixture(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(
    `/projects/${PROJECT_ID}/results?runId=${CLOSED_RUN_ID}`
  );

  await expect(
    page.getByRole("heading", {
      name: "结果不是一张报表，而是一次发布决定。"
    })
  ).toBeVisible();
  await expect(page.getByText("QUALITY GATE")).toBeVisible();
  await expect(page.getByText("OUTCOME CONSTELLATION")).toBeVisible();
  await expect(page.getByText("TRIAGE & EVIDENCE")).toBeVisible();

  await expect(page).toHaveScreenshot("result-center.png", {
    animations: "disabled",
    fullPage: true
  });
});

test("keeps the Insight terrain aligned with the approved prototype", async ({
  page
}) => {
  await installAtlasApiFixture(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/projects/${PROJECT_ID}/insights`);

  await expect(
    page.getByRole("heading", {
      name: "把失败放回它发生的旅程里。"
    })
  ).toBeVisible();
  await expect(page.getByText("1,284")).toBeVisible();
  await expect(page.getByText("DATASET TRACE · LIVE CUT")).toBeVisible();

  await expect(page).toHaveScreenshot("insight-terrain.png", {
    animations: "disabled",
    fullPage: true
  });
});
