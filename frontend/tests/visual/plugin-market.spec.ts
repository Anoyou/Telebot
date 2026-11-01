import { expect, test, type Route } from "@playwright/test";

import {
  installApiFixture,
  pluginMarketCatalogFixture,
  pluginMarketChannelResultFixture,
  pluginMarketDetailFieldsFixture,
  pluginMarketItemFixture,
} from "./fixtures";

// 插件市场（服务器 0.113.0）：浏览、搜索、更新角标、一键全部更新（并发结果逐个显示）、来源管理、旧服务器提示。

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

const installedOld = { version: "1.0.0", enabled: true, source: "repo", source_url: "https://github.com/Anoyou/telebot-plugins.git", same_source: true };

test.describe("插件市场", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "只在 Chromium 项目运行");

  test("浏览、搜索、角标、全部更新与来源管理", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "桌面尺寸覆盖即可");
    await installApiFixture(page);
    const items = [
      pluginMarketItemFixture({ installed: installedOld, update_available: true }),
      pluginMarketItemFixture({
        id: "official:sum",
        key: "sum",
        name: { zh: "群聊总结", en: "sum" },
        kind: "utility",
        icon: "🧰",
        summary: "AI 总结群聊。",
        version: "1.1.38",
        installed: installedOld,
        update_available: true,
        pinned_version: "1.0.0",
      }),
      pluginMarketItemFixture({
        id: "official:todo_reminder",
        key: "todo_reminder",
        name: { zh: "待办提醒", en: "todo_reminder" },
        kind: "automation",
        summary: "定时提醒。",
        version: "0.1.6",
      }),
    ];
    let catalog = pluginMarketCatalogFixture({
      items,
      updates: 1,
      rollouts: [{ key: "codex_image", status: "rolled_back", trigger: "auto", from_version: "1.2.0", to_version: "1.2.1", reason: "熔断打开（3 次调用被熔断拦截）" }],
    });
    const updateBodies: unknown[] = [];
    const sourceBodies: unknown[] = [];
    await page.route("**/api/plugin-market/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "GET" && path === "/api/plugin-market/catalog") return json(route, catalog);
      if (request.method() === "POST" && path === "/api/plugin-market/update") {
        updateBodies.push(request.postDataJSON());
        return json(route, {
          concurrency: 3,
          total: 1,
          updated: 1,
          skipped: 0,
          failed: 0,
          items: [{ key: "game24", display_name: "24 点", from_version: "1.0.0", to_version: "1.1.18", status: "updated", message: "已更新", health_check: true }],
        });
      }
      if (request.method() === "POST" && path === "/api/plugin-market/sources") {
        const body = request.postDataJSON();
        sourceBodies.push(body);
        catalog = {
          ...catalog,
          sources: [
            ...catalog.sources,
            { id: "src-abc", name: "社区精选", url: body.url, trust: body.trust, enabled: true, auto_update: false, builtin: false, available: true, status: "ok", plugin_count: 0, fetched_at: null, error: null, fallback: false },
          ],
        };
        return json(route, catalog.sources[catalog.sources.length - 1], 201);
      }
      return json(route, { detail: "unexpected" }, 500);
    });

    await page.goto("/plugins/market", { waitUntil: "networkidle" });
    await expect(page.getByTestId("plugin-market-update-count")).toHaveText("1");
    await expect(page.getByTestId("plugin-market-item-game24")).toBeVisible();
    await expect(page.getByTestId("plugin-market-badge-game24")).toBeVisible();
    await expect(page.getByTestId("plugin-market-item-sum")).toContainText("已固定 1.0.0");
    await expect(page.getByTestId("plugin-market-badge-sum")).toHaveCount(0);
    await expect(page.getByTestId("plugin-market-rollbacks")).toContainText("熔断打开");

    await page.getByTestId("plugin-market-search").fill("总结");
    await expect(page.getByTestId("plugin-market-item-sum")).toBeVisible();
    await expect(page.getByTestId("plugin-market-item-game24")).toHaveCount(0);
    await page.getByTestId("plugin-market-search").fill("");
    await page.getByTestId("plugin-market-only-updates").click();
    await expect(page.getByTestId("plugin-market-item-game24")).toBeVisible();
    await expect(page.getByTestId("plugin-market-item-todo_reminder")).toHaveCount(0);

    await page.getByTestId("plugin-market-update-all").click();
    const dialog = page.getByTestId("plugin-market-update-result");
    await expect(dialog).toContainText("并发 3 个同时更新");
    await expect(dialog).toContainText("进入健康观察");
    expect(updateBodies).toEqual([{}]);
    await dialog.getByRole("button", { name: "好的" }).click();

    await page.getByTestId("plugin-market-source-url").fill("https://example.com/registry.json");
    await page.getByTestId("plugin-market-source-trust").selectOption("community");
    await page.getByTestId("plugin-market-source-add").click();
    await expect(page.getByTestId("plugin-market-source-src-abc")).toContainText("社区");
    expect(sourceBodies).toEqual([{ url: "https://example.com/registry.json", name: null, trust: "community" }]);
    await expect(page.getByTestId("plugin-market-source-delete-official")).toHaveCount(0);
    await expect(page.getByTestId("plugin-market-source-auto-official")).toBeVisible();
  });

  test("旧服务器没有市场接口时提示更新服务器", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "手机尺寸覆盖即可");
    await installApiFixture(page);
    await page.route("**/api/plugin-market/catalog", (route) => json(route, { detail: "Not Found" }, 404));
    await page.goto("/plugins/market", { waitUntil: "networkidle" });
    await expect(page.getByTestId("plugin-market-unsupported")).toContainText("服务器版本过旧");
    await expect(page.getByTestId("plugin-market-update-count")).toHaveCount(0);
  });
});

// 第 25 轮（服务器 0.117.0）：分类标签、应用商店式详情、稳定 / 测试通道、未审核二次确认。
test.describe("插件市场详情与通道（0.117.0）", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "只在 Chromium 项目运行");

  test("分类筛选、详情内容、切换到测试版与未审核确认", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "tablet", "桌面和手机覆盖即可");
    await installApiFixture(page);
    const items = [
      pluginMarketItemFixture({
        ...pluginMarketDetailFieldsFixture,
        installed: installedOld,
        update_available: true,
        update_version: "1.1.18",
        permissions: ["send_message", "payout"],
      }),
      pluginMarketItemFixture({
        id: "src-x:stranger",
        key: "stranger",
        name: { zh: "陌生插件", en: "stranger" },
        kind: "automation",
        trust: "unreviewed",
        source_id: "src-x",
        source_name: "某来源",
        summary: "来路不明。",
        available_channels: [{ name: "stable", ref: null, version: "0.1.0" }],
      }),
    ];
    const catalog = pluginMarketCatalogFixture({ items, updates: 1 });
    const channelBodies: unknown[] = [];
    const installBodies: unknown[] = [];
    await page.route("**/api/plugin-market/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === "GET" && path === "/api/plugin-market/catalog") return json(route, catalog);
      if (request.method() === "POST" && path === "/api/plugin-market/channel") {
        channelBodies.push(request.postDataJSON());
        return json(route, pluginMarketChannelResultFixture("game24", "1.0.0", "1.2.0-beta.1"));
      }
      if (request.method() === "POST" && path === "/api/plugin-market/install") {
        installBodies.push(request.postDataJSON());
        return json(route, { name: "stranger", version: "0.1.0" }, 201);
      }
      return json(route, { detail: "unexpected" }, 500);
    });

    await page.goto("/plugins/market", { waitUntil: "networkidle" });
    await expect(page.getByTestId("plugin-market-kinds")).toBeVisible();
    await page.getByTestId("plugin-market-kind-automation").click();
    await expect(page.getByTestId("plugin-market-item-stranger")).toBeVisible();
    await expect(page.getByTestId("plugin-market-item-game24")).toHaveCount(0);
    await page.getByTestId("plugin-market-kind-all").click();

    await page.getByTestId("plugin-market-open-game24").click();
    const detail = page.getByTestId("plugin-market-detail-game24");
    await expect(detail).toBeVisible();
    await expect(detail.getByTestId("plugin-market-detail-usage")).toContainText("发送 ,24 开一局");
    await expect(detail.getByTestId("plugin-market-detail-release-notes")).toContainText("2026-10-29");
    await expect(detail.getByTestId("plugin-market-detail-permissions")).toContainText("派发群内游戏币");
    await expect(detail.getByTestId("plugin-market-detail-compat")).toContainText("兼容当前服务器");
    await expect(detail.getByTestId("plugin-market-detail-homepage")).toHaveAttribute("href", /tree\/main\/game24/);
    await expect(detail.getByTestId("plugin-market-channel-beta")).toContainText("v1.2.0-beta.1");
    await detail.getByTestId("plugin-market-switch-beta").click();
    await page.locator("[data-confirm-ok]").click();
    await expect(page.getByTestId("plugin-market-update-result")).toContainText("已切换通道");
    expect(channelBodies).toEqual([{ key: "game24", channel: "beta", source_id: "official" }]);
    await page.getByTestId("plugin-market-update-result").getByRole("button", { name: "好的" }).click();
    await expect(page.getByTestId("plugin-market-update-result")).toHaveCount(0);
    await page.keyboard.press("Escape"); // 关闭详情
    await expect(detail).toHaveCount(0);

    // 未审核插件：先取消一次（不安装），再确认安装
    await page.getByTestId("plugin-market-install-stranger").click();
    await page.locator("[data-confirm-cancel]").click();
    expect(installBodies).toEqual([]);
    await page.getByTestId("plugin-market-install-stranger").click();
    await page.locator("[data-confirm-ok]").click();
    await expect.poll(() => installBodies.length).toBe(1);
    expect(installBodies).toEqual([{ source_id: "src-x", key: "stranger" }]);
  });

  test("旧服务器（0.113–0.116）没有详情与通道字段时照常显示", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "桌面尺寸覆盖即可");
    await installApiFixture(page);
    const catalog = pluginMarketCatalogFixture({ items: [pluginMarketItemFixture()], updates: 0 });
    await page.route("**/api/plugin-market/catalog", (route) => json(route, catalog));
    await page.goto("/plugins/market", { waitUntil: "networkidle" });
    await page.getByTestId("plugin-market-detail-open-game24").click();
    const detail = page.getByTestId("plugin-market-detail-game24");
    await expect(detail).toBeVisible();
    await expect(detail).toContainText("群内 24 点小游戏。");
    await expect(detail.getByTestId("plugin-market-detail-channels")).toHaveCount(0);
    await expect(detail.getByTestId("plugin-market-detail-release-notes")).toHaveCount(0);
    await expect(detail.getByTestId("plugin-market-detail-install")).toBeEnabled();
  });
});
