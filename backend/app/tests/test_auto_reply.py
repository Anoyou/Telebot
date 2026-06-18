"""auto_reply 插件单元测试：mock Telethon event/client + fakeredis + 假 engine。

覆盖：
  - 关键词命中 → engine.acquire 被调用 → event.respond 被调用
  - 不在 scope（私聊规则收到群消息）→ 跳过，不 respond
  - 冷却中（redis 已有 cool_key）→ 跳过
  - 模板变量 {sender} / {chat} / {text} 正确渲染
  - dry-run 函数对关键词 / 正则 / scope 行为正确
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker.command import (
    CommandContext,
    register_plugin_command,
    set_command_context,
    unregister_plugin_command,
)
from app.worker.plugins.base import PluginContext
from app.worker.plugins.builtin.auto_reply import (
    AutoReplyPlugin,
    _dry_run_match,
    _match,
    _parse_duration_seconds,
    _render,
    _scope_ok,
)
from app.worker.ratelimit.engine import RateLimitDecision
from app.worker.ratelimit.humanize import HumanizeOpts


# ─────────────────────────────────────────────────────
# 公用：极简 fake redis（实现 auto_reply 用到的 get/set）
# ─────────────────────────────────────────────────────
class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key: str):
        return self.kv.get(key)

    async def set(self, key: str, val: str, ex: int = 0, nx: bool = False) -> bool:
        if nx and key in self.kv:
            return False
        self.kv[key] = val
        self.ttl[key] = ex
        return True

    async def incr(self, key: str) -> int:
        value = int(self.kv.get(key, "0")) + 1
        self.kv[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttl[key] = seconds
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.kv:
                deleted += 1
                self.kv.pop(key, None)
            self.ttl.pop(key, None)
        return deleted

    async def rpush(self, key: str, val: str) -> int:  # 给 ctx.log 兜底
        return 1


# ─────────────────────────────────────────────────────
# 假规则：模仿 ORM Rule 的最小字段集
# ─────────────────────────────────────────────────────
@dataclass
class _FakeRule:
    id: int
    config: dict
    name: str = "自动回复"
    priority: int = 100
    enabled: bool = True


class _FakeSentMessage:
    def __init__(self) -> None:
        self.edit = AsyncMock()


# ─────────────────────────────────────────────────────
# 构造工具
# ─────────────────────────────────────────────────────
def _make_engine(allowed: bool = True, wait: float = 0.0, outcome: str = "ok") -> Any:
    """假 engine：humanize 用默认值；acquire 返回固定决策。"""
    engine = MagicMock()
    engine.humanize = HumanizeOpts(
        typing_simulate=False, read_before_reply=False
    )  # 关闭真延迟，避免测试里 sleep
    engine.acquire = AsyncMock(
        return_value=RateLimitDecision(allowed=allowed, wait_seconds=wait, outcome=outcome)
    )
    engine.on_flood_wait = AsyncMock()
    engine.on_peer_flood = AsyncMock()
    engine.on_slow_mode = AsyncMock()
    engine.on_phone_flood = AsyncMock()
    return engine


def _make_event(text: str, *, is_private: bool = True, chat_id: int = 100, sender_id: int = 42):
    """构造一个假 Telethon NewMessage 事件。"""
    event = AsyncMock()
    event.raw_text = text
    event.chat_id = chat_id
    event.sender_id = sender_id
    event.is_private = is_private
    event.is_group = not is_private
    event.is_channel = False

    # sender / chat 是 awaitable，返回带名字的 dummy 对象
    sender = MagicMock()
    sender.first_name = "Alice"
    sender.username = None
    sender.id = sender_id
    sender.contact = False
    chat = MagicMock()
    chat.title = "PrivChat" if not is_private else None
    chat.first_name = "Alice"
    chat.id = chat_id
    event.get_sender = AsyncMock(return_value=sender)
    event.get_chat = AsyncMock(return_value=chat)
    event.respond = AsyncMock()
    event.reply = AsyncMock()
    return event


def _make_ctx(rules: list[_FakeRule], engine: Any, redis: Any) -> PluginContext:
    return PluginContext(
        account_id=1,
        feature_key="auto_reply",
        config={},
        rules=list(rules),
        client=MagicMock(),
        engine=engine,
        redis=redis,
        log=AsyncMock(),
    )


# ─────────────────────────────────────────────────────
# 用例：关键词命中
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_keyword_hit_calls_acquire_and_respond() -> None:
    """关键词命中时应调用 engine.acquire，并真正 event.respond 一次。"""
    rule = _FakeRule(
        id=1,
        config={
            "match_type": "keyword",
            "patterns": ["hello"],
            "scope": "all",
            "reply": "hi {sender}",
            "cooldown_seconds": 0,
            # 显式走 event.respond 路径（reply_to=False），便于断言；
            # 默认 reply_to=True 时插件会调用 event.reply（带引用）
            "reply_to": False,
        },
    )
    redis = _FakeRedis()
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    event = _make_event("Hello there", is_private=True)

    await AutoReplyPlugin().on_message(ctx, event)

    engine.acquire.assert_awaited_once()
    event.respond.assert_awaited_once()
    sent_text = event.respond.call_args[0][0]
    assert "Alice" in sent_text  # 模板变量 {sender} 被替换


# ─────────────────────────────────────────────────────
# 用例：scope=private 收到群消息 → 跳过
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scope_private_skips_group_message() -> None:
    """规则限定私聊；事件来自群 → 不应触发 acquire / respond。"""
    rule = _FakeRule(
        id=2,
        config={
            "match_type": "keyword",
            "patterns": ["hello"],
            "scope": "private",
            "reply": "hi",
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hello", is_private=False)

    await AutoReplyPlugin().on_message(ctx, event)

    engine.acquire.assert_not_called()
    event.respond.assert_not_called()


# ─────────────────────────────────────────────────────
# 用例：冷却中 → 跳过
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cooldown_sends_notice_instead_of_reply() -> None:
    """redis 已有冷却 key → 不发送业务回复，但会提示剩余冷却。"""
    rule = _FakeRule(
        id=3,
        config={
            "match_type": "keyword",
            "patterns": ["hello"],
            "scope": "all",
            "reply": "hi",
            "cooldown_seconds": 30,
        },
    )
    redis = _FakeRedis()
    redis.kv["ar:cool:1:3:chat:100"] = "1"  # 模拟"还在冷却"
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    event = _make_event("hello", is_private=True, chat_id=100)

    await AutoReplyPlugin().on_message(ctx, event)

    engine.acquire.assert_awaited_once()
    event.respond.assert_not_called()
    event.reply.assert_awaited_once()
    assert "冷却" in event.reply.await_args.args[0]


# ─────────────────────────────────────────────────────
# 用例：风控决定 drop → 不发送
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_engine_drop_blocks_send() -> None:
    """engine 返回 allowed=False / outcome=drop → respond 不应被调。"""
    rule = _FakeRule(
        id=4,
        config={"patterns": ["hi"], "scope": "all", "reply": "y"},
    )
    engine = _make_engine(allowed=False, outcome="drop")
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hi")

    await AutoReplyPlugin().on_message(ctx, event)

    engine.acquire.assert_awaited_once()
    event.respond.assert_not_called()


# ─────────────────────────────────────────────────────
# 用例：黑名单命中 → 跳过
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_blacklist_chat_skipped() -> None:
    rule = _FakeRule(
        id=5,
        config={
            "patterns": ["x"],
            "scope": "all",
            "reply": "z",
            "blacklist_chats": [100],
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("x", chat_id=100)

    await AutoReplyPlugin().on_message(ctx, event)

    engine.acquire.assert_not_called()
    event.respond.assert_not_called()


@pytest.mark.asyncio
async def test_auto_reply_command_text_dispatches_directly() -> None:
    """自动回复生成白名单命令时应直接派发，不依赖 outgoing update 回流。"""
    set_command_context(
        CommandContext(
            account_id=1,
            templates={
                "hello": {
                    "name": "hello",
                    "type": "reply_text",
                    "config": {"text": "命令已执行 {args}"},
                }
            },
            providers={},
            command_prefix=",",
            scheduler_command_whitelist=["hello"],
        )
    )
    rule = _FakeRule(
        id=6,
        config={
            "match_type": "keyword",
            "patterns": ["go"],
            "scope": "all",
            "reply": ",hello world",
            "cooldown_seconds": 0,
            "reply_to": False,
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("go")

    try:
        await AutoReplyPlugin().on_message(ctx, event)

        event.respond.assert_awaited_once_with("命令已执行 world")
        event.reply.assert_not_called()
    finally:
        set_command_context(
            CommandContext(account_id=1, templates={}, providers={}, command_prefix=",")
        )


@pytest.mark.asyncio
async def test_auto_reply_regex_capture_dispatches_whitelisted_command() -> None:
    """正则捕获组应能渲染到自动命令里，例如：置顶 12345 -> 。pt 12345。"""
    set_command_context(
        CommandContext(
            account_id=1,
            templates={
                "pt": {
                    "name": "pt",
                    "type": "reply_text",
                    "config": {"text": "置顶执行 {args}"},
                }
            },
            providers={},
            command_prefix="。",
            scheduler_command_whitelist=["pt"],
        )
    )
    rule = _FakeRule(
        id=7,
        config={
            "match_type": "regex",
            "patterns": [r"^置顶\s+(\d+)$"],
            "scope": "all",
            "reply": "{prefix}pt {1}",
            "cooldown_seconds": 0,
            "reply_to": False,
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("置顶 12345")

    try:
        await AutoReplyPlugin().on_message(ctx, event)

        event.respond.assert_awaited_once_with("置顶执行 12345")
        event.reply.assert_not_called()
    finally:
        set_command_context(
            CommandContext(account_id=1, templates={}, providers={}, command_prefix=",")
        )


@pytest.mark.asyncio
async def test_auto_reply_regex_capture_default_dispatches_whitelisted_command() -> None:
    """可选捕获为空时，{1|默认值} 应回退到默认参数。"""
    set_command_context(
        CommandContext(
            account_id=1,
            templates={
                "ct": {
                    "name": "ct",
                    "type": "reply_text",
                    "config": {"text": "猜骰执行 {args}"},
                }
            },
            providers={},
            command_prefix="。",
            scheduler_command_whitelist=["ct"],
        )
    )
    rule = _FakeRule(
        id=8,
        config={
            "match_type": "regex",
            "patterns": [r"^我要猜骰\s*(\d+)?$"],
            "scope": "all",
            "reply": "{prefix}ct {1|1000}",
            "cooldown_seconds": 0,
            "reply_to": False,
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("我要猜骰")

    try:
        await AutoReplyPlugin().on_message(ctx, event)

        event.respond.assert_awaited_once_with("猜骰执行 1000")
        event.reply.assert_not_called()
    finally:
        set_command_context(
            CommandContext(account_id=1, templates={}, providers={}, command_prefix=",")
        )


@pytest.mark.asyncio
async def test_auto_reply_user_scoped_cooldown_allows_other_users() -> None:
    rule = _FakeRule(
        id=9,
        config={
            "match_type": "keyword",
            "patterns": ["go"],
            "scope": "all",
            "reply": "ok",
            "cooldown_seconds": "6h",
            "cooldown_scope": "user",
            "reply_to": False,
        },
    )
    redis = _FakeRedis()
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    first = _make_event("go", is_private=False, chat_id=-100123, sender_id=111)
    same_user = _make_event("go", is_private=False, chat_id=-100123, sender_id=111)
    other_user = _make_event("go", is_private=False, chat_id=-100123, sender_id=222)

    await AutoReplyPlugin().on_message(ctx, first)
    await AutoReplyPlugin().on_message(ctx, same_user)
    await AutoReplyPlugin().on_message(ctx, other_user)

    first.respond.assert_awaited_once_with("ok")
    assert "冷却" in same_user.respond.await_args.args[0]
    other_user.respond.assert_awaited_once_with("ok")
    assert redis.ttl["ar:cool:1:9:user:-100123:111"] == 21600


@pytest.mark.asyncio
async def test_auto_reply_daily_limit_per_user_blocks_third_hit() -> None:
    rule = _FakeRule(
        id=10,
        config={
            "match_type": "keyword",
            "patterns": ["go"],
            "scope": "all",
            "reply": "ok",
            "cooldown_seconds": 0,
            "daily_limit_per_user": 2,
            "reply_to": False,
        },
    )
    redis = _FakeRedis()
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    events = [_make_event("go", is_private=False, chat_id=-100123, sender_id=111) for _ in range(3)]

    for event in events:
        await AutoReplyPlugin().on_message(ctx, event)

    events[0].respond.assert_awaited_once_with("ok")
    assert events[1].respond.await_args_list[0].args[0] == "ok"
    assert "当日无法再次使用" in events[1].respond.await_args_list[1].args[0]
    assert "当日无法再次使用" in events[2].respond.await_args_list[0].args[0]
    quota_keys = [key for key in redis.kv if key.startswith("ar:quota:1:10:user_day:")]
    assert len(quota_keys) == 1
    assert redis.kv[quota_keys[0]] == "2"


@pytest.mark.asyncio
async def test_auto_reply_failed_command_does_not_mark_usage() -> None:
    """自动命令返回查询失败时，不应消耗用户冷却和每日次数。"""
    set_command_context(
        CommandContext(
            account_id=1,
            templates={
                "pt": {
                    "name": "pt",
                    "type": "reply_text",
                    "config": {"text": "❌ 查询失败：HTTP 500"},
                }
            },
            providers={},
            command_prefix="。",
            scheduler_command_whitelist=["pt"],
        )
    )
    rule = _FakeRule(
        id=11,
        name="置顶",
        config={
            "match_type": "template",
            "patterns": ["置顶 id=数字"],
            "scope": "all",
            "reply": "{prefix}pt {id}",
            "cooldown_seconds": "6h",
            "cooldown_scope": "user",
            "daily_limit_per_user": 2,
            "usage_label": "置顶促销",
            "reply_to": False,
        },
    )
    redis = _FakeRedis()
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    event = _make_event("置顶 id=12345", is_private=False, chat_id=-100123, sender_id=111)

    try:
        await AutoReplyPlugin().on_message(ctx, event)

        event.respond.assert_awaited_once_with("❌ 查询失败：HTTP 500")
        assert not any(key.startswith("ar:cool:1:11:") for key in redis.kv)
        assert not any(key.startswith("ar:quota:1:11:") for key in redis.kv)
    finally:
        set_command_context(
            CommandContext(account_id=1, templates={}, providers={}, command_prefix=",")
        )


@pytest.mark.asyncio
async def test_auto_reply_missing_seed_id_usage_does_not_mark_usage() -> None:
    """自动查询命令缺少种子 ID 时，不应消耗次数。"""
    set_command_context(
        CommandContext(
            account_id=1,
            templates={
                "ptinfo": {
                    "name": "ptinfo",
                    "type": "reply_text",
                    "config": {"text": "没有种子 ID，请输入后再试"},
                }
            },
            providers={},
            command_prefix="。",
            scheduler_command_whitelist=["ptinfo"],
        )
    )
    rule = _FakeRule(
        id=12,
        name="置顶查询",
        config={
            "match_type": "keyword",
            "patterns": ["查询置顶"],
            "scope": "all",
            "reply": "{prefix}ptinfo",
            "cooldown_seconds": "6h",
            "cooldown_scope": "user",
            "daily_limit_per_user": 2,
            "usage_label": "置顶促销",
            "reply_to": False,
        },
    )
    redis = _FakeRedis()
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    event = _make_event("查询置顶", is_private=False, chat_id=-100123, sender_id=111)

    try:
        await AutoReplyPlugin().on_message(ctx, event)

        event.respond.assert_awaited_once_with("没有种子 ID，请输入后再试")
        assert not any(key.startswith("ar:cool:1:12:") for key in redis.kv)
        assert not any(key.startswith("ar:quota:1:12:") for key in redis.kv)
    finally:
        set_command_context(
            CommandContext(account_id=1, templates={}, providers={}, command_prefix=",")
        )


@pytest.mark.asyncio
async def test_auto_reply_successful_command_marks_usage() -> None:
    """自动命令成功后，才写入用户冷却和今日次数。"""
    set_command_context(
        CommandContext(
            account_id=1,
            templates={
                "pt": {
                    "name": "pt",
                    "type": "reply_text",
                    "config": {"text": "✅ 置顶促销成功！\n种子：{args}"},
                }
            },
            providers={},
            command_prefix="。",
            scheduler_command_whitelist=["pt"],
        )
    )
    rule = _FakeRule(
        id=13,
        name="置顶",
        config={
            "match_type": "template",
            "patterns": ["置顶 id=数字"],
            "scope": "all",
            "reply": "{prefix}pt {id}",
            "cooldown_seconds": "6h",
            "cooldown_scope": "user",
            "daily_limit_per_user": 2,
            "usage_label": "置顶促销",
            "reply_to": False,
        },
    )
    redis = _FakeRedis()
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    event = _make_event("置顶 id=12345", is_private=False, chat_id=-100123, sender_id=111)
    sent = _FakeSentMessage()
    event.respond.return_value = sent

    try:
        await AutoReplyPlugin().on_message(ctx, event)

        event.respond.assert_awaited_once_with("✅ 置顶促销成功！\n种子：12345")
        sent.edit.assert_awaited_once()
        success_notice = sent.edit.await_args.args[0]
        assert success_notice.startswith("✅ 置顶促销成功！\n种子：12345")
        assert "今日已成功置顶促销 1/2 次" in success_notice
        assert "距离下次可用 CD 还剩 6小时" in success_notice
        assert "本次是第" not in success_notice
        assert redis.kv["ar:cool:1:13:user:-100123:111"] == "1"
        quota_keys = [key for key in redis.kv if key.startswith("ar:quota:1:13:")]
        assert len(quota_keys) == 1
        assert redis.kv[quota_keys[0]] == "1"
    finally:
        set_command_context(
            CommandContext(account_id=1, templates={}, providers={}, command_prefix=",")
        )


@pytest.mark.asyncio
async def test_auto_reply_pending_usage_blocks_parallel_command() -> None:
    """慢命令执行中应先占位，避免同一用户并发穿透每日上限。"""
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _slow_command(client: Any, event: Any, args: list[str], account_id: int) -> None:
        del client, args, account_id
        await event.edit("✅ 慢命令开始")
        entered.set()
        await release.wait()

    register_plugin_command("slowpt", _slow_command, owner_plugin_key="test_auto_reply")
    set_command_context(
        CommandContext(
            account_id=1,
            templates={},
            providers={},
            command_prefix="。",
            scheduler_command_whitelist=["slowpt"],
        )
    )
    rule = _FakeRule(
        id=17,
        name="置顶",
        config={
            "match_type": "template",
            "patterns": ["置顶 id=数字"],
            "scope": "all",
            "reply": "{prefix}slowpt {id}",
            "cooldown_seconds": 0,
            "cooldown_scope": "user",
            "daily_limit_per_user": 2,
            "reply_to": False,
        },
    )
    redis = _FakeRedis()
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    plugin = AutoReplyPlugin()
    first = _make_event("置顶 id=31420", is_private=False, chat_id=-100123, sender_id=111)
    second = _make_event("置顶 id=31421", is_private=False, chat_id=-100123, sender_id=111)

    try:
        task = asyncio.create_task(plugin.on_message(ctx, first))
        await asyncio.wait_for(entered.wait(), timeout=1)

        await plugin.on_message(ctx, second)

        assert "冷却" in second.respond.await_args.args[0]
        assert redis.kv["ar:pending:1:17:user:-100123:111"] == "1"
        assert not any(key.startswith("ar:quota:1:17:") for key in redis.kv)

        release.set()
        await task

        quota_keys = [key for key in redis.kv if key.startswith("ar:quota:1:17:")]
        assert len(quota_keys) == 1
        assert redis.kv[quota_keys[0]] == "1"
        assert "ar:pending:1:17:user:-100123:111" not in redis.kv
    finally:
        release.set()
        unregister_plugin_command("slowpt", owner_plugin_key="test_auto_reply")
        set_command_context(
            CommandContext(account_id=1, templates={}, providers={}, command_prefix=",")
        )


@pytest.mark.asyncio
async def test_auto_reply_command_edits_merge_and_append_final_limit_notice() -> None:
    """自动命令多次 edit 应合并为同一条消息，最终次数提示拼到底部。"""

    async def _merge_command(client: Any, event: Any, args: list[str], account_id: int) -> None:
        del client, account_id
        seed_id = args[0]
        await event.edit(f"⏳ 正在获取 ID 为 {seed_id} 的种子的促销信息...")
        await event.edit(
            f"✅ 种子置顶促销成功！\n\n"
            f"种子：<a href=\"https://www.qingwapt.com/details.php?id={seed_id}\">测试标题</a>"
            f"（ID：<code>{seed_id}</code>）\n"
            f"副标题：测试副标题\n"
            f"促销类型：Free\n"
            f"促销时长：1 天\n"
            f"消耗：8,000 蝌蚪",
            parse_mode="html",
        )

    register_plugin_command("mergept", _merge_command, owner_plugin_key="test_auto_reply")
    set_command_context(
        CommandContext(
            account_id=1,
            templates={},
            providers={},
            command_prefix="。",
            scheduler_command_whitelist=["mergept"],
        )
    )
    rule = _FakeRule(
        id=16,
        name="置顶",
        config={
            "match_type": "template",
            "patterns": ["置顶 id=数字"],
            "scope": "all",
            "reply": "{prefix}mergept {id}",
            "cooldown_seconds": 0,
            "cooldown_scope": "user",
            "daily_limit_per_user": 1,
            "usage_label": "置顶促销",
            "reply_to": False,
        },
    )
    redis = _FakeRedis()
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    event = _make_event("置顶 id=31420", is_private=False, chat_id=-100123, sender_id=111)
    sent = _FakeSentMessage()
    event.respond.return_value = sent

    try:
        await AutoReplyPlugin().on_message(ctx, event)

        event.respond.assert_awaited_once_with("⏳ 正在获取 ID 为 31420 的种子的促销信息...")
        event.reply.assert_not_called()
        assert sent.edit.await_count == 2
        assert sent.edit.await_args_list[0].args[0].startswith("✅ 种子置顶促销成功！")
        final_text = sent.edit.await_args_list[1].args[0]
        assert final_text.startswith("✅ 种子置顶促销成功！")
        assert "种子：<a href=\"https://www.qingwapt.com/details.php?id=31420\">测试标题</a>" in final_text
        assert "副标题：测试副标题" in final_text
        assert "今日已成功置顶促销 1/1 次" in final_text
        assert "本次是第" not in final_text
        assert "当日无法再次使用置顶促销功能" in final_text
        assert sent.edit.await_args_list[1].kwargs == {"parse_mode": "html"}
    finally:
        unregister_plugin_command("mergept", owner_plugin_key="test_auto_reply")
        set_command_context(
            CommandContext(account_id=1, templates={}, providers={}, command_prefix=",")
        )


@pytest.mark.asyncio
async def test_auto_reply_cooldown_notice_reports_remaining_and_count() -> None:
    rule = _FakeRule(
        id=14,
        name="置顶",
        config={
            "match_type": "template",
            "patterns": ["置顶 id=数字"],
            "scope": "all",
            "reply": "ok {id}",
            "cooldown_seconds": "6h",
            "cooldown_scope": "user",
            "daily_limit_per_user": 2,
            "usage_label": "置顶促销",
            "reply_to": False,
        },
    )
    redis = _FakeRedis()
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    first = _make_event("置顶 id=12345", is_private=False, chat_id=-100123, sender_id=111)
    second = _make_event("置顶 id=12345", is_private=False, chat_id=-100123, sender_id=111)

    await AutoReplyPlugin().on_message(ctx, first)
    await AutoReplyPlugin().on_message(ctx, second)

    first.respond.assert_awaited_once_with("ok 12345")
    notice = second.respond.await_args.args[0]
    assert "今日已成功置顶促销 1/2 次" in notice
    assert "本次是第" not in notice
    assert "6小时" in notice


@pytest.mark.asyncio
async def test_auto_reply_reset_cooldown_command_by_reply() -> None:
    rule = _FakeRule(
        id=15,
        name="置顶",
        config={
            "match_type": "keyword",
            "patterns": ["go"],
            "scope": "all",
            "reply": "ok",
            "cooldown_scope": "user",
            "daily_limit_per_user": 2,
        },
    )
    redis = _FakeRedis()
    redis.kv["ar:cool:1:15:chat:-100123"] = "1"
    redis.ttl["ar:cool:1:15:chat:-100123"] = 21600
    redis.kv["ar:cool:1:15:user:-100123:111"] = "1"
    redis.ttl["ar:cool:1:15:user:-100123:111"] = 21600
    redis.kv["ar:pending:1:15:chat:-100123"] = "1"
    redis.kv["ar:pending:1:15:user:-100123:111"] = "1"
    day = datetime.now().strftime("%Y%m%d")
    quota_key = f"ar:quota:1:15:user_day:{day}:-100123:111"
    redis.kv[quota_key] = "2"
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    event = _make_event(",arcd", is_private=False, chat_id=-100123, sender_id=999)
    reply = MagicMock()
    reply.sender_id = 111
    event.get_reply_message = AsyncMock(return_value=reply)
    plugin = AutoReplyPlugin()

    await plugin._cmd_reset_cooldown(ctx.client, event, [], 1, ctx)

    assert "ar:cool:1:15:chat:-100123" not in redis.kv
    assert "ar:cool:1:15:user:-100123:111" not in redis.kv
    assert "ar:pending:1:15:chat:-100123" not in redis.kv
    assert "ar:pending:1:15:user:-100123:111" not in redis.kv
    assert quota_key not in redis.kv
    event.edit.assert_awaited_once()
    assert "已重置用户 111" in event.edit.await_args.args[0]


# ─────────────────────────────────────────────────────
# 纯函数：_match / _scope_ok / _render / _dry_run_match
# ─────────────────────────────────────────────────────
def test_match_keyword_case_insensitive() -> None:
    cfg = {"patterns": ["foo"]}
    assert _match(cfg, "foo bar")
    assert _match(cfg, "FOO BAR")  # 默认忽略大小写


def test_match_keyword_case_sensitive() -> None:
    cfg = {"patterns": ["foo"], "case_sensitive": True}
    assert _match(cfg, "foo bar")
    assert not _match(cfg, "FOO BAR")


def test_match_regex() -> None:
    cfg = {"patterns": [r"^hello,? (\w+)$"], "match_type": "regex"}
    assert _match(cfg, "hello world")
    assert _match(cfg, "hello, world")
    assert not _match(cfg, "hi world")


def test_match_template_variables() -> None:
    cfg = {"patterns": ["置顶 id=数字"], "match_type": "template"}
    matched, output = _dry_run_match({**cfg, "reply": "。pt {id}"}, "置顶 id=12345", "group")
    assert matched is True
    assert output == "。pt 12345"
    matched, output = _dry_run_match({**cfg, "reply": "。pt {id}"}, "置顶 id = 12345", "group")
    assert matched is True
    assert output == "。pt 12345"
    assert _dry_run_match({**cfg, "reply": "。pt {id}"}, "置顶 12345", "group")[0] is False
    assert _dry_run_match({**cfg, "reply": "。pt {id}"}, "置顶 id=abc", "group")[0] is False


def test_match_template_optional_variable_default() -> None:
    cfg = {"patterns": ["我要猜骰 num=数字?"], "match_type": "template", "reply": "。ct {num|1000}"}
    matched, output = _dry_run_match(cfg, "我要猜骰", "group")
    assert matched is True
    assert output == "。ct 1000"
    matched, output = _dry_run_match(cfg, "我要猜骰 num=500", "group")
    assert matched is True
    assert output == "。ct 500"


def test_match_template_curly_variables_still_work() -> None:
    cfg = {"patterns": ["置顶 {id}"], "match_type": "template", "reply": "。pt {id}"}
    matched, output = _dry_run_match(cfg, "置顶 12345", "group")
    assert matched is True
    assert output == "。pt 12345"


def test_parse_cooldown_duration_units() -> None:
    assert _parse_duration_seconds(2) == 2
    assert _parse_duration_seconds("2s") == 2
    assert _parse_duration_seconds("2m") == 120
    assert _parse_duration_seconds("2h") == 7200
    assert _parse_duration_seconds("2d") == 172800
    assert _parse_duration_seconds("2小时") == 7200
    assert _parse_duration_seconds("", default=30) == 0
    assert _parse_duration_seconds("nope", default=30) == 30


def test_match_invalid_regex_returns_false() -> None:
    """配错的正则不应让 _match 抛异常。"""
    cfg = {"patterns": ["[invalid"], "match_type": "regex"}
    assert not _match(cfg, "hello")


def test_scope_ok_variants() -> None:
    class _E:
        is_private = True
        is_group = False
        is_channel = False
        chat_id = 7

    cfg_all = {"scope": "all"}
    cfg_private = {"scope": "private"}
    cfg_groups = {"scope": "groups", "groups": [7, 8]}
    cfg_dict = {"scope": {"groups": [9]}}

    e = _E()
    assert _scope_ok(cfg_all, e)
    assert _scope_ok(cfg_private, e)
    assert _scope_ok(cfg_groups, e)
    assert not _scope_ok(cfg_dict, e)


def test_render_variables() -> None:
    sender = MagicMock(first_name="Bob", username=None, id=2, contact=False)
    chat = MagicMock(title="Room", first_name=None, id=3)
    text = _render("hello {sender} in {chat}: {text}", sender, chat, "wave")
    assert text == "hello Bob in Room: wave"


def test_render_sender_hides_contact_remark_name() -> None:
    sender = MagicMock(first_name="备注名", username="public_user", id=2, contact=True)
    text = _render("hello {sender}", sender, None, "wave")
    assert text == "hello public_user"


def test_render_regex_capture_variables_and_defaults() -> None:
    text = _render(
        "。pt {1} {target} {2|1000}",
        None,
        None,
        "置顶 12345",
        {"1": "12345", "target": "abc", "2": ""},
    )
    assert text == "。pt 12345 abc 1000"


def test_render_with_none_sender_chat() -> None:
    """sender / chat 为 None 也不能崩。"""
    out = _render("[{sender}][{chat}][{text}]", None, None, "x")
    assert out == "[][][x]"


def test_dry_run_match_keyword_hit() -> None:
    cfg = {"patterns": ["ok"], "scope": "all", "reply": "got: {text}"}
    matched, output = _dry_run_match(cfg, "all is ok", "private")
    assert matched is True
    assert output == "got: all is ok"


def test_dry_run_match_regex_capture_output() -> None:
    cfg = {
        "patterns": [r"^置顶\s+(?P<target>\d+)$"],
        "match_type": "regex",
        "scope": "all",
        "reply": "。pt {target}",
    }
    matched, output = _dry_run_match(cfg, "置顶 12345", "group")
    assert matched is True
    assert output == "。pt 12345"


def test_dry_run_match_scope_mismatch() -> None:
    cfg = {"patterns": ["ok"], "scope": "private"}
    matched, output = _dry_run_match(cfg, "ok", "group")
    assert matched is False
    assert output is None
