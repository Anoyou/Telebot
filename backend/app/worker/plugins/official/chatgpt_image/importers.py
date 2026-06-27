"""CPA / sub2api token 导入工具。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(frozen=True)
class Sub2APIConfig:
    base_url: str = ""
    email: str = ""
    password: str = ""
    api_key: str = ""
    group_id: str = ""


@dataclass(frozen=True)
class CPAConfig:
    base_url: str = ""
    secret_key: str = ""
    file_names: list[str] = field(default_factory=list)


def parse_names(value: Any) -> list[str]:
    raw = str(value or "")
    parts = re.split(r"[\n,]+", raw)
    return list(dict.fromkeys(part.strip() for part in parts if part.strip()))


def extract_auth_session_access_token(value: Any) -> str:
    """从 chatgpt.com/api/auth/session 返回的完整 JSON 中提取 accessToken。"""

    if isinstance(value, dict):
        return _find_access_token(value)
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if isinstance(payload, dict):
        return _find_access_token(payload)
    return ""


class ImportClient:
    def __init__(self, *, proxy_url: str = "", timeout: int = 60) -> None:
        self.proxy_url = str(proxy_url or "").strip()
        self.timeout = max(15, int(timeout or 60))
        self._sub2api_token_cache: tuple[str, float] | None = None

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(float(self.timeout)),
            "follow_redirects": True,
        }
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.AsyncClient(**kwargs)

    async def list_sub2api_accounts(self, cfg: Sub2APIConfig) -> list[dict[str, Any]]:
        if not cfg.base_url:
            raise ValueError("请先配置 sub2api 地址。")
        headers = await self._sub2api_headers(cfg)
        items: list[dict[str, Any]] = []
        async with self._client() as client:
            page = 1
            while True:
                params: dict[str, Any] = {
                    "platform": "openai",
                    "type": "oauth",
                    "page": page,
                    "page_size": 200,
                }
                if cfg.group_id:
                    params["group"] = cfg.group_id
                response = await client.get(
                    f"{cfg.base_url.rstrip('/')}/api/v1/admin/accounts",
                    headers=headers,
                    params=params,
                )
                _ensure_ok(response, "sub2api 账号列表")
                payload = response.json()
                page_items, total = _extract_paged_items(payload)
                if not page_items:
                    break
                for account in page_items:
                    if not isinstance(account, dict):
                        continue
                    account_id = account.get("id")
                    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
                    items.append({
                        "id": str(account_id) if account_id is not None else "",
                        "name": str(account.get("name") or ""),
                        "email": str(credentials.get("email") or account.get("name") or ""),
                        "status": str(account.get("status") or ""),
                    })
                if page * 200 >= total or len(page_items) < 200:
                    break
                page += 1
        return [item for item in items if item["id"]]

    async def import_sub2api_tokens(self, cfg: Sub2APIConfig) -> list[str]:
        accounts = await self.list_sub2api_accounts(cfg)
        if not accounts:
            return []
        headers = await self._sub2api_headers(cfg)
        tokens: list[str] = []
        async with self._client() as client:
            for account in accounts:
                response = await client.get(
                    f"{cfg.base_url.rstrip('/')}/api/v1/admin/accounts/{account['id']}",
                    headers=headers,
                )
                _ensure_ok(response, f"sub2api 账号 {account['id']}")
                payload = _unwrap_envelope(response.json())
                if not isinstance(payload, dict):
                    continue
                credentials = payload.get("credentials") if isinstance(payload.get("credentials"), dict) else {}
                token = _extract_access_token(credentials)
                if token:
                    tokens.append(token)
        return list(dict.fromkeys(tokens))

    async def _sub2api_headers(self, cfg: Sub2APIConfig) -> dict[str, str]:
        if cfg.api_key:
            return {"x-api-key": cfg.api_key, "Accept": "application/json"}
        if not cfg.email or not cfg.password:
            raise ValueError("sub2api 需要配置 API Key，或同时配置邮箱和密码。")
        cached = self._sub2api_token_cache
        if cached and cached[1] > time.time():
            return {"Authorization": f"Bearer {cached[0]}", "Accept": "application/json"}
        async with self._client() as client:
            response = await client.post(
                f"{cfg.base_url.rstrip('/')}/api/v1/auth/login",
                json={"email": cfg.email, "password": cfg.password},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            _ensure_ok(response, "sub2api 登录")
            body = _unwrap_envelope(response.json())
        if not isinstance(body, dict):
            raise ValueError("sub2api 登录返回格式异常。")
        token = str(body.get("access_token") or "")
        if not token:
            raise ValueError("sub2api 登录没有返回 access_token。")
        expires_in = int(body.get("expires_in") or 3600)
        self._sub2api_token_cache = (token, time.time() + max(60, expires_in) - 300)
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def list_cpa_files(self, cfg: CPAConfig) -> list[dict[str, str]]:
        if not cfg.base_url or not cfg.secret_key:
            raise ValueError("请先配置 CPA 地址和 CPA Secret Key。")
        async with self._client() as client:
            response = await client.get(
                f"{cfg.base_url.rstrip('/')}/v0/management/auth-files",
                headers=_cpa_headers(cfg.secret_key),
            )
            _ensure_ok(response, "CPA 文件列表")
            payload = response.json()
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list):
            raise ValueError("CPA 文件列表返回格式异常。")
        out: list[dict[str, str]] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                out.append({"name": name, "email": str(item.get("email") or item.get("account") or "")})
        return out

    async def import_cpa_tokens(self, cfg: CPAConfig) -> list[str]:
        if not cfg.file_names:
            return []
        tokens: list[str] = []
        async with self._client() as client:
            for name in cfg.file_names:
                response = await client.get(
                    f"{cfg.base_url.rstrip('/')}/v0/management/auth-files/download",
                    headers=_cpa_headers(cfg.secret_key),
                    params={"name": name},
                )
                _ensure_ok(response, f"CPA 文件 {name}")
                payload = response.json()
                if isinstance(payload, dict) and payload.get("access_token"):
                    tokens.append(str(payload["access_token"]).strip())
        return list(dict.fromkeys(token for token in tokens if token))


def _cpa_headers(secret_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret_key}", "Accept": "application/json"}


def _extract_access_token(credentials: object) -> str:
    if not isinstance(credentials, dict):
        return ""
    for key in ("access_token", "accessToken", "token"):
        value = str(credentials.get(key) or "").strip()
        if value:
            return value
    return ""


def _find_access_token(payload: dict[str, Any]) -> str:
    for key in ("accessToken", "access_token"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    for value in payload.values():
        if isinstance(value, dict):
            token = _find_access_token(value)
            if token:
                return token
    return ""


def _unwrap_envelope(payload: object) -> object:
    if isinstance(payload, dict) and "data" in payload and "code" in payload:
        return payload.get("data")
    return payload


def _extract_paged_items(payload: object) -> tuple[list[Any], int]:
    inner = _unwrap_envelope(payload)
    if isinstance(inner, list):
        return inner, len(inner)
    if isinstance(inner, dict):
        for key in ("items", "data", "list"):
            value = inner.get(key)
            if isinstance(value, list):
                return value, int(inner.get("total") or len(value))
    return [], 0


def _ensure_ok(response: httpx.Response, label: str) -> None:
    if 200 <= response.status_code < 300:
        return
    body = response.text[:200]
    raise RuntimeError(f"{label}失败：HTTP {response.status_code} {body}")


__all__ = [
    "CPAConfig",
    "ImportClient",
    "Sub2APIConfig",
    "extract_auth_session_access_token",
    "parse_names",
]
