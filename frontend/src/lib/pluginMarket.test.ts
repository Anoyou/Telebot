import assert from "node:assert/strict";
import { test } from "node:test";

import {
  channelLabel,
  describePermission,
  filterMarketItems,
  hasMultipleChannels,
  isChannelDowngrade,
  kindCounts,
  marketChannels,
  marketUpdateCount,
  renderUsage,
} from "./pluginMarket.ts";

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

test("分类标签按固定顺序计数，未知类型排最后", () => {
  assert.deepEqual(kindCounts([...items, { kind: "zz" }, { kind: "interactive" }]), [
    { kind: "interactive", count: 2 },
    { kind: "automation", count: 1 },
    { kind: "utility", count: 1 },
    { kind: "zz", count: 1 },
  ]);
});

test("搜索也匹配完整简介和指令", () => {
  const extended = [{ ...items[2], description: "支持自然语言时间", commands: ["todo", "undo"] }];
  const base = { kind: "all", onlyUpdates: false, onlyInstalled: false };
  assert.equal(filterMarketItems(extended, { ...base, query: "自然语言" }).length, 1);
  assert.equal(filterMarketItems(extended, { ...base, query: "undo" }).length, 1);
});

test("权限、通道、使用说明的显示", () => {
  assert.match(describePermission("payout"), /游戏币/);
  assert.equal(describePermission("brand_new"), "brand_new");
  assert.equal(channelLabel("beta"), "测试版");
  assert.equal(channelLabel("nightly"), "nightly");
  assert.equal(renderUsage("发送 {prefix}todo 与 {prefix}undo"), "发送 ,todo 与 ,undo");
  assert.equal(renderUsage(null), "");
});

test("旧服务器没有通道字段时隐藏通道界面", () => {
  assert.deepEqual(marketChannels({}), []);
  assert.equal(hasMultipleChannels({ available_channels: [{ name: "stable", version: "1.0.0" }] }), false);
  assert.equal(
    hasMultipleChannels({ available_channels: [{ name: "stable" }, { name: "beta", version: null }] }),
    true,
  );
});

test("切换通道是否降级", () => {
  assert.equal(isChannelDowngrade("1.1.0-beta.1", "1.0.0"), true);
  assert.equal(isChannelDowngrade("1.0.0", "1.1.0-beta.1"), false);
  assert.equal(isChannelDowngrade("1.0.0", "1.0.0-beta.1"), true);
  assert.equal(isChannelDowngrade("1.0.0-beta.2", "1.0.0-beta.1"), true);
  assert.equal(isChannelDowngrade("1.0.0", null), false);
});
