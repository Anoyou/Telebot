"""消息格式渲染器测试。"""
from __future__ import annotations

import pytest

from app.services.llm_format import (
    DEFAULT_TEMPLATE,
    PRESET_MINIMAL,
    PRESET_QUOTE,
    PRESET_SIMPLE,
    PRESET_TRANSLATE,
    _escape_html,
    _escape_mdv2,
    _split_first_n_lines,
    render_output,
)

# ════════════════════════════════════════════════════════════
# 1) HTML 转义（默认模式）
# ════════════════════════════════════════════════════════════


def test_escape_html_specials() -> None:
    """HTML 模式只转义 & < > 三个字符；其它 markdown 字符不动。"""
    assert _escape_html("a&b<c>d") == "a&amp;b&lt;c&gt;d"


def test_escape_html_passthrough_markdown_chars() -> None:
    """``_`` ``*`` 等在 HTML 模式下不应被转义（不是 HTML 特殊字符）。"""
    assert _escape_html("hello_world *bold*") == "hello_world *bold*"


def test_escape_html_chinese() -> None:
    assert _escape_html("你好 <b>世界</b>") == "你好 &lt;b&gt;世界&lt;/b&gt;"


def test_escape_html_empty() -> None:
    assert _escape_html("") == ""


# ════════════════════════════════════════════════════════════
# 1b) MDV2 转义（保留可用，虽然 telethon 1.36 不接受 markdownv2 字符串）
# ════════════════════════════════════════════════════════════


def test_escape_mdv2_specials() -> None:
    raw = "Hello *world* (yes) [link] _under_ #tag + 1=1 ! . > | ~ ` { } -"
    out = _escape_mdv2(raw)
    for ch in "_*[]()~`>#+-=|{}.!":
        assert f"\\{ch}" in out, f"{ch} 没被转义"


# ════════════════════════════════════════════════════════════
# 2) 切前 N 行
# ════════════════════════════════════════════════════════════


def test_split_lines_short_returns_all() -> None:
    head, rest = _split_first_n_lines("only one line", 2)
    assert head == "only one line"
    assert rest == ""


def test_split_lines_at_threshold_no_split() -> None:
    """阈值是 n*2——4 行（=2*2）不切，全进 head；折叠块不出现。"""
    head, rest = _split_first_n_lines("a\nb\nc\nd", 2)
    assert head == "a\nb\nc\nd"
    assert rest == ""


def test_split_lines_above_threshold_splits() -> None:
    """5 行（>2*2）才切，前 2 行 head + 剩余 rest。"""
    head, rest = _split_first_n_lines("a\nb\nc\nd\ne", 2)
    assert head == "a\nb"
    assert rest == "c\nd\ne"


def test_split_lines_empty() -> None:
    assert _split_first_n_lines("", 2) == ("", "")


# ════════════════════════════════════════════════════════════
# 3) render_output 占位符
# ════════════════════════════════════════════════════════════


def test_render_simple_template_basic() -> None:
    out = render_output(
        PRESET_SIMPLE,
        {
            "answer": "hello",
            "model": "gpt-x",
            "in_tokens": 10,
            "out_tokens": 20,
            "routing_note": "",
        },
        escape_format=None,
    )
    assert "hello" in out
    assert "gpt-x" in out
    assert "in 10 / out 20" in out


def test_render_unknown_placeholder_kept_empty() -> None:
    out = render_output("a={a} b={b}", {"a": "1"}, escape_format=None)
    assert "a=1" in out
    assert "b=" in out


def test_render_default_template_is_simple() -> None:
    """DEFAULT_TEMPLATE 应该等于 PRESET_SIMPLE。"""
    assert DEFAULT_TEMPLATE == PRESET_SIMPLE


# ════════════════════════════════════════════════════════════
# 4) 条件块
# ════════════════════════════════════════════════════════════


def test_render_conditional_block_truthy() -> None:
    out = render_output(
        "x{?routing_note}::{routing_note}{/?}y",
        {"routing_note": "auto · code"},
        escape_format=None,
    )
    assert out == "x::auto · codey"


def test_render_conditional_block_falsy() -> None:
    out = render_output(
        "x{?routing_note}::{routing_note}{/?}y",
        {"routing_note": ""},
        escape_format=None,
    )
    assert out == "xy"


def test_render_conditional_zero_int_is_falsy() -> None:
    out = render_output("a{?n}!{/?}b", {"n": 0}, escape_format=None)
    assert out == "ab"
    out2 = render_output("a{?n}!{/?}b", {"n": None}, escape_format=None)
    assert out2 == "ab"


def test_render_quote_preset_omits_quote_when_empty() -> None:
    """引用风：quoted 为空时不该出现 <blockquote> 那一段。"""
    out = render_output(
        PRESET_QUOTE,
        {
            "quoted": "",
            "answer": "短回答",
            "model": "x",
            "provider": "p",
            "in_tokens": 1,
            "out_tokens": 2,
            "total_tokens": 3,
            "routing_note": "",
        },
        escape_format="html",
    )
    # 不包含被引用块
    assert "<blockquote>" not in out
    # 但其它 HTML 标签应该都在
    assert "<b>✨ AI 回答</b>" in out


def test_render_quote_preset_with_quoted_short() -> None:
    """引用风：双 blockquote——quoted 一段、question 一段，独立 expandable。"""
    out = render_output(
        PRESET_QUOTE,
        {
            "quoted": "原文",
            "question": "问题",
            "answer": "回答",
            "model": "x",
            "provider": "p",
            "in_tokens": 1,
            "out_tokens": 2,
            "total_tokens": 3,
            "routing_note": "",
        },
        escape_format="html",
    )
    # 两段独立的 blockquote
    assert out.startswith("<blockquote expandable>原文</blockquote>")
    assert "<blockquote expandable>问题</blockquote>" in out
    assert "<b>✨ AI 回答</b>" in out


def test_render_quote_preset_with_quoted_long() -> None:
    """引用风：长 quoted 也只放进同一个 expandable（TG 自己折叠），不切分。"""
    out = render_output(
        PRESET_QUOTE,
        {
            "quoted": "L1\nL2\nL3\nL4\nL5",
            "question": "请解释",
            "answer": "回答",
            "model": "x",
            "provider": "p",
            "in_tokens": 1,
            "out_tokens": 2,
            "total_tokens": 3,
            "routing_note": "",
        },
        escape_format="html",
    )
    assert out.startswith(
        "<blockquote expandable>L1\nL2\nL3\nL4\nL5</blockquote>"
    )
    assert "<blockquote expandable>请解释</blockquote>" in out


def test_render_quote_preset_question_only() -> None:
    """引用风：没 quoted（用户没回复消息）→ 只渲染 question 那段 blockquote。"""
    out = render_output(
        PRESET_QUOTE,
        {
            "quoted": "",
            "question": "Python 怎么学",
            "answer": "回答",
            "model": "x",
            "provider": "p",
            "in_tokens": 1,
            "out_tokens": 2,
            "total_tokens": 3,
            "routing_note": "",
        },
        escape_format="html",
    )
    # 只有一个 blockquote（question）
    assert out.startswith("<blockquote expandable>Python 怎么学</blockquote>")
    assert "<b>✨ AI 回答</b>" in out


def test_render_quote_preset_quoted_only() -> None:
    """引用风：没 question（用户只回复某消息没打字）→ 只渲染 quoted 那段。"""
    out = render_output(
        PRESET_QUOTE,
        {
            "quoted": "📷 [图片]",
            "question": "",
            "answer": "回答",
            "model": "x",
            "provider": "p",
            "in_tokens": 1,
            "out_tokens": 2,
            "total_tokens": 3,
            "routing_note": "",
        },
        escape_format="html",
    )
    assert out.startswith("<blockquote expandable>📷 [图片]</blockquote>")
    # 不应有针对 question 的空 blockquote
    assert "<blockquote expandable></blockquote>" not in out


# ════════════════════════════════════════════════════════════
# 5) 派生变量 answer_first_2 / answer_rest
# ════════════════════════════════════════════════════════════


def test_render_derived_answer_short() -> None:
    """答案 ≤ 2 行 → answer_rest 为空 → 折叠条件块也空。"""
    template = "{answer_first_2}{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}"
    out = render_output(template, {"answer": "一行回答"}, escape_format="html")
    assert out == "一行回答"


def test_render_derived_answer_long() -> None:
    """答案 > 4 行（n*2）→ answer_rest 包到 <blockquote expandable> 里。"""
    template = "{answer_first_2}{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}"
    out = render_output(
        template,
        {"answer": "第一行\n第二行\n第三行\n第四行\n第五行"},
        escape_format="html",
    )
    assert "第一行\n第二行" in out
    # 折叠引用块包住后面
    assert "<blockquote expandable>第三行\n第四行\n第五行</blockquote>" in out


def test_render_derived_answer_at_threshold_not_split() -> None:
    """答案正好 4 行（≤ n*2 阈值）→ 不切；折叠条件块不渲染。"""
    template = "{answer_first_2}{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}"
    out = render_output(
        template,
        {"answer": "行1\n行2\n行3\n行4"},
        escape_format="html",
    )
    # 4 行全部直出
    assert out == "行1\n行2\n行3\n行4"
    assert "<blockquote expandable>" not in out


# ════════════════════════════════════════════════════════════
# 5b) 派生变量 display_input（quoted 优先 / question 兜底）
# ════════════════════════════════════════════════════════════


def test_render_display_input_uses_quoted_when_present() -> None:
    """有 quoted（用户回复某条消息时）→ display_input = quoted。"""
    out = render_output(
        "{display_input}",
        {"quoted": "被回复的原文", "question": "总结一下"},
        escape_format=None,
    )
    assert out == "被回复的原文"


def test_render_display_input_falls_back_to_question() -> None:
    """没 quoted（用户直接发命令）→ display_input = question（"测试"）。"""
    out = render_output(
        "{display_input}",
        {"quoted": "", "question": "测试"},
        escape_format=None,
    )
    assert out == "测试"


def test_render_display_input_empty_when_both_empty() -> None:
    """quoted / question 都空 → display_input 也空 → 条件块不渲染。"""
    out = render_output(
        "x{?display_input}::{display_input}{/?}y",
        {"quoted": "", "question": ""},
        escape_format=None,
    )
    assert out == "xy"


def test_render_display_input_strips_whitespace() -> None:
    """quoted='   '（仅空白）应视为空，回退到 question。"""
    out = render_output(
        "{display_input}",
        {"quoted": "   ", "question": "实际问题"},
        escape_format=None,
    )
    assert out == "实际问题"


def test_render_display_input_first_2_short() -> None:
    """短输入（≤4 行）→ display_input_first_2 = 全文，display_input_rest 为空。"""
    out = render_output(
        "head={display_input_first_2}|rest={display_input_rest}",
        {"quoted": "L1\nL2"},
        escape_format=None,
    )
    assert out == "head=L1\nL2|rest="


def test_render_display_input_split_long() -> None:
    """长输入（>4 行）→ display_input_first_2 = 前 2 行，display_input_rest = 剩余。"""
    out = render_output(
        "head={display_input_first_2}|rest={display_input_rest}",
        {"quoted": "L1\nL2\nL3\nL4\nL5"},
        escape_format=None,
    )
    assert out == "head=L1\nL2|rest=L3\nL4\nL5"


def test_render_display_input_split_falls_back_to_question() -> None:
    """没 quoted 时切分用 question。"""
    out = render_output(
        "head={display_input_first_2}|rest={display_input_rest}",
        {"question": "Q1\nQ2\nQ3\nQ4\nQ5"},
        escape_format=None,
    )
    assert out == "head=Q1\nQ2|rest=Q3\nQ4\nQ5"


def test_preset_quote_uses_quoted_and_question() -> None:
    """守门测试：PRESET_QUOTE 必须独立用 {quoted} 和 {question}（两段 blockquote）。

    之前用 display_input 把 quoted/question 合一，但用户在 send_new 模式下要求
    两段都显示——所以现在 PRESET_QUOTE 走双 blockquote 布局。
    """
    assert "{quoted}" in PRESET_QUOTE
    assert "{?quoted}" in PRESET_QUOTE
    assert "{question}" in PRESET_QUOTE
    assert "{?question}" in PRESET_QUOTE


# ════════════════════════════════════════════════════════════
# 6) 转义 + 占位符值
# ════════════════════════════════════════════════════════════


def test_render_html_escape_applied_to_values() -> None:
    """HTML 模式下，{answer} 里的 < > & 要被转义。"""
    out = render_output(
        "{answer}",
        {"answer": "<script>alert(1)</script>"},
        escape_format="html",
    )
    assert out == "&lt;script&gt;alert(1)&lt;/script&gt;"


def test_render_html_escape_off_passthrough() -> None:
    """escape_format=None 时占位符值原样进。"""
    out = render_output(
        "{answer}",
        {"answer": "<script>alert(1)</script>"},
        escape_format=None,
    )
    assert out == "<script>alert(1)</script>"


def test_render_template_literal_html_preserved() -> None:
    """模板字面 HTML 标签不会被转义（escape 只动占位符值）。"""
    out = render_output("<b>{answer}</b>", {"answer": "x"}, escape_format="html")
    # 外层 <b></b> 留着；x 因为没特殊字符也不变
    assert out == "<b>x</b>"


def test_render_html_underscore_in_model_not_escaped() -> None:
    """关键回归：HTML 模式下 model="gpt-5.5" 里的 - / . 不应被转义为 \\- \\.

    （这是用户上一轮的 bug：默认走 markdownv2 + MDV2 转义，把 - . 全转义了。）
    """
    out = render_output(
        "model=`{model}`",
        {"model": "gpt-5.5"},
        escape_format="html",
    )
    # 不应该出现反斜杠
    assert "\\" not in out
    # gpt-5.5 原样出现
    assert "gpt-5.5" in out


# ════════════════════════════════════════════════════════════
# 7) 长度截断
# ════════════════════════════════════════════════════════════


def test_render_truncates_to_4000_chars() -> None:
    very_long = "x" * 10_000
    out = render_output("{answer}", {"answer": very_long}, escape_format=None)
    assert len(out) == 4000


# ════════════════════════════════════════════════════════════
# 8) 空模板防御
# ════════════════════════════════════════════════════════════


def test_render_empty_template_returns_empty() -> None:
    assert render_output("", {"answer": "x"}, escape_format="html") == ""


# ════════════════════════════════════════════════════════════
# 9) 四个预设非空合理性
# ════════════════════════════════════════════════════════════


def test_presets_non_empty() -> None:
    assert PRESET_SIMPLE.strip()
    assert PRESET_QUOTE.strip()
    assert PRESET_MINIMAL.strip()
    assert PRESET_TRANSLATE.strip()


def test_translate_preset_no_quoted_block() -> None:
    """翻译预设里不应有 {?quoted} 条件块——它的核心特征就是不显示原引用。"""
    assert "{?quoted}" not in PRESET_TRANSLATE
    assert "{quoted}" not in PRESET_TRANSLATE


@pytest.mark.parametrize("preset", [PRESET_SIMPLE, PRESET_QUOTE, PRESET_MINIMAL, PRESET_TRANSLATE])
def test_presets_render_with_full_ctx(preset: str) -> None:
    """四个预设都应能用完整 ctx 跑通不抛错。"""
    out = render_output(
        preset,
        {
            "question": "?",
            "quoted": "原文",
            "answer": "答\n答\n答\n答",
            "model": "gpt-x",
            "provider": "Any",
            "provider_kind": "openai",
            "in_tokens": 100,
            "out_tokens": 50,
            "total_tokens": 150,
            "routing_note": "auto · matched tag=code",
        },
        escape_format="html",
    )
    assert out
    assert "答" in out
