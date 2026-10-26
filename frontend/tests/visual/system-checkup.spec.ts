import { expect, test } from "@playwright/test";

import { installApiFixture } from "./fixtures";

test.describe("概览一键体检", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "只在 Chromium 项目运行");

  test("点「一键体检」显示结论、各项状态与处理入口", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.goto("/overview", { waitUntil: "networkidle" });
    await page.getByTestId("dashboard-checkup").click();
    const dialog = page.getByRole("dialog", { name: "一键体检" });
    await expect(dialog).toBeVisible();
    const result = dialog.getByTestId("checkup-result");
    await expect(result).toContainText("基本可用，有需关注项");
    await expect(result).toContainText("建议下一步");
    await expect(dialog.getByTestId("checkup-section-errors")).toContainText("需关注");
    await expect(dialog.getByTestId("checkup-section-health")).toContainText("正常");
    const link = result.getByRole("link", { name: "打开日志" }).first();
    await expect(link).toHaveAttribute("href", "/logs");
    await link.click();
    await expect(page).toHaveURL(/\/logs$/);
    await expect(dialog).toBeHidden();
    fixture.assertClean();
  });
});
