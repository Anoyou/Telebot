import { expect, test, type Page } from "@playwright/test";

import { installApiFixture, paymentSourceStatusFixture } from "./fixtures";

const KEY = "payment-source-cn-transfer";

const plugin = {
  key: KEY,
  display_name: "中文转账通知",
  source: "repo",
  source_url: "https://github.com/example/plugins/tree/main/payment-source-cn-transfer",
  source_label: "telebot-plugins",
  version: "1.2.0",
  global_enabled: true,
  signature_ok: true,
  trust_tier: "official",
  lint_warnings: [],
  update: { update_available: false, latest_version: null, last_update_check_at: null, last_update_check_error: null },
  accounts: [],
  recent_load_error: null,
  recent_trace: null,
};

const paymentCard = {
  plugin_key: KEY,
  plugin_name: "中文转账通知",
  card_id: "_host.payment_source",
  slot: "plugin.overview",
  title: "转账识别（只读）",
  subtitle: "插件从可信 Bot 的转账通知里读出的付款人、收款人和金额。只显示，不改任何数据；保留最近 7 天。",
  source_kind: "read",
  host: true,
  refresh_seconds: null,
  blocks: [
    { type: "kv", items: [{ label: "转账通知判定", path: "switch.label", tone_path: "switch.tone" }] },
    {
      type: "stats",
      title: "识别到的转账",
      items: [
        { label: "今日笔数", path: "today.recognized", format: "number" },
        { label: "近 7 日合计", path: "week.amount", format: "number" },
      ],
    },
  ],
};

async function mockPlugin(page: Page, { oldServer }: { oldServer: boolean }) {
  await page.route("**/api/plugins/installed-overview", (route) => route.fulfill({ json: [plugin] }));
  await page.route((url) => url.pathname === "/api/plugin-ui/cards", (route) => {
    const url = new URL(route.request().url());
    const slot = url.searchParams.get("slot") || "";
    const cards = !oldServer && slot === "plugin.overview" && url.searchParams.get("plugin_key") === KEY ? [paymentCard] : [];
    return route.fulfill({ json: { slot, cards, warnings: [] } });
  });
  await page.route(`**/api/plugin-ui/cards/${KEY}/_host.payment_source/data`, (route) =>
    route.fulfill({
      json: {
        plugin_key: KEY, card_id: "_host.payment_source", status: "ok", message: null, updated_at: 1_784_635_200, duration_ms: 4,
        data: { switch: { label: "以旧解析为准（插件只做对照）", tone: "neutral" }, today: { recognized: 3 }, week: { amount: 12345 } },
      },
    }),
  );
  await page.route("**/api/payment-sources/status", (route) =>
    oldServer ? route.fulfill({ status: 404, json: { detail: "Not Found" } }) : route.fulfill({ json: paymentSourceStatusFixture() }),
  );
}

test.describe("转账通知判定开关与资金类只读卡片（第 22 轮）", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "只在 Chromium 项目运行");

  test("转账通知插件详情显示开关状态、大白话说明和只读卡片", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await mockPlugin(page, { oldServer: false });
    await page.goto(`/plugins/manage?detail=${KEY}`, { waitUntil: "networkidle" });
    const dialog = page.getByRole("dialog", { name: "中文转账通知" });
    await expect(dialog).toBeVisible();
    const section = dialog.getByTestId("payment-source-switch");
    await expect(section).toBeVisible();
    await expect(section.getByTestId("payment-source-switch-state")).toHaveText("以旧解析为准（插件只做对照）");
    // 状态以服务器为准：关闭（没有替用户打开）
    await expect(section.getByTestId("payment-source-switch-toggle")).toHaveAttribute("aria-checked", "false");
    await expect(section).toContainText("关：以系统自带的旧解析为准");
    await expect(section).toContainText("开：以这个插件读出的付款人、收款人和金额为准");
    const card = dialog.locator('[data-plugin-card="_host.payment_source"]');
    await card.scrollIntoViewIfNeeded();
    await expect(card.getByText("转账识别（只读）", { exact: true })).toBeVisible();
    await expect(card).toContainText("12,345");
    // 只读卡片没有任何按钮
    await expect(card.getByRole("button", { name: /清零|重置|结算/ })).toHaveCount(0);
    fixture.assertClean();
  });

  test("旧服务器（没有状态接口和宿主卡）不显示开关区、不报错", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await mockPlugin(page, { oldServer: true });
    await page.goto(`/plugins/manage?detail=${KEY}`, { waitUntil: "networkidle" });
    const dialog = page.getByRole("dialog", { name: "中文转账通知" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("当前版本", { exact: true })).toBeVisible();
    await expect(dialog.getByTestId("payment-source-switch")).toHaveCount(0);
    await expect(dialog.locator('[data-plugin-card="_host.payment_source"]')).toHaveCount(0);
    fixture.assertClean();
  });
});
