"""代理 URL 解析工具。

把 ``.env`` 里的 ``TG_DEFAULT_PROXY`` 字符串（或任何 URL 风格的代理串）
转成 Telethon 接受的 PySocks 元组：

    (proxy_type, host, port, rdns, username, password)

支持的格式：
    socks5://user:pass@host:port
    socks5://host:port
    socks4://host:port
    http://host:port
    mtproxy://host:port?secret=xxxx     (Telethon 用 ``connection`` 参数走 MTProxy；
                                         本工具仅支持 PySocks 风格的 socks5/4/http；
                                         mtproxy 留作 TODO，目前会返 None 并记录 warning)

调用：
    >>> parse_proxy_url("socks5://user:pass@127.0.0.1:1080")
    ("socks5", "127.0.0.1", 1080, True, "user", "pass")
    >>> parse_proxy_url("")     # 空字符串视为不配置
    None
"""
from __future__ import annotations

import logging
from urllib.parse import unquote, urlparse

log = logging.getLogger(__name__)

# Telethon / PySocks 接受的 proxy_type 枚举
_VALID_TYPES: dict[str, str] = {
    "socks5": "socks5",
    "socks4": "socks4",
    "http": "http",
    "https": "http",        # https 走 HTTP CONNECT
}


ProxyTuple = tuple[str, str, int, bool, str | None, str | None]


def parse_proxy_url(url: str | None) -> ProxyTuple | None:
    """解析代理 URL；空 / 无效 → 返 None（即直连）。"""
    if not url:
        return None
    url = url.strip()
    if not url:
        return None

    # 兼容用户写 "127.0.0.1:1080" 不带 scheme：默认按 socks5 处理
    if "://" not in url:
        url = "socks5://" + url

    try:
        parsed = urlparse(url)
    except Exception:
        log.warning("代理 URL 无法解析：%r", url)
        return None

    scheme = (parsed.scheme or "").lower()

    if scheme == "mtproxy":
        # Telethon 的 MTProxy 不走 PySocks，需要 connection_class=ConnectionTcpMTProxyRandomizedIntermediate；
        # 这里暂不支持；如真要用，在账号详情里通过 Proxy 表（type='mtproxy'）单独配置——
        # 那条路径走的是 plan 里 build_proxy_tuple 的 (proxy.type, host, port, ...) 写法，
        # Telethon 内部会把 type='mtproxy' 当作 secret 登录。
        log.warning("MTProxy 全局代理暂不支持；请在账号绑定时单独选 mtproxy 代理")
        return None

    proxy_type = _VALID_TYPES.get(scheme)
    if not proxy_type:
        log.warning("未知代理 scheme：%r（仅支持 socks5/socks4/http/https/mtproxy）", scheme)
        return None

    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        log.warning("代理 URL 缺 host 或 port：%r", url)
        return None

    user = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None

    # rdns=True：让代理服务器做 DNS 解析，避免本地 DNS 泄漏（连 TG 时很关键）
    return (proxy_type, host, int(port), True, user, password)


def get_default_proxy_tuple() -> ProxyTuple | None:
    """读 settings.tg_default_proxy 并解析；用作所有未指定 proxy_id 的账号的兜底代理。"""
    # 延迟 import 避免循环
    from ..settings import settings
    return parse_proxy_url(settings.tg_default_proxy)
