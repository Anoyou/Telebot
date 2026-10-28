# syntax=docker/dockerfile:1.7
# 后端镜像：多阶段构建
#   - gateway-builder 阶段：在构建机原生架构上交叉编译内置 telepilot-gateway 静态二进制
#   - deps-spec 阶段：从 pyproject.toml 只提取依赖清单（版本号等变化不打穿依赖层）
#   - builder 阶段：安装编译依赖 + 按依赖清单安装 Python 依赖
#   - runtime 阶段：仅保留必要运行库 + 已装好的 site-packages，体积更小
# 暴露端口 8000；健康检查命中 /healthz；启动命令含 alembic 迁移由 docker-compose 注入

# Gateway 是纯 Go（无 cgo、无第三方依赖）：在构建机原生架构上交叉编译出目标架构的静态二进制，
# 多架构镜像不再在 QEMU 里模拟 arm64 编译。go test / go vet 由 CI「Gateway」job 原生执行，镜像里不重复跑。
# 构建提交不再通过 -ldflags 注入（那会让这一层随每次提交失效），改由运行时阶段最后一层的
# ENV TELEPILOT_GATEWAY_BUILD_COMMIT 提供（gateway/internal/version 读取）。
FROM --platform=$BUILDPLATFORM golang:1.23-bookworm AS gateway-builder

ARG TARGETOS
ARG TARGETARCH
WORKDIR /src/gateway
COPY gateway/ ./
RUN CGO_ENABLED=0 GOOS=${TARGETOS:-linux} GOARCH=${TARGETARCH} go build -trimpath \
    -ldflags="-s -w" \
    -o /out/telepilot-gateway ./cmd/telepilot-gateway


# 依赖清单单独成层：只从 pyproject.toml 提取 [project].dependencies 写成 requirements.txt。
# 下游 COPY --from 按文件内容计算缓存，pyproject 其它内容（说明、工具配置等）变化而依赖不变时，
# 依赖安装层继续命中缓存。这一阶段与目标架构无关，在构建机原生架构上运行。
FROM --platform=$BUILDPLATFORM python:3.12-slim AS deps-spec

WORKDIR /spec
COPY backend/pyproject.toml ./
RUN python -c 'import tomllib; from pathlib import Path; data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8")); print("\n".join(data["project"]["dependencies"]))' \
    > /spec/requirements.txt


FROM python:3.12-slim AS builder

WORKDIR /app

# 安装编译期依赖（cryptg/cffi 需要 gcc/libffi-dev）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip pip install --upgrade pip

# 再拷贝依赖清单并安装 runtime 依赖。不要在这一层拷贝 app/ 或 pyproject.toml，否则任意后端
# 代码或版本号变更都会打穿依赖安装缓存，生产增量更新会被迫重装 Telethon / cryptg 等包
# （arm64 在 QEMU 里重装约 5 分钟）。
COPY --from=deps-spec /spec/requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r /tmp/requirements.txt

# 业务源码由 runtime 阶段直接复制到 /app。应用没有 console entry point，
# 无需再构建一份重复 wheel 到 site-packages；后端代码变化因此不会打穿依赖层。


FROM python:3.12-slim AS runtime

WORKDIR /app

# runtime 需要 libffi/CA；远程模块库浏览和安装依赖 git，SSH 仓库依赖 openssh-client
RUN apt-get update && apt-get install -y --no-install-recommends \
        libffi8 ca-certificates git openssh-client && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /usr/local/lib/python3.12/site-packages/pip \
           /usr/local/lib/python3.12/site-packages/pip-*.dist-info

# 从 builder 拷贝已安装的 Python 包与可执行入口（uvicorn/alembic 等都在这里）
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
RUN python -c "import importlib.metadata as m, pathlib, pip; d=list(pathlib.Path('/usr/local/lib/python3.12/site-packages').glob('pip-*.dist-info')); assert len(d) == 1, d; assert m.version('pip') == pip.__version__, (m.version('pip'), pip.__version__)"
COPY --from=gateway-builder /out/telepilot-gateway /usr/local/bin/telepilot-gateway
COPY gateway/LICENSE gateway/THIRD_PARTY_NOTICES.md /usr/share/licenses/telepilot-gateway/

# 拷贝完整后端源码（含 alembic 迁移脚本、app/ 等）及 Agent 读取的发布日志。
COPY backend/ /app
COPY CHANGELOG.md /app/CHANGELOG.md
# System Agent 只读源码诊断使用。仅复制前端源码与构建配置，不复制依赖和产物。
COPY frontend/src /app/source/frontend/src
COPY frontend/package.json frontend/tsconfig.json frontend/tsconfig.app.json frontend/vite.config.ts /app/source/frontend/

# 关闭 pyc 写盘 + 立即刷新 stdout，便于 docker logs 实时查看
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# 健康检查：访问 /healthz；连续失败 5 次则视为不健康
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()" || exit 1

# 构建提交放在最后：每次提交都会变化，放在前面会让之后所有层（含 Gateway 编译）缓存失效。
ARG BUILD_COMMIT=dev
ENV TELEPILOT_GATEWAY_BUILD_COMMIT=${BUILD_COMMIT}

# 默认启动 uvicorn；docker-compose 中会用 sh -c 包一层先跑 alembic upgrade head
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
