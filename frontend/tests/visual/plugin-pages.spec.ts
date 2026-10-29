import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Route } from "@playwright/test";

import { installApiFixture } from "./fixtures";

// 插件自带页面与菜单项（ui.pages / ui.actions，Plugin API 1.4）：iframe sandbox + postMessage Bridge。
// 页面里用的是服务器真实注入的 Bridge 脚本（从 backend/app/services/plugin_ui_pages.py 读取），验证 Web 宿主与它同一协议。

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const contract = JSON.parse(readFileSync(resolve(ROOT, "schemas/plugin-ui-widgets.json"), "utf-8"));
const CSP: string = contract.pages.sandbox.csp;
const shimSource = readFileSync(resolve(ROOT, "backend/app/services/plugin_ui_pages.py"), "utf-8");
const SHIM = shimSource.split('BRIDGE_SHIM = r"""')[1].split('"""')[0];

const PAGE_HTML = `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="${CSP.replace(/"/g, "&quot;")}"><script>${SHIM}</script>
<meta charset="utf-8"><title>面板</title></head><body>
<div id="greeting">正在连接宿主…</div><div id="theme"></div><div id="isolation"></div>
<button id="run" type="button">调用插件操作</button><div id="result"></div>
<script>
(async function () {
  var tp = window.telepilot;
  var iso = [];
  try { document.cookie; iso.push("cookie 可读"); } catch (e) { iso.push("cookie 不可读"); }
  try { await fetch("/api/auth/me"); iso.push("可以联网"); } catch (e) { iso.push("联网被阻止"); }
  document.getElementById("isolation").textContent = iso.join(" · ");
  var info = await tp.ready();
  var theme = await tp.theme();
  document.getElementById("theme").textContent = "主题：" + theme.scheme;
  var cfg = await tp.config();
  document.getElementById("greeting").textContent = cfg.config.greeting + " · " + info.plugin_key + " · 密钥字段：" + ("api_token" in cfg.config ? "有" : "无");
  document.getElementById("run").addEventListener("click", async function () {
    try { var r = await tp.action("summarize", {n: 1}); document.getElementById("result").textContent = r.message; }
    catch (e) { document.getElementById("result").textContent = "失败：" + e.code; }
  });
})();
</script></body></html>`;

test.describe("插件页面与菜单", () => {
  test.skip(({ browserName }) => browserName !== "chromium", "只在 Chromium 项目运行");

  test("账号详情：入口、沙箱页面 Bridge 往返、菜单项确认", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "手机尺寸覆盖即可");
    await installApiFixture(page);
    const bridgeCalls: { method: string; params: unknown; account_id: unknown }[] = [];
    const menuCalls: unknown[] = [];
    const json = (route: Route, body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    await page.route("**/api/plugin-ui/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      if (request.method() === "GET" && path === "/api/plugin-ui/entries") {
        expect(url.searchParams.get("menu")).toBe("account.detail");
        return json(route, {
          menu: "account.detail",
          pages: [{ plugin_key: "ui_page_demo", plugin_name: "插件页面示例", page_id: "dashboard", title: "ping 面板", subtitle: null, bridge: ["config.read", "actions.invoke"] }],
          actions: [
            { plugin_key: "ui_page_demo", plugin_name: "插件页面示例", action_id: "reset", menu: "account.detail", title: "清零计数", confirm: "清零 ping 计数？", destructive: true, page_id: null },
          ],
          warnings: [],
        });
      }
      if (request.method() === "POST" && path === "/api/plugin-ui/pages/ui_page_demo/dashboard/session") {
        return json(route, {
          plugin_key: "ui_page_demo", page_id: "dashboard", title: "ping 面板", token: "tok", entry: "ui/index.html",
          url: "/api/plugin-ui/page-assets/tok/ui/index.html", expires_at: 4102444800, bridge: ["config.read", "actions.invoke"], protocol: 1, csp: CSP,
        });
      }
      if (request.method() === "GET" && path === "/api/plugin-ui/page-assets/tok/ui/index.html") {
        return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: PAGE_HTML, headers: { "Content-Security-Policy": CSP } });
      }
      if (request.method() === "POST" && path === "/api/plugin-ui/pages/ui_page_demo/dashboard/bridge") {
        const body = request.postDataJSON();
        bridgeCalls.push(body);
        if (body.method === "ready") return json(route, { ok: true, result: { plugin_key: "ui_page_demo", page_id: "dashboard", protocol: 1 } });
        if (body.method === "config.get") return json(route, { ok: true, result: { scope: "account", config: { greeting: "你好" } } });
        if (body.method === "action.invoke") return json(route, { ok: true, result: { ok: true, message: "共 3 次 ping" } });
        return json(route, { ok: false, error: { code: "BRIDGE_METHOD_UNKNOWN", message: "未知" } });
      }
      if (request.method() === "POST" && path === "/api/plugin-ui/actions/ui_page_demo/reset") {
        menuCalls.push(request.postDataJSON());
        return json(route, { ok: true, message: "已清零", refresh: true, data: null });
      }
      return route.fallback();
    });

    await page.goto("/accounts/1", { waitUntil: "networkidle" });
    const entries = page.locator('[data-plugin-ui-entries="account.detail"]');
    await entries.scrollIntoViewIfNeeded();
    await expect(entries.getByText("插件页面与操作", { exact: true })).toBeVisible();
    await expect(entries.locator('[data-plugin-page="dashboard"]')).toContainText("插件页面示例");

    // 菜单项：有确认文案，确认后才调用，不自动重试
    await entries.locator('[data-plugin-menu-action="reset"]').click();
    const confirm = page.getByRole("dialog", { name: "清零计数" });
    await expect(confirm.getByText("清零 ping 计数？")).toBeVisible();
    expect(menuCalls).toHaveLength(0);
    await confirm.getByRole("button", { name: "确定" }).click();
    await expect.poll(() => menuCalls.length).toBe(1);
    expect(menuCalls[0]).toMatchObject({ account_id: 1, menu: "account.detail" });
    await expect(page.getByText("已清零")).toBeVisible();

    // 页面：sandbox 只有 allow-scripts；Bridge 往返；theme.get 由宿主本地回答
    await entries.locator('[data-plugin-page="dashboard"]').click();
    const dialog = page.getByRole("dialog", { name: "ping 面板" });
    await expect(dialog).toBeVisible();
    const iframe = dialog.locator('iframe[data-plugin-page-frame="dashboard"]');
    await expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
    const frame = page.frameLocator('iframe[data-plugin-page-frame="dashboard"]');
    await expect(frame.locator("#greeting")).toHaveText("你好 · ui_page_demo · 密钥字段：无");
    await expect(frame.locator("#theme")).toContainText("主题：");
    await expect(frame.locator("#isolation")).toHaveText("cookie 不可读 · 联网被阻止");
    await frame.locator("#run").click();
    await expect(frame.locator("#result")).toHaveText("共 3 次 ping");
    expect(bridgeCalls.map((call) => call.method)).toEqual(["ready", "config.get", "action.invoke"]);
    expect(bridgeCalls.every((call) => call.account_id === 1)).toBe(true);
    expect(bridgeCalls[2].params).toEqual({ id: "summarize", payload: { n: 1 } });
    const box = await iframe.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThan(300);
  });

  test("没有声明页面的插件不显示入口", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile", "手机尺寸覆盖即可");
    await installApiFixture(page);
    await page.route("**/api/plugin-ui/entries**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ menu: "account.detail", pages: [], actions: [], warnings: [] }) }),
    );
    await page.goto("/accounts/1", { waitUntil: "networkidle" });
    await expect(page.locator("[data-plugin-ui-entries]")).toHaveCount(0);
  });
});
