from __future__ import annotations

from pathlib import Path

from app import feature_registry
from app.feature_registry import BUILTIN_FEATURES, LazyBuiltinFeatures, scan_builtin_manifest_objects


def _write_manifest(dir_path: Path, content: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "manifest.py").write_text(content, encoding="utf-8")


def test_scan_builtin_manifest_objects_returns_empty_when_dir_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(feature_registry, "_BUILTIN_PLUGIN_DIR", tmp_path / "missing")
    result = scan_builtin_manifest_objects()
    assert result == {}


def test_lazy_registry_loads_display_name_and_manifest(monkeypatch, tmp_path) -> None:
    plugin_dir = tmp_path / "builtin"
    _write_manifest(
        plugin_dir / "demo",
        "class _M:\n"
        "    pass\n"
        "MANIFEST = _M()\n"
        "MANIFEST.key = 'demo_key'\n"
        "MANIFEST.display_name = 'Demo Display'\n",
    )
    monkeypatch.setattr(feature_registry, "_BUILTIN_PLUGIN_DIR", plugin_dir)

    registry = LazyBuiltinFeatures()
    assert "demo_key" in registry
    assert registry["demo_key"] == "Demo Display"
    manifest = registry.manifest_for("demo_key")
    assert manifest is not None
    assert manifest.key == "demo_key"


def test_lazy_registry_refresh_picks_latest_manifest(monkeypatch, tmp_path) -> None:
    plugin_dir = tmp_path / "builtin"
    _write_manifest(
        plugin_dir / "alpha",
        "class _M:\n"
        "    pass\n"
        "MANIFEST = _M()\n"
        "MANIFEST.key = 'alpha'\n"
        "MANIFEST.display_name = 'Alpha V1'\n",
    )
    monkeypatch.setattr(feature_registry, "_BUILTIN_PLUGIN_DIR", plugin_dir)

    registry = LazyBuiltinFeatures()
    assert registry["alpha"] == "Alpha V1"

    _write_manifest(
        plugin_dir / "alpha",
        "class _M:\n"
        "    pass\n"
        "MANIFEST = _M()\n"
        "MANIFEST.key = 'alpha'\n"
        "MANIFEST.display_name = 'Alpha V2'\n",
    )
    registry.refresh()
    assert registry["alpha"] == "Alpha V2"


def test_builtin_registry_excludes_codex_image_after_installed_migration() -> None:
    BUILTIN_FEATURES.refresh()
    assert "codex_image" not in set(BUILTIN_FEATURES.keys())


def test_builtin_registry_excludes_legacy_feature_keys() -> None:
    BUILTIN_FEATURES.refresh()
    keys = set(BUILTIN_FEATURES.keys())
    assert "group_admin" not in keys
    assert "monitor" not in keys
