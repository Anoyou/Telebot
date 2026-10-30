import assert from "node:assert/strict";
import test from "node:test";

import {
  dispatchDebugHref,
  inferChatType,
  moduleLabel,
  parseDispatchDebugPrefill,
} from "./navigation.ts";

test("命中调试链接：无参数时是纯路径", () => {
  assert.equal(dispatchDebugHref(), "/dispatch-debug");
  assert.equal(dispatchDebugHref({}), "/dispatch-debug");
});

test("命中调试链接：日志消息带账号 / 会话 / 发送者 / 来源 / 正文，往返解析一致", () => {
  const href = dispatchDebugHref({
    accountId: 3,
    chatId: -1001234567890,
    senderId: 42,
    via: "userbot",
    text: "  ,ai 你好  ",
  });
  const url = new URL(href, "https://example.test");
  assert.equal(url.pathname, "/dispatch-debug");
  assert.deepEqual(parseDispatchDebugPrefill(url.searchParams), {
    accountId: 3,
    chatId: -1001234567890,
    chatType: "supergroup",
    senderId: 42,
    text: ",ai 你好",
    via: "userbot",
  });
});

test("命中调试链接：命中调试不支持的来源（管理 Bot、定时任务）不带 via", () => {
  const href = dispatchDebugHref({ accountId: 1, via: "account_bot" });
  assert.equal(href, "/dispatch-debug?account_id=1");
  const parsed = parseDispatchDebugPrefill(new URLSearchParams("via=scheduler&chat_type=bogus&account_id=x"));
  assert.equal(parsed.via, undefined);
  assert.equal(parsed.chatType, undefined);
  assert.equal(parsed.accountId, undefined);
});

test("会话类型按 chat_id 推断", () => {
  assert.equal(inferChatType(-1001234567890), "supergroup");
  assert.equal(inferChatType(-4567), "group");
  assert.equal(inferChatType(777), "private");
  assert.equal(inferChatType(null), undefined);
  assert.equal(inferChatType(0), undefined);
});

test("统一命名：模块名 Webhook 不再叫入站 Webhook", () => {
  assert.equal(moduleLabel("webhooks"), "Webhook");
  assert.equal(moduleLabel("dispatch_debug"), "命中调试");
  assert.equal(moduleLabel("interaction_bot"), "交互");
});
