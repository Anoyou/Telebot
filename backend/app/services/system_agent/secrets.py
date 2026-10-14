"""聊天密钥抽取与 Action 敏感字段处理（阶段 3）。"""

from __future__ import annotations

import re
from typing import Any

from .actions import encrypt_secret_payload, split_secret_arguments
from .redactor import redact_message_text

# Provider 粘贴路由和持久化前密钥抽取必须共用这些模式。
_URL_RE = re.compile(r"https?://[^\s\"'<>()]+", re.I)
_KNOWN_KEY_RE = re.compile(
    r"\b(?:sk-ant-|sk-proj-|sk-or-|sk-|xai-|gsk_|AIza|ghp_|hf_)[A-Za-z0-9_\-]{8,}"
)
_GENERIC_KEY_RE = re.compile(
    r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{24,}\b"
)
_DOTTED_KEY_RE = re.compile(r"\b[A-Za-z0-9]{12,}\.[A-Za-z0-9]{12,}\b")
_LABELED_SECRET_RE = re.compile(
    r'''(?ix)
    (?:["']?)
    (?:api[_-]?key|apikey|token|authorization|password|secret)
    (?:["']?)\s*[:=]\s*
    (?P<value>"[^"\r\n]{8,}"|'[^'\r\n]{8,}'|[^\s,;}\]]{8,})
    '''
)
_REQUEST_HEADERS_CONTEXT_RE = re.compile(
    r"(?i)(?:\b(?:request|http|custom|compatibility)[\s_-]*headers?\b|请求头|兼容请求头)"
)
_BOT_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_URL_PASSWORD_RE = re.compile(
    r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s@/]+)@",
    re.I,
)
_NON_PROVIDER_URL_HINTS = (
    "github.com",
    "gitlab.com",
    "gitee.com",
    ".git",
    "插件",
    "仓库",
    "plugin",
    "repo",
)
_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    _KNOWN_KEY_RE,
    _BOT_TOKEN_RE,
    re.compile(r"(?i)\b(api[_-]?key|token|authorization|password)\s*[:=]\s*([^\s,;]{12,})"),
)

_SECRET_PLACEHOLDERS = {
    "***",
    "[redacted]",
    "<redacted>",
    "redacted",
    "[masked]",
    "masked",
}


def _is_secret_placeholder(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in _SECRET_PLACEHOLDERS:
        return True
    return bool(normalized) and "***" in normalized

# 会话内占位符格式 `[[TP-CRED-n]]`：纯 ASCII、不含 api_key/token/password/secret 等
# 触发词、不含 `:=`，因此不会命中 redactor 的密钥/敏感标签正则。
# 之所以不用 `[REDACTED]`，是因为模型拿到 `[REDACTED]` 后无法把它填回工具参数；
# 占位符是既可被模型引用、又能还原成真实值的往返令牌。
PLACEHOLDER_PREFIX = "[[TP-CRED-"
PLACEHOLDER_SUFFIX = "]]"
PLACEHOLDER_RE = re.compile(r"\[\[TP-CRED-(\d+)\]\]")
# 单会话最多保留多少个映射；超出后按 FIFO 淘汰，避免加密列无限增长。
MAX_SECRET_MAP_ENTRIES = 20


def make_placeholder(index: int) -> str:
    """生成第 ``index`` 个（从 1 开始）占位符。"""
    return f"{PLACEHOLDER_PREFIX}{index}{PLACEHOLDER_SUFFIX}"


def is_credential_placeholder(value: Any) -> bool:
    """判断一个字符串是否整体就是本机制生成的占位符。"""
    return bool(PLACEHOLDER_RE.fullmatch(str(value or "").strip()))


def find_credential_placeholders(text: Any) -> list[str]:
    """按出现顺序列出文本中的占位符（去重）。"""
    found: list[str] = []
    for token in PLACEHOLDER_RE.findall(str(text or "")):
        candidate = make_placeholder(int(token))
        if candidate not in found:
            found.append(candidate)
    return found


def build_secret_map(secrets: list[str]) -> dict[str, str]:
    """把密钥列表映射成 ``{placeholder: secret}``。"""
    return {make_placeholder(index): secret for index, secret in enumerate(secrets, start=1)}


def cap_secret_map(secret_map: dict[str, str]) -> dict[str, str]:
    """FIFO 截断到 ``MAX_SECRET_MAP_ENTRIES``，保留最新的那批。"""
    if len(secret_map) <= MAX_SECRET_MAP_ENTRIES:
        return dict(secret_map)
    keys = list(secret_map)[-MAX_SECRET_MAP_ENTRIES:]
    return {key: secret_map[key] for key in keys}


def restore_placeholders(text: Any, secret_map: dict[str, str] | None) -> str:
    """把文本里的占位符还原成真实值。

    只替换完整的占位符 token；``secret_map`` 为空或占位符已不在映射中时原样保留，
    绝不因为"猜不到"就把整段文本打码或丢弃——显示边界宁可显示占位符也不能丢内容。
    """
    out = str(text or "")
    if not out or not secret_map:
        return out
    for placeholder, secret in secret_map.items():
        if secret and placeholder in out:
            out = out.replace(placeholder, str(secret))
    return out


def placeholders_remain(text: Any) -> bool:
    """文本中是否还有未还原的占位符。"""
    return bool(PLACEHOLDER_RE.search(str(text or "")))


def apply_secret_placeholders(text: Any, secret_map: dict[str, str] | None) -> str:
    """把文本里的真实密钥替换成占位符。

    出站单点：给模型的用户文本与要落库的用户文本都走这里。只有本会话
    ``build_secret_map`` 生成的映射参与替换，不会碰无关内容。
    """
    out = str(text or "")
    if not out or not secret_map:
        return out
    for placeholder, secret in secret_map.items():
        if secret and secret in out:
            out = out.replace(secret, placeholder)
    return out


def encrypt_secret_map(secret_map: dict[str, str]) -> str | None:
    """序列化并加密占位符映射，供写入 ``SystemAgentSession.secret_map_enc``。"""
    from .actions import encrypt_secret_payload

    return encrypt_secret_payload(cap_secret_map(secret_map))


def decrypt_secret_map(token: str | None) -> dict[str, str]:
    """解密占位符映射；解密失败或格式异常时返回空映射（显示边界退化为占位符）。"""
    from .actions import decrypt_secret_payload

    raw = decrypt_secret_payload(token)
    return {
        str(placeholder): str(value)
        for placeholder, value in raw.items()
        if is_credential_placeholder(placeholder) and str(value)
    }


async def load_session_secret_map(db: Any, session_id: str) -> dict[str, str]:
    """按会话读取占位符映射，供面向 owner 的显示边界还原。"""
    from ...db.models.system_agent import SystemAgentSession

    try:
        session = await db.get(SystemAgentSession, session_id)
    except Exception:  # noqa: BLE001
        return {}
    if session is None:
        return {}
    return decrypt_secret_map(getattr(session, "secret_map_enc", None))


async def load_run_secret_map(db: Any, run_id: str) -> dict[str, str]:
    """按 run 读取所属会话的占位符映射。"""
    from ...db.models.system_agent import SystemAgentRun

    try:
        run = await db.get(SystemAgentRun, run_id)
    except Exception:  # noqa: BLE001
        return {}
    if run is None:
        return {}
    return await load_session_secret_map(db, str(run.session_id))


def _provider_credential_remainder(text: str) -> str | None:
    raw = str(text or "")
    lowered = raw.lower()
    urls = _URL_RE.findall(raw)
    if not urls and "base_url" not in lowered and "baseurl" not in lowered:
        return None
    if any(hint in lowered for hint in _NON_PROVIDER_URL_HINTS):
        return None
    return _URL_RE.sub(" ", raw)


def looks_like_provider_credential_paste(text: str) -> bool:
    """判断 Base URL 与 Provider Key 是否同现。"""

    remainder = _provider_credential_remainder(text)
    if remainder is None:
        return False
    return bool(
        _KNOWN_KEY_RE.search(remainder)
        or _DOTTED_KEY_RE.search(remainder)
        or _GENERIC_KEY_RE.search(remainder)
        or _LABELED_SECRET_RE.search(remainder)
    )


def looks_like_standalone_provider_key(text: str) -> bool:
    """严格判断整条消息是否只有一个可信的 Provider Key。

    该判断只用于上一轮已经进入 Provider 任务后的安全续接。整条消息必须是单一
    token，避免把普通说明中的长 ID、链接或其他领域凭据误认成 Provider Key。
    """

    candidate = str(text or "").strip().strip("\"'`")
    if not candidate or any(ch.isspace() for ch in candidate):
        return False
    return bool(
        _KNOWN_KEY_RE.fullmatch(candidate)
        or _DOTTED_KEY_RE.fullmatch(candidate)
        or _GENERIC_KEY_RE.fullmatch(candidate)
    )


def extract_plaintext_secrets(text: str) -> list[str]:
    """从用户消息中提取疑似密钥明文（去重，保序）。"""

    found: list[str] = []
    seen: set[str] = set()
    raw = str(text or "")
    for match in _LABELED_SECRET_RE.finditer(raw):
        value = str(match.group("value") or "").strip().strip("\"'`")
        if len(value) >= 8 and value not in seen:
            seen.add(value)
            found.append(value)
    for pat in _KEY_PATTERNS:
        for match in pat.finditer(raw):
            # 带捕获组的模式取最后一组
            value = match.group(match.lastindex) if match.lastindex else match.group(0)
            value = str(value or "").strip().strip("\"'`")
            if len(value) < 12:
                continue
            if value.lower() in {"api_key", "token", "authorization", "password"}:
                continue
            if value in seen:
                continue
            seen.add(value)
            found.append(value)
    for match in _URL_PASSWORD_RE.finditer(raw):
        value = match.group(1)
        if value and value not in seen:
            seen.add(value)
            found.append(value)
    if looks_like_standalone_provider_key(raw):
        value = raw.strip().strip("\"'`")
        if value not in seen:
            seen.add(value)
            found.append(value)
    remainder = _provider_credential_remainder(raw)
    if remainder is not None and looks_like_provider_credential_paste(raw):
        for pat in (_DOTTED_KEY_RE, _GENERIC_KEY_RE):
            for match in pat.finditer(remainder):
                value = match.group(0)
                if value not in seen:
                    seen.add(value)
                    found.append(value)
    return found


def redact_known_secrets(text: str, secrets: list[str] | None = None) -> str:
    """先替换已知明文密钥，再做基础字段打码。"""

    out = str(text or "")
    for secret in secrets or []:
        if secret and secret in out:
            out = out.replace(secret, "[REDACTED]")
    return redact_message_text(out)


def contains_request_header_context(text: str) -> bool:
    """自定义请求头值禁止经聊天传递；其自然语言形态无法可靠逐值抽取。"""

    return bool(_REQUEST_HEADERS_CONTEXT_RE.search(str(text or "")))


def redact_user_message(text: str, secrets: list[str] | None = None) -> str:
    """生成可持久化的用户消息；请求头配置整段替换为安全提示。"""

    if contains_request_header_context(text):
        return "[安全提示：自定义请求头不能通过聊天传入，请在 AI Provider 设置中填写并轮换密钥。]"
    return redact_known_secrets(text, secrets)


def merge_secret_into_arguments(
    arguments: dict[str, Any],
    *,
    secret_names: tuple[str, ...],
    chat_secrets: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """合并工具参数中的密钥与聊天提取的密钥。

    若敏感字段均为空或只有掩码占位符，使用当前聊天提取到的真实密钥；
    已有任一真实值时不再跨字段补注入。
    """

    args = dict(arguments or {})
    secrets_list = list(chat_secrets or [])
    if secrets_list and secret_names:
        # 聊天抽取结果没有可靠的字段归属信息：优先替换模型明确给出的脱敏占位符；
        # 仅当所有敏感字段都为空时，才回填约定中的第一个字段。若已有任一真实值，
        # 继续把同一密钥塞入另一个缺失字段会把 API Key 误当成请求头、Token 等。
        has_concrete_secret = any(
            args.get(name) not in (None, "")
            and not _is_secret_placeholder(args.get(name))
            for name in secret_names
        )
        target_name = None
        if not has_concrete_secret:
            target_name = next(
                (name for name in secret_names if _is_secret_placeholder(args.get(name))),
                secret_names[0],
            )
        if target_name is not None:
            args[target_name] = secrets_list[0]
    return split_secret_arguments(args, secret_names)


def restore_argument_placeholders(
    arguments: dict[str, Any],
    secret_map: dict[str, str] | None,
) -> tuple[dict[str, Any], list[str]]:
    """把工具参数里的占位符还原成真实值。

    模型可以把占位符填进**任意**参数（不止约定的 ``secret_argument_names``），
    所以这里扫全部参数值而不是白名单字段。返回还原后的参数与被触发的占位符列表。
    """
    args = dict(arguments or {})
    if not secret_map:
        return args, []
    triggered: list[str] = []

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            matches = find_credential_placeholders(value)
            if not matches:
                return value
            triggered.extend(matches)
            return restore_placeholders(value, secret_map)
        if isinstance(value, list):
            return [_walk(item) for item in value]
        if isinstance(value, dict):
            return {key: _walk(item) for key, item in value.items()}
        return value
    restored = _walk(args)
    return restored, triggered


def encrypt_secrets_dict(secrets: dict[str, Any]) -> str | None:
    return encrypt_secret_payload(secrets)


__all__ = [
    "MAX_SECRET_MAP_ENTRIES",
    "PLACEHOLDER_RE",
    "apply_secret_placeholders",
    "build_secret_map",
    "cap_secret_map",
    "decrypt_secret_map",
    "encrypt_secret_map",
    "cap_secret_map",
    "contains_request_header_context",
    "encrypt_secrets_dict",
    "extract_plaintext_secrets",
    "find_credential_placeholders",
    "is_credential_placeholder",
    "load_run_secret_map",
    "load_session_secret_map",
    "looks_like_provider_credential_paste",
    "looks_like_standalone_provider_key",
    "make_placeholder",
    "merge_secret_into_arguments",
    "placeholders_remain",
    "redact_known_secrets",
    "restore_argument_placeholders",
    "redact_user_message",
    "restore_placeholders",
]
