import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { installApiFixture, pendingActionsFixture, settingsWithPrimaryNav } from "./fixtures";

// 第 20 轮 Web 信息架构第二批：侧栏三组、账号入口、待确认并入 Agent、手机底栏、插件子分页改名、
// 服务器设置分页重排、白名单并入定时任务、统一确认对话框、账号详情分页。

const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

async function routeGet(page: Page, path: string, body: unknown) {
  await page.route((url) => url.pathname === path, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill(json(body));
  });
}

test.describe("Web 信息架构第二批（第 20 轮）", () => {
  test("桌面侧栏：Agent 固定行 + 常用 / 管理 / 工具与设置三组，待确认挂在 Agent 上", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "侧栏只在桌面常驻");
    const fixture = await installApiFixture(page);
    await routeGet(page, "/api/system-agent/actions", pendingActionsFixture);
    await page.goto("/plugins", { waitUntil: "networkidle" });

    const paths = async (section: string) =>
      page.locator(`[data-sidebar-section="${section}"] [data-sidebar-sort-path]`).evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute("data-sidebar-sort-path")),
      );
    expect(await paths("primary")).toEqual(["/plugins", "/interaction", "/overview"]);
    expect(await paths("manage")).toEqual(["/accounts", "/ai", "/operations", "/ledger", "/logs"]);
    expect(await paths("tools")).toEqual(["/webhooks", "/dispatch-debug", "/settings"]);
    // 「待确认」不再是独立侧栏项，数量挂在 Agent 行
    await expect(page.locator('[data-sidebar-sort-path="/assistant/inbox"]')).toHaveCount(0);
    const agentRow = page.locator("[data-sidebar-agent]");
    await expect(agentRow).toHaveAccessibleName("Agent（2 条待确认）");
    await expect(page.locator("[data-sidebar-agent-pending]")).toHaveText("2");

    // 点 Agent 打开 Agent，页内有「待确认」入口
    await agentRow.click();
    const surface = page.locator("[data-assistant-surface]");
    await expect(surface).toHaveAttribute("aria-hidden", "false");
    const inboxLink = surface.locator("[data-assistant-inbox-link]").filter({ visible: true });
    await expect(inboxLink).toHaveAccessibleName("待确认（2）");
    await inboxLink.click();
    await expect(page).toHaveURL(/\/assistant\/inbox$/);
    await expect(page.getByRole("heading", { name: "待确认", exact: true })).toBeVisible();

    // 工具与设置组可折叠，刷新后记住
    await page.goto("/plugins", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "工具与设置" }).click();
    await expect(page.locator('[data-sidebar-sort-path="/settings"]')).toHaveCount(0);
    await page.reload({ waitUntil: "networkidle" });
    await expect(page.locator('[data-sidebar-sort-path="/settings"]')).toHaveCount(0);
    // 当前页在工具组里时自动展开
    await page.goto("/settings", { waitUntil: "networkidle" });
    await expect(page.locator('[data-sidebar-sort-path="/settings"]')).toBeVisible();
    fixture.assertClean();
  });

  test("常用入口同时决定侧栏第一组与手机底栏", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "tablet", "覆盖桌面与手机");
    const fixture = await installApiFixture(page);
    await routeGet(page, "/api/system/settings", settingsWithPrimaryNav(["/ai", "/accounts"]));
    await page.goto("/plugins", { waitUntil: "networkidle" });
    if (testInfo.project.name === "desktop") {
      const primary = await page
        .locator('[data-sidebar-section="primary"] [data-sidebar-sort-path]')
        .evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-sidebar-sort-path")));
      expect(primary).toEqual(["/ai", "/accounts"]);
      // 移出常用的 插件/交互/概览 回到「管理」组
      await expect(page.locator('[data-sidebar-section="manage"] [data-sidebar-sort-path="/plugins"]')).toBeVisible();
    } else {
      const dock = page.locator("[data-mobile-navigation-dock]");
      await expect(dock.getByRole("button", { name: "AI" })).toBeVisible();
      await expect(dock.getByRole("button", { name: "账号" })).toBeVisible();
      await expect(dock.getByRole("button", { name: "插件" })).toHaveCount(0);
    }
    fixture.assertClean();
  });

  test("手机底栏：插件 · 交互 · 概览 + 更多（分组，含使用指南）+ Agent 圆钮", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "仅移动视口");
    const fixture = await installApiFixture(page);
    await routeGet(page, "/api/system-agent/actions", pendingActionsFixture);
    await page.goto("/overview", { waitUntil: "networkidle" });
    const dock = page.locator("[data-mobile-navigation-dock]");
    await expect(dock.getByRole("button", { name: "更多导航" })).toBeVisible();
    const labels = await dock.locator("button").evaluateAll((nodes) => nodes.map((node) => node.textContent?.trim()));
    expect(labels).toEqual(["插件", "交互", "概览", "更多"]);
    await expect(page.locator("[data-assistant-mobile-button]")).toHaveAccessibleName("打开 Agent（2 条待确认）");
    // 手机上不再有第二套「汉堡」抽屉
    await expect(page.getByRole("button", { name: "打开导航菜单" })).toBeHidden();

    await page.getByRole("button", { name: "更多导航" }).click();
    const menu = page.getByRole("menu");
    await expect(menu.getByRole("group", { name: "管理" })).toBeVisible();
    await expect(menu.getByRole("group", { name: "工具与设置" })).toBeVisible();
    for (const label of ["账号", "AI", "指令与任务", "资金台账", "日志", "Webhook", "命中调试", "服务器设置", "使用指南", "更新日志", "GitHub"]) {
      await expect(menu.getByRole("menuitem").filter({ hasText: label }).first()).toBeVisible();
    }
    const axe = await new AxeBuilder({ page }).include("[role='menu']").analyze();
    const blockers = axe.violations.filter((item) => item.impact === "critical" || item.impact === "serious");
    expect(blockers.map((item) => item.id)).toEqual([]);

    await menu.getByRole("menuitem").filter({ hasText: "账号" }).click();
    await expect(page).toHaveURL(/\/accounts$/);
    await expect(page.getByRole("heading", { name: "账号", exact: true })).toBeVisible();
    fixture.assertClean();
  });

  test("账号有了独立列表页，概览浮层地址继续可用", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.goto("/accounts", { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { name: "账号", exact: true })).toBeVisible();
    await expect(page.locator("[data-accounts-list]")).toContainText("视觉测试账号");
    await expect(page.getByRole("link", { name: "新增账号" })).toHaveAttribute("href", "/accounts/new");
    await page.goto("/overview?accounts=1", { waitUntil: "networkidle" });
    await expect(page.getByRole("dialog").getByText("账号 Worker")).toBeVisible();
    fixture.assertClean();
  });

  test("插件页子分页改名：启用与配置 · 市场 · 已安装（地址不变）", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.goto("/plugins", { waitUntil: "networkidle" });
    await expect(page.getByRole("tab", { name: "启用与配置" })).toHaveAttribute("data-state", "active");
    await expect(page.getByRole("tab", { name: /^市场/ })).toBeVisible();
    await page.getByRole("tab", { name: "已安装" }).click();
    await expect(page).toHaveURL(/\/plugins\/manage/);
    await expect(page.getByRole("tab", { name: "插件中心" })).toHaveCount(0);
    fixture.assertClean();
  });

  test("服务器设置分页重排，旧 ?tab= 全部跳到新分页", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.goto("/settings", { waitUntil: "networkidle" });
    await expect(page.getByRole("tab", { name: "界面偏好" })).toBeVisible();
    const tabs = await page.getByRole("tab").evaluateAll((nodes) => nodes.map((node) => node.textContent?.trim()));
    expect(tabs).toEqual(["账号与安全", "平台与模块", "连接与通知", "限流与风控", "备份与维护", "界面偏好"]);

    for (const [old, label] of [
      ["proxy-identity", "连接与通知"],
      ["proxy", "连接与通知"],
      ["rate", "限流与风控"],
      ["backup", "备份与维护"],
      ["platform", "平台与模块"],
      ["navigation", "界面偏好"],
    ] as const) {
      await page.goto(`/settings?tab=${old}`, { waitUntil: "networkidle" });
      await expect(page.getByRole("tab", { name: label })).toHaveAttribute("data-state", "active");
    }

    // 时区、日志保留挪到「平台与模块」；通知渠道挪到「连接与通知」
    await page.getByRole("tab", { name: "平台与模块" }).click();
    await expect(page).toHaveURL(/tab=platform/);
    await expect(page.getByText("时区设置", { exact: true })).toBeVisible();
    await expect(page.getByText("平台模块", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "连接与通知" }).click();
    await expect(page).toHaveURL(/tab=connections/);
    await expect(page.getByText("通知渠道", { exact: true })).toBeVisible();

    // 紧急停止只剩只读状态，开关统一在顶栏
    await page.getByRole("tab", { name: "限流与风控" }).click();
    const emergency = page.locator("[data-settings-emergency-status]");
    await expect(emergency).toContainText("正常运行");
    await expect(emergency.getByRole("switch")).toHaveCount(0);
    await expect(page.getByText("限流模板", { exact: true })).toBeVisible();

    await page.getByRole("tab", { name: "界面偏好" }).click();
    await expect(page.locator('[data-nav-pref-group="primary"]')).toContainText("插件");
    await expect(page.getByRole("button", { name: "加入常用：账号" })).toBeVisible();
    fixture.assertClean();
  });

  test("自动指令白名单并入定时任务，旧地址带账号跳转", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await routeGet(page, "/api/accounts/1/features/scheduler/config", { allowed_command_whitelist: ["help", "24d"] });
    await routeGet(page, "/api/accounts/1/features/scheduler/rules", []);
    await page.goto("/operations/templates", { waitUntil: "networkidle" });
    await expect(page.getByRole("tab", { name: "定时任务" })).toBeVisible();
    const tabs = await page.getByRole("tab").evaluateAll((nodes) => nodes.map((node) => node.textContent?.trim()));
    expect(tabs).toEqual(["自定义指令", "定时任务"]);

    await page.goto("/operations/auto-command-whitelist?aid=1", { waitUntil: "networkidle" });
    await expect(page).toHaveURL(/\/operations\/scheduler\?aid=1&section=whitelist$/);
    await expect(page.getByRole("tab", { name: "定时任务" })).toHaveAttribute("data-state", "active");
    await expect(page.getByLabel("自动指令白名单")).toHaveValue("help\n24d");
    fixture.assertClean();
  });

  test("账号详情：统一标题栏、分页改名并写回网址，旧 ?tab=bot 继续可用", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.goto("/accounts/1", { waitUntil: "networkidle" });
    await expect(page.getByRole("tab", { name: "允许会话" })).toBeVisible();
    await expect(page.locator("[data-page-header]").getByRole("heading", { level: 1 })).toContainText("视觉测试账号");
    const tabs = await page.getByRole("tab").evaluateAll((nodes) => nodes.map((node) => node.textContent?.trim()));
    expect(tabs).toEqual(["概览", "插件", "自定义指令", "管理 Bot", "限流与风控", "代理与登录设备", "允许会话"]);
    await page.getByRole("tab", { name: "限流与风控" }).click();
    await expect(page).toHaveURL(/\/accounts\/1\?tab=rate$/);
    await page.goto("/accounts/1?tab=bot", { waitUntil: "networkidle" });
    await expect(page.getByRole("tab", { name: "管理 Bot" })).toHaveAttribute("data-state", "active");
    fixture.assertClean();
  });

  test("危险操作用统一确认框：取消不发请求", async ({ page }) => {
    const fixture = await installApiFixture(page);
    let nativeDialog = false;
    page.on("dialog", async (dialog) => {
      nativeDialog = true;
      await dialog.dismiss();
    });
    await page.goto("/ledger", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "重置数据" }).click();
    const dialog = page.locator("[data-confirm-dialog]");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "清空资金台账与运营统计？" })).toBeVisible();
    await expect(dialog).toHaveAttribute("data-confirm-tone", "danger");
    // 焦点默认在「取消」上，避免回车误触
    await expect(dialog.getByRole("button", { name: "取消" })).toBeFocused();
    await dialog.getByRole("button", { name: "取消" }).click();
    await expect(dialog).toHaveCount(0);
    expect(nativeDialog).toBe(false);
    fixture.assertClean();
  });
});
