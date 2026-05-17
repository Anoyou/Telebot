from __future__ import annotations

import base64
import json

import plugins.installed.codex_image.plugin as codex_image_plugin
from plugins.installed.codex_image.manifest import MANIFEST
from plugins.installed.codex_image.plugin import (
    CodexImagePlugin,
    _edit_html,
    _extract_codex_artifacts,
    _humanize_codex_error,
    _humanize_codex_exception,
    _image_ext_from_bytes,
    _parse_generation_args,
    _safe_error_text,
    _with_error_prefix,
)

from app.worker.plugins.base import PluginContext


def test_codex_usage_limit_error_is_human_readable() -> None:
    detail = json.dumps(
        {
            "error": {
                "type": "usage_limit_reached",
                "message": "The usage limit has been reached",
                "plan_type": "free",
                "resets_in_seconds": 600077,
            }
        }
    )
    msg = _humanize_codex_error(429, detail)
    assert "额度已用完" in msg
    assert "free" in msg
    assert "6天" in msg
    assert "usage_limit_reached" not in msg


def test_codex_error_redacts_sensitive_values_and_paths() -> None:
    msg = _safe_error_text(
        "Bearer abcdefghijklmn failed with access_token=abcdefghijklmnopqrstuvwxyz at /Users/me/.codex/auth.json"
    )
    assert "abcdefghijklmn" not in msg
    assert "abcdefghijklmnopqrstuvwxyz" not in msg
    assert "/Users/me" not in msg
    assert "<path>" in msg


def test_codex_stream_error_is_human_readable() -> None:
    msg = _humanize_codex_exception(
        RuntimeError("peer closed connection without sending complete message body (incomplete chunked read)")
    )
    assert "流式连接中断" in msg
    assert "incomplete chunked read" not in msg


def test_codex_html_error_is_human_readable() -> None:
    msg = _humanize_codex_error(
        403,
        "<html><head><meta name='viewport'></head><body>Please enable JavaScript</body></html>",
        content_type="text/html; charset=utf-8",
    )
    assert "网页而不是 API 数据" in msg
    assert "<html>" not in msg
    assert "Access Token" in msg


def test_codex_error_prefix_is_not_duplicated() -> None:
    assert _with_error_prefix("❌ 已失败") == "❌ 已失败"
    assert _with_error_prefix("已失败") == "❌ 已失败"


def test_codex_completed_response_result_is_extracted() -> None:
    png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode("ascii")
    image, revised, error = _extract_codex_artifacts(
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "image_generation_call",
                        "result": png_b64,
                        "revised_prompt": "cat",
                    }
                ]
            },
        }
    )
    assert image == png_b64
    assert revised == "cat"
    assert error is None


def test_codex_image_command_options_are_extracted() -> None:
    class Ctx:
        config = {
            "image_size": "1024x1024",
            "aspect_ratio": "1:1",
            "image_format": "png",
        }

    prompt, opts = _parse_generation_args(
        ["--比例", "4:3", "--size=1536x1024", "--format", "jpeg", "云海", "城市"],
        Ctx(),  # type: ignore[arg-type]
    )
    assert prompt == "云海 城市"
    assert opts == {
        "image_size": "1536x1024",
        "aspect_ratio": "4:3",
        "image_format": "jpeg",
    }


def test_codex_image_ext_detects_image_magic_bytes() -> None:
    assert _image_ext_from_bytes(b"\x89PNG\r\n\x1a\nxxxx", "jpeg") == ".png"
    assert _image_ext_from_bytes(b"\xff\xd8\xffxxxx", "png") == ".jpg"
    assert _image_ext_from_bytes(b"RIFFxxxxWEBPxxxx", "png") == ".webp"


def test_codex_image_manifest_is_marked_experimental() -> None:
    assert MANIFEST.experimental is True
    assert MANIFEST.to_dict()["x-experimental"] is True


async def test_codex_status_edit_uses_html_parse_mode() -> None:
    class Event:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        async def edit(self, text: str, **kwargs: str) -> None:
            self.calls.append((text, kwargs))

    event = Event()
    await _edit_html(event, "<b>状态:</b> 正在生成")
    assert event.calls == [("<b>状态:</b> 正在生成", {"parse_mode": "html"})]


async def test_codex_status_edit_fallback_strips_html_tags() -> None:
    class Event:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, str]]] = []

        async def edit(self, text: str, **kwargs: str) -> None:
            self.calls.append((text, kwargs))
            if kwargs.get("parse_mode") == "html":
                raise RuntimeError("bad html")

    event = Event()
    await _edit_html(event, "<b>状态:</b> 正在生成")
    assert event.calls[-1] == ("状态: 正在生成", {})


async def test_codex_image_send_uses_explicit_reply_to_id(monkeypatch) -> None:
    class Client:
        def __init__(self) -> None:
            self.files: list[tuple[int, str, dict[str, object]]] = []

        async def send_file(self, chat_id: int, image_file, **kwargs: object) -> None:
            self.files.append((chat_id, image_file.name, kwargs))

    class Event:
        chat_id = 12345
        id = 67890

        def __init__(self) -> None:
            self.edits: list[tuple[str, dict[str, object]]] = []
            self.deleted = False

        async def edit(self, text: str, **kwargs: object) -> None:
            self.edits.append((text, kwargs))

        async def delete(self) -> None:
            self.deleted = True

    async def fake_call_codex_image(**kwargs: object) -> dict[str, str]:
        png_header = b"\x89PNG\r\n\x1a\n" + b"0" * 16
        return {
            "image_base64": base64.b64encode(png_header).decode("ascii"),
            "revised_prompt": "",
            "status": "completed",
            "response_id": "resp_test",
        }

    monkeypatch.setattr(codex_image_plugin, "_call_codex_image", fake_call_codex_image)

    client = Client()
    event = Event()
    logs: list[tuple[str, str, dict[str, object]]] = []

    async def log(level: str, message: str, **detail: object) -> None:
        logs.append((level, message, detail))

    ctx = PluginContext(
        account_id=1,
        feature_key="codex_image",
        config={
            "access_token": "test-token",
            "delete_command_message": False,
            "status_interval_seconds": 300,
        },
        client=client,  # type: ignore[arg-type]
        log=log,
    )

    plugin = CodexImagePlugin()
    await plugin._cmd_generate(
        ctx,
        "画一只猫",
        event,
        {"image_size": "1024x1024", "aspect_ratio": "1:1", "image_format": "png"},
        None,
        reply_to_id=999,
    )

    assert client.files
    assert client.files[0][1].endswith(".png")
    assert client.files[0][2]["reply_to"] == 999
    assert event.edits[-1][0] == "✅ 图片生成完成"
    assert any(message == "[codex_image] generation completed" for _, message, _ in logs)
