import { expect, test, type Page } from "@playwright/test";

import { installApiFixture, installPluginConfigFixture } from "./fixtures";

// W1 迁移（0.110.0-beta.2）：五个插件的专属配置页改为通用声明式页面，旧页面保留一个版本做对照。
// 设置 W1_SHOT_DIR 时把新旧页面整页截图写到该目录，供人工前后对比（CI 不设置）。
const SHOT_DIR = process.env.W1_SHOT_DIR;

async function shot(page: Page, name: string, projectName: string) {
  if (!SHOT_DIR) return;
  await page.waitForTimeout(150);
  await page.screenshot({ path: `${SHOT_DIR}/${projectName}-${name}.png`, fullPage: true });
}

async function open(page: Page, path: string) {
  await page.goto(path, { waitUntil: "networkidle" });
}

test.describe("W1 插件通用配置页", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "只在 Chromium 项目运行");

  test("auto_reply 通用规则页：列表摘要、字段显隐、新建保存、试运行卡片", async ({ page }, info) => {
    const api = await installApiFixture(page);
    const { writes } = await installPluginConfigFixture(page);
    await open(page, "/accounts/1/features/auto_reply");

    const rows = page.getByTestId("plugin-rule-row");
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText("私聊问候");
    await expect(rows.first()).toContainText("匹配类型：关键词");
    await expect(rows.first()).toContainText("你好、hello");
    await expect(rows.first()).toContainText("作用范围：仅私聊");
    await expect(rows.nth(1)).toContainText("OFF");
    await expect(page.getByTestId("legacy-config-link")).toBeVisible();
    await shot(page, "auto_reply-generic", info.project.name);

    // 新建：作用范围选「指定群」后才出现「指定群 ID」
    await page.getByTestId("plugin-rules-create").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.locator("#plugin-config-group_ids")).toHaveCount(0);
    await dialog.locator("input").first().fill("新规则");
    await dialog.locator("#plugin-config-scope").selectOption("group_specific");
    await expect(dialog.locator("#plugin-config-group_ids")).toBeVisible();
    await dialog.locator("#plugin-config-group_ids").fill("-1001\n-1002");
    // 每行一项：正则里的逗号不会被拆开
    await dialog.locator("#plugin-config-patterns").fill("^签到$\n\\d{1,3}");
    await dialog.locator("#plugin-config-reply").fill("收到 {sender}");
    await shot(page, "auto_reply-generic-edit", info.project.name);
    await dialog.getByRole("button", { name: /保存/ }).click();
    await expect(dialog).toBeHidden();
    const created = writes.find((item) => item.method === "POST" && item.path.endsWith("/rules"));
    expect(created?.body).toMatchObject({
      name: "新规则",
      enabled: true,
      priority: 100,
      config: {
        match: "keyword",
        scope: "group_specific",
        group_ids: ["-1001", "-1002"],
        patterns: ["^签到$", "\\d{1,3}"],
        reply: "收到 {sender}",
        cooldown_scope: "chat",
        reply_to: true,
      },
    });

    // 试运行卡片：行内「试运行」选中规则，填样例消息后运行
    await rows.first().getByRole("button", { name: "试运行" }).click();
    const card = page.getByTestId("plugin-rules-dry-run");
    await expect(card).toBeVisible();
    await card.getByLabel("样例消息").fill("你好呀");
    await card.getByLabel("会话类型").selectOption("private");
    await card.getByTestId("plugin-rules-dry-run-submit").click();
    const result = card.getByTestId("plugin-rules-dry-run-result");
    await expect(result).toContainText("是");
    await expect(result).toContainText("你好 张三");
    const dry = writes.find((item) => item.path.endsWith("/rules/11/dry-run"));
    expect(dry?.body).toEqual({ sample_message: "你好呀", sample_chat_type: "private" });
    await shot(page, "auto_reply-generic-dryrun", info.project.name);

    if (info.project.name === "mobile") {
      // 手机尺寸：规则区不横向溢出
      const overflow = await page.getByTestId("plugin-rules").evaluate((root) => {
        const right = root.getBoundingClientRect().right;
        return Array.from(root.querySelectorAll<HTMLElement>("*")).filter((el) => el.getBoundingClientRect().right > right + 1).length;
      });
      expect(overflow).toBe(0);
    }
    api.assertClean();
  });

  test("autorepeat 通用规则页（整个 config_schema 即规则字段）", async ({ page }, info) => {
    const api = await installApiFixture(page);
    await installPluginConfigFixture(page);
    await open(page, "/accounts/1/features/autorepeat");
    const row = page.getByTestId("plugin-rule-row");
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("群组 ID：-1001234567890");
    await expect(row).toContainText("时间窗口（秒）：300");
    await expect(row).toContainText("最少触发人数：5");
    // 规则字段不再重复渲染成账号配置表单
    await expect(page.locator("#plugin-config-target_chat_id")).toHaveCount(0);
    await expect(page.getByTestId("plugin-rules-dry-run").getByLabel("会话类型")).toHaveCount(0);
    await shot(page, "autorepeat-generic", info.project.name);
    api.assertClean();
  });

  test("chatgpt_image 密钥列表、codex_image / game24 通用表单", async ({ page }, info) => {
    const api = await installApiFixture(page);
    await installPluginConfigFixture(page);
    await open(page, "/accounts/1/features/chatgpt_image");
    // 已保存的密钥与备注渲染在输入框里（遮罩值），按输入框的值断言
    await expect(page.locator("[data-secret-list-item]")).toHaveCount(2);
    await expect(page.getByLabel("第 1 个ChatGPT Token 池")).toHaveValue("eyJh…Qx9z");
    await expect(page.getByLabel("第 1 个备注")).toHaveValue("网页 session");
    // 旧版多行 token 字段已隐藏（保存时由宿主并入列表）
    await expect(page.locator("#plugin-config-token")).toHaveCount(0);
    await shot(page, "chatgpt_image-generic", info.project.name);

    await open(page, "/accounts/1/features/codex_image");
    await expect(page.locator("#plugin-config-command")).toHaveValue("cximg");
    await expect(page.locator("#plugin-config-model")).toHaveValue("gpt-5.5");
    await shot(page, "codex_image-generic", info.project.name);

    await open(page, "/accounts/1/features/game24");
    await expect(page.locator("#plugin-config-command")).toHaveValue("24d");
    await expect(page.locator("#plugin-config-timeout")).toHaveValue("500");
    await shot(page, "game24-generic", info.project.name);
    api.assertClean();
  });

  test("旧专属页保留对照入口；未声明 schema 的旧版 auto_reply 自动落到旧页", async ({ page }, info) => {
    const api = await installApiFixture(page);
    await installPluginConfigFixture(page, { legacyAutoReply: true });
    await open(page, "/accounts/1/features/auto_reply");
    await expect(page).toHaveURL(/\/accounts\/1\/features\/auto_reply\/legacy$/);
    await expect(page.getByTestId("legacy-config-banner")).toBeVisible();
    await expect(page.getByText("私聊问候")).toBeVisible();
    await shot(page, "auto_reply-legacy", info.project.name);

    for (const key of ["autorepeat", "chatgpt_image", "codex_image", "game24"]) {
      await open(page, `/accounts/1/features/${key}/legacy`);
      await expect(page.getByTestId("legacy-config-banner")).toBeVisible();
      await shot(page, `${key}-legacy`, info.project.name);
    }
    api.assertClean();
  });
});
