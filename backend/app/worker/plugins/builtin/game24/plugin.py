"""24 点游戏插件。

触发方式：
  {前缀}24d <奖金金额>      例：,24d 2000

前缀跟随系统设置（默认 `,`），指令名可通过 config.command 自定义。

游戏流程：
  1. 自己发送触发命令（outgoing，如 ,24d 2000）
  2. Bot 编辑该消息，展示 4 个数字、奖金、限时
  3. 群内其他成员回复算式（incoming，必须恰好使用 4 个数字各一次，结果 = 24）
  4. 第一个答对的人获得奖金：Bot 回复其消息 "+<奖金数量>"
  5. 超时（默认 500 秒）无人答对则自动结束
"""
from __future__ import annotations

import ast
import asyncio
import random
import re
from typing import Any

from telethon import events

from app.worker.plugins.base import Plugin, PluginContext, register


# ─────────────────────────────────────────────────────
# 24 点求解器
# ─────────────────────────────────────────────────────
def _can_reach_24(nums: list[float]) -> bool:
    """递归检查 nums 中的数能否通过 +-*/ 和括号得到 24。"""
    if len(nums) == 1:
        return abs(nums[0] - 24) < 1e-6
    for i in range(len(nums)):
        for j in range(len(nums)):
            if i == j:
                continue
            a, b = nums[i], nums[j]
            remaining = [nums[k] for k in range(len(nums)) if k != i and k != j]
            # a + b（加法交换律去重）
            if i < j and _can_reach_24(remaining + [a + b]):
                return True
            # a - b
            if _can_reach_24(remaining + [a - b]):
                return True
            # a * b（乘法交换律去重）
            if i < j and _can_reach_24(remaining + [a * b]):
                return True
            # a / b
            if abs(b) > 1e-9 and _can_reach_24(remaining + [a / b]):
                return True
    return False


def generate_24_puzzle(max_attempts: int = 2000) -> list[int]:
    """生成一组可算出 24 的 4 个整数（范围 1–13，J/Q/K 对应 11/12/13）。"""
    for _ in range(max_attempts):
        nums = [random.randint(1, 13) for _ in range(4)]
        if _can_reach_24([float(n) for n in nums]):
            return nums
    # 兜底：已知一定有解的组
    return [1, 2, 3, 4]


# ─────────────────────────────────────────────────────
# 表达式安全求值与校验
# ─────────────────────────────────────────────────────
# 用户输入中可能使用的运算符别名
_OP_TRANS = str.maketrans("xX÷×（）", "****()")  # x/X/×→*  ÷→/  （）→()


def _safe_eval(expr: str) -> float | None:
    """安全求值：仅允许 +-*/ 和括号，返回 float 或 None。

    通过 ast 白名单过滤节点类型，禁止函数调用、属性访问、幂运算等。
    """
    translated = expr.translate(_OP_TRANS)
    try:
        tree = ast.parse(translated, mode="eval")
    except SyntaxError:
        return None

    allowed_types = (
        ast.Expression,
        ast.Constant,  # Python 3.8+ 字面量
        ast.Num,        # Python 3.7 兼容
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.UAdd,
        ast.USub,
        ast.Load,
    )

    def _walk(node: ast.AST) -> bool:
        if not isinstance(node, allowed_types):
            return False
        for child in ast.iter_child_nodes(node):
            if not _walk(child):
                return False
        return True

    if not _walk(tree):
        return None

    try:
        result = eval(translated, {"__builtins__": {}}, {})  # noqa: S307
        return float(result)
    except Exception:
        return None


def _extract_numbers(expr: str) -> list[int]:
    """从用户表达式中提取所有整数（忽略运算符和括号）。"""
    cleaned = expr.translate(_OP_TRANS)
    return [int(tok) for tok in re.findall(r"\d+", cleaned)]


def check_answer(expr: str, target_numbers: list[int]) -> bool:
    """检查用户答案是否正确。

    条件：
      1. 表达式中恰好出现 target_numbers 各数一次（顺序不限）
      2. 运算结果严格等于 24（容差 1e-6）
    """
    if not expr or not target_numbers:
        return False

    used = sorted(_extract_numbers(expr))
    target_sorted = sorted(target_numbers)
    if used != target_sorted:
        return False

    result = _safe_eval(expr)
    if result is None:
        return False
    return abs(result - 24) < 1e-6


# ─────────────────────────────────────────────────────
# 游戏状态
# ─────────────────────────────────────────────────────
class GameState:
    """单个 chat 的一局 24 点游戏状态。"""

    def __init__(
        self,
        chat_id: int,
        trigger_msg_id: int,
        numbers: list[int],
        prize: int,
        timeout: int = 500,
    ) -> None:
        self.chat_id = chat_id
        self.trigger_msg_id = trigger_msg_id
        self.numbers = numbers
        self.prize = prize
        self.timeout = timeout
        self.active = True
        self.winner_id: int | None = None
        self.winner_name: str | None = None
        self.winner_msg_id: int | None = None
        self._timeout_task: asyncio.Task | None = None


# ─────────────────────────────────────────────────────
# 插件主类
# ─────────────────────────────────────────────────────
@register
class Game24Plugin(Plugin):
    """24 点游戏插件。

    触发走 commands（outgoing 命令分发），答题走 on_message（incoming）。
    指令名默认 24d，可通过 config.command 自定义。
    """

    key = "game24"
    display_name = "24点游戏"

    def __init__(self) -> None:
        super().__init__()
        self._games: dict[int, GameState] = {}
        self._command_name: str = "24d"
        self._timeout: int = 500
        self._account_id: int | None = None
        self._ctx: PluginContext | None = None

    # ── 生命周期 ─────────────────────────────────
    async def on_startup(self, ctx: PluginContext) -> None:
        self._account_id = ctx.account_id
        self._ctx = ctx
        cfg = ctx.config or {}
        cmd = cfg.get("command", "")
        if cmd:
            self._command_name = cmd
        self._timeout = int(cfg.get("timeout", 500))
        # 动态注册命令：按 config.command 名称
        self.commands = {self._command_name: self._cmd_handler}
        if ctx.log:
            await ctx.log(
                "info",
                f"[game24] 启动，指令名={self._command_name}，限时={self._timeout}s",
            )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for gs in self._games.values():
            if gs._timeout_task and not gs._timeout_task.done():
                gs._timeout_task.cancel()
        self._games.clear()

    # ── 命令 handler（outgoing 触发）────────────
    async def _cmd_handler(
        self,
        client: Any,
        event: events.NewMessage.Event,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        """处理 {前缀}{command_name} <奖金> 命令。"""
        # 解析奖金：取 args 第一个数字参数
        prize = 0
        for a in args:
            try:
                prize = int(a)
                break
            except ValueError:
                continue

        if prize <= 0:
            await event.edit("⚠️ 请指定奖金金额")
            return

        chat_id = event.chat_id

        if chat_id in self._games and self._games[chat_id].active:
            await event.edit("⚠️ 当前已有进行中的 24 点游戏，请先答完再开新局。")
            return

        numbers = generate_24_puzzle()
        nums_disp = " ] [ ".join(str(n) for n in numbers)

        display_text = (
            "🎯 24 点开始\n"
            "━━━━━━━━\n"
            f"🎲 数字：[ {nums_disp} ]\n"
            f"💰 奖金：{prize}\n"
            f"⏳ 限时：{self._timeout} 秒\n"
            "🔢 可用符号：+ - x ÷ * / ( )\n"
            "\n"
            "请直接发送算式，结果必须等于 24。\n"
            "示例：(1+2+3)*4、8/(3-8/3)\n"
            "必须恰好使用这 4 个数字各一次，可用 + - x ÷ * / ( )"
        )

        try:
            await event.edit(display_text)
        except Exception as exc:
            if ctx.log:
                await ctx.log("error", f"[game24] 编辑消息失败: {exc}")
            return

        gs = GameState(
            chat_id=chat_id,
            trigger_msg_id=event.message.id,
            numbers=numbers,
            prize=prize,
            timeout=self._timeout,
        )
        self._games[chat_id] = gs

        # 启动超时定时器
        gs._timeout_task = asyncio.create_task(self._game_timeout(ctx, gs))

        if ctx.log:
            await ctx.log(
                "info",
                f"[game24] 新游戏开始 chat={chat_id} nums={numbers} prize={prize}",
            )

    # ── incoming：答题 ─────────────────────────
    async def on_message(self, ctx: PluginContext, event: events.NewMessage.Event) -> None:
        chat_id = event.chat_id
        if chat_id not in self._games:
            return
        gs = self._games[chat_id]
        if gs.active:
            await self._handle_answer(ctx, event, gs)

    # ── 处理答题 ─────────────────────────────
    async def _handle_answer(
        self,
        ctx: PluginContext,
        event: events.NewMessage.Event,
        gs: GameState,
    ) -> None:
        text: str = (event.raw_text or "").strip()
        if len(text) < 3:
            return

        sender = await event.get_sender()
        sender_name = (
            getattr(sender, "first_name", "")
            or getattr(sender, "username", "")
            or str(getattr(sender, "id", event.sender_id))
        )

        if ctx.log:
            await ctx.log(
                "info",
                f"[game24] 收到回答 chat={gs.chat_id} user={sender_name} text={text!r}",
            )

        if not check_answer(text, gs.numbers):
            return

        # 第一个答对的人！
        gs.active = False
        gs.winner_id = event.sender_id
        gs.winner_name = sender_name
        gs.winner_msg_id = event.message.id

        if gs._timeout_task and not gs._timeout_task.done():
            gs._timeout_task.cancel()

        # 发奖：回复获奖者消息 "+奖金数量"
        try:
            await ctx.client.send_message(
                entity=gs.chat_id,
                message=f"+{gs.prize}",
                reply_to=event.message.id,
            )
        except Exception as exc:
            if ctx.log:
                await ctx.log("error", f"[game24] 发奖失败: {exc}")

        # 编辑题目消息宣布获胜者
        try:
            msg_obj = await ctx.client.get_messages(gs.chat_id, ids=gs.trigger_msg_id)
            original = msg_obj.text or ""
            announce = (
                f"{original}\n\n"
                f"🏆 恭喜 {sender_name} 答对！\n"
                f"💰 奖金 +{gs.prize} 已发放。"
            )
            await ctx.client.edit_message(
                entity=gs.chat_id,
                message=gs.trigger_msg_id,
                text=announce,
            )
        except Exception as exc:
            if ctx.log:
                await ctx.log("error", f"[game24] 编辑宣布消息失败: {exc}")

        if ctx.log:
            await ctx.log(
                "info",
                f"[game24] 游戏结束 chat={gs.chat_id} winner={sender_name}({gs.winner_id})",
            )

    # ── 超时处理 ─────────────────────────────
    async def _game_timeout(self, ctx: PluginContext, gs: GameState) -> None:
        await asyncio.sleep(gs.timeout)
        if not gs.active:
            return

        gs.active = False
        try:
            msg_obj = await ctx.client.get_messages(gs.chat_id, ids=gs.trigger_msg_id)
            original = msg_obj.text or ""
            timeout_text = f"{original}\n\n⏰ 时间到！无人答对，游戏结束。"
            await ctx.client.edit_message(
                entity=gs.chat_id,
                message=gs.trigger_msg_id,
                text=timeout_text,
            )
        except Exception as exc:
            if ctx.log:
                await ctx.log("error", f"[game24] 超时宣布失败: {exc}")

        if ctx.log:
            await ctx.log("info", f"[game24] 游戏超时结束 chat={gs.chat_id}")
