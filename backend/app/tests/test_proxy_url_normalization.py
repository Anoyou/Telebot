"""代理 URL 归一化。

用户经常从 Surge/Clash 里直接复制完整代理 URL；后端应在入库前拆开，
避免把 ``http://host:port`` 当成 DNS 主机名。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.proxies import ProxyUpdate, _parse_proxy_url, patch_proxy
from app.services import login_service


def test_parse_proxy_url_splits_http_url() -> None:
    parsed = _parse_proxy_url("http://10.10.8.33:6152")

    assert parsed == {
        "type": "http",
        "host": "10.10.8.33",
        "port": 6152,
    }


def test_parse_proxy_url_splits_auth_and_socks_url() -> None:
    parsed = _parse_proxy_url("socks5://user%40mail:pa%23ss@127.0.0.1:6153")

    assert parsed == {
        "type": "socks5",
        "host": "127.0.0.1",
        "port": 6153,
        "username": "user@mail",
        "password": "pa#ss",
    }


def test_parse_proxy_url_ignores_plain_host() -> None:
    assert _parse_proxy_url("10.10.8.33") is None


def test_parse_proxy_url_rejects_unknown_scheme() -> None:
    with pytest.raises(Exception) as exc_info:
        _parse_proxy_url("ftp://10.10.8.33:6152")

    assert getattr(exc_info.value, "detail")["code"] == "INVALID_PROXY_TYPE"


@pytest.mark.asyncio
async def test_patch_proxy_prefers_pasted_url_over_stale_form_fields() -> None:
    proxy = SimpleNamespace(
        id=1,
        type="socks5",
        host="old.local",
        port=1080,
        username=None,
        password_enc=None,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=proxy)

    with patch("app.api.proxies.audit.write", AsyncMock()):
        out = await patch_proxy(
            1,
            ProxyUpdate(
                type="socks5",
                host="http://10.10.8.33:6152",
                port=1080,
            ),
            db,
            SimpleNamespace(id=1),
        )

    assert proxy.type == "http"
    assert proxy.host == "10.10.8.33"
    assert proxy.port == 6152
    assert out.type == "http"


@pytest.mark.asyncio
async def test_login_proxy_tuple_accepts_legacy_full_url_host() -> None:
    proxy = SimpleNamespace(
        type="socks5",
        host="http://10.10.8.33:6152",
        port=1080,
        username=None,
        password_enc=None,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=proxy)

    assert await login_service._build_proxy_tuple(db, 1) == (
        "http",
        "10.10.8.33",
        6152,
        True,
        None,
        None,
    )
