"""Rich table styling: default borders + stripes, Bot API 10.3 compact tables.

Official contract (core.telegram.org/bots/api):

- ``InputRichBlockTable`` / ``RichBlockTable``: ``is_bordered`` ("Pass True if
  the table has borders"), ``is_striped`` ("Pass True if the table is
  striped"), ``is_compact`` ("Pass True if table cells must have smaller
  indents").  All three are optional fields of type ``True``.  Rich Messages
  (and ``RichBlockTable``) appeared in Bot API 10.1 (2026-06-11), input
  ``blocks`` in 10.2; the 10.3 changelog (2026-08-24) adds only
  ``is_compact`` to the table classes.  ``bordered`` / ``striped`` are
  therefore understood by every Bot API / layer that accepts a rich message
  at all — only ``compact`` needs a downgrade retry.
- Rich HTML: ``<table bordered striped compact>``.
- Rich Markdown has no table flag, but "can contain arbitrary HTML" and
  "Markdown isn't parsed inside block HTML tags other than <details>,
  <tg-collage> and <tg-slideshow>" — so a styled Markdown table is written as
  an HTML ``<table bordered striped>`` block whose cells are inline HTML.

Platform default (0.106.0-beta.15): every outgoing rich-message table is
bordered and striped.  :func:`apply_table_style_defaults` runs at the
transport layer (Bot API ``_call_rich_bot_api`` and the Userbot raw request
builder):

- ``blocks``: a ``table`` block without ``is_bordered`` / ``is_striped`` gets
  ``True``; a key the caller set (including ``False``) is kept as written;
- ``html`` (and raw ``<table>`` tags inside Markdown): a missing ``bordered`` /
  ``striped`` attribute is added.  HTML boolean attributes cannot say
  "false", so ``bordered="false"`` / ``striped="0"`` (``false|0|no|off``) is
  read as an explicit opt-out and the attribute is removed before sending;
- ``markdown``: GFM pipe tables become ``<table bordered striped>`` blocks
  (header row as ``th``, column alignment as ``align``).  If the converted
  text would exceed the 32768-character rich text limit the Markdown is sent
  unchanged.

``compact_tables`` (plugin opt-in) additionally adds ``is_compact`` /
``compact``; the downgrade retry strips only ``compact`` and keeps
``bordered`` / ``striped``.  Helpers here never emit ``is_compact: false``.
"""

from __future__ import annotations

import html
import re
from copy import deepcopy
from typing import Any

# Action-level opt-in understood by the plugin action normalizer.
COMPACT_TABLES_ACTION_KEY = "compact_tables"
# Style attributes every outgoing table gets unless the caller set them (see module doc).
TABLE_STYLE_ATTRS: tuple[str, ...] = ("bordered", "striped")
TABLE_STYLE_BLOCK_KEYS: tuple[str, ...] = ("is_bordered", "is_striped")
# Attributes a converted GFM pipe table carries (``compact`` is appended when requested).
PIPE_TABLE_ATTRS = " ".join(TABLE_STYLE_ATTRS)
# Rich text limit (characters, Bot API); a styled Markdown conversion never pushes past it.
_RICH_TEXT_CHAR_LIMIT = 32_768
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})

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


def apply_table_style_defaults(rich_message: Any) -> Any:
    """Return a copy of an ``InputRichMessage`` dict whose tables are bordered + striped.

    Platform default for every outgoing rich message (see module doc): caller
    values win, only missing flags are filled; ``bordered="false"`` style HTML
    attributes are treated as an explicit opt-out and removed.  Non-dict input
    is returned untouched.
    """

    if not isinstance(rich_message, dict):
        return rich_message
    out = deepcopy(rich_message)
    blocks = out.get("blocks")
    if isinstance(blocks, list):
        out["blocks"] = [_style_blocks(block) for block in blocks]
    value = out.get("html")
    if isinstance(value, str) and value:
        out["html"] = style_html_tables(value)
    value = out.get("markdown")
    if isinstance(value, str) and value:
        converted = style_markdown_tables(value)
        if len(converted) <= _RICH_TEXT_CHAR_LIMIT or len(converted) <= len(value):
            out["markdown"] = converted
    return out


def style_html_tables(text: str) -> str:
    """Add missing ``bordered`` / ``striped`` to every ``<table>`` tag (honouring ``="false"``)."""

    return _rewrite_table_tags(text, op="style", skip_fences=False)


def style_markdown_tables(text: str) -> str:
    """Rewrite GFM pipe tables as ``<table bordered striped>`` blocks; style raw ``<table>`` tags."""

    return _convert_markdown_tables(text, compact=False)


def apply_compact_tables(rich_message: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of an ``InputRichMessage`` dict with every table compact.

    - ``blocks``: every ``{"type": "table"}`` block (nested ones included)
      gets ``is_compact: True``;
    - ``html``: every ``<table …>`` tag gets the ``compact`` attribute;
    - ``markdown``: GFM pipe tables become ``<table bordered striped compact>``
      HTML blocks (header row as ``th``, column alignment as ``align``, cells
      converted to inline HTML); raw ``<table>`` tags only get ``compact``
      (their ``bordered`` / ``striped`` defaults are filled at send time by
      :func:`apply_table_style_defaults`).
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
            out[key] = _rewrite_table_tags(value, op="strip", skip_fences=key == "markdown")
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
    return _rewrite_table_tags(text, op="compact", skip_fences=False)


def compact_markdown_tables(text: str) -> str:
    """Rewrite GFM pipe tables in Rich Markdown as ``<table bordered striped compact>`` blocks."""

    return _convert_markdown_tables(text, compact=True)


def _convert_markdown_tables(text: str, *, compact: bool) -> str:
    op = "compact" if compact else "style"
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
            out.append(_rewrite_line_table_tags(line, op=op))
            index += 1
            continue
        header, aligns, rows, end = table
        if out and out[-1].strip():
            out.append("")
        out.append(_table_html(header, aligns, rows, compact=compact))
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


def _style_blocks(value: Any) -> Any:
    if isinstance(value, list):
        return [_style_blocks(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key != "cells" and isinstance(item, (list, dict)):
            result[key] = _style_blocks(item)
        else:
            result[key] = item  # cell texts are RichText, never blocks
    if str(value.get("type") or "") == "table" and "cells" in value:
        for flag in TABLE_STYLE_BLOCK_KEYS:
            if flag not in result:  # explicit True/False from the caller wins
                result[flag] = True
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


def _rewrite_table_tags(text: str, *, op: str, skip_fences: bool) -> str:
    if "table" not in text.lower():
        return text
    if not skip_fences:
        return _TABLE_TAG_RE.sub(lambda m: _table_tag(m, op=op), text)
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
        lines[i] = _rewrite_line_table_tags(line, op=op)
    return "\n".join(lines)


def _rewrite_line_table_tags(line: str, *, op: str) -> str:
    if "<table" not in line.lower():
        return line
    # leave inline code spans alone
    parts = re.split(r"(`+[^`]*`+)", line)
    for i, part in enumerate(parts):
        if i % 2 == 0:
            parts[i] = _TABLE_TAG_RE.sub(lambda m: _table_tag(m, op=op), part)
    return "".join(parts)


def _attr_re(name: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<!\S){name}(?![\w-])(\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+))?",
        re.IGNORECASE,
    )


_STYLE_ATTR_RES = {name: _attr_re(name) for name in TABLE_STYLE_ATTRS}


def _style_table_tag(match: re.Match[str]) -> str:
    attrs = match.group(1)
    body = attrs.rstrip()
    self_closing = body.endswith("/")
    if self_closing:
        body = body[:-1].rstrip()
    changed = False
    for name in TABLE_STYLE_ATTRS:
        found = _STYLE_ATTR_RES[name].search(body)
        if found is None:
            body = f"{body} {name}"
            changed = True
            continue
        raw_value = found.group(2)
        if raw_value is not None and raw_value.strip("\"'").strip().lower() in _FALSE_VALUES:
            # explicit opt-out: HTML has no boolean false, so drop the attribute
            body = body[: found.start()] + body[found.end() :]
            changed = True
    if not changed:
        return match.group(0)
    body = re.sub(r"\s+", " ", body).rstrip()
    if body and not body.startswith(" "):
        body = " " + body
    return f"<table{body}{' /' if self_closing else ''}>"


def _table_tag(match: re.Match[str], *, op: str) -> str:
    if op == "style":
        return _style_table_tag(match)
    compact = op == "compact"
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


def _table_html(
    header: list[str],
    aligns: list[str | None],
    rows: list[list[str]],
    *,
    compact: bool = False,
) -> str:
    def cell(tag: str, value: str, align: str | None) -> str:
        attr = f' align="{align}"' if align else ""
        return f"<{tag}{attr}>{_inline_to_html(value)}</{tag}>"

    # Platform default: every table bordered + striped; compact only on opt-in.
    parts = [f"<table {PIPE_TABLE_ATTRS}{' compact' if compact else ''}>"]
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
