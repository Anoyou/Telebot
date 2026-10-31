import { expect, test, type Page } from "@playwright/test";

import { agentSessionFixture, installAgentAccountFixture, installApiFixture } from "./fixtures";

// 第 22 轮：每个 Agent 会话记住上次选择的本轮模型（存服务器会话上，换设备也记得）；
// 新会话默认「自动」；所选模型被删除 / 停用时回落「自动」且不报错。回答者名称统一为 TelePilot。

const modelMatrix = [
  { provider_id: 2, provider_name: "Grok", model: "grok-4.20-fast", declared_supports_tools: true, probed_supports_tools: true, probed_status: "supported" },
  { provider_id: 3, provider_name: "OpenAI", model: "gpt-5.1", declared_supports_tools: true, probed_supports_tools: true, probed_status: "supported" },
];

const sessionA = agentSessionFixture({
  id: "model-session-a", title: "固定 Grok 的会话",
  model_selection: { mode: "pinned", provider_id: 2, model: "grok-4.20-fast", execution_backend: "provider" },
});
const sessionB = agentSessionFixture({ id: "model-session-b", title: "从没选过模型的会话", model_selection: null });
const sessionGone = agentSessionFixture({
  id: "model-session-gone", title: "模型已删除的会话",
  model_selection: { mode: "pinned", provider_id: 9, model: "deleted-model", execution_backend: "provider" },
});

const picker = (page: Page) => page.locator("[data-assistant-composer]").first().getByRole("button", { name: "本轮模型" });

async function install(page: Page) {
  const fixture = await installApiFixture(page);
  const state = await installAgentAccountFixture(page, { sessions: [sessionA, sessionB, sessionGone].map((item) => ({ ...item })) });
  await page.route((url) => url.pathname === "/api/system-agent/capabilities", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      enabled: true, ai_enabled: true, timezone: "Asia/Shanghai", tools: [], stage: 1,
      write_tools_available: false, model_matrix: modelMatrix,
    }),
  }));
  await page.route((url) => url.pathname === `/api/system-agent/sessions/${sessionA.id}/messages`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([
      { id: 1, session_id: sessionA.id, role: "user", content: { text: "你好" }, run_status: "completed", created_at: "Wed 2026-10-21 8:00 AM CST (UTC+08:00)" },
      { id: 2, session_id: sessionA.id, role: "assistant", content: { text: "你好，我是 TelePilot。" }, run_status: "completed", created_at: "Wed 2026-10-21 8:00 AM CST (UTC+08:00)" },
    ]),
  }));
  return { fixture, state };
}

test.describe("Agent 会话记住模型（第 22 轮）", () => {
  test.beforeEach(({}, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "手机布局模型选择收在折叠头部，桌面 / 平板覆盖");
  });

  test("打开会话恢复服务器上记住的模型；切换会话各自独立", async ({ page }) => {
    const { fixture } = await install(page);
    await page.goto(`/assistant?session=${sessionA.id}`, { waitUntil: "networkidle" });
    await expect(picker(page)).toContainText("grok-4.20-fast");
    // 回答者名称
    await expect(page.getByTestId("assistant-sender").first()).toHaveAttribute("aria-label", "TelePilot");

    await page.goto(`/assistant?session=${sessionB.id}`, { waitUntil: "networkidle" });
    await expect(picker(page)).toContainText("自动路由");
    fixture.assertClean();
  });

  test("选择模型后写到服务器会话上，清空本机记忆后重进仍恢复", async ({ page }) => {
    const { fixture, state } = await install(page);
    await page.goto(`/assistant?session=${sessionB.id}`, { waitUntil: "networkidle" });
    await expect(picker(page)).toContainText("自动路由");
    await picker(page).click();
    await page.getByRole("menuitem").filter({ hasText: "gpt-5.1" }).click();
    await expect.poll(() => state.patches.filter((item) => item.id === sessionB.id).length).toBe(1);
    expect(state.patches.find((item) => item.id === sessionB.id)?.body).toEqual({
      model_selection: { mode: "pinned", provider_id: 3, model: "gpt-5.1", execution_backend: "provider" },
    });
    await expect(picker(page)).toContainText("gpt-5.1");

    // 模拟换设备：清掉本机记忆后重新进入，仍从服务器恢复
    await page.evaluate(() => window.localStorage.clear());
    await page.goto(`/assistant?session=${sessionB.id}`, { waitUntil: "networkidle" });
    await expect(picker(page)).toContainText("gpt-5.1");
    // 别的会话不受影响
    await page.goto(`/assistant?session=${sessionA.id}`, { waitUntil: "networkidle" });
    await expect(picker(page)).toContainText("grok-4.20-fast");
    fixture.assertClean();
  });

  test("所选模型已被删除：回落「自动」且不报错", async ({ page }) => {
    const { fixture } = await install(page);
    await page.goto(`/assistant?session=${sessionGone.id}`, { waitUntil: "networkidle" });
    await expect(picker(page)).toContainText("自动路由");
    await expect(page.locator("[data-sonner-toast][data-type='error']")).toHaveCount(0);
    fixture.assertClean();
  });

  test("新会话默认「自动」，不沿用上一个会话的模型", async ({ page }) => {
    const { fixture } = await install(page);
    await page.goto(`/assistant?session=${sessionA.id}`, { waitUntil: "networkidle" });
    await expect(picker(page)).toContainText("grok-4.20-fast");
    await page.getByRole("button", { name: "新建", exact: true }).first().click();
    await expect(picker(page)).toContainText("自动路由");
    fixture.assertClean();
  });
});
