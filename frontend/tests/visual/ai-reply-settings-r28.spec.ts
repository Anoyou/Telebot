import { expect, test } from "@playwright/test";

import { AI_REPLY_SETTINGS_FIXTURE, installApiFixture } from "./fixtures";

// 0.120.1：AI 回复相关参数从「服务器设置 › 平台与模块 › 指令前缀」挪到「AI › 回复排版与发送」。
test("AI 回复排版与发送：集中显示并可保存开关", async ({ page }, testInfo) => {
  const fixture = await installApiFixture(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let state: Record<string, unknown> = {
    timezone: "Asia/Shanghai",
    command_prefix: ",",
    ai_reply_style: "inline",
    ai_inline_delete_command: true,
    ...AI_REPLY_SETTINGS_FIXTURE,
    login_security: {},
  };
  let saved: unknown;
  await page.route("**/api/system/settings", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      state = { ...state, ...(saved as Record<string, unknown>) };
    }
    await route.fulfill({ json: state });
  });
  await page.goto("/ai?tab=reply", { waitUntil: "networkidle" });
  const card = page.getByTestId("ai-reply-settings");
  await expect(card.getByText("AI 回复排版与发送", { exact: true })).toBeVisible();
  await expect(card.getByLabel("你发 AI 指令时的回复方式")).toHaveValue("inline");
  const viaBot = card.getByRole("switch", { name: "Bot 在群里时由 Bot 回复" });
  await expect(viaBot).toBeChecked();
  await viaBot.click();
  await expect.poll(() => saved).toEqual({ ai_plugin_reply_via_bot: false });
  await expect(viaBot).not.toBeChecked();
  for (const name of ["问题和回答之间加分割线", "回答和署名之间加分割线", "响应「@Bot 问题」提问"]) {
    await expect(card.getByRole("switch", { name })).toBeChecked();
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("ai-reply-settings.png"), fullPage: true });
  fixture.assertClean();
});

test("服务器设置只留去 AI 页的入口", async ({ page }) => {
  const fixture = await installApiFixture(page);
  await page.route(/\/api\/(sudo|notify-bots)(\?.*)?$/, async (route) => {
    await route.fulfill({ json: [] });
  });
  await page.goto("/settings?tab=platform", { waitUntil: "networkidle" });
  await expect(page.getByText("已挪到「AI › 回复排版与发送」", { exact: false })).toBeVisible();
  await expect(page.locator("#ai-reply-style")).toHaveCount(0);
  await page.getByRole("link", { name: "去设置" }).click();
  await expect(page).toHaveURL(/\/ai\?tab=reply/);
  fixture.assertClean();
});
