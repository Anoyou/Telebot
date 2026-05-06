"""代理（出口 IP）相关 API。

提供：
  - ``GET    /api/proxies``         列表
  - ``POST   /api/proxies``         新建（密码经主密钥加密落盘）
  - ``GET    /api/proxies/{pid}``   详情（不返密码）
  - ``PATCH  /api/proxies/{pid}``   修改
  - ``DELETE /api/proxies/{pid}``   删除（被账号引用时返 409）
  - ``POST   /api/proxies/{pid}/test``  连通性测试 + 出口 IP 与归属地

代理被绑定向导、账号详情用作下拉源；worker 启动时按账号配置串入 Telethon。
"""

from __future__ import annotations

import asyncio
import socket
import time as _time

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict
from python_socks import ProxyConnectionError, ProxyError, ProxyType
from python_socks.async_.asyncio import Proxy as AsyncProxy
from sqlalchemy import select

from ..crypto import decrypt_str, encrypt_str
from ..db.models.account import Account, Proxy
from ..db.models.command import LLMProvider
from ..deps import CurrentUser, DBSession
from ..services import audit, proxy_probe_cache

router = APIRouter(prefix="/api/proxies", tags=["proxies"])


# ── Schemas ──────────────────────────────────────────────────────
class ProxyOut(BaseModel):
    """代理出参，绝不返回明文密码。"""

    id: int
    type: str
    host: str
    port: int
    username: str | None = None
    has_password: bool = False

    model_config = ConfigDict(from_attributes=True)


class ProxyCreate(BaseModel):
    type: str          # socks5 | http | mtproxy
    host: str
    port: int
    username: str | None = None
    password: str | None = None


class ProxyUpdate(BaseModel):
    type: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None     # 显式传空字符串表示清空；None 表示保持
    clear_password: bool = False


class ProxyTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    exit_ip: str | None = None
    country: str | None = None
    region: str | None = None
    city: str | None = None
    error: str | None = None


# ── 工具 ─────────────────────────────────────────────────────────
def _err(code: str, message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _to_out(p: Proxy) -> ProxyOut:
    return ProxyOut(
        id=p.id, type=p.type, host=p.host, port=p.port,
        username=p.username, has_password=bool(p.password_enc),
    )


_VALID_TYPES = {"socks5", "http", "https", "mtproxy"}


def _validate_type(t: str) -> None:
    if t not in _VALID_TYPES:
        raise _err("INVALID_PROXY_TYPE", f"代理类型必须是 {', '.join(sorted(_VALID_TYPES))}")


# ── CRUD ─────────────────────────────────────────────────────────
@router.get("", response_model=list[ProxyOut])
async def list_proxies(db: DBSession, _user: CurrentUser) -> list[ProxyOut]:
    rows = (await db.execute(select(Proxy).order_by(Proxy.id))).scalars().all()
    return [_to_out(p) for p in rows]


@router.post("", response_model=ProxyOut, status_code=status.HTTP_201_CREATED)
async def create_proxy(payload: ProxyCreate, db: DBSession, user: CurrentUser) -> ProxyOut:
    _validate_type(payload.type)
    if payload.port <= 0 or payload.port > 65535:
        raise _err("INVALID_PORT", "端口范围必须是 1-65535")
    p = Proxy(
        type=payload.type, host=payload.host, port=payload.port,
        username=payload.username,
        password_enc=encrypt_str(payload.password) if payload.password else None,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    await audit.write(db, user.id, "create_proxy", target=str(p.id),
                       detail={"type": p.type, "host": p.host, "port": p.port})
    await db.commit()
    return _to_out(p)


@router.get("/{pid}", response_model=ProxyOut)
async def get_proxy(pid: int, db: DBSession, _user: CurrentUser) -> ProxyOut:
    p = await db.get(Proxy, pid)
    if not p:
        raise _err("NOT_FOUND", "代理不存在", 404)
    return _to_out(p)


@router.patch("/{pid}", response_model=ProxyOut)
async def patch_proxy(pid: int, payload: ProxyUpdate, db: DBSession, user: CurrentUser) -> ProxyOut:
    p = await db.get(Proxy, pid)
    if not p:
        raise _err("NOT_FOUND", "代理不存在", 404)
    if payload.type is not None:
        _validate_type(payload.type)
        p.type = payload.type
    if payload.host is not None:
        p.host = payload.host
    if payload.port is not None:
        if payload.port <= 0 or payload.port > 65535:
            raise _err("INVALID_PORT", "端口范围必须是 1-65535")
        p.port = payload.port
    if payload.username is not None:
        p.username = payload.username or None
    if payload.clear_password:
        p.password_enc = None
    elif payload.password is not None and payload.password != "":
        p.password_enc = encrypt_str(payload.password)
    await db.commit()
    await audit.write(db, user.id, "update_proxy", target=str(pid))
    await db.commit()
    return _to_out(p)


@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proxy(pid: int, db: DBSession, user: CurrentUser) -> None:
    p = await db.get(Proxy, pid)
    if not p:
        raise _err("NOT_FOUND", "代理不存在", 404)
    # 被账号引用就拒删；也告诉用户被哪些 LLM 引用，方便他先去解绑
    used_acc = (await db.execute(
        select(Account.id).where(Account.proxy_id == pid).limit(3)
    )).scalars().all()
    used_llm = (await db.execute(
        select(LLMProvider.id).where(LLMProvider.proxy_id == pid).limit(3)
    )).scalars().all()
    if used_acc or used_llm:
        parts = []
        if used_acc:
            parts.append(f"账号 #{','.join(str(i) for i in used_acc)}")
        if used_llm:
            parts.append(f"LLM provider #{','.join(str(i) for i in used_llm)}")
        raise _err(
            "PROXY_IN_USE",
            f"代理被 {' / '.join(parts)} 使用中，无法删除（先去那里改换代理）",
            409,
        )
    await db.delete(p)
    await db.commit()
    await audit.write(db, user.id, "delete_proxy", target=str(pid))
    await db.commit()
    # 清掉探测缓存，避免下次同 id 的代理（理论不会复用 id 但保险起见）拿到旧数据
    await proxy_probe_cache.clear_probe(pid)


# ── 连通性测试 ────────────────────────────────────────────────────
# Telegram MTProto 测试目标：DC2（公开常稳）
_TG_HOST = "149.154.167.50"
_TG_PORT = 443


async def _resolve_country(client_factory) -> dict[str, str | None]:
    """通过给定 client 拿到出口 IP + 国家/地区。优先 ip-api.com，失败回退 ipinfo.io。"""
    try:
        async with client_factory() as cli:
            r = await cli.get("http://ip-api.com/json/", timeout=8.0)
            r.raise_for_status()
            d = r.json()
            if d.get("status") == "success":
                return {
                    "exit_ip": d.get("query"),
                    "country": d.get("countryCode"),
                    "region": d.get("regionName") or d.get("region"),
                    "city": d.get("city"),
                }
    except Exception:
        pass
    try:
        async with client_factory() as cli:
            r = await cli.get("https://ipinfo.io/json", timeout=8.0)
            r.raise_for_status()
            d = r.json()
            return {
                "exit_ip": d.get("ip"),
                "country": d.get("country"),
                "region": d.get("region"),
                "city": d.get("city"),
            }
    except Exception:
        return {"exit_ip": None, "country": None, "region": None, "city": None}


@router.post("/{pid}/test", response_model=ProxyTestResult)
async def test_proxy(pid: int, db: DBSession, _user: CurrentUser) -> ProxyTestResult:
    """通过该代理连接 Telegram MTProto + 查询出口 IP 归属地。"""
    p = await db.get(Proxy, pid)
    if not p:
        raise _err("NOT_FOUND", "代理不存在", 404)

    pwd = decrypt_str(p.password_enc) if p.password_enc else None

    # 第一步：try TCP connect 到 Telegram DC2:443，记录延迟
    t0 = _time.monotonic()
    try:
        if p.type in ("socks5", "http", "https"):
            ptype = {"socks5": ProxyType.SOCKS5, "http": ProxyType.HTTP,
                     "https": ProxyType.HTTP}[p.type]
            proxy_obj = AsyncProxy(
                proxy_type=ptype, host=p.host, port=p.port,
                username=p.username or None, password=pwd or None,
            )
            sock = await asyncio.wait_for(
                proxy_obj.connect(dest_host=_TG_HOST, dest_port=_TG_PORT),
                timeout=8.0,
            )
            sock.close()
        elif p.type == "mtproxy":
            # MTProxy 不走 python-socks；这里只做 TCP 探活到代理端口
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            await asyncio.get_event_loop().run_in_executor(
                None, sock.connect, (p.host, p.port)
            )
            sock.close()
        else:
            return ProxyTestResult(ok=False, error=f"不支持的代理类型: {p.type}")
    except ProxyConnectionError as e:
        return ProxyTestResult(ok=False, error=f"代理连接失败: {e}")
    except ProxyError as e:
        return ProxyTestResult(ok=False, error=f"代理协议错误: {e}")
    except TimeoutError:
        return ProxyTestResult(ok=False, error="超时（8s 内未建立连接）")
    except Exception as e:
        return ProxyTestResult(ok=False, error=f"{type(e).__name__}: {e}")

    latency_ms = int((_time.monotonic() - t0) * 1000)

    # 第二步：通过该代理访问 ipinfo.io 拿出口 IP
    def _make_client():
        if p.type in ("http", "https"):
            url = f"http://{p.username + ':' + pwd + '@' if p.username else ''}{p.host}:{p.port}"
            return httpx.AsyncClient(proxy=url)
        if p.type == "socks5":
            scheme = "socks5"
            auth = f"{p.username}:{pwd}@" if p.username else ""
            url = f"{scheme}://{auth}{p.host}:{p.port}"
            return httpx.AsyncClient(proxy=url)
        # MTProxy 不能拿 IP
        return httpx.AsyncClient()

    geo = await _resolve_country(_make_client)
    result = ProxyTestResult(
        ok=True, latency_ms=latency_ms,
        exit_ip=geo["exit_ip"], country=geo["country"],
        region=geo["region"], city=geo["city"],
    )
    # 写入缓存让账号卡 / 概览页能不重新探测就显示最近一次结果（30 min TTL）
    await proxy_probe_cache.set_probe(
        pid,
        ok=True,
        exit_ip=geo["exit_ip"],
        country=geo["country"],
        region=geo["region"],
        city=geo["city"],
        latency_ms=latency_ms,
    )
    return result


# ── 反查：这条代理被谁用了 ───────────────────────────────────────
class ProxyUsageItem(BaseModel):
    """``/api/proxies/{id}/usage`` 出参的单条；同形结构兼容 account + llm_provider。"""

    kind: str           # "account" | "llm_provider"
    id: int
    name: str | None    # 账号 display_name / provider name；可能为空
    extra: str | None = None  # 账号给 phone；llm 给 default_model

    model_config = ConfigDict(from_attributes=True)


class ProxyUsageResponse(BaseModel):
    """``GET /api/proxies/{id}/usage`` 出参——告诉前端"删了这条代理会断哪些"。"""

    accounts: list[ProxyUsageItem] = []
    llm_providers: list[ProxyUsageItem] = []
    total: int = 0


@router.get("/{pid}/usage", response_model=ProxyUsageResponse)
async def proxy_usage(
    pid: int, db: DBSession, _user: CurrentUser
) -> ProxyUsageResponse:
    """列出引用本代理的所有 account / llm_provider。

    用途：
    - 代理库管理页展开行显示"被 N 个账号 + M 个 LLM 引用"
    - 删除前提示影响面（避免"我都删了，怎么 ai 命令都炸了"那种事故）
    """
    # account
    acc_rows = (await db.execute(
        select(Account.id, Account.display_name, Account.phone)
        .where(Account.proxy_id == pid)
        .order_by(Account.id)
    )).all()
    accounts = [
        ProxyUsageItem(
            kind="account",
            id=r[0],
            name=r[1] or None,
            extra=r[2] or None,
        )
        for r in acc_rows
    ]
    # llm_provider
    llm_rows = (await db.execute(
        select(LLMProvider.id, LLMProvider.name, LLMProvider.default_model)
        .where(LLMProvider.proxy_id == pid)
        .order_by(LLMProvider.id)
    )).all()
    llm_providers = [
        ProxyUsageItem(
            kind="llm_provider",
            id=r[0],
            name=r[1] or None,
            extra=r[2] or None,
        )
        for r in llm_rows
    ]
    return ProxyUsageResponse(
        accounts=accounts,
        llm_providers=llm_providers,
        total=len(accounts) + len(llm_providers),
    )
