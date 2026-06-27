"""Controlled delivery executor for interaction plugin actions."""

from __future__ import annotations

import base64
import binascii
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ...db.base import AsyncSessionLocal
from ...redis_client import get_redis
from .. import account_bot_service
from .contracts import action_send_via_options

log = logging.getLogger(__name__)

INTERACTION_SESSION_CONTROL_ACTIONS = {"end_session", "close_session", "no_session"}
INTERACTION_ACTION_SAVE_KEY_MAX_LENGTH = 200

WriteLog = Callable[..., Awaitable[None]]
RunWorkerAction = Callable[..., Awaitable[tuple[bool, str | None, dict[str, Any]]]]


@dataclass(slots=True)
class InteractionDeliveryExecutor:
    incoming: Any
    write_log: WriteLog
    run_worker_action: RunWorkerAction
    log_context: Callable[[Any], dict[str, Any]]
    trace_context: Callable[[dict[str, Any] | None], dict[str, Any]]
    get_redis_client: Callable[[], Any] = get_redis

    async def apply(
        self,
        actions: list[dict[str, Any]],
        *,
        context: dict[str, Any] | None = None,
        replace_message_id: int | None = None,
    ) -> None:
        for raw_action in actions[:10]:
            action = dict(raw_action)
            if context:
                action["context"] = dict(context)
            action_type = str(action.get("type") or "").strip()
            await self._record_settlement(action)
            if action_type in INTERACTION_SESSION_CONTROL_ACTIONS or action_type == "result":
                continue
            if action_type == "settlement":
                continue
            reply_to_message_id = _int_or_none(action.get("reply_to_message_id"))
            raw_reply_markup = action.get("reply_markup")
            reply_markup = raw_reply_markup if isinstance(raw_reply_markup, dict) else None
            if action_type == "answer_callback":
                await self._answer_callback(action)
                continue
            if action_type == "delete_message":
                await self._apply_delete_message(action)
                continue
            if action_type == "pin_message":
                await self._apply_pin_message(action)
                continue
            if action_type == "send_message":
                replace_message_id = await self._apply_send_message(
                    action,
                    reply_to_message_id=reply_to_message_id,
                    reply_markup=reply_markup,
                    replace_message_id=replace_message_id,
                )
                continue
            if action_type in {"send_photo", "send_file"}:
                replace_message_id = await self._apply_send_media(
                    action,
                    reply_to_message_id=reply_to_message_id,
                    replace_message_id=replace_message_id,
                )
                continue
            log.info("interaction action ignored: unsupported type=%s aid=%s", action_type, self.incoming.account_id)
            await self.write_log(
                self.incoming,
                "info",
                f"interaction action ignored: unsupported type={action_type}",
                action_type=action_type,
                action=action,
                **self.log_context(self.incoming),
            )

    async def delete_message(self, message_id: int | None, *, chat_id: int | None = None, send_via: str = "interaction_bot") -> None:
        target_chat_id = self._target_chat_id(chat_id)
        if target_chat_id is None or message_id is None:
            return
        token = await self._resolve_token(send_via)
        if not token:
            return
        try:
            await account_bot_service.delete_message(
                token,
                target_chat_id,
                message_id,
            )
        except Exception as exc:  # noqa: BLE001
            await self.write_log(
                self.incoming,
                "warn",
                "interaction placeholder delete failed",
                message_id=message_id,
                send_via=send_via,
                error=str(exc),
                **self.log_context(self.incoming),
            )

    async def send_message(
        self,
        text: str,
        *,
        chat_id: int | None = None,
        reply_to_message_id: int | None,
        send_via: str,
        edit_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        target_chat_id = self._target_chat_id(chat_id)
        if target_chat_id is None:
            return False, {}
        if send_via == "userbot_reply":
            ok, error, result = await self.run_worker_action(
                self.incoming,
                payload={
                    "action_type": "send_message",
                    "chat_id": target_chat_id,
                    "text": text,
                    "reply_to_message_id": reply_to_message_id,
                },
            )
            if not ok:
                return False, {"error": error}
            return True, result
        token = await self._resolve_token(send_via)
        if not token:
            await self.write_log(
                self.incoming,
                "warn",
                f"interaction action send_via={send_via} ignored: bot token unavailable",
                send_via=send_via,
                **self.log_context(self.incoming),
            )
            return False, {"error": "bot token unavailable"}
        if send_via == "interaction_bot" and edit_message_id is not None:
            try:
                result = await account_bot_service.edit_message(
                    token,
                    target_chat_id,
                    edit_message_id,
                    text,
                    reply_markup=reply_markup,
                )
                return True, result
            except Exception as exc:  # noqa: BLE001
                await self.write_log(
                    self.incoming,
                    "warn",
                    "interaction action edit placeholder failed, fallback send",
                    send_via=send_via,
                    edit_message_id=edit_message_id,
                    error=str(exc),
                    **self.log_context(self.incoming),
                )
        try:
            result = await account_bot_service.send_message(
                token,
                target_chat_id,
                text,
                reply_to_message_id=reply_to_message_id,
                reply_markup=reply_markup,
            )
        except Exception as exc:  # noqa: BLE001
            await self.write_log(
                self.incoming,
                "warn",
                f"interaction action send_via={send_via} failed",
                send_via=send_via,
                error=str(exc),
                **self.log_context(self.incoming),
            )
            return False, {"error": str(exc)}
        if send_via == "interaction_bot" and edit_message_id is not None:
            await self.delete_message(edit_message_id, chat_id=target_chat_id, send_via=send_via)
        return True, result

    async def send_photo(
        self,
        photo: bytes,
        *,
        chat_id: int | None = None,
        filename: str,
        caption: str | None,
        reply_to_message_id: int | None,
        send_via: str,
    ) -> tuple[bool, dict[str, Any]]:
        target_chat_id = self._target_chat_id(chat_id)
        if target_chat_id is None:
            return False, {}
        if send_via == "userbot_reply":
            ok, error, result = await self.run_worker_action(
                self.incoming,
                payload={
                    "action_type": "send_photo",
                    "chat_id": target_chat_id,
                    "photo_base64": base64.b64encode(photo).decode("ascii"),
                    "filename": filename,
                    "caption": caption,
                    "reply_to_message_id": reply_to_message_id,
                },
            )
            if not ok:
                return False, {"error": error}
            return True, result
        token = await self._resolve_token(send_via)
        if not token:
            await self.write_log(
                self.incoming,
                "warn",
                f"interaction media action send_via={send_via} ignored: bot token unavailable",
                send_via=send_via,
                **self.log_context(self.incoming),
            )
            return False, {"error": "bot token unavailable"}
        try:
            result = await account_bot_service.send_photo_bytes(
                token,
                target_chat_id,
                photo,
                filename=filename,
                caption=caption,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception as exc:  # noqa: BLE001
            await self.write_log(
                self.incoming,
                "warn",
                f"interaction media action send_via={send_via} failed",
                send_via=send_via,
                error=str(exc),
                **self.log_context(self.incoming),
            )
            return False, {"error": str(exc)}
        return True, result

    async def _apply_send_message(
        self,
        action: dict[str, Any],
        *,
        reply_to_message_id: int | None,
        reply_markup: dict[str, Any] | None,
        replace_message_id: int | None,
    ) -> int | None:
        text = str(action.get("text") or "").strip()
        if not text:
            return replace_message_id
        chat_id = _int_or_none(action.get("chat_id"))
        target_chat_id = self._target_chat_id(chat_id)
        placeholder_chat_id = self.incoming.chat_id
        send_via_options = action_send_via_options(action)
        original_replace_message_id = replace_message_id
        edit_message_id = _int_or_none(action.get("edit_message_id"))
        delete_message_id = None
        can_edit_placeholder = (
            bool(send_via_options)
            and send_via_options[0] == "interaction_bot"
            and target_chat_id == placeholder_chat_id
        )
        if edit_message_id is None and replace_message_id is not None and can_edit_placeholder:
            edit_message_id = replace_message_id
            replace_message_id = None
        elif edit_message_id is None and replace_message_id is not None:
            delete_message_id = replace_message_id
            replace_message_id = None
        ok, result, used_send_via = await self._try_send_message_options(
            text,
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            send_via_options=send_via_options,
            edit_message_id=edit_message_id,
            reply_markup=reply_markup,
        )
        if ok and delete_message_id is not None:
            await self.delete_message(delete_message_id, chat_id=placeholder_chat_id)
        if ok and delete_message_id is None and original_replace_message_id is not None and used_send_via != "interaction_bot":
            await self.delete_message(original_replace_message_id, chat_id=placeholder_chat_id)
        if ok and used_send_via == "interaction_bot" and action.get("pin"):
            msg_id = edit_message_id or delivery_message_id(result)
            await self._pin_message(msg_id, chat_id=chat_id, send_via=used_send_via)
        save_key = action_save_message_id_key(action.get("save_message_id_key"))
        if ok and save_key:
            msg_id = delivery_message_id(result)
            if msg_id is not None:
                await self.get_redis_client().set(save_key, str(msg_id), ex=7200)
        return replace_message_id

    async def _apply_send_media(
        self,
        action: dict[str, Any],
        *,
        reply_to_message_id: int | None,
        replace_message_id: int | None,
    ) -> int | None:
        raw_photo = str(action.get("photo_base64") or action.get("file_base64") or "").strip()
        if not raw_photo:
            return replace_message_id
        try:
            photo = base64.b64decode(raw_photo, validate=True)
        except (binascii.Error, ValueError):
            log.info("interaction action ignored: invalid base64 media aid=%s", self.incoming.account_id)
            return replace_message_id
        if not photo:
            return replace_message_id
        filename = str(action.get("filename") or "interaction.png").strip() or "interaction.png"
        caption = str(action.get("caption") or action.get("text") or "").strip() or None
        chat_id = _int_or_none(action.get("chat_id"))
        placeholder_chat_id = self.incoming.chat_id
        ok, _result, _used_send_via = await self._try_send_photo_options(
            photo,
            chat_id=chat_id,
            filename=filename,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
            send_via_options=action_send_via_options(action),
        )
        if ok and replace_message_id is not None:
            await self.delete_message(replace_message_id, chat_id=placeholder_chat_id)
            replace_message_id = None
        return replace_message_id

    async def _answer_callback(self, action: dict[str, Any]) -> None:
        callback_query_id = str(action.get("callback_query_id") or self.incoming.callback_id or "").strip()
        if not callback_query_id:
            return
        await account_bot_service.answer_callback(
            self.incoming.token,
            callback_query_id,
            text=str(action.get("text") or ""),
            show_alert=bool(action.get("show_alert")),
        )

    async def _apply_delete_message(self, action: dict[str, Any]) -> None:
        message_id = _int_or_none(action.get("message_id"))
        chat_id = _int_or_none(action.get("chat_id"))
        for send_via in action_send_via_options(action):
            if send_via not in {"interaction_bot", "bbot_notice"}:
                continue
            await self.delete_message(message_id, chat_id=chat_id, send_via=send_via)
            return

    async def _apply_pin_message(self, action: dict[str, Any]) -> None:
        message_id = _int_or_none(action.get("message_id"))
        chat_id = _int_or_none(action.get("chat_id"))
        for send_via in action_send_via_options(action):
            if send_via not in {"interaction_bot", "bbot_notice"}:
                continue
            await self._pin_message(message_id, chat_id=chat_id, send_via=send_via)
            return

    async def _pin_message(self, message_id: int | None, *, chat_id: int | None = None, send_via: str) -> None:
        target_chat_id = self._target_chat_id(chat_id)
        if send_via not in {"interaction_bot", "bbot_notice"} or message_id is None or target_chat_id is None:
            return
        token = await self._resolve_token(send_via)
        if not token:
            return
        try:
            await account_bot_service.call_bot_api(
                token,
                "pinChatMessage",
                {"chat_id": target_chat_id, "message_id": message_id},
            )
        except Exception:  # noqa: BLE001
            log.debug(
                "interaction action pin message failed aid=%s chat_id=%s message_id=%s",
                self.incoming.account_id,
                target_chat_id,
                message_id,
                exc_info=True,
            )

    async def _try_send_message_options(
        self,
        text: str,
        *,
        chat_id: int | None,
        reply_to_message_id: int | None,
        send_via_options: list[str],
        edit_message_id: int | None,
        reply_markup: dict[str, Any] | None,
    ) -> tuple[bool, dict[str, Any], str]:
        last_result: dict[str, Any] = {}
        for send_via in send_via_options:
            ok, result = await self.send_message(
                text,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                send_via=send_via,
                edit_message_id=edit_message_id if send_via == "interaction_bot" else None,
                reply_markup=reply_markup if send_via in {"interaction_bot", "bbot_notice"} else None,
            )
            if ok:
                return True, result, send_via
            last_result = result
            await self._log_send_via_fallback(send_via, result)
        return False, last_result, send_via_options[0] if send_via_options else "interaction_bot"

    async def _try_send_photo_options(
        self,
        photo: bytes,
        *,
        chat_id: int | None,
        filename: str,
        caption: str | None,
        reply_to_message_id: int | None,
        send_via_options: list[str],
    ) -> tuple[bool, dict[str, Any], str]:
        last_result: dict[str, Any] = {}
        for send_via in send_via_options:
            ok, result = await self.send_photo(
                photo,
                chat_id=chat_id,
                filename=filename,
                caption=caption,
                reply_to_message_id=reply_to_message_id,
                send_via=send_via,
            )
            if ok:
                return True, result, send_via
            last_result = result
            await self._log_send_via_fallback(send_via, result)
        return False, last_result, send_via_options[0] if send_via_options else "interaction_bot"

    async def _log_send_via_fallback(self, send_via: str, result: dict[str, Any]) -> None:
        await self.write_log(
            self.incoming,
            "warn",
            "interaction action send_via fallback",
            send_via=send_via,
            error=result.get("error") if isinstance(result, dict) else None,
            **self.log_context(self.incoming),
        )

    def _target_chat_id(self, chat_id: int | None = None) -> int | None:
        return chat_id if chat_id is not None else self.incoming.chat_id

    async def _resolve_token(self, send_via: str) -> str | None:
        if send_via == "interaction_bot":
            return self.incoming.token
        if send_via == "bbot_notice":
            async with AsyncSessionLocal() as db:
                return await account_bot_service.get_transfer_bot_token(db, self.incoming.account_id)
        return self.incoming.token

    async def _record_settlement(self, action: dict[str, Any]) -> None:
        settlement = action.get("settlement")
        if not isinstance(settlement, dict) and str(action.get("type") or "").strip() == "settlement":
            settlement = {k: v for k, v in action.items() if k != "type"}
        if not isinstance(settlement, dict):
            return
        await self.write_log(
            self.incoming,
            "info",
            "interaction settlement reported",
            action_type=str(action.get("type") or ""),
            settlement=settlement,
            **self.log_context(self.incoming),
            **self.trace_context(action.get("context")),
        )


def delivery_message_id(result: dict[str, Any] | Any) -> int | None:
    if not isinstance(result, dict):
        return None
    return _int_or_none(result.get("message_id"))


def action_save_message_id_key(raw: Any) -> str | None:
    key = str(raw or "").strip()
    if not key or len(key) > INTERACTION_ACTION_SAVE_KEY_MAX_LENGTH:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_.-]*", key):
        return None
    return key


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "INTERACTION_SESSION_CONTROL_ACTIONS",
    "InteractionDeliveryExecutor",
    "action_save_message_id_key",
    "delivery_message_id",
]
