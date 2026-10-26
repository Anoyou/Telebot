import { expect, test, type Page } from "@playwright/test";

import { installApiFixture } from "./fixtures";

const LONG_LINE =
  "- 结果整理成表格，列为：标题、大小、做种/下载、优惠（Free/2xFree/50% 等）、发布时间。标题列写成加粗的详情页链接 `**[种子标题](详情页链接)**`（标题本身就是链接，搜索结果里的“标题链接”可直接照抄，副标题要点可放在结论或备注里），不要再单独放“详情页”列或“[详情](链接)”。按做种数从高到低排列，同名多版本时优先显示官种。";
const UNBROKEN = "https://example.com/" + "a".repeat(260);

const bundledSkill = {
  name: "group-helper",
  description: "群聊答疑助手的能力与行为准则：帮群友搜索、查询、总结信息；站点规则类问题优先依据站点 Wiki；在管理员指定的群里可只读搜索管理员配置的 PT 站点种子并整理成表格。",
  builtin: false,
  bundled: true,
  owner_plugin: "interaction-ai-chat",
  editable: true,
  uninstallable: false,
  user_modified: true,
  upstream_changed: true,
  upstream_version: "0.4.0",
  source_type: "plugin_bundled",
  enabled_agent: false,
  enabled_plugins: true,
  plugin_supported: true,
  updatable: false,
  file_count: 1,
  total_bytes: 4096,
  version: null,
};

const githubSkill = {
  ...bundledSkill,
  name: "pdf-helper",
  description: "拆分、合并与提取 PDF 文本的操作说明。",
  bundled: false,
  owner_plugin: null,
  editable: false,
  uninstallable: true,
  user_modified: false,
  upstream_changed: false,
  upstream_version: null,
  source_type: "github",
  source_url: "https://github.com/example/skills/tree/main/pdf-helper",
  enabled_agent: true,
  updatable: true,
};

const skillMd = [
  "---",
  "name: group-helper",
  "description: 群聊答疑助手的能力与行为准则：帮群友搜索、查询、总结信息；站点规则类问题优先依据站点 Wiki；在管理员指定的群里可只读搜索管理员配置的 PT 站点种子并整理成表格。回答任何群友问题前都应遵循。",
  "---",
  "",
  "# 群聊答疑助手",
  "",
  "## PT 站点种子搜索",
  "",
  LONG_LINE,
  UNBROKEN,
  "",
  ...Array.from({ length: 30 }, (_, index) => `- 第 ${index + 1} 条规则：保持简短。`),
].join("\n");

const upstreamMd = skillMd
  .replace("## PT 站点种子搜索", "## PT 站点搜索")
  .replace(LONG_LINE, LONG_LINE.replace("做种/下载、", ""))
  .replace(`${UNBROKEN}\n`, "");

const diff = [
  "--- 默认版本（插件内置）",
  "+++ 当前版本（你的修改）",
  "@@ -16,13 +16,14 @@",
  " 5. **PT 站点搜索**（仅在管理员开启 PT 的群/用户可用，详见下文）。",
  " 6. **站点 Wiki**：站点规则、新手、蝌蚪/魔力、保种区、工具配置类问题优先查站点 Wiki。",
  " ",
  " 背景一",
  " 背景二",
  " 背景三",
  "-## PT 站点搜索",
  "+## PT 站点种子搜索",
  " ",
  "-" + LONG_LINE.replace("做种/下载、", ""),
  "+" + LONG_LINE,
  "+" + UNBROKEN,
  " - 搜不到就直说「站内没搜到『关键词』」，并建议换关键词。",
  " 尾部一",
  " 尾部二",
  " 尾部三",
  " 尾部四",
].join("\n");

async function installSkillRoutes(page: Page) {
  await page.route("**/api/agent-skills", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [githubSkill, bundledSkill],
        limits: { max_files: 64, max_file_bytes: 262144, max_skill_bytes: 1048576, max_archive_bytes: 10485760, agent_load_max_chars: 20000, plugin_load_max_chars: 8000 },
      }),
    });
  });
  await page.route("**/api/agent-skills/group-helper", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...bundledSkill, skill_md: skillMd, files: [{ path: "SKILL.md", size: 4096 }], skipped_files: [] }),
    });
  });
  await page.route("**/api/agent-skills/group-helper/upstream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        name: "group-helper",
        owner_plugin: "interaction-ai-chat",
        user_modified: true,
        upstream_changed: true,
        upstream_version: "0.4.0",
        current_skill_md: skillMd,
        upstream_skill_md: upstreamMd,
        diff,
      }),
    });
  });
}

/** 断言 root 内没有任何元素横向超出 root 的右边界（允许 1px 误差）。 */
async function expectNoHorizontalOverflow(page: Page, selector: string) {
  const report = await page.locator(selector).evaluate((root) => {
    const box = root.getBoundingClientRect();
    const offenders = Array.from(root.querySelectorAll<HTMLElement>("*"))
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width > 0 && (rect.right > box.right + 1 || rect.left < box.left - 1);
      })
      .slice(0, 8)
      .map((element) => `${element.tagName.toLowerCase()}.${String(element.className).slice(0, 60)} → ${Math.round(element.getBoundingClientRect().right)}`);
    const scrollers = Array.from(root.querySelectorAll<HTMLElement>("textarea, [data-testid=agent-skill-diff] *"))
      .filter((element) => !element.classList.contains("sr-only") && element.scrollWidth > element.clientWidth + 1)
      .slice(0, 8)
      .map((element) => `${element.tagName.toLowerCase()} scrollWidth=${element.scrollWidth} clientWidth=${element.clientWidth}`);
    return {
      dialogRight: Math.round(box.right),
      viewport: document.documentElement.clientWidth,
      offenders,
      scrollers,
    };
  });
  expect(report.dialogRight, JSON.stringify(report)).toBeLessThanOrEqual(report.viewport);
  expect(report.offenders, JSON.stringify(report)).toEqual([]);
  expect(report.scrollers, JSON.stringify(report)).toEqual([]);
}

test.describe("Agent Skills 独立入口与详情对话框", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "只在 Chromium 项目运行");

  for (const theme of ["light", "dark"] as const) {
    test(`Skills 按钮在配置旁、对话框不溢出（${theme}）`, async ({ page }, testInfo) => {
      const fixture = await installApiFixture(page);
      await installSkillRoutes(page);
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.goto("/assistant", { waitUntil: "networkidle" });

      const mobile = testInfo.project.name === "mobile";
      const skillsButton = page.locator(`[data-assistant-skills-button="${mobile ? "mobile" : "desktop"}"]`);
      // 窄屏不展开摘要也能直接点到
      await expect(skillsButton).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath(`assistant-skills-entry-${testInfo.project.name}-${theme}.png`) });
      if (!mobile) {
        // 与「配置」按钮同一行相邻
        const config = page.getByRole("button", { name: "配置", exact: true });
        await expect(config).toBeVisible();
        const [skillsBox, configBox] = await Promise.all([skillsButton.boundingBox(), config.boundingBox()]);
        expect(Math.abs((skillsBox?.y ?? 0) - (configBox?.y ?? 0))).toBeLessThanOrEqual(2);
        expect((configBox?.x ?? 0) - ((skillsBox?.x ?? 0) + (skillsBox?.width ?? 0))).toBeLessThanOrEqual(12);
        // 配置面板里不再出现 Skills 区块
        await config.click();
        await expect(page.locator("[data-assistant-config-panel]")).toBeVisible();
        await expect(page.locator("[data-assistant-config-panel] [data-testid=agent-skills-section]")).toHaveCount(0);
        await config.click();
      }

      await skillsButton.click();
      const listDialog = page.getByTestId("agent-skills-dialog");
      await expect(listDialog).toBeVisible();
      await expect(listDialog.getByRole("heading", { name: "Skills" })).toBeVisible();
      await expect(listDialog.getByText("pdf-helper")).toBeVisible();
      await expectNoHorizontalOverflow(page, "[data-testid=agent-skills-dialog]");
      await page.screenshot({ path: testInfo.outputPath(`agent-skills-list-${testInfo.project.name}-${theme}.png`) });

      await listDialog.getByRole("button", { name: /group-helper/ }).click();
      const detail = page.getByTestId("agent-skill-detail");
      await expect(detail).toBeVisible();
      const editor = detail.getByRole("textbox", { name: "SKILL.md" });
      await expect(editor).toBeVisible();
      await expectNoHorizontalOverflow(page, "[data-testid=agent-skill-detail]");
      if (testInfo.project.name === "desktop") {
        // 加宽：桌面下对话框至少 900px（原先 max-w-3xl = 768px）
        const box = await detail.boundingBox();
        expect(box?.width ?? 0).toBeGreaterThanOrEqual(900);
      }
      await page.screenshot({ path: testInfo.outputPath(`agent-skill-edit-${testInfo.project.name}-${theme}.png`) });

      await detail.getByRole("tab", { name: "与默认版本对比" }).click();
      const diffView = detail.getByTestId("agent-skill-diff");
      await expect(diffView).toBeVisible();
      // 宽屏默认双栏，窄屏自动统一视图
      await expect(diffView).toHaveAttribute("data-diff-view", mobile ? "unified" : "split");
      await expect(diffView.getByLabel("新增 3 行")).toHaveText("+3");
      await expect(diffView.getByLabel("删除 2 行")).toHaveText("−2");
      await expect(diffView.locator('[data-diff-kind="add"]')).toHaveCount(3);
      await expect(diffView.locator('[data-diff-kind="del"]')).toHaveCount(2);
      // 字词级高亮：只标出新增的“种子”和“做种/下载、”
      const addWords = await diffView.locator('mark[data-diff-word="add"]').allTextContents();
      expect(addWords).toContain("种子");
      expect(addWords.join("")).toContain("做种/下载、");
      // 两侧行号：删除行带默认版本行号，未改动大段折叠为「展开 N 行」
      await expect(diffView.locator("[data-diff-gap]").first()).toContainText(/展开 \d+ 行/);
      await expectNoHorizontalOverflow(page, "[data-testid=agent-skill-detail]");
      const [addBg, delBg, wordBg] = await Promise.all([
        diffView.locator('[data-diff-kind="add"]').first().evaluate((el) => getComputedStyle(el).backgroundColor),
        diffView.locator('[data-diff-kind="del"]').first().evaluate((el) => getComputedStyle(el).backgroundColor),
        diffView.locator('mark[data-diff-word="add"]').first().evaluate((el) => getComputedStyle(el).backgroundColor),
      ]);
      expect(addBg).not.toBe(delBg);
      expect(addBg).not.toBe("rgba(0, 0, 0, 0)");
      expect(wordBg).not.toBe(addBg);
      await page.screenshot({ path: testInfo.outputPath(`agent-skill-diff-${testInfo.project.name}-${theme}.png`) });

      // 展开折叠段后行号连续、仍不溢出
      const gaps = diffView.locator("[data-diff-gap]");
      const before = await gaps.count();
      await gaps.first().click();
      await expect(gaps).toHaveCount(before - 1);
      await diffView.getByRole("button", { name: "全部展开" }).click();
      await expect(gaps).toHaveCount(0);
      await expect(diffView.getByText("第 30 条规则：保持简短。")).toHaveCount(mobile ? 1 : 2);
      await expectNoHorizontalOverflow(page, "[data-testid=agent-skill-detail]");

      // 手动切换另一种视图也不溢出
      await diffView.getByRole("radio", { name: mobile ? "双栏" : "统一" }).click();
      await expect(diffView).toHaveAttribute("data-diff-view", mobile ? "split" : "unified");
      // 切换视图不重复渲染行
      await expect(diffView.locator('[data-diff-kind="add"]')).toHaveCount(3);
      await expect(diffView.locator('[data-diff-kind="del"]')).toHaveCount(2);
      await diffView.getByRole("button", { name: "全部展开" }).click();
      await expect(diffView.getByText("第 30 条规则：保持简短。")).toHaveCount(mobile ? 2 : 1);
      await expectNoHorizontalOverflow(page, "[data-testid=agent-skill-detail]");
      await page.screenshot({ path: testInfo.outputPath(`agent-skill-diff-alt-${testInfo.project.name}-${theme}.png`) });
      fixture.assertClean();
    });
  }

  test("插件配置的“去安装”深链直接打开 Skills 对话框", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "桌面验证一次");
    const fixture = await installApiFixture(page);
    await installSkillRoutes(page);
    await page.goto("/assistant?skills=1", { waitUntil: "networkidle" });
    await expect(page.getByTestId("agent-skills-dialog")).toBeVisible();
    await expect(page).toHaveURL(/\/assistant$/);
    fixture.assertClean();
  });
});
