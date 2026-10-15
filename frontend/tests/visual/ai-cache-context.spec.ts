import { expect, test } from "@playwright/test";

import { installApiFixture, installProviderFixture } from "./fixtures";

test("近期调用展示缓存字段并区分历史未采集记录", async ({ page }, testInfo) => {
  const fixture = await installApiFixture(page);
  await installProviderFixture(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/llm/usage/recent**", async (route) => {
    await route.fulfill({ json: [
      { id: 1, model: "cache-model", provider_name: "测试提供商", input_tokens: 100,
        output_tokens: 20, cache_read_tokens: 80, cache_write_tokens: 0,
        success: true, source: "system_agent", created_at: "2026-10-16T00:00:00Z" },
      { id: 2, model: "legacy-model", provider_name: "测试提供商", input_tokens: 100,
        output_tokens: 20, success: true, source: "command:ai", created_at: "2026-10-16T00:00:00Z" },
    ] });
  });
  await page.goto("/ai?tab=usage", { waitUntil: "networkidle" });
  await expect(page.getByText("读取 80 / 写入 0 Token", { exact: true })).toBeVisible();
  await expect(page.getByText("读 80 / 写 0", { exact: true }).filter({ visible: true })).toBeVisible();
  await expect(page.getByText("读 未采集 / 写 未采集", { exact: true }).filter({ visible: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(errors).toEqual([]);
  await page.getByText("读取 80 / 写入 0 Token", { exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("cache-usage.png"), fullPage: true });
  fixture.assertClean();
});

test("系统设置可保存并回显 AI 指令上下文条数", async ({ page }, testInfo) => {
  const fixture = await installApiFixture(page);
  await page.route(/\/api\/(sudo|notify-bots)(\?.*)?$/, async (route) => {
    await route.fulfill({ json: [] });
  });
  let count = 0;
  let saved: unknown;
  await page.route("**/api/system/settings", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      count = (saved as { ai_context_messages: number }).ai_context_messages;
    }
    await route.fulfill({ json: { timezone: "Asia/Shanghai", command_prefix: ",",
      ai_context_messages: count, login_security: {} } });
  });
  await page.goto("/settings?tab=platform", { waitUntil: "networkidle" });
  const input = page.getByLabel("AI 指令上下文条数", { exact: true });
  await expect(input).toHaveValue("0");
  await input.fill("7");
  await input.locator("../..").getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => saved).toEqual({ ai_context_messages: 7 });
  await page.reload({ waitUntil: "networkidle" });
  await expect(input).toHaveValue("7");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await input.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("ai-context-setting.png"), fullPage: true });
  fixture.assertClean();
});
