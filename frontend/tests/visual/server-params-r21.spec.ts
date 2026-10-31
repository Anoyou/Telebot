import { expect, test } from "@playwright/test";

import { installApiFixture } from "./fixtures";

// 第 21 轮：服务器参数中文名 + 英文原名 + 说明（后端 GET /api/system/settings/meta 单一来源），
// 「平台与模块」底部「全部服务器参数」一览标注在哪个页面修改；旧服务器没有元数据时按原键名列出。

test.describe("全部服务器参数（第 21 轮）", () => {
  test("每个参数显示中文名 · 英文原名、说明与修改位置", async ({ page }, testInfo) => {
    const fixture = await installApiFixture(page);
    await page.route(/\/api\/(sudo|notify-bots)(\?.*)?$/, async (route) => {
      await route.fulfill({ json: [] });
    });
    await page.goto("/settings?tab=platform", { waitUntil: "networkidle" });
    const card = page.getByTestId("server-params-overview");
    await card.scrollIntoViewIfNeeded();
    await expect(card.getByText("全部服务器参数", { exact: true })).toBeVisible();

    // 现有表单标签旁保留英文原名（不进入无障碍名称）。
    await expect(page.locator("code", { hasText: /^runtime_log_retention_days$/ }).first()).toBeVisible();
    await expect(page.getByLabel("AI 指令上下文条数", { exact: true })).toBeVisible();

    await card.getByTestId("server-param-group-command").locator("summary").click();
    const context = card.getByTestId("server-param-ai_context_messages");
    await expect(context.getByText("AI 指令上下文条数", { exact: true })).toBeVisible();
    await expect(context.getByText("ai_context_messages", { exact: true })).toBeVisible();
    await expect(context.getByText(/最近几条聊天记录/)).toBeVisible();
    await expect(context.getByRole("link", { name: /去「服务器设置 › 平台与模块」修改/ })).toHaveAttribute("href", "/settings?tab=platform");
    await expect(context.getByText("App：AI › AI 全局设置")).toBeVisible();

    // 搜索英文原名也能找到；限额类跳到「限流与风控」。
    await card.getByLabel("搜索服务器参数").fill("per_minute");
    const perMinute = card.getByTestId("server-param-llm_limits.per_minute");
    await expect(perMinute.getByText("AI 每分钟调用上限", { exact: true })).toBeVisible();
    await expect(perMinute.getByRole("link", { name: /限流与风控/ })).toHaveAttribute("href", "/settings?tab=security");
    await expect(card.getByTestId("server-param-command_prefix")).toHaveCount(0);

    await card.getByLabel("搜索服务器参数").fill("kill_switch");
    const kill = card.getByTestId("server-param-kill_switch");
    await expect(kill.getByText(/只读：只能用「紧急停止」按钮开关/)).toBeVisible();
    await expect(kill.getByText("在「顶栏 › 紧急停止」修改")).toBeVisible();

    await card.getByLabel("搜索服务器参数").fill("");
    await card.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("server-params-overview.png"), fullPage: true });
    fixture.assertClean();
  });

  test("旧服务器没有参数元数据：按原键名列出", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.route(/\/api\/(sudo|notify-bots)(\?.*)?$/, async (route) => {
      await route.fulfill({ json: [] });
    });
    await page.route("**/api/system/settings/meta", async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) });
    });
    await page.goto("/settings?tab=platform", { waitUntil: "networkidle" });
    const fallback = page.getByTestId("server-params-fallback");
    await fallback.scrollIntoViewIfNeeded();
    await expect(fallback.getByText(/还没有参数说明，按原键名列出/)).toBeVisible();
    await expect(fallback.getByText("timezone", { exact: true })).toBeVisible();
    fixture.assertClean();
  });
});
