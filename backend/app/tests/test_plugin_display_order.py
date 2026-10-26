"""插件中心展示顺序：保存、读取与 feature-matrix 排序（Web 与 iOS 共用）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import features as features_api
from app.db.models.system import SystemSetting
from app.schemas.feature import PluginDisplayOrder
from app.services import feature_service


class FakeDB:
    def __init__(self) -> None:
        self.rows: dict[str, SystemSetting] = {}
        self.committed = False

    async def get(self, model, key):
        assert model is SystemSetting
        return self.rows.get(key)

    def add(self, row) -> None:
        self.rows[row.key] = row

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


def _feature(key: str) -> SimpleNamespace:
    return SimpleNamespace(key=key)


def test_apply_display_order_keeps_unlisted_in_original_order() -> None:
    features = [_feature("a"), _feature("b"), _feature("c"), _feature("d")]
    ordered = feature_service.apply_display_order(features, ["c", "ghost", "a"])
    assert [f.key for f in ordered] == ["c", "a", "b", "d"]


def test_apply_display_order_without_order_is_identity() -> None:
    features = [_feature("b"), _feature("a")]
    assert feature_service.apply_display_order(features, []) == features


@pytest.mark.asyncio
async def test_set_and_get_display_order_dedupes_and_strips() -> None:
    db = FakeDB()
    saved = await feature_service.set_plugin_display_order(db, [" b ", "a", "", "b", "c"])
    assert saved == ["b", "a", "c"]
    assert await feature_service.get_plugin_display_order(db) == ["b", "a", "c"]
    # 覆盖写入（幂等）
    saved = await feature_service.set_plugin_display_order(db, ["c", "a"])
    assert saved == ["c", "a"]
    assert await feature_service.get_plugin_display_order(db) == ["c", "a"]


@pytest.mark.asyncio
async def test_get_display_order_ignores_malformed_value() -> None:
    db = FakeDB()
    db.add(SystemSetting(key=feature_service.PLUGIN_DISPLAY_ORDER_KEY, value={"keys": "oops"}))
    assert await feature_service.get_plugin_display_order(db) == []
    db.rows[feature_service.PLUGIN_DISPLAY_ORDER_KEY].value = {"keys": ["a", 3, None, "b"]}
    assert await feature_service.get_plugin_display_order(db) == ["a", "b"]


@pytest.mark.asyncio
async def test_put_display_order_endpoint_saves_and_audits(monkeypatch) -> None:
    db = FakeDB()
    audit_write = AsyncMock()
    monkeypatch.setattr(features_api.audit, "write", audit_write)
    user = SimpleNamespace(id=7)

    result = await features_api.put_plugin_display_order(PluginDisplayOrder(keys=["x", "y", "x"]), db, user)

    assert result.keys == ["x", "y"]
    assert db.committed is True
    audit_write.assert_awaited_once()
    assert audit_write.await_args.args[2] == "feature.display_order"
    got = await features_api.get_plugin_display_order(db, user)
    assert got.keys == ["x", "y"]


@pytest.mark.asyncio
async def test_put_display_order_rejects_overlong_key(monkeypatch) -> None:
    from fastapi import HTTPException

    db = FakeDB()
    monkeypatch.setattr(features_api.audit, "write", AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await features_api.put_plugin_display_order(PluginDisplayOrder(keys=["k" * 200]), db, SimpleNamespace(id=1))
    assert exc.value.status_code == 400
    assert db.committed is False


@pytest.mark.asyncio
async def test_feature_matrix_uses_saved_display_order(monkeypatch) -> None:
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    features = [
        SimpleNamespace(key=key, display_name=key, is_builtin=False, version="1.0.0", manifest={})
        for key in ("alpha", "beta", "gamma")
    ]
    monkeypatch.setattr(feature_service, "list_features", AsyncMock(return_value=features))
    monkeypatch.setattr(feature_service, "get_plugin_display_order", AsyncMock(return_value=["gamma", "alpha"]))
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[Result([]), Result([]), Result([])])

    data = await feature_service.feature_matrix(db)

    assert [item["key"] for item in data["features"]] == ["gamma", "alpha", "beta"]
