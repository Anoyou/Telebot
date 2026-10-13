// 使用指南运行时通道与侧边栏按钮结构的静态回归：
// 防止使用指南被打包进 bundle、nginx 白名单漏放行、或按钮顺序 / 折叠态样式漂移。
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const menu = readFileSync("src/components/layout/UsageGuideMenu.tsx", "utf8");
const sidebar = readFileSync("src/components/layout/Sidebar.tsx", "utf8");
const nginx = readFileSync("nginx.conf", "utf8");
const changelog = readFileSync("src/components/layout/ChangelogMenu.tsx", "utf8");

test("使用指南走 runtime-content 通道而不是打包进 bundle", () => {
  assert.ok(menu.includes('"/runtime-content/docs/USAGE-GUIDE.md"'));
  assert.ok(menu.includes("fetch(USAGE_GUIDE_URL"));
  assert.ok(menu.includes('cache: "no-cache"'));
  // 不能把 Markdown 作为构建期资源导入
  assert.ok(!menu.includes("USAGE-GUIDE.md?raw"));
  assert.ok(!menu.includes("USAGE-GUIDE.md?url"));
  assert.ok(menu.includes("ReactMarkdown"));
  assert.ok(menu.includes("remarkGfm"));
  assert.ok(menu.includes("重新读取"));
});

test("使用指南菜单与更新日志菜单结构保持一致", () => {
  assert.ok(menu.includes('["runtime-content", "usage-guide"]'));
  assert.ok(menu.includes("staleTime: 0"));
  assert.ok(menu.includes("refetchOnWindowFocus: true"));
  assert.ok(menu.includes("prose prose-sm"));
  assert.ok(changelog.includes("prose prose-sm"));
});

test("侧边栏按钮：使用指南在更新日志上方，折叠态不错乱", () => {
  const guideIdx = sidebar.indexOf('aria-label="使用指南"');
  const changelogIdx = sidebar.indexOf('aria-label="更新日志"');
  assert.ok(guideIdx > -1, "必须存在使用指南按钮");
  assert.ok(changelogIdx > -1, "必须存在更新日志按钮");
  assert.ok(guideIdx < changelogIdx, "使用指南必须在更新日志上方");

  // 逐项复用更新日志按钮的样式与行为类
  assert.ok((sidebar.match(/liquid-sidebar-link flex h-11/g) || []).length >= 2);
  assert.ok(sidebar.includes('collapsed && "justify-center px-0"'));
  assert.ok((sidebar.match(/collapsed && "sr-only"/g) || []).length >= 2);
  assert.ok(sidebar.includes('side={mobile ? "top" : "right"}'));
  assert.ok(sidebar.includes("collisionPadding={16}"));
  assert.ok(sidebar.includes("w-[min(28rem,calc(100vw-2rem))]"));
  assert.ok(sidebar.includes('maxHeight: "min(72dvh, 34rem)"'));
  assert.ok(sidebar.includes('lazy(() => import("./UsageGuideMenu"))'));
  assert.ok(sidebar.includes("<UsageGuideMenu />"));
  assert.ok(sidebar.includes('title="使用指南"'));
});

test("nginx 白名单放行使用指南，其它运行时文件仍然 404", () => {
  assert.ok(nginx.includes("|USAGE-GUIDE)"));
  const line = nginx
    .split("\n")
    .find((item) => item.includes("runtime-content/docs/") && item.includes("location ~"));
  assert.ok(line, "必须存在 docs 白名单 location");
  const pattern = /\^\/runtime-content\/docs\/(.+?)\\\.md\$/.exec(line);
  assert.ok(pattern, "白名单正则必须可解析");
  const whitelist = new RegExp(pattern[1]);
  assert.ok(whitelist.test("USAGE-GUIDE.md"), "USAGE-GUIDE.md 必须被放行");
  assert.ok(!whitelist.test("REVISION"), "REVISION 必须保持 404");
  assert.ok(!whitelist.test("SYSTEM-AGENT.md"), "白名单外文档必须保持 404");
});
