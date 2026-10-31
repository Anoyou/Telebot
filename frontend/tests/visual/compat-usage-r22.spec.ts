import { expect, test } from "@playwright/test";

import { installApiFixture, pluginCapabilitiesFixture } from "./fixtures";

test.describe("兼容层使用统计（第 22 轮）", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "只在 Chromium 项目运行");

  test("旧服务器（capabilities 无 compat_usage）不显示卡片", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.goto("/plugins/manage", { waitUntil: "networkidle" });
    await expect(page.getByText("已安装插件").first()).toBeVisible();
    await expect(page.getByTestId("compat-usage-card")).toHaveCount(0);
    fixture.assertClean();
  });

  test("管理员在「安装与更新」底部展开查看只读统计", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.route("**/api/plugins/capabilities", async (route) => {
      await route.fulfill({ json: { ...pluginCapabilitiesFixture, compat_usage: true } });
    });
    await page.goto("/plugins/manage", { waitUntil: "networkidle" });
    const card = page.getByTestId("compat-usage-card");
    await expect(card).toBeVisible();
    await expect(card.getByText("兼容层使用统计")).toBeVisible();
    await card.getByRole("button", { name: "查看统计" }).click();
    await expect(card.getByTestId("compat-usage-summary")).toContainText("3 / 4 项有人在用");
    await expect(card.getByTestId("compat-usage-summary")).toContainText("累计 1,332 次");
    // 用到的按次数排前面，无法计数的放最后
    const rows = card.locator("li");
    await expect(rows.first()).toContainText("C15");
    await expect(rows.last()).toContainText("C01");
    await expect(card.getByTestId("compat-usage-C01")).toContainText("无法计数");
    await expect(card.getByTestId("compat-usage-C03")).toContainText("暂无使用");
    await expect(card.getByTestId("compat-usage-C02")).toContainText("guess_number · 42");
    await expect(card.getByTestId("compat-usage-C20")).toContainText("宿主（无插件） · 3");
    // 只读：卡片里没有任何会写数据的按钮
    await expect(card.getByRole("button")).toHaveCount(1);
    fixture.assertClean();
  });
});
