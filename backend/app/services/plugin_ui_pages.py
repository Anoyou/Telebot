"""插件自带页面（``ui.pages``）与菜单项（``ui.actions``），Plugin API 1.4。

安全模型（见 ``schemas/plugin-ui-widgets.json`` 的 ``pages``）：

- 页面文件只从插件安装目录里**页面入口所在的目录**（如 ``ui/``）读取，解析后仍需落在该目录内，
  隐藏文件与 ``.py`` 一律不提供；
- 打开页面先换一个**会话 token**（HMAC 签名，含插件 / 页面 / 账号 / 过期时间，1 小时），资源走
  ``GET /api/plugin-ui/page-assets/{token}/{path}``：不需要也不读取登录态，响应带严格 CSP
  （``connect-src 'none'``，页面不能联网），HTML 里再注入同样的 ``<meta>`` CSP 与 Bridge 脚本；
- Web 用 ``<iframe sandbox="allow-scripts">``（不给 allow-same-origin：拿不到 Cookie / localStorage /
  宿主 token），iOS 用非持久 WKWebView 并屏蔽网络；
- 页面只能经 Bridge（postMessage / WKScriptMessageHandler）向宿主客户端发请求，宿主客户端带着自己的登录态
  转发到 ``POST /api/plugin-ui/pages/{key}/{page}/bridge``，**服务器按页面声明的 bridge 权限校验**：
  ``config.read`` → 去掉密钥的配置；``cards.read`` → 本插件卡片数据；``actions.invoke`` → ``on_ui_action``。

未声明 ``ui.pages`` / ``ui.actions`` 的插件不会出现任何新入口（旧插件行为与界面不变）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.feature import AccountFeature, Feature
from ..db.models.plugin import InstalledPlugin
from ..settings import settings
from ..worker.plugins import plugin_ui
from . import plugin_ui_cards

BRIDGE_PROTOCOL = 1
PAGE_MENUS = ("plugin.detail", "account.detail")
_ALWAYS_METHODS = {"ready"}
_PERMISSION_METHODS = {"config.get": "config.read", "card.data": "cards.read", "action.invoke": "actions.invoke"}
_TOKEN_VERSION = "1"


class PluginPageError(RuntimeError):
    """页面 / 菜单项不存在、权限不足或请求不合法。"""

    def __init__(self, message: str, code: str = "PLUGIN_PAGE_INVALID", status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _contract_pages() -> Mapping[str, Any]:
    return plugin_ui.load_contract().get("pages") or {}


def page_csp() -> str:
    return str(_contract_pages().get("sandbox", {}).get("csp") or "default-src 'none'")


def session_ttl_seconds() -> int:
    return int(_contract_pages().get("session_ttl_seconds") or 3600)


def max_file_bytes() -> int:
    return int(_contract_pages().get("max_file_bytes") or 2 * 1024 * 1024)


# ─────────────────────────────────────────────────────
# 声明
# ─────────────────────────────────────────────────────


async def plugin_entries(db: AsyncSession, plugin_key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    feature = await db.get(Feature, plugin_key)
    installed = await db.get(InstalledPlugin, plugin_key)
    ui, _reads = plugin_ui_cards.ui_declaration(feature, installed)
    return plugin_ui.normalize_ui_entries(ui)


async def _plugin_name(db: AsyncSession, plugin_key: str) -> str:
    feature = await db.get(Feature, plugin_key)
    return str(getattr(feature, "display_name", "") or plugin_key)


async def list_entries(
    db: AsyncSession,
    *,
    menu: str,
    plugin_key: str | None = None,
    account_id: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """某个菜单位置的页面与菜单项：``(pages, actions, warnings)``，没有声明时都是空列表。"""

    if menu not in plugin_ui.page_menus():
        raise PluginPageError(f"未知菜单位置：{menu}")
    if plugin_key:
        keys = [plugin_key]
    elif menu == "account.detail":
        if account_id is None:
            raise PluginPageError("account.detail 需要 account_id")
        rows = await db.execute(
            select(AccountFeature.feature_key).where(
                AccountFeature.account_id == account_id, AccountFeature.enabled.is_(True)
            )
        )
        keys = sorted({str(item) for item in rows.scalars().all()})
    else:
        raise PluginPageError("plugin.detail 需要 plugin_key")
    pages_out: list[dict[str, Any]] = []
    actions_out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for key in keys:
        pages, actions, problems = await plugin_entries(db, key)
        warnings.extend(f"{key}: {item}" for item in problems)
        if not pages and not actions:
            continue
        name = await _plugin_name(db, key)
        for page in pages:
            if menu in page["menus"]:
                pages_out.append(
                    {
                        "plugin_key": key,
                        "plugin_name": name,
                        "page_id": page["id"],
                        "title": page["title"],
                        "subtitle": page.get("subtitle"),
                        "bridge": list(page["bridge"]),
                    }
                )
        for action in actions:
            if action["menu"] == menu:
                actions_out.append(
                    {
                        "plugin_key": key,
                        "plugin_name": name,
                        "action_id": action["id"],
                        "menu": action["menu"],
                        "title": action["title"],
                        "confirm": action.get("confirm"),
                        "destructive": bool(action.get("destructive")),
                        "page_id": action.get("page"),
                    }
                )
    return pages_out, actions_out, warnings


async def find_page(db: AsyncSession, plugin_key: str, page_id: str) -> dict[str, Any]:
    pages, _actions, _warnings = await plugin_entries(db, plugin_key)
    for page in pages:
        if page["id"] == page_id:
            return page
    raise PluginPageError(f"插件 {plugin_key} 没有声明页面 {page_id}", "PLUGIN_PAGE_NOT_FOUND", 404)


async def find_menu_action(db: AsyncSession, plugin_key: str, action_id: str, menu: str | None) -> dict[str, Any]:
    _pages, actions, _warnings = await plugin_entries(db, plugin_key)
    for action in actions:
        if action["id"] == action_id and (menu is None or action["menu"] == menu):
            return action
    raise PluginPageError(f"插件 {plugin_key} 没有声明菜单项 {action_id}", "PLUGIN_ACTION_NOT_FOUND", 404)


# ─────────────────────────────────────────────────────
# 会话 token（无状态 HMAC，不含任何登录凭据）
# ─────────────────────────────────────────────────────


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signing_key() -> bytes:
    return hashlib.sha256(f"telepilot-plugin-page:{settings.jwt_secret}".encode()).digest()


def issue_page_token(plugin_key: str, page_id: str, account_id: int | None, *, now: float | None = None) -> tuple[str, int]:
    issued = int(now if now is not None else time.time())
    expires_at = issued + session_ttl_seconds()
    payload = {"v": _TOKEN_VERSION, "k": plugin_key, "p": page_id, "a": account_id, "exp": expires_at}
    body = _b64(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    sig = _b64(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest()[:24])
    return f"{body}.{sig}", expires_at


def verify_page_token(token: str, *, now: float | None = None) -> dict[str, Any]:
    body, _, sig = str(token or "").partition(".")
    if not body or not sig:
        raise PluginPageError("页面链接无效", "PLUGIN_PAGE_TOKEN_INVALID", 404)
    expected = _b64(hmac.new(_signing_key(), body.encode(), hashlib.sha256).digest()[:24])
    if not hmac.compare_digest(expected, sig):
        raise PluginPageError("页面链接无效", "PLUGIN_PAGE_TOKEN_INVALID", 404)
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise PluginPageError("页面链接无效", "PLUGIN_PAGE_TOKEN_INVALID", 404) from exc
    if not isinstance(payload, dict) or payload.get("v") != _TOKEN_VERSION:
        raise PluginPageError("页面链接无效", "PLUGIN_PAGE_TOKEN_INVALID", 404)
    if int(payload.get("exp") or 0) < int(now if now is not None else time.time()):
        raise PluginPageError("页面链接已过期，请重新打开", "PLUGIN_PAGE_TOKEN_EXPIRED", 410)
    return payload


async def open_page_session(
    db: AsyncSession, *, plugin_key: str, page_id: str, account_id: int | None
) -> dict[str, Any]:
    page = await find_page(db, plugin_key, page_id)
    if account_id is not None:
        enabled = await db.execute(
            select(AccountFeature.enabled).where(
                AccountFeature.account_id == account_id, AccountFeature.feature_key == plugin_key
            )
        )
        if enabled.scalar_one_or_none() is not True:
            raise PluginPageError("该账号没有启用这个插件", "PLUGIN_PAGE_NOT_ENABLED", 409)
    token, expires_at = issue_page_token(plugin_key, page_id, account_id)
    entry = page["file"]
    return {
        "plugin_key": plugin_key,
        "page_id": page_id,
        "title": page["title"],
        "token": token,
        "url": f"/api/plugin-ui/page-assets/{token}/{entry}",
        "entry": entry,
        "expires_at": expires_at,
        "bridge": list(page["bridge"]),
        "protocol": BRIDGE_PROTOCOL,
        "csp": page_csp(),
    }


# ─────────────────────────────────────────────────────
# 资源
# ─────────────────────────────────────────────────────

BRIDGE_SHIM = r"""(function(){
if (window.telepilot) return;
var pending = {}, seq = 0, themeCbs = [];
var wk = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.telepilot;
function post(msg){ if (wk) { wk.postMessage(msg); } else { window.parent.postMessage(msg, "*"); } }
function applyTheme(data){ try { var s = (data && data.scheme) === "dark" ? "dark" : "light"; document.documentElement.setAttribute("data-theme", s); document.documentElement.style.colorScheme = s; } catch (e) {} }
function receive(msg){
  if (!msg || msg.tp !== "telepilot") return;
  if (msg.event === "theme") { applyTheme(msg.data); themeCbs.forEach(function(cb){ try { cb(msg.data); } catch (e) {} }); return; }
  var p = msg.id && pending[msg.id]; if (!p) return;
  delete pending[msg.id]; clearTimeout(p.t);
  if (msg.ok) { p.resolve(msg.result); } else { var err = new Error((msg.error && msg.error.message) || "请求失败"); err.code = msg.error && msg.error.code; p.reject(err); }
}
window.__telepilotReceive = function(m){ receive(typeof m === "string" ? JSON.parse(m) : m); };
if (!wk) window.addEventListener("message", function(e){ if (e.source === window.parent) receive(e.data); });
function call(method, params){
  return new Promise(function(resolve, reject){
    var id = "r" + (++seq);
    var t = setTimeout(function(){ delete pending[id]; var err = new Error("宿主没有响应"); err.code = "BRIDGE_TIMEOUT"; reject(err); }, 15000);
    pending[id] = {resolve: resolve, reject: reject, t: t};
    post({tp: "telepilot", v: 1, id: id, method: method, params: params || {}});
  });
}
window.telepilot = {
  protocol: 1,
  call: call,
  ready: function(){ return call("ready"); },
  theme: function(){ return call("theme.get").then(function(d){ applyTheme(d); return d; }); },
  onTheme: function(cb){ if (typeof cb === "function") themeCbs.push(cb); },
  config: function(){ return call("config.get"); },
  card: function(id){ return call("card.data", {card_id: id}); },
  action: function(id, payload){ return call("action.invoke", {id: id, payload: payload || {}}); }
};
})();"""


def _plugin_dir(installed: InstalledPlugin | None) -> Path | None:
    path = str(getattr(installed, "installed_path", "") or "").strip()
    if not path:
        return None
    root = Path(path)
    return root if root.is_dir() else None


def inject_into_html(html: str) -> str:
    """在 ``<head>`` 最前面注入 meta CSP 与 Bridge 脚本（iOS loadHTMLString 时 meta CSP 生效）。"""

    csp = page_csp().replace('"', "&quot;")
    injection = (
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        '<meta name="referrer" content="no-referrer">'
        f"<script>{BRIDGE_SHIM}</script>"
    )
    lower = html.lower()
    head = lower.find("<head")
    if head >= 0:
        end = lower.find(">", head)
        if end >= 0:
            return html[: end + 1] + injection + html[end + 1 :]
    return f"<!doctype html><html><head>{injection}</head><body>{html}</body></html>"


def asset_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": page_csp(),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    }


async def read_asset(db: AsyncSession, *, token: str, path: str) -> tuple[bytes, str]:
    """``(内容, content-type)``；token 无效 / 文件越界 / 不存在都抛 :class:`PluginPageError`。"""

    payload = verify_page_token(token)
    plugin_key = str(payload.get("k") or "")
    page = await find_page(db, plugin_key, str(payload.get("p") or ""))
    root = _plugin_dir(await db.get(InstalledPlugin, plugin_key))
    rel = plugin_ui._safe_relative_path(path)  # noqa: SLF001 - 同一套相对路径规则
    if root is None or rel is None:
        raise PluginPageError("文件不存在", "PLUGIN_PAGE_FILE_NOT_FOUND", 404)
    plugin_root = root.resolve()
    # 只提供页面入口文件所在目录（如 ui/）里的文件：plugin.json、插件代码等不对页面开放。
    page_dir = Path(page["file"]).parent
    base = (plugin_root / page_dir).resolve() if str(page_dir) not in {"", "."} else plugin_root
    if base != plugin_root and plugin_root not in base.parents:
        raise PluginPageError("文件不存在", "PLUGIN_PAGE_FILE_NOT_FOUND", 404)
    target = (plugin_root / rel).resolve()
    if base not in target.parents or not target.is_file():
        raise PluginPageError("文件不存在", "PLUGIN_PAGE_FILE_NOT_FOUND", 404)
    if any(part.startswith(".") for part in target.relative_to(base).parts) or target.suffix.lower() == ".py":
        raise PluginPageError("文件不存在", "PLUGIN_PAGE_FILE_NOT_FOUND", 404)
    if target.stat().st_size > max_file_bytes():
        raise PluginPageError("文件过大", "PLUGIN_PAGE_FILE_TOO_LARGE", 413)
    data = target.read_bytes()
    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix.lower() in {".html", ".htm"}:
        data = inject_into_html(data.decode("utf-8", errors="replace")).encode("utf-8")
        content_type = "text/html"
    if content_type.startswith("text/") or content_type in {"application/javascript", "application/json", "image/svg+xml"}:
        content_type += "; charset=utf-8"
    return data, content_type


# ─────────────────────────────────────────────────────
# Bridge
# ─────────────────────────────────────────────────────


def _drop_secrets(value: Any, schema: Any, secret_keys: set[str]) -> Any:
    from .plugin_config_secrets import _path_is_sensitive, is_secret_envelope  # noqa: PLC2701

    if isinstance(value, Mapping):
        props = schema.get("properties") if isinstance(schema, Mapping) else None
        props = props if isinstance(props, Mapping) else {}
        out: dict[str, Any] = {}
        for key, item in value.items():
            text = str(key)
            prop = props.get(text) if isinstance(props.get(text), Mapping) else None
            if text in secret_keys or _path_is_sensitive(text, dict(prop) if prop else None):
                continue
            if isinstance(prop, Mapping) and prop.get("x-secret-list"):
                continue
            out[text] = _drop_secrets(item, prop, set())
        return out
    if isinstance(value, list):
        items = schema.get("items") if isinstance(schema, Mapping) else None
        return [_drop_secrets(item, items, set()) for item in value if not is_secret_envelope(item)]
    if is_secret_envelope(value):
        return None
    return value


def public_config(config: Mapping[str, Any], schema: Any, plugin_key: str | None = None) -> dict[str, Any]:
    """给插件页面的配置：密钥字段整项去掉（不是遮罩），再过一遍通用脱敏。"""

    from .. import plugin_compat
    from . import plugin_secret_list
    from .redactor import redact_value

    specs = plugin_secret_list.secret_list_specs(schema) or plugin_compat.legacy_secret_list_fields(plugin_key, schema)
    secret_keys = set(specs)
    for spec in specs.values():
        if spec.get("legacy_field"):
            secret_keys.add(str(spec["legacy_field"]))
    cleaned = _drop_secrets(dict(config or {}), schema, secret_keys)
    return redact_value(cleaned) if isinstance(cleaned, dict) else {}


async def bridge_call(
    db: AsyncSession,
    *,
    plugin_key: str,
    page_id: str,
    method: str,
    params: Mapping[str, Any] | None,
    account_id: int | None,
) -> Any:
    from . import feature_service

    page = await find_page(db, plugin_key, page_id)
    method = str(method or "").strip()
    params = dict(params or {})
    if method in _ALWAYS_METHODS:
        from .. import __version__

        return {
            "plugin_key": plugin_key,
            "page_id": page_id,
            "account_id": account_id,
            "bridge": list(page["bridge"]),
            "protocol": BRIDGE_PROTOCOL,
            "host_version": str(__version__),
        }
    permission = _PERMISSION_METHODS.get(method)
    if permission is None:
        raise PluginPageError(f"Bridge 不支持方法 {method}", "BRIDGE_METHOD_UNKNOWN", 400)
    if permission not in page["bridge"]:
        raise PluginPageError(f"页面没有声明 Bridge 权限 {permission}", "BRIDGE_PERMISSION_DENIED", 403)
    if method == "config.get":
        feature = await db.get(Feature, plugin_key)
        manifest = getattr(feature, "manifest", None)
        schema = manifest.get("config_schema") if isinstance(manifest, Mapping) else None
        if account_id is not None:
            config = await feature_service.get_effective_plugin_config(db, account_id, plugin_key)
        else:
            config = await feature_service.get_plugin_global_config(db, plugin_key)
        return {"scope": "account" if account_id is not None else "global", "config": public_config(config, schema, plugin_key)}
    if method == "card.data":
        card_id = str(params.get("card_id") or "").strip()
        if not card_id or card_id.startswith(plugin_ui.HOST_CARD_PREFIX):
            raise PluginPageError("card_id 不合法", "BRIDGE_PARAMS_INVALID", 400)
        return await plugin_ui_cards.card_data(db, plugin_key=plugin_key, card_id=card_id, account_id=account_id)
    # action.invoke
    action_id = str(params.get("id") or "").strip()
    if not plugin_ui.ACTION_ID_RE.match(action_id):
        raise PluginPageError("action id 不合法", "BRIDGE_PARAMS_INVALID", 400)
    if page["actions"] and action_id not in page["actions"]:
        raise PluginPageError(f"页面没有声明可调用 {action_id}", "BRIDGE_PERMISSION_DENIED", 403)
    payload = params.get("payload")
    if payload is not None and not isinstance(payload, Mapping):
        raise PluginPageError("payload 必须是对象", "BRIDGE_PARAMS_INVALID", 400)
    if len(json.dumps(payload or {}, ensure_ascii=False, default=str)) > 16_384:
        raise PluginPageError("payload 过大（≤16KB）", "BRIDGE_PARAMS_INVALID", 413)
    return await invoke_ui_action(
        db,
        plugin_key=plugin_key,
        action_id=action_id,
        account_id=account_id,
        context={"page_id": page_id, "menu": None, "payload": dict(payload or {})},
    )


async def invoke_ui_action(
    db: AsyncSession,
    *,
    plugin_key: str,
    action_id: str,
    account_id: int | None,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """调插件 ``on_ui_action(ctx, action_id, context)``（10 秒）。context 至少含 account_id。"""

    from .plugin_config_hooks import call_plugin_hook, plugin_overrides

    _feature, _account, plugin_cls, ctx = await plugin_ui_cards._action_context(db, plugin_key, account_id)  # noqa: SLF001
    if not plugin_overrides(plugin_cls, "on_ui_action"):
        raise PluginPageError("插件没有实现 on_ui_action", "PLUGIN_ACTION_UNAVAILABLE", 404)
    full_context = {"account_id": account_id, **dict(context)}
    result = await call_plugin_hook(plugin_cls, ctx, "on_ui_action", action_id, full_context, event_type=action_id)
    if result is None:
        return {"ok": True, "message": None, "refresh": True, "data": None}
    if not isinstance(result, Mapping):
        raise PluginPageError("on_ui_action 必须返回对象", "PLUGIN_ACTION_FAILED", 400)
    message = result.get("message") or result.get("toast")
    data = result.get("data")
    if data is not None:
        data = json.loads(json.dumps(data, ensure_ascii=False, default=str))
    return {
        "ok": result.get("ok") is not False,
        "message": str(message) if message else None,
        "refresh": result.get("refresh") is not False,
        "data": data,
    }


__all__ = [
    "BRIDGE_PROTOCOL",
    "BRIDGE_SHIM",
    "PluginPageError",
    "asset_headers",
    "bridge_call",
    "find_menu_action",
    "find_page",
    "inject_into_html",
    "invoke_ui_action",
    "issue_page_token",
    "list_entries",
    "open_page_session",
    "public_config",
    "read_asset",
    "verify_page_token",
]
