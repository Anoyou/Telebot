import assert from "node:assert/strict";
import test from "node:test";

import {
  eventTypeLabel,
  formatDurationMs,
  formatRate,
  formatRelativeSeconds,
  hookHealth,
  hookLabel,
  summarizeHookStats,
} from "./pluginHookStats.ts";

test("钩子与事件类型显示", () => {
  assert.equal(hookLabel("on_event"), "事件处理");
  assert.equal(hookLabel("custom_hook"), "custom_hook");
  assert.equal(eventTypeLabel("-"), "");
  assert.equal(eventTypeLabel("message"), "message");
});

test("耗时、比例与相对时间格式", () => {
  assert.equal(formatDurationMs(null), "-");
  assert.equal(formatDurationMs(42.4), "42 毫秒");
  assert.equal(formatDurationMs(1500), "1.5 秒");
  assert.equal(formatDurationMs(30000), "30 秒");
  assert.equal(formatRate(0), "0%");
  assert.equal(formatRate(0.0333), "3.3%");
  assert.equal(formatRate(0.5), "50%");
  const now = 1_800_000_000_000;
  assert.equal(formatRelativeSeconds(null, now), "-");
  assert.equal(formatRelativeSeconds(now / 1000 - 10, now), "刚刚");
  assert.equal(formatRelativeSeconds(now / 1000 - 600, now), "10 分钟前");
  assert.equal(formatRelativeSeconds(now / 1000 - 7200, now), "2 小时前");
});

test("健康度按失败率与失败次数判断", () => {
  assert.equal(hookHealth({ calls: 0, failed: 0, timeout: 0, failure_rate: 0 }), "idle");
  assert.equal(hookHealth({ calls: 10, failed: 0, timeout: 0, failure_rate: 0 }), "ok");
  assert.equal(hookHealth({ calls: 100, failed: 1, timeout: 0, failure_rate: 0.01 }), "warn");
  assert.equal(hookHealth({ calls: 10, failed: 1, timeout: 2, failure_rate: 0.3 }), "danger");
});

test("汇总调用数、平均耗时与弃用调用", () => {
  const summary = summarizeHookStats({
    plugin_key: "demo",
    available: true,
    retention_days: 7,
    hooks: [
      { account_id: 1, hook: "on_event", event_type: "message", calls: 4, ok: 3, failed: 1, timeout: 0, circuit_open: 0, skipped: 0, sum_ms: 400, max_ms: 200, failure_rate: 0.25 },
      { account_id: 1, hook: "on_startup", event_type: "-", calls: 2, ok: 1, failed: 0, timeout: 0, circuit_open: 1, skipped: 0, sum_ms: 100, max_ms: 100, failure_rate: 0 },
    ],
    totals: { calls: 6, ok: 4, fail: 1, timeout: 0, circuit_open: 1, skipped: 0, sum_ms: 500 },
    deprecations: [{ account_id: 1, id: "ai.tag_alias", count: 3 }],
  });
  assert.deepEqual(summary, { calls: 6, failed: 1, timeout: 0, avgMs: 100, hooks: 2, deprecatedCalls: 3 });
  assert.equal(summarizeHookStats(undefined).avgMs, null);
});
