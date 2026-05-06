# 公网部署指南（Caddy + HTTPS）

本指南用于把 Telebot Web 面板安全暴露到公网，反向代理使用 Caddy，自动签发 HTTPS 证书。

## 目录

1. [目标拓扑](#1-目标拓扑)
2. [部署前提](#2-部署前提)
3. [环境变量强制项](#3-环境变量强制项)
4. [安装与配置 Caddy](#4-安装与配置-caddy)
5. [启动后端与前端](#5-启动后端与前端)
6. [systemd 守护（可选）](#6-systemd-守护可选)
7. [自动备份（建议）](#7-自动备份建议)
8. [应急响应手册](#8-应急响应手册)
9. [运行监控建议](#9-运行监控建议)
10. [验收清单](#10-验收清单)
11. [常见问题](#11-常见问题)

## 1. 目标拓扑

- 公网入口：`https://telebot.example.com`
- Caddy：监听 `80/443`，自动申请 TLS
- 后端 FastAPI：仅监听 `127.0.0.1:8000`
- 前端静态资源：`frontend/dist`
- 数据库/Redis：仅内网或本机访问

## 2. 部署前提

- 已准备一个域名，例如 `telebot.example.com`
- 域名 A 记录已指向服务器公网 IP
- 服务器安全组/防火墙已放通 `80/tcp`、`443/tcp`
- 本机已安装 Docker（用于 PostgreSQL/Redis）
- 本机已安装 Node.js + `pnpm`
- 本机可运行 Python 后端

建议机器：

- 2 vCPU / 4 GB RAM 起步
- 磁盘建议 40 GB 以上

## 3. 环境变量强制项

复制模板：

```bash
cd /opt/telebot
cp backend/.env.example .env
chmod 600 .env
```

公网部署必须确认以下字段：

```dotenv
# 公网部署必填
COOKIE_SECURE=true
TRUST_FORWARDED_FOR=true
CORS_ORIGINS=https://telebot.example.com
JWT_SECRET=<32位以上强随机>
MASTER_KEY=<Fernet.generate_key()生成值>
POSTGRES_PASSWORD=<32位强随机>
```

生成密钥示例：

```bash
python3 -c "from cryptography.fernet import Fernet; print('MASTER_KEY=' + Fernet.generate_key().decode())"
python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(64))"
python3 -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(32))"
```

注意：

- `MASTER_KEY` 丢失会导致已加密的 TG 会话无法解密（需要全部重登）。
- `CORS_ORIGINS` 只填真实前端域名，不要写 `*`。
- `TRUST_FORWARDED_FOR=true` 仅在可信反代后使用，本方案即 Caddy 反代场景。

## 4. 安装与配置 Caddy

安装（按系统任选其一）：

```bash
# macOS
brew install caddy

# Ubuntu/Debian（仓库版本）
sudo apt update && sudo apt install -y caddy
```

准备配置文件：

```bash
cd /opt/telebot
cp deploy/Caddyfile.example /etc/caddy/Caddyfile
```

编辑 `/etc/caddy/Caddyfile`：

- 把 `telebot.example.com` 改成你的真实域名
- 把前端目录改为你机器上的真实绝对路径

校验并启动：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable caddy
sudo systemctl restart caddy
sudo systemctl status caddy --no-pager
```

若无 systemd，也可前台运行：

```bash
sudo caddy run --config /etc/caddy/Caddyfile
```

## 5. 启动后端与前端

### 5.1 启动数据库与 Redis

```bash
cd /opt/telebot
make dev-up
```

### 5.2 启动后端（仅本地监听）

```bash
cd /opt/telebot/backend
source .venv/bin/activate
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

关键点：

- `--host 127.0.0.1` 不能改成 `0.0.0.0`，避免后端裸暴露公网。

### 5.3 构建前端静态文件

```bash
cd /opt/telebot/frontend
pnpm install
pnpm run build
```

构建完成后，确认 Caddy `root` 指向 `frontend/dist`。

## 6. systemd 守护（可选）

可把后端做成服务，例如 `/etc/systemd/system/telebot-backend.service`：

```ini
[Unit]
Description=Telebot Backend
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/telebot/backend
EnvironmentFile=/opt/telebot/.env
ExecStart=/opt/telebot/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

加载并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable telebot-backend
sudo systemctl restart telebot-backend
sudo systemctl status telebot-backend --no-pager
```

## 7. 自动备份（建议）

推荐每天做一次：

- PostgreSQL 逻辑备份：`pg_dump`
- `.env` 中关键密钥分离备份（尤其 `MASTER_KEY`）
- 备份压缩后同步到第二台机器/对象存储

仓库已有脚本可参考：

- [deploy/backup.sh](/Users/anoyou/Desktop/telebot/deploy/backup.sh)
- [deploy/backup-keys.sh](/Users/anoyou/Desktop/telebot/deploy/backup-keys.sh)
- [deploy/restore.sh](/Users/anoyou/Desktop/telebot/deploy/restore.sh)

## 8. 应急响应手册

### 8.1 怀疑 Web 账号被盗

- 立即在数据库中提升 `web_user.pwd_version`（旧 token 全失效）
- 同时重置该账号密码

### 8.2 需要强制所有 Web 用户下线

- 轮换 `JWT_SECRET`
- 重启后端
- 结果：所有用户 cookie 失效，需要重新登录

### 8.3 `MASTER_KEY` 泄露或误改

- 泄露：视为高危，立即隔离机器并轮换全部密钥
- 误改：会导致历史 `session_enc/api_key_enc` 解密失败
- 恢复：改回原始 `MASTER_KEY`；若原始值丢失，只能逐个账号重登绑定

## 9. 运行监控建议

- 应用侧：
  - 使用 TG-self 通知（多 Bot 场景）
  - 定期执行 `,status` 观察 worker 状态
- 反代侧：
  - 查看 Caddy 日志中异常 `4xx/5xx`
  - 关注短时间高频写请求（POST/PUT/PATCH/DELETE）
- 基础设施：
  - 监控磁盘剩余空间
  - 监控 PostgreSQL 与 Redis 存活

## 10. 验收清单

按顺序执行并确认：

1. 访问 `https://telebot.example.com` 可打开前端页面
2. 前端登录成功，关键 API（如 `/api/health`）可正常响应
3. 浏览器 Cookie 带 `Secure`（`COOKIE_SECURE=true` 生效）
4. 后端仅监听 `127.0.0.1:8000`
5. Caddy 配置校验通过：`caddy validate --config /etc/caddy/Caddyfile`
6. 重启后（Caddy/后端）服务可自动恢复

## 11. 常见问题

Q: HTTPS 证书申请失败怎么办？  
A: 先检查域名解析是否正确、80 端口是否开放、是否有其他进程占用 80/443。

Q: 登录接口被 CORS 拦截怎么办？  
A: 检查 `CORS_ORIGINS` 是否与实际访问域名完全一致（协议、域名、端口都要匹配）。

Q: 为什么本地调试登不上？  
A: 如果是 HTTP 本地环境，请把 `COOKIE_SECURE=false`，否则浏览器不会保存认证 Cookie。
