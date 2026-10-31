import assert from "node:assert/strict";
import test from "node:test";

import {
  LEGACY_RUNTIME_TREE_ROUTES,
  RUNTIME_TREE_PATH,
  legacySettingsTreeRedirect,
  pluginViewFromSearch,
} from "./runtimeTreeLinks.ts";

test("服务器设置旧运行树分页跳到插件页运行树", () => {
  for (const tab of ["tree", "runtime", "runtime-tree", "platform-tree", "capability-tree"]) {
    assert.equal(legacySettingsTreeRedirect(`?tab=${tab}`), RUNTIME_TREE_PATH, tab);
  }
});

test("服务器设置其它分页照常打开", () => {
  for (const search of ["", "?tab=platform", "?tab=modules", "?tab=interface", "?foo=tree"]) {
    assert.equal(legacySettingsTreeRedirect(search), null, search);
  }
});

test("插件页 ?view=tree 打开运行树，其它为卡片", () => {
  assert.equal(pluginViewFromSearch("?view=tree"), "tree");
  assert.equal(pluginViewFromSearch("?account=2&view=tree"), "tree");
  assert.equal(pluginViewFromSearch(""), "cards");
  assert.equal(pluginViewFromSearch("?view=cards"), "cards");
});

test("旧运行树独立路由都在列表里且不带开头斜杠", () => {
  assert.ok(LEGACY_RUNTIME_TREE_ROUTES.includes("platform-tree"));
  for (const route of LEGACY_RUNTIME_TREE_ROUTES) assert.ok(!route.startsWith("/"), route);
});
