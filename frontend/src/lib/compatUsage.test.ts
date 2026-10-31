import assert from "node:assert/strict";
import test from "node:test";

import type { PluginCompatUsageItem } from "@/api/plugins";

import { compatPluginLabel, sortCompatUsageItems, summarizeCompatUsage } from "./compatUsage.ts";

function item(id: string, total: number, trackable = true): PluginCompatUsageItem {
  return { id, name: id, trackable, counts: "", total, plugin_count: total ? 1 : 0, plugins: [] };
}

test("兼容层统计汇总：有人在用的项数与累计次数", () => {
  const summary = summarizeCompatUsage([item("C01", 0, false), item("C02", 3), item("C03", 0), item("C15", 7)]);
  assert.deepEqual(summary, { trackable: 3, used: 2, total: 10 });
  assert.deepEqual(summarizeCompatUsage(undefined), { trackable: 0, used: 0, total: 0 });
});

test("兼容层统计排序：用到的按次数在前，未用到的保持顺序，无法计数的最后", () => {
  const sorted = sortCompatUsageItems([item("C01", 0, false), item("C02", 3), item("C03", 0), item("C15", 7)]);
  assert.deepEqual(sorted.map((row) => row.id), ["C15", "C02", "C03", "C01"]);
});

test("宿主占位 key 的显示名", () => {
  assert.equal(compatPluginLabel("-"), "宿主（无插件）");
  assert.equal(compatPluginLabel("guess_number"), "guess_number");
});
