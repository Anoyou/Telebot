import assert from "node:assert/strict";
import test from "node:test";

import {
  fieldAllowsNull,
  orderedPropertyEntries,
  sortedConfigEntries,
  withoutClearedValues,
} from "./configFieldOrder.ts";

// JSONB 会把键按长度/字节重排；x-ui-property-order 记录插件声明顺序。
const properties = {
  model: { type: "string", default: "" },
  bot_username: { type: "string", default: "" },
  enable_guest: { type: "boolean", default: true },
  reset_template: { type: "string", default: "", "x-ui-order": 74 },
  help_template: { type: "string", default: "", "x-ui-order": 73 },
};
const order = ["enable_guest", "bot_username", "model", "help_template", "reset_template"];

test("按 x-ui-property-order 还原声明顺序，未列出的字段排在后面", () => {
  assert.deepEqual(
    orderedPropertyEntries(properties, order).map(([key]) => key),
    ["enable_guest", "bot_username", "model", "help_template", "reset_template"],
  );
  assert.deepEqual(
    orderedPropertyEntries(properties, ["model"]).map(([key]) => key),
    ["model", "bot_username", "enable_guest", "reset_template", "help_template"],
  );
  // 没有顺序声明时保持服务器返回的顺序，不按字母排序。
  assert.deepEqual(orderedPropertyEntries(properties).map(([key]) => key), Object.keys(properties));
});

test("x-ui-order 稳定排序：未声明的视为 0，相同时保持声明顺序", () => {
  assert.deepEqual(
    sortedConfigEntries(properties, order).map(([key]) => key),
    ["enable_guest", "bot_username", "model", "help_template", "reset_template"],
  );
});

test("清空不接受 null 的可选字段时不提交，允许 null 的保留", () => {
  assert.equal(fieldAllowsNull({ type: "integer" }), false);
  assert.equal(fieldAllowsNull({ type: ["integer", "null"] }), true);
  assert.equal(fieldAllowsNull({ anyOf: [{ type: "string" }, { type: "null" }] }), true);
  assert.equal(fieldAllowsNull({}), true);
  assert.deepEqual(
    withoutClearedValues(
      { limit: null, nullable_limit: null, name: "", unknown: null },
      { limit: { type: "integer", default: 20 }, nullable_limit: { type: ["integer", "null"] }, name: { type: "string" } },
    ),
    { nullable_limit: null, name: "", unknown: null },
  );
});
