"""Plugin event envelope helpers."""

from __future__ import annotations

from app.worker.plugins.events import event_from_interaction_payload


def test_event_from_interaction_payload_projects_standard_envelope() -> None:
    event = event_from_interaction_payload(
        {
            "source": {
                "type": "payment_confirmed",
                "channel": "interaction_bot",
                "account_id": 1,
                "chat_id": -100123,
                "message_id": 70,
                "update_id": 10,
            },
            "message": {
                "chat_id": -100123,
                "message_id": 70,
                "text": "转账成功",
                "reply_to_message_id": 66,
            },
            "chat": {"id": -100123, "type": "supergroup"},
            "sender": {"user_id": 456, "display_name": "TransferBot", "username": "transfer_bot"},
            "actor": {"user_id": 111, "display_name": "Alice", "username": "alice"},
            "source_actor": {"user_id": 456, "display_name": "TransferBot", "username": "transfer_bot"},
            "player": {"user_id": 111, "display_name": "Alice", "username": "alice"},
            "reply_to": {"message_id": 66, "text": "+10"},
            "payment": {
                "status": "confirmed",
                "amount": 10,
                "payer_user_id": 111,
                "payer_display_name": "Alice",
                "receiver_user_id": 222,
                "receiver_display_name": "Owner",
                "source_message_id": 70,
                "reply_to_message_id": 66,
            },
            "session": {"key": "session-key", "scope": "chat", "active": True, "data": {"round": 1}},
            "trigger": {"rule_id": "paid-game", "entry_key": "start"},
            "raw": {"update_id": 10},
        }
    )

    assert event.type == "payment_confirmed"
    assert event.source_channel == "interaction_bot"
    assert event.account_id == 1
    assert event.message.chat_id == -100123
    assert event.message.chat_type == "supergroup"
    assert event.message.message_id == 70
    assert event.message.text == "转账成功"
    assert event.message.reply_to_message_id == 66
    assert event.message.reply_to_text == "+10"
    assert event.sender.user_id == 456
    assert event.sender.display_name == "TransferBot"
    assert event.actor.user_id == 111
    assert event.actor.display_name == "Alice"
    assert event.source_actor.user_id == 456
    assert event.player.user_id == 111
    assert event.payment is not None
    assert event.payment.amount == 10
    assert event.payment.payer is not None
    assert event.payment.payer.user_id == 111
    assert event.payment.payer.display_name == "Alice"
    assert event.payment.receiver is not None
    assert event.payment.receiver.user_id == 222
    assert event.payment.receiver.display_name == "Owner"
    assert event.session is not None
    assert event.session.key == "session-key"
    assert event.session.data == {"round": 1}
    assert event.trigger["entry_key"] == "start"
    assert event.raw["source_actor"]["display_name"] == "TransferBot"
    assert event.raw["player"]["display_name"] == "Alice"
