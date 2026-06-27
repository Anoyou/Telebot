"""Interaction entry result contract guard."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

INTERACTION_SEND_VIA = {"interaction_bot", "userbot_reply", "bbot_notice"}
INTERACTION_BUTTON_CHANNELS = {"interaction_bot", "bbot_notice"}
TRUSTED_DEFAULT_SEND_VIA = ("interaction_bot", "userbot_reply", "bbot_notice")
INTERACTION_SEND_VIA_ALIASES = {
    "auto": "auto",
    "bot": "interaction_bot",
    "interaction": "interaction_bot",
    "interaction_bot": "interaction_bot",
    "bbot": "interaction_bot",
    "userbot": "userbot_reply",
    "userbot_reply": "userbot_reply",
    "user": "userbot_reply",
    "human": "userbot_reply",
    "notice": "bbot_notice",
    "notice_bot": "bbot_notice",
    "bbot_notice": "bbot_notice",
}

WriteLog = Callable[[str, str], Awaitable[None]]
EntryManifestResolver = Callable[[str | None, str | None], dict[str, Any] | None]


def action_send_via(action: dict[str, Any]) -> str:
    return action_send_via_options(action)[0]


def action_send_via_options(action: dict[str, Any]) -> list[str]:
    options = _raw_send_via_options(action)
    return options or ["interaction_bot"]


def send_via_selector_options(selector: Any) -> list[str]:
    if selector is None or selector == "":
        return []
    if isinstance(selector, dict):
        raw = (
            selector.get("prefer")
            or selector.get("channels")
            or selector.get("send_via")
            or selector.get("channel")
        )
        if raw is None or raw == "":
            return []
    return _normalize_send_via_selector(selector)


def apply_action_send_via_options(action: dict[str, Any], options: list[str]) -> dict[str, Any]:
    clean = _dedupe_valid_send_via(options) or ["interaction_bot"]
    action["send_via"] = clean[0]
    if len(clean) > 1:
        action["send_via_options"] = clean
    else:
        action.pop("send_via_options", None)
    action.pop("channel", None)
    action.pop("channel_selector", None)
    return action


def _raw_send_via_options(action: dict[str, Any]) -> list[str]:
    selector = action.get("channel_selector")
    if selector is None and "channel" in action:
        selector = action.get("channel")
    if selector is None:
        selector = action.get("send_via_options")
    if selector is None:
        selector = action.get("send_via")
    return _normalize_send_via_selector(selector)


def _normalize_send_via_selector(selector: Any) -> list[str]:
    if selector is None or selector == "":
        return ["interaction_bot"]
    if isinstance(selector, dict):
        fallback = bool(selector.get("fallback", True))
        raw = (
            selector.get("prefer")
            or selector.get("channels")
            or selector.get("send_via")
            or selector.get("channel")
        )
        options = _normalize_send_via_selector(raw)
        return options if fallback else options[:1]
    if isinstance(selector, str):
        key = selector.strip().lower()
        mapped = INTERACTION_SEND_VIA_ALIASES.get(key)
        if mapped == "auto":
            return list(TRUSTED_DEFAULT_SEND_VIA)
        if mapped:
            return [mapped]
        return []
    if isinstance(selector, (list, tuple, set)):
        options: list[str] = []
        for item in selector:
            options.extend(_normalize_send_via_selector(item))
        return _dedupe_valid_send_via(options)
    return []


def _dedupe_valid_send_via(options: list[str]) -> list[str]:
    out: list[str] = []
    for option in options:
        item = str(option or "").strip()
        if item in INTERACTION_SEND_VIA and item not in out:
            out.append(item)
    return out


async def guard_interaction_actions(
    *,
    rule: dict[str, Any],
    actions: list[dict[str, Any]],
    resolve_entry_manifest: EntryManifestResolver,
    write_log: Callable[..., Awaitable[None]],
    log_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply ``result_contract`` limits before actions reach delivery."""

    contract = _entry_result_contract(rule, resolve_entry_manifest)
    raw_actions = contract.get("actions")
    allowed_actions = (
        {str(item or "").strip() for item in raw_actions if str(item or "").strip()}
        if isinstance(raw_actions, list)
        else set()
    )
    raw_send_via = contract.get("send_via")
    raw_send_via_items = raw_send_via if isinstance(raw_send_via, list) else [raw_send_via]
    allowed_send_via = {
        option
        for item in raw_send_via_items
        for option in send_via_selector_options(item)
        if option in INTERACTION_SEND_VIA
    } or set(TRUSTED_DEFAULT_SEND_VIA)
    context = dict(log_context or {})
    guarded: list[dict[str, Any]] = []
    for raw_action in actions:
        if not isinstance(raw_action, dict):
            continue
        action = dict(raw_action)
        action_type = str(action.get("type") or "").strip()
        if allowed_actions and action_type not in allowed_actions:
            await write_log(
                "warn",
                f"interaction action blocked by result_contract.actions: {action_type}",
                action_type=action_type,
                allowed_actions=sorted(allowed_actions),
                **context,
            )
            continue
        if action_type in {"send_message", "send_photo", "send_file", "delete_message", "pin_message"}:
            requested_send_via = action_send_via_options(action)
            send_via_options = [item for item in requested_send_via if item in allowed_send_via]
            if not send_via_options:
                await write_log(
                    "warn",
                    f"interaction action blocked by result_contract.send_via: {requested_send_via}",
                    action_type=action_type,
                    send_via=requested_send_via,
                    allowed_send_via=sorted(allowed_send_via),
                    **context,
                )
                continue
            if "reply_markup" in action:
                button_channels = [item for item in send_via_options if item in INTERACTION_BUTTON_CHANNELS]
                if button_channels:
                    if button_channels != send_via_options:
                        await write_log(
                            "info",
                            "interaction action send_via narrowed for reply_markup",
                            action_type=action_type,
                            send_via=send_via_options,
                            narrowed_send_via=button_channels,
                            **context,
                        )
                    send_via_options = button_channels
                else:
                    send_via = send_via_options[0]
                    action.pop("reply_markup", None)
                    await write_log(
                        "info",
                        "interaction action reply_markup stripped for non-bot channel",
                        action_type=action_type,
                        send_via=send_via,
                        **context,
                    )
            apply_action_send_via_options(action, send_via_options)
            send_via = action["send_via"]
            if send_via not in INTERACTION_BUTTON_CHANNELS and "reply_markup" in action:
                action.pop("reply_markup", None)
                await write_log(
                    "info",
                    "interaction action reply_markup stripped for non-bot channel",
                    action_type=action_type,
                    send_via=send_via,
                    **context,
                )
        guarded.append(action)
    return guarded


def _entry_result_contract(
    rule: dict[str, Any],
    resolve_entry_manifest: EntryManifestResolver,
) -> dict[str, Any]:
    module_key = str(rule.get("module_key") or "").strip() or None
    entry_key = str(rule.get("module_action") or "").strip() or None
    entry = resolve_entry_manifest(module_key, entry_key)
    contract = entry.get("result_contract") if isinstance(entry, dict) else None
    return dict(contract) if isinstance(contract, dict) else {}


__all__ = [
    "INTERACTION_BUTTON_CHANNELS",
    "INTERACTION_SEND_VIA",
    "INTERACTION_SEND_VIA_ALIASES",
    "TRUSTED_DEFAULT_SEND_VIA",
    "action_send_via",
    "action_send_via_options",
    "apply_action_send_via_options",
    "guard_interaction_actions",
    "send_via_selector_options",
]
