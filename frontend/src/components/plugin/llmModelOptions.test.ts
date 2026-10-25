import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTO_ROUTE_KEY,
  buildUnifiedModelSelection,
  groupUnifiedOptions,
  resolveUnifiedModelKey,
  unifiedModelKey,
} from "./llmModelOptions.ts";

const providers = [
  {
    id: 7,
    name: "猫羽",
    provider: "openai",
    models: [
      { id: "gpt-4o", label: "GPT-4o", enabled: true },
      { id: "gpt-image-1", enabled: true },
      { id: "o3", enabled: false },
    ],
  },
  {
    id: 3,
    name: "DeepSeek",
    provider: "deepseek",
    models: [{ id: "deepseek-chat", enabled: true }, { id: "deepseek-chat", enabled: true }, "deepseek-reasoner"],
  },
];

test("统一列表按 Provider 分组、只含已启用文本模型，标签为“Provider 名 · 模型”", () => {
  const selection = buildUnifiedModelSelection(providers, "", "", "text");
  assert.deepEqual(
    selection.options.map((option) => [option.group, option.label]),
    [
      ["DeepSeek", "DeepSeek · deepseek-chat"],
      ["DeepSeek", "DeepSeek · deepseek-reasoner"],
      ["猫羽", "猫羽 · GPT-4o（gpt-4o）"],
    ],
  );
  assert.equal(selection.selectedKey, AUTO_ROUTE_KEY);
  assert.equal(selection.legacy, null);
  assert.deepEqual(
    groupUnifiedOptions(selection.options).map((group) => [group.group, group.options.length]),
    [
      ["DeepSeek", 2],
      ["猫羽", 1],
    ],
  );
});

test("非 text 模态保留图片模型", () => {
  const selection = buildUnifiedModelSelection(providers, "", "", undefined);
  assert.ok(selection.options.some((option) => option.modelValue === "gpt-image-1"));
});

test("选中模型时同时写回 Provider；选自动路由时两者清空", () => {
  const selection = buildUnifiedModelSelection(providers, "", "", "text");
  assert.deepEqual(resolveUnifiedModelKey(selection, unifiedModelKey("7", "gpt-4o")), { provider: "7", model: "gpt-4o" });
  assert.deepEqual(resolveUnifiedModelKey(selection, AUTO_ROUTE_KEY), { provider: "", model: "" });
  assert.equal(resolveUnifiedModelKey(selection, "unknown"), null);
});

test("Provider 可按 id、名称或类型匹配当前值", () => {
  for (const selector of ["7", "猫羽", "openai"]) {
    const selection = buildUnifiedModelSelection(providers, selector, "gpt-4o", "text");
    assert.equal(selection.selectedKey, unifiedModelKey("7", "gpt-4o"), selector);
    assert.equal(selection.legacy, null);
  }
});

test("不在列表里的旧值显示为“当前值：…”且选中它时保持原值", () => {
  const disabled = buildUnifiedModelSelection(providers, "7", "o3", "text");
  assert.equal(disabled.legacy?.label, "当前值：猫羽 · o3");
  assert.equal(disabled.selectedKey, disabled.legacy?.key);
  assert.deepEqual(resolveUnifiedModelKey(disabled, disabled.selectedKey), { provider: "7", model: "o3" });

  assert.equal(buildUnifiedModelSelection(providers, "9", "x", "text").legacy?.label, "当前值：9 · x");
  assert.equal(buildUnifiedModelSelection(providers, "3", "", "text").legacy?.label, "当前值：DeepSeek · 默认模型");
  assert.equal(buildUnifiedModelSelection(providers, "", "orphan", "text").legacy?.label, "当前值：orphan（未固定 Provider）");
});

test("Provider 列表为空时只有自动路由", () => {
  const selection = buildUnifiedModelSelection([], "", "", "text");
  assert.deepEqual(selection.options, []);
  assert.equal(selection.selectedKey, AUTO_ROUTE_KEY);
});
