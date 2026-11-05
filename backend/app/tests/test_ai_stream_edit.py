"""「在原消息上编辑」模式的 AI 分段流式输出（0.120.0-beta.2）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services import inline_ai_relay
from app.services.llm_protocol import ModelResponse, ModelStreamEvent, ModelUsage, TextContent
from app.worker import ai_runtime, ai_stream_edit


class FakeEvent:
    def __init__(self, *, chat_id: int = 123, out: bool = True, fail_with: Exception | None = None) -> None:
        self.chat_id = chat_id
        self.out = out
        self.id = 7
        self.message = SimpleNamespace(id=7)
        self.edits: list[tuple[str, Any]] = []
        self._fail_with = fail_with

    async def edit(self, text: str, parse_mode: Any = None) -> None:
        if self._fail_with is not None:
            exc, self._fail_with = self._fail_with, None
            raise exc
        self.edits.append((text, parse_mode))


def _provider(pid: int = 1) -> Any:
    return SimpleNamespace(id=pid, name=f"p{pid}", default_model="m-default", to_dict=lambda: {"name": f"p{pid}"})


def _final(text: str) -> ModelStreamEvent:
    return ModelStreamEvent(
        response=ModelResponse(
            model="m-real",
            content=(TextContent(text),),
            usage=ModelUsage(input_tokens=11, output_tokens=22, total_tokens=33),
            sources=({"url": "https://example.com", "title": "ex"},),
        )
    )


def _stream_fn(deltas: list[str], *, final: str | None = None, error_after: int | None = None, used: Any = None):
    calls: list[Any] = []

    async def fn(primary, providers, request, **kwargs):
        calls.append((request, kwargs))
        for index, delta in enumerate(deltas):
            if error_after is not None and index >= error_after:
                raise RuntimeError("boom")
            yield ModelStreamEvent(delta=delta), used or primary, False
        if error_after is not None and error_after >= len(deltas):
            raise RuntimeError("boom")
        yield _final(final if final is not None else "".join(deltas)), used or primary, False

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def _kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider_dto": _provider(),
        "provider_dtos": {},
        "system": "sys",
        "user_msg": "q",
        "override_model": None,
        "routed_model": "m-routed",
        "max_tokens": 256,
        "temperature": None,
        "reasoning_effort": None,
        "timeout_seconds": 30,
        "web_search": False,
        "web_search_context_size": None,
        "account_id": 1,
        "triggered_by_account_id": None,
        "source": "command:ai",
        "fallback_provider_id": None,
        "matched_tag": None,
    }
    base.update(overrides)
    return base


def test_completed_paragraphs_only_returns_finished_ones() -> None:
    assert ai_stream_edit.completed_paragraphs("正在写第一段") == ""
    assert ai_stream_edit.completed_paragraphs("第一段\n\n第二段写到一半") == "第一段"
    assert ai_stream_edit.completed_paragraphs("一\n\n二\n\n三") == "一\n\n二"
    long_line = "- 项目\n" * 120
    assert ai_stream_edit.completed_paragraphs(long_line + "未完").endswith("- 项目")


def test_preview_html_escapes_and_marks_generating() -> None:
    out = ai_stream_edit.preview_html("a < b & c")
    assert out.startswith("a &lt; b &amp; c")
    assert "生成中" in out
    assert len(ai_stream_edit.preview_html("字" * 5000)) < 3700


def test_stream_interval_slower_in_groups() -> None:
    assert ai_stream_edit.stream_interval(-100123) > ai_stream_edit.stream_interval(42)


def _eligible(**overrides: Any) -> bool:
    kwargs: dict[str, Any] = {
        "pref": {"style": "normal", "ai_stream_edit": True},
        "event": FakeEvent(),
        "cfg": {},
        "command_mode": "chat",
        "inline_handle": None,
        "native_image_mode": False,
        "has_images": False,
        "has_self_image": False,
        "has_self_audio": False,
    }
    kwargs.update(overrides)
    return ai_stream_edit.stream_edit_eligible(**kwargs)


def test_eligibility_rules() -> None:
    assert _eligible()
    assert _eligible(pref={"style": "edit_rich", "ai_stream_edit": True})
    assert not _eligible(pref={"style": "normal"})  # 默认关
    assert not _eligible(pref={"style": "inline", "ai_stream_edit": True})
    assert not _eligible(cfg={"send_mode": "send_new"})
    assert not _eligible(has_images=True)
    assert not _eligible(native_image_mode=True)
    assert not _eligible(command_mode="image")
    assert not _eligible(event=FakeEvent(out=False))
    assert not _eligible(inline_handle=object())


def test_stream_switch_defaults_off() -> None:
    assert inline_ai_relay.AI_LAYOUT_SWITCHES["ai_stream_edit"] is False


@pytest.mark.asyncio
async def test_stream_edits_paragraphs_then_returns_result() -> None:
    event = FakeEvent()
    fn = _stream_fn(["第一段", "内容\n\n第二", "段\n\n结尾"])
    outcome = await ai_stream_edit.stream_answer_into_message(event, stream_fn=fn, **_kwargs())
    assert outcome.shown and not outcome.failed
    assert event.edits, "应该至少编辑一次已完成的段落"
    first_text, parse_mode = event.edits[0]
    assert parse_mode == "html"
    assert first_text.startswith("第一段内容") and "生成中" in first_text
    assert "结尾" not in first_text
    result = outcome.result
    assert result.text == "第一段内容\n\n第二段\n\n结尾"
    assert (result.model, result.input_tokens, result.output_tokens) == ("m-real", 11, 22)
    assert result.sources == [{"url": "https://example.com", "title": "ex"}]
    request, kwargs = fn.calls[0]
    assert request.stream is True and request.model == "m-routed"
    assert request.metadata["model_pinned"] is False
    assert request.metadata["_request_timeout_seconds"] == 30
    assert kwargs["source"] == "command:ai"


@pytest.mark.asyncio
async def test_pinned_model_when_override() -> None:
    fn = _stream_fn(["好"])
    await ai_stream_edit.stream_answer_into_message(FakeEvent(), stream_fn=fn, **_kwargs(override_model="m-x"))
    request, _ = fn.calls[0]
    assert request.model == "m-x" and request.metadata["model_pinned"] is True


@pytest.mark.asyncio
async def test_short_answer_without_paragraph_is_not_edited_midway() -> None:
    event = FakeEvent()
    outcome = await ai_stream_edit.stream_answer_into_message(event, stream_fn=_stream_fn(["一句话回答"]), **_kwargs())
    assert event.edits == []
    assert outcome.result.text == "一句话回答" and not outcome.shown


@pytest.mark.asyncio
async def test_failure_before_first_paragraph_falls_back_silently() -> None:
    event = FakeEvent()
    outcome = await ai_stream_edit.stream_answer_into_message(
        event, stream_fn=_stream_fn(["还没写完"], error_after=1), **_kwargs()
    )
    assert outcome.result is None and not outcome.shown and not outcome.failed
    assert event.edits == []


@pytest.mark.asyncio
async def test_failure_after_partial_output_reports_error() -> None:
    event = FakeEvent()
    outcome = await ai_stream_edit.stream_answer_into_message(
        event, stream_fn=_stream_fn(["第一段\n\n", "第二"], error_after=2), **_kwargs()
    )
    assert outcome.failed and outcome.result is None
    assert "AI 回答中断" in event.edits[-1][0]


@pytest.mark.asyncio
async def test_empty_answer_without_output_falls_back() -> None:
    outcome = await ai_stream_edit.stream_answer_into_message(
        FakeEvent(), stream_fn=_stream_fn([], final=""), **_kwargs()
    )
    assert outcome.result is None and not outcome.failed


@pytest.mark.asyncio
async def test_flood_wait_is_translated_for_throttler() -> None:
    class FloodWaitError(Exception):
        seconds = 5

    event = FakeEvent(fail_with=FloodWaitError("wait"))
    outcome = await ai_stream_edit.stream_answer_into_message(
        event, stream_fn=_stream_fn(["一段\n\n", "二段"]), **_kwargs()
    )
    # 第一次编辑被限流后不报错，流式照常拿到完整回答
    assert outcome.result.text == "一段\n\n二段"


@pytest.mark.asyncio
async def test_runtime_hook_respects_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []

    async def fake_pref(**_: Any) -> dict[str, Any]:
        return {"style": "normal", "ai_stream_edit": False}

    async def fake_stream(event, **kwargs):  # pragma: no cover - 不应被调用
        called.append(kwargs)
        return ai_stream_edit.StreamOutcome()

    monkeypatch.setattr(inline_ai_relay, "read_reply_preference", fake_pref)
    monkeypatch.setattr(ai_stream_edit, "stream_answer_into_message", fake_stream)
    out = await ai_runtime._maybe_stream_llm_into_message(**_hook_kwargs())
    assert out is None and called == []


def _hook_kwargs() -> dict[str, Any]:
    return {
        "event": FakeEvent(),
        "cfg": {},
        "tpl": {"name": "ai"},
        "account_id": 1,
        "triggered_by_account_id": None,
        "ctx_providers": {2: {"name": "backup"}},
        "provider_dto": _provider(1),
        "provider_dtos": {},
        "provider_dict": {"name": "p1"},
        "request_settings": SimpleNamespace(
            system="s", override_model=None, routed_model=None, max_tokens=100,
            temperature=None, reasoning_effort=None, timeout_seconds=None,
        ),
        "user_msg": "q",
        "command_mode": "chat",
        "inline_handle": None,
        "native_image_mode": False,
        "has_images": False,
        "has_self_image": False,
        "has_self_audio": False,
        "routing_mode": "manual",
        "provider_id": 1,
        "routing_matched_tag": None,
        "routing_note": None,
    }


@pytest.mark.asyncio
async def test_runtime_hook_builds_outcome_and_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_pref(**_: Any) -> dict[str, Any]:
        return {"style": "edit_rich", "ai_stream_edit": True}

    results: list[ai_stream_edit.StreamOutcome] = [
        ai_stream_edit.StreamOutcome(result=SimpleNamespace(text="hi"), used_provider=_provider(2), shown=True),
        ai_stream_edit.StreamOutcome(shown=True, failed=True),
        ai_stream_edit.StreamOutcome(),
    ]

    async def fake_stream(event, **kwargs):
        return results.pop(0)

    monkeypatch.setattr(inline_ai_relay, "read_reply_preference", fake_pref)
    monkeypatch.setattr(ai_stream_edit, "stream_answer_into_message", fake_stream)
    out = await ai_runtime._maybe_stream_llm_into_message(**_hook_kwargs())
    assert out.result.text == "hi"
    assert out.provider_dict == {"name": "backup"}
    assert "fallback → @p2" in out.routing_note
    assert await ai_runtime._maybe_stream_llm_into_message(**_hook_kwargs()) is ai_runtime._STREAM_ABORTED
    assert await ai_runtime._maybe_stream_llm_into_message(**_hook_kwargs()) is None
