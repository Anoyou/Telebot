"""读取 OpenAI 官方 Codex Desktop（ChatGPT 桌面 App）最新发布的版本号。

Codex Desktop 的 UA 由两个版本段组成（见 ``llm_identity._codex_desktop_user_agent``）：

- ``codex_desktop_build``：桌面 App 的公开版本号，例如 ``26.1021.31345``；
- ``codex_desktop_core``：App 内置 Codex 核心（codex-rs）的版本，例如
  ``0.164.0-alpha.11.1``。

两者都没有 npm / PyPI 一类的“最新版本”接口，但都能从官方公开发布物中确定：

1. 官方 Sparkle 更新源 ``https://persistent.oaistatic.com/codex-app-prod/appcast.xml``
   （桌面 App 自身检查更新使用的静态 feed）。取 ``sparkle:version`` 最大的条目，
   其 ``sparkle:shortVersionString`` 即最新 App 版本号，``enclosure`` 指向官方 zip。
2. 官方 zip 内 ``ChatGPT.app/Contents/Resources/codex-cli/codex-package.json`` 的
   ``version`` 字段是 App 实际捆绑的 Codex 核心版本。zip 有数百 MB，这里只用 HTTP
   Range 读取尾部中央目录与这一个小文件（约 1 MB 流量），不下载整个安装包。

只读、不落库、不执行任何下载内容；只信任 ``persistent.oaistatic.com`` 的 HTTPS 地址。
"""

from __future__ import annotations

import json
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass

import httpx

APPCAST_URL = "https://persistent.oaistatic.com/codex-app-prod/appcast.xml"
TRUSTED_HOST = "persistent.oaistatic.com"
CORE_PACKAGE_SUFFIX = "/Contents/Resources/codex-cli/codex-package.json"

_SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
_MAX_APPCAST_BYTES = 2 * 1024 * 1024
_EOCD_SEARCH_BYTES = 66 * 1024  # EOCD 最多带 64 KiB 注释
_MAX_CENTRAL_DIRECTORY_BYTES = 16 * 1024 * 1024
_MAX_MEMBER_BYTES = 64 * 1024
_BUILD_RE = re.compile(r"^\d+\.\d+\.\d+$")
_CORE_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[A-Za-z]+(?:\.\d+)*)?$")


class CodexDesktopReleaseError(Exception):
    """官方发布信息无法读取或格式不符合预期。"""


@dataclass(frozen=True)
class AppcastRelease:
    build: str
    sparkle_version: int
    archive_url: str


@dataclass(frozen=True)
class CodexDesktopLatest:
    build: str | None
    core: str | None
    build_error: str | None = None
    core_error: str | None = None


def parse_appcast(xml_text: str | bytes) -> AppcastRelease:
    """解析 Sparkle appcast，返回 ``sparkle:version`` 最大的条目。"""

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise CodexDesktopReleaseError(f"更新源不是有效的 XML：{exc}") from None
    best: AppcastRelease | None = None
    for item in root.iter("item"):
        short = (item.findtext(f"{{{_SPARKLE_NS}}}shortVersionString") or "").strip()
        raw_version = (item.findtext(f"{{{_SPARKLE_NS}}}version") or "").strip()
        enclosure = item.find("enclosure")
        url = (enclosure.get("url") if enclosure is not None else "") or ""
        if not _BUILD_RE.fullmatch(short) or not raw_version.isdigit():
            continue
        candidate = AppcastRelease(build=short, sparkle_version=int(raw_version), archive_url=url.strip())
        if best is None or candidate.sparkle_version > best.sparkle_version:
            best = candidate
    if best is None:
        raise CodexDesktopReleaseError("更新源中没有可识别的版本条目")
    return best


def _require_trusted_url(url: str) -> str:
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or parsed.host != TRUSTED_HOST:
        raise CodexDesktopReleaseError("更新源给出的安装包地址不是 OpenAI 官方下载域名")
    return str(parsed)


def find_eocd(tail: bytes, tail_offset: int) -> tuple[int, int]:
    """从 zip 尾部字节中找出中央目录的 (偏移, 大小)，支持 Zip64。"""

    index = tail.rfind(b"PK\x05\x06")
    if index < 0 or len(tail) - index < 22:
        raise CodexDesktopReleaseError("安装包不是可识别的 zip 格式")
    _sig, _disk, _cd_disk, _n_disk, _n_total, cd_size, cd_offset, _comment = struct.unpack(
        "<IHHHHIIH", tail[index : index + 22]
    )
    if cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF:
        locator = tail.rfind(b"PK\x06\x07", 0, index)
        if locator < 0 or index - locator < 20:
            raise CodexDesktopReleaseError("安装包 Zip64 结构不完整")
        _sig, _disk, zip64_eocd_offset, _disks = struct.unpack("<IIQI", tail[locator : locator + 20])
        relative = zip64_eocd_offset - tail_offset
        if relative < 0 or relative + 56 > len(tail) or tail[relative : relative + 4] != b"PK\x06\x06":
            raise CodexDesktopReleaseError("安装包 Zip64 结构不完整")
        cd_size, cd_offset = struct.unpack("<QQ", tail[relative + 40 : relative + 56])
    return int(cd_offset), int(cd_size)


@dataclass(frozen=True)
class ZipMember:
    name: str
    method: int
    compressed_size: int
    uncompressed_size: int
    local_header_offset: int


def find_member(central_directory: bytes, suffix: str) -> ZipMember | None:
    """在中央目录里查找以 ``suffix`` 结尾的条目。"""

    pos = 0
    while pos + 46 <= len(central_directory) and central_directory[pos : pos + 4] == b"PK\x01\x02":
        fields = struct.unpack("<IHHHHHHIIIHHHHHII", central_directory[pos : pos + 46])
        method, compressed, uncompressed = fields[4], fields[8], fields[9]
        name_len, extra_len, comment_len = fields[10], fields[11], fields[12]
        local_offset = fields[16]
        name = central_directory[pos + 46 : pos + 46 + name_len].decode("utf-8", errors="replace")
        if name.endswith(suffix):
            if 0xFFFFFFFF in (compressed, uncompressed, local_offset):
                extra = central_directory[pos + 46 + name_len : pos + 46 + name_len + extra_len]
                compressed, uncompressed, local_offset = _apply_zip64_extra(
                    extra, compressed, uncompressed, local_offset
                )
            return ZipMember(name, method, compressed, uncompressed, local_offset)
        pos += 46 + name_len + extra_len + comment_len
    return None


def _apply_zip64_extra(extra: bytes, compressed: int, uncompressed: int, offset: int) -> tuple[int, int, int]:
    pos = 0
    while pos + 4 <= len(extra):
        header_id, size = struct.unpack("<HH", extra[pos : pos + 4])
        data = extra[pos + 4 : pos + 4 + size]
        if header_id == 0x0001:
            cursor = 0
            if uncompressed == 0xFFFFFFFF and cursor + 8 <= len(data):
                uncompressed = struct.unpack("<Q", data[cursor : cursor + 8])[0]
                cursor += 8
            if compressed == 0xFFFFFFFF and cursor + 8 <= len(data):
                compressed = struct.unpack("<Q", data[cursor : cursor + 8])[0]
                cursor += 8
            if offset == 0xFFFFFFFF and cursor + 8 <= len(data):
                offset = struct.unpack("<Q", data[cursor : cursor + 8])[0]
            break
        pos += 4 + size
    return compressed, uncompressed, offset


def decode_member(member: ZipMember, local_header_and_data: bytes) -> bytes:
    """从“本地文件头 + 数据”字节中解出成员内容（仅支持存储 / deflate）。"""

    if local_header_and_data[:4] != b"PK\x03\x04" or len(local_header_and_data) < 30:
        raise CodexDesktopReleaseError("安装包内文件头损坏")
    name_len, extra_len = struct.unpack("<HH", local_header_and_data[26:30])
    start = 30 + name_len + extra_len
    data = local_header_and_data[start : start + member.compressed_size]
    if len(data) < member.compressed_size:
        raise CodexDesktopReleaseError("安装包内文件读取不完整")
    if member.method == 0:
        return data
    if member.method == 8:
        try:
            return zlib.decompress(data, -15)
        except zlib.error as exc:
            raise CodexDesktopReleaseError(f"安装包内文件解压失败：{exc}") from None
    raise CodexDesktopReleaseError(f"安装包内文件使用了不支持的压缩方式 {member.method}")


def parse_core_package(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CodexDesktopReleaseError("codex-package.json 不是有效的 JSON") from None
    version = str(payload.get("version") or "").strip() if isinstance(payload, dict) else ""
    if not _CORE_RE.fullmatch(version):
        raise CodexDesktopReleaseError("codex-package.json 没有可识别的核心版本号")
    return version


async def _read_limited(response: httpx.Response, limit: int, too_large: str) -> bytes:
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise CodexDesktopReleaseError(too_large)
    return bytes(buffer)


async def _get_range(client: httpx.AsyncClient, url: str, start: int, end: int) -> bytes:
    """读取 ``[start, end]`` 字节；服务器忽略 Range 时立即放弃，绝不下载整个安装包。"""

    async with client.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as response:
        if response.status_code != 206:
            raise CodexDesktopReleaseError(f"安装包不支持分段读取（HTTP {response.status_code}）")
        return await _read_limited(response, end - start + 1, "安装包分段读取返回了超出请求范围的数据")


async def read_core_version(client: httpx.AsyncClient, archive_url: str) -> str:
    """用 HTTP Range 从官方 zip 中只读出 codex-package.json 的版本号。"""

    url = _require_trusted_url(archive_url)
    head = await client.head(url)
    if head.status_code != 200:
        raise CodexDesktopReleaseError(f"安装包地址返回 HTTP {head.status_code}")
    try:
        total = int(head.headers.get("content-length") or "0")
    except ValueError:
        total = 0
    if total <= 0:
        raise CodexDesktopReleaseError("安装包没有返回文件大小")
    tail_start = max(0, total - _EOCD_SEARCH_BYTES)
    tail = await _get_range(client, url, tail_start, total - 1)
    cd_offset, cd_size = find_eocd(tail, tail_start)
    if cd_size <= 0 or cd_size > _MAX_CENTRAL_DIRECTORY_BYTES or cd_offset + cd_size > total:
        raise CodexDesktopReleaseError("安装包目录大小异常")
    central_directory = await _get_range(client, url, cd_offset, cd_offset + cd_size - 1)
    member = find_member(central_directory, CORE_PACKAGE_SUFFIX)
    if member is None:
        raise CodexDesktopReleaseError("安装包中没有 codex-cli/codex-package.json（App 结构可能已变化）")
    if member.compressed_size > _MAX_MEMBER_BYTES or member.uncompressed_size > _MAX_MEMBER_BYTES:
        raise CodexDesktopReleaseError("codex-package.json 大小异常")
    # 本地文件头 30 字节 + 文件名/扩展字段（各最多 64 KiB，这里留足 1 KiB 通常足够；
    # 不足时按实际长度补读一次）。
    window = 30 + 1024 + member.compressed_size
    chunk = await _get_range(client, url, member.local_header_offset, member.local_header_offset + window - 1)
    if len(chunk) >= 30:
        name_len, extra_len = struct.unpack("<HH", chunk[26:30])
        needed = 30 + name_len + extra_len + member.compressed_size
        if needed > len(chunk):
            chunk = await _get_range(
                client, url, member.local_header_offset, member.local_header_offset + needed - 1
            )
    return parse_core_package(decode_member(member, chunk))


async def detect_codex_desktop_latest(client: httpx.AsyncClient | None = None) -> CodexDesktopLatest:
    """查询官方最新 Codex Desktop App 版本与其内置核心版本（逐项降级，不抛异常）。"""

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    try:
        try:
            async with client.stream("GET", APPCAST_URL) as response:
                if response.status_code != 200:
                    raise CodexDesktopReleaseError(f"官方更新源返回 HTTP {response.status_code}")
                body = await _read_limited(response, _MAX_APPCAST_BYTES, "官方更新源内容过大")
            release = parse_appcast(body)
        except CodexDesktopReleaseError as exc:
            return CodexDesktopLatest(build=None, core=None, build_error=str(exc), core_error=str(exc))
        except httpx.HTTPError as exc:
            message = f"官方更新源请求失败：{type(exc).__name__}"
            return CodexDesktopLatest(build=None, core=None, build_error=message, core_error=message)
        try:
            core = await read_core_version(client, release.archive_url)
        except CodexDesktopReleaseError as exc:
            return CodexDesktopLatest(build=release.build, core=None, core_error=str(exc))
        except httpx.HTTPError as exc:
            return CodexDesktopLatest(
                build=release.build,
                core=None,
                core_error=f"官方安装包读取失败：{type(exc).__name__}",
            )
        return CodexDesktopLatest(build=release.build, core=core)
    finally:
        if owns_client:
            await client.aclose()


__all__ = [
    "APPCAST_URL",
    "AppcastRelease",
    "CodexDesktopLatest",
    "CodexDesktopReleaseError",
    "detect_codex_desktop_latest",
    "parse_appcast",
    "parse_core_package",
    "read_core_version",
]
