"""24 点游戏插件。

触发方式：
  {前缀}24d <奖金金额>      例：,24d 2000

前缀跟随系统设置（默认 `,`），指令名可通过 config.command 自定义。
"""
from __future__ import annotations

import ast
import asyncio
import random
import re
from dataclasses import dataclass
from typing import Any

from telethon import events

from app.worker.command import current_command_prefix
from app.worker.plugins.base import Plugin, PluginContext, register

DEFAULT_COMMAND = "24d"
DEFAULT_TIMEOUT = 500
MIN_TIMEOUT = 30
MAX_TIMEOUT = 3600


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

            if i < j and _can_reach_24(remaining + [a + b]):
                return True
            if _can_reach_24(remaining + [a - b]):
                return True
            if i < j and _can_reach_24(remaining + [a * b]):
                return True
            if abs(b) > 1e-9 and _can_reach_24(remaining + [a / b]):
                return True

    return False


def generate_24_puzzle(max_attempts: int = 2000) -> list[int]:
    """生成一组可算出 24 的 4 个整数（范围 1-13，J/Q/K 对应 11/12/13）。"""

    for _ in range(max_attempts):
        nums = [random.randint(1, 13) for _ in range(4)]
        if _can_reach_24([float(n) for n in nums]):
            return nums
    return [1, 2, 3, 4]


# ─────────────────────────────────────────────────────
# 表达式安全求值与校验
# ─────────────────────────────────────────────────────
_OP_TRANS = str.maketrans("xX÷×（）＋－", "**/*()+-")


@dataclass(frozen=True)
class AnswerCheck:
    ok: bool
    normalized_expr: str = ""
    value: float | None = None
    reason: str = ""


def _normalize_answer_expr(expr: str) -> str:
    """兼容常见答题写法：`表达式=24`、前后文字、全角运算符。"""

    raw = (expr or "").strip()
    if "=" in raw:
        raw = raw.split("=", 1)[0].strip()
    allowed = set("0123456789+-*/()xX÷×（）＋－ \t")
    return "".join(ch for ch in raw if ch in allowed).strip()


def _extract_numbers(expr: str) -> list[int]:
    """从用户表达式中提取所有整数。"""

    return [int(tok) for tok in re.findall(r"\d+", expr.translate(_OP_TRANS))]


def _safe_eval(expr: str) -> float | None:
    """安全求值：仅允许数字、+-*/ 和括号。"""

    translated = expr.translate(_OP_TRANS)
    try:
        tree = ast.parse(translated, mode="eval")
    except SyntaxError:
        return None

    allowed_types = (
        ast.Expression,
        ast.Constant,
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
        if isinstance(node, ast.Constant) and not isinstance(node.value, int | float):
            return False
        return all(_walk(child) for child in ast.iter_child_nodes(node))

    if not _walk(tree):
        return None

    try:
        result = eval(translated, {"__builtins__": {}}, {})  # noqa: S307
        return float(result)
    except Exception:
        return None


def check_answer_detailed(expr: str, target_numbers: list[int]) -> AnswerCheck:
    """检查答案，并返回适合写日志的失败原因。"""

    if not expr:
        return AnswerCheck(ok=False, reason="答案为空")
    if not target_numbers:
        return AnswerCheck(ok=False, reason="本局题目数字为空，无法判题")

    candidate = _normalize_answer_expr(expr)
    if not candidate:
        return AnswerCheck(ok=False, reason="没有识别到可计算的表达式")

    used = sorted(_extract_numbers(candidate))
    target_sorted = sorted(target_numbers)
    if used != target_sorted:
        return AnswerCheck(
            ok=False,
            normalized_expr=candidate,
            reason=f"使用的数字 {used} 与题目数字 {target_sorted} 不一致",
        )

    value = _safe_eval(candidate)
    if value is None:
        return AnswerCheck(ok=False, normalized_expr=candidate, reason="表达式无法安全计算")
    if abs(value - 24) >= 1e-6:
        return AnswerCheck(
            ok=False,
            normalized_expr=candidate,
            value=value,
            reason=f"计算结果是 {value:g}，不是 24",
        )

    return AnswerCheck(ok=True, normalized_expr=candidate, value=value)


def check_answer(expr: str, target_numbers: list[int]) -> bool:
    """兼容旧单测/调用方：只返回是否答对。"""

    return check_answer_detailed(expr, target_numbers).ok


# ─────────────────────────────────────────────────────
# 配置、事件与游戏状态
# ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class Game24Config:
    command: str = DEFAULT_COMMAND
    timeout: int = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class IncomingMessage:
    chat_id: int | None
    message_id: int | None
    sender_id: int | None
    sender_name: str
    text: str
    outgoing: bool


@dataclass
class GameState:
    """单个 chat 的一局 24 点游戏状态。"""

    chat_id: int
    trigger_msg_id: int
    numbers: list[int]
    prize: int
    timeout: int = DEFAULT_TIMEOUT
    active: bool = True
    winner_id: int | None = None
    winner_name: str | None = None
    winner_msg_id: int | None = None
    timeout_task: asyncio.Task | None = None

    @property
    def _timeout_task(self) -> asyncio.Task | None:
        """兼容旧测试/旧代码访问。"""

        return self.timeout_task

    @_timeout_task.setter
    def _timeout_task(self, value: asyncio.Task | None) -> None:
        self.timeout_task = value


def _clean_command_name(value: Any) -> str:
    command = str(value or "").strip()
    if not command or re.search(r"\s", command):
        return DEFAULT_COMMAND
    return command[:32]


def _clamp_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, timeout))


def _load_config(raw: dict[str, Any] | None) -> Game24Config:
    cfg = raw or {}
    return Game24Config(
        command=_clean_command_name(cfg.get("command", DEFAULT_COMMAND)),
        timeout=_clamp_timeout(cfg.get("timeout", DEFAULT_TIMEOUT)),
    )


def _event_message(event: Any) -> Any:
    """兼容 Telethon NewMessage.Event 与裸 Message。"""

    return getattr(event, "message", event)


def _event_chat_id(event: Any) -> int | None:
    return getattr(event, "chat_id", None) or getattr(_event_message(event), "chat_id", None)


def _event_message_id(event: Any) -> int | None:
    msg = _event_message(event)
    return getattr(msg, "id", None) or getattr(event, "id", None)


def _event_text(event: Any) -> str:
    return str(
        getattr(event, "raw_text", None)
        or getattr(_event_message(event), "raw_text", None)
        or getattr(_event_message(event), "text", None)
        or ""
    ).strip()


def _event_sender_id(event: Any) -> int | None:
    return getattr(event, "sender_id", None) or getattr(_event_message(event), "sender_id", None)


def _event_outgoing(event: Any) -> bool:
    try:
        return bool(event.outgoing)
    except AttributeError:
        return bool(getattr(_event_message(event), "out", False))


async def _event_sender_name(event: Any) -> str:
    sender = None
    for target in (event, _event_message(event)):
        getter = getattr(target, "get_sender", None)
        if not callable(getter):
            continue
        try:
            sender = await getter()
            if sender is not None:
                break
        except Exception:
            sender = None

    return (
        getattr(sender, "first_name", "")
        or getattr(sender, "username", "")
        or str(getattr(sender, "id", _event_sender_id(event) or "未知用户"))
    )


async def _adapt_incoming_message(event: Any) -> IncomingMessage:
    return IncomingMessage(
        chat_id=_event_chat_id(event),
        message_id=_event_message_id(event),
        sender_id=_event_sender_id(event),
        sender_name=await _event_sender_name(event),
        text=_event_text(event),
        outgoing=_event_outgoing(event),
    )


# ─────────────────────────────────────────────────────
# 插件主类
# ─────────────────────────────────────────────────────
@register
class Game24Plugin(Plugin):
    """24 点游戏插件。

    触发走 commands（outgoing 命令分发），答题走 on_message（incoming）。
    """

    key = "game24"
    display_name = "24点游戏"
    message_channels = {"incoming"}
    owner_only = False
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._games: dict[int, GameState] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._config = Game24Config()
        self._self_tg_user_id: int | None = None
        self._account_id: int | None = None
        self._ctx: PluginContext | None = None

    @property
    def _command_name(self) -> str:
        return self._config.command

    @property
    def _timeout(self) -> int:
        return self._config.timeout

    # ── 生命周期 ─────────────────────────────────
    async def on_startup(self, ctx: PluginContext) -> None:
        self._account_id = ctx.account_id
        self._ctx = ctx
        self._config = _load_config(ctx.config)
        self.commands = {self._config.command: self._cmd_handler}
        try:
            me = await ctx.client.get_me()
            self._self_tg_user_id = int(getattr(me, "id", 0) or 0) or None
        except Exception:
            self._self_tg_user_id = None

        await self._log(
            ctx,
            "info",
            f"24 点游戏已启动：触发指令是 {self._config.command}，答题限时 {self._config.timeout} 秒。",
            command=self._config.command,
            timeout=self._config.timeout,
            self_tg_user_id=self._self_tg_user_id,
        )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        for gs in self._games.values():
            if gs.timeout_task and not gs.timeout_task.done():
                gs.timeout_task.cancel()
        self._games.clear()
        self._locks.clear()
        await self._log(ctx, "info", "24 点游戏已停止，进行中的局和超时计时器已清理。")

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

        prize = self._parse_prize(args)
        if prize <= 0:
            await event.edit(
                f"⚠️ 请指定奖金金额，例如：{current_command_prefix()}{self._config.command} 2000"
            )
            return

        chat_id = _event_chat_id(event)
        trigger_msg_id = _event_message_id(event)
        if chat_id is None or trigger_msg_id is None:
            await self._log(
                ctx,
                "error",
                "24 点游戏开局失败：没有拿到聊天 ID 或触发消息 ID，无法追踪这局游戏。",
                chat_id=chat_id,
                trigger_msg_id=trigger_msg_id,
            )
            return

        lock = self._lock_for(chat_id)
        async with lock:
            active = self._games.get(chat_id)
            if active and active.active:
                await event.edit("⚠️ 当前已有进行中的 24 点游戏，请先答完再开新局。")
                return

            numbers = generate_24_puzzle()
            try:
                await event.edit(self._render_start_message(numbers, prize))
            except Exception as exc:
                await self._log(
                    ctx,
                    "error",
                    f"24 点游戏开局失败：题目消息没有编辑成功。原因：{type(exc).__name__}: {exc}",
                    chat_id=chat_id,
                    prize=prize,
                )
                return

            gs = GameState(
                chat_id=chat_id,
                trigger_msg_id=trigger_msg_id,
                numbers=numbers,
                prize=prize,
                timeout=self._config.timeout,
            )
            self._games[chat_id] = gs
            gs.timeout_task = asyncio.create_task(self._game_timeout(ctx, gs))

        await self._log(
            ctx,
            "info",
            f"24 点游戏已开局：聊天 {chat_id}，数字 {numbers}，奖金 {prize}，限时 {gs.timeout} 秒。",
            chat_id=chat_id,
            numbers=numbers,
            prize=prize,
            timeout=gs.timeout,
        )

    # ── incoming：答题 ─────────────────────────
    async def on_message(self, ctx: PluginContext, event: events.NewMessage.Event) -> None:
        msg = await _adapt_incoming_message(event)
        if msg.chat_id is None:
            return
        if self._self_tg_user_id is not None and msg.sender_id == self._self_tg_user_id:
            return
        # 兼容兜底：无法识别 self id 时，仍保留 outgoing 保护，避免误处理自己消息
        if self._self_tg_user_id is None and msg.outgoing and msg.sender_id is not None:
            return

        gs = self._games.get(msg.chat_id)
        if not gs or not gs.active:
            return

        async with self._lock_for(msg.chat_id):
            if not gs.active:
                return
            await self._handle_answer(ctx, event, gs, msg)

    # ── 处理答题 ─────────────────────────────
    async def _handle_answer(
        self,
        ctx: PluginContext,
        event: events.NewMessage.Event,
        gs: GameState,
        msg: IncomingMessage,
    ) -> None:
        if len(msg.text) < 3:
            return

        await self._log(
            ctx,
            "debug",
            f"24 点游戏收到一条答案：聊天 {gs.chat_id}，用户 {msg.sender_name}，内容：{msg.text!r}。",
            chat_id=gs.chat_id,
            sender_id=msg.sender_id,
            sender_name=msg.sender_name,
            answer=msg.text,
        )

        result = check_answer_detailed(msg.text, gs.numbers)
        if not result.ok:
            await self._log(
                ctx,
                "debug",
                f"24 点游戏答案未通过：{msg.sender_name} 的答案没有通过。{result.reason}。",
                chat_id=gs.chat_id,
                sender_id=msg.sender_id,
                answer=msg.text,
                normalized_expr=result.normalized_expr,
                numbers=gs.numbers,
                reason=result.reason,
            )
            return

        await self._log(
            ctx,
            "info",
            f"24 点游戏识别到正确答案：{msg.sender_name} 的 {result.normalized_expr!r} 正好等于 24，准备发放 +{gs.prize}。",
            chat_id=gs.chat_id,
            sender_id=msg.sender_id,
            sender_name=msg.sender_name,
            answer=msg.text,
            normalized_expr=result.normalized_expr,
            numbers=gs.numbers,
            prize=gs.prize,
            winner_msg_id=msg.message_id,
        )

        prize_sent = await self._send_prize_reply(ctx, event, gs, msg)
        self._finish_game(gs, msg)
        await self._announce_winner(ctx, gs, msg, prize_sent)

        await self._log(
            ctx,
            "info" if prize_sent else "warn",
            (
                f"24 点游戏结束：{msg.sender_name} 答对，奖励 +{gs.prize} 已发出。聊天 {gs.chat_id}。"
                if prize_sent
                else f"24 点游戏结束：{msg.sender_name} 答对，但奖励 +{gs.prize} 没能发出。聊天 {gs.chat_id}。"
            ),
            chat_id=gs.chat_id,
            winner_id=gs.winner_id,
            winner_name=msg.sender_name,
            prize=gs.prize,
            prize_sent=prize_sent,
        )

    async def _send_prize_reply(
        self,
        ctx: PluginContext,
        event: events.NewMessage.Event,
        gs: GameState,
        msg: IncomingMessage,
    ) -> bool:
        """发奖：优先回复答题消息，失败再用 client.send_message 兜底。"""

        prize_text = f"+{gs.prize}"

        def _send_fail_hint(exc: Exception) -> str:
            name = type(exc).__name__
            msg = str(exc)
            if "ChatWriteForbidden" in name or "CHAT_WRITE_FORBIDDEN" in msg.upper():
                return "当前账号在该会话无发言权限（常见于频道评论区/被禁言）。"
            if "MessageIdInvalid" in name or "REPLY_MESSAGE_ID_INVALID" in msg.upper():
                return "引用的消息不可回复（消息已删除/不可见/跨会话）。"
            if "SlowModeWait" in name:
                return "会话处于慢速模式，当前发送被限流。"
            if "FloodWait" in name:
                seconds = getattr(exc, "seconds", None)
                return f"触发 FloodWait，需等待 {seconds or '?'} 秒。"
            return "请检查会话权限、发言限制和消息可见性。"
        reply = getattr(event, "reply", None)
        if callable(reply):
            try:
                await reply(prize_text)
                await self._log(
                    ctx,
                    "info",
                    f"24 点游戏奖励已发出：已回复 {msg.sender_name} 的答案消息，内容 {prize_text}。",
                    chat_id=gs.chat_id,
                    sender_id=msg.sender_id,
                    winner_msg_id=msg.message_id,
                    send_method="event.reply",
                )
                return True
            except Exception as exc:
                await self._log(
                    ctx,
                    "warn",
                    (
                        f"24 点游戏用 reply 发奖失败，准备改用 send_message 兜底。"
                        f"原因：{type(exc).__name__}: {exc}。提示：{_send_fail_hint(exc)}"
                    ),
                    chat_id=gs.chat_id,
                    sender_id=msg.sender_id,
                    winner_msg_id=msg.message_id,
                    send_method="event.reply",
                    exc_type=type(exc).__name__,
                    exc_repr=repr(exc),
                    hint=_send_fail_hint(exc),
                )

        if msg.message_id is not None:
            try:
                await ctx.client.send_message(
                    entity=gs.chat_id,
                    message=prize_text,
                    reply_to=msg.message_id,
                )
                await self._log(
                    ctx,
                    "info",
                    f"24 点游戏奖励已发出：已回复答案消息 {msg.message_id}，内容 {prize_text}。",
                    chat_id=gs.chat_id,
                    sender_id=msg.sender_id,
                    winner_msg_id=msg.message_id,
                    send_method="client.send_message.reply_to",
                )
                return True
            except Exception as exc:
                await self._log(
                    ctx,
                    "warn",
                    (
                        "24 点游戏用 reply_to 发奖失败，准备改成普通消息发送。"
                        f"频道/匿名频道消息经常会走到这里。原因：{type(exc).__name__}: {exc}。"
                        f"提示：{_send_fail_hint(exc)}"
                    ),
                    chat_id=gs.chat_id,
                    sender_id=msg.sender_id,
                    winner_msg_id=msg.message_id,
                    send_method="client.send_message.reply_to",
                    exc_type=type(exc).__name__,
                    exc_repr=repr(exc),
                    hint=_send_fail_hint(exc),
                )

        try:
            await ctx.client.send_message(entity=gs.chat_id, message=prize_text)
            await self._log(
                ctx,
                "info",
                f"24 点游戏奖励已发出：已向聊天 {gs.chat_id} 发送普通消息 {prize_text}。没有引用回复，但奖励没有丢。",
                chat_id=gs.chat_id,
                sender_id=msg.sender_id,
                winner_msg_id=msg.message_id,
                send_method="client.send_message.plain",
            )
            return True
        except Exception as exc:
            await self._log(
                ctx,
                "error",
                (
                    f"24 点游戏发奖失败：已经识别 {msg.sender_name} 答对，但奖励消息 {prize_text} 没发出去。"
                    f"原因：{type(exc).__name__}: {exc}。提示：{_send_fail_hint(exc)}"
                ),
                chat_id=gs.chat_id,
                sender_id=msg.sender_id,
                winner_msg_id=msg.message_id,
                prize=gs.prize,
                send_method="client.send_message",
                exc_type=type(exc).__name__,
                exc_repr=repr(exc),
                hint=_send_fail_hint(exc),
            )
            return False

    # ── 超时处理 ─────────────────────────────
    async def _game_timeout(self, ctx: PluginContext, gs: GameState) -> None:
        try:
            await asyncio.sleep(gs.timeout)
            async with self._lock_for(gs.chat_id):
                if not gs.active:
                    return
                gs.active = False
                await self._edit_game_message(
                    ctx,
                    gs,
                    suffix="\n\n⏰ 时间到！无人答对，游戏结束。",
                    error_message="24 点游戏超时结束，但题目消息更新失败。",
                )
        except asyncio.CancelledError:
            return

        await self._log(
            ctx,
            "info",
            f"24 点游戏超时结束：聊天 {gs.chat_id} 在 {gs.timeout} 秒内无人答对。",
            chat_id=gs.chat_id,
            timeout=gs.timeout,
        )

    def _finish_game(self, gs: GameState, msg: IncomingMessage) -> None:
        gs.active = False
        gs.winner_id = msg.sender_id
        gs.winner_name = msg.sender_name
        gs.winner_msg_id = msg.message_id
        if gs.timeout_task and not gs.timeout_task.done():
            gs.timeout_task.cancel()

    async def _announce_winner(
        self,
        ctx: PluginContext,
        gs: GameState,
        msg: IncomingMessage,
        prize_sent: bool,
    ) -> None:
        prize_line = (
            f"💰 奖金 +{gs.prize} 已发放。"
            if prize_sent
            else f"⚠️ 已识别正确答案，但 +{gs.prize} 奖励消息发送失败，请看插件日志。"
        )
        suffix = f"\n\n🏆 恭喜 {msg.sender_name} 答对！\n{prize_line}"
        await self._edit_game_message(
            ctx,
            gs,
            suffix=suffix,
            error_message=f"24 点游戏已判定 {msg.sender_name} 答对，但题目消息更新失败。",
            sender_id=msg.sender_id,
        )

    async def _edit_game_message(
        self,
        ctx: PluginContext,
        gs: GameState,
        *,
        suffix: str,
        error_message: str,
        sender_id: int | None = None,
    ) -> None:
        try:
            msg_obj = await ctx.client.get_messages(gs.chat_id, ids=gs.trigger_msg_id)
            original = getattr(msg_obj, "text", None) or getattr(msg_obj, "raw_text", "") or ""
            await ctx.client.edit_message(
                entity=gs.chat_id,
                message=gs.trigger_msg_id,
                text=f"{original}{suffix}",
            )
        except Exception as exc:
            await self._log(
                ctx,
                "error",
                f"{error_message} 原因：{type(exc).__name__}: {exc}",
                chat_id=gs.chat_id,
                sender_id=sender_id,
            )

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock

    @staticmethod
    def _parse_prize(args: list[str]) -> int:
        for arg in args:
            try:
                return int(arg)
            except ValueError:
                continue
        return 0

    def _render_start_message(self, numbers: list[int], prize: int) -> str:
        nums_disp = " ] [ ".join(str(n) for n in numbers)
        return (
            "🎯 24 点开始\n"
            "━━━━━━━━\n"
            f"🎲 数字：[ {nums_disp} ]\n"
            f"💰 奖金：{prize}\n"
            f"⏳ 限时：{self._config.timeout} 秒\n"
            "🔢 可用符号：+ - x ÷ * / ( )\n"
            "\n"
            "请直接发送算式，结果必须等于 24。\n"
            "示例：(1+2+3)*4、8/(3-8/3)\n"
            "必须恰好使用这 4 个数字各一次，可用 + - x ÷ * / ( )"
        )

    @staticmethod
    async def _log(ctx: PluginContext, level: str, message: str, **detail: Any) -> None:
        if ctx.log:
            await ctx.log(level, message, **detail)
