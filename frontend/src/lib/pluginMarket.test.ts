import assert from "node:assert/strict";
import { test } from "node:test";

import { filterMarketItems, marketUpdateCount } from "./pluginMarket.ts";

const items = [
  { key: "game24", kind: "interactive", name: { zh: "24 点", en: "game24" }, summary: "算 24", tags: ["game"], installed: { version: "1.0.0" }, update_available: true, pinned_version: null },
  { key: "sum", kind: "utility", name: { zh: "群聊总结", en: "sum" }, summary: "AI 总结", tags: ["llm"], installed: { version: "1.0.0" }, update_available: true, pinned_version: "1.0.0" },
  { key: "todo", kind: "automation", name: { zh: "待办提醒", en: "todo" }, summary: "", tags: [], installed: null, update_available: false, pinned_version: null },
];

test("搜索匹配名称、简介与标签，不区分大小写", () => {
  const base = { kind: "all", onlyUpdates: false, onlyInstalled: false };
  assert.deepEqual(filterMarketItems(items, { ...base, query: "总结" }).map((i) => i.key), ["sum"]);
  assert.deepEqual(filterMarketItems(items, { ...base, query: "GAME" }).map((i) => i.key), ["game24"]);
  assert.equal(filterMarketItems(items, { ...base, query: "" }).length, 3);
});

test("只看可更新排除固定版本；类型与已安装过滤", () => {
  assert.deepEqual(
    filterMarketItems(items, { query: "", kind: "all", onlyUpdates: true, onlyInstalled: false }).map((i) => i.key),
    ["game24"],
  );
  assert.deepEqual(
    filterMarketItems(items, { query: "", kind: "automation", onlyUpdates: false, onlyInstalled: false }).map((i) => i.key),
    ["todo"],
  );
  assert.deepEqual(
    filterMarketItems(items, { query: "", kind: "all", onlyUpdates: false, onlyInstalled: true }).map((i) => i.key),
    ["game24", "sum"],
  );
});

test("更新角标取服务器给出的未固定可更新数", () => {
  assert.equal(marketUpdateCount(undefined), 0);
  assert.equal(marketUpdateCount({ updates_available: 4 }), 4);
});
