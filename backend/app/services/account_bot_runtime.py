"""账号绑定 Bot 的 polling runtime 与命令处理。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, select

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
from ..db.models.remote_plugin import RemotePlugin
from ..db.models.rule import Rule
from ..redis_client import get_redis
from ..worker.ipc import (
    CMD_EXECUTE_RULE,
    CMD_RELOAD_CONFIG,
    GLOBAL_CHANNEL,
    IPCMessage,
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
_TASK_LOCK = asyncio.Lock()
_CONFIRM_PREFIX = "account_bot_confirm:"
_CONFIRM_TTL_SECONDS = 300
_MAX_BUTTON_ROWS = 24
_REMOTE_POLICY_HINT = "该功能默认关闭，请管理员在 Web 控制台启用后重试（高风险操作，仍需二次确认）。"


@dataclass(slots=True)
class Incoming:
    account_id: int
    token: str
    update_id: int
    user_id: int | None
    chat_id: int | None
    message_id: int | None
    text: str
    callback_id: str | None = None
    callback_data: str | None = None
    display_name: str | None = None


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
    log.info("account bot manager started: %d task(s)", len(rows))


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
            message_id=_int_or_none(msg.get("message_id")),
            text="",
            callback_id=str(cq.get("id") or ""),
            callback_data=str(cq.get("data") or ""),
            display_name=_format_user_name(from_user),
        )
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    from_user = msg.get("from") or {}
    chat = msg.get("chat") or {}
    return Incoming(
        account_id=aid,
        token=token,
        update_id=int(update.get("update_id", 0)),
        user_id=_int_or_none(from_user.get("id")),
        chat_id=_int_or_none(chat.get("id")),
        message_id=_int_or_none(msg.get("message_id")),
        text=str(msg.get("text") or "").strip(),
        display_name=_format_user_name(from_user),
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
        remotes = (
            await db.execute(select(RemotePlugin).order_by(RemotePlugin.name.asc()))
        ).scalars().all()
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
        items = await command_service.list_for_account(db, incoming.account_id)
    lines = ["⌨️ <b>自定义命令模板</b>", "点击按钮可启停当前账号的模板。", ""]
    rows: list[list[dict[str, str]]] = []
    for item in items[:_MAX_BUTTON_ROWS]:
        tpl = item.template
        lines.append(
            f"{'✅' if item.enabled else '⬜️'} <code>,{account_bot_service.html_text(tpl.name)}</code>"
            f" · {account_bot_service.html_text(tpl.type)}"
        )
        if account_bot_service.role_allows(role, ACCOUNT_BOT_ROLE_OPERATOR):
            rows.append([
                _button(
                    f"{'停用' if item.enabled else '启用'} ,{tpl.name}"[:32],
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
