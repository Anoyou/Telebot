# Telebot — 多账号 Telegram Userbot 管理控制台

> Self-hosted Web UI for managing Telegram userbots: auto-reply, forward, scheduler, custom AI commands, rate limiting.

[![CI](https://github.com/yourusername/telebot/workflows/CI/badge.svg)](https://github.com/yourusername/telebot/actions)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Features

- 🪪 **多账号绑定**：基于 Telethon，支持代理与设备伪装
- 💬 **自动回复**：关键词 / 正则匹配，支持作用域、冷却时间、白名单
- 🔁 **消息转发**：4 种模式（原生 / 复制 / 引用 / 仅链接）
- ⏰ **定时任务**：cron / once / interval 三种模式，支持多账号广播
- 🤖 **自定义命令模板**：含 AI 类型（OpenAI / Anthropic / 自建反代）
- 🛡 **风控引擎**：18 actions × 5 policies × 拟人化 + FloodWait 自适应
- 🔌 **插件开发框架**：不是市场——见 [docs/PLUGIN-DEV-GUIDE.md](docs/PLUGIN-DEV-GUIDE.md)
- 📨 **多 Bot 通知通道**：项目启动 / 故障告警

## Screenshots

_Coming soon: Dashboard / 账号详情 / 自动回复编辑 / 命令模板_

## Quick Start

### 本机自用（HTTP，5 分钟）

```bash
git clone https://github.com/yourusername/telebot
cd telebot
cp .env.example .env       # 改 MASTER_KEY / JWT_SECRET
make dev-up                # PG + Redis（OrbStack / Docker）
cd backend && pip install -e .[dev] && alembic upgrade head
uvicorn app.main:app --reload --port 8000
# 另一个 terminal
cd frontend && pnpm install && pnpm dev
# 浏览器开 http://localhost:5173
```

### 公网部署（HTTPS）

见 [docs/DEPLOY-PUBLIC.md](docs/DEPLOY-PUBLIC.md)

## Tech Stack

- **后端**：Python 3.12 / FastAPI / SQLAlchemy 2 / Alembic / asyncpg / Redis / Telethon 1.43+
- **前端**：React 18 / Vite / TypeScript / TailwindCSS / TanStack Query
- **进程模型**：每账号一个 worker 子进程（mp spawn）+ Redis pub/sub IPC
- **数据库**：PostgreSQL 16

## Development

- **插件开发**：[docs/PLUGIN-DEV-GUIDE.md](docs/PLUGIN-DEV-GUIDE.md)
- **安全运维**：[docs/SECURITY-OPS.md](docs/SECURITY-OPS.md)
- **公网部署**：[docs/DEPLOY-PUBLIC.md](docs/DEPLOY-PUBLIC.md)
- **变更日志**：[CHANGELOG.md](CHANGELOG.md)

## FAQ

### Q: 这跟 PagerMaid 有什么区别？

- PagerMaid 是 Pyrogram 单进程；本项目是 FastAPI Web UI 多账号管理 + worker 子进程隔离 + 现代 React 前端
- 不兼容 PagerMaid 插件（Pyrogram → Telethon），但提供移植指南（见插件开发指南）

### Q: 多用户支持？

- 单租户设计，一个超管账号；多用户不在路线图

### Q: 为什么不用 Bot API 而用 userbot？

- userbot = 你的个人 Telegram 账号，能进所有群、看所有 DM；Bot 只能进被人邀请的群
- 见 [Telegram 官方文档](https://core.telegram.org/api#telegram-api) 关于 user vs bot

## Status

Alpha / 个人自用 / 欢迎 fork 但暂不接大 PR。

## License

MIT — 见 [LICENSE](LICENSE)

## 致谢

- [Telethon](https://github.com/LonamiWebs/Telethon) - 优秀的 Telegram MTProto 客户端库
- [PagerMaid-Pyro](https://github.com/TeamPGM/PagerMaid-Pyro) - 插件机制和命令体系的设计灵感来源

本项目是完全重新实现的架构，借鉴了 PagerMaid-Pyro 的设计理念但使用不同的技术栈和实现方式。
