import assert from "node:assert/strict";
import test from "node:test";

import { buildCardBlock, CARD_LAYOUT_TYPES, CARD_LAYOUTS_IN_CONTRACT, formatCardValue } from "./pluginCards.ts";
import {
  CONFIG_WIDGET_IDS,
  PLUGIN_UI_CONTRACT,
  resolveConfigWidget,
  type ContractFieldSchema,
} from "./pluginUiContract.ts";

test("Web 实现了契约里的全部控件，且顺序与 resolution_order 一致", () => {
  assert.deepEqual([...CONFIG_WIDGET_IDS], PLUGIN_UI_CONTRACT.resolution_order);
  assert.deepEqual(
    PLUGIN_UI_CONTRACT.widgets.map((item) => item.id),
    PLUGIN_UI_CONTRACT.resolution_order,
  );
});

for (const widget of PLUGIN_UI_CONTRACT.widgets) {
  test(`契约控件 ${widget.id}：示例字段解析为同一控件`, () => {
    const example = widget.example as { key: string; schema: ContractFieldSchema };
    assert.equal(resolveConfigWidget(example.key, example.schema), widget.id);
  });
}

test("不认识的 x-ui-widget 解析为 unknown（只读），不退化成通用编辑器", () => {
  assert.equal(resolveConfigWidget("color", { type: "string", "x-ui-widget": "color-wheel" }), "unknown");
  assert.equal(resolveConfigWidget("items", { type: "array", "x-ui-widget": "kanban", items: { type: "object" } }), "unknown");
  // 隐藏与只读优先于未知控件
  assert.equal(resolveConfigWidget("color", { type: "string", "x-ui-widget": "color-wheel", readOnly: true }), "readonly");
});

test("数组 items.enum 没写 x-ui-widget 也解析为多选；对象数组默认 json-array", () => {
  assert.equal(resolveConfigWidget("days", { type: "array", items: { type: "string", enum: ["a"] } }), "multi-select");
  assert.equal(resolveConfigWidget("rows", { type: "array", items: { type: "object" } }), "json-array");
  assert.equal(resolveConfigWidget("api_key", { type: ["string", "null"] }), "secret");
});

test("卡片布局：Web 实现了契约首批五种，示例数据都能整理成模型", () => {
  assert.deepEqual([...CARD_LAYOUT_TYPES].sort(), [...CARD_LAYOUTS_IN_CONTRACT].sort());
  for (const layout of PLUGIN_UI_CONTRACT.cards.layouts) {
    const model = buildCardBlock(layout.example as Record<string, unknown>, layout.example_data, 1_767_225_660_000);
    assert.equal(model.type, layout.type, layout.type);
  }
  const stats = buildCardBlock({ type: "stats", items: [{ label: "调用", path: "calls" }, { label: "失败", path: "failed", tone: "danger" }] }, { calls: 12, failed: 1 });
  assert.deepEqual(stats.type === "stats" ? stats.items.map((item) => [item.text, item.tone]) : [], [["12", "neutral"], ["1", "danger"]]);
  const table = buildCardBlock({ type: "table", rows_path: "rows", row_key: "id", columns: [{ key: "id", label: "ID" }, { key: "rate", label: "失败率", format: "percent" }] }, { rows: [{ id: "a", rate: 0.25 }] });
  assert.equal(table.type === "table" ? table.rows[0].cells[1].text : "", "25%");
  assert.equal(table.type === "table" ? table.rows[0].key : "", "a");
  const list = buildCardBlock({ type: "list", items_path: "items", hide_when_empty: true }, { items: [] });
  assert.equal(list.type === "list" && list.hidden, true);
  assert.equal(buildCardBlock({ type: "chart" }, {}).type, "unsupported");
});

test("卡片取值格式", () => {
  assert.equal(formatCardValue(1234, "number"), "1,234");
  assert.equal(formatCardValue(0.0167, "percent"), "1.7%");
  assert.equal(formatCardValue(1800, "duration_ms"), "1.8 秒");
  assert.equal(formatCardValue(1_000, "relative_time", 2_000_000), "16 分钟前");
  assert.equal(formatCardValue(null), "-");
});
