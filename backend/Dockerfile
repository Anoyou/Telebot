# 后端镜像：多阶段构建
#   - builder 阶段：安装编译依赖 + 通过 pyproject.toml 安装 Python 依赖
#   - runtime 阶段：仅保留必要运行库 + 已装好的 site-packages，体积更小
# 暴露端口 8000；健康检查命中 /healthz；启动命令含 alembic 迁移由 docker-compose 注入

FROM python:3.12-slim AS builder

WORKDIR /app

# 安装编译期依赖（cryptg/cffi 需要 gcc/libffi-dev）
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# 先拷贝依赖清单与源码（pyproject.toml 中 [tool.setuptools] packages = ["app"]，
# 故必须将 app 包目录一并放入构建上下文，pip 才能识别本项目并解析依赖）
COPY pyproject.toml /app/
COPY app /app/app

# 升级 pip 并安装本项目（含全部 runtime 依赖：fastapi/telethon/cryptg/python-socks 等）
# --no-compile：跳过 .pyc 写盘，由 runtime 阶段统一控制（缩小最终镜像 30-60MB）
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --no-compile .


FROM python:3.12-slim AS runtime

WORKDIR /app

# runtime 仅需 cffi/cryptography 用到的 libffi 与 CA 证书
RUN apt-get update && apt-get install -y --no-install-recommends \
        libffi8 ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# 从 builder 拷贝已安装的 Python 包与可执行入口（uvicorn/alembic 等都在这里）
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 拷贝完整源码（含 alembic 迁移脚本、app/ 等）
COPY . /app

# 清理 site-packages 里残留的 __pycache__（builder 阶段 --no-compile 已少建一批，
# 这里再扫一遍兜底；运行时 PYTHONDONTWRITEBYTECODE=1 阻止再次生成）。
RUN find /usr/local/lib/python3.12 -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# 关闭 pyc 写盘 + 立即刷新 stdout，便于 docker logs 实时查看
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# 健康检查：访问 /healthz；连续失败 5 次则视为不健康
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()" || exit 1

# 默认启动 uvicorn；docker-compose 中会用 sh -c 包一层先跑 alembic upgrade head
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
