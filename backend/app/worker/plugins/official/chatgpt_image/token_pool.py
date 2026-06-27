"""轻量 token 池：配置持久化，运行态只放内存。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenEntry:
    token: str
    note: str = ""


def parse_token_lines(value: Any) -> list[str]:
    """从多行/逗号分隔配置中提取去重 token，保持原顺序。"""

    raw = str(value or "")
    parts: list[str] = []
    for line in raw.replace(",", "\n").splitlines():
        token = line.strip()
        if token:
            parts.append(token)
    return list(dict.fromkeys(parts))


def format_token_lines(tokens: list[str]) -> str:
    return "\n".join(list(dict.fromkeys(token.strip() for token in tokens if token.strip())))


def parse_token_entries(value: Any, legacy_value: Any = "") -> list[TokenEntry]:
    """读取新版结构化 token 池，并兼容旧版多行 token 字段。"""

    entries: list[TokenEntry] = []
    seen: set[str] = set()

    def add(token: Any, note: Any = "") -> None:
        token_value = str(token or "").strip()
        if not token_value or token_value in seen:
            return
        seen.add(token_value)
        entries.append(TokenEntry(token=token_value, note=str(note or "").strip()))

    if isinstance(value, list):
        for item in value:
            if isinstance(item, TokenEntry):
                add(item.token, item.note)
            elif isinstance(item, dict):
                token = item.get("token") or item.get("accessToken") or item.get("access_token")
                note = item.get("note") or item.get("remark") or item.get("source") or ""
                add(token, note)
            else:
                add(item)
    else:
        for token in parse_token_lines(value):
            add(token)

    for token in parse_token_lines(legacy_value):
        add(token)

    return entries


def format_token_entries(entries: list[TokenEntry] | list[dict[str, Any]] | list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in entries:
        if isinstance(item, TokenEntry):
            token = item.token.strip()
            note = item.note.strip()
        elif isinstance(item, dict):
            token = str(item.get("token") or "").strip()
            note = str(item.get("note") or item.get("remark") or item.get("source") or "").strip()
        else:
            token = str(item or "").strip()
            note = ""
        if not token or token in seen:
            continue
        seen.add(token)
        out.append({"token": token, "note": note})
    return out


def token_id(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return "token:empty"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"token:{digest}"


def mask_token(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return "<empty>"
    if len(value) <= 24:
        return f"{value[:4]}···{value[-4:]}"
    return f"{value[:10]}···{value[-10:]}"


@dataclass
class TokenState:
    token: str
    note: str = ""
    status: str = "未知"
    quota: int | None = None
    image_quota_unknown: bool = True
    account_type: str = ""
    email: str = ""
    default_model_slug: str = ""
    restore_at: str = ""
    last_used_at: float | None = None
    last_error: str = ""
    disabled_until: float = 0.0
    success: int = 0
    fail: int = 0

    @property
    def token_id(self) -> str:
        return token_id(self.token)

    @property
    def masked(self) -> str:
        return mask_token(self.token)

    def is_available(self, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        if self.disabled_until and self.disabled_until > current:
            return False
        return self.status not in {"异常", "禁用"}

    def mark_failure(self, message: str, skip_seconds: int, *, disable_invalid: bool = True) -> None:
        self.fail += 1
        self.last_error = str(message or "").strip()[:240]
        if skip_seconds > 0:
            self.disabled_until = time.time() + skip_seconds
        if not disable_invalid:
            return
        lower = self.last_error.lower()
        if "invalid" in lower or "401" in lower or "token_invalidated" in lower:
            self.status = "异常"
        elif "quota" in lower or "rate" in lower or "limit" in lower or "429" in lower:
            self.status = "限流"

    def mark_success(self) -> None:
        self.success += 1
        self.last_used_at = time.time()
        self.last_error = ""
        self.disabled_until = 0.0
        if self.status in {"未知", "限流"}:
            self.status = "正常"
        if self.quota is not None and not self.image_quota_unknown:
            self.quota = max(0, self.quota - 1)
            if self.quota == 0:
                self.status = "限流"

    def apply_remote_info(self, info: dict[str, Any]) -> None:
        self.email = str(info.get("email") or self.email or "")
        self.account_type = str(info.get("type") or info.get("plan_type") or self.account_type or "")
        self.default_model_slug = str(info.get("default_model_slug") or self.default_model_slug or "")
        self.restore_at = str(info.get("restore_at") or self.restore_at or "")
        self.status = str(info.get("status") or self.status or "正常")
        self.image_quota_unknown = bool(info.get("image_quota_unknown", self.image_quota_unknown))
        quota = info.get("quota")
        if quota is not None:
            try:
                self.quota = max(0, int(quota))
            except (TypeError, ValueError):
                self.quota = None
        self.last_error = ""
        if self.status == "正常":
            self.disabled_until = 0.0


class TokenPool:
    """按配置 token 顺序轮询，失败时短暂跳过。"""

    def __init__(self) -> None:
        self._index = 0
        self._states: dict[str, TokenState] = {}
        self._tokens: list[str] = []
        self._entries: list[TokenEntry] = []

    def sync(self, raw_tokens: Any, legacy_tokens: Any = "") -> None:
        entries = parse_token_entries(raw_tokens, legacy_tokens)
        tokens = [entry.token for entry in entries]
        self._tokens = tokens
        self._entries = entries
        self._states = {
            entry.token: self._sync_state(entry)
            for entry in entries
        }
        if self._tokens:
            self._index %= len(self._tokens)
        else:
            self._index = 0

    def _sync_state(self, entry: TokenEntry) -> TokenState:
        state = self._states.get(entry.token) or TokenState(token=entry.token)
        state.note = entry.note
        return state

    @property
    def tokens(self) -> list[str]:
        return list(self._tokens)

    @property
    def entries(self) -> list[TokenEntry]:
        return list(self._entries)

    def states(self) -> list[TokenState]:
        return [self._states[token] for token in self._tokens if token in self._states]

    def choose(self) -> TokenState:
        if not self._tokens:
            raise RuntimeError("未配置 ChatGPT token，请先在插件配置页填写 token 池。")
        now = time.time()
        total = len(self._tokens)
        for offset in range(total):
            index = (self._index + offset) % total
            state = self._states[self._tokens[index]]
            if state.is_available(now):
                self._index = (index + 1) % total
                return state
        raise RuntimeError("没有可用 token：所有 token 都处于异常、限流或临时跳过状态。")

    def find(self, ident: str) -> TokenState | None:
        needle = str(ident or "").strip()
        if not needle:
            return None
        if needle.isdigit():
            idx = int(needle) - 1
            if 0 <= idx < len(self._tokens):
                return self._states[self._tokens[idx]]
        for state in self.states():
            if needle in {state.token, state.token_id, state.masked}:
                return state
        return None

    def mark_failure(
        self,
        token: str,
        message: str,
        skip_seconds: int,
        *,
        disable_invalid: bool = True,
    ) -> None:
        state = self._states.get(token)
        if state is not None:
            state.mark_failure(message, skip_seconds, disable_invalid=disable_invalid)

    def mark_success(self, token: str) -> None:
        state = self._states.get(token)
        if state is not None:
            state.mark_success()

    def apply_remote_info(self, token: str, info: dict[str, Any]) -> None:
        state = self._states.get(token)
        if state is not None:
            state.apply_remote_info(info)


__all__ = [
    "TokenPool",
    "TokenEntry",
    "TokenState",
    "format_token_entries",
    "format_token_lines",
    "mask_token",
    "parse_token_entries",
    "parse_token_lines",
    "token_id",
]
