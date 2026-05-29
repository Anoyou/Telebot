"""账号绑定 Bot 的 polling runtime 与命令处理。"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import random
import re
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import desc, select

from ..account_bot_defaults import (
    DEFAULT_INTERACTION_RESPONSE_TEMPLATE,
    DEFAULT_TRANSFER_NOTICE_TEMPLATE,
)
from ..db.base import AsyncSessionLocal
from ..db.models.account import Account
from ..db.models.account_bot import (
    ACCOUNT_BOT_ROLE_ADMIN,
    ACCOUNT_BOT_ROLE_OPERATOR,
    ACCOUNT_BOT_STATUS_DISABLED,
    ACCOUNT_BOT_STATUS_ERROR,
    ACCOUNT_BOT_STATUS_RUNNING,
    ACCOUNT_BOT_STATUS_STOPPED,
    AccountBot,
)
from ..db.models.command import AccountCommandLink
from ..db.models.feature import AccountFeature, Feature
from ..db.models.log import LEVEL_ERROR, LEVEL_WARN, RuntimeLog
from ..db.models.rule import Rule
from ..db.models.system import SystemSetting
from ..redis_client import get_redis
from ..settings import settings
from ..worker.ipc import (
    CMD_EXECUTE_RULE,
    CMD_RELOAD_CONFIG,
    CMD_RUN_INTERACTION_ENTRY,
    GLOBAL_CHANNEL,
    RUNTIME_LOG_STREAM,
    IPCMessage,
    RuntimeLogPayload,
    cmd_channel,
    make_cmd,
    publish_cmd_with_ack,
)
from . import (
    account_bot_service,
    account_service,
    audit,
    command_service,
    feature_service,
    remote_plugin_service,
)

log = logging.getLogger(__name__)

_TASKS: dict[int, asyncio.Task[None]] = {}
_INTERACTION_TASKS: dict[int, asyncio.Task[None]] = {}
_TRANSFER_TEST_TASKS: dict[int, asyncio.Task[None]] = {}
_TASK_LOCK = asyncio.Lock()
_CONFIRM_PREFIX = "account_bot_confirm:"
_CONFIRM_TTL_SECONDS = 300
_MAX_BUTTON_ROWS = 24
_REMOTE_POLICY_HINT = "该功能默认关闭，请管理员在 Web 控制台启用后重试（高风险操作，仍需二次确认）。"
_RUNTIME_NOTIFY_DEDUPE_TTL_SECONDS = 180
_RUNTIME_NOTIFY_DEDUPE_PREFIX = "account_bot:runtime_notify:"
_MATH_GAME_PREFIX = "account_bot:math_game:"
_MATH_GAME_CLAIM_PREFIX = "account_bot:math_game_claim:"
_MATH_GAME_TTL_SECONDS = 900
_INTERACTION_RULE_STATE_PREFIX = "account_bot:interaction_rule_state:"
_INTERACTION_TRIGGER_DEDUPE_PREFIX = "account_bot:interaction_trigger:"
_INTERACTION_SESSION_PREFIX = "account_bot:interaction_session:"
_INTERACTION_USER_COOLDOWN_PREFIX = "account_bot:interaction_user_cooldown:"
_INTERACTION_USER_DAILY_PREFIX = "account_bot:interaction_user_daily:"
_INTERACTION_USER_PENDING_PREFIX = "account_bot:interaction_user_pending:"
_INTERACTION_ENTRY_TIMEOUT_SECONDS = 60.0
AUTO_PAYOUT_MODULE_KEYS = {"game24", "math10", "dice_grid_hunt"}


async def _load_command_prefix(db) -> str:
    prefix = settings.command_prefix or ","
    row = await db.get(SystemSetting, "command_prefix")
    if row is not None:
        raw = row.value
        if isinstance(raw, dict):
            value = str(raw.get("value", "") or "").strip()
        else:
            value = str(raw or "").strip()
        if value:
            prefix = value
    return prefix


@dataclass(slots=True)
class Incoming:
    account_id: int
    token: str
    update_id: int
    user_id: int | None
    chat_id: int | None
    message_id: int | None
    text: str
    chat_type: str | None = None
    callback_id: str | None = None
    callback_data: str | None = None
    display_name: str | None = None
    username: str | None = None
    reply_to_user_id: int | None = None
    reply_to_display_name: str | None = None
    reply_to_username: str | None = None
    reply_to_text: str | None = None
    entity_languages: tuple[str, ...] = ()


@dataclass
class MathGameState:
    account_id: int
    chat_id: int
    question: str
    answer: int
    prize: int = 123
    active: bool = True
    game_id: str = ""
    created_at: float = 0.0
    source_update_id: int | None = None
    source_message_id: int | None = None
    winner_update_id: int | None = None
    winner_message_id: int | None = None


_MATH_GAMES: dict[tuple[int, int], MathGameState] = {}


@dataclass
class InteractionEvent:
    """交互 Bot 投递给模块的标准事件。"""

    type: str
    account_id: int
    chat_id: int | None
    rule_id: str
    rule_name: str
    module_key: str
    entry_key: str
    update_id: int
    message_id: int | None
    user_id: int | None
    display_name: str | None
    username: str | None
    text: str
    reply_to_user_id: int | None = None
    reply_to_display_name: str | None = None
    reply_to_username: str | None = None
    reply_to_text: str | None = None
    entity_languages: tuple[str, ...] = ()
    data: dict[str, Any] | None = None


def _button(text: str, action: str, resource: str = "_", *, aid: int, nonce: str | None = None) -> dict[str, str]:
    data = f"ab:{aid}:{action}:{resource}"
    if nonce:
        data += f":{nonce}"
    return {"text": text, "callback_data": data[:64]}


def _keyboard(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


def _main_keyboard(aid: int) -> dict[str, Any]:
    return _keyboard(
        [
            [
                _button("状态", "view", "status", aid=aid),
                _button("功能", "view", "features", aid=aid),
                _button("命令", "view", "commands", aid=aid),
            ],
            [
                _button("插件", "view", "plugins", aid=aid),
                _button("规则", "view", "rules", aid=aid),
                _button("日志", "view", "logs", aid=aid),
            ],
            [
                _button("帮助", "view", "help", aid=aid),
            ],
        ]
    )


def _parse_callback(data: str) -> tuple[int, str, str, str | None] | None:
    parts = data.split(":")
    if len(parts) not in {4, 5} or parts[0] != "ab":
        return None
    try:
        aid = int(parts[1])
    except ValueError:
        return None
    return aid, parts[2], parts[3], parts[4] if len(parts) == 5 else None


def _confirm_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _confirm_redis_key(token: str) -> str:
    return _CONFIRM_PREFIX + _confirm_token_hash(token)


async def _consume_confirm_payload(redis: Any, token: str) -> str | None:
    key = _confirm_redis_key(token)
    getdel = getattr(redis, "getdel", None)
    if callable(getdel):
        return await getdel(key)
    return await redis.eval(
        "local v=redis.call('GET',KEYS[1]); if v then redis.call('DEL',KEYS[1]); end; return v;",
        1,
        key,
    )


async def _read_confirm_payload(redis: Any, token: str) -> str | None:
    return await redis.get(_confirm_redis_key(token))


async def start_account_bot_manager() -> None:
    """启动所有 enabled 的账号 Bot polling task。"""

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(AccountBot).where(AccountBot.enabled.is_(True))
            )
        ).scalars().all()
    for row in rows:
        await restart_account_bot(int(row.account_id))
    log.info("account bot manager started: %d management task(s)", len(rows))


async def stop_account_bot_manager() -> None:
    """停止所有账号 Bot polling task。"""

    async with _TASK_LOCK:
        tasks = list(_TASKS.values())
        _TASKS.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def restart_account_bot(aid: int) -> None:
    """重启单个账号 Bot polling task。"""

    async with _TASK_LOCK:
        old = _TASKS.pop(aid, None)
        if old is not None:
            old.cancel()
        should_start = False
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(AccountBot).where(AccountBot.account_id == aid)
                )
            ).scalar_one_or_none()
            should_start = bool(row and row.enabled and row.bot_token_enc)
            if row and not should_start:
                row.status = ACCOUNT_BOT_STATUS_DISABLED if not row.enabled else ACCOUNT_BOT_STATUS_STOPPED
                await db.commit()
        if should_start:
            _TASKS[aid] = asyncio.create_task(_polling_loop(aid), name=f"account-bot:{aid}")
    if old is not None:
        await asyncio.gather(old, return_exceptions=True)


async def sync_account_bot(aid: int) -> None:
    """配置变更后同步运行时。"""

    await restart_account_bot(aid)


async def start_interaction_bot_manager() -> int:
    """启动所有已启用的交互 Bot polling task。"""

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(SystemSetting).where(
                    SystemSetting.key.like(f"{account_bot_service.TRANSFER_NOTICE_SETTING_PREFIX}%")
                )
            )
        ).scalars().all()
    count = 0
    for row in rows:
        try:
            aid = int(str(row.key).removeprefix(account_bot_service.TRANSFER_NOTICE_SETTING_PREFIX))
        except ValueError:
            continue
        cfg = account_bot_service.normalize_transfer_notice_config(row.value)
        if cfg.get("enabled") and (cfg.get("interaction_bot_token_enc") or cfg.get("transfer_bot_token_enc")):
            await restart_interaction_bot(aid)
            count += 1
    return count


async def stop_interaction_bot_manager() -> None:
    """停止所有交互 Bot polling task。"""

    async with _TASK_LOCK:
        tasks = list(_INTERACTION_TASKS.values()) + list(_TRANSFER_TEST_TASKS.values())
        _INTERACTION_TASKS.clear()
        _TRANSFER_TEST_TASKS.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def is_interaction_bot_running(aid: int) -> bool:
    """返回指定账号的交互 Bot polling task 是否仍在运行。"""

    task = _INTERACTION_TASKS.get(int(aid))
    return bool(task and not task.done())


async def restart_interaction_bot(aid: int) -> None:
    """重启单个交互 Bot polling task。"""

    async with _TASK_LOCK:
        old = _INTERACTION_TASKS.pop(aid, None)
        old_transfer = _TRANSFER_TEST_TASKS.pop(aid, None)
        if old is not None:
            old.cancel()
        if old_transfer is not None:
            old_transfer.cancel()
        should_start = False
        should_start_transfer = False
        async with AsyncSessionLocal() as db:
            cfg = await account_bot_service.get_transfer_notice_config(db, aid)
            should_start = bool(cfg.get("enabled") and cfg.get("has_interaction_bot_token"))
            should_start_transfer = bool(cfg.get("enabled") and cfg.get("has_transfer_bot_token"))
            await _set_interaction_runtime_state(db, aid, error=None)
            await _set_transfer_test_runtime_state(db, aid, error=None)
        if should_start:
            _INTERACTION_TASKS[aid] = asyncio.create_task(
                _interaction_polling_loop(aid),
                name=f"interaction-bot:{aid}",
            )
        if should_start_transfer:
            _TRANSFER_TEST_TASKS[aid] = asyncio.create_task(
                _transfer_test_polling_loop(aid),
                name=f"transfer-test-bot:{aid}",
            )
    if old is not None:
        await asyncio.gather(old, return_exceptions=True)
    if old_transfer is not None:
        await asyncio.gather(old_transfer, return_exceptions=True)


async def notify_account(account_id: int, text: str) -> int:
    """给某账号 Bot 的已授权通知用户发送消息。"""

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(AccountBot).where(
                    AccountBot.account_id == account_id,
                    AccountBot.enabled.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is None or not row.bot_token_enc:
            return 0
        token = account_bot_service.decrypt_bot_token(row)
        users = await account_bot_service.list_bot_users(db, account_id)
        targets = [
            u for u in users
            if u.enabled and u.notify_enabled and u.last_chat_id is not None
        ]
    sent = 0
    for user in targets:
        try:
            await account_bot_service.send_message(
                token,
                int(user.last_chat_id),
                text,
                parse_mode="HTML",
            )
            sent += 1
        except Exception:  # noqa: BLE001
            log.debug("account bot notify failed aid=%s tg_user=%s", account_id, user.tg_user_id, exc_info=True)
    return sent


async def notify_runtime_log(row: RuntimeLog) -> None:
    """运行日志落库后触发账号级错误通知。"""

    if row.account_id is None or row.level not in {LEVEL_ERROR, LEVEL_WARN}:
        return
    source = account_bot_service.html_text(row.source or "worker")
    message = account_bot_service.html_text(row.message)
    digest = hashlib.sha256(
        f"{int(row.account_id)}|{row.level}|{row.source or 'worker'}|{row.message}".encode()
    ).hexdigest()
    dedupe_key = f"{_RUNTIME_NOTIFY_DEDUPE_PREFIX}{int(row.account_id)}:{digest}"
    redis = get_redis()
    try:
        first_hit = await redis.set(
            dedupe_key,
            "1",
            ex=_RUNTIME_NOTIFY_DEDUPE_TTL_SECONDS,
            nx=True,
        )
    except Exception:  # noqa: BLE001
        first_hit = True
    if not first_hit:
        return
    await notify_account(
        int(row.account_id),
        f"⚠️ <b>账号运行告警</b>\n来源：<code>{source}</code>\n内容：{message}",
    )


async def _polling_loop(aid: int) -> None:
    backoff = 2.0
    token = ""
    try:
        while True:
            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(
                        select(AccountBot).where(AccountBot.account_id == aid)
                    )
                ).scalar_one_or_none()
                if row is None or not row.enabled or not row.bot_token_enc:
                    if row is not None:
                        row.status = ACCOUNT_BOT_STATUS_DISABLED
                        await db.commit()
                    return
                try:
                    token = account_bot_service.decrypt_bot_token(row)
                except Exception as exc:  # noqa: BLE001
                    row.status = ACCOUNT_BOT_STATUS_ERROR
                    row.last_error = account_bot_service.sanitize_bot_error(exc)
                    await db.commit()
                    return
                offset = (row.last_update_id + 1) if row.last_update_id is not None else None
                if row.status != ACCOUNT_BOT_STATUS_RUNNING:
                    row.status = ACCOUNT_BOT_STATUS_RUNNING
                    row.last_error = None
                    await db.commit()

            try:
                result = await account_bot_service.call_bot_api(
                    token,
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": 25,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                updates = result.get("result") if isinstance(result, dict) else result
                if not isinstance(updates, list):
                    updates = []
                backoff = 2.0
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    try:
                        await _handle_update(aid, token, update)
                    except Exception:  # noqa: BLE001
                        log.exception("account bot update failed aid=%s update_id=%s", aid, update_id)
                    finally:
                        async with AsyncSessionLocal() as db:
                            row = (
                                await db.execute(
                                    select(AccountBot).where(AccountBot.account_id == aid)
                                )
                            ).scalar_one_or_none()
                            if row is not None:
                                if row.last_update_id is None or update_id > row.last_update_id:
                                    row.last_update_id = update_id
                                row.status = ACCOUNT_BOT_STATUS_RUNNING
                                row.last_error = None
                                await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                clean = account_bot_service.sanitize_bot_error(exc, token=token)
                clean = account_bot_service.label_bot_polling_error(clean, role="management")
                async with AsyncSessionLocal() as db:
                    row = (
                        await db.execute(
                            select(AccountBot).where(AccountBot.account_id == aid)
                        )
                    ).scalar_one_or_none()
                    if row is not None:
                        row.status = ACCOUNT_BOT_STATUS_ERROR
                        row.last_error = clean
                        await db.commit()
                log.warning("account bot polling error aid=%s: %s", aid, clean)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
    except asyncio.CancelledError:
        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(AccountBot).where(AccountBot.account_id == aid)
                )
            ).scalar_one_or_none()
            if row is not None and row.enabled:
                row.status = ACCOUNT_BOT_STATUS_STOPPED
                await db.commit()
        raise


async def _load_interaction_runtime_config(aid: int) -> tuple[str | None, dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        cfg = await account_bot_service.get_transfer_notice_config(db, aid)
        token = await account_bot_service.get_interaction_bot_token(db, aid)
    return token, cfg


async def _set_interaction_runtime_state(
    db: Any,
    aid: int,
    *,
    last_update_id: int | None = None,
    error: str | None = None,
) -> None:
    row = await db.get(SystemSetting, account_bot_service.transfer_notice_setting_key(aid))
    if row is None or not isinstance(row.value, dict):
        return
    data = account_bot_service.normalize_transfer_notice_config(row.value)
    if last_update_id is not None:
        data["interaction_last_update_id"] = last_update_id
    data["interaction_last_error"] = error
    row.value = data
    await db.commit()


async def _load_transfer_test_runtime_config(aid: int) -> tuple[str | None, dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        cfg = await account_bot_service.get_transfer_notice_config(db, aid)
        token = await account_bot_service.get_transfer_bot_token(db, aid)
    return token, cfg


async def _set_transfer_test_runtime_state(
    db: Any,
    aid: int,
    *,
    last_update_id: int | None = None,
    error: str | None = None,
) -> None:
    row = await db.get(SystemSetting, account_bot_service.transfer_notice_setting_key(aid))
    if row is None or not isinstance(row.value, dict):
        return
    data = account_bot_service.normalize_transfer_notice_config(row.value)
    if last_update_id is not None:
        data["transfer_last_update_id"] = last_update_id
    data["transfer_last_error"] = error
    row.value = data
    await db.commit()


async def _interaction_polling_loop(aid: int) -> None:
    backoff = 2.0
    token = ""
    try:
        while True:
            token_opt, cfg = await _load_interaction_runtime_config(aid)
            if not cfg.get("enabled") or not token_opt:
                async with AsyncSessionLocal() as db:
                    await _set_interaction_runtime_state(db, aid, error=None)
                return
            token = token_opt
            offset = (int(cfg["interaction_last_update_id"]) + 1) if cfg.get("interaction_last_update_id") is not None else None

            try:
                result = await account_bot_service.call_bot_api(
                    token,
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": 25,
                        "allowed_updates": ["message"],
                    },
                )
                updates = result.get("result") if isinstance(result, dict) else result
                if not isinstance(updates, list):
                    updates = []
                backoff = 2.0
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    try:
                        await _handle_interaction_update(aid, token, update)
                    except Exception:  # noqa: BLE001
                        log.exception("interaction bot update failed aid=%s update_id=%s", aid, update_id)
                    finally:
                        async with AsyncSessionLocal() as db:
                            await _set_interaction_runtime_state(db, aid, last_update_id=update_id, error=None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                clean = account_bot_service.sanitize_bot_error(exc, token=token)
                clean = account_bot_service.label_bot_polling_error(clean, role="interaction")
                async with AsyncSessionLocal() as db:
                    await _set_interaction_runtime_state(db, aid, error=clean)
                log.warning("interaction bot polling error aid=%s: %s", aid, clean)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
    except asyncio.CancelledError:
        async with AsyncSessionLocal() as db:
            await _set_interaction_runtime_state(db, aid, error="已停止")
        raise


async def _transfer_test_polling_loop(aid: int) -> None:
    backoff = 2.0
    token = ""
    try:
        while True:
            token_opt, cfg = await _load_transfer_test_runtime_config(aid)
            if not cfg.get("enabled") or not token_opt:
                async with AsyncSessionLocal() as db:
                    await _set_transfer_test_runtime_state(db, aid, error=None)
                return
            token = token_opt
            offset = (int(cfg["transfer_last_update_id"]) + 1) if cfg.get("transfer_last_update_id") is not None else None

            try:
                result = await account_bot_service.call_bot_api(
                    token,
                    "getUpdates",
                    {
                        "offset": offset,
                        "timeout": 25,
                        "allowed_updates": ["message"],
                    },
                )
                updates = result.get("result") if isinstance(result, dict) else result
                if not isinstance(updates, list):
                    updates = []
                backoff = 2.0
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    try:
                        await _handle_transfer_test_update(aid, token, update)
                    except Exception:  # noqa: BLE001
                        log.exception("transfer test bot update failed aid=%s update_id=%s", aid, update_id)
                    finally:
                        async with AsyncSessionLocal() as db:
                            await _set_transfer_test_runtime_state(db, aid, last_update_id=update_id, error=None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                clean = account_bot_service.sanitize_bot_error(exc, token=token)
                clean = account_bot_service.label_bot_polling_error(clean, role="transfer_test")
                async with AsyncSessionLocal() as db:
                    await _set_transfer_test_runtime_state(db, aid, error=clean)
                log.warning("transfer test bot polling error aid=%s: %s", aid, clean)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
    except asyncio.CancelledError:
        async with AsyncSessionLocal() as db:
            await _set_transfer_test_runtime_state(db, aid, error="已停止")
        raise


async def _handle_update(aid: int, token: str, update: dict[str, Any]) -> None:
    incoming = _extract_incoming(aid, token, update)
    if incoming is None:
        return
    async with AsyncSessionLocal() as db:
        user = None
        if incoming.user_id is not None:
            user = await account_bot_service.find_bot_user(db, aid, incoming.user_id)
        if user is None or not user.enabled:
            if incoming.text.startswith("/start") or incoming.text.startswith("/help"):
                await _send(
                    incoming,
                    "你还没有被授权使用这个账号 Bot。\n"
                    f"请在 GUI 的账号详情 → Bot 联动里添加 Telegram 用户 ID：<code>{incoming.user_id}</code>",
                    reply_markup=None,
                )
            elif incoming.callback_id:
                await account_bot_service.answer_callback(token, incoming.callback_id, text="未授权", show_alert=True)
            return
        if incoming.chat_id is not None:
            user.last_chat_id = incoming.chat_id
        if incoming.display_name and not user.display_name:
            user.display_name = incoming.display_name
        await db.commit()
        role = user.role

    try:
        if incoming.callback_id and incoming.callback_data:
            await _handle_callback(incoming, role)
        else:
            if not _should_route_text_to_account_commands(incoming):
                return
            await _handle_command(incoming, role)
    except PermissionError as exc:
        if incoming.callback_id:
            await account_bot_service.answer_callback(
                incoming.token,
                incoming.callback_id,
                text=str(exc),
                show_alert=True,
            )
        else:
            await _send(incoming, f"权限不足：{account_bot_service.html_text(exc)}")


async def _handle_interaction_update(aid: int, token: str, update: dict[str, Any]) -> None:
    incoming = _extract_incoming(aid, token, update)
    if incoming is None:
        return
    async with AsyncSessionLocal() as db:
        cfg = await account_bot_service.get_transfer_notice_config(db, incoming.account_id)
        if incoming.user_id is not None and _int_or_none(cfg.get("interaction_bot_id")) == incoming.user_id:
            return
        if await _try_handle_transfer_notice(db, incoming):
            return
        if await _try_handle_interaction_rule_command_or_keyword(db, incoming):
            return
        if await _try_handle_interaction_module_message(db, incoming):
            return
        if await _try_handle_math_answer(incoming):
            return


async def _handle_transfer_test_update(aid: int, token: str, update: dict[str, Any]) -> None:
    incoming = _extract_incoming(aid, token, update)
    if incoming is None:
        return
    async with AsyncSessionLocal() as db:
        await _try_handle_transfer_command(db, incoming)


def _parse_transfer_notice(text: str) -> dict[str, Any] | None:
    """解析官方或测试阶段自定义转账通知文案。"""

    if not text.strip():
        return None
    labeled = {
        "payer_name": r"付款人\s*[:：]\s*(.+)",
        "receiver_name": r"收款人\s*[:：]\s*(.+)",
        "amount": r"金额\s*[:：]\s*(\d+)",
    }
    out: dict[str, Any] = {}
    for key, pattern in labeled.items():
        match = re.search(pattern, text)
        if not match:
            break
        value = match.group(1).strip()
        out[key] = int(value) if key == "amount" else value
    if set(out) == {"payer_name", "receiver_name", "amount"}:
        payer_id_match = re.search(r"付款人\s*(?:用户)?(?:ID|id)\s*[:：]\s*(\d+)", text)
        if payer_id_match:
            out["payer_user_id"] = int(payer_id_match.group(1))
        receiver_id_match = re.search(r"收款人\s*(?:ID|id)\s*[:：]\s*(\d+)", text)
        if receiver_id_match:
            out["receiver_user_id"] = int(receiver_id_match.group(1))
        return out

    payer_match = re.search(r"^\s*(.+?)\s*(?:转出|射出|转账)\s*(\d+)\b", text, re.M)
    receiver_match = re.search(r"^\s*(.+?)\s*(?:收到|接收|收款)\s*(\d+)\b", text, re.M)
    if payer_match and receiver_match:
        payer_amount = int(payer_match.group(2))
        receiver_amount = int(receiver_match.group(2))
        if payer_amount != receiver_amount:
            return None
        parsed = {
            "payer_name": payer_match.group(1).strip(),
            "receiver_name": receiver_match.group(1).strip(),
            "amount": payer_amount,
        }
        payer_id_match = re.search(r"付款人\s*(?:用户)?(?:ID|id)\s*[:：]\s*(\d+)", text)
        if payer_id_match:
            parsed["payer_user_id"] = int(payer_id_match.group(1))
        receiver_id_match = re.search(r"收款人\s*(?:ID|id)\s*[:：]\s*(\d+)", text)
        if receiver_id_match:
            parsed["receiver_user_id"] = int(receiver_id_match.group(1))
        return parsed
    return None


def _render_transfer_notice_response(template: str, data: dict[str, Any]) -> str:
    values = {
        "payer_name": account_bot_service.html_text(data.get("payer_name", "")),
        "payer_user_id": account_bot_service.html_text(data.get("payer_user_id", "")),
        "receiver_name": account_bot_service.html_text(data.get("receiver_name", "")),
        "amount": account_bot_service.html_text(data.get("amount", "")),
        "receiver_user_id": account_bot_service.html_text(data.get("receiver_user_id", "")),
    }
    raw_template = str(template or "").strip() or DEFAULT_INTERACTION_RESPONSE_TEMPLATE
    try:
        return raw_template.format_map(_TemplateValues(values))
    except Exception:
        return DEFAULT_INTERACTION_RESPONSE_TEMPLATE.format_map(_TemplateValues(values))


def _parse_transfer_command(text: str) -> int | None:
    match = re.fullmatch(r"\+(\d{1,9})", text.strip())
    if not match:
        return None
    amount = int(match.group(1))
    return amount if amount > 0 else None


class _TemplateValues(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def _compact_rendered_template(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def _render_transfer_bot_notice_with_error(
    template: str,
    payer_name: str,
    receiver_name: str,
    amount: int,
    *,
    payer_user_id: int | None = None,
    receiver_user_id: int | None = None,
    escape_html: bool = True,
) -> tuple[str, Exception | None]:
    render_error: Exception | None = None

    def _value(value: Any) -> str:
        text = str(value)
        return account_bot_service.html_text(text) if escape_html else text

    payer_id_text = "" if payer_user_id is None else str(payer_user_id)
    receiver_id_text = "" if receiver_user_id is None else str(receiver_user_id)
    values = _TemplateValues(
        {
            "payer_name": _value(payer_name),
            "payer_user_id": _value(payer_id_text),
            "payer_user_id_line": f"付款人ID：{_value(payer_id_text)}" if payer_id_text else "",
            "receiver_name": _value(receiver_name),
            "amount": _value(amount),
            "receiver_user_id": _value(receiver_id_text),
            "receiver_user_id_line": f"收款人ID：{_value(receiver_id_text)}" if receiver_id_text else "",
        }
    )
    raw_template = str(template or "").strip() or DEFAULT_TRANSFER_NOTICE_TEMPLATE
    try:
        rendered = raw_template.format_map(values)
    except Exception as exc:
        render_error = exc
        rendered = DEFAULT_TRANSFER_NOTICE_TEMPLATE.format_map(values)
    rendered = _compact_rendered_template(rendered)
    if rendered:
        return rendered[:4000], render_error
    return _compact_rendered_template(DEFAULT_TRANSFER_NOTICE_TEMPLATE.format_map(values))[:4000], render_error


def _render_transfer_bot_notice(
    template: str,
    payer_name: str,
    receiver_name: str,
    amount: int,
    *,
    payer_user_id: int | None = None,
    receiver_user_id: int | None = None,
    escape_html: bool = True,
) -> str:
    rendered, _render_error = _render_transfer_bot_notice_with_error(
        template,
        payer_name,
        receiver_name,
        amount,
        payer_user_id=payer_user_id,
        receiver_user_id=receiver_user_id,
        escape_html=escape_html,
    )
    return rendered


def _interaction_chat_matches(cfg: dict[str, Any], chat_id: int) -> bool:
    raw_chat_ids = cfg.get("chat_ids")
    if isinstance(raw_chat_ids, list) and raw_chat_ids:
        try:
            return int(chat_id) in {int(item) for item in raw_chat_ids}
        except (TypeError, ValueError):
            return False
    return cfg.get("chat_id") is None or int(cfg["chat_id"]) == int(chat_id)


def _interaction_triggers(cfg: dict[str, Any]) -> list[str]:
    raw_triggers = cfg.get("trigger_texts")
    if isinstance(raw_triggers, list):
        out = [str(item).strip() for item in raw_triggers if str(item or "").strip()]
        if out:
            return out
    return [str(cfg.get("trigger_text") or "转账成功")]


def _matches_interaction_trigger(cfg: dict[str, Any], text: str) -> bool:
    return _message_line_equals_any(text, _interaction_triggers(cfg))


def _legacy_interaction_rule(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "legacy",
        "name": "兼容单规则",
        "enabled": True,
        "chat_ids": cfg.get("chat_ids") or ([cfg["chat_id"]] if cfg.get("chat_id") is not None else []),
        "trigger_mode": cfg.get("trigger_mode") or "payment",
        "trigger_texts": _interaction_triggers(cfg),
        "module_start_keywords": cfg.get("module_start_keywords") or [],
        "receiver_user_id": cfg.get("receiver_user_id"),
        "receiver_text": cfg.get("receiver_text"),
        "amount": cfg.get("amount"),
        "amount_match_mode": cfg.get("amount_match_mode") or "eq",
        "action": cfg.get("action") or "notice",
        "math_prize": cfg.get("math_prize") or 123,
        "module_key": cfg.get("module_key"),
        "module_action": cfg.get("module_action"),
        "module_prize": cfg.get("module_prize"),
        "module_start_text": cfg.get("module_start_text"),
        "open_commands": cfg.get("open_commands") or [],
        "close_commands": cfg.get("close_commands") or [],
        "status_commands": cfg.get("status_commands") or [],
        "disabled_message": cfg.get("disabled_message"),
        "valid_seconds": cfg.get("valid_seconds") or 600,
        "concurrency": cfg.get("concurrency") or "chat",
        "response_template": cfg.get("response_template") or account_bot_service.default_transfer_notice_config()["response_template"],
    }


def _interaction_rules(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rules = cfg.get("rules")
    if isinstance(raw_rules, list) and raw_rules:
        return [rule for rule in raw_rules if isinstance(rule, dict) and rule.get("enabled", True)]
    return [_legacy_interaction_rule(cfg)]


def _rule_chat_matches(rule: dict[str, Any], chat_id: int) -> bool:
    chat_ids = rule.get("chat_ids")
    if not isinstance(chat_ids, list) or not chat_ids:
        return True
    try:
        return int(chat_id) in {int(item) for item in chat_ids}
    except (TypeError, ValueError):
        return False


def _rule_triggers(rule: dict[str, Any]) -> list[str]:
    raw_triggers = rule.get("trigger_texts")
    if isinstance(raw_triggers, list):
        out = [str(item).strip() for item in raw_triggers if str(item or "").strip()]
        if out:
            return out
    return ["转账成功"]


def _rule_matches_trigger(rule: dict[str, Any], text: str) -> bool:
    return _message_line_equals_any(text, _rule_triggers(rule))


def _incoming_trigger_texts(incoming: Incoming) -> list[str]:
    texts: list[str] = []
    for value in (incoming.text, incoming.reply_to_text, *incoming.entity_languages):
        text = str(value or "").strip()
        if text and text not in texts:
            texts.append(text)
    return texts


def _incoming_notice_texts(incoming: Incoming) -> list[str]:
    texts: list[str] = []
    for value in (incoming.text, incoming.reply_to_text):
        text = str(value or "").strip()
        if text and text not in texts:
            texts.append(text)
    return texts


def _parse_incoming_transfer_notice(incoming: Incoming) -> dict[str, Any] | None:
    for text in _incoming_notice_texts(incoming):
        parsed = _parse_transfer_notice(text)
        if parsed is not None:
            return parsed
    return None


def _entity_languages(*entity_lists: Any) -> tuple[str, ...]:
    languages: list[str] = []
    for entity_list in entity_lists:
        if not isinstance(entity_list, list):
            continue
        for entity in entity_list:
            if not isinstance(entity, dict):
                continue
            language = str(entity.get("language") or "").strip()
            if not language:
                continue
            candidates = [language]
            if language.startswith("language-"):
                candidates.append(language.removeprefix("language-").strip())
            for candidate in candidates:
                if candidate and candidate not in languages:
                    languages.append(candidate)
    return tuple(languages)


def _incoming_matches_interaction_trigger(cfg: dict[str, Any], incoming: Incoming) -> bool:
    return any(_matches_interaction_trigger(cfg, text) for text in _incoming_trigger_texts(incoming))


def _rule_matches_incoming_trigger(rule: dict[str, Any], incoming: Incoming) -> bool:
    return any(_rule_matches_trigger(rule, text) for text in _incoming_trigger_texts(incoming))


def _rule_amount_matches(rule: dict[str, Any], amount: int) -> bool:
    expected = rule.get("amount")
    if expected is None:
        return True
    if str(rule.get("amount_match_mode") or "eq") == "gte":
        return int(amount) >= int(expected)
    return int(expected) == int(amount)


def _rule_has_paid_threshold(rule: dict[str, Any]) -> bool:
    return rule.get("amount") is not None


def _rule_trigger_mode_allows(rule: dict[str, Any], trigger_type: str) -> bool:
    mode = str(rule.get("trigger_mode") or "payment")
    if mode == "both":
        return True
    if trigger_type == "payment":
        return mode == "payment"
    if trigger_type == "keyword":
        return mode == "keyword"
    return False


def _rule_keyword_list(rule: dict[str, Any], key: str) -> list[str]:
    raw = rule.get(key)
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item or "").strip()]


def _message_equals_any(text: str, candidates: list[str]) -> bool:
    clean = text.strip()
    return bool(clean and any(clean == candidate for candidate in candidates))


def _message_match_keyword_pattern(text: str, candidates: list[str]) -> dict[str, str] | None:
    clean = text.strip()
    if not clean:
        return None
    for candidate in candidates:
        pattern = candidate.strip()
        if not pattern:
            continue
        if clean == pattern:
            return {}
        regex = re.escape(pattern)
        regex = re.sub(
            r"id(?:\\ )*=(?:\\ )*数字",
            lambda _match: r"id\s*=\s*(?P<id>\d+)",
            regex,
        )
        regex = re.sub(
            r"num(?:\\ )*=(?:\\ )*数字",
            lambda _match: r"num\s*=\s*(?P<num>\d+)",
            regex,
        )
        if regex == re.escape(pattern):
            continue
        try:
            match = re.fullmatch(regex, clean, flags=re.IGNORECASE)
        except re.error:
            continue
        if match:
            return {key: value for key, value in match.groupdict().items() if value}
    return None


def _message_line_equals_any(text: str, candidates: list[str]) -> bool:
    units = [text.strip(), *(line.strip() for line in text.splitlines())]
    return any(_message_equals_any(unit, candidates) for unit in units if unit)


def _rule_state_key(account_id: int, rule: dict[str, Any], chat_id: int | None) -> str:
    scope = str(rule.get("concurrency") or "chat")
    if scope == "none":
        scoped = "global"
    elif scope == "user":
        scoped = f"chat:{chat_id or 0}"
    else:
        scoped = str(chat_id or 0)
    return f"{_INTERACTION_RULE_STATE_PREFIX}{int(account_id)}:{rule.get('id') or 'legacy'}:{scoped}"


def _interaction_session_scope(rule: dict[str, Any], chat_id: int | None, user_id: int | None) -> str:
    scope = str(rule.get("concurrency") or "chat")
    if scope == "none":
        return "global"
    if scope == "user":
        return f"{chat_id or 0}:user:{user_id or 0}"
    return str(chat_id or 0)


def _interaction_session_key(account_id: int, rule: dict[str, Any], chat_id: int | None, user_id: int | None = None) -> str:
    scoped = _interaction_session_scope(rule, chat_id, user_id)
    return f"{_INTERACTION_SESSION_PREFIX}{int(account_id)}:{rule.get('id') or 'legacy'}:{scoped}"


def _interaction_session_ttl(rule: dict[str, Any]) -> int:
    try:
        ttl = int(rule.get("valid_seconds") or 600)
    except (TypeError, ValueError):
        ttl = 600
    return min(max(ttl, 30), 86400)


def _duration_seconds(value: Any, default: int = 0) -> int:
    text = str(value or "").strip().lower()
    if not text:
        return default
    match = re.fullmatch(r"(\d+)\s*([smhd]?)", text)
    if not match:
        return default
    amount = int(match.group(1))
    unit = match.group(2) or "s"
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return max(0, min(amount * multiplier, 30 * 86400))


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds >= 86400:
        days, rest = divmod(seconds, 86400)
        hours = rest // 3600
        return f"{days}天{hours}小时" if hours else f"{days}天"
    if seconds >= 3600:
        hours, rest = divmod(seconds, 3600)
        minutes = rest // 60
        return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"
    if seconds >= 60:
        minutes, rest = divmod(seconds, 60)
        return f"{minutes}分钟{rest}秒" if rest else f"{minutes}分钟"
    return f"{seconds}秒"


def _seconds_until_local_midnight(now: float | None = None) -> int:
    ts = time.time() if now is None else now
    current = time.localtime(ts)
    next_midnight = time.mktime(
        (
            current.tm_year,
            current.tm_mon,
            current.tm_mday + 1,
            0,
            0,
            0,
            current.tm_wday,
            current.tm_yday,
            current.tm_isdst,
        )
    )
    return max(60, int(next_midnight - ts))


def _interaction_session_user_id(incoming: Incoming, data: dict[str, Any] | None = None) -> int | None:
    payload = data if isinstance(data, dict) else {}
    return (
        _int_or_none(payload.get("payer_user_id"))
        or _int_or_none(payload.get("sender_user_id"))
        or incoming.user_id
    )


async def _save_interaction_session(
    incoming: Incoming,
    rule: dict[str, Any],
    event_type: str,
    data: dict[str, Any] | None = None,
) -> None:
    module_key = str(rule.get("module_key") or "").strip()
    entry_key = str(rule.get("module_action") or "").strip()
    if not module_key or not entry_key:
        return
    session_user_id = _interaction_session_user_id(incoming, data)
    payload = {
        "account_id": incoming.account_id,
        "chat_id": incoming.chat_id,
        "rule_id": str(rule.get("id") or "legacy"),
        "rule_name": str(rule.get("name") or ""),
        "module_key": module_key,
        "entry_key": entry_key,
        "started_by_user_id": session_user_id,
        "source_user_id": incoming.user_id,
        "started_by_message_id": incoming.message_id,
        "event_type": event_type,
        "created_at": time.time(),
    }
    try:
        redis = get_redis()
        await redis.set(
            _interaction_session_key(incoming.account_id, rule, incoming.chat_id, session_user_id),
            json.dumps(payload, ensure_ascii=False),
            ex=_interaction_session_ttl(rule),
        )
    except Exception:  # noqa: BLE001
        log.debug("save interaction session failed aid=%s rule=%s", incoming.account_id, rule.get("id"), exc_info=True)


async def _load_interaction_session(incoming: Incoming, rule: dict[str, Any]) -> dict[str, Any] | None:
    keys = [_interaction_session_key(incoming.account_id, rule, incoming.chat_id, incoming.user_id)]
    if str(rule.get("concurrency") or "chat") == "user":
        keys.append(_interaction_session_key(incoming.account_id, rule, incoming.chat_id, None))
    try:
        redis = get_redis()
        for key in keys:
            raw = await redis.get(key)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001
        log.debug("load interaction session failed aid=%s rule=%s", incoming.account_id, rule.get("id"), exc_info=True)
    return None


async def _interaction_session_keys_for_rule(account_id: int, rule: dict[str, Any], chat_id: int | None) -> list[str]:
    if str(rule.get("concurrency") or "chat") != "user":
        return [_interaction_session_key(account_id, rule, chat_id)]

    prefix = _interaction_session_key(account_id, rule, chat_id, None).rsplit(":", 1)[0] + ":"
    redis = get_redis()
    keys: list[str] = []
    scan_iter = getattr(redis, "scan_iter", None)
    if callable(scan_iter):
        async for key in scan_iter(match=f"{prefix}*"):
            keys.append(key.decode("utf-8", errors="ignore") if isinstance(key, bytes) else str(key))
        return keys
    keys_fn = getattr(redis, "keys", None)
    if callable(keys_fn):
        raw_keys = await keys_fn(f"{prefix}*")
        return [key.decode("utf-8", errors="ignore") if isinstance(key, bytes) else str(key) for key in raw_keys]
    return []


async def _list_interaction_sessions_for_rule(account_id: int, rule: dict[str, Any], chat_id: int | None) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    try:
        redis = get_redis()
        for key in await _interaction_session_keys_for_rule(account_id, rule, chat_id):
            raw = await redis.get(key)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="ignore")
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"session_key": key}
            if isinstance(data, dict):
                sessions.append(data)
    except Exception:  # noqa: BLE001
        log.debug("list interaction sessions failed aid=%s rule=%s", account_id, rule.get("id"), exc_info=True)
    return sessions


async def _clear_interaction_session(account_id: int, rule: dict[str, Any], chat_id: int | None, user_id: int | None = None) -> bool:
    try:
        redis = get_redis()
        deleted = await redis.delete(_interaction_session_key(account_id, rule, chat_id, user_id))
        return bool(deleted)
    except Exception:  # noqa: BLE001
        log.debug("clear interaction session failed aid=%s rule=%s", account_id, rule.get("id"), exc_info=True)
        return False


async def _clear_interaction_sessions_for_rule(account_id: int, rule: dict[str, Any], chat_id: int | None) -> int:
    deleted = 0
    try:
        redis = get_redis()
        for key in await _interaction_session_keys_for_rule(account_id, rule, chat_id):
            deleted += int(await redis.delete(key))
        return deleted
    except Exception:  # noqa: BLE001
        log.debug("clear interaction user sessions failed aid=%s rule=%s", account_id, rule.get("id"), exc_info=True)
        return deleted


async def _clear_loaded_interaction_session(
    account_id: int,
    rule: dict[str, Any],
    chat_id: int | None,
    session: dict[str, Any] | None,
    *,
    incoming_user_id: int | None = None,
) -> int:
    if str(rule.get("concurrency") or "chat") != "user":
        return 1 if await _clear_interaction_session(account_id, rule, chat_id) else 0

    user_ids: list[int | None] = []
    if isinstance(session, dict):
        user_ids.append(_int_or_none(session.get("started_by_user_id")))
    user_ids.append(incoming_user_id)

    deleted = 0
    seen: set[int | None] = set()
    for user_id in user_ids:
        if user_id in seen:
            continue
        seen.add(user_id)
        if await _clear_interaction_session(account_id, rule, chat_id, user_id):
            deleted += 1
    return deleted


async def _is_interaction_rule_open(account_id: int, rule: dict[str, Any], chat_id: int | None) -> bool:
    try:
        redis = get_redis()
        value = await redis.get(_rule_state_key(account_id, rule, chat_id))
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        return str(value or "open") != "closed"
    except Exception:  # noqa: BLE001
        log.debug("read interaction rule state failed aid=%s rule=%s", account_id, rule.get("id"), exc_info=True)
        return True


async def _set_interaction_rule_open(account_id: int, rule: dict[str, Any], chat_id: int | None, open_: bool) -> None:
    try:
        redis = get_redis()
        await redis.set(_rule_state_key(account_id, rule, chat_id), "open" if open_ else "closed")
    except Exception:  # noqa: BLE001
        log.debug("write interaction rule state failed aid=%s rule=%s", account_id, rule.get("id"), exc_info=True)


def _account_holder_label_from_row(account: Any, account_id: int) -> str:
    username = str(getattr(account, "tg_username", "") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    display_name = str(getattr(account, "display_name", "") or "").strip()
    if display_name:
        return account_bot_service.html_text(display_name)
    tg_user_id = _int_or_none(getattr(account, "tg_user_id", None))
    if tg_user_id is not None:
        return str(tg_user_id)
    return f"账号 #{int(account_id)}"


async def _load_account_holder_label(account_id: int) -> str:
    try:
        async with AsyncSessionLocal() as db:
            account = await db.get(Account, account_id)
        return _account_holder_label_from_row(account, account_id)
    except Exception:  # noqa: BLE001
        log.debug("load account holder label failed aid=%s", account_id, exc_info=True)
        return f"账号 #{int(account_id)}"


async def _resolve_payout_mode(account_id: int, chat_id: int | None) -> str:
    """根据当前聊天是否纳入自动发奖监听范围，决定公告文案。"""

    if chat_id is None:
        return "manual"
    try:
        async with AsyncSessionLocal() as db:
            cfg = await account_bot_service.get_transfer_notice_config(db, account_id)
    except Exception:  # noqa: BLE001
        log.debug("resolve payout mode failed aid=%s chat_id=%s", account_id, chat_id, exc_info=True)
        return "manual"
    if not bool(cfg.get("enabled")):
        return "manual"
    for rule in _interaction_rules(cfg):
        action = str(rule.get("action") or "")
        module_key = str(rule.get("module_key") or "").strip()
        if action not in {"math10", "module"}:
            continue
        if action == "module" and module_key not in AUTO_PAYOUT_MODULE_KEYS:
            continue
        if _rule_chat_matches(rule, int(chat_id)):
            return "auto"
    return "manual"


async def _interaction_paid_threshold_message(db: Any, incoming: Incoming, rule: dict[str, Any]) -> str:
    rule_name = str(rule.get("name") or rule.get("id") or "该规则").strip()
    amount = rule.get("amount")
    amount_text = str(amount) if amount is not None else "门槛金额"
    receiver_filter = await _rule_receiver_filter(db, incoming.account_id, rule)
    if not receiver_filter.get("explicit"):
        receiver = await _load_account_holder_label(incoming.account_id)
    else:
        receiver_texts = receiver_filter.get("texts") if isinstance(receiver_filter.get("texts"), list) else []
        if receiver_texts:
            receiver = account_bot_service.html_text(str(receiver_texts[0]))
        elif receiver_filter.get("user_id") is not None:
            receiver = account_bot_service.html_text(str(receiver_filter["user_id"]))
        else:
            receiver = await _load_account_holder_label(incoming.account_id)
    return (
        f"该{account_bot_service.html_text(rule_name)}是付费娱乐模块，"
        f"请对收款人：{receiver}的任意消息回复+{account_bot_service.html_text(amount_text)}即可参与。"
    )


async def _close_active_interaction_games(incoming: Incoming, target_rule: dict[str, Any] | None = None) -> int:
    account_id = incoming.account_id
    chat_id = incoming.chat_id
    if chat_id is None:
        return 0
    closed = 0
    close_math = target_rule is None or str(target_rule.get("action") or "") == "math10"
    if close_math:
        math_state = await _load_math_game_state(account_id, chat_id)
        if math_state is not None and math_state.active:
            math_state.active = False
            await _save_math_game_state(math_state)
            closed += 1
    try:
        if target_rule is None:
            async with AsyncSessionLocal() as db:
                cfg = await account_bot_service.get_transfer_notice_config(db, account_id)
            rules = _interaction_rules(cfg)
        else:
            rules = [target_rule]
        for rule in rules:
            if not _rule_chat_matches(rule, chat_id or 0):
                continue
            if str(rule.get("action") or "") != "module":
                continue
            module_key = str(rule.get("module_key") or "").strip()
            entry_key = str(rule.get("module_action") or "").strip()
            sessions = await _list_interaction_sessions_for_rule(account_id, rule, chat_id)
            close_targets = sessions or [None]
            for session in close_targets:
                if not module_key or not entry_key:
                    continue
                payload = await _interaction_module_payload_async(
                    incoming,
                    rule,
                    {"session": session} if session is not None else None,
                    event_type="session_close",
                )
                ok, _error, actions = await _run_worker_interaction_entry(
                    incoming,
                    plugin_key=module_key,
                    entry_key=entry_key,
                    payload=payload,
                )
                if ok and actions:
                    await _apply_interaction_actions(incoming, actions)
            closed += await _clear_interaction_sessions_for_rule(account_id, rule, chat_id)
    except Exception:  # noqa: BLE001
        log.debug("close interaction module sessions failed aid=%s chat_id=%s", account_id, chat_id, exc_info=True)
    return closed


def _interaction_dedupe_key(incoming: Incoming, rule: dict[str, Any], kind: str, payload: Any) -> str:
    if incoming.message_id is not None:
        raw = f"{incoming.account_id}:{incoming.chat_id}:{rule.get('id')}:{kind}:msg:{incoming.message_id}"
    else:
        raw = f"{incoming.account_id}:{incoming.chat_id}:{rule.get('id')}:{kind}:{payload!r}:{incoming.update_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{_INTERACTION_TRIGGER_DEDUPE_PREFIX}{digest}"


async def _claim_interaction_trigger(incoming: Incoming, rule: dict[str, Any], kind: str, payload: Any) -> bool:
    try:
        ttl = int(rule.get("valid_seconds") or 600)
    except (TypeError, ValueError):
        ttl = 600
    ttl = min(max(ttl, 30), 86400)
    try:
        redis = get_redis()
        return bool(await redis.set(_interaction_dedupe_key(incoming, rule, kind, payload), "1", ex=ttl, nx=True))
    except Exception:  # noqa: BLE001
        log.debug("claim interaction trigger failed aid=%s rule=%s", incoming.account_id, rule.get("id"), exc_info=True)
        return True


def _interaction_user_usage_identity(incoming: Incoming, data: dict[str, Any] | None = None) -> tuple[str, str] | None:
    payload = data if isinstance(data, dict) else {}
    payer_id = _int_or_none(payload.get("payer_user_id"))
    if payer_id is not None:
        label = str(payload.get("payer_name") or payer_id).strip()
        return f"id:{payer_id}", account_bot_service.html_text(label)
    payer_name = str(payload.get("payer_name") or "").strip()
    if payer_name:
        return f"name:{payer_name.casefold()}", account_bot_service.html_text(payer_name)
    if incoming.user_id is None:
        return None
    return f"id:{incoming.user_id}", _interaction_user_label(incoming)


def _interaction_user_usage_keys(
    incoming: Incoming,
    rule: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> tuple[str, str, str] | None:
    identity = _interaction_user_usage_identity(incoming, data)
    if identity is None:
        return None
    identity_key, _label = identity
    raw = f"{incoming.account_id}:{incoming.chat_id or 0}:{rule.get('id') or 'legacy'}:{identity_key}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    today = time.strftime("%Y%m%d", time.localtime())
    return (
        f"{_INTERACTION_USER_COOLDOWN_PREFIX}{digest}",
        f"{_INTERACTION_USER_DAILY_PREFIX}{today}:{digest}",
        f"{_INTERACTION_USER_PENDING_PREFIX}{digest}",
    )


def _interaction_user_usage_limits(rule: dict[str, Any]) -> tuple[int, int]:
    if str(rule.get("concurrency") or "chat") != "user":
        return 0, 0
    return (
        _duration_seconds(rule.get("user_cooldown_seconds"), 0),
        _int_or_none(rule.get("daily_limit_per_user")) or 0,
    )


def _interaction_user_pending_ttl(cooldown_seconds: int) -> int:
    return min(max(int(cooldown_seconds or 0), 30), 300)


def _interaction_user_label(incoming: Incoming) -> str:
    if incoming.username:
        return account_bot_service.html_text(f"@{incoming.username.strip().lstrip('@')}")
    if incoming.display_name:
        return account_bot_service.html_text(incoming.display_name)
    if incoming.user_id is not None:
        return str(incoming.user_id)
    return "该用户"


def _interaction_rule_feature_label(rule: dict[str, Any]) -> str:
    return account_bot_service.html_text(str(rule.get("name") or "该功能").strip() or "该功能")


async def _interaction_user_usage_block_message(
    incoming: Incoming,
    rule: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> str | None:
    keys = _interaction_user_usage_keys(incoming, rule, data)
    if keys is None:
        return None
    cooldown_seconds, daily_limit = _interaction_user_usage_limits(rule)
    if cooldown_seconds <= 0 and daily_limit <= 0:
        return None
    cooldown_key, daily_key, pending_key = keys
    identity = _interaction_user_usage_identity(incoming, data)
    user = identity[1] if identity is not None else _interaction_user_label(incoming)
    feature = _interaction_rule_feature_label(rule)
    try:
        redis = get_redis()
        raw_count = await redis.get(daily_key)
        if isinstance(raw_count, bytes):
            raw_count = raw_count.decode("utf-8", errors="ignore")
        count = max(0, int(raw_count or 0))
        if daily_limit > 0 and count >= daily_limit:
            return f"{user} 今日已成功{feature} {count}/{daily_limit} 次，当日无法再次使用。"
        if await redis.get(pending_key):
            remaining = _interaction_user_pending_ttl(cooldown_seconds)
            ttl_fn = getattr(redis, "ttl", None)
            if callable(ttl_fn):
                try:
                    ttl_value = int(await ttl_fn(pending_key))
                    if ttl_value > 0:
                        remaining = ttl_value
                except Exception:  # noqa: BLE001
                    remaining = _interaction_user_pending_ttl(cooldown_seconds)
            return f"{user} 正在处理{feature}，请稍后再试（预计 {_format_duration(remaining)}）。"
        if cooldown_seconds > 0 and await redis.get(cooldown_key):
            ttl_fn = getattr(redis, "ttl", None)
            remaining = cooldown_seconds
            if callable(ttl_fn):
                try:
                    ttl_value = int(await ttl_fn(cooldown_key))
                    if ttl_value > 0:
                        remaining = ttl_value
                except Exception:  # noqa: BLE001
                    remaining = cooldown_seconds
            limit_part = f" {count}/{daily_limit} 次，" if daily_limit > 0 else "，"
            return f"{user} 今日已成功{feature}{limit_part}距离下次可用 CD 还剩 {_format_duration(remaining)}。"
    except Exception:  # noqa: BLE001
        log.debug("interaction user usage check failed aid=%s rule=%s", incoming.account_id, rule.get("id"), exc_info=True)
    return None


async def _claim_interaction_user_usage(
    incoming: Incoming,
    rule: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    keys = _interaction_user_usage_keys(incoming, rule, data)
    if keys is None:
        return True, None
    cooldown_seconds, daily_limit = _interaction_user_usage_limits(rule)
    if cooldown_seconds <= 0 and daily_limit <= 0:
        return True, None
    _cooldown_key, _daily_key, pending_key = keys
    try:
        redis = get_redis()
        claimed = await redis.set(
            pending_key,
            "1",
            ex=_interaction_user_pending_ttl(cooldown_seconds),
            nx=True,
        )
        return bool(claimed), pending_key if claimed else None
    except Exception:  # noqa: BLE001
        log.debug("interaction user usage claim failed aid=%s rule=%s", incoming.account_id, rule.get("id"), exc_info=True)
        return True, None


async def _release_interaction_user_usage_claim(pending_key: str | None) -> None:
    if not pending_key:
        return
    try:
        redis = get_redis()
        delete = getattr(redis, "delete", None)
        if callable(delete):
            await delete(pending_key)
    except Exception:  # noqa: BLE001
        log.debug("interaction user usage claim release failed", exc_info=True)


async def _mark_interaction_user_usage(
    incoming: Incoming,
    rule: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> None:
    keys = _interaction_user_usage_keys(incoming, rule, data)
    if keys is None:
        return
    cooldown_seconds, daily_limit = _interaction_user_usage_limits(rule)
    if cooldown_seconds <= 0 and daily_limit <= 0:
        return
    cooldown_key, daily_key, _pending_key = keys
    try:
        redis = get_redis()
        if cooldown_seconds > 0:
            await redis.set(cooldown_key, "1", ex=cooldown_seconds)
        if daily_limit > 0:
            incr = getattr(redis, "incr", None)
            expire = getattr(redis, "expire", None)
            if callable(incr):
                count = int(await incr(daily_key))
                if count == 1 and callable(expire):
                    await expire(daily_key, _seconds_until_local_midnight())
            else:
                raw_count = await redis.get(daily_key)
                if isinstance(raw_count, bytes):
                    raw_count = raw_count.decode("utf-8", errors="ignore")
                count = max(0, int(raw_count or 0)) + 1
                await redis.set(daily_key, str(count), ex=_seconds_until_local_midnight())
    except Exception:  # noqa: BLE001
        log.debug("interaction user usage mark failed aid=%s rule=%s", incoming.account_id, rule.get("id"), exc_info=True)


def _receiver_name_matches(receiver_filter: str | None, receiver_name: str) -> bool:
    if not receiver_filter:
        return True
    expected = str(receiver_filter or "").strip().lstrip("@").casefold()
    actual = str(receiver_name or "").strip().lstrip("@").casefold()
    return bool(expected and actual and expected == actual)


def _user_identity_texts(*values: Any) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        candidates = [text]
        username_match = re.search(r"@([A-Za-z0-9_]{3,})", text)
        if username_match:
            candidates.append(username_match.group(1))
            candidates.append("@" + username_match.group(1))
        for item in candidates:
            normalized = item.strip()
            if normalized and normalized not in out:
                out.append(normalized)
    return out


async def _rule_receiver_filter(db: Any, account_id: int, rule: dict[str, Any]) -> dict[str, Any]:
    explicit_text = str(rule.get("receiver_text") or "").strip()
    try:
        explicit_user_id = int(rule["receiver_user_id"]) if rule.get("receiver_user_id") not in (None, "") else None
    except (TypeError, ValueError):
        explicit_user_id = None
    if explicit_text or explicit_user_id is not None:
        return {"user_id": explicit_user_id, "texts": _user_identity_texts(explicit_text), "explicit": True}
    get_account = getattr(db, "get", None)
    account = await get_account(Account, account_id) if callable(get_account) else None
    account_user_id = _int_or_none(getattr(account, "tg_user_id", None)) if account is not None else None
    username = str(getattr(account, "tg_username", "") or "").strip()
    display_name = str(getattr(account, "display_name", "") or "").strip()
    return {"user_id": account_user_id, "texts": _user_identity_texts(username, f"@{username}" if username else "", display_name), "explicit": False}


def _receiver_matches_filter(receiver_filter: dict[str, Any], *, user_id: int | None, name: str | None, username: str | None = None) -> bool:
    expected_user_id = receiver_filter.get("user_id")
    texts = receiver_filter.get("texts") if isinstance(receiver_filter.get("texts"), list) else []
    if expected_user_id is not None:
        if user_id is not None:
            return int(user_id) == int(expected_user_id)
        if not texts:
            return False
    if not texts:
        return False
    actuals = _user_identity_texts(name, username, f"@{username}" if username else "")
    return any(_receiver_name_matches(expected, actual) for expected in texts for actual in actuals)


def _trusted_transfer_notice_sender_matches(cfg: dict[str, Any], sender_id: int | None) -> bool:
    if sender_id is None:
        return False
    trusted_ids = {
        int(value)
        for value in (cfg.get("trusted_bot_id"), cfg.get("transfer_bot_id"))
        if value not in (None, "")
    }
    if not trusted_ids:
        return False
    return int(sender_id) in trusted_ids


async def _is_account_user_sender(db: Any, account_id: int, user_id: int) -> bool:
    get_account = getattr(db, "get", None)
    if not callable(get_account):
        return False
    account = await get_account(Account, account_id)
    account_tg_user_id = getattr(account, "tg_user_id", None) if account is not None else None
    return account_tg_user_id is not None and int(account_tg_user_id) == int(user_id)


async def _select_transfer_command_receiver(
    db: Any,
    incoming: Incoming,
    cfg: dict[str, Any],
    amount: int,
) -> dict[str, Any] | None:
    if incoming.reply_to_display_name:
        return {
            "receiver_name": incoming.reply_to_display_name,
            "receiver_user_id": incoming.reply_to_user_id,
            "receiver_username": incoming.reply_to_username,
        }
    for rule in _interaction_rules(cfg):
        if not _rule_chat_matches(rule, incoming.chat_id or 0):
            continue
        if not _rule_trigger_mode_allows(rule, "payment"):
            continue
        receiver_filter = await _rule_receiver_filter(db, incoming.account_id, rule)
        if not receiver_filter.get("explicit"):
            continue
        receiver_name = (receiver_filter.get("texts") or [None])[0]
        receiver_user_id = _int_or_none(receiver_filter.get("user_id"))
        receiver_username = None
        if not receiver_name:
            if receiver_user_id is None:
                continue
            receiver_name = str(receiver_user_id)
        return {
            "receiver_name": receiver_name,
            "receiver_user_id": receiver_user_id,
            "receiver_username": receiver_username,
        }
    return None


def _transfer_command_chat_is_monitored(incoming: Incoming, cfg: dict[str, Any]) -> bool:
    if incoming.chat_id is None:
        return False
    if _interaction_chat_matches(cfg, incoming.chat_id):
        return True
    for rule in _interaction_rules(cfg):
        if not _rule_chat_matches(rule, incoming.chat_id or 0):
            continue
        return True
    return False


def _is_configured_bot_user_id(cfg: dict[str, Any], user_id: int | None) -> bool:
    if user_id is None:
        return False
    bot_ids = {
        int(value)
        for value in (cfg.get("interaction_bot_id"), cfg.get("transfer_bot_id"), cfg.get("trusted_bot_id"))
        if value not in (None, "")
    }
    return int(user_id) in bot_ids


async def _select_transfer_notice_rule(
    db: Any,
    incoming: Incoming,
    cfg: dict[str, Any],
    parsed: dict[str, Any],
) -> dict[str, Any] | None:
    parsed_amount = int(parsed.get("amount") or 0)
    parsed_receiver = str(parsed.get("receiver_name") or "")
    parsed_receiver_id = _int_or_none(parsed.get("receiver_user_id"))
    for rule in _interaction_rules(cfg):
        if not _rule_trigger_mode_allows(rule, "payment"):
            continue
        if not _rule_chat_matches(rule, incoming.chat_id or 0):
            continue
        if not _rule_matches_incoming_trigger(rule, incoming):
            continue
        if not _rule_amount_matches(rule, parsed_amount):
            continue
        if not await _is_interaction_rule_open(incoming.account_id, rule, incoming.chat_id):
            continue
        receiver_filter = await _rule_receiver_filter(db, incoming.account_id, rule)
        if not _receiver_matches_filter(receiver_filter, user_id=parsed_receiver_id, name=parsed_receiver):
            continue
        return rule
    return None


async def _execute_interaction_rule(
    incoming: Incoming,
    rule: dict[str, Any],
    parsed: dict[str, Any] | None = None,
    *,
    event_type: str = "payment_confirmed",
) -> bool:
    if rule.get("action") == "math10":
        log.warning(
            "deprecated interaction rule action=math10 used aid=%s chat_id=%s rule=%s; use action=module module_key=math10 instead",
            incoming.account_id,
            incoming.chat_id,
            rule.get("id"),
        )
        await _start_math_game(incoming, prize=int(rule.get("math_prize") or 123))
        return True
    if rule.get("action") == "module":
        ok, keep_session = await _run_interaction_module(
            incoming,
            rule,
            parsed=parsed,
            event_type=event_type,
        )
        if ok and keep_session:
            await _save_interaction_session(incoming, rule, event_type, parsed)
        return ok
    text = _render_transfer_notice_response(str(rule.get("response_template") or ""), parsed or {})
    await _send(incoming, text)
    return True


async def _try_handle_interaction_rule_command_or_keyword(db: Any, incoming: Incoming) -> bool:
    if incoming.callback_id or incoming.chat_id is None:
        return False
    cfg = await account_bot_service.get_transfer_notice_config(db, incoming.account_id)
    if not cfg.get("enabled"):
        return False
    for rule in _interaction_rules(cfg):
        if not _rule_chat_matches(rule, incoming.chat_id):
            continue
        if _message_equals_any(incoming.text, _rule_keyword_list(rule, "open_commands")):
            if incoming.user_id is None or not await _is_account_user_sender(db, incoming.account_id, incoming.user_id):
                log.info(
                    "interaction rule open command ignored: sender is not account owner aid=%s chat_id=%s sender_id=%s rule=%s",
                    incoming.account_id,
                    incoming.chat_id,
                    incoming.user_id,
                    rule.get("id"),
                )
                return True
            await _set_interaction_rule_open(incoming.account_id, rule, incoming.chat_id, True)
            await _send(incoming, f"规则「{account_bot_service.html_text(str(rule.get('name') or rule.get('id') or '未命名'))}」已开启。")
            return True
        if _message_equals_any(incoming.text, _rule_keyword_list(rule, "close_commands")):
            if incoming.user_id is None or not await _is_account_user_sender(db, incoming.account_id, incoming.user_id):
                log.info(
                    "interaction rule close command ignored: sender is not account owner aid=%s chat_id=%s sender_id=%s rule=%s",
                    incoming.account_id,
                    incoming.chat_id,
                    incoming.user_id,
                    rule.get("id"),
                )
                return True
            await _set_interaction_rule_open(incoming.account_id, rule, incoming.chat_id, False)
            closed = await _close_active_interaction_games(incoming, rule)
            suffix = f"已结束 {closed} 个进行中的游戏。" if closed else "当前没有进行中的游戏。"
            await _send(incoming, f"规则「{account_bot_service.html_text(str(rule.get('name') or rule.get('id') or '未命名'))}」已关闭，{suffix}")
            return True
        if _message_equals_any(incoming.text, _rule_keyword_list(rule, "status_commands")):
            open_ = await _is_interaction_rule_open(incoming.account_id, rule, incoming.chat_id)
            status = "开启中" if open_ else "已关闭"
            await _send(incoming, f"规则「{account_bot_service.html_text(str(rule.get('name') or rule.get('id') or '未命名'))}」当前状态：{status}。")
            return True
        if not _rule_trigger_mode_allows(rule, "keyword"):
            continue
        keyword_payload = _message_match_keyword_pattern(
            incoming.text,
            _rule_keyword_list(rule, "module_start_keywords"),
        )
        if keyword_payload is None:
            continue
        if not await _is_interaction_rule_open(incoming.account_id, rule, incoming.chat_id):
            message = str(rule.get("disabled_message") or "").strip()
            if message:
                await _send(incoming, message)
            return True
        if _rule_has_paid_threshold(rule):
            await _send(
                incoming,
                await _interaction_paid_threshold_message(db, incoming, rule),
                reply_to_message_id=incoming.message_id,
            )
            return True
        usage_block = await _interaction_user_usage_block_message(incoming, rule)
        if usage_block:
            await _send(incoming, usage_block, reply_to_message_id=incoming.message_id)
            return True
        claimed_usage, usage_pending_key = await _claim_interaction_user_usage(incoming, rule)
        if not claimed_usage:
            usage_block = await _interaction_user_usage_block_message(incoming, rule)
            await _send(
                incoming,
                usage_block or "该用户正在处理该功能，请稍后再试。",
                reply_to_message_id=incoming.message_id,
            )
            return True
        if not await _claim_interaction_trigger(incoming, rule, "keyword", incoming.text):
            await _release_interaction_user_usage_claim(usage_pending_key)
            return True
        try:
            executed = await _execute_interaction_rule(
                incoming,
                rule,
                parsed=keyword_payload or None,
                event_type="keyword",
            )
            if executed:
                await _mark_interaction_user_usage(incoming, rule)
        finally:
            await _release_interaction_user_usage_claim(usage_pending_key)
        return True
    return False


async def _try_handle_interaction_module_message(db: Any, incoming: Incoming) -> bool:
    if incoming.callback_id or incoming.chat_id is None or not incoming.text.strip():
        return False
    if incoming.text.startswith("/") or incoming.text.startswith(","):
        return False
    cfg = await account_bot_service.get_transfer_notice_config(db, incoming.account_id)
    if not cfg.get("enabled"):
        return False
    for rule in _interaction_rules(cfg):
        if not _rule_chat_matches(rule, incoming.chat_id):
            continue
        if not await _is_interaction_rule_open(incoming.account_id, rule, incoming.chat_id):
            continue
        if str(rule.get("action") or "") != "module":
            continue
        module_key = str(rule.get("module_key") or "").strip()
        entry_key = str(rule.get("module_action") or "").strip()
        if not module_key or not entry_key:
            continue
        session = await _load_interaction_session(incoming, rule)
        if session is None:
            continue
        if _message_equals_any(incoming.text, _rule_keyword_list(rule, "open_commands")):
            continue
        if _message_equals_any(incoming.text, _rule_keyword_list(rule, "close_commands")):
            continue
        if _message_equals_any(incoming.text, _rule_keyword_list(rule, "status_commands")):
            continue
        if _message_equals_any(incoming.text, _rule_keyword_list(rule, "module_start_keywords")):
            continue
        payload = await _interaction_module_payload_async(
            incoming,
            rule,
            {
                "message_text": incoming.text,
                "sender_user_id": incoming.user_id,
                "sender_name": incoming.display_name,
                "sender_username": incoming.username,
                "message_id": incoming.message_id,
                "session": session,
            },
            event_type="message",
        )
        ok, error, actions = await _run_worker_interaction_entry(
            incoming,
            plugin_key=module_key,
            entry_key=entry_key,
            payload=payload,
        )
        if not ok:
            log.info(
                "interaction module message ignored aid=%s plugin=%s entry=%s error=%s",
                incoming.account_id,
                module_key,
                entry_key,
                error,
            )
            continue
        if not actions:
            continue
        await _apply_interaction_actions(incoming, actions)
        if _interaction_actions_request_no_session(actions):
            await _clear_loaded_interaction_session(
                incoming.account_id,
                rule,
                incoming.chat_id,
                session,
                incoming_user_id=incoming.user_id,
            )
        return True
    return False


def _new_math_question() -> tuple[str, int]:
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    op = random.choice(["+", "-", "x"])
    if op == "+":
        return f"{a} + {b}", a + b
    if op == "-":
        high, low = max(a, b), min(a, b)
        return f"{high} - {low}", high - low
    return f"{a} x {b}", a * b


def _math_game_key(account_id: int, chat_id: int) -> str:
    return f"{_MATH_GAME_PREFIX}{int(account_id)}:{int(chat_id)}"


def _math_game_claim_key(state: MathGameState) -> str:
    return f"{_MATH_GAME_CLAIM_PREFIX}{state.account_id}:{state.chat_id}:{state.game_id}"


def _math_state_from_payload(payload: Any) -> MathGameState | None:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="ignore")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    try:
        return MathGameState(
            account_id=int(payload["account_id"]),
            chat_id=int(payload["chat_id"]),
            question=str(payload["question"]),
            answer=int(payload["answer"]),
            prize=int(payload.get("prize") or 123),
            active=bool(payload.get("active", True)),
            game_id=str(payload.get("game_id") or ""),
            created_at=float(payload.get("created_at") or 0.0),
            source_update_id=_int_or_none(payload.get("source_update_id")),
            source_message_id=_int_or_none(payload.get("source_message_id")),
            winner_update_id=_int_or_none(payload.get("winner_update_id")),
            winner_message_id=_int_or_none(payload.get("winner_message_id")),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _save_math_game_state(state: MathGameState) -> None:
    _MATH_GAMES[(state.account_id, state.chat_id)] = state
    try:
        redis = get_redis()
        await redis.set(
            _math_game_key(state.account_id, state.chat_id),
            json.dumps(asdict(state), ensure_ascii=False),
            ex=_MATH_GAME_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        log.debug("save math game state failed aid=%s chat_id=%s", state.account_id, state.chat_id, exc_info=True)


async def _load_math_game_state(account_id: int, chat_id: int) -> MathGameState | None:
    try:
        redis = get_redis()
        state = _math_state_from_payload(await redis.get(_math_game_key(account_id, chat_id)))
        if state is not None:
            _MATH_GAMES[(state.account_id, state.chat_id)] = state
            return state
    except Exception:  # noqa: BLE001
        log.debug("load math game state failed aid=%s chat_id=%s", account_id, chat_id, exc_info=True)
    return _MATH_GAMES.get((account_id, chat_id))


async def _claim_math_winner(state: MathGameState, incoming: Incoming) -> bool:
    try:
        redis = get_redis()
        acquired = await redis.set(
            _math_game_claim_key(state),
            str(incoming.message_id or incoming.update_id),
            ex=_MATH_GAME_TTL_SECONDS,
            nx=True,
        )
        if not acquired:
            return False
    except Exception:  # noqa: BLE001
        cached = _MATH_GAMES.get((state.account_id, state.chat_id))
        if cached is not state and (cached is None or not cached.active):
            return False
        log.debug("claim math winner fell back to memory aid=%s chat_id=%s", state.account_id, state.chat_id, exc_info=True)

    state.active = False
    state.winner_update_id = incoming.update_id
    state.winner_message_id = incoming.message_id
    await _save_math_game_state(state)
    return True


async def _start_math_game(incoming: Incoming, *, prize: int = 123) -> None:
    if incoming.chat_id is None:
        return
    question, answer = _new_math_question()
    account_holder = await _load_account_holder_label(incoming.account_id)
    state = MathGameState(
        account_id=incoming.account_id,
        chat_id=incoming.chat_id,
        question=question,
        answer=answer,
        prize=prize,
        game_id=secrets.token_hex(8),
        created_at=time.time(),
        source_update_id=incoming.update_id,
        source_message_id=incoming.message_id,
    )
    await _save_math_game_state(state)
    await _send(
        incoming,
        (
            "算数题测试开始\n"
            f"题目：{question} = ?\n"
            f"奖金：{prize}\n"
            f"直接发送答案，答对后我会公告赢家；奖金由 {account_holder} 人工发放。"
        ),
    )


async def _try_handle_math_answer(incoming: Incoming) -> bool:
    if incoming.chat_id is None or incoming.callback_id:
        return False
    state = await _load_math_game_state(incoming.account_id, incoming.chat_id)
    if state is None or not state.active:
        return False
    try:
        answer = int(incoming.text.strip())
    except ValueError:
        return False
    if answer != state.answer:
        return False
    if not await _claim_math_winner(state, incoming):
        return True
    winner = account_bot_service.html_text(incoming.display_name or str(incoming.user_id or "未知用户"))
    account_holder = await _load_account_holder_label(incoming.account_id)
    payout_mode = await _resolve_payout_mode(incoming.account_id, incoming.chat_id)
    payout_line = (
        f"奖金将由 {account_holder} 账号自动发放。"
        if payout_mode == "auto"
        else f"请由 {account_holder} 人工回复赢家发放奖金。"
    )
    await _send(
        incoming,
        (
            f"答对了：{winner}\n"
            f"题目：{state.question} = {state.answer}\n"
            f"奖金：{state.prize}\n"
            f"{payout_line}"
        ),
        reply_to_message_id=incoming.message_id,
    )
    return True


def _interaction_event_payload(
    incoming: Incoming,
    rule: dict[str, Any],
    event_type: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = InteractionEvent(
        type=event_type,
        account_id=incoming.account_id,
        chat_id=incoming.chat_id,
        rule_id=str(rule.get("id") or ""),
        rule_name=str(rule.get("name") or ""),
        module_key=str(rule.get("module_key") or ""),
        entry_key=str(rule.get("module_action") or ""),
        update_id=incoming.update_id,
        message_id=incoming.message_id,
        user_id=incoming.user_id,
        display_name=incoming.display_name,
        username=incoming.username,
        text=incoming.text,
        reply_to_user_id=incoming.reply_to_user_id,
        reply_to_display_name=incoming.reply_to_display_name,
        reply_to_username=incoming.reply_to_username,
        reply_to_text=incoming.reply_to_text,
        entity_languages=incoming.entity_languages,
        data=dict(data or {}),
    )
    return asdict(event)


def _interaction_module_payload(
    incoming: Incoming,
    rule: dict[str, Any],
    parsed: dict[str, Any] | None,
    *,
    event_type: str = "payment_confirmed",
) -> dict[str, Any]:
    data = dict(rule.get("module_config") or {}) if isinstance(rule.get("module_config"), dict) else {}
    data.update(dict(parsed or {}))
    prize = int(rule.get("module_prize") or rule.get("math_prize") or 123)
    payer_user_id = _int_or_none(data.get("payer_user_id")) or incoming.user_id
    payer_name = str(data.get("payer_name") or incoming.display_name or "")
    data.update(
        {
            "event": _interaction_event_payload(incoming, rule, event_type, parsed),
            "event_type": event_type,
            "account_id": incoming.account_id,
            "chat_id": incoming.chat_id,
            "rule_id": str(rule.get("id") or ""),
            "rule_name": str(rule.get("name") or ""),
            "entry_key": str(rule.get("module_action") or ""),
            "module_config": dict(rule.get("module_config") or {}) if isinstance(rule.get("module_config"), dict) else {},
            "valid_seconds": _interaction_session_ttl(rule),
            "payer_user_id": payer_user_id,
            "payer_name": payer_name,
            "source_update_id": incoming.update_id,
            "source_message_id": incoming.message_id,
            "message_text": incoming.text,
            "sender_user_id": incoming.user_id,
            "sender_name": incoming.display_name,
            "sender_username": incoming.username,
            "message_id": incoming.message_id,
            "reply_to_text": incoming.reply_to_text,
            "entity_languages": list(incoming.entity_languages),
            "prize": prize,
        }
    )
    return data


async def _interaction_module_payload_async(
    incoming: Incoming,
    rule: dict[str, Any],
    parsed: dict[str, Any] | None,
    *,
    event_type: str = "payment_confirmed",
) -> dict[str, Any]:
    data = _interaction_module_payload(incoming, rule, parsed, event_type=event_type)
    data["payout_account_label"] = await _load_account_holder_label(incoming.account_id)
    data["payout_mode"] = await _resolve_payout_mode(incoming.account_id, incoming.chat_id)
    return data


async def _run_worker_interaction_entry(
    incoming: Incoming,
    *,
    plugin_key: str,
    entry_key: str,
    payload: dict[str, Any],
) -> tuple[bool, str | None, list[dict[str, Any]]]:
    reply_channel = f"account_bot:interaction_entry:{incoming.account_id}:{secrets.token_hex(8)}"
    redis = get_redis()
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(reply_channel)
        await redis.publish(
            cmd_channel(incoming.account_id),
            make_cmd(
                CMD_RUN_INTERACTION_ENTRY,
                plugin_key=plugin_key,
                entry_key=entry_key,
                payload=payload,
                reply_to=reply_channel,
            ),
        )
        deadline = time.time() + _INTERACTION_ENTRY_TIMEOUT_SECONDS
        while time.time() < deadline:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
            if not msg:
                continue
            response = IPCMessage.decode(msg["data"]).payload
            actions = response.get("actions") if isinstance(response.get("actions"), list) else []
            return bool(response.get("ok")), response.get("error"), [item for item in actions if isinstance(item, dict)]
    except Exception as exc:  # noqa: BLE001
        log.warning("interaction module ipc failed aid=%s plugin=%s entry=%s error=%s", incoming.account_id, plugin_key, entry_key, exc)
        return False, f"{type(exc).__name__}: {exc}", []
    finally:
        try:
            await pubsub.unsubscribe(reply_channel)
        finally:
            close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if close is not None:
                ret = close()
                if hasattr(ret, "__await__"):
                    await ret
    return False, "worker 调用超时", []


async def _write_interaction_runtime_log(
    incoming: Incoming,
    level: str,
    message: str,
    **detail: Any,
) -> None:
    try:
        payload = RuntimeLogPayload(
            account_id=incoming.account_id,
            level=level,  # type: ignore[arg-type]
            source="event",
            message=message,
            detail=detail or None,
        )
        await get_redis().rpush(RUNTIME_LOG_STREAM, payload.encode())
    except Exception:  # noqa: BLE001
        log.debug("write interaction runtime log failed aid=%s", incoming.account_id, exc_info=True)


_INTERACTION_CONTROL_ACTIONS = {"end_session", "close_session", "no_session"}


def _interaction_actions_request_no_session(actions: list[dict[str, Any]]) -> bool:
    return any(str(action.get("type") or "").strip() in _INTERACTION_CONTROL_ACTIONS for action in actions)


def _interaction_actions_mark_success(actions: list[dict[str, Any]]) -> bool:
    markers = [
        action.get("success")
        for action in actions
        if str(action.get("type") or "").strip() == "result"
    ]
    if markers:
        return any(bool(marker) for marker in markers)
    return True


async def _apply_interaction_actions(incoming: Incoming, actions: list[dict[str, Any]]) -> None:
    for action in actions[:10]:
        action_type = str(action.get("type") or "").strip()
        if action_type in _INTERACTION_CONTROL_ACTIONS or action_type == "result":
            continue
        raw_reply_to = action.get("reply_to_message_id")
        reply_to_message_id = _int_or_none(raw_reply_to)
        if action_type == "send_message":
            text = str(action.get("text") or "").strip()
            if not text:
                continue
            await _send(incoming, text, reply_to_message_id=reply_to_message_id)
            continue
        if action_type in {"send_photo", "send_file"}:
            raw_photo = str(action.get("photo_base64") or action.get("file_base64") or "").strip()
            if not raw_photo:
                continue
            try:
                photo = base64.b64decode(raw_photo, validate=True)
            except (binascii.Error, ValueError):
                log.info("interaction action ignored: invalid base64 media aid=%s", incoming.account_id)
                continue
            if not photo:
                continue
            filename = str(action.get("filename") or "interaction.png").strip() or "interaction.png"
            caption = str(action.get("caption") or action.get("text") or "").strip() or None
            await account_bot_service.send_photo_bytes(
                incoming.token,
                incoming.chat_id or 0,
                photo,
                filename=filename,
                caption=caption,
                reply_to_message_id=reply_to_message_id,
            )
            continue
        log.info("interaction action ignored: unsupported type=%s aid=%s", action_type, incoming.account_id)
        await _write_interaction_runtime_log(
            incoming,
            "info",
            f"interaction action ignored: unsupported type={action_type}",
            action_type=action_type,
            action=action,
        )


async def _run_interaction_module(
    incoming: Incoming,
    rule: dict[str, Any],
    *,
    parsed: dict[str, Any] | None = None,
    event_type: str = "payment_confirmed",
) -> tuple[bool, bool]:
    module_key = str(rule.get("module_key") or "").strip()
    entry_key = str(rule.get("module_action") or "").strip()
    if not module_key or not entry_key:
        await _send(incoming, "模块启动失败：请先选择模块和交互入口。")
        return False, False
    start_text = str(rule.get("module_start_text") or "").strip()
    if start_text:
        await _send(incoming, start_text, reply_to_message_id=incoming.message_id)
    payload = await _interaction_module_payload_async(incoming, rule, parsed, event_type=event_type)
    ok, error, actions = await _run_worker_interaction_entry(
        incoming,
        plugin_key=module_key,
        entry_key=entry_key,
        payload=payload,
    )
    if not ok:
        await _send(
            incoming,
            f"模块启动失败：{account_bot_service.html_text(error or f'{module_key}.{entry_key} 不可用')}",
        )
        return False, False
    keep_session = not _interaction_actions_request_no_session(actions)
    await _apply_interaction_actions(incoming, actions)
    return _interaction_actions_mark_success(actions), keep_session


async def _start_interaction_module(
    incoming: Incoming,
    rule: dict[str, Any],
    *,
    parsed: dict[str, Any] | None = None,
    event_type: str = "payment_confirmed",
) -> bool:
    _ok, keep_session = await _run_interaction_module(
        incoming,
        rule,
        parsed=parsed,
        event_type=event_type,
    )
    return keep_session


def _is_private_chat(incoming: Incoming) -> bool:
    return incoming.chat_type == "private" or (
        incoming.chat_id is not None and incoming.user_id is not None and incoming.chat_id == incoming.user_id
    )


def _should_route_text_to_account_commands(incoming: Incoming) -> bool:
    if _is_private_chat(incoming):
        return True
    return incoming.text.startswith("/")


async def _try_handle_transfer_command(db: Any, incoming: Incoming) -> bool:
    if incoming.callback_id or incoming.chat_id is None or incoming.user_id is None:
        return False

    amount = _parse_transfer_command(incoming.text)
    if amount is None:
        return False

    log.info(
        "transfer command candidate aid=%s chat_id=%s sender_id=%s amount=%s reply_to=%s",
        incoming.account_id,
        incoming.chat_id,
        incoming.user_id,
        amount,
        incoming.reply_to_display_name,
    )

    cfg = await account_bot_service.get_transfer_notice_config(db, incoming.account_id)
    if not cfg.get("enabled"):
        log.info("transfer command skipped: disabled aid=%s", incoming.account_id)
        return False
    if _is_configured_bot_user_id(cfg, incoming.user_id) or _is_configured_bot_user_id(cfg, incoming.reply_to_user_id):
        log.info(
            "transfer command skipped: bot sender/receiver aid=%s incoming_user=%s reply_to=%s",
            incoming.account_id,
            incoming.user_id,
            incoming.reply_to_user_id,
        )
        return False
    if not _transfer_command_chat_is_monitored(incoming, cfg):
        log.info(
            "transfer command skipped: chat not monitored aid=%s incoming_chat=%s amount=%s",
            incoming.account_id,
            incoming.chat_id,
            amount,
        )
        return False
    receiver_info = await _select_transfer_command_receiver(db, incoming, cfg, amount)
    if receiver_info is None:
        log.info(
            "transfer command skipped: receiver unknown aid=%s incoming_chat=%s amount=%s",
            incoming.account_id,
            incoming.chat_id,
            amount,
        )
        return False

    transfer_token = await account_bot_service.get_transfer_bot_token(db, incoming.account_id)
    if not transfer_token:
        log.info("transfer command skipped: missing transfer bot token aid=%s", incoming.account_id)
        return False

    payer = incoming.display_name or str(incoming.user_id)
    receiver = str(receiver_info["receiver_name"])
    raw_notice_template = str(cfg.get("transfer_notice_template") or DEFAULT_TRANSFER_NOTICE_TEMPLATE)
    notice, render_error = _render_transfer_bot_notice_with_error(
        raw_notice_template,
        payer,
        receiver,
        amount,
        payer_user_id=incoming.user_id,
        receiver_user_id=_int_or_none(receiver_info.get("receiver_user_id")),
    )
    if render_error is not None:
        error_text = f"{type(render_error).__name__}: {render_error}"
        log.warning(
            "transfer notice template render failed aid=%s chat_id=%s error=%s template=%r",
            incoming.account_id,
            incoming.chat_id,
            error_text,
            raw_notice_template[:500],
        )
        await _write_interaction_runtime_log(
            incoming,
            LEVEL_WARN,
            "转账通知模板渲染失败，已回退默认模板",
            error=error_text,
            template=raw_notice_template[:1000],
        )
    result = await account_bot_service.send_message(
        transfer_token,
        incoming.chat_id,
        notice,
        reply_to_message_id=incoming.message_id,
    )
    log.info(
        "transfer command emitted notice aid=%s chat_id=%s payer=%r receiver=%r amount=%s",
        incoming.account_id,
        incoming.chat_id,
        payer,
        receiver,
        amount,
    )
    from_user = result.get("from") if isinstance(result, dict) else None
    sender_id = (
        int(from_user["id"])
        if isinstance(from_user, dict) and from_user.get("id") is not None
        else None
    )
    if sender_id is not None:
        cfg_bot_id = cfg.get("trusted_bot_id")
        if cfg_bot_id is None:
            log.info("transfer bot user id detected aid=%s bot_id=%s", incoming.account_id, sender_id)
        elif int(cfg_bot_id) != int(sender_id):
            log.info(
                "transfer bot sent notice but trusted_bot_id differs aid=%s sent_by=%s expected=%s",
                incoming.account_id,
                sender_id,
                cfg_bot_id,
            )
        await _remember_transfer_bot_id(db, incoming.account_id, sender_id)

    return True


async def _remember_transfer_bot_id(db: Any, account_id: int, bot_id: int) -> None:
    try:
        row = await db.get(SystemSetting, account_bot_service.transfer_notice_setting_key(account_id))
        if row is None or not isinstance(row.value, dict):
            return
        current = dict(row.value)
        if _int_or_none(current.get("transfer_bot_id")) == int(bot_id):
            return
        current["transfer_bot_id"] = int(bot_id)
        row.value = current
        await db.commit()
    except Exception:  # noqa: BLE001
        log.debug("remember transfer bot id failed aid=%s bot_id=%s", account_id, bot_id, exc_info=True)


async def _try_handle_transfer_notice(db: Any, incoming: Incoming) -> bool:
    if incoming.callback_id or incoming.chat_id is None or incoming.user_id is None:
        return False

    cfg = await account_bot_service.get_transfer_notice_config(db, incoming.account_id)
    if not cfg.get("enabled"):
        return False
    if not _trusted_transfer_notice_sender_matches(cfg, incoming.user_id):
        if _incoming_matches_interaction_trigger(cfg, incoming):
            log.info(
                "transfer notice skipped: sender mismatch aid=%s incoming_user=%s trusted_bot_id=%s transfer_bot_id=%s",
                incoming.account_id,
                incoming.user_id,
                cfg.get("trusted_bot_id"),
                cfg.get("transfer_bot_id"),
            )
        return False

    if not any(
        _rule_trigger_mode_allows(rule, "payment")
        and _rule_chat_matches(rule, incoming.chat_id)
        and _rule_matches_incoming_trigger(rule, incoming)
        for rule in _interaction_rules(cfg)
    ):
        if _incoming_matches_interaction_trigger(cfg, incoming):
            log.info(
                "transfer notice skipped: no chat/trigger rule matched aid=%s incoming_chat=%s",
                incoming.account_id,
                incoming.chat_id,
            )
        return False

    parsed = _parse_incoming_transfer_notice(incoming)
    if parsed is None:
        log.info(
            "transfer notice skipped: parse failed aid=%s chat_id=%s sender_id=%s",
            incoming.account_id,
            incoming.chat_id,
            incoming.user_id,
        )
        return False
    rule = await _select_transfer_notice_rule(db, incoming, cfg, parsed)
    if rule is None:
        log.info(
            "transfer notice skipped: no matching rule aid=%s parsed_receiver=%r parsed_amount=%s",
            incoming.account_id,
            parsed.get("receiver_name"),
            parsed.get("amount"),
        )
        return False
    if not await _is_interaction_rule_open(incoming.account_id, rule, incoming.chat_id):
        log.info("transfer notice skipped: rule closed aid=%s rule=%s", incoming.account_id, rule.get("id"))
        return True
    if not await _claim_interaction_trigger(incoming, rule, "transfer_notice", parsed):
        log.info("transfer notice skipped: duplicate aid=%s rule=%s", incoming.account_id, rule.get("id"))
        return True
    usage_block = await _interaction_user_usage_block_message(incoming, rule, parsed)
    if usage_block:
        await _send(incoming, usage_block, reply_to_message_id=incoming.message_id)
        return True
    claimed_usage, usage_pending_key = await _claim_interaction_user_usage(incoming, rule, parsed)
    if not claimed_usage:
        usage_block = await _interaction_user_usage_block_message(incoming, rule, parsed)
        await _send(
            incoming,
            usage_block or "该用户正在处理该功能，请稍后再试。",
            reply_to_message_id=incoming.message_id,
        )
        return True

    executed = False
    try:
        executed = await _execute_interaction_rule(incoming, rule, parsed)
        if executed:
            await _mark_interaction_user_usage(incoming, rule, parsed)
    finally:
        await _release_interaction_user_usage_claim(usage_pending_key)
    await _audit_transfer_notice(db, incoming, parsed)
    log.info(
        "transfer notice matched aid=%s chat_id=%s sender_id=%s amount=%s",
        incoming.account_id,
        incoming.chat_id,
        incoming.user_id,
        parsed.get("amount"),
    )
    return True


async def _audit_transfer_notice(db: Any, incoming: Incoming, parsed: dict[str, Any]) -> None:
    try:
        await audit.write(
            db,
            None,
            "account_bot.transfer_notice_matched",
            target=f"account:{incoming.account_id}/chat:{incoming.chat_id}",
            detail={
                "trusted_bot_id": incoming.user_id,
                "message_id": incoming.message_id,
                "payer_name": parsed.get("payer_name"),
                "receiver_name": parsed.get("receiver_name"),
                "amount": parsed.get("amount"),
            },
        )
        await db.commit()
    except Exception:  # noqa: BLE001
        log.debug("account bot transfer notice audit failed aid=%s", incoming.account_id, exc_info=True)


def _extract_incoming(aid: int, token: str, update: dict[str, Any]) -> Incoming | None:
    if "callback_query" in update:
        cq = update["callback_query"] or {}
        msg = cq.get("message") or {}
        from_user = cq.get("from") or {}
        chat = msg.get("chat") or {}
        return Incoming(
            account_id=aid,
            token=token,
            update_id=int(update.get("update_id", 0)),
            user_id=_int_or_none(from_user.get("id")),
            chat_id=_int_or_none(chat.get("id")),
            chat_type=str(chat.get("type") or "") or None,
            message_id=_int_or_none(msg.get("message_id")),
            text="",
            callback_id=str(cq.get("id") or ""),
            callback_data=str(cq.get("data") or ""),
            display_name=_format_user_name(from_user),
            username=str(from_user.get("username") or "").strip() or None,
        )
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    from_user = msg.get("from") or {}
    chat = msg.get("chat") or {}
    reply = msg.get("reply_to_message") if isinstance(msg.get("reply_to_message"), dict) else {}
    reply_from = reply.get("from") if isinstance(reply.get("from"), dict) else {}
    reply_text = str(reply.get("text") or reply.get("caption") or "").strip()
    return Incoming(
        account_id=aid,
        token=token,
        update_id=int(update.get("update_id", 0)),
        user_id=_int_or_none(from_user.get("id")),
        chat_id=_int_or_none(chat.get("id")),
        chat_type=str(chat.get("type") or "") or None,
        message_id=_int_or_none(msg.get("message_id")),
        text=str(msg.get("text") or msg.get("caption") or "").strip(),
        display_name=_format_user_name(from_user),
        username=str(from_user.get("username") or "").strip() or None,
        reply_to_user_id=_int_or_none(reply_from.get("id")),
        reply_to_display_name=_format_user_name(reply_from) if reply_from else None,
        reply_to_username=str(reply_from.get("username") or "").strip() or None,
        reply_to_text=reply_text or None,
        entity_languages=_entity_languages(
            msg.get("entities"),
            msg.get("caption_entities"),
            reply.get("entities"),
            reply.get("caption_entities"),
        ),
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_user_name(raw: dict[str, Any]) -> str | None:
    first = str(raw.get("first_name") or "").strip()
    last = str(raw.get("last_name") or "").strip()
    username = str(raw.get("username") or "").strip()
    name = " ".join(x for x in [first, last] if x)
    if username:
        return f"{name} (@{username})" if name else f"@{username}"
    return name or None


def _command_tail(text: str) -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def _handle_command(incoming: Incoming, role: str) -> None:
    command = (incoming.text.split(maxsplit=1)[0] or "/start").lower()
    if command.startswith("/start"):
        await _show_start(incoming, role)
    elif command.startswith("/help"):
        await _show_help(incoming, role)
    elif command.startswith("/status"):
        await _show_status(incoming)
    elif command.startswith("/features"):
        await _show_features(incoming, role)
    elif command.startswith("/commands"):
        await _show_commands(incoming, role)
    elif command.startswith("/plugins"):
        await _handle_plugins_command(incoming, role)
    elif command.startswith("/rules"):
        await _show_rules(incoming, role)
    elif command.startswith("/logs"):
        await _show_logs(incoming)
    elif command.startswith("/pause"):
        await _pause_account(incoming, role)
    elif command.startswith("/resume"):
        await _resume_account(incoming, role)
    elif command.startswith("/restart"):
        await _request_confirm(incoming, role, "restart", "重启账号 worker")
    else:
        await _send(incoming, "未知命令。发送 /help 查看可用操作。", reply_markup=_main_keyboard(incoming.account_id))


async def _handle_callback(incoming: Incoming, role: str) -> None:
    parsed = _parse_callback(incoming.callback_data or "")
    if parsed is None:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="按钮已过期")
        return
    aid, action, resource, nonce = parsed
    if aid != incoming.account_id:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="账号不匹配", show_alert=True)
        return
    try:
        if action == "view":
            await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "")
            if resource == "status":
                await _show_status(incoming, edit=True)
            elif resource == "features":
                await _show_features(incoming, role, edit=True)
            elif resource == "commands":
                await _show_commands(incoming, role, edit=True)
            elif resource == "plugins":
                await _show_plugins(incoming, role, edit=True)
            elif resource == "rules":
                await _show_rules(incoming, role, edit=True)
            elif resource == "logs":
                await _show_logs(incoming, edit=True)
            elif resource == "help":
                await _show_help(incoming, role, edit=True)
            else:
                await _show_start(incoming, role, edit=True)
        elif action == "feature_toggle":
            await _toggle_feature(incoming, role, resource)
        elif action == "command_toggle":
            await _toggle_command(incoming, role, resource)
        elif action == "rule_toggle":
            await _toggle_rule(incoming, role, resource)
        elif action == "rule_exec":
            await _execute_rule(incoming, role, resource)
        elif action == "pause":
            await _pause_account(incoming, role, edit=True)
        elif action == "resume":
            await _resume_account(incoming, role, edit=True)
        elif action == "confirm":
            await _confirm_action(incoming, role, resource, nonce)
        elif action == "cancel":
            await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="已取消")
            await _show_start(incoming, role, edit=True)
        else:
            await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="按钮已过期")
    except PermissionError as exc:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text=str(exc), show_alert=True)
    except Exception as exc:  # noqa: BLE001
        clean = account_bot_service.sanitize_bot_error(exc, token=incoming.token)
        log.exception("account bot callback failed aid=%s action=%s", incoming.account_id, action)
        await account_bot_service.answer_callback(
            incoming.token,
            incoming.callback_id or "",
            text=clean[:180],
            show_alert=True,
        )


async def _show_start(incoming: Incoming, role: str, *, edit: bool = False) -> None:
    text = (
        "🤖 <b>账号 Bot 联动</b>\n"
        f"账号：<code>{incoming.account_id}</code>\n"
        f"你的角色：<code>{account_bot_service.html_text(role)}</code>\n\n"
        "这个 Bot 是当前 UserBot 账号的移动控制入口；复杂配置仍建议回到 GUI。"
    )
    await _send(incoming, text, reply_markup=_main_keyboard(incoming.account_id), edit=edit)


async def _show_help(incoming: Incoming, role: str, *, edit: bool = False) -> None:
    text = (
        "📖 <b>可用命令</b>\n"
        "/status 查看账号、worker 与最近错误\n"
        "/features 查看并启停账号功能\n"
        "/commands 查看并启停自定义命令模板\n"
        "/plugins 查看插件入口（远程插件高风险能力默认关闭）\n"
        "/rules 查看规则，scheduler 规则可手动执行\n"
        "/logs 查看最近运行日志\n"
        "/pause /resume 暂停或恢复账号\n"
        "/restart 重启账号 worker（admin + 二次确认）\n\n"
        "<b>角色说明</b>\n"
        "viewer：只读查看；operator：可启停功能/命令/规则和暂停恢复；admin：可执行危险动作。"
    )
    await _send(incoming, text, reply_markup=_main_keyboard(incoming.account_id), edit=edit)


async def _show_status(incoming: Incoming, *, edit: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        acc = await db.get(Account, incoming.account_id)
        enabled_features = (
            await db.execute(
                select(AccountFeature).where(
                    AccountFeature.account_id == incoming.account_id,
                    AccountFeature.enabled.is_(True),
                )
            )
        ).scalars().all()
        last_log = (
            await db.execute(
                select(RuntimeLog)
                .where(RuntimeLog.account_id == incoming.account_id)
                .order_by(desc(RuntimeLog.ts))
                .limit(1)
            )
        ).scalar_one_or_none()
    if acc is None:
        text = "账号不存在。"
    else:
        name = acc.display_name or (f"@{acc.tg_username}" if acc.tg_username else f"#{acc.id}")
        text = (
            "📌 <b>账号状态</b>\n"
            f"账号：{account_bot_service.html_text(name)}\n"
            f"系统 ID：<code>{acc.id}</code>\n"
            f"Telegram ID：<code>{acc.tg_user_id or '未同步'}</code>\n"
            f"状态：<code>{account_bot_service.html_text(acc.status)}</code>\n"
            f"已启用功能：<code>{len(enabled_features)}</code>\n"
        )
        if last_log:
            text += (
                "\n<b>最近日志</b>\n"
                f"{account_bot_service.html_text(last_log.level)} · "
                f"{account_bot_service.html_text(last_log.source or 'worker')}\n"
                f"{account_bot_service.html_text(last_log.message)}"
            )
    await _send(incoming, text, reply_markup=_main_keyboard(incoming.account_id), edit=edit)


async def _show_features(incoming: Incoming, role: str, *, edit: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        features = await feature_service.list_features(db)
        afs = await feature_service.get_account_features(db, incoming.account_id)
    state = {af.feature_key: af for af in afs}
    lines = ["🧩 <b>账号功能</b>", "点击按钮可启停；复杂配置请用 GUI。", ""]
    rows: list[list[dict[str, str]]] = []
    for feature in features[:_MAX_BUTTON_ROWS]:
        af = state.get(feature.key)
        enabled = bool(af and af.enabled)
        lines.append(f"{'✅' if enabled else '⬜️'} {account_bot_service.html_text(feature.display_name)} <code>{feature.key}</code>")
        if account_bot_service.role_allows(role, ACCOUNT_BOT_ROLE_OPERATOR):
            rows.append([
                _button(
                    f"{'停用' if enabled else '启用'} {feature.display_name}"[:32],
                    "feature_toggle",
                    feature.key,
                    aid=incoming.account_id,
                )
            ])
    rows.append([_button("返回主菜单", "view", "main", aid=incoming.account_id)])
    await _send(incoming, "\n".join(lines), reply_markup=_keyboard(rows), edit=edit)


async def _show_plugins(incoming: Incoming, role: str, *, edit: bool = False) -> None:
    policy = await _get_remote_plugin_policy(incoming.account_id)
    policy_summary = (
        f"总开关：{'开' if policy['enabled'] else '关'}，"
        f"install：{'开' if policy['install'] else '关'}，"
        f"update：{'开' if policy['update'] else '关'}，"
        f"uninstall：{'开' if policy['uninstall'] else '关'}，"
        f"第三方启停：{'开' if policy['enable_disable'] else '关'}"
    )
    async with AsyncSessionLocal() as db:
        features = await feature_service.list_features(db)
        afs = await feature_service.get_account_features(db, incoming.account_id)
        remotes = await remote_plugin_service.list_installed(db)
    state = {af.feature_key: af for af in afs}
    lines = [
        "🧱 <b>插件列表</b>",
        "这里按账号启停插件。远程安装/更新/卸载可用：",
        "<code>/plugins install &lt;git-url&gt;</code>",
        "<code>/plugins update &lt;name&gt;</code>",
        "<code>/plugins uninstall &lt;name&gt;</code>",
        f"远程高风险开关：{policy_summary}",
        "",
    ]
    rows: list[list[dict[str, str]]] = []
    for feature in features[:_MAX_BUTTON_ROWS]:
        af = state.get(feature.key)
        enabled = bool(af and af.enabled)
        source = "内置" if feature.is_builtin else "第三方"
        lines.append(
            f"{'✅' if enabled else '⬜️'} {account_bot_service.html_text(feature.display_name)}"
            f" · {source} · <code>{feature.key}</code>"
        )
        if account_bot_service.role_allows(role, ACCOUNT_BOT_ROLE_OPERATOR):
            rows.append([
                _button(
                    f"{'停用' if enabled else '启用'} {feature.display_name}"[:32],
                    "feature_toggle",
                    feature.key,
                    aid=incoming.account_id,
                )
            ])
    if remotes:
        lines.append("")
        lines.append("<b>远程插件</b>")
        for row in remotes[:12]:
            lines.append(
                f"{'✅' if row.enabled else '⬜️'} "
                f"{account_bot_service.html_text(row.display_name or row.name)}"
                f" · v{account_bot_service.html_text(row.version)} · <code>{row.name}</code>"
            )
    rows.append([_button("返回主菜单", "view", "main", aid=incoming.account_id)])
    await _send(incoming, "\n".join(lines), reply_markup=_keyboard(rows), edit=edit)


async def _handle_plugins_command(incoming: Incoming, role: str) -> None:
    args = _command_tail(incoming.text)
    if not args:
        await _show_plugins(incoming, role)
        return
    parts = args.split(maxsplit=1)
    sub = parts[0].lower()
    value = parts[1].strip() if len(parts) > 1 else ""
    if sub == "install" and value:
        allowed, message = await _check_remote_plugin_permission(incoming.account_id, role, "install")
        if not allowed:
            await _send(incoming, message, reply_markup=_main_keyboard(incoming.account_id))
            return
        await _request_confirm(
            incoming,
            role,
            "plugin_install",
            "安装远程插件",
            payload={"source_url": value},
        )
        return
    if sub == "update" and value:
        allowed, message = await _check_remote_plugin_permission(incoming.account_id, role, "update")
        if not allowed:
            await _send(incoming, message, reply_markup=_main_keyboard(incoming.account_id))
            return
        await _request_confirm(
            incoming,
            role,
            "plugin_update",
            f"更新远程插件 {value}",
            payload={"name": value},
        )
        return
    if sub in {"uninstall", "remove", "delete"} and value:
        allowed, message = await _check_remote_plugin_permission(incoming.account_id, role, "uninstall")
        if not allowed:
            await _send(incoming, message, reply_markup=_main_keyboard(incoming.account_id))
            return
        await _request_confirm(
            incoming,
            role,
            "plugin_uninstall",
            f"卸载远程插件 {value}",
            payload={"name": value},
        )
        return
    await _send(
        incoming,
        "插件命令格式：\n"
        "<code>/plugins</code>\n"
        "<code>/plugins install &lt;git-url&gt;</code>\n"
        "<code>/plugins update &lt;name&gt;</code>\n"
        "<code>/plugins uninstall &lt;name&gt;</code>",
        reply_markup=_main_keyboard(incoming.account_id),
    )


async def _show_commands(incoming: Incoming, role: str, *, edit: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        cmd_prefix = await _load_command_prefix(db)
        items = await command_service.list_for_account(db, incoming.account_id)
    lines = ["⌨️ <b>自定义命令模板</b>", "点击按钮可启停当前账号的模板。", ""]
    rows: list[list[dict[str, str]]] = []
    for item in items[:_MAX_BUTTON_ROWS]:
        tpl = item.template
        lines.append(
            f"{'✅' if item.enabled else '⬜️'} <code>{account_bot_service.html_text(cmd_prefix)}{account_bot_service.html_text(tpl.name)}</code>"
            f" · {account_bot_service.html_text(tpl.type)}"
        )
        if account_bot_service.role_allows(role, ACCOUNT_BOT_ROLE_OPERATOR):
            rows.append([
                _button(
                    f"{'停用' if item.enabled else '启用'} {cmd_prefix}{tpl.name}"[:32],
                    "command_toggle",
                    str(tpl.id),
                    aid=incoming.account_id,
                )
            ])
    rows.append([_button("返回主菜单", "view", "main", aid=incoming.account_id)])
    await _send(incoming, "\n".join(lines), reply_markup=_keyboard(rows), edit=edit)


async def _show_rules(incoming: Incoming, role: str, *, edit: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        rules = (
            await db.execute(
                select(Rule)
                .where(Rule.account_id == incoming.account_id)
                .order_by(Rule.feature_key.asc(), Rule.priority.desc(), Rule.id.asc())
                .limit(20)
            )
        ).scalars().all()
    lines = ["📋 <b>规则</b>", "展示最近 20 条规则；scheduler 规则可手动执行。", ""]
    rows: list[list[dict[str, str]]] = []
    for rule in rules:
        lines.append(
            f"{'✅' if rule.enabled else '⬜️'} #{rule.id} "
            f"{account_bot_service.html_text(rule.name)} · <code>{rule.feature_key}</code>"
        )
        if account_bot_service.role_allows(role, ACCOUNT_BOT_ROLE_OPERATOR):
            row = [
                _button(
                    f"{'停用' if rule.enabled else '启用'} #{rule.id}",
                    "rule_toggle",
                    str(rule.id),
                    aid=incoming.account_id,
                )
            ]
            if rule.feature_key == "scheduler":
                row.append(_button(f"执行 #{rule.id}", "rule_exec", str(rule.id), aid=incoming.account_id))
            rows.append(row)
    if not rules:
        lines.append("暂无规则。")
    rows.append([_button("返回主菜单", "view", "main", aid=incoming.account_id)])
    await _send(incoming, "\n".join(lines), reply_markup=_keyboard(rows), edit=edit)


async def _show_logs(incoming: Incoming, *, edit: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        logs = (
            await db.execute(
                select(RuntimeLog)
                .where(RuntimeLog.account_id == incoming.account_id)
                .order_by(desc(RuntimeLog.ts))
                .limit(8)
            )
        ).scalars().all()
    lines = ["🧾 <b>最近运行日志</b>"]
    for row in logs:
        lines.append(
            f"{account_bot_service.html_text(row.level)} · "
            f"{account_bot_service.html_text(row.source or 'worker')} · "
            f"{account_bot_service.html_text(row.message)}"
        )
    if not logs:
        lines.append("暂无日志。")
    await _send(
        incoming,
        "\n".join(lines),
        reply_markup=_main_keyboard(incoming.account_id),
        edit=edit,
    )


async def _toggle_feature(incoming: Incoming, role: str, key: str) -> None:
    _require(role, ACCOUNT_BOT_ROLE_OPERATOR)
    async with AsyncSessionLocal() as db:
        feature = await db.get(Feature, key)
        if feature is None:
            await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="功能不存在", show_alert=True)
            return
        current = (
            await db.execute(
                select(AccountFeature).where(
                    AccountFeature.account_id == incoming.account_id,
                    AccountFeature.feature_key == key,
                )
            )
        ).scalar_one_or_none()
        enabled = not bool(current and current.enabled)
        if not feature.is_builtin:
            allowed, message = await _check_remote_plugin_permission(incoming.account_id, role, "enable_disable")
            if not allowed:
                await account_bot_service.answer_callback(
                    incoming.token,
                    incoming.callback_id or "",
                    text=message[:100],
                    show_alert=True,
                )
                return
            if incoming.callback_id:
                await account_bot_service.answer_callback(incoming.token, incoming.callback_id, text="请确认")
            await _request_confirm(
                incoming,
                role,
                "plugin_toggle",
                f"{'启用' if enabled else '停用'}插件 {feature.display_name}",
                payload={"feature_key": key, "enabled": enabled},
            )
            return
        if enabled:
            remote = await remote_plugin_service.get_by_name(db, key)
            if remote is not None and not remote.enabled:
                await remote_plugin_service.enable(db, key)
        await feature_service.set_account_feature(db, incoming.account_id, key, enabled)
        await audit.write(
            db,
            None,
            "account_bot.feature_toggle",
            target=f"account:{incoming.account_id}/feature:{key}",
            detail=_audit_detail(incoming, role, {"enabled": enabled}),
        )
        await db.commit()
    await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="已更新")
    await _show_features(incoming, role, edit=True)


async def _toggle_command(incoming: Incoming, role: str, resource: str) -> None:
    _require(role, ACCOUNT_BOT_ROLE_OPERATOR)
    try:
        tpl_id = int(resource)
    except ValueError:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="模板不存在", show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        link = (
            await db.execute(
                select(AccountCommandLink).where(
                    AccountCommandLink.account_id == incoming.account_id,
                    AccountCommandLink.template_id == tpl_id,
                )
            )
        ).scalar_one_or_none()
        if link and link.enabled:
            await command_service.disable_for_account(db, incoming.account_id, tpl_id)
            enabled = False
        else:
            await command_service.enable_for_account(db, incoming.account_id, tpl_id)
            enabled = True
        await audit.write(
            db,
            None,
            "account_bot.command_toggle",
            target=f"account:{incoming.account_id}/command_template:{tpl_id}",
            detail=_audit_detail(incoming, role, {"enabled": enabled}),
        )
        await db.commit()
        await command_service.notify_reload(incoming.account_id)
    await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="已更新")
    await _show_commands(incoming, role, edit=True)


async def _toggle_rule(incoming: Incoming, role: str, resource: str) -> None:
    _require(role, ACCOUNT_BOT_ROLE_OPERATOR)
    try:
        rid = int(resource)
    except ValueError:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="规则不存在", show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        rule = await db.get(Rule, rid)
        if rule is None or rule.account_id != incoming.account_id:
            await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="规则不存在", show_alert=True)
            return
        rule.enabled = not bool(rule.enabled)
        await audit.write(
            db,
            None,
            "account_bot.rule_toggle",
            target=f"account:{incoming.account_id}/rule:{rid}",
            detail=_audit_detail(incoming, role, {"enabled": rule.enabled}),
        )
        await db.commit()
        try:
            redis = get_redis()
            await publish_cmd_with_ack(redis, incoming.account_id, CMD_RELOAD_CONFIG)
        except Exception:
            log.debug("account bot rule reload failed aid=%s", incoming.account_id, exc_info=True)
    await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="已更新")
    await _show_rules(incoming, role, edit=True)


async def _execute_rule(incoming: Incoming, role: str, resource: str) -> None:
    _require(role, ACCOUNT_BOT_ROLE_OPERATOR)
    try:
        rid = int(resource)
    except ValueError:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="规则不存在", show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        rule = await db.get(Rule, rid)
        if rule is None or rule.account_id != incoming.account_id or rule.feature_key != "scheduler":
            await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="仅 scheduler 规则可执行", show_alert=True)
            return
        await audit.write(
            db,
            None,
            "account_bot.rule_execute",
            target=f"account:{incoming.account_id}/rule:{rid}",
            detail=_audit_detail(incoming, role),
        )
        await db.commit()
    redis = get_redis()
    reply_channel = f"worker_reply:{incoming.account_id}:exec_rule:{secrets.token_hex(8)}"
    pubsub = redis.pubsub()
    ok = False
    error = None
    try:
        await pubsub.subscribe(reply_channel)
        await redis.publish(cmd_channel(incoming.account_id), make_cmd(CMD_EXECUTE_RULE, rule_id=rid, reply_to=reply_channel))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            msg = await asyncio.wait_for(
                pubsub.get_message(ignore_subscribe_messages=True, timeout=remaining),
                timeout=remaining + 0.1,
            )
            if not msg or msg.get("type") != "message":
                continue
            payload = IPCMessage.decode(msg["data"]).payload
            ok = bool(payload.get("ok"))
            error = payload.get("error")
            break
    finally:
        try:
            await pubsub.unsubscribe(reply_channel)
            close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            ret = close() if close else None
            if hasattr(ret, "__await__"):
                await ret
        except Exception:
            pass
    await account_bot_service.answer_callback(
        incoming.token,
        incoming.callback_id or "",
        text="已执行" if ok else (str(error or "worker 响应超时")[:100]),
        show_alert=not ok,
    )


async def _pause_account(incoming: Incoming, role: str, *, edit: bool = False) -> None:
    _require(role, ACCOUNT_BOT_ROLE_OPERATOR)
    async with AsyncSessionLocal() as db:
        await account_service.pause(db, incoming.account_id)
        await audit.write(
            db,
            None,
            "account_bot.account_pause",
            target=f"account:{incoming.account_id}",
            detail=_audit_detail(incoming, role),
        )
        await db.commit()
    if incoming.callback_id:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id, text="已暂停")
    await _send(incoming, "账号已暂停。", reply_markup=_main_keyboard(incoming.account_id), edit=edit)


async def _resume_account(incoming: Incoming, role: str, *, edit: bool = False) -> None:
    _require(role, ACCOUNT_BOT_ROLE_OPERATOR)
    async with AsyncSessionLocal() as db:
        await account_service.resume(db, incoming.account_id)
        await audit.write(
            db,
            None,
            "account_bot.account_resume",
            target=f"account:{incoming.account_id}",
            detail=_audit_detail(incoming, role),
        )
        await db.commit()
    if incoming.callback_id:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id, text="已恢复")
    await _send(incoming, "账号已恢复。", reply_markup=_main_keyboard(incoming.account_id), edit=edit)


async def _request_confirm(
    incoming: Incoming,
    role: str,
    action: str,
    label: str,
    *,
    payload: dict[str, Any] | None = None,
) -> None:
    _require(role, ACCOUNT_BOT_ROLE_ADMIN)
    nonce = secrets.token_urlsafe(8)
    redis = get_redis()
    confirm_payload = {
        "account_id": incoming.account_id,
        "tg_user_id": incoming.user_id,
        "action": action,
        "label": label,
        "payload": payload or {},
    }
    await redis.setex(_confirm_redis_key(nonce), _CONFIRM_TTL_SECONDS, json.dumps(confirm_payload, ensure_ascii=False))
    await _audit_confirm_event(
        incoming,
        role,
        "account_bot.confirm_requested",
        action=action,
        extra={"label": label},
    )
    rows = [
        [
            _button("确认执行", "confirm", action, aid=incoming.account_id, nonce=nonce),
            _button("取消", "cancel", action, aid=incoming.account_id, nonce=nonce),
        ]
    ]
    await _send(
        incoming,
        f"⚠️ <b>二次确认</b>\n操作：{account_bot_service.html_text(label)}\n确认票据 5 分钟内有效。",
        reply_markup=_keyboard(rows),
        edit=bool(incoming.callback_id),
    )


async def _confirm_action(
    incoming: Incoming,
    role: str,
    resource: str,
    nonce: str | None,
) -> None:
    _require(role, ACCOUNT_BOT_ROLE_ADMIN)
    if not nonce:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="确认已过期", show_alert=True)
        return
    redis = get_redis()
    raw = await _read_confirm_payload(redis, nonce)
    if not raw:
        await _audit_confirm_event(
            incoming,
            role,
            "account_bot.confirm_expired",
            action=resource,
            extra={"reason": "missing_or_expired"},
        )
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="确认已过期", show_alert=True)
        return
    data = json.loads(raw)
    if data.get("account_id") != incoming.account_id or data.get("tg_user_id") != incoming.user_id:
        await _audit_confirm_event(
            incoming,
            role,
            "account_bot.confirm_rejected",
            action=resource,
            extra={"reason": "owner_mismatch"},
        )
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="只能由原用户确认", show_alert=True)
        return
    if data.get("action") != resource:
        await _audit_confirm_event(
            incoming,
            role,
            "account_bot.confirm_rejected",
            action=resource,
            extra={"reason": "action_mismatch"},
        )
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="确认资源不匹配", show_alert=True)
        return
    consumed = await _consume_confirm_payload(redis, nonce)
    if not consumed:
        await _audit_confirm_event(
            incoming,
            role,
            "account_bot.confirm_expired",
            action=resource,
            extra={"reason": "already_consumed"},
        )
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="确认已过期", show_alert=True)
        return
    await _audit_confirm_event(
        incoming,
        role,
        "account_bot.confirm_consumed",
        action=resource,
    )
    await _execute_confirmed_action(incoming, role, json.loads(consumed))


async def _restart_account_worker(incoming: Incoming, role: str) -> None:
    async with AsyncSessionLocal() as db:
        await audit.write(
            db,
            None,
            "account_bot.account_restart",
            target=f"account:{incoming.account_id}",
            detail=_audit_detail(incoming, role),
        )
        await db.commit()
    redis = get_redis()
    await redis.publish(cmd_channel(incoming.account_id), make_cmd("stop"))
    await redis.publish(GLOBAL_CHANNEL, make_cmd("start_worker", account_id=incoming.account_id))
    if incoming.callback_id:
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id, text="已下发重启")
    await _send(incoming, "已下发账号 worker 重启。", reply_markup=_main_keyboard(incoming.account_id), edit=True)


async def _execute_confirmed_action(
    incoming: Incoming,
    role: str,
    data: dict[str, Any],
) -> None:
    action = str(data.get("action") or "")
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    if action == "restart":
        await _restart_account_worker(incoming, role)
        return
    if action == "plugin_install":
        allowed, message = await _check_remote_plugin_permission(incoming.account_id, role, "install")
        if not allowed:
            await account_bot_service.answer_callback(
                incoming.token, incoming.callback_id or "", text=message[:100], show_alert=True
            )
            return
        source_url = str(payload.get("source_url") or "").strip()
        if not source_url:
            await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="缺少 Git URL", show_alert=True)
            return
        async with AsyncSessionLocal() as db:
            row = await remote_plugin_service.install(db, source_url, default_enabled=False)
            await audit.write(
                db,
                None,
                "account_bot.plugin_install",
                target=f"remote_plugin:{row.name}",
                detail=_audit_detail(incoming, role, {"name": row.name}),
            )
            await db.commit()
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="插件已安装")
        await _show_plugins(incoming, role, edit=True)
        return
    if action == "plugin_update":
        allowed, message = await _check_remote_plugin_permission(incoming.account_id, role, "update")
        if not allowed:
            await account_bot_service.answer_callback(
                incoming.token, incoming.callback_id or "", text=message[:100], show_alert=True
            )
            return
        name = str(payload.get("name") or "").strip()
        async with AsyncSessionLocal() as db:
            row = await remote_plugin_service.update(db, name)
            await audit.write(
                db,
                None,
                "account_bot.plugin_update",
                target=f"remote_plugin:{row.name}",
                detail=_audit_detail(incoming, role, {"name": row.name}),
            )
            await db.commit()
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="插件已更新")
        await _show_plugins(incoming, role, edit=True)
        return
    if action == "plugin_uninstall":
        allowed, message = await _check_remote_plugin_permission(incoming.account_id, role, "uninstall")
        if not allowed:
            await account_bot_service.answer_callback(
                incoming.token, incoming.callback_id or "", text=message[:100], show_alert=True
            )
            return
        name = str(payload.get("name") or "").strip()
        async with AsyncSessionLocal() as db:
            found = await remote_plugin_service.uninstall(db, name)
            if not found:
                await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="插件不存在", show_alert=True)
                return
            await audit.write(
                db,
                None,
                "account_bot.plugin_uninstall",
                target=f"remote_plugin:{name}",
                detail=_audit_detail(incoming, role, {"name": name}),
            )
            await db.commit()
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="插件已卸载")
        await _show_plugins(incoming, role, edit=True)
        return
    if action == "plugin_toggle":
        allowed, message = await _check_remote_plugin_permission(incoming.account_id, role, "enable_disable")
        if not allowed:
            await account_bot_service.answer_callback(
                incoming.token, incoming.callback_id or "", text=message[:100], show_alert=True
            )
            return
        key = str(payload.get("feature_key") or "").strip()
        enabled = bool(payload.get("enabled"))
        if not key:
            await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="缺少插件 key", show_alert=True)
            return
        async with AsyncSessionLocal() as db:
            feature = await db.get(Feature, key)
            if feature is None or feature.is_builtin:
                await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="插件不存在", show_alert=True)
                return
            if enabled:
                remote = await remote_plugin_service.get_by_name(db, key)
                if remote is not None and not remote.enabled:
                    await remote_plugin_service.enable(db, key)
            await feature_service.set_account_feature(db, incoming.account_id, key, enabled)
            await audit.write(
                db,
                None,
                "account_bot.feature_toggle",
                target=f"account:{incoming.account_id}/feature:{key}",
                detail=_audit_detail(incoming, role, {"enabled": enabled}),
            )
            await db.commit()
        await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="已更新")
        await _show_plugins(incoming, role, edit=True)
        return
    await account_bot_service.answer_callback(incoming.token, incoming.callback_id or "", text="未知确认动作", show_alert=True)


async def _get_remote_plugin_policy(account_id: int) -> dict[str, bool]:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(AccountBot).where(AccountBot.account_id == account_id))
        ).scalar_one_or_none()
    return account_bot_service.normalize_remote_plugin_policy(
        row.remote_plugin_policy if row is not None else None
    )


async def _check_remote_plugin_permission(account_id: int, role: str, action: str) -> tuple[bool, str]:
    if not account_bot_service.role_allows(role, ACCOUNT_BOT_ROLE_ADMIN):
        return False, "仅 admin 可执行远程插件高风险操作。"
    policy = await _get_remote_plugin_policy(account_id)
    if not policy.get("enabled", False):
        return False, _REMOTE_POLICY_HINT
    key = action if action in {"install", "update", "uninstall", "enable_disable"} else ""
    if key and not policy.get(key, False):
        return False, f"远程插件 {key} 未开启。{_REMOTE_POLICY_HINT}"
    return True, ""


async def _send(
    incoming: Incoming,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    reply_to_message_id: int | None = None,
    edit: bool = False,
) -> None:
    if incoming.chat_id is None:
        return
    if edit and incoming.message_id is not None:
        try:
            await account_bot_service.edit_message(
                incoming.token,
                incoming.chat_id,
                incoming.message_id,
                text,
                reply_markup=reply_markup,
            )
            return
        except Exception:
            log.debug("edit account bot message failed, fallback send", exc_info=True)
    await account_bot_service.send_message(
        incoming.token,
        incoming.chat_id,
        text,
        reply_markup=reply_markup,
        reply_to_message_id=reply_to_message_id,
    )


def _require(role: str, required: str) -> None:
    if not account_bot_service.role_allows(role, required):
        raise PermissionError("权限不足")


def _audit_detail(
    incoming: Incoming,
    role: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "source": "account_bot",
        "tg_user_id": incoming.user_id,
        "role": role,
        "account_id": incoming.account_id,
    }
    if extra:
        detail.update(extra)
    return detail


async def _audit_confirm_event(
    incoming: Incoming,
    role: str,
    event: str,
    *,
    action: str,
    extra: dict[str, Any] | None = None,
) -> None:
    detail = _audit_detail(incoming, role, {"confirm_action": action})
    if extra:
        detail.update(extra)
    try:
        async with AsyncSessionLocal() as db:
            await audit.write(
                db,
                None,
                event,
                target=f"account:{incoming.account_id}",
                detail=detail,
            )
            await db.commit()
    except Exception:
        log.warning("account bot confirm audit failed aid=%s event=%s", incoming.account_id, event, exc_info=True)
