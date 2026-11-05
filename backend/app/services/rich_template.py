"""把 AI 输出模板（Telegram HTML 风格）渲染成 Rich Markdown（0.119.1）。

``,ai`` 的输出模板按 Telegram HTML parse_mode 写：靠换行分行、用 ``<blockquote expandable>``
包住回答的后半段、用整行「━━━━」当分隔线。inline 代发时交互 Bot 把占位编辑成 Rich Message，
0.119.0 直接把这份模板当 Rich Markdown 发出去，于是：

- Markdown 里单个换行只是空格，模板的分行全部挤成一段；
- 官方规定块级 HTML 标签（``<details>`` 等少数例外）里不解析 Markdown，回答被
  ``<blockquote>`` 包住的部分（表格、标题、列表）原样显示成符号；
- 手打的「━━━━」只是一行文字。

这里改成和插件 AI 答疑同样的做法：模板部分按字面渲染（HTML 转义后的值 + 行内标签），
换行变成 ``<br>``，``<blockquote>`` 变成 Markdown 引用，整行横线变成原生分割线 ``---``；
回答本身不拆开，作为一整段 Markdown 放回 ``{answer}`` 所在位置（``{answer_first_2}`` +
``{answer_rest}`` 折叠写法同样视为整段回答），表格、标题、列表因此与普通 Rich Message 一致。
"""

from __future__ import annotations

import html
import re
from typing import Any

from .render_profile import is_divider_line, normalize_markdown_dividers

ANSWER_SENTINEL = "\u0001TPANSWER\u0001"
_ANSWER_BLOCK = "\u0003TPANSWERBLOCK\u0003"
_ANSWER_INLINE = "\u0003TPANSWERINLINE\u0003"
DIVIDER = "---"
TEXT_DIVIDER = "━━━━━━━━━━━━"
RICH_TEXT_LIMIT = 32768

_PH = "\u0002{}\u0002"
_PH_RE = re.compile("\u0002(\\d+)\u0002")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_PRE_RE = re.compile(r"<pre\b[^>]*>([\s\S]*?)</pre>", re.IGNORECASE)
_QUOTE_RE = re.compile(r"<blockquote\b[^>]*>([\s\S]*?)</blockquote>", re.IGNORECASE)
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^<>]*)>")
_ANCHOR_RE = re.compile(r"<a\b[^>]*?href\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)[^>]*>([\s\S]*?)</a>", re.IGNORECASE)
_SPAN_SPOILER_RE = re.compile(r"<span\b[^>]*class\s*=\s*[\"']?tg-spoiler[\"']?[^>]*>([\s\S]*?)</span>", re.IGNORECASE)
_SAFE_LINK_RE = re.compile(r"^(?:https?://|mailto:|tg://user\?id=\d+$)[^\s<>\"']*$", re.IGNORECASE)
_KEEP_TAGS = {
    "b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "ins": "u", "s": "s", "strike": "s", "del": "s",
    "code": "code", "tg-spoiler": "tg-spoiler", "sub": "sub", "sup": "sup", "mark": "mark",
}
_MD_PUNCT_RE = re.compile(r"([\\`*_\[\]#|~=$>])")
_LINE_START_RE = re.compile(r"^(\s*)([-+]|\d{1,9}[.)])(\s|$)")

# 回答 Markdown 的安全收敛（与插件 AI 答疑 markdown_to_rich_markdown 同口径）。
_FENCE_BLOCK_RE = re.compile(r"(?m)^[ \t]*(```|~~~)[^\n]*\n[\s\S]*?(?:\n[ \t]*\1[ \t]*(?=\n|$)|\Z)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_IMAGE_RE = re.compile(r'!\[([^\]\n]*)\]\(\s*([^)\s]+)(?:\s+"[^\n]*?")?\s*\)')
_LINK_RE = re.compile(r'\[([^\]\n]+)\]\(\s*([^)\s]+)(?:\s+"[^\n]*?")?\s*\)')
_RICH_ALLOWED_TAGS = {
    "u", "ins", "sub", "sup", "s", "del", "strike", "b", "strong", "i", "em",
    "tg-spoiler", "mark", "code", "br", "details", "summary",
}
_AUTOLINK_RE = re.compile(r"^<https?://[^\s<>]+>$")
_ANSWER_LINK_RE = re.compile(r"^(?:https?://|mailto:)[^\s<>\"']+$", re.IGNORECASE)


def _escape_text(text: str) -> str:
    """模板文字按字面显示：Markdown 标点加反斜杠（HTML 实体保持原样）。"""

    return _MD_PUNCT_RE.sub(r"\\\1", text)


def _inline(fragment: str) -> str:
    """模板里的一行 Telegram HTML → Rich Markdown 行内内容。"""

    store: list[str] = []

    def keep(value: str) -> str:
        store.append(value)
        return _PH.format(len(store) - 1)

    def anchor(match: re.Match[str]) -> str:
        url = html.unescape(match.group(1).strip("\"'"))
        label = _inline(match.group(2)) or _escape_text(html.escape(url, quote=False))
        if not _SAFE_LINK_RE.match(url):
            return keep(label)
        return keep(f"[{label}]({url.replace(')', '%29').replace('(', '%28')})")

    work = _ANCHOR_RE.sub(anchor, fragment)
    work = _SPAN_SPOILER_RE.sub(lambda m: keep(f"<tg-spoiler>{_inline(m.group(1))}</tg-spoiler>"), work)

    out: list[str] = []
    pos = 0
    for match in _TAG_RE.finditer(work):
        out.append(_escape_text(work[pos : match.start()]))
        name = _KEEP_TAGS.get(match.group(2).lower())
        if name is not None:
            out.append(f"<{match.group(1)}{name}>")
        pos = match.end()
    out.append(_escape_text(work[pos:]))
    text = "".join(out)
    for _ in range(3):
        if not _PH_RE.search(text):
            break
        text = _PH_RE.sub(lambda m: store[int(m.group(1))], text)
    return text


def _line(fragment: str) -> str:
    text = _inline(fragment).strip()
    # 行首的 “- ” “+ ” “1. ” 在 Markdown 里会变成列表：按字面显示。
    return _LINE_START_RE.sub(lambda m: f"{m.group(1)}\\{m.group(2)}{m.group(3)}", text, count=1)


def _plain(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment))


def sanitize_answer_markdown(text: str) -> str:
    """模型 Markdown → 可放进 ``InputRichMessage.markdown`` 的内容（不嵌媒体、只放行少量 HTML）。"""

    source = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not source:
        return ""
    store: list[str] = []

    def keep(fragment: str) -> str:
        store.append(fragment)
        return _PH.format(len(store) - 1)

    work = _FENCE_BLOCK_RE.sub(lambda m: keep(m.group(0)), source)
    work = _INLINE_CODE_RE.sub(lambda m: keep(m.group(0)), work)

    def image(match: re.Match[str]) -> str:
        label, url = match.group(1).strip(), match.group(2)
        if _ANSWER_LINK_RE.match(url) and url.lower().startswith("http"):
            return f"[{label or url}]({url})"
        return label

    def link(match: re.Match[str]) -> str:
        return match.group(0) if _ANSWER_LINK_RE.match(match.group(2)) else match.group(1)

    work = _IMAGE_RE.sub(image, work)
    work = _LINK_RE.sub(link, work)

    def tag(match: re.Match[str]) -> str:
        raw = match.group(0)
        if _AUTOLINK_RE.match(raw):
            return raw
        name = match.group(2).lower()
        attrs = match.group(3).strip().rstrip("/").strip()
        if name in _RICH_ALLOWED_TAGS and (not attrs or (name == "details" and attrs.lower() == "open")):
            closing = "/" if name == "br" and not match.group(1) else ""
            return f"<{match.group(1)}{name}{' open' if attrs else ''}{closing}>"
        return "&lt;" + raw[1:]

    work = _TAG_RE.sub(tag, work)
    work = work.replace("<!--", "&lt;!--")
    work = re.sub(r"\n{3,}", "\n\n", work)
    for _ in range(3):
        if not _PH_RE.search(work):
            break
        work = _PH_RE.sub(lambda m: store[int(m.group(1))], work)
    return work.strip()


def _quote(markdown: str) -> str:
    return "\n".join(f"> {line}" if line.strip() else ">" for line in markdown.split("\n"))


def _convert_lines(text: str, answer_md: str, blocks: dict[str, str], *, top_level: bool = False) -> list[str]:
    """把模板片段（已拆出 pre / blockquote）转成 Markdown 块列表。"""

    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append("<br>".join(para))
            para.clear()

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            flush()
            continue
        if stripped in blocks:
            flush()
            out.append(blocks[stripped])
            continue
        if ANSWER_SENTINEL in raw_line:
            before, _, after = raw_line.partition(ANSWER_SENTINEL)
            if before.strip():
                para.append(_line(before))
            flush()
            if top_level:
                # 回答单独占一行时才按排版补分割线；和其它文字写在同一行（如 "{answer}|{sources}"）就不补。
                alone = not before.strip() and not after.strip()
                out.append(_ANSWER_BLOCK if alone else _ANSWER_INLINE)
            elif answer_md:
                out.append(answer_md)
            if after.strip():
                para.append(_line(after))
            continue
        if is_divider_line(_plain(raw_line)):
            flush()
            out.append(DIVIDER)
            continue
        para.append(_line(raw_line))
    flush()
    return out


def template_html_to_rich_markdown(
    rendered: str,
    answer_markdown: str,
    *,
    divider_before: bool = True,
    divider_after: bool = True,
    native_divider: bool = True,
) -> str:
    """``render_output(..., escape_format="html")`` 的结果（回答位置是 :data:`ANSWER_SENTINEL`）→ Rich Markdown。"""

    text = str(rendered or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _BR_RE.sub("\n", text)
    answer_md = str(answer_markdown or "").strip()
    blocks: dict[str, str] = {}

    def block(value: str) -> str:
        key = _PH.format(len(blocks))
        blocks[key] = value
        return f"\n{key}\n"

    def pre(match: re.Match[str]) -> str:
        body = _plain(match.group(1)).strip("\n")
        fence = "````" if "```" in body else "```"
        return block(f"{fence}\n{body}\n{fence}")

    def quote(match: re.Match[str]) -> str:
        inner = _convert_lines(match.group(1), answer_md, blocks)
        joined = "\n\n".join(item for item in inner if item)
        return block(_quote(joined)) if joined.strip() else "\n"

    text = _PRE_RE.sub(pre, text)
    had_answer = ANSWER_SENTINEL in _QUOTE_RE.sub("", text)
    text = _QUOTE_RE.sub(quote, text)
    parts = [item for item in _convert_lines(text, answer_md, blocks, top_level=True) if item.strip()]
    if not had_answer and answer_md and _ANSWER_BLOCK not in parts and _ANSWER_INLINE not in parts:
        parts.append(_ANSWER_BLOCK)
    parts = layout_with_dividers(parts, _ANSWER_BLOCK, before=divider_before, after=divider_after)
    divider = DIVIDER if native_divider else TEXT_DIVIDER

    def render(item: str) -> str:
        if item in (_ANSWER_BLOCK, _ANSWER_INLINE):
            return answer_md
        return divider if item == DIVIDER else item

    result = "\n\n".join(render(item) for item in parts if item not in (_ANSWER_BLOCK, _ANSWER_INLINE) or answer_md)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def layout_with_dividers(parts: list[str], answer_marker: str, *, before: bool = True, after: bool = True) -> list[str]:
    """回答排版（0.119.1，用户指定）：引用 / 提示 → 分割线 → 回答 → 分割线 → 署名 / 来源。

    回答前后有其它内容时，中间补一条原生分割线（``---``，InputRichBlockDivider）；模板里已有
    分割线就不重复，相邻的分割线合并，开头 / 结尾不留分割线。
    """

    if answer_marker in parts:
        index = parts.index(answer_marker)
        if before and index > 0 and parts[index - 1] != DIVIDER:
            # 0.120.1：回答上面是「引用块 + 标题行（如 ✨ AI 回答）」时，分割线紧跟在引用下面
            # （引用 → 分割线 → 标题 → 回答），不夹在标题和回答之间。
            anchor = _quote_anchor(parts, index)
            at = anchor + 1 if anchor is not None else index
            parts.insert(at, DIVIDER)
            index += 1
        if after and index < len(parts) - 1 and parts[index + 1] != DIVIDER:
            parts.insert(index + 1, DIVIDER)
    out: list[str] = []
    for item in parts:
        if item == DIVIDER and (not out or out[-1] == DIVIDER):
            continue
        out.append(item)
    while out and out[-1] == DIVIDER:
        out.pop()
    return out


def _quote_anchor(parts: list[str], index: int) -> int | None:
    """回答前最近的引用块位置；引用和回答之间只有普通段落（标题）且没有分割线时返回它，否则 None。"""

    for pos in range(index - 1, -1, -1):
        item = parts[pos]
        if item == DIVIDER:
            return None
        if item.lstrip().startswith(">"):
            return pos if pos < index - 1 else None
        if item in (_ANSWER_BLOCK, _ANSWER_INLINE) or "\n\n" in item or len(_plain(item)) > 60:
            return None
    return None


def render_template_rich_markdown(
    template: str,
    ctx: dict[str, Any],
    *,
    divider_before: bool = True,
    divider_after: bool = True,
    native_divider: bool = True,
) -> str:
    """按 ``,ai`` 输出模板渲染 Rich Markdown：模板按字面，回答保持完整 Markdown。

    排版开关（服务器参数）：``divider_before`` 引用 / 标题与回答之间、``divider_after`` 回答与署名之间
    自动补分割线；``native_divider`` 为 False 时分割线用整行「━」文字，回答里的横线也不改写。
    """

    from .llm_format import render_output  # noqa: PLC0415

    answer = str(ctx.get("answer") or "")
    rich_ctx = dict(ctx)
    rich_ctx["answer"] = ANSWER_SENTINEL if answer.strip() else ""
    # 折叠写法 {answer_first_2} + {answer_rest}：Rich Message 里不拆开回答（拆开会切断表格 / 列表）。
    rich_ctx["answer_first_2"] = ANSWER_SENTINEL if answer.strip() else ""
    rich_ctx["answer_rest"] = ""
    rendered = render_output(template, rich_ctx, escape_format="html")
    # 同一模板里同时写了 {answer} 和 {answer_first_2} 时只保留第一处。
    first = rendered.find(ANSWER_SENTINEL)
    if first >= 0:
        head = rendered[: first + len(ANSWER_SENTINEL)]
        rendered = head + rendered[first + len(ANSWER_SENTINEL) :].replace(ANSWER_SENTINEL, "")
    answer_md = sanitize_answer_markdown(answer)
    if native_divider:
        answer_md = normalize_markdown_dividers(answer_md)
    markdown = template_html_to_rich_markdown(
        rendered,
        answer_md,
        divider_before=divider_before,
        divider_after=divider_after,
        native_divider=native_divider,
    )
    return markdown[:RICH_TEXT_LIMIT]


__all__ = [
    "ANSWER_SENTINEL",
    "TEXT_DIVIDER",
    "layout_with_dividers",
    "render_template_rich_markdown",
    "render_template_text_layout",
    "sanitize_answer_markdown",
    "template_html_to_rich_markdown",
]


# ---------------------------------------------------------------------------
# 普通模式（Telegram HTML / Markdown / 纯文本）：同样的排版，分割线用整行「━」代替
# ---------------------------------------------------------------------------
# 用户账号（非 Premium）只能发普通实体消息（粗体 / 引用 / 代码等），没有原生分割线块；
# Telegram HTML parse_mode 也不支持 <hr>。最接近的做法是单独一行的「━━━━━━━━━━━━」。
_S_ANSWER = "\u0001TPA0\u0001"
_S_FIRST = "\u0001TPA1\u0001"
_S_REST = "\u0001TPA2\u0001"


_QUOTE_THEN_MARK_RE = re.compile(
    "(</blockquote>)[ \t]*(" + "|".join(re.escape(m) for m in (_S_ANSWER, _S_FIRST)) + ")", re.IGNORECASE
)


def _text_quote_anchor(lines: list[str], first: int) -> int | None:
    """回答前最近一个以 ``</blockquote>`` 结尾的行；中间只有短标题行（无分割线）时返回它的行号。"""

    for pos in range(first - 1, -1, -1):
        line = lines[pos].strip()
        if not line:
            continue
        if is_divider_line(_plain(line)):
            return None
        if line.lower().endswith("</blockquote>"):
            return pos if any(lines[i].strip() for i in range(pos + 1, first)) else None
        if "<blockquote" in line.lower() or len(_plain(line)) > 60:
            return None
    return None


def render_template_text_layout(
    template: str,
    ctx: dict[str, Any],
    *,
    escape_format: str | None,
    divider_before: bool = True,
    divider_after: bool = True,
) -> str:
    """按模板渲染普通消息，并按用户指定排版在回答前后补分割线（模板已有分割线时不重复）。

    排版：引用（被回复消息 / 问题）→ 分割线 → 回答 → 分割线 → 署名。回答前后没有其它内容的一侧不补。
    """

    from .llm_format import _build_full_ctx, render_output  # noqa: PLC0415

    answer = str(ctx.get("answer") or "")
    if not answer.strip():
        return render_output(template, ctx, escape_format=escape_format)
    real = _build_full_ctx(ctx, escape_format=escape_format)
    marked = dict(ctx)
    marked["answer"] = _S_ANSWER
    marked["answer_first_2"] = _S_FIRST
    marked["answer_rest"] = _S_REST if str(real.get("answer_rest") or "").strip() else ""
    rendered = render_output(template, marked, escape_format=escape_format)
    if divider_before:
        # 模板写成 ``<blockquote>{question}</blockquote>{answer}``（Telegram 里引用块后自动换行）时，
        # 把回答拆到下一行，才能在引用和回答之间补分割线（0.120.1）。
        rendered = _QUOTE_THEN_MARK_RE.sub(lambda m: f"{m.group(1)}\n{m.group(2)}", rendered, count=1)
    lines = rendered.split("\n")
    hits = [i for i, line in enumerate(lines) if any(mark in line for mark in (_S_ANSWER, _S_FIRST, _S_REST))]
    def alone(line: str) -> bool:
        rest = line
        for mark in (_S_ANSWER, _S_FIRST, _S_REST):
            rest = rest.replace(mark, "")
        return not _plain(rest).strip()

    if hits and alone(lines[hits[0]]) and alone(lines[hits[-1]]):
        first, last = hits[0], hits[-1]
        after = next((i for i in range(last + 1, len(lines)) if lines[i].strip()), None)
        if divider_after and after is not None and not is_divider_line(_plain(lines[after])):
            lines.insert(last + 1, TEXT_DIVIDER)
        before = next((i for i in range(first - 1, -1, -1) if lines[i].strip()), None)
        if divider_before and before is not None and not is_divider_line(_plain(lines[before])):
            anchor = _text_quote_anchor(lines, first)
            lines.insert(anchor + 1 if anchor is not None else first, TEXT_DIVIDER)
    body = "\n".join(lines)
    body = body.replace(_S_ANSWER, real.get("answer", "")).replace(_S_FIRST, real.get("answer_first_2", ""))
    return body.replace(_S_REST, real.get("answer_rest", ""))
