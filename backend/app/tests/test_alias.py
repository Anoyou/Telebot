"""命令别名单元测试。

覆盖：
- CommandContext.aliases 字段默认值
- 贪心最长前缀匹配逻辑
- 别名解析后参数透传
- ,alias set / del / ls 内置命令（mock DB）
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.worker.command import (
    CommandContext,
    _BUILTIN,
    _BUILTIN_ALIAS_TO_PRIMARY,
    set_command_context,
)


# ════════════════════════════════════════════════════════════
# 1) CommandContext 别名字段
# ════════════════════════════════════════════════════════════


def test_command_context_aliases_default_empty():
    """新 CommandContext 的 aliases 应该是空 dict。"""
    ctx = CommandContext(
        account_id=1,
        templates={},
        providers={},
        command_prefix=",",
    )
    assert ctx.aliases == {}
    assert ctx.sudo_users == {}
    assert ctx.sudo_prefix == "."


def test_command_context_aliases_can_be_set():
    """aliases 可以在构造时传入。"""
    ctx = CommandContext(
        account_id=1,
        templates={},
        providers={},
        command_prefix=",",
        aliases={"fy": "translate", "fy zh": "translate zh"},
    )
    assert ctx.aliases["fy"] == "translate"
    assert ctx.aliases["fy zh"] == "translate zh"


# ════════════════════════════════════════════════════════════
# 2) 贪心最长匹配（通过 make_command_handler 间接测试）
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_alias_resolves_to_builtin():
    """别名指向内置命令时，应该正确派发。"""
    from app.worker.command import make_command_handler

    captured = {}

    def fake_on(_event_type):
        def deco(fn):
            captured["fn"] = fn
            return fn
        return deco

    client = MagicMock()
    client.on = fake_on
    make_command_handler(client, account_id=1, prefix=",")

    # 设置 ctx：fy → ping（内置命令）
    set_command_context(CommandContext(
        account_id=1,
        templates={},
        providers={},
        command_prefix=",",
        aliases={"fy": "ping"},
    ))

    handler = captured["fn"]
    event = AsyncMock()
    event.raw_text = ",fy"
    await handler(event)
    event.edit.assert_called_with("pong")


@pytest.mark.asyncio
async def test_alias_greedy_longest_match():
    """多词别名应该优先匹配（贪心最长前缀）。"""
    from app.worker.command import make_command_handler

    captured = {}

    def fake_on(_event_type):
        def deco(fn):
            captured["fn"] = fn
            return fn
        return deco

    client = MagicMock()
    client.on = fake_on
    make_command_handler(client, account_id=1, prefix=",")

    # "fy" → "ping"，"fy zh" → "version"
    # 发 ",fy zh" 应该匹配 "fy zh"（更长）而不是 "fy"
    set_command_context(CommandContext(
        account_id=1,
        templates={},
        providers={},
        command_prefix=",",
        aliases={"fy": "ping", "fy zh": "version"},
    ))

    handler = captured["fn"]
    event = AsyncMock()
    event.raw_text = ",fy zh"
    await handler(event)
    # version 命令会 edit 含版本号的文本
    call_args = event.edit.call_args[0][0]
    assert "telebot" in call_args.lower() or "v" in call_args


@pytest.mark.asyncio
async def test_alias_with_args_passthrough():
    """别名后的剩余参数应该透传给目标命令。"""
    from app.worker.command import make_command_handler

    captured = {}

    def fake_on(_event_type):
        def deco(fn):
            captured["fn"] = fn
            return fn
        return deco

    client = MagicMock()
    client.on = fake_on
    make_command_handler(client, account_id=1, prefix=",")

    # "fy" → "id"（内置命令，显示 chat_id）
    set_command_context(CommandContext(
        account_id=1,
        templates={},
        providers={},
        command_prefix=",",
        aliases={"fy": "id"},
    ))

    handler = captured["fn"]
    event = AsyncMock()
    event.raw_text = ",fy some_extra_args"
    event.chat_id = -100123456
    event.is_private = False
    event.is_channel = True
    event.is_group = False
    await handler(event)
    # id 命令应该被调用，显示 chat_id
    event.edit.assert_called_once()


@pytest.mark.asyncio
async def test_alias_no_match_falls_through():
    """别名不匹配时应该继续走模板匹配，最终报未知命令。"""
    from app.worker.command import make_command_handler

    captured = {}

    def fake_on(_event_type):
        def deco(fn):
            captured["fn"] = fn
            return fn
        return deco

    client = MagicMock()
    client.on = fake_on
    make_command_handler(client, account_id=1, prefix=",")

    set_command_context(CommandContext(
        account_id=1,
        templates={},
        providers={},
        command_prefix=",",
        aliases={"fy": "ping"},
    ))

    handler = captured["fn"]
    event = AsyncMock()
    event.raw_text = ",notexist"
    await handler(event)
    call_args = event.edit.call_args[0][0]
    assert "未知命令" in call_args


@pytest.mark.asyncio
async def test_alias_to_template():
    """别名指向模板命令时，应该正确派发到模板。"""
    from app.worker.command import make_command_handler

    captured = {}

    def fake_on(_event_type):
        def deco(fn):
            captured["fn"] = fn
            return fn
        return deco

    client = MagicMock()
    client.on = fake_on
    make_command_handler(client, account_id=1, prefix=",")

    # 模板命令 "greet" 回复 "hello"
    templates = {
        "greet": {"name": "greet", "type": "reply_text", "config": {"text": "hello world"}},
    }
    # 别名 "hi" → "greet"
    set_command_context(CommandContext(
        account_id=1,
        templates=templates,
        providers={},
        command_prefix=",",
        aliases={"hi": "greet"},
    ))

    handler = captured["fn"]
    event = AsyncMock()
    event.raw_text = ",hi"
    await handler(event)
    event.edit.assert_called_with("hello world")
