import assert from "node:assert/strict";
import test from "node:test";

import {
  GENERIC_START_KEYWORDS_PLACEHOLDER,
  declaredStartKeywords,
  effectiveInteractionEntry,
  ruleEditorSpecFor,
  startKeywordsPlaceholder,
} from "./interactionRuleEditor.ts";

const entry = {
  key: "start_game",
  input_schema: {
    type: "object",
    properties: {
      prize: { type: "integer", title: "奖金" },
      rounds: { type: "integer", title: "轮数", default: 3 },
    },
  },
};

test("没有 ui.rule_editor 时入口原样返回（旧插件 / 旧服务器）", () => {
  assert.equal(effectiveInteractionEntry(entry, undefined), entry);
  assert.equal(ruleEditorSpecFor(null, "start_game"), undefined);
  assert.equal(ruleEditorSpecFor({ entries: {} }, "start_game"), undefined);
});

test("ui.rule_editor 的 schema 与 defaults 合并进 input_schema，不删宿主字段", () => {
  const spec = {
    schema: { type: "object", properties: { level: { type: "string", title: "难度" }, rounds: { title: "回合数" } } },
    defaults: { rounds: 5, level: "easy" },
  };
  const out = effectiveInteractionEntry(entry, spec);
  const props = (out.input_schema as { properties: Record<string, Record<string, unknown>> }).properties;
  assert.deepEqual(Object.keys(props).sort(), ["level", "prize", "rounds"]);
  assert.equal(props.rounds.title, "回合数");
  assert.equal(props.rounds.type, "integer");
  assert.equal(props.rounds.default, 5);
  assert.equal(props.level.default, "easy");
  assert.equal(props.prize.title, "奖金");
});

test("启动词占位：插件声明优先，否则通用提示，不再写死插件启动词", () => {
  assert.equal(startKeywordsPlaceholder(entry, undefined), GENERIC_START_KEYWORDS_PLACEHOLDER);
  const withPolicy = { ...entry, entry_policy: { default_start_keywords: ["开24点", " "] } };
  assert.deepEqual(declaredStartKeywords(withPolicy, undefined), ["开24点"]);
  assert.equal(startKeywordsPlaceholder(withPolicy, undefined), "开24点");
  assert.equal(startKeywordsPlaceholder(withPolicy, { default_start_keywords: ["开局"] }), "开局");
  assert.equal(startKeywordsPlaceholder(withPolicy, { start_keywords_placeholder: "自定义" }), "自定义");
});
