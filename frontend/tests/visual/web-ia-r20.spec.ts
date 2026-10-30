import { expect, test, type Page } from "@playwright/test";

import { accountFixture, installApiFixture } from "./fixtures";

// 第 20 轮 Web 信息架构第一批：统一命名、值守全局提示、绑定管理 Bot 跳转、日志 → 命中调试。

const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const productionProfile = {
  active_profile: null,
  current_profile: "production",
  status: "idle",
  last_error: null,
  operator_id: null,
  updated_at: null,
  modules: {},
  blind_spot: null,
};

const safeWatchProfile = {
  ...productionProfile,
  active_profile: "safe_watch",
  current_profile: "safe_watch",
  status: "active",
  updated_at: "2026-10-30T06:00:00Z",
};

async function routeJson(page: Page, path: string, body: unknown) {
  await page.route((url) => url.pathname === path, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill(json(body));
  });
}

test.describe("Web 信息架构第一批（第 20 轮）", () => {
  test("导航名 = 页面标题：服务器设置、Webhook、插件、交互、AI、待确认", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "侧栏只在桌面常驻");
    const fixture = await installApiFixture(page);
    await page.goto("/plugins", { waitUntil: "networkidle" });
    // Web 默认仍进插件页。
    await expect(page.getByRole("heading", { name: "插件", exact: true })).toBeVisible();
    await expect(page.locator('[data-sidebar-sort-path="/settings"]')).toContainText("服务器设置");
    await expect(page.locator('[data-sidebar-sort-path="/webhooks"]')).toContainText("Webhook");
    await expect(page.locator('[data-sidebar-sort-path="/webhooks"]')).not.toContainText("入站");

    for (const [path, title] of [
      ["/settings", "服务器设置"],
      ["/interaction", "交互"],
      ["/ai", "AI"],
      ["/webhooks", "Webhook"],
      ["/assistant/inbox", "待确认"],
    ] as const) {
      await page.goto(path, { waitUntil: "networkidle" });
      await expect(page.getByRole("heading", { name: title, exact: true }).first()).toBeVisible();
    }
    fixture.assertClean();
  });

  test("值守中显示全局提示条，确认后恢复生产", async ({ page }) => {
    const fixture = await installApiFixture(page);
    let profile: Record<string, unknown> = safeWatchProfile;
    let restoreCalls = 0;
    await page.route("**/api/platform/profile", async (route) => {
      await route.fulfill(json(profile));
    });
    await page.route("**/api/platform/profile/restore", async (route) => {
      restoreCalls += 1;
      profile = productionProfile;
      await route.fulfill(json(productionProfile));
    });
    await page.goto("/overview", { waitUntil: "networkidle" });
    const bar = page.locator('[data-global-alert="safe-watch"]');
    await expect(bar).toBeVisible();
    await expect(bar).toContainText("值守中");
    await bar.getByRole("button", { name: "恢复生产" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("heading", { name: "恢复生产？" })).toBeVisible();
    await dialog.getByRole("button", { name: "确认恢复生产" }).click();
    await expect(bar).toBeHidden();
    expect(restoreCalls).toBe(1);
    fixture.assertClean();
  });

  test("生产模式与旧服务器（无运行模式接口）不显示值守提示", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.goto("/overview", { waitUntil: "networkidle" });
    await expect(page.locator('[data-global-alert="safe-watch"]')).toHaveCount(0);
    fixture.assertClean();

    await page.route("**/api/platform/profile", async (route) => {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) });
    });
    await page.goto("/plugins", { waitUntil: "networkidle" });
    await expect(page.locator('[data-global-alert="safe-watch"]')).toHaveCount(0);
  });

  test("紧急停止统一命名：提示条「紧急停止已开启」+「解除」确认框", async ({ page }, testInfo) => {
    const fixture = await installApiFixture(page);
    await routeJson(page, "/api/system/kill-switch", { enabled: true });
    await page.goto("/overview", { waitUntil: "networkidle" });
    const bar = page.locator('[data-global-alert="kill-switch"]');
    await expect(bar).toContainText("紧急停止已开启");
    if (testInfo.project.name === "desktop") {
      await expect(page.getByRole("button", { name: "解除紧急停止" })).toBeVisible();
    }
    await bar.getByRole("button", { name: "解除" }).click();
    await expect(page.getByRole("dialog").getByRole("heading", { name: "解除紧急停止？" })).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "取消" }).click();
    await expect(page.getByText("全局总闸", { exact: false })).toHaveCount(0);
    fixture.assertClean();
  });

  test("多账号时「绑定管理 Bot」进入所选账号的管理 Bot 分页", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.route("**/api/accounts", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill(json([
        { ...accountFixture, id: 7, display_name: "第二个账号", phone: "+10000000007" },
        accountFixture,
      ]));
    });
    await page.goto("/settings", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "绑定管理 Bot" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("进入该账号的管理 Bot 配置页")).toBeVisible();
    await dialog.getByLabel("账号").selectOption("1");
    await dialog.getByRole("button", { name: "前往配置" }).click();
    await expect(page).toHaveURL(/\/accounts\/1\?tab=bot-management$/);
    fixture.assertClean();
  });

  test("日志页可进入命中调试，消息详情一键带参数复现", async ({ page }) => {
    const fixture = await installApiFixture(page);
    const message = {
      id: 1,
      trace_id: "trace-r20",
      account_id: 1,
      source_channel: "userbot",
      event_type: "message",
      chat_id: -1001234567890,
      message_id: 99,
      sender_user_id: 4242,
      sender_name: "测试用户",
      chat_title: "测试群",
      text_preview: ",ai 为什么没回复",
      status: "ok",
      started_at: "2026-10-30T05:00:00Z",
      ended_at: "2026-10-30T05:00:01Z",
      duration_ms: 12,
      plugin_count: 0,
      plugin_keys: [],
      action_count: 0,
      error_count: 0,
      funel: { received: "pass", routed: "skip", ran: "none", sent: "none", verdict: "no_response_normal", reason_text: "没有插件订阅", next_step: "去命中调试看看" },
      verdict: "no_response_normal",
      stuck_at: null,
      reason_code: "no_subscriber",
      reason_text: "没有插件订阅这条消息",
      next_step: "用命中调试确认路由",
    };
    await routeJson(page, "/api/logs/messages", [message]);
    await routeJson(page, "/api/logs/trace/events/trace-r20", {
      ...message,
      raw_summary: null,
      payload_snapshot: null,
      probe_report: null,
      spans: [],
      actions: [],
      related_runtime_logs: [],
    });
    await page.goto("/logs?trace_id=trace-r20", { waitUntil: "networkidle" });
    const headerLink = page.locator("[data-dispatch-debug-link]").first();
    await expect(headerLink).toHaveAttribute("href", "/dispatch-debug");

    const reproduce = page.getByRole("link", { name: "用命中调试复现" });
    if (!(await reproduce.isVisible().catch(() => false))) {
      await page.getByText(",ai 为什么没回复").first().click();
    }
    await expect(reproduce).toBeVisible();
    await reproduce.click();
    await expect(page).toHaveURL(/\/dispatch-debug\?/);
    await expect(page.locator("#dispatch-chat-id")).toHaveValue("-1001234567890");
    await expect(page.locator("#dispatch-chat-type")).toHaveValue("supergroup");
    await expect(page.locator("#dispatch-via")).toHaveValue("userbot");
    await expect(page.locator("textarea").first()).toHaveValue(",ai 为什么没回复");
    fixture.assertClean();
  });

  test("命中调试模块关闭时日志页不显示入口", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.route("**/api/system/capabilities", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill(json({
        modules: ["ai", "interaction_bot", "webhooks", "ledger", "dispatch_debug"].map((key) => ({
          key,
          label: key,
          desired_enabled: key !== "dispatch_debug",
          generation: 0,
          runtime_state: key === "dispatch_debug" ? "stopped" : "ready",
        })),
      }));
    });
    await page.goto("/logs", { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: /日志/ }).first()).toBeVisible();
    await expect(page.locator("[data-dispatch-debug-link]")).toHaveCount(0);
    fixture.assertClean();
  });
});
