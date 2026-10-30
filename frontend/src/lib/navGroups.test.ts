import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_PRIMARY_NAV,
  groupNavPaths,
  mergeGroupOrder,
  navGroupOf,
  normalizePrimaryNav,
  reorderWithinGroup,
} from "./navGroups.ts";

const ALL = [
  "/plugins",
  "/ai",
  "/interaction",
  "/operations",
  "/overview",
  "/accounts",
  "/ledger",
  "/webhooks",
  "/dispatch-debug",
  "/logs",
  "/settings",
];

test("默认三组：常用 插件·交互·概览；管理 账号·AI·指令与任务·资金台账·日志；工具与设置", () => {
  const groups = groupNavPaths({ visible: ALL });
  assert.deepEqual(groups.primary, ["/plugins", "/interaction", "/overview"]);
  assert.deepEqual(groups.manage, ["/accounts", "/ai", "/operations", "/ledger", "/logs"]);
  assert.deepEqual(groups.tools, ["/webhooks", "/dispatch-debug", "/settings"]);
});

test("常用入口清洗：去掉三级入口与未知路径、去重、最多 4 个，空时回默认", () => {
  assert.deepEqual(normalizePrimaryNav(["/settings", "/ai", "/ai", "/nope"]), ["/ai"]);
  assert.deepEqual(normalizePrimaryNav([]), DEFAULT_PRIMARY_NAV);
  assert.deepEqual(normalizePrimaryNav(undefined), DEFAULT_PRIMARY_NAV);
  assert.deepEqual(
    normalizePrimaryNav(["/ai", "/logs", "/ledger", "/accounts", "/plugins"]),
    ["/ai", "/logs", "/ledger", "/accounts"],
  );
});

test("移出常用的入口回到管理组最前；选进常用的从管理组消失", () => {
  const groups = groupNavPaths({ visible: ALL, primaryNav: ["/ai", "/plugins"] });
  assert.deepEqual(groups.primary, ["/ai", "/plugins"]);
  assert.deepEqual(groups.manage, ["/overview", "/interaction", "/accounts", "/operations", "/ledger", "/logs"]);
});

test("模块关闭：入口隐藏，常用组少一格", () => {
  const visible = ALL.filter((path) => path !== "/interaction" && path !== "/webhooks");
  const groups = groupNavPaths({ visible });
  assert.deepEqual(groups.primary, ["/plugins", "/overview"]);
  assert.deepEqual(groups.tools, ["/dispatch-debug", "/settings"]);
});

test("sidebar_order 只影响管理与工具组的组内顺序", () => {
  const groups = groupNavPaths({
    visible: ALL,
    sidebarOrder: ["/logs", "/settings", "/overview", "/plugins", "/ai"],
  });
  assert.deepEqual(groups.primary, ["/plugins", "/interaction", "/overview"]);
  assert.deepEqual(groups.manage, ["/logs", "/ai", "/accounts", "/operations", "/ledger"]);
  assert.deepEqual(groups.tools, ["/settings", "/webhooks", "/dispatch-debug"]);
});

test("组内拖动与合并", () => {
  assert.deepEqual(reorderWithinGroup(["/a", "/b", "/c"], "/c", "/a"), ["/c", "/a", "/b"]);
  assert.equal(reorderWithinGroup(["/a", "/b"], "/a", "/x"), null);
  assert.deepEqual(
    mergeGroupOrder(["/p", "/a", "/q", "/b"], ["/b", "/a"]),
    ["/p", "/b", "/q", "/a"],
  );
});

test("navGroupOf", () => {
  assert.equal(navGroupOf("/plugins"), "primary");
  assert.equal(navGroupOf("/accounts"), "manage");
  assert.equal(navGroupOf("/settings"), "tools");
  assert.equal(navGroupOf("/plugins", ["/ai"]), "manage");
  assert.equal(navGroupOf("/nope"), null);
});
