import assert from "node:assert/strict";
import test from "node:test";

import { flattenSettings, formatSettingValue, originalName, readSettingValue } from "./serverParams.ts";

test("按点号路径读取嵌套参数", () => {
  const settings = { command_prefix: ",", log_retention: { trace_enabled: false, runtime_log_retention_days: 0 } };
  assert.equal(readSettingValue(settings, "command_prefix"), ",");
  assert.equal(readSettingValue(settings, "log_retention.trace_enabled"), false);
  assert.equal(readSettingValue(settings, "log_retention.missing"), undefined);
  assert.equal(readSettingValue(settings, "ui_preferences.primary_nav"), undefined);
});

test("显示值：开关、单位、0 的含义、选项中文、列表", () => {
  assert.equal(formatSettingValue(true), "开启");
  assert.equal(formatSettingValue(30, { unit: "天", zero_means: "不自动删除", options: [] }), "30 天");
  assert.equal(formatSettingValue(0, { unit: "天", zero_means: "不自动删除", options: [] }), "0（不自动删除）");
  assert.equal(formatSettingValue("warn", { unit: null, zero_means: null, options: [{ value: "warn", label: "警告" }] }), "警告");
  assert.equal(formatSettingValue([]), "（空 = 默认）");
  assert.equal(formatSettingValue(["/ai", "/plugins"]), "/ai、/plugins");
  assert.equal(formatSettingValue(undefined), "未设置");
});

test("旧服务器按原键名拍平，英文原名取最后一段", () => {
  const rows = flattenSettings({ timezone: "Asia/Shanghai", llm_limits: { per_minute: 3 }, ui_preferences: {} });
  assert.deepEqual(rows.map((row) => row.key), ["llm_limits.per_minute", "timezone", "ui_preferences"]);
  assert.equal(originalName("log_retention.trace_enabled"), "trace_enabled");
  assert.equal(originalName("timezone"), "timezone");
});
