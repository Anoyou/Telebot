"""交互 Bot 配置与测试转账通知配置。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_str, encrypt_str
from ..db.models.account_bot import AccountBot
from ..db.models.system import SystemSetting
from . import account_bot_service

TRANSFER_NOTICE_SETTING_PREFIX = "account_bot_transfer_notice:"
VALID_TRIGGER_MODES = {"payment", "keyword", "both"}
VALID_AMOUNT_MATCH_MODES = {"eq", "gte"}
VALID_CONCURRENCY = {"chat", "user", "none"}


def transfer_notice_setting_key(aid: int) -> str:
    return f"{TRANSFER_NOTICE_SETTING_PREFIX}{int(aid)}"


def default_transfer_notice_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "chat_id": None,
        "chat_ids": [],
        "interaction_bot_token_enc": None,
        "has_interaction_bot_token": False,
        "interaction_bot_username": None,
        "interaction_bot_id": None,
        "interaction_running": False,
        "interaction_runtime_status": "stopped",
        "interaction_last_update_id": None,
        "interaction_last_error": None,
        "trusted_bot_id": None,
        "transfer_bot_token_enc": None,
        "has_transfer_bot_token": False,
        "trigger_mode": "payment",
        "trigger_text": "转账成功",
        "trigger_texts": ["转账成功"],
        "module_start_keywords": [],
        "receiver_text": None,
        "amount": None,
        "amount_match_mode": "eq",
        "action": "notice",
        "math_prize": 123,
        "module_key": None,
        "module_action": None,
        "module_prize": None,
        "module_start_text": None,
        "open_commands": [],
        "close_commands": [],
        "status_commands": [],
        "disabled_message": "规则已关闭，暂时不能开启该模块。",
        "valid_seconds": 600,
        "concurrency": "chat",
        "response_template": "检测到 {payer_name} 向 {receiver_name} 转账 {amount}，已进入娱乐流程。",
        "rules": [],
    }


def normalize_transfer_notice_config(raw: Any) -> dict[str, Any]:
    base = default_transfer_notice_config()
    if isinstance(raw, dict):
        for key in base:
            if key in raw:
                base[key] = raw[key]
    base["enabled"] = bool(base.get("enabled", False))
    for key in (
        "chat_id",
        "interaction_bot_id",
        "interaction_last_update_id",
        "trusted_bot_id",
        "amount",
        "math_prize",
        "module_prize",
        "valid_seconds",
    ):
        try:
            base[key] = int(base[key]) if base[key] not in (None, "") else None
        except (TypeError, ValueError):
            base[key] = None
    chat_ids: list[int] = []
    raw_chat_ids = base.get("chat_ids")
    if isinstance(raw_chat_ids, list):
        for raw_id in raw_chat_ids:
            try:
                chat_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if chat_id not in chat_ids:
                chat_ids.append(chat_id)
    if base.get("chat_id") is not None and int(base["chat_id"]) not in chat_ids:
        chat_ids.insert(0, int(base["chat_id"]))
    base["chat_ids"] = chat_ids
    base["chat_id"] = chat_ids[0] if chat_ids else None
    if base.get("math_prize") is None or int(base["math_prize"]) <= 0:
        base["math_prize"] = 123
    action = str(base.get("action") or "notice").strip()
    base["action"] = action if action in {"notice", "math10", "module"} else "notice"
    trigger_mode = str(base.get("trigger_mode") or "payment").strip()
    base["trigger_mode"] = trigger_mode if trigger_mode in VALID_TRIGGER_MODES else "payment"
    amount_match_mode = str(base.get("amount_match_mode") or "eq").strip()
    base["amount_match_mode"] = amount_match_mode if amount_match_mode in VALID_AMOUNT_MATCH_MODES else "eq"
    concurrency = str(base.get("concurrency") or "chat").strip()
    base["concurrency"] = concurrency if concurrency in VALID_CONCURRENCY else "chat"
    if base.get("valid_seconds") is None or int(base["valid_seconds"]) < 30:
        base["valid_seconds"] = 600
    base["valid_seconds"] = min(int(base["valid_seconds"]), 86400)
    if base.get("module_prize") is not None and int(base["module_prize"]) <= 0:
        base["module_prize"] = None
    for key in ("module_key", "module_action", "module_start_text", "disabled_message"):
        value = str(base.get(key) or "").strip()
        base[key] = value or None
    base["has_interaction_bot_token"] = bool(base.get("interaction_bot_token_enc"))
    username = str(base.get("interaction_bot_username") or "").strip().lstrip("@")
    base["interaction_bot_username"] = username or None
    base["interaction_running"] = bool(base.get("interaction_running", False))
    runtime_status = str(base.get("interaction_runtime_status") or "").strip()
    base["interaction_runtime_status"] = runtime_status if runtime_status in {"running", "stopped"} else "stopped"
    error = str(base.get("interaction_last_error") or "").strip()
    base["interaction_last_error"] = account_bot_service.label_bot_polling_error(error, role="interaction") if error else None
    if not base["enabled"] or not base["has_interaction_bot_token"]:
        base["interaction_running"] = False
        base["interaction_runtime_status"] = "stopped"
        base["interaction_last_error"] = None
    base["has_transfer_bot_token"] = bool(base.get("transfer_bot_token_enc"))
    trigger = str(base.get("trigger_text") or "").strip()
    base["trigger_text"] = trigger or "转账成功"
    triggers: list[str] = []
    raw_triggers = base.get("trigger_texts")
    if isinstance(raw_triggers, list):
        for raw_trigger in raw_triggers:
            item = str(raw_trigger or "").strip()
            if item and item not in triggers:
                triggers.append(item)
    if base["trigger_text"] not in triggers:
        triggers.insert(0, base["trigger_text"])
    base["trigger_texts"] = triggers or ["转账成功"]
    base["trigger_text"] = base["trigger_texts"][0]
    base["module_start_keywords"] = _normalize_string_list(base.get("module_start_keywords"))
    base["open_commands"] = _normalize_string_list(base.get("open_commands"))
    base["close_commands"] = _normalize_string_list(base.get("close_commands"))
    base["status_commands"] = _normalize_string_list(base.get("status_commands"))
    receiver = str(base.get("receiver_text") or "").strip()
    base["receiver_text"] = receiver or None
    template = str(base.get("response_template") or "").strip()
    base["response_template"] = template or default_transfer_notice_config()["response_template"]
    rules = normalize_interaction_rules(base.get("rules"))
    if not rules:
        rules = [
            {
                "id": "legacy-default",
                "name": "默认转账联动",
                "enabled": True,
                "chat_ids": list(base["chat_ids"]),
                "trigger_mode": base["trigger_mode"],
                "trigger_texts": list(base["trigger_texts"]),
                "module_start_keywords": list(base["module_start_keywords"]),
                "receiver_text": base["receiver_text"],
                "amount": base["amount"],
                "amount_match_mode": base["amount_match_mode"],
                "action": base["action"],
                "math_prize": base["math_prize"],
                "module_key": base["module_key"],
                "module_action": base["module_action"],
                "module_prize": base["module_prize"],
                "module_start_text": base["module_start_text"],
                "open_commands": list(base["open_commands"]),
                "close_commands": list(base["close_commands"]),
                "status_commands": list(base["status_commands"]),
                "disabled_message": base["disabled_message"],
                "valid_seconds": base["valid_seconds"],
                "concurrency": base["concurrency"],
                "response_template": base["response_template"],
            }
        ]
    first_enabled = next((rule for rule in rules if rule.get("enabled", True)), rules[0])
    base["chat_ids"] = list(first_enabled.get("chat_ids") or base["chat_ids"])
    base["chat_id"] = base["chat_ids"][0] if base["chat_ids"] else None
    base["trigger_mode"] = first_enabled.get("trigger_mode") or "payment"
    base["trigger_texts"] = list(first_enabled.get("trigger_texts") or base["trigger_texts"])
    base["trigger_text"] = base["trigger_texts"][0] if base["trigger_texts"] else "转账成功"
    base["module_start_keywords"] = list(first_enabled.get("module_start_keywords") or [])
    base["receiver_text"] = first_enabled.get("receiver_text")
    base["amount"] = first_enabled.get("amount")
    base["amount_match_mode"] = first_enabled.get("amount_match_mode") or "eq"
    base["action"] = first_enabled.get("action") or "notice"
    base["math_prize"] = first_enabled.get("math_prize") or 123
    base["module_key"] = first_enabled.get("module_key")
    base["module_action"] = first_enabled.get("module_action")
    base["module_prize"] = first_enabled.get("module_prize")
    base["module_start_text"] = first_enabled.get("module_start_text")
    base["open_commands"] = list(first_enabled.get("open_commands") or [])
    base["close_commands"] = list(first_enabled.get("close_commands") or [])
    base["status_commands"] = list(first_enabled.get("status_commands") or [])
    base["disabled_message"] = first_enabled.get("disabled_message")
    base["valid_seconds"] = first_enabled.get("valid_seconds") or 600
    base["concurrency"] = first_enabled.get("concurrency") or "chat"
    base["response_template"] = first_enabled.get("response_template") or base["response_template"]
    base["rules"] = rules
    return base


def _normalize_string_list(raw: Any, *, default: list[str] | None = None) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for raw_item in raw:
            item = str(raw_item or "").strip()
            if item and "\n" not in item and item not in out:
                out.append(item)
    return out or list(default or [])


def normalize_interaction_rules(raw: Any) -> list[dict[str, Any]]:
    """归一化后续多模块规则列表；旧单规则字段仍保留在顶层。"""

    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw[:20]):
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("id") or f"rule-{index + 1}").strip()[:64] or f"rule-{index + 1}"
        if rule_id in seen_ids:
            rule_id = f"{rule_id}-{index + 1}"[:64]
        seen_ids.add(rule_id)
        name = str(item.get("name") or rule_id).strip()[:64] or rule_id
        chat_ids: list[int] = []
        raw_chat_ids = item.get("chat_ids")
        if isinstance(raw_chat_ids, list):
            for raw_chat_id in raw_chat_ids:
                try:
                    chat_id = int(raw_chat_id)
                except (TypeError, ValueError):
                    continue
                if chat_id not in chat_ids:
                    chat_ids.append(chat_id)
        triggers: list[str] = []
        raw_triggers = item.get("trigger_texts")
        if isinstance(raw_triggers, list):
            for raw_trigger in raw_triggers:
                trigger = str(raw_trigger or "").strip()
                if trigger and trigger not in triggers:
                    triggers.append(trigger)
        if not triggers:
            triggers = ["转账成功"]
        module_start_keywords = _normalize_string_list(item.get("module_start_keywords"))
        open_commands = _normalize_string_list(item.get("open_commands"))
        close_commands = _normalize_string_list(item.get("close_commands"))
        status_commands = _normalize_string_list(item.get("status_commands"))
        try:
            amount = int(item["amount"]) if item.get("amount") not in (None, "") else None
        except (TypeError, ValueError):
            amount = None
        amount_match_mode = str(item.get("amount_match_mode") or "eq").strip()
        if amount_match_mode not in VALID_AMOUNT_MATCH_MODES:
            amount_match_mode = "eq"
        trigger_mode = str(item.get("trigger_mode") or "payment").strip()
        if trigger_mode not in VALID_TRIGGER_MODES:
            trigger_mode = "payment"
        concurrency = str(item.get("concurrency") or "chat").strip()
        if concurrency not in VALID_CONCURRENCY:
            concurrency = "chat"
        try:
            valid_seconds = int(item.get("valid_seconds") or 600)
        except (TypeError, ValueError):
            valid_seconds = 600
        valid_seconds = min(max(valid_seconds, 30), 86400)
        try:
            math_prize = int(item.get("math_prize") or 123)
        except (TypeError, ValueError):
            math_prize = 123
        action = str(item.get("action") or "notice").strip()
        if action not in {"notice", "math10", "module"}:
            action = "notice"
        module_key = str(item.get("module_key") or "").strip() or None
        module_action = str(item.get("module_action") or "").strip() or None
        module_start_text = str(item.get("module_start_text") or "").strip() or None
        try:
            module_prize = int(item["module_prize"]) if item.get("module_prize") not in (None, "") else None
        except (TypeError, ValueError):
            module_prize = None
        response_template = str(item.get("response_template") or "").strip()
        if not response_template:
            response_template = default_transfer_notice_config()["response_template"]
        receiver_text = str(item.get("receiver_text") or "").strip() or None
        disabled_message = str(item.get("disabled_message") or "").strip() or None
        out.append(
            {
                "id": rule_id,
                "name": name,
                "enabled": bool(item.get("enabled", True)),
                "chat_ids": chat_ids,
                "trigger_mode": trigger_mode,
                "trigger_texts": triggers,
                "module_start_keywords": module_start_keywords,
                "receiver_text": receiver_text,
                "amount": amount if amount is None or amount > 0 else None,
                "amount_match_mode": amount_match_mode,
                "action": action,
                "math_prize": math_prize if math_prize > 0 else 123,
                "module_key": module_key,
                "module_action": module_action,
                "module_prize": module_prize if module_prize is None or module_prize > 0 else None,
                "module_start_text": module_start_text,
                "open_commands": open_commands,
                "close_commands": close_commands,
                "status_commands": status_commands,
                "disabled_message": disabled_message,
                "valid_seconds": valid_seconds,
                "concurrency": concurrency,
                "response_template": response_template,
            }
        )
    return out


async def get_transfer_notice_config(db: AsyncSession, aid: int) -> dict[str, Any]:
    await account_bot_service.ensure_account(db, aid)
    row = await db.get(SystemSetting, transfer_notice_setting_key(aid))
    data = normalize_transfer_notice_config(row.value if row is not None else None)
    data.pop("interaction_bot_token_enc", None)
    data.pop("transfer_bot_token_enc", None)
    data["interaction_bot_token"] = None
    data["transfer_bot_token"] = None
    return data


async def get_interaction_bot_config(db: AsyncSession, aid: int) -> dict[str, Any]:
    return await get_transfer_notice_config(db, aid)


async def update_transfer_notice_config(
    db: AsyncSession,
    aid: int,
    payload: Any,
) -> dict[str, Any]:
    await account_bot_service.ensure_account(db, aid)
    setting_key = transfer_notice_setting_key(aid)
    row = await db.get(SystemSetting, setting_key)
    current = normalize_transfer_notice_config(row.value if row is not None else None)
    incoming = dict(payload or {})
    if "rules" not in incoming:
        current.pop("rules", None)
    if incoming.get("clear_interaction_bot_token"):
        current["interaction_bot_token_enc"] = None
        current["interaction_bot_username"] = None
        current["interaction_bot_id"] = None
        current["interaction_last_update_id"] = None
        current["interaction_last_error"] = None
    interaction_token = str(incoming.get("interaction_bot_token") or "").strip()
    if interaction_token:
        management_row = await db.get(AccountBot, aid)
        if management_row is not None and account_bot_service._encrypted_token_matches_plain(  # noqa: SLF001
            management_row.bot_token_enc,
            interaction_token,
        ):
            raise account_bot_service._bad(  # noqa: SLF001
                "INTERACTION_BOT_TOKEN_CONFLICTS_WITH_ACCOUNT_BOT",
                "交互 Bot Token 不能和管理 Bot Token 使用同一个 Bot。请为 Bbot 创建独立 Bot。",
                422,
            )
        current["interaction_bot_token_enc"] = encrypt_str(interaction_token)
        current["interaction_last_update_id"] = None
        current["interaction_last_error"] = None
        try:
            me = await account_bot_service.get_me(interaction_token)
            username = me.get("username")
            bot_id = me.get("id")
            current["interaction_bot_username"] = username if isinstance(username, str) else None
            current["interaction_bot_id"] = int(bot_id) if bot_id is not None else None
        except Exception as exc:  # noqa: BLE001
            current["interaction_last_error"] = account_bot_service.sanitize_bot_error(exc, token=interaction_token)
    if incoming.get("clear_transfer_bot_token"):
        current["transfer_bot_token_enc"] = None
    token = str(incoming.get("transfer_bot_token") or "").strip()
    if token:
        current["transfer_bot_token_enc"] = encrypt_str(token)
    for transient_key in (
        "interaction_bot_token",
        "clear_interaction_bot_token",
        "has_interaction_bot_token",
        "interaction_bot_username",
        "interaction_bot_id",
        "interaction_running",
        "interaction_runtime_status",
        "interaction_last_update_id",
        "interaction_last_error",
        "transfer_bot_token",
        "clear_transfer_bot_token",
        "has_transfer_bot_token",
    ):
        incoming.pop(transient_key, None)
    data = normalize_transfer_notice_config({**current, **incoming})
    if row is None:
        row = SystemSetting(key=setting_key, value=data)
        db.add(row)
    else:
        row.value = data
    await db.flush()
    out = dict(data)
    out.pop("interaction_bot_token_enc", None)
    out.pop("transfer_bot_token_enc", None)
    out["interaction_bot_token"] = None
    out["transfer_bot_token"] = None
    return out


async def update_interaction_bot_config(
    db: AsyncSession,
    aid: int,
    payload: Any,
) -> dict[str, Any]:
    return await update_transfer_notice_config(db, aid, payload)


async def get_interaction_bot_token(db: AsyncSession, aid: int) -> str | None:
    row = await db.get(SystemSetting, transfer_notice_setting_key(aid))
    data = normalize_transfer_notice_config(row.value if row is not None else None)
    token_enc = data.get("interaction_bot_token_enc")
    if not token_enc:
        return None
    try:
        return decrypt_str(str(token_enc))
    except ValueError:
        return None


async def get_transfer_bot_token(db: AsyncSession, aid: int) -> str | None:
    row = await db.get(SystemSetting, transfer_notice_setting_key(aid))
    data = normalize_transfer_notice_config(row.value if row is not None else None)
    token_enc = data.get("transfer_bot_token_enc")
    if not token_enc:
        return None
    try:
        return decrypt_str(str(token_enc))
    except ValueError:
        return None
