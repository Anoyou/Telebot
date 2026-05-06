# Telegram Userbot 管理系统 — 部署指南

> 本文档面向部署人员，说明本地（Mac）开发环境与 VPS 生产环境的搭建步骤。
> 全部配置/命令均假设当前目录为仓库根目录 `telebot/`。

---

## 一、本地开发（Mac）

适用场景：在本机起 Postgres/Redis 容器，后端用 venv、前端用 pnpm 直接跑（带热重载）。

### 1. 安装基础工具

```bash
# Python 3.12（项目要求 >=3.12）
brew install python@3.12

# Node 20+ 与 pnpm（推荐通过 corepack）
brew install node
corepack enable

# Docker Desktop（本地依赖容器）
brew install --cask docker
```

### 2. 创建后端虚拟环境

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
cd ..
```

### 3. 启动本地依赖（PostgreSQL + Redis）

```bash
make dev-up           # 等价于 docker compose -f docker-compose.dev.yml up -d
make dev-logs         # 查看日志（可选）
```

### 4. 准备 `.env`

```bash
cp .env.example .env
```

然后用下列命令分别生成 **MASTER_KEY**（Fernet 主密钥）与 **JWT_SECRET**：

```bash
python3 -c "from cryptography.fernet import Fernet; print('MASTER_KEY=' + Fernet.generate_key().decode())"
python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(64))"
```

把输出写入 `.env`。**MASTER_KEY 一定要离线备份**，丢失则所有 TG 账号 session 全部失效，需要重新登录绑定。

### 5. 执行数据库迁移

```bash
make migrate          # 等价于 cd backend && alembic upgrade head
```

### 6. 启动后端 + 前端

终端 A：

```bash
make backend          # uvicorn --reload，监听 http://localhost:8000
```

终端 B：

```bash
make frontend         # vite dev server，监听 http://localhost:5173
```

浏览器访问 <http://localhost:5173> 即可。前端 dev 默认通过 vite proxy 转发到后端 8000。

### 7. 常用辅助命令

```bash
make test             # 跑后端单测
make lint             # ruff 检查
make codegen          # 从 OpenAPI 生成前端 TS 类型
make dev-down         # 关停本地依赖
```

---

## 二、VPS 生产部署

适用场景：单台 Linux VPS（建议 ≥2 vCPU / 4GB 内存）一键 docker-compose 部署。

### 1. 安装 Docker + Docker Compose

**Ubuntu / Debian**：

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # 让当前用户免 sudo 调 docker；需重新登录
sudo systemctl enable --now docker
```

**CentOS / RHEL / Rocky**：

```bash
sudo yum install -y yum-utils
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

验证：

```bash
docker --version
docker compose version
```

### 2. 拉取代码

```bash
sudo mkdir -p /opt/telebot && sudo chown "$USER":"$USER" /opt/telebot
git clone <你的仓库地址> /opt/telebot
cd /opt/telebot
```

### 3. 配置 `.env`

```bash
cp .env.example .env
python3 -c "from cryptography.fernet import Fernet; print('MASTER_KEY=' + Fernet.generate_key().decode())"
python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(64))"
```

把生成的 `MASTER_KEY` / `JWT_SECRET` 写入 `.env`，并按需修改：

| 关键变量 | 说明 |
|---|---|
| `MASTER_KEY` | **必须离线备份**，丢失等于全量重新登录 |
| `JWT_SECRET` | Web 登录 JWT 签名密钥 |
| `POSTGRES_USER/PASSWORD/DB` | 数据库账号（默认 `telebot/telebot/telebot`，强烈建议改） |
| `WEB_PORT_PUBLISH` | 对外发布的 HTTP 端口（默认 80） |
| `CORS_ORIGINS` | 前端域名，多个用逗号分隔 |
| `KILL_SWITCH` | 紧急总闸，true 时所有账号停止主动动作 |

### 4. 一键启动

```bash
docker compose up -d --build
docker compose ps                 # 查看状态
docker compose logs -f web        # 查看后端日志
```

首次启动 web 容器会自动跑 `alembic upgrade head` 建表。

### 5. 反向代理 + HTTPS

推荐 **Caddy**（自动签证书，最省事）：

`/etc/caddy/Caddyfile`：

```Caddyfile
userbot.example.com {
    reverse_proxy localhost:80
}
```

```bash
sudo systemctl reload caddy
```

或者使用 **nginx + certbot**：

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d userbot.example.com
```

`/etc/nginx/sites-available/userbot.conf`（关键片段）：

```nginx
server {
    listen 443 ssl http2;
    server_name userbot.example.com;
    # certbot 自动注入 ssl_certificate / ssl_certificate_key

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:80;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 6. 备份

```bash
chmod +x deploy/backup.sh
./deploy/backup.sh                # 手动备份一次

# 加入 crontab，每天 03:00 自动备份
crontab -e
# 0 3 * * * cd /opt/telebot && ./deploy/backup.sh >> /var/log/telebot-backup.log 2>&1
```

备份产物默认放 `/var/backups/telebot/`，包含：

- `db-<时间戳>.sql` —— PostgreSQL 全量 dump
- `sessions-<时间戳>.tgz` —— 加密的 session 卷打包

> **重要**：恢复时 `.env` 中的 `MASTER_KEY` 必须与备份时一致，否则 session 解密失败。
> 强烈建议把 `MASTER_KEY` + 最新备份分别异地存放。

### 7. 升级 / 回滚

```bash
git pull
docker compose pull               # 第三方镜像（pg/redis/nginx）拉新版
docker compose up -d --build      # 重建并滚动重启
```

回滚：`git checkout <旧 tag/commit> && docker compose up -d --build`。

---

## 三、常见故障排查

### 1. worker 起不来

- 看后端日志：`docker compose logs -f web`
- 进容器看 runtime_log 表：

  ```bash
  docker compose exec postgres psql -U telebot -d telebot \
      -c "SELECT ts, level, source, message FROM runtime_log ORDER BY ts DESC LIMIT 50;"
  ```

- 常见原因：账号 session 失效（status=`login_required`）、API ID/Hash 错误、代理不通。

### 2. session 失效（账号变 `login_required`）

- Web 端进入账号详情，重新走绑定向导即可。
- 如果是大批量失效，先确认 `MASTER_KEY` 是否被改动；若改动了，所有 session 都无法解密。

### 3. FloodWait 频繁

- 进入「账号详情 → 风控与限流」：
  - 调高拟人化抖动比例
  - 调低对应动作的阈值（per_minute / per_hour）
  - 启用「冷启动渐进」给新号留缓冲
- 也可以在「系统设置 → 全局总闸」临时停用所有主动动作。

### 4. 容器健康检查反复重启

- `docker compose ps` 看 unhealthy 的服务
- web 健康检查依赖 `/healthz`，若 alembic 迁移失败导致 uvicorn 没起来就会一直 unhealthy
- 检查迁移日志：`docker compose logs web | grep -i "alembic\|error"`

### 5. 前端 502 / 静态资源 404

- 确认 web 容器健康：`docker compose ps`
- 确认 `frontend` 容器内 nginx 配置正确：`docker compose exec frontend nginx -T`
- 浏览器强刷（带缓存清理）

---

## 四、参考文档

- 产品需求：`teleuserbot.md`
- 接口/契约：`CONTRACTS.md`
- 多 Agent 分工：`AGENTS.md`
