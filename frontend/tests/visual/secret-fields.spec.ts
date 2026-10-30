import { expect, test } from "@playwright/test";

import { SECRET_REVEAL_FIXTURE, installApiFixture } from "./fixtures";

// 第 20 轮：密钥 / 密码 / Token 输入框统一为 SecretInput——有标题、圆点遮罩、右侧眼睛；
// 已保存的值点眼睛时才请求 /api/secrets/reveal，新输入的值眼睛只在本地切换。

const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

test.describe("密钥输入框（第 20 轮）", () => {
  test("管理 Bot Token：有标题，已保存值点眼睛才读取明文，再点隐藏", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile", "手机布局账号详情走另一套入口，桌面 / 平板覆盖");
    const fixture = await installApiFixture(page);
    await page.route((url) => url.pathname === "/api/accounts/1/bot/capabilities", async (route) => {
      await route.fulfill(json({
        account_id: 1,
        guest_updates_enabled: true,
        management_bot: { role: "management", configured: true, username: "fixture_admin_bot" },
        interaction_bot: { role: "interaction", configured: false },
        guest_mode_help: "",
      }));
    });
    await page.route((url) => url.pathname === "/api/accounts/1/bot", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill(json({
        account_id: 1, enabled: true, status: "running", has_token: true, username: "fixture_admin_bot",
        remote_plugin_policy: { enabled: false, install: false, update: false, uninstall: false, enable_disable: false },
      }));
    });
    const revealRequests: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/secrets/reveal") revealRequests.push(url.search);
    });
    await page.goto("/accounts/1?tab=bot", { waitUntil: "networkidle" });

    const input = page.getByLabel("管理 Bot Token", { exact: true });
    await expect(input).toBeVisible();
    await expect(input).toHaveAttribute("type", "password");
    await expect(input).toHaveAttribute("placeholder", /已配置 ••••••••/);
    await expect(input).toHaveValue("");
    expect(revealRequests).toEqual([]);

    const field = page.locator('[data-secret-input="management_bot_token"]');
    await field.getByRole("button", { name: "显示" }).click();
    await expect(input).toHaveAttribute("type", "text");
    await expect(input).toHaveValue(SECRET_REVEAL_FIXTURE.management_bot_token);
    expect(revealRequests).toHaveLength(1);
    expect(revealRequests[0]).toContain("kind=management_bot_token");
    expect(revealRequests[0]).toContain("account_id=1");

    await field.getByRole("button", { name: "隐藏" }).click();
    await expect(input).toHaveAttribute("type", "password");
    await expect(input).toHaveValue("");

    // 新输入的值：眼睛只在本地切换，不再请求服务器。
    await input.fill("123:NEW-TOKEN");
    await field.getByRole("button", { name: "显示" }).click();
    await expect(input).toHaveAttribute("type", "text");
    await expect(input).toHaveValue("123:NEW-TOKEN");
    expect(revealRequests).toHaveLength(1);
    fixture.assertClean();
  });

  test("Webhook Token 默认圆点遮罩，点眼睛显示", async ({ page }) => {
    const fixture = await installApiFixture(page);
    await page.goto("/webhooks?account_id=1", { waitUntil: "networkidle" });
    const token = page.locator("[data-webhook-token]");
    await expect(token).toBeVisible();
    await expect(token).toHaveAttribute("data-webhook-token", "masked");
    await expect(token).not.toContainText("fixture-webhook-token");
    await token.locator("xpath=..").getByRole("button", { name: "显示" }).click();
    await expect(token).toHaveAttribute("data-webhook-token", "visible");
    await expect(token).toContainText("fixture-webhook-token");
    fixture.assertClean();
  });
});
