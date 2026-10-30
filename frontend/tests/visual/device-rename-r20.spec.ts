import { expect, test, type Page } from "@playwright/test";

import { installApiFixture } from "./fixtures";

// 第 20 轮：「设备配置 / 设备伪装 / 设备标识模板」统一改名为「登录设备」。只改文案，
// API 路径（/api/device-profiles）、字段名与备份 key（device_profiles）不变。

const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const profiles = [
  {
    id: 1,
    name: "我的 iPhone",
    device_model: "iPhone 15 Pro",
    system_version: "iOS 18",
    app_version: "Telegram iOS 11.5",
    lang_code: "zh",
    system_lang_code: "zh-Hans",
    is_default: true,
    created_at: "2026-10-30T00:00:00Z",
    updated_at: "2026-10-30T00:00:00Z",
  },
];

async function routeProfiles(page: Page) {
  await page.route((url) => url.pathname === "/api/device-profiles", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill(json(profiles));
  });
}

test.describe("登录设备改名（第 20 轮）", () => {
  test("服务器设置分页与卡片叫「登录设备」，顶部说明 + 中文字段名", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await routeProfiles(page);
    await page.goto("/settings?tab=proxy-identity", { waitUntil: "networkidle" });
    // 第 20 轮第二批：「代理与登录设备」分页并入「连接与通知」，旧 ?tab=proxy-identity 仍跳到这里
    await expect(page.getByRole("tab", { name: "连接与通知" })).toHaveAttribute("data-state", "active");
    await expect(page.getByText("登录设备", { exact: true }).first()).toBeVisible();
    await expect(page.getByText(/会显示在 Telegram「设置 › 设备」列表里，例如 iPhone 15 Pro · iOS 18/).first()).toBeVisible();
    await expect(page.getByText("代理与标识")).toHaveCount(0);
    await expect(page.getByText("设备标识模板")).toHaveCount(0);

    await page.getByRole("button", { name: "新增" }).click();
    for (const label of ["机型", "系统版本", "App 版本", "语言代码"]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
    }
    fixture.assertClean();
  });

  test("账号详情分页「代理与登录设备」，选择器叫「登录设备」", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await routeProfiles(page);
    await page.goto("/accounts/1?tab=proxy", { waitUntil: "networkidle" });
    await expect(page.getByRole("tab", { name: "代理与登录设备" })).toBeVisible();
    await expect(page.getByText("Telegram「设置 › 设备」中将显示：")).toBeVisible();
    await expect(page.getByText("设备伪装")).toHaveCount(0);
    fixture.assertClean();
  });
});
