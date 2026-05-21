# 公网部署指南（Docker Compose + Caddy）

本指南用于把 TelePilot Web / PWA 控制台安全暴露到公网。当前推荐路径是：生产栈统一由 Docker Compose 启动，`frontend` 容器用 nginx 托管前端并反代后端，公网 HTTPS 由 Caddy 负责。

仓库里部分默认卷名、数据库名和环境标记仍保留 `telebot` 兼容命名，不影响对外产品名 `TelePilot`。

## 1. 推荐拓扑

- 公网入口：`https://telepilot.example.com`
- Caddy：监听服务器 `80/443`，自动申请 TLS
- TelePilot frontend 容器：只发布到本机 `127.0.0.1:8080`
- TelePilot web 容器：仅在 Docker 网络内提供 `web:8000`
- PostgreSQL / Redis / sessions / 远程模块目录：Docker volume 持久化

## 2. 一条命令安装

SSH 到 Debian / Ubuntu 服务器后，先用开箱部署脚本安装并启动生产栈：

```bash
curl -fsSL https://raw.githubusercontent.com/Anoyou/telebot/main/scripts/install-server.sh \
  | env WEB_PORT_PUBLISH=127.0.0.1:8080 COOKIE_SECURE=true bash
```

这条命令会安装基础依赖与 Docker Compose v2、拉取仓库到 `/opt/telepilot`、生成生产 `.env`，并执行 `make prod-up` 启动 `postgres` / `redis` / `web` / `frontend`。如果 `WEB_PORT_PUBLISH` 指定的端口已被占用，脚本会保留 host 绑定并自动递增到可用端口，例如从 `127.0.0.1:8080` 改到 `127.0.0.1:8081`。

如果已经克隆仓库，也可以在仓库目录内手动配置：

```bash
cp .env.example .env
# 修改 MASTER_KEY / JWT_SECRET / POSTGRES_PASSWORD / COOKIE_SECURE / WEB_PORT_PUBLISH
make prod-up
```

公网 HTTPS 场景建议在 `.env` 中确认：

```dotenv
COOKIE_SECURE=true
TRUST_FORWARDED_FOR=true
CORS_ORIGINS=https://telepilot.example.com
WEB_PORT_PUBLISH=127.0.0.1:8080
```

`WEB_PORT_PUBLISH=127.0.0.1:8080` 可以避免 nginx 前端容器直接裸露到公网，只让 Caddy 作为唯一外部入口。

## 3. Caddy 配置

安装 Caddy：

```bash
sudo apt update
sudo apt install -y caddy
```

写入 `/etc/caddy/Caddyfile`：

```Caddyfile
telepilot.example.com {
    encode gzip zstd

    reverse_proxy 127.0.0.1:8080 {
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "same-origin"
    }
}
```

启动或重载：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

## 4. 升级与回滚

升级：

```bash
cd /opt/telepilot
make prod-update
```

`make prod-update` 会先检查远程变更，再按文件范围选择增量动作：仅后端变更时只重建
`web`，仅前端变更时只重建 `frontend`，纯文档变更不重启服务；如果涉及 Dockerfile、
Compose、依赖锁文件或部署脚本，会自动回退到完整 `make prod-up`。更新前如果工作区
存在未提交改动会拒绝执行，避免覆盖服务器上的本地修改。

想先看本次会走哪条路径，可以执行：

```bash
cd /opt/telepilot
make prod-update PROD_UPDATE_ARGS=--dry-run
```

回滚到指定版本：

```bash
cd /opt/telepilot
git checkout <tag-or-commit>
make prod-up
```

`make prod-up` 会重新构建镜像、启动容器，并在 `web` 容器启动时执行 `alembic upgrade head`。

## 5. 备份

至少备份三类数据：

- PostgreSQL 数据库
- `.env`，尤其 `MASTER_KEY`
- Docker volumes：`sessions`、`plugins_installed`、`plugin_repos`

仓库已有脚本可参考：

- [deploy/backup.sh](../deploy/backup.sh)
- [deploy/backup-keys.sh](../deploy/backup-keys.sh)
- [deploy/restore.sh](../deploy/restore.sh)

`MASTER_KEY` 必须和数据库备份分开保存。丢失 `MASTER_KEY` 后，已有 Telegram session、api_id、api_hash、TOTP secret 和 Bot Token 都无法解密。

## 6. 验收清单

1. `docker compose ps` 中 `postgres` / `redis` / `web` / `frontend` 均为 running 或 healthy。
2. `curl -I http://127.0.0.1:8080` 能返回前端响应。
3. `https://telepilot.example.com` 可打开登录页。
4. 浏览器 Cookie 带 `Secure`，确认 `COOKIE_SECURE=true` 生效。
5. 服务器安全组只对公网开放 `80/tcp` 和 `443/tcp`，不要额外开放 `8000`。
6. 登录后概览页资源占用能看到应用进程与服务器资源。

## 7. 常见问题

Q: HTTPS 证书申请失败怎么办？  
A: 检查域名 A 记录是否指向服务器公网 IP，安全组是否放通 80/443，以及是否有其它服务占用这两个端口。

Q: 登录接口被 CORS 拦截怎么办？  
A: 检查 `.env` 中 `CORS_ORIGINS` 是否和实际访问地址完全一致，包括协议、域名和端口。

Q: PWA 安装后无法保持登录怎么办？
A: 公网 HTTPS 部署必须设置 `COOKIE_SECURE=true`，并通过 `https://` 访问。

Q: 远程模块更新后重建容器，模块文件不见了怎么办？
A: 确认 `docker-compose.yml` 里的 `plugins_installed` 和 `plugin_repos` volume 没有被改成容器临时目录。
