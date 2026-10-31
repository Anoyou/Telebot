import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_SESSION_MODEL_SELECTION,
  fromApiModelSelection,
  loadSessionModelSelection,
  resolveAvailableSelection,
  saveSessionModelSelection,
} from "./sessionModelSelection.ts";

function installLocalStorage(): void {
  const values = new Map<string, string>();
  const localStorage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
    clear: () => { values.clear(); },
    key: (index: number) => [...values.keys()][index] ?? null,
    get length() { return values.size; },
  };
  (globalThis as unknown as { window: { localStorage: typeof localStorage } }).window = {
    localStorage,
  };
}

test("每个会话记住自己的模型和调用方式，切换会话互不影响", () => {
  installLocalStorage();
  saveSessionModelSelection("session-a", {
    mode: "pinned",
    providerId: 7,
    model: "grok-4.5",
    executionBackend: "direct",
    clientIdentityProfile: "codex_tui",
  });

  // 别的会话不受影响，仍是「自动」
  assert.deepEqual(loadSessionModelSelection("session-b"), DEFAULT_SESSION_MODEL_SELECTION);
  assert.deepEqual(loadSessionModelSelection("session-a"), {
    mode: "pinned",
    providerId: 7,
    model: "grok-4.5",
    executionBackend: "direct",
    clientIdentityProfile: "codex_tui",
  });

  saveSessionModelSelection("session-a", { mode: "auto", executionBackend: "provider" });
  assert.deepEqual(loadSessionModelSelection("session-a"), DEFAULT_SESSION_MODEL_SELECTION);
});

test("新会话草稿默认「自动」，不沿用上一个会话", () => {
  installLocalStorage();
  saveSessionModelSelection("session-a", {
    mode: "pinned",
    providerId: 7,
    model: "grok-4.5",
    executionBackend: "provider",
  });
  assert.deepEqual(loadSessionModelSelection(null), DEFAULT_SESSION_MODEL_SELECTION);
  saveSessionModelSelection(null, { mode: "auto", executionBackend: "codex_gateway" });
  assert.deepEqual(loadSessionModelSelection(null), DEFAULT_SESSION_MODEL_SELECTION);
});

test("服务器会话上的选择优先于本机记忆", () => {
  installLocalStorage();
  saveSessionModelSelection("session-a", {
    mode: "pinned",
    providerId: 7,
    model: "local-model",
    executionBackend: "provider",
  });
  assert.deepEqual(
    loadSessionModelSelection("session-a", {
      mode: "pinned",
      provider_id: 3,
      model: "server-model",
      execution_backend: "direct",
      client_identity_profile: "claude_code",
    }),
    {
      mode: "pinned",
      providerId: 3,
      model: "server-model",
      executionBackend: "direct",
      clientIdentityProfile: "claude_code",
    },
  );
  // 服务器没有值（null / 旧服务器缺字段）时退回本机
  assert.equal(
    (loadSessionModelSelection("session-a", null) as { model?: string }).model,
    "local-model",
  );
  assert.equal(
    (loadSessionModelSelection("session-a", undefined) as { model?: string }).model,
    "local-model",
  );
});

test("旧版全局「最近一次选择」不再影响任何会话", () => {
  installLocalStorage();
  window.localStorage.setItem(
    "telepilot.system-agent.last-model-selection.v1",
    JSON.stringify({ mode: "pinned", providerId: 9, model: "old", executionBackend: "provider" }),
  );
  assert.deepEqual(loadSessionModelSelection("session-x"), DEFAULT_SESSION_MODEL_SELECTION);
  saveSessionModelSelection("session-x", { mode: "auto", executionBackend: "provider" });
  assert.equal(window.localStorage.getItem("telepilot.system-agent.last-model-selection.v1"), null);
});

test("所选模型被删除或停用时回落「自动」", () => {
  const pinned = {
    mode: "pinned" as const,
    providerId: 3,
    model: "gone",
    executionBackend: "provider" as const,
  };
  assert.equal(resolveAvailableSelection(pinned, null), pinned);
  assert.equal(resolveAvailableSelection(pinned, [{ providerId: 3, model: "gone" }]), pinned);
  assert.deepEqual(
    resolveAvailableSelection(pinned, [{ providerId: 3, model: "other" }]),
    DEFAULT_SESSION_MODEL_SELECTION,
  );
  assert.deepEqual(resolveAvailableSelection(pinned, []), DEFAULT_SESSION_MODEL_SELECTION);
  const auto = { mode: "auto" as const, executionBackend: "direct" as const };
  assert.equal(resolveAvailableSelection(auto, []), auto);
});

test("服务器值格式不对时当作未选择", () => {
  assert.equal(fromApiModelSelection(null), null);
  assert.equal(
    fromApiModelSelection({ mode: "pinned", provider_id: 0, model: "" }),
    null,
  );
});
