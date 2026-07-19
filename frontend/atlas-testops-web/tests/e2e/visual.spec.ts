import { expect, test } from "@playwright/test";

import {
  installAtlasApiFixture,
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
