import assert from "node:assert/strict";
import test from "node:test";

import contract from "./pluginUiWidgets.json" with { type: "json" };
import {
  dryRunSpec,
  formatRuleValue,
  isFieldVisible,
  missingRequiredFields,
  parseChatId,
  pluginRulesLayout,
  ruleDefaults,
  ruleFormValues,
  ruleSummaryKeys,
} from "./pluginRules.ts";

const autoReplyLike = {
  type: "object",
  "x-ui-mode": "rules",
  properties: {},
  "x-rule-summary": ["match", "scope"],
  "x-dry-run": { sample_fields: ["message", "chat_type", "chat_id"], title: "试一条" },
  "x-rule-schema": {
    type: "object",
    required: ["patterns", "reply"],
    properties: {
      match: { type: "string", enum: ["keyword", "regex"], enumNames: ["关键词", "正则"], default: "keyword" },
      scope: { type: "string", enum: ["private", "group_specific"], default: "private" },
      group_ids: { type: "array", title: "指定群", "x-ui-visible-if": { field: "scope", equals: "group_specific" } },
      patterns: { type: "array", title: "模式", default: [] },
      reply: { type: "string", title: "回复内容" },
    },
  },
};

test("契约 2 声明了通用规则页的键", () => {
  const keys = new Set(contract.keys.map((item: { key: string }) => item.key));
  for (const key of ["x-rule-schema", "x-rule-summary", "x-dry-run", "x-ui-visible-if"]) assert.ok(keys.has(key), key);
  assert.ok(contract.contract_version >= 2);
});

test("x-rule-schema 优先；省略时整个 config_schema 是规则字段", () => {
  const layout = pluginRulesLayout(autoReplyLike);
  assert.equal(layout?.explicit, true);
  assert.deepEqual(Object.keys(layout?.ruleSchema.properties ?? {}), ["match", "scope", "group_ids", "patterns", "reply"]);
  const legacy = { "x-ui-mode": "rules", properties: { target_chat_id: { type: "integer" } } };
  assert.equal(pluginRulesLayout(legacy)?.explicit, false);
  assert.equal(pluginRulesLayout(legacy)?.ruleSchema, legacy);
  assert.equal(pluginRulesLayout({ "x-ui-mode": "single", properties: {} }), null);
});

test("x-ui-visible-if equals / in / not_in，不合法声明一律显示", () => {
  const field = { "x-ui-visible-if": { field: "scope", equals: "group_specific" } };
  assert.equal(isFieldVisible(field, { scope: "private" }), false);
  assert.equal(isFieldVisible(field, { scope: "group_specific" }), true);
  assert.equal(isFieldVisible({ "x-ui-visible-if": { field: "n", in: [1, 2] } }, { n: "2" }), true);
  assert.equal(isFieldVisible({ "x-ui-visible-if": { field: "n", not_in: ["a"] } }, { n: "a" }), false);
  assert.equal(isFieldVisible({ "x-ui-visible-if": "bad" }, {}), true);
  assert.equal(isFieldVisible(undefined, {}), true);
});

test("默认值、编辑回填与必填校验（隐藏字段不算必填）", () => {
  const rule = pluginRulesLayout(autoReplyLike)!.ruleSchema;
  const defaults = ruleDefaults(rule);
  assert.deepEqual(defaults, { match: "keyword", scope: "private", patterns: [] });
  (defaults.patterns as string[]).push("x");
  assert.deepEqual(ruleDefaults(rule).patterns, [], "默认值必须深拷贝");
  assert.deepEqual(ruleFormValues(rule, { scope: "group_specific", legacy: 1 }), {
    match: "keyword", scope: "group_specific", patterns: [], legacy: 1,
  });
  assert.deepEqual(missingRequiredFields(rule, { patterns: [], reply: " " }), ["模式", "回复内容"]);
  const withHiddenRequired = { ...rule, required: ["group_ids"] };
  assert.deepEqual(missingRequiredFields(withHiddenRequired, { scope: "private" }), []);
  assert.deepEqual(missingRequiredFields(withHiddenRequired, { scope: "group_specific" }), ["指定群"]);
});

test("摘要列与值格式化", () => {
  const layout = pluginRulesLayout(autoReplyLike)!;
  assert.deepEqual(ruleSummaryKeys(autoReplyLike, layout, ["match", "scope", "group_ids"]), ["match", "scope"]);
  const legacy = { "x-ui-mode": "rules", properties: { a: { type: "string" }, h: { type: "string", "x-ui-hidden": true }, b: { type: "string" }, c: {} } };
  assert.deepEqual(ruleSummaryKeys(legacy, pluginRulesLayout(legacy)!, ["a", "h", "b", "c"]), ["a", "b"]);
  const match = layout.ruleSchema.properties!.match;
  assert.equal(formatRuleValue(match, "regex"), "正则");
  assert.equal(formatRuleValue(undefined, ["你好", "hi"]), "你好、hi");
  assert.equal(formatRuleValue(undefined, true), "是");
  assert.equal(formatRuleValue(undefined, ""), "—");
});

test("试运行声明：默认开启、false 关闭、过滤未知样本字段", () => {
  assert.deepEqual(dryRunSpec(autoReplyLike).sampleFields, ["message", "chat_type", "chat_id"]);
  assert.equal(dryRunSpec(autoReplyLike).title, "试一条");
  assert.deepEqual(dryRunSpec({}).sampleFields, ["message", "chat_id"]);
  assert.equal(dryRunSpec({ "x-dry-run": false }).enabled, false);
  assert.deepEqual(dryRunSpec({ "x-dry-run": { sample_fields: ["sender"] } }).sampleFields, ["message", "chat_id"]);
  assert.equal(parseChatId(" -1001234 "), -1001234);
  assert.equal(parseChatId("abc"), null);
  assert.equal(parseChatId(""), null);
});
