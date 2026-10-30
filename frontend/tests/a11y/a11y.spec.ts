import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { installApiFixture } from "../visual/fixtures";

const pages = [
  ["dashboard", "/overview"],
  ["accounts", "/?accounts=1"],
  ["ledger", "/ledger"],
  ["logs", "/logs"],
  ["extensions", "/plugins/manage"],
  ["ai", "/ai"],
  ["llm-providers", "/ai?tab=providers"],
  ["bot", "/accounts/1?tab=bot"],
  // 第 20 轮 Web IA 第二批：账号列表、服务器设置新分页（含移动视口）
  ["accounts-list", "/accounts"],
  ["settings-interface", "/settings?tab=interface"],
  ["settings-security", "/settings?tab=security"],
] as const;

for (const [name, path] of pages) {
  test(`${name} 页面无 critical/serious axe 问题`, async ({ page }, testInfo) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    const fixture = await installApiFixture(page);
    await page.goto(path, { waitUntil: "networkidle" });
    await expect(page.locator("main")).toBeVisible();
    expect(pageErrors, "页面渲染期间出现未捕获异常").toEqual([]);
    fixture.assertClean();
    const results = await new AxeBuilder({ page }).analyze();
    const impactCounts = Object.fromEntries(
      (["critical", "serious", "moderate", "minor"] as const).map((impact) => [
        impact,
        results.violations
          .filter((item) => item.impact === impact)
          .reduce((count, item) => count + item.nodes.length, 0),
      ]),
    );
    console.log(`axe-summary ${testInfo.project.name}/${name} ${JSON.stringify(impactCounts)}`);
    const blockers = results.violations
      .filter((item) => item.impact === "critical" || item.impact === "serious")
      .map((item) => ({
        id: item.id,
        impact: item.impact,
        nodes: item.nodes.map((node) => ({
          target: node.target,
          html: node.html,
          summary: node.failureSummary,
        })),
      }));
    expect(blockers, JSON.stringify(blockers, null, 2)).toEqual([]);
    fixture.assertClean();
  });
}
