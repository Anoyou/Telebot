"""0.120.0：交互 Bot inline 提问、AI 回答排版（引用→分割线→回答→分割线→署名）、原生分割线。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services import account_bot_service, inline_ai_ask
from app.services.interaction.guest_reply import GuestRateLimiter
from app.services.llm_format import PRESET_QUOTE, PRESET_SIMPLE
from app.services.render_profile import is_divider_line, normalize_markdown_dividers
from app.services.rich_template import render_template_rich_markdown, render_template_text_layout


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self.data[key] = value

    async def get(self, key: str) -> Any:
        return self.data.get(key)


USER_TEMPLATE = (
    "{?quoted}<blockquote expandable>{quoted}</blockquote>\n{/?}"
    "{?question}<blockquote>❓ {question}</blockquote>\n{/?}"
    "{answer}\n————————\n✦Magpie·Workbuddy/{model}✦"
)
ANSWER = "## 结论\n\n| 项 | 值 |\n|---|---|\n| a | 1 |\n\n———\n\n- 第一点\n- 第二点"


def _ctx(**extra: Any) -> dict[str, Any]:
    base = {
        "answer": ANSWER,
        "question": "怎么选",
        "quoted": "原消息",
        "model": "deepseek V4.1 Flash",
        "provider": "Any",
        "in_tokens": 1,
        "out_tokens": 2,
        "total_tokens": 3,
        "routing_note": "",
    }
    base.update(extra)
    return base


# ── 分割线 ──────────────────────────────────────────────


@pytest.mark.parametrize("line", ["---", "***", "- - -", "————————", "────", "━━━━━━━━", "═══"])
def test_divider_lines_detected(line: str) -> None:
    assert is_divider_line(line)


@pytest.mark.parametrize("line", ["--", "——", "a --- b", "|---|---|", "— 模型 · in 1"])
def test_non_divider_lines(line: str) -> None:
    assert not is_divider_line(line)


def test_normalize_dividers_avoids_setext_and_keeps_code() -> None:
    assert normalize_markdown_dividers("标题\n---\n内容") == "标题\n\n---\n\n内容"
    assert normalize_markdown_dividers("a\n────\nb") == "a\n\n---\n\nb"
    assert normalize_markdown_dividers("```\n————\n```") == "```\n————\n```"
    assert normalize_markdown_dividers("| a |\n|---|\n| 1 |") == "| a |\n|---|\n| 1 |"


# ── 富文本排版 ──────────────────────────────────────────


def test_rich_layout_quote_divider_answer_divider_signature() -> None:
    md = render_template_rich_markdown(USER_TEMPLATE, _ctx())
    blocks = md.split("\n\n")
    assert blocks[0] == "> 原消息"
    assert blocks[1] == "> ❓ 怎么选"
    assert blocks[2] == "---"
    assert blocks[3] == "## 结论"
    # 署名单独一段，紧跟在原生分割线后面；手打横线不再和署名挤在一行
    assert blocks[-2:] == ["---", "✦Magpie·Workbuddy/deepseek V4.1 Flash✦"]
    assert "————————" not in md
    # 回答里的表格保持 Markdown（不被 <blockquote> 包住），回答里的横线也是原生分割线
    assert "| a | 1 |" in md
    assert "———" not in md


def test_rich_layout_quote_preset_keeps_answer_whole_and_lines() -> None:
    md = render_template_rich_markdown(PRESET_QUOTE, _ctx(quoted=""))
    assert "<blockquote" not in md
    assert "| a | 1 |" in md
    assert md.endswith("deepseek V4.1 Flash · Any<br>In: 1 \\| Out: 2 \\| Total: 3")
    assert "━━━" not in md


def test_rich_layout_switches() -> None:
    md = render_template_rich_markdown(PRESET_SIMPLE, _ctx(), divider_after=False)
    assert not md.rstrip().endswith("---\n\n— deepseek V4.1 Flash · in 1 / out 2")
    text_md = render_template_rich_markdown(USER_TEMPLATE, _ctx(), native_divider=False)
    assert "\n---\n" not in text_md
    assert "━━━━━━━━━━━━" in text_md
    no_before = render_template_rich_markdown(USER_TEMPLATE, _ctx(), divider_before=False)
    assert no_before.split("\n\n")[2] == "## 结论"


def test_text_layout_inserts_text_divider_without_duplicates() -> None:
    body = render_template_text_layout(USER_TEMPLATE, _ctx(answer="第一行 <x>\n第二行"), escape_format="html")
    lines = body.split("\n")
    assert lines[:3] == ["<blockquote expandable>原消息</blockquote>", "<blockquote>❓ 怎么选</blockquote>", "━━━━━━━━━━━━"]
    assert "第一行 &lt;x&gt;" in lines
    # 模板自己的「————————」在署名前，不再补第二条
    assert lines[-2:] == ["————————", "✦Magpie·Workbuddy/deepseek V4.1 Flash✦"]
    simple = render_template_text_layout(PRESET_SIMPLE, _ctx(answer="答"), escape_format="html")
    assert simple.split("\n")[:2] == ["答", "━━━━━━━━━━━━"]
    off = render_template_text_layout(
        PRESET_SIMPLE, _ctx(answer="答"), escape_format="html", divider_before=False, divider_after=False
    )
    assert "━" not in off


# ── inline 提问 ─────────────────────────────────────────


def test_query_answer_variants() -> None:
    empty = inline_ai_ask.query_answer("  ", chat_type="supergroup", unavailable_reason=None)
    assert empty["results"][0]["id"] == inline_ai_ask.HELP_RESULT_ID
    blocked = inline_ai_ask.query_answer("问题", chat_type="group", unavailable_reason="guest_rate_limited")
    assert blocked["results"] == [] and blocked["cache_time"] == inline_ai_ask.UNAVAILABLE_CACHE_SECONDS
    ask = inline_ai_ask.query_answer("今天 <b>吃</b> 什么", chat_type="supergroup", unavailable_reason=None)
    result = ask["results"][0]
    assert result["title"] == "发送这个问题"
    assert result["description"] == "今天 <b>吃</b> 什么"
    assert inline_ai_ask.is_ask_result_id(result["id"]) and len(result["id"]) <= 64
    assert inline_ai_ask.chat_type_from_result_id(result["id"]) == "supergroup"
    assert "&lt;b&gt;" in result["input_message_content"]["message_text"]
    assert result["reply_markup"]["inline_keyboard"]


def test_synthetic_guest_update_routes_as_guest() -> None:
    from app.services.account_bot_runtime import _extract_incoming

    update = inline_ai_ask.synthetic_guest_update(
        update_id=7,
        from_user={"id": 42, "first_name": "Ann"},
        question="问题",
        inline_message_id="IMID",
        chat_type="supergroup",
    )
    incoming = _extract_incoming(1, "tok", update)
    assert incoming is not None
    assert incoming.event_type == "guest_message"
    assert incoming.guest_query_id == "tpask:IMID"
    assert incoming.text == "问题" and incoming.user_id == 42 and incoming.chat_id is None


@pytest.mark.asyncio
async def test_prepare_inline_edit_decorates_registered_message() -> None:
    redis = FakeRedis()
    await inline_ai_ask.register("IMID", "怎么 *选*", redis=redis)
    text, rich, markup = await inline_ai_ask.prepare_inline_edit(
        "IMID", rich_message={"markdown": "回答"}, redis=redis
    )
    assert rich == {"markdown": "> ❓ 怎么 \\*选\\*\n\n---\n\n回答"}
    assert markup == inline_ai_ask.REMOVE_KEYBOARD
    assert json.loads(redis.data[next(iter(redis.data))])["touched"] == 1
    # 未登记的 inline 消息原样
    other = await inline_ai_ask.prepare_inline_edit("OTHER", text="x", parse_mode="HTML", redis=redis)
    assert other == ("x", None, None)


@pytest.mark.asyncio
async def test_prepare_inline_edit_respects_hidden_question() -> None:
    redis = FakeRedis()
    await inline_ai_ask.register("IMID", "问题", show_question=False, redis=redis)
    text, _, _ = await inline_ai_ask.prepare_inline_edit("IMID", text="回答", parse_mode="HTML", redis=redis)
    assert text == "回答"


@pytest.mark.asyncio
async def test_answer_guest_query_for_inline_ask_edits_message(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(token: str, method: str, payload: dict[str, Any], **_: Any) -> Any:
        calls.append((method, payload))
        return True

    async def fake_lookup(inline_message_id: str | None, **_: Any) -> dict[str, Any] | None:
        return {"q": "问题", "touched": 0, "show": True, "divider": True} if inline_message_id == "IMID" else None

    async def fake_touched(*_: Any, **__: Any) -> None:
        return None

    async def fake_link(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(account_bot_service, "call_bot_api", fake_call)
    monkeypatch.setattr(inline_ai_ask, "lookup", fake_lookup)
    monkeypatch.setattr(inline_ai_ask, "mark_touched", fake_touched)
    monkeypatch.setattr("app.services.interaction.guest_reply.link_guest_inline_origin", fake_link)
    sent = await account_bot_service.answer_guest_query(
        "tok",
        "tpask:IMID",
        result={"type": "article", "id": "x", "input_message_content": {"rich_message": {"markdown": "**答**"}}},
    )
    assert sent == {"inline_message_id": "IMID"}
    method, payload = calls[0]
    assert method == "editMessageText" and payload["inline_message_id"] == "IMID"
    assert payload["rich_message"]["markdown"].startswith("> ❓ 问题\n\n---\n\n**答**")
    assert payload["reply_markup"] == inline_ai_ask.REMOVE_KEYBOARD


def test_rate_limiter_would_allow_does_not_consume() -> None:
    limiter = GuestRateLimiter(per_user=1, per_bot=10)
    assert limiter.would_allow(account_id=1, role="interaction_bot", user_id=5, now=0.0)
    assert limiter.would_allow(account_id=1, role="interaction_bot", user_id=5, now=0.0)
    assert limiter.allow(account_id=1, role="interaction_bot", user_id=5, now=0.0)
    assert not limiter.would_allow(account_id=1, role="interaction_bot", user_id=5, now=1.0)


@pytest.mark.asyncio
async def test_inline_query_fallback_answers_and_respects_switch(monkeypatch) -> None:
    from app.services import account_bot_runtime as rt
    from app.services import inline_ai_relay

    answered: list[dict[str, Any]] = []

    async def fake_answer(token: str, query_id: str, **kwargs: Any) -> None:
        answered.append(kwargs)

    pref = {"ai_inline_ask_enabled": True, "ai_inline_ask_show_question": True}

    async def fake_pref(**_: Any) -> dict[str, Any]:
        return dict(pref)

    async def available(_incoming: Any) -> None:
        return None

    monkeypatch.setattr(rt.account_bot_service, "answer_inline_query", fake_answer)
    monkeypatch.setattr(inline_ai_relay, "read_reply_preference", fake_pref)
    monkeypatch.setattr(rt, "_inline_ask_unavailable_reason", available)
    incoming = rt.Incoming(
        account_id=1, token="tok", update_id=1, user_id=5, chat_id=None, message_id=None, text="问",
        inline_query_id="Q1", inline_query_text="问", inline_chat_type="supergroup",
    )
    await rt._answer_inline_ask_query(incoming)
    assert answered[0]["results"][0]["title"] == "发送这个问题"
    assert incoming.inline_answered is True

    pref["ai_inline_ask_enabled"] = False
    answered.clear()
    await rt._answer_inline_ask_query(rt.Incoming(
        account_id=1, token="tok", update_id=2, user_id=5, chat_id=None, message_id=None, text="问",
        inline_query_id="Q2", inline_query_text="问",
    ))
    assert answered == []


@pytest.mark.asyncio
async def test_edit_rich_in_place_falls_back_silently(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.services import userbot_rich_message
    from app.worker import ai_runtime

    edits: list[Any] = []

    async def cap_ok(_client: Any) -> Any:
        return SimpleNamespace(available=True, error_code=None)

    async def cap_no(_client: Any) -> Any:
        return SimpleNamespace(available=False, error_code="premium_required")

    async def fake_edit(client: Any, peer: Any, message_id: int, rich: Any, **_: Any) -> dict[str, Any]:
        edits.append(rich)
        return {"message_id": message_id}

    event = SimpleNamespace(out=True, chat_id=-100, id=9)
    monkeypatch.setattr(userbot_rich_message, "edit_rich_message", fake_edit)
    monkeypatch.setattr(userbot_rich_message, "detect_rich_message_capability", cap_no)
    assert not await ai_runtime._maybe_edit_rich_in_place(object(), event, USER_TEMPLATE, _ctx(), account_id=1)
    assert edits == []
    monkeypatch.setattr(userbot_rich_message, "detect_rich_message_capability", cap_ok)
    assert await ai_runtime._maybe_edit_rich_in_place(object(), event, USER_TEMPLATE, _ctx(), account_id=1)
    assert edits[0]["markdown"].split("\n\n")[2] == "---"
