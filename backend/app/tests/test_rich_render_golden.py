"""出站表格外观 golden 对比：中间件化（0.109.0）前后输出逐字节一致。

``golden/rich_render_golden.json`` 由 0.108.0-beta.1（cf2241b9）生成：边框 / 斑马纹 / 紧凑表格、
旧 Bot API 降级重发、Userbot 中立输入，覆盖 blocks / html / markdown 与 5 种动作写法。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.tests.golden.render_outputs import compute_outputs

GOLDEN = Path(__file__).parent / "golden" / "rich_render_golden.json"


@pytest.fixture(scope="module")
def outputs() -> dict:
    logging.disable(logging.CRITICAL)
    try:
        return compute_outputs()
    finally:
        logging.disable(logging.NOTSET)


def test_golden_keys_match(outputs: dict) -> None:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(outputs) == sorted(expected)


@pytest.mark.parametrize("case", sorted(json.loads(GOLDEN.read_text(encoding="utf-8"))))
def test_rich_render_output_unchanged(outputs: dict, case: str) -> None:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))[case]
    assert outputs[case] == expected
