"""Codex Desktop 官方版本检测：Sparkle 更新源 + 安装包内 codex-package.json。

全部使用本地构造的 appcast 与 zip，经 ``httpx.MockTransport`` 模拟 Range 读取，
不访问网络。
"""

from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from app.api import commands
from app.services import codex_desktop_release as release
from app.services import llm_identity

ZIP_URL = "https://persistent.oaistatic.com/codex-app-prod/ChatGPT-darwin-arm64-26.1021.31345.zip"


def _appcast(items: list[tuple[str, int, str]]) -> bytes:
    body = "".join(
        f"""
        <item>
            <title>{short}</title>
            <sparkle:version>{build}</sparkle:version>
            <sparkle:shortVersionString>{short}</sparkle:shortVersionString>
            <enclosure url="{url}" length="1" type="application/octet-stream" />
        </item>"""
        for short, build, url in items
    )
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle" version="2.0">'
        f"<channel><title>Codex</title>{body}</channel></rss>"
    ).encode()


def _zip(core_version: str | None, *, padding: int = 200_000) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        # 前置一个较大的成员，确保目标文件不在尾部读取窗口内，必须走中央目录定位。
        archive.writestr("ChatGPT.app/Contents/Resources/app.asar", b"x" * padding)
        if core_version is not None:
            archive.writestr(
                "ChatGPT.app/Contents/Resources/codex-cli/codex-package.json",
                json.dumps({"layoutVersion": 1, "version": core_version, "variant": "codex"}),
            )
        archive.writestr("ChatGPT.app/Contents/Info.plist", b"<plist/>")
    return buffer.getvalue()


class _Server:
    def __init__(self, appcast: bytes, archive: bytes, *, honour_range: bool = True) -> None:
        self.appcast = appcast
        self.archive = archive
        self.honour_range = honour_range
        self.requests: list[tuple[str, str, str | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        rng = request.headers.get("range")
        self.requests.append((request.method, url, rng))
        if url == release.APPCAST_URL:
            return httpx.Response(200, content=self.appcast)
        if url == ZIP_URL:
            if request.method == "HEAD":
                return httpx.Response(200, headers={"content-length": str(len(self.archive))})
            if not self.honour_range or not rng:
                return httpx.Response(200, content=self.archive)
            start_text, end_text = rng.removeprefix("bytes=").split("-")
            start, end = int(start_text), int(end_text)
            return httpx.Response(206, content=self.archive[start : end + 1])
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def test_parse_appcast_picks_highest_sparkle_version() -> None:
    parsed = release.parse_appcast(
        _appcast(
            [
                ("26.1016.62342", 21477, ZIP_URL),
                ("26.1021.31345", 21828, ZIP_URL),
                ("26.803.81509", 9000, ZIP_URL),
            ]
        )
    )
    assert parsed.build == "26.1021.31345"
    assert parsed.sparkle_version == 21828
    assert parsed.archive_url == ZIP_URL


def test_parse_appcast_rejects_feed_without_versions() -> None:
    with pytest.raises(release.CodexDesktopReleaseError):
        release.parse_appcast(_appcast([("not-a-version", 1, ZIP_URL)]))
    with pytest.raises(release.CodexDesktopReleaseError):
        release.parse_appcast(b"<rss><channel>")


def test_parse_core_package_validates_version() -> None:
    assert release.parse_core_package(b'{"version": "0.164.0-alpha.11.1"}') == "0.164.0-alpha.11.1"
    with pytest.raises(release.CodexDesktopReleaseError):
        release.parse_core_package(b'{"version": "0.1; rm -rf /"}')
    with pytest.raises(release.CodexDesktopReleaseError):
        release.parse_core_package(b"not json")


@pytest.mark.asyncio
async def test_detect_reads_build_and_core_with_range_requests_only() -> None:
    server = _Server(_appcast([("26.1021.31345", 21828, ZIP_URL)]), _zip("0.164.0-alpha.11.1"))
    async with server.client() as client:
        latest = await release.detect_codex_desktop_latest(client)

    assert latest.build == "26.1021.31345"
    assert latest.core == "0.164.0-alpha.11.1"
    assert latest.build_error is None and latest.core_error is None
    zip_gets = [item for item in server.requests if item[0] == "GET" and item[1] == ZIP_URL]
    assert zip_gets and all(rng is not None for _method, _url, rng in zip_gets)
    # 两个版本号都通过 Codex Desktop UA 版本校验。
    assert llm_identity.is_valid_version(latest.build)
    assert llm_identity.is_valid_version(latest.core)


@pytest.mark.asyncio
async def test_detect_refuses_full_download_when_range_unsupported() -> None:
    server = _Server(
        _appcast([("26.1021.31345", 21828, ZIP_URL)]),
        _zip("0.164.0-alpha.11.1"),
        honour_range=False,
    )
    async with server.client() as client:
        latest = await release.detect_codex_desktop_latest(client)

    assert latest.build == "26.1021.31345"
    assert latest.core is None
    assert latest.core_error and "分段读取" in latest.core_error


@pytest.mark.asyncio
async def test_detect_reports_missing_core_package() -> None:
    server = _Server(_appcast([("26.1021.31345", 21828, ZIP_URL)]), _zip(None))
    async with server.client() as client:
        latest = await release.detect_codex_desktop_latest(client)

    assert latest.build == "26.1021.31345"
    assert latest.core is None
    assert latest.core_error and "codex-package.json" in latest.core_error


@pytest.mark.asyncio
async def test_detect_rejects_untrusted_archive_host() -> None:
    evil = "https://example.com/ChatGPT.zip"
    server = _Server(_appcast([("26.1021.31345", 21828, evil)]), _zip("0.164.0-alpha.11.1"))
    async with server.client() as client:
        latest = await release.detect_codex_desktop_latest(client)

    assert latest.build == "26.1021.31345"
    assert latest.core is None
    assert latest.core_error and "官方下载域名" in latest.core_error
    assert all(url != evil for _method, url, _rng in server.requests)


@pytest.mark.asyncio
async def test_detect_degrades_when_appcast_unavailable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        latest = await release.detect_codex_desktop_latest(client)

    assert latest.build is None and latest.core is None
    assert latest.build_error and "503" in latest.build_error
    assert latest.core_error == latest.build_error


@pytest.mark.asyncio
async def test_detect_endpoint_maps_desktop_keys_from_one_lookup(monkeypatch) -> None:
    calls: list[str] = []

    async def _desktop() -> release.CodexDesktopLatest:
        calls.append("desktop")
        return release.CodexDesktopLatest(build="26.1021.31345", core="0.164.0-alpha.11.1")

    async def _registry(registry: str) -> tuple[str | None, str | None]:
        assert not registry.startswith("appcast:")
        return "9.9.9", None

    monkeypatch.setattr(commands, "_detect_codex_desktop_latest", _desktop)
    monkeypatch.setattr(commands, "_detect_registry_latest", _registry)

    response = await commands.detect_client_identity_versions(_user=None)
    by_key = {item.key: item for item in response.items}

    assert calls == ["desktop"]
    assert by_key["codex_desktop_build"].latest == "26.1021.31345"
    assert by_key["codex_desktop_core"].latest == "0.164.0-alpha.11.1"
    assert by_key["codex_tui"].latest == "9.9.9"
    assert set(by_key) == set(llm_identity.version_key_metadata())


@pytest.mark.asyncio
async def test_detect_endpoint_surfaces_desktop_errors_per_key(monkeypatch) -> None:
    async def _desktop() -> release.CodexDesktopLatest:
        return release.CodexDesktopLatest(build="26.1021.31345", core=None, core_error="安装包结构已变化")

    async def _registry(_registry: str) -> tuple[str | None, str | None]:
        return None, "offline"

    monkeypatch.setattr(commands, "_detect_codex_desktop_latest", _desktop)
    monkeypatch.setattr(commands, "_detect_registry_latest", _registry)

    response = await commands.detect_client_identity_versions(_user=None)
    by_key = {item.key: item for item in response.items}

    assert by_key["codex_desktop_build"].latest == "26.1021.31345"
    assert by_key["codex_desktop_build"].error is None
    assert by_key["codex_desktop_core"].latest is None
    assert by_key["codex_desktop_core"].error == "安装包结构已变化"
