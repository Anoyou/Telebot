import { expect, test, type Page } from "@playwright/test";

import { accountFixture, agentSessionFixture, installAgentAccountFixture, installApiFixture } from "./fixtures";

// 第 20 轮（#111 后续）：只有 1 个账号时新建 Agent 会话默认关联该账号（多账号保持系统级）；
// 从账号页、插件页进入 Agent 时自动带入该账号。旧服务器（无 features.agent_session_default_account）由客户端按账号数决定。

const secondAccount = { ...accountFixture, id: 2, display_name: "备用账号", tg_username: "backup", phone: "+8613800000002" };
const accountSelect = (page: Page) => page.getByTestId("agent-account-context").first();

async function createFromEmptyState(page: Page) {
  await page.getByRole("button", { name: "新建会话", exact: true }).click();
}

test.describe("Agent 会话账号上下文（第 20 轮）", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "手机布局账号上下文收在折叠头部，桌面 / 平板覆盖");
  });

  test("只有 1 个账号：新会话交给服务器自动关联，并提示可改回系统级", async ({ page }) => {
    const fixture = await installApiFixture(page);
    const state = await installAgentAccountFixture(page);
    await page.goto("/assistant", { waitUntil: "networkidle" });
    await createFromEmptyState(page);
    await expect.poll(() => state.creates.length).toBe(1);
    expect(state.creates[0]).toEqual({ account_id: null, default_account: true });
    await expect(accountSelect(page)).toHaveValue("1");
    await expect(page.getByTestId("agent-account-notice").first()).toContainText("只有一个账号，新会话已自动关联");

    // 改回系统级：立即 PATCH 会话 account_id=null
    await accountSelect(page).selectOption("");
    await expect.poll(() => state.patches.length).toBe(1);
    expect(state.patches[0].body).toEqual({ account_id: null });
    await expect(accountSelect(page)).toHaveValue("");
    fixture.assertClean();
  });

  test("旧服务器只有 1 个账号：客户端自己填 account_id", async ({ page }) => {
    const fixture = await installApiFixture(page);
    const state = await installAgentAccountFixture(page, { serverDefault: false });
    await page.goto("/assistant", { waitUntil: "networkidle" });
    await createFromEmptyState(page);
    await expect.poll(() => state.creates.length).toBe(1);
    expect(state.creates[0]).toEqual({ account_id: 1, default_account: true });
    await expect(accountSelect(page)).toHaveValue("1");
    await expect(page.getByTestId("agent-account-notice").first()).toContainText("自动关联");
    fixture.assertClean();
  });

  test("多个账号：新会话保持系统级", async ({ page }) => {
    const fixture = await installApiFixture(page);
    const state = await installAgentAccountFixture(page, { accounts: [accountFixture, secondAccount] });
    await page.goto("/assistant", { waitUntil: "networkidle" });
    await createFromEmptyState(page);
    await expect.poll(() => state.creates.length).toBe(1);
    expect(state.creates[0]).toEqual({ account_id: null, default_account: true });
    await expect(accountSelect(page)).toHaveValue("");
    await expect(page.getByTestId("agent-account-notice")).toHaveCount(0);
    fixture.assertClean();
  });

  test("账号页「问 Agent」：带入该账号，发送前不建会话，新建时带上账号", async ({ page }) => {
    const fixture = await installApiFixture(page);
    const state = await installAgentAccountFixture(page, {
      accounts: [accountFixture, secondAccount],
      sessions: [agentSessionFixture()],
    });
    await page.goto("/accounts/1", { waitUntil: "networkidle" });
    await page.getByTestId("ask-agent-with-account").click();
    await expect(page.getByTestId("agent-account-notice").first()).toContainText("来自账号页");
    await expect(accountSelect(page)).toHaveValue("1");
    // 参数已被消费，Dock 不因地址变化收起
    await expect(page).toHaveURL(/\/accounts\/1$/);
    await expect(page.locator("[data-assistant-composer]").first()).toBeVisible();
    expect(state.creates).toHaveLength(0);

    await page.getByRole("button", { name: "新建", exact: true }).first().click();
    await expect.poll(() => state.creates.length).toBe(1);
    expect(state.creates[0]).toEqual({ account_id: 1 });
    fixture.assertClean();
  });

  test("在账号页直接打开 Agent：当前会话不是该账号时自动带入", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "桌面悬浮助手入口");
    const fixture = await installApiFixture(page);
    const state = await installAgentAccountFixture(page, {
      accounts: [accountFixture, secondAccount],
      sessions: [agentSessionFixture()],
    });
    await page.goto("/accounts/1", { waitUntil: "networkidle" });
    await page.locator("[data-assistant-desktop-pet]").click();
    await expect(page.getByTestId("agent-account-notice").first()).toContainText("来自账号页");
    await expect(accountSelect(page)).toHaveValue("1");
    expect(state.creates).toHaveLength(0);
    fixture.assertClean();
  });

  test("插件页「问 Agent」：带入所选账号", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await installAgentAccountFixture(page, { accounts: [accountFixture, secondAccount] });
    await page.route((url) => url.pathname === "/api/feature-matrix", (route) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        features: [],
        accounts: [
          { id: 1, name: "视觉测试账号", features: {}, feature_enabled: {} },
          { id: 2, name: "备用账号", features: {}, feature_enabled: {} },
        ],
      }),
    }));
    await page.route((url) => url.pathname === "/api/accounts/2/features", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
    await page.goto("/plugins?account=2", { waitUntil: "networkidle" });
    await page.getByTestId("ask-agent-with-account").click();
    await expect(page.getByTestId("agent-account-notice").first()).toContainText("来自插件页");
    await expect(accountSelect(page)).toHaveValue("2");
    fixture.assertClean();
  });
});
