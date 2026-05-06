# Agent F：部署（Docker / docker-compose / nginx / README）

> 你是新会话里的工程师。注释一律中文。

## 目标

把已就绪的后端 + 前端打包成 docker-compose 一键部署：本地 Mac 开发 + VPS 生产。

## 项目根目录

`/Users/anoyou/Desktop/telebot`

## 必读（只读）

1. `/Users/anoyou/Desktop/telebot/teleuserbot.md` §10、§11
2. `/Users/anoyou/Desktop/telebot/CONTRACTS.md`
3. `/Users/anoyou/Desktop/telebot/backend/pyproject.toml`（依赖清单）
4. `/Users/anoyou/Desktop/telebot/.env.example`
5. `/Users/anoyou/Desktop/telebot/docker-compose.dev.yml`（dev 用的 pg/redis，已就绪，**不要修改**）
6. `/Users/anoyou/Desktop/telebot/Makefile`（已有大量目标，可阅读）
7. `/Users/anoyou/Desktop/telebot/frontend/package.json`（如果 E 已写完）

## 你的可写文件白名单

- `backend/Dockerfile`（新建）
- `backend/.dockerignore`（新建）
- `frontend/Dockerfile`（新建：多阶段，build → nginx alpine）
- `frontend/.dockerignore`（新建）
- `frontend/nginx.conf`（新建）
- `docker-compose.yml`（新建：生产用，含全部 5 个服务）
- `deploy/README.md`（新建：部署文档，**中文**）
- `deploy/backup.sh`（新建：占位备份脚本——pg_dump + 加密 session 等）
- `deploy/restore.sh`（新建：占位）
- `README.md`（仓库根，新建：项目总览 + 本地启动说明）
- `Makefile`（已有，可**追加** target，但**不要改**已有 target）

**禁止修改**：`backend/`、`frontend/` 内除 Dockerfile/.dockerignore/nginx.conf 之外的任何文件。

## 实施

### 1. `backend/Dockerfile`

```dockerfile
# 多阶段：builder 装依赖，runtime 瘦身
FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*
COPY pyproject.toml /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e .

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        libffi8 ca-certificates && \
    rm -rf /var/lib/apt/lists/*
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 2. `backend/.dockerignore`

```
__pycache__
*.pyc
.venv
.pytest_cache
.ruff_cache
.alma-snapshots
sessions
*.db
.env
.env.*
```

### 3. `frontend/Dockerfile`

```dockerfile
FROM node:22-alpine AS builder
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine AS runtime
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
```

### 4. `frontend/nginx.conf`

```nginx
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # 静态资源
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 反向代理后端 API
    location /api/ {
        proxy_pass http://web:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    location /healthz {
        proxy_pass http://web:8000/healthz;
    }

    # 超过 50MB 的文件上传放宽（备份恢复用）
    client_max_body_size 50M;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml application/xml+rss text/javascript;
}
```

### 5. `docker-compose.yml`（生产）

```yaml
# 完整生产部署：postgres + redis + web (FastAPI + supervisor) + frontend (nginx)
# 使用：cp .env.example .env && 编辑 .env && docker compose up -d --build

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-telebot}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-telebot}
      POSTGRES_DB: ${POSTGRES_DB:-telebot}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER:-telebot} -d $${POSTGRES_DB:-telebot}"]
      interval: 10s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 10

  web:
    build:
      context: ./backend
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-telebot}:${POSTGRES_PASSWORD:-telebot}@postgres:5432/${POSTGRES_DB:-telebot}
      REDIS_URL: redis://redis:6379/0
      WEB_HOST: 0.0.0.0
      WEB_PORT: "8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - sessions:/app/sessions
    # web 进程内含 supervisor 自动拉起每账号 worker 子进程；不需要单独 worker 容器
    # 内存：每 worker ~80MB；按账号数预估
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()"]
      interval: 15s
      timeout: 5s
      retries: 5
    command: ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
    # ⚠ 必须 --workers 1：supervisor 在主进程内管子进程，多 worker 会重复拉起 worker 子进程

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    restart: unless-stopped
    depends_on:
      - web
    ports:
      - "${WEB_PORT_PUBLISH:-80}:80"

volumes:
  pgdata:
  redisdata:
  sessions:
```

### 6. `deploy/README.md`（部署文档）

写一份详细中文部署指南，章节：
- **本地开发**（Mac）：
  1. `brew install python@3.12`
  2. `cd backend && python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
  3. `make dev-up`（拉起 pg + redis）
  4. `cp .env.example .env && 修改 MASTER_KEY 和 JWT_SECRET`（命令也要给出）
     ```bash
     python3 -c "from cryptography.fernet import Fernet; print('MASTER_KEY=' + Fernet.generate_key().decode())"
     python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(64))"
     ```
  5. `cd backend && alembic upgrade head`
  6. `make backend` + 另开终端 `make frontend`
  7. 浏览器 http://localhost:5173

- **VPS 生产部署**：
  1. 装 Docker + Docker Compose（给 Ubuntu / Debian / CentOS 一键命令）
  2. `git clone <repo> /opt/telebot && cd /opt/telebot`
  3. `cp .env.example .env && 编辑`（强调 MASTER_KEY 必须备份）
  4. `docker compose up -d --build`
  5. 反代 / HTTPS（推荐 Caddy 或 nginx + certbot；给一个 Caddyfile 例子）：
     ```Caddyfile
     userbot.example.com {
         reverse_proxy localhost:80
     }
     ```
  6. 备份：`./deploy/backup.sh`（cron 每天 03:00 跑）

- **常见故障排查**：
  - worker 起不来 → `docker compose logs web` + 看 runtime_log 表
  - session 失效 → 重新登录绑定（错误显示 login_required）
  - FloodWait 频繁 → 调大动作阈值或开启冷启动模式

### 7. `deploy/backup.sh`

```bash
#!/usr/bin/env bash
# 每日备份：pg_dump + 拷贝 sessions volume + 加密打包
set -e
TS=$(date +%Y%m%d-%H%M)
DIR=${BACKUP_DIR:-/var/backups/telebot}
mkdir -p "$DIR"
docker compose exec -T postgres pg_dump -U telebot -d telebot --no-owner > "$DIR/db-$TS.sql"
docker run --rm -v telebot_sessions:/sessions -v "$DIR":/backup alpine \
    tar czf "/backup/sessions-$TS.tgz" -C / sessions
echo "备份完成: $DIR/db-$TS.sql / $DIR/sessions-$TS.tgz"
echo "⚠ 注意：还原前必须确保 .env 中 MASTER_KEY 和原备份时一致，否则 session 解密失败！"
```

### 8. `deploy/restore.sh`

类似的 restore 占位脚本。

### 9. `README.md`（仓库根）

简短中文项目总览：
- 一句话定位
- 核心特性
- 快速开始（指向 `deploy/README.md`）
- 文档：`teleuserbot.md`（PRD）、`CONTRACTS.md`（契约）、`AGENTS.md`（多 Agent 分工）

### 10. `Makefile` 追加 target

不要改已有 target。**追加**：
```makefile
prod-build:
	docker compose build

prod-up:
	docker compose up -d --build

prod-down:
	docker compose down

backup:
	./deploy/backup.sh
```

如果上面 target 已存在则跳过。

## 自检

```bash
cd /Users/anoyou/Desktop/telebot
docker compose -f docker-compose.yml config   # 不报错就 OK
shellcheck deploy/*.sh                        # 如果装了 shellcheck
```

## 完成报告

≤300 字总结：建立的文件清单、关键决策（如：web 容器内合并 supervisor + uvicorn）、TODO（如：HTTPS 自动化）。
