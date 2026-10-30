// IA 迁移收尾（WP-B11）静态回归：
// 「指令与任务」工作台的页面壳（Templates / Scheduler / AutoCommandWhitelist / Index）
// 不得再从 @/pages/Plugins 反向导入**页面组件**——交叉 import 说明页面归属错了工作区，
// 会把两个工作区的 chunk 牵连在一起。允许复用共享工具（featureConfig / configs/_shared），
// 它们本来就是跨工作区的公共实现。
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";

const dir = "src/pages/Operations";
const PAGE_SHELLS = ["Templates.tsx", "Scheduler.tsx", "SchedulerCommandWhitelist.tsx", "Index.tsx"];

test("指令与任务页面壳不再从 @/pages/Plugins 导入页面组件", () => {
  for (const name of PAGE_SHELLS) {
    const source = readFileSync(`${dir}/${name}`, "utf8");
    const offenders = source
      .split("\n")
      .filter((line) => line.includes("@/pages/Plugins/") && !line.includes("/_shared"));
    assert.deepEqual(offenders, [], `${dir}/${name} 仍引用 Plugins 页面组件`);
  }
});

test("共用组件本体位于 Operations 目录内", () => {
  for (const name of ["TemplatesEditor.tsx", "SchedulerConfig.tsx"]) {
    const source = readFileSync(`${dir}/${name}`, "utf8");
    assert.ok(source.length > 0, `${name} 必须存在于 Operations 目录`);
  }
});

test("路由 URL 保持不变", () => {
  const index = readFileSync(`${dir}/Index.tsx`, "utf8");
  assert.ok(index.includes('path="templates"'));
  assert.ok(index.includes('path="scheduler"'));
  // 第 20 轮第二批：白名单并入定时任务，旧地址保留为跳转
  assert.ok(index.includes('path="auto-command-whitelist"'));
  assert.ok(index.includes("SchedulerWhitelistRedirect"));
  assert.ok(index.includes('<Navigate to="/operations/templates" replace />'));
});

test("账号级插件配置页仍复用同一份调度组件", () => {
  const app = readFileSync("src/App.tsx", "utf8");
  assert.ok(app.includes('import("@/pages/Operations/SchedulerConfig")'));
});

test("Operations 目录不再出现对被搬迁文件的旧路径引用", () => {
  const files = readdirSync(dir)
    .filter((name) => name.endsWith(".tsx") && !name.endsWith(".test.ts"))
    .map((name) => `${dir}/${name}`);
  for (const file of files) {
    const source = readFileSync(file, "utf8");
    assert.ok(!source.includes("pages/Plugins/TemplatesEditor"), `${file} 引用了旧 TemplatesEditor 路径`);
    assert.ok(!source.includes("pages/Plugins/configs/Scheduler"), `${file} 引用了旧 Scheduler 路径`);
  }
});
