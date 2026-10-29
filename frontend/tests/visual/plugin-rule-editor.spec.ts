import { expect, test, type Route } from "@playwright/test";

import { installApiFixture } from "./fixtures";

// P4b W4（0.112.0）：交互规则编辑器不再写死插件默认值；参数区按插件 ui.rule_editor 声明渲染，
// 并显示 slot interaction.rule_editor 的卡片。旧的“算数题”规则仍可原样编辑。
const features = [
  {
    key: "demo_game",
    display_name: "示例玩法",
    is_builtin: false,
    version: "1.0.0",
    experimental: false,
    interaction_entries: [
      {
        key: "start_demo",
        title: "开一局",
        interaction_profile: "session_game",
        session_scope: "chat",
        input_schema: { type: "object", properties: { prize: { type: "integer", title: "奖金" } } },
        entry_policy: { session_scope: "chat", prize: true, default_start_keywords: ["开示例"] },
      },
    ],
    rule_editor: {
      entries: {
        start_demo: {
          hint: "来自插件声明的参数区说明",
          start_keywords_placeholder: "开示例局",
          schema: { type: "object", properties: { rounds: { type: "integer", title: "回合数" } } },
          defaults: { rounds: 3 },
        },
      },
    },
  },
];

const legacyMath10Rule = {
  id: "old-math",
  name: "旧算数题",
  enabled: true,
  chat_ids: [-1001],
  trigger_mode: "both",
  action: "math10",
  math_prize: 123,
};

test.describe("交互规则编辑器（P4b W4）", () => {
  // 规则编辑器的双栏布局只在桌面宽度同时展示列表与编辑区；移动端流程由其它基线覆盖。
  test.skip(({ browserName, viewport }) => browserName !== "chromium" || !viewport || viewport.width < 1200, "只在桌面 Chromium 项目运行");

  test("新规则不默认写死插件，参数区来自插件声明，旧算数题规则保留选项", async ({ page }) => {
    await installApiFixture(page);
    const json = (route: Route, body: unknown) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    const slotRequests: string[] = [];
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const pathname = url.pathname;
      if (request.method() !== "GET") return route.fallback();
      if (pathname === "/api/feature-matrix") return json(route, { features, accounts: [] });
      if (pathname === "/api/accounts/1/features") {
        return json(route, [{ feature_key: "demo_game", enabled: true, state: "running", last_error: null, config: {} }]);
      }
      if (pathname === "/api/accounts/1/interaction-bot") {
        return json(route, { enabled: true, trigger_text: "转账成功", response_template: "", rules: [legacyMath10Rule] });
      }
      if (pathname === "/api/plugin-ui/cards" && url.searchParams.get("slot") === "interaction.rule_editor") {
        slotRequests.push(url.searchParams.get("plugin_key") || "");
        return json(route, { slot: "interaction.rule_editor", cards: [], warnings: [] });
      }
      return route.fallback();
    });

    await page.goto("/interaction?aid=1", { waitUntil: "networkidle" });

    const openSection = async (title: string) => {
      const summary = page.locator("details > summary").filter({ hasText: title }).first();
      const details = summary.locator("xpath=..");
      if (!(await details.evaluate((el) => (el as HTMLDetailsElement).open))) await summary.click();
    };

    // 旧算数题规则：动作下拉里仍有“发算数题（旧规则）”，说明文字提示改用启动玩法
    await openSection("启动内容");
    const actionSelect = page.locator("select").filter({ has: page.locator('option[value="notice"]') }).first();
    await expect(actionSelect).toHaveValue("math10");
    await expect(actionSelect.locator('option[value="math10"]')).toHaveText("发算数题（旧规则）");
    await expect(page.getByText("这是旧版“算数题”规则，仍按原方式运行。")).toBeVisible();

    // 新规则：没有“发算数题”选项；切到“启动玩法”后不自动选中任何写死插件
    await page.getByRole("button", { name: "新增规则" }).click();
    await openSection("触发");
    const newAction = page.locator("select:visible").filter({ has: page.locator('option[value="notice"]') }).first();
    await expect(newAction.locator('option[value="math10"]')).toHaveCount(0);
    await newAction.selectOption("module");
    await openSection("启动内容");
    await expect(page.getByText("插件入口未选").first()).toBeVisible();

    // 选中插件入口后：启动词占位、参数区提示与 rule_editor schema 字段来自插件声明
    await page.getByRole("button", { name: /开一局/ }).first().click();
    await page.locator("select:visible").filter({ has: page.locator('option[value="keyword"]') }).first().selectOption("keyword");
    await expect(page.getByPlaceholder("开示例局")).toBeVisible();
    await openSection("插件额外参数与技术详情");
    await expect(page.getByText("来自插件声明的参数区说明")).toBeVisible();
    await expect(page.getByText("回合数")).toBeVisible();
    await expect.poll(() => slotRequests.includes("demo_game")).toBe(true);
  });
});
