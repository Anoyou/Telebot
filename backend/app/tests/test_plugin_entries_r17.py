"""第 17 轮 P4b 收尾：math10 / dice_grid_hunt / guess_number 用 ``legacy_entry_keys`` 声明旧入口 key。

背景：兜底入口表（``app/plugin_compat.py``）里这三个插件各有一个旧入口 key 插件自己没声明
（``start_math10`` / ``answer_dice_grid_hunt`` / ``start_game``），兜底表对它们只给出“按群会话”。
插件新版本在正式入口里写 ``legacy_entry_keys``，新宿主读到后不再查兜底表。

对比（golden）测试：``fixtures/plugin_entries_r17/golden.json.gz``（gzip 的 JSON）是**改动前**的宿主（0.112.0-beta.2，
Beta 95631c7a）装旧版插件时算出的全部推断与规则归一化结果。下面逐项证明：

- 旧插件 + 新宿主 == golden（没声明的旧插件照旧走兜底表）；
- 新插件 + 新宿主 == golden（声明结论与兜底表完全一致，且确实不再查兜底表）；
- 新插件 + 旧宿主：旧宿主不认识 ``legacy_entry_keys``，新插件也没写 ``entry_policy``，所以旧宿主
  照旧走兜底表——这里用“去掉 ``legacy_entry_keys`` 后的新插件”模拟，结果 == golden。

重新生成 golden（只应在改动前的宿主上做）::

    PYTHONPATH=backend python -m app.tests.test_plugin_entries_r17 --write-golden
"""

from __future__ import annotations

import copy
import gzip
import itertools
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import plugin_compat
from app.services import account_bot_service

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plugin_entries_r17"
PLUGINS: dict[str, dict[str, Any]] = json.loads((FIXTURES / "plugins.json").read_text(encoding="utf-8"))
GOLDEN_PATH = FIXTURES / "golden.json.gz"

# 三个插件声明的旧入口 key，应与兜底表里“插件未正式声明”的行一一对应。
LEGACY_KEYS = {
    "math10": "start_math10",
    "dice_grid_hunt": "answer_dice_grid_hunt",
    "guess_number": "start_game",
}
# 兜底表里出现的其它插件（不在本轮改动范围内，未安装 → 走兜底表）。
OTHER_MODULES = ("game24", "pt_promote", "blackjack", "not_installed_plugin")
RULE_FIELDS = (
    "id",
    "action",
    "module_key",
    "module_action",
    "module_session_scope",
    "participant_policy",
    "module_prize",
    "math_prize",
    "module_config",
    "module_start_keywords",
    "trigger_mode",
    "concurrency",
)


def _all_entry_keys() -> list[str | None]:
    keys: set[str] = set()
    for rows in (
        plugin_compat.FALLBACK_CHAT_SESSION_MODULE_ENTRIES,
        plugin_compat.FALLBACK_NO_PRIZE_MODULE_ENTRIES,
        plugin_compat.FALLBACK_SOLO_OWNER_MODULE_ENTRIES,
    ):
        keys.update(entry for _module, entry in rows)
    for meta in PLUGINS.values():
        for side in ("old", "new"):
            keys.update(str(item["key"]) for item in meta[side]["interaction_entries"])
    return [*sorted(keys), "unknown_entry", None]


# 在任何 monkeypatch 之前固定下来（清空兜底表的测试不能改变被比较的入口集合）。
ALL_ENTRY_KEYS: list[str | None] = _all_entry_keys()


def _install(root: Path, side: str | None, *, strip_legacy: bool = False) -> None:
    """把三个插件的 plugin.json 写进临时安装目录；side=None 表示都不安装。"""

    for key, meta in PLUGINS.items():
        target = root / key / "plugin.json"
        if side is None:
            if target.exists():
                target.unlink()
            continue
        data = copy.deepcopy(meta[side])
        if strip_legacy:
            for entry in data["interaction_entries"]:
                entry.pop("legacy_entry_keys", None)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"name": key, **data}, ensure_ascii=False), encoding="utf-8")


def _module_facts() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for module_key in [*sorted(PLUGINS), *OTHER_MODULES]:
        facts: dict[str, Any] = {"single_entry_key": account_bot_service._declared_module_single_entry_key(module_key)}
        for entry_key in ALL_ENTRY_KEYS:
            manifest = account_bot_service.declared_module_entry_manifest(module_key, entry_key)
            facts[str(entry_key)] = {
                "session_scope": account_bot_service._declared_module_entry_session_scope(module_key, entry_key),
                "participant_policy": account_bot_service._declared_module_entry_participant_policy(module_key, entry_key),
                "has_prize": account_bot_service.declared_module_entry_has_field(module_key, entry_key, "prize"),
                "has_timeout": account_bot_service.declared_module_entry_has_field(module_key, entry_key, "timeout"),
                "manifest_key": (manifest or {}).get("key"),
                "events": account_bot_service.declared_module_entry_events(module_key, entry_key),
                "timeout_seconds": account_bot_service.declared_module_entry_timeout_seconds(module_key, entry_key),
                "default_start_keywords": account_bot_service.declared_module_default_start_keywords(module_key, entry_key),
            }
        out[module_key] = facts
    return out


def _rule_matrix() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    modules = [*sorted(PLUGINS), "game24", "blackjack", "pt_promote"]
    for index, (module_key, entry_key, concurrency, scope, prize, trigger_mode) in enumerate(
        itertools.product(
            modules,
            ALL_ENTRY_KEYS,
            ("chat", "user"),
            (None, "user", "chat"),
            ((None, None), (50, None), (50, 77)),
            ("both",),
        )
    ):
        math_prize, module_prize = prize
        rule: dict[str, Any] = {
            "id": f"r{index}",
            "action": "module",
            "module_key": module_key,
            "concurrency": concurrency,
            "trigger_mode": trigger_mode,
            "module_start_keywords": ["开一局"],
        }
        if entry_key is not None:
            rule["module_action"] = entry_key
        if scope is not None:
            rule["module_session_scope"] = scope
        if math_prize is not None:
            rule["math_prize"] = math_prize
        if module_prize is not None:
            rule["module_prize"] = module_prize
        rules.append(rule)
    # 旧动作 action="math10"（不转换，照旧走运行时兼容）
    for concurrency in ("chat", "user"):
        rules.append({"id": f"legacy-{concurrency}", "action": "math10", "concurrency": concurrency, "math_prize": 88})
    return rules


def _normalized_rules() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rules = _rule_matrix()
    for start in range(0, len(rules), 20):  # normalize_interaction_rules 一次最多 20 条
        out.extend(account_bot_service.normalize_interaction_rules(rules[start : start + 20]))
    # 只保留与入口推断相关的字段（其余字段与入口声明无关，避免 golden 过大）
    return [{key: rule.get(key) for key in RULE_FIELDS} for rule in out]


def _normalized_configs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for module_key, entry_key in itertools.product([*sorted(PLUGINS), "game24"], [None, *sorted(LEGACY_KEYS.values())]):
        raw: dict[str, Any] = {"enabled": True, "action": "module", "module_key": module_key, "concurrency": "user"}
        if entry_key:
            raw["module_action"] = entry_key
        config = account_bot_service.normalize_transfer_notice_config(raw)
        out.append({key: config.get(key) for key in sorted(config) if key.startswith("module_") or key in {"action", "interaction_rules"}})
    return out


def _snapshot() -> dict[str, Any]:
    return json.loads(
        json.dumps(
            {"facts": _module_facts(), "rules": _normalized_rules(), "configs": _normalized_configs()},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )


def _snapshot_for(root: Path, side: str | None, *, strip_legacy: bool = False) -> dict[str, Any]:
    _install(root, side, strip_legacy=strip_legacy)
    return _snapshot()


@pytest.fixture
def installed(monkeypatch, tmp_path):
    monkeypatch.setattr(account_bot_service, "settings", SimpleNamespace(plugins_installed_path=tmp_path))
    monkeypatch.setattr(account_bot_service.BUILTIN_FEATURES, "manifest_for", lambda _key: None)
    return tmp_path


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return json.loads(gzip.decompress(GOLDEN_PATH.read_bytes()).decode("utf-8"))


@pytest.mark.parametrize("side", ["old", "new", "new_on_old_host", "none"])
def test_snapshot_equals_pre_change_golden(installed, golden, side) -> None:
    """四种组合的全部推断与规则归一化结果，与改动前的宿主 + 旧插件逐字段相同。"""

    if side == "new_on_old_host":
        actual = _snapshot_for(installed, "new", strip_legacy=True)
        expected = golden["old"]
    elif side == "none":
        actual = _snapshot_for(installed, None)
        expected = golden["none"]
    else:
        actual = _snapshot_for(installed, side)
        expected = golden["old"]
    assert actual["facts"] == expected["facts"]
    assert actual["configs"] == expected["configs"]
    assert len(actual["rules"]) == len(expected["rules"])
    for got, want in zip(actual["rules"], expected["rules"], strict=True):
        assert got == want, got.get("id")


def test_new_plugins_no_longer_consult_fallback_table(installed, monkeypatch) -> None:
    """新插件声明后，即使把兜底表清空，结论也不变（证明结论来自插件声明而不是写死表）。"""

    _install(installed, "new")
    with_table = _snapshot()
    for name in (
        "FALLBACK_CHAT_SESSION_MODULE_ENTRIES",
        "FALLBACK_NO_PRIZE_MODULE_ENTRIES",
        "FALLBACK_SOLO_OWNER_MODULE_ENTRIES",
    ):
        rows = getattr(plugin_compat, name)
        monkeypatch.setattr(plugin_compat, name, frozenset(row for row in rows if row[0] not in LEGACY_KEYS))
    without_table = _snapshot()
    for module_key in LEGACY_KEYS:
        assert without_table["facts"][module_key] == with_table["facts"][module_key]
    for got, want in zip(without_table["rules"], with_table["rules"], strict=True):
        if got.get("module_key") in LEGACY_KEYS:
            assert got == want, got.get("id")


def test_old_plugins_still_need_fallback_table(installed, monkeypatch) -> None:
    """旧插件没声明：兜底表仍在起作用（清空它会改变旧入口 key 的会话推断）。"""

    _install(installed, "old")
    monkeypatch.setattr(
        plugin_compat,
        "FALLBACK_CHAT_SESSION_MODULE_ENTRIES",
        frozenset(row for row in plugin_compat.FALLBACK_CHAT_SESSION_MODULE_ENTRIES if row[0] not in LEGACY_KEYS),
    )
    for module_key, legacy_key in LEGACY_KEYS.items():
        assert account_bot_service._declared_module_entry_session_scope(module_key, legacy_key) is None


def test_declarations_mirror_fallback_rows() -> None:
    """插件声明的每个旧 key 与兜底表的行一一对应（会话 / 参与策略 / 奖金推断都相同）。"""

    for module_key, legacy_key in LEGACY_KEYS.items():
        entries = PLUGINS[module_key]["new"]["interaction_entries"]
        assert len(entries) == 1  # 单入口推断不受影响
        declared = entries[0]["legacy_entry_keys"]
        assert declared == [{"key": legacy_key, "session_scope": "chat"}]
        assert plugin_compat.fallback_entry_session_scope(module_key, legacy_key) == "chat"
        assert plugin_compat.fallback_entry_participant_policy(module_key, legacy_key) is None
        assert plugin_compat.fallback_entry_has_field(module_key, legacy_key, "prize") is None
        # 新插件不写 entry_policy：0.112.0-beta.2 见到 entry_policy 会整表跳过兜底
        assert all("entry_policy" not in entry for entry in entries)
        # 其余字段与旧版完全一致
        old_entries = PLUGINS[module_key]["old"]["interaction_entries"]
        stripped = [{k: v for k, v in entry.items() if k != "legacy_entry_keys"} for entry in entries]
        assert stripped == old_entries


def test_normalize_legacy_entry_keys() -> None:
    normalize = account_bot_service.normalize_legacy_entry_keys
    assert normalize(None) is None
    assert normalize("start_math10") is None
    assert normalize([]) == []
    assert normalize(
        [
            {"key": "a", "session_scope": "chat", "participant_policy": "open_race", "prize": False, "x": 1},
            {"key": " a "},
            {"key": "b", "session_scope": "bogus", "participant_policy": "bogus", "prize": "yes"},
            {"title": "no key"},
            "c",
        ]
    ) == [
        {"key": "a", "session_scope": "chat", "participant_policy": "open_race", "prize": False},
        {"key": "b"},
    ]


def test_legacy_key_never_shadows_formal_entry(installed) -> None:
    """旧 key 与正式入口同名时以正式入口为准；空列表也算“已声明”（不再查兜底表）。"""

    plugin_dir = installed / "dice_grid_hunt"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "dice_grid_hunt",
                "interaction_entries": [
                    {
                        "key": "start_dice_grid_hunt",
                        "session_scope": "user",
                        "legacy_entry_keys": [
                            {"key": "start_dice_grid_hunt", "session_scope": "chat", "participant_policy": "solo_owner"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert account_bot_service._declared_module_entry_session_scope("dice_grid_hunt", "start_dice_grid_hunt") == "user"
    assert account_bot_service._declared_module_entry_participant_policy("dice_grid_hunt", "start_dice_grid_hunt") is None
    # 未列出的旧 key：插件已声明 → 不再查兜底表
    assert account_bot_service.module_declares_entry_policy("dice_grid_hunt") is True
    assert account_bot_service._declared_module_entry_session_scope("dice_grid_hunt", "answer_dice_grid_hunt") is None


def test_legacy_key_full_declaration(installed) -> None:
    plugin_dir = installed / "pt_promote"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "pt_promote",
                "interaction_entries": [
                    {
                        "key": "promote_v2",
                        "input_schema": {"properties": {"prize": {"type": "integer"}}},
                        "legacy_entry_keys": [
                            {"key": "promote_torrent", "session_scope": "user", "participant_policy": "solo_owner", "prize": False}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert account_bot_service._declared_module_entry_session_scope("pt_promote", "promote_torrent") == "user"
    assert account_bot_service._declared_module_entry_participant_policy("pt_promote", "promote_torrent") == "solo_owner"
    assert account_bot_service.declared_module_entry_has_field("pt_promote", "promote_torrent", "prize") is False
    # 旧 key 不是正式入口：不进入单入口推断、不返回入口声明（时限 / 事件 / 编辑器都按未知入口）
    assert account_bot_service._declared_module_single_entry_key("pt_promote") == "promote_v2"
    assert account_bot_service.declared_module_entry_manifest("pt_promote", "promote_torrent") is None
    assert account_bot_service.declared_module_entry_events("pt_promote", "promote_torrent") == []


def test_capability_registered() -> None:
    from app.worker.plugins import capabilities as caps_mod
    from app.worker.plugins.versioning import PLUGIN_API_VERSION

    item = caps_mod.CAPABILITY_INDEX["interaction.legacy_entry_keys"]
    assert item.plugin_api == "1.6"
    # Plugin API 只增：1.7（契约版本探测）起宿主版本继续递增，引入该能力的版本号保持 1.6
    assert tuple(int(p) for p in PLUGIN_API_VERSION.split(".")) >= (1, 6)
    assert item.since == caps_mod._R17_RELEASE


def _write_golden() -> None:
    import tempfile
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with (
            patch.object(account_bot_service, "settings", SimpleNamespace(plugins_installed_path=root)),
            patch.object(account_bot_service.BUILTIN_FEATURES, "manifest_for", lambda _key: None),
        ):
            golden = {"old": _snapshot_for(root, "old"), "none": _snapshot_for(root, None)}
    raw = json.dumps(golden, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    GOLDEN_PATH.write_bytes(gzip.compress(raw, mtime=0))
    print(f"wrote {GOLDEN_PATH} ({GOLDEN_PATH.stat().st_size} bytes, {len(golden['old']['rules'])} rules)")


if __name__ == "__main__":  # pragma: no cover
    if "--write-golden" in sys.argv:
        _write_golden()
