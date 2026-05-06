"""auto_reply 插件单元测试：mock Telethon event/client + fakeredis + 假 engine。

覆盖：
  - 关键词命中 → engine.acquire 被调用 → event.respond 被调用
  - 不在 scope（私聊规则收到群消息）→ 跳过，不 respond
  - 冷却中（redis 已有 cool_key）→ 跳过
  - 模板变量 {sender} / {chat} / {text} 正确渲染
  - dry-run 函数对关键词 / 正则 / scope 行为正确
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker.plugins.base import PluginContext
from app.worker.plugins.builtin.auto_reply import (
    AutoReplyPlugin,
    _dry_run_match,
    _match,
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

    async def get(self, key: str):
        return self.kv.get(key)

    async def set(self, key: str, val: str, ex: int = 0) -> bool:
        self.kv[key] = val
        return True

    async def rpush(self, key: str, val: str) -> int:  # 给 ctx.log 兜底
        return 1


# ─────────────────────────────────────────────────────
# 假规则：模仿 ORM Rule 的最小字段集
# ─────────────────────────────────────────────────────
@dataclass
class _FakeRule:
    id: int
    config: dict
    priority: int = 100
    enabled: bool = True


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


def _make_event(text: str, *, is_private: bool = True, chat_id: int = 100):
    """构造一个假 Telethon NewMessage 事件。"""
    event = AsyncMock()
    event.raw_text = text
    event.chat_id = chat_id
    event.is_private = is_private
    event.is_group = not is_private
    event.is_channel = False

    # sender / chat 是 awaitable，返回带名字的 dummy 对象
    sender = MagicMock()
    sender.first_name = "Alice"
    sender.username = None
    sender.id = 42
    chat = MagicMock()
    chat.title = "PrivChat" if not is_private else None
    chat.first_name = "Alice"
    chat.id = chat_id
    event.get_sender = AsyncMock(return_value=sender)
    event.get_chat = AsyncMock(return_value=chat)
    event.respond = AsyncMock()
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
async def test_cooldown_skips_send() -> None:
    """redis 已有冷却 key → 不应再回复。"""
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
    redis.kv["ar:cool:1:3:100"] = "1"  # 模拟"还在冷却"
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, redis)
    event = _make_event("hello", is_private=True, chat_id=100)

    await AutoReplyPlugin().on_message(ctx, event)

    engine.acquire.assert_not_called()
    event.respond.assert_not_called()


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
    sender = MagicMock(first_name="Bob", username=None, id=2)
    chat = MagicMock(title="Room", first_name=None, id=3)
    text = _render("hello {sender} in {chat}: {text}", sender, chat, "wave")
    assert text == "hello Bob in Room: wave"


def test_render_with_none_sender_chat() -> None:
    """sender / chat 为 None 也不能崩。"""
    out = _render("[{sender}][{chat}][{text}]", None, None, "x")
    assert out == "[][][x]"


def test_dry_run_match_keyword_hit() -> None:
    cfg = {"patterns": ["ok"], "scope": "all", "reply": "got: {text}"}
    matched, output = _dry_run_match(cfg, "all is ok", "private")
    assert matched is True
    assert output == "got: all is ok"


def test_dry_run_match_scope_mismatch() -> None:
    cfg = {"patterns": ["ok"], "scope": "private"}
    matched, output = _dry_run_match(cfg, "ok", "group")
    assert matched is False
    assert output is None
