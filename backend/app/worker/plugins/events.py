"""TelePilot-level plugin event objects.

These dataclasses describe the framework-facing shape of Telegram activity.
MTProto events and Bot API updates are drivers; plugins should prefer these
stable TelePilot references when they do not need driver-specific details.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ActorRef:
    user_id: int | None = None
    display_name: str | None = None
    username: str | None = None


@dataclass(slots=True)
class MessageRef:
    chat_id: int | None = None
    message_id: int | None = None
    text: str = ""
    chat_type: str | None = None
    reply_to_message_id: int | None = None
    reply_to_text: str | None = None


@dataclass(slots=True)
class CallbackRef:
    id: str | None = None
    data: str | None = None
    message: MessageRef | None = None


@dataclass(slots=True)
class PaymentRef:
    status: str | None = None
    amount: int | None = None
    currency: str | None = None
    payer: ActorRef | None = None
    receiver: ActorRef | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionRef:
    key: str | None = None
    scope: str | None = None
    active: bool = True
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TelePilotEvent:
    type: str
    source_channel: str = "interaction_bot"
    account_id: int | None = None
    message: MessageRef = field(default_factory=MessageRef)
    sender: ActorRef = field(default_factory=ActorRef)
    actor: ActorRef = field(default_factory=ActorRef)
    source_actor: ActorRef = field(default_factory=ActorRef)
    player: ActorRef = field(default_factory=ActorRef)
    callback: CallbackRef | None = None
    payment: PaymentRef | None = None
    session: SessionRef | None = None
    trigger: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def event_from_interaction_payload(payload: dict[str, Any]) -> TelePilotEvent:
    """Build a TelePilot event from the current interaction payload envelope."""

    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    message_raw = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    chat_raw = payload.get("chat") if isinstance(payload.get("chat"), dict) else {}
    sender_raw = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    actor_raw = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
    source_actor_raw = payload.get("source_actor") if isinstance(payload.get("source_actor"), dict) else {}
    player_raw = payload.get("player") if isinstance(payload.get("player"), dict) else {}
    payment_raw = payload.get("payment") if isinstance(payload.get("payment"), dict) else None
    session_raw = payload.get("session") if isinstance(payload.get("session"), dict) else None
    trigger_raw = payload.get("trigger") if isinstance(payload.get("trigger"), dict) else {}
    reply_to = payload.get("reply_to") if isinstance(payload.get("reply_to"), dict) else {}

    message = MessageRef(
        chat_id=_int_or_none(message_raw.get("chat_id") or chat_raw.get("id") or source.get("chat_id") or payload.get("chat_id")),
        message_id=_int_or_none(message_raw.get("message_id") or source.get("message_id") or payload.get("message_id")),
        text=str(message_raw.get("text") or source.get("text") or payload.get("message_text") or ""),
        chat_type=str(chat_raw.get("type") or source.get("chat_type") or "") or None,
        reply_to_message_id=_int_or_none(
            message_raw.get("reply_to_message_id") or reply_to.get("message_id") or payload.get("reply_to_message_id")
        ),
        reply_to_text=str(reply_to.get("text") or payload.get("reply_to_text") or "") or None,
    )
    sender = _actor_from_payload(sender_raw, payload)
    actor = _actor_from_payload(actor_raw, payload)
    source_actor = _actor_from_payload(source_actor_raw, payload)
    player = _actor_from_payload(player_raw, {})
    callback_id = str(source.get("callback_query_id") or payload.get("callback_query_id") or "") or None
    callback_data = str(source.get("callback_data") or payload.get("callback_data") or "") or None
    callback = CallbackRef(id=callback_id, data=callback_data, message=message) if callback_id or callback_data else None

    payment = None
    if isinstance(payment_raw, dict):
        payer_raw = payment_raw.get("payer") if isinstance(payment_raw.get("payer"), dict) else {}
        receiver_raw = payment_raw.get("receiver") if isinstance(payment_raw.get("receiver"), dict) else {}
        payment = PaymentRef(
            status=str(payment_raw.get("status") or "") or None,
            amount=_int_or_none(payment_raw.get("amount")),
            currency=str(payment_raw.get("currency") or "") or None,
            payer=ActorRef(
                user_id=_int_or_none(payer_raw.get("user_id") or payment_raw.get("payer_user_id")),
                display_name=str(
                    payer_raw.get("display_name")
                    or payment_raw.get("payer_display_name")
                    or payment_raw.get("payer_name")
                    or ""
                ) or None,
                username=str(payer_raw.get("username") or "") or None,
            ),
            receiver=ActorRef(
                user_id=_int_or_none(receiver_raw.get("user_id") or payment_raw.get("receiver_user_id")),
                display_name=str(
                    receiver_raw.get("display_name")
                    or payment_raw.get("receiver_display_name")
                    or payment_raw.get("receiver_name")
                    or ""
                ) or None,
                username=str(receiver_raw.get("username") or "") or None,
            ),
            raw=dict(payment_raw),
        )

    session = None
    if isinstance(session_raw, dict):
        session = SessionRef(
            key=str(session_raw.get("key") or "") or None,
            scope=str(session_raw.get("scope") or "") or None,
            active=bool(session_raw.get("active", True)),
            data=dict(session_raw.get("data") or {}) if isinstance(session_raw.get("data"), dict) else {},
        )

    return TelePilotEvent(
        type=str(source.get("type") or payload.get("event_type") or "message"),
        source_channel=str(source.get("channel") or source.get("bot_role") or "interaction_bot"),
        account_id=_int_or_none(source.get("account_id") or payload.get("account_id")),
        message=message,
        sender=sender,
        actor=actor,
        source_actor=source_actor,
        player=player,
        callback=callback,
        payment=payment,
        session=session,
        trigger=dict(trigger_raw),
        raw=dict(payload),
    )


def _actor_from_payload(raw: dict[str, Any], fallback: dict[str, Any]) -> ActorRef:
    return ActorRef(
        user_id=_int_or_none(raw.get("user_id") or fallback.get("sender_user_id")),
        display_name=str(raw.get("display_name") or fallback.get("sender_name") or "") or None,
        username=str(raw.get("username") or fallback.get("sender_username") or "") or None,
    )


def _int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "ActorRef",
    "CallbackRef",
    "MessageRef",
    "PaymentRef",
    "SessionRef",
    "TelePilotEvent",
    "event_from_interaction_payload",
]
