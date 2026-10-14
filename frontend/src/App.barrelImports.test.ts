// WP-B18 静态回归：路由装配（App.tsx）不得再经 barrel 整目录
// import("@/pages/Plugins") 拉页面组件——那会把整个目录（含所有兄弟页面）拖进
// 同一个 chunk。逐页直连后 pages/Plugins/index.ts 只保留兼容 re-export。
// 同时守住 WP-B11 IA 迁移后 App.tsx 的新归属导入没有被改回旧路径。
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const app = readFileSync("src/App.tsx", "utf8");
const barrel = readFileSync("src/pages/Plugins/index.ts", "utf8");

test("App.tsx 不再整包导入 @/pages/Plugins", () => {
  const offenders = app
    .split("\n")
    .filter((line) => line.includes("import(\"@/pages/Plugins\")"));
  assert.deepEqual(offenders, [], "路由装配必须直连目标页面模块，不得再经 barrel 整目录");
});

test("插件中心与模板实验室页面已直连本体", () => {
  assert.ok(app.includes('import("@/pages/Plugins/Home")'));
  assert.ok(app.includes('import("@/pages/Plugins/MessageTemplateLab")'));
  assert.ok(app.includes("m.PluginsHome"));
  assert.ok(app.includes("m.MessageTemplateLabPage"));
});

test("barrel 只保留兼容 re-export", () => {
  assert.ok(barrel.includes('export { PluginsHome } from "./Home"'));
  assert.ok(barrel.includes('export { MessageTemplateLabPage } from "./MessageTemplateLab"'));
});

test("IA 迁移后的指令与任务组件仍从 Operations 装配", () => {
  // WP-B11：Templates/Scheduler 搬进 Operations，App.tsx 只保留对 SchedulerConfig 的引用。
  assert.ok(app.includes('import("@/pages/Operations/SchedulerConfig")'));
  assert.ok(!app.includes('import("@/pages/Plugins/TemplatesEditor")'));
  assert.ok(!app.includes('import("@/pages/Plugins/Scheduler")'));
});
