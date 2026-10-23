"""阶段 B 测试：plugin_install_service 的 zip 解析 / 验签 / 安装 / 升级 / 卸载。

覆盖：
- ``parse_zip`` 正常路径（顶层布局）
- ``parse_zip`` "外面包了一层目录"自动展开
- ``parse_zip`` 缺少必含文件 → InvalidZipStructure
- ``parse_zip`` zip slip（含 ../）拒绝
- ``parse_zip`` 体积超限拒绝
- ``parse_zip`` manifest.key 与 builtin 冲突 → KeyConflict
- ``parse_zip`` manifest.py 内不导出 MANIFEST → ManifestError
- ``verify_signature`` 三态：无 sig / 通过 / 失败
- ``install_zip`` 正常入库 + 文件落盘
- ``install_zip`` 升级（同 key 第二次安装覆盖目录、保留 enabled）
- ``set_enabled`` 签名失败时拒绝启用
- ``uninstall`` 删表 + 删目录
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.datastructures import UploadFile

from app.api import plugins_install as plugins_install_api
from app.db.models.feature import FEATURE_FORWARD, AccountFeature, Feature
from app.db.models.plugin import PLUGIN_SOURCE_ZIP, InstalledPlugin, PluginInstallHistory
from app.db.models.plugin_global_config import PluginGlobalConfig
from app.deps import get_current_user, get_db
from app.main import app
from app.services import plugin_install_service as pis


# ─────────────────────────────────────────────────────
# Fake DB：超薄实现，仅支持 InstalledPlugin 的 get/add/delete/flush/execute(select)
# ─────────────────────────────────────────────────────
class _FakeDB:
    """用 dict/list 模拟本测试覆盖到的最小表行为。"""

    def __init__(self) -> None:
        self.installed_rows: dict[str, InstalledPlugin] = {}
        self.features: dict[str, Feature] = {}
        self.account_features: list[AccountFeature] = []
        self.global_configs: dict[str, PluginGlobalConfig] = {}
        self.install_history: list[PluginInstallHistory] = []
        self.committed = False

    async def get(self, model, pk):  # noqa: ANN001
        if model is InstalledPlugin:
            return self.installed_rows.get(pk)
        if model is Feature:
            return self.features.get(pk)
        if model is PluginGlobalConfig:
            return self.global_configs.get(pk)
        return None

    def add(self, obj) -> None:  # noqa: ANN001
        if isinstance(obj, InstalledPlugin):
            self.installed_rows[obj.key] = obj
        elif isinstance(obj, Feature):
            self.features[obj.key] = obj
        elif isinstance(obj, AccountFeature):
            self.account_features.append(obj)
        elif isinstance(obj, PluginGlobalConfig):
            self.global_configs[obj.plugin_key] = obj
        elif isinstance(obj, PluginInstallHistory):
            self.install_history.append(obj)

    async def delete(self, obj) -> None:  # noqa: ANN001
        if isinstance(obj, InstalledPlugin):
            self.installed_rows.pop(obj.key, None)
        elif isinstance(obj, Feature):
            self.features.pop(obj.key, None)
        elif isinstance(obj, AccountFeature):
            self.account_features = [row for row in self.account_features if row is not obj]
        elif isinstance(obj, PluginGlobalConfig):
            self.global_configs.pop(obj.plugin_key, None)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None

    async def execute(self, stmt):  # noqa: ANN001
        model = stmt.column_descriptions[0].get("entity")
        if model is InstalledPlugin:
            return _FakeResult(list(self.installed_rows.values()))
        if model is AccountFeature:
            key = _where_value(stmt)
            return _FakeResult(
                [row for row in self.account_features if row.feature_key == key]
            )
        if model is PluginInstallHistory:
            key = _where_value(stmt)
            return _FakeResult(
                [row for row in reversed(self.install_history) if row.plugin_key == key]
            )
        return _FakeResult([])


def _where_value(stmt) -> object | None:  # noqa: ANN001
    for criterion in getattr(stmt, "_where_criteria", ()):
        value = getattr(getattr(criterion, "right", None), "value", None)
        if value is not None:
            return value
    return None


class _FakeResult:
    def __init__(self, items: list) -> None:
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeScalars:
    def __init__(self, items: list) -> None:
        self._items = items

    def all(self) -> list:
        return list(self._items)


# ─────────────────────────────────────────────────────
# 工具：构造一个最小可用的合法 zip
# ─────────────────────────────────────────────────────
def _make_zip(
    *,
    key: str = "demo",
    version: str = "0.1.0",
    layout: str = "flat",  # flat | nested(包一层 outer/) | missing-plugin | missing-init | missing-plugin-json
    include_platform_capabilities: bool = True,
    extra_members: list[tuple[str, bytes]] | None = None,
    plugin_json_override: bytes | None = None,
) -> bytes:
    """生成一个最小化的插件 zip 字节流。"""
    declaration = (
        ", requires_platform_capabilities=[]"
        if include_platform_capabilities
        else ""
    )
    manifest_py = (
        "from app.worker.plugins.manifest import Manifest\n"
        f"MANIFEST = Manifest(key={key!r}, display_name='Demo', version={version!r}{declaration})\n"
    ).encode()
    init_py = (
        b"from .plugin import DemoPlugin\n"
        b"from .manifest import MANIFEST\n"
        b"PLUGIN_CLASS = DemoPlugin\n"
    )
    plugin_py = (
        "from app.worker.plugins.base import Plugin, register\n"
        "@register\n"
        "class DemoPlugin(Plugin):\n"
        f"    key = {key!r}\n"
        "    display_name = 'Demo'\n"
    ).encode()
    # plugin.json 是 worker 加载（_preflight_installed_plugin）的硬性要求，
    # 合法包必须带上；usage 字段用于避免"未声明使用说明"的 lint warning。
    if plugin_json_override is not None:
        plugin_json = plugin_json_override
    else:
        plugin_json = json.dumps(
            {
                "name": key,
                "display_name": "Demo",
                "version": version,
                "usage": "单元测试用示例插件。",
            },
            ensure_ascii=False,
        ).encode("utf-8")

    files: dict[str, bytes] = {
        "manifest.py": manifest_py,
        "__init__.py": init_py,
        "plugin.py": plugin_py,
        "plugin.json": plugin_json,
    }
    if layout == "missing-plugin":
        files.pop("plugin.py")
    elif layout == "missing-init":
        files.pop("__init__.py")
    elif layout == "missing-plugin-json":
        files.pop("plugin.json")

    prefix = ""
    if layout == "nested":
        prefix = "outer/"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(prefix + name, data)
        if extra_members:
            for name, data in extra_members:
                zf.writestr(name, data)
    return buf.getvalue()


# ─────────────────────────────────────────────────────
# parse_zip 正常路径
# ─────────────────────────────────────────────────────
def test_parse_zip_flat_layout() -> None:
    z = _make_zip(key="hello_world", version="1.2.3")
    parsed = pis.parse_zip(z)
    try:
        assert parsed.manifest.key == "hello_world"
        assert parsed.manifest.version == "1.2.3"
        assert (parsed.extract_dir / "manifest.py").exists()
        assert (parsed.extract_dir / "plugin.py").exists()
        assert (parsed.extract_dir / "__init__.py").exists()
        # plugin.json 与其他必含文件一起落盘
        assert (parsed.extract_dir / "plugin.json").exists()
    finally:
        shutil.rmtree(parsed.extract_dir, ignore_errors=True)


def test_parse_zip_nested_layout_auto_flattened() -> None:
    """打包者外面包了一层目录 → 自动展开。"""
    z = _make_zip(key="nested_demo", layout="nested")
    parsed = pis.parse_zip(z)
    try:
        assert parsed.manifest.key == "nested_demo"
        # 展平到顶层
        assert (parsed.extract_dir / "manifest.py").exists()
        # outer 子目录应已被消化掉
        assert not (parsed.extract_dir / "outer").exists()
    finally:
        shutil.rmtree(parsed.extract_dir, ignore_errors=True)


def test_parse_zip_missing_required_file() -> None:
    z = _make_zip(layout="missing-plugin")
    with pytest.raises(pis.InvalidZipStructure) as ex:
        pis.parse_zip(z)
    assert ex.value.code == "MISSING_REQUIRED_FILE"


def test_parse_zip_path_traversal_rejected() -> None:
    """包内成员含 ``..`` 必须被拒绝。"""
    z = _make_zip(extra_members=[("../escape.txt", b"x")])
    with pytest.raises(pis.InvalidZipStructure) as ex:
        pis.parse_zip(z)
    assert ex.value.code in ("ZIP_PATH_TRAVERSAL", "ZIP_ABS_PATH")


def test_parse_zip_too_large(monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugin_zip_max_bytes", 50)
    z = _make_zip(key="too_big")
    with pytest.raises(pis.ZipTooLarge):
        pis.parse_zip(z)


def test_parse_zip_key_conflicts_builtin() -> None:
    z = _make_zip(key=FEATURE_FORWARD)
    with pytest.raises(pis.KeyConflict) as ex:
        pis.parse_zip(z)
    assert ex.value.code == "KEY_CONFLICTS_BUILTIN"


def test_parse_zip_requires_plugin_json() -> None:
    """缺 plugin.json 的 zip 必须在安装阶段被拒：worker 加载时（_preflight）必拒，装上即坏件。"""
    z = _make_zip(key="no_plugin_json", layout="missing-plugin-json")
    with pytest.raises(pis.InvalidZipStructure) as ex:
        pis.parse_zip(z)
    assert ex.value.code == "MISSING_REQUIRED_FILE"


def test_parse_zip_rejects_unparsable_plugin_json() -> None:
    """plugin.json 不是合法 JSON 时拒绝。"""
    z = _make_zip(
        key="broken_plugin_json",
        plugin_json_override=b"{not-json",
    )
    with pytest.raises(pis.InvalidZipStructure) as ex:
        pis.parse_zip(z)
    assert ex.value.code == "BAD_PLUGIN_JSON"


def test_parse_zip_rejects_plugin_json_without_key() -> None:
    """plugin.json 缺少可用的 name/key 字段时拒绝。"""
    z = _make_zip(
        key="no_key_plugin_json",
        plugin_json_override=json.dumps({"display_name": "Demo"}).encode(),
    )
    with pytest.raises(pis.InvalidZipStructure) as ex:
        pis.parse_zip(z)
    assert ex.value.code == "BAD_PLUGIN_JSON_KEY"


def test_parse_zip_rejects_plugin_json_key_mismatch() -> None:
    """plugin.json 的静态 key 与 manifest.key 不一致时拒绝（与 worker 加载期检查对齐）。"""
    z = _make_zip(
        key="mismatch_a",
        plugin_json_override=json.dumps({"name": "mismatch_b", "version": "0.1.0"}).encode(),
    )
    with pytest.raises(pis.ManifestError) as ex:
        pis.parse_zip(z)
    assert ex.value.code == "PLUGIN_JSON_KEY_MISMATCH"


def test_parse_zip_manifest_missing_const(tmp_path) -> None:
    """manifest.py 不导出 MANIFEST → ManifestError。"""
    bad_manifest = b"x = 1\n"
    init_py = b"PLUGIN_CLASS = None\nMANIFEST = None\n"
    plugin_py = b"class X: pass\n"
    plugin_json = json.dumps({"name": "bad_manifest_demo", "version": "0.1.0"}).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.py", bad_manifest)
        zf.writestr("__init__.py", init_py)
        zf.writestr("plugin.py", plugin_py)
        zf.writestr("plugin.json", plugin_json)
    with pytest.raises(pis.ManifestError) as ex:
        pis.parse_zip(buf.getvalue())
    assert ex.value.code in ("MANIFEST_MISSING_CONST", "BAD_MANIFEST")


def test_parse_zip_rejects_new_install_without_platform_capability_declaration() -> None:
    z = _make_zip(key="missing_caps", include_platform_capabilities=False)
    with pytest.raises(pis.ManifestError) as ex:
        pis.parse_zip(z)
    assert ex.value.code == "PLATFORM_CAPABILITIES_REQUIRED"


# ─────────────────────────────────────────────────────
# 签名校验三态
# ─────────────────────────────────────────────────────
def _ed25519_keypair():
    """生成一对 Ed25519 测试密钥（PEM 公钥 + 原始私钥），仅在测试用。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return priv, pub_pem


def _sign_payload(payload: bytes) -> tuple[bytes, str]:
    """返回 payload 的有效签名和对应公钥。"""
    priv, pub_pem = _ed25519_keypair()
    return priv.sign(payload), pub_pem


def _csrf_headers() -> dict[str, str]:
    return {
        "X-Requested-With": "telepilot-ui",
        "X-CSRF-Token": "test-token",
        "Cookie": "csrf_token=test-token",
    }


def _install_api_overrides(
    db: _FakeDB, monkeypatch
) -> tuple[list[tuple[str, str]], dict]:  # noqa: ANN001
    audit_events: list[tuple[str, str]] = []
    previous_overrides = dict(app.dependency_overrides)

    async def _override_db():
        yield db

    async def _override_user():
        return SimpleNamespace(id=42)

    async def _fake_audit_write(_db, user_id, action, target=None, detail=None):  # noqa: ANN001, ARG001
        audit_events.append((action, target))

    async def _fake_broadcast(_db) -> int:  # noqa: ANN001
        return 0

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    monkeypatch.setattr(plugins_install_api.audit, "write", _fake_audit_write)
    monkeypatch.setattr(plugins_install_api, "_broadcast_reload_config", _fake_broadcast)
    return audit_events, previous_overrides


def _restore_api_overrides(previous_overrides: dict) -> None:
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)


def test_verify_signature_none_when_missing() -> None:
    assert pis.verify_signature(b"x", None, "PEM...") is None
    assert pis.verify_signature(b"x", b"sig", None) is None


def test_verify_signature_valid_and_invalid() -> None:
    priv, pub_pem = _ed25519_keypair()
    payload = b"hello plugin"
    sig = priv.sign(payload)
    assert pis.verify_signature(payload, sig, pub_pem) is True
    # 篡改 payload 后必失败
    assert pis.verify_signature(payload + b"x", sig, pub_pem) is False


@pytest.mark.asyncio
async def test_installed_plugin_changelog_api_reads_file_and_reports_missing(tmp_path, monkeypatch) -> None:
    installed_root = tmp_path / "installed"
    plugin_root = installed_root / "with_log"
    plugin_root.mkdir(parents=True)
    (plugin_root / "CHANGELOG.md").write_text("# 更新日志\n\n## 1.0.0\n- 首次发布\n", encoding="utf-8")
    missing_root = installed_root / "without_log"
    missing_root.mkdir()
    large_root = installed_root / "large_log"
    large_root.mkdir()
    (large_root / "CHANGELOG.md").write_bytes(b"x" * (256 * 1024 + 32))

    monkeypatch.setattr(plugins_install_api.settings, "plugins_installed_dir", str(installed_root))
    db = _FakeDB()
    db.installed_rows["with_log"] = InstalledPlugin(
        key="with_log",
        source="repo",
        installed_path=str(plugin_root),
        version="1.0.0",
        enabled=True,
        signature_ok=True,
        trust_tier="community",
        lint_warnings=[],
    )
    db.installed_rows["without_log"] = InstalledPlugin(
        key="without_log",
        source="repo",
        installed_path=str(missing_root),
        version="1.0.0",
        enabled=True,
        signature_ok=True,
        trust_tier="community",
        lint_warnings=[],
    )
    db.installed_rows["large_log"] = InstalledPlugin(
        key="large_log",
        source="repo",
        installed_path=str(large_root),
        version="1.0.0",
        enabled=True,
        signature_ok=True,
        trust_tier="community",
        lint_warnings=[],
    )
    _events, previous_overrides = _install_api_overrides(db, monkeypatch)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            available = await client.get(
                "/api/plugins/install/with_log/changelog",
                headers=_csrf_headers(),
            )
            missing = await client.get(
                "/api/plugins/install/without_log/changelog",
                headers=_csrf_headers(),
            )
            large = await client.get(
                "/api/plugins/install/large_log/changelog",
                headers=_csrf_headers(),
            )
    finally:
        _restore_api_overrides(previous_overrides)

    assert available.status_code == 200
    assert available.json()["available"] is True
    assert "首次发布" in available.json()["content"]
    assert missing.status_code == 200
    assert missing.json() == {
        "key": "without_log",
        "available": False,
        "content": "",
        "truncated": False,
        "message": "该插件未提供 CHANGELOG.md。",
    }
    assert large.status_code == 200
    assert large.json()["available"] is True
    assert large.json()["truncated"] is True
    assert len(large.json()["content"].encode("utf-8")) == 256 * 1024


@pytest.mark.asyncio
async def test_read_zip_upload_bounded_aborts_before_reading_whole_body(monkeypatch) -> None:
    """超限请求必须在读取阶段中止，不允许把整个 body 读进内存。"""
    monkeypatch.setattr(plugins_install_api.settings, "plugin_zip_max_bytes", 8)
    read_calls = 0

    class _CountingStream:
        """每次 read 都声称还有更多数据，验证循环会提前抛错。"""

        def read(self, size: int = -1) -> bytes:  # noqa: ANN001
            nonlocal read_calls
            read_calls += 1
            return b"x" * size

        def seek(self, offset: int) -> None:  # noqa: ANN001
            return None

        def close(self) -> None:
            return None

    upload = UploadFile(file=_CountingStream(), filename="big.zip", size=None)

    with pytest.raises(pis.ZipTooLarge) as ex:
        await plugins_install_api._read_zip_upload_bounded(upload)

    assert ex.value.code == "ZIP_TOO_LARGE"
    # 上限 8 字节 + 8 字节块 → 最多再读一块就中止，不会读穿整个 body。
    assert read_calls <= 2


@pytest.mark.asyncio
async def test_read_zip_upload_bounded_reads_all_bytes_within_limit() -> None:
    """未超限时按分块读取并还原完整字节，且不触发上限判定。"""
    payload = os.urandom(300 * 1024)
    original = plugins_install_api.settings.plugin_zip_max_bytes
    plugins_install_api.settings.plugin_zip_max_bytes = len(payload)
    try:
        with io.BytesIO(payload) as stream:
            upload = UploadFile(file=stream, filename="ok.zip", size=None)
            data = await plugins_install_api._read_zip_upload_bounded(upload)
    finally:
        plugins_install_api.settings.plugin_zip_max_bytes = original
    assert data == payload


# ─────────────────────────────────────────────────────
# upload API
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_upload_plugin_package_api_installs_zip_with_signature_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    db = _FakeDB()
    audit_events, previous_overrides = _install_api_overrides(db, monkeypatch)
    z = _make_zip(key="api_upload", version="1.0.0")
    sig, pub = _sign_payload(z)
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/plugins/install/upload",
                headers=_csrf_headers(),
                files={
                    "file": ("api_upload.zip", z, "application/zip"),
                    "signature_file": ("api_upload.sig", sig, "application/octet-stream"),
                },
            )
    finally:
        _restore_api_overrides(previous_overrides)

    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "api_upload"
    assert body["version"] == "1.0.0"
    assert body["signature_ok"] is True
    assert db.committed is True
    assert "api_upload" in db.installed_rows
    assert audit_events == [("plugin.install_upload", "plugin:api_upload")]


@pytest.mark.asyncio
async def test_upload_plugin_package_api_returns_signature_failed_for_bad_signature_field(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    db = _FakeDB()
    _, previous_overrides = _install_api_overrides(db, monkeypatch)
    z = _make_zip(key="api_bad_sig", version="1.0.0")
    _, pub = _ed25519_keypair()
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/plugins/install/upload",
                headers=_csrf_headers(),
                files={"file": ("api_bad_sig.zip", z, "application/zip")},
                data={"signature": base64.b64encode(b"\x00" * 64).decode("ascii")},
            )
    finally:
        _restore_api_overrides(previous_overrides)

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "SIGNATURE_FAILED"
    assert db.committed is False
    assert db.installed_rows == {}


@pytest.mark.asyncio
async def test_upload_plugin_package_api_returns_413_for_oversize_zip(tmp_path, monkeypatch) -> None:
    """超过上限的 zip 必须在读取阶段就 413，且不进入安装流程。"""
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    monkeypatch.setattr(pis.settings, "plugin_zip_max_bytes", 512)
    db = _FakeDB()
    _, previous_overrides = _install_api_overrides(db, monkeypatch)
    z = _make_zip(key="api_oversize", extra_members=[("pad.bin", b"x" * 64)])
    sig, pub = _sign_payload(z)
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/plugins/install/upload",
                headers=_csrf_headers(),
                files={"file": ("api_oversize.zip", z, "application/zip")},
            )
    finally:
        _restore_api_overrides(previous_overrides)

    assert r.status_code == 413
    assert r.json()["error"]["code"] == "ZIP_TOO_LARGE"
    assert db.committed is False
    assert db.installed_rows == {}


@pytest.mark.asyncio
async def test_upload_plugin_package_api_accepts_zip_within_limit(tmp_path, monkeypatch) -> None:
    """恰好在上限之内的 zip 仍然正常安装（413 判定为严格大于）。"""
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    db = _FakeDB()
    _, previous_overrides = _install_api_overrides(db, monkeypatch)
    z = _make_zip(key="api_at_limit", version="1.0.0")
    sig, pub = _sign_payload(z)
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)
    # 上限正好等于 zip 体积：不得误判为超限。
    monkeypatch.setattr(pis.settings, "plugin_zip_max_bytes", len(z))

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/plugins/install/upload",
                headers=_csrf_headers(),
                files={
                    "file": ("api_at_limit.zip", z, "application/zip"),
                    "signature_file": ("api_at_limit.sig", sig, "application/octet-stream"),
                },
            )
    finally:
        _restore_api_overrides(previous_overrides)

    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "api_at_limit"
    assert db.installed_rows["api_at_limit"].source == "zip"


# ─────────────────────────────────────────────────────
# install_zip 正常落盘 + 升级 + 卸载
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_install_zip_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    z = _make_zip(key="my_demo", version="1.0.0")
    sig, pub = _sign_payload(z)
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)
    db = _FakeDB()
    row = await pis.install_zip(db, zip_bytes=z, signature=sig)

    assert row.key == "my_demo"
    assert row.version == "1.0.0"
    assert row.signature_ok is True
    assert row.enabled is False
    target = Path(row.installed_path)
    assert target.is_dir()
    assert (target / "manifest.py").is_file()
    assert (target / "plugin.py").is_file()
    installed = db.installed_rows["my_demo"]
    assert installed.source == "zip"
    assert installed.version == "1.0.0"
    assert installed.installed_path == str(target)
    assert installed.signature_ok is True
    assert installed.trust_tier == "verified"
    assert installed.source_label == "ZIP"
    assert installed.last_install_error is None
    assert installed.lint_warnings == []


@pytest.mark.asyncio
async def test_install_zip_keeps_concurrent_staging_and_cleans_stale_residue(
    tmp_path, monkeypatch
) -> None:
    """并发安装的随机 staging 目录互不删除；只有过期残留会被顺带清理。

    串行模拟交错：A 已创建随机 staging（进行中）→ B 同 key 全量安装走完 →
    A 的 staging 仍在（A 可恢复；最终 rename 竞争行为保持现状，由重试收口）。
    """
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    installed_root = tmp_path / "installed"
    installed_root.mkdir(parents=True)
    final_dir = installed_root / "race_demo"

    # A 流程：并发安装已创建自己的随机 staging（进行中）
    a_staging = pis._new_staging_dir(final_dir)
    assert a_staging.name.startswith("race_demo.installing.")
    a_staging.mkdir(parents=True)
    a_marker = a_staging / "a-marker.txt"
    a_marker.write_text("A in progress", encoding="utf-8")

    # 历史残留：旧固定名 + 旧随机名，修改时间拨回 2 小时前
    stale_legacy = installed_root / "race_demo.installing"
    stale_legacy.mkdir()
    (stale_legacy / "leftover").write_text("crash leftover", encoding="utf-8")
    stale_random = pis._new_staging_dir(final_dir)
    stale_random.mkdir()
    old_ts = time.time() - 7200
    os.utime(stale_legacy, (old_ts, old_ts))
    os.utime(stale_random, (old_ts, old_ts))

    # B 流程：同 key 全量安装走完
    z = _make_zip(key="race_demo", version="1.0.0")
    sig, pub = _sign_payload(z)
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)
    db = _FakeDB()
    row = await pis.install_zip(db, zip_bytes=z, signature=sig)

    assert row.key == "race_demo"
    assert final_dir.is_dir()
    # A 的进行中 staging 未被 B 删除
    assert a_marker.exists()
    # 过期残留被顺带清理（覆盖旧固定名与新随机命名两种模式）
    assert stale_legacy.exists()
    assert not stale_random.exists()


@pytest.mark.asyncio
async def test_cleanup_staging_residue_never_removes_legit_plugin(tmp_path) -> None:
    """名为 <key>.installing<任意后缀> 的合法插件目录不得被残留清理误删。

    插件 key 允许含 "."；宽松 glob `<key>.installing*` 会把合法已安装插件
    `demo.installinger` 当成 `demo` 的 staging 残留整目录删除。
    """
    installed_root = tmp_path / "installed"
    installed_root.mkdir(parents=True)

    # 合法插件：demo.installinger（其内部还有真实文件），修改时间已超过清理阈值
    legit = installed_root / "demo.installinger"
    legit.mkdir()
    (legit / "plugin.json").write_text("{}", encoding="utf-8")
    old_ts = time.time() - 7200
    os.utime(legit, (old_ts, old_ts))

    # demo 的过期 staging 残留：应被清理
    stale = pis._new_staging_dir(installed_root / "demo")
    stale.mkdir()
    (stale / "leftover").write_text("x", encoding="utf-8")
    os.utime(stale, (old_ts, old_ts))
    stale_hex = pis._new_staging_dir(installed_root / "demo")
    stale_hex.mkdir()
    os.utime(stale_hex, (old_ts, old_ts))

    pis._cleanup_staging_residue(installed_root / "demo")

    assert legit.is_dir(), "合法插件目录被误删"
    assert (legit / "plugin.json").exists()
    assert not stale.exists()
    assert not stale_hex.exists()

    # remote 通道的同名清理函数行为一致
    from app.services import remote_plugin_service as rps

    legit2 = installed_root / "demo2.installinger"
    legit2.mkdir()
    (legit2 / "plugin.json").write_text("{}", encoding="utf-8")
    os.utime(legit2, (old_ts, old_ts))
    stale2 = rps._new_staging_dir(installed_root / "demo2")
    stale2.mkdir()
    os.utime(stale2, (old_ts, old_ts))

    rps._cleanup_staging_residue(installed_root / "demo2")

    assert legit2.is_dir(), "remote 通道误删合法插件目录"
    assert not stale2.exists()


@pytest.mark.asyncio
async def test_install_zip_upgrade_keeps_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    db = _FakeDB()
    z1 = _make_zip(key="upgr", version="1.0.0")
    priv, pub = _ed25519_keypair()
    sig1 = priv.sign(z1)
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)

    # 1) 先装 1.0.0 + 开启
    row = await pis.install_zip(db, zip_bytes=z1, signature=sig1)
    row.enabled = True
    legacy_db = Path(row.installed_path) / "runtime.sqlite3"
    legacy_writer = sqlite3.connect(legacy_db)
    legacy_writer.execute("PRAGMA journal_mode = WAL")
    legacy_writer.execute("CREATE TABLE events(value TEXT NOT NULL)")
    legacy_writer.execute("INSERT INTO events(value) VALUES ('before-upgrade')")
    legacy_writer.commit()

    # 2) 装 1.1.0 → 版本升级、enabled 保留 True
    z2 = _make_zip(key="upgr", version="1.1.0")
    sig2 = priv.sign(z2)
    row2 = await pis.install_zip(db, zip_bytes=z2, signature=sig2)
    legacy_writer.execute("INSERT INTO events(value) VALUES ('old-connection-after-upgrade')")
    legacy_writer.commit()
    legacy_writer.close()
    assert row2.version == "1.1.0"
    assert row2.enabled is True
    assert db.installed_rows["upgr"].enabled is True
    assert db.installed_rows["upgr"].version == "1.1.0"
    persisted = tmp_path / "installed" / "_data" / "upgr" / "runtime.sqlite3"
    with sqlite3.connect(persisted) as conn:
        assert [row[0] for row in conn.execute("SELECT value FROM events ORDER BY rowid")] == [
            "before-upgrade",
            "old-connection-after-upgrade",
        ]


@pytest.mark.asyncio
async def test_install_zip_upgrade_without_platform_capability_declaration_is_rejected(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    db = _FakeDB()
    private_key, public_key = _ed25519_keypair()
    monkeypatch.setattr(pis.settings, "plugin_pubkey", public_key)
    first = _make_zip(key="upgrade_caps", version="1.0.0")
    row = await pis.install_zip(db, zip_bytes=first, signature=private_key.sign(first))
    row.enabled = True
    install_dir = Path(row.installed_path)

    second = _make_zip(
        key="upgrade_caps",
        version="2.0.0",
        include_platform_capabilities=False,
    )
    with pytest.raises(pis.ManifestError) as ex:
        await pis.install_zip(db, zip_bytes=second, signature=private_key.sign(second))

    assert ex.value.code == "PLATFORM_CAPABILITIES_REQUIRED"
    assert db.installed_rows["upgrade_caps"].version == "1.0.0"
    assert db.installed_rows["upgrade_caps"].enabled is True
    assert "1.0.0" in (install_dir / "manifest.py").read_text(encoding="utf-8")
    assert not (tmp_path / "installed" / "upgrade_caps.bak-zip").exists()
    assert list((tmp_path / "installed").glob("upgrade_caps.installing*")) == []


@pytest.mark.asyncio
async def test_install_zip_upgrade_link_failure_restores_previous_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    db = _FakeDB()
    private_key, public_key = _ed25519_keypair()
    monkeypatch.setattr(pis.settings, "plugin_pubkey", public_key)
    first = _make_zip(key="zip_retry", version="1.0.0")
    row = await pis.install_zip(db, zip_bytes=first, signature=private_key.sign(first))
    with sqlite3.connect(Path(row.installed_path) / "runtime.sqlite3") as conn:
        conn.execute("CREATE TABLE events(value TEXT NOT NULL)")
        conn.execute("INSERT INTO events(value) VALUES ('preserved')")
    monkeypatch.setattr(
        pis,
        "_attach_legacy_plugin_sqlite_links",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("link failed")),
    )
    second = _make_zip(key="zip_retry", version="2.0.0")

    with pytest.raises(OSError, match="link failed"):
        await pis.install_zip(db, zip_bytes=second, signature=private_key.sign(second))

    install_dir = tmp_path / "installed" / "zip_retry"
    assert "1.0.0" in (install_dir / "manifest.py").read_text(encoding="utf-8")
    assert not (tmp_path / "installed" / "zip_retry.bak-zip").exists()
    with sqlite3.connect(install_dir / "runtime.sqlite3") as conn:
        assert conn.execute("SELECT value FROM events").fetchone()[0] == "preserved"


@pytest.mark.asyncio
async def test_install_zip_writes_lint_warnings_to_installed_plugin(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    extra = [
        (
            "extra.py",
            b"import requests\nfrom app.services.auth_service import x\nrequests.get('https://example.com')\n",
        )
    ]
    z = _make_zip(key="linted_zip", version="1.0.0", extra_members=extra)
    sig, pub = _sign_payload(z)
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)
    db = _FakeDB()

    await pis.install_zip(db, zip_bytes=z, signature=sig)

    warnings = db.installed_rows["linted_zip"].lint_warnings
    assert any("app.services.auth_service" in item for item in warnings)
    assert any("requests.get" in item and "timeout" in item for item in warnings)


@pytest.mark.asyncio
async def test_install_zip_signature_failed_force_disabled(tmp_path, monkeypatch) -> None:
    """签名失败应在解析前直接拒绝。"""
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    _, pub = _ed25519_keypair()
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)

    db = _FakeDB()
    z = _make_zip(key="sig_demo", version="1.0.0")
    bad_sig = b"\x00" * 64
    with pytest.raises(pis.SignatureFailed) as ex:
        await pis.install_zip(db, zip_bytes=z, signature=bad_sig)
    assert ex.value.code == "SIGNATURE_FAILED"


@pytest.mark.asyncio
async def test_install_zip_rejects_unsigned_before_parse(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    _, pub = _ed25519_keypair()
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)
    db = _FakeDB()
    z = _make_zip(key="unsigned", version="1.0.0")

    called = False

    def _boom(_zip_bytes: bytes):  # noqa: ANN001
        nonlocal called
        called = True
        raise AssertionError("parse_zip 不应被调用")

    monkeypatch.setattr(pis, "parse_zip", _boom)
    with pytest.raises(pis.SignatureFailed):
        await pis.install_zip(db, zip_bytes=z, signature=None)
    assert called is False


@pytest.mark.asyncio
async def test_install_zip_unsigned_allowed_when_no_pubkey(tmp_path, monkeypatch) -> None:
    """未配置公钥 + 允许新未签名安装开关开启时可显式安装。"""
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    monkeypatch.setattr(pis.settings, "plugin_pubkey", "")
    monkeypatch.setattr(pis.settings, "plugin_allow_new_unsigned_plugins", True)
    db = _FakeDB()
    z = _make_zip(key="unsigned_ok", version="1.0.0")

    row = await pis.install_zip(db, zip_bytes=z, signature=None)

    assert row.key == "unsigned_ok"
    assert row.version == "1.0.0"
    assert row.signature_ok is None
    assert row.trust_tier == "community"
    assert row.enabled is False
    target = Path(row.installed_path)
    assert (target / "manifest.py").is_file()
    installed = db.installed_rows["unsigned_ok"]
    assert installed.signature_ok is None
    assert installed.trust_tier == "community"


@pytest.mark.asyncio
async def test_install_zip_unsigned_rejected_when_switch_off(tmp_path, monkeypatch) -> None:
    """未配置公钥但关闭新未签名安装开关时仍拒绝安装。"""
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    monkeypatch.setattr(pis.settings, "plugin_pubkey", "")
    monkeypatch.setattr(pis.settings, "plugin_allow_new_unsigned_plugins", False)
    db = _FakeDB()
    z = _make_zip(key="unsigned_blocked", version="1.0.0")

    with pytest.raises(pis.SignatureFailed) as ex:
        await pis.install_zip(db, zip_bytes=z, signature=None)
    assert ex.value.code == "SIGNATURE_FAILED"
    assert db.installed_rows == {}


@pytest.mark.asyncio
async def test_set_enabled_blocks_when_signature_failed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    z = _make_zip(key="locked", version="1.0.0")
    priv, pub = _ed25519_keypair()
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)
    db = _FakeDB()
    row = await pis.install_zip(db, zip_bytes=z, signature=priv.sign(z))
    row.signature_ok = False

    with pytest.raises(pis.SignatureFailed):
        await pis.set_enabled(db, "locked", True)


@pytest.mark.asyncio
async def test_set_enabled_updates_installed_plugin_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    z = _make_zip(key="toggle", version="1.0.0")
    sig, pub = _sign_payload(z)
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)
    db = _FakeDB()
    await pis.install_zip(db, zip_bytes=z, signature=sig)

    await pis.set_enabled(db, "toggle", True)

    assert db.installed_rows["toggle"].enabled is True
    assert [event.event_type for event in db.install_history] == ["installed", "enabled"]


@pytest.mark.asyncio
async def test_batch_install_state_supports_repo_and_zip_sources(monkeypatch) -> None:
    db = _FakeDB()
    db.installed_rows["repo_demo"] = InstalledPlugin(
        key="repo_demo",
        source="repo",
        installed_path="/tmp/repo_demo",
        version="1.0.0",
        enabled=True,
        signature_ok=None,
        trust_tier="community",
        lint_warnings=[],
    )
    db.installed_rows["zip_demo"] = InstalledPlugin(
        key="zip_demo",
        source="zip",
        installed_path="/tmp/zip_demo",
        version="1.0.0",
        enabled=True,
        signature_ok=True,
        trust_tier="verified",
        lint_warnings=[],
    )
    audit_events, previous_overrides = _install_api_overrides(db, monkeypatch)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/plugins/install/batch-state",
                headers=_csrf_headers(),
                json={"keys": ["repo_demo", "zip_demo"], "enabled": False},
            )
    finally:
        _restore_api_overrides(previous_overrides)

    assert response.status_code == 200
    assert response.json()["succeeded"] == 2
    assert response.json()["failed"] == 0
    assert db.installed_rows["repo_demo"].enabled is False
    assert db.installed_rows["zip_demo"].enabled is False
    assert audit_events == [("plugin.install_batch_disable", "plugins:batch")]


@pytest.mark.asyncio
async def test_uninstall_removes_row_and_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    db = _FakeDB()
    z = _make_zip(key="bye", version="1.0.0")
    sig, pub = _sign_payload(z)
    monkeypatch.setattr(pis.settings, "plugin_pubkey", pub)
    row = await pis.install_zip(db, zip_bytes=z, signature=sig)
    target = Path(row.installed_path)
    assert target.exists()
    db.features["bye"] = Feature(key="bye", display_name="Bye", is_builtin=False)
    db.features["other"] = Feature(key="other", display_name="Other", is_builtin=False)
    db.account_features.append(AccountFeature(account_id=1, feature_key="bye", enabled=True))
    db.account_features.append(AccountFeature(account_id=2, feature_key="other", enabled=True))
    db.global_configs["bye"] = PluginGlobalConfig(plugin_key="bye", config={"token": "secret"})
    db.global_configs["other"] = PluginGlobalConfig(plugin_key="other", config={"keep": True})

    deleted = await pis.uninstall(db, "bye")
    assert deleted is True
    assert "bye" not in db.installed_rows
    assert "bye" not in db.features
    assert [row.feature_key for row in db.account_features] == ["other"]
    assert "bye" not in db.global_configs
    assert "other" in db.features
    assert "other" in db.global_configs
    assert not target.exists()
    assert [event.event_type for event in db.install_history] == ["installed", "uninstalled"]
    assert db.install_history[-1].plugin_key == "bye"
    assert db.install_history[-1].detail is None


@pytest.mark.asyncio
async def test_uninstall_missing_returns_false() -> None:
    db = _FakeDB()
    deleted = await pis.uninstall(db, "ghost")
    assert deleted is False


@pytest.mark.asyncio
async def test_uninstall_refuses_dir_outside_installed_root(tmp_path, monkeypatch) -> None:
    """installed_path 指向 installed 根之外时拒绝 rmtree（DB 行仍清理）。"""
    monkeypatch.setattr(pis.settings, "plugins_installed_dir", str(tmp_path / "installed"))
    outside = tmp_path / "elsewhere" / "hostage"
    outside.mkdir(parents=True)
    (outside / "keep.txt").write_text("data", encoding="utf-8")
    db = _FakeDB()
    db.installed_rows["evil"] = InstalledPlugin(
        key="evil",
        source=PLUGIN_SOURCE_ZIP,
        installed_path=str(outside),
        version="1.0.0",
        enabled=True,
        trust_tier=pis.PLUGIN_TRUST_COMMUNITY,
    )

    deleted = await pis.uninstall(db, "evil")

    assert deleted is True
    assert "evil" not in db.installed_rows
    # 目录与内容原样保留，未被 rmtree。
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "data"


@pytest.mark.asyncio
async def test_installed_plugin_history_limit_is_bounded() -> None:
    """插件安装历史的 limit 用 Query 声明式钳制（1..200）：超限 → 422。"""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import app.deps as deps

    app = FastAPI()
    app.include_router(plugins_install_api.router)
    app.dependency_overrides[deps.get_db] = lambda: object()
    app.dependency_overrides[deps.get_current_user] = lambda: SimpleNamespace(id=1)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (
            await client.get("/api/plugins/install/demo/history", params={"limit": 201})
        ).status_code == 422
        assert (
            await client.get("/api/plugins/install/demo/history", params={"limit": 0})
        ).status_code == 422
