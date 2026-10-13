import assert from "node:assert/strict";
import test from "node:test";

import { formatStageTimings, resolveModelRunSummary, resolveToolCallCount } from "./modelRunMeta.ts";

test("正常轮：摘要展示 provider · model", () => {
  const run = resolveModelRunSummary({
    schema_version: 2,
    provider_name: "openai",
    provider_id: 1,
    model: "gpt-5.3",
    requested_provider_name: "openai",
    requested_model: "gpt-5.3",
    used_fallback: false,
  });
  assert.equal(run?.summary, "openai · gpt-5.3");
  assert.equal(run?.usedFallback, false);
});

test("Magpie / 网关场景：展示上游模型而不是用户选择的模型", () => {
  const run = resolveModelRunSummary({
    provider_name: "Magpie 网关",
    model: "workbuddy/glm-5.3-flash",
    requested_provider_name: "Magpie 网关",
    requested_model: "glm-5.3",
    used_fallback: false,
  });
  // 没有 fallback 时仍优先用 requested_model 作为摘要模型；此时应与上游一致或显示上游
  assert.ok(run?.summary.includes("Magpie 网关"));
  assert.equal(run?.reported, "Magpie 网关 · workbuddy/glm-5.3-flash");
});

test("fallback 轮：摘要换成实际模型，并保留原选择供单独展示", () => {
  const run = resolveModelRunSummary({
    provider_name: "backup",
    model: "model-b",
    requested_provider_name: "primary",
    requested_model: "model-a",
    used_fallback: true,
  });
  assert.equal(run?.summary, "backup · model-b");
  assert.equal(run?.summaryModel, "model-b");
  assert.equal(run?.requested, "primary · model-a");
  assert.equal(run?.reported, "backup · model-b");
  assert.equal(run?.usedFallback, true);
  assert.notEqual(run?.requested, run?.reported);
});

test("provider_name 缺失时回落到 requested_provider_name", () => {
  const run = resolveModelRunSummary({
    model: "gpt-5.3",
    requested_provider_name: "openai",
    requested_model: "gpt-5.3",
  });
  assert.equal(run?.summary, "openai · gpt-5.3");
});

test("usage 为空或字段全空时不出摘要行", () => {
  assert.equal(resolveModelRunSummary(null), null);
  assert.equal(resolveModelRunSummary(undefined), null);
  assert.equal(resolveModelRunSummary({}), null);
  assert.equal(resolveModelRunSummary({ model: "   ", provider_name: "  " }), null);
});

test("工具调用数优先实际调用，向后兼容旧 tool_count", () => {
  assert.equal(resolveToolCallCount({ schema_version: 2, tool_calls: 3, tool_count: 8 }), 3);
  assert.equal(resolveToolCallCount({ schema_version: 2, tool_count: 8 }), null);
  assert.equal(resolveToolCallCount({ tool_count: 8 }), 8);
});

test("阶段耗时标签", () => {
  assert.deepEqual(
    formatStageTimings({ stage_timings: { verify_ms: 900, route_ms: 1500, first_token_ms: 400 } }),
    ["探测 900ms", "路由 1.5s", "首 token 400ms"],
  );
  assert.deepEqual(formatStageTimings({}), []);
});
