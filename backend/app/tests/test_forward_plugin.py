"""forward 插件单元测试：mock Telethon event/client + fakeredis + 假 engine。

覆盖：
  - source_kind=all → 命中 → engine.acquire 被调用 → forward_to 被调用
  - source_kind=peers → chat_id 不在列表 → 跳过
  - source_kind=keyword → 关键词命中 → 触发；不命中 → 跳过
  - include_media=False → 含媒体消息跳过
  - mode=copy_text / quote / link_only 各自走对应 send_message 路径
  - FloodWait → 自动 sleep 后重试一次（断言 forward_to 被调用 2 次）
  - engine.acquire 拒绝（allowed=False）→ 不调用 forward_to
  - 纯函数 _match_source / _expand_chat_id / _build_msg_link / _dry_run_match
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker.plugins.base import PluginContext
from app.worker.plugins.builtin.forward.plugin import (
    ForwardPlugin,
    _build_msg_link,
    _dry_run_match,
    _expand_chat_id,
    _match_source,
)
from app.worker.ratelimit.engine import RateLimitDecision
from app.worker.ratelimit.humanize import HumanizeOpts


# ─────────────────────────────────────────────────────
# 公用：极简 fake redis（forward 只在 ctx.log 路径用 rpush）
# ─────────────────────────────────────────────────────
class _FakeRedis:
    async def rpush(self, key: str, val: str) -> int:
        return 1

    async def get(self, key: str):
        return None

    async def set(self, *_a, **_kw):
        return True


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
    )
    engine.acquire = AsyncMock(
        return_value=RateLimitDecision(
            allowed=allowed, wait_seconds=wait, outcome=outcome
        )
    )
    engine.on_flood_wait = AsyncMock()
    return engine


def _make_event(
    text: str,
    *,
    chat_id: int = 100,
    msg_id: int = 555,
    has_media: bool = False,
    chat_title: str | None = "源群",
):
    """构造一个假 Telethon NewMessage 事件。"""
    event = AsyncMock()
    event.raw_text = text
    event.chat_id = chat_id
    event.is_private = False
    event.is_group = True
    event.is_channel = False

    # message 子对象：text / media / id / forward_to
    msg = MagicMock()
    msg.text = text
    msg.media = object() if has_media else None
    msg.id = msg_id
    msg.forward_to = AsyncMock()
    event.message = msg

    chat = MagicMock()
    chat.title = chat_title
    chat.username = None
    chat.first_name = None
    event.get_chat = AsyncMock(return_value=chat)
    return event


def _make_ctx(rules: list[_FakeRule], engine: Any, redis: Any) -> PluginContext:
    client = MagicMock()
    client.send_message = AsyncMock()
    return PluginContext(
        account_id=1,
        feature_key="forward",
        config={},
        rules=list(rules),
        client=client,
        engine=engine,
        redis=redis,
        log=AsyncMock(),
    )


# ─────────────────────────────────────────────────────
# 用例：source_kind=all → 命中 forward_native
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_all_source_native_forward() -> None:
    """source_kind=all、mode=forward_native 应调 forward_to + acquire 一次。"""
    rule = _FakeRule(
        id=1,
        config={
            "source_kind": "all",
            "target_chat_id": 999,
            "mode": "forward_native",
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hello")

    await ForwardPlugin().on_message(ctx, event)

    engine.acquire.assert_awaited_once()
    # 第一个位置参数：account_id；第二个：动作；kwargs 应有 peer_id
    args, kwargs = engine.acquire.call_args
    assert args[1] == "forward_message"
    assert kwargs.get("peer_id") == 999

    event.message.forward_to.assert_awaited_once_with(999)
    ctx.client.send_message.assert_not_called()


# ─────────────────────────────────────────────────────
# 用例：source_kind=peers → 不在列表 → 跳过
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_peers_source_miss_skips() -> None:
    rule = _FakeRule(
        id=2,
        config={
            "source_kind": "peers",
            "source_peers": [-1009999999999],
            "target_chat_id": 999,
            "mode": "forward_native",
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hi", chat_id=-1001234567890)

    await ForwardPlugin().on_message(ctx, event)

    engine.acquire.assert_not_called()
    event.message.forward_to.assert_not_called()


@pytest.mark.asyncio
async def test_peers_source_hit_with_equivalent_id() -> None:
    """填的是 bare id（去掉 -100 前缀），应当能匹配到完整 -100 形式的 chat_id。"""
    rule = _FakeRule(
        id=3,
        config={
            "source_kind": "peers",
            "source_peers": [1234567890],  # 用户从 t.me/c/<id> 复制下来的形式
            "target_chat_id": 999,
            "mode": "forward_native",
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hi", chat_id=-1001234567890)

    await ForwardPlugin().on_message(ctx, event)
    event.message.forward_to.assert_awaited_once()


# ─────────────────────────────────────────────────────
# 用例：keyword 命中 / 不命中
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_keyword_hit_triggers_copy_text() -> None:
    rule = _FakeRule(
        id=4,
        config={
            "source_kind": "keyword",
            "keyword": "紧急",
            "target_chat_id": 999,
            "mode": "copy_text",
            "header": "[警报] ",
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("紧急 测试")

    await ForwardPlugin().on_message(ctx, event)

    ctx.client.send_message.assert_awaited_once()
    target, body = ctx.client.send_message.call_args[0]
    assert target == 999
    assert body == "[警报] 紧急 测试"


@pytest.mark.asyncio
async def test_keyword_miss_skipped() -> None:
    rule = _FakeRule(
        id=5,
        config={
            "source_kind": "keyword",
            "keyword": "紧急",
            "target_chat_id": 999,
            "mode": "copy_text",
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hello world")

    await ForwardPlugin().on_message(ctx, event)
    engine.acquire.assert_not_called()
    ctx.client.send_message.assert_not_called()


# ─────────────────────────────────────────────────────
# 用例：include_media=False → 含媒体消息跳过
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_include_media_false_skips_media_msg() -> None:
    rule = _FakeRule(
        id=6,
        config={
            "source_kind": "all",
            "target_chat_id": 999,
            "mode": "forward_native",
            "include_media": False,
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("photo caption", has_media=True)

    await ForwardPlugin().on_message(ctx, event)
    engine.acquire.assert_not_called()
    event.message.forward_to.assert_not_called()


# ─────────────────────────────────────────────────────
# 用例：mode=quote → 引用包装文案带"📨 来自"
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mode_quote_wraps_text() -> None:
    rule = _FakeRule(
        id=7,
        config={
            "source_kind": "all",
            "target_chat_id": 999,
            "mode": "quote",
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hi", chat_title="重要群")

    await ForwardPlugin().on_message(ctx, event)
    target, body = ctx.client.send_message.call_args[0]
    assert target == 999
    assert "📨 来自 重要群" in body
    assert "hi" in body


# ─────────────────────────────────────────────────────
# 用例：mode=link_only → 公开超级群生成 t.me/c/ 链接
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mode_link_only_supergroup_link() -> None:
    rule = _FakeRule(
        id=8,
        config={
            "source_kind": "all",
            "target_chat_id": 999,
            "mode": "link_only",
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hi", chat_id=-1001234567890, msg_id=42)

    await ForwardPlugin().on_message(ctx, event)
    _, body = ctx.client.send_message.call_args[0]
    assert body == "https://t.me/c/1234567890/42"


# ─────────────────────────────────────────────────────
# 用例：FloodWait → sleep & retry once
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_floodwait_retries_once(monkeypatch) -> None:
    """forward_to 第一次抛 FloodWaitError，第二次成功；on_flood_wait 应被调用一次。"""
    # 构造一个 FloodWaitError 兼容类（避免硬依赖 telethon 异常类层级）
    try:
        from telethon.errors import FloodWaitError
    except Exception:  # pragma: no cover
        class FloodWaitError(Exception):  # type: ignore[no-redef]
            def __init__(self, seconds: int):
                super().__init__(f"flood {seconds}s")
                self.seconds = seconds

    rule = _FakeRule(
        id=9,
        config={
            "source_kind": "all",
            "target_chat_id": 999,
            "mode": "forward_native",
        },
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hi")

    # 让 forward_to 第一次抛 FloodWait，第二次成功
    call_count = {"n": 0}

    async def _flaky_forward_to(target):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # FloodWaitError 在不同 telethon 版本里构造方式略有差异；用 try/except 兜底
            try:
                raise FloodWaitError(2)
            except TypeError as orig:
                exc = FloodWaitError("flood")
                exc.seconds = 2
                raise exc from orig
        return None

    event.message.forward_to = _flaky_forward_to

    # 把 asyncio.sleep mock 掉，避免真等 2 秒
    import app.worker.plugins.builtin.forward.plugin as plugin_mod

    monkeypatch.setattr(plugin_mod.asyncio, "sleep", AsyncMock())

    await ForwardPlugin().on_message(ctx, event)

    assert call_count["n"] == 2, "forward_to 应被尝试 2 次（首次 FloodWait + 重试一次）"
    engine.on_flood_wait.assert_awaited_once()


# ─────────────────────────────────────────────────────
# 用例：engine.acquire allowed=False → 不发送
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_engine_drop_blocks_forward() -> None:
    rule = _FakeRule(
        id=10,
        config={
            "source_kind": "all",
            "target_chat_id": 999,
            "mode": "forward_native",
        },
    )
    engine = _make_engine(allowed=False, outcome="drop")
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hi")

    await ForwardPlugin().on_message(ctx, event)
    engine.acquire.assert_awaited_once()
    event.message.forward_to.assert_not_called()


# ─────────────────────────────────────────────────────
# 用例：未填 target_chat_id → 跳过 + 写 warn 日志
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_missing_target_chat_id_skipped() -> None:
    rule = _FakeRule(
        id=11,
        config={"source_kind": "all", "mode": "forward_native"},  # 缺 target
    )
    engine = _make_engine()
    ctx = _make_ctx([rule], engine, _FakeRedis())
    event = _make_event("hi")

    await ForwardPlugin().on_message(ctx, event)
    engine.acquire.assert_not_called()


# ─────────────────────────────────────────────────────
# 纯函数：_match_source / _expand_chat_id / _build_msg_link / _dry_run_match
# ─────────────────────────────────────────────────────
def test_expand_chat_id_supergroup() -> None:
    """超级群 chat_id 应该展开出 bare / -bare / +bare 三种形式都能匹配。"""
    s = _expand_chat_id(-1001234567890)
    assert -1001234567890 in s
    assert 1001234567890 in s
    assert 1234567890 in s
    assert -1234567890 in s


def test_expand_chat_id_bare_id_round_trip() -> None:
    """从 bare id 反推也能命中超级群形式。"""
    s = _expand_chat_id(1234567890)
    assert -1001234567890 in s
    assert 1234567890 in s


def test_build_msg_link_supergroup() -> None:
    class _M:
        id = 99

    class _E:
        chat_id = -1009876543210
        message = _M()

    assert _build_msg_link(_E()) == "https://t.me/c/9876543210/99"


def test_build_msg_link_basic_group_fallback() -> None:
    class _M:
        id = 5

    class _E:
        chat_id = -1234567890
        message = _M()

    out = _build_msg_link(_E())
    assert "消息引用" in out
    assert "id=5" in out


def test_match_source_all_always_true() -> None:
    class _E:
        chat_id = 0
        raw_text = ""

        class message:
            text = ""

    assert _match_source(_E(), {"source_kind": "all"})


def test_match_source_keyword_case_insensitive() -> None:
    class _M:
        text = "URGENT please"

    class _E:
        chat_id = 1
        raw_text = "URGENT please"
        message = _M()

    assert _match_source(_E(), {"source_kind": "keyword", "keyword": "urgent"})
    assert not _match_source(_E(), {"source_kind": "keyword", "keyword": "spam"})


def test_match_source_keyword_empty_returns_false() -> None:
    """空 keyword 应当返回 False，避免误把所有消息都当成命中。"""

    class _M:
        text = "anything"

    class _E:
        chat_id = 1
        raw_text = "anything"
        message = _M()

    assert not _match_source(_E(), {"source_kind": "keyword", "keyword": ""})


def test_dry_run_match_all_kind() -> None:
    matched, output = _dry_run_match(
        {"source_kind": "all", "target_chat_id": 999, "mode": "forward_native"},
        "anything",
    )
    assert matched is True
    assert output is not None
    assert "999" in output
    assert "forward_native" in output


def test_dry_run_match_peers_miss_returns_false() -> None:
    matched, output = _dry_run_match(
        {
            "source_kind": "peers",
            "source_peers": [-1001234567890],
            "target_chat_id": 999,
            "mode": "copy_text",
        },
        "x",
        chat_id=-1009999999999,
    )
    assert matched is False
    assert output is None


def test_dry_run_match_keyword_hit() -> None:
    matched, output = _dry_run_match(
        {
            "source_kind": "keyword",
            "keyword": "ok",
            "target_chat_id": 999,
            "mode": "quote",
        },
        "all is OK now",
    )
    assert matched is True
    assert output and "999" in output
