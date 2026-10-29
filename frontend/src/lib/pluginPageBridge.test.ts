import assert from "node:assert/strict";
import test from "node:test";

import { bridgeError, bridgeOk, localBridgeResult, PAGE_SANDBOX, parseBridgeRequest, themeEvent } from "./pluginPageBridge.ts";

test("只接受协议 v1 的 Bridge 请求", () => {
  assert.deepEqual(parseBridgeRequest({ tp: "telepilot", v: 1, id: "r1", method: "config.get" }), { id: "r1", method: "config.get", params: {} });
  assert.deepEqual(parseBridgeRequest({ tp: "telepilot", v: 1, id: "r2", method: "action.invoke", params: { id: "x" } })?.params, { id: "x" });
  assert.equal(parseBridgeRequest({ tp: "telepilot", v: 2, id: "r1", method: "ready" }), null);
  assert.equal(parseBridgeRequest({ tp: "other", v: 1, id: "r1", method: "ready" }), null);
  assert.equal(parseBridgeRequest({ tp: "telepilot", v: 1, id: "", method: "ready" }), null);
  assert.equal(parseBridgeRequest({ tp: "telepilot", v: 1, id: "r1", method: "Bad Method" }), null);
  assert.equal(parseBridgeRequest({ tp: "telepilot", v: 1, id: "r1", method: "ready", params: [1] }), null);
  assert.equal(parseBridgeRequest({ tp: "telepilot", v: 1, id: "r1", method: "ready", params: { big: "x".repeat(40_000) } }), null);
  assert.equal(parseBridgeRequest("hello"), null);
});

test("回复与事件格式", () => {
  assert.deepEqual(bridgeOk("r1", { a: 1 }), { tp: "telepilot", v: 1, id: "r1", ok: true, result: { a: 1 } });
  assert.deepEqual(bridgeError("r1", "X", "失败"), { tp: "telepilot", v: 1, id: "r1", ok: false, error: { code: "X", message: "失败" } });
  assert.deepEqual(themeEvent("dark"), { tp: "telepilot", v: 1, event: "theme", data: { scheme: "dark" } });
  assert.deepEqual(localBridgeResult("theme.get", "light"), { handled: true, result: { scheme: "light" } });
  assert.equal(localBridgeResult("config.get", "light").handled, false);
});

test("iframe 沙箱不给同源", () => {
  assert.equal(PAGE_SANDBOX.includes("allow-same-origin"), false);
});
