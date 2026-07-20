import { expect, test } from "@playwright/test";

test("renders the production login surface without demo credentials", async ({
  page
}) => {
  await page.goto("/login");

  await expect(
    page.getByRole("heading", { name: "一次登录， 唤醒整座测试空间。" })
  ).toBeVisible();
  await expect(page.getByLabel("测试空间", { exact: true })).toBeVisible();
  await expect(page.getByLabel("邮箱或工号")).toBeVisible();
  await expect(page.getByRole("button", { name: "登录测试空间" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "飞书一键登录" })).toBeDisabled();
});

test("never submits credentials through a pre-hydration GET form", async ({
  browser,
  baseURL
}) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  await page.goto(`${baseURL}/login`);

  await expect(page.locator("form")).toHaveAttribute("method", "post");
  await expect(
    page.getByRole("button", { name: "登录测试空间" })
  ).toBeDisabled();

  await page.getByLabel("邮箱或工号").fill("admin@mail.com");
  const password = page.locator('input[name="password"]');
  await password.fill("local-password");
  await password.press("Enter");

  expect(new URL(page.url()).search).toBe("");
  await context.close();
});
