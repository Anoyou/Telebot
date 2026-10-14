"""WP-B14：gateway/Dockerfile 运行时基础镜像必须带 CA 根证书。

Gateway 用 Go `net/http` 访问 HTTPS 上游，需要系统 CA 根证书完成校验。`FROM scratch`
没有 `/etc/ssl/certs`，同一个二进制在里面请求任何 HTTPS 上游都会以
`x509: certificate signed by unknown authority` 失败。这条静态回归守住 distroless
选择，也顺带锁定"只复制二进制"的最小运行时约束（不引入 shell / 包管理器层）。
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GATEWAY_DOCKERFILE = REPO_ROOT / "gateway" / "Dockerfile"
GATEWAY_UPSTREAM_DOC = REPO_ROOT / "gateway" / "UPSTREAM.md"

def _runtime_stages(source: str) -> list[str]:
    """返回所有非 builder 的运行时阶段指令（多阶段文件的最后一个 FROM 段）。"""
    lines = source.splitlines()
    stages: list[str] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("FROM "):
            if current:
                stages.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        stages.append("\n".join(current))
    return [stage for stage in stages if "AS builder" not in stage.splitlines()[0]]


def test_gateway_runtime_base_image_carries_ca_certificates() -> None:
    source = GATEWAY_DOCKERFILE.read_text(encoding="utf-8")
    stages = _runtime_stages(source)
    assert stages, "gateway/Dockerfile 应该至少有一个运行时阶段"
    for stage in stages:
        base = stage.splitlines()[0]
        assert "FROM scratch" not in base, (
            "运行时阶段不能是 FROM scratch：Gateway 发 HTTPS 上游会因缺少 CA 根证书失败"
        )
        assert "distroless" in base, f"运行时基础镜像应改用 distroless，当前为：{base}"


def test_gateway_runtime_stage_stays_minimal() -> None:
    """仍遵守"只复制二进制"：不装包、不引入 shell。"""
    source = GATEWAY_DOCKERFILE.read_text(encoding="utf-8")
    for stage in _runtime_stages(source):
        assert "apt-get" not in stage
        assert "apk add" not in stage
        assert "yum install" not in stage


def test_gateway_base_image_choice_is_documented() -> None:
    doc = GATEWAY_UPSTREAM_DOC.read_text(encoding="utf-8")
    assert "FROM scratch" in doc
    assert "distroless/static-debian12" in doc
    assert "certificate signed by unknown authority" in doc
