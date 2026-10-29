import { expect, test, type Route } from "@playwright/test";

import { installApiFixture, pluginMarketCatalogFixture, pluginMarketItemFixture } from "./fixtures";

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
