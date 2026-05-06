"""worker.runtime 私有 helper 的单元测试（不连真 DB）。"""
from __future__ import annotations

from app.worker.runtime import _build_proxy_url


def test_build_proxy_url_socks5_with_auth() -> None:
    out = _build_proxy_url("socks5", "127.0.0.1", 1080, "alice", "p@ss")
    # urllib.parse.quote 会把 @ 转 %40
    assert out == "socks5://alice:p%40ss@127.0.0.1:1080"


def test_build_proxy_url_socks5_no_auth() -> None:
    assert _build_proxy_url("socks5", "10.0.0.1", 1080, None, "") == "socks5://10.0.0.1:1080"


def test_build_proxy_url_http_uppercase_type() -> None:
    """type 大小写应不敏感（前端可能传 SOCKS5）。"""
    assert _build_proxy_url("HTTP", "p.example.com", 8080, None, "") == "http://p.example.com:8080"


def test_build_proxy_url_https_falls_to_http_scheme() -> None:
    """https 类型也走 HTTP CONNECT 形式（httpx 用 http:// 前缀拨 CONNECT 隧道）。"""
    assert _build_proxy_url("https", "p.example.com", 443, None, "") == "http://p.example.com:443"


def test_build_proxy_url_username_only() -> None:
    """有 user 无 pass 时不能拼出 ``user:@host``——urllib 行为是 ``user@host``，httpx 接受。"""
    out = _build_proxy_url("socks5", "10.0.0.1", 1080, "alice", "")
    assert out == "socks5://alice@10.0.0.1:1080"


def test_build_proxy_url_mtproxy_not_supported() -> None:
    """mtproxy 类型 httpx 不支持 → 返 None。"""
    assert _build_proxy_url("mtproxy", "x", 443, None, "") is None


def test_build_proxy_url_unknown_type_returns_none() -> None:
    assert _build_proxy_url("ftp", "x", 21, None, "") is None
