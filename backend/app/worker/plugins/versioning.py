"""插件版本协商：宿主版本比较（含 beta 号）与 Plugin API 版本范围。

本模块只依赖标准库，主进程、worker、插件库兼容检查脚本都可以直接导入。

两类版本：

- **宿主版本**（``TELEPILOT_VERSION``，如 ``0.107.0-beta.1``）：插件用 ``min_telepilot_version``
  声明最低宿主版本。0.107.0 起比较时考虑 prerelease，插件可以声明 ``0.106.0-beta.12``。
- **Plugin API 版本**（:data:`PLUGIN_API_VERSION`，``MAJOR.MINOR``）：插件可在 ``plugin.json`` 里用
  ``plugin_api`` 声明兼容范围（如 ``"^1.0"``、``">=1.0,<2"``、``"1.x"``）。新增能力只升 MINOR，
  破坏兼容才升 MAJOR；能力细粒度协商见 :mod:`.capabilities`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

# Plugin API 版本。规则：
# - 新增插件可用的接口 / 能力（向后兼容）→ MINOR + 1；
# - 删除或改变已有接口语义（不兼容）→ MAJOR + 1，并在弃用登记里提前至少一个 minor 宿主版本公告。
# 1.0：0.107.0 首次引入（能力注册表 ctx.capabilities、钩子统计、弃用登记、prerelease 版本比较）。
# 1.1：0.108.0 通用交互原语（回调 token own_buttons、统一 MessageOps、ctx.host.read、bot_api_extra /
#      extra_body 透传、钩子 Trace 与熔断同口径、interaction_entries[].policy）。
# 1.2：0.109.0 消息管线中间件（inbound / outbound / delivered、render_profile、core.*、
#      stream_complete extra_body、同步阻塞统计）。
PLUGIN_API_VERSION = "1.2"

_VERSION_RE = re.compile(
    r"^\s*v?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?\s*$"
)


@total_ordering
@dataclass(frozen=True)
class Version:
    """宽松解析的 SemVer：``0.106.0-beta.12`` / ``v0.9`` / ``1``。"""

    major: int
    minor: int = 0
    patch: int = 0
    prerelease: tuple[int | str, ...] = ()

    @property
    def core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    def _cmp_key(self) -> tuple:
        # SemVer 2.0 §11：同一 core 下，带 prerelease 的版本低于正式版；
        # prerelease 各段数字按数值比较、字符串按字典序，数字段低于字符串段。
        if not self.prerelease:
            pre_key: tuple = ((1,),)
        else:
            pre_key = tuple(
                (0, 0, item, "") if isinstance(item, int) else (0, 1, 0, item) for item in self.prerelease
            )
            pre_key = ((0,), *pre_key)
        return (self.core, pre_key)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._cmp_key() < other._cmp_key()

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(str(item) for item in self.prerelease)
        return text


def parse_version(raw: str | None) -> Version | None:
    """解析版本号；无法解析时返回 ``None``（不抛异常）。"""

    if raw is None:
        return None
    match = _VERSION_RE.match(str(raw))
    if not match:
        return None
    prerelease: tuple[int | str, ...] = ()
    if match.group("pre"):
        prerelease = tuple(
            int(part) if part.isdigit() else part for part in match.group("pre").split(".") if part
        )
    return Version(
        int(match.group("major")),
        int(match.group("minor") or 0),
        int(match.group("patch") or 0),
        prerelease,
    )


def compare_versions(left: str | None, right: str | None) -> int:
    """完整 SemVer 比较（含 prerelease）。无法解析的一侧视为最低。"""

    lv, rv = parse_version(left), parse_version(right)
    if lv is None and rv is None:
        return 0
    if lv is None:
        return -1
    if rv is None:
        return 1
    return (lv > rv) - (lv < rv)


def host_satisfies_min_version(host_version: str | None, min_version: str | None) -> bool:
    """宿主版本是否满足插件声明的 ``min_telepilot_version``。

    - 插件没声明或声明无法解析 → 满足（与旧宿主一致：只拒绝能确定太旧的情况）。
    - 声明**不带** prerelease（如 ``0.106.0``）→ 只比较 ``x.y.z``，与 0.107.0 之前的行为一致：
      ``0.106.0-beta.3`` 宿主满足 ``0.106.0``（同一候选版本线），避免已发布插件在 beta 宿主上被误拒。
    - 声明**带** prerelease（如 ``0.106.0-beta.12``）→ 完整 SemVer 比较：``0.106.0-beta.3`` 不满足，
      ``0.106.0-beta.12``、``0.106.0``、``0.107.0-beta.1`` 都满足。
    """

    required = parse_version(min_version)
    if required is None:
        return True
    host = parse_version(host_version)
    if host is None:
        return True
    if not required.is_prerelease:
        return host.core >= required.core
    return host >= required


# ─────────────────────────────────────────────────────
# Plugin API 版本范围
# ─────────────────────────────────────────────────────
_COMPARATOR_RE = re.compile(r"^(?P<op>>=|<=|>|<|==|=|\^|~)?\s*(?P<ver>[0-9xX*][0-9A-Za-z.*-]*)$")


class PluginApiRangeError(ValueError):
    """``plugin_api`` 范围写法无法识别。"""


def _api_core(raw: str) -> tuple[tuple[int, int], int]:
    """解析 ``1`` / ``1.2`` / ``1.x``，返回 ``((major, minor), 精度段数)``。"""

    parts = [part for part in raw.split(".") if part]
    if not parts or len(parts) > 3:
        raise PluginApiRangeError(raw)
    numbers: list[int] = []
    for part in parts[:2]:
        if part in {"x", "X", "*"}:
            break
        if not part.isdigit():
            raise PluginApiRangeError(raw)
        numbers.append(int(part))
    if not numbers:
        return (0, 0), 0
    major = numbers[0]
    minor = numbers[1] if len(numbers) > 1 else 0
    return (major, minor), len(numbers)


def _expand_comparator(token: str) -> list[tuple[str, tuple[int, int]]]:
    match = _COMPARATOR_RE.match(token.strip())
    if not match:
        raise PluginApiRangeError(token)
    op = match.group("op") or ""
    (major, minor), precision = _api_core(match.group("ver"))
    if precision == 0:  # "*" / "x"
        return []
    if op in {"", "=", "=="}:
        if precision == 1:  # "1" / "1.x" → >=1.0 <2.0
            return [(">=", (major, 0)), ("<", (major + 1, 0))]
        # 裸 "1.2" 视为 ^1.2：需要 1.2 起的能力，且不能跨 MAJOR
        return [(">=", (major, minor)), ("<", (major + 1, 0))]
    if op == "^":
        return [(">=", (major, minor)), ("<", (major + 1, 0))]
    if op == "~":
        if precision == 1:
            return [(">=", (major, 0)), ("<", (major + 1, 0))]
        return [(">=", (major, minor)), ("<", (major, minor + 1))]
    return [(op, (major, minor))]


def parse_plugin_api_range(spec: str) -> list[tuple[str, tuple[int, int]]]:
    """把 ``plugin_api`` 范围解析成比较条件列表；空列表表示任意版本。

    支持：``"1"``、``"1.x"``、``"1.2"``（= ``^1.2``）、``"^1.2"``、``"~1.2"``、``">=1.1"``、
    ``">=1.0,<2"``（逗号或空格分隔取交集）、``"*"``。
    """

    text = str(spec or "").strip()
    if not text:
        return []
    tokens = [token for token in re.split(r"[,\s]+", text) if token]
    # 允许 ">= 1.0" 这种运算符与版本之间带空格的写法
    merged: list[str] = []
    for token in tokens:
        if merged and re.fullmatch(r">=|<=|>|<|==|=|\^|~", merged[-1]):
            merged[-1] = merged[-1] + token
        else:
            merged.append(token)
    conditions: list[tuple[str, tuple[int, int]]] = []
    for token in merged:
        conditions.extend(_expand_comparator(token))
    return conditions


def plugin_api_satisfies(spec: str | None, api_version: str = PLUGIN_API_VERSION) -> bool:
    """宿主 Plugin API 版本是否落在插件声明的范围里。范围写法错误时抛 :class:`PluginApiRangeError`。"""

    conditions = parse_plugin_api_range(spec or "")
    (major, minor), precision = _api_core(str(api_version))
    if precision == 0:
        raise PluginApiRangeError(str(api_version))
    current = (major, minor)
    for op, target in conditions:
        if op == ">=" and not current >= target:
            return False
        if op == ">" and not current > target:
            return False
        if op == "<=" and not current <= target:
            return False
        if op == "<" and not current < target:
            return False
    return True


__all__ = [
    "PLUGIN_API_VERSION",
    "PluginApiRangeError",
    "Version",
    "compare_versions",
    "host_satisfies_min_version",
    "parse_plugin_api_range",
    "parse_version",
    "plugin_api_satisfies",
]
