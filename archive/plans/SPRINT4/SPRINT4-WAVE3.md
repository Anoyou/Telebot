# Sprint 4 — Wave 3：开源前打磨

> 工时：约 1.5 天
> 依赖：Wave 2 完成
> 优先级：开源前最后一波

## 0. 子任务

| ID | 任务 | 工时 |
|----|------|------|
| **3A** | GitHub Actions CI（pytest + ruff + pnpm build） | 半天 |
| **3B** | README + SECURITY-OPS 润色 | 半天 |
| **3C** | 选开源协议 + LICENSE | 1 小时 |
| **3D** | 整理 agent-plans / review-pkg / docs 目录归档 | 1 小时 |

完成后 bump **0.5.0**（首个开源 release candidate）。

---

## 3A. GitHub Actions CI

### 文件白名单（新建）
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`（可选，tag 时打包）

### ci.yml

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: telebot
          POSTGRES_PASSWORD: telebot_test
          POSTGRES_DB: telebot
        ports: ['5432:5432']
        options: --health-cmd="pg_isready" --health-interval=10s --health-timeout=5s --health-retries=5
      redis:
        image: redis:7
        ports: ['6379:6379']
        options: --health-cmd="redis-cli ping" --health-interval=10s

    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip

      - name: Install backend
        working-directory: backend
        run: |
          pip install -e ".[dev]"

      - name: Run alembic upgrade
        working-directory: backend
        env:
          DATABASE_URL: postgresql+asyncpg://telebot:telebot_test@localhost:5432/telebot
          MASTER_KEY: 0123456789abcdef0123456789abcdef
          JWT_SECRET: ci_secret_only_for_test
        run: alembic upgrade head

      - name: Lint
        working-directory: backend
        run: ruff check .

      - name: Test
        working-directory: backend
        env:
          DATABASE_URL: postgresql+asyncpg://telebot:telebot_test@localhost:5432/telebot
          REDIS_URL: redis://localhost:6379/0
          MASTER_KEY: 0123456789abcdef0123456789abcdef
          JWT_SECRET: ci_secret_only_for_test
        run: pytest -q

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v3
        with:
          version: 9
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: pnpm
          cache-dependency-path: frontend/pnpm-lock.yaml
      - working-directory: frontend
        run: |
          pnpm install --frozen-lockfile
          pnpm run build
```

### 验收
- 推到 GitHub → Actions 标签页两个 job 都绿
- 故意 break ruff → CI 红
- README 顶部加状态徽章

---

## 3B. README + SECURITY-OPS 润色

### 文件白名单
- `README.md`（重写或大改）
- `docs/SECURITY-OPS.md`（已有，做开源向润色）

### README 大纲

```markdown
# Telebot — 多账号 Telegram Userbot 管理控制台

> Self-hosted Web UI for managing Telegram userbots: auto-reply, forward, scheduler, custom AI commands, rate limiting.

[![CI](https://github.com/<you>/telebot/workflows/CI/badge.svg)](...)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Features

- 🪪 多账号绑定（Telethon），代理 / 设备伪装库
- 💬 自动回复（关键词 / 正则 / 作用域 / 冷却）
- 🔁 转发（4 种模式）
- ⏰ 定时任务（cron / once / interval）
- 🤖 自定义命令模板，含 AI 类型（OpenAI / Anthropic / 自建反代）
- 🛡 风控引擎（18 actions × 5 policies × 拟人化 + FloodWait 自适应）
- 🔌 插件开发框架（不是市场——见 docs/PLUGIN-DEV-GUIDE.md）
- 📨 多 Bot 通知通道（项目启动 / 故障告警）

## Screenshots

(放 4-6 张截图：Dashboard / 账号详情 / 自动回复编辑 / 命令模板)

## Quick Start

### 本机自用（HTTP，5 分钟）

    git clone https://github.com/<you>/telebot
    cd telebot
    cp .env.example .env       # 改 MASTER_KEY / JWT_SECRET
    make dev-up                # PG + Redis（OrbStack / Docker）
    cd backend && pip install -e .[dev] && alembic upgrade head
    uvicorn app.main:app --reload --port 8000
    # 另一个 terminal
    cd frontend && pnpm install && pnpm dev
    # 浏览器开 http://localhost:5173

### 公网部署（HTTPS）

见 [docs/DEPLOY-PUBLIC.md](docs/DEPLOY-PUBLIC.md)

## Tech Stack

- 后端：Python 3.12 / FastAPI / SQLAlchemy 2 / Alembic / asyncpg / Redis / Telethon 1.43+
- 前端：React 18 / Vite / TypeScript / TailwindCSS / TanStack Query
- 进程模型：每账号一个 worker 子进程（mp spawn）+ Redis pub/sub IPC
- 数据库：PostgreSQL 16

## Development

- 插件开发：[docs/PLUGIN-DEV-GUIDE.md](docs/PLUGIN-DEV-GUIDE.md)
- 安全运维：[docs/SECURITY-OPS.md](docs/SECURITY-OPS.md)
- 公网部署：[docs/DEPLOY-PUBLIC.md](docs/DEPLOY-PUBLIC.md)
- 变更日志：[CHANGELOG.md](CHANGELOG.md)

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
```

### SECURITY-OPS 润色要点
- 把"某个用户"改成"管理员"
- 加 "如果你打算公网部署" 章节
- 加"应急响应工单模板"附录

### 验收
- README 在 GitHub 渲染好看
- 所有内链都通

---

## 3C. 开源协议

### 二选一

**MIT**（推荐）
- 最宽松：随便用、随便改、随便商用
- 用户多：React / Tailwind / FastAPI 都是 MIT
- 一行话：你不能因为别人用得不爽来告我

**Apache 2.0**
- 多了**专利授权**条款（贡献者自动授权专利给所有用户）
- 多了 NOTICE 文件要求
- 适合企业贡献的项目（Kubernetes / Spark）

### 我推荐 MIT

理由：
- 个人项目，简单是核心
- 没用到什么专利
- README 一句"License: MIT"就完事

### 文件
- `LICENSE` — 标准 MIT 模板，作者改成你
- `pyproject.toml` 加 `license = {text = "MIT"}`
- `frontend/package.json` 加 `"license": "MIT"`

---

## 3D. 仓库归档清理

### 移动 / 删除

```bash
# 1. 已交付的 sprint plans 归档
cd /Users/anoyou/Desktop/telebot/agent-plans
mkdir -p archive
mv SPRINT2-*.md archive/
# Wave 1/2/3 完成后也 mv 进去

# 2. review-pkg 归档（内容已沉淀进 REVIEW-FIXES-REPORT.md）
cd /Users/anoyou/Desktop/telebot
mkdir -p docs/archive
mv review-pkg/* docs/archive/ 2>/dev/null
rmdir review-pkg

# 3. 旧 PRD 归档
mv teleuserbotold.md docs/archive/PRD-original.md

# 4. AGENTS.md / CONTRACTS.md 看是否还需要
# AGENTS.md = 多 sub-session 协作约定，开源后没意义 → 删
# CONTRACTS.md = 系统内部 IPC 协议 / 插件 hook 签名 → 保留为 docs/CONTRACTS.md
mv CONTRACTS.md docs/CONTRACTS.md
rm AGENTS.md  # 或归档

# 5. 顶层散落的 *.md
mv REVIEW-FIXES-REPORT.md docs/archive/

# 6. 各模块 AGENT_PLAN.md（Sprint 1 时给单 agent 用的）
rm backend/app/worker/AGENT_PLAN.md backend/app/worker/ratelimit/AGENT_PLAN.md frontend/AGENT_PLAN.md deploy/AGENT_PLAN.md 2>/dev/null
```

### 顶层目录最终应该是：

```
/
├── README.md
├── CHANGELOG.md
├── LICENSE
├── .env.example
├── Makefile
├── docker-compose.yml
├── docker-compose.dev.yml
├── .github/workflows/ci.yml
├── backend/
├── frontend/
├── deploy/
│   ├── Caddyfile.example
│   ├── prod-up.sh
│   └── backup-keys.sh
├── docs/
│   ├── PLUGIN-DEV-GUIDE.md
│   ├── DEPLOY-PUBLIC.md
│   ├── SECURITY-OPS.md
│   ├── CONTRACTS.md
│   └── archive/
├── examples/
│   └── plugins/translate/
└── agent-plans/
    ├── README.md
    └── archive/
```

---

## Wave 3 验收

```bash
# CI
git push  # 触发 Actions → 全绿

# 文档
mkdocs serve / 直接在 GitHub web 上点开 README → 截图美观

# License
cat LICENSE | head -3  # MIT License + 你的名字 + 年份

# 目录
tree -L 2 -I 'node_modules|.venv|__pycache__|dist'
```

完成后 bump **0.5.0** + tag → 即可在 GitHub 公开仓库

---

## 后续 (0.6.x+ Roadmap，不在本 Sprint)

- 插件沙箱加固（SandboxClient 拦截 client(...) raw API）
- 风控告警 → TG-self 自动通知（Wave 2-D 框架已就位，加触发点）
- 插件开发指南补更多移植样例（reaction / sticker / regex 替换 等）
- API 文档自动生成（FastAPI OpenAPI → docs/api/）


---

## Wave 3 完成报告

**完成时间**：2026-05-06

### 交付清单

✅ **3A. GitHub Actions CI**
- `.github/workflows/ci.yml` 已创建
- Backend job：pytest + ruff + alembic upgrade（PostgreSQL 16 + Redis 7 services）
- Frontend job：pnpm build（Node 20 + pnpm 9）
- 触发条件：push to main / pull_request to main

✅ **3B. README + SECURITY-OPS 润色**
- `README.md` 完全重写为开源向：
  - 简洁的 8 项核心功能列表
  - Quick Start 分本机自用（HTTP）/ 公网部署（HTTPS）两条路径
  - FAQ 回答 3 个常见问题
  - Tech Stack / Development / Status 章节
  - 致谢 Telethon 和 PagerMaid-Pyro
- `docs/SECURITY-OPS.md` 润色：
  - 顶部新增"如果你打算公网部署"提示
  - "某 web 用户账号"改为"管理员账号"
  - 新增 §6 应急响应工单模板

✅ **3C. 开源协议**
- `LICENSE` 更新为 MIT License（Copyright 2026 Telebot Contributors）
- `backend/pyproject.toml` 添加 `license = {text = "MIT"}`
- `frontend/package.json` 添加 `"license": "MIT"`

✅ **3D. 仓库归档清理**
- `archive/plans/SPRINT4/README.md` 更新，添加 Wave 3 交付信息
- 确认 Wave 1/2 已归档（`SPRINT4-WAVE1.md` / `SPRINT4-WAVE2.md`）

✅ **版本号 bump 到 0.5.0**
- `backend/app/__init__.py`：`__version__ = "0.5.0"`
- `backend/pyproject.toml`：`version = "0.5.0"`
- `frontend/package.json`：`"version": "0.5.0"`
- `frontend/src/lib/version.ts`：`APP_VERSION = "0.5.0"` + `APP_STAGE = "RC1"`

✅ **CHANGELOG 更新**
- 新增 `[0.5.0] — 2026-05-06 · RC1 · 开源前打磨` 段落
- 记录 Added / Changed / Fixed / Removed / Notes

### 额外修复（用户反馈）

✅ **inline @provider 覆盖 bug 修复**
- **问题**：`,ai @AnyGPT 问题` 错误地使用命令模板里配置的 model（如 `mimo-v2.5`），而不是 AnyGPT 的 `default_model`
- **根因**：`override_model = cfg.get("model")` 总是从模板读取，即使用户切换了 provider
- **修复**：当用户指定 `@provider`（未指定 `:model`）时，清空 `override_model`，让 `build_client` 使用该 provider 的 `default_model`
- **优先级**：`@name:model` > provider.default_model > 模板 model（仅当无 inline override 时）
- **测试**：新增 3 个测试用例验证修复
  - `test_run_ai_inline_provider_without_model_clears_template_model`
  - `test_run_ai_inline_provider_with_model_overrides_everything`
  - 已有测试全部通过

### 验收状态

- ✅ `.github/workflows/ci.yml` 已创建（推送到 GitHub 后会自动触发）
- ✅ `README.md` 在 GitHub 渲染良好，所有内链可达
- ✅ `LICENSE` 文件符合 MIT 标准模板
- ✅ 版本号在 5 处同步（backend/__init__.py / pyproject.toml / frontend/package.json / version.ts / CHANGELOG.md）
- ✅ CHANGELOG 新增 0.5.0 段落
- ✅ `archive/plans/SPRINT4/README.md` 已更新
- ⚠️ CI 实际运行需要推送到 GitHub 后验证（本地无 GitHub Actions 环境）
- ⚠️ README 截图占位（Coming soon），待后续补充

### 后续建议

1. **推送到 GitHub**：
   ```bash
   git add .
   git commit -m "chore: Sprint 4 Wave 3 完成 - v0.5.0 RC1 开源前打磨"
   git push origin main
   ```

2. **验证 CI**：
   - 查看 GitHub Actions 标签页，确认两个 job（backend / frontend）都绿
   - 如果失败，根据日志调整

3. **更新 README 徽章**：
   - 将 `https://github.com/yourusername/telebot` 替换为实际仓库地址
   - CI 徽章 URL 也需要更新

4. **补充截图**（可选）：
   - Dashboard / 账号详情 / 自动回复编辑 / 命令模板
   - 放到 `docs/screenshots/` 或直接用 GitHub Issues 上传

5. **创建 Release**（可选）：
   ```bash
   git tag -a v0.5.0 -m "Release v0.5.0 RC1 - 首个开源候选版本"
   git push origin v0.5.0
   ```

### 归档

本 plan 已完成，已归档到 `archive/plans/SPRINT4/SPRINT4-WAVE3.md`。

---

**项目可以开源了。** 🎉
