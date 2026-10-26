import { expect, test } from "@playwright/test";

import { installAccessExpiredFixture, installApiFixture } from "./fixtures";

test.describe("Cloudflare Access 会话过期", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "只在 Chromium 项目运行");

  test("API 被 302 到 cloudflareaccess.com 时提示刷新页面重新登录", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "仅桌面视口验证");
    const fixture = await installApiFixture(page);
    await installAccessExpiredFixture(page, /^\/api\/(system\/health-overview|auth\/csrf)$/);

    await page.goto("/", { waitUntil: "domcontentloaded" });
    const notice = page.getByText("Cloudflare Access 登录已过期，请刷新页面重新登录。").first();
    await expect(notice).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "刷新页面" })).toBeVisible();
    fixture.assertClean();
  });
});
