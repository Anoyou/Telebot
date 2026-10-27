"""Bot API 10.3 compact rich tables (``is_compact`` / ``<table compact>``).

Official contract (core.telegram.org/bots/api, Bot API 10.3, 2026-08-24):

- ``InputRichBlockTable.is_compact``: ``True``, optional — "Pass True if table
  cells must have smaller indents"; ``RichBlockTable.is_compact`` mirrors it on
  received messages.
- Rich HTML: ``<table bordered striped compact>``.
- Rich Markdown has no table flag, but "can contain arbitrary HTML" and
  "Markdown isn't parsed inside block HTML tags other than <details>,
  <tg-collage> and <tg-slideshow>" — so a compact Markdown table is written as
  an HTML ``<table bordered compact>`` block whose cells are inline HTML.
- ``is_compact`` only shrinks cell indents; borders are a separate flag
  (``is_bordered`` / ``bordered``).  A GFM pipe table is rendered by Telegram
  with borders, so the converted block keeps ``bordered`` — otherwise turning
  compact on would silently drop the grid lines.  ``striped`` is not added:
  the docs do not say pipe tables are striped by default.
- ``blocks`` / ``html`` tables only gain ``is_compact`` / ``compact``; the
  caller's own ``is_bordered`` / ``is_striped`` / ``bordered`` / ``striped``
  are kept exactly as written.

Helpers here never emit ``is_compact: false``; without an explicit opt-in the
payload is returned untouched (byte-for-byte identical to older releases).
"""

from __future__ import annotations

import html
import re
from copy import deepcopy
from typing import Any

# Action-level opt-in understood by the plugin action normalizer.
COMPACT_TABLES_ACTION_KEY = "compact_tables"
# Attributes a converted GFM pipe table keeps besides ``compact`` (see module doc).
PIPE_TABLE_ATTRS = "bordered"

_TABLE_TAG_RE = re.compile(r"<table(?=[\s>/])([^<>]*)>", re.IGNORECASE)
_COMPACT_ATTR_RE = re.compile(r"(?<![\w-])compact(?![\w-])(\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+))?", re.IGNORECASE)
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_DELIM_CELL_RE = re.compile(r"^\s*:?-+:?\s*$")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(\s*<?([^\s()<>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_ESCAPABLE = r"!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~"
_PLACEHOLDER = "\x00{}\x00"


# ---------------------------------------------------------------------------
# public API


def apply_compact_tables(rich_message: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of an ``InputRichMessage`` dict with every table compact.

    - ``blocks``: every ``{"type": "table"}`` block (nested ones included)
      gets ``is_compact: True``;
    - ``html``: every ``<table …>`` tag gets the ``compact`` attribute;
    - ``markdown``: GFM pipe tables become ``<table bordered compact>`` HTML
      blocks (header row as ``th``, column alignment as ``align``, cells
      converted to inline HTML); raw ``<table>`` tags only get ``compact``.
    Fenced code is left untouched.  Other keys are copied as-is.
    """

    out = deepcopy(rich_message)
    blocks = out.get("blocks")
    if isinstance(blocks, list):
        out["blocks"] = [_mark_blocks(block, compact=True) for block in blocks]
    value = out.get("html")
    if isinstance(value, str) and value:
        out["html"] = compact_html_tables(value)
    value = out.get("markdown")
    if isinstance(value, str) and value:
        out["markdown"] = compact_markdown_tables(value)
    return out


def strip_compact_tables(rich_message: dict[str, Any]) -> dict[str, Any]:
    """Inverse of :func:`apply_compact_tables` for the format-downgrade retry."""

    out = deepcopy(rich_message)
    blocks = out.get("blocks")
    if isinstance(blocks, list):
        out["blocks"] = [_mark_blocks(block, compact=False) for block in blocks]
    for key in ("html", "markdown"):
        value = out.get(key)
        if isinstance(value, str) and value:
            out[key] = _rewrite_table_tags(value, compact=False, skip_fences=key == "markdown")
    return out


def rich_message_has_compact_tables(rich_message: Any) -> bool:
    if not isinstance(rich_message, dict):
        return False
    blocks = rich_message.get("blocks")
    if isinstance(blocks, list) and any(_has_compact_block(block) for block in blocks):
        return True
    for key in ("html", "markdown"):
        value = rich_message.get(key)
        if isinstance(value, str) and "table" in value.lower():
            for match in _TABLE_TAG_RE.finditer(value):
                if _COMPACT_ATTR_RE.search(match.group(1)):
                    return True
    return False


def compact_html_tables(text: str) -> str:
    return _rewrite_table_tags(text, compact=True, skip_fences=False)


def compact_markdown_tables(text: str) -> str:
    """Rewrite GFM pipe tables in Rich Markdown as ``<table bordered compact>`` blocks."""

    lines = text.split("\n")
    out: list[str] = []
    fence: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        fence_match = _FENCE_RE.match(line)
        if fence is not None:
            out.append(line)
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = None
            index += 1
            continue
        if fence_match:
            fence = fence_match.group(1)
            out.append(line)
            index += 1
            continue
        table = _match_table(lines, index)
        if table is None:
            out.append(_rewrite_line_table_tags(line, compact=True))
            index += 1
            continue
        header, aligns, rows, end = table
        if out and out[-1].strip():
            out.append("")
        out.append(_table_html(header, aligns, rows))
        if end < len(lines) and lines[end].strip():
            out.append("")
        index = end
    return "\n".join(out)


def is_compact_unsupported_error(exc: BaseException | str) -> bool:
    """Telegram rejected the request in a way an older Bot API / layer would for compact.

    Only definite rejections (``Bad Request`` / HTTP 400 descriptions) qualify, so
    the caller knows the first request was not delivered.  Network errors,
    timeouts, 5xx and flood waits never match.
    """

    text = str(exc or "").lower()
    if not text:
        return False
    if any(marker in text for marker in ("not modified", "too many requests", "flood", "timeout", "timed out")):
        return False
    code = getattr(exc, "code", None)
    definite = text.startswith("bad request") or "400" in text.split(":", 1)[0] or code == 400
    if not definite:
        return False
    return any(
        marker in text
        for marker in (
            "compact",
            "rich message",
            "rich_message",
            "can't parse",
            "unsupported",
            "unknown",
            "table",
            "invalid",
        )
    )


# ---------------------------------------------------------------------------
# blocks


def _mark_blocks(value: Any, *, compact: bool) -> Any:
    if isinstance(value, list):
        return [_mark_blocks(item, compact=compact) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "cells":
            result[key] = item  # cell texts are RichText, never blocks
        elif isinstance(item, (list, dict)):
            result[key] = _mark_blocks(item, compact=compact)
        else:
            result[key] = item
    if str(value.get("type") or "") == "table" and "cells" in value:
        if compact:
            result["is_compact"] = True
        else:
            result.pop("is_compact", None)
    return result


def _has_compact_block(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_compact_block(item) for item in value)
    if not isinstance(value, dict):
        return False
    if str(value.get("type") or "") == "table" and value.get("is_compact"):
        return True
    return any(_has_compact_block(item) for key, item in value.items() if key != "cells" and isinstance(item, (list, dict)))


# ---------------------------------------------------------------------------
# html tags


def _rewrite_table_tags(text: str, *, compact: bool, skip_fences: bool) -> str:
    if "table" not in text.lower():
        return text
    if not skip_fences:
        return _TABLE_TAG_RE.sub(lambda m: _table_tag(m, compact=compact), text)
    lines = text.split("\n")
    fence: str | None = None
    for i, line in enumerate(lines):
        fence_match = _FENCE_RE.match(line)
        if fence is not None:
            if fence_match and fence_match.group(1)[0] == fence[0] and len(fence_match.group(1)) >= len(fence):
                fence = None
            continue
        if fence_match:
            fence = fence_match.group(1)
            continue
        lines[i] = _rewrite_line_table_tags(line, compact=compact)
    return "\n".join(lines)


def _rewrite_line_table_tags(line: str, *, compact: bool) -> str:
    if "<table" not in line.lower():
        return line
    # leave inline code spans alone
    parts = re.split(r"(`+[^`]*`+)", line)
    for i, part in enumerate(parts):
        if i % 2 == 0:
            parts[i] = _TABLE_TAG_RE.sub(lambda m: _table_tag(m, compact=compact), part)
    return "".join(parts)


def _table_tag(match: re.Match[str], *, compact: bool) -> str:
    attrs = match.group(1)
    self_closing = attrs.rstrip().endswith("/")
    if compact:
        if _COMPACT_ATTR_RE.search(attrs):
            return match.group(0)
        body = attrs.rstrip()
        if self_closing:
            body = body[:-1].rstrip()
        return f"<table{body} compact{' /' if self_closing else ''}>"
    if not _COMPACT_ATTR_RE.search(attrs):
        return match.group(0)
    cleaned = re.sub(r"\s+", " ", _COMPACT_ATTR_RE.sub("", attrs)).rstrip()
    if cleaned and not cleaned.startswith(" "):
        cleaned = " " + cleaned
    return f"<table{cleaned}>"


# ---------------------------------------------------------------------------
# markdown tables


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = 0
    i = 0
    while i < len(body):
        char = body[i]
        if char == "\\" and i + 1 < len(body) and body[i + 1] == "|":
            current.append("\\|")
            i += 2
            continue
        if char == "`":
            run = len(body[i:]) - len(body[i:].lstrip("`"))
            in_code = 0 if in_code == run else (run if not in_code else in_code)
            current.append("`" * run)
            i += run
            continue
        if char == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        i += 1
    cells.append("".join(current).strip())
    return cells


def _is_row_candidate(line: str) -> bool:
    if not line.strip() or "|" not in line:
        return False
    return len(line) - len(line.lstrip(" ")) < 4


def _match_table(lines: list[str], start: int) -> tuple[list[str], list[str | None], list[list[str]], int] | None:
    if start + 1 >= len(lines):
        return None
    header_line, delim_line = lines[start], lines[start + 1]
    if not _is_row_candidate(header_line) or not _is_row_candidate(delim_line):
        return None
    delims = _split_row(delim_line)
    if not delims or not all(_DELIM_CELL_RE.match(cell) for cell in delims):
        return None
    header = _split_row(header_line)
    if len(header) != len(delims):
        return None
    aligns: list[str | None] = []
    for cell in delims:
        cell = cell.strip()
        left, right = cell.startswith(":"), cell.endswith(":")
        aligns.append("center" if left and right else "right" if right else "left" if left else None)
    rows: list[list[str]] = []
    end = start + 2
    while end < len(lines) and _is_row_candidate(lines[end]) and not _FENCE_RE.match(lines[end]):
        cells = _split_row(lines[end])
        cells = (cells + [""] * len(header))[: len(header)]
        rows.append(cells)
        end += 1
    return header, aligns, rows, end


def _table_html(header: list[str], aligns: list[str | None], rows: list[list[str]]) -> str:
    def cell(tag: str, value: str, align: str | None) -> str:
        attr = f' align="{align}"' if align else ""
        return f"<{tag}{attr}>{_inline_to_html(value)}</{tag}>"

    # Pipe tables render with borders by default; keep them when going compact.
    parts = [f"<table {PIPE_TABLE_ATTRS} compact>"]
    parts.append("<tr>" + "".join(cell("th", value, aligns[i]) for i, value in enumerate(header)) + "</tr>")
    for row in rows:
        parts.append("<tr>" + "".join(cell("td", value, aligns[i]) for i, value in enumerate(row)) + "</tr>")
    parts.append("</table>")
    return "\n".join(parts)


def _inline_to_html(text: str) -> str:
    """Convert one table cell's inline Rich Markdown to Rich HTML."""

    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return _PLACEHOLDER.format(len(stash) - 1)

    text = text.replace("\\|", "|")
    # code spans first: their content is literal
    text = re.sub(
        r"(`+)(.+?)\1",
        lambda m: keep(f"<code>{html.escape(m.group(2).strip(), quote=False)}</code>"),
        text,
    )
    # backslash escapes
    text = re.sub(rf"\\([{_ESCAPABLE}])", lambda m: keep(html.escape(m.group(1), quote=False)), text)

    def link(match: re.Match[str]) -> str:
        label = match.group(1)
        url = match.group(2)
        return keep(f'<a href="{html.escape(url, quote=True)}">') + label + keep("</a>")

    text = _LINK_RE.sub(link, text)
    # author-written inline HTML and entities pass through (Telegram parses them)
    text = _HTML_TAG_RE.sub(lambda m: keep(m.group(0)), text)
    text = _ENTITY_RE.sub(lambda m: keep(m.group(0)), text)
    text = html.escape(text, quote=False)
    for pattern, tag in (
        (r"\*\*(?=\S)(.+?)(?<=\S)\*\*", "b"),
        (r"(?<![\w])__(?=\S)(.+?)(?<=\S)__(?![\w])", "b"),
        (r"~~(?=\S)(.+?)(?<=\S)~~", "s"),
        (r"\|\|(?=\S)(.+?)(?<=\S)\|\|", "tg-spoiler"),
        (r"==(?=\S)(.+?)(?<=\S)==", "mark"),
        (r"(?<![\*\w])\*(?=\S)([^*]+?)(?<=\S)\*(?![\*\w])", "i"),
        (r"(?<![\w_])_(?=\S)([^_]+?)(?<=\S)_(?![\w_])", "i"),
    ):
        text = re.sub(pattern, lambda m, tag=tag: f"<{tag}>{m.group(1)}</{tag}>", text)
    for _ in range(3):  # placeholders may nest (link label inside stash)
        if "\x00" not in text:
            break
        text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
    return text
