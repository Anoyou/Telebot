"""图片字节工具：格式识别、尺寸读取和 Telegram 文件封装。"""

from __future__ import annotations

import base64
import io
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageInfo:
    data: bytes
    mime_type: str
    width: int
    height: int
    extension: str


def image_ext_from_bytes(data: bytes, preferred_format: str = "png") -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if preferred_format == "jpeg":
        return ".jpg"
    if preferred_format == "webp":
        return ".webp"
    return ".png"


def mime_from_bytes(data: bytes, preferred_format: str = "png") -> str:
    ext = image_ext_from_bytes(data, preferred_format)
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/png")


def infer_image_size(data: bytes) -> tuple[int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data.startswith(b"\xff\xd8\xff"):
        return _jpeg_size(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_size(data)
    return 1024, 1024


def _jpeg_size(data: bytes) -> tuple[int, int]:
    pos = 2
    size = len(data)
    while pos + 9 < size:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        pos += 2
        if marker in {0xD8, 0xD9}:
            continue
        if pos + 2 > size:
            break
        length = struct.unpack(">H", data[pos:pos + 2])[0]
        if length < 2 or pos + length > size:
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            height = struct.unpack(">H", data[pos + 3:pos + 5])[0]
            width = struct.unpack(">H", data[pos + 5:pos + 7])[0]
            return width, height
        pos += length
    return 1024, 1024


def _webp_size(data: bytes) -> tuple[int, int]:
    if len(data) < 30:
        return 1024, 1024
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8 " and len(data) >= 30:
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return width, height
    if chunk == b"VP8L" and len(data) >= 25:
        b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
        width = 1 + (((b1 & 0x3F) << 8) | b0)
        height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
        return width, height
    return 1024, 1024


def image_info(data: bytes, preferred_format: str = "png") -> ImageInfo:
    mime_type = mime_from_bytes(data, preferred_format)
    width, height = infer_image_size(data)
    return ImageInfo(
        data=data,
        mime_type=mime_type,
        width=width,
        height=height,
        extension=image_ext_from_bytes(data, preferred_format),
    )


def decode_base64_image(value: str, preferred_format: str = "png") -> ImageInfo:
    raw = str(value or "").strip()
    payload = raw.split(",", 1)[1] if raw.startswith("data:") and "," in raw else raw
    data = base64.b64decode(payload)
    return image_info(data, preferred_format)


def telegram_file(data: bytes, filename: str) -> io.BytesIO:
    buf = io.BytesIO(data)
    buf.name = filename
    return buf


__all__ = [
    "ImageInfo",
    "decode_base64_image",
    "image_ext_from_bytes",
    "image_info",
    "telegram_file",
]
