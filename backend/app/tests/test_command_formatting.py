from __future__ import annotations

from app.worker.commands.formatting import (
    _ensure_html_safe,
    _safe_exception_text,
    _split_long_message,
)


def test_safe_exception_text_redacts_paths_and_tokens() -> None:
    msg = _safe_exception_text(
        RuntimeError("boom /Users/me/app.py sk-abcdef0123456789 Bearer secret_token")
    )
    assert "/Users/me/app.py" not in msg
    assert "sk-abcdef0123456789" not in msg
    assert "Bearer secret_token" not in msg


def test_split_long_message_chunks_are_bounded() -> None:
    parts = _split_long_message("a" * 9000, threshold=3000)
    assert len(parts) == 3
    assert all(len(part) <= 3000 for part in parts)


def test_ensure_html_safe_closes_open_tags() -> None:
    safe = _ensure_html_safe("<b>hello")
    assert safe.endswith("</b>")
