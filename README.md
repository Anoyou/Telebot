<p align="center">
  <img src="./assets/readme/runtime-map.webp" width="100%" alt="TelePilot 运行结构：Web/PWA、FastAPI 控制面、独立账号 Worker 与 Telegram 通道">
</p>

<p align="center">
  <strong>自托管的 Telegram 多账号控制台。</strong><br>
  账号、插件、交互 Bot、AI、资金动作与运维，在一个自己掌控的 Web/PWA 工作台里完成。
</p>

<p align="center">
  <a href="#快速开始"><img src="https://img.shields.io/badge/deploy-self--hosted-f97316?style=flat-square" alt="自托管部署"></a>
  <a href="./backend/pyproject.toml"><img src="https://img.shields.io/badge/Python-3.12-3776ab?style=flat-square" alt="Python 3.12"></a>
  <a href="./frontend/package.json"><img src="https://img.shields.io/badge/PWA-Web%20%2B%20Mobile-171717?style=flat-square" alt="Web 与 PWA"></a>
  <a href="./LICENSE"><img src="https://img.shields.io/github/license/Anoyou/Telebot?style=flat-square&amp;color=171717" alt="MIT License"></a>
</p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="./docs/USAGE-GUIDE.md">使用指南</a> ·
  <a href="./docs/TELEPILOT-ARCHITECTURE.md">架构说明</a> ·
  <a href="./docs/PLUGIN-DEV-GUIDE.md">插件开发</a> ·
  <a href="./CHANGELOG.md">更新日志</a>
</p>

> [!IMPORTANT]
> TelePilot 操作 Telegram **用户账号**，不是普通 Bot API 托管服务。自动回复、群发、频繁操作和第三方插件可能触发账号风控，也可能带来数据与资金风险。请先理解这些边界，并遵守 Telegram 规则和当地法律。

## 为什么是 TelePilot

Telegram 自动化通常散落在脚本、Bot、定时任务和几套互不相通的日志里。TelePilot 把它们收进一个可观察、可回滚的自托管工作台：

- **账号隔离**：每个 Telegram 用户账号由独立 Worker 子进程运行，拥有自己的 session、代理、设备参数和插件配置。
- **统一控制面**：FastAPI 统一处理认证、配置、权限、运行状态、日志、备份和在线更新。
- **插件可复用**：指令、消息模板、AI 指令和插件按账号启用，新账号不必从零配置。
- **数据自己掌握**：业务数据、加密凭据和运行记录留在自己的 PostgreSQL、Redis 与部署目录中。
- **失败可见**：消息流、命中调试、Agent 运行视角和资金台账保留真实链路，不把“命令返回成功”当作业务已经成功。

## 当前界面

插件中心是默认入口。先沉淀可复用的能力，再按账号启用。

<p align="center">
  <img src="./assets/readme/plugin-center.png" width="100%" alt="TelePilot 插件中心，包含首次部署推荐、插件分类和账号插件配置">
</p>

<p align="center">
  <img src="./assets/readme/ai-workspace.png" width="49%" alt="TelePilot AI 中心，集中管理模型、AI 指令和调用记录">
  <img src="./assets/readme/agent-perspective.png" width="49%" alt="TelePilot Agent 运行视角，展示模型、路由、Skill、工具、Token 和阶段耗时">
</p>

<details>
<summary><strong>查看概览、日志与 PWA 移动端</strong></summary>

<br>

<p align="center">
  <img src="./docs/frontend/baseline/screenshots/dashboard-desktop-light.png" width="100%" alt="TelePilot 桌面端概览，显示账号 Worker、AI、插件、日志与资源状态">
</p>

<p align="center">
  <img src="./docs/frontend/baseline/screenshots/logs-desktop-light.png" width="49%" alt="TelePilot 日志中心，按消息流、Agent 运行、系统控制台和运行事件分视图">
  <img src="./docs/frontend/baseline/screenshots/ledger-desktop-light.png" width="49%" alt="TelePilot 资金台账，按账号和插件核对入账、出账与净额">
</p>

<p align="center">
  <img src="./docs/frontend/baseline/screenshots/dashboard-mobile-dark.png" width="39%" alt="TelePilot PWA 深色主题概览">
  <img src="./docs/frontend/baseline/screenshots/ai-mobile-light.png" width="39%" alt="TelePilot PWA 浅色主题 AI 中心">
</p>

</details>

## 能做什么

| 工作台 | 主要能力 |
| --- | --- |
| **账号** | Web 向导绑定账号，管理代理、设备参数、Account Bot 和功能开关；Supervisor 负责启动、停止、重连、退避与资源状态 |
| **插件** | 内置插件、本地导入、ZIP 安装和 Git 仓库；支持签名策略、权限声明、版本检查、全局安装、按账号启停和 Worker 热重载 |
| **AI** | 接入 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages 和 Ollama 兼容协议；支持多 Key 密钥池、模型元数据、测活、路由标签、调用记录、Token 与预算 |
| **系统助手** | Web 悬浮助手和管理 Bot `/agent` 共用可恢复 Durable Run；工具渐进披露、只读工具直接执行、写操作先生成待确认 Action |
| **交互** | Interaction Bot 承接关键词、付款确认、按钮回调和群会话；会话状态持久化，命令入口仍可走用户账号通道 |
| **指令与任务** | 消息模板、AI 指令、定时任务和自动命令白名单集中管理，支持按账号启用和立即执行 |
| **资金台账** | Action ledger、按账号查询、统计与导出、payout 限额、持久化幂等、失败补偿和 ambiguous 人工核对 |
| **可观察性** | 消息流、命中调试、Runtime/Audit 日志、Event Trace、reason code 和原始事件关联下钻 |
| **系统运维** | 平台模块热关闭与恢复、系统健康、全局紧急停用、配置导出、备份恢复、在线增量更新和版本提示 |

更完整的页面说明和操作步骤见 [使用指南](./docs/USAGE-GUIDE.md)。

## 运行结构

```mermaid
flowchart LR
  UI["Web / PWA"] --> API["FastAPI 控制面"]
  API --> PG[("PostgreSQL")]
  API --> Redis[("Redis")]
  API --> Agent["System Agent"]
  API --> Supervisor["Worker Supervisor"]
  Supervisor --> W1["Account Worker A"]
  Supervisor --> W2["Account Worker B"]
  W1 --> TG["Telegram 用户账号<br/>Telethon / MTProto"]
  W2 --> TG
  W1 --> Plugins["Plugin Runtime"]
  W2 --> Plugins
  Plugins --> LLM["LLM Providers"]
  API --> AccountBot["Account Bot Manager"]
  API --> InteractionBot["Interaction Bot Manager"]
  AccountBot --> BotAPI["Telegram Bot API"]
  InteractionBot --> BotAPI
  API --> Updater["Private Updater"]
```

- FastAPI 负责 Web API、认证、配置、System Agent、两个 Bot manager 和 Worker Supervisor。
- 每个运行中的账号对应一个独立 `multiprocessing.spawn` Worker，Worker 内运行 Telethon、插件、命令和调度器。
- PostgreSQL 保存业务数据和加密后的敏感字段；Redis 用于 IPC、租约、去重、限速和短期状态。
- FastAPI 启动时分别拉起 Supervisor、Account Bot manager 和 Interaction Bot manager。失败会进入后台重试，并让 `/readyz` 保持未就绪。
- `/healthz` 只表示 FastAPI 进程存活；生产流量和更新完成判定使用 `/readyz`。

### Telegram 通道边界

| 通道 | 负责什么 |
| --- | --- |
| 用户账号通道（UserBot） | Account Worker 使用 Telethon 以用户账号 session 连接 Telegram；负责消息、命令会话、插件、定时任务和 payout |
| Account Bot | 每账号可选的管理 Bot，提供授权用户远程运维和 `/agent` 入口 |
| Interaction Bot | 关键词、付款确认、按钮回调、群互动和会话消息 |
| Webhook | 外部系统向指定账号投递标准事件，不绕过平台能力、权限、限流和 Trace |

`MTProto` 是 Telethon 连接 Telegram 用户账号的底层协议，不是额外部署的服务。Account Bot 与 Interaction Bot 才使用 Telegram Bot API。普通命令默认走用户账号通道；关键词、付款和按钮会话默认走 Interaction Bot；`payout` 固定交给用户账号通道。

完整组件、生命周期和隔离边界见 [架构说明](./docs/TELEPILOT-ARCHITECTURE.md)。

## 快速开始

### 本机试用

需要 Python 3.12、Node.js 22、pnpm 10.23、Docker Desktop 或 Docker Engine、Docker Compose v2、curl 和 `make`。后端包声明为 Python `>=3.12`，但当前 CI 基线只验证 3.12；使用更高版本前请先跑完整后端测试。

```bash
git clone https://github.com/Anoyou/Telebot telepilot
cd telepilot
make up
```

`make up` 首次运行会自动创建 `backend/.venv`、生成带随机密钥的 `.env`、安装前后端依赖、启动 PostgreSQL 与 Redis、执行迁移，再启动 FastAPI 和 Vite。

- Web：[http://localhost:5173](http://localhost:5173)
- FastAPI 文档：[http://localhost:8000/docs](http://localhost:8000/docs)

```bash
make status   # 查看 PostgreSQL、Redis、后端和前端状态
make logs     # 跟踪后端与前端日志
make restart  # 清理旧 Worker 后重新启动全部开发进程
make down     # 停止服务，保留数据和依赖
```

### VPS 一键安装

适用于 Ubuntu 或 Debian：

```bash
curl -fsSL https://raw.githubusercontent.com/Anoyou/telebot/main/scripts/install-server.sh | bash
```

脚本默认安装到 `/opt/telepilot`，生成生产密钥，从 GHCR 拉取预构建的 AMD64/ARM64 应用镜像，并启动 PostgreSQL、Redis、FastAPI/Worker、前端 Nginx 和内网 Updater。服务器不需要编译前端或应用镜像。

公网使用前还要配置 HTTPS、反向代理和备份，详见 [公网部署指南](./docs/DEPLOY-PUBLIC.md)。已克隆仓库可以手动初始化生产配置：

```bash
make init-prod-env
make prod-up
```

### 第一次启动后

1. 在 [my.telegram.org](https://my.telegram.org) 申请 Telegram `API ID` 和 `API Hash`。
2. 打开 TelePilot，注册第一个 Web 管理员。
3. 通过账号向导绑定 Telegram 账号，完成验证码或两步密码验证。
4. 按需配置账号代理、Account Bot、Interaction Bot、通知 Bot 和 AI Provider。
5. 从插件中心安装或导入插件，再按账号启用。
6. 先用 dry-run、命中调试和日志确认规则，再开启自动动作或 payout。

大部分业务配置都在 Web 面板完成。`.env` 只保存进程启动前必须知道的密钥、数据库连接、端口、反代信任和资源限制。

## 安全边界

- `MASTER_KEY` 加密 Telegram session、API Hash、代理密码、Bot Token、Webhook Token、TOTP secret、LLM Key 和 Provider 兼容请求头。丢失后旧密文无法恢复，请与数据库备份分开保存。
- 公网环境必须使用 HTTPS，设置强 `JWT_SECRET` 和数据库密码，并限制后端端口只在可信网络可见。
- TelePilot 采用个人可信插件模型，不提供公共插件市场式强沙箱。新 ZIP 默认拒绝未签名安装；历史未签名插件使用独立兼容开关。
- Webhook Token 默认只从 `X-TelePilot-Webhook-Token` 请求头读取；查询参数兼容默认关闭。
- payout 限额、AI 预算、关键风控、平台能力缓存和交互 claim 在依赖故障时采用 fail-closed。
- payout 遇到超时或未知发送结果时进入 ambiguous，不会把异常视为未发送后自动重付。
- 全局紧急停用会并行收敛 Worker、Account Bot 和 Interaction Bot；部分失败返回 `KILL_SWITCH_PARTIAL_FAILURE`。
- Updater 可以控制宿主机 Docker，必须使用独立 `UPDATER_TOKEN`，不能和 `JWT_SECRET` 复用。
- System Agent 聊天里的 Key 以 `[[TP-CRED-n]]` 占位符交给模型；真实值仅在面向本人的回复与工具执行时还原。
- 自动检查更新只做“检查 + 记录 + 提示”，不会后台 pull 或重启；容器内无法比对 Git 时不会误报有新版本。

生产检查、密钥轮换、备份恢复和应急流程见 [安全运维 SOP](./docs/SECURITY-OPS.md)。

## 插件与开发

普通插件通过 Event Bus、标准事件信封、MessageOps/action、存储、身份、HTTP 和 AI facade 接入，不需要直接接触 Bot Token 或 Telegram session。

```bash
make plugin-new name=my_game profile=session_game
make plugin-check dir=plugins/local_imports/my_game
make plugin-register dir=plugins/local_imports/my_game
```

| 要做什么 | 从这里开始 |
| --- | --- |
| 写第一个插件 | [插件 5 分钟 Quickstart](./docs/PLUGIN-QUICKSTART.md) |
| 理解插件生命周期 | [插件开发指南](./docs/PLUGIN-DEV-GUIDE.md) |
| 查字段、事件和 action | [插件 API 参考](./docs/PLUGIN-API-REFERENCE.md) |
| 确认权限与安全边界 | [插件安全边界](./docs/PLUGIN-SAFETY.md) |
| 发布远程插件 | [远程插件](./docs/PLUGIN-REMOTE.md) |
| 参与 TelePilot 本体开发 | [开发指南](./CONTRIBUTING.md) |

## 文档

| 主题 | 文档 |
| --- | --- |
| 安装、升级、备份和回滚 | [公网部署](./docs/DEPLOY-PUBLIC.md) |
| 生产安全、密钥轮换和应急停用 | [安全运维](./docs/SECURITY-OPS.md) |
| 组件、数据流和生命周期 | [架构说明](./docs/TELEPILOT-ARCHITECTURE.md) |
| 平台模块热关闭与恢复 | [平台能力](./docs/PLATFORM-CAPABILITIES.md) |
| System Agent、Durable Run 与 Action | [系统助手](./docs/SYSTEM-AGENT.md) |
| Codex 客户端兼容 Gateway | [Codex Gateway](./docs/CODEX-GATEWAY.md) |
| LLM 多 Key 密钥池与 Magpie 接入 | [LLM 密钥池](./docs/LLM-KEY-POOL.md) |
| 内部 HTTP API、认证、错误与类型生成 | [API 契约](./docs/API-CONTRACT.md) |
| 外部系统触发插件 | [Webhook Quickstart](./docs/PLUGIN-WEBHOOK-QUICKSTART.md) |
| 插件 HTTP 与 AI 能力 | [HTTP facade](./docs/PLUGIN-HTTP.md) · [AI facade](./docs/PLUGIN-AI.md) |
| 页面怎么用、问题先查哪里 | [使用指南](./docs/USAGE-GUIDE.md) |
| iOS App 推送通知（APNs）配置 | [iOS 推送教程](./docs/IOS-PUSH.md) |
| 版本变化 | [CHANGELOG](./CHANGELOG.md) |

## 技术栈

`Python 3.12` · `FastAPI` · `SQLAlchemy 2` · `Alembic` · `PostgreSQL 16` · `Redis 7` · `Telethon` · `React 18` · `TypeScript` · `Vite 5` · `Tailwind CSS 3` · `TanStack Query 5` · `Radix UI` · `Playwright` · `Docker Compose` · `Nginx`

## 项目状态

TelePilot 仍处于 0.x 快速迭代阶段，主要面向单租户、自托管和个人可信环境。`main` 是稳定发布线，开发分支使用对应正式版本的 `beta.N` 预发布号；接口、页面和插件契约仍可能调整。升级前请阅读 [CHANGELOG](./CHANGELOG.md) 并完成备份。

当前源码版本以 `backend/app/__init__.py`、`backend/pyproject.toml`、`frontend/package.json` 与 `frontend/src/lib/version.ts` 四个源文件为准，并由 `make codegen` 同步到 `openapi/telepilot.openapi.json` 的 `info.version` 作为第五处发布校验。仓库历史名称仍是 `Telebot`，部分数据库默认值、Docker volume 和兼容字段保留旧名称，避免已有部署升级后连接到空数据；产品名称和界面统一使用 TelePilot。

## 致谢与许可

[MIT](./LICENSE)。感谢 [Telethon](https://codeberg.org/LonamiWebs/Telethon)、[FastAPI](https://fastapi.tiangolo.com/)、[React](https://react.dev/) 与 [Tailwind CSS](https://tailwindcss.com/) 及其社区。

README 首屏运行图使用 [live-panel](https://github.com/ythx-101/live-panel-skill)（MIT）生成；源配置位于 [`assets/readme/source/`](./assets/readme/source/)，画面数字与日志均为结构示意。
