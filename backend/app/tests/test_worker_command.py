"""worker 命令派发的纯函数测试。

不连真 Telethon，不起子进程；只验证内置命令能正确调用 ``event.edit``。
"""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker.command import _BUILTIN, CommandContext, set_command_context


@pytest.mark.asyncio
async def test_help():
    """``,help`` 应当 edit 一次原消息列出命令。"""
    client = AsyncMock()
    event = AsyncMock()
    await _BUILTIN["help"].handler(client, event, [], 1)
    event.edit.assert_called_once()


@pytest.mark.asyncio
async def test_status():
    """``,status`` 应当列出账号 id 与昵称。"""
    client = AsyncMock()
    # client.get_me 是 async；返回一个带 first_name 字段的 mock 对象
    me = AsyncMock()
    me.first_name = "alice"
    me.username = None
    me.id = 1
    client.get_me.return_value = me
    event = AsyncMock()
    await _BUILTIN["status"].handler(client, event, [], 42)
    event.edit.assert_called_once()
    args = event.edit.call_args[0][0]
    assert "#42" in args


@pytest.mark.asyncio
async def test_ping():
    """``,ping`` 必须回复 pong。"""
    client = AsyncMock()
    event = AsyncMock()
    await _BUILTIN["ping"].handler(client, event, [], 1)
    event.edit.assert_called_once_with("pong")


# ════════════════════════════════════════════════════════════
# 命令前缀热加载：handler 应每次消息从 ctx 读 prefix
# 见 worker/command.py:make_command_handler
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_handler_uses_dynamic_prefix_from_ctx():
    """改 ctx.command_prefix 后，**已注册** 的 handler 下一条消息就要按新前缀匹配。

    回归用例：以前 prefix 是闭包里固定 pattern，改系统设置不会生效。
    """
    from app.worker.command import make_command_handler

    # 用 MagicMock 而非真 TelegramClient；只关心 .on(...) 装饰器是否能拿到 handler
    captured = {}

    def fake_on(_event_type):
        def deco(fn):
            captured["fn"] = fn
            return fn

        return deco

    client = MagicMock()
    client.on = fake_on

    # 注册 handler；初始 prefix 闭包默认 ","
    make_command_handler(client, account_id=1, prefix=",")
    handler = captured["fn"]

    # ctx 用 "-" 前缀，模拟用户在 web 上把前缀改成 "-"
    set_command_context(
        CommandContext(
            account_id=1,
            templates={},
            providers={},
            command_prefix="-",
        )
    )

    # 发一条 "-ping"——按新 prefix 应该命中 ping，event.edit 被调用为 "pong"
    event = AsyncMock()
    event.raw_text = "-ping"
    await handler(event)
    event.edit.assert_called_with("pong")

    # 发一条 ",ping" 用旧 prefix——不应匹配新 pattern，handler 直接 return；
    # event.edit 不会被调用
    event2 = AsyncMock()
    event2.raw_text = ",ping"
    await handler(event2)
    event2.edit.assert_not_called()

    # 发一条 "-bogus"——已用新前缀但是未知命令；提示里要含新前缀 "-help"
    event3 = AsyncMock()
    event3.raw_text = "-bogus"
    await handler(event3)
    msg = event3.edit.call_args[0][0]
    assert "未知命令" in msg
    assert "-help" in msg  # 提示用新前缀，不是 ",help"


@pytest.mark.asyncio
async def test_handler_falls_back_when_ctx_missing():
    """ctx 为空时（worker 启动早期）handler 应用闭包 fallback prefix 工作。"""
    from app.worker import command as wcmd
    from app.worker.command import make_command_handler

    captured = {}

    def fake_on(_event_type):
        def deco(fn):
            captured["fn"] = fn
            return fn

        return deco

    client = MagicMock()
    client.on = fake_on
    make_command_handler(client, account_id=1, prefix=";")
    handler = captured["fn"]

    # 模拟 ctx 还没初始化
    wcmd._ctx = None  # type: ignore[attr-defined]
    try:
        event = AsyncMock()
        event.raw_text = ";ping"
        await handler(event)
        event.edit.assert_called_with("pong")
    finally:
        # 恢复一个空 ctx，避免影响其它测试
        wcmd._ctx = CommandContext(
            account_id=1, templates={}, providers={}, command_prefix=","
        )


def test_command_context_has_command_prefix_field():
    """守门测试：CommandContext 必须有 command_prefix 字段且默认 ","。"""
    ctx = CommandContext(account_id=1, templates={}, providers={})
    assert ctx.command_prefix == ","
    ctx2 = CommandContext(
        account_id=1, templates={}, providers={}, command_prefix="-"
    )
    assert ctx2.command_prefix == "-"


def test_re_escape_special_prefix():
    """守门测试：handler 内对 prefix 用 ``re.escape``，所以特殊字符（如 ``.``）也安全。"""
    # 模拟 handler 里那条 pattern 编译；以前出过 bug 让点 = 任意字符
    p = "."
    pat = re.compile(rf"^{re.escape(p)}(\w+)(?:\s+(.*))?$", re.S)
    assert pat.match(".ping")
    # ``aping`` 不应该命中（如果没 escape，"." 会匹配 "a"）
    assert not pat.match("aping")


def test_restart_commands_registered():
    """守门测试：项目级/账号级重启命令必须都存在。"""
    assert "restart" in _BUILTIN
    assert "reboot" in _BUILTIN
