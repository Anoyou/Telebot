import assert from "node:assert/strict";
import test from "node:test";

import type { PlatformCapabilities } from "../api/types.ts";
import {
  capabilityEnabledMap,
  filterNavByCapabilities,
  isRouteEnabledByCapabilities,
  pluginRuntimeAvailabilityLabel,
  resolveCapabilityGatePhase,
} from "./navigation.ts";

const sampleCaps = (enabled: Partial<Record<string, boolean>>): PlatformCapabilities => ({
  modules: [
    {
      key: "ai",
      label: "AI",
      desired_enabled: enabled.ai ?? true,
      generation: 0,
      runtime_state: "ready",
    },
    {
      key: "interaction_bot",
      label: "Interaction",
      desired_enabled: enabled.interaction_bot ?? true,
      generation: 0,
      runtime_state: "ready",
    },
    {
      key: "webhooks",
      label: "Webhooks",
      desired_enabled: enabled.webhooks ?? true,
      generation: 0,
      runtime_state: "ready",
    },
    {
      key: "ledger",
      label: "Ledger",
      desired_enabled: enabled.ledger ?? true,
      generation: 0,
      runtime_state: "ready",
    },
    {
      key: "dispatch_debug",
      label: "Debug",
      desired_enabled: enabled.dispatch_debug ?? true,
      generation: 0,
      runtime_state: "ready",
    },
  ],
  channels: [],
  worker_convergence: {
    total_accounts: 0,
    notified: 0,
    acked: 0,
    pending: 0,
    offline_or_timeout: 0,
  },
  cache_ready: true,
});

test("capability navigation hides disabled module routes", () => {
  const map = capabilityEnabledMap(sampleCaps({ ai: false, ledger: false }));
  const nav = filterNavByCapabilities(
    [
      { to: "/overview" },
      { to: "/ai" },
      { to: "/ai/liveness" },
      { to: "/ledger" },
      { to: "/plugins" },
      { to: "/settings" },
    ],
    map,
  );
  assert.deepEqual(
    nav.map((n) => n.to),
    ["/overview", "/plugins", "/settings"],
  );
});

test("capability map falls back to settings ai_enabled when caps missing", () => {
  const map = capabilityEnabledMap(null, false);
  assert.equal(map.ai, false);
  assert.equal(isRouteEnabledByCapabilities("/ai", map), false);
  assert.equal(isRouteEnabledByCapabilities("/plugins", map), true);
});

test("plugin runtime availability labels", () => {
  assert.equal(pluginRuntimeAvailabilityLabel("partial"), "部分可用");
  assert.equal(pluginRuntimeAvailabilityLabel("paused"), "已暂停");
  assert.equal(pluginRuntimeAvailabilityLabel("transitioning"), "等待热加载");
  assert.equal(pluginRuntimeAvailabilityLabel("ready"), "正常");
});

// ─────────── WP-B10：CapabilityGate 竞态收敛 ───────────

test("capability gate shows skeleton only while capabilities is loading", () => {
  assert.equal(
    resolveCapabilityGatePhase({
      capsLoading: true,
      caps: undefined,
      moduleKey: "ai",
      fallbackAiEnabled: true,
    }),
    "pending",
  );
});

test("capability gate does not wait for settings query", () => {
  // settings 已返回而 capabilities 仍在飞：旧写法按 && 判断会跳出骨架，
  // 让已暂停模块的内容先渲染一帧。必须继续等 capabilities。
  assert.equal(
    resolveCapabilityGatePhase({
      capsLoading: true,
      caps: undefined,
      moduleKey: "ai",
      fallbackAiEnabled: true,
    }),
    "pending",
  );
});

test("capability gate pauses disabled module after capabilities arrives", () => {
  const phase = resolveCapabilityGatePhase({
    capsLoading: false,
    caps: sampleCaps({ ai: false }),
    moduleKey: "ai",
    fallbackAiEnabled: true,
  });
  assert.equal(phase, "paused");
});

test("capability gate releases enabled module once capabilities arrives", () => {
  const phase = resolveCapabilityGatePhase({
    capsLoading: false,
    caps: sampleCaps({ ai: true }),
    moduleKey: "ai",
    fallbackAiEnabled: false, // settings 说关闭，但 capabilities 已就绪并以它为准
  });
  assert.equal(phase, "ready");
});

test("capability gate keeps default-true when capabilities is empty", () => {
  assert.equal(
    resolveCapabilityGatePhase({
      capsLoading: false,
      caps: { modules: [] } as PlatformCapabilities,
      moduleKey: "ai",
      fallbackAiEnabled: true,
    }),
    "ready",
  );
  assert.equal(
    resolveCapabilityGatePhase({
      capsLoading: false,
      caps: { modules: [] } as PlatformCapabilities,
      moduleKey: "ai",
      fallbackAiEnabled: false,
    }),
    "paused",
  );
});

test("capability gate ignores settings for non-ai modules", () => {
  assert.equal(
    resolveCapabilityGatePhase({
      capsLoading: false,
      caps: sampleCaps({ ledger: false }),
      moduleKey: "ledger",
      fallbackAiEnabled: false,
    }),
    "paused",
  );
  assert.equal(
    resolveCapabilityGatePhase({
      capsLoading: false,
      caps: sampleCaps({ ledger: true }),
      moduleKey: "ledger",
      fallbackAiEnabled: false,
    }),
    "ready",
  );
});
