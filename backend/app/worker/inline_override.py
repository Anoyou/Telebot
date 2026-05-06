"""``,ai`` 命令的 inline @override 语法解析。

让用户**临时**指定本次调用用哪个 provider / model，**不需要**先建模板。

支持的语法（``args[0]`` 必须以 ``@`` 起头才视为 override；其它情况原样返回）：

- ``@<name>``               强制走 name=匹配项的 provider；模型用其 default_model
- ``@<name>:<model>``        provider + 具体 model（model 必须在该 provider.models[].enabled 中）
- ``@auto``                  本次强制 auto 路由（即使模板配的 fixed）
- ``@list``                  返回"可用 provider 列表"——调用方应 edit 给用户看，不真的调 LLM

匹配规则：
- 大小写不敏感
- 去掉所有非字母数字字符（"Mimo-CN" / "mimo cn" / "mimo_cn" 都算等价）

设计目标：
- **零状态**：不读不写 DB / Redis / 配置；纯参数解析
- **零侵入**：与 fixed/auto 路由模式正交；和反幻觉/视觉/STT 守卫共存
- 失败时调用方可读出可用列表，给一个 friendly 错误

返回值：
- ``(override, remaining_args)``。``override`` 为 None 表示参数没 ``@`` 开头，
  调用方按原 ``args`` 继续；``override`` 非 None 时 ``remaining_args`` 已剥掉 ``@xxx``。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

# 任意非字母数字字符都视为分隔符，方便用户记忆名字
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def _normalize(s: str) -> str:
    """把 provider 名归一为可宽松匹配的形式：小写 + 去掉非字母数字。

    ``Mimo-CN``/``mimo_cn``/``mimo cn`` → 都得到 ``"mimocn"``
    """
    return _NON_ALNUM.sub("", s.lower())


@dataclass(frozen=True)
class InlineOverride:
    """单次调用的 inline 覆盖参数。"""

    kind: Literal["provider", "auto", "list"]
    """三类指令：
    - provider: 用户指定了具体 provider（含可选 model）
    - auto:     强制 auto 路由
    - list:     列出可用——调用方该返一个列表给用户
    """
    provider_id: int | None = None
    """kind=provider 时是匹配到的 provider 的 id；其它情况 None"""
    model: str | None = None
    """kind=provider 时若用户写了 :model 则给具体 model；否则 None（用 default）"""


@dataclass(frozen=True)
class InlineOverrideError(Exception):
    """解析失败时由 ``parse_inline_override`` 抛出。

    `message` 已经是给用户看的人话；调用方直接 ``edit(str(exc))`` 即可。
    """

    message: str

    def __str__(self) -> str:
        return self.message


def parse_inline_override(
    args: list[str],
    providers: dict[int, dict[str, Any]],
    *,
    cmd_prefix: str = ",",
    template_name: str = "ai",
) -> tuple[InlineOverride | None, list[str]]:
    """从 ``args[0]`` 解析 inline override；解析成功剥掉那一项再返回。

    ``providers`` 是 ``CommandContext.providers``：``{id: provider_dict}``，
    用于按 ``name`` 反查 id 与查 ``models[]`` 是否启用了某 model。

    ``cmd_prefix`` / ``template_name``：仅用于"找不到 provider"错误时给出的
    "用法"提示行。其它分支不依赖这两个参数。

    若 ``args[0]`` 不以 ``@`` 开头：返回 ``(None, args)``，调用方按原参数继续。
    """
    if not args:
        return None, args
    head = args[0]
    if not head.startswith("@"):
        return None, args

    raw = head[1:]  # 去掉 @
    rest = list(args[1:])

    # 特殊指令
    if raw.lower() == "list":
        return InlineOverride(kind="list"), rest
    if raw.lower() == "auto":
        return InlineOverride(kind="auto"), rest

    # provider[:model]
    if ":" in raw:
        name_raw, model = raw.split(":", 1)
        model = model.strip()
        if not model:
            raise InlineOverrideError(
                "✗ inline override 语法错：@name:model 中冒号后不能为空。\n"
                "  · 想强制 provider 不指定 model，去掉冒号：@name\n"
                "  · 想指定 model：@name:gpt-5.5"
            )
    else:
        name_raw = raw
        model = None

    # 按 normalize 后的名字匹配 provider；要求唯一命中
    target = _normalize(name_raw)
    if not target:
        raise InlineOverrideError(
            _format_unknown_provider(
                name_raw, providers,
                cmd_prefix=cmd_prefix, template_name=template_name,
            )
        )
    matches: list[tuple[int, dict[str, Any]]] = []
    for pid, p in providers.items():
        nm = str(p.get("name") or "")
        if _normalize(nm) == target:
            matches.append((pid, p))
    if not matches:
        raise InlineOverrideError(
            _format_unknown_provider(
                name_raw, providers,
                cmd_prefix=cmd_prefix, template_name=template_name,
            )
        )
    if len(matches) > 1:
        # 多条 provider 同名（normalize 后）；要求用户用更精确的名字
        names = ", ".join(repr(p["name"]) for _, p in matches)
        raise InlineOverrideError(
            f"✗ @{name_raw} 命中多条 provider（{names}）。请用更精确的名字"
        )
    pid, prov = matches[0]

    # 校验 model（若用户写了的话）
    if model is not None:
        enabled_models = [
            m.get("id")
            for m in (prov.get("models") or [])
            if isinstance(m, dict) and m.get("enabled")
        ]
        if model not in enabled_models:
            # 也允许 default_model 当作隐式启用
            default = prov.get("default_model")
            if default and model == default:
                pass
            else:
                avail = enabled_models or [default] if default else enabled_models
                hint = "（无可用模型）" if not avail else "可用：" + ", ".join(map(str, avail))
                raise InlineOverrideError(
                    f"✗ @{prov.get('name')}:{model} —— 该 model 未启用或不存在。{hint}"
                )

    return InlineOverride(kind="provider", provider_id=int(pid), model=model), rest


def format_provider_list(
    providers: dict[int, dict[str, Any]],
    *,
    cmd_prefix: str = ",",
    template_name: str = "ai",
) -> str:
    """把当前所有 provider 渲染成一段 TG 可读的列表（``@list`` 输出）。

    每条形如：
        · @mimo-cn   OpenAI · mimo-v2.5         vision  ⚠ 未配 key
    用户可以直接把 ``@xxx`` 复制走当 inline override 用。

    ``cmd_prefix`` 与 ``template_name`` 让"用法"那行显示用户实际配置的命令前缀
    （比如 ``。``）和模板名（比如 ``问``），而不是写死的 ``,ai``。
    """
    if not providers:
        return "（尚未配置任何 LLM Provider；先到 AI 设置 → 模型提供商 新建一条）"
    # 按 cost_tier asc → name asc 排
    rows = sorted(
        providers.values(),
        key=lambda p: (int(p.get("cost_tier") or 2), str(p.get("name") or "")),
    )
    lines: list[str] = ["可用 provider（@后接名字，大小写/分隔符不敏感）："]
    for p in rows:
        name = p.get("name") or "(unnamed)"
        kind = p.get("provider") or "?"
        default_model = p.get("default_model") or "?"
        modality = p.get("modality") or "text"
        has_key = bool(p.get("api_key_enc"))
        is_ollama = kind == "ollama"
        marks = []
        if modality and modality != "text":
            marks.append(modality)
        if not has_key and not is_ollama:
            marks.append("⚠未配 key")
        suffix = f"  ({', '.join(marks)})" if marks else ""
        lines.append(f"  · @{name}   {kind} · {default_model}{suffix}")
    lines.append("")
    cmd = f"{cmd_prefix}{template_name}"
    lines.append(
        f"用法：{cmd} @<name> 你的问题   /   {cmd} @<name>:<model> 你的问题   /   {cmd} @auto 你的问题"
    )
    return "\n".join(lines)


def _format_unknown_provider(
    name: str,
    providers: dict[int, dict[str, Any]],
    *,
    cmd_prefix: str = ",",
    template_name: str = "ai",
) -> str:
    """匹配失败时给一段"找不到 + 可用列表"——比单一句"找不到"友好得多。"""
    return f"✗ 未找到 provider @{name}\n\n" + format_provider_list(
        providers, cmd_prefix=cmd_prefix, template_name=template_name
    )


__all__ = [
    "InlineOverride",
    "InlineOverrideError",
    "format_provider_list",
    "parse_inline_override",
]
