"""计算 golden 语料在当前实现下的输出（生成 fixture 与测试共用同一份计算逻辑）。

输出覆盖出站路径上所有表格样式处理点：

- ``action``：插件动作规范化 + 出站管线（compact_tables / render_profile），即 worker 交给投递层的动作；
- ``bot_payload``：交互 Bot 出口 ``_call_rich_bot_api`` 实际发给 Telegram 的 payload；
- ``bot_retry_payload``：旧 Bot API 拒绝紧凑表格后降级重发的 payload；
- ``userbot``：Userbot Rich Message 中立输入（``_build_neutral_input``）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from typing import Any

from .rich_render_corpus import ACTION_VARIANTS, CORPUS


def _normalize_action(raw: dict[str, Any]) -> dict[str, Any]:
    from ...worker.plugins import loader

    finalize = getattr(loader, "_finalize_actions_for_golden", None)
    if finalize is not None:
        return asyncio.run(finalize([raw]))[0]
    return loader._normalize_interaction_action(raw)


def _bot_payloads(rich_message: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    from ...services import account_bot_service

    sent: list[dict[str, Any]] = []

    async def fake_call(token: str, method: str, payload: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        sent.append(deepcopy(payload))
        if len(sent) == 1:
            raise RuntimeError("Bad Request: rich message table compact unsupported")
        return {"ok": True}

    original = account_bot_service.call_bot_api
    account_bot_service.call_bot_api = fake_call  # type: ignore[assignment]
    try:
        try:
            asyncio.run(
                account_bot_service._call_rich_bot_api("t", "sendRichMessage", {"chat_id": 1, "rich_message": rich_message})
            )
        except RuntimeError:
            pass
    finally:
        account_bot_service.call_bot_api = original  # type: ignore[assignment]
    return sent[0], (sent[1] if len(sent) > 1 else None)


def _userbot(rich_message: dict[str, Any]) -> Any:
    from ...services import userbot_rich_message

    try:
        return userbot_rich_message._build_neutral_input(rich_message).to_dict()
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}


def compute_outputs() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, rich_message in CORPUS.items():
        for variant, base in ACTION_VARIANTS.items():
            raw = dict(base)
            raw["rich_message"] = deepcopy(rich_message)
            action = _normalize_action(raw)
            action_rm = action.get("rich_message")
            first, retry = _bot_payloads(deepcopy(action_rm))
            out[f"{name}::{variant}"] = {
                "action_rich_message": action_rm,
                "action_keys": sorted(k for k in action if not k.startswith("_tp")),
                "bot_payload": first,
                "bot_retry_payload": retry,
                "userbot": _userbot(deepcopy(action_rm)),
            }
    return _digest_long(json.loads(json.dumps(out, ensure_ascii=False, sort_keys=True)))


def _digest_long(value: Any) -> Any:
    """超过 2000 字的字符串记为 ``sha256:<hex>:<长度>``，fixture 保持小巧且仍逐字节比对。"""

    if isinstance(value, str) and len(value) > 2000:
        return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}:{len(value)}"
    if isinstance(value, list):
        return [_digest_long(item) for item in value]
    if isinstance(value, dict):
        return {key: _digest_long(item) for key, item in value.items()}
    return value
