"""Rich 表格渲染 golden 语料（0.109.0 中间件化前后输出必须逐字节一致）。

``rich_render_golden.json`` 由 0.108.0-beta.1（cf2241b9，中间件化之前）的实现生成，
测试 ``test_rich_render_golden.py`` 用当前实现重新计算并逐项比对。
"""

from __future__ import annotations

from typing import Any

LONG_ROWS = "\n".join(f"| r{i} | **b{i}** | `c{i}` |" for i in range(1200))

CORPUS: dict[str, dict[str, Any]] = {
    "blocks_basic": {
        "blocks": [
            {"type": "paragraph", "text": "hi"},
            {"type": "table", "cells": [[{"text": "a"}, {"text": "b"}], [{"text": "1"}, {"text": "2"}]]},
        ]
    },
    "blocks_explicit_false": {
        "blocks": [
            {"type": "table", "is_bordered": False, "cells": [[{"text": "a"}]]},
            {"type": "table", "is_striped": False, "is_compact": True, "cells": [[{"text": "b"}]]},
        ]
    },
    "blocks_nested": {
        "blocks": [
            {
                "type": "details",
                "summary": "s",
                "blocks": [{"type": "table", "cells": [[{"text": "x", "type": "table"}]]}],
            }
        ]
    },
    "html_plain": {"html": "<p>t</p><table><tr><td>1</td></tr></table>"},
    "html_attrs": {
        "html": '<table bordered="false" striped><tr><td>1</td></tr></table>\n<table class="x" compact><tr><td>2</td></tr></table>'
    },
    "html_self_closing": {"html": "<table/><table striped='0' />"},
    "html_no_table": {"html": "<b>no tables here</b>"},
    "markdown_pipe": {"markdown": "# T\n| a | b |\n|:--|--:|\n| 1 | **2** |\n| `x|y` | [l](https://e.x) |\ntext after"},
    "markdown_center_and_escape": {"markdown": "| h \\| x | c |\n|:-:|---|\n| ~~s~~ | ||sp|| ==m== _i_ *j* |"},
    "markdown_fence": {"markdown": "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```\n\n| c | d |\n|---|---|\n| 3 | 4 |"},
    "markdown_raw_table": {"markdown": "before\n<table><tr><td>raw</td></tr></table>\n`<table>` inline code"},
    "markdown_not_table": {"markdown": "a | b\nno delimiter row"},
    "markdown_long": {"markdown": "| a | b | c |\n|---|---|---|\n" + LONG_ROWS},
    "mixed_other_keys": {"markdown": "| a |\n|---|\n| 1 |", "skip_entities": True, "extra": {"k": [1, 2]}},
    "empty": {},
}

ACTION_VARIANTS: dict[str, dict[str, Any]] = {
    "send_rich": {"type": "send_rich_message", "chat_id": -100123},
    "send_rich_compact": {"type": "send_rich_message", "chat_id": -100123, "compact_tables": True},
    "send_rich_compact_false": {"type": "send_rich_message", "chat_id": -100123, "compact_tables": False},
    "edit_inline_compact": {"type": "edit_message", "inline_message_id": "AAA", "compact_tables": True},
    "send_rich_userbot_compact": {
        "type": "send_rich_message",
        "chat_id": -100123,
        "send_via": "userbot_reply",
        "compact_tables": True,
    },
}
