"""富文本表格：默认带边框 + 隔行着色（0.106.0-beta.15），Bot API 10.3 紧凑表格（is_compact / <table compact>）透传、转换开关、回退。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from telethon.tl import functions, types

from app.services import account_bot_service
from app.services.rich_compact import (
    apply_compact_tables,
    apply_table_style_defaults,
    compact_html_tables,
    compact_markdown_tables,
    is_compact_unsupported_error,
    rich_message_has_compact_tables,
    strip_compact_tables,
    style_html_tables,
    style_markdown_tables,
)
from app.services.rich_message import RichMessageValidationError, normalize_rich_message
from app.services.telegram_text import parse_rich_message, serialize_rich_message
from app.services.userbot_rich_message import (
    evaluate_rich_message_capability,
)
from app.services.userbot_rich_message import (
    send_rich_message as userbot_send_rich_message,
)
from app.worker.plugins import loader as loader_mod
from app.worker.plugins.message_ops import BufferedMessageOps

PT_MARKDOWN = (
    "找到 2 个结果：\n"
    "| 标题 | 大小 | 发布时间 |\n"
    "|:---|---:|---|\n"
    "| **[Foo.2024.1080p](https://pt.example.com/details.php?id=1&hit=1)** | 12.3 GB | 2024-05-01 |\n"
    "| **[Bar `x|y`](https://pt.example.com/details.php?id=2)** | 700 MB | 2024-05-02 |\n"
    "\n"
    "以上按做种数排序。"
)


# -- 转换器 ---------------------------------------------------------------


def test_markdown_pipe_table_becomes_compact_html_block() -> None:
    converted = compact_markdown_tables(PT_MARKDOWN)

    # 平台默认带边框 + 隔行着色；紧凑只缩小内边距
    assert converted.startswith("找到 2 个结果：\n\n<table bordered striped compact>\n")
    assert '<tr><th align="left">标题</th><th align="right">大小</th><th>发布时间</th></tr>' in converted
    assert (
        '<td align="left"><b><a href="https://pt.example.com/details.php?id=1&amp;hit=1">Foo.2024.1080p</a></b></td>'
        in converted
    )
    # 代码片段里的竖线不拆列，内容按字面转义
    assert "<code>x|y</code>" in converted
    assert "</table>\n\n以上按做种数排序。" in converted
    assert "|:---|" not in converted


def test_markdown_fenced_code_and_inline_code_are_untouched() -> None:
    source = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```\n`<table>` 与 <table bordered>x</table>"
    converted = compact_markdown_tables(source)

    assert converted.startswith("```\n| a | b |\n|---|---|\n| 1 | 2 |\n```\n")
    assert "`<table>`" in converted
    assert "<table bordered compact>x</table>" in converted


def test_markdown_without_tables_is_returned_unchanged() -> None:
    source = "# 标题\n\n- a | b\n- 没有分隔行\n\n普通段落 a|b"
    assert compact_markdown_tables(source) == source


def test_inline_markdown_in_cells_is_converted_and_html_escaped() -> None:
    converted = compact_markdown_tables("| a | b |\n|---|---|\n| *斜* ~~删~~ \\| <sup>1</sup> | 1 < 2 & 3 |")

    assert "<td><i>斜</i> <s>删</s> | <sup>1</sup></td>" in converted
    assert "<td>1 &lt; 2 &amp; 3</td>" in converted


def test_markdown_table_keeps_borders_header_and_every_alignment() -> None:
    converted = compact_markdown_tables(
        "| 左 | 右 | 中 | 默认 |\n| :--- | ---: | :---: | --- |\n| a | b | c | d |\n| e | f | g | h |"
    )

    assert converted.splitlines()[0] == "<table bordered striped compact>"
    assert converted.count("<table") == 1 and converted.endswith("</table>")
    assert (
        '<tr><th align="left">左</th><th align="right">右</th><th align="center">中</th><th>默认</th></tr>'
        in converted
    )
    assert '<tr><td align="left">a</td><td align="right">b</td><td align="center">c</td><td>d</td></tr>' in converted
    assert '<tr><td align="left">e</td><td align="right">f</td><td align="center">g</td><td>h</td></tr>' in converted
    assert converted.count("<th") == 4  # 只有表头行用 th


def test_markdown_raw_html_table_keeps_author_border_choice() -> None:
    # 作者在 Markdown 里直接写的 <table>：只加 compact，不擅自加/去 bordered
    assert compact_markdown_tables("<table><tr><td>1</td></tr></table>") == "<table compact><tr><td>1</td></tr></table>"
    assert compact_markdown_tables("<table striped><tr><td>1</td></tr></table>") == (
        "<table striped compact><tr><td>1</td></tr></table>"
    )


def test_blocks_and_html_keep_caller_border_flags_exactly() -> None:
    blocks = {
        "blocks": [
            {"type": "table", "cells": []},
            {"type": "table", "cells": [], "is_bordered": True, "is_striped": True},
        ]
    }
    compact = apply_compact_tables(blocks)
    assert compact["blocks"][0] == {"type": "table", "cells": [], "is_compact": True}
    assert compact["blocks"][1] == {
        "type": "table",
        "cells": [],
        "is_bordered": True,
        "is_striped": True,
        "is_compact": True,
    }
    assert apply_compact_tables({"html": "<table><tr><td>1</td></tr></table>"})["html"] == (
        "<table compact><tr><td>1</td></tr></table>"
    )
    assert apply_compact_tables({"html": "<table bordered striped>x</table>"})["html"] == (
        "<table bordered striped compact>x</table>"
    )


def test_html_tables_get_compact_once() -> None:
    assert compact_html_tables("<table bordered striped><tr><td>1</td></tr></table>") == (
        "<table bordered striped compact><tr><td>1</td></tr></table>"
    )
    assert compact_html_tables("<table compact>x</table>") == "<table compact>x</table>"
    assert compact_html_tables("<p>无表格</p>") == "<p>无表格</p>"


def test_blocks_tables_get_is_compact_true_including_nested() -> None:
    rich = {
        "blocks": [
            {"type": "table", "is_bordered": True, "cells": [[{"text": {"type": "plain", "text": "a"}}]]},
            {"type": "details", "summary": "更多", "blocks": [{"type": "table", "cells": []}]},
            {"type": "paragraph", "text": "表格"},
        ]
    }

    compact = apply_compact_tables(rich)

    assert compact["blocks"][0]["is_compact"] is True
    assert compact["blocks"][0]["is_bordered"] is True
    assert compact["blocks"][1]["blocks"][0]["is_compact"] is True
    assert "is_compact" not in compact["blocks"][2]
    assert "is_compact" not in rich["blocks"][0]  # 不修改调用方对象
    assert rich_message_has_compact_tables(compact)
    assert not rich_message_has_compact_tables(rich)


def test_strip_compact_is_inverse_for_all_formats() -> None:
    blocks = {"blocks": [{"type": "table", "cells": [], "is_bordered": True}]}
    html = {"html": "<table bordered><tr><td>1</td></tr></table>"}
    markdown = {"markdown": PT_MARKDOWN}

    assert strip_compact_tables(apply_compact_tables(blocks)) == blocks
    assert strip_compact_tables(apply_compact_tables(html)) == html
    stripped = strip_compact_tables(apply_compact_tables(markdown))["markdown"]
    # 回退（去掉紧凑重发）仍保留边框
    assert "<table bordered striped>" in stripped and "compact" not in stripped
    assert not rich_message_has_compact_tables({"markdown": stripped})


def test_compact_never_emits_false() -> None:
    compact = apply_compact_tables({"blocks": [{"type": "table", "cells": [], "is_compact": False}]})
    assert compact["blocks"][0]["is_compact"] is True
    stripped = strip_compact_tables(compact)
    assert "is_compact" not in stripped["blocks"][0]
    assert "false" not in json.dumps(stripped)


# -- 显式透传与接收 ---------------------------------------------------------


def test_explicit_is_compact_block_passes_validation_unchanged() -> None:
    raw = {"blocks": [{"type": "table", "is_compact": True, "is_striped": True, "cells": [[{"text": "a"}]]}]}
    assert normalize_rich_message(raw) == raw


def test_is_compact_must_be_boolean() -> None:
    with pytest.raises(RichMessageValidationError, match="is_compact"):
        normalize_rich_message({"blocks": [{"type": "table", "is_compact": "yes", "cells": []}]})


def test_received_rich_block_table_keeps_is_compact() -> None:
    raw = {"blocks": [{"type": "table", "cells": [], "is_bordered": True, "is_compact": True}]}
    assert serialize_rich_message(parse_rich_message(raw)) == raw


# -- 插件 SDK / 动作归一化 -------------------------------------------------


@pytest.mark.asyncio
async def test_send_rich_compact_tables_flag_only_when_true() -> None:
    ops = BufferedMessageOps()
    assert ops.rich_table_compact is True

    default = await ops.send_rich(chat_id=1, markdown="| a |\n|---|\n| 1 |")
    compact = await ops.send_rich(chat_id=1, markdown="| a |\n|---|\n| 1 |", compact_tables=True)
    edited = await ops.edit_rich(chat_id=1, message_id=2, html="<table></table>", compact_tables=True)
    guest = await ops.answer_guest_query(rich_message={"markdown": "x"}, compact_tables=True)

    assert "compact_tables" not in default
    assert compact["compact_tables"] is True
    assert edited["compact_tables"] is True
    assert guest["compact_tables"] is True


def test_live_and_entry_facades_advertise_capability() -> None:
    assert loader_mod._LiveMessageOps.rich_table_compact is True  # noqa: SLF001
    assert loader_mod._InteractionEntryMessageOps.rich_table_compact is True  # noqa: SLF001
    assert loader_mod._LiveMessageOps.rich_table_default_style is True  # noqa: SLF001
    assert loader_mod._InteractionEntryMessageOps.rich_table_default_style is True  # noqa: SLF001
    assert BufferedMessageOps.rich_table_default_style is True


def test_normalize_action_applies_and_consumes_compact_tables() -> None:
    action = {
        "type": "send_rich_message",
        "chat_id": 1,
        "rich_message": {"markdown": PT_MARKDOWN},
        "compact_tables": True,
    }

    normalized = loader_mod._normalize_interaction_action(action)  # noqa: SLF001

    assert "compact_tables" not in normalized
    assert "<table bordered striped compact>" in normalized["rich_message"]["markdown"]
    assert action["rich_message"]["markdown"] == PT_MARKDOWN


def test_normalize_action_without_flag_is_byte_identical() -> None:
    action = {
        "type": "send_rich_message",
        "chat_id": 1,
        "rich_message": {"markdown": PT_MARKDOWN, "blocks": None},
        "send_via": ["interaction_bot"],
    }
    normalized = loader_mod._normalize_interaction_action(action)  # noqa: SLF001
    assert json.dumps(normalized["rich_message"], ensure_ascii=False) == json.dumps(
        action["rich_message"], ensure_ascii=False
    )

    disabled = loader_mod._normalize_interaction_action({**action, "compact_tables": False})  # noqa: SLF001
    assert "compact_tables" not in disabled
    assert disabled["rich_message"] == action["rich_message"]


def test_normalize_action_edit_and_guest_blocks() -> None:
    edit = loader_mod._normalize_interaction_action(  # noqa: SLF001
        {
            "type": "edit_message",
            "inline_message_id": "abc",
            "rich_message": {"blocks": [{"type": "table", "cells": []}]},
            "compact_tables": True,
        }
    )
    guest = loader_mod._normalize_interaction_action(  # noqa: SLF001
        {"type": "answer_guest_query", "rich_message": {"html": "<table>x</table>"}, "compact_tables": True}
    )
    assert edit["rich_message"]["blocks"][0]["is_compact"] is True
    assert guest["rich_message"]["html"] == "<table compact>x</table>"


# -- 回退 -----------------------------------------------------------------


def test_unsupported_error_classifier_only_matches_definite_rejections() -> None:
    assert is_compact_unsupported_error("Bad Request: can't parse rich message: unknown attribute compact")
    assert is_compact_unsupported_error("Bad Request: unsupported field is_compact")
    assert not is_compact_unsupported_error("Internal Server Error")
    assert not is_compact_unsupported_error("Too Many Requests: retry after 5")
    assert not is_compact_unsupported_error("Bad Request: message is not modified")
    assert not is_compact_unsupported_error("")


@pytest.mark.asyncio
async def test_send_rich_retries_once_without_compact_on_bad_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    async def call_bot_api(token, method, payload, **kwargs):  # noqa: ANN001, ANN003
        calls.append(json.loads(json.dumps(payload)))
        if len(calls) == 1:
            raise RuntimeError("Bad Request: can't parse rich message: unknown attribute compact")
        return {"message_id": 9}

    monkeypatch.setattr(account_bot_service, "call_bot_api", call_bot_api)

    result = await account_bot_service.send_rich_message(
        "123:abc", 1, apply_compact_tables({"html": "<table bordered><tr><td>1</td></tr></table>"})
    )

    assert result == {"message_id": 9}
    assert len(calls) == 2
    # 发送时补默认 striped；回退只去掉 compact，边框与隔行着色保留
    assert calls[0]["rich_message"]["html"].startswith("<table bordered compact striped>")
    assert calls[1]["rich_message"]["html"].startswith("<table bordered striped>")


@pytest.mark.asyncio
async def test_pt_markdown_table_keeps_borders_on_send_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """interaction-ai-chat 的 PT 结果表走 markdown 管道表格 + compact_tables：首发与回退都要有边框。"""

    calls: list[dict] = []

    async def call_bot_api(token, method, payload, **kwargs):  # noqa: ANN001, ANN003
        calls.append(json.loads(json.dumps(payload)))
        if len(calls) == 1:
            raise RuntimeError("Bad Request: can't parse rich message: unknown attribute compact")
        return {"message_id": 10}

    monkeypatch.setattr(account_bot_service, "call_bot_api", call_bot_api)
    normalized = loader_mod._normalize_interaction_action(  # noqa: SLF001
        {"type": "send_rich_message", "chat_id": 1, "rich_message": {"markdown": PT_MARKDOWN}, "compact_tables": True}
    )

    await account_bot_service.send_rich_message("123:abc", 1, normalized["rich_message"])

    assert len(calls) == 2
    first, retry = calls[0]["rich_message"]["markdown"], calls[1]["rich_message"]["markdown"]
    assert "<table bordered striped compact>" in first
    assert "<table bordered striped>" in retry and "compact" not in retry
    for body in (first, retry):
        assert '<th align="left">标题</th>' in body and '<th align="right">大小</th>' in body


@pytest.mark.asyncio
async def test_edit_rich_blocks_fallback_drops_is_compact(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    async def call_bot_api(token, method, payload, **kwargs):  # noqa: ANN001, ANN003
        calls.append(json.loads(json.dumps(payload)))
        if len(calls) == 1:
            raise RuntimeError("Bad Request: field \"is_compact\" is unknown")
        return {"message_id": 3}

    monkeypatch.setattr(account_bot_service, "call_bot_api", call_bot_api)

    await account_bot_service.edit_inline_rich_message(
        "123:abc", "inline-1", {"blocks": [{"type": "table", "cells": [], "is_compact": True}]}
    )

    assert calls[0]["rich_message"]["blocks"][0]["is_compact"] is True
    assert "is_compact" not in calls[1]["rich_message"]["blocks"][0]


@pytest.mark.asyncio
async def test_guest_answer_fallback_strips_nested_rich_message(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    async def call_bot_api(token, method, payload, **kwargs):  # noqa: ANN001, ANN003
        calls.append(json.loads(json.dumps(payload)))
        if len(calls) == 1:
            raise RuntimeError("Bad Request: can't parse rich message")
        return {"inline_message_id": "x"}

    monkeypatch.setattr(account_bot_service, "call_bot_api", call_bot_api)
    result = {
        "type": "article",
        "id": "1",
        "title": "t",
        "input_message_content": {"rich_message": {"html": "<table compact>x</table>"}},
    }

    await account_bot_service.answer_guest_query("123:abc", "q", result=result)

    assert calls[0]["result"]["input_message_content"]["rich_message"]["html"] == (
        "<table compact bordered striped>x</table>"
    )
    assert calls[1]["result"]["input_message_content"]["rich_message"]["html"] == "<table bordered striped>x</table>"
    # 调用方的候选结果不被改动
    assert result["input_message_content"]["rich_message"]["html"] == "<table compact>x</table>"


@pytest.mark.asyncio
async def test_no_retry_without_compact_or_on_non_definite_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    call = AsyncMock(side_effect=RuntimeError("Bad Request: can't parse rich message"))
    monkeypatch.setattr(account_bot_service, "call_bot_api", call)
    with pytest.raises(RuntimeError):
        await account_bot_service.send_rich_message("123:abc", 1, {"html": "<table>x</table>"})
    assert call.await_count == 1

    call = AsyncMock(side_effect=RuntimeError("Internal Server Error"))
    monkeypatch.setattr(account_bot_service, "call_bot_api", call)
    with pytest.raises(RuntimeError):
        await account_bot_service.send_rich_message("123:abc", 1, {"html": "<table compact>x</table>"})
    assert call.await_count == 1

    call = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
    monkeypatch.setattr(account_bot_service, "call_bot_api", call)
    with pytest.raises(httpx.ReadTimeout):
        await account_bot_service.send_rich_message("123:abc", 1, {"html": "<table compact>x</table>"})
    assert call.await_count == 1


@pytest.mark.asyncio
async def test_retry_failure_is_raised_and_attempted_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    call = AsyncMock(side_effect=RuntimeError("Bad Request: can't parse rich message"))
    monkeypatch.setattr(account_bot_service, "call_bot_api", call)
    with pytest.raises(RuntimeError):
        await account_bot_service.send_rich_message("123:abc", 1, {"html": "<table compact>x</table>"})
    assert call.await_count == 2


class _RpcBadRequest(Exception):
    code = 400


class _MockClient:
    def __init__(self, responses: list[object]) -> None:
        self.get_input_entity = AsyncMock(return_value=types.InputPeerChat(chat_id=99))
        self.requests: list[object] = []
        self._responses = list(responses)

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.mark.asyncio
async def test_userbot_rich_message_compact_fallback_on_rpc_400() -> None:
    capability = evaluate_rich_message_capability(
        layer=228, raw_types_available=True, is_premium=True, rich_message_posting=True
    )
    client = _MockClient([_RpcBadRequest("RICH_MESSAGE_INVALID"), SimpleNamespace(id=7)])

    await userbot_send_rich_message(client, -100, {"html": "<table compact>x</table>"}, capability=capability)

    assert len(client.requests) == 2
    first, second = client.requests
    assert isinstance(first, functions.messages.SendMessageRequest)
    assert first.rich_message.html == "<table compact bordered striped>x</table>"
    assert second.rich_message.html == "<table bordered striped>x</table>"


# -- 默认边框 + 隔行着色（0.106.0-beta.15）-----------------------------------


def test_style_markdown_pipe_table_bordered_striped_without_compact() -> None:
    converted = style_markdown_tables(PT_MARKDOWN)

    assert converted.startswith("找到 2 个结果：\n\n<table bordered striped>\n")
    assert "compact" not in converted
    assert '<tr><th align="left">标题</th><th align="right">大小</th><th>发布时间</th></tr>' in converted
    assert '<td align="right">12.3 GB</td>' in converted
    assert "</table>\n\n以上按做种数排序。" in converted


def test_style_markdown_raw_tags_and_fences() -> None:
    source = "```\n| a | b |\n|---|---|\n```\n`<table>` 与 <table>x</table>\n<table striped bordered>y</table>"
    converted = style_markdown_tables(source)

    assert converted.startswith("```\n| a | b |\n|---|---|\n```\n")
    assert "`<table>`" in converted
    assert "<table bordered striped>x</table>" in converted
    assert "<table striped bordered>y</table>" in converted  # 已写全：原样
    assert style_markdown_tables("# 无表格\n\na | b") == "# 无表格\n\na | b"


def test_style_html_fills_missing_and_honours_explicit_false() -> None:
    assert style_html_tables("<table><tr><td>1</td></tr></table>") == (
        "<table bordered striped><tr><td>1</td></tr></table>"
    )
    assert style_html_tables("<table bordered>x</table>") == "<table bordered striped>x</table>"
    assert style_html_tables("<table striped compact>x</table>") == "<table striped compact bordered>x</table>"
    assert style_html_tables("<table bordered striped compact>x</table>") == "<table bordered striped compact>x</table>"
    # HTML 布尔属性无法表达 false：bordered="false" / striped='0' 视为调用方显式关闭，发送前去掉
    assert style_html_tables('<table bordered="false">x</table>') == "<table striped>x</table>"
    assert style_html_tables("<table striped='0' compact>x</table>") == "<table compact bordered>x</table>"
    assert style_html_tables('<table bordered="no" striped=off>x</table>') == "<table>x</table>"
    assert style_html_tables('<table bordered="true">x</table>') == '<table bordered="true" striped>x</table>'
    # 属性值里的同名单词、data-* 属性不算显式指定
    assert style_html_tables('<table class="bordered" data-striped="1">x</table>') == (
        '<table class="bordered" data-striped="1" bordered striped>x</table>'
    )
    assert style_html_tables("<table/>") == "<table bordered striped />"
    assert style_html_tables("<p>无表格</p>") == "<p>无表格</p>"


def test_style_blocks_fill_missing_keep_explicit_values() -> None:
    rich = {
        "blocks": [
            {"type": "table", "cells": []},
            {"type": "table", "cells": [], "is_bordered": False, "is_striped": True},
            {"type": "details", "summary": "更多", "blocks": [{"type": "table", "cells": [], "is_striped": False}]},
            {"type": "paragraph", "text": "表格"},
        ]
    }

    styled = apply_table_style_defaults(rich)

    assert styled["blocks"][0] == {"type": "table", "cells": [], "is_bordered": True, "is_striped": True}
    assert styled["blocks"][1] == {"type": "table", "cells": [], "is_bordered": False, "is_striped": True}
    assert styled["blocks"][2]["blocks"][0] == {
        "type": "table",
        "cells": [],
        "is_striped": False,
        "is_bordered": True,
    }
    assert styled["blocks"][3] == {"type": "paragraph", "text": "表格"}
    assert rich["blocks"][0] == {"type": "table", "cells": []}  # 不修改调用方对象
    assert normalize_rich_message(styled) == styled  # 补出的字段通过本地校验


def test_style_defaults_idempotent_and_compose_with_compact() -> None:
    for rich in (
        {"markdown": PT_MARKDOWN},
        {"html": "<table>x</table>"},
        {"blocks": [{"type": "table", "cells": []}]},
    ):
        once = apply_table_style_defaults(rich)
        assert apply_table_style_defaults(once) == once
        compact = apply_table_style_defaults(apply_compact_tables(rich))
        assert apply_table_style_defaults(compact) == compact
        assert rich_message_has_compact_tables(compact)
        dumped = json.dumps(compact, ensure_ascii=False)
        assert ("bordered" in dumped) and ("striped" in dumped)
    assert apply_table_style_defaults({"markdown": "纯文本"}) == {"markdown": "纯文本"}
    assert apply_table_style_defaults(None) is None


def test_style_markdown_skipped_when_conversion_exceeds_text_limit() -> None:
    row = "| " + "x" * 40 + " | " + "y" * 40 + " |\n"
    source = "| a | b |\n|---|---|\n" + row * 360  # ≈31.7k 字符，转 HTML 后超限
    assert len(source) <= 32_768
    assert apply_table_style_defaults({"markdown": source}) == {"markdown": source}


@pytest.mark.asyncio
async def test_bot_api_exits_style_tables_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict]] = []

    async def call_bot_api(token, method, payload, **kwargs):  # noqa: ANN001, ANN003
        calls.append((method, json.loads(json.dumps(payload))))
        return {"message_id": 1, "inline_message_id": "x"}

    monkeypatch.setattr(account_bot_service, "call_bot_api", call_bot_api)
    md = {"markdown": "| a | b |\n|:-:|--:|\n| 1 | 2 |"}

    await account_bot_service.send_rich_message("123:abc", 1, md)
    await account_bot_service.send_rich_message_draft("123:abc", 1, 5, md)
    await account_bot_service.edit_rich_message("123:abc", 1, 2, {"html": "<table>x</table>"})
    await account_bot_service.edit_inline_rich_message(
        "123:abc", "inline-1", {"blocks": [{"type": "table", "cells": [], "is_striped": False}]}
    )
    guest_result = {
        "type": "article",
        "id": "1",
        "title": "t",
        "input_message_content": {"rich_message": {"html": '<table bordered="false">x</table>'}},
    }
    await account_bot_service.answer_guest_query("123:abc", "q", result=guest_result)

    methods = [method for method, _ in calls]
    assert methods == ["sendRichMessage", "sendRichMessageDraft", "editMessageText", "editMessageText", "answerGuestQuery"]
    for _, payload in calls[:2]:
        body = payload["rich_message"]["markdown"]
        assert body.startswith("<table bordered striped>\n")
        assert '<th align="center">a</th><th align="right">b</th>' in body
    assert calls[2][1]["rich_message"]["html"] == "<table bordered striped>x</table>"
    assert calls[3][1]["rich_message"]["blocks"][0] == {
        "type": "table",
        "cells": [],
        "is_striped": False,
        "is_bordered": True,
    }
    assert calls[4][1]["result"]["input_message_content"]["rich_message"]["html"] == "<table striped>x</table>"
    # 调用方对象不被改动
    assert md == {"markdown": "| a | b |\n|:-:|--:|\n| 1 | 2 |"}
    assert guest_result["input_message_content"]["rich_message"]["html"] == '<table bordered="false">x</table>'


@pytest.mark.asyncio
async def test_plugin_action_without_compact_gets_default_style_on_send(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    async def call_bot_api(token, method, payload, **kwargs):  # noqa: ANN001, ANN003
        calls.append(json.loads(json.dumps(payload)))
        return {"message_id": 1}

    monkeypatch.setattr(account_bot_service, "call_bot_api", call_bot_api)
    normalized = loader_mod._normalize_interaction_action(  # noqa: SLF001
        {"type": "send_rich_message", "chat_id": 1, "rich_message": {"markdown": PT_MARKDOWN}}
    )
    assert normalized["rich_message"] == {"markdown": PT_MARKDOWN}  # 归一化层不改，发送出口统一补

    await account_bot_service.send_rich_message("123:abc", 1, normalized["rich_message"])

    body = calls[0]["rich_message"]["markdown"]
    assert "<table bordered striped>" in body and "compact" not in body
    assert '<th align="left">标题</th>' in body


@pytest.mark.asyncio
async def test_styled_but_not_compact_rejection_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """bordered/striped 与 Rich Message 同在 Bot API 10.1 引入：没有紧凑时被拒不做降级重发。"""

    call = AsyncMock(side_effect=RuntimeError("Bad Request: can't parse rich message"))
    monkeypatch.setattr(account_bot_service, "call_bot_api", call)
    with pytest.raises(RuntimeError):
        await account_bot_service.send_rich_message("123:abc", 1, {"markdown": PT_MARKDOWN})
    assert call.await_count == 1
    sent = call.await_args.args[2]["rich_message"]["markdown"]
    assert "<table bordered striped>" in sent


@pytest.mark.asyncio
async def test_userbot_exits_style_tables_by_default() -> None:
    capability = evaluate_rich_message_capability(
        layer=228, raw_types_available=True, is_premium=True, rich_message_posting=True
    )
    client = _MockClient([SimpleNamespace(id=7), SimpleNamespace(id=8)])

    await userbot_send_rich_message(
        client, -100, {"markdown": "| a |\n|---|\n| 1 |"}, capability=capability
    )
    await userbot_send_rich_message(
        client, -100, {"html": "<table striped=\"false\">x</table>"}, capability=capability
    )

    first, second = client.requests
    assert first.rich_message.markdown.startswith("<table bordered striped>\n<tr><th>a</th></tr>")
    assert second.rich_message.html == "<table bordered>x</table>"
