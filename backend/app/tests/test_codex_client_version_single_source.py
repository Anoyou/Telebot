"""WP-B18：Codex TUI 客户端版本号必须是单一来源。

背景：`codex_tui` 版本号此前分别写死在三处——
`gateway/internal/routing/table.go`（旧控制面快照缺 `codex_client_version` 的兼容回退）、
`gateway/internal/codex/identity.go`（Codex 身份请求头缺省值）、
`backend/app/services/llm_identity.py`（`codex_tui` 默认版本）。三处各改一处就会漂移：
症状是 Python 直连链路与 Gateway 链路发出不同的 `User-Agent` / `Version` 头，上游可能
按版本策略拒绝或降级，且这种不一致在功能测试里看不出来（请求都成功）。

本轮收敛后的唯一字面量在 `gateway/contract/types.go` 的
`DefaultCodexClientVersion`，`routing` 与 `codex` 两个包都引用它；Python 侧
`llm_identity.py` 的默认值是控制面快照的下发来源，与 Go 常量必须一致。

因此这里做跨语言静态守卫：把 Go 侧的常量值与 Python 默认值直接比对，任一侧改动
而另一侧没跟上立刻失败（CI 的 py-go 之间不共享编译期检查，只能这样守）。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.services import llm_identity

REPO_ROOT = Path(__file__).resolve().parents[3]
GATEWAY_CONTRACT = REPO_ROOT / "gateway" / "contract" / "types.go"

_CONST_PATTERN = re.compile(
    r"const\s+DefaultCodexClientVersion\s*=\s*\"([0-9][0-9A-Za-z.+-]*)\""
)


def _gateway_default_version() -> str:
    source = GATEWAY_CONTRACT.read_text(encoding="utf-8")
    match = _CONST_PATTERN.search(source)
    assert match, f"gateway/contract/types.go 缺少 DefaultCodexClientVersion 常量：{GATEWAY_CONTRACT}"
    return match.group(1)


def test_gateway_and_python_agree_on_codex_tui_default_version() -> None:
    assert (
        _gateway_default_version()
        == llm_identity.default_client_versions()["codex_tui"]
    ), (
        "gateway 兼容回退常量与 Python 控制面默认版本漂移："
        f"gateway={_gateway_default_version()} "
        f"python={llm_identity.default_client_versions()['codex_tui']}"
    )


def test_gateway_packages_reference_the_contract_constant() -> None:
    """Go 侧两个消费点都必须是 `contract.DefaultCodexClientVersion` 的引用。

    如果有人在 package 内重新写死字面量（哪怕值当前一致），下一次 bump 就会漏改，
    这条守卫就是用来挂住这种写法的。
    """
    for relative in (
        "gateway/internal/routing/table.go",
        "gateway/internal/codex/identity.go",
    ):
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert "contract.DefaultCodexClientVersion" in source, (
            f"{relative} 必须引用 contract.DefaultCodexClientVersion，而不是写死版本号"
        )


def test_contract_constant_is_the_only_go_literal() -> None:
    """gateway 内除 contract 外不得出现裸的 0.x 默认版本字面量。"""
    default = _gateway_default_version()
    gateway_root = REPO_ROOT / "gateway"
    offenders: list[str] = []
    for path in sorted(gateway_root.rglob("*.go")):
        if "test" in path.name:  # 测试允许写固定值做对照
            continue
        source = path.read_text(encoding="utf-8")
        if path == GATEWAY_CONTRACT:
            continue
        if f'{default}"' in source and "contract.DefaultCodexClientVersion" not in source:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
    "这些 Go 文件写死了版本字面量，应改为引用 contract 常量：" + repr(offenders)
)
