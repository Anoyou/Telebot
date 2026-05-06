"""消息格式渲染器：把 ``output_template`` + 上下文 → 最终 TG 消息字符串。

设计：
- **占位符** ``{key}``       — 直接替换；未知 key 留空（不抛 KeyError）
- **条件块** ``{?key}...{/?}`` — 仅当 ``ctx[key]`` 是真值（非空字符串/非零）时渲染括号内
- **派生变量**：``answer_first_2`` / ``answer_rest`` ——把 ``answer`` 切成"前 2 行 + 剩余"，
  让用户用 ``{answer_first_2}`` + ``<blockquote expandable>{answer_rest}</blockquote>``
  实现 alma 风的"前两行 + 折叠"
- **格式转义**：默认对所有占位符**值**做对应格式的转义；模板字面 markdown / HTML 标签不动
- **截断**：最终输出截到 4000 字符（TG 单条上限 4096，留余量）

关于 parse_mode 选择
=====================

Telethon 1.36 的 ``sanitize_parse_mode`` 只接受 ``md`` / ``markdown`` / ``html`` 字符串，
**不接受 ``markdownv2``**。所以我们没法直接走 Telegram Bot API 风的 MDV2——之前传
``markdownv2`` 会让 telethon 抛 ValueError，最终消息以纯文本发出，反斜杠原样显示。

因此默认走 **HTML**：telethon 内置完整支持，且 ``<blockquote expandable>`` 直接对应
"折叠引用块"的官方实现，比 MDV2 的 ``**>...**`` 更好控制。
"""

from __future__ import annotations

import re
import time as _time
from typing import Any

# 输出最大字符数；TG 单条上限 4096，预留缓冲
_MAX_OUTPUT_CHARS = 4000

# {?key}...{/?} 条件块
_COND_BLOCK_RE = re.compile(r"\{\?(\w+)\}(.*?)\{/\?\}", re.DOTALL)

# {key} 占位符（不含 { } / ? / ; 等特殊字符）
# 注意：要避开 {?key} 形式（前缀含 ?），所以这里负向先行
_PLACEHOLDER_RE = re.compile(r"\{(?!\?)(\w+)\}")

# Telegram MarkdownV2 必须转义的字符（来自 Bot API 文档）
# 仅在 escape_format='mdv2' 时使用；HTML 模式下不需要
_MDV2_SPECIAL_CHARS = set("_*[]()~`>#+-=|{}.!\\")


def _escape_mdv2(text: str) -> str:
    """对单个值做 Telegram MarkdownV2 转义（仅 escape_format='mdv2' 时用）。

    每个特殊字符前加反斜杠。空字符串原样返回。
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        if ch in _MDV2_SPECIAL_CHARS:
            out.append("\\")
        out.append(ch)
    return "".join(out)


def _escape_html(text: str) -> str:
    """对单个值做 Telegram HTML 转义（默认转义模式）。

    Telegram HTML 只识别这三个特殊字符：& < >
    其它字符（包括 _ * 等 markdown 字符）都不会被解析为格式，所以模板里
    可以直接写 ``<b>{model}</b>``，{model} 的值里有 _ 也不会被搞乱。
    """
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _split_first_n_lines(text: str, n: int) -> tuple[str, str]:
    """把 ``text`` 按 ``\\n`` 切成 ``(前 n 行, 剩余)``，剩余包括行间的 ``\\n``。

    切分阈值：**总行数 > n*2 才切**——避免短答案（3 行被切成 2+1）看着像被
    折叠掉一半。当总行数 ≤ n*2（n=2 时就是 ≤ 4 行）时，``head=text, rest=""``，
    引用风模板里的 ``{?answer_rest}<blockquote expandable>...{/?}`` 整段不渲染。

    - 不足或等于阈值：``(原文, "")``
    - 超过阈值：``(前 n 行, 剩下所有)``
    """
    if not text:
        return "", ""
    lines = text.splitlines()
    # 总行数没超过阈值 → 不切，全进 head；折叠块就不出现
    if len(lines) <= n * 2:
        return text, ""
    head = "\n".join(lines[:n])
    rest = "\n".join(lines[n:])
    return head, rest


def _is_truthy(v: Any) -> bool:
    """条件块判断：None / 空串 / 0 / False / 空 list 都视为假。"""
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, (list, dict, tuple)):
        return len(v) > 0
    return bool(v)


def _build_full_ctx(ctx: dict[str, Any], escape_format: str | None) -> dict[str, str]:
    """把入参 ``ctx`` 加工成"全字符串、转义后"的最终上下文。

    - 数值 / None 转字符串
    - 自动追加派生变量：``answer_first_2`` / ``answer_rest`` / ``display_input``、
      ``time``（若没传）
    - escape_format=``'html'`` 时走 HTML 转义；``'mdv2'`` 时走 MarkdownV2 转义；
      ``None`` 不转义（plain / markdown_v1 模式都用 None）
    """
    full: dict[str, Any] = dict(ctx)

    # 派生变量：answer 切前 2 行 + 剩余
    answer = str(full.get("answer", "") or "")
    a_head, a_rest = _split_first_n_lines(answer, 2)
    full.setdefault("answer_first_2", a_head)
    full.setdefault("answer_rest", a_rest)

    # 派生变量：display_input = quoted（被回复消息）或 question（用户跟在命令后的文字）
    # 用途：用户写 ``,ai 测试`` 时 quoted 为空、question="测试"；
    #       用户**回复某条消息**写 ``,ai 总结一下`` 时 quoted=被回消息正文、question="总结一下"。
    # 引用风模板想"无论哪种情况都把用户的输入显示在引用块里"——用这个派生变量最简单。
    quoted = str(full.get("quoted", "") or "").strip()
    question = str(full.get("question", "") or "").strip()
    display_input = quoted or question
    full.setdefault("display_input", display_input)
    # 同 answer：display_input 也切"前 2 行 + 剩余"，让模板能复用 answer 的折叠模式
    di_head, di_rest = _split_first_n_lines(display_input, 2)
    full.setdefault("display_input_first_2", di_head)
    full.setdefault("display_input_rest", di_rest)

    # 派生变量：time 默认当前 HH:MM
    full.setdefault("time", _time.strftime("%H:%M"))

    # 全部转字符串（None → ""）
    str_ctx: dict[str, str] = {}
    for k, v in full.items():
        if v is None:
            str_ctx[k] = ""
        elif isinstance(v, bool):
            # bool 是 int 子类，单独处理避免 True/False → "1"/"0"
            str_ctx[k] = "true" if v else ""
        else:
            str_ctx[k] = str(v)

    if escape_format == "html":
        str_ctx = {k: _escape_html(v) for k, v in str_ctx.items()}
    elif escape_format == "mdv2":
        str_ctx = {k: _escape_mdv2(v) for k, v in str_ctx.items()}
    return str_ctx


def render_output(
    template: str,
    ctx: dict[str, Any],
    *,
    escape_format: str | None = "html",
) -> str:
    """按 ``template`` + ``ctx`` 渲染最终消息。

    Args:
        template:       用户配的输出模板字符串
        ctx:            原始上下文 dict（answer / quoted / model / in_tokens / ...）
        escape_format:  ``'html'``（默认；转 ``& < >``）/ ``'mdv2'``（转 MDV2 所有特殊字符）/
                        ``None``（不转义；用于 plain 与 markdown_v1）

    Returns:
        最终消息字符串（已截到 4000 字符上限）
    """
    if not template:
        return ""

    full_ctx = _build_full_ctx(ctx, escape_format=escape_format)

    # 1) 先处理条件块：判断真假用**未转义**的原始 ctx，但渲染括号内仍用最终上下文
    raw_ctx = dict(ctx)
    answer_raw = str(raw_ctx.get("answer", "") or "")
    a_head, a_rest = _split_first_n_lines(answer_raw, 2)
    raw_ctx.setdefault("answer_first_2", a_head)
    raw_ctx.setdefault("answer_rest", a_rest)
    # 同 _build_full_ctx：display_input = quoted 或 question
    quoted_raw = str(raw_ctx.get("quoted", "") or "").strip()
    question_raw = str(raw_ctx.get("question", "") or "").strip()
    display_input_raw = quoted_raw or question_raw
    raw_ctx.setdefault("display_input", display_input_raw)
    di_head, di_rest = _split_first_n_lines(display_input_raw, 2)
    raw_ctx.setdefault("display_input_first_2", di_head)
    raw_ctx.setdefault("display_input_rest", di_rest)
    raw_ctx.setdefault("time", _time.strftime("%H:%M"))

    def _replace_cond(m: re.Match[str]) -> str:
        key = m.group(1)
        body = m.group(2)
        return body if _is_truthy(raw_ctx.get(key)) else ""

    expanded = _COND_BLOCK_RE.sub(_replace_cond, template)

    # 2) 再替换普通占位符 {key}（用最终上下文，含转义后的值）
    def _replace_ph(m: re.Match[str]) -> str:
        key = m.group(1)
        return full_ctx.get(key, "")

    rendered = _PLACEHOLDER_RE.sub(_replace_ph, expanded)

    # 3) 截断到 TG 单条上限以内
    return rendered[:_MAX_OUTPUT_CHARS]


# ────────────────────────────────────────────────────────────
# 预设（前端"快捷预设"按钮直接填进 textarea）
# 注意：HTML 模式下默认；这些字符串里的 <b> <blockquote> 等是字面 HTML，
# 渲染时**只**对占位符值做 HTML 转义，模板自身的标签保留。
# ────────────────────────────────────────────────────────────

# A. 简洁（默认）：纯文本风，任何 parse_mode 下都好看
PRESET_SIMPLE = (
    "{answer}\n\n"
    "— {model} · in {in_tokens} / out {out_tokens}"
    "{?routing_note}  ·  {routing_note}{/?}"
)

# B. 引用风（HTML 版）：alma 截图风格；前 2 行 + 折叠引用块
# 用 ``{display_input}`` 派生变量——它在"用户回复某条消息"时取被回消息正文，
# 在"用户直接发命令"时取命令后跟的问题文本。统一覆盖两种场景。
# footer 走精简风：模型 · 提供商 / In·Out·Total / 路由说明（仅 auto 模式）
PRESET_QUOTE = (
    # 双 expandable blockquote 布局：
    # - 第一段：被回复消息正文（quoted）；媒体类型由 worker 转成 "📷 [图片]" 等占位
    # - 第二段：用户在命令后跟的问题（question）
    # 任一为空就跳过对应 blockquote（条件块自然处理）。两段都空时只渲染答案。
    # 这样在 send_new 模式（删命令重发新消息）里能完整显示"上下文 + 问题 + 答案"，
    # 在 edit 模式里也能让用户的问题被独立看到（之前 display_input 只取一个，丢失另一个）。
    "{?quoted}<blockquote expandable>{quoted}</blockquote>\n{/?}"
    "{?question}<blockquote expandable>{question}</blockquote>\n{/?}"
    "<b>✨ AI 回答</b>\n"
    "{answer_first_2}"
    "{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}\n\n"
    "━━━━━━━━━━━━━━━\n"
    "{model} · {provider}\n"
    "In: {in_tokens} | Out: {out_tokens} | Total: {total_tokens}"
    "{?routing_note}\n{routing_note}{/?}"
)

# C. 极简：答案 + 一行模型 / token 标签
PRESET_MINIMAL = "{answer}\n<code>{model}</code> · {total_tokens}t"

# D. 翻译/简答风：不显示引用（即使 quote_replied=True 仅供模型上下文，UI 不重复展示）
#   适合 ``,翻译`` / ``,简答`` 等命令——用户只想看答案，不想看自己/对方原文复读
PRESET_TRANSLATE = (
    "{answer}\n\n"
    "<i>— {model}</i>"
)

PRESETS: dict[str, str] = {
    "simple": PRESET_SIMPLE,
    "quote": PRESET_QUOTE,
    "minimal": PRESET_MINIMAL,
    "translate": PRESET_TRANSLATE,
}

# 默认模板（cfg.output_template 没设时使用）
DEFAULT_TEMPLATE = PRESET_SIMPLE


# ────────────────────────────────────────────────────────────
# 占位符元数据（前端用来渲染"占位符按钮 + 中文释义"）
# ────────────────────────────────────────────────────────────

PLACEHOLDER_META: list[dict[str, str]] = [
    {"key": "answer", "label": "[回答]", "desc": "AI 的回答正文"},
    {"key": "answer_first_2", "label": "[回答-前2行]", "desc": "回答的前 2 行（折叠用）"},
    {"key": "answer_rest", "label": "[回答-剩余]", "desc": "回答从第 3 行起（配 <blockquote expandable> 折叠）"},
    {"key": "display_input", "label": "[输入]", "desc": "用户的输入：被回复消息正文（优先）/ 没有则用问题"},
    {"key": "display_input_first_2", "label": "[输入-前2行]", "desc": "输入的前 2 行（折叠用）"},
    {"key": "display_input_rest", "label": "[输入-剩余]", "desc": "输入从第 3 行起（配 <blockquote expandable> 折叠）"},
    {"key": "question", "label": "[问题]", "desc": "用户在命令后跟的问题文本"},
    {"key": "quoted", "label": "[被引用]", "desc": "被回复消息的正文（仅用户回复某条消息时才有）"},
    {"key": "model", "label": "[模型]", "desc": "API 实际返回的模型名"},
    {"key": "provider", "label": "[提供商]", "desc": "提供商名称（如 Any GPT）"},
    {"key": "provider_kind", "label": "[厂商]", "desc": "openai / anthropic / ollama"},
    {"key": "in_tokens", "label": "[输入tokens]", "desc": "输入 token 数"},
    {"key": "out_tokens", "label": "[输出tokens]", "desc": "输出 token 数"},
    {"key": "total_tokens", "label": "[总tokens]", "desc": "输入 + 输出"},
    {"key": "routing_note", "label": "[路由说明]", "desc": "auto 模式的决策原因（fixed 模式空）"},
    {"key": "time", "label": "[时间]", "desc": "当前时间 HH:MM"},
]

# 条件块元数据（条件块语法略不同，UI 单独一组按钮）
CONDITIONAL_META: list[dict[str, str]] = [
    {
        "key": "display_input",
        "label": "[条件:有输入]",
        "desc": "仅当用户有输入（被回复消息或命令后问题）才渲染；用作引用框最常见",
        "snippet": "{?display_input}\n\n{/?}",
    },
    {
        "key": "quoted",
        "label": "[条件:被引用]",
        "desc": "仅当被回复消息非空才渲染括号内（用户必须回复某条消息）",
        "snippet": "{?quoted}\n\n{/?}",
    },
    {
        "key": "routing_note",
        "label": "[条件:路由]",
        "desc": "仅 auto 模式才渲染括号内",
        "snippet": "{?routing_note}\n\n{/?}",
    },
    {
        "key": "answer_rest",
        "label": "[条件:有剩余]",
        "desc": "仅当回答超过 2 行才渲染（配折叠块用）",
        "snippet": "{?answer_rest}\n<blockquote expandable>{answer_rest}</blockquote>{/?}",
    },
    {
        "key": "display_input_rest",
        "label": "[条件:输入有剩余]",
        "desc": "仅当输入超过 2 行才渲染（配折叠块用）",
        "snippet": "{?display_input_rest}\n<blockquote expandable>{display_input_rest}</blockquote>{/?}",
    },
]


__all__ = [
    "CONDITIONAL_META",
    "DEFAULT_TEMPLATE",
    "PLACEHOLDER_META",
    "PRESETS",
    "PRESET_MINIMAL",
    "PRESET_QUOTE",
    "PRESET_SIMPLE",
    "PRESET_TRANSLATE",
    "render_output",
]
