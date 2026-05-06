# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [SemVer](https://semver.org/lang/zh-CN/)：MAJOR.MINOR.PATCH。

- **MAJOR**：不向后兼容（数据库不兼容迁移 / 协议大改 / 配置项重命名 / API 路径变更）
- **MINOR**：向后兼容的功能增量（一个 Sprint 通常 +1）
- **PATCH**：bug 修复 / 文档 / 小调整 / hotfix

> 版本号在 5 处必须保持同步：`backend/app/__init__.py`、`backend/pyproject.toml`、`frontend/package.json`、`frontend/src/lib/version.ts`、本文件顶部段落。`backend/app/main.py` 通过 `from . import __version__` 自动跟随，无需单独改。详见 `agent-plans/README.md` §6。

---

## [0.5.1] — 2026-05-06 · RC2 · 兼容性修复与部署优化

### Added
- **项目归档与预览支持**：
  - 新增 `docs/screenshots/` 文件夹用于存放 UI 截图
  - `.gitignore` 增加 `archive/` 与 `.github_backup/` 规则
- **部署依赖**：`backend/pyproject.toml` 补全 `psycopg2-binary` 依赖，解决 Docker 模式下 Alembic 无法运行同步迁移的问题

### Changed
- **Claude API 协议优化**：`AnthropicClient` 切换为 **Streaming (SSE)** 模式调用，解决 Anyrouter 代理返回 400 的兼容性问题
- **README 全面优化**：
  - 更新为真实的用户名 `Anoyou`
  - 增加 **Docker 部署（生产环境推荐）** 指南
  - 增加 **Prerequisites**（API ID/Hash 申请与网络代理配置）
  - 增加 **Configuration** 核心安全项说明
- **Docker 构建优化**：前端镜像 Build Context 提升至项目根目录，支持构建时访问 `docs/` 进行文档打包

### Fixed
- **Worker 缓存过时**：在执行每个 AI 命令前强制从 DB 刷新 `LLMProvider` 列表，解决“前端新增 provider 后 ,ai @list 仍显示未找到”的问题
- **Worker 环境补全**：修复 `_run_ai` 缺失 `api_format` 导致无法正确识别模型协议的 bug
- **文件名大小写修复**：`Dashboard.png` -> `dashboard.png` 确保在 Linux 容器下正常渲染

---

## [0.5.0] — 2026-05-06 · RC1 · 开源前打磨

### Added
- **GitHub Actions CI**：`.github/workflows/ci.yml` 自动化测试流水线
  - Backend job：pytest + ruff + alembic upgrade（PostgreSQL 16 + Redis 7 services）
  - Frontend job：pnpm build（Node 20 + pnpm 9）
  - 推送到 main 或 PR 时自动触发
- **开源协议**：MIT License，同步更新 `pyproject.toml` 和 `package.json` license 字段
- **应急响应工单模板**：`docs/SECURITY-OPS.md` 新增 §6，提供安全事件记录模板

### Changed
- **README 重写为开源向**：
  - 简洁的 Feature 列表（8 项核心功能）
  - Quick Start 分本机自用 / 公网部署两条路径
  - FAQ 回答"与 PagerMaid 区别"、"多用户支持"、"为什么用 userbot"
  - 状态声明：Alpha / 个人自用 / 欢迎 fork 但暂不接大 PR
- **SECURITY-OPS 润色**：
  - 顶部新增"如果你打算公网部署"提示
  - "某 web 用户账号"改为"管理员账号"（单租户语境）
  - 应急 SOP 措辞更精准

### Fixed
- **inline @provider 覆盖 bug**：`,ai @provider 问题` 现在将会正确使用 已配置的provider 的 default_model，而不是错误地使用命令模板里配置的 model
  - 旧逻辑：`override_model = cfg.get("model")` 总是从模板读取，导致切换 provider 后 model 不匹配
  - 新逻辑：当用户指定 `@provider`（未指定 `:model`）时，清空 `override_model`，让 `build_client` 使用该 provider 的 `default_model`
  - 优先级现在是：`@name:model` > provider.default_model > 模板 model（仅当无 inline override 时）
  - 新增测试用例：`test_run_ai_inline_provider_without_model_clears_template_model`

### Removed
- **仓库归档清理**（已在 Wave 2 完成，本次补充文档）：
  - `agent-plans/SPRINT4-*.md` 归档到 `archive/plans/SPRINT4/`
  - `archive/plans/SPRINT4/README.md` 溯源文档更新

### Notes
- 首个 Release Candidate（RC1），可以开源了
- CI 徽章 URL 需要替换为实际 GitHub 仓库地址
- README 截图占位（Coming soon），待后续补充

---

## [0.4.2] — 2026-05-06 · Sprint 4 hotfix · 版本检测

### Added
- **前后端版本不一致检测**：避免再发生"代码改了但 uvicorn 没重启 / SW 缓存老前端 → 用户看老行为以为代码没生效"的幻觉式 bug。
  - 后端：`GET /api/system/version`（**public 无鉴权**）返回 `{version, stage}`
  - 前端：`GlobalAlertBar` 拆成 `VersionMismatchBar` + `KillSwitchBar`；启动 + 每 60s 拉一次后端版本，不一致时顶部弹**琥珀色横幅**："前后端版本不一致 · 前端 vX.Y.Z · 后端 vA.B.C — 请 `make restart` + 硬刷"，含一键"硬刷新"按钮。
- **agent-plans/README.md §4.1**：新增"必跑 `make restart`"规范，列明哪些情况必须整套重启 + 哪些 `--reload` 能搞定。
- **agent-plans/README.md §3** 共用基础设施：把 `make up` / `make restart` / `make down` 提到首位，`make backend`（带 reload）降级为"调试时用"。

### Notes
- 单元测试不要直接 `from app.api.system_health import router` 然后调 `/api/system/version`——这是 public 端点没鉴权依赖，pytest 拿到的是 `VersionInfo(version=__version__, stage="Sprint 4")`，下次 bump 版本号时 `stage` 字段如果摘掉得记得这里同步。

---

## [0.4.1] — 2026-05-06 · Sprint 4 hotfix · UX 体验四件套

### Added
- **内置命令面板**：自定义命令模板页顶部新增「内置命令（只读）」卡片，列出 worker 静态注册的所有命令（name + aliases + doc），让用户起自定义模板名时一眼知道哪些已被占用。
  - 后端：`GET /api/commands/builtin` 从 worker `_BUILTIN` 字典读取（无 DB 依赖，无运行时状态）
  - 安全：纯只读，无敏感数据
- **扩展中心**（路由 `/extensions`）：合并原「功能矩阵」+「插件管理」+「插件开发指南」三处入口，三 tab 形式呈现：
  - Tab 1：功能矩阵（账号 × 功能 启停状态总览，含克隆其他账号规则功能）
  - Tab 2：已加载插件（builtin + 第三方装到 `data/plugins/installed/` 的）
  - Tab 3：开发指南（`docs/PLUGIN-DEV-GUIDE.md` 通过 vite raw import 打包进前端，react-markdown + remark-gfm + rehype-highlight 渲染）
- 前端依赖：`react-markdown` / `remark-gfm` / `rehype-highlight` / `highlight.js` / `@tailwindcss/typography`。

### Removed
- **`group_admin` / `monitor` 残留清理**（v0.4.0 删了 builtin 目录但前端 / DB 未跟上）：
  - `frontend/src/pages/Features/GroupAdmin.tsx` / `Monitor.tsx` 文件
  - `App.tsx` 两条路由
  - `Wizard.tsx` cloneConfig 列表中的两项
  - `Detail.tsx` `FEATURE_KEYS` 数组中的两项
  - `feature.py` 的 `FEATURE_GROUP_ADMIN` / `FEATURE_MONITOR` 常量 + `BUILTIN_FEATURES` 映射
  - 数据库 `feature` / `account_feature` 表中的旧行（迁移 `0014_drop_legacy_features`）
- 老路由 `/matrix`、`/plugins`、`/settings/plugins` 改为 redirect 到 `/extensions`（书签兼容）
- `frontend/src/pages/FeatureMatrix.tsx` / `Plugins.tsx` / `Settings/PluginManager.tsx` 文件（内容并入 `Extensions.tsx`）

### Changed
- Sidebar 顶层菜单 7 项 → 7 项（"功能矩阵" + "插件管理" 合成 "扩展中心"，腾出位置但不增加）。

---

## [0.4.0] — 2026-05-06 · Sprint 4 Wave 2

### Added
- **定时任务插件**（迁移无新增，复用 `rule.config`）：完整实装 cron / once / interval 三种模式；前端 `Features/Scheduler.tsx` 完整规则编辑页 + dry-run；通过 `ctx.engine.acquire("send_message_group")` 走风控（`scheduler/plugin.py` 20 行 → 283 行）。
- **多 Telegram Bot 通知通道**（迁移 `0013_notify_bot`）：项目启动时发"📦 telebot vX.Y.Z started"；worker 进 dead 状态时发告警。
  - `bot_token` Fernet 加密入库，GET 返 `has_token: bool`，永远不返明文（约定 D）
  - 是 Bot Token（@BotFather 创建）走 HTTP API，与 userbot session 完全独立
  - 前端 `Settings/NotifyBots.tsx` 提供 CRUD + 一键测试
- **插件开发指南**：`docs/PLUGIN-DEV-GUIDE.md` 含目录骨架、Manifest 字段、Plugin/Context hook、风控接入、Telethon 速查、PagerMaid→Telethon 移植映射表（约定使用菜谱思维而非装他的电器）、沙箱权限说明。
- **首个移植样例**：`examples/plugins/translate/`——参考 PagerMaid_Plugins/translate 的"功能目标"重写为 Telethon + 我们的 Manifest 风格，作为开发模板（不放进 builtin/）。
- **公网部署文档**：`docs/DEPLOY-PUBLIC.md` + `deploy/Caddyfile.example`（Let's Encrypt 自动证书 + HSTS + 写操作限速 + reverse_proxy）。

### Removed
- `group_admin` / `monitor` 两个 builtin 插件骨架（PRD 列出但实际不需要的功能；20 行占位删除）。
- 插件市场 UI（`PluginMarket.tsx` / `plugin_repo_service.py` / 远程订阅 API / zip 上传 API）：单用户场景没人订阅远端仓库；用 `git clone` 到 `data/plugins/installed/<key>/` 替代。
- `plugin_repo` 表（迁移 `0012_drop_plugin_repo`）。

### Changed
- `PluginManager.tsx` 简化为"已加载列表 + enable/disable + uninstall"。
- `Settings/Index.tsx` 移除 `<PluginMarket />` 嵌入；新增 `<NotifyBots />`。
- `main.py` lifespan 启动时尝试发项目启动通知；`supervisor.py` 在 account 进 dead 时发告警（NotifyBot 未配置时静默跳过）。

### Fixed
- alembic 0012 误写 `down_revision="0010"` 与 0011 构成分叉，汇总验收时修正为 `down_revision="0011"`，再次回到单 head。

---

## [0.3.1] — 2026-05-06 · Sprint 4 Wave 1

### Added
- 内置命令新增 `,del N`：撤回当前会话中自己最近 N 条消息（含命令消息本身）。
- 自定义命令模板新增 `aliases` 字段（迁移 `0011_command_aliases`），支持一个模板多个短命令入口。

### Changed
- Telethon 依赖升级到 `>=1.43,<2.0.0`。
- 内置命令支持短别名：`help(h)`、`status(s, st)`、`id(i)`、`version(v)`。
- `,help` 输出改为折叠展示主命令与别名，并合并展示自定义模板别名。
- 命令模板设置页新增别名输入与展示列；保存时对别名格式和冲突做校验。

### Fixed
- 自定义命令名/别名冲突规则统一：同账号维度下，模板 `name + aliases` 不可互撞，也不可与内置命令及其别名冲突。

## [0.3.0] — 2026-05-05 · Sprint 3 · LLM 重构 + 安全加固

### Added — LLM Provider 体系大升级
- **多模型支持**（迁移 `0008`）：`llm_provider.models` JSONB 数组，单 provider 可启用多个模型；老数据自动把 `default_model` 回填到 `models[0]`。
- **API 协议解耦**（迁移 `0009`）：新增 `llm_provider.api_format`，独立于 provider 厂商：
  - `chat_completions`（OpenAI 经典 `/v1/chat/completions`）
  - `responses`（OpenAI 2024 新协议 `/v1/responses`，国内反代如 anyrouter 只接这个）
  - `anthropic_messages`（Anthropic `/v1/messages`）
  - 老数据按厂商自动回填；同一个反代可改协议而不改 provider。
- **LLM 走代理**（迁移 `0007`）：`llm_provider.proxy_id` 外键，支持调用 LLM 时走指定 SOCKS5/HTTP，绕开网络封锁。`mtproxy` 类型 schema 层拒绝。
- `services/llm_client.py` 重构：`OpenAIClient` / `ResponsesClient` / `AnthropicClient` 三客户端，按 `api_format` 路由。错误信息统一带 host 让用户一眼看出是哪个反代不通。
- `services/llm_format.py`（新建，336 行）：输出模板渲染器
  - `{key}` 占位符 + `{?key}...{/?}` 条件块（未知 key 留空，不抛 KeyError）
  - 派生变量 `answer_first_2` / `answer_rest`，配合 `<blockquote expandable>` 实装"前两行 + 折叠"
  - 默认走 HTML（telethon 1.36 不支持 markdownv2）
  - 4000 字符截断（TG 单条 4096 上限留余量）

### Added — 安全加固
- **JWT 版本校验**（迁移 `0010`）：`web_user.pwd_version` 默认 0
  - JWT payload 加 `pwd_v`
  - 改密后 `pwd_version += 1`
  - 旧 token 上的 `pwd_v` 不匹配自动失效
  - 这是 0.2.1 SECURITY-OPS 里"改密后强制下线"的真正实装；之前只是清当前 cookie，旧 token 在另一台设备上还能用

### Added — 命令模板编辑增强
- 新增 4 个预设输出模板（simple / quote / minimal / translate）一键插入。
- 占位符按钮 + 条件块按钮 → 不会写模板的用户也能拼出消息格式。
- ProviderModelSelect / ProviderModelsSection：在多模型 provider 里展开式选具体 model。

### Changed
- LLMProviders 页改造：API format 选择 / 模态 / tag / cost_tier / 模型列表管理。
- CommandTemplates 页改造：输出模板可视化编辑、provider+model 双层选择。

### Notes
- 升级路径：`alembic upgrade head` 一次跑齐 0007 → 0008 → 0009 → 0010。
- Sprint 2 完成 (0.2.0) 时 LLM 是骨架级（仅 OpenAI/Anthropic 直连 + 单模型 + chat_completions 写死），本次相当于把这条线做到生产可用级别。

---

## [0.2.1] — 2026-05-03 · Sprint 2 hotfix

### Fixed
- alembic 0003 分叉（`0003_command_template` 与 `0003_add_device_profile` 同号同 down_revision）线性化为 `0003 → 0003b`，`alembic upgrade head` 现在能跑通。
- 自动回复"突然不工作"——根因是 0006 给 `llm_provider` 加了 `modality` / `tags` / `cost_tier` / `notes` 但生产 PG 没升级；worker 启动时 `select(LLMProvider)` 报 UndefinedColumnError 导致 supervisor 把账号置 dead。修复后跑了 `alembic upgrade head`。
- `test_accounts.py` 的 `AsyncMock(db)` fixture 没跟上 `device_profile.get_default` 的引入，9 个 confirm 系列测试失败；新增 `_stub_device_profile` autouse fixture 修复。

### Added
- 日志中心拆成「消息日志 / 系统日志」两个 tab：
  - **消息日志** (`source=event`)：incoming 消息事件 / plugin 命中 / 命令派发，排查"为什么没回复"用
  - **系统日志** (`source=system`)：worker 启停 / IPC reload / 风控状态 / 技术异常，排查"账号是不是真的活着"用
  - 后端 `/api/logs/runtime` 加 `source` 过滤参数，自动兼容历史 `worker` / `plugin` 旧值
- `worker/runtime.py` `_log()` 与 `worker/plugins/loader.py` `_log()` 增加 `source` 关键字参数（默认 `system` / `event`）。
- `RecentPeersResponse`：`fetch_recent` 拆 `(worker_alive, items)`，前端可精准引导"worker 离线 vs 没收到消息"两种空状态。

### Changed
- 忽略 tab 在最近活跃为空时显示 worker 在线/离线徽章 + 三态精准提示文案。
- 风控基础动作中文标签全部对齐后端 `_DEFAULTS`（之前 8 个 key 名错位 + 6 个未覆盖）。
- 详情页 tg_user_id 缺失警示条上加"重启 worker 同步"按钮（pause → 1s → resume）。

---

## [0.2.0] — 2026-05-03 · Sprint 2

### Added
- 自定义命令模板（reply_text / forward_to / ai / run_plugin）+ `,help` 自动列出。
- LLM Provider 抽象（OpenAI / Anthropic，API key Fernet 加密入库，仅 worker 进程内解密）。
- 忽略群组：per-account `ignored_peers`、worker `recent_peers` LRU、前端一键加入。
- 账号头像（懒加载、缓存 24h）+ Humanize 子区。
- 插件模块化 tier C：Manifest + zip 安装 + 仓库订阅 + 权限沙箱。
- 转发插件（4 mode：原生 / 复制 / 引用 / 仅链接）。
- Web 端：修改密码 / 禁用 TOTP。
- KillSwitch 全局红色横幅 + AccountStatusBadge 复合状态。
- 风控每个动作显示中文标签 + 一句话说明。
- 内置命令 `,version`：在 TG 内查看版本与运行环境。
- `docs/SECURITY-OPS.md` 生产部署安全清单。

### Changed
- 账号详情 "忽略" tab 改名 "忽略的群组"，最近活跃为空时显示精准引导。
- "出口/代理" tab 改名 "出口/伪装"。
- 老账号 `tg_user_id` / `tg_username` 缺失时给出"重启 worker 自动同步"提示。
- `backend/app/main.py` 的 `FastAPI(version=...)` 改为读 `app.__version__`，避免与 `__init__.py` 不同步。
- 前端版本号改为 `frontend/src/lib/version.ts` 单点定义，Sidebar 通过 `APP_VERSION_LABEL` 引用。

### Fixed
- 紧急停用后 UI 状态联动（之前 banner 不存在 + badge 还显示运行中）。

---

## [0.1.0] — 2026-04-xx · MVP / Sprint 1

### Added
- Telethon 切换（替换 PagerMaid-Pyro）。
- 登录向导（验证码 + 2FA）。
- Worker 子进程模型（mp spawn context + atexit/SIGTERM 守护）。
- IPC pub/sub（Redis）。
- 自动回复（关键词 / 正则 / 作用范围 / 冷却 / 白黑名单 / reply_to）。
- 风控引擎（18 actions × 5 policies × 3 层继承）。
- FloodWait / PeerFlood / SlowMode 异常映射。
- 代理库 + 连通性测试。
- 顶部网络环境徽章（IP / 国家 / 缓存 5min）。
- 内置命令：`,help` `,id` `,status` `,ping` `,pause` `,resume`。
- 两轮 review 修复（21 处真实改动，详见 `REVIEW-FIXES-REPORT.md`）。
